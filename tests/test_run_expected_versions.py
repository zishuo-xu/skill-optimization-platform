import pytest
from app import services
from app.errors import ConflictError

@pytest.mark.parametrize('field', ['expected_skill_version_id', 'expected_environment_id', 'expected_dataset_id'])
def test_create_run_rejects_changed_pointer_before_snapshot(monkeypatch, field):
    monkeypatch.setattr(services.persistence, 'get_project', lambda _: {
        'current_skill_version_id': 's', 'current_environment_id': 'e', 'current_dataset_id': 'd'})
    monkeypatch.setattr(services.persistence, 'get_environment', lambda _: pytest.fail('must not load environment'))
    with pytest.raises(ConflictError) as error:
        services.create_run({'project_id': 'p', field: 'changed'})
    assert error.value.code == 'version_conflict'
    assert error.value.details['field'] == field
