"""Single-case trial (试跑): runs one case or one query now, scores it, stores nothing."""

from __future__ import annotations

import pytest

from app import persistence, services, trial
from app.errors import ValidationError


def _counts() -> tuple[int, int, int]:
    one = lambda sql: persistence._fetchone(sql)["n"]  # noqa: E731
    return (one("SELECT COUNT(*) AS n FROM runs"),
            one("SELECT COUNT(*) AS n FROM case_results"),
            one("SELECT COUNT(*) AS n FROM events"))


def _first_failing_case(run_id: str) -> dict:
    cases = services.run_cases(run_id, limit=200)["items"]
    failing = [item for item in cases if item.get("change_type") == "fixed"]
    assert failing, "demo optimize run should fix at least one case"
    return failing[0]


def test_trial_case_matches_the_formal_score_and_writes_nothing(demo, optimize_run):
    row = _first_failing_case(optimize_run["id"])
    before = _counts()

    baseline = trial.try_case({"case_id": row["case_id"], "variant": "baseline"},
                              run_id=optimize_run["id"])
    candidate = trial.try_case({"case_id": row["case_id"], "variant": "candidate"},
                               run_id=optimize_run["id"])

    assert _counts() == before
    assert baseline["scored"] is True and baseline["trial"] is True
    assert baseline["result"]["passed"] is False
    assert candidate["result"]["passed"] is True
    assert baseline["skill_version"]["id"] == optimize_run["baseline_skill_version_id"]
    assert candidate["skill_version"]["id"] == optimize_run["candidate_skill_version_id"]
    # Same diagnostics as the formal run, so the UI explains it the same way.
    formal = {(d["category"], d["field_path"]) for d in row["baseline"]["diagnostics"]}
    tried = {(d["category"], d["field_path"]) for d in baseline["result"]["diagnostics"]}
    assert tried == formal
    assert baseline["events"] and all(event.get("ts") for event in baseline["events"])


def test_project_trial_uses_current_version_and_given_dataset(demo, baseline_run):
    project = persistence.get_project(demo["project"]["id"])
    case = persistence.all_cases(project["current_dataset_id"])[0]
    result = trial.try_case({"case_id": case["case_id"],
                             "dataset_id": project["current_dataset_id"]},
                            project_id=project["id"])
    assert result["case_id"] == case["case_id"]
    assert result["skill_version"]["id"] == project["current_skill_version_id"]
    assert result["expected"] == case["expected"]
    assert result["result"]["status"] in ("pass", "fail")


def test_free_query_runs_unscored_with_placeholder_tool_results(demo):
    project_id = demo["project"]["id"]
    case = persistence.all_cases(persistence.get_project(project_id)["current_dataset_id"])[0]
    result = trial.try_case({"query": case["query"]}, project_id=project_id)
    assert result["scored"] is False
    assert result["expected"] is None
    assert result["result"]["passed"] is None
    # The offline model recognises dataset queries; its tool call must not hit fixture_miss.
    if result["result"]["tool_calls"]:
        assert result["result"]["error_code"] != "fixture_miss"


def test_trial_input_errors(demo, baseline_run):
    project_id = demo["project"]["id"]
    with pytest.raises(ValidationError):
        trial.try_case({}, project_id=project_id)
    with pytest.raises(ValidationError):
        trial.try_case({"query": "x" * (trial.MAX_QUERY_CHARS + 1)}, project_id=project_id)
    with pytest.raises(ValidationError):
        trial.try_case({"case_id": "opt_001", "variant": "candidate"}, run_id=baseline_run["id"])


def test_trial_endpoints(client, demo, optimize_run):
    row = _first_failing_case(optimize_run["id"])
    response = client.post(f"/api/v3/runs/{optimize_run['id']}/debug-cases",
                           json={"case_id": row["case_id"], "variant": "candidate"})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["passed"] is True

    project_id = demo["project"]["id"]
    response = client.post(f"/api/v3/projects/{project_id}/debug-cases", json={})
    assert response.status_code == 422
    assert response.json()["error"]["field_path"] == "query"
