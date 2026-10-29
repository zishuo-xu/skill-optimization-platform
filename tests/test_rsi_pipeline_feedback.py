import json

import pytest

from app import optimizer, single_round
from app.errors import AppError


def test_retest_info_events_link_candidate_and_metrics(demo):
    from app import persistence
    from tests.conftest import complete_run
    run = complete_run(demo, 'optimize')
    events = persistence.list_events(run['id'], limit=2000)
    start = next(e for e in events if e['type'] == 'retest_started')
    end = next(e for e in events if e['type'] == 'retest_completed')
    assert start['level'] == end['level'] == 'info'
    assert start['payload']['candidate_skill_version_id'] == run['candidate_skill_version_id']
    assert start['payload']['planned_executions'] > 0
    assert end['payload']['elapsed_ms'] >= 0
    assert end['payload']['delta'] == run['metrics']['delta']


@pytest.mark.parametrize('used_fallback', [True, False])
def test_static_failure_reports_actual_error_and_candidate_source(demo, monkeypatch, used_fallback):
    from app import persistence
    from tests.conftest import complete_run

    original = optimizer.generate_candidate

    def generate(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result['status'] == 'ok'
        result['patch']['edits'][0]['replace'] += '\n[操作说明](missing-instructions.md)\n'
        result['used_fallback'] = used_fallback
        return result

    monkeypatch.setattr(optimizer, 'generate_candidate', generate)
    run = complete_run(demo, 'optimize')
    events = persistence.list_events(run['id'], limit=2000)
    failure = next(event for event in events if event['type'] == 'static_check_failed')['payload']
    issue = failure
    assert ('规则修复候选' if used_fallback else '模型候选') in issue['message']
    assert '引用目标不存在: missing-instructions.md' in issue['message']
    assert failure['candidate_source'] == ('heuristic' if used_fallback else 'model')
    assert any(error['code'] == 'dangling_reference' for error in failure['errors'])
    checks = [event for event in events if event['type'] == 'static_check_item']
    assert len(checks) == 6 and all(event['level'] == 'info' for event in checks)
    failed_check = next(event['payload'] for event in checks if not event['payload']['ok'])
    assert failed_check['name'] == 'manifest_integrity'
    assert failed_check['errors'][0]['code'] == 'dangling_reference'
    assert not any(event['type'] == 'retest_started' for event in events)
    assert run['candidate_skill_version_id'] is None


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
