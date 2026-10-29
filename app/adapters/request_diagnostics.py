"""Bounded, content-free transport observations for optimizer run records."""
from __future__ import annotations

import time
from collections.abc import Mapping


class RequestDiagnostics:
    PROGRESS_INTERVAL_S = 10.0

    def __init__(self, observer, attempt: int, max_attempts: int):
        self.observer = observer
        self.started = time.monotonic()
        self.last_progress = self.started
        self.stats = {"attempt": attempt, "max_attempts": max_attempts,
                      "response_status": None, "response_ms": None,
                      "first_byte_ms": None, "first_content_ms": None,
                      "first_tool_ms": None, "tool_argument_chars": 0, "tool_delta_count": 0,
                      "received_bytes": 0, "chunk_count": 0, "sse_event_count": 0,
                      "content_chars": 0, "reasoning_chars": 0, "done_seen": False}

    def elapsed_ms(self):
        return round((time.monotonic() - self.started) * 1000)

    def emit(self, phase, **extra):
        if callable(self.observer):
            # Diagnostics must never change retry, scoring or request behavior.
            try:
                self.observer({**self.stats, "phase": phase,
                               "elapsed_ms": self.elapsed_ms(), **extra})
            except Exception:
                pass

    def response(self, status):
        self.stats.update(response_status=int(status), response_ms=self.elapsed_ms())
        self.emit("response_received")

    def completed(self, result):
        usage = result.get("usage") or {}
        self.stats["content_chars"] = len(result.get("content") or "")
        self.emit("attempt_completed", input_tokens=usage.get("input_tokens"),
                  output_tokens=usage.get("output_tokens"))

    def received(self, chunk):
        self.stats["received_bytes"] += len(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
        self.stats["chunk_count"] += 1
        if chunk and self.stats["first_byte_ms"] is None:
            self.stats["first_byte_ms"] = self.elapsed_ms()
            self.emit("first_byte")

    def parsed(self, events):
        self.stats["sse_event_count"] += len(events)
        for event in events:
            self.stats["done_seen"] |= bool(event.get("done"))
            payload = event.get("json")
            if not isinstance(payload, Mapping):
                continue
            choices = payload.get("choices")
            if not isinstance(choices, list):
                continue
            for choice in choices:
                delta = choice.get("delta") if isinstance(choice, Mapping) else None
                if not isinstance(delta, Mapping):
                    continue
                tool_calls = delta.get("tool_calls")
                for tool in tool_calls if isinstance(tool_calls, list) else []:
                    if not isinstance(tool, Mapping):
                        continue
                    self.stats["tool_delta_count"] += 1
                    function = tool.get("function") or {}
                    arguments = function.get("arguments") if isinstance(function, Mapping) else None
                    if isinstance(arguments, str):
                        self.stats["tool_argument_chars"] += len(arguments)
                for field, counter in (("content", "content_chars"),
                                       ("reasoning_content", "reasoning_chars")):
                    value = delta.get(field)
                    if isinstance(value, str):
                        self.stats[counter] += len(value)
        if self.stats["content_chars"] and self.stats["first_content_ms"] is None:
            self.stats["first_content_ms"] = self.elapsed_ms()
            self.emit("first_content")
        if self.stats["tool_delta_count"] and self.stats["first_tool_ms"] is None:
            self.stats["first_tool_ms"] = self.elapsed_ms()
            self.emit("first_tool")
        now = time.monotonic()
        if now - self.last_progress >= self.PROGRESS_INTERVAL_S:
            self.last_progress = now
            self.emit("stream_progress")
