"""Deterministic image-editing demo adapter (progressive reference loading).

This is the second demo domain. Like :mod:`demo_scripted` it is a rule
simulator, not a real model: it follows the Skill dispatch table, the selected
reference file and the clarify section, so a reviewed Skill patch changes its
behaviour in the next run. The demo environment selects it through
``model_config.model = "demo-image-v1"`` (or ``domain = "image"``).
"""

from __future__ import annotations

import json
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
from .image_params import contract_params, parse_contract

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


def _is_business_call(call: Mapping[str, Any], business_tool: str) -> bool:
    """Recognize the native wrapper while tolerating provider-normalized calls."""
    name = call.get("name") or (call.get("function") or {}).get("name")
    if name != "call_tool":
        return False
    arguments = call.get("arguments") or (call.get("function") or {}).get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            return False
    return isinstance(arguments, Mapping) and arguments.get("name") == business_tool


class DemoImageScriptedAdapter(ModelAdapter):
    """Rule simulator for the image-editing sample project."""

    name = "demo_scripted"
    capabilities = {"routing": True, "tool_arguments": True, "native_tools": True,
                    "usage": True, "streaming": False}

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.config = dict(config or {})
        self.model = self.config.get("model") or "demo-image-v1"

    def _invoke_legacy(self, messages: list[dict[str, Any]],
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
                tool_calls=[_tool_call("call_tool", {"name": BUSINESS_TOOL, "arguments": arguments}, 0)],
                usage=usage, finish_reason="tool_calls")

        return self._response(
            tool_calls=[_tool_call("finish", {
                "action": "tool_call", "selected_skills": [skill_id],
                "missing_fields": [], "summary": "已完成图片编辑工作流调用"}, 0)],
            usage=usage, finish_reason="tool_calls")

    def invoke(self, messages: list[dict[str, Any]],
               config: dict[str, Any] | None = None,
               tools: list[dict[str, Any]] | None = None, *,
               emit=None) -> dict[str, Any]:
        text = _messages_text(messages)
        main_skill = _extract_section(text, "【主 SKILL.md】", "【子技能清单】")
        manifest = _parse_manifest(text)
        if int(manifest.get("version", 1)) < 2:
            return self._invoke_legacy(messages, config, tools, emit=emit)
        query = _user_query(messages)
        read_files = _read_files(messages)
        assistant_calls = [message for message in messages
                           if message.get("role") == "assistant"
                           and message.get("tool_calls")]
        usage = {"input_tokens": max(1, len(text) // USAGE_DIVISOR),
                 "output_tokens": 16, "model": self.model}

        selected = self._selected_from_messages(messages, manifest)
        chosen_skills = self._choose_skills(main_skill, query)
        # Count resolved operation skills, so conjunctions or repeated
        # keywords inside one operation do not consume the cap.
        if len(chosen_skills) > 3:
            return self._response(tool_calls=[_tool_call("finish", {
                "action": "clarify", "selected_skills": [],
                "missing_fields": ["operations"], "summary": "一次最多处理三个操作"}, 0)], usage=usage)
        if not read_files:
            if not chosen_skills:
                return self._response(
                    tool_calls=[_tool_call("finish", {
                        "action": "no_action", "selected_skills": [],
                        "missing_fields": [], "summary": "未匹配任何图片编辑子技能"}, 0)],
                    usage=usage)
            skill_path = self._skill_path(manifest, chosen_skills[0]["skill_id"])
            return self._response(
                tool_calls=[
                    _tool_call("select_skill", {"skill_id": chosen_skills[0]["skill_id"]}, 0),
                    _tool_call("read_skill", {"path": skill_path}, 1),
                ], usage=usage)

        # Progressive loading: one reference per model turn, in user order.
        loaded_ids = {item["skill_id"] for item in selected
                      if item.get("skill_id") in {x.get("skill_id") for x in manifest.get("subskills") or []}}
        for item in chosen_skills:
            if item["skill_id"] not in loaded_ids:
                return self._response(tool_calls=[
                    _tool_call("select_skill", {"skill_id": item["skill_id"]}, 0),
                    _tool_call("read_skill", {"path": self._skill_path(manifest, item["skill_id"])}, 1),
                ], usage=usage)

        if not chosen_skills:
            return self._response(tool_calls=[], usage=usage)
        business_sent = any(
            _is_business_call(call, BUSINESS_TOOL)
            for message in messages if message.get("role") == "assistant"
            for call in (message.get("tool_calls") or [])
        )
        if not business_sent:
            calls = []
            all_missing: list[str] = []
            for item in chosen_skills:
                skill_id = item["skill_id"]
                skill_path = self._skill_path(manifest, skill_id)
                skill_text = read_files.get(skill_path, "")
                contract = parse_contract(skill_text)
                if contract is not None:
                    clause = str(item.get("_clause") or query)
                    arg_values, missing = contract_params(contract, clause)
                    all_missing.extend(str(field) for field in missing)
                    method = str(contract.get("method") or item.get("tool") or "")
                else:
                    params = _parse_image_params(skill_text)
                    arg_values = {field: value for field in params["fields"]
                                  if (value := _extract_value(field, query, params)) is not None}
                    missing = [params["fields"][0]] if params["fields"] and not arg_values else []
                    method = str(item.get("tool") or _tool_for(main_skill, skill_id))
                if missing:
                    continue
                calls.append(_tool_call("call_tool", {
                    "name": BUSINESS_TOOL,
                    "arguments": {
                        "method": method, "script": SCRIPT_PATH,
                        "request": {"query": query}, "args": arg_values,
                    },
                }, len(calls)))
            if all_missing:
                return self._response(tool_calls=[_tool_call("finish", {
                    "action": "clarify", "selected_skills": [x["skill_id"] for x in chosen_skills],
                    "missing_fields": all_missing,
                    "summary": "缺少必要参数，需要用户补充"}, 0)], usage=usage)
            return self._response(tool_calls=calls, usage=usage, finish_reason="tool_calls")

        return self._response(
            tool_calls=[_tool_call("finish", {
                "action": "tool_call", "selected_skills": [x["skill_id"] for x in chosen_skills],
                "missing_fields": [], "summary": "已完成图片编辑工作流调用"}, 0)],
            usage=usage, finish_reason="tool_calls")

    @staticmethod
    def _selected_from_messages(messages: Sequence[Mapping[str, Any]],
                                manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        by_id = {str(item.get("skill_id")): item for item in manifest.get("subskills") or []
                 if isinstance(item, Mapping) and item.get("skill_id")}
        for message in messages:
            for call in message.get("tool_calls") or []:
                name = call.get("name") or (call.get("function") or {}).get("name")
                args = call.get("arguments") or (call.get("function") or {}).get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if name == "select_skill" and isinstance(args, Mapping):
                    skill_id = str(args.get("skill_id") or "")
                    if skill_id in by_id and not any(x["skill_id"] == skill_id for x in selected):
                        selected.append(by_id[skill_id])
        return selected

    @staticmethod
    def _choose_skills(main_skill: str, query: str) -> list[dict[str, Any]]:
        """Conservative demo routing; genuine language understanding uses a model.

        Split independent instructions, retain modifiers with their operation,
        and merge compatible fields of the same subskill. Negated clauses never
        re-enter the route through a later scan of the original query.
        """
        rows = {row["skill_id"]: row for row in _parse_dispatch(main_skill)}
        patterns = (
            ("image_background_remove", r"抠图|去背景|去掉.*背景|去除.*(?:背景|底色)|透明底|透明背景"),
            ("image_background_replace", r"换背景|替换背景|背景.*(?:换成|替换为)|(?:换成|改成|替换为).*背景"),
            ("image_tone_adjust", r"亮度|对比度|饱和度|曝光"),
            ("image_filter", r"滤镜|黑白|胶片|复古"),
            ("image_portrait_retouch", r"磨皮|美白|瘦脸|美颜"),
            ("image_resize", r"宽度|高度|宽高|尺寸|缩放|宽\s*\d|高\s*\d"),
            ("image_transform", r"旋转|翻转|镜像|顺时针|逆时针|变换"),
            ("image_crop", r"裁剪|裁切|裁成|裁一下"),
            ("image_color_transfer", r"换色|调成|调[蓝红绿紫黄橙暖冷]|换成.*色|弄成.*色|变成.*色|换个?颜色"),
            ("image_enhance", r"提亮|变亮|清晰|降噪|增强|噪点|模糊|太暗"),
            ("image_object_remove", r"移除|移掉|删除|擦除|去掉.*(?:路人|杂物|物体|电线杆|水印)|路人去掉"),
        )
        effective = query
        correction = re.search(r"(?:不对|算了)[，,\s]*(.*)", effective)
        if correction:
            effective = correction.group(1)
        chunks = [piece.strip() for piece in re.split(
            r"(?:然后|接着|最后|再|之后|后(?=把|将)|[，,；;、])", effective) if piece.strip()]
        chosen: list[dict[str, Any]] = []
        for chunk in chunks:
            if re.match(r"(?:先)?(?:不要|别|不是|不做)", chunk) or re.search(r"(?:不要|别做)[。！!]*$", chunk):
                continue
            skill_id = next((sid for sid, pattern in patterns if sid in rows and re.search(pattern, chunk)), None)
            if skill_id is None:
                fallback, _ = _choose_skill(main_skill, chunk)
                if fallback and fallback["skill_id"] == "image_one_word_fix":
                    skill_id = fallback["skill_id"]
            if skill_id is None:
                if chosen:
                    chosen[-1]["_clause"] += "，" + chunk
                continue
            existing = next((item for item in chosen if item["skill_id"] == skill_id), None)
            if existing:
                existing["_clause"] += "，" + chunk
            else:
                chosen.append({**rows[skill_id], "_clause": chunk})
        if len(chosen) > 1:
            chosen = [item for item in chosen if item["skill_id"] != "image_one_word_fix"]
        return chosen

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
