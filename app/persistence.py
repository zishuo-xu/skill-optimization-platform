"""Persistence layer: projects, versions, runs, case results, events, artifacts.

All functions return plain dictionaries (JSON columns decoded) so the API layer
can serialise them directly. Immutable byte payloads live on disk:

* ``data/skills/<skill_version_id>/files/**`` – unpacked skill package versions
* ``data/zips/<skill_version_id>.zip``        – downloadable package archives
* ``data/trace/<run_id>/<artifact_id>.json``  – large trace payloads
"""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import config, hashing, ids
from .db import get_connection, transaction
from .errors import ConflictError, NotFoundError, ValidationError


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a sqlite Row to a dict, decoding ``*_json`` columns."""
    if row is None:
        return {}
    data: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key.endswith("_json"):
            data[key[:-5]] = _loads(value, [] if key.endswith("s_json") else {})
        else:
            data[key] = value
    return data


def _fetchone(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    row = get_connection().execute(sql, params).fetchone()
    return row_to_dict(row) if row is not None else None


def _fetchall(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in get_connection().execute(sql, params).fetchall()]


def _get_or_404(table: str, ident: str, label: str) -> dict[str, Any]:
    row = _fetchone(f"SELECT * FROM {table} WHERE id = ?", (ident,))
    if not row:
        raise NotFoundError(f"{label} 不存在: {ident}", code="not_found")
    return row


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #
def create_project(name: str, description: str = "") -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValidationError("项目名称不能为空", field_path="name")
    project_id = ids.new_id("project")
    stamp = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, description, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (project_id, name, description or "", stamp, stamp),
        )
    return get_project(project_id)


def get_project(project_id: str) -> dict[str, Any]:
    return _get_or_404("projects", project_id, "项目")


def list_projects(include_archived: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM projects"
    params: list[Any] = []
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY created_at DESC"
    return _fetchall(sql, params)


def update_project(project_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {"name", "description", "archived"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_project(project_id)
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = ?" for k in updates)
    with transaction() as conn:
        conn.execute(f"UPDATE projects SET {sets} WHERE id = ?",
                     [*updates.values(), project_id])
    return get_project(project_id)


def _set_project_pointer(project_id: str, column: str, value: str | None) -> None:
    with transaction() as conn:
        conn.execute(f"UPDATE projects SET {column} = ?, updated_at = ? WHERE id = ?",
                     (value, now_iso(), project_id))


def set_current_skill_version(project_id: str, skill_version_id: str | None) -> None:
    _set_project_pointer(project_id, "current_skill_version_id", skill_version_id)


def activate_skill_version_cas(project_id: str, version_id: str,
                               expected_current_version: str | None) -> None:
    """Move the current Skill pointer only if the expected version still holds."""
    with transaction() as conn:
        if expected_current_version is None:
            cursor = conn.execute(
                "UPDATE projects SET current_skill_version_id = ?, updated_at = ? "
                "WHERE id = ?",
                (version_id, now_iso(), project_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE projects SET current_skill_version_id = ?, updated_at = ? "
                "WHERE id = ? AND current_skill_version_id = ?",
                (version_id, now_iso(), project_id, expected_current_version),
            )
        if cursor.rowcount != 1:
            raise ConflictError("项目当前 Skill 已变更，请重新比较后再恢复", code="version_conflict")


def set_current_environment(project_id: str, environment_id: str | None) -> None:
    _set_project_pointer(project_id, "current_environment_id", environment_id)


def set_current_dataset(project_id: str, dataset_id: str | None) -> None:
    _set_project_pointer(project_id, "current_dataset_id", dataset_id)


def activate_dataset_cas(project_id: str, dataset_id: str,
                         expected_current_version: str | None) -> None:
    """Atomically switch a dataset only while the observed pointer is current."""
    with transaction() as conn:
        cursor = conn.execute(
            "UPDATE projects SET current_dataset_id = ?, updated_at = ? "
            "WHERE id = ? AND current_dataset_id IS ?",
            (dataset_id, now_iso(), project_id, expected_current_version),
        )
        if cursor.rowcount != 1:
            raise ConflictError("项目当前数据集已变更", code="conflict")


# --------------------------------------------------------------------------- #
# skill versions
# --------------------------------------------------------------------------- #
def skill_version_dir(skill_version_id: str) -> Path:
    return config.SKILLS_DIR / skill_version_id


def skill_files_dir(skill_version_id: str) -> Path:
    return skill_version_dir(skill_version_id) / "files"


def create_skill_version(project_id: str, files: Mapping[str, bytes], manifest: dict[str, Any],
                         *, parent_id: str | None = None, status: str = "published",
                         message: str = "", source_run_id: str | None = None,
                         version_id: str | None = None,
                         set_current: bool = False) -> dict[str, Any]:
    if not files:
        raise ValidationError("Skill 包不能为空")
    version_id = version_id or ids.new_id("skill_version")
    target = skill_files_dir(version_id)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    content_hash = hashing.hash_files(files)
    stamp = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO skill_versions (id, project_id, parent_id, status, manifest_json,"
            " files_json, content_hash, source_run_id, message, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (version_id, project_id, parent_id, status, _dumps(manifest),
             _dumps(hashing.file_hashes(files)), content_hash, source_run_id,
             message or "", stamp),
        )
    if set_current and status == "published":
        set_current_skill_version(project_id, version_id)
    return get_skill_version(version_id)


def update_skill_version_files(skill_version_id: str, files: Mapping[str, bytes],
                               manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Rewrite a draft version's files in place (drafts only, no version bump)."""
    version = get_skill_version(skill_version_id)
    if version["status"] != "draft":
        raise ConflictError("只有草稿版本可以原地编辑", code="version_read_only")
    target = skill_files_dir(skill_version_id)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    content_hash = hashing.hash_files(files)
    with transaction() as conn:
        conn.execute(
            "UPDATE skill_versions SET files_json = ?, content_hash = ?,"
            " manifest_json = COALESCE(?, manifest_json) WHERE id = ?",
            (_dumps(hashing.file_hashes(files)), content_hash,
             _dumps(dict(manifest)) if manifest is not None else None,
             skill_version_id))
    return get_skill_version(skill_version_id)


