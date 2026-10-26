"""One-shot, bounded connection probes using the production adapters."""

from __future__ import annotations

import math
import time
from urllib.parse import urlparse
from typing import Any, Mapping

from .adapters.base import AdapterError
from .adapters.registry import build_adapter
from .model_output import redact_output
from .services import normalize_model_config


def probe_connection(model_config: Mapping[str, Any], *, target: str = "business",
                     input_text: str | None = None) -> dict[str, Any]:
    try:
        cfg = normalize_model_config(model_config)
    except Exception as exc:
        return {"ok": False, "performed": False, "target": target,
                "provider": model_config.get("provider") if isinstance(model_config, Mapping) else None,
                "model": model_config.get("model") if isinstance(model_config, Mapping) else None,
                "latency_ms": 0, "message": f"配置无效: {type(exc).__name__}",
                "error_code": getattr(exc, "code", "config_error"),
                "response_preview": None, "usage": None}
    provider = str(cfg.get("provider") or "")
    model = str(cfg.get("model") or cfg.get("model_name") or "").strip()
    base = {"ok": False, "performed": False, "target": target,
            "provider": provider, "model": model, "latency_ms": None,
            "message": "", "error_code": None, "response_preview": None, "usage": None,
            "finish_reason": None, "response_truncated": False}
    if provider in {"demo_scripted", "heuristic"}:
        base.update(performed=False, ok=True,
                    message="演示/启发式适配器不执行真实网络探测")
        return base
    if provider == "flow":
        base.update(performed=False, message="Python flow 不执行整套业务流程探测",
                    error_code="probe_unsupported")
        return base
    url = cfg.get("ws_url") if provider == "enterprise_ws" else (cfg.get("url") or cfg.get("endpoint") or cfg.get("base_url"))
    parsed = urlparse(str(url)) if url else None
    allowed = ("ws", "wss") if provider == "enterprise_ws" else ("http", "https")
    if not parsed or parsed.scheme not in allowed or not parsed.netloc:
        base.update(message="地址无效", error_code="config_error")
        return base
    if not model:
        base.update(message="缺少模型名", error_code="config_error")
        return base
    prompt = "只回复 OK" if input_text is None or not input_text.strip() else input_text
    started = time.monotonic()
    effective = dict(cfg)
    try:
        effective["max_retries"] = 0
        effective["model"] = model
        for name, default, maximum, cast in (("max_tokens", 64, 64, int),
                                             ("timeout_s", 20, 20, float),
                                             ("total_timeout_s", 20, 20, float),
                                             ("connect_timeout_s", 10, 20, float),
                                             ("first_packet_timeout_s", 20, 20, float),
                                             ("idle_timeout_s", 20, 20, float)):
            raw = effective.get(name)
            number = cast(default if raw in (None, "") else raw)
            if isinstance(raw, bool) or not math.isfinite(number) or number <= 0:
                raise ValueError("invalid positive limit")
            effective[name] = min(number, maximum)
    except (TypeError, ValueError, OverflowError):
        base.update(latency_ms=0, message="超时和最大输出 Token 必须为正数", error_code="config_error")
        return base
    effective["deadline_monotonic"] = time.monotonic() + effective["timeout_s"]
    try:
        adapter = build_adapter(effective)
        base["performed"] = True
        result = adapter.invoke([{"role": "user", "content": prompt}], effective, [], emit=None)
        if not isinstance(result, Mapping) or not any(result.get(key)
                                                      for key in ("content", "tool_calls", "finish_reason")):
            raise AdapterError("模型响应格式无效", code="format_error", retryable=False)
        safe = redact_output(result, effective)
        content = safe.get("content") if isinstance(safe, Mapping) else ""
        reason = safe.get("finish_reason")
        reached_limit = reason in ("length", "max_tokens", "max_output_tokens")
        base.update(ok=True, message="连接成功；测试回复达到输出上限" if reached_limit else "连接成功",
                    model=safe.get("model") or model,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    response_preview=(str(content)[:240] if content else None),
                    finish_reason=reason,
                    response_truncated=reached_limit or bool(content and len(str(content)) > 240),
                    usage=safe.get("usage") if isinstance(safe, Mapping) else None)
    except AdapterError as exc:
        safe_error = redact_output({"message": exc.message}, effective)["message"]
        base.update(latency_ms=int((time.monotonic() - started) * 1000),
                    message=str(safe_error), error_code=exc.code)
    except Exception as exc:  # do not expose provider body or credentials
        base.update(latency_ms=int((time.monotonic() - started) * 1000),
                    message=f"连接探测失败: {type(exc).__name__}", error_code="probe_failed")
    return redact_output(base, effective)
