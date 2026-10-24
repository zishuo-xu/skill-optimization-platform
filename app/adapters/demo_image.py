"""Deterministic image-editing demo adapter (progressive reference loading).

This is the second demo domain. Like :mod:`demo_scripted` it is a rule
simulator, not a real model: it follows the Skill dispatch table, the selected
reference file and the clarify section, so a reviewed Skill patch changes its
behaviour in the next run. The demo environment selects it through
``model_config.model = "demo-image-v1"`` (or ``domain = "image"``).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .base import ModelAdapter
from .demo_scripted import (
    USAGE_DIVISOR,
    _choose_skill,
    _extract_section,
    _messages_text,
    _parse_clarify_rules,
    _parse_dispatch,
    _parse_manifest,
    _read_files,
    _tool_call,
    _user_query,
)

SCRIPT_PATH = "scripts/ImageEditorAdaptor.ets"
BUSINESS_TOOL = "imageEditWorkflow"
COLORS = ("红色", "蓝色", "绿色", "紫色", "黑色", "白色", "粉色", "橙色",
          "黄色", "灰色", "暖色", "冷色", "胶片", "电影感")


def _parse_image_params(text: str) -> dict[str, Any]:
    """Parse a reference's ``## 参数`` / ``## 默认`` / ``## 映射`` sections."""
    fields: list[str] = []
    defaults: dict[str, str] = {}
    mappings: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        required = re.match(r"^-\s*必填参数[：:]\s*([A-Za-z_][A-Za-z0-9_]*)", line)
        if required:
            if required.group(1) not in fields:
                fields.append(required.group(1))
            continue
        bullet = re.match(r"^-\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:[：:]|$)", line)
        if bullet:
            if bullet.group(1) not in fields:
                fields.append(bullet.group(1))
            continue
        default = re.match(r"^默认[：:]\s*(.+)$", line)
        if default:
            for pair in re.split(r"[；;]", default.group(1)):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    defaults[key.strip()] = value.strip()
            continue
        if "->" in line:
            for match in re.finditer(
                    r"([^\s；;，,、：:]+?)\s*->\s*([^\s；;，,、：:]+)", line):
                mappings[match.group(1).strip()] = match.group(2).strip()
    return {"fields": fields, "defaults": defaults, "mappings": mappings}


def _extract_value(field: str, query: str, params: Mapping[str, Any]) -> Any:
    defaults = params.get("defaults") or {}
    mappings = params.get("mappings") or {}
    if field == "useOneWordFixPicture":
        return query.strip()
    if field == "targetColor":
        for key in sorted(mappings, key=len, reverse=True):
            if key in query:
                return mappings[key]
        for color in COLORS:
            if color in query:
                return color
        return None
    if field == "keepSubject":
        return True
    if field == "enhanceType":
        if any(word in query for word in ("提亮", "变亮", "暗", "曝光", "夜景")):
            return "lighting"
        if any(word in query for word in ("清晰", "锐化", "模糊")):
            return "clarity"
        if "降噪" in query:
            return "denoise"
        return "auto"
    if field == "strength":
        value = defaults.get(field, "0.8")
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if field in defaults:
        return defaults[field]
    return None


def _tool_for(main_skill: str, skill_id: str | None) -> str:
    for row in _parse_dispatch(main_skill):
        if row["skill_id"] == skill_id:
            return row["tool"]
    return ""