def get_skill_version(skill_version_id: str) -> dict[str, Any]:
    return _get_or_404("skill_versions", skill_version_id, "Skill 版本")


def list_skill_versions(project_id: str) -> list[dict[str, Any]]:
    return _fetchall(
        "SELECT * FROM skill_versions WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    )


def publish_skill_version(skill_version_id: str, set_current: bool = True) -> dict[str, Any]:
    version = get_skill_version(skill_version_id)
    with transaction() as conn:
        conn.execute("UPDATE skill_versions SET status = 'published' WHERE id = ?",
                     (skill_version_id,))
    if set_current:
        set_current_skill_version(version["project_id"], skill_version_id)
    return get_skill_version(skill_version_id)


def read_skill_file(skill_version_id: str, relpath: str) -> bytes:
    root = skill_files_dir(skill_version_id).resolve()
    target = (root / relpath).resolve()
    if root not in target.parents and target != root:
        raise ValidationError("非法文件路径", field_path="path")
    if not target.is_file():
        raise NotFoundError(f"文件不存在: {relpath}", code="not_found")
    return target.read_bytes()


def export_skill_zip(skill_version_id: str, dest: Path | None = None) -> Path:
    version = get_skill_version(skill_version_id)
    dest = dest or (config.ZIPS_DIR / f"{skill_version_id}.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    root = skill_files_dir(skill_version_id)
    tmp = dest.with_suffix(".tmp.zip")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
        if "manifest.json" not in {entry["path"] for entry in version.get("files") or []}:
            zf.writestr("manifest.json", _dumps(version["manifest"]))
    tmp.replace(dest)
    return dest


def diff_skill_versions(from_id: str | None, to_id: str) -> list[dict[str, Any]]:
    """Return per-file unified diff between two versions."""
    import difflib

    from_files = _version_texts(from_id) if from_id else {}
    to_files = _version_texts(to_id)
    changes: list[dict[str, Any]] = []
    for path in sorted(set(from_files) | set(to_files)):
        before = from_files.get(path, "")
        after = to_files.get(path, "")
        if before == after:
            continue
        status = ("added" if path not in from_files
                  else "removed" if path not in to_files else "modified")
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
        ))
        changes.append({
            "path": path,
            "status": status,
            "additions": sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")),
            "deletions": sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")),
            "diff": diff,
        })
    return changes


def _version_texts(version_id: str) -> dict[str, str]:
    root = skill_files_dir(version_id)
    texts: dict[str, str] = {}
    if not root.exists():
        return texts
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            try:
                texts[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                texts[rel] = ""
    return texts


# --------------------------------------------------------------------------- #
# environments
# --------------------------------------------------------------------------- #
_ENV_FIELDS = (
    "prompt", "model_config", "optimizer_config", "loader_config",
    "run_config", "budget_config", "scorer_config", "acceptance_config",
    "adapter_hash", "reproducibility_note",
)


def compute_environment_hash(payload: Mapping[str, Any]) -> str:
    return hashing.hash_json({key: payload.get(key) for key in _ENV_FIELDS})


def create_environment(project_id: str, payload: Mapping[str, Any], *,
                       parent_id: str | None = None, status: str = "published",
                       set_current: bool = False) -> dict[str, Any]:
    missing = [field for field in ("prompt", "model_config") if not payload.get(field)]
    if missing:
        raise ValidationError(f"执行环境缺少字段: {', '.join(missing)}", field_path=missing[0])
    env_hash = compute_environment_hash(payload)
    last = _fetchone("SELECT MAX(version_seq) AS seq FROM environments WHERE project_id = ?",
                     (project_id,))
    version_seq = int((last or {}).get("seq") or 0) + 1
    environment_id = ids.new_id("environment")
    stamp = now_iso()
    values = {key: payload.get(key) for key in _ENV_FIELDS}
    with transaction() as conn:
        conn.execute(
            "INSERT INTO environments (id, project_id, parent_id, version_seq, status,"
            " prompt, model_config_json, optimizer_config_json, loader_config_json,"
            " run_config_json, budget_config_json, scorer_config_json, acceptance_config_json,"
            " adapter_hash, hash, reproducibility_note, created_at, published_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                environment_id, project_id, parent_id, version_seq, status,
                values["prompt"],
                _dumps(values["model_config"] or {}),
                _dumps(values["optimizer_config"] or {}),
                _dumps(values["loader_config"] or {}),
                _dumps(values["run_config"] or {}),
                _dumps(values["budget_config"] or {}),
                _dumps(values["scorer_config"] or {}),
                _dumps(values["acceptance_config"] or {}),
                values["adapter_hash"] or "",
                env_hash,
                values["reproducibility_note"] or "",
                stamp,
                stamp if status == "published" else None,
            ),
        )
    if set_current and status == "published":
        set_current_environment(project_id, environment_id)
    return get_environment(environment_id)


def get_environment(environment_id: str) -> dict[str, Any]:
    return _get_or_404("environments", environment_id, "执行环境版本")


def list_environments(project_id: str) -> list[dict[str, Any]]:
    return _fetchall(
        "SELECT * FROM environments WHERE project_id = ? ORDER BY version_seq DESC",
        (project_id,),
    )


