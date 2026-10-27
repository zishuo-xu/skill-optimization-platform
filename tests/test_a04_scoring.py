"""A04: score-v1 determinism — wrong route/call/order/tool/args/type/extra.

Covers requirement §15/§16 and contract §2.4: R/A/J independent scoring, strict
JSON deep comparison, schema-before-value validation, variants, clarify /
no_action, capability vs infrastructure errors and field-path diagnostics.
"""

from __future__ import annotations

import copy

from app.evaluation import score_case

BASE_CALL = {"name": "list_tasks",
             "arguments": {"project_id": "P123", "status": "in_progress"}}
BASE_EXPECTED = {
    "action": "tool_call",
    "routes": [["task_query"]],
    "tool_calls": [BASE_CALL],
}


def expected(**overrides) -> dict:
    value = copy.deepcopy(BASE_EXPECTED)
    value.update(copy.deepcopy(overrides))
    return value


def actual(**overrides) -> dict:
    value = {
        "case_id": "case_001",
        "selected_skills": ["task_query"],
        "action": "tool_call",
        "tool_calls": [copy.deepcopy(BASE_CALL)],
        "capabilities": {"routing": True, "tool_arguments": True},
    }
    value.update(copy.deepcopy(overrides))
    return value


def score(expectation=None, result=None, compare_rules=None) -> dict:
    return score_case(expectation if expectation is not None else expected(),
                      result if result is not None else actual(),
                      compare_rules)


def categories(result: dict) -> list[str]:
    return [diagnostic["category"] for diagnostic in result["diagnostics"]]


def find_diagnostic(result: dict, category: str) -> dict:
    for diagnostic in result["diagnostics"]:
        if diagnostic["category"] == category:
            return diagnostic
    raise AssertionError(f"缺少诊断类别 {category}: {result['diagnostics']}")


# --------------------------------------------------------------------------- #
# happy path / strict JSON comparison
# --------------------------------------------------------------------------- #
def test_exact_match_passes_and_object_key_order_is_irrelevant():
    result = score(
        expected(),
        actual(tool_calls=[{"name": "list_tasks", "arguments": {
            "status": "in_progress", "project_id": "P123"}}]),
    )
    assert result["status"] == "pass"
    assert result["routing"] == {
        "evaluable": True, "passed": True,
        "reason": result["routing"]["reason"],
        "actual_routes": [["task_query"]],
        "allowed_routes": [["task_query"]],
    }
    assert result["tool_args"]["passed"] is True
    assert result["joint"] is True
    assert result["diagnostics"] == []


def test_missing_argument_fails_with_field_path():
    result = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123",
                                            "status": "in_progress"}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"project_id": "P123"}}]),
    )
    assert result["status"] == "fail"
    assert result["tool_args"]["passed"] is False
    diagnostic = find_diagnostic(result, "missing_arg")
    assert diagnostic["field_path"] == "$.status"
    assert diagnostic["expected_value"] == "in_progress"
    assert diagnostic["tool"] == "list_tasks"


def test_extra_argument_fails():
    result = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123"}}]),
        actual(tool_calls=[{"name": "list_tasks", "arguments": {
            "project_id": "P123", "limit": 10}}]),
    )
    assert result["tool_args"]["passed"] is False
    diagnostic = find_diagnostic(result, "extra_arg")
    assert diagnostic["field_path"] == "$.limit"
    assert diagnostic["actual_value"] == 10


def test_type_is_strict():
    wrong_type = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123"}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"project_id": 123}}]),
    )
    assert wrong_type["tool_args"]["passed"] is False
    assert "type_error" in categories(wrong_type)

    bool_is_not_int = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"flag": True}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"flag": 1}}]),
    )
    assert bool_is_not_int["tool_args"]["passed"] is False
    assert "type_error" in categories(bool_is_not_int)


def test_value_mismatch_and_nested_field_path():
    result = score(
        expected(tool_calls=[{"name": "search_tasks", "arguments": {
            "filters": {"status": "in_progress"}}}]),
        actual(tool_calls=[{"name": "search_tasks", "arguments": {
            "filters": {"status": "active"}}}]),
    )
    assert result["tool_args"]["passed"] is False
    diagnostic = find_diagnostic(result, "value_error")
    assert diagnostic["field_path"] == "$.filters.status"
    assert diagnostic["expected_value"] == "in_progress"
    assert diagnostic["actual_value"] == "active"


