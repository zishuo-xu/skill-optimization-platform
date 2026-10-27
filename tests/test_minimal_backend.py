"""Regression coverage for the minimal redesign backend contract."""

from __future__ import annotations

import pytest

from app import persistence, services
from app.errors import ConflictError, ValidationError
from tests.conftest import environment_payload, create_demo_project, dataset_payload


@pytest.mark.parametrize("credential", ["LITERAL_REF", {"value": "NESTED_LITERAL"}])
def test_public_environment_masks_literal_refs_and_nested_values(client, credential):
    demo = create_demo_project(env_overrides={
        "model_config": {
            "provider": "demo_scripted",
            "api_key_ref": credential,
            "env_ref": "env:DEMO_KEY",
        }
    })
    public = services.public_environment(demo["environment"])
    text = repr(public)
    assert "LITERAL_REF" not in text
    assert "NESTED_LITERAL" not in text
    assert public["model_config"]["api_key_ref"] == "***"
    assert public["model_config"]["env_ref"] == "env:DEMO_KEY"
    assert public["credential_status"]["model_config"]["api_key_ref"] is True
    api = client.get(f"/api/v3/environments/{demo['environment']['id']}")
    assert api.status_code == 200
    assert "NESTED_LITERAL" not in api.text
    run = services.create_run({"project_id": demo["project"]["id"], "mode": "baseline"})
    report = services.build_report(run["id"])
    assert "LITERAL_REF" not in repr(report)
    assert "NESTED_LITERAL" not in repr(report)
    report_response = client.get(f"/api/v3/runs/{run['id']}/report.json")
    assert report_response.status_code == 200
    assert "LITERAL_REF" not in report_response.text
    assert "NESTED_LITERAL" not in report_response.text


def test_parent_blank_and_masked_values_retain_server_credentials():
    project = persistence.create_project("credential inheritance")
    parent = services.create_environment(
        project["id"], environment_payload(model_config={
            "provider": "demo_scripted", "api_key_ref": "env:PARENT_KEY",
        }), status="published", set_current=True)
    child = services.create_environment(
        project["id"], environment_payload(
            model_config={"provider": "demo_scripted", "api_key_ref": ""},
            parent_id=parent["id"]), parent_id=parent["id"], status="draft")
    assert child["model_config"]["api_key_ref"] == "env:PARENT_KEY"
    published = services.publish_environment(child["id"], set_current=True)
    assert published["model_config"]["api_key_ref"] == "env:PARENT_KEY"
    masked = services.create_environment(
        project["id"], environment_payload(
            model_config={"provider": "demo_scripted", "api_key_ref": "***"},
            parent_id=parent["id"]), parent_id=parent["id"], status="draft")
    assert masked["model_config"]["api_key_ref"] == "env:PARENT_KEY"


def test_parent_does_not_restore_cleared_noncredential_fields():
    project = persistence.create_project("ordinary field inheritance")
    parent = services.create_environment(
        project["id"], environment_payload(model_config={
            "provider": "demo_scripted", "base_url": "http://parent",
            "api_key_ref": "env:PARENT_KEY",
        }), status="published")
    child = services.create_environment(
        project["id"], environment_payload(model_config={
            "provider": "demo_scripted", "base_url": "",
            "api_key_ref": "",
        }), parent_id=parent["id"], status="draft")
    assert child["model_config"]["base_url"] == ""
    assert child["model_config"]["api_key_ref"] == "env:PARENT_KEY"


def test_provider_change_with_empty_credential_does_not_restore_parent_secret():
    project = persistence.create_project("provider empty inheritance")
    parent = services.create_environment(
        project["id"], environment_payload(model_config={
            "provider": "demo_scripted", "api_key_ref": "env:PARENT_KEY",
        }), status="published")
    child = services.create_environment(
        project["id"], environment_payload(model_config={
            "provider": "openai_http", "base_url": "http://local",
            "api_key_ref": "",
        }), parent_id=parent["id"], status="draft")
    assert child["model_config"].get("api_key_ref", "") == ""


def test_provider_change_does_not_inherit_and_masked_without_parent_is_rejected():
    project = persistence.create_project("provider isolation")
    parent = services.create_environment(
        project["id"], environment_payload(model_config={
            "provider": "demo_scripted", "api_key_ref": "env:PARENT_KEY",
        }), status="published")
    with pytest.raises(ValidationError):
        services.create_environment(
            project["id"], environment_payload(model_config={
                "provider": "openai_http", "base_url": "http://local",
                "api_key_ref": "***",
            }), parent_id=parent["id"], status="draft")
    with pytest.raises(ValidationError):
        services.create_environment(
            project["id"], environment_payload(model_config={
                "provider": "demo_scripted", "api_key_ref": "***",
            }), status="draft")


def test_cross_project_parent_is_rejected():
    first = persistence.create_project("first")
    second = persistence.create_project("second")
    parent = services.create_environment(
        first["id"], environment_payload(), status="published")
    with pytest.raises(ValidationError):
        services.create_environment(
            second["id"], environment_payload(), parent_id=parent["id"],
            status="draft")


