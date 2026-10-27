"""Old successful baselines must not bypass the new execution checks."""

from app import hashing, services


def test_progressive_loop_revision_invalidates_previous_baseline():
    previous = hashing.hash_json({
        "skill_version_id": "skill", "environment_id": "env",
        "dataset_id": "dataset", "scorer_version": "score-v1", "repeats": 1,
        "finish_policy": "finish-v2-business-independent",
        "skill_load_policy": "select-load-v1",
    })
    current = services.run_fingerprint("skill", "env", "dataset", "score-v1", 1)
    assert current != previous
    assert current == services.run_fingerprint("skill", "env", "dataset", "score-v1", 1)
