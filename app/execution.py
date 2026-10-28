"""Case execution loop: ``run_case(case_request, adapter, emit)`` (contract §2.3/§2.6).

The executor assembles the frozen messages (system prompt + main SKILL.md + the
sub-skill index/preloaded content + query + allowed context), drives the model
loop through the unified adapter interface, executes the internal observation
tools and business tools (business tools are **only** simulated from fixtures),
and returns the complete ``CaseExecutionResult`` plus the emitted event list.

Hard invariants:

* ``expected`` / scoring labels are never placed in messages, tools or adapter
  requests (A03/A11); a defensive scrub also removes them from the incoming
  request/template.
* a tool call without a matching fixture fails immediately with ``fixture_miss``
  and no real service is ever contacted;
* internal tools (``select_skill``/``read_skill``/``finish``) never appear in the
  business ``tool_calls`` result;
* events are emitted as they happen with ``case_id``/``call_id`` attached; ``seq``
  is assigned by the pipeline (``persistence.append_event``); when the sink
  returns an enriched event its ``seq`` is preserved.
* cancel checks are polled between steps and passed to the adapter so an active
  WS/HTTP request can be closed.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from . import event_types
from .model_output import capture_model_response, response_summary
from .adapters import text_parse
from .adapters.base import (
    AdapterError,
    ModelAdapter,
    collect_events,
    emit_event,
    json_dumps,
    normalize_capabilities,
    normalize_usage,
    parse_tool_arguments,
    redact,
    sum_usage,
)
from .errors import AppError
from .obs_tools import INTERNAL_TOOL_NAMES, ObsToolError, ObsToolRuntime

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_LIMITS", "build_messages", "run_case"]

#: Frozen defaults (§10: 12 model responses, 120 s per case).
DEFAULT_LIMITS: dict[str, Any] = {
    "max_model_calls": 12,
    "max_steps": 12,
    "case_timeout_s": 120.0,
    "max_context_chars": 32000,
    "context_overflow": "error",
}

#: Keys that must never reach the model / user flow / adapter request.
_FORBIDDEN_KEYS = frozenset({
    "expected", "expected_output", "scoring", "score", "rubric", "labels",
    "answer", "answers", "ground_truth", "golden", "grade",
})

_INTERNAL = frozenset(INTERNAL_TOOL_NAMES)


def _message_context_chars(message: Mapping[str, Any]) -> int:
    """Count prompt content plus serialized tool arguments consistently."""
    total = len(str(message.get("content") or ""))
    tool_calls = message.get("tool_calls")
    if tool_calls:
        total += len(json.dumps(tool_calls, ensure_ascii=False,
                                separators=(",", ":")))
    return total

#: Trigger values for the frozen "frozen_strategy" overflow behaviour.
_OVERFLOW_STRATEGIES = ("index_only", "drop_preload", "preload_off", "drop_context")

#: Frozen text protocol replacement for select_skill/read_skill/finish (§41).
TEXT_PROTOCOL_ADDENDUM = """
【输出协议（纯文本，固定）】
本适配器不使用原生工具调用，请只输出以下固定行，不要输出其他内容：
依据：<一句话业务依据摘要>
子技能：<skill_id>[,<skill_id>...]
工具：<工具名> {"参数": "值"}        （每次业务工具调用一行，按调用顺序排列）
结束：{"action": "tool_call|clarify|no_action", "selected_skills": ["<skill_id>"], "missing_fields": [], "summary": "<简短摘要>"}
首次选择子技能后，先等待平台返回子技能正文，再在下一轮输出业务工具调用或结束。
"""



class _Failure(Exception):
    """Internal control-flow failure carrying a case error code."""

    def __init__(self, code: str, message: str, *, details: Any = None):
        super().__init__(message)
        self.code = code if code in event_types.CASE_ERROR_CODES else "platform_error"
        self.message = message
        self.details = details

    @property
    def kind(self) -> str:
        return event_types.CASE_ERROR_CODES[self.code]


def _scrub(value: Any, *, depth: int = 0) -> Any:
    """Recursively drop expected/answer-like keys from caller supplied data."""
    if depth > 8:
        return value
    if isinstance(value, Mapping):
        return {key: _scrub(item, depth=depth + 1) for key, item in value.items()
                if not (isinstance(key, str) and key.lower() in _FORBIDDEN_KEYS)}
    if isinstance(value, list):
        return [_scrub(item, depth=depth + 1) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


def _deep_subset(expected: Any, actual: Any) -> bool:
    """Strict recursive subset match used by the local fixture fallback."""
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        for key, value in expected.items():
            if key not in actual:
                return False
            if not _deep_subset(value, actual[key]):
                return False
        return True
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)) or len(expected) != len(actual):
            return False
        return all(_deep_subset(left, right) for left, right in zip(expected, actual))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected is actual
    if expected is None or actual is None:
        return expected is None and actual is None
    return type(expected) is type(actual) and expected == actual


class _LocalFixtureRuntime:
    """Fallback fixture matcher; replaced by ``app.fixtures.FixtureRuntime``.

    Kept small and contract-faithful (name + deep subset of ``match``, ``once``
    consumed in call order, ``always`` repeatable) so execution works before T1's
    module lands. It is only used when the shared fixture module is unavailable.
    """

    def __init__(self, fixtures: Sequence[Mapping[str, Any]] | None = None):
        self._fixtures = [dict(item) for item in (fixtures or []) if isinstance(item, Mapping)]
        self._consumed: set[int] = set()
        self.misses: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._consumed.clear()
        self.misses.clear()

    def resolve(self, name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any] | None:
        args = dict(arguments or {})
        for index, fixture in enumerate(self._fixtures):
            if fixture.get("name") not in (name, "*"):
                continue
            consume = str(fixture.get("consume") or "always")
            if consume == "once" and index in self._consumed:
                continue
            try:
                if not _deep_subset(fixture.get("match") or {}, args):
                    continue
            except (TypeError, ValueError):
                continue
            consumed = consume == "once"
            if consumed:
                self._consumed.add(index)
            return {
                "matched": True,
                "tool": name,
                "arguments": args,
                "response": fixture.get("response"),
                "fixture_index": index,
                "consume": consume,
                "consumed": consumed,
            }
        self.misses.append({"tool": name, "arguments": args})
        return None


def _build_fixture_runtime(fixtures: Sequence[Mapping[str, Any]] | None):
    """Prefer T1's shared ``app.fixtures.FixtureRuntime`` when it is importable."""
    try:
        from .fixtures import FixtureRuntime  # type: ignore[attr-defined]
    except ImportError:
        logger.warning("app.fixtures unavailable, using local fixture fallback")
        return _LocalFixtureRuntime(fixtures)
    except Exception:  # pragma: no cover - defensive against broken module import
        logger.exception("app.fixtures import failed, using local fixture fallback")
        return _LocalFixtureRuntime(fixtures)
    try:
        runtime = FixtureRuntime(fixtures or [])
    except Exception:
        logger.exception("FixtureRuntime init failed, using local fixture fallback")
        return _LocalFixtureRuntime(fixtures)
    return runtime


