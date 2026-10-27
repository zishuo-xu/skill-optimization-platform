"""A20: 凭据脱敏；新增观测不改变评分与接受结论。"""

from __future__ import annotations

import io
import json
import zipfile

from app import persistence
from tests.conftest import (complete_run, create_demo_project, environment_payload,
                            run_sync)


def test_credentials_never_appear_in_logs_reports_or_exports(client):
    payload = environment_payload()
    payload["model_config"] = dict(payload["model_config"])
    payload["model_config"]["api_key"] = "SECRET-A20-KEY"
    payload["model_config"]["user"] = "gateway-user"
    demo = create_demo_project(env_overrides={"model_config": payload["model_config"]})
    project_id = demo["project"]["id"]
    baseline = complete_run(demo, "baseline")

    run_text = client.get(f"/api/v3/runs/{baseline['id']}").text
    events_text = client.get(
        f"/api/v3/runs/{baseline['id']}/event-history?limit=1000").text
    report_text = client.get(f"/api/v3/runs/{baseline['id']}/report.json").text
    env_text = client.get(
        f"/api/v3/environments/{demo['environment']['id']}").text
    for text in (run_text, events_text, report_text):
        assert "SECRET-A20-KEY" not in text
    assert "SECRET-A20-KEY" not in env_text
    assert '"***"' in env_text or "***" in env_text

    exported = client.get(
        f"/api/v3/skill-versions/{demo['skill_version']['id']}/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        for name in archive.namelist():
            assert "SECRET-A20-KEY" not in archive.read(name).decode(
                "utf-8", errors="ignore")

    # Observability additions must not change scoring: baseline metrics equal the
    # deterministic expected values for the sample dataset.
    metrics = baseline["metrics"]["baseline"]
    assert metrics["routing"]["evaluated"] == 30
    assert metrics["tool_args"]["evaluated"] == 30
    assert metrics["joint"]["total_cases"] == 33
    assert metrics["execution"]["infra_errors"] == 0


def test_validation_and_final_answers_not_leaked_to_execution():
    from app import execution
    from app.adapters.demo_scripted import DemoScriptedAdapter
    from app.skill_package import import_package
    from pathlib import Path
    from app import config

    case = {
        "query": "查询项目 P123 中进行中的任务",
        "context": {},
        "expected": {"action": "tool_call",
                     "routes": [["task_query"]],
                     "tool_calls": [{"name": "list_tasks",
                                     "arguments": {"project_id": "P123",
                                                   "status": "SECRET_EXPECTED_VALUE"}}]},
        "fixtures": [{"name": "list_tasks", "match": {}, "response": {"ok": True}}],
    }
    package = import_package(__import__("tests.conftest", fromlist=["build_package_zip"])
                             .build_package_zip())
    snapshot = config.TMP_DIR / "case_request_a20"
    snapshot.mkdir(parents=True, exist_ok=True)
    for path, data in package["files"].items():
        target = snapshot / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    request = {
        "query": case["query"], "context": case["context"],
        "skill_snapshot_path": str(snapshot),
        "system_prompt": (config.SAMPLES_DIR / "system_prompt.md").read_text(
            encoding="utf-8"),
        "model_config": {"provider": "demo_scripted"},
        "fixtures": case["fixtures"], "limits": {"max_model_calls": 6},
        "run_id": "run_a20", "case_id": "case_a20",
        "manifest": package["manifest"],
        "allowed_skill_ids": [entry["skill_id"]
                              for entry in package["manifest"]["subskills"]],
    }
    messages = execution.build_messages(request)
    joined = json.dumps(messages, ensure_ascii=False)
    assert "expected" not in joined
    assert "SECRET_EXPECTED_VALUE" not in joined