def publish_environment(environment_id: str, set_current: bool = True) -> dict[str, Any]:
    env = get_environment(environment_id)
    with transaction() as conn:
        conn.execute(
            "UPDATE environments SET status = 'published', published_at = ? WHERE id = ?",
            (now_iso(), environment_id),
        )
    if set_current:
        set_current_environment(env["project_id"], environment_id)
    return get_environment(environment_id)


# --------------------------------------------------------------------------- #
# datasets / cases
# --------------------------------------------------------------------------- #
def create_dataset(project_id: str, cases: Sequence[Mapping[str, Any]],
                   split_config: Mapping[str, Any] | None = None, *,
                   source_name: str = "", import_report: Mapping[str, Any] | None = None,
                   parent_id: str | None = None, status: str = "published",
                   set_current: bool = False,
                   dataset_id: str | None = None,
                   version_seq: int | None = None) -> dict[str, Any]:
    if not cases:
        raise ValidationError("数据集不能为空")
    payload = [dict(case) for case in cases]
    ds_hash = hashing.hash_json(payload)
    dataset_id = dataset_id or ids.new_id("dataset")
    if version_seq is None:
        last = _fetchone("SELECT MAX(version_seq) AS seq FROM datasets WHERE project_id = ?",
                         (project_id,))
        version_seq = int((last or {}).get("seq") or 0) + 1
    stamp = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO datasets (id, project_id, parent_id, version_seq, status, hash,"
            " split_config_json, source_name, import_report_json, created_at, published_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dataset_id, project_id, parent_id, version_seq, status, ds_hash,
             _dumps(split_config or {}), source_name or "",
             _dumps(import_report or {}), stamp,
             stamp if status == "published" else None),
        )
        for case in payload:
            conn.execute(
                "INSERT INTO cases (dataset_id, case_id, query, context_json, expected_json,"
                " fixtures_json, split, group_id, tags_json, validation_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (dataset_id, case["case_id"], case["query"],
                 _dumps(case.get("context") or {}),
                 _dumps(case.get("expected") or {}),
                 _dumps(case.get("fixtures") or []),
                 case.get("split") or "optimize",
                 case.get("group_id") or "",
                 _dumps(case.get("tags") or []),
                 _dumps(case.get("validation") or {}),
                 stamp),
            )
    if set_current and status == "published":
        set_current_dataset(project_id, dataset_id)
    return get_dataset(dataset_id)


def get_dataset(dataset_id: str) -> dict[str, Any]:
    dataset = _get_or_404("datasets", dataset_id, "数据集版本")
    dataset["case_count"] = _fetchone(
        "SELECT COUNT(*) AS n FROM cases WHERE dataset_id = ?", (dataset_id,))["n"]
    return dataset


def list_datasets(project_id: str) -> list[dict[str, Any]]:
    rows = _fetchall(
        "SELECT * FROM datasets WHERE project_id = ? ORDER BY version_seq DESC",
        (project_id,),
    )
    for row in rows:
        row["case_count"] = _fetchone(
            "SELECT COUNT(*) AS n FROM cases WHERE dataset_id = ?", (row["id"],))["n"]
    return rows


def publish_dataset(dataset_id: str, set_current: bool = True) -> dict[str, Any]:
    ds = get_dataset(dataset_id)
    with transaction() as conn:
        conn.execute("UPDATE datasets SET status = 'published', published_at = ? WHERE id = ?",
                     (now_iso(), dataset_id))
    if set_current:
        set_current_dataset(ds["project_id"], dataset_id)
    return get_dataset(dataset_id)


