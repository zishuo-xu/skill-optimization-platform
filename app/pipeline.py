"""Project home as one pipeline: 准备 → 测试 → 优化 → 验收 → 应用.

This module only *reads*: it summarises existing runs, versions and the
working copy into five steps with one recommended next action. Scoring,
isolation, acceptance and apply rules stay in ``services``.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import persistence, services, workspace
from .errors import NotFoundError

ACTIVE = ("queued", "running", "cancel_requested")
STEP_LABELS = {
    "prepare": "准备", "test": "测试", "optimize": "优化",
    "validate": "验收", "apply": "应用",
}


def _endpoint_missing(cfg: Mapping[str, Any] | None, label: str) -> list[str]:
    cfg = cfg or {}
    provider = cfg.get("provider")
    if provider in (None, ""):
        return [f"{label}未选择接入方式"]
    if provider in ("demo_scripted", "heuristic"):
        return []
    issues = []
    endpoint = cfg.get("ws_url") if provider == "enterprise_ws" else (cfg.get("base_url") or cfg.get("url"))
    if not str(endpoint or "").strip():
        issues.append(f"{label}缺少地址")
    if not str(cfg.get("model") or "").strip():
        issues.append(f"{label}缺少模型 ID")
    return issues


def environment_issues(env: Mapping[str, Any] | None) -> list[str]:
    """Fields a user still has to fill before the environment can run."""
    if not env:
        return ["尚未配置 Prompt 与模型"]
    issues = []
    if not str(env.get("prompt") or "").strip():
        issues.append("业务 Prompt 为空")
    issues += _endpoint_missing(env.get("model_config"), "业务模型")
    optimizer = env.get("optimizer_config") or {}
    if optimizer:
        issues += _endpoint_missing(optimizer, "优化模型")
    scorer = env.get("scorer_config") or {}
    compare = scorer.get("compare") or {}
    semantic = compare.get("mode") == "semantic" or any(
        (rule or {}).get("mode") == "semantic" for rule in compare.get("field_rules") or [])
    if semantic and not scorer.get("judge"):
        issues.append("语义评分需要评分模型")
    return issues


def _metric_values(block: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not block:
        return None
    out = {}
    for key in ("joint", "routing", "tool_args"):
        item = block.get(key) or {}
        out[key] = {"value": item.get("value"), "passed": item.get("passed"),
                    "evaluated": item.get("evaluated"), "status": item.get("status")}
    return out


def _case_counts(run_id: str) -> dict[str, int]:
    counts = {"fixed": 0, "regressed": 0, "unchanged_fail": 0, "unchanged_pass": 0}
    sides: dict[str, dict[str, bool]] = {"baseline": {}, "candidate": {}}
    for phase in sides:
        for row in persistence.get_case_results_for_run(run_id, phase):
            if (row.get("repeat_index") or 1) == 1:
                sides[phase][row["case_id"]] = bool((row.get("scores") or {}).get("passed"))
    for case_id, before in sides["baseline"].items():
        if case_id not in sides["candidate"]:
            continue
        after = sides["candidate"][case_id]
        key = ("fixed" if after else "unchanged_fail") if not before else ("unchanged_pass" if after else "regressed")
        counts[key] += 1
    return counts


def _run_brief(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {"id": run["id"], "mode": run.get("mode"), "status": run.get("status"),
            "decision": run.get("decision"), "stage": run.get("stage"),
            "stage_progress": run.get("stage_progress") or {},
            "created_at": run.get("created_at"), "finished_at": run.get("finished_at"),
            "error_message": run.get("error_message")}


def _round_count(run: Mapping[str, Any]) -> int:
    return len((run.get("snapshot") or {}).get("ancestor_run_ids") or []) + 1


def pipeline(project_id: str) -> dict[str, Any]:
    ov = services.overview(project_id)
    project = ov["project"]
    ws = workspace.summary(project_id)
    runs = persistence.list_runs(project_id=project_id, limit=40)["items"]
    active = next((run for run in runs if run.get("status") in ACTIVE), None)

    # ---- prepare -------------------------------------------------------
    env_id = ws["items"]["environment"]["draft_id"] or project.get("current_environment_id")
    env = persistence.get_environment(env_id) if env_id else None
    prepare_issues = [f"缺少{item['label']}" for item in ws["missing"] if item["kind"] != "environment"]
    prepare_issues += environment_issues(env)
    prepared = not prepare_issues

    # ---- test: the current Skill's score on the daily set ---------------
    official = None
    metrics = ov.get("metrics")
    if metrics:
        official = {"run_id": metrics["run_id"], "evaluated_at": metrics.get("evaluated_at"),
                    "metrics": _metric_values(metrics)}
    tested = bool(ov["baseline"]["compatible"]) and not ws["pending"]
    all_passed = bool(ov.get("baseline_all_passed"))

    # ---- optimize / validate / apply -----------------------------------
    current_skill = project.get("current_skill_version_id")
    candidate = None
    validation = None
    latest_opt = None
    for run in runs:
        if run.get("mode") not in ("optimize", "validate") or run.get("status") in ACTIVE:
            continue
        root = (run.get("snapshot") or {}).get("root_skill_version_id") or run.get("baseline_skill_version_id")
        if root != current_skill:
            continue
        if run["mode"] == "validate" and validation is None and latest_opt is None:
            validation = run
            continue
        if run["mode"] == "optimize":
            latest_opt = run
            break
    if validation is not None:
        source_id = (validation.get("snapshot") or {}).get("source_run_id")
        try:
            latest_opt = persistence.get_run(source_id) if source_id else latest_opt
        except NotFoundError:
            pass
    if latest_opt and latest_opt.get("status") == "completed" and latest_opt.get("candidate_skill_version_id"):
        view = services.run_view(latest_opt["id"])
        candidate = {
            "run_id": latest_opt["id"],
            "skill_version_id": latest_opt["candidate_skill_version_id"],
            "round": _round_count(latest_opt),
            "baseline": _metric_values((view.get("metrics") or {}).get("baseline")),
            "metrics": _metric_values((view.get("metrics") or {}).get("candidate")),
            "delta": (view.get("metrics") or {}).get("delta"),
            "counts": _case_counts(latest_opt["id"]),
            "changed_files": len(((view.get("candidate") or {}).get("changed_files")) or []),
            "summary": (((view.get("candidate") or {}).get("diagnosis")) or {}).get("summary") or "",
            "can_continue": bool(view.get("can_continue_optimization")),
            "can_validate": view.get("can_validate") is not False,
        }
    last_optimize_outcome = None
    if latest_opt and not candidate:
        last_optimize_outcome = {
            "run_id": latest_opt["id"], "status": latest_opt.get("status"),
            "reason": latest_opt.get("decision_reason") or latest_opt.get("error_message") or "",
        }
    validation_info = None
    if validation:
        view = services.run_view(validation["id"])
        validation_info = {
            "run_id": validation["id"], "status": validation.get("status"),
            "decision": validation.get("decision"),
            "reason": validation.get("decision_reason") or validation.get("error_message") or "",
            "baseline": _metric_values((view.get("metrics") or {}).get("baseline")),
            "metrics": _metric_values((view.get("metrics") or {}).get("candidate")),
            "can_apply": bool(view.get("can_apply")),
        }

    # Last applied candidate (the cycle restarts after apply).
    applied = None
    for run in runs:
        if run.get("mode") == "validate" and run.get("decision") == "accept" \
                and run.get("candidate_skill_version_id") == current_skill:
            view = services.run_view(run["id"])
            if (view.get("acceptance") or {}).get("applied_at"):
                applied = {"run_id": run["id"], "applied_at": view["acceptance"]["applied_at"],
                           "baseline": _metric_values((view.get("metrics") or {}).get("baseline")),
                           "metrics": _metric_values((view.get("metrics") or {}).get("candidate"))}
            break

    # ---- step states and next action -----------------------------------
    states = {key: "todo" for key in STEP_LABELS}
    current = "prepare"
    if prepared:
        states["prepare"] = "done"
        current = "test"
        if tested:
            states["test"] = "done"
            current = "optimize"
            if candidate or all_passed:
                states["optimize"] = "done" if candidate else "skipped"
                current = "validate" if candidate else "done"
                if candidate and validation_info and validation_info["status"] == "completed":
                    if validation_info["decision"] == "accept":
                        states["validate"] = "done"
                        current = "apply"
                    else:
                        states["validate"] = "failed"
                        current = "validate"
    if active:
        states_active = {"evaluate": "test", "baseline": "test", "optimize": "optimize",
                         "validate": "validate", "final_test": "validate"}.get(active.get("mode"))
        if states_active:
            current = states_active

    actions: list[dict[str, Any]] = []
    if active:
        actions.append({"id": "view_run", "label": "查看进度", "primary": True, "run_id": active["id"]})
    elif current == "prepare":
        missing_kinds = [item["kind"] for item in ws["missing"]]
        tab = {"skill": "skill", "dataset": "dataset"}.get(missing_kinds[0], "model") if missing_kinds else "model"
        actions.append({"id": "settings", "label": "去设置", "primary": True, "tab": tab})
    elif current == "test":
        actions.append({"id": "evaluate", "label": "运行测试", "primary": True})
    elif current == "optimize":
        actions.append({"id": "optimize", "label": "优化 Skill", "primary": True,
                        "hint": "根据失败案例生成一个候选，并在测试集重跑"})
    elif current == "validate":
        if validation_info and validation_info["status"] == "completed" and validation_info["decision"] != "accept":
            actions.append({"id": "continue", "label": "基于候选继续优化", "primary": True,
                            "run_id": candidate["run_id"]} if candidate and candidate["can_continue"] else
                           {"id": "optimize", "label": "重新优化", "primary": True})
            actions.append({"id": "view_run", "label": "查看验收结果", "run_id": validation_info["run_id"]})
        elif candidate:
            actions.append({"id": "validate", "label": "独立验收", "primary": True,
                            "run_id": candidate["run_id"], "disabled": not candidate["can_validate"],
                            "hint": "用独立验收集对比原版与候选，通过后才能应用"})
            if candidate["can_continue"]:
                actions.append({"id": "continue", "label": "继续优化", "run_id": candidate["run_id"]})
            actions.append({"id": "view_run", "label": "查看候选", "run_id": candidate["run_id"]})
    elif current == "apply" and validation_info:
        actions.append({"id": "apply", "label": "应用候选", "primary": True,
                        "run_id": validation_info["run_id"], "disabled": not validation_info["can_apply"]})
        actions.append({"id": "view_run", "label": "查看验收结果", "run_id": validation_info["run_id"]})
    elif current == "done":
        actions.append({"id": "settings", "label": "补充测试案例", "primary": True, "tab": "dataset",
                        "hint": "当前测试集全部通过，没有可改进的失败案例"})
        actions.append({"id": "optimize", "label": "仍要优化"})
    if not active and ws["pending"] and current not in ("prepare", "test"):
        actions.append({"id": "evaluate", "label": "让修改生效并重新测试"})

    steps = [{"key": key, "label": label, "state": "active" if key == current and states[key] != "failed" and current != "done" else states[key]}
             for key, label in STEP_LABELS.items()]
    return {
        "project": {"id": project["id"], "name": project["name"],
                    "description": project.get("description") or ""},
        **({"guide": ov["guide"]} if ov.get("guide") else {}),
        "steps": steps,
        "current": current,
        "prepare": {"ready": prepared, "issues": prepare_issues, "workspace": ws},
        "official": official,
        "all_passed": all_passed,
        "candidate": candidate,
        "last_optimize": last_optimize_outcome,
        "validation": validation_info,
        "applied": applied,
        "active_run": _run_brief(active),
        "actions": actions,
        "history": [_run_brief(run) for run in runs[:12]],
        "has_final_test": _has_final_test(project),
        "offline": ((env or {}).get("model_config") or {}).get("provider") == "demo_scripted",
    }


def _has_final_test(project: Mapping[str, Any]) -> bool:
    dataset_id = project.get("current_dataset_id")
    if not dataset_id:
        return False
    try:
        dataset = persistence.get_dataset(dataset_id)
    except NotFoundError:
        return False
    return bool((dataset.get("split_counts") or {}).get("final_test"))
