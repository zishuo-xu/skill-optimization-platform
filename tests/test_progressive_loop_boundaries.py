"""Observable read-before-act behavior and growing transcript boundaries."""

import itertools
import json

import pytest

from app.execution import build_messages, run_case
from tests.test_a13_capabilities import (
    NATIVE_CAPS, TEXT_TOOL_CAPS, ScriptedAdapter, finish_response,
    make_request, package, select_response,
)

ARGS = {"project_id": "P123", "status": "in_progress"}


def business(call_id="business", arguments=None):
    return ScriptedAdapter.tool("call_tool", {
        "name": "list_tasks", "arguments": arguments or ARGS,
    }, call_id)


def once_fixture(response=None, match=None):
    return [{"name": "list_tasks", "match": ARGS if match is None else match,
             "response": response or {"tasks": []}, "consume": "once"}]


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
@pytest.mark.parametrize("loader_name", ["select_skill", "read_skill"])
def test_every_batch_order_defers_actions_and_preserves_tool_pairing(package, order, loader_name):
    root, manifest = package
    loader_args = ({"skill_id": "task_query"} if loader_name == "select_skill"
                   else {"path": "references/task_query.md"})
    selected = ("task_query",) if loader_name == "select_skill" else ()
    batch = [ScriptedAdapter.tool(loader_name, loader_args, "load"), business(),
             finish_response(selected=selected)["tool_calls"][0]]

    def after_load(calls):
        messages = calls[-1]["messages"]
        replies = [m for m in messages if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in replies] == [batch[i]["id"] for i in order]
        assert sum("任务查询：查询项目任务。" in m["content"] for m in replies) == 1
        for reply in replies:
            if reply["tool_call_id"] != "load":
                assert json.loads(reply["content"])["error"] == "skill_loaded_requires_new_model_turn"
        return {"tool_calls": [business("retry")]}

    adapter = ScriptedAdapter([
        {"tool_calls": [batch[i] for i in order]}, after_load,
        finish_response(selected=selected),
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest, fixtures=once_fixture()), adapter)
    assert result["error"] is None
    assert result["tool_calls"] == [{"name": "list_tasks", "arguments": ARGS}]
    assert len(adapter.calls) == 3


@pytest.mark.parametrize("preloaded", [False, True])
def test_already_visible_skill_does_not_defer_business_or_finish(package, preloaded):
    root, manifest = package
    batch = {"tool_calls": [
        ScriptedAdapter.tool("select_skill", {"skill_id": "task_query"}, "load"),
        business(), finish_response()["tool_calls"][0],
    ]}
    adapter = ScriptedAdapter(([select_response("task_query")] if not preloaded else []) + [batch],
                              capabilities=NATIVE_CAPS)
    request = make_request(root, manifest, fixtures=once_fixture(), loader_config={
        "mode": "preload_all" if preloaded else "on_demand",
    })
    result = run_case(request, adapter)
    assert result["error"] is None
    assert len(result["tool_calls"]) == 1
    assert len(adapter.calls) == (1 if preloaded else 2)


@pytest.mark.parametrize("preloaded", [False, True])
def test_text_loading_is_a_valid_round_and_consumes_fixture_only_once(package, preloaded):
    root, manifest = package
    content = ('依据：查询任务\n子技能：task_query\n工具：list_tasks ' + json.dumps(ARGS) +
               '\n结束：{"action":"tool_call","selected_skills":["task_query"]}')

    def next_turn(calls):
        messages = calls[-1]["messages"]
        assert all(m["role"] != "tool" for m in messages)
        assert "本轮工具调用和结束声明未执行" in messages[-1]["content"]
        assert "任务查询：查询项目任务。" in messages[-1]["content"]
        return {"content": content}

    adapter = ScriptedAdapter([{"content": content}, next_turn], capabilities=TEXT_TOOL_CAPS)
    result = run_case(make_request(root, manifest, fixtures=once_fixture(), loader_config={
        "mode": "preload_all" if preloaded else "on_demand",
    }), adapter)
    assert result["error"] is None
    assert len(result["tool_calls"]) == 1
    assert len(adapter.calls) == (1 if preloaded else 2)


@pytest.mark.parametrize("growth", ["fixture", "arguments", "skill"])
def test_large_runtime_content_stops_before_next_request_without_losing_evidence(package, growth):
    root, manifest = package
    large = "RUNTIME_PAYLOAD_" * 1000
    if growth == "skill":
        (root / "references/task_query.md").write_text(large, encoding="utf-8")
    request = make_request(root, manifest, fixtures=once_fixture(
        response={"data": large} if growth == "fixture" else None,
        match={} if growth == "arguments" else None,
    ))
    request["limits"]["max_context_chars"] = sum(len(m["content"]) for m in build_messages(request)) + 2000
    response = (select_response("task_query") if growth == "skill" else
                {"tool_calls": [business(arguments={"payload": large} if growth == "arguments" else ARGS)]})
    adapter = ScriptedAdapter([response, finish_response()], capabilities=NATIVE_CAPS)
    result = run_case(request, adapter)
    assert result["error"]["code"] == "context_overflow"
    assert result["error"]["details"]["actual_chars"] > request["limits"]["max_context_chars"]
    assert len(adapter.calls) == 1
    assert len(result["tool_calls"]) == (0 if growth == "skill" else 1)
    assert len([e for e in result["events"] if e["type"] == "model_call_started"]) == 1