class _FixtureHandle:
    """Uniform façade over the shared fixture runtime and the local fallback."""

    def __init__(self, fixtures: Sequence[Mapping[str, Any]] | None):
        self._runtime = _build_fixture_runtime(fixtures)

    def reset(self) -> None:
        reset = getattr(self._runtime, "reset", None)
        if callable(reset):
            reset()

    def resolve(self, name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any] | None:
        resolve = getattr(self._runtime, "resolve", None)
        try:
            hit = resolve(name, dict(arguments or {})) if callable(resolve) else None
        except Exception:
            logger.exception("fixture resolution failed for tool %s", name)
            hit = None
        if hit is None:
            return None
        if isinstance(hit, Mapping):
            if hit.get("matched") is False:
                return None
            if "response" in hit:
                return dict(hit)
        return {"matched": True, "tool": name, "arguments": dict(arguments or {}),
                "response": hit, "fixture_index": None, "consume": "always"}

    def misses(self) -> list[dict[str, Any]]:
        misses = getattr(self._runtime, "misses", None)
        if isinstance(misses, list):
            return [dict(item) for item in misses if isinstance(item, Mapping)]
        return []

    def diagnose_miss(self, name: str,
                      arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        diagnose = getattr(self._runtime, "diagnose_miss", None)
        if callable(diagnose):
            try:
                result = diagnose(name, dict(arguments or {}))
                if isinstance(result, Mapping):
                    return dict(result)
            except Exception:
                logger.exception("fixture miss diagnosis failed for tool %s", name)
        return {"reason": "unknown", "fixture_count": None,
                "available_tools": [], "candidate_indices": []}


def _fixture_miss_message(name: str, diagnosis: Mapping[str, Any]) -> str:
    reason = diagnosis.get("reason")
    if reason == "empty_fixtures":
        return f"工具 {name} 所在案例没有配置 fixtures"
    if reason == "tool_name_mismatch":
        tools = ", ".join(str(item) for item in diagnosis.get("available_tools") or [])
        return f"工具 {name} 没有对应 fixture（已声明：{tools or '无'}）"
    if reason == "fixtures_consumed":
        return f"工具 {name} 的匹配 fixture 已被 consume=once 用尽"
    if reason == "no_miss":
        return f"工具 {name} 的 fixture 在诊断时仍可匹配；请检查并发或重复执行状态"
    if reason == "arguments_mismatch":
        paths = sorted({path for values in (diagnosis.get("mismatch_paths") or {}).values()
                        for path in values})
        return f"工具 {name} 有对应 fixture，但参数不匹配{('（字段：' + ', '.join(paths[:8]) + '）') if paths else ''}"
    return f"工具 {name} 的调用没有匹配 fixture"


def _fixture_miss_next_action(diagnosis: Mapping[str, Any]) -> str:
    return {
        "empty_fixtures": "为该案例添加与工具调用对应的 fixture。",
        "tool_name_mismatch": "核对 Skill 声明、预期工具名与 fixture 名称，修正不一致的一方。",
        "fixtures_consumed": "若预期允许重复调用，补足模拟返回次数；否则检查模型是否重复调用。",
        "arguments_mismatch": "检查 fixture.match 的字段层级、值和数据类型。",
        "no_miss": "检查重复调用或并发执行状态后重试。",
    }.get(diagnosis.get("reason"), "检查工具调用与案例 fixture 配置。")


# --------------------------------------------------------------------------- #
# message assembly
# --------------------------------------------------------------------------- #
def _safe_read(root: Path, relpath: str) -> str:
    normalized = relpath.replace("\\", "/").lstrip("/")
    if normalized.startswith("../") or "/../" in normalized:
        raise _Failure("platform_error", f"非法 Skill 文件路径: {relpath}")
    target = (root / normalized).resolve()
    if root not in target.parents and target != root:
        raise _Failure("platform_error", f"非法 Skill 文件路径: {relpath}")
    if not target.is_file():
        raise _Failure("platform_error", f"Skill 包文件缺失: {relpath}")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _Failure("platform_error", f"Skill 文件不是 UTF-8 文本: {relpath}") from exc


NATIVE_PROTOCOL_ADDENDUM = (
    "\n\n【内部工具协议补充】\n"
    "调用 select_skill 后，所选技能正文已在当前上下文中：首次加载直接返回正文，预加载或重复选择不重复注入；"
    "无需再调用 read_skill 读取同一路径。read_skill 仅用于读取尚未加载的其他引用文件。"
    "\n\n【原生业务工具调用协议】\n"
    "平台只提供一个业务入口 call_tool。业务工具名称和参数格式来自已加载的子技能；"
    "调用时必须传入 JSON：{\"name\": \"<Skill 中的业务工具名>\", \"arguments\": {<工具参数>}}。"
    "不要直接调用业务工具名，也不要把 select_skill、read_skill、finish 或 call_tool 放入 name。"
    "select_skill/read_skill 首次返回正文后必须等待下一轮模型推理；不要在同一批次继续调用依赖该正文的业务工具或 finish。"
)


def build_messages(case_request: Mapping[str, Any], *,
                   loader_config: Mapping[str, Any] | None = None,
                   limits: Mapping[str, Any] | None = None,
                   text_protocol: bool = False,
                   loaded_files: MutableMapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Assemble the frozen messages (system prompt + main skill + query/context).

    Markers are part of the frozen protocol consumed by adapters/demo scripts:

    * system = ``system_prompt + "\\n\\n【主 SKILL.md】\\n" + main + "\\n\\n【子技能清单】\\n" + manifest json``
    * user   = ``"用户请求：" + query [+ "\\n上下文：" + canonical json]``
    """
    request = dict(case_request)
    if loaded_files is not None:
        loaded_files.clear()
    limits = {**DEFAULT_LIMITS, **(request.get("limits") or {}), **(limits or {})}
    loader = dict(loader_config if loader_config is not None
                  else (request.get("loader_config") or {}))
    snapshot = Path(str(request.get("skill_snapshot_path") or "")).resolve()
    manifest = request.get("manifest") or {}
    main_rel = str(manifest.get("main") or "SKILL.md")
    main_text = _safe_read(snapshot, main_rel)
    system_prompt = str(request.get("system_prompt") or "")
    if "expected" in system_prompt.lower() and "不得" not in system_prompt:
        # Never inject scoring labels; nothing to scrub otherwise.
        logger.warning("system prompt mentions 'expected' but no labels are injected")

    allowed = request.get("allowed_skill_ids")
    allowed_set = set(str(item) for item in allowed) if allowed is not None else None
    subskills = []
    for entry in manifest.get("subskills") or []:
        if not isinstance(entry, Mapping):
            continue
        skill_id = str(entry.get("skill_id") or "")
        if allowed_set is not None and skill_id not in allowed_set:
            continue
        subskills.append(entry)

    manifest_for_prompt = dict(manifest)
    if allowed_set is not None:
        manifest_for_prompt["subskills"] = subskills

    inject_main = loader.get("inject_main_skill", True) is not False
    index_mode = str(loader.get("subskill_index") or "manifest").lower()
    include_index = index_mode not in ("none", "off", "false", "disabled")
    system_parts = [system_prompt]
    if inject_main:
        system_parts.extend(["\n\n【主 SKILL.md】\n", main_text])
    if include_index:
        system_parts.extend(["\n\n【子技能清单】\n",
                             json.dumps(manifest_for_prompt, ensure_ascii=False)])
    if text_protocol:
        system_parts.append(TEXT_PROTOCOL_ADDENDUM)
    else:
        system_parts.append(NATIVE_PROTOCOL_ADDENDUM)

    mode = str(loader.get("mode") or loader.get("strategy") or "on_demand").lower()
    preload_ids = loader.get("preload_skill_ids")
    preload_all = mode in ("preload_all", "all", "preload", "full") or loader.get(
        "preload") is True
    preload_set = set(str(item) for item in preload_ids) if preload_ids else set()
    if preload_all or preload_set:
        preloaded: list[str] = []
        for entry in subskills:
            skill_id = str(entry.get("skill_id") or "")
            if not preload_all and skill_id not in preload_set:
                continue
            path = entry.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            text = _safe_read(snapshot, path)
            preloaded.append(f"\n\n【子技能内容 {path}】\n" + text)
            if loaded_files is not None:
                loaded_files[path] = text
        system_parts.extend(preloaded)

    user_parts = ["用户请求：" + str(request.get("query") or "")]
    context = request.get("context")
    if context not in (None, {}, []):
        user_parts.append("\n上下文：" + _canonical_json(context))
    messages = [
        {"role": "system", "content": "".join(system_parts)},
        {"role": "user", "content": "".join(user_parts)},
    ]

    max_chars = int(limits.get("max_context_chars") or 0)
    total_chars = sum(_message_context_chars(message) for message in messages)
    if max_chars and total_chars > max_chars:
        overflow = str(limits.get("context_overflow") or "error").lower()
        strategy = str(loader.get("overflow_strategy") or "index_only").lower()
        if overflow == "frozen_strategy" and strategy in _OVERFLOW_STRATEGIES:
            logger.info("context overflow (%s > %s): applying frozen strategy %s",
                        total_chars, max_chars, strategy)
            loader = dict(loader)
            loader["mode"] = "index_only"
            loader["preload"] = False
            loader["preload_skill_ids"] = []
            request_for_retry = dict(request)
            if strategy == "drop_context":
                request_for_retry["context"] = None
            retry_limits = dict(limits)
            retry_limits["context_overflow"] = "error"
            return build_messages(request_for_retry, loader_config=loader,
                                  limits=retry_limits, text_protocol=text_protocol,
                                  loaded_files=loaded_files)
        raise _Failure(
            "context_overflow",
            f"上下文超过 {max_chars} 字符上限（当前 {total_chars}），"
            "加载策略未允许静默截断")
    return messages


# --------------------------------------------------------------------------- #
# executor
# --------------------------------------------------------------------------- #
class _CaseRunner:
    def __init__(self, case_request: Mapping[str, Any], adapter: ModelAdapter,
                 emit: Callable[..., Any] | None):
        if not isinstance(case_request, Mapping):
            raise AppError("case_request 必须是对象", code="config_error",
                           status_code=422)
        self.request: dict[str, Any] = _scrub(dict(case_request))
        self.adapter = adapter
        self.sink, self.events = collect_events(emit)
        self.run_id = str(self.request.get("run_id") or "")
        self.case_id = str(self.request.get("case_id") or "")
        self.stage = self.request.get("stage")
        self.split = self.request.get("split")
        self.variant = self.request.get("variant")
        self.repeat_index = self.request.get("repeat_index")
        self.attempt = self.request.get("attempt")
        self.limits = {**DEFAULT_LIMITS, **(self.request.get("limits") or {})}
        self.model_config: dict[str, Any] = dict(self.request.get("model_config") or {})
        self.request_template: dict[str, Any] = _scrub(
            dict(self.request.get("request_template") or {}))
        self.fixtures = _FixtureHandle(self.request.get("fixtures") or [])
        self.capabilities = normalize_capabilities(
            getattr(adapter, "capabilities", {}) or {})
        self.cancel_check = self.request.get("cancel_check")
        self.aliases: dict[str, str] = {}
        manifest = self.request.get("manifest") or {}
        for entry in manifest.get("subskills") or []:
            if isinstance(entry, Mapping) and entry.get("name") and entry.get("skill_id"):
                self.aliases[str(entry["name"])] = str(entry["skill_id"])
        explicit_aliases = (self.request.get("skill_aliases")
                             or self.model_config.get("skill_aliases") or {})
        if isinstance(explicit_aliases, Mapping):
            self.aliases.update({str(k): str(v) for k, v in explicit_aliases.items()})

        self.tool_protocol = str(
            self.request.get("tool_protocol")
            or self.model_config.get("tool_protocol")
            or ("native" if self.capabilities.get("native_tools") else "text")).lower()
        self.obs = ObsToolRuntime(
            skill_snapshot_path=str(self.request.get("skill_snapshot_path") or ""),
            manifest=manifest,
            allowed_skill_ids=self.request.get("allowed_skill_ids"),
            emit=self._adapter_emit,
            protocol_mode="native" if self.tool_protocol == "native" else "text",
            aliases=self.aliases,
        )
        self.messages: list[dict[str, Any]] = []
        self.model_calls = 0
        self.step_count = 0
        self.finish_reason: str | None = None
        # finish is a protocol signal.  By default it is advisory so that
        # business-tool correctness can be scored independently.
        self.finish_protocol = "missing"
        self.inferred_action: str | None = None
        self.last_content = ""
        self.usage_parts: list[dict[str, int] | None] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.fixture_misses: list[dict[str, Any]] = []
        self.result_source = ("native_tool"
                              if self.tool_protocol == "native"
                              and self.capabilities.get("native_tools") else "text_parse")
        self.started = time.monotonic()
        self.deadline = self.started + float(self.limits.get("case_timeout_s") or 120)
        self._call_seen: set[str] = set()
        self._current_call_id: str | None = None

    # -- event helpers ----------------------------------------------------- #
    def _emit(self, event_type: str, *, payload: Mapping[str, Any] | None = None,
              level: str = "info", call_id: str | None = None,
              attempt: int | None = None) -> dict[str, Any]:
        return emit_event(
            self.sink, event_type, level=level, payload=dict(payload or {}),
            run_id=self.run_id or None, case_id=self.case_id or None,
            stage=self.stage, split=self.split, variant=self.variant,
            repeat_index=self.repeat_index,
            attempt=attempt if attempt is not None else self.attempt,
            call_id=call_id,
        )

    def _adapter_emit(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Child sink handed to adapters: stamps case fields, tracking lifecycle."""
        event_type = str(event.get("type") or "")
        self._call_seen.add(event_type)
        payload = event.get("payload")
        return self._emit(
            event_type,
            payload=dict(payload) if isinstance(payload, Mapping) else {},
            level=str(event.get("level") or "info"),
            call_id=event.get("call_id") or self._current_call_id,
            attempt=event.get("attempt"),
        )

    def _adapter_model_name(self) -> str:
        value = getattr(self.adapter, "model_name", None)
        if callable(value):
            try:
                value = value()
            except Exception:  # noqa: BLE001 - display helper only
                value = None
        if value:
            return str(value)
        fallback = (self.model_config.get("model")
                    or getattr(self.adapter, "model", None) or "")
        return str(fallback)

    def _ensure_model_events(self, call_id: str, status: str, *,
                             invoked: Mapping[str, Any] | None = None,
                             error: AdapterError | None = None) -> None:
        """Synthesize lifecycle events for adapters that do not emit them."""
        if event_types.MODEL_CALL_STARTED not in self._call_seen:
            self._emit(
                event_types.MODEL_CALL_STARTED, call_id=call_id,
                payload={"provider": getattr(self.adapter, "name", "unknown"),
                         "synthetic": True,
                         "model": self._adapter_model_name(),
                         "message_count": len(self.messages),
                         "tool_count": len(self.tools_for_call())})
        if status == "ok" and event_types.MODEL_CALL_FINISHED not in self._call_seen:
            invoked = invoked or {}
            captured = capture_model_response(invoked, self.model_config)
            self._emit(
                event_types.MODEL_CALL_FINISHED, call_id=call_id,
                payload={"provider": getattr(self.adapter, "name", "unknown"),
                         "synthetic": True,
                         "finish_reason": invoked.get("finish_reason"),
                         "usage": invoked.get("usage"),
                         "tool_call_count": len(invoked.get("tool_calls") or []),
                         "response": captured,
                         "response_summary": response_summary(captured)})
        if status == "error" and event_types.MODEL_CALL_FAILED not in self._call_seen:
            error = error or AdapterError("模型调用失败")
            self._emit(
                event_types.MODEL_CALL_FAILED, level="error", call_id=call_id,
                payload={"provider": getattr(self.adapter, "name", "unknown"),
                         "synthetic": True,
                         "error": {"code": error.code, "kind": error.error_kind,
                                   "message": error.message},
                         "issue": event_types.issue(
                             error.code, "模型调用失败", error.message,
                             impact="该案例无法获得完整结果。",
                             next_action="检查模型连接配置或稍后重试。")})

    # -- checks ------------------------------------------------------------ #
    def _cancel_requested(self) -> bool:
        callback = self.cancel_check
        if not callable(callback):
            return False
        try:
            return bool(callback())
        except Exception:  # noqa: BLE001 - a broken cancel probe must not crash a case
            logger.exception("cancel_check failed")
            return False

    def _check_cancel(self) -> None:
        if self._cancel_requested():
            raise _Failure("cancelled", "案例执行已取消")

    def _check_timeout(self) -> None:
        if time.monotonic() >= self.deadline:
            raise _Failure("timeout", "案例执行超时（case_timeout_s）")

    def _count_step(self, what: str) -> None:
        self.step_count += 1
        if self.step_count > int(self.limits.get("max_steps") or 12):
            raise _Failure(
                "steps_exhausted",
                f"工具执行步数超过上限 {self.limits.get('max_steps')}（{what}）")

    # -- tools for one call ------------------------------------------------ #
    def tools_for_call(self) -> list[dict[str, Any]]:
        if self.tool_protocol != "native" or not self.capabilities.get("native_tools"):
            return []
        # Business tools are declared by the loaded Skill, so the environment
        # does not carry a second tool registry.  Native providers still need
        # one stable callable shape; the model supplies the actual business
        # name and arguments in the call payload.
        business = {
            "type": "function",
            "function": {
                "name": "call_tool",
                "description": "调用子技能中定义的业务工具。name 填工具名，arguments 填工具参数。",
                "parameters": {
                    "type": "object",
                    "required": ["name", "arguments"],
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "arguments": {"type": "object"},
                    },
                },
            },
        }
        return [business, *self.obs.tool_definitions()]

    def _invoke_config(self) -> dict[str, Any]:
        config = dict(self.model_config)
        # A case-level request template overrides the environment one key by key;
        # when absent the adapter keeps its own frozen template (gateway fields
        # such as api_key/user/extra_body are never dropped here).
        if self.request_template:
            template = dict(self.model_config.get("request_template") or {})
            template.update(self.request_template)
            config["request_template"] = template
        config["cancel_check"] = self._cancel_requested
        config["call_id"] = self._current_call_id
        config["deadline_monotonic"] = min(
            self.deadline, float(config.get("deadline_monotonic") or self.deadline))
        config.setdefault("provider", getattr(self.adapter, "name", "unknown"))
        return config

    # -- run --------------------------------------------------------------- #
    def run(self) -> dict[str, Any]:
        try:
            if not self.request.get("skill_snapshot_path"):
                raise _Failure("platform_error", "缺少 skill_snapshot_path")
            loaded_files: dict[str, str] = {}
            self.messages = build_messages(
                self.request,
                loader_config=self.request.get("loader_config") or {},
                limits=self.limits,
                text_protocol=self.tool_protocol != "native",
                loaded_files=loaded_files)
            for path, content in loaded_files.items():
                self.obs.prime_loaded_file(path, content)
            return self._run_model_loop()
        except _Failure as failure:
            return self._failure_result(failure)
        except AdapterError as exc:
            return self._failure_result(_Failure(exc.code, exc.message,
                                                 details=exc.details))
        except ObsToolError as exc:
            return self._failure_result(_Failure(exc.code, exc.message,
                                                 details=exc.details))
        except AppError as exc:
            return self._failure_result(_Failure(
                exc.code if exc.code in event_types.CASE_ERROR_CODES else "platform_error",
                exc.message, details=exc.details))
        except Exception as exc:  # noqa: BLE001 - never leak an executor crash
            logger.exception("run_case crashed case=%s", self.case_id)
            return self._failure_result(_Failure(
                "platform_error", f"执行器内部错误: {type(exc).__name__}: {exc}"))

    # -- model loop -------------------------------------------------------- #
    def _run_model_loop(self) -> dict[str, Any]:
        while True:
            self._check_cancel()
            self._check_timeout()
            max_calls = int(self.limits.get("max_model_calls") or 12)
            if self.model_calls >= max_calls:
                raise _Failure("steps_exhausted",
                               f"模型调用次数达到上限 {max_calls}，案例未完成")
            result = self._model_turn()
            if result is not None:
                return result

    def _model_turn(self) -> dict[str, Any] | None:
        self._check_context_limit()
        self.model_calls += 1
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        self._current_call_id = call_id
        self._call_seen = set()
        tools = self.tools_for_call()
        invoke_config = self._invoke_config()
        try:
            invoked = self.adapter.invoke(self.messages, invoke_config, tools,
                                          emit=self._adapter_emit)
        except AdapterError as exc:
            self._ensure_model_events(call_id, "error", error=exc)
            if exc.code == "cancelled":
                raise _Failure("cancelled", exc.message) from exc
            raise _Failure(exc.code, exc.message, details=exc.details) from exc
        except ObsToolError as exc:
            self._ensure_model_events(call_id, "error")
            raise _Failure(exc.code, exc.message, details=exc.details) from exc
        except Exception as exc:  # noqa: BLE001 - adapter bug becomes infra failure
            logger.exception("adapter invoke crashed case=%s", self.case_id)
            error = AdapterError(f"模型适配器异常: {type(exc).__name__}: {exc}",
                                 code="platform_error", retryable=False)
            self._ensure_model_events(call_id, "error", error=error)
            raise _Failure("platform_error", error.message) from exc
        if not isinstance(invoked, Mapping):
            self._ensure_model_events(call_id, "ok", invoked={})
            raise _Failure("format_error", "模型适配器返回值必须是对象")
        self._ensure_model_events(call_id, "ok", invoked=invoked)

        self.usage_parts.append(normalize_usage(invoked.get("usage")))
        content = invoked.get("content")
        content = content if isinstance(content, str) else ""
        self.last_content = content or self.last_content
        finish_reason = invoked.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            self.finish_reason = finish_reason
        raw_tool_calls = invoked.get("tool_calls") or []
        if not isinstance(raw_tool_calls, (list, tuple)):
            raise _Failure("format_error", "模型返回的 tool_calls 必须是数组")
        tool_calls = self._normalize_tool_calls(raw_tool_calls)
        adapter_source = invoked.get("result_source")
        if adapter_source in ("native_tool", "text_parse", "runtime_event"):
            self.result_source = adapter_source
        elif tool_calls and self.capabilities.get("native_tools"):
            self.result_source = "native_tool"

        self._check_timeout()
        if tool_calls:
            self._dispatch_native_batch(tool_calls, content, call_id)
            if self.obs.is_finished():
                self._raise_for_finish_reason()
                self.finish_protocol = "ok"
                return self._success()
            self._raise_for_finish_reason()
            return None

        # No structured tool calls: follow the frozen protocol for this adapter.
        if self.tool_protocol == "native" and self.capabilities.get("native_tools"):
            self._raise_for_finish_reason()
            if bool((self.request.get("loader_config") or {}).get("strict_finish")):
                raise _Failure("protocol_error", "模型未通过 finish 工具结束案例")
            # A normal terminal response is still useful evidence.  Infer the
            # action only when business calls were observed; otherwise leave it
            # indeterminate instead of silently passing a no-action case.
            if self.tool_calls:
                action = "tool_call"
                self.finish_protocol = "missing"
                return self._build_result(
                    action=action,
                    selected_skills=self.obs.result()["selected_skills"],
                    tool_calls=self.tool_calls,
                    missing_fields=[], raw_output=self.last_content,
                )
            self.finish_protocol = "missing_undetermined"
            return self._build_result(
                action="undetermined",
                selected_skills=self.obs.result()["selected_skills"],
                tool_calls=[], missing_fields=[], raw_output=self.last_content,
            )
        self._raise_for_finish_reason()
        self._handle_text_response(content, call_id)
        if self.obs.is_finished():
            return self._success()
        return None

    def _check_context_limit(self) -> None:
        """Reject a growing runtime transcript before sending it to the model.

        Initial loading may use the frozen overflow strategy in ``build_messages``.
        Once the loop has started, silently dropping tool history would make the
        simulated conversation non-causal, so runtime growth is an explicit error.
        """
        max_chars = int(self.limits.get("max_context_chars") or 0)
        if not max_chars:
            return
        total_chars = sum(_message_context_chars(message) for message in self.messages)
        if total_chars > max_chars:
            raise _Failure(
                "context_overflow",
                f"运行中上下文超过 {max_chars} 字符上限（当前 {total_chars}），"
                "不会静默截断工具历史",
                details={"max_context_chars": max_chars,
                         "actual_chars": total_chars,
                         "phase": "runtime"},
            )

    def _normalize_tool_calls(self, raw_calls: Sequence[Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_calls):
            if not isinstance(raw, Mapping):
                raise _Failure("format_error", "模型返回的工具调用必须是对象")
            name = str(raw.get("name") or "").strip()
            if not name:
                raise _Failure("format_error", "模型返回的工具调用缺少名称")
            try:
                arguments = parse_tool_arguments(raw.get("arguments"))
            except AdapterError as exc:
                raise _Failure(exc.code, exc.message, details=exc.details) from exc
            calls.append({"id": str(raw.get("id") or f"call_{index}"),
                          "name": name, "arguments": arguments})
        return calls

    def _raise_for_finish_reason(self) -> None:
        reason = (self.finish_reason or "").lower()
        if reason in ("length", "max_tokens", "max_output_tokens"):
            raise _Failure("truncated", "模型输出被截断（finish_reason=length），"
                                        "不自动提高输出上限重跑")
        if reason in ("content_filter", "safety"):
            raise _Failure("refused", "模型因内容安全策略拒绝返回结果")

    # -- native tool dispatch ---------------------------------------------- #
    def _dispatch_native_batch(self, tool_calls: list[dict[str, Any]],
                               content: str, call_id: str) -> None:
        assistant_message: dict[str, Any] = {"role": "assistant", "content": content or ""}
        assistant_message["tool_calls"] = [
            {"id": call["id"], "type": "function",
             "function": {"name": call["name"],
                          "arguments": json.dumps(call["arguments"], ensure_ascii=False)}}
            for call in tool_calls
        ]
        self.messages.append(assistant_message)
        # A model batch is generated before any tool result is visible.  If it
        # contains a first-time loader call, every business/finish call in the
        # batch must wait for a new model turn, regardless of ordering.
        fresh_skill_loaded = any(
            call["name"] in ("select_skill", "read_skill")
            and self.obs.would_load_new_file(call["name"], call["arguments"])
            for call in tool_calls
        )
        for call in tool_calls:
            self._check_cancel()
            self._check_timeout()
            if self.obs.is_finished():
                self.messages.append({
                    "role": "tool", "tool_call_id": call["id"], "name": call["name"],
                    "content": json.dumps({"ok": False, "error": "案例已结束，未执行该调用"},
                                          ensure_ascii=False)})
                continue
            protocol_name = call["name"]
            name = protocol_name
            arguments = call["arguments"]
            wrapped_business = False
            if name == "call_tool":
                if set(arguments) != {"name", "arguments"}:
                    raise _Failure("format_error",
                                   "call_tool 只允许 name 与 arguments 字段")
                business_name = arguments.get("name")
                business_arguments = arguments.get("arguments")
                if not isinstance(business_name, str) or not business_name.strip():
                    raise _Failure("format_error", "call_tool 缺少业务工具名称")
                business_name = business_name.strip()
                if business_name == "call_tool" or business_name in _INTERNAL:
                    raise _Failure("protocol_error",
                                   f"call_tool 不能调用内部工具: {business_name}")
                if not isinstance(business_arguments, Mapping):
                    raise _Failure("format_error", "call_tool.arguments 必须是 JSON 对象")
                name = business_name
                arguments = dict(business_arguments)
                wrapped_business = True
            if fresh_skill_loaded and name not in ("select_skill", "read_skill"):
                self.messages.append({
                    "role": "tool", "tool_call_id": call["id"],
                    "name": protocol_name,
                    "content": json_dumps({
                        "ok": False,
                        "error": "skill_loaded_requires_new_model_turn",
                        "message": "子技能刚刚加载，需下一轮模型推理后再调用业务工具或 finish。",
                    }),
                })
                # Every call needs a reply, including skipped calls before the
                # loader. Nothing generated in this batch has seen its body.
                continue
            if name in _INTERNAL:
                self._count_step(name)
                try:
                    outcome = self.obs.handle(name, arguments, call_id=call_id)
                except ObsToolError as exc:
                    raise _Failure(exc.code, exc.message, details=exc.details) from exc
                tool_text = self._internal_tool_message(name, outcome)
                self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                      "name": name, "content": tool_text})
                continue
            if self.tool_protocol == "native" and not wrapped_business:
                raise _Failure("protocol_error",
                               f"原生协议必须通过 call_tool 调用业务工具: {name}")
            # All non-internal names come from the loaded Skill.  Fixtures are
            # the execution boundary; an unknown name fails there without
            # contacting a real service or consulting a hidden registry.
            self._execute_business_tool(name, arguments, call_id=call_id,
                                        tool_call_id=call["id"],
                                        message_name=protocol_name)

    @staticmethod
    def _internal_tool_message(name: str, outcome: Mapping[str, Any]) -> str:
        if name == "read_skill":
            if outcome.get("cached"):
                return json_dumps({"ok": True, "tool": "read_skill",
                                   "path": outcome.get("path"), "cached": True,
                                   "message": "技能正文已加载，未重复读取磁盘。"})
            path = outcome.get("path") or ""
            return f"【文件 {path}】\n{outcome.get('content') or ''}"
        if name == "select_skill":
            path = outcome.get("path") or ""
            if outcome.get("cached"):
                return json_dumps({"ok": True, "tool": "select_skill",
                                   "skill_id": outcome.get("skill_id"),
                                   "path": path, "cached": True,
                                   "message": "技能正文已在上下文中提供。"})
            return f"【文件 {path}】\n{outcome.get('content') or ''}"
        return json_dumps({"ok": True, "action": outcome.get("action")})

    def _execute_business_tool(self, name: str, arguments: Mapping[str, Any], *,
                               call_id: str, tool_call_id: str,
                               message_name: str | None = None) -> None:
        self._count_step(name)
        safe_arguments = redact(dict(arguments))
        started = time.monotonic()
        self._emit(event_types.TOOL_CALL_STARTED, call_id=call_id,
                   payload={"tool": name, "arguments": safe_arguments})
        hit = self.fixtures.resolve(name, dict(arguments))
        duration_ms = int((time.monotonic() - started) * 1000)
        if hit is None:
            miss = {"tool": name, "arguments": dict(arguments)}
            self.fixture_misses.append(miss)
            diagnosis = self.fixtures.diagnose_miss(name, arguments)
            self._emit(event_types.TOOL_CALL_FINISHED, level="warning", call_id=call_id,
                       payload={"tool": name, "ok": False, "duration_ms": duration_ms,
                                "error": "fixture_miss"})
            issue = event_types.issue(
                "fixture_miss", "工具调用没有匹配的模拟返回",
                _fixture_miss_message(name, diagnosis),
                impact="案例按能力失败计分；不会访问真实业务服务，也不会重试猜测。",
                next_action=_fixture_miss_next_action(diagnosis),
                technical_detail=redact({"tool": name, "arguments": safe_arguments,
                                         "fixture_diagnosis": diagnosis}))
            self._emit(event_types.FIXTURE_MISS, level="error", call_id=call_id,
                       payload={"tool": name, "arguments": safe_arguments, "issue": issue})
            raise _Failure("fixture_miss",
                           _fixture_miss_message(name, diagnosis) + "（未访问真实服务）")
        response = hit.get("response")
        try:
            payload_response = json.loads(json_dumps(response))
        except (TypeError, ValueError):
            payload_response = str(response)
        self._emit(event_types.TOOL_CALL_FINISHED, call_id=call_id,
                   payload={"tool": name, "ok": True, "duration_ms": duration_ms,
                            "fixture_index": hit.get("fixture_index")})
        self._emit(event_types.TOOL_RESULT, call_id=call_id,
                   payload={"tool": name, "arguments": safe_arguments,
                            "response": payload_response,
                            "fixture_index": hit.get("fixture_index"),
                            "consume": hit.get("consume")})
        self.tool_calls.append({"name": name, "arguments": dict(arguments)})
        self.messages.append({
            "role": "tool", "tool_call_id": tool_call_id,
            "name": message_name or name,
            "content": json_dumps(payload_response),
        })

    # -- text protocol ------------------------------------------------------ #
    def _handle_text_response(self, content: str, call_id: str) -> None:
        require_routing = bool(self.capabilities.get("routing", True))
        allow_missing_routing = bool(self.obs.selected_skills or self.tool_calls)
        try:
            parsed = text_parse.parse_text_output(
                content, self.capabilities,
                known_skill_ids=self.obs.known_skill_ids,
                aliases=self.aliases,
                known_tool_names=None,
                require_routing=require_routing,
                allow_missing_routing=allow_missing_routing)
        except text_parse.TextParseError as exc:
            raise _Failure("parse_failed", exc.message, details=exc.detail) from exc

        fresh_skill_loaded = False
        loaded_messages: list[str] = []
        for skill_id in parsed["skills"]:
            if skill_id not in self.obs.selected_skills:
                try:
                    outcome = self.obs.record_text_selection(
                        skill_id, basis=parsed["basis"], call_id=call_id)
                    if not outcome.get("cached"):
                        fresh_skill_loaded = True
                        loaded_messages.append(self._internal_tool_message(
                            "select_skill", outcome))
                except ObsToolError as exc:
                    raise _Failure(exc.code, exc.message, details=exc.details) from exc

        finish = parsed["finish"]
        business_calls = list(parsed["tool_calls"])
        if fresh_skill_loaded:
            self.messages.append({"role": "assistant", "content": content})
            self.messages.append({
                "role": "user",
                "content": "\n\n".join(loaded_messages) +
                "\n\n【加载结果】正文已加载。本轮工具调用和结束声明未执行；"
                "请根据正文，在下一轮重新输出所需的工具调用或结束声明。",
            })
            self.result_source = "text_parse"
            return
        if finish is not None:
            declared = finish.get("selected_skills")
            if declared is not None and set(declared) != set(self.obs.selected_skills):
                raise _Failure("protocol_error",
                               "文本协议 finish 的选择摘要与运行时事件不一致")
            action = finish["action"]
            if business_calls and action != "tool_call":
                raise _Failure("protocol_error",
                               "文本协议中 finish.action 与实际工具调用不一致")
            try:
                self.obs.finish({
                    "action": action,
                    "selected_skills": list(self.obs.selected_skills),
                    "missing_fields": finish.get("missing_fields", []),
                    "summary": finish.get("summary", ""),
                }, call_id=call_id)
            except ObsToolError as exc:
                raise _Failure(exc.code, exc.message, details=exc.details) from exc
            self.finish_protocol = "ok"
        elif not business_calls:
            if bool((self.request.get("loader_config") or {}).get("strict_finish")):
                raise _Failure("protocol_error", "模型未通过 finish 协议结束案例")
            self.finish_protocol = "missing"
            # Routing-only / plain text result: action inferred from observation.
            action = "tool_call" if self.tool_calls else "no_action"
            if not self.tool_calls:
                self.inferred_action = "undetermined"
                self.finish_protocol = "missing_undetermined"
            try:
                self.obs.finish({
                    "action": action,
                    "selected_skills": list(self.obs.selected_skills),
                    "missing_fields": [],
                    "summary": "",
                }, call_id=call_id)
            except ObsToolError as exc:
                raise _Failure(exc.code, exc.message, details=exc.details) from exc
            return

        if not business_calls:
            return
        self.messages.append({"role": "assistant", "content": content})
        results: list[dict[str, Any]] = []
        for index, call in enumerate(business_calls):
            self._check_cancel()
            self._check_timeout()
            self._execute_business_text(call["name"], call["arguments"],
                                        call_id=call_id, index=index,
                                        results=results)
        if finish is None:
            self.messages.append({
                "role": "user",
                "content": "【工具结果】\n" + _canonical_json(results),
            })
        self.result_source = "text_parse"

    def _execute_business_text(self, name: str, arguments: Mapping[str, Any], *,
                               call_id: str, index: int,
                               results: list[dict[str, Any]]) -> None:
        """Text protocol has no native tool ids; execute + collect results."""
        self._count_step(name)
        safe_arguments = redact(dict(arguments))
        started = time.monotonic()
        self._emit(event_types.TOOL_CALL_STARTED, call_id=call_id,
                   payload={"tool": name, "arguments": safe_arguments,
                            "source": "text_parse"})
        hit = self.fixtures.resolve(name, dict(arguments))
        duration_ms = int((time.monotonic() - started) * 1000)
        if hit is None:
            miss = {"tool": name, "arguments": dict(arguments)}
            self.fixture_misses.append(miss)
            diagnosis = self.fixtures.diagnose_miss(name, arguments)
            self._emit(event_types.TOOL_CALL_FINISHED, level="warning", call_id=call_id,
                       payload={"tool": name, "ok": False,
                                "duration_ms": duration_ms,
                                "error": "fixture_miss"})
            issue = event_types.issue(
                "fixture_miss", "工具调用没有匹配的模拟返回",
                _fixture_miss_message(name, diagnosis),
                impact="案例按能力失败计分；不会访问真实业务服务。",
                next_action=_fixture_miss_next_action(diagnosis),
                technical_detail=redact({"tool": name, "arguments": safe_arguments,
                                         "fixture_diagnosis": diagnosis}))
            self._emit(event_types.FIXTURE_MISS, level="error", call_id=call_id,
                       payload={"tool": name, "arguments": safe_arguments, "issue": issue})
            raise _Failure("fixture_miss",
                           _fixture_miss_message(name, diagnosis) + "（未访问真实服务）")
        response = hit.get("response")
        self._emit(event_types.TOOL_CALL_FINISHED, call_id=call_id,
                   payload={"tool": name, "ok": True, "duration_ms": duration_ms,
                            "fixture_index": hit.get("fixture_index")})
        self._emit(event_types.TOOL_RESULT, call_id=call_id,
                   payload={"tool": name, "arguments": safe_arguments,
                            "response": response,
                            "fixture_index": hit.get("fixture_index"),
                            "consume": hit.get("consume")})
        self.tool_calls.append({"name": name, "arguments": dict(arguments)})
        results.append({"index": index, "tool": name, "arguments": dict(arguments),
                        "response": response})

    # -- results ------------------------------------------------------------ #
    def _route_events(self) -> list[dict[str, Any]]:
        route_events: list[dict[str, Any]] = []
        for index, event in enumerate(self.events):
            if event.get("type") != event_types.SKILL_SELECTED:
                continue
            payload = event.get("payload") or {}
            skill_id = payload.get("skill_id")
            if not skill_id:
                continue
            seq = event.get("seq")
            route_events.append({
                "skill_id": str(skill_id),
                "seq": seq if isinstance(seq, int) else index,
            })
        if route_events:
            return route_events
        for index, entry in enumerate(self.obs.route_events or []):
            if not isinstance(entry, Mapping) or not entry.get("skill_id"):
                continue
            seq = entry.get("seq")
            route_events.append({
                "skill_id": str(entry["skill_id"]),
                "seq": seq if isinstance(seq, int) else index,
            })
        return route_events

    def _success(self, *, action: str | None = None,
                 selected_skills: list[str] | None = None,
                 tool_calls: list[dict[str, Any]] | None = None,
                 missing_fields: list[str] | None = None,
                 raw_output: str | None = None) -> dict[str, Any]:
        obs_result = self.obs.result()
        return self._build_result(
            action=action if action is not None else (self.inferred_action or obs_result["action"]),
            selected_skills=selected_skills if selected_skills is not None
            else obs_result["selected_skills"],
            tool_calls=tool_calls if tool_calls is not None else self.tool_calls,
            missing_fields=missing_fields if missing_fields is not None
            else obs_result["missing_fields"],
            raw_output=raw_output if raw_output is not None else self.last_content,
        )

    def _failure_result(self, failure: _Failure) -> dict[str, Any]:
        error: dict[str, Any] = {"code": failure.code, "message": failure.message}
        if failure.details is not None:
            try:
                error["details"] = json.loads(json_dumps(failure.details))
            except (TypeError, ValueError):
                error["details"] = str(failure.details)
        obs_result = self.obs.result()
        if failure.code == "protocol_error":
            self.finish_protocol = "error"
        return self._build_result(
            action=obs_result["action"] if self.obs.finished else ("tool_call" if self.tool_calls else "no_action"),
            selected_skills=obs_result["selected_skills"],
            tool_calls=list(self.tool_calls),
            missing_fields=obs_result["missing_fields"],
            raw_output=self.last_content,
            error=error,
            error_kind=failure.kind,
        )

    def _build_result(self, *, action: str, selected_skills: list[str],
                      tool_calls: list[dict[str, Any]],
                      missing_fields: list[str], raw_output: str,
                      error: dict[str, Any] | None = None,
                      error_kind: str | None = None) -> dict[str, Any]:
        duration_ms = int((time.monotonic() - self.started) * 1000)
        return {
            "selected_skills": list(selected_skills),
            "route_events": self._route_events(),
            "action": action,
            "tool_calls": [{"name": call["name"], "arguments": dict(call.get("arguments") or {})}
                           for call in tool_calls if isinstance(call, Mapping)],
            "missing_fields": list(missing_fields),
            "error": error,
            "error_kind": error_kind,
            "finish_reason": self.finish_reason,
            "finish_protocol": self.finish_protocol,
            "usage": sum_usage(self.usage_parts),
            "duration_ms": duration_ms,
            "result_source": self.result_source,
            "capabilities": dict(self.capabilities),
            "events": list(self.events),
            "fixture_misses": list(self.fixture_misses),
            "raw_output": raw_output or "",
            "model_calls": self.model_calls,
        }


def run_case(case_request: Mapping[str, Any], adapter: ModelAdapter,
             emit: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Execute one case; see module docstring and contract §2.3 for the result.

    ``emit`` receives one event mapping per event (``persistence.append_event``
    parameter names, without ``seq``); the returned ``events`` list contains the
    same events in execution order.
    """
    runner = _CaseRunner(case_request, adapter, emit)
    return runner.run()
