from __future__ import annotations

from app.fixtures import FixtureMatcher
from app.execution import run_case
from tests.test_a13_capabilities import NATIVE_CAPS, TEXT_TOOL_CAPS, ScriptedAdapter, finish_response, make_request
import json


def test_empty_fixtures_are_distinguished_without_exposing_response():
    result = FixtureMatcher().diagnose_miss("imageEditWorkflow", {})
    assert result == {
        "reason": "empty_fixtures", "fixture_count": 0,
        "available_tools": [], "candidate_indices": [],
    }


def test_tool_name_mismatch_lists_only_declared_names():
    result = FixtureMatcher([
        {"name": "editImage", "match": {}, "response": {"secret": "hidden"}},
    ]).diagnose_miss("imageEditWorkflow", {})
    assert result["reason"] == "tool_name_mismatch"
    assert result["available_tools"] == ["editImage"]
    assert "secret" not in str(result)


def test_argument_mismatch_reports_nested_paths_without_consuming():
    matcher = FixtureMatcher([{
        "name": "imageEditWorkflow",
        "match": {"request": {"currentStep": 1}, "args": {"strength": 3}},
        "response": {"private": True},
    }])
    result = matcher.diagnose_miss(
        "imageEditWorkflow", {"request": {"currentStep": 2}, "args": {"strength": "3"}})
    assert result["reason"] == "arguments_mismatch"
    assert result["mismatch_paths"] == {"0": ["$.request.currentStep", "$.args.strength"]}
    assert matcher.match("imageEditWorkflow", {"request": {"currentStep": 1}, "args": {"strength": 3}})["matched"]


def test_once_fixture_is_reported_as_consumed_and_wildcard_remains_available():
    matcher = FixtureMatcher([
        {"name": "imageEditWorkflow", "match": {}, "consume": "once", "response": {"private": 1}},
        {"name": "*", "match": {}, "response": {"ok": True}},
    ])
    matcher.match("imageEditWorkflow", {})
    result = matcher.diagnose_miss("imageEditWorkflow", {})
    assert result["reason"] == "no_miss"
    assert result["candidate_indices"] == [1]
    assert matcher.match("imageEditWorkflow", {})["matched"]


def test_native_fixture_miss_event_contains_reason_without_fixture_response(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# skill", encoding="utf-8")
    (root / "references" / "task_query.md").write_text("query", encoding="utf-8")
    manifest = {"version": 1, "main": "SKILL.md", "subskills": [
        {"skill_id": "task_query", "name": "query", "path": "references/task_query.md"}]}
    adapter = ScriptedAdapter([
        {"tool_calls": [ScriptedAdapter.tool("call_tool", {
            "name": "imageEditWorkflow", "arguments": {"args": {"strength": "3"}}})]},
    ], capabilities=NATIVE_CAPS)
    request = make_request(root, manifest, fixtures=[{
        "name": "imageEditWorkflow", "match": {"args": {"strength": 3}},
        "response": {"private": "must-not-leak"},
    }])
    result = run_case(request, adapter)
    assert result["error"]["code"] == "fixture_miss"
    assert result["error_kind"] == "capability"
    assert result["action"] == "tool_call"
    assert result["tool_calls_include_failed_attempts"] is True
    assert result["tool_calls"] == [{"name": "imageEditWorkflow", "arguments": {"args": {"strength": "3"}}}]
    assert result["tool_results"] == []
    assert not any(event["type"] == "tool_result" for event in result["events"])
    issue_events = [event for event in result["events"] if event["type"] == "fixture_miss"]
    assert issue_events
    issue = issue_events[0]["payload"]["issue"]
    assert "参数不匹配" in issue["message"]
    assert "$.args.strength" in str(issue["technical_detail"])
    assert "must-not-leak" not in json.dumps(issue, ensure_ascii=False)


def test_text_fixture_miss_reports_argument_reason(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# skill", encoding="utf-8")
    (root / "references" / "task_query.md").write_text("query", encoding="utf-8")
    manifest = {"version": 1, "main": "SKILL.md", "subskills": [
        {"skill_id": "task_query", "name": "query", "path": "references/task_query.md"}]}
    content = ("依据：查询\n子技能：task_query\n"
               "工具：imageEditWorkflow {}\n"
               "结束：{\"action\":\"tool_call\",\"selected_skills\":[\"task_query\"],\"missing_fields\":[],\"summary\":\"x\"}")
    adapter = ScriptedAdapter([{"content": content}], capabilities=TEXT_TOOL_CAPS)
    request = make_request(root, manifest, fixtures=[{
        "name": "imageEditWorkflow", "match": {"args": {"strength": 3}},
        "response": {"ok": True},
    }])
    result = run_case(request, adapter)
    assert result["error"]["code"] == "fixture_miss"
    assert result["error_kind"] == "capability"
    assert result["action"] == "tool_call"
    assert result["tool_calls_include_failed_attempts"] is True
    assert result["tool_calls"] == [{"name": "imageEditWorkflow", "arguments": {}}]
    assert result["tool_results"] == []
    assert not any(event["type"] == "tool_result" for event in result["events"])
    issue = next(event["payload"]["issue"] for event in result["events"]
                 if event["type"] == "fixture_miss")
    assert "参数不匹配" in issue["message"]


def test_native_fixture_miss_reports_consumed_once_reason(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# skill", encoding="utf-8")
    (root / "references" / "task_query.md").write_text("query", encoding="utf-8")
    manifest = {"version": 1, "main": "SKILL.md", "subskills": [
        {"skill_id": "task_query", "name": "query", "path": "references/task_query.md"}]}
    adapter = ScriptedAdapter([
        {"tool_calls": [ScriptedAdapter.tool("call_tool", {"name": "imageEditWorkflow", "arguments": {}})]},
        {"tool_calls": [ScriptedAdapter.tool("call_tool", {"name": "imageEditWorkflow", "arguments": {}})]},
    ], capabilities=NATIVE_CAPS)
    request = make_request(root, manifest, fixtures=[{
        "name": "imageEditWorkflow", "match": {}, "consume": "once", "response": {"ok": True},
    }])
    result = run_case(request, adapter)
    assert result["error"]["code"] == "fixture_miss"
    assert result["action"] == "tool_call"
    assert result["tool_calls"] == [
        {"name": "imageEditWorkflow", "arguments": {}},
        {"name": "imageEditWorkflow", "arguments": {}},
    ]
    assert result["tool_results"] == [{"name": "imageEditWorkflow", "arguments": {}, "response": {"ok": True}}]
    issue = next(event["payload"]["issue"] for event in result["events"]
                 if event["type"] == "fixture_miss")
    assert "consume=once" in issue["message"]
