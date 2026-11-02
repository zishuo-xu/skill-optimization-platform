"""Route batch observation, the "routes as steps" warning and fixture capture."""

from __future__ import annotations

import pytest

from app import dataset, evaluation
from app.execution import run_case
from app.fixtures import FixtureRuntime, capture_error, parse_path
from tests.test_a13_capabilities import (
    NATIVE_CAPS, ScriptedAdapter, finish_response, make_request, package, select_response,
)

MANIFEST = {"skills": [{"skill_id": "image_adjust", "path": "references/adjust.md"},
                       {"skill_id": "image_filter", "path": "references/filter.md"}]}


def _business(name, arguments, call_id):
    return ScriptedAdapter.tool("call_tool", {"name": name, "arguments": arguments}, call_id)


# --------------------------------------------------------------------------- #
# ② batches: observed, not scored
# --------------------------------------------------------------------------- #
def test_route_events_carry_the_model_turn_as_batch(package):
    root, manifest = package
    one_batch = run_case(make_request(root, manifest), ScriptedAdapter([
        select_response("task_query", "task_create", call_ids=["s1", "s2"]),
        finish_response(selected=("task_query", "task_create"), action="no_action"),
    ], capabilities=NATIVE_CAPS))
    two_batches = run_case(make_request(root, manifest), ScriptedAdapter([
        select_response("task_query"),
        select_response("task_create"),
        finish_response(selected=("task_query", "task_create"), action="no_action"),
    ], capabilities=NATIVE_CAPS))
    assert evaluation.route_batches(one_batch) == [["task_query", "task_create"]]
    assert evaluation.route_batches(two_batches) == [["task_query"], ["task_create"]]

    # Scoring compares the flattened route: both shapes satisfy one route [A, B].
    expected = {"action": "no_action", "routes": [["task_query", "task_create"]], "tool_calls": []}
    for actual in (one_batch, two_batches):
        assert evaluation.score_case(expected, actual)["action_ok"]
        assert evaluation._actual_route(actual) == ["task_query", "task_create"]


def test_route_batches_without_batch_numbers_is_one_batch():
    actual = {"route_events": [{"skill_id": "a", "seq": 1}, {"skill_id": "b", "seq": 2}]}
    assert evaluation.route_batches(actual) == [["a", "b"]]


def _prepare(case):
    return dataset.validate_and_prepare([case], manifest=MANIFEST)


def test_dataset_warns_when_alternative_routes_look_like_steps():
    case = {"case_id": "multi", "query": "把曝光调到最低，再加个经典滤镜",
            "expected": {"action": "tool_call", "routes": [["image_adjust"], ["image_filter"]],
                         "tool_calls": [{"name": "edit", "arguments": {"op": "adjust"}},
                                        {"name": "edit", "arguments": {"op": "filter"}}]}}
    result = _prepare(case)
    assert not result["errors"]
    warnings = [item for item in result["warnings"] if item["code"] == "routes_look_like_steps"]
    assert len(warnings) == 1
    assert '[["image_adjust", "image_filter"]]' in warnings[0]["message"]

    fixed = dict(case, expected=dict(case["expected"], routes=[["image_adjust", "image_filter"]]))
    assert not [item for item in _prepare(fixed)["warnings"] if item["code"] == "routes_look_like_steps"]
    # Genuine alternatives with a single call stay silent.
    single = dict(case, expected=dict(case["expected"], tool_calls=case["expected"]["tool_calls"][:1]))
    assert not [item for item in _prepare(single)["warnings"] if item["code"] == "routes_look_like_steps"]


# --------------------------------------------------------------------------- #
# ③ fixture capture: write-then-read chains
# --------------------------------------------------------------------------- #
CHAIN = [
    {"name": "memory", "match": {"method": "add"},
     "capture": [{"var": "note", "path": "args.content"}],
     "response": {"code": 0, "memoryId": "mem_7f3a"}},
    {"name": "memory", "match": {"method": "search"},
     "response": {"code": 0, "memoryList": [{"memoryId": "mem_7f3a", "content": "${note}"}]}},
    {"name": "memory", "match": {"method": "delete", "args": {"content": "${note}"}},
     "response": {"code": 0}},
]


