"""Run event emission with stage progress bookkeeping."""

from __future__ import annotations

import threading
from typing import Any, Mapping

from . import config, event_types, persistence
from .model_output import response_json
from .runtime_logging import log_run_event


class EventEmitter:
    """Persist events for one run and keep ``runs.stage_progress`` in sync.

    A single writer per run is guaranteed by the worker lease; this class also
    serialises appends from case threads inside one process.
    """

    def __init__(self, run_id: str, stage: str | None = None):
        self.run_id = run_id
        self.stage = stage
        self._lock = threading.Lock()
        self._progress: dict[str, Any] = {
            "stage": stage or "preflight", "completed": 0, "total": 0,
            "current_case_ids": [], "message": "", "started_at": persistence.now_iso(),
            "last_activity_at": persistence.now_iso(),
        }

    # -- stage/progress ---------------------------------------------------
    def set_stage(self, stage: str, *, message: str = "", total: int = 0,
                  completed: int = 0) -> None:
        self.stage = stage
        with self._lock:
            self._progress.update({
                "stage": stage, "message": message, "total": total,
                "completed": completed, "current_case_ids": [],
                "started_at": persistence.now_iso(),
                "last_activity_at": persistence.now_iso(),
            })
            progress = dict(self._progress)
        persistence.update_run(self.run_id, stage=stage, stage_progress=progress)

    def progress(self, *, completed: int | None = None, total: int | None = None,
                 current_case_ids: list[str] | None = None,
                 message: str | None = None) -> None:
        with self._lock:
            if completed is not None:
                self._progress["completed"] = completed
            if total is not None:
                self._progress["total"] = total
            if current_case_ids is not None:
                self._progress["current_case_ids"] = list(current_case_ids)
            if message is not None:
                self._progress["message"] = message
            self._progress["last_activity_at"] = persistence.now_iso()
            progress = dict(self._progress)
        persistence.update_run(self.run_id, stage_progress=progress)

    def case_started(self, case_id: str) -> None:
        with self._lock:
            ids = list(self._progress.get("current_case_ids") or [])
            if case_id not in ids:
                ids.append(case_id)
            self._progress["current_case_ids"] = ids[-8:]
            progress = dict(self._progress)
        persistence.update_run(self.run_id, stage_progress=progress)

    def case_finished(self, case_id: str) -> None:
        with self._lock:
            ids = [item for item in
                   (self._progress.get("current_case_ids") or []) if item != case_id]
            self._progress["current_case_ids"] = ids
            self._progress["completed"] = int(self._progress.get("completed") or 0) + 1
            self._progress["last_activity_at"] = persistence.now_iso()
            progress = dict(self._progress)
        persistence.update_run(self.run_id, stage_progress=progress)

    # -- events -----------------------------------------------------------
    def emit(self, event_type: str, payload: Mapping[str, Any] | None = None, *,
             level: str = "info", stage: str | None = None,
             case_id: str | None = None, split: str | None = None,
             variant: str | None = None, repeat_index: int | None = None,
             attempt: int | None = None, call_id: str | None = None,
             **_extra: Any) -> dict[str, Any]:
        actual_stage = stage or self.stage
        safe_payload = dict(payload or {})
        if event_type in (event_types.MODEL_CALL_FINISHED,
                          event_types.OPTIMIZER_CALL_FINISHED) and "response" in safe_payload:
            try:
                encoded = response_json(safe_payload["response"])
                if len(encoded) > 8192 and self.run_id:
                    artifact = persistence.save_artifact(
                        self.run_id, "model_response", encoded,
                        summary=str(safe_payload.get("response_summary") or ""))
                    safe_payload.pop("response", None)
                    safe_payload["artifacts"] = list(safe_payload.get("artifacts") or []) + [{
                        "id": artifact["id"], "kind": "model_response",
                        "size": artifact.get("size", len(encoded)),
                        "summary": artifact.get("summary", ""),
                    }]
            except Exception:
                response = safe_payload.get("response")
                preview = response.get("content", "") if isinstance(response, Mapping) else ""
                safe_payload["response"] = {"content": str(preview)[:500]}
                safe_payload["response_note"] = "详细模型输出保存失败，已保留安全预览"
        if level not in event_types.LEVELS:
            level = "info"
        with self._lock:
            event = persistence.append_event(
                self.run_id, event_type, stage=actual_stage, case_id=case_id,
                split=split, variant=variant, repeat_index=repeat_index,
                attempt=attempt, call_id=call_id, level=level,
                payload=safe_payload)
        log_run_event(event)
        return event

    def issue(self, event_type: str, *, error_code: str, title: str, message: str,
              impact: str = "", next_action: str = "", level: str = "error",
              cause_event_seq: int | None = None,
              technical_detail: dict | None = None, stage: str | None = None,
              case_id: str | None = None, split: str | None = None,
              variant: str | None = None, repeat_index: int | None = None,
              attempt: int | None = None, call_id: str | None = None,
              extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = event_types.issue(
            error_code, title, message, impact=impact, next_action=next_action,
            cause_event_seq=cause_event_seq, technical_detail=technical_detail)
        if extra:
            payload.update(dict(extra))
        return self.emit(event_type, payload, level=level, stage=stage,
                         case_id=case_id, split=split, variant=variant,
                         repeat_index=repeat_index, attempt=attempt,
                         call_id=call_id)


class NullEmitter:
    """Emitter used by unit tests of pure stages."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: Mapping[str, Any] | None = None, **_kw: Any) -> dict:
        event = {"type": event_type, "payload": dict(payload or {}), **{
            key: value for key, value in _kw.items() if value is not None}}
        self.events.append(event)
        return event

    def issue(self, event_type: str, **kwargs: Any) -> dict:
        return self.emit(event_type, kwargs, level=kwargs.get("level", "error"))
