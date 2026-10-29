"""Optimizer protocol: prompt, output parsing, heuristic fallback, candidate flow.

Contract §2.7 / §19 / §33 / §49.  The optimizer reads *only* optimize-split
failure evidence and may only patch existing Markdown files inside
``allowed_paths``.  ``generate_candidate`` is the orchestration entry point used
by ``app/single_round.py``:

    optimizer_input_built → optimizer_call_started → optimizer_call_finished
    → optimizer_output_parsed → patch_created

Failure paths always record the *direct* reason first (``optimizer_not_configured``
/ ``optimizer_call_failed`` / ``optimizer_empty_response`` /
``optimizer_parse_failed`` / ``optimizer_no_change``) and only then
``fallback_started`` → ``patch_created`` / ``fallback_no_change``.  ``no_patch`` is
never used in place of the direct cause.

The heuristic fallback (``heuristic_candidate``) turns failure evidence into
generic local edits and must not hardcode a single query or case id:

* routing failure → append the failing case tags to the matching row of the
  dispatch table in ``SKILL.md`` (``| skill_id | name | tool | keywords |``);
* clarify failure → maintain a ``## 澄清条件`` section in ``SKILL.md``;
* wrong argument value → replace the wrong literal on a ``... -> value`` mapping
  line in the routed sub-skill;
* missing argument → append a required-parameter line to the ``## 参数`` section
  of the routed sub-skill.

Emit callback signature (compatible with the persistence event envelope, extra
keywords tolerated)::

    emit(type, payload=None, *, level="info", case_id=None, stage=None,
         call_id=None, **extra)

Log safety: event payloads only carry truncated/redacted previews; the full
model output is returned as an already-redacted ``raw_output`` for the trace
artifact, never dumped into the event log.
"""

from __future__ import annotations

import json
import re
import time
import traceback
import uuid
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import event_types
from . import patch as patch_module
from .model_output import capture_model_response, response_summary

OPTIMIZER_PROTOCOL_VERSION = "optimizer-v1"

ATTRIBUTIONS = ("routing", "arguments", "handoff", "dataset_issue", "uncertain")
_ATTRIBUTION_PRIORITY = {
    "routing": 0, "handoff": 1, "arguments": 2, "dataset_issue": 3, "uncertain": 4,
}

_HEURISTIC_STRATEGY = "heuristic_v1"
# The sample dataset needs 5 files / ~9-10 generic edits for one candidate; keep
# a bounded but sufficient default and allow per-run overrides via context.
_MAX_HEURISTIC_EDITS = 16
_MAX_HEURISTIC_FILES = 8
_MAX_HEURISTIC_EDITS_CAP = 64
_MAX_HEURISTIC_FILES_CAP = 32
_PREVIEW_LIMIT = 200

_CASE_ID_RE = re.compile(r"\bcase_[A-Za-z0-9_]*\d[A-Za-z0-9_]*\b")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_FENCE_RE = re.compile(r"^\s*```", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^(\s*)\|(.*)\|(\s*)$")
_SECTION_END_RE = re.compile(r"^#{1,2}\s+")
_ASCII_WORD_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

_ERROR_TITLES = {
    "optimizer_not_configured": "优化器未配置",
    "optimizer_call_failed": "优化器调用失败",
    "optimizer_empty_response": "优化器返回为空",
    "optimizer_parse_failed": "优化器输出解析失败",
    "optimizer_no_change": "优化器未给出修改",
}

_SECRET_PATTERNS = (
    (re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)['\"]?[^\s'\",}]+"), r"\1***"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*)['\"]?[^\s'\",}]+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9]{8,}"), "sk-***"),
)

_CONTRACT_SCHEMA = """{
  "diagnosis": {
    "summary": "共性问题的简短说明",
    "attribution": "routing | arguments | handoff | dataset_issue | uncertain",
    "findings": [
      {
        "case_id": "关联的失败案例 ID",
        "attribution": "routing | arguments | handoff | dataset_issue | uncertain",
        "observable_events": ["skill_selected", "score_completed"],
        "evidence": "可观察到的字段差异或事件事实，不写隐藏思维链"
      }
    ]
  },
  "hypotheses": ["可验证的修改假设 1", "可验证的修改假设 2"],
  "changed_files": ["SKILL.md"],
  "patch": {
    "summary": "本补丁要解决的问题",
    "edits": [
      {
        "path": "SKILL.md",
        "op": "replace",
        "search": "目标文件中恰好出现一次的原文片段",
        "replace": "替换后的完整片段",
        "reason": "与哪类失败证据相关"
      }
    ]
  },
  "expected_effect": "对 R / A / J 的预期影响",
  "risk_cases": ["可能受影响的案例类型或 case_id"]
}"""

_SYSTEM_PROMPT = (
    "你是 Skill 包优化器（协议 optimizer-v1）。你的唯一任务：根据优化集失败证据，"
    "对一个由主 Skill（SKILL.md）与 references 子技能组成的 Skill 包，提出最小、通用、"
    "可审查的局部补丁，并只返回一个 JSON 对象。\n"
    "\n"
    "硬性约束：\n"
    "1. 只能修改 allowed_paths 中列出的、已存在的 .md 文件；不得新增或删除文件。\n"
    "2. 不得修改 manifest.json、脚本、资源文件、执行环境、工具 Schema、评分器或数据标签。\n"
    "3. 不得写入任何测试 case_id、标准答案/答案表，也不得针对单条 query 写硬编码分支；"
    "优先解决共性错误、优先局部修改。\n"
    "4. 每个 edit 使用 op=replace；search 必须是目标文件中恰好出现一次的原文片段，"
    "replace 是替换后的完整片段；不要改动无关内容。\n"
    "5. 如果证据不足以支持修改，patch.edits 返回空数组，并在 diagnosis 中说明原因。\n"
    "6. 疑似数据标注问题时 attribution 使用 dataset_issue，只提出反馈，不要修改 expected。\n"
    "7. 只输出一个 JSON 对象，不要输出 JSON 之外的说明文字（可以用 ```json 代码块包裹）。\n"
    "\n"
    "必须返回的 JSON 契约（字段名不可更改）：\n"
    + _CONTRACT_SCHEMA
)


class OptimizerOutputError(Exception):
    """Raised by :func:`parse_output` with a stable direct-cause error code."""

    def __init__(self, code: str, message: str, *,
                 technical_detail: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.technical_detail = dict(technical_detail or {})


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _redact(text: Any) -> str:
    value = str(text if text is not None else "")
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _preview(text: Any, limit: int = _PREVIEW_LIMIT) -> tuple[str, bool]:
    masked = _redact(text)
    if len(masked) <= limit:
        return masked, False
    return masked[:limit] + "…", True


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return "<binary omitted>"
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=str)
    return str(value)


def _emit(emit: Callable[..., Any] | None, event_type: str, payload: Mapping[str, Any] | None = None,
          *, level: str = "info", stage: str = "candidate_generation",
          call_id: str | None = None, case_id: str | None = None) -> Any:
    if emit is None:
        return None
    kwargs: dict[str, Any] = {"level": level, "stage": stage}
    if call_id is not None:
        kwargs["call_id"] = call_id
    if case_id is not None:
        kwargs["case_id"] = case_id
    try:
        return emit(event_type, dict(payload or {}), **kwargs)
    except TypeError as exc:  # tolerate narrower emit callbacks
        if "unexpected keyword" not in str(exc):
            raise
        try:
            return emit(event_type, dict(payload or {}), level=level)
        except TypeError as nested:
            if "unexpected keyword" not in str(nested):
                raise
            return emit(event_type, dict(payload or {}))


