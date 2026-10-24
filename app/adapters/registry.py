"""Adapter registry: ``build_adapter(model_config) -> ModelAdapter`` (contract §2.6)."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from ..errors import AppError
from .base import ModelAdapter
from .enterprise_ws import EnterpriseWSAdapter
from .flow import FlowAdapter
from .openai_http import OpenAIHTTPAdapter

logger = logging.getLogger(__name__)

__all__ = ["ADAPTER_PROVIDERS", "build_adapter", "provider_name"]

#: Canonical provider names (contract) plus accepted aliases.
ADAPTER_PROVIDERS = {
    "demo_scripted": "demo_scripted",
    "openai_http": "openai_http",
    "openai": "openai_http",
    "http": "openai_http",
    "chat_completions": "openai_http",
    "enterprise_ws": "enterprise_ws",
    "enterprise_gateway": "enterprise_ws",
    "ws": "enterprise_ws",
    "flow": "flow",
    "python_flow": "flow",
    "python": "flow",
}


def provider_name(model_config: Mapping[str, Any] | None) -> str:
    if not isinstance(model_config, Mapping):
        return ""
    value = model_config.get("provider") or model_config.get("type") or ""
    return str(value).strip().lower()


def build_adapter(model_config: Mapping[str, Any] | None) -> ModelAdapter:
    """Instantiate the configured adapter.

    ``demo_scripted`` is imported lazily because it is a demo-only adapter owned
    by the pipeline layer; unknown providers raise :class:`AppError` with
    ``code="unknown_provider"`` so the API can return a 422 with a field path.
    """
    if not isinstance(model_config, Mapping):
        raise AppError("model_config 必须是对象", code="config_error",
                       status_code=422, field_path="model_config")
    raw_provider = provider_name(model_config)
    if not raw_provider:
        raise AppError("模型配置缺少 provider", code="config_error",
                       status_code=422, field_path="model_config.provider")
    canonical = ADAPTER_PROVIDERS.get(raw_provider)
    if canonical is None:
        raise AppError(
            f"未知的模型 provider: {raw_provider}",
            code="unknown_provider",
            status_code=422,
            field_path="model_config.provider",
            details={"allowed": sorted(set(ADAPTER_PROVIDERS.values()))},
        )
    if canonical == "demo_scripted":
        return _build_demo_adapter(model_config)
    if canonical == "openai_http":
        return OpenAIHTTPAdapter(model_config)
    if canonical == "enterprise_ws":
        return EnterpriseWSAdapter(model_config)
    if canonical == "flow":
        return FlowAdapter(model_config)
    raise AppError(f"未知的模型 provider: {raw_provider}", code="unknown_provider",
                   status_code=422, field_path="model_config.provider")


def _build_demo_adapter(model_config: Mapping[str, Any]) -> ModelAdapter:
    model = str(model_config.get("model") or "").lower()
    domain = str(model_config.get("domain") or "").lower()
    if domain == "image" or model.startswith("demo-image"):
        try:
            module = __import__("app.adapters.demo_image", fromlist=["DemoImageScriptedAdapter"])
            adapter_class = getattr(module, "DemoImageScriptedAdapter")
            return adapter_class(model_config)
        except (ImportError, AttributeError) as exc:
            logger.warning("demo image adapter unavailable: %s", exc)
            raise AppError(
                "图片编辑演示适配器不可用",
                code="provider_unavailable",
                status_code=422,
                field_path="model_config.provider",
            ) from exc
    try:
        module = __import__("app.adapters.demo_scripted", fromlist=["DemoScriptedAdapter"])
        adapter_class = getattr(module, "DemoScriptedAdapter")
    except (ImportError, AttributeError) as exc:
        logger.warning("demo_scripted adapter unavailable: %s", exc)
        raise AppError(
            "demo_scripted 适配器不可用（演示脚本尚未安装）",
            code="provider_unavailable",
            status_code=422,
            field_path="model_config.provider",
        ) from exc
    return adapter_class(model_config)


def adapter_capabilities(adapter: ModelAdapter) -> dict[str, bool]:
    """Capability snapshot helper used by the executor and the API."""
    getter = getattr(adapter, "capability_snapshot", None)
    if callable(getter):
        return getter()
    return dict(getattr(adapter, "capabilities", {}) or {})
