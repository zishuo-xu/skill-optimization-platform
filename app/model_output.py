"""Safe, reusable projection of model responses for trace visibility."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping


def _resolve_secret_ref(ref: Any) -> str | None:
    if ref is None or ref == "":
        return None
    if isinstance(ref, Mapping):
        if ref.get("env"):
            return os.environ.get(str(ref["env"]))
        if "value" in ref and ref.get("value") is not None:
            return str(ref["value"])
        return None
    if isinstance(ref, str):
        if ref.startswith("env:"):
            return os.environ.get(ref[4:])
        return os.environ.get(ref, ref)
    return str(ref)

_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|id[_-]?token|^token$|secret|password|cookie|credential)", re.I)
_COUNT_KEY = re.compile(r"(?:^|_)(?:input|output|prompt|completion|total)_tokens$", re.I)
_TEXT_PATTERNS = (
    (re.compile(r"(?i)(\b(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret|cookie)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s\"',}]+)"), r'\1"***"'),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-***"),
)


def _secrets(config: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in config.items():
        if _SECRET_KEY.search(str(key)) and value:
            resolved = _resolve_secret_ref(value)
            if resolved and len(str(resolved)) >= 3:
                values.append(str(resolved))
        if isinstance(value, Mapping):
            values.extend(_secrets(value))
            continue
        if not _SECRET_KEY.search(str(key)):
            continue
        resolved = _resolve_secret_ref(value)
        if resolved and len(str(resolved)) >= 3:
            values.append(str(resolved))
    headers = config.get("headers")
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if str(key).lower() in {"authorization", "x-api-key"} and value:
                values.append(str(value))
                match = re.match(r"Bearer\s+(.+)", str(value), re.I)
                if match:
                    values.append(match.group(1))
    for item in config.values():
        if isinstance(item, list):
            for nested in item:
                if isinstance(nested, Mapping):
                    values.extend(_secrets(nested))
    return sorted(set(values), key=len, reverse=True)


def _safe(value: Any, secrets: list[str], *, key: str = "") -> Any:
    if _SECRET_KEY.search(key) and not _COUNT_KEY.search(key):
        return "***"
    if isinstance(value, Mapping):
        return {str(k): _safe(v, secrets, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(item, secrets, key=key) for item in value]
    if isinstance(value, tuple):
        return [_safe(item, secrets, key=key) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "***")
        for pattern, replacement in _TEXT_PATTERNS:
            value = pattern.sub(replacement, value)
    return value


def capture_model_response(result: Any, model_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a display-only, whitelisted and redacted response projection."""
    config = model_config if isinstance(model_config, Mapping) else {}
    source = result if isinstance(result, Mapping) else {}
    response: dict[str, Any] = {}
    secrets = _secrets(config)
    for key in ("content", "tool_calls", "finish_reason", "usage", "model"):
        if key in source:
            value = source[key]
            if key == "tool_calls" and isinstance(value, (list, tuple)):
                value = [{field: call[field] for field in ("id", "name", "arguments", "function")
                          if field in call} for call in value if isinstance(call, Mapping)]
            response[key] = _safe(value, secrets, key=key)
    if not response:
        response = {"content": ""}
    return response


def response_summary(response: Mapping[str, Any]) -> str:
    content = response.get("content")
    tool_calls = response.get("tool_calls")
    if tool_calls:
        names = [str(item.get("name") or (item.get("function") or {}).get("name") or "未命名工具")
                 for item in tool_calls if isinstance(item, Mapping)]
        summary = f"结构化工具调用 {len(tool_calls)} 个：" + "、".join(names)
        summary = summary[:240]
    elif content and not isinstance(content, str):
        summary = json.dumps(content, ensure_ascii=False)[:240]
    elif isinstance(content, str) and content:
        summary = content[:240] + ("…" if len(content) > 240 else "")
    else:
        summary = "模型返回为空"
    return summary


def redact_output(value: Any, model_config: Mapping[str, Any] | None = None) -> Any:
    """Redact an arbitrary legacy result without changing usage counters."""
    config = model_config if isinstance(model_config, Mapping) else {}
    return _safe(value, _secrets(config))


def response_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


__all__ = ["capture_model_response", "redact_output", "response_json", "response_summary"]
