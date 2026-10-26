"""SSE-style stream event assembler (pure, unit-testable).

Enterprise gateways transport OpenAI compatible Chat Completions deltas over
WebSocket: every frame is a chunk of an SSE-like text stream. Frames may split
an event in half, carry several events, use ``\\r\\n`` or ``\\n``, interleave
heartbeats/comments and may send JSON objects directly. This module restores the
SSE event boundaries and aggregates ``choices[].delta`` payloads into a complete
result (``content`` + ``tool_calls`` + ``finish_reason`` + tail ``usage``).

Both the real adapters and the acceptance tests use exactly this implementation
so a streamed result and a one-shot full result aggregate identically (A12).
"""

from __future__ import annotations

import codecs
import json
import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "SSEEventAssembler", "SSEParseError", "aggregate", "extract_stream_error",
    "normalize_usage", "iter_event_payloads",
]

#: Values that terminate a gateway stream ("[DONE]" or an equivalent marker).
DEFAULT_DONE_MARKERS = ("[DONE]", "[done]")

_NO_LINE = object()

_JSON_OBJECT_RE = re.compile(r"^\s*[\[{]")


class SSEParseError(ValueError):
    """Raised for unrecoverable stream decoding problems (mapped to format_error)."""


def normalize_usage(usage: Any) -> dict[str, int] | None:
    """Normalise OpenAI/gateway usage blocks; never invent missing values."""
    if not isinstance(usage, Mapping) or not usage:
        return None

    def pick(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(value)
        return None

    input_tokens = pick("input_tokens", "prompt_tokens")
    output_tokens = pick("output_tokens", "completion_tokens")
    total_tokens = pick("total_tokens")
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    result: dict[str, int] = {}
    if input_tokens is not None:
        result["input_tokens"] = input_tokens
    if output_tokens is not None:
        result["output_tokens"] = output_tokens
    if total_tokens is not None:
        result["total_tokens"] = total_tokens
    else:
        result["total_tokens"] = (input_tokens or 0) + (output_tokens or 0)
    return result


class SSEEventAssembler:
    """Incrementally reassemble SSE events from arbitrary text/byte frames.

    ``feed`` accepts ``str`` / ``bytes`` / already-decoded ``dict`` / ``list``
    payloads and returns the complete events available so far. ``finish`` flushes
    a trailing unterminated event when a connection closes cleanly.

    An event is a dict::

        {"event": str|None, "id": str|None, "data": str,
         "json": dict|list|None, "done": bool}
    """

    def __init__(self, *, done_markers: Sequence[str] | None = None,
                 max_buffer_chars: int = 8_000_000):
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buffer = ""
        self._data_lines: list[str] = []
        self._event_name: str | None = None
        self._event_id: str | None = None
        self._done_markers = tuple(done_markers or DEFAULT_DONE_MARKERS)
        self._max_buffer_chars = max_buffer_chars
        self._decoded_chars = 0

    # -- public API -------------------------------------------------------- #
    def feed(self, frame: bytes | str | Mapping[str, Any] | Sequence[Any] | None) -> list[dict[str, Any]]:
        if frame is None:
            return []
        if isinstance(frame, Mapping):
            return self._feed_direct(frame)
        if isinstance(frame, (list, tuple)):
            return self._feed_direct(list(frame))
        if isinstance(frame, memoryview):
            frame = bytes(frame)
        if isinstance(frame, bytes):
            text = self._decoder.decode(frame)
        elif isinstance(frame, str):
            text = frame
        else:
            raise SSEParseError(f"不支持的流帧类型: {type(frame).__name__}")
        return self._feed_text(text)

    def finish(self) -> list[dict[str, Any]]:
        """Flush buffered data at a clean end-of-stream."""
        # The incremental decoder may still hold a partial multi-byte sequence;
        # producing a replacement char here lets JSON parsing fail visibly instead
        # of silently dropping a frame.
        tail = self._decoder.decode(b"", final=False)
        events: list[dict[str, Any]] = []
        if tail:
            events.extend(self._feed_text(tail))
        if self._buffer:
            leftover = self._buffer
            self._buffer = ""
            # A trailing \r without a following \n terminates the final line.
            if leftover.endswith("\r"):
                leftover = leftover[:-1]
            event = self._process_line(leftover)
            if event is not None:
                events.append(event)
        pending = self._dispatch()
        if pending is not None:
            events.append(pending)
        return events

    @property
    def pending_chars(self) -> int:
        return len(self._buffer) + sum(len(part) + 1 for part in self._data_lines)

    # -- frame handling ---------------------------------------------------- #
    def _feed_direct(self, frame: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
        # Some gateways send an SSE envelope object: {"event": ..., "data": "..."}
        if isinstance(frame, Mapping):
            envelope_keys = set(frame.keys())
            if envelope_keys <= {"event", "data", "id", "retry", "type"} \
                    and isinstance(frame.get("data"), str):
                if not self._data_lines and not self._buffer:
                    return [self._make_event(
                        event_name=frame.get("event") or frame.get("type"),
                        event_id=frame.get("id"),
                        data=frame["data"],
                    )]
        return [self._make_event(event_name=None, event_id=None,
                                 data=json.dumps(frame, ensure_ascii=False),
                                 payload=frame)]

    def _feed_text(self, text: str) -> list[dict[str, Any]]:
        if not text:
            return []
        self._decoded_chars += len(text)
        events: list[dict[str, Any]] = []
        # Fast path: a frame that is exactly one complete JSON payload (the common
        # "one JSON object per WebSocket message" gateway mode) when no SSE event
        # is being assembled.
        if not self._buffer and not self._data_lines:
            stripped = text.strip()
            if stripped and _JSON_OBJECT_RE.match(stripped):
                try:
                    payload = json.loads(stripped)
                except (TypeError, ValueError):
                    pass
                else:
                    events.append(self._make_event(
                        event_name=None, event_id=None, data=stripped, payload=payload))
                    return events

        self._buffer += text
        while True:
            line = self._next_line()
            if line is _NO_LINE:
                break
            event = self._process_line(line)
            if event is not None:
                events.append(event)
        # Only data still waiting for an event boundary is bounded. Complete
        # frames are released immediately, so a long stream of large frames
        # (e.g. gateways echoing the request in telemetry on every frame) is fine.
        if self.pending_chars > self._max_buffer_chars:
            raise SSEParseError("流缓冲区超过上限")
        return events

    def _next_line(self) -> Any:
        buffer = self._buffer
        idx_n = buffer.find("\n")
        idx_r = buffer.find("\r")
        if idx_n == -1 and idx_r == -1:
            return _NO_LINE
        if idx_r == -1 or (idx_n != -1 and idx_n < idx_r):
            line = buffer[:idx_n]
            self._buffer = buffer[idx_n + 1:]
            return line
        # \r first: it may be the first half of a \r\n split across frames.
        if idx_r + 1 >= len(buffer):
            return _NO_LINE
        line = buffer[:idx_r]
        self._buffer = buffer[idx_r + 2:] if buffer[idx_r + 1] == "\n" else buffer[idx_r + 1:]
        return line

    def _process_line(self, line: str) -> dict[str, Any] | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None  # comment / heartbeat
        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""
        if field == "data":
            self._data_lines.append(value)
            return None
        if field == "event":
            self._event_name = value
            return None
        if field == "id":
            self._event_id = value
            return None
        if field == "retry":
            return None
        # Bare JSON line (one JSON object per line inside a frame).
        if _JSON_OBJECT_RE.match(line):
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                return None
            return self._make_event(event_name=None, event_id=None,
                                    data=line, payload=payload)
        return None

    def _dispatch(self) -> dict[str, Any] | None:
        if not self._data_lines and self._event_name is None:
            return None
        data = "".join(part + "\n" for part in self._data_lines)
        if data.endswith("\n"):
            data = data[:-1]
        event_name = self._event_name
        event_id = self._event_id
        self._data_lines = []
        self._event_name = None
        self._event_id = None
        if data == "" and event_name is None:
            return None
        return self._make_event(event_name=event_name, event_id=event_id, data=data)

    def _make_event(self, *, event_name: str | None, event_id: str | None,
                    data: str, payload: Any = _NO_LINE) -> dict[str, Any]:
        if payload is _NO_LINE:
            payload = _parse_data_payload(data)
        done = _is_done(data, event_name, payload, self._done_markers)
        return {
            "event": event_name,
            "id": event_id,
            "data": data,
            "json": payload,
            "done": done,
        }


def _parse_data_payload(data: str) -> Any:
    text = data.strip()
    if not text or text.upper() in ("[DONE]",):
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    # SSE "data:" may be split over several lines; when every line is itself a
    # complete JSON document treat the event as a batch of payloads.
    parts = [line.strip() for line in data.split("\n") if line.strip()]
    if len(parts) > 1:
        parsed: list[Any] = []
        for part in parts:
            try:
                parsed.append(json.loads(part))
            except (TypeError, ValueError):
                return None
        return parsed
    return None


def _is_done(data: str, event_name: str | None, payload: Any,
             markers: Sequence[str]) -> bool:
    stripped = (data or "").strip()
    if stripped in markers:
        return True
    if stripped.upper() == "[DONE]":
        return True
    if event_name and event_name.lower() in ("done", "end", "finish"):
        return True
    if isinstance(payload, Mapping):
        if payload.get("done") is True or payload.get("finished") is True:
            return True
        if isinstance(payload.get("type"), str) and payload["type"].lower() in (
                "message_stop", "done", "end"):
            return True
    return False


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def iter_event_payloads(events: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    """Yield JSON payload objects from stream events (lists are flattened)."""
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("json")
        if isinstance(payload, Mapping):
            yield payload
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, Mapping):
                    yield item


#: Top-level ``code`` values gateways use for success / acknowledgement frames.
_SUCCESS_CODES = (None, 0, "0", 200, "200")


def extract_stream_error(events: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the provider error object if the stream carried one."""
    for payload in iter_event_payloads(events):
        if payload.get("choices"):
            continue
        error = payload.get("error")
        if isinstance(error, Mapping):
            return error
        if isinstance(error, str) and error:
            return {"message": error}
        if payload.get("code") not in _SUCCESS_CODES and payload.get("message"):
            return {"code": payload.get("code"), "message": payload.get("message")}
    return None


def _choice_state(choices: dict[int, dict[str, Any]], index: int) -> dict[str, Any]:
    return choices.setdefault(index, {
        "content": [],
        "tool_calls": {},
        "finish_reason": None,
    })


def _append_fragment(state: dict[str, Any], item: Mapping[str, Any],
                     fallback_index: int) -> None:
    raw_index = item.get("index", fallback_index)
    try:
        tool_index = int(raw_index)
    except (TypeError, ValueError):
        tool_index = fallback_index
    slot = state["tool_calls"].setdefault(
        tool_index, {"id": "", "name": "", "arguments": ""})
    call_id = item.get("id")
    if isinstance(call_id, str) and call_id and not slot["id"]:
        slot["id"] = call_id
    function = item.get("function")
    if isinstance(function, Mapping):
        name = function.get("name")
        if isinstance(name, str) and name:
            slot["name"] += name
        arguments = function.get("arguments")
    else:
        name = item.get("name")
        if isinstance(name, str) and name:
            slot["name"] += name
        arguments = item.get("arguments")
    if isinstance(arguments, str):
        slot["arguments"] += arguments
    elif isinstance(arguments, Mapping):
        slot["arguments"] = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    elif arguments is not None:
        slot["arguments"] += json.dumps(arguments, ensure_ascii=False)


def _merge_tool_calls(state: dict[str, Any], items: Any) -> None:
    if not isinstance(items, (list, tuple)):
        return
    for position, item in enumerate(items):
        if isinstance(item, Mapping):
            _append_fragment(state, item, position)


def aggregate(events: Iterable[Mapping[str, Any]], *,
              choice_index: int | None = None) -> dict[str, Any]:
    """Aggregate stream events into ``content`` / ``tool_calls`` / usage / done.

    Empty deltas, heartbeats and unknown payloads are ignored. ``tool_calls``
    arguments are concatenated per ``(choice index, tool index)`` and parsed as a
    JSON object; malformed argument fragments are reported in ``argument_errors``
    instead of being guessed.
    """
    choices: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] | None = None
    done = False

    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("done"):
            done = True
        payload = event.get("json")
        payloads: list[Any] = payload if isinstance(payload, list) else [payload]
        for item in payloads:
            if not isinstance(item, Mapping):
                continue
            if item.get("choices"):
                pass
            elif item.get("usage"):
                usage = normalize_usage(item.get("usage")) or usage
                continue
            else:
                continue
            item_usage = normalize_usage(item.get("usage"))
            if item_usage is not None:
                usage = item_usage
            for raw_choice in item.get("choices") or []:
                if not isinstance(raw_choice, Mapping):
                    continue
                raw_index = raw_choice.get("index", 0)
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    index = 0
                state = _choice_state(choices, index)
                delta = raw_choice.get("delta")
                message = raw_choice.get("message")
                if isinstance(delta, Mapping):
                    _merge_content(state, delta)
                    _merge_tool_calls(state, delta.get("tool_calls"))
                    _merge_legacy_function_call(state, delta)
                if isinstance(message, Mapping):
                    _merge_content(state, message)
                    _merge_tool_calls(state, message.get("tool_calls"))
                    _merge_legacy_function_call(state, message)
                finish_reason = raw_choice.get("finish_reason")
                if isinstance(finish_reason, str) and finish_reason:
                    state["finish_reason"] = finish_reason

    primary_index: int | None
    if choice_index is not None and choice_index in choices:
        primary_index = choice_index
    elif 0 in choices:
        primary_index = 0
    elif choices:
        primary_index = min(choices)
    else:
        primary_index = None

    content = ""
    tool_calls: list[dict[str, Any]] = []
    argument_errors: list[dict[str, Any]] = []
    finish_reason = None
    if primary_index is not None:
        state = choices[primary_index]
        content = "".join(state["content"])
        finish_reason = state["finish_reason"]
        for tool_index in sorted(state["tool_calls"]):
            slot = state["tool_calls"][tool_index]
            raw_arguments = slot["arguments"]
            parsed: dict[str, Any] = {}
            error = None
            if raw_arguments.strip():
                try:
                    value = json.loads(raw_arguments)
                except (TypeError, ValueError) as exc:
                    error = f"工具参数不是合法 JSON: {exc}"
                else:
                    if isinstance(value, Mapping):
                        parsed = dict(value)
                    else:
                        error = "工具参数必须是 JSON 对象"
            if not slot["name"]:
                error = error or "工具调用缺少 function.name"
            entry = {
                "id": slot["id"] or f"call_{tool_index}",
                "name": slot["name"],
                "arguments": parsed,
            }
            tool_calls.append(entry)
            if error:
                argument_errors.append({
                    "index": tool_index,
                    "raw": raw_arguments,
                    "error": error,
                })

    if finish_reason is None:
        for index in sorted(choices):
            if choices[index]["finish_reason"]:
                finish_reason = choices[index]["finish_reason"]
                break

    return {
        "content": content,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        "usage": usage,
        "done": done,
        "argument_errors": argument_errors,
    }


def _merge_content(state: dict[str, Any], payload: Mapping[str, Any]) -> None:
    content = payload.get("content")
    if isinstance(content, str) and content:
        state["content"].append(content)
    elif isinstance(content, list):
        # Some gateways emit OpenAI "content parts"; join text parts only.
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                state["content"].append(part["text"])


def _merge_legacy_function_call(state: dict[str, Any], payload: Mapping[str, Any]) -> None:
    function_call = payload.get("function_call")
    if isinstance(function_call, Mapping):
        _append_fragment(state, {
            "index": 0,
            "id": payload.get("id"),
            "function": function_call,
        }, 0)
