"""Fixed text-protocol parsers (no model guessing, frozen protocol).

Some adapters (routing-only Python scripts, text models, the demo adapter) return
plain text instead of native tool calls. The platform converts that text with the
frozen parsers in this module:

* routing: exactly two labelled lines, ``依据：...`` + ``子技能：<skill_id>``
  (the basis is only a human summary and is *never* used as routing evidence);
* tool calls: ``工具：<name> <json object>`` (one line per call, order preserved);
* finish: ``结束：{"action": ..., "selected_skills": [...],
  "missing_fields": [...], "summary": "..."}``.

Unknown names, missing lines, ambiguous duplicates or truncated JSON raise
:class:`TextParseError` with ``code="parse_failed"``; the executor records a
capability failure instead of asking another model to reinterpret the output.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ACTION_VALUES", "TextParseError", "parse_finish", "parse_routing",
    "parse_text_output", "parse_tool_calls",
]

ACTION_VALUES = ("tool_call", "clarify", "no_action")

_BASIS_RE = re.compile(r"^(?:依据|依据摘要|理由|判断依据)\s*[:：]\s*(.*)$")
_SKILLS_RE = re.compile(r"^(?:子技能|子技能ID|子技能id|子技能列表)\s*[:：]\s*(.*)$")
_TOOL_RE = re.compile(r"^(?:工具|工具调用|调用工具|tool|tool_call|tool-call|tool call)\s*[:：]\s*(.*)$",
                      re.IGNORECASE)
_FINISH_RE = re.compile(r"^(?:结束|完成|finish)\s*[:：]\s*(.*)$", re.IGNORECASE)
_FENCE_RE = re.compile(r"^```")
_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.:-]*)\s*(.*)$", re.DOTALL)
_SKILL_TOKEN_RE = re.compile(r"^[^\s,，、;；|/]+$")


class TextParseError(ValueError):
    """A deterministic protocol failure (mapped to ``parse_failed``)."""

    code = "parse_failed"

    def __init__(self, message: str, *, detail: Any = None, line: int | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.line = line


def _clean_lines(text: str) -> list[tuple[int, str]]:
    if not isinstance(text, str):
        raise TextParseError("模型输出不是文本，无法使用文本协议解析")
    lines: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if _FENCE_RE.match(stripped):
            continue  # tolerate Markdown fences, never interpret them as data
        if stripped.startswith("#"):
            continue
        lines.append((lineno, stripped))
    if not lines:
        raise TextParseError("模型输出为空，无法解析文本协议")
    return lines


def _scan(text: str, *, allow_routing: bool, allow_tools: bool,
          allow_finish: bool) -> dict[str, list[tuple[int, str]]]:
    """Collect labelled protocol lines; reject any unrecognised content."""
    found: dict[str, list[tuple[int, str]]] = {
        "basis": [], "skills": [], "tools": [], "finish": []}
    for lineno, line in _clean_lines(text):
        match = _BASIS_RE.match(line)
        if match and allow_routing:
            found["basis"].append((lineno, match.group(1).strip()))
            continue
        match = _SKILLS_RE.match(line)
        if match and allow_routing:
            found["skills"].append((lineno, match.group(1).strip()))
            continue
        match = _FINISH_RE.match(line)
        if match and allow_finish:
            found["finish"].append((lineno, match.group(1).strip()))
            continue
        match = _TOOL_RE.match(line)
        if match and allow_tools:
            found["tools"].append((lineno, match.group(1).strip()))
            continue
        raise TextParseError(
            f"存在无法识别的协议行（第 {lineno} 行），无法确定如何解析",
            detail={"line": lineno, "text": line[:200]}, line=lineno)
    return found


def _split_skill_tokens(value: str) -> list[str]:
    if not value:
        raise TextParseError("子技能行为空（输出被截断或缺少名称）")
    tokens: list[str] = []
    for raw_token in re.split(r"[,，、;；|/]+|\s{2,}|\s+", value):
        token = raw_token.strip().strip("`'\"[]<>").strip()
        if not token:
            continue
        # "task_query（references/…）" or "task_query -> references/…"
        token = re.split(r"[（(]|->|→", token, maxsplit=1)[0].strip().strip("`'\"")
        if token:
            tokens.append(token)
    if not tokens:
        raise TextParseError("子技能行缺少有效的子技能名称（输出被截断）")
    return tokens


def parse_routing(text: str, *, known_skill_ids: Iterable[str] | None = None,
                  aliases: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Parse ``依据 + 子技能`` two-line output.

    Returns ``{"skills": [...], "basis": str, "raw_text": str}``. Raises
    :class:`TextParseError` for missing/duplicated lines or unknown names.
    """
    found = _scan(text, allow_routing=True, allow_tools=False, allow_finish=False)
    if len(found["basis"]) != 1:
        raise TextParseError(
            "缺少唯一的“依据”行（应为两行固定输出：依据 + 子技能）",
            detail={"count": len(found["basis"])})
    if len(found["skills"]) != 1:
        raise TextParseError(
            "缺少唯一的“子技能”行（应为两行固定输出：依据 + 子技能）",
            detail={"count": len(found["skills"])})
    basis = found["basis"][0][1]
    if not basis:
        raise TextParseError("依据行为空（输出被截断）")
    tokens = _split_skill_tokens(found["skills"][0][1])
    known = set(known_skill_ids) if known_skill_ids is not None else None
    alias_map = {str(k): str(v) for k, v in (aliases or {}).items()}
    resolved: list[str] = []
    for token in tokens:
        name = alias_map.get(token, token)
        if not _SKILL_TOKEN_RE.match(name):
            raise TextParseError("子技能名称包含无法识别的字符，拒绝猜测",
                                 detail={"token": token})
        if known is not None and name not in known:
            raise TextParseError(f"未知的子技能名称: {name}", detail={"token": token})
        if name not in resolved:
            resolved.append(name)
    return {"skills": resolved, "basis": basis, "raw_text": text}


