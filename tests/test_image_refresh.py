"""Updating the bundled sample must preserve user models and history."""
import json

import pytest

from app import config, persistence, seed, services, skill_package
from app.errors import ConflictError, ValidationError


def existing_image_project():
    project = persistence.create_project(seed.DEMO_IMAGE_PROJECT_NAME)
    package = skill_package.import_package(seed.build_image_package_zip())
    files = dict(package['files'])
    files['SKILL.md'] += b'\nPrevious sample version.\n'
    version = persistence.create_skill_version(
        project['id'], files, package['manifest'], status='published', set_current=True)
    template = json.loads((config.SAMPLES_DIR / 'image_environment.json').read_text())
    template['prompt'] = 'Previous business prompt'
    template['tool_schema'] = json.loads((config.SAMPLES_DIR / 'image_tool_schema.json').read_text())
    template['model_config']['api_key_ref'] = 'env:IMAGE_SAMPLE_TEST_KEY'
    template['loader_config']['max_steps'] = 8
    env = services.create_environment(project['id'], template)
    data = services.create_dataset(project['id'], {
        'filename': 'image.jsonl',
        'content': (config.SAMPLES_DIR / 'image_dataset.jsonl').read_text(),
    })
    return persistence.get_project(project['id']), version, env, data


def test_refresh_preserves_models_old_versions_and_does_not_start_runs():
    project, old_skill, old_env, old_data = existing_image_project()
    result = seed.refresh_image_sample()
    current = result['project']
    assert result['updated'] is True
    assert current['current_skill_version_id'] != old_skill['id']
    assert current['current_environment_id'] != old_env['id']
    assert current['current_dataset_id'] != old_data['id']
    assert result['environment']['model_config'] == old_env['model_config']
    assert result['environment']['optimizer_config'] == old_env['optimizer_config']
    assert result['environment']['budget_config'] == old_env['budget_config']
    assert result['environment']['loader_config']['max_steps'] >= 16
    assert persistence.get_skill_version(old_skill['id'])['content_hash'] == old_skill['content_hash']
    assert persistence.get_environment(old_env['id'])['prompt'] == 'Previous business prompt'
    assert persistence.get_dataset(old_data['id'])['hash'] == old_data['hash']
    assert persistence.list_runs(project_id=project['id'])['total'] == 0


def test_refresh_is_idempotent():
    existing_image_project()
    first = seed.refresh_image_sample()
    second = seed.refresh_image_sample()
    assert second['updated'] is False
    assert second['project'] == first['project']
    assert len(persistence.list_skill_versions(first['project']['id'])) == 2


def test_invalid_package_does_not_change_current_pointers(monkeypatch):
    project, *_ = existing_image_project()
    image_spec = next(s for s in seed.DEMO_SPECS if s['name'] == seed.DEMO_IMAGE_PROJECT_NAME)
    monkeypatch.setitem(image_spec, 'zip', lambda: b'not a zip')
    with pytest.raises(ValidationError):
        seed.refresh_image_sample()
    assert persistence.get_project(project['id']) == project
    assert len(persistence.list_skill_versions(project['id'])) == 1


def test_active_run_prevents_sample_refresh():
    project, *_ = existing_image_project()
    services.create_run({'project_id': project['id'], 'mode': 'baseline'})
    with pytest.raises(ConflictError, match='活动运行'):
        seed.refresh_image_sample()
    assert persistence.get_project(project['id']) == project
