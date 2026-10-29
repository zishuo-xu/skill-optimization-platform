import json
import pytest

from app import config, optimizer, persistence, services
from app.errors import ConflictError, ValidationError
from tests.conftest import complete_run, create_demo_project, dataset_payload, environment_payload, run_sync


def test_candidate_cannot_be_published_directly(demo):
    source = complete_run(demo, 'optimize')
    candidate = source['candidate_skill_version_id']
    with pytest.raises(ConflictError) as error:
        services.publish_skill_version(candidate, set_current=False)
    assert error.value.code == 'candidate_requires_acceptance'
    assert persistence.get_project(demo['project']['id'])['current_skill_version_id'] == source['baseline_skill_version_id']


def test_chain_uses_candidate_and_validates_against_root(demo, monkeypatch):
    generate = optimizer.heuristic_candidate
    # First round intentionally fixes one issue; the second must really repair
    # remaining failures and produce another candidate through the pipeline.
    monkeypatch.setattr(optimizer, 'heuristic_candidate', lambda context: generate({
        **context, 'heuristic_limits': {'max_edits': 1}}))
    first = complete_run(demo, 'optimize')
    monkeypatch.setattr(optimizer, 'heuristic_candidate', generate)
    second = complete_run(demo, 'optimize', source_run_id=first['id'])
    assert second['status'] == 'completed', second
    assert second['baseline_skill_version_id'] == first['candidate_skill_version_id']
    assert second['snapshot']['root_skill_version_id'] == first['baseline_skill_version_id']
    assert second['candidate_skill_version_id']
    assert second['candidate_skill_version_id'] != first['candidate_skill_version_id']
    assert services.run_view(second['id'])['can_continue_optimization']
    assert persistence.get_project(demo['project']['id'])['current_skill_version_id'] == first['baseline_skill_version_id']
    validation = complete_run(demo, 'validate', source_run_id=second['id'])
    assert validation['status'] == 'completed', validation
    assert validation['baseline_skill_version_id'] == first['baseline_skill_version_id']
    assert validation['candidate_skill_version_id'] == second['candidate_skill_version_id']
    assert validation['decision'] == 'accept'
    applied = services.apply_candidate(validation['id'], {
        'expected_current_version': first['baseline_skill_version_id']})
    assert applied['project']['current_skill_version_id'] == second['candidate_skill_version_id']


@pytest.mark.parametrize('field', ['case_id', 'query', 'group_id'])
def test_validation_checks_all_ancestor_exposure(demo, field):
    first = complete_run(demo, 'optimize')
    exposed = next(c for c in persistence.all_cases(first['dataset_id']) if c['split'] == 'optimize')
    sample = json.loads((config.SAMPLES_DIR / 'dataset.jsonl').read_text().splitlines()[0])
    sample.update(id='other', query='other unique query', group_id='other', split='optimize')
    services.create_dataset(demo['project']['id'], dataset_payload(content=json.dumps(sample)))
    second = complete_run(demo, 'optimize', source_run_id=first['id'])
    assert second['status'] == 'completed', second
    case = {'case_id': 'new', 'query': 'new query', 'group_id': 'new', 'split': 'validation'}
    case[field] = exposed[field]
    with pytest.raises(ValidationError) as error:
        services._check_validation_independence(second, [case])
    assert error.value.code == 'dataset_leakage'


def test_continue_rejects_stale_and_foreign_sources(demo):
    source = complete_run(demo, 'optimize')
    other = create_demo_project()
    with pytest.raises(ConflictError):
        services.create_run({'project_id': other['project']['id'], 'mode': 'optimize', 'source_run_id': source['id']})
    services.create_environment(demo['project']['id'], environment_payload(), status='published', set_current=True)
    assert services.run_view(source['id'])['can_continue_optimization']
    assert not services.run_view(source['id'])['can_validate']
    continued = services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize', 'source_run_id': source['id']})
    assert continued['baseline_cache_hit']
    # Changing the published root still rejects continuation.
    from tests.conftest import build_package_zip
    services.create_skill_version(demo['project']['id'], zip_bytes=build_package_zip(), status='published', set_current=True)
    with pytest.raises(ConflictError):
        services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize', 'source_run_id': source['id']})


def test_evaluation_cache_rejects_infrastructure_error_and_incomplete_repeats(demo):
    evaluation = complete_run(demo, 'evaluate')
    rows = persistence.get_case_results_for_run(evaluation['id'], 'baseline')
    persistence.save_case_result(evaluation['id'], rows[0]['case_id'], 'baseline',
        split='optimize', scores={'status': 'infra'}, error_code='provider_error')
    run = services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize'})
    assert not run['baseline_cache_hit']
    copied = persistence.get_case_results_for_run(run['id'])
    assert len(copied) == len(rows) - 1
    assert all(row['case_id'] != rows[0]['case_id'] for row in copied)


def test_evaluation_cache_requires_matching_rules(demo):
    evaluation = complete_run(demo, 'evaluate')
    persistence.update_run(evaluation['id'], rules_version='old-rules')
    run = services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize'})
    assert not run['baseline_cache_hit']


def test_publish_endpoint_rejects_candidate_even_without_activation(client, demo):
    source = complete_run(demo, 'optimize')
    response = client.post(f"/api/v3/skill-versions/{source['candidate_skill_version_id']}/publish",
                           json={'set_current': False})
    assert response.status_code == 409
    assert persistence.get_skill_version(source['candidate_skill_version_id'])['status'] == 'candidate'


def test_cache_requires_every_frozen_repeat():
    demo = create_demo_project(env_overrides={'run_config': {'base_repeats': 2}})
    evaluation = complete_run(demo, 'evaluate')
    rows = persistence.get_case_results_for_run(evaluation['id'], 'baseline')
    assert {r['repeat_index'] for r in rows} == {1, 2}
    with persistence.transaction() as conn:
        conn.execute('DELETE FROM case_results WHERE run_id = ? AND case_id = ? AND repeat_index = 2',
                     (evaluation['id'], rows[0]['case_id']))
    optimize = services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize'})
    assert not optimize['baseline_cache_hit']


def test_no_failure_continuation_keeps_source_candidate_discoverable(demo):
    first = complete_run(demo, 'optimize')
    view = services.overview(demo['project']['id'])
    assert view['primary_action']['mode'] == 'continue'
    assert view['primary_action']['run_id'] == first['id']
    second = complete_run(demo, 'optimize', source_run_id=first['id'])
    assert services.run_view(second['id'])['decision_code'] == 'no_failures'
    view = services.overview(demo['project']['id'])
    assert view['primary_action']['run_id'] == first['id']


def test_queued_continuation_rechecks_current_environment(demo):
    first = complete_run(demo, 'optimize')
    queued = services.create_run({'project_id': demo['project']['id'],
                                 'mode': 'optimize', 'source_run_id': first['id']})
    services.create_environment(demo['project']['id'], environment_payload())
    result = run_sync(queued['id'])
    assert result['status'] == 'failed'
    assert result['error_code'] == 'version_conflict'
    assert not persistence.list_events(result['id'], event_type='model_call_started')
