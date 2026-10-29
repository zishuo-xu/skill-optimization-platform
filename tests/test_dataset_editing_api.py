"""Public editing workflow preserves published snapshots and rejects stale edits."""
import copy

from app import persistence, services, hashing
from app.db import transaction

FIELDS = ("case_id", "query", "context", "expected", "fixtures", "split", "group_id", "tags")


def editable(row):
    return {key: copy.deepcopy(row.get(key)) for key in FIELDS}


def clone(client, dataset_id):
    response = client.post(f"/api/v3/datasets/{dataset_id}/draft", json={})
    assert response.status_code == 201, response.text
    return response.json()


def put(client, draft, case):
    return client.put(f"/api/v3/datasets/{draft['id']}/cases/{case['case_id']}",
                      json={"case": case, "expected_hash": draft["hash"]})


def publish(client, draft, current):
    return client.post(f"/api/v3/datasets/{draft['id']}/publish", json={
        "expected_hash": draft["hash"], "expected_current_version": current})


def test_full_edit_publish_flow_preserves_run_and_invalidates_baseline(client, demo, baseline_run):
    original = demo["dataset"]
    old_rows = persistence.all_cases(original["id"])
    old_results = copy.deepcopy(persistence.get_case_results_for_run(baseline_run["id"]))
    draft = clone(client, original["id"])
    assert draft["status"] == "draft" and draft["parent_id"] == original["id"]
    assert [editable(row) for row in persistence.all_cases(draft["id"])] == [editable(row) for row in old_rows]
    assert persistence.get_project(demo["project"]["id"])["current_dataset_id"] == original["id"]
    selected = next(row for row in old_rows if row['split'] == 'optimize' and any("project_id" in call.get("arguments", {})
                    for call in row["expected"].get("tool_calls", [])))
    case = editable(selected)
    case["query"] += "（在线修订）"
    case["expected"]["tool_calls"][0]["arguments"]["project_id"] = "P-EDITED"
    for fixture in case["fixtures"]:
        if "project_id" in (fixture.get("match") or {}):
            fixture["match"]["project_id"] = "P-EDITED"
    saved = put(client, draft, case)
    assert saved.status_code == 200, saved.text
    updated = saved.json()["dataset"]
    assert updated["hash"] != draft["hash"]
    assert updated["hash"] == hashing.hash_json([persistence._case_payload(row) for row in persistence.all_cases(draft["id"])])
    assert updated["import_report"]["stats"]["total"] == len(old_rows)
    assert saved.json()["case"]["query"] == case["query"]
    assert services.overview(demo["project"]["id"])["baseline"]["compatible"]
    result = publish(client, updated, original["id"])
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "published"
    assert persistence.get_project(demo["project"]["id"])["current_dataset_id"] == draft["id"]
    assert not services.overview(demo["project"]["id"])["baseline"]["compatible"]
    assert persistence.get_run(baseline_run["id"])["dataset_id"] == original["id"]
    assert persistence.all_cases(original["id"]) == old_rows
    assert persistence.get_case_results_for_run(baseline_run["id"]) == old_results
    assert put(client, result.json(), case).status_code == 409
    assert publish(client, updated, original["id"]).status_code == 409


def test_stale_edit_and_stale_publish_do_not_partially_write(client, demo):
    draft = clone(client, demo["dataset"]["id"])
    case = editable(persistence.all_cases(draft["id"])[0])
    case["query"] += " first"
    saved = put(client, draft, case)
    assert saved.status_code == 200
    current = saved.json()["dataset"]
    before = copy.deepcopy(persistence.all_cases(draft["id"]))
    case["query"] += " stale"
    assert put(client, draft, case).status_code == 409
    assert persistence.all_cases(draft["id"]) == before
    assert publish(client, draft, demo["dataset"]["id"]).status_code == 409
    assert persistence.get_dataset(draft["id"])["status"] == "draft"
    assert publish(client, current, "another-current-id").status_code == 409
    assert persistence.get_dataset(draft["id"])["status"] == "draft"
    assert persistence.get_project(demo["project"]["id"])["current_dataset_id"] == demo["dataset"]["id"]


def test_invalid_case_and_group_conflict_preserve_all_other_rows(client):
    project = persistence.create_project("groups")
    cases = [{"case_id": name, "query": name, "expected": {"action": "no_action"},
              "split": split, "group_id": group, "context": {}, "fixtures": [], "tags": []}
             for name,split,group in (("a","optimize","g1"),("b","validation","g2"))]
    original = persistence.create_dataset(project["id"], cases, set_current=True)
    draft = clone(client, original["id"])
    row = editable(persistence.get_case(draft["id"], "b"))
    before = copy.deepcopy(persistence.all_cases(draft["id"]))
    row["group_id"] = "  g1  "
    conflict = put(client, draft, row)
    assert conflict.status_code == 422, conflict.text
    assert persistence.all_cases(draft["id"]) == before
    row["group_id"] = "g2"
    row["query"] = ""
    assert put(client, draft, row).status_code == 422
    row["query"] = "b"
    row["split"] = "typo"
    assert put(client, draft, row).status_code == 422
    row["split"] = "validation"
    assert client.put(f"/api/v3/datasets/{draft['id']}/cases/b",
                      json={"case": {**row, "case_id": "renamed"}, "expected_hash": draft["hash"]}).status_code == 422
    assert persistence.get_dataset(draft["id"])["hash"] == draft["hash"]


def test_publish_revalidates_tampered_data_and_does_not_shuffle_splits(client, demo):
    draft = clone(client, demo["dataset"]["id"])
    rows = persistence.all_cases(draft["id"])
    with transaction() as conn:
        conn.execute("UPDATE cases SET query='' WHERE dataset_id=? AND case_id=?", (draft["id"], rows[0]["case_id"]))
    assert publish(client, draft, demo["dataset"]["id"]).status_code == 422
    assert persistence.get_dataset(draft["id"])["status"] == "draft"
    with transaction() as conn:
        conn.execute("UPDATE cases SET query=?,split='invalid' WHERE dataset_id=? AND case_id=?",
                     (rows[0]["query"], draft["id"], rows[0]["case_id"]))
    result = publish(client, draft, demo["dataset"]["id"])
    assert result.status_code == 422, result.text
    assert persistence.get_case(draft["id"], rows[0]["case_id"])["split"] == "invalid"
    assert persistence.get_dataset(draft["id"])["status"] == "draft"


def test_publish_requires_explicit_hash_and_pointer_guard(client, demo):
    draft = clone(client, demo["dataset"]["id"])
    result = client.post(f"/api/v3/datasets/{draft['id']}/publish", json={})
    assert result.status_code == 422
    assert persistence.get_dataset(draft["id"])["status"] == "draft"
