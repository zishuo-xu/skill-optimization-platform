"""End-to-end checks for the expanded image Skill sample.

These tests deliberately exercise the frozen execution protocol and scorer,
instead of checking only the JSON source or duplicating adapter parsing rules.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from app import config, dataset, execution, optimizer, patch, persistence, services, skill_package, single_round
from app.adapters.registry import build_adapter
from app.evaluation import score_case
from app.worker import Worker
from app.adapters.image_params import contract_params, parse_contract


def _samples() -> Path:
    return Path(__file__).resolve().parent.parent / "app" / "samples"


def _image_fixture_data() -> tuple[list[dict], dict]:
    samples = _samples()
    items, fmt = dataset.parse_payload(
        (samples / "image_dataset.jsonl").read_bytes(), "image_dataset.jsonl")
    assert fmt == "jsonl"
    manifest = json.loads(
        (samples / "image_skill_package" / "manifest.json").read_text(encoding="utf-8"))
    prepared = dataset.validate_and_prepare(
        items, manifest=manifest,
        split_config={"optimize": 0.7, "validation": 0.2, "final_test": 0.1},
        seed=42)
    assert prepared["errors"] == []
    return prepared["cases"], manifest


def _case_request(case: dict, manifest: dict, root: Path) -> dict:
    return {
        "query": case["query"],
        "context": case.get("context") or {},
        "skill_snapshot_path": str(root),
        "system_prompt": (_samples() / "image_system_prompt.md").read_text(encoding="utf-8"),
        "model_config": {"provider": "demo_scripted", "model": "demo-image-v1", "domain": "image"},
        "request_template": {},
        "fixtures": case.get("fixtures") or [],
        "limits": {"max_model_calls": 20, "max_steps": 20, "case_timeout_s": 10,
                   "max_context_chars": 60000},
        "loader_config": {"mode": "on_demand", "max_steps": 20},
        "run_id": "image_sample_test",
        "case_id": case["case_id"],
        "manifest": manifest,
        "allowed_skill_ids": [entry["skill_id"] for entry in manifest["subskills"]],
    }


def test_image_package_import_export_contains_all_references():
    root = _samples() / "image_skill_package"
    files = {path.relative_to(root).as_posix(): path.read_bytes()
             for path in sorted(root.rglob("*")) if path.is_file()}
    archive = skill_package.build_zip(files)
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        names = set(package.namelist())
        assert "SKILL.md" in names and "manifest.json" in names
        assert len([name for name in names if name.startswith("references/")]) == 13
        manifest = json.loads(package.read("manifest.json"))
        assert len(manifest["subskills"]) == 13
        assert all(item["path"] in names for item in manifest["subskills"])


def test_image_dataset_coverage_and_protocol_shape():
    cases, manifest = _image_fixture_data()
    by_split = {split: sum(case["split"] == split for case in cases)
                for split in ("optimize", "validation", "final_test")}
    assert len(cases) == 117
    assert by_split == {"optimize": 52, "validation": 34, "final_test": 31}
    skill_ids = [item["skill_id"] for item in manifest["subskills"]]
    for skill_id in skill_ids:
        assert sum(case["split"] == "optimize" and skill_id in case["expected"]["routes"][0]
                   for case in cases if case["expected"]["routes"]) >= 2
        assert any(case["split"] == "validation" and skill_id in case["expected"]["routes"][0]
                   for case in cases if case["expected"]["routes"])
        assert any(case["split"] == "final_test" and skill_id in case["expected"]["routes"][0]
                   for case in cases if case["expected"]["routes"])
    assert sum(case["expected"]["action"] == "clarify" for case in cases) == 18
    assert sum(case["expected"]["action"] == "no_action" for case in cases) == 3
    assert any(case["expected"].get("route_ordered") and len(case["expected"]["routes"][0]) == 3
               for case in cases)
    # Compact tool entries use the user-facing wrapper and normalize to full arguments.
    for case in cases:
        for call in case["expected"]["tool_calls"]:
            assert set(call["arguments"]) == {"method", "script", "request", "args"}
            assert call["arguments"]["script"] == "handlers/photo_ops.py"
            request = call["arguments"]["request"]
            assert 1 <= request["currentStep"] <= request["totalSteps"] <= 3
            assert "image_ids" not in call["arguments"]["args"]


@pytest.mark.parametrize('reference', sorted((_samples() / 'image_skill_package' / 'references').glob('*.md')),
                         ids=lambda path: path.stem)
def test_reference_example_is_executable_and_matches_declared_parameters(reference):
    _cases, manifest = _image_fixture_data()
    content = reference.read_text()
    example = json.loads(re.search(r'## 参数示例.*?```json\s*(.*?)\s*```', content, re.S).group(1))
    skill = next(item['skill_id'] for item in manifest['subskills'] if item['path'].endswith(reference.name))
    case = {'case_id': reference.stem, 'query': example['arguments']['request']['query'],
            'fixtures': [{'name': 'editImage', 'match': {}, 'response': {'code': 0}}]}
    actual = execution.run_case(_case_request(case, manifest, _samples() / 'image_skill_package'),
                                build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))
    assert actual['error'] is None
    assert actual['selected_skills'] == [skill]
    assert actual['tool_calls'] == [{'name': example['functionName'], 'arguments': example['arguments']}]


@pytest.mark.parametrize("case_index", range(117))
def test_each_image_case_executes_and_scores_full_protocol(case_index: int):
    cases, manifest = _image_fixture_data()
    case = cases[case_index]
    root = _samples() / "image_skill_package"
    actual = execution.run_case(_case_request(case, manifest, root),
                                build_adapter({"provider": "demo_scripted",
                                               "model": "demo-image-v1", "domain": "image"}))
    assert actual["error"] is None, (case["case_id"], actual.get("error"))
    score = score_case(case["expected"], actual)
    assert score["status"] == "pass", (case["case_id"], score)
    assert score["joint"] is True
    if case["expected"]["action"] == "tool_call":
        responses = [event["payload"]["response"] for event in actual["events"]
                     if event["type"] == "tool_result"]
        failures = [response for response in responses if response.get("code") != 0]
        if failures:
            assert actual["raw_output"] == failures[0]["message"]
        elif len(actual["tool_calls"]) == 1:
            sid = case["expected"]["routes"][0][0]
            path = next(item["path"] for item in manifest["subskills"] if item["skill_id"] == sid)
            contract = parse_contract((root / path).read_text())
            assert actual["raw_output"] == contract["success_message"]


def test_wrong_method_and_script_are_rejected_by_full_protocol_score():
    cases, _manifest = _image_fixture_data()
    case = next(case for case in cases if case["case_id"] == "image_color_transfer_opt_1")
    actual = {
        "action": "tool_call", "selected_skills": ["image_color_transfer"],
        "tool_calls": [{"name": "editImage", "arguments": {
            "method": "wrongMethod", "script": "wrong.ets",
            "request": {"query": case["query"]},
            "args": case["expected"]["tool_calls"][0]["arguments"]["args"]}}],
    }
    score = score_case(case["expected"], actual)
    assert score["status"] == "fail"
    assert score["tool_args"]["passed"] is False


def test_sky_region_does_not_invent_a_target_color():
    _cases, manifest = _image_fixture_data()
    request = _case_request({"case_id": "missing-color", "query": "把天空换个颜色"},
                            manifest, _samples() / "image_skill_package")
    actual = execution.run_case(request, build_adapter({"provider": "demo_scripted", "domain": "image"}))
    assert actual["action"] == "clarify"
    assert actual["selected_skills"] == ["image_color_transfer"]
    assert actual["missing_fields"] == ["targetColor"]
    assert actual["tool_calls"] == []


def test_clarify_and_too_many_operations_never_execute_business_tool():
    cases, manifest = _image_fixture_data()
    root = _samples() / "image_skill_package"
    for case in cases:
        if case["expected"]["action"] != "clarify":
            continue
        actual = execution.run_case(_case_request(case, manifest, root),
                                    build_adapter({"provider": "demo_scripted",
                                                   "model": "demo-image-v1", "domain": "image"}))
        assert actual["tool_calls"] == [], case["case_id"]
        assert set(actual["missing_fields"]) == set(case["expected"]["missing_fields"]), case["case_id"]


def test_three_operations_load_only_the_required_references_before_business_calls():
    cases, manifest = _image_fixture_data()
    case = next(case for case in cases if case['case_id'] == 'multi_three_ops')
    actual = execution.run_case(
        _case_request(case, manifest, _samples() / 'image_skill_package'),
        build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))
    paths = {s['skill_id']: s['path'] for s in manifest['subskills']}
    expected_ids = case['expected']['routes'][0]
    reads = [(i, e['payload']['path']) for i, e in enumerate(actual['events']) if e['type'] == 'skill_read']
    first_business = next(i for i, e in enumerate(actual['events']) if e['type'] == 'tool_call_started')
    loaded_paths = [e['payload']['path'] for e in actual['events']
                    if e['type'] == 'skill_read' and not e['payload'].get('cached')]
    assert loaded_paths == [paths[sid] for sid in expected_ids]
    assert all(not e['payload'].get('cached') for e in actual['events'] if e['type'] == 'skill_read')
    assert {path for _, path in reads} == set(loaded_paths)
    assert all(i < first_business for i, _ in reads)
    assert [event['skill_id'] for event in actual['route_events']] == expected_ids
    execution_events = [event['type'] for event in actual['events']
                        if event['type'] in ('tool_call_started', 'tool_result')]
    assert execution_events == ['tool_call_started', 'tool_result'] * 3


def test_preloaded_references_are_selected_and_executed_without_reloading():
    cases, manifest = _image_fixture_data()
    case = next(case for case in cases if case['case_id'] == 'multi_three_ops')
    request = _case_request(case, manifest, _samples() / 'image_skill_package')
    request['loader_config']['mode'] = 'preload_all'
    actual = execution.run_case(request, build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))
    assert actual['error'] is None
    assert score_case(case['expected'], actual)['status'] == 'pass'
    assert all(event['payload'].get('cached') for event in actual['events'] if event['type'] == 'skill_read')


@pytest.mark.parametrize('response', [{}, {'code': '0'}, {'code': False}, {'code': None}])
def test_missing_or_invalid_status_cannot_report_success_or_continue(response):
    cases, manifest = _image_fixture_data()
    case = next(case for case in cases if case['case_id'] == 'multi_decolor_filter')
    request = _case_request(case, manifest, _samples() / 'image_skill_package')
    request['fixtures'] = [{'name': 'editImage', 'match': {}, 'response': response}]
    actual = execution.run_case(request, build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))
    assert actual['error'] is None
    assert len(actual['tool_calls']) == 1
    assert actual['raw_output'] == '操作未确认完成，请检查服务返回'


def test_structured_user_source_and_step_query_are_not_copied_wholesale():
    cases, manifest = _image_fixture_data()
    case = next(case for case in cases if case['case_id'] == 'multi_decolor_filter')
    request = _case_request(case, manifest, _samples() / 'image_skill_package')
    request['query'] = json.dumps({'query': case['query'], 'source': 'drag'}, ensure_ascii=False)
    actual = execution.run_case(request, build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))
    assert actual['error'] is None
    requests = [call['arguments']['request'] for call in actual['tool_calls']]
    assert requests == [
        {'query': '先让背景黑白', 'totalSteps': 2, 'currentStep': 1, 'source': 'drag'},
        {'query': '加胶片滤镜', 'totalSteps': 2, 'currentStep': 2, 'source': 'drag'},
    ]


def test_context_source_is_retained_for_structured_query_without_source():
    cases, manifest = _image_fixture_data()
    case = next(case for case in cases if case['case_id'] == 'multi_decolor_filter')
    request = _case_request(case, manifest, _samples() / 'image_skill_package')
    request['query'] = json.dumps({'query': case['query']}, ensure_ascii=False)
    request['context'] = {'source': 'drag'}
    actual = execution.run_case(request, build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))
    assert actual['error'] is None
    assert all(call['arguments']['request']['source'] == 'drag' for call in actual['tool_calls'])


def test_failure_on_second_step_stops_before_third():
    cases, manifest = _image_fixture_data()
    case = next(case for case in cases if case['case_id'] == 'multi_three_ops')
    request = _case_request(case, manifest, _samples() / 'image_skill_package')
    request['fixtures'] = [
        {'name': 'editImage', 'match': {'method': 'crop_frame'}, 'response': {'code': 0}},
        {'name': 'editImage', 'match': {'method': 'recolor_region'}, 'response': {'code': 9, 'message': '第二步失败'}},
        {'name': 'editImage', 'match': {'method': 'apply_preset'}, 'response': {'code': 0}},
    ]
    actual = execution.run_case(request, build_adapter({'provider': 'demo_scripted', 'domain': 'image'}))
    assert actual['error'] is None
    assert [call['arguments']['method'] for call in actual['tool_calls']] == ['crop_frame', 'recolor_region']
    assert actual['raw_output'] == '第二步失败'


def test_contract_defaults_are_respected_for_region_and_background_fields():
    contract = {'fields': {'region': {'type': 'string'}, 'keepSubject': {'type': 'boolean'},
                           'outputBackground': {'type': 'string'}},
                'defaults': {'region': 'subject', 'keepSubject': False, 'outputBackground': 'white'}}
    args, missing = contract_params(contract, '处理图片')
    assert args == contract['defaults']
    assert missing == []


def test_v1_minimal_skill_package_remains_importable(tmp_path):
    root = tmp_path / "v1"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# old skill\n", encoding="utf-8")
    (root / "references" / "legacy.md").write_text("legacy", encoding="utf-8")
    manifest = {"version": 1, "main": "SKILL.md", "subskills": [
        {"skill_id": "legacy_color", "name": "legacy", "path": "references/legacy.md"}]}
    # A legacy package still executes through the old dispatch/reference format.
    (root / "SKILL.md").write_text(
        "# legacy\n\n| skill_id | name | method | keywords |\n| --- | --- | --- | --- |\n"
        "| legacy_color | 换色 | recolor_region | 换色 |\n\n"
        "## 澄清条件\n当 legacy_color 缺少 targetColor 时需要澄清。\n",
        encoding="utf-8")
    (root / "references" / "legacy.md").write_text(
        "# legacy color\n\n## 参数\n- targetColor: string\n\n## 映射\n夕阳 -> 红色\n",
        encoding="utf-8")
    files = {p.relative_to(root).as_posix(): p.read_bytes()
             for p in root.rglob("*") if p.is_file()}
    archive = skill_package.build_zip(files, manifest=manifest)
    assert archive and len(archive) > 0
    request = {
        "query": "帮我换色成夕阳", "skill_snapshot_path": str(root),
        "system_prompt": "legacy", "manifest": manifest,
        "allowed_skill_ids": ["legacy_color"],
        "fixtures": [{"name": "editImage", "match": {}, "response": {"ok": True}}],
        "limits": {"max_model_calls": 6, "max_steps": 6, "case_timeout_s": 5},
        "loader_config": {"mode": "on_demand"}, "run_id": "legacy", "case_id": "legacy-1",
    }
    actual = execution.run_case(request, build_adapter({"provider": "demo_scripted", "model": "demo-image-v1", "domain": "image"}))
    assert actual["error"] is None
    assert actual["tool_calls"][0]["arguments"]["args"] == {"targetColor": "红色"}
    request["query"] = "帮我换色"
    clarified = execution.run_case(request, build_adapter({"provider": "demo_scripted", "model": "demo-image-v1", "domain": "image"}))
    assert clarified["action"] == "clarify"
    assert clarified["tool_calls"] == []


def test_image_baseline_and_noop_optimize_lifecycle_is_isolated():
    """Exercise the real run worker on a tiny image dataset without global data."""
    root = _samples()
    package_root = root / "image_skill_package"
    files = {path.relative_to(package_root).as_posix(): path.read_bytes()
             for path in sorted(package_root.rglob("*")) if path.is_file()}
    project = persistence.create_project("图片生命周期测试")
    services.create_skill_version(project["id"], zip_bytes=skill_package.build_zip(files),
                                  status="published", set_current=True, message="image test")
    env_template = json.loads((root / "image_environment.json").read_text(encoding="utf-8"))
    env_template["prompt"] = (root / "image_system_prompt.md").read_text(encoding="utf-8")
    env_template["run_config"].update({"concurrency": 1, "base_repeats": 1, "validation_repeats": 1})
    services.create_environment(project["id"], env_template, status="published", set_current=True)
    lines = (root / "image_dataset.jsonl").read_text(encoding="utf-8").splitlines()
    tiny = "\n".join([lines[0], lines[2]]) + "\n"
    services.create_dataset(project["id"], {"filename": "tiny-image.jsonl", "content": tiny,
                                             "split_config": {"optimize": 0.5, "validation": 0.5, "final_test": 0},
                                             "seed": 42, "source_name": "tiny image"})
    baseline = services.create_run({"project_id": project["id"], "mode": "baseline"})
    worker = Worker(executor=single_round.execute_run)
    assert worker.process_once() is True
    baseline_done = persistence.get_run(baseline["id"])
    assert baseline_done["status"] == "completed"
    assert baseline_done["metrics"]["baseline"]["joint"]["value"] == 1.0
    optimize = services.create_run({"project_id": project["id"], "mode": "optimize"})
    assert worker.process_once() is True
    optimize_done = persistence.get_run(optimize["id"])
    assert optimize_done["status"] == "completed"
    assert optimize_done.get("decision") != "accept"
    assert "没有失败案例" in optimize_done.get("decision_reason", "")


def test_legacy_mapping_repair_changes_actual_execution(tmp_path):
    """A real failed observation must become a passing execution after patching."""
    manifest = {"version": 1, "main": "SKILL.md", "subskills": [
        {"skill_id": "image_color_transfer", "name": "换色", "path": "references/color.md"}]}
    main = "# editor\n\n| skill_id | name | method | keywords |\n| --- | --- | --- | --- |\n| image_color_transfer | 换色 | recolor_region | 换色 |\n"
    reference = "# color\n\n## 参数\n- targetColor\n\n## 映射\n夕阳 -> 红色\n"
    files = {"SKILL.md": main, "references/color.md": reference}
    root = tmp_path / "legacy-color"
    for name, content in files.items():
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    query = "帮我换色成夕阳色调"
    expected = {"action": "tool_call", "routes": [["image_color_transfer"]],
                "tool_calls": [{"name": "editImage", "arguments": {
                    "method": "recolor_region", "script": "handlers/photo_ops.py",
                    "request": {"query": query}, "args": {"targetColor": "橙色"}}}],
                "missing_fields": []}
    case = {"case_id": "legacy-color-repair", "query": query,
            "fixtures": [{"name": "editImage", "match": {}, "response": {"ok": True}}]}
    request = _case_request(case, manifest, root)
    adapter = build_adapter({"provider": "demo_scripted", "model": "demo-image-v1", "domain": "image"})
    actual = execution.run_case(request, adapter)
    score = score_case(expected, actual)
    assert score["status"] == "fail"
    outcome = optimizer.generate_candidate({
        "baseline_skill_version": {"manifest": manifest, "files": files},
        "allowed_paths": list(files), "failures": [{
            "case_id": case["case_id"], "tags": ["夕阳"], "expected": expected,
            "actual": actual, "score": score}], "optimizer_config": {}}, adapter=None, emit=None)
    assert outcome["status"] == "ok" and outcome["used_fallback"] is True
    patched, issues = patch.apply_edits(files, outcome["patch"]["edits"], list(files))
    assert issues == []
    for name, content in patched.items():
        (root / name).write_bytes(content.encode() if isinstance(content, str) else content)
    repaired = execution.run_case(request, adapter)
    assert repaired["error"] is None
    assert score_case(expected, repaired)["joint"] is True