def parse_tool_calls(text: str, *, known_tool_names: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Parse fixed ``工具：<name> <json>`` lines into business tool calls.

    Order is preserved. Unknown names / missing or truncated JSON raise
    :class:`TextParseError` (``parse_failed``), never a guessed call.
    """
    found = _scan(text, allow_routing=False, allow_tools=True, allow_finish=False)
    known = set(known_tool_names) if known_tool_names is not None else None
    calls: list[dict[str, Any]] = []
    for lineno, value in found["tools"]:
        match = _NAME_RE.match(value)
        if not match:
            raise TextParseError(
                f"工具行格式错误（第 {lineno} 行）：应为 工具：<name> <JSON 参数>",
                detail={"line": lineno}, line=lineno)
        name, rest = match.group(1), match.group(2).strip()
        if known is not None and name not in known:
            raise TextParseError(f"未知的工具名称: {name}",
                                 detail={"tool": name}, line=lineno)
        if not rest:
            raise TextParseError(f"工具调用缺少 JSON 参数: {name}",
                                 detail={"tool": name}, line=lineno)
        if not rest.startswith("{"):
            raise TextParseError(f"工具调用参数必须是 JSON 对象: {name}",
                                 detail={"tool": name, "raw": rest[:200]}, line=lineno)
        try:
            arguments = json.loads(rest)
        except (TypeError, ValueError) as exc:
            truncated = _looks_truncated(rest)
            raise TextParseError(
                f"工具调用参数 JSON {'被截断' if truncated else '不合法'}: {name}",
                detail={"tool": name, "raw": rest[:500], "truncated": truncated},
                line=lineno) from exc
        if not isinstance(arguments, Mapping):
            raise TextParseError(f"工具调用参数必须是 JSON 对象: {name}",
                                 detail={"tool": name}, line=lineno)
        calls.append({"name": name, "arguments": dict(arguments)})
    return calls


def parse_finish(text: str) -> dict[str, Any] | None:
    """Parse the optional fixed ``结束：{...}`` line; ``None`` when absent."""
    found = _scan(text, allow_routing=False, allow_tools=False, allow_finish=True)
    if not found["finish"]:
        return None
    if len(found["finish"]) > 1:
        raise TextParseError("存在多个“结束”行，协议输出有歧义",
                             detail={"count": len(found["finish"])})
    lineno, raw = found["finish"][0]
    if not raw:
        raise TextParseError("结束行为空（输出被截断）", line=lineno)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise TextParseError(
            f"结束行 JSON {'被截断' if _looks_truncated(raw) else '不合法'}",
            detail={"raw": raw[:500]}, line=lineno) from exc
    if not isinstance(payload, Mapping):
        raise TextParseError("结束行必须是 JSON 对象", line=lineno)
    action = payload.get("action")
    if not isinstance(action, str) or action not in ACTION_VALUES:
        raise TextParseError("结束行的 action 非法（应为 tool_call/clarify/no_action）",
                             detail={"action": action}, line=lineno)
    selected = payload.get("selected_skills")
    if selected is not None and not isinstance(selected, list):
        raise TextParseError("结束行的 selected_skills 必须是数组", line=lineno)
    missing = payload.get("missing_fields")
    if missing is not None and not isinstance(missing, list):
        raise TextParseError("结束行的 missing_fields 必须是数组", line=lineno)
    result: dict[str, Any] = {"action": action}
    if selected is not None:
        result["selected_skills"] = [str(item) for item in selected]
    if missing is not None:
        result["missing_fields"] = [str(item) for item in missing]
    summary = payload.get("summary")
    if summary is not None:
        result["summary"] = str(summary)
    return result


def _looks_truncated(raw: str) -> bool:
    """Heuristic *for the error message only* – never for guessing values."""
    text = raw.strip()
    if not text:
        return True
    if text.count("{") != text.count("}") or text.count("[") != text.count("]"):
        return True
    # Odd number of unescaped quotes means the JSON string was cut mid-token.
    unescaped = len(re.findall(r'(?<!\\)"', text))
    return unescaped % 2 == 1


def parse_text_output(text: str, capabilities: Mapping[str, bool], *,
                      known_skill_ids: Iterable[str] | None = None,
                      aliases: Mapping[str, str] | None = None,
                      known_tool_names: Iterable[str] | None = None,
                      require_routing: bool = True,
                      allow_missing_routing: bool = False) -> dict[str, Any]:
    """Parse one text response according to the frozen capability declaration.

    Returns ``{"skills", "basis", "tool_calls", "finish"}``. When
    ``capabilities.tool_arguments`` is false only routing is parsed (a routing
    script cannot be scored on arguments, A13). ``allow_missing_routing`` lets a
    follow-up turn (after tools already ran) omit repeated routing lines while
    still rejecting any unrecognised content.
    """
    caps = capabilities or {}
    parse_routing_enabled = bool(caps.get("routing", True)) or require_routing
    parse_tools_enabled = bool(caps.get("tool_arguments", False))
    allow_tools = parse_tools_enabled
    allow_finish = parse_tools_enabled or parse_routing_enabled
    # A single pass validates that no unrecognised lines are silently ignored.
    found = _scan(text, allow_routing=parse_routing_enabled,
                  allow_tools=allow_tools, allow_finish=allow_finish)

    skills: list[str] = []
    basis = ""
    routing_labels_present = bool(found["basis"] or found["skills"])
    if parse_routing_enabled and (routing_labels_present or not allow_missing_routing):
        if len(found["basis"]) != 1 or len(found["skills"]) != 1:
            raise TextParseError(
                "缺少唯一的“依据/子技能”两行输出",
                detail={"basis_lines": len(found["basis"]),
                        "skill_lines": len(found["skills"])})
        basis = found["basis"][0][1]
        if not basis:
            raise TextParseError("依据行为空（输出被截断）")
        tokens = _split_skill_tokens(found["skills"][0][1])
        known = set(known_skill_ids) if known_skill_ids is not None else None
        alias_map = {str(k): str(v) for k, v in (aliases or {}).items()}
        for token in tokens:
            name = alias_map.get(token, token)
            if known is not None and name not in known:
                raise TextParseError(f"未知的子技能名称: {name}", detail={"token": token})
            if name not in skills:
                skills.append(name)

    tool_calls: list[dict[str, Any]] = []
    if parse_tools_enabled:
        known_tools = set(known_tool_names) if known_tool_names is not None else None
        for lineno, value in found["tools"]:
            match = _NAME_RE.match(value)
            if not match:
                raise TextParseError(f"工具行格式错误（第 {lineno} 行）",
                                     detail={"line": lineno}, line=lineno)
            name, rest = match.group(1), match.group(2).strip()
            if known_tools is not None and name not in known_tools:
                raise TextParseError(f"未知的工具名称: {name}",
                                     detail={"tool": name}, line=lineno)
            if not rest.startswith("{"):
                raise TextParseError(f"工具调用参数必须是 JSON 对象: {name}",
                                     detail={"tool": name}, line=lineno)
            try:
                arguments = json.loads(rest)
            except (TypeError, ValueError) as exc:
                raise TextParseError(
                    f"工具调用参数 JSON {'被截断' if _looks_truncated(rest) else '不合法'}: {name}",
                    detail={"tool": name, "raw": rest[:500]}, line=lineno) from exc
            if not isinstance(arguments, Mapping):
                raise TextParseError(f"工具调用参数必须是 JSON 对象: {name}",
                                     detail={"tool": name}, line=lineno)
            tool_calls.append({"name": name, "arguments": dict(arguments)})

    finish: dict[str, Any] | None = None
    if found["finish"]:
        if len(found["finish"]) > 1:
            raise TextParseError("存在多个“结束”行，协议输出有歧义")
        lineno, raw = found["finish"][0]
        try:
            payload = json.loads(raw) if raw else None
        except (TypeError, ValueError) as exc:
            raise TextParseError("结束行 JSON 不合法或被截断",
                                 detail={"raw": raw[:500]}, line=lineno) from exc
        if not isinstance(payload, Mapping):
            raise TextParseError("结束行必须是 JSON 对象", line=lineno)
        action = payload.get("action")
        if action not in ACTION_VALUES:
            raise TextParseError("结束行的 action 非法", detail={"action": action},
                                 line=lineno)
        finish = {"action": action}
        for key in ("selected_skills", "missing_fields"):
            if payload.get(key) is not None:
                if not isinstance(payload[key], list):
                    raise TextParseError(f"结束行的 {key} 必须是数组", line=lineno)
                finish[key] = [str(item) for item in payload[key]]
        if payload.get("summary") is not None:
            finish["summary"] = str(payload["summary"])

    return {"skills": skills, "basis": basis, "tool_calls": tool_calls, "finish": finish}
