"""Read-only model-output APIs support new captures and honestly labelled history."""
import json

import pytest

from app import persistence, services
from app.db import transaction
from tests.conftest import create_demo_project, complete_run


def output_url(run_id, seq):
    return f"/api/v3/runs/{run_id}/events/{seq}/model-output"


def test_new_model_output_is_visible_without_changing_case_results(client, baseline_run):
    run_id = baseline_run["id"]
    events = persistence.list_events(run_id, limit=1000)
    event = next(item for item in events if item["type"] == "model_call_finished")
    before = persistence.get_case_results_for_run(run_id)
    response = client.get(output_url(run_id, event["seq"]))
    assert response.status_code == 200
    result = response.json()
    assert result["source"] == "captured"
    assert result["scope"] == "model_call"
    assert result["response"]["tool_calls"]
    assert before == persistence.get_case_results_for_run(run_id)


def test_legacy_event_exposes_case_result_with_clear_scope(client, baseline_run):
    run_id = baseline_run["id"]
    case = persistence.get_case_results_for_run(run_id)[0]
    event = persistence.append_event(run_id, "model_call_finished", case_id=case["case_id"],
                                     stage="baseline_eval", payload={"tool_call_count": 1})
    result = client.get(output_url(run_id, event["seq"])).json()
    assert result["source"] == "legacy_case_result"
    assert result["scope"] == "case_result"
    assert result["response"] is None
    assert result["case_results"][0]["phase"] == "baseline"
    assert "不代表本次单独模型回复" in result["message"]
    assert "expected" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("wrapped", [False, True])
def test_large_output_artifact_is_loaded_on_demand(client, baseline_run, wrapped):
    run_id = baseline_run["id"]
    text = "模型返回。" * 3000
    saved = {"content": text, "tool_calls": [], "finish_reason": "stop"}
    if wrapped:
        saved = {"response": saved}
    artifact = persistence.save_artifact(run_id, "model_response", json.dumps(saved, ensure_ascii=False).encode())
    event = persistence.append_event(run_id, "model_call_finished", payload={
        "artifacts": [{"id": artifact["id"], "kind": "model_response"}]})
    response = client.get(output_url(run_id, event["seq"]))
    assert response.status_code == 200
    assert response.json()["response"]["content"] == text
    assert str(artifact["path"]) not in response.text


def test_output_artifact_cannot_be_loaded_from_another_run(client, baseline_run, demo):
    artifact = persistence.save_artifact(baseline_run["id"], "model_response", b'{"content":"private"}')
    other = services.create_run({"project_id": demo["project"]["id"], "mode": "baseline"})
    event = persistence.append_event(other["id"], "model_call_finished", payload={
        "artifacts": [{"id": artifact["id"], "kind": "model_response"}]})
    assert client.get(output_url(other["id"], event["seq"])).status_code == 404


def test_missing_output_and_invalid_event_are_explicit(client, baseline_run):
    run_id = baseline_run["id"]
    event = persistence.append_event(run_id, "model_call_finished", payload={})
    response = client.get(output_url(run_id, event["seq"]))
    assert response.json()["source"] == "unavailable"
    assert response.json()["response"] is None
    invalid = persistence.append_event(run_id, "run_completed")
    assert client.get(output_url(run_id, invalid["seq"])).status_code == 422
    assert client.get(output_url(run_id, "bad")).status_code == 422
    assert client.get(output_url(run_id, 100000)).status_code == 404


def test_historical_optimizer_preview_does_not_claim_full_response(client, baseline_run):
    event = persistence.append_event(baseline_run["id"], "optimizer_call_finished",
                                     payload={"output_preview": "old preview"})
    result = client.get(output_url(baseline_run["id"], event["seq"])).json()
    assert result["response"]["content"] == "old preview"
    assert "可能已截断" in result["message"]


def test_legacy_display_redacts_credentials_and_excludes_reasoning(client):
    demo = create_demo_project(env_overrides={"model_config": {
        "provider": "demo_scripted", "api_key_ref": {"value": "LOCAL_LEGACY_SECRET"}}})
    run = complete_run(demo, "baseline")
    row = persistence.get_case_results_for_run(run["id"])[0]
    actual = {"raw_output": "echo LOCAL_LEGACY_SECRET", "reasoning_content": "hidden reasoning"}
    with transaction() as conn:
        conn.execute("UPDATE case_results SET actual_json=? WHERE id=?", (json.dumps(actual), row["id"]))
    event = persistence.append_event(run["id"], "model_call_finished", case_id=row["case_id"])
    response = client.get(output_url(run["id"], event["seq"]))
    assert response.status_code == 200
    assert "LOCAL_LEGACY_SECRET" not in response.text
    assert "hidden reasoning" not in response.text
    assert "echo ***" in response.text


def test_event_emitter_and_output_api_roundtrip_large_unicode_response(client, baseline_run):
    from app.run_events import EventEmitter
    text = "完整返回。" * 3000 + "END_OF_OUTPUT"
    event = EventEmitter(baseline_run["id"]).emit("model_call_finished", {
        "response": {"content": text, "tool_calls": [], "finish_reason": "stop"},
        "response_summary": "长文本"})
    assert "response" not in event["payload"]
    artifact = event["payload"]["artifacts"][0]
    assert artifact["size"] > 8192 and "path" not in artifact
    result = client.get(output_url(baseline_run["id"], event["seq"])).json()
    assert result["source"] == "captured"
    assert result["response"]["content"] == text