def test_missing_is_not_null():
    omitted = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123",
                                            "status": None}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"project_id": "P123"}}]),
    )
    assert omitted["tool_args"]["passed"] is False
    assert "missing_arg" in categories(omitted)

    extra_null = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123"}}]),
        actual(tool_calls=[{"name": "list_tasks", "arguments": {
            "project_id": "P123", "status": None}}]),
    )
    assert extra_null["tool_args"]["passed"] is False
    assert "extra_arg" in categories(extra_null)


def test_array_order_is_significant():
    result = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"ids": ["a", "b"]}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"ids": ["b", "a"]}}]),
    )
    assert result["tool_args"]["passed"] is False
    assert find_diagnostic(result, "value_error")["field_path"] == "$.ids[0]"


def test_expected_arguments_define_the_comparison_contract():
    result = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"status": "in_progress"}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"status": "in_progress"}}]),
    )
    assert result["tool_args"]["passed"] is True


def test_expected_value_mismatch_fails():
    result = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123"}}]),
        actual(tool_calls=[{"name": "list_tasks", "arguments": {
            "project_id": "P123", "status": "archived"}}]),
    )
    assert result["tool_args"]["passed"] is False
    assert "extra_arg" in categories(result)


def test_arbitrary_skill_defined_tool_is_scored():
    result = score(
        expected(tool_calls=[{"name": "unknown_tool",
                              "arguments": {"x": 1}}]),
        actual(tool_calls=[{"name": "unknown_tool",
                            "arguments": {"x": 1}}]),
    )
    assert result["tool_args"]["passed"] is True


# --------------------------------------------------------------------------- #
# tool call sequence: order / wrong / missing / extra
# --------------------------------------------------------------------------- #
def test_wrong_order_reports_wrong_order():
    expectation = expected(tool_calls=[
        {"name": "list_tasks", "arguments": {"project_id": "P123"}},
        {"name": "search_tasks", "arguments": {"filters": {"status": "active"}}},
    ])
    result = score(expectation, actual(tool_calls=[
        {"name": "search_tasks", "arguments": {"filters": {"status": "active"}}},
        {"name": "list_tasks", "arguments": {"project_id": "P123"}},
    ]))
    assert result["tool_args"]["passed"] is False
    assert find_diagnostic(result, "wrong_order")["field_path"] == "$.tool_calls"


def test_wrong_tool_missing_tool_and_extra_tool():
    wrong_tool = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123"}}]),
        actual(tool_calls=[{"name": "search_tasks",
                            "arguments": {"project_id": "P123"}}]),
    )
    assert wrong_tool["tool_args"]["passed"] is False
    assert "wrong_tool" in categories(wrong_tool)

    missing_tool = score(
        expected(tool_calls=[
            {"name": "list_tasks", "arguments": {"project_id": "P123"}},
            {"name": "search_tasks", "arguments": {}},
        ]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"project_id": "P123"}}]),
    )
    assert missing_tool["tool_args"]["passed"] is False
    assert "missing_tool" in categories(missing_tool)

    extra_tool = score(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"project_id": "P123"}}]),
        actual(tool_calls=[
            {"name": "list_tasks", "arguments": {"project_id": "P123"}},
            {"name": "search_tasks", "arguments": {}},
        ]),
    )
    assert extra_tool["tool_args"]["passed"] is False
    assert "extra_tool" in categories(extra_tool)


def test_unordered_call_match_allows_any_order_but_rejects_extra_call():
    expectation = expected(
        call_match="unordered",
        tool_calls=[
            {"name": "list_tasks", "arguments": {"project_id": "P123"}},
            {"name": "search_tasks", "arguments": {"filters": {"status": "active"}}},
        ],
    )
    reordered = score(expectation, actual(tool_calls=[
        {"name": "search_tasks", "arguments": {"filters": {"status": "active"}}},
        {"name": "list_tasks", "arguments": {"project_id": "P123"}},
    ]))
    assert reordered["tool_args"]["passed"] is True
    assert reordered["joint"] is True

    extra = score(expectation, actual(tool_calls=[
        {"name": "search_tasks", "arguments": {"filters": {"status": "active"}}},
        {"name": "list_tasks", "arguments": {"project_id": "P123"}},
        {"name": "search_tasks", "arguments": {"filters": {"status": "active"}}},
    ]))
    assert extra["tool_args"]["passed"] is False
    assert "extra_tool" in categories(extra)


