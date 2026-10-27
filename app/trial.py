"""Single-case trial ("试跑"): run one case, or one free-form query, right now.

A trial executes exactly like a case inside a formal run (same request builder,
adapter, tool fixtures and scorer, including the judge model for semantic
comparison), but it is synchronous and isolated: nothing is written to runs,
case results or metrics, so trials never move a baseline, a decision or the
project overview. Judge verdicts still go through the shared judge cache, which
is keyed by content and therefore safe to share.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from . import evaluation, event_types, execution, persistence
from . import judge as judge_mod
from .adapters.registry import build_adapter
from .errors import ValidationError

log = logging.getLogger(__name__)

TRIAL_RUN_ID = "trial"
MAX_QUERY_CHARS = 4000
MAX_EVENTS = 200
PLACEHOLDER_RESPONSE = {"ok": True, "note": "试跑占位返回"}


class _NeverCancelled:
    def cancelled(self) -> bool:
        return False


class _TrialEmitter:
    """Collect events in memory with timestamps (the drawer shows a timeline)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: Mapping[str, Any]) -> dict[str, Any]:
        record = {
            "seq": len(self.events) + 1,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "type": str(event.get("type") or "event"),
            "level": str(event.get("level") or "info"),
            "case_id": event.get("case_id"),
            "call_id": event.get("call_id"),
            "payload": dict(event.get("payload") or {}),
        }
        if len(self.events) < MAX_EVENTS:
            self.events.append(record)
        return record


def _resolve(body: Mapping[str, Any], *, project_id: str | None,
             run_id: str | None) -> dict[str, Any]:
    """Pick project, Skill version, environment and dataset for the trial."""
    run = persistence.get_run(run_id) if run_id else None
    project_id = project_id or (run or {}).get("project_id") or body.get("project_id")
    if not project_id:
        raise ValidationError("缺少 project_id", field_path="project_id")
    project = persistence.get_project(project_id)

    skill_version_id = body.get("skill_version_id")
    variant = str(body.get("variant") or "").strip()
    if not skill_version_id and run:
        if variant == "candidate":
            skill_version_id = run.get("candidate_skill_version_id")
            if not skill_version_id:
                raise ValidationError("这次运行没有候选版本", field_path="variant")
        else:
            skill_version_id = run.get("baseline_skill_version_id")
    skill_version_id = skill_version_id or project.get("current_skill_version_id")
    environment_id = (body.get("environment_id") or (run or {}).get("environment_id")
                      or project.get("current_environment_id"))
    dataset_id = (body.get("dataset_id") or (run or {}).get("dataset_id")
                  or project.get("current_dataset_id"))
    if not skill_version_id:
        raise ValidationError("项目还没有 Skill 版本", field_path="skill_version_id")
    if not environment_id:
        raise ValidationError("项目还没有执行环境", field_path="environment_id")
    return {"project": project, "run": run, "skill_version_id": skill_version_id,
            "environment_id": environment_id, "dataset_id": dataset_id}


def _load_case(body: Mapping[str, Any], dataset_id: str | None) -> dict[str, Any]:
    case_id = str(body.get("case_id") or "").strip()
    query = str(body.get("query") or "").strip()
    if case_id:
        if not dataset_id:
            raise ValidationError("项目还没有数据集", field_path="dataset_id")
        return dict(persistence.get_case(dataset_id, case_id))
    if not query:
        raise ValidationError("请选择一个案例，或输入要试跑的问题", field_path="query")
    if len(query) > MAX_QUERY_CHARS:
        raise ValidationError(f"问题太长（最多 {MAX_QUERY_CHARS} 字）", field_path="query")
    # A free-form query has no expectation: it shows what the model does, unscored.
    # Business tools get a placeholder success so multi-step calls can continue.
    return {"case_id": None, "query": query, "split": None, "expected": None,
            "fixtures": [{"name": "*", "consume": "always", "response": PLACEHOLDER_RESPONSE}],
            "context": {}}


def _request(env: Mapping[str, Any], case: Mapping[str, Any], skill_version_id: str,
             manifest: Mapping[str, Any]) -> dict[str, Any]:
    run_config = env.get("run_config") or {}
    loader_config = env.get("loader_config") or {}
    timeout_s = int(run_config.get("case_timeout_s") or 120)
    model_config = dict(env.get("model_config") or {})
    model_config["deadline_monotonic"] = time.monotonic() + timeout_s
    return {
        "query": case["query"],
        "context": case.get("context") or {},
        "skill_snapshot_path": str(persistence.skill_files_dir(skill_version_id)),
        "system_prompt": env["prompt"],
        "model_config": model_config,
        "request_template": (env.get("model_config") or {}).get("request_template"),
        "tool_schema": env.get("tool_schema") or [],
        "fixtures": case.get("fixtures") or [],
        "limits": {
            **loader_config,
            "max_model_calls": int(run_config.get("max_model_calls") or 12),
            "max_steps": int(loader_config.get("max_steps") or 8),
            "case_timeout_s": timeout_s,
        },
        "loader_config": dict(loader_config),
        "run_id": TRIAL_RUN_ID,
        "case_id": case.get("case_id") or "trial",
        "manifest": manifest,
        "allowed_skill_ids": [item["skill_id"] for item in manifest.get("subskills") or []],
        "cancel_check": _NeverCancelled().cancelled,
        "stage": "trial",
        "split": case.get("split"),
        "variant": "trial",
        "repeat_index": 1,
    }


