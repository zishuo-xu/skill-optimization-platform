"""Canonical hashing helpers used for content-addressed versions and caches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_files(files: Mapping[str, bytes]) -> str:
    """Hash a mapping of relative path -> bytes (paths sorted, lengths delimited)."""
    digest = hashlib.sha256()
    for rel in sorted(files):
        data = files[rel]
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\x00")
        digest.update(data)
        digest.update(b"\x00")
    return digest.hexdigest()


def file_hashes(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    entries = []
    for rel in sorted(files):
        data = files[rel]
        entries.append({"path": rel, "sha256": sha256_bytes(data), "size": len(data)})
    return entries


def hash_directory(root: Path, ignore: Iterable[str] = ()) -> str:
    ignored = set(ignore)
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if rel in ignored:
                continue
            files[rel] = path.read_bytes()
    return hash_files(files)


def readiness_fingerprint(parts: dict[str, Any]) -> str:
    """Fingerprint used for baseline cache reuse checks."""
    return hash_json(parts)
