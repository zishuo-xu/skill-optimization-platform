"""A09: 应用候选更新当前版本、并发冲突 409、回滚与导出恢复历史内容。"""

from __future__ import annotations

import json
import zipfile

from app import persistence
from tests.conftest import run_sync


def test_apply_conflict_and_rollback(client, demo, accepted_run):
    project = demo["project"]
    project_id = project["id"]
    assert accepted_run["decision"] == "accept"
    candidate = persistence.get_candidate_for_run(accepted_run["id"])
    assert candidate and candidate["skill_version_id"]
    candidate_version_id = candidate["skill_version_id"]
    baseline_version_id = project["current_skill_version_id"]

    applied = client.post(
        f"/api/v3/runs/{accepted_run['id']}/apply",
        json={"expected_current_version": baseline_version_id})
    assert applied.status_code == 200, applied.text
    assert applied.json()["project"]["current_skill_version_id"] == \
        candidate_version_id
    assert client.get(f"/api/v3/projects/{project_id}").json()[
        "current_skill_version_id"] == candidate_version_id

    # Re-applying with a stale expected version must conflict (409).
    conflict = client.post(
        f"/api/v3/runs/{accepted_run['id']}/apply",
        json={"expected_current_version": baseline_version_id})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "version_conflict"
    assert conflict.json()["error"]["details"]["current_version"] == \
        candidate_version_id

    # Roll back by activating the previous published version.
    rollback = client.post(
        f"/api/v3/skill-versions/{baseline_version_id}/activate",
        json={"expected_current_version": candidate_version_id})
    assert rollback.status_code == 200
    assert client.get(f"/api/v3/projects/{project_id}").json()[
        "current_skill_version_id"] == baseline_version_id
    audit = client.get(f"/api/v3/projects/{project_id}/versions").json()["audit"]
    actions = {entry["action"] for entry in audit}
    assert "candidate.apply" in actions
    assert "skill_version.activate" in actions

    # Exported historical package restores the exact file content.
    exported = client.get(f"/api/v3/skill-versions/{baseline_version_id}/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(__import__("io").BytesIO(exported.content)) as zf:
        restored = zf.read("SKILL.md")
    expected = persistence.read_skill_file(baseline_version_id, "SKILL.md")
    assert restored == expected


def test_final_test_runs_only_on_isolated_split(client, demo, baseline_run):
    run = client.post("/api/v3/runs", json={"project_id": demo["project"]["id"],
                                            "mode": "final_test"}).json()
    final = run_sync(run["id"])
    assert final["status"] == "completed"
    assert final["decision"] is None
    metrics = final["metrics"]["final"]
    assert metrics["joint"]["total_cases"] == 4
    assert metrics["execution"]["planned"] == 4
    # final-test cases are never mixed into optimization selection
    rows = persistence.get_case_results_for_run(final["id"], "final")
    assert len(rows) == 4
    assert all(row["split"] == "final_test" for row in rows)


def test_versions_diff_between_candidate_and_parent(client, optimize_run):
    candidate = persistence.get_candidate_for_run(optimize_run["id"])
    diff = client.get(
        f"/api/v3/skill-versions/{candidate['skill_version_id']}/diff")
    assert diff.status_code == 200
    changes = diff.json()["changes"]
    assert changes
    paths = {change["path"] for change in changes}
    assert "SKILL.md" in paths
    for change in changes:
        assert change["status"] == "modified"
        assert "--- a/" in change["diff"] and "+++ b/" in change["diff"]
