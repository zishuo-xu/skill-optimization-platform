"""Regression tests for loading a selected skill in the same tool turn."""

from __future__ import annotations

import os

import pytest

from app import evaluation, execution, hashing, services
from app.obs_tools import ObsToolError, ObsToolRuntime

from tests.test_a13_capabilities import (
    NATIVE_CAPS,
    ScriptedAdapter,
    TOOL_SCHEMA,
    finish_response,
    make_request,
    package,
    select_response,
)


def test_select_skill_returns_content_and_records_one_read_event(package):
    root, manifest = package
    events = []
    runtime = ObsToolRuntime(skill_snapshot_path=root, manifest=manifest,
                             allowed_skill_ids=["task_query"], emit=events.append)

    result = runtime.handle("select_skill", {"skill_id": "task_query"}, call_id="s1")

    assert result["skill_id"] == "task_query"
    assert result["path"] == "references/task_query.md"
    assert result["content"] == "任务查询：查询项目任务。"
    assert result["chars"] == len(result["content"])
    assert [event["type"] for event in events] == ["skill_selected", "skill_read"]
    assert events[-1]["payload"].get("source") == "select_skill"


def test_repeated_read_uses_cached_content_but_keeps_audit_event(package):
    root, manifest = package
    events = []
    runtime = ObsToolRuntime(skill_snapshot_path=root, manifest=manifest,
                             allowed_skill_ids=["task_query"], emit=events.append)
    first = runtime.handle("select_skill", {"skill_id": "task_query"})
    second = runtime.handle("read_skill", {"path": first["path"]})

    assert second["content"] == first["content"]
    reads = [event for event in events if event["type"] == "skill_read"]
    assert len(reads) == 2
    assert reads[-1]["payload"].get("cached") is True


def test_select_and_read_same_batch_do_not_duplicate_body(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        {"tool_calls": [
            ScriptedAdapter.tool("select_skill", {"skill_id": "task_query"}, "s1"),
            ScriptedAdapter.tool("read_skill", {"path": "references/task_query.md"}, "r1"),
        ]},
        {"tool_calls": [ScriptedAdapter.tool(
            "list_tasks", {"project_id": "P123", "status": "in_progress"}, "b1")]},
        finish_response(),
    ], capabilities=NATIVE_CAPS)

    result = execution.run_case(make_request(root, manifest), adapter)
    assert result["error"] is None, result["error"]
    assert len(adapter.calls) == 3
    second_messages = adapter.calls[1]["messages"]
    skill_messages = [message for message in second_messages
                      if message.get("role") == "tool"
                      and "【文件 references/task_query.md】" in message.get("content", "")]
    assert len(skill_messages) == 1
    assert sum("任务查询：查询项目任务。" in message.get("content", "")
               for message in skill_messages) == 1
    cached_message = next(m for m in second_messages if m.get("name") == "read_skill")
    assert "cached" in cached_message["content"]
    assert "【文件" not in cached_message["content"]


def test_adaptive_model_skips_read_roundtrip_and_scores_same(package):
    root, manifest = package

    class Adaptive(ScriptedAdapter):
        def invoke(self, messages, config=None, tools=None, *, emit=None):
            self.calls.append({"messages": messages, "tools": tools or [],
                               "config": {}})
            tool_messages = [message for message in messages if message.get("role") == "tool"]
            if not tool_messages:
                return {"tool_calls": [self.tool("select_skill", {"skill_id": "task_query"})]}
            if any("【文件 references/task_query.md】" in message.get("content", "")
                   for message in tool_messages):
                if not any(message.get("name") == "list_tasks" for message in tool_messages):
                    return {"tool_calls": [self.tool("list_tasks", {
                        "project_id": "P123", "status": "in_progress"})]}
                return finish_response()
            return {"tool_calls": [self.tool("read_skill", {"path": "references/task_query.md"})]}

    adapter = Adaptive([], capabilities=NATIVE_CAPS)
    result = execution.run_case(make_request(root, manifest), adapter)
    assert result["error"] is None, result["error"]
    assert result["tool_calls"][0]["name"] == "list_tasks"
    assert len(adapter.calls) == 3


