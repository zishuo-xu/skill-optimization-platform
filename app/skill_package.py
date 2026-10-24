"""Skill package import, validation, archive building and file helpers."""

from __future__ import annotations

import io
import json
import re
import stat
import zipfile
from pathlib import Path
from typing import Any, Mapping

from . import hashing
from .errors import ValidationError

MAX_ENTRIES = 1000
MAX_UNCOMPRESSED_BYTES = 30 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts",
                 ".css", ".html", ".csv"}
SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

MAX_FILE_BYTES = 2 * 1024 * 1024


def _err(code: str, message: str, path: str = "") -> dict[str, Any]:
    return {"code": code, "message": message, "path": path}


def _warn(code: str, message: str, path: str = "") -> dict[str, Any]:
    return {"code": code, "message": message, "path": path}


def _safe_rel(name: str) -> str | None:
    """Return a normalised POSIX relative path or None when unsafe."""
    if not name or "\x00" in name:
        return None
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:/", name):
        return None
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return bool(mode) and stat.S_ISLNK(mode)


def _decode_text(data: bytes) -> str | None:
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


REFERENCE_ROOTS = {"reference", "references"}


def _root_main_name(files: Mapping[str, bytes]) -> str | None:
    """Return the root-level main markdown file, case-insensitive on the name."""
    for name in sorted(files):
        if "/" not in name and name.lower() == "skill.md":
            return name
    return None


def _is_reference_md(name: str) -> bool:
    if not name.endswith(".md"):
        return False
    parts = name.split("/")
    return len(parts) >= 2 and parts[0].lower() in REFERENCE_ROOTS


def _normalise_root_main(files: dict[str, bytes]) -> tuple[dict[str, bytes], bool]:
    """Rename a lowercase ``skill.md`` at the root to ``SKILL.md``."""
    if _root_main_name(files) == "SKILL.md" or _root_main_name(files) is None:
        return files, False
    renamed = dict(files)
    actual = _root_main_name(files)
    if actual is None:
        return files, False
    renamed["SKILL.md"] = renamed.pop(actual)
    return renamed, True


def _align_manifest_main(manifest: dict[str, Any],
                         files: Mapping[str, bytes]) -> dict[str, Any]:
    main = manifest.get("main") or "SKILL.md"
    if main in files:
        return manifest
    if "/" not in main:
        for name in files:
            if "/" not in name and name.lower() == str(main).lower():
                manifest = dict(manifest)
                manifest["main"] = name
                return manifest
    return manifest


def _sanitize_skill_id(raw: Any, index: int) -> str:
    value = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    if not value or not value[0].isalpha():
        value = f"skill_{index}"
    value = value[:60]
    if len(value) < 2:
        value = f"skill_{index}"
    return value


