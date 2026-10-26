"""Enterprise WebSocket gateway protocol used in production.

OpenAI Chat Completions request body sent as one WebSocket frame; identity via
body ``api_key`` (service name) + ``sender`` header; every response frame is a
complete chunk JSON that carries ``usage`` and telemetry (``result_info`` /
``debug_info`` echoing the request); there is no ``[DONE]`` marker.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.adapters.base import AdapterError
from app.adapters.enterprise_ws import EnterpriseWSAdapter
from app.adapters.stream_events import SSEEventAssembler, SSEParseError
from tests.test_a11_ws_gateway import FakeGateway

TEMPLATE = {
    "user": "u10001",
    "extra_body": {"enable_thinking": False},
    "stream_options": {"include_usage": True},
}
HEADERS = {"sender": "E12345"}
TOOLS = [{"name": "list_tasks", "description": "查询任务",
          "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}}}]


def _frame(step: int, body: dict, *, content=None, tool=None, finish=None,
           echo_pad: int = 0, model: str = "vendor-model-x") -> str:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if tool is not None:
        delta["tool_calls"] = [tool]
    return json.dumps({
        "id": "chatcmpl-1", "object": "chat.completion.chunk", "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        # Cumulative usage on every frame.
        "usage": {"prompt_tokens": 120, "completion_tokens": step + 1,
                  "total_tokens": 121 + step},
        "result_info": {"code": 0, "msg": "ok", "trace_id": "trace-1"},
        "debug_info": {"request": body, "pad": "x" * echo_pad},
    }, ensure_ascii=False)


async def _hold_open(websocket) -> None:
    # The gateway never sends [DONE] and keeps the socket open after finishing.
    try:
        await asyncio.wait_for(websocket.wait_closed(), timeout=5)
    except asyncio.TimeoutError:
        pass


def _text_gateway(n_frames: int = 4, *, echo_pad: int = 0, ack: bool = False):
    async def behavior(websocket, body, connection, gateway):
        if ack:
            await websocket.send(json.dumps({"code": 0, "message": "success",
                                             "result_info": {"trace_id": "t"}}))
        for step in range(n_frames):
            await websocket.send(_frame(step, body, content=f"片段{step}", echo_pad=echo_pad))
        await websocket.send(_frame(n_frames, body, finish="stop", echo_pad=echo_pad))
        await _hold_open(websocket)
    return behavior


def _adapter(url: str, **overrides) -> EnterpriseWSAdapter:
    config = {"provider": "enterprise_ws", "ws_url": url, "model": "vendor-model-x",
              "api_key_ref": "AccessServiceName", "request_template": dict(TEMPLATE),
              "headers": dict(HEADERS), "temperature": 0, "max_tokens": 1024,
              "max_retries": 0}
    config.update(overrides)
    return EnterpriseWSAdapter(config)


def _messages():
    return [{"role": "system", "content": "系统提示"}, {"role": "user", "content": "查询项目 P123"}]


def test_request_body_and_headers_match_gateway_protocol():
    with FakeGateway(_text_gateway()) as gateway:
        _adapter(gateway.url).invoke(_messages(), {}, TOOLS)
    request = gateway.requests[0]
    body, headers = request["body"], request["headers"]
    assert body["api_key"] == "AccessServiceName"          # service name in the body
    assert body["user"] == "u10001"
    assert body["extra_body"] == {"enable_thinking": False}
    assert body["stream_options"] == {"include_usage": True}
    assert body["model"] == "vendor-model-x"
    assert body["stream"] is True
    assert body["temperature"] == 0 and body["max_tokens"] == 1024
    assert body["messages"] == _messages()
    assert body["tools"][0]["function"]["name"] == "list_tasks"
    assert headers["sender"] == "E12345"
    assert "authorization" not in headers                  # never Bearer


def test_no_done_marker_and_per_frame_usage_finish_immediately():
    with FakeGateway(_text_gateway(n_frames=4)) as gateway:
        started = time.monotonic()
        result = _adapter(gateway.url).invoke(_messages(), {}, [])
        elapsed = time.monotonic() - started
    assert result["content"] == "片段0片段1片段2片段3"
    assert result["finish_reason"] == "stop"
    # Cumulative usage: the last frame wins, frames are not summed.
    assert result["usage"] == {"input_tokens": 120, "output_tokens": 5, "total_tokens": 125}
    assert result["model"] == "vendor-model-x"
    # The finish frame already carries usage, so no tail grace wait is needed.
    assert elapsed < 0.5, elapsed


def test_streamed_tool_call_with_per_frame_usage():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_frame(0, body, tool={
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "list_tasks", "arguments": ""}}))
        for step, part in enumerate(['{"project_id"', ':"P123"}'], start=1):
            await websocket.send(_frame(step, body, tool={"index": 0, "function": {"arguments": part}}))
        await websocket.send(_frame(3, body, finish="tool_calls"))
        await _hold_open(websocket)

    with FakeGateway(behavior) as gateway:
        result = _adapter(gateway.url).invoke(_messages(), {}, TOOLS)
    assert result["tool_calls"] == [{"id": "call_1", "name": "list_tasks",
                                     "arguments": {"project_id": "P123"}}]
    assert result["finish_reason"] == "tool_calls"
    assert result["usage"]["output_tokens"] == 4


def test_success_ack_frame_without_choices_is_not_an_error():
    with FakeGateway(_text_gateway(ack=True)) as gateway:
        result = _adapter(gateway.url).invoke(_messages(), {}, [])
    assert result["content"].startswith("片段0")


def test_gateway_error_frame_is_still_reported():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(json.dumps({"code": 40003, "message": "sender 无权限访问该模型"}))
        await _hold_open(websocket)

    with FakeGateway(behavior) as gateway:
        with pytest.raises(AdapterError) as excinfo:
            _adapter(gateway.url).invoke(_messages(), {}, [])
    assert "sender 无权限" in excinfo.value.message


def test_large_telemetry_on_every_frame_is_tolerated():
    # 200 frames each echoing ~60 KB: ~12M characters over one call.
    with FakeGateway(_text_gateway(n_frames=200, echo_pad=60_000)) as gateway:
        result = _adapter(gateway.url, total_timeout_s=30).invoke(_messages(), {}, [])
    assert result["content"].endswith("片段199")
    assert result["usage"]["output_tokens"] == 201


def test_single_frame_larger_than_one_mebibyte_is_accepted():
    with FakeGateway(_text_gateway(n_frames=2, echo_pad=1_500_000)) as gateway:
        result = _adapter(gateway.url).invoke(_messages(), {}, [])
    assert result["content"] == "片段0片段1"


def test_assembler_bounds_only_unfinished_data():
    assembler = SSEEventAssembler(max_buffer_chars=1_000)
    frame = json.dumps({"choices": [{"index": 0, "delta": {"content": "x" * 600}}]})
    for _ in range(10):                      # 6000+ chars in complete frames
        assert len(assembler.feed(frame)) == 1
    with pytest.raises(SSEParseError):
        assembler.feed("data: " + "y" * 2_000)   # one unterminated event over the limit
