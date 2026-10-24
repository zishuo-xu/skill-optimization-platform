"""Patch application and static checks for Skill-package candidates.

Pure logic only: these helpers never touch the database, the network or the
filesystem.  A candidate is a list of "replace" edits on *existing* Markdown
files (contract §2.7 / §19):

* ``apply_edits`` applies edits atomically and validates the Markdown whitelist,
  unique ``search`` hits and the no-add / no-delete rule.  On any failure the
  untouched base mapping is returned together with the issue list.
* ``static_check`` verifies the patched package (path whitelist, manifest
  references, Markdown structure, protected content, encoding / size) and is
  what the ``static_check`` pipeline stage reports.
* ``diff_files`` produces unified diffs (difflib) for the UI "文件差异" tab.

Issue shape: ``{"code", "path", "message"}`` (+ ``edit_index`` for edits), matching
the import/package error style used elsewhere (contract §2.1).
"""

from __future__ import annotations

import difflib
import posixpath
import re
from typing import Any, Iterable, Mapping, Sequence

# Generous but bounded limits: Skill documents are small text files.
MAX_MARKDOWN_BYTES = 512 * 1024
MAX_CHANGED_BYTES = 2 * 1024 * 1024
MAX_FILE_COUNT = 512

_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_CASE_ID_RE = re.compile(r"\bcase_[A-Za-z0-9_]*\d[A-Za-z0-9_]*\b")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_FENCE_RE = re.compile(r"^\s*```", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\]\((?!https?:|#|mailto:)([^)\s]+?\.md)(?:#[^)]*)?\)")
_REF_BACKTICK_RE = re.compile(r"`((?:references|skills?)/[A-Za-z0-9_./-]+\.md)`")
_ANSWER_TOKEN_RE = re.compile(r"(expected|标准答案|答案表|expected_answer)", re.IGNORECASE)
_ANSWER_FIELD_RE = re.compile(r"(tool_calls|routes|missing_fields|call_match)", re.IGNORECASE)
_SCRIPT_SUFFIXES = (
    ".json", ".py", ".sh", ".js", ".ts", ".yaml", ".yml", ".toml", ".ini",
    ".csv", ".txt", ".xml", ".html", ".zip", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".svg", ".bin", ".exe", ".dll", ".so", ".pkl", ".db", ".lock",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def normalize_path(path: Any) -> str | None:
    """Normalize a package-relative path or return ``None`` when unsafe."""
    if not isinstance(path, str):
        return None
    candidate = path.strip().replace("\\", "/")
    if not candidate or candidate.startswith("/") or _ABSOLUTE_RE.match(candidate):
        return None
    candidate = posixpath.normpath(candidate)
    if candidate in ("", ".", ".."):
        return None
    if candidate.startswith("../") or "/../" in f"/{candidate}/":
        return None
    if candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate


def _decode(data: Any) -> str | None:
    if isinstance(data, str):
        return data
    if isinstance(data, (bytes, bytearray)):
        try:
            return bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _issue(code: str, path: str | None, message: str, *,
           edit_index: int | None = None) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "path": path, "message": message}
    if edit_index is not None:
        issue["edit_index"] = edit_index
    return issue


def _short(text: str, limit: int = 120) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


def _normalize_allowed(allowed_paths: Iterable[str] | None) -> set[str] | None:
    if allowed_paths is None:
        return None
    allowed: set[str] = set()
    for raw in allowed_paths:
        norm = normalize_path(raw)
        if norm is not None:
            allowed.add(norm)
    return allowed


def _normalized_files(files: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Map normalized path -> data, plus normalized path -> original key."""
    data_by_norm: dict[str, Any] = {}
    original: dict[str, str] = {}
    for key, value in (files or {}).items():
        norm = normalize_path(key)
        if norm is None or norm in data_by_norm:
            continue
        data_by_norm[norm] = value
        original[norm] = key
    return data_by_norm, original


# --------------------------------------------------------------------------- #
# applying edits
# --------------------------------------------------------------------------- #
def apply_edits(files: Mapping[str, bytes], edits: Sequence[Mapping[str, Any]] | None,
                allowed_paths: Iterable[str] | None) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    """Apply ``replace`` edits to existing Markdown files.

    Returns ``(new_files, issues)``.  Application is atomic: when at least one
    edit is invalid the base files are returned unchanged (as a copy) and every
    problem is reported in ``issues`` so the run log can explain the rejection.
    """
    base = dict(files or {})
    base_norm, original = _normalized_files(base)
    allowed = _normalize_allowed(allowed_paths)
    issues: list[dict[str, Any]] = []

    if edits is None:
        issues.append(_issue("invalid_edits", None, "edits 必须是列表"))
        return dict(base), issues
    if isinstance(edits, (str, bytes, Mapping)) or not isinstance(edits, Sequence):
        issues.append(_issue("invalid_edits", None, "edits 必须是列表"))
        return dict(base), issues

    texts: dict[str, str] = {}
    changed: set[str] = set()

    for index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            issues.append(_issue("invalid_edit", None, "edit 必须是对象", edit_index=index))
            continue
        raw_path = edit.get("path")
        path = normalize_path(raw_path)
        op = edit.get("op", "replace")
        if not isinstance(op, str):
            issues.append(_issue("invalid_edit", None if path is None else path,
                                 "edit.op 必须是字符串", edit_index=index))
            continue
        op = op.strip().lower()
        if op in {"add", "create", "delete", "remove", "rename", "move"}:
            issues.append(_issue("file_add_delete_not_allowed",
                                 path or (raw_path if isinstance(raw_path, str) else None),
                                 f"不允许新增、删除或重命名文件（op={op}）", edit_index=index))
            continue
        if op != "replace":
            issues.append(_issue("unsupported_edit_op", path,
                                 f"不支持的 edit.op: {op}（仅支持 replace）", edit_index=index))
            continue
        if path is None:
            issues.append(_issue("invalid_edit_path", None,
                                 "edit.path 必须是包内相对路径，且不能包含 ..", edit_index=index))
            continue
        if path not in base_norm:
            issues.append(_issue("edit_file_missing", path,
                                 "目标文件不存在：只允许修改已存在的文件", edit_index=index))
            continue
        if allowed is not None and path not in allowed:
            issues.append(_issue("edit_path_not_allowed", path,
                                 "目标文件不在允许修改的路径白名单内", edit_index=index))
            continue
        if not path.lower().endswith(".md"):
            issues.append(_issue("edit_not_markdown", path,
                                 "只允许修改已存在的 Markdown（.md）文件", edit_index=index))
            continue

        search = edit.get("search")
        replace = edit.get("replace")
        if not isinstance(search, str) or search == "":
            issues.append(_issue("invalid_edit", path,
                                 "edit.search 必须是非空字符串", edit_index=index))
            continue
        if not isinstance(replace, str):
            issues.append(_issue("invalid_edit", path,
                                 "edit.replace 必须是字符串", edit_index=index))
            continue
        if replace == search:
            continue  # no-op edit: nothing to change

        text = texts.get(path)
        if text is None:
            text = _decode(base_norm[path])
            if text is None:
                issues.append(_issue("encoding_error", path,
                                     "目标文件不是合法的 UTF-8 文本", edit_index=index))
                continue
            texts[path] = text
        count = text.count(search)
        if count == 0:
            issues.append(_issue("edit_not_found", path,
                                 f"search 未命中（片段：{_short(search)}）", edit_index=index))
            continue
        if count > 1:
            issues.append(_issue("edit_not_unique", path,
                                 f"search 命中 {count} 次，必须唯一（片段：{_short(search)}）",
                                 edit_index=index))
            continue
        texts[path] = text.replace(search, replace, 1)
        changed.add(path)

    if issues:
        return dict(base), issues

    new_files: dict[str, bytes] = {}
    for norm, data in base_norm.items():
        key = original[norm]
        if norm in changed:
            new_files[key] = texts[norm].encode("utf-8")
        else:
            new_files[key] = data
    # Preserve any base key that could not be normalized (defensive; the
    # package import layer rejects such paths before a candidate is built).
    for key, data in base.items():
        if key not in new_files:
            new_files[key] = data
    return new_files, []


def diff_files(base_files: Mapping[str, Any],
               patched_files: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return per-file unified diffs between two file mappings (UI diff tab)."""
    base = dict(base_files or {})
    patched = dict(patched_files or {})
    changes: list[dict[str, Any]] = []
    for path in sorted(set(base) | set(patched)):
        before_data = base.get(path)
        after_data = patched.get(path)
        if before_data is not None and after_data is not None and before_data == after_data:
            continue
        status = ("added" if before_data is None
                  else "removed" if after_data is None else "modified")
        before = _decode(before_data) if before_data is not None else ""
        after = _decode(after_data) if after_data is not None else ""
        if before is None or after is None:
            diff = ""
        else:
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
            ))
        changes.append({
            "path": path,
            "status": status,
            "additions": sum(1 for line in diff.splitlines()
                             if line.startswith("+") and not line.startswith("+++")),
            "deletions": sum(1 for line in diff.splitlines()
                             if line.startswith("-") and not line.startswith("---")),
            "diff": diff,
        })
    return changes


# --------------------------------------------------------------------------- #
# static checks
# --------------------------------------------------------------------------- #
def static_check(base_files: Mapping[str, Any], patched_files: Mapping[str, Any],
                 manifest: Mapping[str, Any] | None,
                 case_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Run the frozen static checks on a candidate package.

    Returns ``{"ok": bool, "checks": [{"name", "ok", "message"}], "errors": [...]}``.
    ``errors`` uses the same ``{"code", "path", "message"}`` shape as the package
    importer so the run log and the issues endpoint can render it directly.
    """
    base = dict(base_files or {})
    patched = dict(patched_files or {})
    base_norm, base_original = _normalized_files(base)
    patched_norm, patched_original = _normalized_files(patched)
    manifest = manifest if isinstance(manifest, Mapping) else {}

    checks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    def add_check(name: str, check_errors: list[dict[str, Any]],
                  ok_message: str, fail_message: str) -> None:
        checks.append({
            "name": name,
            "ok": not check_errors,
            "message": ok_message if not check_errors else fail_message,
        })
        errors.extend(check_errors)

    added = sorted(set(patched_norm) - set(base_norm))
    removed = sorted(set(base_norm) - set(patched_norm))
    changed = sorted(p for p in patched_norm
                     if p in base_norm and base_norm[p] != patched_norm[p])

    # 1. file set: candidates may never add or remove files -------------------
    file_errors: list[dict[str, Any]] = []
    for path in added:
        file_errors.append(_issue("file_added", path, "候选不得新增文件"))
    for path in removed:
        file_errors.append(_issue("file_removed", path, "候选不得删除文件"))
    if len(patched_norm) > MAX_FILE_COUNT:
        file_errors.append(_issue("too_many_files", None,
                                  f"文件数量 {len(patched_norm)} 超过上限 {MAX_FILE_COUNT}"))
    add_check("file_set_unchanged", file_errors,
              f"文件集合未变化（{len(patched_norm)} 个文件）", "文件集合发生变化")

    # 2. path whitelist: only existing Markdown files may be modified ---------
    whitelist_errors: list[dict[str, Any]] = []
    for path in changed:
        if not path.lower().endswith(".md"):
            whitelist_errors.append(_issue("path_not_markdown", path,
                                           "只允许修改 Markdown（.md）文件"))
            continue
        if path == "manifest.json":
            whitelist_errors.append(_issue("manifest_modified", path,
                                           "manifest.json 由平台管理，候选不得修改"))
            continue
        if path.lower().endswith(_SCRIPT_SUFFIXES):
            whitelist_errors.append(_issue("protected_file_modified", path,
                                           "脚本与资源文件不得修改"))
    if "manifest.json" in base_norm and "manifest.json" in patched_norm:
        if base_norm["manifest.json"] != patched_norm["manifest.json"]:
            if not any(e["code"] == "manifest_modified" for e in whitelist_errors):
                whitelist_errors.append(_issue("manifest_modified", "manifest.json",
                                               "manifest.json 由平台管理，候选不得修改"))
    add_check("path_whitelist", whitelist_errors,
              "仅修改了白名单内的已有 Markdown 文件", "存在白名单外的文件变更")

    # 3. manifest references stay complete -----------------------------------
    manifest_errors: list[dict[str, Any]] = []
    if not manifest:
        manifest_errors.append(_issue("bad_manifest", "manifest.json",
                                      "缺少 manifest 或格式不是 JSON 对象"))
    else:
        main = normalize_path(manifest.get("main") or "SKILL.md")
        if main is None or main not in patched_norm:
            manifest_errors.append(_issue("missing_main", main or "SKILL.md",
                                          "manifest.main 指向的主 Skill 文件不存在"))
        subskills = manifest.get("subskills")
        if subskills is None:
            subskills = []
        if not isinstance(subskills, list):
            manifest_errors.append(_issue("bad_manifest", "manifest.json",
                                          "manifest.subskills 必须是数组"))
        else:
            seen_ids: set[str] = set()
            seen_paths: set[str] = set()
            for index, item in enumerate(subskills):
                if not isinstance(item, Mapping):
                    manifest_errors.append(_issue("bad_manifest", "manifest.json",
                                                  f"subskills[{index}] 必须是对象"))
                    continue
                skill_id = item.get("skill_id")
                path = normalize_path(item.get("path"))
                if not isinstance(skill_id, str) or not skill_id.strip():
                    manifest_errors.append(_issue("bad_manifest", "manifest.json",
                                                  f"subskills[{index}].skill_id 缺失"))
                elif skill_id in seen_ids:
                    manifest_errors.append(_issue("duplicate_skill_id", "manifest.json",
                                                  f"重复的 skill_id: {skill_id}"))
                else:
                    seen_ids.add(skill_id)
                if path is None:
                    manifest_errors.append(_issue("missing_reference", "manifest.json",
                                                  f"subskills[{index}].path 非法或缺失"))
                elif path not in patched_norm:
                    manifest_errors.append(_issue("missing_reference", path,
                                                  f"manifest 引用的子技能文件不存在: {path}"))
                elif path in seen_paths:
                    manifest_errors.append(_issue("duplicate_path", path,
                                                  f"manifest 中重复引用路径: {path}"))
                else:
                    seen_paths.add(path)
    # Light reference scan on changed Markdown documents (links / backticks).
    for path in changed:
        if not path.lower().endswith(".md"):
            continue
        text = _decode(patched_norm.get(path))
        if text is None:
            continue
        directory = posixpath.dirname(path)
        targets: list[str] = []
        for match in _MD_LINK_RE.finditer(text):
            link = match.group(1)
            if link.startswith("/") or _ABSOLUTE_RE.match(link):
                continue
            targets.append(posixpath.normpath(posixpath.join(directory, link)))
        for match in _REF_BACKTICK_RE.finditer(text):
            link = match.group(1)
            targets.append(posixpath.normpath(posixpath.join(directory, link)))
        for target in targets:
            if normalize_path(target) is None:
                continue
            if target not in patched_norm:
                manifest_errors.append(_issue("dangling_reference", path,
                                              f"引用目标不存在: {target}"))
    add_check("manifest_integrity", manifest_errors,
              "manifest 引用完整，无重复 ID / 路径", "manifest 引用或结构不完整")

    # 4. Markdown structure --------------------------------------------------
    structure_errors: list[dict[str, Any]] = []
    for path in changed:
        if not path.lower().endswith(".md"):
            continue
        text = _decode(patched_norm.get(path))
        if text is None:
            continue  # reported by encoding check
        if not text.strip():
            structure_errors.append(_issue("empty_file", path, "Markdown 文件不能为空"))
            continue
        if "\x00" in text:
            structure_errors.append(_issue("markdown_structure", path,
                                           "Markdown 文件包含 NUL 字符"))
        if not _HEADING_RE.search(text):
            structure_errors.append(_issue("markdown_structure", path,
                                           "Markdown 文件缺少标题（# 标题）"))
        if len(_FENCE_RE.findall(text)) % 2 != 0:
            structure_errors.append(_issue("markdown_structure", path,
                                           "Markdown 代码围栏（```）不配对"))
    main_path = normalize_path(manifest.get("main") or "SKILL.md")
    if main_path and main_path in patched_norm:
        main_text = _decode(patched_norm[main_path])
        if main_text is not None and not main_text.strip():
            structure_errors.append(_issue("empty_file", main_path, "主 Skill 文件不能为空"))
    add_check("markdown_structure", structure_errors,
              "Markdown 结构有效", "Markdown 结构异常")

    # 5. protected content: no test ids / expected answers hardcoded ---------
    protected_errors: list[dict[str, Any]] = []
    forbidden_ids = [str(item) for item in (case_ids or []) if str(item)]
    for path in changed:
        if not path.lower().endswith(".md"):
            continue
        text = _decode(patched_norm.get(path))
        if text is None:
            continue
        for case_id in forbidden_ids:
            if case_id and case_id in text:
                protected_errors.append(_issue(
                    "test_case_id_written", path,
                    f"候选写入了测试 case_id: {case_id}"))
        for match in _CASE_ID_RE.finditer(text):
            protected_errors.append(_issue(
                "test_case_id_written", path,
                f"候选写入了疑似 case_id: {match.group(0)}"))
        lines = text.splitlines()
        for lineno, line in enumerate(lines):
            token = _ANSWER_TOKEN_RE.search(line)
            if token is None:
                continue
            window = "\n".join(lines[max(0, lineno - 1): lineno + 2])
            marker = token.group(1)
            if marker in ("标准答案", "答案表", "expected_answer") \
                    or _ANSWER_FIELD_RE.search(window):
                protected_errors.append(_issue(
                    "expected_answer_written", path,
                    f"疑似写入标准答案硬编码（第 {lineno + 1} 行）"))
    add_check("protected_content", protected_errors,
              "未写入测试 case_id 或标准答案", "候选包含禁止写入的测试内容")

    # 6. encoding / size -----------------------------------------------------
    encoding_errors: list[dict[str, Any]] = []
    changed_total = 0
    for path in changed:
        data = patched_norm.get(path)
        if not isinstance(data, (bytes, bytearray)):
            if isinstance(data, str):
                data = data.encode("utf-8")
        size = len(data) if isinstance(data, (bytes, bytearray)) else 0
        changed_total += size
        if path.lower().endswith(".md"):
            if _decode(data) is None:
                encoding_errors.append(_issue("encoding_error", path,
                                              "文件不是合法的 UTF-8 文本"))
            if size > MAX_MARKDOWN_BYTES:
                encoding_errors.append(_issue("too_large", path,
                                              f"文件大小 {size} 超过上限 {MAX_MARKDOWN_BYTES} 字节"))
    if changed_total > MAX_CHANGED_BYTES:
        encoding_errors.append(_issue("too_large", None,
                                      f"变更总大小 {changed_total} 超过上限 {MAX_CHANGED_BYTES} 字节"))
    add_check("encoding_and_size", encoding_errors,
              "编码与大小符合限制", "编码或大小不符合限制")

    return {
        "ok": not errors and all(check["ok"] for check in checks),
        "checks": checks,
        "errors": errors,
    }


__all__ = [
    "MAX_MARKDOWN_BYTES",
    "MAX_CHANGED_BYTES",
    "MAX_FILE_COUNT",
    "apply_edits",
    "diff_files",
    "normalize_path",
    "static_check",
]
