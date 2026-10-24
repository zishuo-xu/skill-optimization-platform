"""Internal observation tools: ``select_skill`` / ``read_skill`` / ``finish`` (§32).

These tools are Agent-runtime observation tooling, not business tools: they never
count towards the tool-argument metric (§12). They record explicit route events
(reading a reference file alone must not be counted as selecting a sub-skill) and
they are the only evidence used for routing scores.

Safety rules enforced here:

* ``read_skill`` only reads files mapped by the package manifest and never
  escapes ``skill_snapshot_path`` (path traversal / absolute paths rejected);
* ``finish`` must agree with the selections actually observed as events,
  otherwise the case fails with ``protocol_error``;
* the protocol mode (native tool calls vs fixed text protocol) is frozen when the
  runtime is created and cannot change mid case.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import event_types
from .adapters.base import emit_event, json_dumps

__all__ = [
    "INTERNAL_TOOL_NAMES", "ObsToolError", "ObsToolRuntime",
    "internal_tool_definitions",
]

INTERNAL_TOOL_NAMES = ("select_skill", "read_skill", "finish")

ACTION_VALUES = ("tool_call", "clarify", "no_action")

_ABS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class ObsToolError(Exception):
    """Protocol failure raised by an internal tool (mapped to ``protocol_error``)."""

    def __init__(self, message: str, *, code: str = "protocol_error",
                 details: Any = None):
        super().__init__(message)
        self.code = code if code in event_types.CASE_ERROR_CODES else "protocol_error"
        self.message = message
        self.details = details


def _schema(name: str, description: str, parameters: Mapping[str, Any],
            required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": dict(parameters),
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def internal_tool_definitions(*, need_finish: bool = True) -> list[dict[str, Any]]:
    """OpenAI style function definitions for the internal tools."""
    tools = [
        _schema(
            "select_skill",
            "选择要使用的子技能。传入 manifest 中的稳定 skill_id；可多次调用以选择多个子技能。",
            {"skill_id": {"type": "string", "description": "子技能稳定 ID"}},
            ["skill_id"],
        ),
        _schema(
            "read_skill",
            "读取已映射的 Skill 包文件内容（例如子技能对应的 references 文件）。",
            {"path": {"type": "string", "description": "包内相对路径，如 references/task_query.md"}},
            ["path"],
        ),
    ]
    if need_finish:
        tools.append(_schema(
            "finish",
            "结束本案例并报告 action、已选择的子技能、仍缺少的字段和简短摘要。",
            {
                "action": {"type": "string", "enum": list(ACTION_VALUES),
                           "description": "tool_call（已调用或应调用业务工具）/ clarify / no_action"},
                "selected_skills": {"type": "array", "items": {"type": "string"},
                                    "description": "与事件一致的子技能 ID 列表"},
                "missing_fields": {"type": "array", "items": {"type": "string"},
                                   "description": "clarify 时仍缺少的字段"},
                "summary": {"type": "string", "description": "简短业务摘要"},
            },
            ["action", "selected_skills"],
        ))
    return tools


def _normalize_relpath(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObsToolError("文件路径不能为空", details={"path": value})
    raw = value.strip()
    if raw.startswith(("/", "\\")) or _ABS_DRIVE_RE.match(raw):
        raise ObsToolError("拒绝读取绝对路径", details={"path": raw})
    normalized = posixpath.normpath(raw.replace("\\", "/"))
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise ObsToolError("拒绝读取包外路径（路径穿越）", details={"path": raw})
    return normalized


class ObsToolRuntime:
    """Per-case internal tool state machine.

    Parameters
    ----------
    skill_snapshot_path:
        Absolute path to the immutable package snapshot directory for this case.
    manifest:
        Parsed ``manifest.json`` (``main`` + ``subskills[].skill_id/path``).
    allowed_skill_ids:
        Optional restriction (e.g. routing test subset); unknown ids are rejected.
    emit:
        Event sink with the platform ``emit(event)`` contract.
    protocol_mode:
        ``"native"`` (model calls tools) or ``"text"`` (fixed parser supplies
        selections). Frozen for the case and recorded in capability/report data.
    aliases:
        Frozen alias map (alias -> skill_id) applied to text-protocol names.
    """

    def __init__(self, *, skill_snapshot_path: str | Path,
                 manifest: Mapping[str, Any] | None = None,
                 allowed_skill_ids: Iterable[str] | None = None,
                 emit: Callable[..., Any] | None = None,
                 protocol_mode: str = "native",
                 aliases: Mapping[str, str] | None = None,
                 max_read_chars: int = 400_000):
        if protocol_mode not in ("native", "text"):
            raise ValueError(f"未知的协议模式: {protocol_mode}")
        self.root = Path(skill_snapshot_path).resolve()
        self.manifest: dict[str, Any] = dict(manifest or {})
        self.emit = emit
        self.protocol_mode = protocol_mode
        self.aliases = {str(k): str(v) for k, v in (aliases or {}).items()}
        self.max_read_chars = max_read_chars
        self.selected_skills: list[str] = []
        self.route_events: list[dict[str, Any]] = []
        self.read_files: list[str] = []
        self.finished: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self._known_ids: list[str] = []
        self._skill_paths: dict[str, str] = {}
        self._mapped_files: list[str] = []
        self._parse_manifest(allowed_skill_ids)

    # -- manifest mapping -------------------------------------------------- #
    def _parse_manifest(self, allowed_skill_ids: Iterable[str] | None) -> None:
        allowed = set(str(item) for item in allowed_skill_ids) \
            if allowed_skill_ids is not None else None
        main = self.manifest.get("main") or "SKILL.md"
        try:
            main_path = posixpath.normpath(str(main).replace("\\", "/"))
        except (TypeError, ValueError):
            main_path = "SKILL.md"
        if main_path and main_path != ".":
            self._mapped_files.append(main_path)
        for entry in self.manifest.get("subskills") or []:
            if not isinstance(entry, Mapping):
                continue
            skill_id = str(entry.get("skill_id") or "").strip()
            path = entry.get("path")
            if not skill_id or not isinstance(path, str) or not path.strip():
                continue
            try:
                normalized = _normalize_relpath(path)
            except ObsToolError:
                continue
            if allowed is not None and skill_id not in allowed:
                continue
            self._known_ids.append(skill_id)
            self._skill_paths[skill_id] = normalized
            if normalized not in self._mapped_files:
                self._mapped_files.append(normalized)

    # -- introspection ----------------------------------------------------- #
    @property
    def known_skill_ids(self) -> list[str]:
        return list(self._known_ids)

    @property
    def mapped_files(self) -> list[str]:
        return list(self._mapped_files)

    def tool_definitions(self) -> list[dict[str, Any]]:
        if self.protocol_mode != "native":
            return []
        return internal_tool_definitions()

    def resolve_skill_alias(self, name: str) -> str:
        return self.aliases.get(name, name)

    def is_finished(self) -> bool:
        return self.finished is not None

    # -- dispatch ---------------------------------------------------------- #
    def handle(self, name: Any, arguments: Any, *,
               call_id: str | None = None) -> dict[str, Any]:
        if not isinstance(name, str) or name not in INTERNAL_TOOL_NAMES:
            raise ObsToolError(f"未知的内部工具: {name!r}")
        if self.finished is not None and name != "finish":
            raise ObsToolError("案例已结束（finish 之后不应再调用工具）")
        if not isinstance(arguments, Mapping):
            raise ObsToolError(f"{name} 的参数必须是 JSON 对象",
                               details={"tool": name})
        if name == "select_skill":
            return self.select_skill(arguments, call_id=call_id)
        if name == "read_skill":
            return self.read_skill(arguments, call_id=call_id)
        return self.finish(arguments, call_id=call_id)

    # -- select_skill ------------------------------------------------------ #
    def select_skill(self, arguments: Mapping[str, Any], *,
                     call_id: str | None = None) -> dict[str, Any]:
        skill_id = arguments.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ObsToolError("select_skill 缺少合法的 skill_id")
        skill_id = skill_id.strip()
        self._validate_skill_id(skill_id)
        self._record_selection(skill_id, source="native_tool",
                               basis=arguments.get("basis"), call_id=call_id)
        return {
            "ok": True,
            "tool": "select_skill",
            "skill_id": skill_id,
            "path": self._skill_paths.get(skill_id),
        }

    def record_text_selection(self, skill_id: str, *, basis: str | None = None,
                              call_id: str | None = None) -> dict[str, Any]:
        """Record a routing selection produced by the frozen text parser."""
        if self.finished is not None:
            raise ObsToolError("案例已结束，不能继续记录子技能选择")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ObsToolError("文本协议解析出的子技能名称为空")
        skill_id = skill_id.strip()
        self._validate_skill_id(skill_id)
        self._record_selection(skill_id, source="text_parse", basis=basis,
                               call_id=call_id)
        return {"ok": True, "skill_id": skill_id, "path": self._skill_paths.get(skill_id)}

    def _validate_skill_id(self, skill_id: str) -> None:
        if skill_id not in self._skill_paths:
            raise ObsToolError(
                f"未知的子技能 ID: {skill_id}",
                details={"skill_id": skill_id,
                         "allowed": list(self._skill_paths)})

    def _record_selection(self, skill_id: str, *, source: str,
                          basis: Any, call_id: str | None) -> None:
        if skill_id not in self.selected_skills:
            self.selected_skills.append(skill_id)
        payload: dict[str, Any] = {
            "skill_id": skill_id,
            "skill_path": self._skill_paths.get(skill_id),
            "source": source,
            "protocol_mode": self.protocol_mode,
        }
        if isinstance(basis, str) and basis:
            payload["basis"] = basis[:500]
        event = emit_event(self.emit, event_types.SKILL_SELECTED,
                           payload=payload, call_id=call_id)
        self.events.append(event)
        self.route_events.append({
            "skill_id": skill_id,
            "seq": event.get("seq"),
            "call_id": call_id,
        })

    # -- read_skill -------------------------------------------------------- #
    def read_skill(self, arguments: Mapping[str, Any], *,
                   call_id: str | None = None) -> dict[str, Any]:
        relpath = _normalize_relpath(arguments.get("path"))
        if relpath not in self._mapped_files:
            raise ObsToolError(
                "read_skill 只能读取 manifest 已映射的包内文件",
                details={"path": relpath, "allowed": list(self._mapped_files)})
        target = (self.root / relpath).resolve()
        if self.root not in target.parents and target != self.root:
            raise ObsToolError("拒绝读取包外路径（路径穿越）", details={"path": relpath})
        if not target.is_file():
            raise ObsToolError("映射的包内文件不存在", details={"path": relpath})
        size = target.stat().st_size
        if size > self.max_read_chars:
            raise ObsToolError("Skill 文件过大，超过单次读取上限",
                               details={"path": relpath, "bytes": size,
                                        "limit": self.max_read_chars})
        data = target.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ObsToolError("Skill 文件不是 UTF-8 文本", details={"path": relpath}) from exc
        payload = {
            "path": relpath,
            "chars": len(text),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        event = emit_event(self.emit, event_types.SKILL_READ, payload=payload,
                           call_id=call_id)
        self.events.append(event)
        self.read_files.append(relpath)
        return {
            "ok": True,
            "tool": "read_skill",
            "path": relpath,
            "content": text,
            "chars": len(text),
        }

    # -- finish ------------------------------------------------------------ #
    def finish(self, arguments: Mapping[str, Any], *,
               call_id: str | None = None) -> dict[str, Any]:
        if self.finished is not None:
            raise ObsToolError("finish 只能调用一次")
        action = arguments.get("action")
        if action not in ACTION_VALUES:
            raise ObsToolError("finish.action 非法（应为 tool_call/clarify/no_action）",
                               details={"action": action})
        raw_selected = arguments.get("selected_skills")
        if raw_selected is None:
            raw_selected = []
        if not isinstance(raw_selected, (list, tuple)):
            raise ObsToolError("finish.selected_skills 必须是数组")
        selected: list[str] = []
        for item in raw_selected:
            if not isinstance(item, str) or not item.strip():
                raise ObsToolError("finish.selected_skills 只能包含非空字符串")
            name = self.resolve_skill_alias(item.strip())
            if name not in selected:
                selected.append(name)
        observed = set(self.selected_skills)
        declared = set(selected)
        if observed != declared:
            missing = sorted(observed - declared)
            extra = sorted(declared - observed)
            raise ObsToolError(
                "finish 的选择摘要与运行事件不一致（协议错误）",
                details={"missing_in_finish": missing,
                         "not_selected_by_event": extra})
        raw_missing = arguments.get("missing_fields")
        if raw_missing is None:
            raw_missing = []
        if not isinstance(raw_missing, (list, tuple)):
            raise ObsToolError("finish.missing_fields 必须是数组")
        missing_fields = [str(item) for item in raw_missing]
        summary = arguments.get("summary")
        if summary is not None and not isinstance(summary, str):
            summary = json_dumps(summary)
        self.finished = {
            "action": action,
            "selected_skills": list(selected),
            "missing_fields": missing_fields,
            "summary": summary or "",
            "call_id": call_id,
        }
        return {"ok": True, "tool": "finish", "action": action}

    # -- result ------------------------------------------------------------ #
    def result(self) -> dict[str, Any]:
        selected = list(self.finished["selected_skills"]) if self.finished \
            else list(self.selected_skills)
        return {
            "selected_skills": selected,
            "route_events": list(self.route_events),
            "action": self.finished["action"] if self.finished else "no_action",
            "missing_fields": list(self.finished["missing_fields"]) if self.finished else [],
            "finished": self.finished is not None,
            "summary": self.finished["summary"] if self.finished else "",
        }
