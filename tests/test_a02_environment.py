"""A02: Prompt 发布后不可原地改；环境变更生成新版本并让基线缓存失效。"""

from __future__ import annotations

from app import persistence
from tests.conftest import complete_run, create_demo_project, environment_payload


def test_environment_versions_are_immutable_and_invalidate_baseline(demo, baseline_run):
    project_id = demo["project"]["id"]
    baseline = baseline_run
    original_env = demo["environment"]
    assert baseline["baseline_cache_hit"] == 0

    # The baseline cache is compatible right now.
    from app import services
    overview = services.overview(project_id)
    assert overview["baseline"]["compatible"] is True
    assert overview["baseline"]["run_id"] == baseline["id"]

    # Publishing a new environment version creates a new immutable version; the
    # previous row and its stored prompt hash stay untouched.
    payload = environment_payload()
    payload["prompt"] = payload["prompt"] + "\n<!-- 新版本 Prompt -->\n"
    new_env = services.create_environment(project_id, payload, status="published",
                                          parent_id=original_env["id"],
                                          set_current=True)
    assert new_env["version_seq"] == original_env["version_seq"] + 1
    assert persistence.get_environment(original_env["id"])["prompt"] == \
        original_env["prompt"]
    assert new_env["hash"] != original_env["hash"]

    overview = services.overview(project_id)
    assert overview["baseline"]["compatible"] is False
    assert "兼容基线" in overview["completeness"]["items"]["baseline"]["detail"]

    # Optimize run now re-runs the baseline instead of reusing stale results.
    run = complete_run(demo, "optimize")
    assert run["baseline_cache_hit"] == 0
    events = persistence.list_events(run["id"], limit=500)
    cache_events = [event for event in events
                    if event["type"] == "stage_completed"
                    and (event.get("payload") or {}).get("cache_hit") is True]
    assert not cache_events
    baseline_events = [event for event in events
                       if event["type"] == "stage_started"
                       and event.get('stage') == 'results_preparation'
                       and (event.get('payload') or {}).get('execute_count', 0) > 0]
    assert baseline_events


def test_optimizer_cannot_modify_environment_or_manifest():
    from app import patch
    from tests.conftest import build_package_zip
    from app import skill_package

    package = skill_package.import_package(build_package_zip())
    files = package["files"]
    # Only existing Markdown files are editable: manifest and non-md are refused.
    patched, issues = patch.apply_edits(
        files,
        [{"path": "manifest.json", "op": "replace",
          "search": '"main": "SKILL.md"', "replace": '"main": "OTHER.md"'}],
        allowed_paths=sorted(files))
    assert issues and patched == files
    patched, issues = patch.apply_edits(
        files,
        [{"path": "references/task_query.md", "op": "replace",
          "search": "用途：", "replace": "用途：env "}],
        allowed_paths=["SKILL.md"])
    assert issues

    # Static check rejects files added by a candidate.
    added = dict(files)
    added["references/evil.md"] = b"# evil\n"
    result = patch.static_check(files, added, package["manifest"],
                                ["case_001"])
    assert result["ok"] is False
