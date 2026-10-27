"""A10: 澄清、无工具、模拟返回缺失、预算耗尽与基础设施错误处理。"""

from __future__ import annotations

import json

from app import persistence
from tests.conftest import complete_run, create_demo_project, environment_payload


def _case(case_id, query, split, expected, fixtures=None, tags=None, critical=False):
    return {
        "id": case_id, "query": query, "group_id": f"g_{case_id}", "split": split,
        "tags": tags or [], "expected": expected,
        "fixtures": fixtures or [], "critical": critical,
    }


def _edge_dataset(*, missing_fixture=False) -> str:
    cases = [
        _case("edge_clarify", "帮我建任务", "optimize",
              {"action": "clarify", "routes": [["task_create"]],
               "missing_fields": ["title", "project_id"],
               "tool_calls": []},
              fixtures=[{"name": "create_task", "match": {}, "response": {"ok": True}}],
              tags=["澄清"]),
        _case("edge_no_action", "今天天气怎么样", "optimize",
              {"action": "no_action", "routes": [], "tool_calls": []}),
        _case("edge_fixture_miss", "查询任务 T99 的详情", "optimize",
              {"action": "tool_call", "routes": [["task_query"]],
               "tool_calls": [{"name": "list_tasks",
                               "arguments": {"task_id": "T99"}}]},
              fixtures=[] if missing_fixture else [{"name": "list_tasks", "match": {},
                                                       "response": {"ok": True}}], tags=["fixture"]),
        _case("edge_validation_ok", "查询项目 P123 的详情", "validation",
              {"action": "tool_call", "routes": [["project_query"]],
               "tool_calls": [{"name": "get_project",
                               "arguments": {"project_id": "P123"}}]},
              fixtures=[{"name": "get_project", "match": {},
                         "response": {"ok": True}}]),
    ]
    return "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n"


def test_uncovered_expected_fixture_blocks_evaluation():
    demo = create_demo_project(dataset_content=_edge_dataset(missing_fixture=True))
    baseline = complete_run(demo, "baseline")
    assert baseline["status"] == "failed"
    assert baseline["error_code"] == "fixture_coverage_incomplete"
    assert "fixture" in baseline["error_message"]


def test_clarify_and_no_action_scoring():
    demo = create_demo_project(dataset_content=_edge_dataset())
    baseline = complete_run(demo, "baseline")
    assert baseline["status"] == "completed"
    metrics = baseline["metrics"]["baseline"]
    assert metrics["joint"]["total_cases"] == 3
    assert metrics["execution"]["infra_errors"] == 0
    clarify = persistence.get_case_result(baseline["id"], "baseline", "edge_clarify")
    assert clarify["scores"]["action_ok"] is False
    assert clarify["scores"]["routing"]["evaluable"] is False
    assert clarify["scores"]["tool_args"]["evaluable"] is False
    no_action = persistence.get_case_result(baseline["id"], "baseline", "edge_no_action")
    assert no_action["action"] == "no_action"


def test_budget_exhaustion_is_inconclusive_not_accept():
    demo = create_demo_project(dataset_content=_edge_dataset(),
                              env_overrides={"budget_config": {
                                  "max_tokens": 1, "max_seconds": 1800,
                                  "max_cost": None}})
    run = complete_run(demo, "optimize")
    assert run["status"] == "completed"
    assert run["decision"] == "inconclusive"
    assert "预算" in (run.get("decision_reason") or "")
    events = persistence.list_events(run["id"], limit=2000)
    assert any(event["type"] in ("budget_exceeded", "run_failed") for event in events)
    assert run["stage"] != "running"
    # No permanently running step.
    assert persistence.claim_run("audit-worker") is None


def test_unreachable_gateway_reports_infrastructure_error():
    payload = environment_payload()
    payload["model_config"] = {
        "provider": "enterprise_ws",
        "ws_url": "ws://127.0.0.1:9",
        "model": "Qwen/Qwen3.5-397B-A17B",
        "timeout_s": 0.5,
        "max_retries": 0,
    }
    demo = create_demo_project(dataset_content=_edge_dataset(),
                               env_overrides={"model_config": payload["model_config"]})
    baseline = complete_run(demo, "baseline")
    assert baseline["status"] == "completed"
    metrics = baseline["metrics"]["baseline"]
    assert metrics["execution"]["infra_errors"] >= 1
    rows = persistence.get_case_results_for_run(baseline["id"], "baseline")
    assert any((row["scores"] or {}).get("status") == "infra" for row in rows)