def test_security_checks_remain_in_force(package, tmp_path):
    root, manifest = package
    runtime = ObsToolRuntime(skill_snapshot_path=root, manifest=manifest,
                             allowed_skill_ids=["task_query"], max_read_chars=3)
    with pytest.raises(ObsToolError):
        runtime.handle("select_skill", {"skill_id": "task_query"})

    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(ObsToolError):
        runtime.handle("read_skill", {"path": "../outside.md"})

    manifest = dict(manifest, subskills=list(manifest["subskills"]) + [
        {"skill_id": "bad_utf8", "path": "references/bad.bin"},
        {"skill_id": "linked", "path": "references/linked.md"},
    ])
    (root / "references/bad.bin").write_bytes(b"\xff\xfe")
    os.symlink(str(outside), str(root / "references/linked.md"))
    guarded = ObsToolRuntime(skill_snapshot_path=root, manifest=manifest,
                             allowed_skill_ids=["bad_utf8", "linked"])
    with pytest.raises(ObsToolError):
        guarded.handle("select_skill", {"skill_id": "bad_utf8"})
    with pytest.raises(ObsToolError):
        guarded.handle("select_skill", {"skill_id": "linked"})
    with pytest.raises(ObsToolError):
        guarded.handle("select_skill", {"skill_id": "missing"})


@pytest.mark.parametrize("loader,expected_cached", [
    ({"mode": "preload_all"}, True),
    ({"mode": "on_demand", "preload_skill_ids": ["task_query"]}, True),
    ({"mode": "on_demand", "preload_skill_ids": ["task_create"]}, False),
    ({"mode": "preload_all", "overflow_strategy": "index_only"}, False),
])
def test_actual_preloading_and_overflow_do_not_duplicate_or_lose_body(package, loader, expected_cached):
    root, manifest = package
    body = "SELECTED_BODY_" * 200
    (root / "references/task_query.md").write_text(body)
    request = make_request(root, manifest, loader_config=loader)
    if "overflow_strategy" in loader:
        baseline = execution.build_messages(make_request(root, manifest))
        request["limits"]["max_context_chars"] = sum(len(m["content"]) for m in baseline) + 50
        request["limits"]["context_overflow"] = "frozen_strategy"
    def after_select(calls):
        content = "\n".join(m.get("content", "") for m in calls[-1]["messages"])
        assert content.count(body) == 1
        return finish_response(action="no_action")
    adapter = ScriptedAdapter([select_response("task_query"), after_select], capabilities=NATIVE_CAPS)
    actual = execution.run_case(request, adapter)
    assert actual["error"] is None, actual["error"]
    reads = [e for e in actual["events"] if e["type"] == "skill_read"]
    assert reads[0]["payload"]["cached"] is expected_cached


def test_prompt_marker_does_not_falsely_mark_skill_loaded(package):
    root, manifest = package
    request = make_request(root, manifest)
    request["system_prompt"] = "示例标记：【子技能内容 references/task_query.md】（此处没有正文）"
    adapter = ScriptedAdapter([select_response("task_query"), finish_response()], capabilities=NATIVE_CAPS)
    actual = execution.run_case(request, adapter)
    assert actual["error"] is None
    assert any("任务查询：查询项目任务。" in m.get("content", "") for m in adapter.calls[1]["messages"])
    assert next(e for e in actual["events"] if e["type"] == "skill_read")["payload"]["cached"] is False


def test_new_three_turn_flow_and_legacy_four_turn_flow_score_equally(package):
    root, manifest = package
    business = {"tool_calls": [ScriptedAdapter.tool("list_tasks", {"project_id": "P123", "status": "in_progress"})]}
    sequences = [
        [select_response("task_query"), business, finish_response()],
        [select_response("task_query"), {"tool_calls": [ScriptedAdapter.tool("read_skill", {"path": "references/task_query.md"})]}, business, finish_response()],
    ]
    for sequence in sequences:
        adapter = ScriptedAdapter(sequence, capabilities=NATIVE_CAPS)
        actual = execution.run_case(make_request(root, manifest), adapter)
        expected = {"action": "tool_call", "routes": [["task_query"]], "tool_calls": [{"name": "list_tasks", "arguments": {"project_id": "P123", "status": "in_progress"}}]}
        assert evaluation.score_case(expected, actual, TOOL_SCHEMA)["passed"]
        assert len(adapter.calls) == len(sequence)
        assert sum(m.get("content", "").count("任务查询：查询项目任务。") for m in adapter.calls[-1]["messages"]) == 1

def test_runtime_contract_explains_auto_loaded_content(package):
    root, manifest = package
    captured = []

    class Capture(ScriptedAdapter):
        def invoke(self, messages, config=None, tools=None, *, emit=None):
            captured.append(messages)
            return finish_response()

    execution.run_case(make_request(root, manifest), Capture([], capabilities=NATIVE_CAPS))
    contract = captured[0][0]["content"]
    assert "首次加载直接返回正文" in contract
    assert "无需再调用 read_skill" in contract


def test_finish_policy_revision_invalidates_legacy_fingerprint():
    old = hashing.hash_json({"skill_version_id": "sv", "environment_id": "env",
                             "dataset_id": "ds", "scorer_version": "score-v1",
                             "repeats": 1, "finish_policy": "finish-v2-business-independent"})
    assert old != services.run_fingerprint("sv", "env", "ds", "score-v1", 1)