def test_variants_accept_alternative_complete_sequences():
    expectation = expected(variants=[{
        "routes": [["task_search"]],
        "tool_calls": [{"name": "search_tasks",
                        "arguments": {"filters": {"status": "active"}}}],
    }])
    result = score(expectation, actual(
        selected_skills=["task_search"],
        tool_calls=[{"name": "search_tasks",
                     "arguments": {"filters": {"status": "active"}}}],
    ))
    assert result["routing"]["passed"] is True
    assert result["tool_args"]["passed"] is True
    assert result["joint"] is True
    assert result["status"] == "pass"


# --------------------------------------------------------------------------- #
# routing (R)
# --------------------------------------------------------------------------- #
def test_route_set_mode_deduplicates_and_ordered_mode_rejects_extra_call():
    set_mode = score(result=actual(selected_skills=["task_query", "task_query"]))
    assert set_mode["routing"]["passed"] is True

    ordered = score(expected(route_ordered=True),
                    actual(selected_skills=["task_query", "task_query"]))
    assert ordered["routing"]["passed"] is False
    assert "extra_route" in categories(ordered)


def test_route_ordered_reports_wrong_order():
    expectation = expected(routes=[["task_query", "task_search"]],
                           route_ordered=True,
                           tool_calls=[])
    swapped = score(expectation,
                    actual(selected_skills=["task_search", "task_query"],
                           tool_calls=[]))
    assert swapped["routing"]["passed"] is False
    assert "wrong_order" in categories(swapped)


def test_route_wrong_missing_and_extra():
    wrong = score(result=actual(selected_skills=["task_search"]))
    assert wrong["routing"]["passed"] is False
    assert "wrong_route" in categories(wrong)

    missing = score(
        expected(routes=[["task_query", "task_search"]]),
        actual(selected_skills=["task_query"]),
    )
    assert missing["routing"]["passed"] is False
    diagnostic = find_diagnostic(missing, "missing_route")
    assert diagnostic["skill_id"] == "task_search"

    extra = score(
        expected(routes=[["task_query"]]),
        actual(selected_skills=["task_query", "other_skill"]),
    )
    assert extra["routing"]["passed"] is False
    assert find_diagnostic(extra, "extra_route")["skill_id"] == "other_skill"


def test_route_events_are_used_in_sequence_order():
    result = score(
        expected(routes=[["task_query", "task_search"]], route_ordered=True,
                 tool_calls=[]),
        actual(route_events=[{"skill_id": "task_search", "seq": 2},
                             {"skill_id": "task_query", "seq": 1}],
               selected_skills=[], tool_calls=[]),
    )
    assert result["routing"]["actual_routes"] == [
        ["task_query", "task_search"]]
    assert result["routing"]["passed"] is True


# --------------------------------------------------------------------------- #
# clarify / no_action / action
# --------------------------------------------------------------------------- #
def test_clarify_matches_actions_and_missing_fields_as_sets():
    expectation = {"action": "clarify", "routes": [], "tool_calls": [],
                   "missing_fields": ["project_id", "status"]}
    pass_result = score(expectation, actual(
        action="clarify", tool_calls=[],
        missing_fields=["status", "project_id"]))
    assert pass_result["action_ok"] is True
    assert pass_result["joint"] is True
    assert pass_result["status"] == "pass"

    fail_result = score(expectation, actual(
        action="clarify", tool_calls=[], missing_fields=["project_id"]))
    assert fail_result["joint"] is False
    assert find_diagnostic(fail_result, "clarify_mismatch")["field_path"] == \
        "missing_fields"


def test_clarify_without_expected_missing_fields_only_checks_action():
    expectation = {"action": "clarify", "routes": [], "tool_calls": []}
    result = score(expectation, actual(action="clarify", tool_calls=[],
                                       missing_fields=["whatever"]))
    assert result["joint"] is True


def test_no_action_rejects_unexpected_business_calls():
    expectation = {"action": "no_action", "routes": [], "tool_calls": []}
    pass_result = score(expectation, actual(action="no_action", tool_calls=[]))
    assert pass_result["joint"] is True and pass_result["status"] == "pass"

    fail_result = score(expectation, actual(action="no_action"))
    assert fail_result["joint"] is False
    assert "unexpected_call" in categories(fail_result)


def test_action_mismatch_diagnostic():
    result = score(expected(), actual(action="no_action", tool_calls=[]))
    assert result["action_ok"] is False
    assert result["joint"] is False
    diagnostic = find_diagnostic(result, "action_mismatch")
    assert diagnostic["expected_value"] == "tool_call"
    assert diagnostic["actual_value"] == "no_action"