def test_capture_feeds_later_responses_and_matches():
    runtime = FixtureRuntime(CHAIN)
    # Reading before writing: the read depends on an uncaptured value -> miss.
    assert runtime.resolve("memory", {"method": "search"}) is None
    assert runtime.diagnose_miss("memory", {"method": "search"})["reason"] == "awaiting_capture"

    hit = runtime.resolve("memory", {"method": "add", "args": {"content": "喜欢冷色调"}})
    assert hit["captured"] == {"note": "喜欢冷色调"}
    found = runtime.resolve("memory", {"method": "search"})
    assert found["response"]["memoryList"][0]["content"] == "喜欢冷色调"
    assert runtime.resolve("memory", {"method": "delete", "args": {"content": "别的"}}) is None
    assert runtime.resolve("memory", {"method": "delete", "args": {"content": "喜欢冷色调"}})

    runtime.reset()
    assert runtime.state == {}


def test_fixtures_without_capture_are_unchanged():
    plain = [{"name": "t", "match": {"q": "a"}, "response": {"text": "${not_a_var"}}]
    runtime = FixtureRuntime(plain)
    assert runtime.resolve("t", {"q": "a"})["response"] == {"text": "${not_a_var"}
    assert "captured" not in runtime.resolve("t", {"q": "a"})


@pytest.mark.parametrize("capture,message", [
    ("x", "数组"),
    ([{"var": "1bad", "path": "a"}], "变量名"),
    ([{"var": "ok", "from": "response", "path": "a"}], "只支持 arguments"),
    ([{"var": "ok", "path": "a..b"}], "参数路径"),
])
def test_capture_validation(capture, message):
    assert message in capture_error(capture)
    case = {"case_id": "c", "query": "q",
            "expected": {"action": "no_action", "routes": [], "tool_calls": []},
            "fixtures": [{"name": "t", "capture": capture, "response": {}}]}
    result = _prepare(case)
    assert [error["code"] for error in result["errors"]] == ["invalid_fixture_capture"]


def test_capture_survives_dataset_preparation():
    case = {"case_id": "c", "query": "记住我喜欢冷色调",
            "expected": {"action": "tool_call", "routes": [["image_adjust"]],
                         "tool_calls": [{"name": "memory", "arguments": {"method": "add"}}]},
            "fixtures": CHAIN}
    prepared = _prepare(case)["cases"][0]
    assert prepared["fixtures"][0]["capture"] == [
        {"var": "note", "from": "arguments", "path": "args.content"}]
    assert parse_path("$.items[0].id") == ["items", 0, "id"]


def test_execution_uses_captured_value_in_the_returned_tool_message(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        select_response("task_query"),
        {"tool_calls": [_business("memory", {"method": "add", "args": {"content": "冷色调"}}, "b1")]},
        {"tool_calls": [_business("memory", {"method": "search"}, "b2")]},
        finish_response(selected=("task_query",)),
    ], capabilities=NATIVE_CAPS)
    actual = run_case(make_request(root, manifest, fixtures=CHAIN), adapter)
    assert actual["error"] is None
    results = actual["tool_results"]
    assert results[1]["response"]["memoryList"][0]["content"] == "冷色调"

    skipped = ScriptedAdapter([
        select_response("task_query"),
        {"tool_calls": [_business("memory", {"method": "search"}, "b1")]},
        finish_response(selected=("task_query",)),
    ], capabilities=NATIVE_CAPS)
    actual = run_case(make_request(root, manifest, fixtures=CHAIN), skipped)
    assert actual["error"]["code"] == "fixture_miss"
    assert "前序" in actual["error"]["message"]
