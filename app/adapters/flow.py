"""Flow-level adapter: call a user supplied Python ``run_case`` (§37/§40).

A flow adapter owns routing and sub-skill execution; the platform passes the
frozen package snapshot, the frozen system prompt and the case inputs and
receives normalized observations. ``expected`` / scoring labels are never placed
in the request. The returned result must report really observed routes and tool
arguments (``result_source="runtime_event"``); the platform only normalizes and
validates the structure, it never guesses missing values.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import logging
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ..errors import AppError
from .base import (
    AdapterError,
    FlowAdapterBase,
    default_capabilities,
    emit_event,
    normalize_capabilities,
    normalize_usage,
)

logger = logging.getLogger(__name__)

__all__ = ["FlowAdapter", "load_flow_entry"]

#: Keys that must never be forwarded to user flow code.
_FORBIDDEN_REQUEST_KEYS = frozenset({
    "expected", "expected_output", "scoring", "score", "labels", "rubric",
    "answer", "answers", "ground_truth", "golden",
})

_ACTION_VALUES = ("tool_call", "clarify", "no_action")
_INTERNAL_TOOL_NAMES = frozenset({"select_skill", "read_skill", "finish"})


def load_flow_entry(reference: Any, config: Mapping[str, Any] | None = None) -> Callable[..., Any]:
    """Resolve a flow ``run_case`` callable from config.

    ``reference`` may be a callable (tests/demos), a dotted module path
    (``my_flows.task_query`` with a ``run_case`` attribute) or a ``.py`` file path
    resolved relative to ``config["base_dir"]``. Arbitrary code execution is
    limited to the trusted environment code version (§42).
    """
    if callable(reference) and not isinstance(reference, str):
        return reference
    if not isinstance(reference, str) or not reference.strip():
        raise AppError("流程级适配器未配置 Python 入口", code="config_error",
                       status_code=422, field_path="model_config.entry")
    target = reference.strip()
    attribute = "run_case"
    if ":" in target:
        target, attribute = target.rsplit(":", 1)
    if target.endswith(".py") or "/" in target or "\\" in target:
        path = Path(target)
        if not path.is_absolute():
            base_dir = Path(str((config or {}).get("base_dir") or ".")).resolve()
            path = (base_dir / path).resolve()
        if not path.is_file():
            raise AppError(f"流程入口文件不存在: {path}", code="config_error",
                           status_code=422, field_path="model_config.entry")
        module_name = f"skillopt_flow_{abs(hash(str(path)))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise AppError(f"无法加载流程入口: {path}", code="config_error",
                           status_code=422)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(target)
        except ImportError as exc:
            raise AppError(f"无法导入流程模块: {target}", code="config_error",
                           status_code=422, details={"error": str(exc)}) from exc
    entry = getattr(module, attribute, None)
    if not callable(entry):
        raise AppError(f"流程模块缺少可调用的 {attribute}: {target}",
                       code="config_error", status_code=422)
    return entry


class FlowAdapter(FlowAdapterBase):
    """Wraps a user flow as a case-level adapter."""

    name = "flow"
    capabilities = default_capabilities(
        routing=True, tool_arguments=True, native_tools=False, usage=True,
        streaming=False)

    def __init__(self, model_config: Mapping[str, Any] | None = None):
        super().__init__(model_config)
        config = self.model_config
        reference = (config.get("run_case") or config.get("runner")
                     or config.get("case_runner") or config.get("entry")
                     or config.get("python_entry"))
        self._entry: Callable[..., Any] | None = None
        self._entry_error: Exception | None = None
        if reference is not None:
            try:
                self._entry = load_flow_entry(reference, config)
            except (AppError, ImportError) as exc:  # surfaced on first use
                self._entry_error = exc
        declared = config.get("capabilities")
        if isinstance(declared, Mapping):
            base = dict(self.capabilities)
            base["native_tools"] = False
            self.capabilities = normalize_capabilities(declared, base=base)

    # -- ModelAdapter API -------------------------------------------------- #
    def invoke(self, messages: list[dict[str, Any]], config: dict[str, Any],
               tools: list[dict[str, Any]], *,
               emit: Callable[..., Any] | None = None) -> dict[str, Any]:
        raise AdapterError("流程级适配器不提供单次模型调用，请通过 run_case 使用",
                           code="platform_error", retryable=False)

    # -- case-level API ---------------------------------------------------- #
    def invoke_case(self, case_request: Mapping[str, Any], *,
                    emit: Callable[..., Any] | None = None,
                    cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
        if self._entry is None:
            if self._entry_error is not None:
                raise AdapterError(str(self._entry_error), code="platform_error",
                                   retryable=False) from self._entry_error
            raise AdapterError("流程级适配器未配置 Python 入口", code="platform_error",
                               retryable=False)
        request = self._build_flow_request(case_request)
        if cancel_check is not None and cancel_check():
            raise AdapterError("流程执行已取消", code="cancelled", retryable=False)
        started = time.monotonic()
        try:
            raw_result = self._call_entry(request, emit=emit)
        except AdapterError:
            raise
        except Exception as exc:  # user flow crash = infrastructure failure
            raise AdapterError(f"流程执行失败: {type(exc).__name__}: {exc}",
                               code="platform_error", retryable=False) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        if cancel_check is not None and cancel_check():
            raise AdapterError("流程执行已取消", code="cancelled", retryable=False)
        result = self._normalize_result(raw_result, duration_ms)
        self._forward_events(result.get("events") or [], emit=emit)
        return result

    # -- internals --------------------------------------------------------- #
    def _build_flow_request(self, case_request: Mapping[str, Any]) -> dict[str, Any]:
        request: dict[str, Any] = {}
        for key, value in dict(case_request).items():
            if key in _FORBIDDEN_REQUEST_KEYS:
                continue
            if key == "cancel_check":
                continue
            request[key] = value
        # The flow must execute against the frozen snapshot, never an external one.
        snapshot = request.get("skill_snapshot_path")
        if snapshot is not None:
            request["skill_snapshot_path"] = str(snapshot)
        request["protocol_version"] = "flow-v1"
        return request

    def _call_entry(self, request: Mapping[str, Any],
                    *, emit: Callable[..., Any] | None) -> Any:
        assert self._entry is not None
        entry = self._entry
        kwargs: dict[str, Any] = {}
        try:
            signature = inspect.signature(entry)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            params = signature.parameters
            if "emit" in params:
                kwargs["emit"] = emit
        value = entry(dict(request), **kwargs)
        if inspect.isawaitable(value):
            value = asyncio.run(value)
        return value

    def _normalize_result(self, raw_result: Any, duration_ms: int) -> dict[str, Any]:
        if not isinstance(raw_result, Mapping):
            raise AdapterError("流程 run_case 返回值必须是对象", code="protocol_error",
                               retryable=False)
        selected_skills_raw = raw_result.get("selected_skills") or []
        if not isinstance(selected_skills_raw, (list, tuple)):
            raise AdapterError("流程结果的 selected_skills 必须是数组",
                               code="protocol_error", retryable=False)
        selected_skills: list[str] = []
        for item in selected_skills_raw:
            name = str(item)
            if name and name not in selected_skills:
                selected_skills.append(name)

        tool_calls_raw = raw_result.get("tool_calls") or []
        if not isinstance(tool_calls_raw, (list, tuple)):
            raise AdapterError("流程结果的 tool_calls 必须是数组",
                               code="protocol_error", retryable=False)
        tool_calls: list[dict[str, Any]] = []
        for index, call in enumerate(tool_calls_raw):
            if not isinstance(call, Mapping):
                raise AdapterError("流程结果的工具调用必须是对象",
                                   code="protocol_error", retryable=False)
            name = str(call.get("name") or "").strip()
            if not name:
                raise AdapterError("流程结果的工具调用缺少名称",
                                   code="protocol_error", retryable=False)
            arguments = call.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, Mapping):
                raise AdapterError(f"流程结果工具参数必须是对象: {name}",
                                   code="protocol_error", retryable=False)
            if name in _INTERNAL_TOOL_NAMES:
                continue  # internal tools never count as business tools
            tool_calls.append({
                "name": name,
                "arguments": dict(arguments),
                "call_id": str(call.get("call_id") or call.get("id") or f"flow_call_{index}"),
            })

        action = raw_result.get("action")
        if action is None:
            action = "tool_call" if tool_calls else "no_action"
        if action not in _ACTION_VALUES:
            raise AdapterError(f"流程结果的 action 非法: {action!r}",
                               code="protocol_error", retryable=False)

        missing_fields = raw_result.get("missing_fields") or []
        if not isinstance(missing_fields, (list, tuple)):
            raise AdapterError("流程结果的 missing_fields 必须是数组",
                               code="protocol_error", retryable=False)

        events: list[dict[str, Any]] = []
        for raw_event in raw_result.get("events") or []:
            if isinstance(raw_event, Mapping) and isinstance(raw_event.get("type"), str):
                events.append(dict(raw_event))

        error = raw_result.get("error")
        if error is not None and not isinstance(error, Mapping):
            error = {"code": "protocol_error", "message": str(error)}

        route_events: list[dict[str, Any]] = []
        for raw_route in raw_result.get("route_events") or []:
            if isinstance(raw_route, Mapping) and raw_route.get("skill_id"):
                route_events.append({str(k): v for k, v in raw_route.items()
                                     if k in ("skill_id", "seq", "call_id")})

        result_source = raw_result.get("result_source") or "runtime_event"
        if result_source not in ("native_tool", "text_parse", "runtime_event"):
            result_source = "runtime_event"
        capabilities = normalize_capabilities(raw_result.get("capabilities"),
                                              base=self.capabilities)
        result: dict[str, Any] = {
            "selected_skills": selected_skills,
            "route_events": route_events,
            "action": action,
            "tool_calls": tool_calls,
            "missing_fields": [str(item) for item in missing_fields],
            "error": dict(error) if isinstance(error, Mapping) else None,
            "finish_reason": raw_result.get("finish_reason"),
            "usage": normalize_usage(raw_result.get("usage")),
            "duration_ms": int(raw_result.get("duration_ms") or duration_ms),
            "result_source": result_source,
            "capabilities": capabilities,
            "events": events,
            "raw_output": str(raw_result.get("raw_output") or ""),
            "fixture_misses": [],
        }
        return result

    def _forward_events(self, events: list[dict[str, Any]],
                        *, emit: Callable[..., Any] | None) -> None:
        """Re-emit observed flow events through the pipeline sink (seq owner)."""
        if emit is None:
            return
        for event in events:
            event_type = event.get("type")
            if not isinstance(event_type, str):
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                payload = {}
            emit_event(
                emit, event_type,
                level=str(event.get("level") or "info"),
                payload=dict(payload),
                call_id=event.get("call_id"),
                stage=event.get("stage"),
                case_id=event.get("case_id"),
                attempt=event.get("attempt"),
            )
