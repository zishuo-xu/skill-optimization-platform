"""Legacy budget columns are removed safely without changing run evidence."""
import sqlite3

import pytest

from app import config, db, persistence, services
from tests.conftest import environment_payload


def _legacy_database(path, monkeypatch):
    monkeypatch.setattr(config, 'DB_PATH', path)
    db.reset_connection()
    db.init_db(path)
    project = persistence.create_project('upgrade')
    env = services.create_environment(project['id'], environment_payload(), status='published')
    run = persistence.create_run(project['id'], mode='baseline', baseline_skill_version_id='skill',
                                 environment_id=env['id'], dataset_id='dataset')
    conn = db.get_connection(path)
    conn.execute("UPDATE runs SET metrics_json = '{\"score\": 1}', cost_json = '{\"tokens\": 17}' WHERE id = ?", (run['id'],))
    conn.execute("ALTER TABLE environments ADD COLUMN budget_config_json TEXT NOT NULL DEFAULT '{}' ")
    conn.execute("ALTER TABLE runs ADD COLUMN budget_json TEXT NOT NULL DEFAULT '{}' ")
    conn.execute("UPDATE environments SET budget_config_json = '{\"max_tokens\":123}'")
    conn.execute("UPDATE runs SET budget_json = '{\"max_tokens\":123}'")
    return project, env, run


def test_upgrade_removes_budget_columns_preserving_records_and_backup(tmp_path, monkeypatch):
    path = tmp_path / 'app.db'
    project, env, run = _legacy_database(path, monkeypatch)
    db.reset_connection(path)
    db.init_db(path)
    conn = db.get_connection(path)
    for table, column in [('environments', 'budget_config_json'), ('runs', 'budget_json')]:
        assert column not in {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
    assert 'budget_config' not in persistence.get_environment(env['id'])
    actual = persistence.get_run(run['id'])
    assert 'budget' not in actual
    assert actual['metrics'] == {'score': 1}
    assert actual['cost'] == {'tokens': 17}
    backups = list(tmp_path.glob('app.db.pre-budget.*.bak'))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('SELECT budget_config_json FROM environments').fetchone()[0] == '{"max_tokens":123}'
        assert backup.execute('SELECT budget_json FROM runs').fetchone()[0] == '{"max_tokens":123}'
    updated = environment_payload()
    updated.pop('budget_config', None)
    created = services.create_environment(project['id'], updated, status='draft')
    assert 'budget_config' not in created
    assert persistence.compute_environment_hash({**updated, 'budget_config': {'max_tokens': 1}}) == persistence.compute_environment_hash(updated)
    db.reset_connection(path)
    db.init_db(path)
    assert len(list(tmp_path.glob('app.db.pre-budget.*.bak'))) == 1


def test_failed_upgrade_rolls_back_both_columns(tmp_path, monkeypatch):
    path = tmp_path / 'app.db'
    _legacy_database(path, monkeypatch)
    db.get_connection(path).execute('CREATE VIEW legacy_run_view AS SELECT budget_json FROM runs')
    db.reset_connection(path)
    with pytest.raises(sqlite3.OperationalError):
        db.init_db(path)
    conn = db.get_connection(path)
    assert 'budget_config_json' in {row[1] for row in conn.execute('PRAGMA table_info(environments)')}
    assert 'budget_json' in {row[1] for row in conn.execute('PRAGMA table_info(runs)')}
    assert list(tmp_path.glob('app.db.pre-budget.*.bak'))
