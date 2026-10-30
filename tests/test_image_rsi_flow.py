"""Worker-level checks for the full v3 image sample and its frozen optimizer."""
import json
from pathlib import Path

from app import persistence, services, single_round, skill_package
from app.worker import Worker

SAMPLES = Path(__file__).resolve().parent.parent / 'app' / 'samples'


def _project(*, corrupt=False, stub_optimizer=False):
    root = SAMPLES / 'image_skill_package'
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    if corrupt:
        path = 'references/image-color-transfer.md'
        text = files[path].decode()
        assert '"夕阳": "橙色"' in text
        files[path] = text.replace('"夕阳": "橙色"', '"夕阳": "红色"').encode()
    project = persistence.create_project('完整修图 RSI')
    services.create_skill_version(project['id'], zip_bytes=skill_package.build_zip(files),
                                  status='published', set_current=True, message='worker test')
    env = json.loads((SAMPLES / 'image_environment.json').read_text())
    env['prompt'] = (SAMPLES / 'image_system_prompt.md').read_text()
    if stub_optimizer:
        env['optimizer_config'] = {'provider': 'openai_http', 'model': 'test-stub'}
    services.create_environment(project['id'], env, status='published', set_current=True)
    lines = (SAMPLES / 'image_dataset.jsonl').read_text().splitlines()
    if corrupt:
        cases = [json.loads(line) for line in lines]
        for case_id, query in [('image_color_transfer_opt_1', '把天空换成夕阳色'),
                               ('image_color_transfer_val_1', '把衣服换成夕阳色')]:
            case = next(case for case in cases if case['id'] == case_id)
            case['query'] = query
            case['tool']['arguments']['request']['query'] = query
            case['tool']['arguments']['args']['targetColor'] = '橙色'
        lines = [json.dumps(case, ensure_ascii=False) for case in cases]
    ds = services.create_dataset(project['id'], {
        'filename': 'image_dataset.jsonl', 'content': '\n'.join(lines) + '\n',
        'split_config': {'optimize': 0.7, 'validation': 0.2, 'final_test': 0.1},
        'seed': 42, 'source_name': 'full image sample'})
    return project, ds


def _run(project, mode, **kwargs):
    run = services.create_run({'project_id': project['id'], 'mode': mode, **kwargs})
    assert Worker(executor=single_round.execute_run).process_once()
    return persistence.get_run(run['id'])


def test_full_sample_baseline_and_no_failure_optimization():
    project, ds = _project()
    cases = persistence.all_cases(ds['id'])
    assert len(cases) == 112
    optimize_ids = {c['case_id'] for c in cases if c['split'] == 'optimize'}
    assert len(optimize_ids) == 45
    baseline = _run(project, 'baseline')
    assert baseline['status'] == 'completed', baseline
    assert baseline['metrics']['baseline']['joint']['value'] == 1.0
    rows = persistence.get_case_results_for_run(baseline['id'])
    assert {row['case_id'] for row in rows} == optimize_ids
    assert all(row['split'] == 'optimize' for row in rows)
    optimized = _run(project, 'optimize')
    assert optimized['status'] == 'completed', optimized
    assert optimized['decision'] == 'inconclusive'
    assert '没有失败案例' in optimized['decision_reason']
    assert optimized.get('candidate_skill_version_id') is None
    assert persistence.get_candidate_for_run(optimized['id']) is None
    assert {row['case_id'] for row in persistence.get_case_results_for_run(optimized['id'])} == optimize_ids


def test_v3_json_mapping_failure_does_not_create_a_fake_repair():
    project, _ = _project(corrupt=True)
    run = _run(project, 'optimize')
    assert run['status'] == 'completed', run
    assert run['metrics']['baseline']['joint']['value'] < 1.0
    assert run['decision'] == 'inconclusive'
    assert run.get('candidate_skill_version_id') is None
    assert persistence.get_candidate_for_run(run['id']) is None


def test_v3_stub_optimizer_candidate_and_independent_validation(monkeypatch):
    """The stub supplies a known patch; this does not test model intelligence."""
    project, ds = _project(corrupt=True, stub_optimizer=True)
    real_build = single_round.build_adapter
    path = 'references/image-color-transfer.md'
    optimizer_messages = []

    class StubOptimizer:
        def invoke(self, messages, config, tools):
            optimizer_messages.extend(messages)
            return {'content': {
                'diagnosis': {'summary': '映射错误', 'attribution': 'arguments',
                              'findings': [{'case_id': 'image_color_transfer_opt_1',
                                            'observable_events': ['tool_result', 'score_completed'],
                                            'evidence': '色值不符'}]},
                'hypotheses': ['修正夕阳色映射'], 'changed_files': [path],
                'patch': {'summary': '修正夕阳映射', 'edits': [{'path': path,
                          'search': '"夕阳": "红色"', 'replace': '"夕阳": "橙色"'}]},
                'expected_effect': '改善颜色参数', 'risk_cases': []},
                'usage': {'input_tokens': 1, 'output_tokens': 1}, 'model': 'test-stub'}

    monkeypatch.setattr(single_round, 'build_adapter',
                        lambda cfg: StubOptimizer() if cfg.get('model') == 'test-stub' else real_build(cfg))
    optimized = _run(project, 'optimize')
    assert optimized['status'] == 'completed', optimized
    assert optimized.get('candidate_skill_version_id'), optimized
    assert optimized['metrics']['baseline']['joint']['value'] < 1.0
    assert optimized['metrics']['candidate']['joint']['value'] == 1.0
    cases = persistence.all_cases(ds['id'])
    optimize_ids = {c['case_id'] for c in cases if c['split'] == 'optimize'}
    validation_ids = {c['case_id'] for c in cases if c['split'] == 'validation'}
    independent_ids = {c['case_id'] for c in cases if c['split'] in ('validation', 'final_test')}
    assert optimizer_messages
    prompt = '\n'.join(message['content'] for message in optimizer_messages)
    assert 'image_color_transfer_opt_1' in prompt
    assert '把衣服换成夕阳色' not in prompt
    assert all(case_id not in prompt for case_id in independent_ids)
    assert {row['case_id'] for row in persistence.get_case_results_for_run(optimized['id'])} == optimize_ids
    validated = _run(project, 'validate', source_run_id=optimized['id'])
    assert validated['status'] == 'completed', validated
    assert validated['decision'] == 'accept', (validated['decision_reason'], validated['cost'])
    rows = persistence.get_case_results_for_run(validated['id'])
    assert {row['case_id'] for row in rows} == validation_ids
    assert len(rows) == len(validation_ids) * 3 * 2
    assert validated['metrics']['baseline']['joint']['value'] < 1.0
    assert validated['metrics']['candidate']['joint']['value'] == 1.0
    assert optimized['cost']['tokens'] < 1000000
    assert validated['cost']['tokens'] < 1000000
    services.apply_candidate(validated['id'], {'expected_current_version': validated['baseline_skill_version_id']})
    assert persistence.get_project(project['id'])['current_skill_version_id'] == optimized['candidate_skill_version_id']
    assert persistence.get_decision_for_run(validated['id'])['applied_at']