def _markdown_heading(text: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if match:
            return match.group(1)[:120]
    return ""


def _markdown_description(text: str) -> str:
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        if stripped.startswith(("#", ">", "-", "*", "|", "!")):
            continue
        if stripped.startswith("<!--"):
            continue
        return stripped[:200]
    return ""


def _read_zip(data: bytes) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValidationError("不是有效的 ZIP 文件", code="invalid_zip")
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ValidationError(f"ZIP 条目过多（上限 {MAX_ENTRIES}）", code="too_large")
    total = 0
    for info in infos:
        rel = _safe_rel(info.filename)
        if rel is None:
            errors.append(_err("path_traversal", f"非法路径: {info.filename}", info.filename))
            continue
        # Ignore OS metadata produced by Finder/Explorer, not part of the package.
        base_name = Path(rel).name
        if (rel.startswith("__MACOSX/") or base_name in (".DS_Store", "Thumbs.db")
                or base_name.startswith("._")):
            continue
        if info.is_dir():
            continue
        if _is_symlink(info):
            errors.append(_err("symlink", f"不允许符号链接: {rel}", rel))
            continue
        if info.file_size > MAX_FILE_BYTES:
            errors.append(_err("too_large", f"文件过大: {rel}", rel))
            continue
        total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValidationError("ZIP 解压后体积超过上限", code="too_large")
        if rel in files:
            errors.append(_err("duplicate_path", f"重复文件: {rel}", rel))
            continue
        files[rel] = zf.read(info)
    return files, errors


def _strip_single_root(files: dict[str, bytes]) -> tuple[dict[str, bytes], bool]:
    if _root_main_name(files):
        return files, False
    roots = {name.split("/")[0] for name in files if "/" in name}
    if len(roots) == 1:
        root = next(iter(roots))
        prefix = root + "/"
        stripped = {name[len(prefix):]: data for name, data in files.items()
                    if name.startswith(prefix)}
        if _root_main_name(stripped):
            return stripped, True
    return files, False


def generate_manifest(files: Mapping[str, bytes], main: str = "SKILL.md") -> dict[str, Any]:
    """Build a stable mapping for packages without manifest.json.

    Accepts both ``reference/`` and ``references/`` roots, markdown files at any
    depth below them, and derives readable names from the first markdown heading.
    """
    subskills: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for name in sorted(files):
        if not _is_reference_md(name):
            continue
        stem = Path(name).stem
        parent = Path(name).parent.name
        raw_id = parent if stem.lower() in ("skill", "index", "readme") else stem
        skill_id = _sanitize_skill_id(raw_id, len(subskills) + 1)
        base_id = skill_id
        suffix = 2
        while skill_id in used_ids:
            skill_id = f"{base_id}_{suffix}"
            suffix += 1
        used_ids.add(skill_id)
        text = _decode_text(files[name]) or ""
        subskills.append({
            "skill_id": skill_id,
            "name": _markdown_heading(text) or stem or skill_id,
            "path": name,
            "description": _markdown_description(text),
        })
    return {"version": 1, "main": main, "subskills": subskills}


def parse_manifest(files: Mapping[str, bytes]) -> tuple[dict[str, Any] | None,
                                                        list[dict[str, Any]],
                                                        list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    raw = files.get("manifest.json")
    if raw is None:
        main = _root_main_name(files) or "SKILL.md"
        manifest = generate_manifest(files, main)
        if manifest["subskills"]:
            warnings.append(_warn(
                "manifest_generated",
                "未提供 manifest.json，已按 reference(s)/ 下的 Markdown 文件自动生成 skill_id 映射"))
        return manifest, errors, warnings
    text = _decode_text(raw)
    if text is None:
        return None, [_err("encoding_error", "manifest.json 不是 UTF-8 编码", "manifest.json")], warnings
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [_err("bad_manifest", f"manifest.json 解析失败: {exc.msg} 第 {exc.lineno} 行",
                           "manifest.json")], warnings
    if not isinstance(manifest, dict):
        return None, [_err("bad_manifest", "manifest.json 必须是对象", "manifest.json")], warnings
    return manifest, errors, warnings


def validate_manifest(manifest: Mapping[str, Any],
                      files: Mapping[str, bytes]) -> tuple[list[dict[str, Any]],
                                                           list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    main = manifest.get("main") or "SKILL.md"
    if not isinstance(main, str) or _safe_rel(main) is None:
        errors.append(_err("bad_manifest", "manifest.main 非法", "manifest.json"))
        main = "SKILL.md"
    if main not in files:
        errors.append(_err("missing_main", f"缺少主 Skill 文件 {main}", main))
    elif not main.endswith(".md"):
        errors.append(_err("bad_manifest", "主 Skill 必须是 Markdown 文件", main))
    subskills = manifest.get("subskills")
    if not isinstance(subskills, list):
        errors.append(_err("bad_manifest", "manifest.subskills 必须是数组", "manifest.json"))
        subskills = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    referenced: set[str] = set()
    for index, item in enumerate(subskills):
        where = f"manifest.json#subskills[{index}]"
        if not isinstance(item, dict):
            errors.append(_err("bad_manifest", "子技能项必须是对象", where))
            continue
        skill_id = item.get("skill_id")
        path = item.get("path")
        if not isinstance(skill_id, str) or not SKILL_ID_RE.match(skill_id):
            errors.append(_err("bad_manifest", f"skill_id 非法: {skill_id!r}", where))
        elif skill_id in seen_ids:
            errors.append(_err("duplicate_skill_id", f"重复 skill_id: {skill_id}", where))
        else:
            seen_ids.add(skill_id)
        if not isinstance(path, str) or _safe_rel(path) is None:
            errors.append(_err("bad_manifest", f"子技能路径非法: {path!r}", where))
            continue
        if path in seen_paths:
            errors.append(_err("duplicate_path", f"重复子技能路径: {path}", where))
        seen_paths.add(path)
        referenced.add(path)
        if path not in files:
            errors.append(_err("missing_reference", f"子技能文件不存在: {path}", path))
        elif not path.endswith(".md"):
            errors.append(_err("bad_manifest", f"子技能必须是 Markdown: {path}", path))
        elif _decode_text(files[path]) is None:
            errors.append(_err("encoding_error", f"文件不是 UTF-8 编码: {path}", path))
    for name in files:
        if _is_reference_md(name) and name not in referenced:
            warnings.append(_warn("dangling_reference", f"reference(s) 文件未在 manifest 中登记: {name}", name))
    if not subskills:
        warnings.append(_warn("no_subskills", "包内没有子技能映射"))
    if len(seen_ids) != len(subskills):
        pass
    return errors, warnings


def import_package(data: bytes) -> dict[str, Any]:
    """Parse and validate a ZIP package. Returns files/manifest/hash/errors/warnings."""
    files, errors = _read_zip(data)
    if errors:
        return {"files": {}, "manifest": None, "errors": errors, "warnings": [],
                "content_hash": "", "file_hashes": []}
    files, stripped = _strip_single_root(files)
    files, renamed_main = _normalise_root_main(files)
    warnings: list[dict[str, Any]] = []
    if stripped:
        warnings.append(_warn("root_folder_stripped", "已移除 ZIP 外层目录，按包根目录处理"))
    if renamed_main:
        warnings.append(_warn("main_file_normalized", "已把根目录 skill.md 规范化为 SKILL.md"))
    manifest, manifest_errors, manifest_warnings = parse_manifest(files)
    errors.extend(manifest_errors)
    warnings.extend(manifest_warnings)
    if manifest is not None:
        manifest = _align_manifest_main(dict(manifest), files)
        more_errors, more_warnings = validate_manifest(manifest, files)
        errors.extend(more_errors)
        warnings.extend(more_warnings)
    # Encoding check for text files we may render or edit.
    for name, payload in files.items():
        suffix = Path(name).suffix.lower()
        if suffix in TEXT_SUFFIXES and _decode_text(payload) is None:
            errors.append(_err("encoding_error", f"文件不是 UTF-8 编码: {name}", name))
        if name == "manifest.json":
            continue
        if Path(name).suffix.lower() not in TEXT_SUFFIXES and _decode_text(payload) is not None:
            continue
    return {
        "files": files,
        "manifest": manifest,
        "errors": errors,
        "warnings": warnings,
        "content_hash": hashing.hash_files(files) if not errors else "",
        "file_hashes": hashing.file_hashes(files),
    }


def import_json_files(files_payload: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Import from the browser folder-upload JSON representation."""
    files: dict[str, bytes] = {}
    errors: list[dict[str, Any]] = []
    for entry in files_payload:
        path = entry.get("path")
        rel = _safe_rel(path) if isinstance(path, str) else None
        if rel is None:
            errors.append(_err("path_traversal", f"非法路径: {path!r}"))
            continue
        content = entry.get("content")
        if content is None:
            errors.append(_err("bad_manifest", f"文件缺少 content: {rel}", rel))
            continue
        if isinstance(content, str):
            payload = content.encode("utf-8")
        elif isinstance(content, bytes):
            payload = content
        else:
            errors.append(_err("bad_manifest", f"文件 content 类型非法: {rel}", rel))
            continue
        if len(payload) > MAX_FILE_BYTES:
            errors.append(_err("too_large", f"文件过大: {rel}", rel))
            continue
        files[rel] = payload
    if errors:
        return {"files": {}, "manifest": None, "errors": errors, "warnings": [],
                "content_hash": "", "file_hashes": []}
    files, stripped = _strip_single_root(files)
    files, renamed_main = _normalise_root_main(files)
    warnings: list[dict[str, Any]] = []
    if stripped:
        warnings.append(_warn("root_folder_stripped", "已移除上传目录的外层目录，按包根目录处理"))
    if renamed_main:
        warnings.append(_warn("main_file_normalized", "已把根目录 skill.md 规范化为 SKILL.md"))
    manifest, manifest_errors, manifest_warnings = parse_manifest(files)
    errors.extend(manifest_errors)
    warnings.extend(manifest_warnings)
    if manifest is not None:
        manifest = _align_manifest_main(dict(manifest), files)
        more_errors, more_warnings = validate_manifest(manifest, files)
        errors.extend(more_errors)
        warnings.extend(more_warnings)
    return {
        "files": files,
        "manifest": manifest,
        "errors": errors,
        "warnings": warnings,
        "content_hash": hashing.hash_files(files) if not errors else "",
        "file_hashes": hashing.file_hashes(files),
    }


def build_zip(files: Mapping[str, bytes], manifest: Mapping[str, Any] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            zf.writestr(name, files[name])
        if manifest is not None:
            zf.writestr("manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def is_editable(path: str) -> bool:
    return path.lower().endswith((".md", ".markdown"))


def package_summary(files: Mapping[str, bytes], manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "file_count": len(files),
        "main": (manifest or {}).get("main", "SKILL.md"),
        "subskill_count": len((manifest or {}).get("subskills") or []),
        "size": sum(len(data) for data in files.values()),
    }
