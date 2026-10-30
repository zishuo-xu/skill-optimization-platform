"""Whole-workflow recovery boundaries found during user-journey acceptance."""
import json
import pytest

from app import optimizer, persistence, services
from app.errors import ConflictError
from tests.conftest import complete_run, environment_payload, start_run, run_sync
from tests.test_a06_optimizer import FakeAdapter, FakeEmit, _context, _model_candidate, _routing_failure
from tests.workflow_gateway import WorkflowGateway


def test_dataset_diagnosis_does_not_turn_into_heuristic_skill_edit():
    candidate = _model_candidate()
    candidate['diagnosis']['attribution'] = 'dataset_issue'
    candidate['diagnosis']['summary'] = 'The required capability query is missing from fixtures.'
    candidate['diagnosis']['findings'][0]['attribution'] = 'dataset_issue'
    candidate['changed_files'] = []
    candidate['patch']['edits'] = []
    adapter = FakeAdapter(FakeAdapter.text(json.dumps(candidate)))
    emit = FakeEmit()
    result = optimizer.generate_candidate(_context([_routing_failure()]), adapter, emit)
    assert result['status'] == 'no_patch'
    assert result['used_fallback'] is False
    assert result['diagnosis'] == candidate['diagnosis']
    assert 'fallback_started' not in emit.types


def test_optimizer_only_environment_update_does_not_dead_end_acceptance(demo):
    source = complete_run(demo, 'optimize')
    assert source['candidate_skill_version_id']
    original_environment = source['environment_id']
    services.create_environment(demo['project']['id'], environment_payload(
        optimizer_config={'provider': 'heuristic', 'timeout_s': 240}), status='published', set_current=True)
    assert services.run_view(source['id'])['can_validate']
    validation = complete_run(demo, 'validate', source_run_id=source['id'])
    assert validation['decision'] == 'accept'
    assert validation['environment_id'] != original_environment
    assert persistence.get_run(source['id'])['environment_id'] == original_environment
    result = services.apply_candidate(validation['id'], {'expected_current_version': source['baseline_skill_version_id']})
    assert result['project']['current_skill_version_id'] == source['candidate_skill_version_id']


@pytest.mark.parametrize('field,value', [
    ('prompt', 'Different execution rules'),
    ('model_config', {'provider': 'demo_scripted', 'model': 'other'}),
    ('loader_config', {'max_chars': 1000}),
    ('run_config', {'validation_repeats': 4}),
    ('scorer_config', {'version': 'score-v1', 'seed': 99}),
    ('acceptance_config', {'validation_repeats': 4}),
])
def test_execution_or_acceptance_change_still_requires_new_optimization(demo, field, value):
    source = complete_run(demo, 'optimize')
    services.create_environment(demo['project']['id'], environment_payload(**{field: value}),
                                status='published', set_current=True)
    assert not services.run_view(source['id'])['can_validate']
    with pytest.raises(ConflictError):
        start_run(demo, 'validate', source_run_id=source['id'])


def test_environment_change_after_validation_is_queued_still_conflicts(demo):
    source = complete_run(demo, 'optimize')
    validation = start_run(demo, 'validate', source_run_id=source['id'])
    services.create_environment(demo['project']['id'], environment_payload(
        optimizer_config={'provider': 'heuristic', 'timeout_s': 240}), status='published', set_current=True)
    result = run_sync(validation['id'])
    assert result['decision'] != 'accept'
    assert not persistence.get_case_results_for_run(validation['id'])


def test_dataset_diagnosis_is_saved_without_candidate_or_retest(demo, monkeypatch):
    diagnosis = {'summary': 'Required query has no fixture.', 'attribution': 'dataset_issue', 'findings': []}
    monkeypatch.setattr(optimizer, 'generate_candidate', lambda *args, **kwargs: optimizer._make_result(
        status='no_patch', error_code='dataset_issue', diagnosis=diagnosis))
    result = complete_run(demo, 'optimize')
    assert result['candidate_skill_version_id'] is None
    assert '测试数据需核对' in result['decision_reason']
    diff = services.run_diff(result['id'])
    assert diff['diagnosis'] == diagnosis
    assert not diff['patch']['edits']
    assert not any(row['phase'] == 'candidate' for row in persistence.get_case_results_for_run(result['id']))


def test_http_optimizer_full_workflow_and_failed_patch_recovery(demo):
    with WorkflowGateway() as gateway:
        pid = demo['project']['id']
        services.create_environment(pid, environment_payload(optimizer_config=gateway.config('invalid-patch')),
                                    status='published', set_current=True)
        baseline = complete_run(demo, 'evaluate')
        failed = complete_run(demo, 'optimize', evaluation_run_id=baseline['id'])
        assert not failed['candidate_skill_version_id']
        diff = services.run_diff(failed['id'])
        assert diff['patch']['edits'][0]['search'] == 'SYNTHETIC MISSING SOURCE'
        assert diff['checks']['errors'][0]['code'] == 'edit_not_found'
        assert not persistence.list_events(failed['id'], event_type='fallback_started')
        services.create_environment(pid, environment_payload(optimizer_config=gateway.config()),
                                    status='published', set_current=True)
        recovered = complete_run(demo, 'optimize', evaluation_run_id=baseline['id'])
        assert recovered['status'] == 'completed' and recovered['decision'] is None
        assert recovered['candidate_skill_version_id']
        assert not persistence.list_events(recovered['id'], event_type='fallback_started')
        rows = persistence.get_case_results_for_run(recovered['id'])
        assert len([r for r in rows if r['phase'] == 'candidate']) == len(
            [r for r in rows if r['phase'] == 'baseline']) > 0
        validation = complete_run(demo, 'validate', source_run_id=recovered['id'])
        assert validation['decision'] == 'accept'
        services.apply_candidate(validation['id'], {'expected_current_version': baseline['baseline_skill_version_id']})
        assert persistence.get_project(pid)['current_skill_version_id'] == recovered['candidate_skill_version_id']
        assert persistence.skill_version_integrity(baseline['baseline_skill_version_id'])['ok']
        assert len(gateway.requests) == 2


@pytest.mark.parametrize('model', ['timeout', 'dataset-issue'])
def test_http_failure_is_explicit_and_recoverable(demo, model):
    with WorkflowGateway() as gateway:
        pid = demo['project']['id']
        services.create_environment(pid, environment_payload(optimizer_config=gateway.config(model, 0.15 if model == 'timeout' else 5)),
                                    status='published', set_current=True)
        first = complete_run(demo, 'optimize')
        fallback = persistence.list_events(first['id'], event_type='fallback_started')
        if model == 'timeout':
            assert fallback
            assert persistence.list_events(first['id'], event_type='optimizer_call_failed')
        else:
            assert not fallback and not first['candidate_skill_version_id']
            assert services.run_diff(first['id'])['diagnosis']['attribution'] == 'dataset_issue'
        services.create_environment(pid, environment_payload(optimizer_config=gateway.config()),
                                    status='published', set_current=True)
        recovered = complete_run(demo, 'optimize')
        assert recovered['candidate_skill_version_id']
        assert not persistence.list_events(recovered['id'], event_type='fallback_started')
