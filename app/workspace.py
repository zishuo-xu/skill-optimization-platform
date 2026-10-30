"""Project working copy: edit-and-save settings, frozen automatically at run start.

Users no longer manage drafts and publishing by hand. Each kind of input
(Skill, environment, dataset) has at most one *pending* draft: the newest draft
whose chain of draft parents leads back to the project's current published
version. Settings pages edit that draft (creating it from the current version
on first edit); starting a run publishes every pending draft that actually
changed and points the project at it. Published versions, hashes and run
snapshots keep working exactly as before — only the manual steps disappear.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import persistence
from .db import transaction
from .errors import ConflictError, ValidationError

KINDS: dict[str, dict[str, str]] = {
    "skill": {"label": "Skill", "pointer": "current_skill_version_id",
              "table": "skill_versions", "hash": "content_hash"},
    "environment": {"label": "Prompt 与模型", "pointer": "current_environment_id",
                    "table": "environments", "hash": "hash"},
    "dataset": {"label": "测试集", "pointer": "current_dataset_id",
                "table": "datasets", "hash": "hash"},
}
DISCARDED = "discarded"


def _kind(kind: str) -> dict[str, str]:
    if kind not in KINDS:
        raise ValidationError("kind 只能是 skill/environment/dataset", field_path="kind")
    return KINDS[kind]


def _rows(project_id: str, kind: str) -> list[dict[str, Any]]:
    table = _kind(kind)["table"]
    order = "created_at DESC" if kind == "skill" else "version_seq DESC, created_at DESC"
    conn = persistence.get_connection()
    return [dict(row) for row in conn.execute(
        f"SELECT id, parent_id, status, created_at, {KINDS[kind]['hash']} AS hash "
        f"FROM {table} WHERE project_id = ? ORDER BY {order}", (project_id,)).fetchall()]


def _get(kind: str, version_id: str) -> dict[str, Any]:
    if kind == "skill":
        return persistence.get_skill_version(version_id)
    if kind == "environment":
        return persistence.get_environment(version_id)
    return persistence.get_dataset(version_id)


def pending_draft(project: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    """Newest draft rooted (through draft parents only) at the current version."""
    meta = _kind(kind)
    current = project.get(meta["pointer"])
    rows = _rows(project["id"], kind)
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        if row["status"] != "draft":
            continue
        cursor, seen = row, set()
        while cursor and cursor["status"] == "draft" and cursor["id"] not in seen:
            seen.add(cursor["id"])
            parent = cursor.get("parent_id")
            cursor = by_id.get(parent) if parent else None
            if parent is None:
                break
        root = cursor["id"] if cursor else None
        if root == current:
            return row
    return None


def _changed(project: Mapping[str, Any], kind: str, draft: Mapping[str, Any]) -> bool:
    current = project.get(_kind(kind)["pointer"])
    if not current:
        return True
    current_row = next((row for row in _rows(project["id"], kind) if row["id"] == current), None)
    return not current_row or current_row.get("hash") != draft.get("hash")


def summary(project_id: str) -> dict[str, Any]:
    """What each input will use at the next run, and what is still missing."""
    project = persistence.get_project(project_id)
    items: dict[str, Any] = {}
    for kind, meta in KINDS.items():
        current = project.get(meta["pointer"])
        draft = pending_draft(project, kind)
        changed = bool(draft and _changed(project, kind, draft))
        items[kind] = {
            "kind": kind, "label": meta["label"], "current_id": current,
            "draft_id": draft["id"] if draft else None,
            "pending": changed,
            "configured": bool(current or draft),
        }
    pending = [item for item in items.values() if item["pending"]]
    missing = [item for item in items.values() if not item["configured"]]
    return {"items": items, "pending": pending, "missing": missing}


def ensure_draft(project_id: str, kind: str) -> dict[str, Any]:
    """Return the editable working copy, creating it from the current version."""
    project = persistence.get_project(project_id)
    meta = _kind(kind)
    draft = pending_draft(project, kind)
    if draft:
        return _get(kind, draft["id"])
    current = project.get(meta["pointer"])
    if not current:
        raise ConflictError(f"还没有{meta['label']}，请先导入", code="nothing_to_edit")
    if kind == "skill":
        from .services import _read_version_files
        version = persistence.get_skill_version(current)
        return persistence.create_skill_version(
            project_id, _read_version_files(current), version.get("manifest") or {},
            parent_id=current, status="draft", message="工作副本", set_current=False)
    if kind == "dataset":
        from .services import create_dataset_draft
        return create_dataset_draft(current)
    raise ConflictError("Prompt 与模型通过保存表单生成工作副本", code="use_save_environment")


def save_environment(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Save the Prompt/model form as the pending environment draft.

    Environment rows are immutable, so each save adds a draft whose parent is
    the previous pending draft (credentials are inherited along that chain).
    """
    from . import services
    project = persistence.get_project(project_id)
    draft = pending_draft(project, "environment")
    parent = draft["id"] if draft else project.get("current_environment_id")
    body = dict(payload)
    body.pop("status", None)
    body.pop("parent_id", None)
    return services.create_environment(project_id, body, status="draft",
                                       parent_id=parent, set_current=False)


