"""Optimization consumes existing evidence, not a second execution of it."""
import json

import pytest

from app import execution, optimizer, persistence, services
from app.errors import ConflictError, ValidationError
from tests.conftest import complete_run, create_demo_project, dataset_payload, environment_payload, run_sync


def events(run, event_type):
    return persistence.list_events(run['id'], event_type=event_type, limit=2000)


def baseline_starts(run):
    return [event for event in events(run, 'case_started') if event['stage'] == 'results_preparation']


def optimize_from(demo, source, **extra):
    return services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize',
                                'evaluation_run_id': source['id'], **extra})


def test_selected_evaluation_is_not_replaced_by_newer_result(demo):
    selected = complete_run(demo, 'evaluate')
    complete_run(demo, 'evaluate')
    run = run_sync(optimize_from(demo, selected)['id'])
    assert run['baseline_run_id'] == selected['id']
    assert run['baseline_cache_hit']
    assert not baseline_starts(run)
    assert events(run, 'optimizer_input_built')
    assert any(e['stage'] == 'retest' for e in events(run, 'case_started'))
    assert run['metrics']['baseline']['execution']['tokens'] == 0
    assert persistence.get_run(selected['id'])['metrics']['baseline']['execution']['tokens'] > 0


def test_continuation_uses_candidate_results_and_keeps_root(demo, monkeypatch):
    original = optimizer.heuristic_candidate
    monkeypatch.setattr(optimizer, 'heuristic_candidate', lambda context: original({
        **context, 'heuristic_limits': {'max_edits': 1}}))
    first = complete_run(demo, 'optimize')
    monkeypatch.setattr(optimizer, 'heuristic_candidate', original)
    second = complete_run(demo, 'optimize', source_run_id=first['id'])
    assert second['candidate_skill_version_id']
    assert second['baseline_cache_hit']
    assert not baseline_starts(second)
    assert second['snapshot']['evaluation_reuse']['source_phase'] == 'candidate'
    assert second['snapshot']['root_skill_version_id'] == first['baseline_skill_version_id']
    old = persistence.get_case_results_for_run(first['id'], 'candidate')
    copied = persistence.get_case_results_for_run(second['id'], 'baseline')
    assert [(r['case_id'], r['scores'], r['actual']) for r in old] == [(r['case_id'], r['scores'], r['actual']) for r in copied]
    assert len([e for e in events(second, 'case_started') if e['stage'] == 'retest']) == len(old)


@pytest.mark.parametrize('continued', [False, True])
def test_optimizer_only_changes_reuse_existing_business_results(demo, continued):
    source = complete_run(demo, 'optimize' if continued else 'evaluate')
    payload = environment_payload(optimizer_config={'provider': 'heuristic', 'timeout_s': 240})
    services.create_environment(demo['project']['id'], payload, status='published', set_current=True)
    run = (complete_run(demo, 'optimize', source_run_id=source['id']) if continued
           else run_sync(optimize_from(demo, source)['id']))
    assert run['baseline_cache_hit']
    assert not baseline_starts(run)
    assert run['environment_id'] != source['environment_id']
    assert persistence.get_run(source['id'])['environment_id'] == source['environment_id']
    if not continued:
        assert services.overview(demo['project']['id'])['baseline']['compatible']


@pytest.mark.parametrize('group,field,value,reason', [
    ('model_config', 'temperature', 0.7, '业务模型'),
    ('scorer_config', 'version', 'changed-score', '评分配置'),
    ('loader_config', 'max_steps', 7, '加载配置'),
    ('run_config', 'case_timeout_s', 999, '执行限制'),
])
def test_execution_changes_force_retest(demo, group, field, value, reason):
    source = complete_run(demo, 'evaluate')
    payload = environment_payload()
    payload[group] = {**payload.get(group, {}), field: value}
    services.create_environment(demo['project']['id'], payload, status='published', set_current=True)
    run = optimize_from(demo, source)
    plan = run['snapshot']['evaluation_reuse']
    assert plan['reused_count'] == 0 and plan['execute_count'] == plan['planned_count']
    assert reason in plan['reason']
    run = run_sync(run['id'])
    assert len(baseline_starts(run)) == plan['planned_count']


@pytest.mark.parametrize('field', ['query', 'expected', 'fixtures'])
def test_daily_data_changes_invalidate_results(demo, field):
    source = complete_run(demo, 'evaluate')
    cases = [json.loads(line) for line in services.export_dataset(source['dataset_id']).decode().splitlines()]
    case = next(c for c in cases if c['split'] == 'optimize')
    if field == 'query':
        case[field] += ' additional condition'
    elif field == 'expected':
        case[field]['critical'] = True
    else:
        case[field][0]['response'] = {'changed': True}
    services.create_dataset(demo['project']['id'], dataset_payload(content='\n'.join(json.dumps(c) for c in cases)))
    run = optimize_from(demo, source)
    assert run['snapshot']['evaluation_reuse']['reused_count'] == 0
    assert '日常测试' in run['snapshot']['evaluation_reuse']['reason']