# --------------------------------------------------------------------------- #
# capability vs infrastructure errors
# --------------------------------------------------------------------------- #
def test_infrastructure_error_is_infra_and_not_evaluated():
    result = score(result=actual(
        error_kind="infrastructure",
        error={"code": "timeout", "message": "网关超时"},
        tool_calls=[],
    ))
    assert result["status"] == "infra"
    assert result["passed"] is False
    assert result["joint"] is False
    assert result["routing"]["evaluable"] is False
    assert result["tool_args"]["evaluable"] is False


def test_capability_errors_fail_and_create_diagnostics():
    format_error = score(result=actual(
        error_kind="capability",
        error={"code": "format_error", "message": "输出格式错误"},
        tool_calls=[], selected_skills=[],
    ))
    assert format_error["status"] == "fail"
    assert format_error["passed"] is False
    assert format_error["joint"] is False
    diagnostic = find_diagnostic(format_error, "protocol_error")
    assert diagnostic["actual_value"] == "format_error"

    fixture_miss = score(result=actual(
        error_kind="capability",
        error={"code": "fixture_miss", "message": "模拟返回缺失"},
        fixture_misses=[{"tool": "list_tasks",
                         "arguments": {"project_id": "P123"}}],
        tool_calls=[],
    ))
    assert fixture_miss["status"] == "fail"
    diagnostic = find_diagnostic(fixture_miss, "fixture_miss")
    assert diagnostic["tool"] == "list_tasks"


def test_capability_tool_arguments_false_makes_a_not_evaluable():
    result = score(expected(), actual(
        tool_calls=[],
        capabilities={"routing": True, "tool_arguments": False},
    ))
    assert result["routing"]["evaluable"] is True
    assert result["routing"]["passed"] is True
    assert result["tool_args"]["evaluable"] is False
    assert result["tool_args"]["passed"] is False
    assert result["joint"] is False


# --------------------------------------------------------------------------- #
# versioned compare rules
# --------------------------------------------------------------------------- #
def _args_case(expected_arguments: dict, actual_arguments: dict,
               compare: dict | None = None) -> dict:
    expectation = {"action": "tool_call", "routes": [],
                   "tool_calls": [{"name": "list_tasks",
                                   "arguments": expected_arguments}]}
    if compare is not None:
        expectation["compare"] = compare
    return score(expectation,
                 actual(tool_calls=[{"name": "list_tasks",
                                     "arguments": actual_arguments}]))


def test_numeric_tolerance_requires_explicit_versioned_rule():
    without_rules = _args_case({"score": 1.0}, {"score": 1.04})
    assert without_rules["tool_args"]["passed"] is False

    with_rules = _args_case(
        {"score": 1.0}, {"score": 1.04},
        {"version": "compare-v1", "numeric_tolerance": 0.1})
    assert with_rules["tool_args"]["passed"] is True


def test_datetime_normalisation_rule():
    left = "2026-01-01T00:00:00Z"
    right = "2026-01-01T08:00:00+08:00"
    assert _args_case({"at": left}, {"at": right})["tool_args"]["passed"] is False
    assert _args_case(
        {"at": left}, {"at": right},
        {"version": "compare-v1", "normalize_datetime": True},
    )["tool_args"]["passed"] is True


def test_unordered_arrays_rule():
    assert _args_case({"ids": [1, 2, 3]}, {"ids": [3, 1, 2]})[
        "tool_args"]["passed"] is False
    assert _args_case(
        {"ids": [1, 2, 3]}, {"ids": [3, 1, 2]},
        {"version": "compare-v1", "unordered_arrays": True},
    )["tool_args"]["passed"] is True


def test_allowed_values_rule():
    assert _args_case({"status": "in_progress"}, {"status": "active"})[
        "tool_args"]["passed"] is False
    assert _args_case(
        {"status": "in_progress"}, {"status": "active"},
        {"version": "compare-v1",
         "allowed_values": {"$.status": ["active", "in_progress"]}},
    )["tool_args"]["passed"] is True


def test_unknown_compare_rule_version_invalidates_case():
    result = _args_case({"score": 1.0}, {"score": 1.0},
                        {"version": "compare-v99"})
    assert result["status"] == "invalid"
    assert "protocol_error" in categories(result)


