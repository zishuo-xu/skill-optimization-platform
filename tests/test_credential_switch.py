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
def test_switching_method_with_stale_mask_names_the_field(group):
    project = persistence.create_project("switch with mask")
    parent = _published(project["id"], **{group: dict(WS_CONFIG)})
    switched = dict(services.public_environment(parent)[group],
                    provider="openai_http", base_url="https://gw.example/v1")
    with pytest.raises(ValidationError) as excinfo:
        services.create_environment(
            project["id"], environment_payload(**{group: switched}),
            parent_id=parent["id"], status="draft")
    assert excinfo.value.field_path == f"{group}.request_template.api_key"
    assert f"{group}.request_template.api_key" in excinfo.value.message
    assert "重新填写" in excinfo.value.message


def test_switching_method_without_masks_keeps_plain_fields_and_no_credential():
    # What the page now submits after a switch: masked credentials removed.
    project = persistence.create_project("switch clean")
    parent = _published(project["id"], model_config=dict(WS_CONFIG))
    switched = dict(services.public_environment(parent)["model_config"],
                    provider="openai_http", base_url="https://gw.example/v1")
    switched["request_template"] = {"user": "u1"}
    draft = services.create_environment(
        project["id"], environment_payload(model_config=switched),
        parent_id=parent["id"], status="draft")
    assert draft["model_config"]["provider"] == "openai_http"
    assert "api_key" not in draft["model_config"]["request_template"]
    assert draft["model_config"]["request_template"]["user"] == "u1"
    assert "***" not in repr(draft)


def test_http_authorization_header_mask_is_not_carried_to_websocket():
    project = persistence.create_project("header switch")
    parent = _published(project["id"], model_config={
        "provider": "openai_http", "base_url": "https://gw.example/v1", "model": "m1",
        "headers": {"Authorization": "Bearer sk-x"}})
    switched = dict(services.public_environment(parent)["model_config"],
                    provider="enterprise_ws", ws_url="ws://gw.example/llm")
    assert switched["headers"]["Authorization"] == "***"
    with pytest.raises(ValidationError) as excinfo:
        services.create_environment(
            project["id"], environment_payload(model_config=switched),
            parent_id=parent["id"], status="draft")
    assert excinfo.value.field_path == "model_config.headers.Authorization"
