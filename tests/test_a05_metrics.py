"""A05: R/A/J independence and metric aggregation (score-v1).

The acceptance scenario (§35 A05) requires that a wrong dispatch with
coincidentally correct arguments still passes A, and that correct dispatch with
wrong arguments still passes R — while J fails in both cases. This file also
covers denominator rules (R/A 0 → not_evaluated, infra excluded, capability
failure counted), diagnostics aggregation and execution statistics.
"""

from __future__ import annotations

import copy

from app.evaluation import aggregate_metrics, score_case

TOOL_SCHEMA = [
    {
        "name": "list_tasks",
        "parameters": {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string"},
                "status": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {"name": "search_tasks", "parameters": {"type": "object"}},
]

BASE_CALL = {"name": "list_tasks",
             "arguments": {"project_id": "P123", "status": "in_progress"}}
BASE_EXPECTED = {
    "action": "tool_call",
    "routes": [["task_query"]],
    "tool_calls": [copy.deepcopy(BASE_CALL)],
}


def make_case(case_id: str = "case_001", expectation: dict | None = None) -> dict:
    return {
        "case_id": case_id,
        "query": f"query for {case_id}",
        "expected": copy.deepcopy(expectation if expectation is not None
                                  else BASE_EXPECTED),
    }


def make_actual(*, selected_skills=("task_query",), action="tool_call",
                calls=None, **overrides) -> dict:
    value = {
        "case_id": "case_001",
        "selected_skills": list(selected_skills),
        "action": action,
        "tool_calls": copy.deepcopy(calls if calls is not None
                                    else [BASE_CALL]),
        "capabilities": {"routing": True, "tool_arguments": True},
    }
    value.update(copy.deepcopy(overrides))
    return value


def score(case: dict, actual: dict) -> dict:
    return score_case(case["expected"], actual, TOOL_SCHEMA, None)


def assert_metric(metric: dict, *, value, evaluated: int, passed: int,
                  total_cases: int) -> None:
    assert metric["status"] == ("ok" if evaluated else "not_evaluated")
    assert metric["value"] == value
    assert metric["evaluated"] == evaluated
    assert metric["passed"] == passed
    assert metric["total_cases"] == total_cases


# --------------------------------------------------------------------------- #
# A05: R and A are scored independently
# --------------------------------------------------------------------------- #
def test_wrong_dispatch_with_correct_arguments_passes_a_but_fails_r_and_j():
    case = make_case()
    result = score(case, make_actual(selected_skills=["task_search"]))

    assert result["routing"]["passed"] is False
    assert result["tool_args"]["passed"] is True
    assert result["joint"] is False
    assert result["status"] == "fail"


def test_correct_dispatch_with_wrong_arguments_passes_r_but_fails_a_and_j():
    case = make_case()
    wrong_call = {"name": "list_tasks",
                  "arguments": {"project_id": "P123", "status": "active"}}
    result = score(case, make_actual(calls=[wrong_call]))

    assert result["routing"]["passed"] is True
    assert result["tool_args"]["passed"] is False
    assert result["joint"] is False
    assert result["status"] == "fail"


def test_both_dimensions_correct_pass_jointly():
    result = score(make_case(), make_actual())
    assert result["routing"]["passed"] is True
    assert result["tool_args"]["passed"] is True
    assert result["joint"] is True
    assert result["status"] == "pass"


def test_wrong_route_and_wrong_arguments_fail_both_dimensions():
    wrong_call = {"name": "list_tasks",
                  "arguments": {"project_id": "P999"}}
    result = score(make_case(),
                   make_actual(selected_skills=["task_search"],
                               calls=[wrong_call]))
    assert result["routing"]["passed"] is False
    assert result["tool_args"]["passed"] is False
    assert result["joint"] is False


# --------------------------------------------------------------------------- #
# aggregate metrics: denominators and independence
# --------------------------------------------------------------------------- #
def _mixed_results() -> list[tuple[dict, dict]]:
    results: list[tuple[dict, dict]] = []

    correct = make_case("case_001")
    results.append((correct, score(correct, make_actual())))

    wrong_route = make_case("case_002")
    results.append((wrong_route,
                    score(wrong_route,
                          make_actual(selected_skills=["task_search"]))))

    wrong_args = make_case("case_003")
    results.append((wrong_args, score(
        wrong_args,
        make_actual(calls=[{"name": "list_tasks",
                            "arguments": {"project_id": "P123",
                                          "status": "active"}}]),
    )))

    clarify = make_case("case_004", {
        "action": "clarify", "routes": [], "tool_calls": [],
        "missing_fields": ["project_id"],
    })
    results.append((clarify, score(
        clarify,
        make_actual(selected_skills=[], action="clarify", calls=[],
                    missing_fields=["project_id"]),
    )))

    no_action = make_case("case_005", {
        "action": "no_action", "routes": [], "tool_calls": [],
    })
    results.append((no_action, score(
        no_action,
        make_actual(selected_skills=[], action="no_action", calls=[]),
    )))

    routing_only = make_case("case_006", {
        "action": "tool_call", "routes": [["task_query"]], "tool_calls": [],
    })
    results.append((routing_only, score(
        routing_only, make_actual(selected_skills=["task_query"], calls=[]),
    )))

    infra = make_case("case_007")
    results.append((infra, score(
        infra,
        make_actual(calls=[], error_kind="infrastructure",
                    error={"code": "timeout", "message": "超时"}),
    )))

    capability = make_case("case_008")
    results.append((capability, score(
        capability,
        make_actual(calls=[], error_kind="capability",
                    error={"code": "format_error", "message": "格式错误"}),
    )))
    return results


def test_aggregate_denominators_and_independent_values():
    results = _mixed_results()
    metrics = aggregate_metrics(results, planned=9)

    # R: cases 1,2,3,6,8 declare routes; passed 1,3,6,8 (case 2 wrong route,
    # case 8 kept its route even though the case failed as a capability error).
    assert_metric(metrics["routing"], value=0.8, evaluated=5, passed=4,
                  total_cases=8)
    # A: cases 1,2,3,8 declare expected tool calls; passed 1,2.
    assert_metric(metrics["tool_args"], value=0.5, evaluated=4, passed=2,
                  total_cases=8)
    # J: all valid cases (infra excluded) = 7; passed 1,4,5,6.
    assert metrics["joint"]["evaluated"] == 7
    assert metrics["joint"]["passed"] == 4
    assert metrics["joint"]["value"] == round(4 / 7, 6)
    assert metrics["joint"]["status"] == "ok"

    execution = metrics["execution"]
    assert execution["planned"] == 9
    assert execution["completed"] == 8
    assert execution["valid"] == 7
    assert execution["infra_errors"] == 1
    assert execution["capability_failures"] == 1


def test_aggregate_not_evaluated_when_denominator_is_zero():
    clarify = make_case("case_001", {
        "action": "clarify", "routes": [], "tool_calls": [],
        "missing_fields": ["project_id"],
    })
    no_action = make_case("case_002", {
        "action": "no_action", "routes": [], "tool_calls": [],
    })
    results = [
        (clarify, score(clarify, make_actual(
            selected_skills=[], action="clarify", calls=[],
            missing_fields=["project_id"]))),
        (no_action, score(no_action, make_actual(
            selected_skills=[], action="no_action", calls=[]))),
    ]
    metrics = aggregate_metrics(results)

    assert_metric(metrics["routing"], value=None, evaluated=0, passed=0,
                  total_cases=2)
    assert_metric(metrics["tool_args"], value=None, evaluated=0, passed=0,
                  total_cases=2)
    assert_metric(metrics["joint"], value=1.0, evaluated=2, passed=2,
                  total_cases=2)


def test_aggregate_empty_results():
    metrics = aggregate_metrics([])
    for key in ("routing", "tool_args", "joint"):
        assert_metric(metrics[key], value=None, evaluated=0, passed=0,
                      total_cases=0)
    assert metrics["execution"]["planned"] == 0
    assert metrics["execution"]["completed"] == 0


def test_infrastructure_errors_are_excluded_from_every_denominator():
    good = make_case("case_001")
    infra = make_case("case_002")
    results = [
        (good, score(good, make_actual())),
        (infra, score(infra, make_actual(
            calls=[], error_kind="infrastructure",
            error={"code": "network_error", "message": "断网"}))),
    ]
    metrics = aggregate_metrics(results)

    assert_metric(metrics["routing"], value=1.0, evaluated=1, passed=1,
                  total_cases=2)
    assert_metric(metrics["tool_args"], value=1.0, evaluated=1, passed=1,
                  total_cases=2)
    assert_metric(metrics["joint"], value=1.0, evaluated=1, passed=1,
                  total_cases=2)
    execution = metrics["execution"]
    assert execution["valid"] == 1
    assert execution["infra_errors"] == 1
    assert execution["completed"] == 2


def test_capability_failure_counts_in_denominator_and_fails():
    case = make_case()
    failed = score(case, make_actual(
        selected_skills=[], calls=[], error_kind="capability",
        error={"code": "fixture_miss", "message": "无模拟返回"},
        fixture_misses=[{"tool": "list_tasks",
                         "arguments": {"project_id": "P123"}}],
    ))
    metrics = aggregate_metrics([(case, failed)])

    assert_metric(metrics["routing"], value=0.0, evaluated=1, passed=0,
                  total_cases=1)
    assert_metric(metrics["tool_args"], value=0.0, evaluated=1, passed=0,
                  total_cases=1)
    assert_metric(metrics["joint"], value=0.0, evaluated=1, passed=0,
                  total_cases=1)
    assert metrics["execution"]["capability_failures"] == 1
    assert metrics["execution"]["valid"] == 1


def test_missing_tool_arguments_capability_makes_a_not_evaluated():
    case = make_case()
    result = score(case, make_actual(
        calls=[], capabilities={"routing": True, "tool_arguments": False}))
    metrics = aggregate_metrics([(case, result)])

    # Only routing is evaluated; A must not be reported as 0%.
    assert_metric(metrics["routing"], value=1.0, evaluated=1, passed=1,
                  total_cases=1)
    assert_metric(metrics["tool_args"], value=None, evaluated=0, passed=0,
                  total_cases=1)
    assert metrics["joint"]["evaluated"] == 1
    assert metrics["joint"]["passed"] == 0


# --------------------------------------------------------------------------- #
# diagnostics and execution statistics
# --------------------------------------------------------------------------- #
def test_aggregate_diagnostics_group_by_skill_tool_field_and_category():
    case = make_case()
    failed = score(case, make_actual(
        selected_skills=["task_search"],
        calls=[{"name": "list_tasks", "arguments": {}}],
    ))
    metrics = aggregate_metrics([(case, failed)])

    diagnostics = metrics["diagnostics"]
    assert diagnostics["skills"] == {"task_search": 1}
    assert diagnostics["tools"] == {"list_tasks": 2}
    assert diagnostics["fields"] == {"$.project_id": 1, "$.status": 1}
    assert diagnostics["categories"]["wrong_route"] == 1
    assert diagnostics["categories"]["missing_arg"] == 2


def test_execution_statistics_include_tokens_cost_duration_and_misses():
    case = make_case()
    fixture_miss = score(case, make_actual(
        calls=[], error_kind="capability",
        error={"code": "fixture_miss", "message": "无模拟返回"},
        fixture_misses=[{"tool": "list_tasks", "arguments": {}}],
    ))
    metrics = aggregate_metrics(
        [(case, fixture_miss)], planned=10, duration_ms=1234,
        usage={"input_tokens": 100, "output_tokens": 20}, cost=0.5,
    )
    execution = metrics["execution"]
    assert execution["planned"] == 10
    assert execution["completed"] == 1
    assert execution["duration_ms"] == 1234
    assert execution["tokens"] == 120
    assert execution["cost"] == 0.5
    assert execution["fixture_misses"] == 1

    explicit = aggregate_metrics([(case, fixture_miss)], fixture_misses=3)
    assert explicit["execution"]["fixture_misses"] == 3


def test_aggregate_accepts_dict_entries_as_well_as_tuples():
    case = make_case()
    result = score(case, make_actual())
    tuple_metrics = aggregate_metrics([(case, result)])
    dict_metrics = aggregate_metrics([{"case": case, "score": result}])
    assert tuple_metrics == dict_metrics
