"""Opt-in NDJSON trial stream exposes execution events before the result."""

import json


def test_trial_ndjson_stream_contains_events_and_single_result(client, demo, optimize_run):
    from app import persistence

    row = persistence.get_case(demo["dataset"]["id"], "opt_001")
    response = client.post(
        f"/api/v3/projects/{demo['project']['id']}/debug-cases",
        json={"case_id": row["case_id"]},
        headers={"accept": "application/x-ndjson"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert lines
    assert lines[-1]["type"] == "result"
    assert sum(item["type"] == "result" for item in lines) == 1
    assert any(item["type"] == "event" for item in lines[:-1])
    assert lines[-1]["result"]["trial"] is True


def test_trial_default_response_remains_json(client, demo):
    response = client.post(
        f"/api/v3/projects/{demo['project']['id']}/debug-cases",
        json={"query": "列出任务"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["trial"] is True
