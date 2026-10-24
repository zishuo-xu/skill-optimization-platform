"""Unified model adapter interface and shared helpers.

Every model transport (demo scripted, OpenAI compatible HTTP, enterprise
WebSocket gateway, user supplied Python flow) implements :class:`ModelAdapter`
so ``app.execution.run_case`` can drive the same Agent loop independently of the
transport.

Capabilities are part of the frozen execution environment (§37/§41):
``routing`` / ``tool_arguments`` / ``native_tools`` / ``usage`` / ``streaming``.
A text-only adapter must declare ``tool_arguments=False`` instead of pretending
to produce native tool calls; the evaluator then reports "未评估" for the tool
argument metric instead of awarding a fake perfect score.

Event contract (``emit``)
-------------------------
The ``emit`` callable accepted by :meth:`ModelAdapter.invoke` and
``app.execution.run_case`` takes **one mapping** whose keys match the parameters
of ``persistence.append_event`` (minus ``seq``, which the pipeline allocates)::

    {"run_id", "ts", "stage", "case_id", "split", "variant", "repeat_index",
     "attempt", "call_id", "type", "level", "payload"}

:func:`call_emit` also tolerates keyword-style callables (``def sink(type, ...,
**kwargs)``) so wiring into the persistence layer stays trivial.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from .. import event_types
from ..errors import AppError

logger = logging.getLogger(__name__)

#: Canonical capability keys, frozen for the first version.
CAPABILITY_KEYS = ("routing", "tool_arguments", "native_tools", "usage", "streaming")

DEFAULT_CAPABILITIES: dict[str, bool] = {
    "routing": True,
    "tool_arguments": False,
    "native_tools": False,
    "usage": False,
    "streaming": False,
}

#: Event envelope fields shared with ``persistence.append_event``.
EVENT_FIELDS = (
    "run_id",
    "ts",
    "stage",
    "case_id",
    "split",
    "variant",
    "repeat_index",
    "attempt",
    "call_id",
    "type",
    "level",
    "payload",
)

#: Error codes that are worth retrying (§34: network timeout / rate limit only).
RETRYABLE_ERROR_CODES = frozenset({"timeout", "rate_limited", "network_error"})

#: Default retry budget beyond the first attempt (total attempts = 3).
MAX_RETRIES = 2

_SECRET_KEY_RE = re.compile(r"(api[_-]?key|authorization|token|secret|password|cookie)", re.I)


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    """UTC ISO-8601 timestamp with milliseconds, matching persistence.now_iso."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_dumps(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys, separators=(",", ":"),
                      default=str)


