"""A12: SSE stream assembly and enterprise WebSocket streaming aggregation.

All network activity is a local fake gateway on 127.0.0.1 (random port); no
external service is contacted. Covers split/merged frames, ``\\r\\n``, multi-line
``data:``, heartbeats, empty deltas, fragmented tool-call arguments/names,
tail usage, ``[DONE]``/other end markers, finish-reason termination, stream
interruption and the equality between streamed and one-shot aggregation.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from app.adapters.base import AdapterError
from app.adapters.enterprise_ws import EnterpriseWSAdapter
from app.adapters.stream_events import (
    SSEEventAssembler,
    aggregate,
    extract_stream_error,
)

# --------------------------------------------------------------------------- #
# local fake WebSocket gateway (thread + asyncio loop)
# --------------------------------------------------------------------------- #
class FakeGateway:
    """Runs ``websockets.asyncio.server.serve`` in a background thread."""

    def __init__(self, behavior, *, process_request=None):
        self.behavior = behavior
        self.process_request = process_request
        self.requests: list[dict] = []
        self.connection_count = 0
        self.server_errors: list[BaseException] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._ready = threading.Event()
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def __enter__(self) -> "FakeGateway":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="fake-ws-gateway",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("fake gateway did not start")
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def _run(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start())
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    async def _start(self) -> None:
        from websockets.asyncio.server import serve

        self._server = await serve(
            self._handle, "127.0.0.1", 0, process_request=self.process_request)
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]
        self._ready.set()

    async def _handle(self, websocket) -> None:
        self.connection_count += 1
        current = self.connection_count
        try:
            raw = await websocket.recv()
            body = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
            self.requests.append({
                "body": body,
                "headers": dict(websocket.request.headers),
                "connection": current,
            })
            await self.behavior(websocket, body, current, self)
        except BaseException as exc:  # keep server errors inspectable in tests
            self.server_errors.append(exc)

    def stop(self) -> None:
        if self._loop is None or self._server is None:
            return

        async def _shutdown() -> None:
            self._server.close()
            await self._server.wait_closed()

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(5)


def _ws_config(url: str, **overrides) -> dict:
    config = {
        "provider": "enterprise_ws",
        "ws_url": url,
        "model": "Qwen/Qwen3.5-397B-A17B",
        "request_template": {},
        "stream": True,
        "connect_timeout_s": 2.0,
        "first_packet_timeout_s": 2.0,
        "idle_timeout_s": 2.0,
        "total_timeout_s": 8.0,
        "retry_backoff_s": 0.0,
        "heartbeat_interval_s": None,
        "max_retries": 0,
    }
    config.update(overrides)
    return config


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _frame_behavior(frames, *, delay: float = 0.0, close_after: bool = True):
    async def behavior(websocket, body, connection, gateway):
        for frame in frames:
            await websocket.send(frame)
            if delay:
                await asyncio.sleep(delay)
        if close_after:
            await websocket.close()

    return behavior


def _collect_events() -> tuple[list[dict], object]:
    events: list[dict] = []

    def sink(event: dict) -> dict:
        events.append(event)
        return event

    return events, sink


# --------------------------------------------------------------------------- #
# pure assembler / aggregate tests
# --------------------------------------------------------------------------- #
def test_feed_bytes_handles_split_multibyte_and_crlf():
    text = (
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "你好世界"}}]},
                              ensure_ascii=False) + "\r\n\r\n"
        + "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "，完成"}}]},
                                ensure_ascii=False) + "\r\n\r\n"
        + "data: [DONE]\r\n\r\n"
    )
    raw = text.encode("utf-8")
    assembler = SSEEventAssembler()
    events: list[dict] = []
    for index in range(0, len(raw), 5):  # splits inside Chinese multi-byte chars
        events.extend(assembler.feed(raw[index:index + 5]))
    events.extend(assembler.finish())
    result = aggregate(events)
    assert result["content"] == "你好世界，完成"
    assert result["done"] is True


def test_single_frame_with_multiple_events_and_heartbeat():
    payload = "".join([
        ": keep-alive\n\n",
        _sse({"choices": [{"index": 0, "delta": {"content": "A"}}]}),
        _sse({"choices": [{"index": 0, "delta": {}}]}),  # empty delta ignored
        _sse({"choices": [{"index": 0, "delta": {"content": "B"}}]}),
        "data: [DONE]\n\n",
    ])
    assembler = SSEEventAssembler()
    events = assembler.feed(payload)
    result = aggregate(events)
    assert result["content"] == "AB"
    assert result["done"] is True


def test_multiline_data_payloads_are_merged():
    payload = (
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "X"}}]}) + "\n"
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "Y"}}]}) + "\n\n"
        "data: [DONE]\n\n"
    )
    events = SSEEventAssembler().feed(payload)
    result = aggregate(events)
    assert result["content"] == "XY"


def test_fragmented_tool_calls_by_choice_and_tool_index():
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_a",
             "function": {"name": "list_", "arguments": '{"proj'}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"name": "tasks", "arguments": 'ect_id":"P123",'}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 1, "id": "call_b",
             "function": {"name": "get_", "arguments": '{"id":'}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"status":"in_progress"}'}},
            {"index": 1, "function": {"name": "task", "arguments": '"T1"}'}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                  "total_tokens": 120}},
        "[DONE]",
    ]
    assembler = SSEEventAssembler()
    events: list[dict] = []
    for chunk in chunks:
        if chunk == "[DONE]":
            events.extend(assembler.feed("data: [DONE]\n\n"))
        else:
            events.extend(assembler.feed(_sse(chunk)))
    result = aggregate(events)
    assert result["finish_reason"] == "tool_calls"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20,
                               "total_tokens": 120}
    assert result["done"] is True
    assert result["tool_calls"] == [
        {"id": "call_a", "name": "list_tasks",
         "arguments": {"project_id": "P123", "status": "in_progress"}},
        {"id": "call_b", "name": "get_task", "arguments": {"id": "T1"}},
    ]
    assert result["argument_errors"] == []


def test_empty_delta_and_heartbeat_do_not_produce_result():
    payload = ": ping\n\ndata: {\"choices\":[{\"index\":0,\"delta\":{}}]}\n\n"
    result = aggregate(SSEEventAssembler().feed(payload))
    assert result["content"] == ""
    assert result["tool_calls"] == []
    assert result["finish_reason"] is None
    assert result["done"] is False


def test_invalid_tool_argument_fragments_are_reported_not_guessed():
    payload = _sse({"choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "id": "call_x", "function": {"name": "do", "arguments": '{"a": '}},
    ]}}]})
    result = aggregate(SSEEventAssembler().feed(payload))
    assert result["tool_calls"] == [{"id": "call_x", "name": "do", "arguments": {}}]
    assert result["argument_errors"] and "JSON" in result["argument_errors"][0]["error"]


def test_finish_event_name_and_json_done_marker_are_end_signals():
    events = SSEEventAssembler().feed(
        "event: done\ndata: {}\n\n")
    assert aggregate(events)["done"] is True
    events = SSEEventAssembler().feed(_sse({"done": True}))
    assert aggregate(events)["done"] is True


def test_finish_flushes_unterminated_trailing_event():
    assembler = SSEEventAssembler()
    events = assembler.feed(_sse({"choices": [{"index": 0, "delta": {"content": "ok"}}]})
                            + "data: [DONE]")  # no trailing blank line
    events.extend(assembler.finish())
    result = aggregate(events)
    assert result["content"] == "ok"
    assert result["done"] is True


def test_provider_error_frame_is_detected():
    events = SSEEventAssembler().feed(
        _sse({"error": {"code": 429, "message": "rate limit"}}))
    error = extract_stream_error(events)
    assert error == {"code": 429, "message": "rate limit"}


# --------------------------------------------------------------------------- #
# fake gateway / adapter integration
# --------------------------------------------------------------------------- #
def test_ws_streamed_result_matches_one_shot_aggregation():
    content_chunks = ["查询", "项目", " P123", " 的任务"]
    complete_payloads = [
        {"choices": [{"index": 0, "delta": {"content": "查询"}}]},
        {"choices": [{"index": 0, "delta": {"content": "项目"}}]},
        {"choices": [{"index": 0, "delta": {"content": " P123"}}]},
        {"choices": [{"index": 0, "delta": {"content": " 的任务"}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1",
             "function": {"name": "list_tasks",
                          "arguments": '{"project_id":"P123",'
                                        '"status":"in_progress"}'}}]},
            "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
        {"choices": [], "done": True},
    ]
    complete_stream = "".join(_sse(payload) for payload in complete_payloads)
    # one-shot aggregation reference
    expected = aggregate(SSEEventAssembler().feed(complete_stream))
    assert expected["content"] == "".join(content_chunks)

    # stream split into arbitrary pieces across WS frames (including mid-event)
    pieces = [complete_stream[i:i + 17] for i in range(0, len(complete_stream), 17)]
    events, sink = _collect_events()
    with FakeGateway(_frame_behavior(pieces)) as gateway:
        adapter = EnterpriseWSAdapter(_ws_config(gateway.url))
        result = adapter.invoke(
            [{"role": "user", "content": "hi"}], {}, [], emit=sink)
    assert result["content"] == expected["content"]
    assert result["tool_calls"] == expected["tool_calls"]
    assert result["finish_reason"] == "tool_calls"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20,
                               "total_tokens": 120}
    types = [event["type"] for event in events]
    assert "model_call_started" in types
    assert "model_call_finished" in types
    assert types.index("model_call_started") < types.index("model_call_finished")
    # first-packet status event is present but carries no business content
    assert any(event["type"] == "model_delta"
               and event["payload"].get("phase") == "first_packet"
               for event in events)


def test_ws_single_frame_multiple_events_crlf_and_empty_deltas():
    frames = [
        ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "一"}}]})
         + "\r\n\r\n").encode("utf-8"),
        (
            ": heartbeat\n\n"
            "data: " + json.dumps({"choices": [{"index": 0, "delta": {}}]}) + "\n\n"
            "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "二"}}]}) + "\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8"),
    ]
    with FakeGateway(_frame_behavior(frames)) as gateway:
        adapter = EnterpriseWSAdapter(_ws_config(gateway.url))
        result = adapter.invoke([{"role": "user", "content": "q"}], {}, [])
    assert result["content"] == "一二"
    assert result["finish_reason"] is None
    assert result["usage"] is None


def test_ws_tail_usage_after_finish_reason_is_consumed():
    frames = [
        _sse({"choices": [{"index": 0, "delta": {"content": "ok"}}]}),
        _sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        _sse({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}),
        _sse({"choices": [], "done": True}),
    ]
    with FakeGateway(_frame_behavior(frames)) as gateway:
        adapter = EnterpriseWSAdapter(_ws_config(gateway.url))
        result = adapter.invoke([{"role": "user", "content": "q"}], {}, [])
    assert result["finish_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}


def test_ws_stream_interrupted_after_partial_content_is_not_retried_or_scored():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse(
            {"choices": [{"index": 0, "delta": {"content": "半成品"}}]}))
        await websocket.close(code=1011, reason="gateway crash")

    events, sink = _collect_events()
    with FakeGateway(behavior) as gateway:
        adapter = EnterpriseWSAdapter(_ws_config(gateway.url))
        with pytest.raises(AdapterError) as excinfo:
            adapter.invoke([{"role": "user", "content": "q"}], {}, [], emit=sink)
        assert gateway.connection_count == 1  # no cross-connection retry
    error = excinfo.value
    assert error.code == "stream_interrupted"
    assert error.error_kind == "infrastructure"
    types = [event["type"] for event in events]
    assert "model_call_started" in types
    assert "model_call_failed" in types
    failed = [event for event in events if event["type"] == "model_call_failed"][-1]
    assert failed["payload"]["error"]["code"] == "stream_interrupted"


def test_ws_finish_reason_without_done_marker_can_complete_the_stream():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse({"choices": [{"index": 0, "delta": {"content": "done"}}]}))
        await websocket.send(_sse(
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
        await websocket.close()

    with FakeGateway(behavior) as gateway:
        adapter = EnterpriseWSAdapter(_ws_config(gateway.url))
        result = adapter.invoke([{"role": "user", "content": "q"}], {}, [])
        assert result["content"] == "done"
        assert gateway.connection_count == 1


def test_ws_finish_reason_as_done_can_be_disabled():
    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse(
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
        await websocket.close()

    with FakeGateway(behavior) as gateway:
        adapter = EnterpriseWSAdapter(_ws_config(
            gateway.url, accept_finish_reason_as_done=False))
        with pytest.raises(AdapterError) as excinfo:
            adapter.invoke([{"role": "user", "content": "q"}], {}, [])
    assert excinfo.value.code == "stream_interrupted"


def test_ws_invalid_json_arguments_raise_format_error():
    frames = [
        _sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c", "function": {"name": "do", "arguments": '{"x":'}}]}}]}),
        _sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        _sse({"choices": [], "done": True}),
    ]
    with FakeGateway(_frame_behavior(frames)) as gateway:
        adapter = EnterpriseWSAdapter(_ws_config(gateway.url))
        with pytest.raises(AdapterError) as excinfo:
            adapter.invoke([{"role": "user", "content": "q"}], {}, [])
    assert excinfo.value.code == "format_error"
    assert excinfo.value.error_kind == "capability"
