"""Selecting a skill loads its mapped file without a second model round trip."""

from app.execution import run_case
from app.obs_tools import ObsToolRuntime
from tests.test_a13_capabilities import (
    NATIVE_CAPS, ScriptedAdapter, make_request, package,
)


def test_select_skill_returns_content_and_repeated_read_is_cached(package):
    root, manifest = package
    events = []
    runtime = ObsToolRuntime(skill_snapshot_path=root, manifest=manifest,
                             allowed_skill_ids=["task_query"], emit=events.append)
    selected = runtime.select_skill({"skill_id": "task_query"}, call_id="s1")
    assert selected["content"] == "任务查询：查询项目任务。"
    assert selected["cached"] is False
    repeated = runtime.read_skill({"path": "references/task_query.md"}, call_id="r1")
    assert repeated["content"] == selected["content"]
    assert repeated["cached"] is True
    reads = [event for event in events if event["type"] == "skill_read"]
    assert [event["payload"]["source"] for event in reads] == ["select_skill", "read_skill"]
    assert reads[1]["payload"]["cached"] is True


def test_native_model_can_go_select_to_business_without_read_round_trip(package):
    root, manifest = package
    adapter = ScriptedAdapter([
        {"tool_calls": [ScriptedAdapter.tool("select_skill", {"skill_id": "task_query"}, "s1")]},
        {"tool_calls": [ScriptedAdapter.tool("call_tool", {"name": "list_tasks", "arguments": {"project_id": "P123", "status": "in_progress"}}, "b1")]},
        {"tool_calls": [ScriptedAdapter.tool("finish", {
            "action": "tool_call", "selected_skills": ["task_query"],
            "missing_fields": [], "summary": "完成"}, "f1")]},
    ], capabilities=NATIVE_CAPS)
    result = run_case(make_request(root, manifest), adapter)
    assert result["error"] is None
    assert result["tool_calls"] == [{"name": "list_tasks", "arguments": {
        "project_id": "P123", "status": "in_progress"}}]
    assert len(adapter.calls) == 3
    first_tool_messages = [m for m in adapter.calls[1]["messages"] if m.get("role") == "tool"]
    assert any("任务查询：查询项目任务。" in m.get("content", "") for m in first_tool_messages)
