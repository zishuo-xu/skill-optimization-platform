"""Frozen candidate acceptance rules (``accept-v1``, requirements §17).

The rule set is deterministic and the thresholds are frozen before the run:
``evaluate`` only compares the paired baseline/candidate metrics it is given.

Decision precedence
-------------------
1. Any core metric (R / A / J) not evaluable on either side -> ``inconclusive``
   (metrics never default to a perfect score).
2. Definitive violations -> ``reject``:
   * R decreases, A decreases or J decreases;
   * a critical case regressed;
   * infrastructure errors happened;
   * the package integrity check failed.
3. Incomplete evidence -> ``inconclusive``: validation not complete or fewer
   than the frozen number of repeats.
4. No strict improvement (R and A both flat) -> ``reject`` (all-flat is a reject).
5. Otherwise -> ``accept``.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

RULES_VERSION = "accept-v1"

# Engineering default from §17: baseline and candidate each run 3 times.
MIN_REPEATS = 3
_EPS = 1e-9

_CORE_METRICS = (
    ("routing", "R", "分发准确率 R"),
    ("tool_args", "A", "工具入参准确率 A"),
    ("joint", "J", "联合通过率 J"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _metric(metrics: Mapping[str, Any] | None, key: str) -> tuple[float | None, str]:
    """Return ``(value, status)``; value is ``None`` unless the metric is ok."""
    if not isinstance(metrics, Mapping):
        return None, "missing"
    block = metrics.get(key)
    if not isinstance(block, Mapping):
        return None, "missing"
    status = block.get("status")
    value = _number(block.get("value"))
    if status is None:
        status = "ok" if value is not None else "not_evaluated"
    if status != "ok" or value is None:
        return None, str(status)
    return value, "ok"


def _pct(value: float | None) -> str:
    if value is None:
        return "未评估"
    return f"{value * 100:.1f}%"


def _delta(before: float, after: float) -> str:
    diff = (after - before) * 100
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.1f}pp"


def _ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        items: Sequence[Any] = [value]
    elif isinstance(value, Mapping):
        items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        items = [value]
    result: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            item = (item.get("case_id") or item.get("id") or item.get("query")
                    or item.get("reason") or "")
        if isinstance(item, bytes):
            item = item.decode("utf-8", "replace")
        text = str(item).strip() if item is not None else ""
        if text:
            result.append(text)
    return result


def _count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, (list, tuple, set, frozenset, Mapping)):
        return len(value)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def evaluate(baseline_metrics: Mapping[str, Any] | None,
             candidate_metrics: Mapping[str, Any] | None, *,
             critical_regressions: Any = (),
             infra_errors: Any = 0,
             package_ok: Any = True,
             validation_complete: Any = True,
             repeats: int = MIN_REPEATS) -> dict[str, Any]:
    """Evaluate the frozen accept-v1 rules.

    ``repeats`` is the actual number of paired validation repeats; fewer than
    :data:`MIN_REPEATS` means the evidence is incomplete, so the decision is
    ``inconclusive`` instead of ``accept``.
    """
    rules: list[dict[str, Any]] = []

    def record(rule: str, ok: bool, detail: str) -> bool:
        rules.append({"rule": rule, "ok": bool(ok), "detail": detail})
        return bool(ok)

    values: dict[str, tuple[float | None, str, float | None, str]] = {}
    for key, label, title in _CORE_METRICS:
        before, before_status = _metric(baseline_metrics, key)
        after, after_status = _metric(candidate_metrics, key)
        values[key] = (before, before_status, after, after_status)
        if before is not None and after is not None:
            record(f"{label}_no_decrease", after >= before - _EPS,
                   f"{title} {_pct(before)} → {_pct(after)}（{_delta(before, after)}）")
        else:
            record(f"{label}_no_decrease", False,
                   f"{title}未评估（基线: {before_status}，候选: {after_status}）")

    evaluable = all(
        values[key][0] is not None and values[key][2] is not None
        for key, _, _ in _CORE_METRICS
    )
    if evaluable:
        detail = "R、A、J 均可评估"
    else:
        missing = [label for key, label, _ in _CORE_METRICS
                   if values[key][0] is None or values[key][2] is None]
        detail = "核心指标未评估: " + "、".join(missing)
    record("metrics_evaluable", evaluable, detail)

    routing_before, _, routing_after, _ = values["routing"]
    args_before, _, args_after, _ = values["tool_args"]
    strict = False
    if evaluable:
        strict = (routing_after > routing_before + _EPS
                  or args_after > args_before + _EPS)
        if strict:
            parts = []
            if routing_after > routing_before + _EPS:
                parts.append(f"R {_pct(routing_before)} → {_pct(routing_after)}")
            if args_after > args_before + _EPS:
                parts.append(f"A {_pct(args_before)} → {_pct(args_after)}")
            detail = "至少一项核心指标严格提高：" + "；".join(parts)
        else:
            detail = (f"R 与 A 均未严格提高（R {_pct(routing_before)} → "
                      f"{_pct(routing_after)}，A {_pct(args_before)} → {_pct(args_after)}）")
    else:
        detail = "R 或 A 未评估，无法判断是否严格提高"
    record("strict_improvement", strict, detail)

    critical_ids = _ids(critical_regressions)
    record("critical_no_regression", not critical_ids,
           "无 critical 案例回归" if not critical_ids
           else "critical 案例从通过变失败: " + "、".join(critical_ids[:20]))

    infra_count = _count(infra_errors)
    record("no_infra_errors", infra_count == 0,
           "无基础设施错误" if infra_count == 0
           else f"存在 {infra_count} 个基础设施错误，阻止自动晋级")

    record("package_integrity", package_ok is True,
           "包完整性检查通过" if package_ok is True
           else "包完整性检查未通过或未知")

    record("validation_complete", validation_complete is True,
           "验证流程完整" if validation_complete is True
           else "验证未完整完成，证据不足")

    try:
        repeat_count = int(repeats)
    except (TypeError, ValueError):
        repeat_count = 0
    record("repeats_sufficient", repeat_count >= MIN_REPEATS,
           f"配对重复 {repeat_count} 次（要求 ≥ {MIN_REPEATS}）"
           if repeat_count >= MIN_REPEATS
           else f"配对重复仅 {repeat_count} 次，少于冻结要求 {MIN_REPEATS} 次")

    rule_ok = {rule["rule"]: rule["ok"] for rule in rules}
    failed_rules = [rule for rule in rules if not rule["ok"]]

    if not rule_ok.get("metrics_evaluable"):
        decision = "inconclusive"
        reason = ("证据不足：核心指标未评估（"
                  + "；".join(rule["detail"] for rule in failed_rules
                             if rule["rule"] == "metrics_evaluable")
                  + "）。不允许把未评估当作满分。")
    elif not (rule_ok.get("R_no_decrease") and rule_ok.get("A_no_decrease")
              and rule_ok.get("J_no_decrease")):
        decision = "reject"
        reason = "拒绝：" + "；".join(rule["detail"] for rule in failed_rules
                                    if rule["rule"] in {"R_no_decrease", "A_no_decrease",
                                                         "J_no_decrease"}) + "。"
    elif not rule_ok.get("critical_no_regression"):
        decision = "reject"
        reason = "拒绝：" + next(rule["detail"] for rule in failed_rules
                                if rule["rule"] == "critical_no_regression") + "。"
    elif not rule_ok.get("no_infra_errors"):
        decision = "reject"
        reason = "拒绝：" + next(rule["detail"] for rule in failed_rules
                                if rule["rule"] == "no_infra_errors") + "。"
    elif not rule_ok.get("package_integrity"):
        decision = "reject"
        reason = "拒绝：" + next(rule["detail"] for rule in failed_rules
                                if rule["rule"] == "package_integrity") + "。"
    elif not (rule_ok.get("validation_complete") and rule_ok.get("repeats_sufficient")):
        decision = "inconclusive"
        details = [rule["detail"] for rule in failed_rules
                   if rule["rule"] in {"validation_complete", "repeats_sufficient"}]
        reason = "证据不足：" + "；".join(details) + "。"
    elif not rule_ok.get("strict_improvement"):
        decision = "reject"
        reason = ("拒绝：R 与 A 均未严格提高（全部指标持平不构成改进），"
                  "未达到 accept-v1 的晋级条件。")
    else:
        decision = "accept"
        reason = ("符合 accept-v1：R、A 均未下降且至少一项严格提高，J 未下降；"
                  "无 critical 回归、基础设施错误与包完整性错误，验证完整。")

    return {"decision": decision, "rules": rules, "reason": reason}


__all__ = ["RULES_VERSION", "MIN_REPEATS", "evaluate"]