class DemoImageScriptedAdapter(ModelAdapter):
    """Rule simulator for the image-editing sample project."""

    name = "demo_scripted"
    capabilities = {"routing": True, "tool_arguments": True, "native_tools": True,
                    "usage": True, "streaming": False}

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.config = dict(config or {})
        self.model = self.config.get("model") or "demo-image-v1"

    def invoke(self, messages: list[dict[str, Any]],
               config: dict[str, Any] | None = None,
               tools: list[dict[str, Any]] | None = None, *,
               emit=None) -> dict[str, Any]:
        text = _messages_text(messages)
        main_skill = _extract_section(text, "【主 SKILL.md】", "【子技能清单】")
        manifest = _parse_manifest(text)
        query = _user_query(messages)
        read_files = _read_files(messages)
        assistant_calls = [message for message in messages
                           if message.get("role") == "assistant"
                           and message.get("tool_calls")]
        usage = {"input_tokens": max(1, len(text) // USAGE_DIVISOR),
                 "output_tokens": 16, "model": self.model}

        if not read_files:
            chosen, rows = _choose_skill(main_skill, query)
            if chosen is None:
                return self._response(
                    tool_calls=[_tool_call("finish", {
                        "action": "no_action", "selected_skills": [],
                        "missing_fields": [], "summary": "未匹配任何图片编辑子技能"}, 0)],
                    usage=usage)
            skill_path = self._skill_path(manifest, chosen["skill_id"])
            return self._response(
                tool_calls=[
                    _tool_call("select_skill", {"skill_id": chosen["skill_id"]}, 0),
                    _tool_call("read_skill", {"path": skill_path}, 1),
                ], usage=usage)

        chosen = self._chosen_from_messages(messages, manifest)
        if chosen is None:
            return self._response(tool_calls=[], usage=usage)
        skill_id = chosen["skill_id"]
        skill_path = self._skill_path(manifest, skill_id)
        skill_text = read_files.get(skill_path, "")
        params = _parse_image_params(skill_text)

        # Clarify rules are maintained in SKILL.md by the optimizer.
        for rule in _parse_clarify_rules(main_skill):
            if rule["skill_id"] != skill_id:
                continue
            missing = [field for field in rule["fields"]
                       if _extract_value(field, query, params) is None]
            if missing:
                return self._response(
                    tool_calls=[_tool_call("finish", {
                        "action": "clarify", "selected_skills": [skill_id],
                        "missing_fields": missing,
                        "summary": "缺少必要参数，需要用户补充"}, 0)],
                    usage=usage)

        if len(assistant_calls) < 2:
            arg_values: dict[str, Any] = {}
            for field in params["fields"]:
                value = _extract_value(field, query, params)
                if value is not None:
                    arg_values[field] = value
            method = _tool_for(main_skill, skill_id)
            arguments = {
                "method": method,
                "script": SCRIPT_PATH,
                "request": {"query": query},
                "args": arg_values,
            }
            return self._response(
                tool_calls=[_tool_call(BUSINESS_TOOL, arguments, 0)],
                usage=usage, finish_reason="tool_calls")

        return self._response(
            tool_calls=[_tool_call("finish", {
                "action": "tool_call", "selected_skills": [skill_id],
                "missing_fields": [], "summary": "已完成图片编辑工作流调用"}, 0)],
            usage=usage, finish_reason="tool_calls")

    def _response(self, *, tool_calls: list[dict[str, Any]],
                  usage: dict[str, Any], finish_reason: str = "tool_calls") -> dict[str, Any]:
        return {
            "content": "",
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
            "model": self.model,
            "raw": {"provider": "demo_scripted", "domain": "image"},
        }

    @staticmethod
    def _skill_path(manifest: Mapping[str, Any], skill_id: str) -> str:
        for item in manifest.get("subskills") or []:
            if item.get("skill_id") == skill_id:
                return item.get("path", "")
        return ""

    @staticmethod
    def _chosen_from_messages(messages: Sequence[Mapping[str, Any]],
                              manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        skill_id = None
        for message in messages:
            for call in message.get("tool_calls") or []:
                name = call.get("name") or (call.get("function") or {}).get("name")
                arguments = call.get("arguments") or \
                    (call.get("function") or {}).get("arguments")
                if isinstance(arguments, str):
                    try:
                        import json
                        arguments = json.loads(arguments)
                    except Exception:  # pragma: no cover - defensive
                        arguments = {}
                if name == "select_skill" and isinstance(arguments, Mapping):
                    skill_id = arguments.get("skill_id")
        if not skill_id:
            for item in manifest.get("subskills") or []:
                if item.get("path") in _read_files(messages):
                    skill_id = item.get("skill_id")
                    break
        return {"skill_id": skill_id} if skill_id else None