def _score(case: Mapping[str, Any], actual: Mapping[str, Any], env: Mapping[str, Any],
           emit: _TrialEmitter) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    expected = case.get("expected")
    if not expected:
        return None, {}
    tool_schema = env.get("tool_schema") or []
    compare_rules = (env.get("scorer_config") or {}).get("compare")
    value_judge = judge_mod.ValueJudge.from_env(env)
    score = evaluation.score_case(expected, actual, tool_schema, compare_rules,
                                  semantic_fallback=value_judge is None)
    pending = score.get("pending_judgments") or []
    if not pending or value_judge is None:
        return score, {}

    def judge_emit(event: Mapping[str, Any]) -> Any:
        tagged = dict(event)
        tagged["payload"] = {**dict(tagged.get("payload") or {}), "purpose": "judge"}
        return emit(tagged)

    try:
        verdicts, stats = value_judge.judge(pending, query=case.get("query"), emit=judge_emit)
    except judge_mod.JudgeError as exc:
        emit({"type": event_types.JUDGE_FAILED, "level": "error",
              "payload": {"error_code": exc.code, "title": "评分模型调用失败",
                          "message": exc.message}})
        return evaluation.mark_infrastructure(score, f"评分模型不可用：{exc.message}"), {}
    score = evaluation.score_case(expected, actual, tool_schema, compare_rules,
                                  judgments=verdicts)
    score["judge"] = {key: stats[key] for key in ("items", "cached", "judged", "passed")}
    emit({"type": event_types.JUDGE_COMPLETED,
          "payload": judge_mod.judge_event_payload(stats, verdicts, pending)})
    return score, dict(stats.get("usage") or {})


def _side(actual: Mapping[str, Any], score: Mapping[str, Any] | None) -> dict[str, Any]:
    """Same shape as one side of ``/runs/{id}/cases`` so the UI renders it alike."""
    scores = score or {}
    joint = scores.get("joint")
    return {
        "selected_skills": actual.get("selected_skills") or [],
        "action": actual.get("action"),
        "tool_calls": actual.get("tool_calls") or [],
        "missing_fields": actual.get("missing_fields") or [],
        "passed": scores.get("passed") if score else None,
        "status": scores.get("status") if score else None,
        "finish_protocol": scores.get("finish_protocol") or actual.get("finish_protocol"),
        "error_code": (actual.get("error") or {}).get("code") or scores.get("error_code"),
        "reason": (joint or {}).get("reason") if isinstance(joint, dict) else scores.get("reason"),
        "error_message": (actual.get("error") or {}).get("message"),
        "duration_ms": actual.get("duration_ms"),
        "usage": actual.get("usage") or {},
        "routing": scores.get("routing"),
        "tool_args": scores.get("tool_args"),
        "action_ok": scores.get("action_ok"),
        "diagnostics": scores.get("diagnostics") or [],
        "judgments": scores.get("judgments") or [],
        "raw_output": str(actual.get("raw_output") or "")[:4000],
    }


def try_case(body: Mapping[str, Any], *, project_id: str | None = None,
             run_id: str | None = None) -> dict[str, Any]:
    """Run one case (``case_id``) or one free-form ``query`` and score it."""
    from .services import redact_secrets

    picked = _resolve(body, project_id=project_id, run_id=run_id)
    env = persistence.get_environment(picked["environment_id"])
    version = persistence.get_skill_version(picked["skill_version_id"])
    manifest = version.get("manifest") or {}
    case = _load_case(body, picked["dataset_id"])
    emit = _TrialEmitter()
    adapter = build_adapter(env.get("model_config") or {})
    request = _request(env, case, picked["skill_version_id"], manifest)
    started = time.monotonic()
    try:
        actual = execution.run_case(request, adapter, emit)
    except Exception as exc:  # noqa: BLE001 - report, never crash the request
        log.exception("trial execution crashed")
        actual = {
            "selected_skills": [], "route_events": [], "action": "no_action",
            "tool_calls": [], "missing_fields": [],
            "error": {"code": "platform_error", "message": f"{type(exc).__name__}: {exc}"},
            "error_kind": "infrastructure", "finish_reason": "error", "usage": {},
            "duration_ms": int((time.monotonic() - started) * 1000),
            "result_source": "runtime_event",
            "capabilities": {"routing": True, "tool_arguments": True},
            "events": [], "fixture_misses": [],
        }
    score, judge_usage = _score(case, actual, env, emit)
    return {
        "case_id": case.get("case_id"),
        "query": case["query"],
        "split": case.get("split"),
        "critical": bool(case.get("critical")),
        "expected": case.get("expected"),
        "scored": score is not None,
        "result": _side(actual, score),
        "judge_usage": judge_usage or None,
        "skill_version": {"id": version["id"], "status": version.get("status"),
                          "message": version.get("message")},
        "environment": {"id": env["id"], "version_seq": env.get("version_seq"),
                        "status": env.get("status")},
        "events": redact_secrets(emit.events),
        "trial": True,
        "note": "试跑结果不写入运行记录，也不影响指标和结论。",
    }
