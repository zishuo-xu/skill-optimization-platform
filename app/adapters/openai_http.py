"""Standard OpenAI compatible Chat Completions HTTP adapter.

Kept as the "standard HTTP" entry point required by §121 while the enterprise
gateway uses WebSocket (see :mod:`app.adapters.enterprise_ws`). Credentials are
resolved from environment references or config at call time and are never logged
or persisted into events/prompts.

Error classification (§34, contract §2.6): auth failures terminate immediately;
network timeouts and 429 are retried at most twice with backoff and reported as
retry events before the final failure.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Callable, Mapping

from .. import event_types
from .base import (
    AdapterError,
    ModelAdapter,
    default_capabilities,
    emit_event,
    json_dumps,
    normalize_tool_definitions,
    normalize_usage,
    parse_tool_arguments,
    resolve_secret_ref,
    safe_log_config,
)
from .stream_events import SSEEventAssembler, aggregate, extract_stream_error

logger = logging.getLogger(__name__)

__all__ = ["OpenAIHTTPAdapter"]

_RETRYABLE_CODES = {"timeout", "rate_limited", "network_error"}


def _call_id(config: Mapping[str, Any]) -> str:
    value = config.get("call_id") if isinstance(config, Mapping) else None
    return str(value) if value else f"call_{uuid.uuid4().hex[:12]}"


def _error_from_status(status_code: int, body_text: str) -> AdapterError:
    detail = (body_text or "")[:1000]
    if status_code in (401, 403):
        return AdapterError(f"模型服务鉴权失败（HTTP {status_code}）",
                            code="auth_failed", details={"status": status_code},
                            retryable=False)
    if status_code == 429:
        return AdapterError("模型服务限流（HTTP 429）", code="rate_limited",
                            details={"status": status_code}, retryable=True)
    if status_code in (408, 504):
        return AdapterError(f"模型服务超时（HTTP {status_code}）", code="timeout",
                            details={"status": status_code}, retryable=True)
    if status_code >= 500:
        return AdapterError(f"模型服务异常（HTTP {status_code}）", code="platform_error",
                            details={"status": status_code, "body": detail},
                            retryable=True)
    return AdapterError(f"模型请求被拒绝（HTTP {status_code}）", code="platform_error",
                        details={"status": status_code, "body": detail},
                        retryable=False)


class OpenAIHTTPAdapter(ModelAdapter):
    """Chat Completions over HTTP(S), optional SSE streaming."""

    name = "openai_http"
    capabilities = default_capabilities(
        routing=True, tool_arguments=True, native_tools=True, usage=True, streaming=True)

    def __init__(self, model_config: Mapping[str, Any] | None = None):
        super().__init__(model_config)
        config = self.model_config
        base_url = (config.get("base_url") or config.get("baseUrl")
                    or os.environ.get("OPENAI_BASE_URL") or "")
        self.base_url = str(base_url).strip()
        explicit = config.get("url") or config.get("endpoint")
        self.url = str(explicit) if explicit else self._default_url(self.base_url)
        self.timeout_s = float(config.get("timeout_s") or config.get("timeout") or 60.0)
        self.max_retries = int(config.get("max_retries", 2))
        self.retry_backoff_s = float(config.get("retry_backoff_s", 0.5))

    @staticmethod
    def _default_url(base_url: str) -> str:
        if not base_url:
            return ""
        cleaned = base_url.rstrip("/")
        if cleaned.endswith("/chat/completions"):
            return cleaned
        return cleaned + "/chat/completions"

    # -- configuration ----------------------------------------------------- #
    def _effective_config(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        effective = dict(self.model_config)
        if config:
            effective.update(config)
        if not self.url:
            base_url = (effective.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "")
            self.url = self._default_url(str(base_url))
        return effective

    def _headers(self, config: Mapping[str, Any]) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
        lowered = {key.lower() for key in headers}
        secret = resolve_secret_ref(config.get("api_key_ref") if config.get("api_key_ref")
                                    is not None else config.get("api_key"))
        if secret and "authorization" not in lowered:
            headers["Authorization"] = f"Bearer {secret}"
        if "content-type" not in lowered:
            headers["Content-Type"] = "application/json"
        return headers

    def _build_body(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                    config: Mapping[str, Any]) -> dict[str, Any]:
        template = config.get("request_template") or config.get("request_body") or {}
        body: dict[str, Any] = dict(template) if isinstance(template, Mapping) else {}
        body["model"] = config.get("model") or body.get("model") or ""
        body["messages"] = list(messages)
        body["stream"] = bool(config.get("stream", False))
        if config.get("temperature") is not None:
            body["temperature"] = config["temperature"]
        if config.get("max_tokens") is not None:
            body["max_tokens"] = config["max_tokens"]
        if config.get("top_p") is not None:
            body["top_p"] = config["top_p"]
        tool_defs = normalize_tool_definitions(tools)
        if tool_defs:
            body["tools"] = tool_defs
            if config.get("tool_choice") is not None:
                body["tool_choice"] = config["tool_choice"]
        extra_body = config.get("extra_body")
        if isinstance(extra_body, Mapping):
            body.update(extra_body)
        if body.get("stream"):
            stream_options = dict(config.get("stream_options") or {})
            stream_options.setdefault("include_usage", True)
            body["stream_options"] = stream_options
        return body

    # -- public API -------------------------------------------------------- #
    def invoke(self, messages: list[dict[str, Any]], config: dict[str, Any],
               tools: list[dict[str, Any]], *,
               emit: Callable[..., Any] | None = None) -> dict[str, Any]:
        effective = self._effective_config(config)
        call_id = _call_id(effective)
        attempt = 0
        total_attempts = max(0, self.max_retries) + 1
        while True:
            attempt += 1
            emit_event(
                emit, event_types.MODEL_CALL_STARTED, call_id=call_id, attempt=attempt,
                payload={
                    "provider": self.name,
                    "model": effective.get("model", ""),
                    "stream": bool(effective.get("stream", False)),
                    "message_count": len(messages),
                    "tool_count": len(tools),
                },
            )
            started = time.monotonic()
            try:
                result = self._invoke_once(messages, tools, effective, emit=emit,
                                           call_id=call_id, attempt=attempt)
            except AdapterError as exc:
                exc.attempt = attempt
                should_retry = (exc.retryable and attempt < total_attempts)
                if should_retry:
                    delay = self._backoff(attempt, exc.retry_after)
                    emit_event(
                        emit, event_types.MODEL_CALL_FAILED, level="warning",
                        call_id=call_id, attempt=attempt,
                        payload={
                            "provider": self.name,
                            "retrying": True,
                            "attempt": attempt,
                            "max_attempts": total_attempts,
                            "retry_in_s": delay,
                            "error": {"code": exc.code, "kind": exc.error_kind,
                                      "message": exc.message},
                            "issue": event_types.issue(
                                exc.code, "模型调用失败，正在重试",
                                exc.message,
                                impact="本次调用未产生结果，重试会计入预算；不会重复计分。",
                                next_action="等待自动重试；若持续失败请检查模型服务。",
                                technical_detail={"attempt": attempt,
                                                  "max_attempts": total_attempts}),
                        },
                    )
                    if delay:
                        time.sleep(delay)
                    continue
                emit_event(
                    emit, event_types.MODEL_CALL_FAILED, level="error",
                    call_id=call_id, attempt=attempt,
                    payload={
                        "provider": self.name,
                        "retrying": False,
                        "attempt": attempt,
                        "error": {"code": exc.code, "kind": exc.error_kind,
                                  "message": exc.message},
                        "issue": event_types.issue(
                            exc.code, "模型调用失败", exc.message,
                            impact="该案例无法获得完整结果。",
                            next_action="检查模型连接、凭据或稍后重试。",
                            technical_detail={"attempt": attempt,
                                              "max_attempts": total_attempts,
                                              "retryable": exc.retryable}),
                    },
                )
                raise
            duration_ms = int((time.monotonic() - started) * 1000)
            emit_event(
                emit, event_types.MODEL_CALL_FINISHED, call_id=call_id, attempt=attempt,
                payload={
                    "provider": self.name,
                    "model": result.get("model") or effective.get("model", ""),
                    "finish_reason": result.get("finish_reason"),
                    "duration_ms": duration_ms,
                    "usage": result.get("usage"),
                    "tool_call_count": len(result.get("tool_calls") or []),
                    "content_chars": len(result.get("content") or ""),
                },
            )
            return result

    # -- internals --------------------------------------------------------- #
    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(0.0, float(retry_after))
        if self.retry_backoff_s <= 0:
            return 0.0
        return min(self.retry_backoff_s * (2 ** (attempt - 1)), 8.0)

    def _invoke_once(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                     config: dict[str, Any], *, emit: Callable[..., Any] | None,
                     call_id: str, attempt: int) -> dict[str, Any]:
        import httpx  # imported lazily so unit tests need no patch machinery

        if not self.url:
            raise AdapterError("未配置模型服务地址（base_url/url）", code="platform_error",
                               retryable=False)
        body = self._build_body(messages, tools, config)
        headers = self._headers(config)
        cancel_check = config.get("cancel_check")
        stream = bool(body.get("stream"))
        logger.info("openai_http invoke %s", json_dumps({
            "url": self.url, "model": body.get("model"), "stream": stream,
            "messages": len(messages), "tools": len(tools or []),
            "config": safe_log_config(config)}))
        timeout = httpx.Timeout(self.timeout_s)
        try:
            with httpx.Client(timeout=timeout) as client:
                if not stream:
                    response = client.post(self.url, json=body, headers=headers)
                    return self._parse_response(response, config)
                with client.stream("POST", self.url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        text = response.read().decode("utf-8", "replace")
                        raise _error_from_status(response.status_code, text)
                    return self._consume_stream(response, config, emit=emit,
                                                call_id=call_id, attempt=attempt,
                                                cancel_check=cancel_check)
        except AdapterError:
            raise
        except httpx.TimeoutException as exc:
            raise AdapterError(f"模型请求超时（>{self.timeout_s:.0f}s）", code="timeout",
                               details={"url": self.url}, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(f"模型网络请求失败: {type(exc).__name__}", code="network_error",
                               details={"url": self.url}, retryable=True) from exc

    def _parse_response(self, response: Any, config: Mapping[str, Any]) -> dict[str, Any]:
        if response.status_code >= 400:
            raise _error_from_status(response.status_code, response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError("模型响应不是合法 JSON", code="format_error",
                               details={"body": response.text[:1000]},
                               retryable=False) from exc
        return self._result_from_payload(payload, config)

    def _result_from_payload(self, payload: Any, config: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise AdapterError("模型响应结构不是 JSON 对象", code="format_error",
                               retryable=False)
        error = payload.get("error")
        if isinstance(error, Mapping):
            code = error.get("code")
            if code in (401, "401", "unauthorized"):
                raise AdapterError("模型服务鉴权失败", code="auth_failed",
                                   retryable=False)
            if code in (429, "429", "rate_limit_exceeded"):
                raise AdapterError("模型服务限流", code="rate_limited", retryable=True)
            raise AdapterError(str(error.get("message") or "模型返回错误"),
                               code="platform_error",
                               details={"code": code}, retryable=False)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AdapterError("模型响应缺少 choices", code="format_error",
                               details={"keys": list(payload)},
                               retryable=False)
        choice = choices[0] if isinstance(choices[0], Mapping) else {}
        message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = json_dumps(content)
        tool_calls: list[dict[str, Any]] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            if not isinstance(raw_call, Mapping):
                continue
            function = raw_call.get("function") if isinstance(raw_call.get("function"), Mapping) else {}
            name = str(function.get("name") or raw_call.get("name") or "").strip()
            if not name:
                raise AdapterError("模型返回的工具调用缺少名称", code="format_error",
                                   retryable=False)
            arguments = parse_tool_arguments(function.get("arguments", raw_call.get("arguments")))
            tool_calls.append({
                "id": str(raw_call.get("id") or f"call_{index}"),
                "name": name,
                "arguments": arguments,
            })
        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": choice.get("finish_reason"),
            "usage": normalize_usage(payload.get("usage")),
            "model": str(payload.get("model") or config.get("model") or ""),
            "raw": {"status": "ok", "model": payload.get("model"),
                    "choices": len(choices)},
        }

    def _consume_stream(self, response: Any, config: Mapping[str, Any], *,
                        emit: Callable[..., Any] | None, call_id: str,
                        attempt: int, cancel_check: Callable[[], bool] | None) -> dict[str, Any]:
        assembler = SSEEventAssembler(
            done_markers=tuple(config.get("end_markers") or ("[DONE]",)))
        events: list[dict[str, Any]] = []
        emit_deltas = bool(config.get("emit_deltas", False))
        try:
            for chunk in response.iter_bytes():
                if cancel_check is not None and cancel_check():
                    raise AdapterError("模型调用已取消", code="cancelled", retryable=False)
                new_events = assembler.feed(chunk)
                events.extend(new_events)
                if emit_deltas:
                    for event in new_events:
                        text = _delta_text(event)
                        if text:
                            emit_event(emit, event_types.MODEL_DELTA, call_id=call_id,
                                       attempt=attempt,
                                       payload={"content": text, "provider": self.name})
            events.extend(assembler.finish())
        except AdapterError:
            raise
        except Exception as exc:  # network cut mid-stream
            raise AdapterError("模型流式连接中断", code="stream_interrupted",
                               details={"error": type(exc).__name__},
                               retryable=False) from exc

        provider_error = extract_stream_error(events)
        if provider_error is not None:
            message = str(provider_error.get("message") or "模型流返回错误")
            code = provider_error.get("code")
            if code in (401, "401", "unauthorized"):
                raise AdapterError("模型流鉴权失败", code="auth_failed", retryable=False)
            if code in (429, "429", "rate_limit_exceeded"):
                raise AdapterError("模型流被限流", code="rate_limited", retryable=True)
            raise AdapterError(message, code="platform_error",
                               details={"code": code}, retryable=False)

        result = aggregate(events)
        if result["argument_errors"]:
            raise AdapterError("模型返回的工具参数不完整或不是合法 JSON",
                               code="format_error",
                               details={"errors": result["argument_errors"][:3]},
                               retryable=False)
        done = result["done"]
        if not done and result["finish_reason"] is None and not config.get(
                "accept_eof_without_finish", False):
            raise AdapterError("模型流在结束标记前中断", code="stream_interrupted",
                               retryable=False)
        raw = {"status": "ok", "stream": True, "events": len(events),
               "done": done}
        return {
            "content": result["content"],
            "tool_calls": result["tool_calls"],
            "finish_reason": result["finish_reason"],
            "usage": result["usage"],
            "model": str(config.get("model") or ""),
            "raw": raw,
        }


def _delta_text(event: Mapping[str, Any]) -> str:
    payload = event.get("json")
    if not isinstance(payload, Mapping):
        return ""
    texts: list[str] = []
    for choice in payload.get("choices") or []:
        if not isinstance(choice, Mapping):
            continue
        delta = choice.get("delta") or {}
        if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
            texts.append(delta["content"])
    return "".join(texts)
