"""Connection scope: standard API and WebSocket gateway; Python flow is removed."""

from __future__ import annotations

import pytest

from app.adapters.enterprise_ws import EnterpriseWSAdapter
from app.adapters.openai_http import OpenAIHTTPAdapter
from app.adapters.registry import build_adapter
from app.errors import AppError
from tests.conftest import environment_payload


@pytest.mark.parametrize("provider", ["flow", "python_flow", "python"])
def test_registry_rejects_removed_python_flow(provider):
    with pytest.raises(AppError) as excinfo:
        build_adapter({"provider": provider})
    assert excinfo.value.code == "unknown_provider"
    assert "Python 流程接入已移除" in excinfo.value.message


def test_supported_transports_still_build():
    assert isinstance(build_adapter({"provider": "openai_http"}), OpenAIHTTPAdapter)
    assert isinstance(build_adapter({"provider": "enterprise_ws"}), EnterpriseWSAdapter)
    # Offline demo stays available for the bundled sample projects.
    assert getattr(build_adapter({"provider": "demo_scripted"}), "name", "") == "demo_scripted"


@pytest.mark.parametrize("field", ["model_config", "optimizer_config"])
def test_environment_api_rejects_python_flow(client, demo, field):
    payload = environment_payload(**{field: {"provider": "flow", "python_entry": "pkg.run"}})
    response = client.post(f"/api/v3/projects/{demo['project']['id']}/environments",
                           json={**payload, "status": "draft"})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert "Python 流程接入已移除" in error["message"]


def test_connection_test_rejects_python_flow(client):
    response = client.post("/api/v3/environments/test-connection", json={
        "target": "business",
        "model_config": {"provider": "flow", "python_entry": "pkg.run"},
    })
    assert response.status_code == 422, response.text
    assert "Python 流程接入已移除" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# standard API: URL + model id + API key (OpenAI compatible)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url, expected", [
    ("https://api.example.com/v1", "https://api.example.com/v1/chat/completions"),
    ("https://api.example.com/v1/", "https://api.example.com/v1/chat/completions"),
    ("https://api.example.com/v1/chat/completions", "https://api.example.com/v1/chat/completions"),
])
def test_standard_api_single_url_field(url, expected):
    adapter = OpenAIHTTPAdapter({"provider": "openai_http", "base_url": url, "model": "m"})
    assert adapter.url == expected


def test_standard_api_key_is_sent_as_bearer(monkeypatch):
    literal = OpenAIHTTPAdapter({"provider": "openai_http", "base_url": "https://h/v1",
                                 "model": "m", "api_key_ref": "sk-test-123"})
    assert literal._headers(literal.model_config)["Authorization"] == "Bearer sk-test-123"

    monkeypatch.setenv("STD_API_KEY_FOR_TEST", "sk-from-env")
    from_env = OpenAIHTTPAdapter({"provider": "openai_http", "base_url": "https://h/v1",
                                  "model": "m", "api_key_ref": "env:STD_API_KEY_FOR_TEST"})
    assert from_env._headers(from_env.model_config)["Authorization"] == "Bearer sk-from-env"
