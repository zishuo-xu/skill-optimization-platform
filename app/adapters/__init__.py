"""Model adapters: unified transport interface plus concrete implementations.

Importing this package never touches the network. ``registry.build_adapter`` is
the single construction entry point; ``demo_scripted`` is imported lazily so the
demo adapter can live alongside the pipeline without creating an import cycle.
"""

from __future__ import annotations

from .base import (
    AdapterError,
    FlowAdapterBase,
    ModelAdapter,
    call_emit,
    default_capabilities,
    emit_event,
    normalize_capabilities,
    normalize_usage,
    redact,
)
from .enterprise_ws import EnterpriseWSAdapter
from .flow import FlowAdapter
from .openai_http import OpenAIHTTPAdapter
from .registry import ADAPTER_PROVIDERS, build_adapter, provider_name
from .stream_events import SSEEventAssembler, aggregate, extract_stream_error
from . import text_parse

__all__ = [
    "ADAPTER_PROVIDERS", "AdapterError", "EnterpriseWSAdapter", "FlowAdapter",
    "FlowAdapterBase", "ModelAdapter", "OpenAIHTTPAdapter", "SSEEventAssembler",
    "aggregate", "build_adapter", "call_emit", "default_capabilities",
    "emit_event", "extract_stream_error", "normalize_capabilities",
    "normalize_usage", "provider_name", "redact", "text_parse",
]
