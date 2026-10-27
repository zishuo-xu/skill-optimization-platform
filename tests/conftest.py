"""Shared pytest fixtures: isolated data dir, demo project, run helpers."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Configure an isolated data root before importing application modules.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="skillopt-tests-"))
os.environ["SKILLOPT_DATA_DIR"] = str(_TMP_ROOT / "data")
os.environ["SKILLOPT_DB_PATH"] = str(_TMP_ROOT / "data" / "app.db")
os.environ["SKILLOPT_DISABLE_WORKER"] = "1"

from app import config, db, persistence, services, single_round, skill_package  # noqa: E402
from app.worker import Worker  # noqa: E402


@pytest.fixture(autouse=True)
def clean_workspace():
    db.reset_connection()
    if _TMP_ROOT.exists():
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    config.ensure_dirs()
    db.init_db()
    yield
    db.reset_connection()


@pytest.fixture
def client():
    from starlette.testclient import TestClient
    from app.server import create_app

    app = create_app(start_worker=False)
    with TestClient(app) as test_client:
        yield test_client


def build_package_zip() -> bytes:
    root = config.SAMPLES_DIR / "skill_package"
    files = {path.relative_to(root).as_posix(): path.read_bytes()
             for path in sorted(root.rglob("*")) if path.is_file()}
    return skill_package.build_zip(files)


def environment_payload(**overrides):
    template = json.loads((config.SAMPLES_DIR / "environment.json").read_text(
        encoding="utf-8"))
    template["prompt"] = (config.SAMPLES_DIR / "system_prompt.md").read_text(
        encoding="utf-8")
    template.update(overrides)
    return template


def dataset_payload(filename: str = "dataset.jsonl", content: str | None = None):
    if content is None:
        content = (config.SAMPLES_DIR / "dataset.jsonl").read_text(encoding="utf-8")
    return {
        "filename": filename,
        "content": content,
        "split_config": {"optimize": 0.7, "validation": 0.2, "final_test": 0.1},
        "seed": 42,
        "source_name": "pytest dataset",
    }


def create_demo_project(env_overrides=None, dataset_content=None):
    project = persistence.create_project("测试项目")
    version = services.create_skill_version(project["id"], zip_bytes=build_package_zip(),
                                            status="published", set_current=True,
                                            message="pytest")
    env = services.create_environment(
        project["id"], environment_payload(**(env_overrides or {})),
        status="published", set_current=True)
    dataset = services.create_dataset(
        project["id"], dataset_payload(content=dataset_content))
    return {"project": persistence.get_project(project["id"]),
            "skill_version": version, "environment": env, "dataset": dataset}


def run_sync(run_id: str, timeout_runs: int = 1) -> dict:
    worker = Worker(executor=single_round.execute_run)
    for _ in range(timeout_runs):
        if not worker.process_once():
            break
    return persistence.get_run(run_id)


def start_run(demo, mode: str, **payload) -> dict:
    run = services.create_run({"project_id": demo["project"]["id"], "mode": mode,
                               **payload})
    return run


def complete_run(demo, mode: str, **payload) -> dict:
    run = start_run(demo, mode, **payload)
    return run_sync(run["id"])


@pytest.fixture
def demo():
    return create_demo_project()


@pytest.fixture
def baseline_run(demo):
    return complete_run(demo, "baseline")


@pytest.fixture
def optimize_run(demo, baseline_run):
    return complete_run(demo, "optimize")


@pytest.fixture
def accepted_run(demo, optimize_run):
    return complete_run(demo, "validate", source_run_id=optimize_run["id"])
