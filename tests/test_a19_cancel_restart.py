"""A19: 取消或进程重启后不存在永久执行中的步骤。"""

from __future__ import annotations

import json
import threading
import time

from app import config, persistence, services
from tests.conftest import create_demo_project, run_sync


def _large_dataset_content(count: int = 120) -> str:
    lines = []
    base_cases = [json.loads(line) for line in
                  (config.SAMPLES_DIR / "dataset.jsonl").read_text(
                      encoding="utf-8").splitlines() if line.strip()]
    optimize_cases = [case for case in base_cases if case["split"] == "optimize"]
    for index in range(count):
        template = optimize_cases[index % len(optimize_cases)]
        case = json.loads(json.dumps(template))
        case["id"] = f"perf_{index:04d}"
        case["query"] = f"{case['query']}（压力用例 {index}）"
        case["split"] = "optimize"
        case["group_id"] = f"perf_group_{index % 10}"
        lines.append(json.dumps(case, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def test_cancelled_run_has_no_running_step():
    demo = create_demo_project(dataset_content=_large_dataset_content())
    run = services.create_run(
        {"project_id": demo["project"]["id"], "mode": "baseline"})
    thread = threading.Thread(target=run_sync, args=(run["id"],), daemon=True)
    thread.start()
    time.sleep(0.05)
    persistence.request_cancel(run["id"])
    thread.join(timeout=60)
    final = persistence.get_run(run["id"])
    assert final["status"] in ("cancelled", "completed")
    assert final["status"] != "running"
    if final["status"] == "cancelled":
        assert final["cancel_requested_at"]
        events = persistence.list_events(run["id"], limit=2000)
        assert any(event["type"] == "run_cancelled" for event in events)
    # The worker must not leave a queued/running row behind.
    assert persistence.claim_run("audit-worker") is None


def test_crash_recovery_marks_interrupted_and_requeues(demo):
    run = services.create_run({"project_id": demo["project"]["id"],
                               "mode": "baseline"})
    claimed = persistence.claim_run("crashed-worker", 10)
    assert claimed["id"] == run["id"]
    persistence.update_run(run["id"],
                           lease_expires_at="2000-01-01T00:00:00.000Z")
    recovered = persistence.recover_stale_runs()
    assert run["id"] in recovered
    assert persistence.get_run(run["id"])["status"] == "queued"
    run_sync(run["id"])
    final = persistence.get_run(run["id"])
    assert final["status"] == "completed"
    # Every started case event has a terminal counterpart or belongs to a retried
    # run; the run itself is terminal and not stuck in a stage.
    assert final["finished_at"]
