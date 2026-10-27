"""SQLite storage bootstrap and connection helpers.

A single SQLite database holds metadata; immutable payloads (skill packages,
trace artifacts, exports) are stored on disk under ``data/`` and referenced here.
"""

from __future__ import annotations

import sqlite3
import threading
import os
import tempfile
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

from . import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    current_skill_version_id TEXT,
    current_environment_id TEXT,
    current_dataset_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS skill_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_id TEXT,
    status TEXT NOT NULL DEFAULT 'published',
    manifest_json TEXT NOT NULL,
    files_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_run_id TEXT,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_versions_project ON skill_versions(project_id, created_at);

CREATE TABLE IF NOT EXISTS environments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_id TEXT,
    version_seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    prompt TEXT NOT NULL,
    model_config_json TEXT NOT NULL,
    optimizer_config_json TEXT NOT NULL,
    loader_config_json TEXT NOT NULL,
    run_config_json TEXT NOT NULL,
    budget_config_json TEXT NOT NULL,
    scorer_config_json TEXT NOT NULL,
    acceptance_config_json TEXT NOT NULL,
    adapter_hash TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL,
    reproducibility_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_environments_project ON environments(project_id, version_seq);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_id TEXT,
    version_seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    hash TEXT NOT NULL,
    split_config_json TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    import_report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_datasets_project ON datasets(project_id, version_seq);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    query TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    expected_json TEXT NOT NULL DEFAULT '{}',
    fixtures_json TEXT NOT NULL DEFAULT '[]',
    split TEXT NOT NULL,
    group_id TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    validation_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(dataset_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_cases_dataset ON cases(dataset_id, split);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    stage_progress_json TEXT NOT NULL DEFAULT '{}',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    baseline_skill_version_id TEXT NOT NULL,
    candidate_skill_version_id TEXT,
    environment_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    parent_run_id TEXT,
    baseline_run_id TEXT,
    baseline_cache_hit INTEGER NOT NULL DEFAULT 0,
    budget_json TEXT NOT NULL DEFAULT '{}',
    acceptance_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    cost_json TEXT NOT NULL DEFAULT '{}',
    decision TEXT,
    decision_reason TEXT,
    rules_version TEXT,
    fingerprint TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    queued_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    cancel_requested_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_idempotency
    ON runs(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS case_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT 'main',
    repeat_index INTEGER NOT NULL DEFAULT 1,
    phase TEXT NOT NULL,
    split TEXT NOT NULL DEFAULT '',
    selected_skills_json TEXT NOT NULL DEFAULT '[]',
    action TEXT,
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    actual_json TEXT NOT NULL DEFAULT '{}',
    scores_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    result_source TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    usage_json TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, phase, case_id, variant, repeat_index)
);
CREATE INDEX IF NOT EXISTS idx_case_results_run ON case_results(run_id, phase);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    stage TEXT,
    case_id TEXT,
    split TEXT,
    variant TEXT,
    repeat_index INTEGER,
    attempt INTEGER,
    call_id TEXT,
    type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, seq);

CREATE TABLE IF NOT EXISTS trace_artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    sha256 TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON trace_artifacts(run_id);

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    skill_version_id TEXT,
    diagnosis_json TEXT NOT NULL DEFAULT '{}',
    hypotheses_json TEXT NOT NULL DEFAULT '[]',
    changed_files_json TEXT NOT NULL DEFAULT '[]',
    patch_json TEXT NOT NULL DEFAULT '{}',
    expected_effect TEXT NOT NULL DEFAULT '',
    risk_cases_json TEXT NOT NULL DEFAULT '[]',
    checks_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_log(project_id, created_at);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale_json TEXT NOT NULL DEFAULT '{}',
    expected_current_version TEXT,
    applied_at TEXT,
    applied_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id);

-- Judge-model verdicts on argument values. The key covers the judge model
-- identity, prompt version, field path, expected/actual values and criteria,
-- so an identical output always receives the identical verdict (stable
-- paired validation) and is never paid for twice.
CREATE TABLE IF NOT EXISTS judge_cache (
    key TEXT PRIMARY KEY,
    passed INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    judge_model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_local = threading.local()


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 8000")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None,
                           check_same_thread=False)
    _configure(conn)
    return conn


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Thread-local connection used by request handlers and workers."""
    path = str(db_path if db_path is not None else config.DB_PATH)
    cached = getattr(_local, "conn", None)
    if cached is None or getattr(_local, "path", None) != path:
        if cached is not None:
            try:
                cached.close()
            except Exception:
                pass
        cached = connect(path)
        _local.conn = cached
        _local.path = path
    return cached


def init_db(db_path: Path | str | None = None) -> None:
    config.ensure_dirs()
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    _migrate_removed_tool_schema(conn, db_path)


def _migrate_removed_tool_schema(conn: sqlite3.Connection,
                                 db_path: Path | str | None = None) -> None:
    """Remove the pre-Prompt+Skill environment column from an existing DB.

    ``CREATE TABLE IF NOT EXISTS`` cannot change an already-created table. Older
    development databases therefore still have a NOT NULL ``tool_schema_json``
    column, which makes new environment inserts fail once the application stops
    supplying that obsolete field. Keep one SQLite backup beside the database,
    then perform the supported SQLite DROP COLUMN migration exactly once.
    """
    conn.execute("BEGIN IMMEDIATE")
    temp_backup: Path | None = None
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(environments)")}
        if "tool_schema_json" not in columns:
            conn.execute("COMMIT")
            return
        path = Path(db_path) if db_path is not None else config.DB_PATH
        backup_path = path.with_name(
            f"{path.name}.pre-tool-schema.{uuid.uuid4().hex}.bak")
        if not path.is_file():
            raise RuntimeError(f"Cannot back up database before schema upgrade: {path}")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.pre-tool-schema.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        temp_backup = Path(temp_name)
        os.chmod(temp_backup, 0o600)
        with closing(sqlite3.connect(str(path), timeout=30)) as source:
            with closing(sqlite3.connect(str(temp_backup), timeout=30)) as destination:
                source.backup(destination)
                destination.commit()
        os.replace(temp_backup, backup_path)
        temp_backup = None
        conn.execute("ALTER TABLE environments DROP COLUMN tool_schema_json")
    except Exception:
        conn.execute("ROLLBACK")
        if temp_backup is not None:
            temp_backup.unlink(missing_ok=True)
        raise
    else:
        conn.execute("COMMIT")


@contextmanager
def transaction(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def reset_connection(db_path: Path | str | None = None) -> None:
    """Close the thread-local connection (used when tests switch data roots)."""
    cached = getattr(_local, "conn", None)
    if cached is not None:
        try:
            cached.close()
        except Exception:
            pass
    _local.conn = None
    _local.path = None
