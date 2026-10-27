"""A13: capability declaration, routing-only adapters and protocol strictness.

Covers adapter registry, capability reporting (``routing``/``tool_arguments``/
``native_tools``/``usage``/``streaming``), routing-only text adapters whose tool
argument metric must be *not evaluated*, frozen text protocol parse failures,
internal tool path safety / finish consistency, fixture simulation, execution
limits (model calls / steps / timeout / cancellation) and truncation
classification.

No external network is used.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import event_types, execution
from app.adapters.base import (
    AdapterError,
    ModelAdapter,
    normalize_capabilities,
)
from app.adapters.enterprise_ws import EnterpriseWSAdapter
from app.adapters.openai_http import OpenAIHTTPAdapter, _error_from_status
from app.adapters.registry import build_adapter
from app.adapters.text_parse import TextParseError, parse_routing, parse_tool_calls
from app.errors import AppError
from app.execution import run_case
from app.obs_tools import ObsToolError, ObsToolRuntime

ANSWER_SENTINEL = "A13_EXPECTED_ANSWER_NEVER_LEAK"


# --------------------------------------------------------------------------- #
# test doubles
# --------------------------------------------------------------------------- #
class ScriptedAdapter(ModelAdapter):
    """Deterministic adapter; records every request for leak assertions."""

    name = "scripted"

    def __init__(self, responses, *, capabilities: dict | None = None,
                 delay: float = 0.0):
        super().__init__({"capabilities": capabilities} if capabilities else {})
        if capabilities:
            self.capabilities = normalize_capabilities(capabilities)
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.delay = delay

    def invoke(self, messages, config, tools, *, emit=None):
        call = {
            "messages": json.loads(json.dumps(messages, ensure_ascii=False,
                                              default=str)),
            "tools": json.loads(json.dumps(tools, ensure_ascii=False, default=str)),
            "config": {key: value for key, value in config.items()
                       if key != "cancel_check"},
        }
        self.calls.append(call)
        if self.delay:
            time.sleep(self.delay)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        response = self.responses[index]
        if callable(response):
            response = response(self.calls)
        base = {
            "content": "",
            "tool_calls": [],
            "finish_reason": "tool_calls" if response.get("tool_calls") else "stop",
            "usage": None,
            "model": "scripted-v1",
            "raw": {},
        }
        base.update(response)
        return base

    @staticmethod
    def tool(name: str, arguments: dict, call_id: str = "call_1") -> dict:
        return {"id": call_id, "name": name, "arguments": arguments}


TEXT_ONLY_CAPS = {"routing": True, "tool_arguments": False, "native_tools": False,
                  "usage": False, "streaming": False}
TEXT_TOOL_CAPS = {"routing": True, "tool_arguments": True, "native_tools": False,
                  "usage": False, "streaming": False}
NATIVE_CAPS = {"routing": True, "tool_arguments": True, "native_tools": True,
               "usage": True, "streaming": False}

TOOL_SCHEMA = [{
    "name": "list_tasks",
    "description": "按项目与状态查询任务",
    "parameters": {
        "type": "object",
        "properties": {"project_id": {"type": "string"},
                       "status": {"type": "string"}},
        "required": ["project_id", "status"],
    },
}]


# --------------------------------------------------------------------------- #
# skill package / case request helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def package(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# 主 Skill\n按条件选择子技能。\n", encoding="utf-8")
    (root / "references" / "task_query.md").write_text(
        "任务查询：查询项目任务。", encoding="utf-8")
    (root / "references" / "task_create.md").write_text(
        "任务创建：创建任务。", encoding="utf-8")
    (root / "notes.txt").write_text("not mapped by the manifest", encoding="utf-8")
    manifest = {
        "version": 1,
        "main": "SKILL.md",
        "subskills": [
            {"skill_id": "task_query", "name": "任务查询",
             "path": "references/task_query.md", "description": "查询任务"},
            {"skill_id": "task_create", "name": "任务创建",
             "path": "references/task_create.md", "description": "创建任务"},
        ],
    }
    return root, manifest


def make_request(root, manifest, *, fixtures=None, limits=None,
                 loader_config=None, adapter_config=None) -> dict:
    return {
        "query": "查询项目 P123 中进行中的任务",
        "context": {"timezone": "Asia/Shanghai"},
        "skill_snapshot_path": str(root),
        "system_prompt": "你是使用 Skill 包完成用户任务的执行 Agent。",
        "model_config": adapter_config or {},
        "request_template": {},
        "tool_schema": TOOL_SCHEMA,
        "fixtures": fixtures if fixtures is not None else [{
            "name": "list_tasks",
            "match": {"project_id": "P123", "status": "in_progress"},
            "response": {"tasks": []},
            "consume": "always",
        }],
        "limits": limits or {"max_model_calls": 6, "max_steps": 6,
                             "case_timeout_s": 5, "max_context_chars": 20000},
        "loader_config": loader_config or {"mode": "on_demand"},
        "run_id": "run_a13",
        "case_id": "case_013",
        "manifest": manifest,
        "allowed_skill_ids": ["task_query", "task_create"],
        "expected": {"action": "tool_call", "routes": [["task_query"]],
                     "answer": ANSWER_SENTINEL},
    }


def finish_response(selected=("task_query",), action="tool_call",
                    missing=None, call_id="call_f", summary="ok") -> dict:
    return {"tool_calls": [{"id": call_id, "name": "finish", "arguments": {
        "action": action, "selected_skills": list(selected),
        "missing_fields": list(missing or []), "summary": summary}}]}


def select_response(*skill_ids, call_ids=None) -> dict:
    calls = []
    for index, skill_id in enumerate(skill_ids):
        call_id = (call_ids or [f"call_sel_{index}"])[index]
        calls.append({"id": call_id, "name": "select_skill",
                      "arguments": {"skill_id": skill_id}})
    return {"tool_calls": calls}


# --------------------------------------------------------------------------- #
# registry / capabilities
# --------------------------------------------------------------------------- #
def test_registry_builds_every_provider():
    assert isinstance(build_adapter({"provider": "openai_http"}), OpenAIHTTPAdapter)
    assert isinstance(build_adapter({"provider": "enterprise_ws"}),
                      EnterpriseWSAdapter)
    demo = build_adapter({"provider": "demo_scripted"})
    assert getattr(demo, "name", "") == "demo_scripted"
    # documented aliases resolve to the canonical adapters
    assert isinstance(build_adapter({"provider": "openai"}), OpenAIHTTPAdapter)
    assert isinstance(build_adapter({"provider": "ws"}), EnterpriseWSAdapter)


def test_registry_unknown_provider_raises_app_error():
    with pytest.raises(AppError) as excinfo:
        build_adapter({"provider": "totally_unknown"})
    assert excinfo.value.code == "unknown_provider"
    assert excinfo.value.status_code == 422
    with pytest.raises(AppError):
        build_adapter({})
    with pytest.raises(AppError):
        build_adapter("not-a-mapping")  # type: ignore[arg-type]


def test_capability_declarations_are_frozen_booleans():
    text_adapter = ScriptedAdapter([], capabilities=TEXT_ONLY_CAPS)
    assert text_adapter.capability_snapshot() == TEXT_ONLY_CAPS
    native = EnterpriseWSAdapter({"ws_url": "ws://127.0.0.1:1", "capabilities": {
        "native_tools": False, "usage": False}})
    caps = native.capability_snapshot()
    assert caps["native_tools"] is False
    assert caps["usage"] is False
    assert caps["routing"] is True  # unspecified keys keep adapter defaults


def test_openai_http_error_classification():
    auth = _error_from_status(401, "no")
    assert auth.code == "auth_failed" and auth.retryable is False
    rate = _error_from_status(429, "slow down")
    assert rate.code == "rate_limited" and rate.retryable is True
    server = _error_from_status(503, "oops")
    assert server.code == "platform_error" and server.retryable is True


# --------------------------------------------------------------------------- #
# A13 core: routing-only adapter => tool argument metric not evaluated
# --------------------------------------------------------------------------- #
def test_router_only_text_adapter_reports_tool_arguments_unevaluated(package):
    root, manifest = package
    adapter = ScriptedAdapter(
        [{"content": "依据：用户查询任务\n子技能：task_query"}],
        capabilities=TEXT_ONLY_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"] is None
    assert result["selected_skills"] == ["task_query"]
    assert result["tool_calls"] == []  # nothing invented
    assert result["result_source"] == "text_parse"
    assert result["capabilities"]["tool_arguments"] is False
    assert result["route_events"][0]["skill_id"] == "task_query"

    from app.evaluation import score_case

    score = score_case(
        {"action": "tool_call", "routes": [["task_query"]],
         "tool_calls": [{"name": "list_tasks",
                         "arguments": {"project_id": "P123",
                                       "status": "in_progress"}}]},
        result, TOOL_SCHEMA, None)
    assert score["routing"]["evaluable"] is True
    assert score["routing"]["passed"] is True
    assert score["tool_args"]["evaluable"] is False
    assert score["tool_args"]["passed"] is False


def test_text_tool_protocol_produces_pure_text_parse_result(package):
    root, manifest = package
    content = (
        "依据：查询项目任务\n"
        "子技能：task_query\n"
        "工具：list_tasks {\"project_id\":\"P123\",\"status\":\"in_progress\"}\n"
        "结束：{\"action\":\"tool_call\",\"selected_skills\":[\"task_query\"],"
        "\"missing_fields\":[],\"summary\":\"已查询\"}"
    )
    adapter = ScriptedAdapter([{"content": content}], capabilities=TEXT_TOOL_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"] is None, result["error"]
    assert result["result_source"] == "text_parse"
    assert result["selected_skills"] == ["task_query"]
    assert result["tool_calls"] == [{"name": "list_tasks", "arguments": {
        "project_id": "P123", "status": "in_progress"}}]
    assert result["action"] == "tool_call"
    # text protocol receives no native tools
    assert adapter.calls[0]["tools"] == []


@pytest.mark.parametrize("content, reason", [
    ("子技能：task_query", "missing basis line"),
    ("依据：x", "missing skill line"),
    ("依据：x\n子技能：task_query\n依据：y", "ambiguous duplicate lines"),
    ("依据：x\n子技能：unknown_skill", "unknown skill name"),
    ("依据：x\n子技能：task_query\n工具：list_tasks {\"project_id\":", "truncated tool json"),
    ("依据：x\n子技能：task_query\n额外说明：自由发挥", "unrecognised line"),
])
def test_text_protocol_parse_failures_are_capability_errors(package, content, reason):
    root, manifest = package
    adapter = ScriptedAdapter([{"content": content}], capabilities=TEXT_TOOL_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"]["code"] == "parse_failed", reason
    assert result["error_kind"] == "capability"
    assert len(adapter.calls) == 1


def test_text_parsers_are_pure_and_strict():
    assert parse_routing("依据：b\n子技能：task_query, task_create")["skills"] == [
        "task_query", "task_create"]
    with pytest.raises(TextParseError):
        parse_routing("依据：只有依据")
    with pytest.raises(TextParseError):
        parse_tool_calls("工具：list_tasks {\"a\": 1}", known_tool_names=["other"])
    with pytest.raises(TextParseError):
        parse_tool_calls("工具：list_tasks {\"a\":")
    assert parse_tool_calls("工具：list_tasks {}") == [
        {"name": "list_tasks", "arguments": {}}]


# --------------------------------------------------------------------------- #
# native adapter + internal tools
# --------------------------------------------------------------------------- #
def test_native_adapter_internal_tools_never_count_as_business_calls(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        {"tool_calls": [
            ScriptedAdapter.tool("select_skill", {"skill_id": "task_query"}, "c1"),
            ScriptedAdapter.tool("read_skill",
                                 {"path": "references/task_query.md"}, "c2"),
        ]},
        {"tool_calls": [ScriptedAdapter.tool(
            "list_tasks", {"project_id": "P123", "status": "in_progress"}, "c3")]},
        finish_response(),
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"] is None, result["error"]
    assert result["result_source"] == "native_tool"
    assert result["tool_calls"] == [{"name": "list_tasks", "arguments": {
        "project_id": "P123", "status": "in_progress"}}]
    assert "select_skill" not in json.dumps(result["tool_calls"])
    assert "read_skill" not in json.dumps(result["tool_calls"])
    event_types_seen = [event["type"] for event in result["events"]]
    assert "skill_selected" in event_types_seen
    assert "skill_read" in event_types_seen
    # read_skill tool message uses the frozen marker
    second_call_messages = adapter.calls[1]["messages"]
    assert any(message.get("role") == "tool"
               and "【文件 references/task_query.md】" in message.get("content", "")
               for message in second_call_messages)


def test_native_business_result_can_end_without_finish_in_relaxed_mode(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        {"tool_calls": [ScriptedAdapter.tool("select_skill", {"skill_id": "task_query"}, "s1")]},
        {"tool_calls": [ScriptedAdapter.tool("list_tasks", {"project_id": "P123", "status": "in_progress"}, "b1")]},
        {"content": "已完成查询"},
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"] is None
    assert result["action"] == "tool_call"
    assert result["finish_protocol"] == "missing"
    assert result["tool_calls"][0]["name"] == "list_tasks"


def test_native_missing_finish_can_be_strictly_rejected(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        {"tool_calls": [ScriptedAdapter.tool("list_tasks", {"project_id": "P123", "status": "in_progress"}, "b1")]},
        {"content": "已完成查询"},
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest,
                                   loader_config={"mode": "on_demand", "strict_finish": True}), adapter)
    assert result["error"]["code"] == "protocol_error"


def test_finish_summary_must_match_observed_selection_events(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        select_response("task_query"),
        # claims task_create although only task_query was selected
        finish_response(selected=["task_create"]),
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"]["code"] == "protocol_error"
    assert result["error_kind"] == "capability"


def test_unknown_native_tool_is_protocol_error(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        {"tool_calls": [ScriptedAdapter.tool("drop_database", {}, "c1")]},
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"]["code"] == "protocol_error"
    assert len(adapter.calls) == 1


def test_native_model_stopping_without_finish_is_undetermined_in_relaxed_mode(package):
    root, manifest = package
    adapter = ScriptedAdapter([{"content": "我已经回答完了", "finish_reason": "stop"}],
                              capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"] is None
    assert result["action"] == "undetermined"
    assert result["finish_protocol"] == "missing_undetermined"


def test_truncated_and_refused_finish_reasons_are_capability_failures(package):
    root, manifest = package
    truncated = ScriptedAdapter(
        [{"content": "部分", "finish_reason": "length"}], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), truncated)
    assert result["error"]["code"] == "truncated"
    assert result["error_kind"] == "capability"

    refused = ScriptedAdapter(
        [{"content": "无法回答", "finish_reason": "content_filter"}],
        capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), refused)
    assert result["error"]["code"] == "refused"
    assert result["error_kind"] == "capability"


# --------------------------------------------------------------------------- #
# internal observation tools (direct)
# --------------------------------------------------------------------------- #
def test_obs_tools_reject_path_traversal_and_unmapped_files(package):
    root, manifest = package
    runtime = ObsToolRuntime(skill_snapshot_path=root, manifest=manifest,
                             allowed_skill_ids=["task_query", "task_create"])
    with pytest.raises(ObsToolError) as excinfo:
        runtime.read_skill({"path": "../outside.md"})
    assert excinfo.value.code == "protocol_error"
    with pytest.raises(ObsToolError):
        runtime.read_skill({"path": "/etc/passwd"})
    with pytest.raises(ObsToolError):
        runtime.read_skill({"path": "notes.txt"})  # exists but not mapped
    with pytest.raises(ObsToolError):
        runtime.select_skill({"skill_id": "unknown"})

    result = runtime.read_skill({"path": "references/task_query.md"})
    assert "任务查询" in result["content"]
    # reading does not select a skill
    assert runtime.selected_skills == []
    assert [event["type"] for event in runtime.events] == [
        event_types.SKILL_READ]

    selection = runtime.select_skill({"skill_id": "task_query"})
    assert selection["path"] == "references/task_query.md"
    assert runtime.selected_skills == ["task_query"]


def test_obs_tools_finish_consistency_and_single_finish(package):
    root, manifest = package
    runtime = ObsToolRuntime(skill_snapshot_path=root, manifest=manifest)
    runtime.select_skill({"skill_id": "task_query"})
    with pytest.raises(ObsToolError) as excinfo:
        runtime.finish({"action": "tool_call", "selected_skills": ["task_create"]})
    assert excinfo.value.code == "protocol_error"
    runtime.finish({"action": "tool_call",
                    "selected_skills": ["task_query"],
                    "missing_fields": [], "summary": "ok"})
    with pytest.raises(ObsToolError):
        runtime.finish({"action": "tool_call", "selected_skills": ["task_query"]})
    with pytest.raises(ObsToolError):
        runtime.finish({"action": "invalid", "selected_skills": []})


# --------------------------------------------------------------------------- #
# fixture simulation
# --------------------------------------------------------------------------- #
def test_fixtures_once_and_always_consumption(package):
    root, manifest = package
    fixtures = [
        {"name": "lookup", "match": {"q": "a"}, "response": {"n": 1},
         "consume": "once"},
        {"name": "lookup", "match": {}, "response": {"n": 2}, "consume": "always"},
    ]
    tool_schema = [{"name": "lookup", "parameters": {"type": "object"}}]
    responses = [
        {"tool_calls": [ScriptedAdapter.tool("lookup", {"q": "a"}, "c1")]},
        {"tool_calls": [ScriptedAdapter.tool("lookup", {"q": "a"}, "c2")]},
        finish_response(selected=[]),
    ]
    adapter = ScriptedAdapter(responses, capabilities=NATIVE_CAPS)
    request = make_request(root, manifest, fixtures=fixtures)
    request["tool_schema"] = tool_schema
    result = run_case(request, adapter)
    assert result["error"] is None, result["error"]
    assert len(result["tool_calls"]) == 2
    assert result["fixture_misses"] == []
    responses_in_events = [
        event["payload"].get("response") for event in result["events"]
        if event["type"] == "tool_result"]
    assert responses_in_events == [{"n": 1}, {"n": 2}]


def test_fixture_miss_fails_immediately_without_real_service(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        {"tool_calls": [ScriptedAdapter.tool(
            "list_tasks", {"project_id": "OTHER", "status": "done"}, "c1")]},
        finish_response(),  # must never be reached
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"]["code"] == "fixture_miss"
    assert result["error_kind"] == "capability"
    assert len(adapter.calls) == 1
    assert result["fixture_misses"] == [{
        "tool": "list_tasks",
        "arguments": {"project_id": "OTHER", "status": "done"}}]
    miss_events = [event for event in result["events"]
                   if event["type"] == event_types.FIXTURE_MISS]
    assert miss_events and miss_events[0]["level"] == "error"
    assert miss_events[0]["payload"]["issue"]["error_code"] == "fixture_miss"


# --------------------------------------------------------------------------- #
# limits / cancellation / timeout
# --------------------------------------------------------------------------- #
def test_max_model_calls_limit(package):
    root, manifest = package
    adapter = ScriptedAdapter([select_response("task_query")],
                              capabilities=NATIVE_CAPS)
    request = make_request(root, manifest)
    request["limits"] = {"max_model_calls": 3, "max_steps": 100,
                         "case_timeout_s": 5, "max_context_chars": 20000}
    result = run_case(request, adapter)
    assert result["error"]["code"] == "steps_exhausted"
    assert result["error_kind"] == "capability"
    assert result["model_calls"] == 3
    assert len(adapter.calls) == 3


def test_max_steps_limit(package):
    root, manifest = package
    business = {"tool_calls": [ScriptedAdapter.tool(
        "list_tasks", {"project_id": "P123", "status": "in_progress"},
        "calls")]}
    adapter = ScriptedAdapter([business, business, business],
                              capabilities=NATIVE_CAPS)
    request = make_request(root, manifest)
    request["limits"] = {"max_model_calls": 10, "max_steps": 2,
                         "case_timeout_s": 5, "max_context_chars": 20000}
    result = run_case(request, adapter)
    assert result["error"]["code"] == "steps_exhausted"
    assert len(result["tool_calls"]) == 2


def test_case_timeout_is_infrastructure_error(package):
    root, manifest = package
    adapter = ScriptedAdapter([finish_response()], capabilities=NATIVE_CAPS,
                              delay=0.2)
    request = make_request(root, manifest)
    request["limits"] = {"max_model_calls": 3, "max_steps": 3,
                         "case_timeout_s": 0.05, "max_context_chars": 20000}
    result = run_case(request, adapter)
    assert result["error"]["code"] == "timeout"
    assert result["error_kind"] == "infrastructure"


def test_cancel_before_start_and_between_calls(package):
    root, manifest = package
    adapter = ScriptedAdapter([finish_response()], capabilities=NATIVE_CAPS)
    request = make_request(root, manifest)
    request["cancel_check"] = lambda: True
    result = run_case(request, adapter)
    assert result["error"]["code"] == "cancelled"
    assert result["error_kind"] == "cancelled"
    assert adapter.calls == []

    adapter = ScriptedAdapter([select_response("task_query")],
                              capabilities=NATIVE_CAPS)
    request = make_request(root, manifest)
    request["cancel_check"] = lambda: len(adapter.calls) >= 1
    result = run_case(request, adapter)
    assert result["error"]["code"] == "cancelled"
    assert len(adapter.calls) == 1


# --------------------------------------------------------------------------- #
# expected never reaches the adapter
# --------------------------------------------------------------------------- #
def test_expected_and_answer_never_reach_adapter(package):
    root, manifest = package
    adapter = ScriptedAdapter([finish_response(selected=[])],
                              capabilities=NATIVE_CAPS)
    request = make_request(root, manifest)
    request["request_template"] = {"expected": {"answer": ANSWER_SENTINEL},
                                   "user": "tester"}
    result = run_case(request, adapter)
    assert result["error"] is None, result["error"]
    serialized = json.dumps(adapter.calls, ensure_ascii=False, default=str)
    assert ANSWER_SENTINEL not in serialized
    assert '"expected"' not in serialized
    assert adapter.calls[0]["config"].get("request_template") == {"user": "tester"}


# --------------------------------------------------------------------------- #
# standard Chat Completions HTTP adapter (local fake server, no external net)
# --------------------------------------------------------------------------- #
class _HTTPHandler(BaseHTTPRequestHandler):
    server: "_FakeHTTP"

    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        record = {
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": json.loads(raw or b"{}"),
        }
        self.server.requests.append(record)
        index = min(len(self.server.requests) - 1, len(self.server.script) - 1)
        status, payload = self.server.script[index]
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output quiet
        return


class _FakeHTTP:
    def __init__(self, script):
        self.script = script
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def __enter__(self) -> "_FakeHTTP":
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _HTTPHandler)
        self._server.script = self.script
        self._server.requests = self.requests
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(5)


def _http_adapter(url: str, **overrides) -> OpenAIHTTPAdapter:
    config = {"provider": "openai_http", "base_url": url, "model": "fake-http",
              "api_key": "sk-test-secret", "timeout_s": 5,
              "retry_backoff_s": 0.0, "max_retries": 2}
    config.update(overrides)
    return OpenAIHTTPAdapter(config)


def _http_ok_payload() -> dict:
    return {
        "model": "fake-http",
        "choices": [{
            "message": {
                "content": "查询完成",
                "tool_calls": [{
                    "id": "call_http",
                    "type": "function",
                    "function": {"name": "list_tasks",
                                 "arguments": '{"project_id":"P123"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


def test_openai_http_parses_tool_calls_and_keeps_credentials_out_of_events():
    events: list[dict] = []
    with _FakeHTTP([(200, _http_ok_payload())]) as server:
        adapter = _http_adapter(server.url)
        result = adapter.invoke(
            [{"role": "user", "content": "查询任务"}], {}, [], emit=events.append)
        request = server.requests[0]
    assert result["content"] == "查询完成"
    assert result["tool_calls"] == [{"id": "call_http", "name": "list_tasks",
                                     "arguments": {"project_id": "P123"}}]
    assert result["finish_reason"] == "tool_calls"
    assert result["usage"] == {"input_tokens": 5, "output_tokens": 2,
                               "total_tokens": 7}
    assert request["body"]["model"] == "fake-http"
    assert request["body"]["messages"] == [{"role": "user", "content": "查询任务"}]
    assert request["headers"]["authorization"] == "Bearer sk-test-secret"
    assert "sk-test-secret" not in json.dumps(events, ensure_ascii=False)
    assert "sk-test-secret" not in json.dumps(result, ensure_ascii=False)


def test_openai_http_auth_failure_is_not_retried():
    with _FakeHTTP([(401, {"error": {"message": "bad key"}}),
                    (200, _http_ok_payload())]) as server:
        adapter = _http_adapter(server.url)
        with pytest.raises(AdapterError) as excinfo:
            adapter.invoke([{"role": "user", "content": "q"}], {}, [])
        assert len(server.requests) == 1
    assert excinfo.value.code == "auth_failed"
    assert excinfo.value.error_kind == "infrastructure"


def test_openai_http_rate_limit_retries_then_succeeds():
    events: list[dict] = []
    with _FakeHTTP([(429, {"error": {"message": "slow down"}}),
                    (200, _http_ok_payload())]) as server:
        adapter = _http_adapter(server.url)
        result = adapter.invoke([{"role": "user", "content": "q"}], {},
                                [], emit=events.append)
        assert len(server.requests) == 2
    assert result["content"] == "查询完成"
    retries = [event for event in events
               if event["type"] == "model_call_failed"
               and event["payload"].get("retrying")]
    assert len(retries) == 1
    assert retries[0]["payload"]["error"]["code"] == "rate_limited"


def test_keyword_style_emit_sink_is_supported(package):
    """The API debug endpoint uses def emit(event_type, payload=None, **fields)."""
    root, manifest = package
    adapter = ScriptedAdapter([finish_response(selected=[])])
    recorded: list[dict] = []

    def sink(event_type, payload=None, **fields):
        recorded.append({"type": event_type, "payload": payload or {}, **fields})

    result = run_case(make_request(root, manifest), adapter, sink)
    assert result["error"] is None, result["error"]
    types = [event["type"] for event in recorded]
    assert "model_call_started" in types
    assert "model_call_finished" in types
    assert all(event.get("case_id") == "case_013" for event in recorded)
