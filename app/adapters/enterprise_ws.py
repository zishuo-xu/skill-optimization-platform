"""Enterprise gateway WebSocket adapter (OpenAI messages over WS + SSE deltas).

Requirements (§37/§38/§40/§42, contract §2.6):

* the request body keeps gateway specific fields (``api_key``, ``user``,
  ``extra_body`` ...) exactly as supplied; they are **never** rewritten into an
  ``Authorization`` header;
* incoming WS frames are buffered and restored into SSE events (events can be
  split across frames or several per frame, ``\\r\\n`` or ``\\n``, heartbeats and
  empty deltas ignored, tail ``usage`` consumed after ``finish_reason``,
  ``[DONE]`` or configured end marker terminates; gateways without an end
  marker finish on ``finish_reason`` — immediately when that frame already
  carries ``usage``, otherwise after a short grace window for tail usage);
* frames may be one complete JSON chunk each and carry large telemetry blocks
  (``result_info`` / ``debug_info``); telemetry is dropped after parsing and the
  default frame size limit is 16 MiB;
* connection / first-packet / idle / total timeouts and heartbeat are
  configurable;
* auth failures are not retried; timeouts / 429 retry at most twice with a fresh
  connection (outputs are never concatenated across connections);
* an interrupted stream raises ``stream_interrupted`` (infrastructure) and never
  yields a half-aggregated scored result;
* cancel closes the connection and raises ``cancelled``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Mapping

from .. import event_types
from ..model_output import capture_model_response, response_summary
from .base import (
    AdapterError,
    ModelAdapter,
    default_capabilities,
    emit_event,
    json_dumps,
    normalize_tool_definitions,
    safe_log_config,
    resolve_secret_ref,
)
from .stream_events import SSEEventAssembler, SSEParseError, aggregate, extract_stream_error
from .request_diagnostics import RequestDiagnostics

logger = logging.getLogger(__name__)

__all__ = ["EnterpriseWSAdapter"]

#: Default gateway stream end marker.
DEFAULT_END_MARKERS = ("[DONE]",)

#: Poll interval while waiting for the next frame (keeps cancel/timeout responsive).
_POLL_INTERVAL = 0.2

#: Default per-frame size limit. Gateways may inject large telemetry blocks
#: (e.g. ``debug_info`` echoing the whole request) into every frame; the
#: websockets library default of 1 MiB would drop such connections.
DEFAULT_MAX_FRAME_BYTES = 16 * 1024 * 1024

#: Payload keys the aggregator needs; everything else (telemetry such as
#: ``result_info`` / ``debug_info``) is dropped right after parsing.
_KEPT_PAYLOAD_KEYS = ("choices", "usage", "model", "error", "code", "message",
                      "done", "finished", "type", "id", "object")


def _slim_payload(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        return {key: payload[key] for key in _KEPT_PAYLOAD_KEYS if key in payload}
    if isinstance(payload, list):
        return [_slim_payload(item) for item in payload]
    return payload


def _slim_event(event: dict[str, Any]) -> dict[str, Any]:
    """Drop gateway telemetry from a parsed event to bound memory per call."""
    if isinstance(event.get("json"), (Mapping, list)):
        event["json"] = _slim_payload(event["json"])
        event["data"] = ""
    return event


def _payload_has_usage(event: Mapping[str, Any]) -> bool:
    payload = event.get("json")
    if not isinstance(payload, Mapping):
        return False
    usage = payload.get("usage")
    return isinstance(usage, Mapping) and bool(usage)


def _call_id(config: Mapping[str, Any]) -> str:
    value = config.get("call_id") if isinstance(config, Mapping) else None
    return str(value) if value else f"call_{uuid.uuid4().hex[:12]}"


def _first_packet_text(event: Mapping[str, Any]) -> str:
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


def _payload_finish_reason(event: Mapping[str, Any]) -> str | None:
    payload = event.get("json")
    if not isinstance(payload, Mapping):
        return None
    for choice in payload.get("choices") or []:
        if isinstance(choice, Mapping) and isinstance(choice.get("finish_reason"), str) \
                and choice.get("finish_reason"):
            return choice["finish_reason"]
    return None


class EnterpriseWSAdapter(ModelAdapter):
    """One connection per request by default (gateway has no multiplex protocol)."""

    name = "enterprise_ws"
    capabilities = default_capabilities(
        routing=True, tool_arguments=True, native_tools=True, usage=True, streaming=True)

    def __init__(self, model_config: Mapping[str, Any] | None = None):
        super().__init__(model_config)
        config = self.model_config
        self.ws_url = str(config.get("ws_url") or config.get("url") or "").strip()
        self.connect_timeout_s = float(config.get("connect_timeout_s", 10.0))
        self.first_packet_timeout_s = float(config.get("first_packet_timeout_s", 30.0))
        self.idle_timeout_s = float(config.get("idle_timeout_s", 60.0))
        self.total_timeout_s = float(config.get("total_timeout_s", 120.0))
        self.close_timeout_s = float(config.get("close_timeout_s", 5.0))
        self.heartbeat_interval_s = config.get("heartbeat_interval_s", 20.0)
        # ``max_size: null`` in the config keeps "no limit"; absent means default.
        self.max_size = (config.get("max_size") if "max_size" in config
                         else DEFAULT_MAX_FRAME_BYTES)
        self.max_retries = int(config.get("max_retries", 2))
        self.retry_backoff_s = float(config.get("retry_backoff_s", 0.5))
        self.end_markers = tuple(config.get("end_markers") or DEFAULT_END_MARKERS)
        self.accept_finish_reason_as_done = bool(
            config.get("accept_finish_reason_as_done", True))
        self.finish_grace_s = float(config.get("finish_grace_s", 1.0))
        self.emit_deltas = bool(config.get("emit_deltas", False))

    # -- configuration ----------------------------------------------------- #
    def _effective_config(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        effective = dict(self.model_config)
        if config:
            effective.update(config)
        return effective

    def _headers(self, config: Mapping[str, Any]) -> dict[str, str]:
        # Only explicit transport headers. Gateway fields such as api_key stay in
        # the JSON body and are never translated into Authorization (§38).
        return {str(key): str(value) for key, value in (config.get("headers") or {}).items()}

    def build_request_body(self, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]],
                           config: Mapping[str, Any]) -> dict[str, Any]:
        template = config.get("request_template") or config.get("request_body") or {}
        body: dict[str, Any] = dict(template) if isinstance(template, Mapping) else {}
        # Gateway specific fields already inside the template are preserved as-is.
        body["messages"] = list(messages)
        if config.get("model") is not None:
            body["model"] = config.get("model")
        body.setdefault("stream", True)
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
        else:
            body.pop("tools", None)
            body.pop("tool_choice", None)
        extra_body = config.get("extra_body")
        if isinstance(extra_body, Mapping) and "extra_body" not in body:
            body["extra_body"] = dict(extra_body)
        if ("api_key" not in body or body.get("api_key") == "***") and (config.get("api_key_ref") is not None or
                                      config.get("api_key") is not None):
            ref = config.get("api_key_ref") if config.get("api_key_ref") is not None else config.get("api_key")
            secret = resolve_secret_ref(ref)
            if secret is None or secret == "***":
                raise AdapterError("企业网关凭据引用不可用", code="auth_failed", retryable=False)
            body["api_key"] = secret
        if body.get("api_key") == "***":
            raise AdapterError("企业网关凭据不可用", code="auth_failed", retryable=False)
        stream_options = config.get("stream_options")
        if stream_options is not None and "stream_options" not in body:
            body["stream_options"] = dict(stream_options)
        return body

    # -- public API -------------------------------------------------------- #
    def invoke(self, messages: list[dict[str, Any]], config: dict[str, Any],
               tools: list[dict[str, Any]], *,
               emit: Callable[..., Any] | None = None) -> dict[str, Any]:
        effective = self._effective_config(config)
        call_id = _call_id(effective)
        if not self.ws_url:
            raise AdapterError("未配置企业网关地址（ws_url）", code="platform_error",
                               retryable=False)
        body = self.build_request_body(messages, tools, effective)
        total_attempts = max(0, self.max_retries) + 1
        deadline = time.monotonic() + self.total_timeout_s
        configured_deadline = effective.get("deadline_monotonic")
        if isinstance(configured_deadline, (int, float)):
            deadline = min(deadline, float(configured_deadline))
        logger.info("enterprise_ws invoke %s", json_dumps({
            "ws_url": self.ws_url,
            "model": body.get("model"),
            "messages": len(messages),
            "tools": len(tools or []),
            "config": safe_log_config(effective),
        }))
        attempt = 0
        while True:
            attempt += 1
            emit_event(
                emit, event_types.MODEL_CALL_STARTED, call_id=call_id, attempt=attempt,
                payload={
                    "provider": self.name,
                    "phase": "connecting",
                    "model": effective.get("model", ""),
                    "message_count": len(messages),
                    "tool_count": len(tools or []),
                    "stream": bool(body.get("stream", True)),
                },
            )
            started = time.monotonic()
            observer = effective.get("_request_observer") or (lambda payload: emit_event(
                emit, event_types.MODEL_REQUEST_INFO, call_id=call_id, attempt=attempt, payload=payload))
            diagnostics = RequestDiagnostics(observer, attempt, total_attempts)
            diagnostics.emit("attempt_started", provider=self.name, stream=True,
                             request_timeout_s=self.total_timeout_s,
                             first_packet_timeout_s=self.first_packet_timeout_s,
                             idle_timeout_s=self.idle_timeout_s,
                             input_chars=sum(len(str(m.get("content") or "")) for m in messages),
                             max_output_tokens=effective.get("max_tokens"))
            try:
                if effective.get("cancel_check") and effective["cancel_check"]():
                    raise AdapterError("模型调用已取消", code="cancelled", retryable=False)
                if time.monotonic() >= deadline:
                    raise AdapterError("模型调用超过总超时", code="timeout", retryable=False)
                attempt_config = dict(effective)
                attempt_config["deadline_monotonic"] = deadline
                attempt_config["_request_diagnostics"] = diagnostics
                result = asyncio.run(self._run_once(
                    body, attempt_config, emit=emit, call_id=call_id, attempt=attempt))
            except AdapterError as exc:
                exc.attempt = attempt
                partial = bool(getattr(exc, "partial", False))
                should_retry = exc.retryable and attempt < total_attempts and not partial
                retry_delay = self._backoff(attempt, exc.retry_after) if should_retry else None
                diagnostics.emit("attempt_failed", error_code=exc.code, partial_output=partial,
                                 retry_planned=bool(should_retry), retry_in_s=retry_delay)
                if should_retry:
                    delay = retry_delay
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
                                exc.code, "模型调用失败，正在新建连接重试",
                                exc.message,
                                impact="重试会重新发送请求并产生额外用量；不会拼接中断前的半成品输出。",
                                next_action="等待自动重试；连续失败请检查网关。",
                                technical_detail={"attempt": attempt,
                                                  "max_attempts": total_attempts}),
                        },
                    )
                    if delay:
                        wait_until = min(time.monotonic() + delay, deadline)
                        while time.monotonic() < wait_until:
                            if effective.get("cancel_check") and effective["cancel_check"]():
                                raise AdapterError("模型调用已取消", code="cancelled", retryable=False)
                            time.sleep(min(0.05, wait_until - time.monotonic()))
                    if time.monotonic() >= deadline:
                        raise AdapterError("模型调用超过总超时", code="timeout", retryable=False)
                    continue
                emit_event(
                    emit, event_types.MODEL_CALL_FAILED, level="error",
                    call_id=call_id, attempt=attempt,
                    payload={
                        "provider": self.name,
                        "retrying": False,
                        "partial": partial,
                        "attempt": attempt,
                        "error": {"code": exc.code, "kind": exc.error_kind,
                                  "message": exc.message},
                        "issue": event_types.issue(
                            exc.code, "模型调用失败", exc.message,
                            impact="该案例无法获得结果；中断前的增量不做评分。",
                            next_action="检查网关连接、凭据、超时配置或稍后重试。",
                            technical_detail={"attempt": attempt,
                                              "max_attempts": total_attempts,
                                              "partial": partial}),
                    },
                )
                raise
            duration_ms = int((time.monotonic() - started) * 1000)
            diagnostics.completed(result)
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
                    "response": capture_model_response(result, effective),
                    "response_summary": response_summary(capture_model_response(result, effective)),
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

    async def _run_once(self, body: Mapping[str, Any], config: Mapping[str, Any], *,
                        emit: Callable[..., Any] | None, call_id: str,
                        attempt: int) -> dict[str, Any]:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.total_timeout_s
        configured_deadline = config.get("deadline_monotonic")
        if isinstance(configured_deadline, (int, float)):
            deadline = min(deadline, float(configured_deadline))
        cancel_check = config.get("cancel_check")
        assembler = SSEEventAssembler(done_markers=self.end_markers)
        events: list[dict[str, Any]] = []
        finish_reason: str | None = None
        received_frames = 0
        content_chars = 0
        business_output = False
        finish_deadline: float | None = None
        model_name = str(config.get("model") or "")
        connect_kwargs: dict[str, Any] = {
            "open_timeout": self.connect_timeout_s,
            "close_timeout": self.close_timeout_s,
            "additional_headers": self._headers(config) or None,
            "ping_interval": self.heartbeat_interval_s if self.heartbeat_interval_s else None,
            "ping_timeout": self.heartbeat_interval_s if self.heartbeat_interval_s else None,
        }
        connect_kwargs["max_size"] = self.max_size
        diagnostics = config.get("_request_diagnostics")
        try:
            async with connect(self.ws_url, **connect_kwargs) as websocket:
                if diagnostics:
                    diagnostics.emit("connection_opened")
                await asyncio.wait_for(
                    websocket.send(json.dumps(body, ensure_ascii=False)),
                    timeout=self.connect_timeout_s)
                first_packet_deadline = loop.time() + self.first_packet_timeout_s
                last_activity = loop.time()
                while True:
                    if cancel_check is not None and cancel_check():
                        raise AdapterError("模型调用已取消", code="cancelled",
                                           retryable=False)
                    now = loop.time()
                    if now >= deadline:
                        raise self._partial_error(
                            AdapterError("模型调用超过总超时", code="timeout",
                                         retryable=True), received_frames,
                            business_output=business_output)
                    # A finish_reason is a valid business response even when
                    # content is empty; its grace window takes precedence over
                    # the first-content deadline while tail usage/DONE arrives.
                    if finish_deadline is not None and now >= finish_deadline:
                        break
                    if finish_deadline is None and not business_output and now >= first_packet_deadline:
                        raise AdapterError("等待模型首包超时", code="timeout",
                                           retryable=True)
                    # Once any frame (including a heartbeat) has arrived, use
                    # the configured idle window. Heartbeats don't satisfy the
                    # first-business-packet deadline, but they must not keep a
                    # stalled attempt alive until the gateway closes it.
                    idle_limit = (self.idle_timeout_s if received_frames
                                  else self.first_packet_timeout_s)
                    if finish_deadline is None and now - last_activity >= idle_limit:
                        raise self._partial_error(
                            AdapterError("模型流空闲超时", code="timeout",
                                         retryable=True), received_frames,
                            business_output=business_output)
                    try:
                        frame = await asyncio.wait_for(
                            websocket.recv(), timeout=_POLL_INTERVAL)
                    except asyncio.TimeoutError:
                        continue
                    last_activity = loop.time()
                    received_frames += 1
                    if diagnostics:
                        diagnostics.received(frame)
                    if received_frames == 1:
                        size = len(frame) if isinstance(frame, (bytes, str)) else 0
                        emit_event(
                            emit, event_types.MODEL_DELTA, level="debug",
                            call_id=call_id, attempt=attempt,
                            payload={"provider": self.name, "phase": "first_packet",
                                     "bytes": size},
                        )
                    try:
                        new_events = assembler.feed(frame)
                        if diagnostics:
                            diagnostics.parsed(new_events)
                    except SSEParseError as exc:
                        raise AdapterError("企业网关流格式解析失败", code="format_error",
                                           details={"error": str(exc)}, retryable=False) from exc
                    new_events = [_slim_event(event) for event in new_events]
                    events.extend(new_events)
                    early_error = extract_stream_error(new_events)
                    if early_error is not None:
                        error_code = early_error.get("code")
                        message = str(early_error.get("message") or "企业网关返回错误")
                        if error_code in (401, "401", "unauthorized"):
                            raise AdapterError(message, code="auth_failed", retryable=False)
                        if error_code in (429, "429", "rate_limit_exceeded"):
                            raise AdapterError(message, code="rate_limited", retryable=not business_output)
                        raise AdapterError(message, code="platform_error", retryable=False)
                    for event in new_events:
                        text = _first_packet_text(event)
                        if text:
                            business_output = True
                            content_chars += len(text)
                            if self.emit_deltas:
                                emit_event(emit, event_types.MODEL_DELTA,
                                           call_id=call_id, attempt=attempt,
                                           payload={"provider": self.name,
                                                    "phase": "receiving",
                                                    "content": text})
                        payload = event.get("json")
                        if isinstance(payload, Mapping):
                            for choice in payload.get("choices") or []:
                                delta = choice.get("delta") if isinstance(choice, Mapping) else None
                                if isinstance(delta, Mapping) and delta.get("tool_calls"):
                                    business_output = True
                        reason = _payload_finish_reason(event)
                        if reason:
                            finish_reason = reason
                            if self.accept_finish_reason_as_done and finish_deadline is None:
                                # The grace window only exists to collect a tail
                                # usage frame / end marker. Gateways that put usage
                                # on every frame (including the finish frame) and
                                # send no [DONE] are complete right here.
                                grace = (0.0 if _payload_has_usage(event)
                                         else max(0.0, self.finish_grace_s))
                                finish_deadline = loop.time() + grace
                        if isinstance(payload, Mapping) and isinstance(
                                payload.get("model"), str):
                            model_name = payload["model"]
                    if any(event.get("done") for event in new_events):
                        break
                events.extend(assembler.finish())
        except InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                raise AdapterError("企业网关鉴权失败", code="auth_failed",
                                   details={"status": status}, retryable=False) from exc
            if status == 429:
                raise AdapterError("企业网关限流", code="rate_limited",
                                   details={"status": status}, retryable=True) from exc
            raise AdapterError(f"企业网关握手失败（HTTP {status}）", code="network_error",
                               details={"status": status}, retryable=True) from exc
        except ConnectionClosed as exc:
            close_code = None
            for attribute in ("rcvd", "sent"):
                frame = getattr(exc, attribute, None)
                if frame is not None and getattr(frame, "code", None) is not None:
                    close_code = frame.code
                    break
            interrupted = (close_code not in (None, 1000, 1001) or
                           not (finish_reason is not None and
                                self.accept_finish_reason_as_done))
            if interrupted:
                raise self._partial_error(
                    AdapterError("模型流连接中断，未收到结束标记",
                                 code="stream_interrupted", retryable=False),
                    received_frames, business_output=business_output,
                    details={"close_code": close_code})
        except AdapterError:
            raise
        except (OSError, asyncio.TimeoutError) as exc:
            raise AdapterError(f"企业网关连接失败: {type(exc).__name__}",
                               code="network_error", retryable=True) from exc

        provider_error = extract_stream_error(events)
        if provider_error is not None:
            message = str(provider_error.get("message") or "企业网关返回错误")
            code = provider_error.get("code")
            if code in (401, "401", "unauthorized"):
                raise AdapterError("企业网关流鉴权失败", code="auth_failed",
                                   retryable=False)
            if code in (429, "429", "rate_limit_exceeded"):
                raise AdapterError("企业网关流被限流", code="rate_limited",
                                   retryable=True)
            raise AdapterError(message, code="platform_error",
                               details={"code": code}, retryable=False)

        result = aggregate(events)
        if result["argument_errors"]:
            raise AdapterError("模型返回的工具参数不完整或不是合法 JSON",
                               code="format_error",
                               details={"errors": result["argument_errors"][:3]},
                               retryable=False)
        done = bool(result["done"])
        if not done:
            if finish_reason is not None and self.accept_finish_reason_as_done:
                done = True
            else:
                raise self._partial_error(
                    AdapterError("模型流在结束标记前中断", code="stream_interrupted",
                                 retryable=False), received_frames,
                    business_output=business_output)
        return {
            "content": result["content"],
            "tool_calls": result["tool_calls"],
            "finish_reason": result["finish_reason"] or finish_reason,
            "usage": result["usage"],
            "model": model_name,
            "raw": {
                "status": "ok",
                "stream": True,
                "events": len(events),
                "frames": received_frames,
                "done": done,
                "content_chars": content_chars,
            },
        }

    @staticmethod
    def _partial_error(error: AdapterError, received_frames: int,
                       *, business_output: bool,
                       details: Mapping[str, Any] | None = None) -> AdapterError:
        error.partial = business_output  # type: ignore[attr-defined]
        error.details = {
            "frames": received_frames,
            **(dict(details) if details else {}),
        }
        return error
