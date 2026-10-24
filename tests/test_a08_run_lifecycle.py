"""A08: 幂等启动、事件顺序、取消、Worker 崩溃/租约恢复后任务一致。"""

from __future__ import annotations

from app import persistence, single_round
from app.worker import Worker
from tests.conftest import create_demo_project, run_sync


def test_idempotency_and_event_history(client, demo):
    project_id = demo["project"]["id"]
    headers = {"Idempotency-Key": "idem-A08-1"}
    first = client.post("/api/v3/runs", json={"project_id": project_id,
                                              "mode": "baseline"}, headers=headers)
    assert first.status_code == 202
    run_id = first.json()["id"]
    second = client.post("/api/v3/runs", json={"project_id": project_id,
                                               "mode": "baseline"}, headers=headers)
    assert second.status_code == 202
    assert second.json()["id"] == run_id
    assert len([run for run in persistence.list_runs(project_id=project_id)["items"]
                if run["id"] == run_id]) == 1

    assert client.get(f"/api/v3/runs/{run_id}/event-history").json()["items"] == []
    run_sync(run_id)
    events = client.get(f"/api/v3/runs/{run_id}/event-history?limit=500").json()["items"]
    assert events, "run must persist events"
    seqs = [event["seq"] for event in events]
    assert seqs == list(range(1, len(seqs) + 1))
    cursor = events[5]["seq"]
    tail = client.get(
        f"/api/v3/runs/{run_id}/event-history?after_seq={cursor}").json()["items"]
    assert all(event["seq"] > cursor for event in tail)
    assert tail[0]["seq"] == cursor + 1
    detail = client.get(f"/api/v3/runs/{run_id}").json()
    assert detail["status"] == "completed"


def test_cancel_queued_run(client, demo):
    run = client.post("/api/v3/runs", json={"project_id": demo["project"]["id"],
                                            "mode": "baseline"}).json()
    cancelled = client.post(f"/api/v3/runs/{run['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    # Nothing left for the worker to pick up.
    worker = Worker(executor=single_round.execute_run)
    assert worker.process_once() is False


def test_stale_lease_is_recovered_and_completed(demo):
    from app import services

    run = services.create_run({"project_id": demo["project"]["id"],
                               "mode": "baseline"})
    claimed = persistence.claim_run("dead-worker", 30)
    assert claimed and claimed["id"] == run["id"]
    persistence.update_run(run["id"],
                           lease_expires_at="2000-01-01T00:00:00.000Z")
    recovered = persistence.recover_stale_runs()
    assert run["id"] in recovered
    assert persistence.get_run(run["id"])["status"] == "queued"
    run_sync(run["id"])
    final = persistence.get_run(run["id"])
    assert final["status"] in ("completed", "failed")
    assert final["status"] == "completed"
    events = persistence.list_events(run["id"], limit=1000)
    assert events and events[-1]["type"] in ("run_completed", "run_failed")


def test_worker_restart_keeps_events_consistent(demo):
    """A second worker claims a queued run and its events are append-only."""
    from app import services

    run = services.create_run({"project_id": demo["project"]["id"],
                               "mode": "baseline"})
    first_worker = Worker(executor=single_round.execute_run)
    # Simulate a worker that claimed then crashed before any event was written.
    assert first_worker.process_once() is True
    run = persistence.get_run(run["id"])
    assert run["status"] == "completed"
    before = persistence.list_events(run["id"], limit=2000)
    # Re-running a terminal run must not duplicate events.
    single_round.execute_run(run["id"])
    after = persistence.list_events(run["id"], limit=2000)
    assert [event["seq"] for event in after] == [event["seq"] for event in before]
