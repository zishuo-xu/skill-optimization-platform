"""Evaluation report builders (JSON / CSV source data)."""

from __future__ import annotations

from typing import Any, Mapping

from . import config, event_types, persistence


def _redact(value: Any) -> Any:
    """Remove credential-like fields from report payloads."""
    import re

    pattern = re.compile(
        r"(^|_)(api[_-]?key|authorization|access_token|refresh_token|id_token|"
        r"bearer|token|secret|password|passwd|cookie)($|_)", re.I)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and pattern.search(key):
                out[key] = "***"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def build_report(run_id: str) -> dict[str, Any]:
    from . import services

    run = services.run_view(run_id)
    project = persistence.get_project(run["project_id"])
    env = None
    try:
        env = persistence.get_environment(run["environment_id"])
    except Exception:
        env = None
    dataset = None
    try:
        dataset = persistence.get_dataset(run["dataset_id"])
    except Exception:
        dataset = None
    candidate = run.get("candidate") or {}
    baseline_version = None
    try:
        baseline_version = services.skill_version_detail(
            run["baseline_skill_version_id"])
    except Exception:
        baseline_version = None
    diff = services.run_diff(run_id)
    case_rows = services.run_cases(run_id, limit=1000)["items"]
    issues = run.get("issues_summary") or {"items": [], "total": 0}
    decision = persistence.get_decision_for_run(run_id) or {}
    metrics = run.get("metrics") or {}
    return {
        "report_version": 1,
        "generated_at": persistence.now_iso(),
        "run": {
            "id": run["id"], "project_id": run["project_id"],
            "project_name": project.get("name"), "mode": run["mode"],
            "status": run["status"], "stage": run["stage"],
            "decision": run.get("decision"),
            "decision_reason": run.get("decision_reason"),
            "created_at": run.get("created_at"), "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "baseline_skill_version_id": run.get("baseline_skill_version_id"),
            "candidate_skill_version_id": run.get("candidate_skill_version_id"),
            "baseline_cache_hit": bool(run.get("baseline_cache_hit")),
        },
        "fingerprint": (run.get("snapshot") or {}).get("fingerprint"),
        "versions": {
            "baseline": _redact(baseline_version),
            "environment": _redact({
                "id": (env or {}).get("id"),
                "version_seq": (env or {}).get("version_seq"),
                "hash": (env or {}).get("hash"),
                "model_config": (env or {}).get("model_config"),
                "optimizer_config": (env or {}).get("optimizer_config"),
                "scorer_config": _redact((env or {}).get("scorer_config")),
                "run_config": (env or {}).get("run_config"),
                "acceptance_config": (env or {}).get("acceptance_config"),
                "reproducibility_note": (env or {}).get("reproducibility_note"),
            }),
            "dataset": {
                "id": (dataset or {}).get("id"),
                "version_seq": (dataset or {}).get("version_seq"),
                "hash": (dataset or {}).get("hash"),
                "case_count": (dataset or {}).get("case_count"),
                "split_config": (dataset or {}).get("split_config"),
            },
        },
        "scoring": {
            "scorer_version": (run.get("snapshot") or {}).get("scorer_version"),
            "rules_version": (run.get("acceptance") or {}).get("rules_version"),
            "metric_definitions": {
                "routing": "实际子技能路线匹配任一允许路线的案例次数 ÷ 分发有效案例次数",
                "tool_args": "完整业务工具序列的名称、数量、顺序及参数全部正确的案例次数 ÷ 需要工具调用的有效案例次数",
                "joint": "分发、动作、工具及参数全部正确的案例次数 ÷ 全部有效案例次数",
                "infra_policy": "基础设施错误不计入分母、单独报告并阻止自动晋级",
            },
            "repeats": (run.get("acceptance") or {}).get("validation_repeats"),
        },
        "metrics": {
            "baseline": metrics.get("baseline"),
            "candidate": metrics.get("candidate"),
            "delta": metrics.get("delta"),
            "validation": metrics.get("validation"),
            "final": metrics.get("final"),
        },
        "acceptance": {
            "decision": run.get("decision"),
            "rules": (decision.get("rationale") or {}).get("rules") or [],
            "reason": (decision.get("rationale") or {}).get("reason")
            or run.get("decision_reason"),
            "applied_at": decision.get("applied_at"),
        },
        "candidate": {
            "skill_version_id": candidate.get("skill_version_id"),
            "patch": _redact(candidate.get("patch")),
            "diagnosis": _redact(candidate.get("diagnosis")),
            "hypotheses": candidate.get("hypotheses") or [],
            "changed_files": candidate.get("changed_files") or [],
            "expected_effect": candidate.get("expected_effect") or "",
            "risk_cases": candidate.get("risk_cases") or [],
            "checks": candidate.get("checks") or {},
        },
        "files": {
            "changes": diff.get("changes") or [],
            "related_cases": diff.get("related_cases") or [],
        },
        "cases": [
            {
                "case_id": item["case_id"],
                "query": item["query"],
                "split": item["split"],
                "change_type": item["change_type"],
                "baseline_passed": (item.get("baseline") or {}).get("passed"),
                "candidate_passed": (item.get("candidate") or {}).get("passed"),
                "baseline_reason": (item.get("baseline") or {}).get("reason"),
                "candidate_reason": (item.get("candidate") or {}).get("reason"),
                "diagnostics": item.get("diagnostics") or [],
            }
            for item in case_rows
        ],
        "cost": run.get("cost") or {},
        "budget": run.get("budget") or {},
        "issues": issues,
        "conclusion": run.get("decision_reason") or "",
        "notice": ("本报告包含评分口径与逐案例差异；不包含密钥、隐藏思维链或最终测试集答案。"
                   "若模型供应商无法固定快照，报告会标注可复现性限制。"),
    }
