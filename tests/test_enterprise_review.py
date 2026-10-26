"""Focused local-gateway regression coverage for enterprise_ws."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.adapters.base import AdapterError
from app.adapters.enterprise_ws import EnterpriseWSAdapter
from tests.test_a12_stream_assembly import FakeGateway, _sse, _ws_config


def test_enterprise_request_body_resolves_api_key_ref_without_auth_header():
    adapter = EnterpriseWSAdapter({"ws_url": "ws://127.0.0.1:1",
                                   "api_key_ref": {"value": "local-secret"}})
    body = adapter.build_request_body([{"role": "user", "content": "q"}], [],
                                      adapter.model_config)
    assert body["api_key"] == "local-secret"


def test_finish_reason_grace_accepts_live_connection_without_done_marker():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse({
            "choices": [{"index": 0, "delta": {"content": "ok"},
                         "finish_reason": "stop"}],
        }))
        # A gateway may leave the socket open briefly after its final choice.
        await asyncio.sleep(0.5)

    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, idle_timeout_s=0.1,
                            first_packet_timeout_s=0.5, total_timeout_s=1.0)
        result = EnterpriseWSAdapter(config).invoke([{"role": "user", "content": "q"}], {}, [])
    assert result["content"] == "ok"
    assert result["finish_reason"] == "stop"
    assert gateway.connection_count == 1


def test_heartbeat_frame_without_business_output_retries_and_second_connection_succeeds():
    async def behavior(websocket, body, connection, gateway):
        if connection == 1:
            await websocket.send(": keep-alive\n\n")
            await asyncio.sleep(0.5)
            return
        await websocket.send(_sse({
            "choices": [{"index": 0, "delta": {"content": "retry"}}],
        }) + _sse({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }) + "data: [DONE]\n\n")

    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, idle_timeout_s=0.1,
                            first_packet_timeout_s=0.5, total_timeout_s=1.0,
                            max_retries=1)
        result = EnterpriseWSAdapter(config).invoke([{"role": "user", "content": "q"}], {}, [])
    assert result["content"] == "retry"
    assert gateway.connection_count == 2


def test_finish_reason_without_content_uses_grace_before_first_packet_timeout():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }))
        await asyncio.sleep(0.02)
        await websocket.send(_sse({"choices": [], "usage": {
            "prompt_tokens": 1, "completion_tokens": 0,
        }}))
        await websocket.send("data: [DONE]\n\n")

    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, first_packet_timeout_s=0.05,
                            finish_grace_s=0.2)
        result = EnterpriseWSAdapter(config).invoke([{"role": "user", "content": "q"}], {}, [])
    assert result["content"] == ""
    assert result["finish_reason"] == "stop"
    assert result["usage"]["input_tokens"] == 1


def test_finish_grace_collects_usage_and_done_tail():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse({
            "choices": [{"index": 0, "delta": {"content": "ok"},
                         "finish_reason": "stop"}],
        }))
        await asyncio.sleep(0.02)
        await websocket.send(_sse({"choices": [], "usage": {
            "prompt_tokens": 3, "completion_tokens": 2,
        }}))
        await websocket.send("data: [DONE]\n\n")

    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, finish_grace_s=0.2)
        result = EnterpriseWSAdapter(config).invoke([{"role": "user", "content": "q"}], {}, [])
    assert result["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}


def test_api_key_ref_missing_environment_is_auth_failure():
    adapter = EnterpriseWSAdapter({"ws_url": "ws://127.0.0.1:1",
                                   "api_key_ref": "env:DOES_NOT_EXIST_FOR_REVIEW"})
    with pytest.raises(AdapterError) as excinfo:
        adapter.build_request_body([], [], adapter.model_config)
    assert excinfo.value.code == "auth_failed"


def test_total_deadline_includes_retry_backoff():
    async def behavior(websocket, body, connection, gateway):
        await asyncio.sleep(0.5)

    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, first_packet_timeout_s=0.05,
                            total_timeout_s=0.15, max_retries=2,
                            retry_backoff_s=1.0)
        started = time.monotonic()
        with pytest.raises(AdapterError) as excinfo:
            EnterpriseWSAdapter(config).invoke([{"role": "user", "content": "q"}], {}, [])
        elapsed = time.monotonic() - started
    assert excinfo.value.code == "timeout"
    assert elapsed < 0.6
    assert gateway.connection_count == 1


def test_cancel_during_retry_backoff_is_responsive():
    cancelled = threading.Event()

    async def behavior(websocket, body, connection, gateway):
        await asyncio.sleep(0.5)

    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, first_packet_timeout_s=0.05,
                            total_timeout_s=2.0, max_retries=2,
                            retry_backoff_s=1.0,
                            cancel_check=cancelled.is_set)
        timer = threading.Timer(0.15, cancelled.set)
        timer.start()
        started = time.monotonic()
        with pytest.raises(AdapterError) as excinfo:
            EnterpriseWSAdapter(config).invoke([{"role": "user", "content": "q"}], {}, [])
        timer.cancel()
        elapsed = time.monotonic() - started
    assert excinfo.value.code == "cancelled"
    assert elapsed < 0.6


def test_provider_error_frame_fails_without_waiting_for_idle_timeout():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse({"error": {"code": 500, "message": "gateway failed"}}))
        await asyncio.sleep(0.5)

    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, idle_timeout_s=1.0, total_timeout_s=2.0)
        started = time.monotonic()
        with pytest.raises(AdapterError) as excinfo:
            EnterpriseWSAdapter(config).invoke([{"role": "user", "content": "q"}], {}, [])
        elapsed = time.monotonic() - started
    assert excinfo.value.code == "platform_error"
    assert elapsed < 0.4


def test_business_output_followed_by_idle_timeout_does_not_retry():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse({"choices": [{"delta": {"content": "partial"}}]}))
        await asyncio.sleep(0.6)
    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, idle_timeout_s=0.05, max_retries=2)
        with pytest.raises(AdapterError) as exc:
            EnterpriseWSAdapter(config).invoke([], {}, [])
    assert exc.value.code == "timeout"
    assert gateway.connection_count == 1


def test_finish_only_remains_valid_beyond_first_packet_deadline():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
        await asyncio.sleep(0.8)
    with FakeGateway(behavior) as gateway:
        config = _ws_config(gateway.url, first_packet_timeout_s=0.05,
                            finish_grace_s=0.25, total_timeout_s=1)
        result = EnterpriseWSAdapter(config).invoke([], {}, [])
    assert result["finish_reason"] == "stop"
