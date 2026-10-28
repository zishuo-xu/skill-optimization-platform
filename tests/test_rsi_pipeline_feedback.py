import json

import pytest

from app import optimizer, single_round
from app.errors import AppError


def test_expected_fixture_coverage_checks_arguments_once_and_alternatives():
    case = {"case_id": "x", "expected": {"tool_calls": [
        {"name": "edit", "arguments": {"color": "red"}},
        {"name": "edit", "arguments": {"color": "red"}}]},
        "fixtures": [{"name": "edit", "match": {"color": "red"},
                      "response": {}, "consume": "once"}]}
    assert single_round._fixture_gap_cases([case]) == ["x"]
    case["fixtures"][0]["consume"] = "always"
    assert single_round._fixture_gap_cases([case]) == []
    case["fixtures"][0]["match"] = {"color": "blue"}
    assert single_round._fixture_gap_cases([case]) == ["x"]
    for call in case["expected"]["tool_calls"]:
        call["arguments_path"] = "args"
    assert single_round._fixture_gap_cases([case]) == []  # incomplete arguments


def test_optimizer_payload_contains_context_but_never_holdout():
    messages = optimizer.build_prompt({"business_execution_prompt": "business policy",
        "failures": [{"split": "optimize", "context": {"image": "a"},
                      "fixtures": [{"name": "edit", "response": {"ok": True}}]},
                     {"split": "validation", "query": "secret holdout"}],
        "passing_summary": {"cases": [{"split": "optimize", "query": "kept"},
                                      {"split": "final_test", "query": "secret final"}]}})
    payload = json.loads(messages[1]["content"].split("\n", 1)[1])
    assert payload["business_execution_prompt"] == "business policy"
    assert payload["failures"][0]["context"] == {"image": "a"}
    assert payload["failures"][0]["fixtures"][0]["response"] == {"ok": True}
    assert "secret holdout" not in messages[1]["content"]
    assert "secret final" not in messages[1]["content"]


class Emitter:
    def set_stage(self, *args, **kwargs): pass
    def emit(self, *args, **kwargs): pass
    def issue(self, *args, **kwargs): pass


def test_preflight_blocks_semantic_without_judge(monkeypatch):
    monkeypatch.setattr(single_round.persistence, "skill_version_integrity", lambda _: {"ok": True})
    env = {"scorer_config": {"compare": {"mode": "semantic"}}}
    with pytest.raises(AppError, match="语义比较需要配置评分模型"):
        single_round._preflight({"baseline_skill_version_id": "v", "acceptance": {}}, env, {},
                                [{"case_id": "a", "expected": {}}], Emitter(), mode="optimize")


def test_wrong_actual_arguments_remain_capability_failure():
    from app import evaluation
    case = {"case_id": "covered", "expected": {"action": "tool_call",
        "tool_calls": [{"name": "edit", "arguments": {"color": "red"}}]},
        "fixtures": [{"name": "edit", "match": {"color": "red"}, "response": {}}]}
    assert single_round._fixture_gap_cases([case]) == []
    actual = {"action": "tool_call", "tool_calls": [{"name": "edit",
        "arguments": {"color": "blue"}}], "fixture_misses": [{"tool": "edit",
        "arguments": {"color": "blue"}}], "error": {"code": "fixture_miss"},
        "error_kind": "capability"}
    score = evaluation.score_case(case["expected"], actual)
    assert score["status"] == "fail"
    assert score["error_kind"] == "capability"


def test_trial_blocks_uncovered_expected_fixture(demo, monkeypatch):
    from app import trial
    from app.errors import ValidationError
    monkeypatch.setattr(trial, "_load_case", lambda *_: {
        "case_id": "bad", "query": "x", "expected": {"action": "tool_call",
        "tool_calls": [{"name": "edit", "arguments": {}}]}, "fixtures": []})
    with pytest.raises(ValidationError) as error:
        trial.try_case({"case_id": "bad"}, project_id=demo["project"]["id"])
    assert error.value.code == "fixture_coverage_incomplete"
