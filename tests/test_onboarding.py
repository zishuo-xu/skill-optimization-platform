import concurrent.futures
from collections import Counter

import pytest

from app import db, onboarding, persistence, services


def test_guide_assets_and_idempotence():
    other = persistence.create_project('用户项目')
    project = onboarding.ensure_guide()
    assert onboarding.ensure_guide()['id'] == project['id']
    assert persistence.get_project(other['id']) == other
    assert project['current_environment_id'] is None
    skill = persistence.get_skill_version(project['current_skill_version_id'])
    assert len(skill['manifest']['subskills']) == 3
    assert b'-> urgent' in persistence.read_skill_file(skill['id'], 'references/task_create.md')
    assert b'-> active' in persistence.read_skill_file(skill['id'], 'references/task_query.md')
    cases = persistence.all_cases(project['current_dataset_id'])
    assert Counter(c['split'] for c in cases) == {'optimize': 24, 'validation': 12, 'final_test': 6}
    env, = persistence.list_environments(project['id'])
    assert env['status'] == 'draft'
    assert env['model_config']['model'] == env['model_config']['base_url'] == ''
    assert env['optimizer_config']['model'] == env['optimizer_config']['base_url'] == ''
    assert 'judge' not in env['scorer_config']
    assert env['scorer_config']['compare']['field_rules'][0]['mode'] == 'semantic'
    assert persistence.list_runs(project_id=project['id'])['total'] == 0
    assert not services.overview(project['id'])['completeness']['ready']


def test_preserves_user_changes_and_archive():
    project = onboarding.ensure_guide()
    env = persistence.list_environments(project['id'])[0]
    persistence.update_project(project['id'], name='已改名', archived=1)
    persistence.set_current_environment(project['id'], env['id'])
    after = onboarding.ensure_guide()
    assert after['id'] == project['id'] and after['name'] == '已改名' and after['archived'] == 1
    assert after['current_environment_id'] == env['id']
    assert len(persistence.list_projects(include_archived=True)) == 1


def test_install_rolls_back_and_recovers(monkeypatch):
    original = onboarding.hashing.file_hashes
    def fail(_):
        raise RuntimeError('injected failure')
    monkeypatch.setattr(onboarding.hashing, 'file_hashes', fail)
    with pytest.raises(RuntimeError):
        onboarding.ensure_guide()
    assert persistence.list_projects(include_archived=True) == []
    monkeypatch.setattr(onboarding.hashing, 'file_hashes', original)
    assert onboarding.ensure_guide()['current_dataset_id']


def test_concurrent_install():
    def create(_):
        try:
            return onboarding.ensure_guide()['id']
        finally:
            db.reset_connection()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(4)))
    assert len(set(ids)) == 1
    assert len(persistence.list_projects(include_archived=True)) == 1


def test_lifespan_installs_and_exposes_guide(client):
    response = client.get('/api/v3/projects')
    assert response.status_code == 200
    payload = response.json()
    project = next(p for p in payload['items'] if p.get('guide'))
    guide = project['guide']
    assert guide['key'] == onboarding.KEY
    overview = client.get(f"/api/v3/projects/{project['id']}/overview").json()
    assert overview['guide'] == guide
    assert not overview['completeness']['ready']


def test_completion_requires_applied_validation_and_matching_current_inputs():
    project = onboarding.ensure_guide()
    pid = project['id']
    env_id = onboarding.guide_for_project(pid)['environment_draft_id']
    skill_id, dataset_id = project['current_skill_version_id'], project['current_dataset_id']
    persistence.set_current_environment(pid, env_id)
    run = persistence.create_run(pid, mode='validate', baseline_skill_version_id='original',
                                 candidate_skill_version_id=skill_id, environment_id=env_id,
                                 dataset_id=dataset_id, status='completed')
    persistence.update_run(run['id'], decision='accept')
    decision = persistence.create_decision(run['id'], 'accept', 'accept-v1', {})
    assert 'completed_run_id' not in onboarding.guide_for_project(pid)
    persistence.mark_decision_applied(decision['id'], 'original')
    assert onboarding.guide_for_project(pid)['completed_run_id'] == run['id']
    # Completion remains evidence-backed even if the project is renamed.
    persistence.update_project(pid, name='我的任务项目')
    assert services.overview(pid)['guide']['completed_run_id'] == run['id']
    for setter, original in [(persistence.set_current_skill_version, skill_id),
                             (persistence.set_current_environment, env_id),
                             (persistence.set_current_dataset, dataset_id)]:
        setter(pid, 'changed')
        assert 'completed_run_id' not in onboarding.guide_for_project(pid)
        setter(pid, original)
    for field, value, original in [('mode', 'optimize', 'validate'),
                                   ('status', 'failed', 'completed'),
                                   ('decision', 'reject', 'accept')]:
        with db.transaction() as conn:
            conn.execute(f'UPDATE runs SET {field}=? WHERE id=?', (value, run['id']))
        assert 'completed_run_id' not in onboarding.guide_for_project(pid)
        with db.transaction() as conn:
            conn.execute(f'UPDATE runs SET {field}=? WHERE id=?', (original, run['id']))
    assert onboarding.guide_for_project(pid)['completed_run_id'] == run['id']