def _seq_of(event: Any) -> int | None:
    if isinstance(event, Mapping):
        seq = event.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            return seq
    return None


def _issue_payload(code: str, message: str, *, title: str | None = None,
                   impact: str, next_action: str,
                   technical_detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "issue": event_types.issue(
            code,
            title or _ERROR_TITLES.get(code, code),
            message,
            impact=impact,
            next_action=next_action,
            technical_detail=dict(technical_detail or {}),
        )
    }


def _context_files(context: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    version = context.get("baseline_skill_version")
    version = version if isinstance(version, Mapping) else {}
    files = version.get("files")
    if not isinstance(files, Mapping):
        files = context.get("files")
    files = files if isinstance(files, Mapping) else {}
    manifest = version.get("manifest")
    if not isinstance(manifest, Mapping):
        manifest = context.get("manifest")
    manifest = manifest if isinstance(manifest, Mapping) else {}
    return files, manifest


def _optimize_failures(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = context.get("failures")
    if not isinstance(raw, list):
        return []
    failures: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        # Optimizer must never see validation / final-test samples (§11).
        if item.get("split") in ("validation", "final_test"):
            continue
        failures.append(item)
    return failures


def _allowed_paths(context: Mapping[str, Any]) -> set[str] | None:
    raw = context.get("allowed_paths")
    if raw is None:
        return None
    allowed: set[str] = set()
    for item in raw:
        norm = patch_module.normalize_path(item)
        if norm is not None:
            allowed.add(norm)
    return allowed


_NON_MODEL_PROVIDERS = {"heuristic", "none", "disabled", "off", "noop", "manual"}


def _config_is_model_backed(config: Mapping[str, Any] | None) -> bool:
    """Reject pseudo providers used by the demo environment ("heuristic")."""
    if not config:
        return False
    provider = str(config.get("provider") or "").strip().lower()
    return provider not in _NON_MODEL_PROVIDERS


def adapter_mode_is_heuristic(config: Mapping[str, Any] | None) -> bool:
    """True when the optimizer is deliberately configured as rule-based repair."""
    return str((config or {}).get("provider") or "").strip().lower() == "heuristic"


def _resolve_optimizer_config(context: Mapping[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Return ``(config, explicitly_configured_key_present)``."""
    for key in ("optimizer_config", "model_config"):
        if key in context:
            value = context.get(key)
            if isinstance(value, Mapping):
                return dict(value), True
            return None, True
    value = context.get("config")
    if isinstance(value, Mapping):
        return dict(value), False
    return None, False


def _input_stats(context: Mapping[str, Any], messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    files, _ = _context_files(context)
    failures = _optimize_failures(context)
    file_paths = sorted(
        filter(None, (patch_module.normalize_path(path) for path in files))
    )
    input_chars = sum(len(str(message.get("content") or "")) for message in messages)
    return {
        "protocol_version": OPTIMIZER_PROTOCOL_VERSION,
        "failure_count": len(failures),
        "failure_case_ids": [str(item.get("case_id")) for item in failures
                             if item.get("case_id")][:100],
        "file_count": len(file_paths),
        "input_files": file_paths[:100],
        "input_chars": input_chars,
        "truncated": False,
        "loading_strategy": "full",
    }


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
def _prompt_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    context = context if isinstance(context, Mapping) else {}
    files, manifest = _context_files(context)
    version = context.get("baseline_skill_version")
    version = version if isinstance(version, Mapping) else {}
    passing = context.get("passing_summary") or {}
    passing = dict(passing) if isinstance(passing, Mapping) else {}
    if isinstance(passing.get("cases"), list):
        passing["cases"] = [item for item in passing["cases"]
                            if isinstance(item, Mapping) and
                            item.get("split") not in ("validation", "final_test")][:5]
    return {
        "project": _jsonable(context.get("project") or {}),
        "baseline_skill_version": {
            "id": _jsonable(version.get("id")),
            "manifest": _jsonable(manifest),
            "files": _jsonable(files),
        },
        "allowed_paths": _jsonable(context.get("allowed_paths") or []),
        "business_execution_prompt": _jsonable(context.get("business_execution_prompt") or ""),
        "failures": _jsonable(_optimize_failures(context)),
        "passing_summary": _jsonable(passing),
        "diagnosis_hints": _jsonable(context.get("diagnosis_hints") or {}),
    }


def build_prompt(context: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build the optimizer messages (Chinese, structured, JSON output contract).

    Only optimize-split failure evidence is included; the frozen optimizer model
    config is never placed into the prompt (credentials stay server-side).
    """
    payload = _prompt_payload(context)
    user_content = (
        "以下为本轮优化器输入（仅包含优化集证据，不含验证集/最终测试集内容）：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# --------------------------------------------------------------------------- #
# output parsing
# --------------------------------------------------------------------------- #
def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return ""
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def parse_output(raw: Any) -> dict[str, Any]:
    """Parse the optimizer model output into a JSON object.

    Raises :class:`OptimizerOutputError` with ``optimizer_empty_response`` for an
    empty response and ``optimizer_parse_failed`` for invalid / non-object JSON.
    A single surrounding Markdown code fence is stripped deterministically; no
    free-model guessing or repair is performed.
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            raise OptimizerOutputError(
                "optimizer_parse_failed", "优化器输出不是合法的 UTF-8 文本",
                technical_detail={"raw_length": len(raw)},
            )
    if raw is None:
        raise OptimizerOutputError("optimizer_empty_response", "优化器返回空内容",
                                   technical_detail={"raw_length": 0})
    if not isinstance(raw, str):
        raise OptimizerOutputError(
            "optimizer_parse_failed", f"优化器输出类型非法: {type(raw).__name__}",
            technical_detail={"raw_length": 0},
        )
    text = _strip_fence(raw)
    if not text:
        raise OptimizerOutputError("optimizer_empty_response", "优化器返回空内容",
                                   technical_detail={"raw_length": len(raw)})
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OptimizerOutputError(
            "optimizer_parse_failed",
            f"优化器输出不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列）",
            technical_detail={
                "raw_length": len(raw),
                "raw_preview": _preview(raw, 300)[0],
                "json_error": str(exc),
            },
        ) from exc
    if not isinstance(obj, dict):
        raise OptimizerOutputError(
            "optimizer_parse_failed", "优化器输出的顶层必须是 JSON 对象",
            technical_detail={"raw_length": len(raw),
                              "top_level_type": type(obj).__name__},
        )
    return obj


def _error(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _event_names(raw: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(raw, list):
        return names
    for item in raw:
        value: Any = None
        if isinstance(item, str):
            value = item
        elif isinstance(item, Mapping):
            value = (item.get("type") or item.get("event_type") or item.get("name"))
        if isinstance(value, str) and value.strip() and value.strip() not in names:
            names.append(value.strip())
    return names


def validate_candidate_output(obj: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Validate and normalize the optimizer output contract.

    Returns ``(normalized, errors)``.  ``errors`` is empty only when every
    required field is present, paths are normalized Markdown paths and
    ``changed_files`` matches the edit paths exactly.  No model guessing / repair
    is performed.
    """
    errors: list[dict[str, str]] = []
    if not isinstance(obj, Mapping):
        return (
            {"diagnosis": {"summary": "", "attribution": "uncertain", "findings": []},
             "hypotheses": [], "changed_files": [],
             "patch": {"summary": "", "edits": []},
             "expected_effect": "", "risk_cases": []},
            [_error("bad_output", "$", "优化器输出必须是 JSON 对象")],
        )

    # ---- diagnosis --------------------------------------------------------
    diagnosis_raw = obj.get("diagnosis")
    diagnosis: dict[str, Any] = {"summary": "", "attribution": "uncertain", "findings": []}
    if not isinstance(diagnosis_raw, Mapping):
        errors.append(_error("missing_field", "diagnosis", "缺少 diagnosis 对象"))
    else:
        summary = diagnosis_raw.get("summary")
        diagnosis["summary"] = summary if isinstance(summary, str) else ""
        attribution = diagnosis_raw.get("attribution")
        if attribution not in ATTRIBUTIONS:
            errors.append(_error("invalid_attribution", "diagnosis.attribution",
                                 f"diagnosis.attribution 必须是 {list(ATTRIBUTIONS)} 之一"))
            attribution = "uncertain"
        diagnosis["attribution"] = attribution
        findings_raw = diagnosis_raw.get("findings")
        if findings_raw is None:
            findings_raw = diagnosis_raw.get("cases")
        if not isinstance(findings_raw, list) or not findings_raw:
            errors.append(_error("missing_field", "diagnosis.findings",
                                 "diagnosis.findings 必须是非空数组"))
            findings_raw = []
        findings: list[dict[str, Any]] = []
        for index, item in enumerate(findings_raw):
            if not isinstance(item, Mapping):
                errors.append(_error("invalid_type", f"diagnosis.findings[{index}]",
                                     "finding 必须是对象"))
                continue
            case_id = item.get("case_id") or item.get("id")
            if not isinstance(case_id, str) or not case_id.strip():
                errors.append(_error("missing_field", f"diagnosis.findings[{index}].case_id",
                                     "finding 必须关联 case_id"))
                continue
            events = _event_names(item.get("observable_events")
                                  or item.get("observable") or item.get("events"))
            if not events:
                errors.append(_error("missing_field",
                                     f"diagnosis.findings[{index}].observable_events",
                                     "finding 必须列出可观察事件"))
                continue
            finding_attr = item.get("attribution") or attribution
            if finding_attr not in ATTRIBUTIONS:
                errors.append(_error("invalid_attribution",
                                     f"diagnosis.findings[{index}].attribution",
                                     f"finding 归因标签必须是 {list(ATTRIBUTIONS)} 之一"))
                finding_attr = attribution if attribution in ATTRIBUTIONS else "uncertain"
            evidence = item.get("evidence")
            findings.append({
                "case_id": case_id.strip(),
                "attribution": finding_attr,
                "observable_events": events,
                "evidence": evidence if isinstance(evidence, str) else "",
            })
        diagnosis["findings"] = findings

    # ---- hypotheses -------------------------------------------------------
    hypotheses_raw = obj.get("hypotheses")
    hypotheses: list[str] = []
    if not isinstance(hypotheses_raw, list):
        errors.append(_error("missing_field", "hypotheses", "缺少 hypotheses 数组"))
    else:
        for index, item in enumerate(hypotheses_raw):
            text = ""
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, Mapping):
                candidate = (item.get("hypothesis") or item.get("text")
                             or item.get("description"))
                if isinstance(candidate, str):
                    text = candidate.strip()
            if not text:
                errors.append(_error("invalid_type", f"hypotheses[{index}]",
                                     "hypothesis 必须是非空字符串"))
                continue
            hypotheses.append(text)
        if not hypotheses:
            errors.append(_error("missing_field", "hypotheses",
                                 "hypotheses 必须至少包含一条假设"))

    # ---- patch / edits ----------------------------------------------------
    patch_raw = obj.get("patch")
    patch: dict[str, Any] = {"summary": "", "edits": []}
    edit_paths: list[str] = []
    if not isinstance(patch_raw, Mapping):
        errors.append(_error("missing_field", "patch", "缺少 patch 对象"))
    else:
        summary = patch_raw.get("summary")
        if isinstance(summary, str) and summary.strip():
            patch["summary"] = summary.strip()
        else:
            errors.append(_error("missing_field", "patch.summary",
                                 "patch.summary 必须是非空字符串"))
        edits_raw = patch_raw.get("edits")
        if not isinstance(edits_raw, list):
            errors.append(_error("missing_field", "patch.edits", "缺少 patch.edits 数组"))
            edits_raw = []
        edits: list[dict[str, Any]] = []
        for index, item in enumerate(edits_raw):
            field = f"patch.edits[{index}]"
            if not isinstance(item, Mapping):
                errors.append(_error("invalid_type", field, "edit 必须是对象"))
                continue
            raw_path = item.get("path")
            path = patch_module.normalize_path(raw_path)
            if path is None:
                errors.append(_error("invalid_edit_path", f"{field}.path",
                                     "edit.path 必须是包内相对路径"))
                continue
            if not path.lower().endswith(".md"):
                errors.append(_error("invalid_edit_path", f"{field}.path",
                                     "edit.path 只能是 .md 文件"))
                continue
            op = item.get("op", "replace")
            if not isinstance(op, str) or op.strip().lower() != "replace":
                errors.append(_error("unsupported_edit_op", f"{field}.op",
                                     "仅支持 op=replace"))
                continue
            search = item.get("search")
            replace = item.get("replace")
            if not isinstance(search, str) or search == "":
                errors.append(_error("invalid_edit", f"{field}.search",
                                     "edit.search 必须是非空字符串"))
                continue
            if not isinstance(replace, str):
                errors.append(_error("invalid_edit", f"{field}.replace",
                                     "edit.replace 必须是字符串"))
                continue
            reason = item.get("reason")
            edits.append({
                "path": path,
                "op": "replace",
                "search": search,
                "replace": replace,
                "reason": reason if isinstance(reason, str) else "",
            })
            edit_paths.append(path)
        patch["edits"] = edits

    # ---- changed_files ----------------------------------------------------
    changed_raw = obj.get("changed_files")
    changed_files: list[str] = []
    if not isinstance(changed_raw, list):
        errors.append(_error("missing_field", "changed_files", "缺少 changed_files 数组"))
    else:
        for index, item in enumerate(changed_raw):
            path = patch_module.normalize_path(item)
            if path is None:
                errors.append(_error("invalid_path", f"changed_files[{index}]",
                                     "changed_files 必须是包内相对路径"))
                continue
            if not path.lower().endswith(".md"):
                errors.append(_error("invalid_path", f"changed_files[{index}]",
                                     "changed_files 只能包含 .md 文件"))
                continue
            if path not in changed_files:
                changed_files.append(path)
        if set(changed_files) != set(edit_paths):
            errors.append(_error(
                "changed_files_mismatch", "changed_files",
                "changed_files 必须与 patch.edits 的 path 集合完全一致",
            ))

    # ---- expected_effect / risk_cases ------------------------------------
    expected_effect = obj.get("expected_effect")
    if not isinstance(expected_effect, str) or not expected_effect.strip():
        errors.append(_error("missing_field", "expected_effect",
                             "expected_effect 必须是非空字符串"))
        expected_effect = ""
    else:
        expected_effect = expected_effect.strip()

    risk_raw = obj.get("risk_cases")
    risk_cases: list[Any] = []
    if not isinstance(risk_raw, list):
        errors.append(_error("missing_field", "risk_cases", "缺少 risk_cases 数组"))
    else:
        for index, item in enumerate(risk_raw):
            if isinstance(item, str):
                risk_cases.append(item)
            elif isinstance(item, Mapping):
                risk_cases.append(_jsonable(item))
            else:
                errors.append(_error("invalid_type", f"risk_cases[{index}]",
                                     "risk_cases 必须是字符串或对象"))

    normalized = {
        "diagnosis": diagnosis,
        "hypotheses": hypotheses,
        "changed_files": changed_files,
        "patch": patch,
        "expected_effect": expected_effect,
        "risk_cases": risk_cases,
    }
    return normalized, errors


def _edit_target_errors(context: Mapping[str, Any],
                        edits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    files, _ = _context_files(context)
    base_paths = {norm for norm in
                  (patch_module.normalize_path(path) for path in files) if norm}
    allowed = _allowed_paths(context)
    errors: list[dict[str, Any]] = []
    for index, edit in enumerate(edits):
        path = edit.get("path")
        if not isinstance(path, str) or not path.lower().endswith(".md"):
            errors.append({"code": "edit_not_markdown", "path": path,
                           "message": "只允许修改 .md 文件", "edit_index": index})
            continue
        if allowed is not None and path not in allowed:
            errors.append({"code": "edit_path_not_allowed", "path": path,
                           "message": "edit.path 不在 allowed_paths 白名单内",
                           "edit_index": index})
        if base_paths and path not in base_paths:
            errors.append({"code": "edit_file_missing", "path": path,
                           "message": "目标文件在基线包中不存在", "edit_index": index})
    return errors


# --------------------------------------------------------------------------- #
# result assembly
# --------------------------------------------------------------------------- #
def _make_result(*, status: str, diagnosis: Mapping[str, Any] | None = None,
                 hypotheses: Sequence[Any] | None = None,
                 changed_files: Sequence[str] | None = None,
                 patch: Mapping[str, Any] | None = None,
                 expected_effect: str = "", risk_cases: Sequence[Any] | None = None,
                 error_code: str | None = None, used_fallback: bool = False,
                 raw_output: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "diagnosis": dict(diagnosis) if diagnosis else
        {"summary": "", "attribution": "uncertain", "findings": []},
        "hypotheses": list(hypotheses or []),
        "changed_files": list(changed_files or []),
        "patch": dict(patch) if patch else {"summary": "", "edits": []},
        "expected_effect": expected_effect or "",
        "risk_cases": list(risk_cases or []),
        "error_code": error_code,
        "used_fallback": used_fallback,
        "raw_output": raw_output or "",
    }


def _fallback(context: Mapping[str, Any], emit: Callable[..., Any] | None,
              cause_code: str, cause_seq: int | None, allow_fallback: bool, *,
              raw_output: str = "") -> dict[str, Any]:
    """Record fallback events and run the heuristic; never silently swallow."""
    if not allow_fallback:
        return _make_result(status="failed", error_code=cause_code,
                            used_fallback=False, raw_output=raw_output)
    _emit(emit, event_types.FALLBACK_STARTED, {
        "reason": cause_code,
        "cause_event_seq": cause_seq,
        "strategy": _HEURISTIC_STRATEGY,
    })
    candidate = heuristic_candidate(context)
    if candidate is None:
        _emit(emit, event_types.FALLBACK_NO_CHANGE, {
            "reason": cause_code,
            "cause_event_seq": cause_seq,
            **_issue_payload(
                "fallback_no_change",
                f"优化器未生成可用补丁（{cause_code}），启发式回退也未找到安全的局部修改。",
                impact="本轮没有候选补丁，不会进入静态检查与复测。",
                next_action="检查失败证据与 Skill 文件结构，修复优化器输出后新建运行。",
                technical_detail={"optimizer_error_code": cause_code},
            ),
        }, level="warning")
        return _make_result(status="no_patch", error_code="fallback_no_change",
                            used_fallback=True, raw_output=raw_output)
    _emit(emit, event_types.PATCH_CREATED, {
        "summary": candidate["patch"].get("summary", ""),
        "changed_files": candidate["changed_files"],
        "edit_count": len(candidate["patch"].get("edits") or []),
        "used_fallback": True,
        "fallback_reason": cause_code,
        "diagnosis": candidate["diagnosis"],
        "expected_effect": candidate["expected_effect"],
    })
    return _make_result(status="ok", used_fallback=True, raw_output=raw_output,
                        **candidate)


# --------------------------------------------------------------------------- #
# heuristic fallback
# --------------------------------------------------------------------------- #
def _route_skill_ids(expected: Mapping[str, Any]) -> list[str]:
    routes = expected.get("routes")
    ids: list[str] = []
    if not isinstance(routes, list):
        return ids
    for route in routes:
        if isinstance(route, str):
            candidates: list[Any] = [route]
        elif isinstance(route, list):
            candidates = route
        else:
            continue
        for skill_id in candidates:
            if isinstance(skill_id, str) and skill_id.strip() \
                    and skill_id.strip() not in ids:
                ids.append(skill_id.strip())
    return ids


def _first_route_skill(expected: Mapping[str, Any]) -> str | None:
    ids = _route_skill_ids(expected)
    return ids[0] if ids else None


def _actual_routes(actual: Mapping[str, Any],
                   routing: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    selected = actual.get("selected_skills")
    if isinstance(selected, list):
        for item in selected:
            if isinstance(item, str) and item.strip():
                found.add(item.strip())
    actual_routes = routing.get("actual_routes")
    if isinstance(actual_routes, list):
        for route in actual_routes:
            if isinstance(route, str):
                found.add(route.strip())
            elif isinstance(route, list):
                for item in route:
                    if isinstance(item, str) and item.strip():
                        found.add(item.strip())
    return found


def _field_name(field_path: Any) -> str:
    if not isinstance(field_path, str):
        return ""
    text = field_path.strip()
    if not text:
        return ""
    parts = [part for part in re.split(r"[.$\[\]/]+", text) if part]
    return parts[-1] if parts else ""


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def _replace_literal(line: str, old: str, new: str) -> str | None:
    if not old:
        return None
    if _ASCII_WORD_RE.match(old):
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])")
        replaced, count = pattern.subn(lambda match: new, line)
        return replaced if count else None
    if old in line:
        return line.replace(old, new)
    return None


def _observable_events(failure: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    events = failure.get("events")
    if isinstance(events, list):
        for item in events:
            value: Any = None
            if isinstance(item, Mapping):
                value = item.get("type") or item.get("event_type")
            elif isinstance(item, str):
                value = item
            if isinstance(value, str) and value.strip() and value.strip() not in names:
                names.append(value.strip())
    if not names:
        names = ["score_completed"]
    return names[:8]


def _split_table_row(line: str) -> tuple[str, list[str], str] | None:
    match = _TABLE_ROW_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2).split("|"), match.group(3)


def _find_dispatch_row(text: str, skill_id: str, name: str,
                       path: str | None) -> str | None:
    fallback: str | None = None
    for line in text.splitlines():
        parts = _split_table_row(line)
        if parts is None:
            continue
        cells = [cell.strip() for cell in parts[1]]
        if len(cells) < 2 or not any(cells):
            continue
        if skill_id and cells[0] == skill_id:
            return line
        if fallback is not None:
            continue
        if (skill_id and skill_id in line) or (name and name in line) \
                or (path and path in line):
            fallback = line
    return fallback


def _append_keywords_to_row(line: str, keywords: Sequence[str]) -> str | None:
    parts = _split_table_row(line)
    if parts is None:
        return None
    prefix, cells, suffix = parts
    if len(cells) < 2:
        return None
    appended: list[str] = []
    for keyword in keywords:
        keyword = keyword.strip()
        if not keyword or keyword in line or keyword in appended:
            continue
        appended.append(keyword)
    if not appended:
        return None
    last = cells[-1]
    core = last.rstrip()
    trailing = last[len(core):]
    joined = "、".join(appended)
    if core.strip():
        cells[-1] = f"{core}、{joined}{trailing}"
    else:
        cells[-1] = f" {joined}{trailing}"
    return prefix + "|" + "|".join(cells) + "|" + suffix


def _section_end(lines: Sequence[str], heading_index: int) -> int:
    for index in range(heading_index + 1, len(lines)):
        if _SECTION_END_RE.match(lines[index]):
            end = index
            while end > heading_index + 1 and not lines[end - 1].strip():
                end -= 1
            return end
    end = len(lines)
    while end > heading_index + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _find_section(lines: Sequence[str], heading: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == heading:
            return index
    return None


def _edit_for_insertion(text: str, insert_index: int,
                        added_lines: Sequence[str]) -> tuple[str, str] | None:
    """Build a unique ``(search, replace)`` pair that inserts lines at a position."""
    if not added_lines:
        return None
    lines = text.splitlines()
    insert_index = max(0, min(insert_index, len(lines)))
    if not lines:
        return None
    for span in range(1, min(6, insert_index) + 1):
        start = insert_index - span
        search = "\n".join(lines[start:insert_index])
        if search and text.count(search) == 1:
            return search, search + "\n" + "\n".join(added_lines)
    for span in range(1, min(6, len(lines) - insert_index) + 1):
        end = insert_index + span
        search = "\n".join(lines[insert_index:end])
        if search and text.count(search) == 1:
            return search, "\n".join(added_lines) + "\n" + search
    return None


def _contains_forbidden_reference(text: str, forbidden: Iterable[str]) -> bool:
    if _CASE_ID_RE.search(text):
        return True
    return any(case_id and case_id in text for case_id in forbidden)


def _bounded_int(value: Any, default: int, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(1, min(int(value), upper))


def _heuristic_limits(context: Mapping[str, Any]) -> tuple[int, int]:
    """Per-run heuristic caps (``context["heuristic_limits"]`` or flat keys)."""
    max_edits, max_files = _MAX_HEURISTIC_EDITS, _MAX_HEURISTIC_FILES
    limits = context.get("heuristic_limits")
    if isinstance(limits, Mapping):
        max_edits = _bounded_int(limits.get("max_edits"), max_edits,
                                 _MAX_HEURISTIC_EDITS_CAP)
        max_files = _bounded_int(limits.get("max_files"), max_files,
                                 _MAX_HEURISTIC_FILES_CAP)
    else:
        max_edits = _bounded_int(context.get("heuristic_max_edits"), max_edits,
                                 _MAX_HEURISTIC_EDITS_CAP)
        max_files = _bounded_int(context.get("heuristic_max_files"), max_files,
                                 _MAX_HEURISTIC_FILES_CAP)
    return max_edits, max_files


def _try_add_edit(working: dict[str, str], edits: list[dict[str, Any]],
                  allowed: set[str] | None, forbidden: set[str], path: str | None,
                  search: str, replacement: str, reason: str, *,
                  max_edits: int = _MAX_HEURISTIC_EDITS,
                  max_files: int = _MAX_HEURISTIC_FILES) -> bool:
    if len(edits) >= max_edits:
        return False
    if not path or path not in working:
        return False
    if allowed is not None and path not in allowed:
        return False
    if not path.lower().endswith(".md"):
        return False
    if not isinstance(search, str) or not search or not isinstance(replacement, str):
        return False
    if replacement == search:
        return False
    if len({edit["path"] for edit in edits} | {path}) > max_files:
        return False
    text = working[path]
    if text.count(search) != 1:
        return False
    if _contains_forbidden_reference(replacement, forbidden):
        return False
    new_text = text.replace(search, replacement, 1)
    # Do not break the Markdown document (heading / code fences stay valid).
    if not _HEADING_RE.search(new_text):
        return False
    if len(_FENCE_RE.findall(new_text)) % 2 != 0:
        return False
    edits.append({
        "path": path,
        "op": "replace",
        "search": search,
        "replace": replacement,
        "reason": reason,
    })
    working[path] = new_text
    return True


def _description_keywords(description: str, limit: int = 3) -> list[str]:
    if not description:
        return []
    tokens = [token.strip() for token in re.split(r"[、，,;；/\s]+", description)]
    keywords: list[str] = []
    for token in tokens:
        if len(token) < 2 or len(token) > 12:
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def heuristic_candidate(context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Generate a generic local patch from failure evidence, or ``None``.

    The generated edits are deterministic, stay inside ``allowed_paths``, only
    touch existing Markdown files and never embed case ids / queries.
    """
    context = context if isinstance(context, Mapping) else {}
    files, manifest = _context_files(context)
    failures = _optimize_failures(context)
    if not files or not failures:
        return None
    allowed = _allowed_paths(context)
    texts: dict[str, str] = {}
    for raw_path, raw_content in files.items():
        norm = patch_module.normalize_path(raw_path)
        if norm is None or not norm.lower().endswith(".md"):
            continue
        if allowed is not None and norm not in allowed:
            continue
        if isinstance(raw_content, (bytes, bytearray)):
            try:
                text = bytes(raw_content).decode("utf-8")
            except UnicodeDecodeError:
                continue
        elif isinstance(raw_content, str):
            text = raw_content
        else:
            continue
        texts[norm] = text
    if not texts:
        return None

    main_path = patch_module.normalize_path(manifest.get("main") or "SKILL.md") \
        or "SKILL.md"
    by_id: dict[str, dict[str, Any]] = {}
    for item in manifest.get("subskills") or []:
        if not isinstance(item, Mapping):
            continue
        skill_id = item.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            continue
        by_id[skill_id.strip()] = {
            "skill_id": skill_id.strip(),
            "name": str(item.get("name") or skill_id.strip()),
            "path": patch_module.normalize_path(item.get("path")),
            "description": str(item.get("description") or ""),
        }

    routing_targets: dict[str, dict[str, Any]] = {}
    clarify_targets: dict[tuple, dict[str, Any]] = {}
    mapping_targets: dict[tuple, dict[str, Any]] = {}
    missing_targets: dict[tuple, dict[str, Any]] = {}
    case_meta: dict[str, dict[str, Any]] = {}
    case_order: list[str] = []
    forbidden = {str(item.get("case_id")) for item in failures if item.get("case_id")}

    def note_case(case_id: str, attribution: str, events: list[str],
                  evidence: str) -> None:
        if case_id not in case_meta:
            case_order.append(case_id)
            case_meta[case_id] = {"attribution": attribution, "events": events,
                                  "evidence": []}
        meta = case_meta[case_id]
        if _ATTRIBUTION_PRIORITY.get(attribution, 9) < _ATTRIBUTION_PRIORITY.get(
                meta["attribution"], 9):
            meta["attribution"] = attribution
        if evidence and evidence not in meta["evidence"]:
            meta["evidence"].append(evidence)

    for failure in failures:
        case_id = failure.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            continue
        case_id = case_id.strip()
        expected = failure.get("expected")
        expected = expected if isinstance(expected, Mapping) else {}
        actual = failure.get("actual")
        actual = actual if isinstance(actual, Mapping) else {}
        score = failure.get("score")
        score = score if isinstance(score, Mapping) else {}
        if score.get("status") == "infra":
            continue
        tags = [str(tag).strip() for tag in (failure.get("tags") or [])
                if str(tag).strip()]
        events = _observable_events(failure)
        diagnostics = [item for item in (score.get("diagnostics") or [])
                       if isinstance(item, Mapping)]
        categories = {str(item.get("category") or "") for item in diagnostics}
        route_skill = _first_route_skill(expected)
        expected_ids = _route_skill_ids(expected)
        routing = score.get("routing") if isinstance(score.get("routing"),
                                                    Mapping) else {}
        actual_routes = _actual_routes(actual, routing)
        missing_ids = [skill_id for skill_id in expected_ids
                       if skill_id not in actual_routes and skill_id in by_id]
        routing_failed = (routing.get("passed") is False
                          or bool(categories & {"wrong_route", "missing_route"}))
        route_evidence = None
        if expected_ids:
            route_evidence = (
                f"预期路线 {'/'.join(expected_ids)}，实际路线 "
                f"{', '.join(sorted(actual_routes)) or '无'}"
            )
        if routing_failed and missing_ids:
            detail = route_evidence or "分发路线不匹配"
            if route_evidence:
                detail += f"，漏选 {', '.join(missing_ids)}"
            note_case(case_id, "routing", events, detail)
            for skill_id in missing_ids:
                target = routing_targets.setdefault(
                    skill_id, {"kind": "routing", "skill_id": skill_id,
                               "tags": [], "case_ids": []})
                for tag in tags:
                    if tag not in target["tags"]:
                        target["tags"].append(tag)
                if case_id not in target["case_ids"]:
                    target["case_ids"].append(case_id)

        for diagnostic in diagnostics:
            category = str(diagnostic.get("category") or "")
            skill_id = str(diagnostic.get("skill_id") or route_skill or "").strip()
            if category in ("value_error", "type_error"):
                field = _field_name(diagnostic.get("field_path"))
                expected_value = diagnostic.get("expected_value")
                actual_value = diagnostic.get("actual_value")
                if skill_id not in by_id or expected_value is None \
                        or actual_value is None:
                    continue
                if _literal(expected_value) == _literal(actual_value):
                    continue
                key = (skill_id, field, _literal(actual_value),
                       _literal(expected_value))
                target = mapping_targets.setdefault(key, {
                    "kind": "mapping", "skill_id": skill_id, "field": field,
                    "actual": actual_value, "expected": expected_value,
                    "tool": diagnostic.get("tool") or "", "case_ids": [],
                })
                if case_id not in target["case_ids"]:
                    target["case_ids"].append(case_id)
                evidence = (
                    f"{diagnostic.get('tool') or '工具'} 参数 {field or '?'} "
                    f"预期 {_literal(expected_value)}，实际 {_literal(actual_value)}"
                )
                note_case(case_id, "arguments", events, evidence)
            elif category == "missing_arg":
                field = _field_name(diagnostic.get("field_path"))
                if not field or skill_id not in by_id:
                    continue
                key = (skill_id, field)
                target = missing_targets.setdefault(key, {
                    "kind": "missing_arg", "skill_id": skill_id, "field": field,
                    "tool": diagnostic.get("tool") or "", "case_ids": [],
                })
                if case_id not in target["case_ids"]:
                    target["case_ids"].append(case_id)
                note_case(case_id, "arguments", events,
                          f"{diagnostic.get('tool') or '工具'} 缺少必填参数 {field}")

        expected_action = expected.get("action") or "tool_call"
        actual_action = actual.get("action")
        if expected_action == "clarify":
            fields = [str(field).strip() for field in
                      (expected.get("missing_fields") or []) if str(field).strip()]
            actual_fields = sorted(str(field) for field in
                                   (actual.get("missing_fields") or []))
            if fields and (actual_action != "clarify"
                           or sorted(fields) != actual_fields):
                skill_label = route_skill or "该任务"
                key = (skill_label, tuple(fields))
                target = clarify_targets.setdefault(key, {
                    "kind": "clarify", "skill_id": route_skill,
                    "fields": fields, "case_ids": [],
                })
                if case_id not in target["case_ids"]:
                    target["case_ids"].append(case_id)
                note_case(case_id, "handoff", events,
                          f"预期动作 clarify 且缺少 {'、'.join(fields)}，"
                          f"实际动作 {actual_action or '未提供'}")

    proposals: list[dict[str, Any]] = []
    for target in routing_targets.values():
        proposals.append(target)
    for target in clarify_targets.values():
        proposals.append(target)
    for target in mapping_targets.values():
        proposals.append(target)
    for target in missing_targets.values():
        proposals.append(target)
    if not proposals:
        return None

    working = dict(texts)
    edits: list[dict[str, Any]] = []
    hypotheses: list[str] = []
    max_edits, max_files = _heuristic_limits(context)

    def add_edit(path: str | None, search: str, replacement: str,
                 reason: str) -> bool:
        return _try_add_edit(working, edits, allowed, forbidden, path, search,
                             replacement, reason, max_edits=max_edits,
                             max_files=max_files)
    applied_case_ids: set[str] = set()
    applied_kinds: set[str] = set()

    for proposal in proposals:
        kind = proposal["kind"]
        ok = False
        hypothesis = ""
        if kind == "routing":
            skill = by_id[proposal["skill_id"]]
            main_text = working.get(main_path)
            if main_text is not None:
                keywords = list(proposal["tags"]) \
                    or _description_keywords(skill["description"])
                row = _find_dispatch_row(main_text, skill["skill_id"],
                                         skill["name"], skill["path"])
                if row is not None and keywords:
                    new_row = _append_keywords_to_row(row, keywords)
                    if new_row and new_row != row:
                        ok = add_edit(
                            main_path, row, new_row,
                            f"补充 {skill['skill_id']} 的适用关键词",
                        )
                if ok:
                    hypothesis = (
                        f"在 SKILL.md 分发表为 {skill['skill_id']} 补充适用关键词，"
                        "可提升该子技能的分发命中率。"
                    )
        elif kind == "clarify":
            main_text = working.get(main_path)
            if main_text is not None:
                skill_label = proposal.get("skill_id") or "该任务"
                fields = proposal["fields"]
                bullet = (f"- 当 {skill_label} 缺少 {'、'.join(fields)} 时，"
                          "返回 clarify 并列出缺失字段。")
                lines = main_text.splitlines()
                section_index = _find_section(lines, "## 澄清条件")
                if section_index is None:
                    insert_index = len(lines)
                    added = ["", "## 澄清条件", bullet]
                else:
                    insert_index = _section_end(lines, section_index)
                    section_text = "\n".join(lines[section_index:insert_index])
                    if bullet in section_text or (
                            skill_label in section_text
                            and all(field in section_text for field in fields)):
                        ok = False
                        insert_index = -1
                    else:
                        added = [bullet]
                if insert_index >= 0:
                    built = _edit_for_insertion(main_text, insert_index, added)
                    if built is not None:
                        ok = add_edit(
                            main_path, built[0], built[1], "补充 clarify 澄清条件",
                        )
                if ok:
                    hypothesis = (
                        f"在 SKILL.md 补充 {skill_label} 缺少 "
                        f"{'、'.join(fields)} 时的 clarify 条件，"
                        "可改善澄清动作与联合通过率。"
                    )
        elif kind == "mapping":
            skill = by_id[proposal["skill_id"]]
            path = skill.get("path")
            text = working.get(path) if path else None
            if text is not None:
                field = proposal.get("field") or ""
                actual_lit = _literal(proposal["actual"])
                expected_lit = _literal(proposal["expected"])
                matches = [line for line in text.splitlines()
                           if "->" in line and actual_lit in line]
                if len(matches) > 1 and field:
                    with_field = [line for line in matches if field in line]
                    if with_field:
                        matches = with_field
                if len(matches) == 1 and expected_lit not in matches[0]:
                    new_line = _replace_literal(matches[0], actual_lit,
                                                expected_lit)
                    if new_line and new_line != matches[0]:
                        ok = add_edit(
                            path, matches[0], new_line,
                            f"修正字段 {field} 的映射字面量",
                        )
                if ok:
                    hypothesis = (
                        f"修正子技能 {skill['skill_id']} 中字段 {field} 的映射"
                        f"字面量 {actual_lit} → {expected_lit}，可提升工具入参准确率。"
                    )
        elif kind == "missing_arg":
            skill = by_id[proposal["skill_id"]]
            path = skill.get("path")
            text = working.get(path) if path else None
            if text is not None:
                field = proposal["field"]
                bullet = f"- 必填参数：{field}（从请求中提取）"
                lines = text.splitlines()
                section_index = _find_section(lines, "## 参数")
                if section_index is None:
                    insert_index = len(lines)
                    added = ["", "## 参数", bullet]
                else:
                    insert_index = _section_end(lines, section_index)
                    section_text = "\n".join(lines[section_index:insert_index])
                    if bullet in section_text or f"必填参数：{field}" in section_text:
                        insert_index = -1
                    else:
                        added = [bullet]
                if insert_index >= 0:
                    built = _edit_for_insertion(text, insert_index, added)
                    if built is not None:
                        ok = add_edit(
                            path, built[0], built[1],
                            f"补充字段 {field} 的必填提示",
                        )
                if ok:
                    hypothesis = (
                        f"在子技能 {skill['skill_id']} 的参数区补充必填字段 "
                        f"{field}，可减少缺参失败。"
                    )
        if ok:
            applied_kinds.add(kind)
            applied_case_ids.update(proposal.get("case_ids") or [])
            if hypothesis and hypothesis not in hypotheses:
                hypotheses.append(hypothesis)

    if not edits:
        return None

    findings: list[dict[str, Any]] = []
    for case_id in case_order:
        if case_id not in applied_case_ids:
            continue
        meta = case_meta[case_id]
        findings.append({
            "case_id": case_id,
            "attribution": meta["attribution"],
            "observable_events": meta["events"],
            "evidence": "；".join(meta["evidence"])[:800],
        })

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["attribution"]] = counts.get(finding["attribution"], 0) + 1
    top_attribution = "uncertain"
    if counts:
        top_attribution = min(
            counts,
            key=lambda item: (-counts[item], _ATTRIBUTION_PRIORITY.get(item, 9)),
        )

    effect_parts: list[str] = []
    if "routing" in applied_kinds:
        effect_parts.append("补充子技能适用关键词后，预期提升分发准确率（R）")
    if applied_kinds & {"mapping", "missing_arg"}:
        effect_parts.append("修正参数映射/补全必填提示后，预期提升工具入参准确率（A）")
    if "clarify" in applied_kinds:
        effect_parts.append("补充澄清条件后，预期改善澄清动作与联合通过率（J）")
    expected_effect = "；".join(effect_parts) + "。" if effect_parts else \
        "对失败证据对应的通用能力做局部修改。"

    summary_labels = {
        "routing": "补充分发关键词",
        "clarify": "补充澄清条件",
        "mapping": "修正参数映射",
        "missing_arg": "补充必填参数提示",
    }
    summary = "启发式局部修改：" + "、".join(
        summary_labels[kind] for kind in ("routing", "clarify", "mapping",
                                          "missing_arg") if kind in applied_kinds
    )

    diagnosis = {
        "summary": (
            f"分析 {len(failures)} 条失败案例，命中 {len(findings)} 条可归因证据，"
            f"生成 {len(edits)} 处局部修改。"
        ),
        "attribution": top_attribution,
        "findings": findings,
    }
    return {
        "diagnosis": diagnosis,
        "hypotheses": hypotheses,
        "changed_files": sorted({edit["path"] for edit in edits}),
        "patch": {"summary": summary, "edits": edits},
        "expected_effect": expected_effect,
        "risk_cases": [],
    }


# --------------------------------------------------------------------------- #
# generate_candidate
# --------------------------------------------------------------------------- #
def generate_candidate(context: Mapping[str, Any],
                       adapter: Any,
                       emit: Callable[..., Any] | None,
                       *,
                       allow_fallback: bool = True) -> dict[str, Any]:
    """Run the full candidate-generation flow and return the contract result.

    Order of events (all ``stage="candidate_generation"``):

    ``optimizer_input_built`` → ``optimizer_call_started`` →
    ``optimizer_call_finished`` → ``optimizer_output_parsed`` → ``patch_created``.

    On failure the direct cause event is emitted first, then ``fallback_started``
    and finally ``patch_created`` (heuristic succeeded) or ``fallback_no_change``
    (warning).  ``status="no_patch"`` only describes the final outcome; the
    direct reason is always present in the event log / ``error_code``.
    """
    context = context if isinstance(context, Mapping) else {}
    messages = build_prompt(context)
    _emit(emit, event_types.OPTIMIZER_INPUT_BUILT, _input_stats(context, messages))

    config, explicit = _resolve_optimizer_config(context)
    configured = adapter is not None and (
        _config_is_model_backed(config) if explicit else True)
    if not configured and explicit and adapter_mode_is_heuristic(config):
        # The environment deliberately chose rule-based repair: record the
        # direct reason first (§49) but as information, not as a failure.
        event = _emit(emit, event_types.OPTIMIZER_NOT_CONFIGURED, {
            "adapter_present": adapter is not None,
            "optimizer_config_present": explicit,
            "optimizer_mode": "heuristic",
            **_issue_payload(
                "optimizer_not_configured",
                "执行环境选择了规则修复模式：按固定规则生成候选，不调用优化模型。",
                title="规则修复模式",
                impact="候选仍需通过静态检查与配对验证后才会给出结论。",
                next_action="需要由模型提出修改时，在执行环境配置优化模型并发布新版本。",
                technical_detail={"adapter_present": adapter is not None,
                                  "optimizer_config_present": explicit,
                                  "provider": "heuristic"},
            ),
        }, level="info")
        return _fallback(context, emit, "optimizer_not_configured",
                         _seq_of(event), allow_fallback)

    if not configured:
        event = _emit(emit, event_types.OPTIMIZER_NOT_CONFIGURED, {
            "adapter_present": adapter is not None,
            "optimizer_config_present": explicit,
            **_issue_payload(
                "optimizer_not_configured",
                "执行环境未配置可用的优化模型或未提供适配器，已跳过模型优化。",
                impact="本轮无法生成模型候选，将使用启发式回退（如启用）。",
                next_action="在执行环境配置优化模型并发布新环境版本后新建运行。",
                technical_detail={"adapter_present": adapter is not None,
                                  "optimizer_config_present": explicit},
            ),
        }, level="error")
        return _fallback(context, emit, "optimizer_not_configured",
                         _seq_of(event), allow_fallback)

    if config is None:
        config = {}
    call_id = f"opt_{uuid.uuid4().hex[:12]}"
    _emit(emit, event_types.OPTIMIZER_CALL_STARTED, {
        "protocol_version": OPTIMIZER_PROTOCOL_VERSION,
        "provider": config.get("provider") if isinstance(config, Mapping) else None,
        "model": config.get("model") if isinstance(config, Mapping) else None,
        "failure_count": len(_optimize_failures(context)),
        "input_chars": sum(len(str(message.get("content") or ""))
                           for message in messages),
        "message_count": len(messages),
        "truncated": False,
    }, call_id=call_id)

    started_at = time.monotonic()
    try:
        response = adapter.invoke(messages, config, [])
    except Exception as exc:  # noqa: BLE001 - direct cause must be recorded
        detail = {
            "exception_type": type(exc).__name__,
            "exception_message": _preview(str(exc), 500)[0],
            "traceback": _redact(traceback.format_exc())[:4000],
            "adapter_error_code": getattr(exc, "code", None),
            "elapsed_ms": round((time.monotonic() - started_at) * 1000),
        }
        timed_out = detail["adapter_error_code"] == "timeout"
        if config.get("provider") == "openai_http":
            detail["request_timeout_s"] = config.get("timeout_s") or config.get("timeout") or 60
        event = _emit(emit, event_types.OPTIMIZER_CALL_FAILED, {
            **_issue_payload(
                "optimizer_call_failed",
                "优化模型请求超时，未获得完整可用输出。" if timed_out else "调用优化模型失败，未获得可用输出。",
                title="优化模型请求超时" if timed_out else "优化器调用失败",
                impact="本轮无法生成模型候选，将尝试启发式回退。" if allow_fallback else "本轮无法生成模型候选，未启用启发式回退。",
                next_action=("在执行环境草稿中调整优化模型的请求超时，保存并发布后新建运行；流式输出不会重置总计时，连接测试成功也不保证完整优化请求能按时完成。"
                             if timed_out else "检查模型连接、凭据与请求配置后新建运行。"),
                technical_detail=detail,
            ),
            "exception_type": detail["exception_type"],
        }, level="error", call_id=call_id)
        return _fallback(context, emit, "optimizer_call_failed", _seq_of(event),
                         allow_fallback)

    if isinstance(response, Mapping):
        content = response.get("content")
        model = response.get("model")
        finish_reason = response.get("finish_reason")
        usage = response.get("usage")
        if isinstance(content, Mapping):
            raw_obj: Mapping[str, Any] | None = dict(content)
            raw_text = json.dumps(content, ensure_ascii=False, sort_keys=True)
        elif content is None:
            raw_obj = None
            raw_text = ""
        elif isinstance(content, str):
            raw_obj = None
            raw_text = content
        elif isinstance(content, (list, tuple)):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            raw_obj = None
            raw_text = "".join(parts)
        else:
            raw_obj = None
            raw_text = str(content)
    elif isinstance(response, str):
        raw_obj = None
        raw_text = response
        model = finish_reason = usage = None
    else:
        event = _emit(emit, event_types.OPTIMIZER_CALL_FAILED, {
            **_issue_payload(
                "optimizer_call_failed",
                "优化器适配器返回结构不符合统一接口契约。",
                impact="本轮无法生成模型候选，将使用启发式回退（如启用）。",
                next_action="检查适配器返回值是否包含 content 字段。",
                technical_detail={"response_type": type(response).__name__},
            ),
        }, level="error", call_id=call_id)
        return _fallback(context, emit, "optimizer_call_failed", _seq_of(event),
                         allow_fallback)

    output_preview, preview_truncated = _preview(raw_text)
    captured = capture_model_response(response, config)
    _emit(emit, event_types.OPTIMIZER_CALL_FINISHED, {
        "model": model if isinstance(model, str) else None,
        "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
        "usage": _jsonable(usage),
        "content_chars": len(raw_text),
        "output_preview": output_preview,
        "preview_truncated": preview_truncated,
        "response": captured,
        "response_summary": response_summary(captured),
    }, call_id=call_id)

    if raw_obj is not None:
        obj: Any = raw_obj
    else:
        try:
            obj = parse_output(raw_text)
        except OptimizerOutputError as exc:
            event = _emit(emit, exc.code, {
                **_issue_payload(
                    exc.code,
                    exc.message,
                    impact="本轮无法使用模型输出，将使用启发式回退（如启用）。",
                    next_action="检查优化器 Prompt 与模型输出格式后新建运行。",
                    technical_detail=exc.technical_detail,
                ),
                "raw_length": len(raw_text),
            }, level="error", call_id=call_id)
            return _fallback(context, emit, exc.code, _seq_of(event),
                             allow_fallback)

    normalized, errors = validate_candidate_output(obj)
    if errors:
        event = _emit(emit, event_types.OPTIMIZER_PARSE_FAILED, {
            **_issue_payload(
                "optimizer_parse_failed",
                "优化器输出缺少必要字段或字段格式不符合契约。",
                impact="本轮无法使用模型输出，将使用启发式回退（如启用）。",
                next_action="检查优化器输出契约，修复模型输出后新建运行。",
                technical_detail={
                    "validation_errors": errors[:20],
                    "validation_error_count": len(errors),
                    "error_count": len(errors),
                    "raw_preview": _preview(raw_text, 300)[0],
                },
            ),
            "validation_error_count": len(errors),
        }, level="error", call_id=call_id)
        return _fallback(context, emit, "optimizer_parse_failed",
                         _seq_of(event), allow_fallback)

    target_errors = _edit_target_errors(context, normalized["patch"]["edits"])
    if target_errors:
        event = _emit(emit, event_types.OPTIMIZER_PARSE_FAILED, {
            **_issue_payload(
                "optimizer_parse_failed",
                "优化器补丁引用了不允许或不存在的文件路径。",
                impact="本轮无法使用模型输出，将使用启发式回退（如启用）。",
                next_action="检查 allowed_paths 与优化器输出后新建运行。",
                technical_detail={"target_errors": target_errors[:20]},
            ),
        }, level="error", call_id=call_id)
        return _fallback(context, emit, "optimizer_parse_failed",
                         _seq_of(event), allow_fallback)

    redacted_output = _redact(raw_text)
    if not normalized["patch"]["edits"]:
        event = _emit(emit, event_types.OPTIMIZER_NO_CHANGE, {
            **_issue_payload(
                "optimizer_no_change",
                "模型判断当前证据不足以支持修改，未给出补丁。",
                impact="本轮没有模型候选，将使用启发式回退（如启用）。",
                next_action="补充失败证据或检查 Skill 文件结构后新建运行。",
                technical_detail={
                    "diagnosis_summary": normalized["diagnosis"].get("summary", ""),
                    "hypothesis_count": len(normalized["hypotheses"]),
                },
            ),
        }, level="warning", call_id=call_id)
        return _fallback(context, emit, "optimizer_no_change", _seq_of(event),
                         allow_fallback, raw_output=redacted_output)

    _emit(emit, event_types.OPTIMIZER_OUTPUT_PARSED, {
        "diagnosis_cases": [finding["case_id"] for finding in
                            normalized["diagnosis"]["findings"]],
        "attribution": normalized["diagnosis"]["attribution"],
        "hypothesis_count": len(normalized["hypotheses"]),
        "edit_count": len(normalized["patch"]["edits"]),
        "changed_files": normalized["changed_files"],
        "parse_ok": True,
    }, call_id=call_id)

    _emit(emit, event_types.PATCH_CREATED, {
        "summary": normalized["patch"]["summary"],
        "changed_files": normalized["changed_files"],
        "edit_count": len(normalized["patch"]["edits"]),
        "used_fallback": False,
        "diagnosis": normalized["diagnosis"],
        "expected_effect": normalized["expected_effect"],
    }, call_id=call_id)

    return _make_result(status="ok", used_fallback=False,
                        raw_output=redacted_output, **normalized)


__all__ = [
    "ATTRIBUTIONS",
    "OPTIMIZER_PROTOCOL_VERSION",
    "OptimizerOutputError",
    "build_prompt",
    "generate_candidate",
    "heuristic_candidate",
    "parse_output",
    "validate_candidate_output",
]
