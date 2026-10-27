"""Contract-driven parameter extraction for the expanded image sample.

This module deliberately knows nothing about datasets or expected answers.  It
reads the JSON contract embedded in one reference and performs conservative
Chinese utterance normalization for the fields declared there.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping


def parse_contract(text: str) -> dict[str, Any] | None:
    marker = text.find("## 参数契约")
    if marker < 0:
        return None
    block = text[marker:]
    start = block.find("```json")
    if start < 0:
        start = block.find("{")
    else:
        start += len("```json")
    if start < 0:
        return None
    object_start = block[start:].find("{")
    if object_start < 0:
        return None
    start += object_start
    try:
        value, _ = json.JSONDecoder().raw_decode(block[start:])
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _number(value: str) -> float | int | None:
    value = value.strip().replace("，", "")
    try:
        n = float(value)
        return int(n) if n.is_integer() else n
    except ValueError:
        pass
    if any(unit in value for unit in ("十", "百", "千")):
        total = 0
        current = 0
        units = {"十": 10, "百": 100, "千": 1000}
        for char in value:
            if char in units:
                total += (current or 1) * units[char]
                current = 0
            elif char in _CN_NUM:
                current = _CN_NUM[char]
            else:
                return None
        return total + current
    if len(value) > 1 and all(c in _CN_NUM for c in value):
        return int("".join(str(_CN_NUM[c]) for c in value))
    return _CN_NUM.get(value)


def _number_match(text: str) -> float | int | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if match:
        return _number(match.group(0))
    match = re.search(r"[零一二两三四五六七八九十百千]+", text)
    return _number(match.group(0)) if match else None


def _mapping_value(field: str, query: str, contract: Mapping[str, Any]) -> Any:
    mappings = contract.get("mappings") if isinstance(contract.get("mappings"), Mapping) else {}
    table = mappings.get(field) if isinstance(mappings.get(field), Mapping) else {}
    for phrase, value in sorted(table.items(), key=lambda item: len(str(item[0])), reverse=True):
        if str(phrase) in query:
            return value
    fields = contract.get("fields") if isinstance(contract.get("fields"), Mapping) else {}
    schema = fields.get(field) if isinstance(fields.get(field), Mapping) else {}
    enum = schema.get("enum") if isinstance(schema.get("enum"), list) else []
    # Direct enum values are only accepted when they occur as an explicit token.
    for value in sorted(enum, key=lambda item: len(str(item)), reverse=True):
        if isinstance(value, str) and str(value) in query:
            return value
    return None


def _range_ok(value: Any, schema: Mapping[str, Any]) -> bool:
    declared = schema.get("type")
    if declared == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return False
    if declared == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return False
    if declared == "string" and (not isinstance(value, str) or len(value.strip()) < schema.get("minLength", 0)):
        return False
    if declared == "boolean" and not isinstance(value, bool):
        return False
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            return False
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            return False
    return True


def _strength(query: str) -> float | None:
    m = re.search(r"(?:强度|力度)\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)\s*%?", query)
    if not m:
        m = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%\s*(?:强度|力度)?", query)
    if not m:
        m = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(?:强度|力度)", query)
    if not m:
        return None
    value = float(m.group(1))
    if "%" in m.group(0):
        value /= 100
    return int(value) if value.is_integer() else value


def _extract_dimension(field: str, query: str) -> int | None:
    aliases = {"width": "(?:width|宽度|宽)", "height": "(?:height|高度|高)"}
    # Keep the numeric token separate from the surrounding wording.  Real
    # requests commonly say “高度缩放到 600” or “宽度调到 1200”, while the
    # earlier expression only accepted a small set of assignment verbs.
    number = r"(?:[-+]?\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万]+)"
    assignment = r"(?:改成|改为|调整为|调整到|设为|设置为|设置成|缩放到|缩放为|调到|调为)?"
    m = re.search(rf"{aliases.get(field, field)}\s*{assignment}\s*({number})\s*(?:像素|px)?", query, re.I)
    if not m and field in ("width", "height") and re.search(r"宽高|长宽", query):
        m = re.search(rf"(?:宽高|长宽)\s*(?:都)?\s*{assignment}\s*({number})", query)
    if not m:
        return None
    value = _number(m.group(1))
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) else None


def _extract_target(query: str, replace: bool = False) -> str | None:
    if replace:
        m = re.search(r"(?:换成|替换为|替换成|改成)\s*(.+?)(?:，|,|并|保留|$)", query)
    else:
        m = re.search(r"(?:移除|删除|去掉|去除|擦除)\s*(.+?)(?:，|,|并|保留|$)", query)
    if not m:
        return None
    value = m.group(1).strip(" 的")
    value = re.sub(r"背景$", "", value).strip()
    if value in {"一个东西", "那个", "它", "某个东西", "物体", "背景"}:
        return None
    return value or None


def contract_params(contract: Mapping[str, Any], query: str) -> tuple[dict[str, Any], list[str]]:
    fields = contract.get("fields") if isinstance(contract.get("fields"), Mapping) else {}
    defaults = contract.get("defaults") if isinstance(contract.get("defaults"), Mapping) else {}
    args: dict[str, Any] = {}
    invalid: list[str] = []

    for field, raw_schema in fields.items():
        schema = raw_schema if isinstance(raw_schema, Mapping) else {}
        value = _mapping_value(field, query, contract)
        if field == "instruction" or (len(fields) == 1 and schema.get("type") == "string"
                                      and not schema.get("enum")
                                      and not any((contract.get("mappings") or {}).values())):
            value = query.strip()
        elif field == "targetColor":
            # Mapping wins; otherwise only consume a clean color token after a color verb.
            if value is None:
                m = re.search(r"(?:调成|换成|改成|变成)\s*(红色|蓝色|绿色|紫色|黑色|白色|粉色|橙色|黄色|灰色|暖色|冷色)", query)
                value = m.group(1) if m else None
                if value is None:
                    short = re.search(r"调([红蓝绿紫黑白粉橙黄灰暖冷])(?:色)?", query)
                    value = short.group(1) + "色" if short else None
        elif field == "region" and value is None:
            value = defaults.get(field)
        elif field in ("width", "height"):
            value = _extract_dimension(field, query)
        elif field in ("brightness", "contrast", "saturation", "exposure"):
            zh = {"brightness": "亮度", "contrast": "对比度", "saturation": "饱和度", "exposure": "曝光"}[field]
            number = r"([-+]?\d+(?:\.\d+)?|[零一二两三四五六七八九十百千]+)"
            change = r"(?:提高|降低|增加|减少|调到|加|减|为)"
            m = re.search(rf"(?:{change}\s*)?(?:{field}|{zh})\s*(?:{change}\s*)?{number}", query, re.I)
            value = None
            if m:
                value = _number(m.group(1))
                if value is not None and any(word in m.group(0) for word in ("降低", "减少", "减")):
                    value = -abs(value)
        elif field == "enhanceType" and value is None:
            if "降噪" in query:
                value = "denoise"
            elif any(x in query for x in ("清晰", "锐化", "模糊")):
                value = "clarity"
        elif field == "strength":
            value = _strength(query)
        elif field == "angle":
            value = _mapping_value(field, query, contract)
            if value is None:
                m = re.search(r"(?:旋转|转)\s*([-+]?\d+(?:\.\d+)?|[零一二两三四五六七八九十百千]+)\s*度?", query)
                if m:
                    value = _number(m.group(1))
                    if "逆时针" in query:
                        value = -abs(value)
        elif field == "flip" and value is None:
            value = _mapping_value(field, query, contract)
        elif field == "aspectRatio" and value is None:
            m = re.search(r"(\d+\s*:\s*\d+|[一二三四五六七八九十]+比[一二三四五六七八九十]+)", query)
            if m:
                value = m.group(1).replace(" ", "").replace("比", ":")
                parts = value.split(":")
                value = f"{_number(parts[0])}:{_number(parts[1])}" if len(parts) == 2 else value
        elif field == "target":
            value = _extract_target(query)
        elif field == "background":
            value = _extract_target(query, replace=True)
        elif field in ("smoothSkin", "whiten", "slimFace"):
            # Only requested operations are emitted; natural beauty is an explicit composite.
            if "自然美颜" in query:
                value = defaults.get(field) if field in ("smoothSkin", "whiten") else None
            else:
                value = _mapping_value(field, query, contract)
                if "轻微" in query and value is not None:
                    value = {"smoothSkin": 0.2, "whiten": 0.1, "slimFace": 0.15}[field]
            label = {"smoothSkin": "磨皮", "whiten": "美白", "slimFace": "瘦脸"}[field]
            explicit = re.search(rf"{label}\s*(?:强度|力度)?\s*([-+]?\d+(?:\.\d+)?)\s*(%)?", query)
            if explicit:
                value = float(explicit.group(1)) / (100 if explicit.group(2) else 1)
        elif value is None and field in defaults:
            value = defaults[field]
        if value is None and field in defaults and field not in ("smoothSkin", "whiten", "slimFace"):
            value = defaults[field]
        if value is not None:
            if _range_ok(value, schema):
                args[str(field)] = value
            else:
                invalid.append(str(field))

    # Resize sets aspect preservation based on the number of explicit dimensions.
    is_resize = {"width", "height", "keepAspectRatio"}.issubset(fields)
    is_tone = {"brightness", "contrast", "saturation", "exposure"}.issubset(fields)
    is_transform = {"angle", "flip"}.issubset(fields)
    if is_resize:
        dimensions = sum(field in args for field in ("width", "height"))
        if dimensions == 2 and "keepAspectRatio" in fields:
            args["keepAspectRatio"] = False
        elif dimensions == 1 and "keepAspectRatio" in fields and "keepAspectRatio" not in args:
            args["keepAspectRatio"] = defaults.get("keepAspectRatio", True)

    missing: list[str] = list(invalid)
    required = contract.get("required") if isinstance(contract.get("required"), list) else []
    for field in required:
        if field not in args:
            missing.append(str(field))
    if is_resize and not invalid and not any(field in args for field in ("width", "height")):
        missing.append("width_or_height")
    if is_tone and not args:
        missing.append("tone_value")
    if is_transform and args.get("angle", 0) == 0 and args.get("flip", "none") == "none":
        missing.append("angle_or_flip")
    return args, list(dict.fromkeys(missing))
