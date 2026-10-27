"""Upgrade coverage for development databases created before tool schemas were removed."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app import config, db, persistence, services
from tests.conftest import environment_payload


def _make_legacy_table(path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", path)
    db.reset_connection()
    db.init_db(path)
    conn = db.get_connection(path)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(environments)")]
    project = persistence.create_project("existing-project")
    services.create_environment(project["id"], environment_payload(),
                                status="published", set_current=True)
    conn.execute("DROP INDEX idx_environments_project")
    conn.execute("ALTER TABLE environments RENAME TO environments_current")
    legacy_schema = db.SCHEMA_SQL.replace(
        "    optimizer_config_json TEXT NOT NULL,",
        "    optimizer_config_json TEXT NOT NULL,\n    tool_schema_json TEXT NOT NULL,")
    conn.executescript(legacy_schema)
    names = ", ".join(columns)
    conn.execute(f"INSERT INTO environments ({names}, tool_schema_json) "
                 f"SELECT {names}, ? FROM environments_current", ('[{"name":"obsolete"}]',))
    conn.execute("DROP TABLE environments_current")
    return conn, columns


def test_init_db_migrates_legacy_not_null_column_and_preserves_database(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    conn, columns = _make_legacy_table(path, monkeypatch)
    before = [dict(row) for row in conn.execute("SELECT * FROM environments")]
    project = persistence.create_project("legacy-db")
    with pytest.raises(sqlite3.IntegrityError):
        services.create_environment(project["id"], environment_payload(),
                                    status="published", set_current=True)
    db.reset_connection(path)

    db.init_db(path)
    conn = db.get_connection(path)
    migrated_columns = {row[1] for row in conn.execute("PRAGMA table_info(environments)")}
    assert "tool_schema_json" not in migrated_columns
    assert set(columns) == migrated_columns
    assert [dict(row) for row in conn.execute("SELECT * FROM environments")] == [
        {key: value for key, value in row.items() if key != "tool_schema_json"}
        for row in before]
    assert persistence.get_project(project["id"])["name"] == "legacy-db"
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                        ("idx_environments_project",)).fetchone()
    environment = services.create_environment(
        project["id"], environment_payload(), status="draft", set_current=False)
    assert environment["status"] == "draft"
    assert environment["project_id"] == project["id"]
    backups = sorted(path.parent.glob(f"{path.name}.pre-tool-schema.*.bak"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    backup_conn = sqlite3.connect(backups[0])
    try:
        backup_conn.row_factory = sqlite3.Row
        assert [dict(row) for row in backup_conn.execute("SELECT * FROM environments")] == before
        assert "tool_schema_json" in {row[1] for row in backup_conn.execute(
            "PRAGMA table_info(environments)")}
    finally:
        backup_conn.close()
    backup_mtime = backups[0].stat().st_mtime_ns
    db.reset_connection(path)
    db.init_db(path)
    assert len(list(path.parent.glob(f"{path.name}.pre-tool-schema.*.bak"))) == 1
    assert backups[0].stat().st_mtime_ns == backup_mtime


def test_legacy_drop_failure_rolls_back_and_keeps_old_column(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    conn, _ = _make_legacy_table(path, monkeypatch)
    conn.execute("CREATE VIEW legacy_environment_view AS SELECT tool_schema_json FROM environments")
    db.reset_connection(path)
    with pytest.raises(sqlite3.OperationalError):
        db.init_db(path)
    conn = db.get_connection(path)
    assert "tool_schema_json" in {row[1] for row in conn.execute("PRAGMA table_info(environments)")}
    assert list(path.parent.glob(f"{path.name}.pre-tool-schema.*.bak"))


def test_concurrent_initialization_creates_one_backup_and_drops_once(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _make_legacy_table(path, monkeypatch)
    db.reset_connection(path)
    errors = []

    def initialize():
        try:
            db.init_db(path)
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=initialize) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(list(path.parent.glob(f"{path.name}.pre-tool-schema.*.bak"))) == 1
    assert "tool_schema_json" not in {row[1] for row in db.get_connection(path).execute(
        "PRAGMA table_info(environments)")}


def test_service_startup_upgrades_old_database_before_draft_api(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from app.server import create_app

    conn, _ = _make_legacy_table(tmp_path / "app.db", monkeypatch)
    existing = conn.execute("SELECT id, project_id FROM environments").fetchone()
    db.reset_connection()
    with TestClient(create_app()) as client:
        payload = {**environment_payload(), "parent_id": existing["id"], "status": "draft"}
        response = client.post(f"/api/v3/projects/{existing['project_id']}/environments", json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "draft"
        assert response.json()["parent_id"] == existing["id"]


def test_backup_failure_leaves_legacy_schema_and_removes_partial_backup(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _make_legacy_table(path, monkeypatch)
    db.reset_connection()
    original_connect = sqlite3.connect

    def fail_backup_destination(database, *args, **kwargs):
        if str(database).endswith('.tmp'):
            raise OSError('simulated backup write failure')
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', fail_backup_destination)
    with pytest.raises(OSError, match='backup write failure'):
        db.init_db(path)
    conn = db.get_connection(path)
    assert not conn.in_transaction
    assert 'tool_schema_json' in {row[1] for row in conn.execute('PRAGMA table_info(environments)')}
    assert conn.execute('SELECT count(*) FROM environments').fetchone()[0] == 1
    assert not list(path.parent.glob('*.bak'))
    assert not list(path.parent.glob('.*.tmp'))
