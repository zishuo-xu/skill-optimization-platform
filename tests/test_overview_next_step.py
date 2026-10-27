"""Overview next step, no-failure outcome and rule-based repair mode."""

from __future__ import annotations

import json

from app import config, event_types, persistence, seed, services
from tests.conftest import environment_payload, run_sync


def _image_project():
    """Image sample: the offline model passes every optimize case."""
    project = persistence.create_project("全部通过的项目")
    services.create_skill_version(project["id"], zip_bytes=seed.build_image_package_zip(),
                                  status="published", set_current=True, message="pytest")
    template = json.loads((config.SAMPLES_DIR / "image_environment.json").read_text(
        encoding="utf-8"))
    template["prompt"] = (config.SAMPLES_DIR / "image_system_prompt.md").read_text(
        encoding="utf-8")
    services.create_environment(project["id"], template, status="published",
                                set_current=True)
    services.create_dataset(project["id"], {
        "filename": "image.jsonl",
        "content": (config.SAMPLES_DIR / "image_dataset.jsonl").read_text(encoding="utf-8"),
    })
    return persistence.get_project(project["id"])


def _complete(project_id: str, mode: str) -> dict:
    run = services.create_run({"project_id": project_id, "mode": mode})
    return run_sync(run["id"])


def test_overview_points_to_pending_candidate_until_applied(client, demo, optimize_run):
    project = demo["project"]
    assert optimize_run["decision"] == "accept"

    data = services.overview(project["id"])
    assert data["primary_action"]["mode"] == "apply"
    assert data["primary_action"]["run_id"] == optimize_run["id"]
    pending = data["pending_candidate"]
    assert pending["run_id"] == optimize_run["id"]
    assert pending["candidate_joint"] > pending["baseline_joint"]

    applied = client.post(
        f"/api/v3/runs/{optimize_run['id']}/apply",
        json={"expected_current_version": project["current_skill_version_id"]})
    assert applied.status_code == 200, applied.text

    after = services.overview(project["id"])
    assert after["pending_candidate"] is None
    assert after["primary_action"]["mode"] != "apply"


def test_newer_unaccepted_round_hides_older_candidate(demo, optimize_run):
    # Simulate a later optimize round that finished without an accepted candidate.
    later = services.create_run({"project_id": demo["project"]["id"], "mode": "optimize"})
    persistence.finish_run(later["id"], status="completed", decision="reject",
                           decision_reason="pytest", stage="decision")
    data = services.overview(demo["project"]["id"])
    assert data["pending_candidate"] is None
    assert data["primary_action"]["mode"] == "optimize"


def test_all_passing_baseline_suggests_cases_and_marks_no_failures(client):
    project = _image_project()
    baseline = _complete(project["id"], "baseline")
    assert baseline["status"] == "completed"

    data = services.overview(project["id"])
    assert data["baseline_all_passed"] is True
    assert data["primary_action"]["mode"] == "add_cases"
    assert data["primary_action"]["enabled"] is True

    optimize = _complete(project["id"], "optimize")
    assert optimize["decision"] == "inconclusive"
    view = services.run_view(optimize["id"])
    assert view["decision_code"] == "no_failures"

    listed = client.get(f"/api/v3/runs?project_id={project['id']}").json()["items"]
    codes = {item["id"]: item["decision_code"] for item in listed}
    assert codes[optimize["id"]] == "no_failures"
    assert codes[baseline["id"]] is None

    recent = services.overview(project["id"])["recent_runs"]["items"]
    assert {run["id"]: run["decision_code"] for run in recent}[optimize["id"]] == "no_failures"

    decisions = persistence.list_events(optimize["id"], event_type=event_types.DECISION_MADE)
    assert decisions and decisions[-1]["level"] == "info"
    issues = services.run_view(optimize["id"])["issues_summary"]["items"]
    assert not [item for item in issues if item["stage"] == "decision"]


def test_failing_baseline_still_offers_optimize(demo, baseline_run):
    data = services.overview(demo["project"]["id"])
    assert data["baseline_all_passed"] is False
    assert data["primary_action"]["mode"] == "optimize"


def test_rule_based_repair_is_information_not_an_issue(demo, optimize_run):
    view = services.run_view(optimize_run["id"])
    assert view["optimizer_mode"] == "heuristic"
    events = persistence.list_events(optimize_run["id"],
                                     event_type=event_types.OPTIMIZER_NOT_CONFIGURED)
    assert events and all(event["level"] == "info" for event in events)
    assert events[0]["payload"]["issue"]["title"] == "规则修复模式"
    codes = [item["error_code"] for item in view["issues_summary"]["items"]]
    assert "optimizer_not_configured" not in codes
    assert view["issues_summary"]["counts"]["error"] == 0


def test_legacy_heuristic_error_event_is_hidden_from_issues(demo, optimize_run):
    # Runs recorded before this change stored the expected fallback as an error.
    persistence.append_event(
        optimize_run["id"], event_types.OPTIMIZER_NOT_CONFIGURED, level="error",
        stage="candidate_generation",
        payload={"issue": event_types.issue(
            "optimizer_not_configured", "优化器未配置", "legacy",
            impact="legacy", next_action="legacy")})
    summary = services.run_view(optimize_run["id"])["issues_summary"]
    assert "optimizer_not_configured" not in [i["error_code"] for i in summary["items"]]


def test_unconfigured_model_optimizer_is_still_reported(demo):
    # A model provider that cannot be built is a real problem and stays visible.
    services.create_environment(
        demo["project"]["id"],
        environment_payload(optimizer_config={"provider": "openai_http",
                                              "base_url": "", "model": ""}),
        status="published", set_current=True)
    run = _complete(demo["project"]["id"], "optimize")
    view = services.run_view(run["id"])
    assert view["optimizer_mode"] == "model"
    issues = view["issues_summary"]["items"]
    optimizer_issues = [item for item in issues
                        if item["error_code"] in event_types.OPTIMIZER_ERROR_CODES]
    assert optimizer_issues and optimizer_issues[0]["level"] == "error"
    assert view["issues_summary"]["counts"]["error"] >= 1
