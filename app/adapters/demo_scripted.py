"""Deterministic demo model adapter.

This adapter is **not** a real model. It is a keyword-rule simulator used for the
offline demo environment and the automated acceptance suite, so the platform can be
exercised end-to-end without external credentials. It follows the fixed Skill files
(routing keyword table, parameter/mapping sections, clarify rules), therefore a
reviewed Skill patch really changes its behaviour in the next run.

Real deployments select ``openai_http`` or ``enterprise_ws`` instead; results from
different adapters must not be mixed (§41/§42 of the requirements).
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .base import ModelAdapter

USAGE_DIVISOR = 4

TITLE_PATTERNS = [
    re.compile(r"(?:创建任务|新建任务|建任务)[：:]\s*(.+?)(?:[（(]|，|,|$)"),
    re.compile(r"新建.*?任务[：:]\s*(.+?)(?:[（(]|，|,|$)"),
    re.compile(r"《([^》]+)》"),
    re.compile(r"“([^”]+)”"),
    re.compile(r"\"([^\"]+)\""),
]

FIELD_PATTERNS: dict[str, list[str]] = {
    "project_id": [r"\bP\d+\b"],
    "task_id": [r"\bT\d+\b"],
    "task_ids": [r"\bT\d+\b"],
    "assignee_id": [r"\bU\d+\b"],
    "depends_on": [r"\bT\d+\b"],
    "due_date": [r"\d{4}-\d{2}-\d{2}"],
    "filename": [r"[\w\-.]+\.\w+"],
    "hours": [r"(\d+(?:\.\d+)?)\s*(?:个)?小时"],
    "comment": [r"(?:评论|留言)[：:]\s*(.+?)\s*$"],
    "tag": [r"标签\s*([^\s，,；;]+)"],
    "template": [r"模板\s*([^\s，,；;]+)"],
    "keyword": [r"包含\s*(.+?)(?:\s*的任务)?\s*$", r"(?:搜索|查找)\s*(.+?)(?:\s*的任务)?\s*$"],
    "name": [r"里程碑\s*([^\s，,；;]+)"],
    "format": [r"(Excel|CSV|xlsx|csv)", r"格式\s*(Excel|CSV|xlsx|csv)"],
    "channel": [r"(app|email|sms)"],
}

STATUS_WORDS = {"进行中", "已完成", "待开始", "完成", "未开始"}
PRIORITY_WORDS = {"紧急", "普通", "低", "高"}


def _messages_text(messages: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _user_query(messages: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            text = content if isinstance(content, str) else _messages_text([message])
            text = text.strip()
            for prefix in ("用户请求：", "用户请求:", "用户："):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
            return text.split("\n上下文：")[0].strip()
    return ""


def _extract_section(text: str, start: str, end: str | None) -> str:
    index = text.find(start)
    if index < 0:
        return ""
    index += len(start)
    if end is None:
        return text[index:]
    stop = text.find(end, index)
    return text[index:stop] if stop >= 0 else text[index:]


def _parse_manifest(text: str) -> dict[str, Any]:
    block = _extract_section(text, "【子技能清单】", None)
    if not block:
        return {}
    start = block.find("{")
    if start < 0:
        return {}
    decoder = json.JSONDecoder()
    try:
        manifest, _ = decoder.raw_decode(block[start:].strip())
        return manifest if isinstance(manifest, dict) else {}
    except json.JSONDecodeError:
        return {}


def _read_files(messages: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    files: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        match = re.match(r"^【文件 ([^】]+)】\n(.*)$", content, re.S)
        if match:
            files[match.group(1)] = match.group(2)
    return files


def _parse_dispatch(main_skill: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in main_skill.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("skill_id", "---"):
            continue
        if set(cells[0]) <= {"-", ":"}:
            continue
        skill_id = cells[0]
        if not re.match(r"^[a-z][a-z0-9_]+$", skill_id):
            continue
        keywords = [kw for kw in re.split(r"[、,，;；]", cells[3]) if kw]
        rows.append({"skill_id": skill_id, "name": cells[1], "tool": cells[2],
                     "keywords": keywords})
    return rows


def _choose_skill(main_skill: str, query: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    rows = _parse_dispatch(main_skill)
    scored: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        matched = [kw for kw in row["keywords"] if kw in query]
        score = sum(len(kw) for kw in matched)
        scored.append((score, len(matched), -index, row))
    scored.sort(reverse=True)
    if not scored or scored[0][0] == 0:
        return None, rows
    return scored[0][3], rows


def _parse_params(skill_text: str) -> dict[str, Any]:
    fields: list[str] = []
    defaults: dict[str, str] = {}
    mappings: list[tuple[str, str]] = []
    required: list[str] = []
    for raw in skill_text.splitlines():
        line = raw.strip()
        bullet = re.match(r"^-\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[：:]", line)
        if bullet:
            fields.append(bullet.group(1))
            continue
        required_line = re.match(r"^[-*]?\s*必填参数[：:]\s*([a-zA-Z_][a-zA-Z0-9_]*)", line)
        if required_line:
            required.append(required_line.group(1))
            continue
        default_line = re.match(r"^默认[：:]\s*(.+)$", line)
        if default_line:
            for pair in re.split(r"[；;]", default_line.group(1)):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    defaults[key.strip()] = value.strip()
            continue
        if "映射" in line and "->" in line:
            for match in re.finditer(r"([^\s；;，,、：:]+?)\s*->\s*([^\s；;，,、：:]+)", line):
                mappings.append((match.group(1).strip(), match.group(2).strip()))
    for field in required:
        if field not in fields:
            fields.append(field)
    return {"fields": fields, "defaults": defaults, "mappings": mappings}


def _parse_clarify_rules(main_skill: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    section = _extract_section(main_skill, "## 澄清条件", None)
    if not section:
        return rules
    for line in section.splitlines():
        match = re.search(r"当\s*([a-z][a-z0-9_]+)\s*缺少\s*([^。\n]+?)\s*时", line)
        if not match:
            continue
        fields = [field.strip() for field in re.split(r"[、,，;；/\s]+", match.group(2))
                  if field.strip()]
        rules.append({"skill_id": match.group(1), "fields": fields})
    return rules


def _extract(field: str, query: str, mappings: list[tuple[str, str]],
             defaults: Mapping[str, str]) -> Any:
    if field == "status":
        candidates = [(query.find(key), key, value) for key, value in mappings
                      if key in query]
        if candidates:
            candidates.sort()
            return candidates[0][2]
        return None
    if field == "priority":
        candidates = [(query.find(key), key, value) for key, value in mappings
                      if key in query and key in PRIORITY_WORDS]
        if candidates:
            candidates.sort()
            return candidates[0][2]
        return None
    if field == "action":
        for word, value in (("转给", "assign"), ("转交", "assign"), ("指派", "assign"),
                            ("分配", "assign"), ("更新", "update"), ("归档", "archive")):
            if word in query:
                return value
        return None
    if field == "title":
        for pattern in TITLE_PATTERNS:
            match = pattern.search(query)
            if match:
                return match.group(1).strip()
        return None
    if field == "depends_on":
        matches = re.findall(r"\bT\d+\b", query)
        if len(matches) >= 2:
            return matches[1]
        return None
    if field in ("name", "tag", "template", "comment", "keyword"):
        patterns = FIELD_PATTERNS.get(field, [])
        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                return match.group(1).strip().rstrip("的")
        return None
    patterns = FIELD_PATTERNS.get(field, [])
    for pattern in patterns:
        if field == "task_ids":
            matches = re.findall(pattern, query)
            if matches:
                return matches
            continue
        match = re.search(pattern, query)
        if not match:
            continue
        value = match.group(1) if match.groups() else match.group(0)
        if field == "hours":
            try:
                number = float(value)
                return int(number) if number.is_integer() else number
            except ValueError:
                return None
        if field == "format":
            lowered = value.lower()
            if "excel" in lowered or lowered == "xlsx":
                return "xlsx"
            if "csv" in lowered:
                return "csv"
            return None
        return value
    if field in defaults:
        default = defaults[field]
        if field == "hours":
            try:
                return float(default)
            except ValueError:
                return default
        return default
    return None


def _field_present(field: str, query: str, mappings: list[tuple[str, str]]) -> bool:
    return _extract(field, query, mappings, {}) is not None


def _tool_call(name: str, arguments: dict[str, Any], index: int) -> dict[str, Any]:
    return {"id": f"call_{index}", "name": name, "arguments": arguments}


class DemoScriptedAdapter(ModelAdapter):
    """Keyword-rule simulator implementing the unified model adapter contract."""

    name = "demo_scripted"
    capabilities = {"routing": True, "tool_arguments": True, "native_tools": True,
                    "usage": True, "streaming": False}

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.config = dict(config or {})
        self.model = self.config.get("model") or "demo-scripted-v1"

    # -- adapter API -----------------------------------------------------
    def invoke(self, messages: list[dict[str, Any]], config: dict[str, Any] | None = None,
               tools: list[dict[str, Any]] | None = None, *, emit=None) -> dict[str, Any]:
        config = config or self.config
        text = _messages_text(messages)
        main_skill = _extract_section(text, "【主 SKILL.md】", "【子技能清单】")
        manifest = _parse_manifest(text)
        query = _user_query(messages)
        read_files = _read_files(messages)
        assistant_calls = [m for m in messages if m.get("role") == "assistant"
                           and m.get("tool_calls")]
        usage = {"input_tokens": max(1, len(text) // USAGE_DIVISOR),
                 "output_tokens": 16, "model": self.model}

        # Turn 1: route and read the chosen skill file.
        if not read_files:
            chosen, _rows = _choose_skill(main_skill, query)
            if chosen is None:
                return self._response(
                    tool_calls=[_tool_call("finish", {
                        "action": "no_action", "selected_skills": [],
                        "missing_fields": [], "summary": "未匹配任何子技能"}, 0)],
                    usage=usage, finish_reason="tool_calls")
            skill_path = self._skill_path(manifest, chosen["skill_id"])
            return self._response(
                tool_calls=[
                    _tool_call("select_skill", {"skill_id": chosen["skill_id"]}, 0),
                    _tool_call("read_skill", {"path": skill_path}, 1),
                ],
                usage=usage, finish_reason="tool_calls")

        # Turn 2: after reading the skill file, emit business tool calls.
        if len(assistant_calls) < 2:
            chosen = self._chosen_from_messages(messages, manifest)
            if chosen is None:
                return self._response(tool_calls=[], usage=usage,
                                      finish_reason="stop")
            skill_path = self._skill_path(manifest, chosen["skill_id"])
            skill_text = read_files.get(skill_path, "")
            params = _parse_params(skill_text)
            rules = _parse_clarify_rules(main_skill)
            for rule in rules:
                if rule["skill_id"] != chosen["skill_id"]:
                    continue
                missing = [field for field in rule["fields"]
                           if not _field_present(field, query, params["mappings"])]
                if missing:
                    return self._response(
                        tool_calls=[_tool_call("finish", {
                            "action": "clarify",
                            "selected_skills": [chosen["skill_id"]],
                            "missing_fields": missing,
                            "summary": "缺少必要字段，需要用户补充"}, 0)],
                        usage=usage, finish_reason="tool_calls")
            arguments: dict[str, Any] = {}
            for field in params["fields"]:
                value = _extract(field, query, params["mappings"], params["defaults"])
                if value is not None:
                    arguments[field] = value
            calls = [_tool_call(chosen["tool"], arguments, 0)]
            return self._response(tool_calls=calls, usage=usage,
                                  finish_reason="tool_calls")

        # Turn 3: finish with the observed result.
        chosen = self._chosen_from_messages(messages, manifest)
        selected = [chosen["skill_id"]] if chosen else []
        action = "tool_call" if selected else "no_action"
        return self._response(
            tool_calls=[_tool_call("finish", {
                "action": action,
                "selected_skills": selected,
                "missing_fields": [],
                "summary": "已完成业务工具调用"}, 0)],
            usage=usage, finish_reason="tool_calls")

    # -- helpers ---------------------------------------------------------
    def _response(self, *, tool_calls: list[dict[str, Any]], usage: dict[str, Any],
                  finish_reason: str) -> dict[str, Any]:
        return {
            "content": "",
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
            "model": self.model,
            "raw": {"provider": "demo_scripted"},
        }

    @staticmethod
    def _skill_path(manifest: Mapping[str, Any], skill_id: str) -> str:
        for item in manifest.get("subskills") or []:
            if item.get("skill_id") == skill_id:
                return item.get("path", "")
        return ""

    def _chosen_from_messages(self, messages: Sequence[Mapping[str, Any]],
                              manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        skill_id = None
        for message in messages:
            for call in message.get("tool_calls") or []:
                name = call.get("name") or (call.get("function") or {}).get("name")
                arguments = call.get("arguments") or (call.get("function") or {}).get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if name == "select_skill" and isinstance(arguments, dict):
                    skill_id = arguments.get("skill_id")
        if not skill_id:
            read_files = _read_files(messages)
            path = next(iter(read_files), "")
            for item in manifest.get("subskills") or []:
                if item.get("path") == path:
                    skill_id = item.get("skill_id")
        return {"skill_id": skill_id, "tool": self._tool_for(manifest, skill_id)} \
            if skill_id else None

    @staticmethod
    def _tool_for(manifest: Mapping[str, Any], skill_id: str | None) -> str:
        # The business tool mapping lives in the fixed tools protocol; the demo
        # adapter knows the demo package's mapping through the dispatch table it
        # parsed on the first turn, so keep a static fallback here.
        return DEMO_SKILL_TOOLS.get(skill_id or "", "")


DEMO_SKILL_TOOLS = {
    "task_query": "list_tasks",
    "task_create": "create_task",
    "task_update_status": "update_task_status",
    "task_assign": "assign_task",
    "task_priority": "set_priority",
    "project_query": "get_project",
    "project_members": "list_members",
    "task_comment": "add_comment",
    "task_attachment": "add_attachment",
    "task_time_log": "log_work",
    "task_dependency": "add_dependency",
    "task_milestone": "create_milestone",
    "task_tag": "add_tag",
    "task_search": "search_tasks",
    "task_batch": "batch_update_tasks",
    "task_export": "export_tasks",
    "task_stats": "query_stats",
    "task_reminder": "create_reminder",
    "task_template": "create_from_template",
    "task_archive": "archive_task",
    "permission_check": "check_permission",
}