def discard(project_id: str, kind: str) -> dict[str, Any]:
    """Drop unapplied edits; the current version stays as it is."""
    project = persistence.get_project(project_id)
    table = _kind(kind)["table"]
    removed = []
    while True:
        draft = pending_draft(project, kind)
        if not draft:
            break
        with transaction() as conn:
            conn.execute(f"UPDATE {table} SET status = ? WHERE id = ? AND status = 'draft'",
                         (DISCARDED, draft["id"]))
        removed.append(draft["id"])
    if removed:
        persistence.append_audit(project_id, "workspace.discard",
                                 {"kind": kind, "ids": removed})
    return summary(project_id)


def freeze(project_id: str) -> list[dict[str, Any]]:
    """Publish every changed pending draft and point the project at it.

    Unchanged working copies are discarded instead of creating duplicate
    versions. Returns what was frozen, for the run start confirmation.
    """
    from . import services
    frozen: list[dict[str, Any]] = []
    for kind, meta in KINDS.items():
        project = persistence.get_project(project_id)
        draft = pending_draft(project, kind)
        if not draft:
            continue
        if not _changed(project, kind, draft):
            discard(project_id, kind)
            continue
        try:
            if kind == "skill":
                published = services.publish_skill_version(draft["id"], set_current=True)
            elif kind == "environment":
                published = services.publish_environment(draft["id"], set_current=True)
            else:
                dataset = persistence.get_dataset(draft["id"])
                published = services.publish_dataset_version(draft["id"], {
                    "expected_hash": dataset.get("hash"),
                    "expected_current_version": project.get(meta["pointer"]),
                    "set_current": True,
                })
        except (ValidationError, ConflictError) as exc:
            raise type(exc)(f"{meta['label']}的修改无法生效：{exc.message}",
                            code=exc.code, details={"kind": kind, "errors": exc.details}) from exc
        frozen.append({"kind": kind, "label": meta["label"], "id": published["id"],
                       "version_seq": published.get("version_seq")})
    return frozen


def start(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Start a run in one call: freeze pending edits when the mode uses them."""
    from . import services
    mode = payload.get("mode") or "evaluate"
    persistence.get_project(project_id)
    key = payload.get("idempotency_key")
    if key:
        existing = persistence.find_idempotent_run(project_id, key)
        if existing:
            return {"run": existing, "frozen": []}
    # Continuing a candidate, validation and final tests compare against the
    # frozen evidence chain; they never pick up new edits.
    uses_edits = mode in ("evaluate", "baseline") or (mode == "optimize" and not payload.get("source_run_id"))
    frozen = freeze(project_id) if uses_edits else []
    body = {key: value for key, value in payload.items()
            if key in ("mode", "source_run_id", "idempotency_key", "reuse_baseline",
                       "skill_version_id")}
    body["project_id"] = project_id
    run = services.create_run(body)
    return {"run": run, "frozen": frozen}
