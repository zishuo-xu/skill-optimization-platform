"""Edit-and-save working copy, automatic freeze at run start, and the home pipeline."""
import pytest

from app import persistence, pipeline, services, workspace
from app.errors import ConflictError
from tests.conftest import environment_payload, run_sync


def _pid(demo):
    return demo["project"]["id"]


def test_fresh_project_starts_at_test_step(demo):
    data = pipeline.pipeline(_pid(demo))
    assert data["prepare"]["ready"]
    assert data["current"] == "test"
    assert [step["state"] for step in data["steps"]][:2] == ["done", "active"]
    assert data["actions"][0]["id"] == "evaluate"


def test_skill_edit_is_pending_until_run_start(demo):
    pid = _pid(demo)
    before = persistence.get_project(pid)["current_skill_version_id"]
    draft = workspace.ensure_draft(pid, "skill")
    assert draft["status"] == "draft" and draft["parent_id"] == before
    # Same working copy is returned on the next edit.
    assert workspace.ensure_draft(pid, "skill")["id"] == draft["id"]
    # An untouched copy is not a pending change.
    assert workspace.summary(pid)["pending"] == []
    services.save_draft_file(draft["id"], "SKILL.md", "# 改过的 Skill\n" +
                             services.skill_file_content(draft["id"], "SKILL.md")["text"])
    assert [item["kind"] for item in workspace.summary(pid)["pending"]] == ["skill"]
    assert persistence.get_project(pid)["current_skill_version_id"] == before

    result = workspace.start(pid, {"mode": "evaluate"})
    assert [item["kind"] for item in result["frozen"]] == ["skill"]
    project = persistence.get_project(pid)
    assert project["current_skill_version_id"] == draft["id"]
    assert persistence.get_skill_version(draft["id"])["status"] == "published"
    assert result["run"]["baseline_skill_version_id"] == draft["id"]
    assert workspace.summary(pid)["pending"] == []


def test_unchanged_copy_is_discarded_not_published(demo):
    pid = _pid(demo)
    before = persistence.get_project(pid)["current_skill_version_id"]
    draft = workspace.ensure_draft(pid, "skill")
    result = workspace.start(pid, {"mode": "evaluate"})
    assert result["frozen"] == []
    assert persistence.get_project(pid)["current_skill_version_id"] == before
    assert persistence.get_skill_version(draft["id"])["status"] == workspace.DISCARDED
    assert draft["id"] not in {v["id"] for v in persistence.list_skill_versions(pid)}


def test_environment_saves_chain_and_discard(demo):
    pid = _pid(demo)
    current = persistence.get_project(pid)["current_environment_id"]
    first = workspace.save_environment(pid, environment_payload(prompt="新的 Prompt 一"))
    second = workspace.save_environment(pid, environment_payload(prompt="新的 Prompt 二"))
    assert first["parent_id"] == current and second["parent_id"] == first["id"]
    assert workspace.summary(pid)["items"]["environment"]["draft_id"] == second["id"]
    workspace.discard(pid, "environment")
    summary = workspace.summary(pid)
    assert summary["pending"] == [] and summary["items"]["environment"]["draft_id"] is None
    assert persistence.get_project(pid)["current_environment_id"] == current


def test_dataset_edit_freezes_with_cas(demo):
    pid = _pid(demo)
    draft = workspace.ensure_draft(pid, "dataset")
    case = persistence.all_cases(draft["id"])[0]
    edited = {key: case.get(key) for key in
              ("case_id", "query", "context", "expected", "fixtures", "split", "group_id", "tags")}
    edited["query"] = case["query"] + "（改）"
    services.update_dataset_case(draft["id"], edited, persistence.get_dataset(draft["id"])["hash"],
                                 case["case_id"])
    result = workspace.start(pid, {"mode": "evaluate"})
    assert [item["kind"] for item in result["frozen"]] == ["dataset"]
    assert persistence.get_project(pid)["current_dataset_id"] == draft["id"]


def test_continue_and_validate_ignore_pending_edits(demo):
    pid = _pid(demo)
    run_sync(workspace.start(pid, {"mode": "evaluate"})["run"]["id"])
    optimize = run_sync(workspace.start(pid, {"mode": "optimize"})["run"]["id"])
    assert optimize["candidate_skill_version_id"]
    workspace.save_environment(pid, environment_payload(prompt="尚未生效的 Prompt"))
    validate = workspace.start(pid, {"mode": "validate", "source_run_id": optimize["id"]})
    assert validate["frozen"] == []
    assert [item["kind"] for item in workspace.summary(pid)["pending"]] == ["environment"]


def test_pipeline_walks_through_cycle(demo):
    pid = _pid(demo)
    run_sync(workspace.start(pid, {"mode": "evaluate"})["run"]["id"])
    data = pipeline.pipeline(pid)
    assert data["current"] == "optimize" and data["official"]["metrics"]["joint"]["value"] is not None
    optimize = run_sync(workspace.start(pid, {"mode": "optimize"})["run"]["id"])
    data = pipeline.pipeline(pid)
    assert data["current"] == "validate"
    assert data["candidate"]["run_id"] == optimize["id"]
    assert sum(data["candidate"]["counts"].values()) > 0
    assert data["actions"][0]["id"] == "validate"
    validate = run_sync(workspace.start(pid, {"mode": "validate", "source_run_id": optimize["id"]})["run"]["id"])
    data = pipeline.pipeline(pid)
    if validate["decision"] == "accept":
        assert data["current"] == "apply" and data["actions"][0]["id"] == "apply"
        services.apply_candidate(validate["id"], {
            "expected_current_version": validate["baseline_skill_version_id"]})
        data = pipeline.pipeline(pid)
        assert data["applied"]["run_id"] == validate["id"]
        assert data["candidate"] is None
    else:
        assert data["steps"][3]["state"] == "failed"


def test_live_outcomes_in_stage_progress(demo):
    pid = _pid(demo)
    run = run_sync(workspace.start(pid, {"mode": "evaluate"})["run"]["id"])
    outcomes = (run.get("stage_progress") or {}).get("outcomes")
    assert outcomes and sum(outcomes.values()) == (run["stage_progress"]["total"])


def test_environment_issues_flag_missing_endpoint():
    assert pipeline.environment_issues({"prompt": "x", "model_config": {"provider": "openai_http"}}) == [
        "业务模型缺少地址", "业务模型缺少模型 ID"]
    assert pipeline.environment_issues({"prompt": "x", "model_config": {"provider": "demo_scripted"}}) == []


def test_api_routes(client, demo):
    pid = _pid(demo)
    assert client.get(f"/api/v3/projects/{pid}/pipeline").status_code == 200
    assert client.post(f"/api/v3/projects/{pid}/workspace/skill/draft").status_code == 200
    response = client.post(f"/api/v3/projects/{pid}/start", json={"mode": "evaluate"})
    assert response.status_code == 202 and response.json()["run"]["mode"] == "evaluate"
    with pytest.raises(ConflictError):
        workspace.ensure_draft(pid, "environment")
