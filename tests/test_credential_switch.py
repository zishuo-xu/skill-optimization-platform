"""Credentials across connection-method switches (template / header masks)."""

from __future__ import annotations

import pytest

from app import persistence, services
from app.errors import ValidationError
from tests.conftest import environment_payload

WS_CONFIG = {
    "provider": "enterprise_ws", "ws_url": "ws://gw.example/llm", "model": "m1",
    "request_template": {"api_key": "AccessServiceName", "user": "u1"},
    "headers": {"sender": "E123"},
}


def _published(project_id: str, **overrides):
    return services.create_environment(
        project_id, environment_payload(**overrides), status="published", set_current=True)


@pytest.mark.parametrize("group", ["model_config", "optimizer_config"])
def test_same_method_draft_inherits_masked_template_credential(group):
    project = persistence.create_project("same method")
    parent = _published(project["id"], **{group: dict(WS_CONFIG)})
    public = services.public_environment(parent)
    assert public[group]["request_template"]["api_key"] == "***"

    draft = services.create_environment(
        project["id"], environment_payload(**{group: public[group]}),
        parent_id=parent["id"], status="draft")
    assert draft[group]["request_template"]["api_key"] == "AccessServiceName"


@pytest.mark.parametrize("group", ["model_config", "optimizer_config"])
def test_switching_to_standard_api_drops_gateway_template_and_headers(group):
    # Standard API only has URL / model / key; masked gateway fields cannot leak.
    project = persistence.create_project("switch to http")
    parent = _published(project["id"], **{group: dict(WS_CONFIG)})
    switched = dict(services.public_environment(parent)[group],
                    provider="openai_http", base_url="https://gw.example/v1")
    draft = services.create_environment(
        project["id"], environment_payload(**{group: switched}),
        parent_id=parent["id"], status="draft")
    assert draft[group]["provider"] == "openai_http"
    assert "request_template" not in draft[group] and "headers" not in draft[group]
    assert "***" not in repr(draft)