def test_holdout_changes_do_not_enter_reuse_identity(demo):
    source = complete_run(demo, 'evaluate')
    cases = [json.loads(line) for line in services.export_dataset(source['dataset_id']).decode().splitlines()]
    next(c for c in cases if c['split'] == 'validation')['query'] += ' holdout only'
    services.create_dataset(demo['project']['id'], dataset_payload(content='\n'.join(json.dumps(c) for c in cases)))
    run = optimize_from(demo, source)
    assert run['baseline_cache_hit']


@pytest.mark.parametrize('bad_kind', ['missing', 'infra'])
def test_only_missing_or_infra_repeat_is_executed(bad_kind):
    demo = create_demo_project(env_overrides={'run_config': {'base_repeats': 2}})
    source = complete_run(demo, 'evaluate')
    original_rows = persistence.get_case_results_for_run(source['id'], 'baseline')
    row = next(r for r in original_rows if r['repeat_index'] == 2)
    if bad_kind == 'missing':
        with persistence.transaction() as conn:
            conn.execute('DELETE FROM case_results WHERE id = ?', (row['id'],))
    else:
        persistence.save_case_result(source['id'], row['case_id'], 'baseline', repeat_index=2,
            scores={'status': 'infra', 'error_kind': 'infrastructure'})
    run = optimize_from(demo, source)
    assert run['snapshot']['evaluation_reuse']['execute_count'] == 1
    assert len(persistence.get_case_results_for_run(run['id'])) == len(original_rows) - 1
    run = run_sync(run['id'])
    assert [e['case_id'] for e in baseline_starts(run)] == [row['case_id']]
    assert len(persistence.get_case_results_for_run(run['id'], 'baseline')) == len(original_rows)
    assert events(run, 'optimizer_input_built')


def test_failed_supplement_does_not_call_optimizer(demo, monkeypatch):
    source = complete_run(demo, 'evaluate')
    row = persistence.get_case_results_for_run(source['id'], 'baseline')[0]
    persistence.save_case_result(source['id'], row['case_id'], 'baseline', scores={'status': 'infra'})
    def broken(*args, **kwargs):
        raise RuntimeError('gateway unavailable')
    monkeypatch.setattr(execution, 'run_case', broken)
    run = run_sync(optimize_from(demo, source)['id'])
    assert run['decision'] == 'inconclusive'
    assert services.run_decision_code(run) == 'evaluation_incomplete'
    assert not events(run, 'optimizer_input_built')
    assert not run['candidate_skill_version_id']
    assert len(baseline_starts(run)) == 1


def test_explicit_source_rejects_foreign_holdout_or_conflicting_selection(demo):
    source = complete_run(demo, 'evaluate')
    other = create_demo_project()
    with pytest.raises(ConflictError):
        optimize_from(other, source)
    holdout = complete_run(demo, 'final_test')
    with pytest.raises(ConflictError):
        optimize_from(demo, holdout)
    with pytest.raises(ValidationError):
        services.create_run({'project_id': demo['project']['id'], 'mode': 'baseline', 'evaluation_run_id': source['id']})
    run = run_sync(optimize_from(demo, source, reuse_baseline=False)['id'])
    assert not run['baseline_cache_hit']
    assert len(baseline_starts(run)) == run['snapshot']['evaluation_reuse']['planned_count']


def test_source_copy_failure_never_leaves_a_claimable_partial_run(demo, monkeypatch):
    source = complete_run(demo, 'evaluate')
    before = persistence.list_runs(project_id=demo['project']['id'])['total']
    original = services.prepare_evaluation_reuse
    def missing_source(*args, **kwargs):
        plan, rows = original(*args, **kwargs)
        rows[-1] = {**rows[-1], 'id': -1}
        return plan, rows
    monkeypatch.setattr(services, 'prepare_evaluation_reuse', missing_source)
    with pytest.raises(ConflictError) as error:
        optimize_from(demo, source)
    assert error.value.code == 'evaluation_source_changed'
    assert persistence.list_runs(project_id=demo['project']['id'])['total'] == before
    assert persistence.claim_run('test-worker') is None


def test_logging_only_changes_do_not_invalidate_execution(demo):
    source = complete_run(demo, 'evaluate')
    payload = environment_payload()
    payload['model_config']['transport_diagnostics'] = True
    payload['run_config']['log_level'] = 'DEBUG'
    services.create_environment(demo['project']['id'], payload, status='published', set_current=True)
    assert optimize_from(demo, source)['baseline_cache_hit']
