"""Single-round pipeline: baseline evaluation, one candidate, retest, decision.

Stages (§18):

``preflight → baseline_eval → error_analysis → candidate_generation →
static_check → retest → decision``

Baseline runs only execute ``baseline_eval``; final-test runs only execute
``final_test``.  The worker has already claimed the run (status ``running``)
before :func:`execute_run` is called.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import config, event_types, evaluation, execution, hashing, persistence, patch
from . import optimizer, services, skill_package
from .adapters.registry import build_adapter
from .errors import AppError
from .run_events import EventEmitter

log = logging.getLogger(__name__)

MAX_CONCURRENCY = 8


class RunCancelled(Exception):
    pass


class BudgetExhausted(Exception):
    pass


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def execute_run(run_id: str) -> None:
    run = persistence.get_run(run_id)
    emitter = EventEmitter(run_id, run.get("stage"))
    try:
        _execute(run, emitter)
    except RunCancelled:
        emitter.issue("run_cancelled", error_code="cancelled",
                      title="运行已取消", message="用户取消了本次运行，已停止派发新案例。",
                      impact="已产生的费用不会回退；在途请求已尽力终止。",
                      next_action="如仍需优化，请新建运行。", level="warning")
        persistence.finish_run(run_id, status="cancelled",
                               decision_reason="用户取消运行",
                               metrics=run.get("metrics") or {})
    except BudgetExhausted as exc:
        emitter.issue("budget_exceeded", error_code="budget_exceeded",
                      title="预算已用尽", message=str(exc),
                      impact="停止派发新案例，验证可能不完整，不能接受候选。",
                      next_action="提高预算或缩小数据集后新建运行。", level="error")
        persistence.finish_run(run_id, status="completed", decision="inconclusive",
                               decision_reason=f"证据不足：预算用尽（{exc}）",
                               stage="decision")
    except Exception as exc:  # noqa: BLE001 - persisted for the run log
        log.exception("pipeline failed: %s", run_id)
        emitter.issue("run_failed", error_code="platform_error",
                      title="运行执行失败",
                      message=f"{type(exc).__name__}: {exc}",
                      impact="本次运行未产生有效结论。",
                      next_action="查看技术详情并重试；如重复出现请提交缺陷。",
                      level="error",
                      technical_detail={"traceback": traceback.format_exc(limit=10)})
        try:
            persistence.finish_run(run_id, status="failed",
                                   error_code="platform_error",
                                   error_message=f"{type(exc).__name__}: {exc}",
                                   decision_reason="运行执行失败")
        except Exception:
            log.exception("failed to persist run failure")


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #
def _execute(run: Mapping[str, Any], emitter: EventEmitter) -> None:
    run = persistence.get_run(run["id"])
    if run["status"] in config.TERMINAL_RUN_STATUSES:
        return
    project = persistence.get_project(run["project_id"])
    env = persistence.get_environment(run["environment_id"])
    dataset = persistence.get_dataset(run["dataset_id"])
    cases = persistence.all_cases(dataset["id"])
    if not cases:
        raise AppError("数据集为空，无法运行")
    adapter = build_adapter(env.get("model_config") or {})
    budget = _Budget(env.get("budget_config") or {})
    cancel = _CancellationToken(run["id"])
    started = time.monotonic()
    emitter.emit(event_types.RUN_STARTED, {
        "mode": run["mode"], "baseline_skill_version_id": run["baseline_skill_version_id"],
        "environment_id": env["id"], "dataset_id": dataset["id"],
        "baseline_cache_hit": bool(run.get("baseline_cache_hit")),
        "budget": budget.describe(),
    })
    if run["mode"] == "baseline":
        _run_baseline(run, env, dataset, cases, adapter, emitter, budget, cancel, started)
    elif run["mode"] == "final_test":
        _run_final_test(run, env, dataset, cases, adapter, emitter, budget, cancel,
                        started)
    else:
        _run_optimize(run, env, dataset, cases, adapter, emitter, budget, cancel,
                      started)


# --------------------------------------------------------------------------- #
# baseline-only run
# --------------------------------------------------------------------------- #
def _run_baseline(run: Mapping[str, Any], env: Mapping[str, Any],
                  dataset: Mapping[str, Any], cases: Sequence[Mapping[str, Any]],
                  adapter: Any, emitter: EventEmitter, budget: "_Budget",
                  cancel: "_CancellationToken", started: float) -> None:
    optimize_cases = [case for case in cases
                      if (case.get("split") or "optimize") == "optimize"]
    if not optimize_cases:
        raise AppError("数据集没有优化集案例，无法运行基线")
    _preflight(run, env, dataset, cases, emitter, mode="baseline")
    _stage(emitter, "baseline_eval", "执行优化集基线评测")
    metrics = _evaluate_split(
        run, env, optimize_cases, phase="baseline", repeats=1, adapter=adapter,
        emitter=emitter, budget=budget, cancel=cancel)
    cost = _cost(metrics)
    persistence.finish_run(
        run["id"], status="completed", decision=None,
        decision_reason="基线评测完成",
        metrics={"baseline": metrics, "candidate": None,
                 "delta": None, "validation": None, "final": None},
        cost=cost, stage="decision")
    emitter.set_stage("decision", message="基线评测完成")
    emitter.emit(event_types.RUN_COMPLETED, {
        "mode": "baseline", "metrics": _metric_summary(metrics),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }, level="info")


# --------------------------------------------------------------------------- #
# final-test run
# --------------------------------------------------------------------------- #
def _run_final_test(run: Mapping[str, Any], env: Mapping[str, Any],
                    dataset: Mapping[str, Any], cases: Sequence[Mapping[str, Any]],
                    adapter: Any, emitter: EventEmitter, budget: "_Budget",
                    cancel: "_CancellationToken", started: float) -> None:
    final_cases = [case for case in cases
                   if (case.get("split") or "optimize") == "final_test"]
    if not final_cases:
        raise AppError("数据集没有最终测试集案例")
    _preflight(run, env, dataset, cases, emitter, mode="final_test")
    _stage(emitter, "final_test", "执行最终测试集")
    repeats = 1
    metrics = _evaluate_split(
        run, env, final_cases, phase="final", repeats=repeats, adapter=adapter,
        emitter=emitter, budget=budget, cancel=cancel)
    cost = _cost(metrics)
    persistence.finish_run(
        run["id"], status="completed", decision=None,
        decision_reason="最终测试集评测完成（仅用于阶段验收，不参与候选选择）",
        metrics={"final": metrics, "baseline": None, "candidate": None,
                 "delta": None, "validation": None},
        cost=cost, stage="decision")
    emitter.set_stage("decision", message="最终测试完成")
    emitter.emit(event_types.RUN_COMPLETED, {
        "mode": "final_test", "metrics": _metric_summary(metrics),
        "duration_ms": int((time.monotonic() - started) * 1000),
    })


# --------------------------------------------------------------------------- #
# optimize run
# --------------------------------------------------------------------------- #
def _run_optimize(run: Mapping[str, Any], env: Mapping[str, Any],
                  dataset: Mapping[str, Any], cases: Sequence[Mapping[str, Any]],
                  adapter: Any, emitter: EventEmitter, budget: "_Budget",
                  cancel: "_CancellationToken", started: float) -> None:
    run_id = run["id"]
    optimize_cases = [case for case in cases
                      if (case.get("split") or "optimize") == "optimize"]
    validation_cases = [case for case in cases
                        if (case.get("split") or "optimize") == "validation"]
    if not optimize_cases or not validation_cases:
        raise AppError("优化运行需要优化集与验证集")
    _preflight(run, env, dataset, cases, emitter, mode="optimize")

    # -- baseline ---------------------------------------------------------
    cached_rows = persistence.get_case_results_for_run(run_id, "baseline")
    cached_case_ids = {row["case_id"] for row in cached_rows}
    if cached_rows and cached_case_ids >= {case["case_id"] for case in optimize_cases}:
        emitter.set_stage("baseline_eval", message="复用兼容基线结果")
        emitter.emit(event_types.STAGE_STARTED, {
            "message": "复用兼容基线", "baseline_run_id": run.get("baseline_run_id"),
            "cases": len(cached_rows)}, stage="baseline_eval")
        baseline_metrics = _metrics_from_rows(run, optimize_cases, cached_rows,
                                              planned=len(optimize_cases))
        emitter.emit(event_types.STAGE_COMPLETED, {
            "metrics": _metric_summary(baseline_metrics), "cache_hit": True,
        }, stage="baseline_eval")
    else:
        _stage(emitter, "baseline_eval", "执行优化集基线评测")
        baseline_metrics = _evaluate_split(
            run, env, optimize_cases, phase="baseline", repeats=1,
            adapter=adapter, emitter=emitter, budget=budget, cancel=cancel)

    # -- error analysis ---------------------------------------------------
    _stage(emitter, "error_analysis", "聚合失败证据（仅优化集）")
    baseline_rows = persistence.get_case_results_for_run(run_id, "baseline")
    failures = _build_failures(run_id, optimize_cases, baseline_rows)
    passing = [row for row in baseline_rows
               if (row.get("scores") or {}).get("passed")]
    emitter.emit(event_types.STAGE_COMPLETED, {
        "failure_count": len(failures),
        "passing_count": len(passing),
        "categories": _failure_categories(failures),
    }, stage="error_analysis")
    if not failures:
        _finish_inconclusive(
            run, emitter, "优化集没有失败案例，无法生成有证据支持的候选。",
            error_code="no_failures",
            metrics={"baseline": baseline_metrics, "candidate": None,
                     "delta": None, "validation": None, "final": None})
        return

    # -- candidate generation --------------------------------------------
    _stage(emitter, "candidate_generation", "生成单个候选补丁")
    skill_version = persistence.get_skill_version(run["baseline_skill_version_id"])
    files = _skill_files(skill_version["id"])
    manifest = skill_version.get("manifest") or {}
    context = {
        "project": {"id": run["project_id"], "name": (run.get("snapshot") or {}).get(
            "project", {}).get("name", "")},
        "baseline_skill_version": {
            "id": skill_version["id"], "manifest": manifest,
            "files": {path: data.decode("utf-8", errors="replace")
                      for path, data in files.items()
                      if path.lower().endswith((".md", ".markdown"))},
        },
        "allowed_paths": sorted(path for path in files
                                if path.lower().endswith((".md", ".markdown"))),
        "failures": failures,
        "passing_summary": {"count": len(passing),
                            "sample_case_ids": [row["case_id"] for row in passing[:10]]},
        "budget_remaining": {"tokens": budget.remaining_tokens()},
        "optimizer_config": env.get("optimizer_config") or {},
        "model_config": env.get("optimizer_config") or {},
        "rules_version": config.ACCEPTANCE_RULES_VERSION,
        "scorer_version": (env.get("scorer_config") or {}).get(
            "version", config.SCORER_VERSION),
    }
    optimizer_adapter = None
    opt_config = env.get("optimizer_config") or {}
    if opt_config.get("provider") not in (None, "", "heuristic", "none"):
        try:
            optimizer_adapter = build_adapter(opt_config)
        except AppError as exc:
            optimizer_adapter = None
            emitter.issue(event_types.OPTIMIZER_NOT_CONFIGURED,
                          error_code="optimizer_not_configured",
                          title="优化器适配器不可用",
                          message=exc.message,
                          impact="本轮将使用启发式回退。",
                          next_action="修复执行环境的优化模型配置后新建运行。",
                          level="warning", stage="candidate_generation")
    if cancel.cancelled():
        raise RunCancelled()
    budget.ensure_available()
    if optimizer_adapter is not None:
        optimizer_adapter = _ControlledOptimizer(optimizer_adapter, cancel, budget)
    outcome = optimizer.generate_candidate(
        context, optimizer_adapter, emitter.emit, budget=budget.remaining_tokens())
    if cancel.cancelled():
        raise RunCancelled()
    budget.ensure_available()
    if outcome.get("status") != "ok":
        _finish_inconclusive(
            run, emitter,
            f"候选未生成：{outcome.get('error_code') or 'no_patch'}",
            error_code=outcome.get("error_code") or "no_patch",
            metrics={"baseline": baseline_metrics, "candidate": None,
                     "delta": None, "validation": None, "final": None})
        return

    # -- apply patch + static check --------------------------------------
    _stage(emitter, "static_check", "应用补丁并执行静态检查")
    edits = (outcome.get("patch") or {}).get("edits") or []
    patched_files, issues = patch.apply_edits(files, edits, context["allowed_paths"])
    candidate_row = None
    if issues:
        emitter.issue(event_types.STATIC_CHECK_FAILED,
                      error_code="static_check_failed",
                      title="补丁应用失败",
                      message="补丁无法唯一命中目标文件或包含非法操作。",
                      impact="本轮不生成候选版本，不进入复测。",
                      next_action="检查候补丁与 Skill 文件版本后新建运行。",
                      level="error", stage="static_check",
                      extra={"issues": issues})
        static_result = {"ok": False, "checks": [], "errors": issues}
        _finish_inconclusive(
            run, emitter, "补丁应用失败，未进入复测",
            error_code="static_check_failed",
            metrics={"baseline": baseline_metrics, "candidate": None,
                     "delta": None, "validation": None, "final": None})
        _record_candidate(run_id, None, outcome, checks=static_result)
        return
    all_case_ids = [case["case_id"] for case in cases]
    static_result = patch.static_check(files, patched_files, manifest, all_case_ids)
    emitter.emit(event_types.STATIC_CHECK_STARTED, {"checks": static_result.get("checks")},
                 stage="static_check")
    if not static_result.get("ok"):
        emitter.issue(event_types.STATIC_CHECK_FAILED,
                      error_code="static_check_failed",
                      title="静态检查未通过",
                      message="候选包存在路径、引用、结构或答案硬编码问题。",
                      impact="本轮候选被拒绝，不进入复测。",
                      next_action="查看检查项并修正优化器输出。",
                      level="error", stage="static_check",
                      extra={"checks": static_result.get("checks")})
        _record_candidate(run_id, None, outcome, checks=static_result)
        _finish_inconclusive(
            run, emitter, "静态检查未通过，未进入复测",
            error_code="static_check_failed",
            metrics={"baseline": baseline_metrics, "candidate": None,
                     "delta": None, "validation": None, "final": None})
        return
    emitter.emit(event_types.STATIC_CHECK_PASSED,
                 {"checks": static_result.get("checks")}, stage="static_check")
    candidate_version = persistence.create_skill_version(
        run["project_id"], patched_files, manifest,
        parent_id=run["baseline_skill_version_id"], status="candidate",
        message=(outcome.get("patch") or {}).get("summary") or "候选补丁",
        source_run_id=run_id, set_current=False)
    integrity = persistence.skill_version_integrity(candidate_version["id"])
    candidate = _record_candidate(run_id, candidate_version["id"], outcome,
                                  checks=static_result)
    persistence.update_run(run_id, candidate_skill_version_id=candidate_version["id"])
    emitter.emit(event_types.PATCH_APPLIED, {
        "candidate_skill_version_id": candidate_version["id"],
        "changed_files": outcome.get("changed_files") or [],
        "integrity": integrity,
    }, stage="static_check")
    run = persistence.get_run(run_id)

    # -- retest -----------------------------------------------------------
    _stage(emitter, "retest", "复测候选并执行配对验证")
    candidate_optimize = _evaluate_split(
        run, env, optimize_cases, phase="candidate", repeats=1,
        adapter=adapter, emitter=emitter, budget=budget, cancel=cancel,
        skill_version_id=candidate_version["id"])
    repeats = _validation_repeats(env)
    validation_complete = True
    for repeat in range(1, repeats + 1):
        try:
            _evaluate_split(
                run, env, validation_cases, phase="validation_baseline",
                repeats=1, adapter=adapter, emitter=emitter, budget=budget,
                cancel=cancel, repeat_index=repeat)
            _evaluate_split(
                run, env, validation_cases, phase="validation_candidate",
                repeats=1, adapter=adapter, emitter=emitter, budget=budget,
                cancel=cancel, skill_version_id=candidate_version["id"],
                repeat_index=repeat)
        except BudgetExhausted as exc:
            validation_complete = False
            emitter.issue(event_types.BUDGET_EXCEEDED,
                          error_code="budget_exceeded",
                          title="验证复测预算不足",
                          message=str(exc),
                          impact="验证不完整，本轮只能给出证据不足结论。",
                          next_action="提高预算后新建运行。",
                          level="error", stage="retest")
            break
    rows = persistence.get_case_results_for_run(run_id)
    validation_baseline_rows = [row for row in rows
                                if row["phase"] == "validation_baseline"]
    validation_candidate_rows = [row for row in rows
                                 if row["phase"] == "validation_candidate"]
    expected_validation = len(validation_cases) * repeats * 2
    if len(validation_baseline_rows) + len(validation_candidate_rows) < expected_validation:
        validation_complete = False
    validation_baseline = _metrics_from_rows(
        run, validation_cases, validation_baseline_rows,
        planned=len(validation_cases) * repeats)
    validation_candidate = _metrics_from_rows(
        run, validation_cases, validation_candidate_rows,
        planned=len(validation_cases) * repeats)
    critical_regressions = _critical_regressions(
        validation_cases, validation_baseline_rows, validation_candidate_rows)
    infra_errors = _infra_errors(validation_baseline_rows) + \
        _infra_errors(validation_candidate_rows)
    package_ok = bool(static_result.get("ok") and integrity.get("ok"))

    # -- decision ---------------------------------------------------------
    _stage(emitter, "decision", "按冻结规则判定候选")
    from . import acceptance

    result = acceptance.evaluate(
        validation_baseline, validation_candidate,
        critical_regressions=critical_regressions,
        infra_errors=infra_errors,
        package_ok=package_ok,
        validation_complete=validation_complete,
        repeats=repeats)
    decision = result["decision"]
    for rule in result.get("rules") or []:
        emitter.emit(event_types.RULE_EVALUATED, dict(rule), stage="decision",
                     level="info")
    if critical_regressions:
        emitter.emit(event_types.REGION_REGRESSION, {
            "case_ids": critical_regressions,
        }, level="warning", stage="decision")
    persistence.create_decision(
        run_id, decision, config.ACCEPTANCE_RULES_VERSION,
        {"rules": result.get("rules"), "reason": result.get("reason"),
         "validation": {"baseline": _metric_summary(validation_baseline),
                        "candidate": _metric_summary(validation_candidate)},
         "repeats": repeats})
    emitter.emit(event_types.DECISION_MADE, {
        "decision": decision, "reason": result.get("reason"),
        "rules": result.get("rules"),
    }, level="info" if decision == "accept" else "warning", stage="decision")
    metrics = {
        "baseline": baseline_metrics,
        "candidate": candidate_optimize,
        "delta": services.delta_metrics(baseline_metrics, candidate_optimize),
        "validation": {
            "baseline": validation_baseline,
            "candidate": validation_candidate,
            "delta": services.delta_metrics(validation_baseline,
                                            validation_candidate),
            "repeats": repeats,
            "complete": validation_complete,
            "critical_regressions": critical_regressions,
        },
        "final": None,
    }
    persistence.finish_run(
        run_id, status="completed", decision=decision,
        decision_reason=result.get("reason") or "",
        metrics=metrics, cost=_cost(candidate_optimize),
        candidate_skill_version_id=candidate_version["id"], stage="decision")
    emitter.emit(event_types.RUN_COMPLETED, {
        "decision": decision,
        "metrics": {
            "baseline": _metric_summary(baseline_metrics),
            "candidate": _metric_summary(candidate_optimize),
            "validation": {
                "baseline": _metric_summary(validation_baseline),
                "candidate": _metric_summary(validation_candidate),
            },
        },
        "duration_ms": int((time.monotonic() - started) * 1000),
    }, level="info")


# --------------------------------------------------------------------------- #
# stage helpers
# --------------------------------------------------------------------------- #
def _stage(emitter: EventEmitter, stage: str, message: str, total: int = 0) -> None:
    emitter.set_stage(stage, message=message, total=total)
    emitter.emit(event_types.STAGE_STARTED, {"message": message, "total": total},
                 stage=stage)


def _finish_inconclusive(run: Mapping[str, Any], emitter: EventEmitter,
                         reason: str, *, error_code: str,
                         metrics: Mapping[str, Any]) -> None:
    decision = "inconclusive"
    persistence.create_decision(
        run["id"], decision, config.ACCEPTANCE_RULES_VERSION,
        {"reason": reason, "error_code": error_code, "rules": []})
    emitter.emit(event_types.DECISION_MADE, {
        "decision": decision, "reason": reason, "error_code": error_code,
    }, level="info" if error_code == "no_failures" else "warning", stage="decision")
    persistence.finish_run(run["id"], status="completed", decision=decision,
                           decision_reason=reason, metrics=metrics,
                           stage="decision")


def _record_candidate(run_id: str, skill_version_id: str | None,
                      outcome: Mapping[str, Any],
                      checks: Mapping[str, Any]) -> dict[str, Any]:
    return persistence.create_candidate(
        run_id, skill_version_id=skill_version_id,
        diagnosis=outcome.get("diagnosis") or {},
        hypotheses=outcome.get("hypotheses") or [],
        changed_files=outcome.get("changed_files") or [],
        patch=outcome.get("patch") or {},
        expected_effect=outcome.get("expected_effect") or "",
        risk_cases=outcome.get("risk_cases") or [],
        checks=checks)


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #
def _preflight(run: Mapping[str, Any], env: Mapping[str, Any],
               dataset: Mapping[str, Any], cases: Sequence[Mapping[str, Any]],
               emitter: EventEmitter, *, mode: str) -> None:
    emitter.set_stage("preflight", message="检查文件、映射、环境、数据、预算与模拟覆盖")
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, *, level: str = "info",
              next_action: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        payload = {"name": name, "ok": ok, "detail": detail}
        if ok:
            emitter.emit(event_types.PREFLIGHT_ITEM, payload, stage="preflight")
        else:
            emitter.issue(event_types.PREFLIGHT_FAILED,
                          error_code="preflight_failed",
                          title=f"预检失败：{name}", message=detail,
                          impact="本次运行无法继续。",
                          next_action=next_action or "修复配置后重新启动运行。",
                          level="error" if level == "error" else "warning",
                          stage="preflight", extra=payload)

    integrity = persistence.skill_version_integrity(run["baseline_skill_version_id"])
    check("skill_integrity", integrity["ok"],
          "Skill 包文件哈希校验通过" if integrity["ok"]
          else "；".join(integrity["errors"]), level="error",
          next_action="重新导入或发布 Skill 包版本。")
    check("environment_tools", bool(env.get("tool_schema")),
          f"工具协议包含 {len(env.get('tool_schema') or [])} 个工具", level="error",
          next_action="在执行环境配置工具协议并发布版本。")
    splits = {case.get("split") or "optimize" for case in cases}
    required = {"optimize"} if mode == "baseline" else (
        {"final_test"} if mode == "final_test" else {"optimize", "validation"})
    check("dataset_splits", required <= splits,
          f"数据划分: {', '.join(sorted(splits))}", level="error",
          next_action="补充缺少的数据划分并发布新数据集版本。")
    budget_config = env.get("budget_config") or {}
    check("budget_configured",
          bool(budget_config.get("max_tokens") or budget_config.get("max_cost")
               or budget_config.get("max_seconds")),
          "预算上限已冻结" if budget_config else "未设置预算上限", level="error",
          next_action="在执行环境配置 Token/金额/时长上限。")
    relevant_splits = {"optimize"} if mode == "baseline" else (
        {"final_test"} if mode == "final_test" else {"optimize", "validation"})
    fixture_cases = _fixture_gap_cases(
        [case for case in cases
         if (case.get("split") or "optimize") in relevant_splits])
    if fixture_cases:
        emitter.issue("fixture_coverage_warning",
                      error_code="fixture_miss",
                      title="部分业务调用缺少模拟返回",
                      message=f"{len(fixture_cases)} 个案例的预期工具调用没有对应 fixture。",
                      impact="这些案例会按 fixture_miss 能力失败计分，不会访问真实服务。",
                      next_action="补充 fixtures 后发布新数据集版本。",
                      level="warning", stage="preflight",
                      extra={"case_ids": fixture_cases[:20]})
    else:
        check("fixture_coverage", True, "所有预期业务调用都有模拟返回")
    acceptance = run.get("acceptance") or {}
    check("acceptance_frozen", bool(acceptance.get("rules_version")),
          f"接受规则版本: {acceptance.get('rules_version')}", level="warning")
    emitter.emit(event_types.STAGE_COMPLETED, {"checks": checks},
                 stage="preflight")


def _fixture_gap_cases(cases: Sequence[Mapping[str, Any]]) -> list[str]:
    missing: list[str] = []
    for case in cases:
        expected_calls = (case.get("expected") or {}).get("tool_calls") or []
        fixtures = case.get("fixtures") or []
        names = {fixture.get("name") for fixture in fixtures
                 if isinstance(fixture, Mapping)}
        for call in expected_calls:
            if call.get("name") and call["name"] not in names:
                missing.append(case["case_id"])
                break
    return missing


# --------------------------------------------------------------------------- #
# case evaluation
# --------------------------------------------------------------------------- #
def _evaluate_split(run: Mapping[str, Any], env: Mapping[str, Any],
                    cases: Sequence[Mapping[str, Any]], *, phase: str,
                    repeats: int, adapter: Any, emitter: EventEmitter,
                    budget: "_Budget", cancel: "_CancellationToken",
                    skill_version_id: str | None = None,
                    repeat_index: int | None = None) -> dict[str, Any]:
    run_id = run["id"]
    skill_version_id = skill_version_id or run["baseline_skill_version_id"]
    manifest = persistence.get_skill_version(skill_version_id).get("manifest") or {}
    run_config = env.get("run_config") or {}
    loader_config = env.get("loader_config") or {}
    concurrency = max(1, min(int(run_config.get("concurrency") or 3), MAX_CONCURRENCY))
    cases_by_id = {case["case_id"]: case for case in cases}
    repeat_indices = [repeat_index] if repeat_index is not None else list(
        range(1, repeats + 1))
    total = len(cases) * len(repeat_indices)
    completed = 0
    started = time.monotonic()
    usage_total: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    duration_total = 0
    rows: list[dict[str, Any]] = []

    def emit_mapping(event: Mapping[str, Any]) -> dict[str, Any]:
        return emitter.emit(
            str(event.get("type") or "event"),
            event.get("payload") or {},
            level=str(event.get("level") or "info"),
            stage=event.get("stage") or emitter.stage,
            case_id=event.get("case_id"), split=event.get("split"),
            variant=event.get("variant"), repeat_index=event.get("repeat_index"),
            attempt=event.get("attempt"), call_id=event.get("call_id"))

    def run_one(case: Mapping[str, Any], repeat: int) -> dict[str, Any]:
        emitter.case_started(case["case_id"])
        request_payload = _case_request(run, env, case, skill_version_id,
                                        manifest, loader_config, run_config, cancel)
        request_payload.update(stage=emitter.stage, split=case.get("split"),
                               variant=case.get("variant") or "main", repeat_index=repeat)
        if budget.max_seconds is not None:
            request_payload["model_config"] = {
                **request_payload["model_config"],
                "deadline_monotonic": budget.started + float(budget.max_seconds),
            }
        try:
            actual = execution.run_case(request_payload, adapter, emit_mapping)
        except Exception as exc:  # noqa: BLE001 - isolate case failures
            log.exception("case execution crashed: %s", case["case_id"])
            actual = {
                "selected_skills": [], "route_events": [], "action": "no_action",
                "tool_calls": [], "missing_fields": [],
                "error": {"code": "platform_error",
                          "message": f"{type(exc).__name__}: {exc}"},
                "error_kind": "infrastructure", "finish_reason": "error",
                "usage": {}, "duration_ms": 0, "result_source": "runtime_event",
                "capabilities": {"routing": True, "tool_arguments": True},
                "events": [], "fixture_misses": [],
            }
        score = evaluation.score_case(
            case.get("expected") or {}, actual, env.get("tool_schema") or [],
            (env.get("scorer_config") or {}).get("compare"))
        persistence.save_case_result(
            run_id, case["case_id"], phase, variant="main", repeat_index=repeat,
            split=case.get("split") or phase,
            selected_skills=actual.get("selected_skills") or [],
            action=actual.get("action"),
            tool_calls=actual.get("tool_calls") or [],
            missing_fields=actual.get("missing_fields") or [],
            actual=actual, scores=score,
            error_code=(actual.get("error") or {}).get("code"),
            error_message=(actual.get("error") or {}).get("message"),
            result_source=actual.get("result_source"),
            capabilities=actual.get("capabilities") or {},
            usage=actual.get("usage") or {},
            duration_ms=actual.get("duration_ms"))
        case_emitter_payload = {
            "case_id": case["case_id"], "phase": phase, "repeat_index": repeat,
            "passed": score.get("passed"), "status": score.get("status"),
            "routing": score.get("routing"), "tool_args": score.get("tool_args"),
            "joint": score.get("joint"), "error_kind": score.get("error_kind"),
            "duration_ms": actual.get("duration_ms"),
        }
        if score.get("status") == "fail" or score.get("status") == "invalid":
            # An ordinary metric failure is a *result*, not an operational issue:
            # keep it as an info record so the issue summary only surfaces
            # problems that actually affect the run (§44).
            emitter.emit(event_types.CASE_FAILED, {
                **case_emitter_payload,
                "failure_reason": _score_reason(score),
                "diagnostic_count": len(score.get("diagnostics") or []),
            }, level="info", case_id=case["case_id"],
                split=case.get("split"), repeat_index=repeat)
        elif score.get("status") == "infra":
            emitter.issue(event_types.CASE_FAILED,
                          error_code=(actual.get("error") or {}).get("code")
                          or "platform_error",
                          title="案例基础设施错误",
                          message=_score_reason(score),
                          impact="该案例不计入指标分母，但会阻止自动晋级。",
                          next_action="检查模型/网关连通性或凭据后重试。",
                          case_id=case["case_id"], split=case.get("split"),
                          repeat_index=repeat)
        else:
            emitter.emit(event_types.CASE_COMPLETED, case_emitter_payload,
                         case_id=case["case_id"], split=case.get("split"),
                         repeat_index=repeat)
        emitter.case_finished(case["case_id"])
        return {"case": case, "score": score, "actual": actual}

    executor = ThreadPoolExecutor(max_workers=concurrency,
                                  thread_name_prefix=f"case-{phase}")
    futures = {}
    for repeat in repeat_indices:
        for case in cases:
            if cancel.cancelled():
                break
            try:
                budget.ensure_available()
            except BudgetExhausted:
                cancel.set_budget_exhausted()
                break
            futures[executor.submit(run_one, case, repeat)] = (case, repeat)
        if cancel.cancelled() or cancel.budget_exhausted():
            break
    try:
        for future in as_completed(futures):
            try:
                entry = future.result()
            except Exception as exc:  # noqa: BLE001 - one case must not kill the run
                log.exception("case future failed")
                emitter.issue(event_types.CASE_FAILED,
                              error_code="platform_error",
                              title="案例执行异常",
                              message=f"{type(exc).__name__}: {exc}",
                              impact="该案例按基础设施错误处理。",
                              next_action="查看技术详情后重试。",
                              level="error")
                completed += 1
                emitter.progress(completed=completed, total=total)
                continue
            rows.append(entry)
            usage = entry["actual"].get("usage") or {}
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    usage_total[key] += int(value)
            duration_total += int(entry["actual"].get("duration_ms") or 0)
            budget.consume(usage)
            completed += 1
            emitter.progress(completed=completed, total=total)
    finally:
        executor.shutdown(wait=True)
    if cancel.cancelled():
        raise RunCancelled()
    if cancel.budget_exhausted():
        raise BudgetExhausted("运行预算已用尽，部分案例未执行")
    metrics = evaluation.aggregate_metrics(
        [(entry["case"], entry["score"]) for entry in rows],
        planned=len(cases) * len(repeat_indices),
        duration_ms=duration_total,
        usage=usage_total,
        cost=_cost_value(usage_total),
        fixture_misses=sum(len(entry["actual"].get("fixture_misses") or [])
                           for entry in rows))
    emitter.emit(event_types.STAGE_COMPLETED, {
        "phase": phase, "count": len(rows), "metrics": _metric_summary(metrics),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }, stage=emitter.stage)
    return metrics


def _case_request(run: Mapping[str, Any], env: Mapping[str, Any],
                  case: Mapping[str, Any], skill_version_id: str,
                  manifest: Mapping[str, Any], loader_config: Mapping[str, Any],
                  run_config: Mapping[str, Any], cancel: Any) -> dict[str, Any]:
    return {
        "query": case["query"],
        "context": case.get("context") or {},
        "skill_snapshot_path": str(persistence.skill_files_dir(skill_version_id)),
        "system_prompt": env["prompt"],
        "model_config": env.get("model_config") or {},
        "request_template": (env.get("model_config") or {}).get("request_template"),
        "tool_schema": env.get("tool_schema") or [],
        "fixtures": case.get("fixtures") or [],
        "limits": {
            **loader_config,
            "max_model_calls": int(run_config.get("max_model_calls") or 12),
            "max_steps": int(loader_config.get("max_steps") or 8),
            "case_timeout_s": int(run_config.get("case_timeout_s") or 120),
        },
        "loader_config": dict(loader_config),
        "run_id": run["id"],
        "case_id": case["case_id"],
        "manifest": manifest,
        "allowed_skill_ids": [item["skill_id"]
                              for item in manifest.get("subskills") or []],
        "cancel_check": cancel.cancelled,
    }


def _metrics_from_rows(run: Mapping[str, Any], cases: Sequence[Mapping[str, Any]],
                       rows: Sequence[Mapping[str, Any]], *,
                       planned: int | None = None) -> dict[str, Any]:
    cases_by_id = {case["case_id"]: case for case in cases}
    pairs = []
    usage_total: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    duration = 0
    for row in rows:
        case = cases_by_id.get(row["case_id"])
        if case is None:
            continue
        pairs.append((case, row.get("scores") or {}))
        usage = row.get("usage") or {}
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                usage_total[key] += int(value)
        duration += int(row.get("duration_ms") or 0)
    return evaluation.aggregate_metrics(
        pairs, planned=planned or len(rows), duration_ms=duration,
        usage=usage_total, cost=_cost_value(usage_total),
        fixture_misses=sum(len((row.get("actual") or {}).get("fixture_misses") or [])
                           for row in rows))


def _build_failures(run_id: str, cases: Sequence[Mapping[str, Any]],
                    rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cases_by_id = {case["case_id"]: case for case in cases}
    events_by_case: dict[str, list[str]] = {}
    for event in persistence.list_events(run_id, after_seq=0, limit=2000):
        case_id = event.get("case_id")
        if not case_id:
            continue
        events_by_case.setdefault(case_id, []).append(event.get("type"))
    failures: list[dict[str, Any]] = []
    for row in rows:
        scores = row.get("scores") or {}
        if scores.get("status") not in ("fail", "invalid"):
            continue
        if scores.get("error_kind") == "infrastructure":
            continue
        case = cases_by_id.get(row["case_id"])
        if not case:
            continue
        failures.append({
            "case_id": row["case_id"],
            "query": case["query"],
            "split": case.get("split") or "optimize",
            "tags": case.get("tags") or [],
            "group_id": case.get("group_id"),
            "expected": case.get("expected") or {},
            "actual": row.get("actual") or {},
            "score": scores,
            "events": events_by_case.get(row["case_id"]) or [],
        })
    failures.sort(key=lambda item: item["case_id"])
    return failures


def _failure_categories(failures: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for failure in failures:
        for diagnostic in (failure.get("score") or {}).get("diagnostics") or []:
            category = diagnostic.get("category") or "unknown"
            counts[category] = counts.get(category, 0) + 1
    return counts


def _critical_regressions(cases: Sequence[Mapping[str, Any]],
                          baseline_rows: Sequence[Mapping[str, Any]],
                          candidate_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    critical = {case["case_id"] for case in cases
                if case.get("critical")
                or (case.get("expected") or {}).get("critical")}
    if not critical:
        return []
    by_repeat: dict[tuple[str, int], dict[str, bool]] = {}
    for phase, rows in (("baseline", baseline_rows), ("candidate", candidate_rows)):
        for row in rows:
            case_id = row["case_id"]
            if case_id not in critical:
                continue
            key = (case_id, int(row.get("repeat_index") or 1))
            by_repeat.setdefault(key, {})[phase] = bool(
                (row.get("scores") or {}).get("passed"))
    regressions: list[str] = []
    for (case_id, _repeat), values in by_repeat.items():
        if values.get("baseline") and values.get("candidate") is False:
            if case_id not in regressions:
                regressions.append(case_id)
    return regressions


def _infra_errors(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows
               if (row.get("scores") or {}).get("status") == "infra")


def _score_reason(score: Mapping[str, Any]) -> str:
    joint = score.get("joint")
    if isinstance(joint, Mapping):
        return str(joint.get("reason") or "案例未通过")
    routing = score.get("routing") if isinstance(score.get("routing"), Mapping) else {}
    if routing.get("reason"):
        return str(routing["reason"])
    tool_args = score.get("tool_args") if isinstance(score.get("tool_args"), Mapping) else {}
    if tool_args.get("reason"):
        return str(tool_args["reason"])
    diagnostics = score.get("diagnostics") or []
    if diagnostics:
        first = diagnostics[0]
        return str(first.get("category") or "案例未通过")
    return "案例未通过"


def _metric_summary(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    if not metrics:
        return {}
    return {
        "routing": metrics.get("routing"),
        "tool_args": metrics.get("tool_args"),
        "joint": metrics.get("joint"),
        "execution": metrics.get("execution"),
    }


def _skill_files(version_id: str) -> dict[str, bytes]:
    root = persistence.skill_files_dir(version_id)
    files: dict[str, bytes] = {}
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _validation_repeats(env: Mapping[str, Any]) -> int:
    run_config = env.get("run_config") or {}
    acceptance = env.get("acceptance_config") or {}
    return max(1, int(acceptance.get("validation_repeats")
                     or run_config.get("validation_repeats") or 3))


def _cost_value(usage: Mapping[str, int]) -> dict[str, Any] | None:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    amount = (input_tokens / 1000.0) * config.PRICE_PER_1K_INPUT_TOKENS + \
        (output_tokens / 1000.0) * config.PRICE_PER_1K_OUTPUT_TOKENS
    if config.PRICE_PER_1K_INPUT_TOKENS == 0 and config.PRICE_PER_1K_OUTPUT_TOKENS == 0:
        amount_value = None
    else:
        amount_value = round(amount, 6)
    return {"tokens": input_tokens + output_tokens, "amount": amount_value}


def _cost(metrics: Mapping[str, Any]) -> dict[str, Any]:
    execution = (metrics or {}).get("execution") or {}
    return {"tokens": execution.get("tokens") or 0,
            "amount": execution.get("cost"),
            "duration_ms": execution.get("duration_ms") or 0}


# --------------------------------------------------------------------------- #
# budget / cancellation
# --------------------------------------------------------------------------- #
class _ControlledOptimizer:
    """Apply runtime controls without modifying the frozen environment config."""

    def __init__(self, adapter: Any, cancel: Any, budget: Any):
        self.adapter, self.cancel, self.budget = adapter, cancel, budget

    def invoke(self, messages: Any, model_config: Mapping[str, Any], tools: Any) -> Any:
        runtime_config = dict(model_config)
        runtime_config["cancel_check"] = self.cancel.cancelled
        if self.budget.max_seconds is not None:
            runtime_config["deadline_monotonic"] = (
                self.budget.started + float(self.budget.max_seconds))
        remaining = self.budget.remaining_tokens()
        if remaining is not None:
            runtime_config["max_tokens"] = min(
                int(runtime_config.get("max_tokens") or remaining), remaining)
        result = self.adapter.invoke(messages, runtime_config, tools)
        if isinstance(result, Mapping):
            self.budget.consume(result.get("usage"))
        return result


class _Budget:
    def __init__(self, config_payload: Mapping[str, Any]):
        self.max_tokens = config_payload.get("max_tokens")
        self.max_cost = config_payload.get("max_cost")
        self.max_seconds = config_payload.get("max_seconds")
        self.tokens_used = 0
        self.cost_used = 0.0
        self.started = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, usage: Mapping[str, Any] | None) -> None:
        usage = usage or {}
        tokens = 0
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                tokens += int(value)
        with self._lock:
            self.tokens_used += tokens
            self.cost_used += (tokens / 1000.0) * (
                config.PRICE_PER_1K_INPUT_TOKENS or 0)

    def remaining_tokens(self) -> int | None:
        if self.max_tokens is None:
            return None
        return max(0, int(self.max_tokens) - self.tokens_used)

    def ensure_available(self) -> None:
        if self.max_tokens is not None and self.tokens_used >= int(self.max_tokens):
            raise BudgetExhausted("Token 预算已用尽")
        if self.max_seconds is not None and \
                time.monotonic() - self.started >= float(self.max_seconds):
            raise BudgetExhausted("运行时长预算已用尽")
        if self.max_cost is not None and self.cost_used >= float(self.max_cost):
            raise BudgetExhausted("金额预算已用尽")

    def describe(self) -> dict[str, Any]:
        return {"max_tokens": self.max_tokens, "max_cost": self.max_cost,
                "max_seconds": self.max_seconds}


class _CancellationToken:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._budget_exhausted = False
        self._cancelled = False
        self._last_check = 0.0

    def cancelled(self) -> bool:
        if self._cancelled:
            return True
        now = time.monotonic()
        if now - self._last_check < 0.5:
            return False
        self._last_check = now
        try:
            run = persistence.get_run(self.run_id)
        except Exception:
            return False
        if run.get("status") in ("cancel_requested", "cancelled"):
            self._cancelled = True
        return self._cancelled

    def set_budget_exhausted(self) -> None:
        self._budget_exhausted = True

    def budget_exhausted(self) -> bool:
        return self._budget_exhausted