def publish_dataset_cas(dataset_id: str, expected_hash: str,
                        expected_current_version: str | None, set_current: bool = True,
                        *, import_report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    ds = get_dataset(dataset_id)
    with transaction() as conn:
        cursor = conn.execute(
            "UPDATE datasets SET status='published', published_at=?, import_report_json=? "
            "WHERE id=? AND status='draft' AND hash=?",
            (now_iso(), _dumps(import_report if import_report is not None else ds.get("import_report") or {}),
             dataset_id, expected_hash))
        if cursor.rowcount != 1:
            raise ConflictError("数据集草稿已变更，请刷新后重试", code="dataset_conflict")
        if set_current:
            pointer = conn.execute(
                "UPDATE projects SET current_dataset_id=?, updated_at=? WHERE id=? AND current_dataset_id IS ?",
                (dataset_id, now_iso(), ds["project_id"], expected_current_version))
            if pointer.rowcount != 1:
                raise ConflictError("项目当前数据集已变更", code="conflict")
    return get_dataset(dataset_id)


def clone_dataset(dataset_id: str, *, status: str = "draft") -> dict[str, Any]:
    source = get_dataset(dataset_id)
    if source["status"] != "published":
        raise ConflictError("只能从已发布数据集创建编辑草稿", code="dataset_not_published")
    cases = [_case_payload(row) for row in all_cases(dataset_id)]
    return create_dataset(source["project_id"], cases,
                          source.get("split_config") or {},
                          source_name=source.get("source_name", ""),
                          import_report=source.get("import_report") or {},
                          parent_id=dataset_id, status=status, set_current=False)


def _case_payload(case: Mapping[str, Any]) -> dict[str, Any]:
    payload = {key: case.get(key) for key in
               ("case_id", "query", "context", "expected", "fixtures", "split", "group_id", "tags", "validation")}
    payload["validation"] = case.get("validation") or {}
    return payload


def update_case_cas(dataset_id: str, case: Mapping[str, Any], expected_hash: str,
                    import_report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    ds = get_dataset(dataset_id)
    if ds["status"] != "draft":
        raise ConflictError("只有草稿数据集可以编辑", code="dataset_read_only")
    case_id = str(case.get("case_id") or "")
    if not case_id or not get_case(dataset_id, case_id):
        raise NotFoundError(f"案例不存在: {case_id}", code="not_found")
    old_cases = all_cases(dataset_id)
    payload = [_case_payload(row) for row in old_cases]
    for index, row in enumerate(payload):
        if row["case_id"] == case_id:
            payload[index] = _case_payload(case)
            break
    new_hash = hashing.hash_json(payload)
    with transaction() as conn:
        cursor = conn.execute("UPDATE datasets SET hash = ?, import_report_json = ? WHERE id = ? AND status = 'draft' AND hash = ?",
                              (new_hash, _dumps(import_report or {}), dataset_id, expected_hash))
        if cursor.rowcount != 1:
            raise ConflictError("数据集已被其他编辑修改，请刷新后重试", code="dataset_conflict")
        conn.execute("UPDATE cases SET query = ?, context_json = ?, expected_json = ?, fixtures_json = ?, split = ?, group_id = ?, tags_json = ?, validation_json = ? WHERE dataset_id = ? AND case_id = ?",
                     (case["query"], _dumps(case.get("context") or {}), _dumps(case.get("expected") or {}),
                      _dumps(case.get("fixtures") or []), case.get("split") or "optimize",
                      case.get("group_id") or "", _dumps(case.get("tags") or []),
                      _dumps(case.get("validation") or {}), dataset_id, case_id))
    return {"dataset": get_dataset(dataset_id), "case": get_case(dataset_id, case_id)}


def _case_row(row: dict[str, Any]) -> dict[str, Any]:
    row["expected"] = row.get("expected") or {}
    row["fixtures"] = row.get("fixtures") or []
    return row


def list_cases(dataset_id: str, *, split: str | None = None,
               search: str | None = None, tag: str | None = None,
               group_id: str | None = None,
               limit: int = 100, offset: int = 0) -> dict[str, Any]:
    clauses = ["dataset_id = ?"]
    params: list[Any] = [dataset_id]
    if split:
        clauses.append("split = ?")
        params.append(split)
    if search:
        clauses.append("query LIKE ?")
        params.append(f"%{search}%")
    if tag:
        clauses.append("tags_json LIKE ?")
        params.append(f'%"{tag}"%')
    if group_id:
        clauses.append("group_id = ?")
        params.append(group_id)
    where = " WHERE " + " AND ".join(clauses)
    total = _fetchone(f"SELECT COUNT(*) AS n FROM cases{where}", params)["n"]
    rows = _fetchall(
        f"SELECT * FROM cases{where} ORDER BY split, case_id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return {"items": [_case_row(row) for row in rows], "total": total,
            "limit": limit, "offset": offset}


def get_case(dataset_id: str, case_id: str) -> dict[str, Any]:
    row = _fetchone("SELECT * FROM cases WHERE dataset_id = ? AND case_id = ?",
                    (dataset_id, case_id))
    if not row:
        raise NotFoundError(f"案例不存在: {case_id}", code="not_found")
    return _case_row(row)


def all_cases(dataset_id: str) -> list[dict[str, Any]]:
    rows = _fetchall("SELECT * FROM cases WHERE dataset_id = ? ORDER BY split, case_id",
                     (dataset_id,))
    return [_case_row(row) for row in rows]


def dataset_coverage(dataset_id: str, skill_ids: Iterable[str],
                     tools: Iterable[str]) -> dict[str, Any]:
    cases = all_cases(dataset_id)
    skill_counts: dict[str, dict[str, int]] = {}
    for skill_id in skill_ids:
        skill_counts[skill_id] = {"optimize": 0, "validation": 0, "final_test": 0, "total": 0}
    for case in cases:
        split = case.get("split") or "optimize"
        routes = (case.get("expected") or {}).get("routes") or []
        seen: set[str] = set()
        for route in routes:
            for skill_id in route:
                if skill_id in skill_counts and skill_id not in seen:
                    seen.add(skill_id)
                    skill_counts[skill_id][split] = skill_counts[skill_id].get(split, 0) + 1
                    skill_counts[skill_id]["total"] += 1
    tool_counts: dict[str, int] = {tool: 0 for tool in tools}
    for case in cases:
        for call in (case.get("expected") or {}).get("tool_calls") or []:
            name = call.get("name")
            if name in tool_counts:
                tool_counts[name] += 1
    multi_route = sum(1 for c in cases if len((c.get("expected") or {}).get("routes") or []) > 1)
    actions = {"tool_call": 0, "clarify": 0, "no_action": 0}
    for case in cases:
        action = (case.get("expected") or {}).get("action") or "tool_call"
        actions[action] = actions.get(action, 0) + 1
    split_counts: dict[str, int] = {}
    for case in cases:
        split_counts[case.get("split") or "optimize"] = split_counts.get(
            case.get("split") or "optimize", 0) + 1
    return {
        "total": len(cases),
        "split_counts": split_counts,
        "skills": skill_counts,
        "tools": tool_counts,
        "multi_route_cases": multi_route,
        "actions": actions,
        "clarify_cases": actions.get("clarify", 0),
        "no_action_cases": actions.get("no_action", 0),
    }


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #
_RUN_JSON_FIELDS = {"stage_progress", "snapshot", "budget", "acceptance", "metrics", "cost"}
_RUN_PLAIN_FIELDS = {
    "status", "stage", "baseline_skill_version_id", "candidate_skill_version_id",
    "environment_id", "dataset_id", "parent_run_id", "baseline_run_id",
    "baseline_cache_hit", "decision", "decision_reason", "rules_version",
    "fingerprint", "idempotency_key", "lease_owner", "lease_expires_at",
    "heartbeat_at", "queued_at", "started_at", "finished_at", "cancel_requested_at",
    "error_code", "error_message",
}


def create_run(project_id: str, *, mode: str, baseline_skill_version_id: str,
               environment_id: str, dataset_id: str,
               candidate_skill_version_id: str | None = None,
               parent_run_id: str | None = None, baseline_run_id: str | None = None,
               budget: Mapping[str, Any] | None = None,
               acceptance: Mapping[str, Any] | None = None,
               snapshot: Mapping[str, Any] | None = None,
               idempotency_key: str | None = None,
               fingerprint: str = "", status: str = "queued",
               stage: str = "preflight") -> dict[str, Any]:
    run_id = ids.new_id("run")
    stamp = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO runs (id, project_id, mode, status, stage, snapshot_json,"
            " baseline_skill_version_id, candidate_skill_version_id, environment_id,"
            " dataset_id, parent_run_id, baseline_run_id, budget_json, acceptance_json,"
            " fingerprint, idempotency_key, queued_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, project_id, mode, status, stage,
             _dumps(snapshot or {}), baseline_skill_version_id,
             candidate_skill_version_id, environment_id, dataset_id, parent_run_id,
             baseline_run_id, _dumps(budget or {}), _dumps(acceptance or {}),
             fingerprint, idempotency_key, stamp, stamp),
        )
    return get_run(run_id)


def get_run(run_id: str) -> dict[str, Any]:
    run = _get_or_404("runs", run_id, "运行")
    run["case_stats"] = case_result_stats(run_id)
    return run


def list_runs(*, project_id: str | None = None, status: str | None = None,
              limit: int = 50, offset: int = 0) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    total = _fetchone(f"SELECT COUNT(*) AS n FROM runs{where}", params)["n"]
    rows = _fetchall(
        f"SELECT * FROM runs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def update_run(run_id: str, **fields: Any) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _RUN_JSON_FIELDS:
            updates[key + "_json"] = _dumps(value)
        elif key in _RUN_PLAIN_FIELDS:
            updates[key] = value
        else:
            raise ValidationError(f"不支持更新运行字段: {key}")
    if not updates:
        return get_run(run_id)
    sets = ", ".join(f"{column} = ?" for column in updates)
    with transaction() as conn:
        conn.execute(f"UPDATE runs SET {sets} WHERE id = ?",
                     [*updates.values(), run_id])
    return get_run(run_id)


def find_idempotent_run(project_id: str, idempotency_key: str) -> dict[str, Any] | None:
    return _fetchone(
        "SELECT * FROM runs WHERE project_id = ? AND idempotency_key = ?",
        (project_id, idempotency_key),
    )


def find_latest_evaluation(project_id: str, fingerprint: str) -> dict[str, Any] | None:
    return _fetchone(
        "SELECT * FROM runs WHERE project_id = ? AND fingerprint = ? AND mode = 'evaluate'"
        " AND status = 'completed' ORDER BY created_at DESC LIMIT 1",
        (project_id, fingerprint),
    )


def find_cached_baseline(project_id: str, fingerprint: str) -> dict[str, Any] | None:
    candidates = _fetchall(
        "SELECT * FROM runs WHERE project_id = ? AND fingerprint = ? AND mode = 'baseline'"
        " AND status = 'completed' AND decision IS NULL ORDER BY created_at DESC",
        (project_id, fingerprint),
    )
    for run in candidates:
        expected = {case["case_id"] for case in all_cases(run["dataset_id"])
                    if (case.get("split") or "optimize") == "optimize"}
        rows = get_case_results_for_run(run["id"], "baseline")
        actual = {row["case_id"] for row in rows}
        if (expected and actual == expected
                and all((row.get("scores") or {}).get("status") in ("pass", "fail", "invalid")
                        for row in rows)):
            return run
    return None


def claim_run(worker_id: str, lease_seconds: int = 30) -> dict[str, Any] | None:
    stamp = now_iso()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    with transaction() as conn:
        rows = conn.execute(
            "SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchall()
        if not rows:
            return None
        run_id = rows[0]["id"]
        cursor = conn.execute(
            "UPDATE runs SET status = 'running', lease_owner = ?, lease_expires_at = ?,"
            " heartbeat_at = ?, started_at = COALESCE(started_at, ?),"
            " stage = CASE WHEN stage = 'preflight' THEN 'preflight' ELSE stage END"
            " WHERE id = ? AND status = 'queued'",
            (worker_id, expires, stamp, stamp, run_id),
        )
        if cursor.rowcount != 1:
            return None
    return get_run(run_id)


def heartbeat_run(run_id: str, worker_id: str, lease_seconds: int = 30) -> bool:
    expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    with transaction() as conn:
        cursor = conn.execute(
            "UPDATE runs SET heartbeat_at = ?, lease_expires_at = ?"
            " WHERE id = ? AND lease_owner = ? AND status IN ('running', 'cancel_requested')",
            (now_iso(), expires, run_id, worker_id),
        )
    return cursor.rowcount == 1


def recover_stale_runs(now: str | None = None) -> list[str]:
    """Return stale running runs to the queue (crash recovery)."""
    now = now or now_iso()
    recovered: list[str] = []
    with transaction() as conn:
        rows = conn.execute(
            "SELECT id FROM runs WHERE status IN ('running', 'cancel_requested')"
            " AND (lease_expires_at IS NULL OR lease_expires_at < ?)", (now,)
        ).fetchall()
        for row in rows:
            run_id = row["id"]
            conn.execute(
                "UPDATE runs SET status = 'queued', lease_owner = NULL,"
                " lease_expires_at = NULL WHERE id = ?", (run_id,))
            recovered.append(run_id)
    return recovered


def request_cancel(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if run["status"] in config.TERMINAL_RUN_STATUSES:
        return run
    if run["status"] == "queued":
        with transaction() as conn:
            conn.execute(
                "UPDATE runs SET status = 'cancelled', cancel_requested_at = ?,"
                " finished_at = ?, decision_reason = '用户取消（未开始执行）'"
                " WHERE id = ? AND status = 'queued'",
                (now_iso(), now_iso(), run_id),
            )
    else:
        with transaction() as conn:
            conn.execute(
                "UPDATE runs SET status = 'cancel_requested', cancel_requested_at = ?"
                " WHERE id = ? AND status = 'running'",
                (now_iso(), run_id),
            )
    return get_run(run_id)


def finish_run(run_id: str, *, status: str, decision: str | None = None,
               decision_reason: str = "", metrics: Mapping[str, Any] | None = None,
               cost: Mapping[str, Any] | None = None,
               error_code: str | None = None,
               error_message: str | None = None,
               candidate_skill_version_id: str | None = None,
               stage: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "status": status,
        "finished_at": now_iso(),
        "decision": decision,
        "decision_reason": decision_reason,
        "error_code": error_code,
        "error_message": error_message,
    }
    if metrics is not None:
        fields["metrics"] = metrics
    if cost is not None:
        fields["cost"] = cost
    if candidate_skill_version_id:
        fields["candidate_skill_version_id"] = candidate_skill_version_id
    if stage:
        fields["stage"] = stage
    return update_run(run_id, **fields)


# --------------------------------------------------------------------------- #
# case results
# --------------------------------------------------------------------------- #
def save_case_result(run_id: str, case_id: str, phase: str, *,
                     variant: str = "main", repeat_index: int = 1,
                     split: str = "", selected_skills: Sequence[str] = (),
                     action: str | None = None,
                     tool_calls: Sequence[Mapping[str, Any]] = (),
                     missing_fields: Sequence[str] = (),
                     actual: Mapping[str, Any] | None = None,
                     scores: Mapping[str, Any] | None = None,
                     error_code: str | None = None,
                     error_message: str | None = None,
                     result_source: str | None = None,
                     capabilities: Mapping[str, Any] | None = None,
                     usage: Mapping[str, Any] | None = None,
                     duration_ms: int | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO case_results (run_id, case_id, variant, repeat_index, phase, split,"
            " selected_skills_json, action, tool_calls_json, missing_fields_json, actual_json,"
            " scores_json, error_code, error_message, result_source, capabilities_json,"
            " usage_json, duration_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(run_id, phase, case_id, variant, repeat_index) DO UPDATE SET"
            " split = excluded.split, selected_skills_json = excluded.selected_skills_json,"
            " action = excluded.action, tool_calls_json = excluded.tool_calls_json,"
            " missing_fields_json = excluded.missing_fields_json,"
            " actual_json = excluded.actual_json, scores_json = excluded.scores_json,"
            " error_code = excluded.error_code, error_message = excluded.error_message,"
            " result_source = excluded.result_source, capabilities_json = excluded.capabilities_json,"
            " usage_json = excluded.usage_json, duration_ms = excluded.duration_ms",
            (run_id, case_id, variant, repeat_index, phase, split,
             _dumps(list(selected_skills)), action, _dumps(list(tool_calls)),
             _dumps(list(missing_fields)), _dumps(actual or {}), _dumps(scores or {}),
             error_code, error_message, result_source, _dumps(capabilities or {}),
             _dumps(usage or {}), duration_ms, now_iso()),
        )


def list_case_results(run_id: str, *, phase: str | None = None,
                      case_id: str | None = None, split: str | None = None,
                      only_failed: bool = False,
                      limit: int = 200, offset: int = 0) -> dict[str, Any]:
    clauses = ["run_id = ?"]
    params: list[Any] = [run_id]
    if phase:
        clauses.append("phase = ?")
        params.append(phase)
    if case_id:
        clauses.append("case_id = ?")
        params.append(case_id)
    if split:
        clauses.append("split = ?")
        params.append(split)
    where = " WHERE " + " AND ".join(clauses)
    rows = _fetchall(
        f"SELECT * FROM case_results{where} ORDER BY case_id, repeat_index LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    if only_failed:
        rows = [row for row in rows if not (row.get("scores") or {}).get("passed")]
    total = len(rows)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def get_case_results_for_run(run_id: str, phase: str | None = None) -> list[dict[str, Any]]:
    rows = _fetchall(
        "SELECT * FROM case_results WHERE run_id = ?" + (" AND phase = ?" if phase else "")
        + " ORDER BY case_id, repeat_index",
        (run_id, phase) if phase else (run_id,),
    )
    return rows


def get_case_result(run_id: str, phase: str, case_id: str, *,
                    variant: str = "main", repeat_index: int = 1) -> dict[str, Any] | None:
    return _fetchone(
        "SELECT * FROM case_results WHERE run_id = ? AND phase = ? AND case_id = ?"
        " AND variant = ? AND repeat_index = ?",
        (run_id, phase, case_id, variant, repeat_index),
    )


def case_result_stats(run_id: str) -> dict[str, int]:
    row = _fetchone(
        "SELECT COUNT(*) AS planned,"
        " SUM(CASE WHEN scores_json LIKE '%\"passed\"%' THEN 1 ELSE 0 END) AS scored,"
        " SUM(CASE WHEN error_code IS NOT NULL THEN 1 ELSE 0 END) AS errors"
        " FROM case_results WHERE run_id = ?", (run_id,))
    return {
        "case_results": int((row or {}).get("planned") or 0),
        "scored": int((row or {}).get("scored") or 0),
        "errors": int((row or {}).get("errors") or 0),
    }


def delete_case_results(run_id: str, phase: str | None = None) -> None:
    with transaction() as conn:
        if phase:
            conn.execute("DELETE FROM case_results WHERE run_id = ? AND phase = ?",
                         (run_id, phase))
        else:
            conn.execute("DELETE FROM case_results WHERE run_id = ?", (run_id,))


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
def append_event(run_id: str, event_type: str, *, stage: str | None = None,
                 case_id: str | None = None, split: str | None = None,
                 variant: str | None = None, repeat_index: int | None = None,
                 attempt: int | None = None, call_id: str | None = None,
                 level: str = "info", payload: Mapping[str, Any] | None = None,
                 ts: str | None = None) -> dict[str, Any]:
    stamp = ts or now_iso()
    with transaction() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS seq FROM events WHERE run_id = ?",
            (run_id,)).fetchone()
        seq = int(row["seq"]) + 1
        conn.execute(
            "INSERT INTO events (run_id, seq, ts, stage, case_id, split, variant,"
            " repeat_index, attempt, call_id, type, level, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, seq, stamp, stage, case_id, split, variant, repeat_index,
             attempt, call_id, event_type, level, _dumps(payload or {})),
        )
    return {
        "run_id": run_id, "seq": seq, "ts": stamp, "stage": stage,
        "case_id": case_id, "split": split, "variant": variant,
        "repeat_index": repeat_index, "attempt": attempt, "call_id": call_id,
        "type": event_type, "level": level, "payload": dict(payload or {}),
    }


def list_events(run_id: str, *, after_seq: int = 0, limit: int = 200,
                level: str | None = None, event_type: str | None = None,
                case_ids: Sequence[str] | None = None,
                stage: str | None = None,
                errors_only: bool = False) -> list[dict[str, Any]]:
    clauses = ["run_id = ?", "seq > ?"]
    params: list[Any] = [run_id, after_seq]
    if level:
        clauses.append("level = ?")
        params.append(level)
    if event_type:
        clauses.append("type = ?")
        params.append(event_type)
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    if case_ids:
        placeholders = ",".join("?" for _ in case_ids)
        clauses.append(f"case_id IN ({placeholders})")
        params.extend(case_ids)
    if errors_only:
        clauses.append("level IN ('error', 'warning')")
    sql = ("SELECT * FROM events WHERE " + " AND ".join(clauses)
           + " ORDER BY seq ASC LIMIT ?")
    return _fetchall(sql, [*params, limit])


def max_event_seq(run_id: str) -> int:
    row = _fetchone("SELECT COALESCE(MAX(seq), 0) AS seq FROM events WHERE run_id = ?",
                    (run_id,))
    return int((row or {}).get("seq") or 0)


def get_event(run_id: str, seq: int) -> dict[str, Any] | None:
    return _fetchone("SELECT * FROM events WHERE run_id = ? AND seq = ?", (run_id, seq))


# --------------------------------------------------------------------------- #
# trace artifacts
# --------------------------------------------------------------------------- #
def save_artifact(run_id: str, kind: str, data: bytes,
                  *, content_type: str = "application/json",
                  summary: str = "") -> dict[str, Any]:
    artifact_id = ids.new_id("artifact")
    root = config.TRACE_DIR / run_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{artifact_id}.json"
    path.write_bytes(data)
    record = {
        "id": artifact_id, "run_id": run_id, "kind": kind,
        "content_type": content_type, "sha256": hashing.sha256_bytes(data),
        "size": len(data), "path": str(path), "summary": summary,
        "created_at": now_iso(),
    }
    with transaction() as conn:
        conn.execute(
            "INSERT INTO trace_artifacts (id, run_id, kind, content_type, sha256, size,"
            " path, summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, run_id, kind, content_type, record["sha256"], len(data),
             str(path), summary, record["created_at"]),
        )
    return record


def get_artifact(run_id: str, artifact_id: str) -> dict[str, Any]:
    artifact = _fetchone(
        "SELECT * FROM trace_artifacts WHERE id = ? AND run_id = ?",
        (artifact_id, run_id))
    if not artifact:
        raise NotFoundError(f"轨迹资源不存在: {artifact_id}", code="not_found")
    path = Path(artifact["path"]).resolve()
    trace_root = config.TRACE_DIR.resolve()
    if trace_root not in path.parents:
        raise ValidationError("非法资源路径", field_path="path")
    artifact["content_path"] = path
    return artifact


# --------------------------------------------------------------------------- #
# candidates / decisions
# --------------------------------------------------------------------------- #
def create_candidate(run_id: str, *, skill_version_id: str | None,
                     diagnosis: Mapping[str, Any], hypotheses: Sequence[Any],
                     changed_files: Sequence[Any], patch: Mapping[str, Any],
                     expected_effect: str = "", risk_cases: Sequence[Any] = (),
                     checks: Mapping[str, Any] | None = None) -> dict[str, Any]:
    candidate_id = ids.new_id("candidate")
    with transaction() as conn:
        conn.execute(
            "INSERT INTO candidates (id, run_id, skill_version_id, diagnosis_json,"
            " hypotheses_json, changed_files_json, patch_json, expected_effect,"
            " risk_cases_json, checks_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, run_id, skill_version_id, _dumps(diagnosis),
             _dumps(list(hypotheses)), _dumps(list(changed_files)), _dumps(patch),
             expected_effect, _dumps(list(risk_cases)), _dumps(checks or {}), now_iso()),
        )
    return get_candidate(candidate_id)


def get_candidate(candidate_id: str) -> dict[str, Any]:
    return _get_or_404("candidates", candidate_id, "候选")


def get_candidate_for_run(run_id: str) -> dict[str, Any] | None:
    return _fetchone("SELECT * FROM candidates WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                     (run_id,))


def update_candidate(candidate_id: str, **fields: Any) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"skill_version_id", "expected_effect"}:
            mapped[key] = value
        elif key == "checks":
            mapped["checks_json"] = _dumps(value)
        else:
            raise ValidationError(f"不支持更新候选字段: {key}")
    if not mapped:
        return get_candidate(candidate_id)
    sets = ", ".join(f"{column} = ?" for column in mapped)
    with transaction() as conn:
        conn.execute(f"UPDATE candidates SET {sets} WHERE id = ?",
                     [*mapped.values(), candidate_id])
    return get_candidate(candidate_id)


def create_decision(run_id: str, decision: str, rules_version: str,
                    rationale: Mapping[str, Any], *,
                    expected_current_version: str | None = None) -> dict[str, Any]:
    decision_id = ids.new_id("decision")
    with transaction() as conn:
        conn.execute(
            "INSERT INTO decisions (id, run_id, rules_version, decision, rationale_json,"
            " expected_current_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (decision_id, run_id, rules_version, decision, _dumps(rationale),
             expected_current_version, now_iso()),
        )
    return get_decision(decision_id)


def get_decision(decision_id: str) -> dict[str, Any]:
    return _get_or_404("decisions", decision_id, "决策")


def get_decision_for_run(run_id: str) -> dict[str, Any] | None:
    return _fetchone("SELECT * FROM decisions WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                     (run_id,))


def mark_decision_applied(decision_id: str, expected_current_version: str,
                          applied_by: str = "user") -> dict[str, Any]:
    with transaction() as conn:
        conn.execute(
            "UPDATE decisions SET applied_at = ?, applied_by = ?,"
            " expected_current_version = ? WHERE id = ?",
            (now_iso(), applied_by, expected_current_version, decision_id),
        )
    return get_decision(decision_id)


def apply_candidate_cas(run_id: str, project_id: str, candidate_version_id: str,
                        expected_skill_id: str, expected_environment_id: str | None,
                        expected_dataset_id: str | None, decision_id: str) -> None:
    """Atomically publish a candidate and mark its decision applied."""
    with transaction() as conn:
        cursor = conn.execute(
            "UPDATE projects SET current_skill_version_id = ?, updated_at = ? "
            "WHERE id = ? AND current_skill_version_id = ? "
            "AND current_environment_id IS ? AND current_dataset_id IS ?",
            (candidate_version_id, now_iso(), project_id, expected_skill_id,
             expected_environment_id, expected_dataset_id),
        )
        if cursor.rowcount != 1:
            raise ConflictError("项目配置已变更，请重新比较后再应用", code="version_conflict")
        decision = conn.execute(
            "UPDATE decisions SET applied_at = ?, applied_by = ?, "
            "expected_current_version = ? WHERE id = ? AND applied_at IS NULL",
            (now_iso(), "user", expected_skill_id, decision_id),
        )
        if decision.rowcount != 1:
            raise ConflictError("候选已经应用或决策已失效", code="version_conflict")
        conn.execute("UPDATE skill_versions SET status = 'published' WHERE id = ?",
                     (candidate_version_id,))


# --------------------------------------------------------------------------- #
# audit log
# --------------------------------------------------------------------------- #
def append_audit(project_id: str, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    stamp = now_iso()
    with transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO audit_log (project_id, action, payload_json, created_at)"
            " VALUES (?, ?, ?, ?)", (project_id, action, _dumps(payload or {}), stamp))
        audit_id = cursor.lastrowid
    return {"id": audit_id, "project_id": project_id, "action": action,
            "payload": dict(payload or {}), "created_at": stamp}


def list_audit(project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return _fetchall(
        "SELECT * FROM audit_log WHERE project_id = ? ORDER BY id DESC LIMIT ?",
        (project_id, limit))


# --------------------------------------------------------------------------- #
# integrity helpers
# --------------------------------------------------------------------------- #
def skill_version_integrity(skill_version_id: str) -> dict[str, Any]:
    """Verify on-disk files match the recorded hashes."""
    version = get_skill_version(skill_version_id)
    root = skill_files_dir(skill_version_id)
    errors: list[str] = []
    actual: dict[str, bytes] = {}
    if not root.exists():
        errors.append("版本文件目录缺失")
    else:
        for path in root.rglob("*"):
            if path.is_file():
                actual[path.relative_to(root).as_posix()] = path.read_bytes()
    expected_hash = version["content_hash"]
    if actual and hashing.hash_files(actual) != expected_hash:
        errors.append("内容哈希与版本记录不一致")
    for entry in version.get("files") or []:
        data = actual.get(entry["path"])
        if data is None:
            errors.append(f"缺少文件: {entry['path']}")
        elif hashing.sha256_bytes(data) != entry["sha256"]:
            errors.append(f"文件哈希不一致: {entry['path']}")
    return {"ok": not errors, "errors": errors, "files": len(actual)}


def assert_project_version(project_id: str, expected_current_version: str | None) -> None:
    project = get_project(project_id)
    if expected_current_version is not None and \
            project["current_skill_version_id"] != expected_current_version:
        raise ConflictError(
            "项目当前版本已变更，请重新比较后再应用",
            details={"current_version": project["current_skill_version_id"],
                     "expected_current_version": expected_current_version},
        )


# --------------------------------------------------------------------------- #
# judge cache
# --------------------------------------------------------------------------- #
def get_judgments(keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Cached judge verdicts by cache key."""
    keys = [key for key in dict.fromkeys(keys) if key]
    if not keys:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in _fetchall(f"SELECT * FROM judge_cache WHERE key IN ({placeholders})", chunk):
            found[row["key"]] = {"passed": bool(row["passed"]), "reason": row["reason"],
                                 "judge_model": row["judge_model"], "cached": True}
    return found


def save_judgments(verdicts: Mapping[str, Mapping[str, Any]], judge_model: str) -> None:
    """Store verdicts; the first verdict for a key wins (never overwritten)."""
    if not verdicts:
        return
    stamp = now_iso()
    with transaction() as conn:
        for key, verdict in verdicts.items():
            conn.execute(
                "INSERT OR IGNORE INTO judge_cache (key, passed, reason, judge_model, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, 1 if verdict.get("passed") else 0, str(verdict.get("reason") or "")[:500],
                 judge_model, stamp))
