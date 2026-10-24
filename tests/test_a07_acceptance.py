"""A07 验收：accept-v1 候选接受规则（§17）。

覆盖：R/A/J 不下降、至少一项严格提高、critical 回归拒绝、基础设施/包完整性
阻断、全持平拒绝、验证不完整/重复不足/指标未评估 → 证据不足。
"""

from __future__ import annotations

import pytest

from app import acceptance


def _metrics(routing=0.80, tool_args=0.75, joint=0.68) -> dict:
    def block(value: float, total: int = 10) -> dict:
        passed = round(value * total)
        return {"value": value, "status": "ok", "evaluated": total,
                "passed": passed, "total_cases": total}

    return {
        "routing": block(routing),
        "tool_args": block(tool_args),
        "joint": block(joint),
        "execution": {"planned": 10, "completed": 10, "valid": 10,
                      "infra_errors": 0, "duration_ms": 1200,
                      "tokens": 1000, "cost": None, "fixture_misses": 0,
                      "capability_failures": 0},
    }


def _not_evaluated() -> dict:
    return {"value": None, "status": "not_evaluated", "evaluated": 0,
            "passed": 0, "total_cases": 0}


def _evaluate(baseline=None, candidate=None, **kwargs):
    kwargs.setdefault("critical_regressions", [])
    kwargs.setdefault("infra_errors", 0)
    kwargs.setdefault("package_ok", True)
    kwargs.setdefault("validation_complete", True)
    kwargs.setdefault("repeats", 3)
    return acceptance.evaluate(baseline or _metrics(),
                               candidate or _metrics(routing=0.85, tool_args=0.80,
                                                     joint=0.72), **kwargs)


def _rule(result: dict, name: str) -> dict:
    return next(rule for rule in result["rules"] if rule["rule"] == name)


# --------------------------------------------------------------------------- #
# accept
# --------------------------------------------------------------------------- #
def test_accept_when_r_and_a_improve_and_joint_does_not_drop():
    result = _evaluate()
    assert result["decision"] == "accept"
    assert result["reason"]
    assert result["rules"]
    for rule in result["rules"]:
        assert set(rule) == {"rule", "ok", "detail"}
        assert isinstance(rule["ok"], bool)
        assert isinstance(rule["detail"], str) and rule["detail"]
    assert all(rule["ok"] for rule in result["rules"])
    assert _rule(result, "R_no_decrease")["ok"] is True
    assert _rule(result, "A_no_decrease")["ok"] is True
    assert _rule(result, "J_no_decrease")["ok"] is True
    assert _rule(result, "strict_improvement")["ok"] is True
    assert _rule(result, "metrics_evaluable")["ok"] is True


def test_accept_when_only_one_core_metric_strictly_improves():
    result = _evaluate(candidate=_metrics(routing=0.85, tool_args=0.75,
                                          joint=0.68))
    assert result["decision"] == "accept"
    assert _rule(result, "A_no_decrease")["ok"] is True
    assert _rule(result, "strict_improvement")["ok"] is True


def test_accept_tolerates_tiny_float_noise():
    result = _evaluate(baseline=_metrics(routing=0.8, tool_args=0.75),
                       candidate=_metrics(routing=0.8 - 1e-12,
                                          tool_args=0.80, joint=0.70))
    assert result["decision"] == "accept"


# --------------------------------------------------------------------------- #
# reject: metric regression / no improvement
# --------------------------------------------------------------------------- #
def test_reject_when_routing_decreases_even_if_arguments_improve():
    result = _evaluate(candidate=_metrics(routing=0.75, tool_args=0.90,
                                          joint=0.75))
    assert result["decision"] == "reject"
    assert _rule(result, "R_no_decrease")["ok"] is False
    assert "R" in result["reason"]


def test_reject_when_tool_arguments_decrease():
    result = _evaluate(candidate=_metrics(routing=0.90, tool_args=0.70,
                                          joint=0.75))
    assert result["decision"] == "reject"
    assert _rule(result, "A_no_decrease")["ok"] is False


def test_reject_when_joint_decreases():
    result = _evaluate(candidate=_metrics(routing=0.85, tool_args=0.80,
                                          joint=0.60))
    assert result["decision"] == "reject"
    assert _rule(result, "J_no_decrease")["ok"] is False


def test_reject_when_all_metrics_are_flat():
    result = _evaluate(candidate=_metrics())
    assert result["decision"] == "reject"
    assert _rule(result, "strict_improvement")["ok"] is False
    assert "持平" in result["reason"] or "未严格提高" in result["reason"]


