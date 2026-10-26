from __future__ import annotations

import json

from app import services
from app.connection_probe import probe_connection
from app.adapters.openai_http import OpenAIHTTPAdapter
from app.adapters.base import AdapterError
from tests.test_a13_capabilities import _FakeHTTP


def test_demo_probe_is_truthful_and_does_not_claim_network():
    result = probe_connection({"provider": "heuristic", "model": "h"}, target="optimizer")
    assert result["ok"] is True
    assert result["performed"] is False
    assert result["target"] == "optimizer"


def test_check_mode_keeps_legacy_local_validation(monkeypatch):
    called = []
    monkeypatch.setattr(services, "test_connection", lambda payload: called.append(payload) or {"ok": True})
    result = services.probe_connection({"mode": "check", "model_config": {"provider": "heuristic"}})
    assert result == {"ok": True}
    assert called


def test_real_http_probe_is_single_short_request_and_returns_preview():
    payload = {"model": "local", "choices": [{"message": {"content": "OK"},
              "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    with _FakeHTTP([(200, payload)]) as server:
        result = probe_connection({"provider": "openai_http", "base_url": server.url,
                                   "model": "local", "stream": False, "api_key": "local-secret"})
        assert result["ok"] is True
        assert result["performed"] is True
        assert result["response_preview"] == "OK"
        assert len(server.requests) == 1
        assert server.requests[0]["body"]["messages"] == [{"role": "user", "content": "只回复 OK"}]


def test_provider_switch_does_not_inherit_stored_credentials(monkeypatch):
    observed = {}

    def fake_probe(cfg, *, target, input_text=None):
        observed.update(cfg)
        return {"provider": cfg["provider"], "performed": False}

    monkeypatch.setattr("app.connection_probe.probe_connection", fake_probe)
    monkeypatch.setattr("app.persistence.get_environment", lambda _id: {
        "model_config": {"provider": "openai_http", "api_key": "stored", "base_url": "http://old"},
        "optimizer_config": {},
    })
    services.probe_connection({"environment_id": "env", "model_config": {
        "provider": "enterprise_ws", "ws_url": "ws://new"}})
    assert observed["provider"] == "enterprise_ws"
    assert "api_key" not in observed


def test_custom_input_is_sent_verbatim_and_default_is_ping():
    payload = {"model": "local", "choices": [{"message": {"content": "OK"},
              "finish_reason": "stop"}]}
    with _FakeHTTP([(200, payload)]) as server:
        result = services.probe_connection({
            "model_config": {"provider": "openai_http", "base_url": server.url,
                              "model": "local", "stream": False},
            "input": "  请保留  内部空白  ",
        })
        assert result["ok"] is True
        assert server.requests[0]["body"]["messages"][0]["content"] == "  请保留  内部空白  "
    with _FakeHTTP([(200, payload)]) as server:
        services.probe_connection({"model_config": {"provider": "openai_http",
                                                      "base_url": server.url,
                                                      "model": "local", "stream": False},
                                   "input": "   "})
        assert server.requests[0]["body"]["messages"][0]["content"] == "只回复 OK"


def test_invalid_input_rejected_before_network(monkeypatch):
    called = []
    monkeypatch.setattr(services, "_probe_config", lambda *args: called.append(True) or {})
    for value in (123, "x" * 16001):
        try:
            services.probe_connection({"input": value, "model_config": {"provider": "heuristic"}})
        except Exception as exc:
            assert getattr(exc, "code", None) == "validation_error" or "input" in str(exc)
        else:
            raise AssertionError("invalid input accepted")
    assert called == []


def test_invalid_url_and_numeric_config_fail_before_adapter(monkeypatch):
    called = []
    monkeypatch.setattr("app.connection_probe.build_adapter", lambda cfg: called.append(cfg))
    result = probe_connection({"provider": "openai_http", "base_url": "not-a-url",
                               "model": "m", "timeout_s": "bad"})
    assert result["ok"] is False and result["error_code"] == "config_error"
    assert called == []


def test_empty_adapter_response_is_format_error(monkeypatch):
    class Empty:
        def invoke(self, *args, **kwargs):
            return {}

    monkeypatch.setattr("app.connection_probe.build_adapter", lambda cfg: Empty())
    result = probe_connection({"provider": "openai_http", "base_url": "http://127.0.0.1:1",
                               "model": "m"})
    assert result["ok"] is False and result["error_code"] == "format_error"


def test_adapter_error_message_is_redacted(monkeypatch):
    class Failing:
        def invoke(self, *args, **kwargs):
            raise AdapterError("gateway echoed local-secret", code="auth_failed", retryable=False)

    monkeypatch.setattr("app.connection_probe.build_adapter", lambda cfg: Failing())
    result = probe_connection({"provider": "openai_http", "base_url": "http://127.0.0.1:1",
                               "model": "m", "api_key": "local-secret"})
    assert "local-secret" not in result["message"]
