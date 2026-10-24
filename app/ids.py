"""Identifier helpers with readable, stable prefixes."""

from __future__ import annotations

import hashlib
import re
import uuid

_PREFIXES = {
    "project": "prj",
    "skill_version": "sv",
    "environment": "env",
    "dataset": "ds",
    "run": "run",
    "candidate": "cand",
    "decision": "dec",
    "artifact": "art",
    "event": "evt",
    "draft": "drf",
}


def new_id(kind: str) -> str:
    prefix = _PREFIXES.get(kind, kind)
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def stable_case_id(query: str, group_id: str | None = None) -> str:
    """Deterministic id for a case that does not supply one."""
    basis = (query or "").strip() + "\x00" + (group_id or "")
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
    return f"case_{digest}"


def slugify(value: str, default: str = "item") -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "").strip("-").lower()
    return value or default