def normalize_capabilities(value: Any | None,
                           *, base: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """Validate/normalise a capability declaration.

    Only booleans are accepted; unknown keys are ignored so future capability
    additions cannot break older call sites. Missing keys fall back to the
    provided base (``DEFAULT_CAPABILITIES`` by default).
    """
    resolved = dict(base if base is not None else DEFAULT_CAPABILITIES)
    if isinstance(value, Mapping):
        for key in CAPABILITY_KEYS:
            if key in value:
                flag = value[key]
                if isinstance(flag, bool):
                    resolved[key] = flag
                elif flag in (0, 1):
                    resolved[key] = bool(flag)
                else:
                    raise AppError(f"capabilities.{key} 必须是布尔值",
                                   code="config_error", status_code=422,
                                   field_path=f"capabilities.{key}")
    return resolved


def default_capabilities(**overrides: bool) -> dict[str, bool]:
    caps = dict(DEFAULT_CAPABILITIES)
    caps.update({k: bool(v) for k, v in overrides.items() if k in CAPABILITY_KEYS})
    return caps


def normalize_usage(usage: Any) -> dict[str, int] | None:
    """Normalise provider usage blocks to ``{input_tokens, output_tokens,...}``.

    Accepts both OpenAI style (``prompt_tokens`` / ``completion_tokens``) and the
    already-normalised platform style. Returns ``None`` when nothing usable is
    present (usage must not be invented, §40).
    """
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
    elif input_tokens is not None or output_tokens is not None:
        result["total_tokens"] = (input_tokens or 0) + (output_tokens or 0)
    return result


def sum_usage(usages: Iterable[Mapping[str, int] | None]) -> dict[str, int] | None:
    total: dict[str, int] = {}
    seen = False
    for usage in usages:
        if not isinstance(usage, Mapping):
            continue
        seen = True
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            total[key] = total.get(key, 0) + int(value)
    return total if seen and total else None


# --------------------------------------------------------------------------- #
# tool definitions
# --------------------------------------------------------------------------- #
def normalize_tool_definitions(tools: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalise tool schema entries to OpenAI ``function`` tool objects.

    Accepts either already-wrapped ``{"type": "function", "function": {...}}``
    entries or flat ``{"name", "description", "parameters"|"input_schema"}``.
    """
    result: list[dict[str, Any]] = []
    for entry in tools or []:
        if not isinstance(entry, Mapping):
            continue
        if isinstance(entry.get("function"), Mapping):
            fn = dict(entry["function"])
        else:
            fn = {
                "name": entry.get("name"),
                "description": entry.get("description", ""),
                "parameters": entry.get("parameters") or entry.get("input_schema")
                or {"type": "object", "properties": {}},
            }
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        if not isinstance(fn.get("parameters"), Mapping):
            fn["parameters"] = {"type": "object", "properties": {}}
        result.append({"type": "function", "function": fn})
    return result


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Parse provider ``function.arguments`` into a dict.

    Raises :class:`AdapterError` with ``format_error`` when the provider emitted
    arguments that are not a JSON object.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise AdapterError(f"模型返回的工具参数不是合法 JSON: {exc}",
                               code="format_error", details={"raw": _truncate(raw)}) from exc
        if not isinstance(value, Mapping):
            raise AdapterError("模型返回的工具参数必须是 JSON 对象",
                               code="format_error", details={"raw": _truncate(raw)})
        return dict(value)
    raise AdapterError("模型返回的工具参数类型不受支持", code="format_error",
                       details={"type": type(raw).__name__})


def _truncate(value: Any, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json_dumps(value)
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------- #
# secrets / redaction
# --------------------------------------------------------------------------- #
def resolve_secret_ref(ref: Any, *, environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve a credential reference without ever logging the value.

    Supported forms:

    * ``None`` / ``""`` -> ``None``
    * ``"ENV_NAME"``    -> value of that environment variable (if set)
    * ``"env:ENV_NAME"`` -> explicit environment reference
    * ``{"env": "ENV_NAME"}`` -> environment reference
    * ``{"value": "..."}`` -> literal (discouraged, kept for local demos)
    * other literal strings are returned unchanged (e.g. gateway "AccessService")
    """
    env = environ if environ is not None else os.environ
    if ref is None or ref == "":
        return None
    if isinstance(ref, Mapping):
        if ref.get("env"):
            return env.get(str(ref["env"]))
        if "value" in ref:
            value = ref.get("value")
            return str(value) if value is not None else None
        return None
    if isinstance(ref, str):
        if ref.startswith("env:"):
            return env.get(ref[4:])
        return env.get(ref, ref)
    return str(ref)


def redact(value: Any, *, depth: int = 0) -> Any:
    """Return a deep-copied structure with credential-looking values masked."""
    if depth > 8:
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                result[key] = "***"
            else:
                result[key] = redact(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item, depth=depth + 1) for item in value]
    return value


def mask_headers(headers: Mapping[str, Any] | None) -> dict[str, Any]:
    if not headers:
        return {}
    masked = dict(headers)
    for key in list(masked):
        if _SECRET_KEY_RE.search(str(key)):
            masked[key] = "***"
    return masked


def safe_log_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Config summary that is safe to log (no credentials, no prompt bodies)."""
    if not isinstance(config, Mapping):
        return {}
    keep = ("provider", "model", "base_url", "ws_url", "url", "stream", "timeout_s",
            "connect_timeout_s", "first_packet_timeout_s", "idle_timeout_s",
            "total_timeout_s", "max_retries", "name")
    summary = {key: config.get(key) for key in keep if key in config}
    if "headers" in config:
        summary["headers"] = mask_headers(config.get("headers") or {})
    return summary


# --------------------------------------------------------------------------- #
# adapter errors
# --------------------------------------------------------------------------- #
class AdapterError(AppError):
    """Transport/format failure mapped to a case ``error`` entry.

    ``code`` must be one of ``event_types.CASE_ERROR_CODES`` (``auth_failed``,
    ``timeout``, ``rate_limited``, ``network_error``, ``platform_error``,
    ``stream_interrupted``, ``format_error``, ``truncated``, ``refused``,
    ``cancelled`` ...). ``error_kind`` is derived from that registry so callers
    never have to decide whether a failure is a capability or infrastructure
    error.
    """

    status_code = 502

    def __init__(self, message: str, *, code: str = "platform_error",
                 details: Any = None, retryable: bool | None = None,
                 retry_after: float | None = None, attempt: int | None = None):
        if code not in event_types.CASE_ERROR_CODES:
            code = "platform_error"
        super().__init__(message, code=code, details=details)
        self.error_kind: str = event_types.CASE_ERROR_CODES[code]
        self.retryable = (code in RETRYABLE_ERROR_CODES) if retryable is None else retryable
        self.retry_after = retry_after
        self.attempt = attempt

    def as_result_error(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = _truncate(self.details, 2000) if not isinstance(
                self.details, (dict, list)) else redact(self.details)
        return payload


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
def make_event(event_type: str, *, level: str = "info",
               payload: Mapping[str, Any] | None = None,
               run_id: str | None = None, stage: str | None = None,
               case_id: str | None = None, split: str | None = None,
               variant: str | None = None, repeat_index: int | None = None,
               attempt: int | None = None, call_id: str | None = None,
               ts: str | None = None) -> dict[str, Any]:
    """Build an event envelope (same fields as ``persistence.append_event``)."""
    if level not in event_types.LEVELS:
        level = "info"
    return {
        "run_id": run_id,
        "ts": ts or now_iso(),
        "stage": stage,
        "case_id": case_id,
        "split": split,
        "variant": variant,
        "repeat_index": repeat_index,
        "attempt": attempt,
        "call_id": call_id,
        "type": event_type,
        "level": level,
        "payload": dict(payload or {}),
    }


def _emit_style(callback: Callable[..., Any]) -> str:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return "mapping"
    params = list(signature.parameters.values())
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params):
        return "kwargs"
    if any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in params):
        return "mapping"
    positional = [param for param in params
                  if param.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                    inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    return "mapping" if positional else "kwargs"


def call_emit(emit: Callable[..., Any] | None, event: Mapping[str, Any]) -> Any:
    """Call an event sink, tolerating mapping-style and keyword-style sinks.

    The documented convention is ``emit(event: dict)``. Keyword-style sinks
    (``def emit(event_type, payload=None, **fields)``) are supported too, both
    for the ``type`` -> ``event_type`` parameter spelling and for filtering to a
    fixed parameter list. Returns whatever the sink returned; a sink that
    persists events may return the event enriched with ``seq`` and that
    enrichment is propagated to ``run_case`` results.
    """
    if emit is None:
        return None
    if _emit_style(emit) == "kwargs":
        values = dict(event)
        try:
            parameters = inspect.signature(emit).parameters
        except (TypeError, ValueError):
            parameters = {}
        if parameters and "type" not in parameters and "event_type" in parameters:
            values["event_type"] = values.pop("type", None)
        if parameters and not any(
                param.kind is inspect.Parameter.VAR_KEYWORD
                for param in parameters.values()):
            values = {key: value for key, value in values.items() if key in parameters}
        return emit(**values)
    return emit(dict(event))


def emit_event(emit: Callable[..., Any] | None, event_type: str, *,
               level: str = "info", payload: Mapping[str, Any] | None = None,
               **fields: Any) -> dict[str, Any]:
    """Build + emit one event. Returns the event (enriched by the sink if any)."""
    event = make_event(event_type, level=level, payload=payload, **fields)
    try:
        result = call_emit(emit, event)
    except Exception:  # noqa: BLE001 - event sinks must not break a model call
        logger.exception("event sink failed for %s", event_type)
        return event
    if isinstance(result, Mapping):
        enriched = dict(event)
        for key in EVENT_FIELDS:
            if key in result and result[key] is not None:
                enriched[key] = result[key]
        return enriched
    return event


def collect_events(emit: Callable[..., Any] | None) -> tuple[Callable[[Mapping[str, Any]], dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(sink, events)`` where ``sink`` records events and forwards.

    Used by ``execution.run_case`` when it needs to both return the emitted
    events and forward them to the pipeline sink.
    """
    events: list[dict[str, Any]] = []

    def sink(event: Mapping[str, Any]) -> dict[str, Any]:
        stored = dict(event)
        result = call_emit(emit, stored)
        if isinstance(result, Mapping):
            for key in EVENT_FIELDS:
                if key in result and result[key] is not None:
                    stored[key] = result[key]
        events.append(stored)
        return stored

    return sink, events


# --------------------------------------------------------------------------- #
# adapter interface
# --------------------------------------------------------------------------- #
class ModelAdapter:
    """Unified model transport interface (contract §2.6)."""

    name: str = "base"
    #: Flow-level adapters expose the whole case runner instead of one call.
    is_flow: bool = False
    capabilities: dict[str, bool] = dict(DEFAULT_CAPABILITIES)

    def __init__(self, model_config: Mapping[str, Any] | None = None):
        self.model_config: dict[str, Any] = dict(model_config or {})
        declared = self.model_config.get("capabilities")
        if declared is not None:
            self.capabilities = normalize_capabilities(declared)
        else:
            self.capabilities = normalize_capabilities(
                getattr(self, "capabilities", None), base=DEFAULT_CAPABILITIES)

    # -- capabilities ------------------------------------------------------ #
    @property
    def model_name(self) -> str:
        value = self.model_config.get("model") or self.model_config.get("model_name")
        return str(value) if value else ""

    def capability_snapshot(self) -> dict[str, bool]:
        return normalize_capabilities(self.capabilities)

    # -- invocation -------------------------------------------------------- #
    def invoke(self, messages: list[dict[str, Any]], config: dict[str, Any],
               tools: list[dict[str, Any]], *,
               emit: Callable[..., Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError(f"{type(self).__name__}.invoke 未实现")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<{type(self).__name__} name={self.name!r} model={self.model_name!r}>"


class FlowAdapterBase(ModelAdapter):
    """Marker base class for flow-level adapters (§37)."""

    is_flow = True
    name = "flow"

    def invoke_case(self, case_request: Mapping[str, Any], *, emit: Callable[..., Any] | None = None,
                    cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Run the whole user flow for one case and return an observed result."""
        raise NotImplementedError


__all__ = [
    "CAPABILITY_KEYS", "DEFAULT_CAPABILITIES", "EVENT_FIELDS", "MAX_RETRIES",
    "RETRYABLE_ERROR_CODES", "AdapterError", "FlowAdapterBase", "ModelAdapter",
    "call_emit", "collect_events", "default_capabilities", "emit_event",
    "json_dumps", "make_event", "mask_headers", "normalize_capabilities",
    "normalize_tool_definitions", "normalize_usage", "now_iso",
    "parse_tool_arguments", "redact", "resolve_secret_ref", "safe_log_config",
    "sum_usage",
]
