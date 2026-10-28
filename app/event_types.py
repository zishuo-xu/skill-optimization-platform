"""Canonical event type / stage / issue-code registry.

Every observable step of a run is persisted as an event (see ``persistence.append_event``).
The frontend "运行记录" (run log) tab, the SSE stream and the acceptance tests all rely on
these names, so keep this file as the single source of truth.

Event envelope
--------------
``run_id, seq, ts, stage, case_id, split, variant, repeat_index, attempt, call_id,
type, level, payload``

* ``seq`` is strictly increasing inside one run (allocated under a DB transaction).
* ``ts`` is ISO-8601 UTC with milliseconds.
* Case/attempt fields are ``None`` for run-wide events.
* ``level`` is one of ``debug`` / ``info`` / ``warning`` / ``error``.
* For ``level in {warning, error}`` the payload SHOULD contain an *issue* object:

  ``{"error_code": str, "title": str, "message": str, "impact": str,
     "next_action": str, "cause_event_seq": int|None, "technical_detail": {...}}``

  ``cause_event_seq`` may only be filled when the causality is known by the program
  (never inferred from temporal adjacency).
"""

from __future__ import annotations

STAGES = [
    "preflight",
    "baseline_eval",
    "error_analysis",
    "candidate_generation",
    "static_check",
    "retest",
    "decision",
    "final_test",
]

STAGE_LABELS = {
    "preflight": "预检",
    "baseline_eval": "基线评测",
    "error_analysis": "错误分析",
    "candidate_generation": "生成候选",
    "static_check": "静态检查",
    "retest": "复测验证",
    "decision": "评估结论",
    "final_test": "最终测试",
}

LEVELS = ["debug", "info", "warning", "error"]

# ---- lifecycle ------------------------------------------------------------
RUN_CREATED = "run_created"
RUN_STARTED = "run_started"
RUN_COMPLETED = "run_completed"
RUN_FAILED = "run_failed"
RUN_CANCELLED = "run_cancelled"
RUN_INTERRUPTED = "run_interrupted"
STAGE_STARTED = "stage_started"
STAGE_COMPLETED = "stage_completed"
STAGE_FAILED = "stage_failed"
PROGRESS_UPDATED = "progress_updated"

# ---- preflight ------------------------------------------------------------
PREFLIGHT_ITEM = "preflight_item"
PREFLIGHT_FAILED = "preflight_failed"

# ---- case execution -------------------------------------------------------
CASE_STARTED = "case_started"
CASE_COMPLETED = "case_completed"
CASE_FAILED = "case_failed"
CASE_CANCELLED = "case_cancelled"
SKILL_SELECTED = "skill_selected"
SKILL_READ = "skill_read"
MODEL_CALL_STARTED = "model_call_started"
MODEL_CALL_FINISHED = "model_call_finished"
MODEL_CALL_FAILED = "model_call_failed"
MODEL_DELTA = "model_delta"
TOOL_CALL_STARTED = "tool_call_started"
TOOL_CALL_FINISHED = "tool_call_finished"
TOOL_RESULT = "tool_result"
FIXTURE_MISS = "fixture_miss"
SCORE_COMPLETED = "score_completed"
SCORE_FAILED = "score_failed"
JUDGE_COMPLETED = "judge_completed"
JUDGE_FAILED = "judge_failed"

# ---- optimizer ------------------------------------------------------------
OPTIMIZER_INPUT_BUILT = "optimizer_input_built"
OPTIMIZER_CALL_STARTED = "optimizer_call_started"
OPTIMIZER_CALL_FINISHED = "optimizer_call_finished"
OPTIMIZER_CALL_FAILED = "optimizer_call_failed"
OPTIMIZER_EMPTY_RESPONSE = "optimizer_empty_response"
OPTIMIZER_PARSE_FAILED = "optimizer_parse_failed"
OPTIMIZER_NOT_CONFIGURED = "optimizer_not_configured"
OPTIMIZER_NO_CHANGE = "optimizer_no_change"
OPTIMIZER_OUTPUT_PARSED = "optimizer_output_parsed"
FALLBACK_STARTED = "fallback_started"
FALLBACK_NO_CHANGE = "fallback_no_change"

# ---- patch / static check / retest ---------------------------------------
PATCH_CREATED = "patch_created"
PATCH_APPLIED = "patch_applied"
STATIC_CHECK_STARTED = "static_check_started"
STATIC_CHECK_ITEM = "static_check_item"
STATIC_CHECK_PASSED = "static_check_passed"
STATIC_CHECK_FAILED = "static_check_failed"
RETEST_STARTED = "retest_started"
RETEST_COMPLETED = "retest_completed"

# ---- decision -------------------------------------------------------------
DECISION_MADE = "decision_made"
RULE_EVALUATED = "rule_evaluated"
REGION_REGRESSION = "regression_detected"

# Errors before a stage can even run.
CONFIG_ERROR = "config_error"

ERROR_LEVEL_TYPES = {
    RUN_FAILED, STAGE_FAILED, CASE_FAILED, MODEL_CALL_FAILED, FIXTURE_MISS,
    SCORE_FAILED, OPTIMIZER_CALL_FAILED, OPTIMIZER_EMPTY_RESPONSE,
    OPTIMIZER_PARSE_FAILED, OPTIMIZER_NOT_CONFIGURED, FALLBACK_NO_CHANGE,
    STATIC_CHECK_FAILED, PREFLIGHT_FAILED, CONFIG_ERROR,
}

# Optimizer error codes (§49) – these must be recorded *before* heuristic fallback.
OPTIMIZER_ERROR_CODES = [
    "optimizer_not_configured",
    "optimizer_call_failed",
    "optimizer_empty_response",
    "optimizer_parse_failed",
    "optimizer_no_change",
]

# Case error kinds (§15): capability failures count in metric denominators,
# infrastructure errors are reported separately and block automatic acceptance.
CASE_ERROR_KIND_CAPABILITY = "capability"
CASE_ERROR_KIND_INFRA = "infrastructure"

CASE_ERROR_CODES = {
    "format_error": CASE_ERROR_KIND_CAPABILITY,
    "refused": CASE_ERROR_KIND_CAPABILITY,
    "steps_exhausted": CASE_ERROR_KIND_CAPABILITY,
    "context_overflow": CASE_ERROR_KIND_CAPABILITY,
    "truncated": CASE_ERROR_KIND_CAPABILITY,
    "protocol_error": CASE_ERROR_KIND_CAPABILITY,
    "fixture_miss": CASE_ERROR_KIND_CAPABILITY,
    "parse_failed": CASE_ERROR_KIND_CAPABILITY,
    "auth_failed": CASE_ERROR_KIND_INFRA,
    "network_error": CASE_ERROR_KIND_INFRA,
    "timeout": CASE_ERROR_KIND_INFRA,
    "rate_limited": CASE_ERROR_KIND_INFRA,
    "platform_error": CASE_ERROR_KIND_INFRA,
    "stream_interrupted": CASE_ERROR_KIND_INFRA,
    "cancelled": "cancelled",
}


def issue(error_code: str, title: str, message: str, *,
          impact: str = "", next_action: str = "",
          cause_event_seq: int | None = None,
          technical_detail: dict | None = None) -> dict:
    payload = {
        "error_code": error_code,
        "title": title,
        "message": message,
        "impact": impact,
        "next_action": next_action,
        "cause_event_seq": cause_event_seq,
    }
    if technical_detail is not None:
        payload["technical_detail"] = technical_detail
    return payload


def is_issue_level(level: str) -> bool:
    return level in ("warning", "error")
