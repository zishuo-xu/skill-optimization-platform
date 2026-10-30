"""Gateway configuration round-trips use only isolated SQLite and fake values."""
import time

import pytest

from app import persistence, services, single_round
from app.errors import ValidationError
from tests.conftest import create_demo_project, environment_payload


def gateway_config(provider="openai_http"):
    return {
        "provider": provider, "base_url": "https://example.invalid/v1",
        "ws_url": "wss://example.invalid/model", "model": "local-test",
        "request_template": {"api_key": "BODY_PRIVATE", "user": "before",
                             "extra_body": {"client_secret": "NESTED_PRIVATE"}},
        "headers": {"Authorization": "Bearer HEADER_PRIVATE", "X-Api-Key": "CUSTOM_PRIVATE"},
    }


@pytest.mark.parametrize("provider", ["enterprise_ws"])
def test_masked_gateway_template_and_headers_survive_public_roundtrip(provider):
    demo = create_demo_project(env_overrides={"model_config": gateway_config(provider),
                                             "optimizer_config": gateway_config(provider)})
    env = demo["environment"]
    public = services.public_environment(env)
    for group in ("model_config", "optimizer_config"):
        assert public[group]["headers"]["X-Api-Key"] == "***"
        public[group]["request_template"]["user"] = "after"
    child = services.create_environment(demo["project"]["id"], public,
                                        parent_id=env["id"], status="draft")
    for group in ("model_config", "optimizer_config"):
        assert child[group]["headers"] == env[group]["headers"]
        assert child[group]["request_template"]["api_key"] == "BODY_PRIVATE"
        assert child[group]["request_template"]["extra_body"]["client_secret"] == "NESTED_PRIVATE"
        assert child[group]["request_template"]["user"] == "after"
    safe = repr(services.public_environment(child))
    for secret in ("BODY_PRIVATE", "NESTED_PRIVATE", "HEADER_PRIVATE", "CUSTOM_PRIVATE"):
        assert secret not in safe


def test_nested_masks_without_matching_parent_are_rejected():
    demo = create_demo_project(env_overrides={"model_config": gateway_config("enterprise_ws")})
    public = services.public_environment(demo["environment"])
    with pytest.raises(ValidationError):
        services.create_environment(demo["project"]["id"], public, status="draft")


def test_optimizer_preserves_output_limit_and_counts_usage():
    seen = {}
    class FakeAdapter:
        def invoke(self, messages, config, tools):
            seen.update(config)
            return {"content": "irrelevant", "usage": {"input_tokens": 8, "output_tokens": 3}}
    class Cancel:
        def cancelled(self):
            return False
    usage = single_round._UsageTracker()
    frozen = {"max_tokens": 100}
    single_round._ControlledOptimizer(FakeAdapter(), Cancel(), usage).invoke([], frozen, [])
    assert frozen == {"max_tokens": 100}
    assert seen["max_tokens"] == 100
    assert seen["cancel_check"]() is False
    assert "deadline_monotonic" not in seen
    assert usage.tokens_used == 11


def test_standard_api_drops_gateway_overrides():
    demo = create_demo_project(env_overrides={"model_config": gateway_config("openai_http")})
    config = demo["environment"]["model_config"]
    assert config["base_url"] == "https://example.invalid/v1"
    for key in ("headers", "request_template", "url"):
        assert key not in config