def test_connection_environment_id_uses_server_config_without_returning_it():
    demo = create_demo_project(env_overrides={
        "model_config": {"provider": "openai_http", "base_url": "http://local",
                          "api_key": "SERVER_ONLY"}
    })
    result = services.test_connection({"environment_id": demo["environment"]["id"]})
    assert result["ok"] is True
    assert persistence.get_environment(demo["environment"]["id"])["model_config"]["api_key"] == "SERVER_ONLY"
    assert "SERVER_ONLY" not in repr(result)


def test_changed_environment_or_dataset_blocks_apply_and_marks_run_stale(client, accepted_run):
    run_id = accepted_run["id"]
    demo_project = persistence.get_project(accepted_run["project_id"])
    child_env = services.create_environment(
        demo_project["id"], environment_payload(), status="published", set_current=True)
    view = services.run_view(run_id)
    assert view["applicability"]["current"] is False
    assert view["can_apply"] is False
    response = client.post(f"/api/v3/runs/{run_id}/apply",
                           json={"expected_current_version": demo_project["current_skill_version_id"]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert child_env["id"] != accepted_run["environment_id"]


def test_changed_dataset_blocks_apply(client, accepted_run):
    project = persistence.get_project(accepted_run["project_id"])
    baseline = project["current_skill_version_id"]
    changed = services.create_dataset(
        project["id"], dataset_payload(filename="replacement.jsonl"))
    view = services.run_view(accepted_run["id"])
    assert view["applicability"]["current"] is False
    assert "数据集已变更" in view["applicability"]["stale_reasons"]
    response = client.post(f"/api/v3/runs/{accepted_run['id']}/apply",
                           json={"expected_current_version": baseline})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert changed["id"] != accepted_run["dataset_id"]


def test_correct_conditions_apply_once_and_old_version_remains_recoverable(client, accepted_run):
    candidate = persistence.get_candidate_for_run(accepted_run["id"])
    project = persistence.get_project(accepted_run["project_id"])
    baseline = project["current_skill_version_id"]
    response = client.post(f"/api/v3/runs/{accepted_run['id']}/apply",
                           json={"expected_current_version": baseline})
    assert response.status_code == 200
    assert response.json()["project"]["current_skill_version_id"] == candidate["skill_version_id"]
    second = client.post(f"/api/v3/runs/{accepted_run['id']}/apply",
                         json={"expected_current_version": baseline})
    assert second.status_code == 409
    restored = client.post(f"/api/v3/skill-versions/{baseline}/activate",
                            json={"expected_current_version": candidate["skill_version_id"]})
    assert restored.status_code == 200
    assert persistence.get_project(project["id"])["current_skill_version_id"] == baseline


def test_activate_skill_version_uses_atomic_expected_version(client, accepted_run):
    candidate = persistence.get_candidate_for_run(accepted_run["id"])
    project = persistence.get_project(accepted_run["project_id"])
    baseline = project["current_skill_version_id"]
    applied = client.post(f"/api/v3/runs/{accepted_run['id']}/apply",
                          json={"expected_current_version": baseline})
    assert applied.status_code == 200
    current = candidate["skill_version_id"]
    stale = client.post(f"/api/v3/skill-versions/{baseline}/activate",
                        json={"expected_current_version": baseline})
    assert stale.status_code == 409
    assert persistence.get_project(project["id"])["current_skill_version_id"] == current


def test_changed_candidate_files_cannot_be_applied(client, accepted_run):
    candidate = persistence.get_candidate_for_run(accepted_run["id"])
    version_id = candidate["skill_version_id"]
    baseline = persistence.get_project(accepted_run["project_id"])["current_skill_version_id"]
    (persistence.skill_files_dir(version_id) / "SKILL.md").write_text("changed after evaluation")
    response = client.post(f"/api/v3/runs/{accepted_run['id']}/apply",
                           json={"expected_current_version": baseline})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "candidate_changed"
    assert persistence.get_project(accepted_run["project_id"])["current_skill_version_id"] == baseline
    assert not persistence.get_decision_for_run(accepted_run["id"]).get("applied_at")
    assert persistence.get_skill_version(version_id)["status"] == "candidate"


def test_apply_cas_detects_dataset_change_after_precheck(client, accepted_run, monkeypatch):
    """A change after service validation must still fail the atomic write."""
    original_cas = persistence.apply_candidate_cas
    project_id = accepted_run['project_id']
    baseline = accepted_run['baseline_skill_version_id']

    def race(*args, **kwargs):
        services.create_dataset(project_id, dataset_payload(filename='raced.jsonl'))
        return original_cas(*args, **kwargs)

    monkeypatch.setattr(persistence, 'apply_candidate_cas', race)
    response = client.post(f"/api/v3/runs/{accepted_run['id']}/apply",
                           json={'expected_current_version': baseline})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'version_conflict'
    assert persistence.get_project(project_id)['current_skill_version_id'] == baseline
    assert not persistence.get_decision_for_run(accepted_run['id'])['applied_at']