def test_reject_when_only_joint_improves_but_r_and_a_flat():
    result = _evaluate(candidate=_metrics(routing=0.80, tool_args=0.75,
                                          joint=0.75))
    assert result["decision"] == "reject"
    assert _rule(result, "J_no_decrease")["ok"] is True
    assert _rule(result, "strict_improvement")["ok"] is False


# --------------------------------------------------------------------------- #
# reject: critical regression / infrastructure / package integrity
# --------------------------------------------------------------------------- #
def test_reject_when_critical_case_regresses():
    result = _evaluate(critical_regressions=["case_003"])
    assert result["decision"] == "reject"
    assert _rule(result, "critical_no_regression")["ok"] is False
    assert "case_003" in result["reason"]


def test_reject_when_critical_regressions_given_as_objects():
    result = _evaluate(critical_regressions=[{"case_id": "case_007",
                                              "reason": "路由回归"}])
    assert result["decision"] == "reject"
    assert "case_007" in result["reason"]


def test_reject_when_infrastructure_errors_exist():
    result = _evaluate(infra_errors=2)
    assert result["decision"] == "reject"
    assert _rule(result, "no_infra_errors")["ok"] is False

    result = _evaluate(infra_errors=["auth_failed", "timeout"])
    assert result["decision"] == "reject"


def test_reject_when_package_integrity_failed():
    result = _evaluate(package_ok=False)
    assert result["decision"] == "reject"
    assert _rule(result, "package_integrity")["ok"] is False


def test_hard_violation_wins_over_incomplete_validation():
    result = _evaluate(candidate=_metrics(routing=0.70, tool_args=0.90,
                                          joint=0.72),
                       validation_complete=False)
    assert result["decision"] == "reject"


# --------------------------------------------------------------------------- #
# inconclusive
# --------------------------------------------------------------------------- #
def test_inconclusive_when_validation_incomplete():
    result = _evaluate(validation_complete=False)
    assert result["decision"] == "inconclusive"
    assert "证据不足" in result["reason"]
    assert _rule(result, "validation_complete")["ok"] is False


def test_inconclusive_when_repeats_below_frozen_minimum():
    result = _evaluate(repeats=2)
    assert result["decision"] == "inconclusive"
    assert _rule(result, "repeats_sufficient")["ok"] is False
    assert "2" in _rule(result, "repeats_sufficient")["detail"]


def test_inconclusive_when_candidate_metric_not_evaluated():
    candidate = _metrics(routing=0.85, tool_args=0.80, joint=0.72)
    candidate["tool_args"] = _not_evaluated()
    result = _evaluate(candidate=candidate)
    assert result["decision"] == "inconclusive"
    assert result["decision"] != "accept"
    assert _rule(result, "metrics_evaluable")["ok"] is False
    assert _rule(result, "A_no_decrease")["ok"] is False


def test_inconclusive_when_baseline_metric_missing():
    baseline = _metrics()
    baseline["routing"] = _not_evaluated()
    result = _evaluate(baseline=baseline)
    assert result["decision"] == "inconclusive"
    assert _rule(result, "metrics_evaluable")["ok"] is False


def test_inconclusive_with_missing_metrics_mapping():
    result = acceptance.evaluate(None, None)
    assert result["decision"] == "inconclusive"
    assert _rule(result, "metrics_evaluable")["ok"] is False


def test_inconclusive_when_budget_not_provided_means_validation_incomplete():
    result = _evaluate(validation_complete=None)
    assert result["decision"] == "inconclusive"


# --------------------------------------------------------------------------- #
# contract surface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kwargs,expected", [
    ({"validation_complete": False}, "inconclusive"),
    ({"repeats": 1}, "inconclusive"),
    ({"infra_errors": 1}, "reject"),
    ({"package_ok": False}, "reject"),
    ({"critical_regressions": ["case_1"]}, "reject"),
])
def test_decision_contract_matrix(kwargs, expected):
    result = _evaluate(**kwargs)
    assert set(result) == {"decision", "rules", "reason"}
    assert result["decision"] == expected
    names = {rule["rule"] for rule in result["rules"]}
    assert {"metrics_evaluable", "R_no_decrease", "A_no_decrease",
            "J_no_decrease", "strict_improvement", "critical_no_regression",
            "no_infra_errors", "package_integrity", "validation_complete",
            "repeats_sufficient"} <= names
