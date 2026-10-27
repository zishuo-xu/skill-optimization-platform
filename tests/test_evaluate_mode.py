"""Daily runs and independent acceptance use separate cases and decisions."""
import json

import pytest

from app import config, persistence, services
from app.errors import ConflictError, ValidationError
from tests.conftest import complete_run, create_demo_project, dataset_payload


def _cases(*splits):
    sample = json.loads((config.SAMPLES_DIR / 'dataset.jsonl').read_text().splitlines()[0])
    rows = []
    for i, split in enumerate(splits):
        row = dict(sample, id=f'case_{i}', group_id=f'group_{i}', split=split,
                   query=f"{sample['query']} unique {i}")
        rows.append(json.dumps(row, ensure_ascii=False))
    return '\n'.join(rows)


def test_evaluate_only_runs_test_cases():
    demo = create_demo_project(dataset_content=_cases('optimize', 'validation', 'final_test'))
    run = complete_run(demo, 'evaluate')
    assert run['status'] == 'completed'
    rows = persistence.get_case_results_for_run(run['id'])
    assert {row['case_id'] for row in rows} == {'case_0'}
    assert run['decision'] is None and run['metrics']['final'] is None


def test_evaluate_supplies_compatible_optimization_baseline(demo):
    run = complete_run(demo, 'evaluate')
    assert run['status'] == 'completed'
    overview = services.overview(demo['project']['id'])
    assert overview['baseline']['compatible']
    optimize = services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize'})
    assert optimize['baseline_cache_hit'] and optimize['baseline_run_id'] == run['id']
    assert persistence.get_case_results_for_run(optimize['id'], 'baseline')


def test_daily_requires_test_set():
    demo = create_demo_project(dataset_content=_cases('validation', 'final_test'))
    for mode in ('evaluate', 'optimize'):
        with pytest.raises(ValidationError, match='测试集'):
            services.create_run({'project_id': demo['project']['id'], 'mode': mode})


def test_optimize_then_validate_and_apply(demo):
    run = complete_run(demo, 'optimize')
    assert run['status'] == 'completed', run
    assert run['decision'] is None and run['candidate_skill_version_id']
    assert run['metrics']['validation'] is None
    assert services.run_view(run['id'])['can_validate']
    rows = persistence.get_case_results_for_run(run['id'])
    assert {r['split'] for r in rows} == {'optimize'}
    with pytest.raises(ConflictError):
        services.apply_candidate(run['id'], {'expected_current_version': run['baseline_skill_version_id']})
    validation = complete_run(demo, 'validate', source_run_id=run['id'])
    assert validation['status'] == 'completed', validation
    assert validation['decision'] in ('accept', 'reject', 'inconclusive')
    assert {r['split'] for r in persistence.get_case_results_for_run(validation['id'])} == {'validation'}
    assert services.run_view(validation['id'])['source_run_id'] == run['id']
    if validation['decision'] == 'accept':
        result = services.apply_candidate(validation['id'], {'expected_current_version': run['baseline_skill_version_id']})
        assert result['project']['current_skill_version_id'] == run['candidate_skill_version_id']


def test_validate_rejects_missing_or_overlapping_cases(demo):
    run = complete_run(demo, 'optimize')
    assert run['candidate_skill_version_id']
    source_cases = persistence.all_cases(run['dataset_id'])
    optimize_case = next(c for c in source_cases if c['split'] == 'optimize')
    sample = json.loads((config.SAMPLES_DIR / 'dataset.jsonl').read_text().splitlines()[0])
    sample.update(id='leak', group_id='new-group', split='validation', query=optimize_case['query'])
    services.create_dataset(demo['project']['id'], dataset_payload(content=json.dumps(sample, ensure_ascii=False)))
    assert services.run_view(run['id'])['can_validate']
    with pytest.raises(ValidationError, match='重复'):
        services.create_run({'project_id': demo['project']['id'], 'mode': 'validate', 'source_run_id': run['id']})
    services.create_dataset(demo['project']['id'], dataset_payload(content=_cases('optimize')))
    with pytest.raises(ValidationError, match='独立验收集'):
        services.create_run({'project_id': demo['project']['id'], 'mode': 'validate', 'source_run_id': run['id']})


def test_test_only_optimization_then_new_validation_dataset():
    raw = [json.loads(line) for line in (config.SAMPLES_DIR / 'dataset.jsonl').read_text().splitlines()]
    only_test = '\n'.join(json.dumps(row, ensure_ascii=False) for row in raw if row['split'] == 'optimize')
    only_validation = '\n'.join(json.dumps(row, ensure_ascii=False) for row in raw if row['split'] == 'validation')
    demo = create_demo_project(dataset_content=only_test)
    source = complete_run(demo, 'optimize')
    assert source['status'] == 'completed' and source['candidate_skill_version_id']
    assert source['decision'] is None
    services.create_dataset(demo['project']['id'], dataset_payload(content=only_validation))
    assert services.run_view(source['id'])['can_validate']
    result = complete_run(demo, 'validate', source_run_id=source['id'])
    assert result['status'] == 'completed', result
    assert result['snapshot']['source_run_id'] == source['id']
    assert result['snapshot']['candidate_skill_version']['id'] == source['candidate_skill_version_id']
    assert result['snapshot']['candidate_skill_version']['content_hash']
    assert result['decision'] == 'accept'
    overview = services.overview(demo['project']['id'])
    assert not overview['baseline']['compatible']
    assert overview['pending_candidate']['run_id'] == result['id']
    assert overview['primary_action']['mode'] == 'apply'
    applied = services.apply_candidate(result['id'], {'expected_current_version': source['baseline_skill_version_id']})
    assert applied['project']['current_skill_version_id'] == source['candidate_skill_version_id']


def test_validate_rejects_changed_environment_and_foreign_source(demo):
    from tests.conftest import environment_payload
    source = complete_run(demo, 'optimize')
    other = create_demo_project()
    with pytest.raises(ConflictError, match='本项目'):
        services.create_run({'project_id': other['project']['id'], 'mode': 'validate', 'source_run_id': source['id']})
    services.create_environment(demo['project']['id'], environment_payload(), status='published', set_current=True)
    assert not services.run_view(source['id'])['can_validate']
    with pytest.raises(ConflictError, match='环境'):
        services.create_run({'project_id': demo['project']['id'], 'mode': 'validate', 'source_run_id': source['id']})


def test_validate_checks_frozen_candidate_before_any_execution(demo):
    from tests.conftest import run_sync
    source = complete_run(demo, 'optimize')
    validation = services.create_run({'project_id': demo['project']['id'], 'mode': 'validate', 'source_run_id': source['id']})
    (persistence.skill_files_dir(source['candidate_skill_version_id']) / 'SKILL.md').write_text('tampered')
    result = run_sync(validation['id'])
    assert result['status'] == 'failed'
    assert not persistence.get_case_results_for_run(validation['id'])
    assert result['decision'] is None


@pytest.mark.parametrize('overlap_field', ['case_id', 'query', 'group_id'])
def test_validate_rejects_each_type_of_source_overlap(demo, overlap_field):
    source = complete_run(demo, 'optimize')
    test_case = next(case for case in persistence.all_cases(source['dataset_id'])
                     if case['split'] == 'optimize')
    isolated = {'case_id': 'different-id', 'query': 'different query',
                'group_id': 'different-group', 'split': 'validation'}
    isolated[overlap_field] = test_case[overlap_field]
    with pytest.raises(ValidationError) as error:
        services._check_validation_independence(source, [isolated])
    assert error.value.code == 'dataset_leakage'
