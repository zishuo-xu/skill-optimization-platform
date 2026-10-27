"""A11: run a case through a real (local, fake) WebSocket gateway.

The gateway is a real ``websockets`` server bound to a random 127.0.0.1 port; the
adapter is the production :class:`EnterpriseWSAdapter`. Assertions cover:

* the request body is sent over WS with gateway fields preserved verbatim
  (``api_key``/``user``/``extra_body`` ...) and never converted to Authorization;
* the messages are assembled from the frozen system prompt + main SKILL.md +
  sub-skill index + query + allowed context and never contain expected answers;
* internal tools / business tools / fixtures drive a complete case result;
* auth failures are not retried; timeouts retry at most twice; cancellation
  closes the connection.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from app.adapters.base import AdapterError
from app.adapters.enterprise_ws import EnterpriseWSAdapter
from app.execution import build_messages, run_case

ANSWER_SENTINEL = "EXPECTED_ANSWER_MUST_NOT_LEAK_7f3a"

# --------------------------------------------------------------------------- #
# local fake WebSocket gateway (real server on 127.0.0.1, random port)
# --------------------------------------------------------------------------- #
class FakeGateway:
    def __init__(self, behavior, *, process_request=None):
        self.behavior = behavior
        self.process_request = process_request
        self.requests: list[dict] = []
        self.connection_count = 0
        self.handshake_count = 0
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
        self._thread = threading.Thread(target=self._run, name="fake-ws-a11", daemon=True)
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

        self._server = await serve(self._handle, "127.0.0.1", 0,
                                   process_request=self._process_request)
        self.port = self._server.sockets[0].getsockname()[1]
        self._ready.set()

    def _process_request(self, connection, request):
        self.handshake_count += 1
        if self.process_request is None:
            return None
        return self.process_request(connection, request)

    async def _handle(self, websocket) -> None:
        self.connection_count += 1
        connection = self.connection_count
        try:
            raw = await websocket.recv()
            body = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
            self.requests.append({
                "body": body,
                "headers": {key.lower(): value for key, value in
                            websocket.request.headers.items()},
                "connection": connection,
            })
            await self.behavior(websocket, body, connection, self)
        except BaseException as exc:
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

    # -- assertions helpers ------------------------------------------------ #
    def serialized_requests(self) -> str:
        return json.dumps(self.requests, ensure_ascii=False)


def _unauthorized_process_request(connection, request):
    return connection.respond(401, "unauthorized")


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _stream(content: str = "", tool_calls: list[dict] | None = None,
            finish_reason: str | None = None, usage: dict | None = None,
            done: bool = True) -> list[str]:
    frames: list[str] = []
    if content:
        frames.append(_sse({"choices": [{"index": 0, "delta": {"content": content}}]}))
    if tool_calls:
        frames.append(_sse({"choices": [{"index": 0, "delta": {"tool_calls": tool_calls}}]}))
    frames.append(_sse({"choices": [{"index": 0, "delta": {},
                                     "finish_reason": finish_reason or "stop"}]}))
    if usage:
        frames.append(_sse({"choices": [], "usage": usage}))
    if done:
        frames.append(_sse({"choices": [], "done": True}))
    return frames


def _scripted_behavior(scripts: dict[int, list[str]], *, close_code: int = 1000):
    async def behavior(websocket, body, connection, gateway):
        frames = scripts.get(connection) or scripts.get(-1) or []
        for frame in frames:
            await websocket.send(frame)
            await asyncio.sleep(0)
        await websocket.close(code=close_code)

    return behavior


def _gateway_config(url: str, **overrides) -> dict:
    config = {
        "provider": "enterprise_ws",
        "ws_url": url,
        "model": "Qwen/Qwen3.5-397B-A17B",
        "stream": True,
        "request_template": {
            "api_key": "AccessService",
            "user": "skillopt-tester",
            "extra_body": {"enable_thinking": False},
            "stream_options": {"include_usage": True},
            "temperature": 0.0,
            "max_tokens": 512,
        },
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


# --------------------------------------------------------------------------- #
# fixtures / case request
# --------------------------------------------------------------------------- #
@pytest.fixture()
def skill_package(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "# 主 Skill\n\n根据用户请求选择子技能。\n", encoding="utf-8")
    (root / "references" / "task_query.md").write_text(
        "任务查询子技能：查询项目任务列表，需要 project_id 与 status。\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "main": "SKILL.md",
        "subskills": [{
            "skill_id": "task_query", "name": "任务查询",
            "path": "references/task_query.md",
            "description": "查询项目或成员的任务列表、任务详情与状态",
        }],
    }
    return root, manifest


def _case_request(skill_root, manifest, config: dict, *, limits=None,
                  loader_config=None) -> dict:
    return {
        "query": "查询项目 P123 中进行中的任务",
        "context": {"timezone": "Asia/Shanghai"},
        "skill_snapshot_path": str(skill_root),
        "system_prompt": "你是使用 Skill 包完成用户任务的执行 Agent。",
        "model_config": config,
        "request_template": {},
        "fixtures": [{
            "name": "list_tasks",
            "match": {"project_id": "P123", "status": "in_progress"},
            "response": {"tasks": []},
            "consume": "always",
        }],
        "limits": limits or {"max_model_calls": 6, "max_steps": 6,
                             "case_timeout_s": 10, "max_context_chars": 20000},
        "loader_config": loader_config or {"mode": "on_demand"},
        "run_id": "run_a11",
        "case_id": "case_001",
        "manifest": manifest,
        "allowed_skill_ids": ["task_query"],
        # Deliberately planted to prove the executor scrubs it everywhere.
        "expected": {
            "action": "tool_call",
            "routes": [["task_query"]],
            "tool_calls": [{"name": "list_tasks",
                            "arguments": {"project_id": "P123",
                                          "status": "in_progress"}}],
            "answer": ANSWER_SENTINEL,
        },
    }


def _case_scripts() -> dict[int, list[str]]:
    return {
        # turn 1: dispatch through internal tools
        1: _stream(tool_calls=[
            {"index": 0, "id": "call_select",
             "function": {"name": "select_skill",
                          "arguments": '{"skill_id":"task_query"}'}},
            {"index": 1, "id": "call_read",
             "function": {"name": "read_skill",
                          "arguments": '{"path":"references/task_query.md"}'}},
        ], finish_reason="tool_calls",
            usage={"prompt_tokens": 120, "completion_tokens": 12}),
        # turn 2: business tool call
        2: _stream(tool_calls=[
             {"index": 0, "id": "call_task",
             "function": {"name": "call_tool",
                          "arguments": '{"name":"list_tasks","arguments":{"project_id":"P123","status":"in_progress"}}'}},
        ], finish_reason="tool_calls",
            usage={"prompt_tokens": 150, "completion_tokens": 20}),
        # turn 3: finish
        3: _stream(tool_calls=[
            {"index": 0, "id": "call_finish",
             "function": {"name": "finish",
                          "arguments": '{"action":"tool_call",'
                                        '"selected_skills":["task_query"],'
                                        '"missing_fields":[],"summary":"已查询任务"}'}},
        ], finish_reason="tool_calls",
            usage={"prompt_tokens": 180, "completion_tokens": 8}),
    }


# --------------------------------------------------------------------------- #
# A11 main case
# --------------------------------------------------------------------------- #
def test_ws_gateway_runs_case_with_correct_messages_and_no_answers(skill_package):
    skill_root, manifest = skill_package
    events: list[dict] = []

    with FakeGateway(_scripted_behavior(_case_scripts())) as gateway:
        config = _gateway_config(gateway.url)
        adapter = EnterpriseWSAdapter(config)
        result = run_case(_case_request(skill_root, manifest, config),
                          adapter, emit=events.append)
        assert gateway.server_errors == []
        assert gateway.connection_count == 3
        requests = list(gateway.requests)

    # -- result -------------------------------------------------------- #
    assert result["error"] is None, result["error"]
    assert result["selected_skills"] == ["task_query"]
    assert result["route_events"] and result["route_events"][0]["skill_id"] == "task_query"
    assert result["action"] == "tool_call"
    assert result["tool_calls"] == [{"name": "list_tasks",
                                     "arguments": {"project_id": "P123",
                                                   "status": "in_progress"}}]
    assert result["missing_fields"] == []
    assert result["result_source"] == "native_tool"
    assert result["capabilities"]["native_tools"] is True
    assert result["usage"] == {"input_tokens": 450, "output_tokens": 40,
                               "total_tokens": 490}
    assert result["fixture_misses"] == []
    assert result["model_calls"] == 3

    # -- request bodies: gateway fields preserved, no Authorization --- #
    for request in requests:
        body = request["body"]
        assert body["api_key"] == "AccessService"
        assert body["user"] == "skillopt-tester"
        assert body["extra_body"] == {"enable_thinking": False}
        assert body["stream_options"] == {"include_usage": True}
        assert body["model"] == "Qwen/Qwen3.5-397B-A17B"
        assert body["stream"] is True
        assert "authorization" not in request["headers"]
        assert "api_key" not in request["headers"]
        assert ANSWER_SENTINEL not in json.dumps(body, ensure_ascii=False)

    # -- messages built from the frozen markers and no expected ------- #
    first_messages = requests[0]["body"]["messages"]
    assert first_messages[0]["role"] == "system"
    system_text = first_messages[0]["content"]
    assert "【主 SKILL.md】" in system_text
    assert "【子技能清单】" in system_text
    assert "根据用户请求选择子技能" in system_text
    assert "task_query" in system_text
    assert first_messages[1]["role"] == "user"
    user_text = first_messages[1]["content"]
    assert user_text.startswith("用户请求：查询项目 P123 中进行中的任务")
    assert "\n上下文：" in user_text
    assert "Asia/Shanghai" in user_text
    all_serialized = json.dumps([request["body"] for request in requests],
                                ensure_ascii=False)
    assert ANSWER_SENTINEL not in all_serialized
    assert '"expected"' not in all_serialized

    # follow-up turns carry tool outputs, never the expected block
    second_messages = requests[1]["body"]["messages"]
    assert any(message["role"] == "tool"
               and "【文件 references/task_query.md】" in message["content"]
               for message in second_messages)
    third_messages = requests[2]["body"]["messages"]
    assert any(message["role"] == "tool"
               and '"tasks":[]' in message["content"].replace(" ", "")
               for message in third_messages)

    # -- events carry case_id and unique call ids --------------------- #
    types = [event["type"] for event in events]
    assert types.count("model_call_started") == 3
    assert types.count("model_call_finished") == 3
    assert "skill_selected" in types
    assert "skill_read" in types
    assert "tool_result" in types
    call_ids = {event["call_id"] for event in events
                if event["type"] == "model_call_started"}
    assert len(call_ids) == 3
    assert all(event["case_id"] == "case_001" for event in events)
    assert ANSWER_SENTINEL not in json.dumps(events, ensure_ascii=False)
    assert ANSWER_SENTINEL not in json.dumps(result, ensure_ascii=False)


def test_ws_gateway_401_auth_failure_is_not_retried(skill_package):
    skill_root, manifest = skill_package
    with FakeGateway(None, process_request=_unauthorized_process_request) as gateway:
        config = _gateway_config(gateway.url)
        adapter = EnterpriseWSAdapter(config)
        with pytest.raises(AdapterError) as excinfo:
            adapter.invoke([{"role": "user", "content": "q"}], {}, [])
        assert gateway.handshake_count == 1
    assert excinfo.value.code == "auth_failed"
    assert excinfo.value.error_kind == "infrastructure"

    # Through run_case the failure becomes an infrastructure case error.
    with FakeGateway(None, process_request=_unauthorized_process_request) as gateway:
        config = _gateway_config(gateway.url)
        result = run_case(_case_request(skill_root, manifest, config),
                          EnterpriseWSAdapter(config))
        assert gateway.handshake_count == 1
    assert result["error"]["code"] == "auth_failed"
    assert result["error_kind"] == "infrastructure"
    assert result["tool_calls"] == []


def test_ws_gateway_first_packet_timeout_retries_then_succeeds():
    async def behavior(websocket, body, connection, gateway):
        if connection <= 2:
            await asyncio.sleep(1.0)  # wait for the adapter to time out
            return
        for frame in _stream(content="重试成功", finish_reason="stop"):
            await websocket.send(frame)
        await websocket.close()

    events: list[dict] = []
    with FakeGateway(behavior) as gateway:
        config = _gateway_config(gateway.url, first_packet_timeout_s=0.15,
                                 total_timeout_s=3.0, max_retries=2)
        adapter = EnterpriseWSAdapter(config)
        result = adapter.invoke([{"role": "user", "content": "q"}], {}, [],
                                emit=events.append)
        assert gateway.connection_count == 3
    assert result["content"] == "重试成功"
    attempts = [event["attempt"] for event in events
                if event["type"] == "model_call_started"]
    assert attempts == [1, 2, 3]
    retries = [event for event in events
               if event["type"] == "model_call_failed"
               and event["payload"].get("retrying")]
    assert len(retries) == 2
    assert all(event["payload"]["error"]["code"] == "timeout" for event in retries)


def test_ws_gateway_timeout_retries_at_most_twice():
    async def behavior(websocket, body, connection, gateway):
        await asyncio.sleep(1.0)  # never send a first packet

    with FakeGateway(behavior) as gateway:
        config = _gateway_config(gateway.url, first_packet_timeout_s=0.1,
                                 total_timeout_s=1.0, max_retries=2)
        adapter = EnterpriseWSAdapter(config)
        with pytest.raises(AdapterError) as excinfo:
            adapter.invoke([{"role": "user", "content": "q"}], {}, [])
        assert gateway.connection_count == 3  # initial + 2 retries
    assert excinfo.value.code == "timeout"


def test_ws_gateway_cancel_closes_connection_and_returns_cancelled(skill_package):
    skill_root, manifest = skill_package
    cancelled = threading.Event()
    release = threading.Event()

    async def behavior(websocket, body, connection, gateway):
        await websocket.send(_sse(
            {"choices": [{"index": 0, "delta": {"content": "部分"}}]}))
        timer = threading.Timer(0.3, cancelled.set)
        timer.start()
        try:
            while not release.is_set():
                try:
                    await asyncio.wait_for(websocket.recv(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    return  # client closed the connection -> expected
        finally:
            timer.cancel()

    with FakeGateway(behavior) as gateway:
        config = _gateway_config(gateway.url, first_packet_timeout_s=2.0,
                                 idle_timeout_s=5.0, total_timeout_s=10.0)
        adapter = EnterpriseWSAdapter(config)
        started = time.monotonic()
        with pytest.raises(AdapterError) as excinfo:
            adapter.invoke([{"role": "user", "content": "q"}],
                           {"cancel_check": cancelled.is_set}, [])
        elapsed = time.monotonic() - started
    assert excinfo.value.code == "cancelled"
    assert elapsed < 3.0


def test_run_case_cancel_before_start_returns_cancelled_without_connection(skill_package):
    skill_root, manifest = skill_package
    with FakeGateway(_scripted_behavior(_case_scripts())) as gateway:
        config = _gateway_config(gateway.url)
        request = _case_request(skill_root, manifest, config)
        request["cancel_check"] = lambda: True
        result = run_case(request, EnterpriseWSAdapter(config))
        assert gateway.connection_count == 0
    assert result["error"]["code"] == "cancelled"
    assert result["error_kind"] == "cancelled"


def test_messages_loader_preload_includes_subskill_content(skill_package):
    skill_root, manifest = skill_package
    request = _case_request(skill_root, manifest, {})
    messages = build_messages(request, loader_config={"mode": "preload_all"})
    system_text = messages[0]["content"]
    assert "任务查询子技能：查询项目任务列表" in system_text
    assert "【子技能内容 references/task_query.md】" in system_text
    assert ANSWER_SENTINEL not in json.dumps(messages, ensure_ascii=False)


def test_messages_context_overflow_fails_or_applies_frozen_strategy(skill_package):
    from app.execution import run_case

    skill_root, manifest = skill_package
    request = _case_request(skill_root, manifest, {})
    request["limits"] = {"max_context_chars": 10, "context_overflow": "error"}
    result = run_case(request, _NoopAdapter())
    assert result["error"]["code"] == "context_overflow"
    assert result["error_kind"] == "capability"

    request = _case_request(skill_root, manifest, {})
    request["limits"] = {"max_context_chars": 10,
                         "context_overflow": "frozen_strategy"}
    request["loader_config"] = {"mode": "preload_all"}
    result = run_case(request, _NoopAdapter())
    # Frozen strategy drops preloaded content; if still over the hard limit the
    # explicit error is returned instead of silently truncating.
    assert result["error"]["code"] == "context_overflow"


class _NoopAdapter:
    name = "noop"
    capabilities = {"routing": True, "tool_arguments": False, "native_tools": False,
                    "usage": False, "streaming": False}
    model_name = ""

    def invoke(self, messages, config, tools, *, emit=None):  # pragma: no cover
        raise AssertionError("adapter must not be called for a context overflow")
