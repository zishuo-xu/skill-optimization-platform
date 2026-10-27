"""Runs must execute and describe only the requested project's versions."""

from app import services
from tests.conftest import build_package_zip, create_demo_project


def test_final_test_rejects_another_projects_skill(client, demo):
    other = create_demo_project()
    response = client.post('/api/v3/runs', json={
        'project_id': demo['project']['id'], 'mode': 'final_test',
        'skill_version_id': other['skill_version']['id'],
    })
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'version_conflict'


def test_final_test_fingerprint_uses_selected_version(demo):
    other_version = services.create_skill_version(
        demo['project']['id'], zip_bytes=build_package_zip(),
        status='published', set_current=False, message='alternate')
    run = services.create_run({'project_id': demo['project']['id'], 'mode': 'final_test',
                               'skill_version_id': other_version['id']})
    expected = services.run_fingerprint(other_version['id'], run['environment_id'],
                                        run['dataset_id'], run['snapshot']['scorer_version'],
                                        run['snapshot']['base_repeats'])
    assert run['baseline_skill_version_id'] == other_version['id']
    assert run['fingerprint'] == expected
    assert run['snapshot']['fingerprint'] == expected
    assert run['acceptance']['frozen']['fingerprint'] == expected


def test_concurrent_dataset_activation_cannot_overwrite_newer_choice(demo, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app import persistence
    from app.errors import ConflictError

    original = demo['dataset']['id']
    first = persistence.clone_dataset(original)
    second = persistence.clone_dataset(original)
    persistence.publish_dataset(first['id'], set_current=False)
    persistence.publish_dataset(second['id'], set_current=False)
    barrier = Barrier(2)
    real_cas = persistence.activate_dataset_cas

    def simultaneous_cas(*args):
        barrier.wait(timeout=5)
        return real_cas(*args)

    monkeypatch.setattr(persistence, 'activate_dataset_cas', simultaneous_cas)

    def activate(dataset_id):
        try:
            services.activate_dataset(dataset_id, expected_current_version=original)
            return 'ok', dataset_id
        except ConflictError as exc:
            return exc.code, dataset_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(activate, [first['id'], second['id']]))
    assert sorted(outcome for outcome, _ in outcomes) == ['conflict', 'ok']
    winner = next(dataset_id for outcome, dataset_id in outcomes if outcome == 'ok')
    assert persistence.get_project(demo['project']['id'])['current_dataset_id'] == winner


def test_infrastructure_error_baseline_is_not_reused(demo, baseline_run):
    from app import persistence

    row = persistence.get_case_results_for_run(baseline_run['id'], 'baseline')[0]
    persistence.save_case_result(
        baseline_run['id'], row['case_id'], 'baseline', split=row['split'],
        scores={'status': 'infra', 'error_kind': 'infrastructure', 'passed': False},
        actual={'error': {'code': 'timeout'}})
    run = services.create_run({'project_id': demo['project']['id'], 'mode': 'optimize'})
    assert not run['baseline_cache_hit']
    assert run['baseline_run_id'] is None


def test_incomplete_and_inconclusive_baselines_are_not_cached(demo, baseline_run):
    from app import persistence
    from app.db import transaction
    persistence.update_run(baseline_run['id'], decision='inconclusive')
    assert persistence.find_cached_baseline(demo['project']['id'], baseline_run['fingerprint']) is None
    persistence.update_run(baseline_run['id'], decision=None)
    rows = persistence.get_case_results_for_run(baseline_run['id'], 'baseline')
    with transaction() as conn:
        conn.execute('DELETE FROM case_results WHERE run_id = ? AND case_id = ?',
                     (baseline_run['id'], rows[0]['case_id']))
    assert persistence.find_cached_baseline(demo['project']['id'], baseline_run['fingerprint']) is None
