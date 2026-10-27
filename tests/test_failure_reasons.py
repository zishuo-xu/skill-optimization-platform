"""Failure reasons carry the rule they were judged by ("判定依据")."""

from __future__ import annotations

from app import evaluation, single_round
from app.evaluation import score_case

EXPECTED = {
    "action": "tool_call",
    "routes": [["task_assign"]],
    "tool_calls": [{"name": "assign_task", "arguments": {"task_id": "T42", "assignee_id": "U7"}}],
}
SCHEMA = [{"type": "function", "function": {
    "name": "assign_task",
    "parameters": {"type": "object", "required": ["task_id", "assignee_id"],
                   "properties": {"task_id": {"type": "string"},
                                  "assignee_id": {"type": "string"},
                                  "priority": {"enum": ["low", "high"]}}}}}]


def _actual(**overrides):
    base = {"selected_skills": ["task_assign"], "action": "tool_call",
            "tool_calls": [{"name": "assign_task", "arguments": {"task_id": "T42", "assignee_id": "U7"}}]}
    base.update(overrides)
    return base


def _basis(result, category):
    return [d.get("basis") for d in result["diagnostics"] if d["category"] == category]


def test_value_mismatch_names_the_comparison_mode():
    strict = score_case(EXPECTED, _actual(tool_calls=[{"name": "assign_task", "arguments": {
        "task_id": "T42", "assignee_id": "u7"}}]), SCHEMA, None)
    assert strict["passed"] is False
    assert _basis(strict, "value_error") == ["严格比较：值必须与数据集预期完全相等"]

    rules = {"version": "compare-v2", "mode": "lenient",
             "field_rules": [{"path": "$.assignee_id", "mode": "strict"}]}
    by_field_rule = score_case(EXPECTED, _actual(tool_calls=[{"name": "assign_task", "arguments": {
        "task_id": "t42", "assignee_id": "u7"}}]), SCHEMA, rules)
    bases = _basis(by_field_rule, "value_error")
    assert len(bases) == 1 and bases[0].startswith("按字段规则 $.assignee_id，严格比较")


def test_route_action_tool_and_schema_bases():
    wrong_route = score_case(EXPECTED, _actual(selected_skills=["task_query"]), SCHEMA, None)
    assert _basis(wrong_route, "wrong_route") == ["数据集允许的技能路线：task_assign"]

    no_call = score_case(EXPECTED, _actual(action="no_action", tool_calls=[]), SCHEMA, None)
    assert _basis(no_call, "action_mismatch") == ["数据集预期动作：调用工具"]
    assert _basis(no_call, "missing_tool") == ["数据集预期的工具调用：assign_task"]

    only_task = dict(EXPECTED, tool_calls=[{"name": "assign_task", "arguments": {"task_id": "T42"}}])
    rules = {"version": "compare-v2", "mode": "strict", "allow_extra_keys": True}
    schema = score_case(only_task, _actual(tool_calls=[{"name": "assign_task", "arguments": {
        "task_id": "T42", "priority": "urgent"}}]), SCHEMA, rules)
    assert "工具 Schema 把这个字段列为必填（required）" in _basis(schema, "missing_arg")
    assert any(basis.startswith("工具 Schema 只允许这些值") for basis in _basis(schema, "value_error"))


def test_execution_error_basis_quotes_the_model_error():
    result = score_case(EXPECTED, _actual(action="no_action", tool_calls=[], error={
        "code": "steps_exhausted", "message": "模型调用 12 次仍未给出最终结果"}), SCHEMA, None)
    assert _basis(result, "protocol_error") == ["模型返回：模型调用 12 次仍未给出最终结果"]


def test_log_reason_lists_the_actual_failure_not_a_passing_part():
    result = score_case(EXPECTED, _actual(tool_calls=[{"name": "assign_task", "arguments": {
        "task_id": "T42"}}]), SCHEMA, None)
    reason = single_round._score_reason(result)
    assert "实际路线匹配" not in reason
    assert "缺少assign_task 的参数 assignee_id（应为 U7）" in reason
    assert "依据：数据集预期包含这个字段" in reason


def test_describe_diagnostic_without_basis_is_plain_text():
    text = evaluation.describe_diagnostic({"category": "wrong_tool", "expected_value": "a",
                                           "actual_value": "b"})
    assert text == "工具用错：调了 b，应为 a"
