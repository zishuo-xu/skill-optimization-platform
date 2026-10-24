"""A14: 基线缓存复用条件与失效（环境/适配器/数据变化都会让缓存失效）。"""

from __future__ import annotations

import json

from app import persistence, services
from tests.conftest import complete_run, create_demo_project, environment_payload


def _cache_hit_events(run_id):
    return [event for event in persistence.list_events(run_id, limit=2000)
            if event["type"] == "stage_completed"
            and (event.get("payload") or {}).get("cache_hit") is True]


def test_baseline_cache_reuse_and_invalidation(demo, baseline_run):
    first = complete_run(demo, "optimize")
    assert first["baseline_cache_hit"] == 1
    assert _cache_hit_events(first["id"])

    # Changing the environment version (request template/stream parser included in
    # adapter_hash) invalidates the compatible baseline.
    payload = environment_payload()
    payload["model_config"] = dict(payload["model_config"])
    payload["model_config"]["request_template"] = {"template": "changed"}
    services.create_environment(demo["project"]["id"], payload, status="published",
                                set_current=True)
    third = complete_run(demo, "optimize")
    assert third["baseline_cache_hit"] == 0
    assert not _cache_hit_events(third["id"])

    # New dataset version also invalidates the cache.
    from pathlib import Path as _Path

    dataset = services.create_dataset(demo["project"]["id"], {
        "filename": "dataset.jsonl",
        "content": (_Path(services.config.SAMPLES_DIR / "dataset.jsonl")
                    .read_text(encoding="utf-8")),
        "split_config": {"optimize": 0.7, "validation": 0.2, "final_test": 0.1},
        "seed": 7,
        "source_name": "dataset v2",
    })
    assert dataset["version_seq"] == demo["dataset"]["version_seq"] + 1
    fourth = complete_run(demo, "optimize")
    assert fourth["baseline_cache_hit"] == 0


def test_fingerprint_covers_skill_environment_dataset_scorer_repeats():
    fingerprint = services.run_fingerprint("sv1", "env1", "ds1", "score-v1", 1)
    assert fingerprint != services.run_fingerprint("sv2", "env1", "ds1", "score-v1", 1)
    assert fingerprint != services.run_fingerprint("sv1", "env2", "ds1", "score-v1", 1)
    assert fingerprint != services.run_fingerprint("sv1", "env1", "ds2", "score-v1", 1)
    assert fingerprint != services.run_fingerprint("sv1", "env1", "ds1", "score-v2", 1)
    assert fingerprint != services.run_fingerprint("sv1", "env1", "ds1", "score-v1", 3)