# --------------------------------------------------------------------------- #
# malformed expectation / actual
# --------------------------------------------------------------------------- #
def test_invalid_expected_action_invalidates_case():
    result = score(expected(action="delegate"), actual())
    assert result["status"] == "invalid"


def test_non_mapping_actual_is_invalid():
    result = score_case(expected(), ["not", "a", "mapping"])
    assert result["status"] == "invalid"


def test_all_diagnostics_have_contract_fields():
    result = score(expected(), actual(tool_calls=[
        {"name": "list_tasks", "arguments": {"project_id": "P123"}}]))
    assert result["diagnostics"]
    for diagnostic in result["diagnostics"]:
        contract = {"category", "skill_id", "tool", "field_path",
                    "expected_value", "actual_value", "case_id"}
        assert contract <= set(diagnostic)
        # Optional extras: ``note`` (judge detail) and ``basis`` (判定依据).
        assert set(diagnostic) - contract <= {"note", "basis"}
        assert diagnostic.get("basis")
        assert diagnostic["case_id"] == "case_001"


# --------------------------------------------------------------------------- #
# environment scorer config vs versioned compare rules (pipeline integration)
# --------------------------------------------------------------------------- #
def test_environment_scorer_config_keeps_default_strict_comparison():
    scorer_config = {"version": "score-v1", "route_compare": "set",
                     "param_compare": "strict_json", "seed": 42}
    passing = score_case(expected(), actual(), scorer_config)
    assert passing["status"] == "pass"
    assert "protocol_error" not in categories(passing)

    failing = score_case(
        expected(),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"project_id": "P999"}}]),
        scorer_config,
    )
    assert failing["status"] == "fail"
    assert "value_error" in categories(failing)


def test_compare_rules_as_fourth_argument_need_explicit_version():
    passing = score_case(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"score": 1.0}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"score": 1.04}}]),
        {"version": "compare-v1", "numeric_tolerance": 0.1},
    )
    assert passing["tool_args"]["passed"] is True

    unversioned = score_case(
        expected(tool_calls=[{"name": "list_tasks",
                              "arguments": {"score": 1.0}}]),
        actual(tool_calls=[{"name": "list_tasks",
                            "arguments": {"score": 1.04}}]),
        {"numeric_tolerance": 0.1},
    )
    assert unversioned["status"] == "invalid"

    unsupported = score_case(
        expected(), actual(),
        {"version": "compare-v99", "numeric_tolerance": 0.1},
    )
    assert unsupported["status"] == "invalid"


def test_arguments_path_compares_only_the_args_subtree():
    expectation = {
        "action": "tool_call",
        "routes": [["gallery_image_editor"]],
        "tool_calls": [{
            "name": "editImage",
            "arguments": {"targetColor": "蓝色"},
            "arguments_path": "args",
        }],
    }
    result = {
        "case_id": "case_img_001",
        "selected_skills": ["gallery_image_editor"],
        "action": "tool_call",
        "tool_calls": [{
            "name": "editImage",
            "arguments": {
                "method": "recolor_region",
                "script": "handlers/photo_ops.py",
                "request": {"query": "调成蓝色"},
                "args": {"targetColor": "蓝色"},
            },
        }],
        "capabilities": {"routing": True, "tool_arguments": True},
    }
    scored = score_case(expectation, result)
    assert scored["tool_args"]["passed"] is True
    assert scored["joint"] is True


def test_arguments_path_failure_reports_nested_field_path():
    expectation = {
        "action": "tool_call",
        "routes": [["gallery_image_editor"]],
        "tool_calls": [{
            "name": "editImage",
            "arguments": {"targetColor": "蓝色"},
            "arguments_path": "args",
        }],
    }
    result = {
        "case_id": "case_img_002",
        "selected_skills": ["gallery_image_editor"],
        "action": "tool_call",
        "tool_calls": [{
            "name": "editImage",
            "arguments": {
                "method": "recolor_region",
                "script": "handlers/photo_ops.py",
                "request": {"query": "调成红色"},
                "args": {"targetColor": "红色"},
            },
        }],
        "capabilities": {"routing": True, "tool_arguments": True},
    }
    scored = score_case(expectation, result)
    assert scored["tool_args"]["passed"] is False
    diagnostic = find_diagnostic(scored, "value_error")
    assert diagnostic["field_path"] == "$.args.targetColor"
    assert diagnostic["expected_value"] == "蓝色"
    assert diagnostic["actual_value"] == "红色"
