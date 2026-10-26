"""Real local transports exercise the public probe API without paid model calls."""
import asyncio
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import persistence, services
from tests.conftest import create_demo_project
from tests.test_a13_capabilities import _FakeHTTP
from tests.test_a12_stream_assembly import FakeGateway, _sse
from tests.test_http_gateway_review import _HTTPStream

URL = "/api/v3/environments/test-connection"


def answer(text="OK"):
    return {"model": "local-model", "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1}}


def config(api_root, **patch):
    return {"provider": "openai_http", "base_url": api_root, "model": "local-model", "stream": False,
            "timeout_s": 2, **patch}


def test_published_business_and_optimizer_probe_inherit_secrets_without_mutation(client):
    with _FakeHTTP([(200, answer()), (200, answer())]) as gateway:
        business = config(gateway.url, api_key_ref={"value": "BUSINESS_PRIVATE"},
                          request_template={"api_key": "BODY_PRIVATE", "user": "before"},
                          headers={"X-Company": "local"})
        optimizer = config(gateway.url, api_key_ref={"value": "OPTIMIZER_PRIVATE"})
        demo = create_demo_project(env_overrides={"model_config": business, "optimizer_config": optimizer})
        before = copy.deepcopy(persistence.get_environment(demo["environment"]["id"]))
        public = services.public_environment(before)["model_config"]
        public["request_template"]["user"] = "unsaved"
        first = client.post(URL, json={"environment_id": before["id"], "target": "business", "model_config": public})
        second = client.post(URL, json={"environment_id": before["id"], "target": "optimizer"})
        assert first.json()["ok"] and second.json()["ok"]
        assert first.json()["target"] == "business" and second.json()["target"] == "optimizer"
        assert gateway.requests[0]["headers"]["authorization"] == "Bearer BUSINESS_PRIVATE"
        assert gateway.requests[1]["headers"]["authorization"] == "Bearer OPTIMIZER_PRIVATE"
        assert gateway.requests[0]["body"]["api_key"] == "BODY_PRIVATE"
        assert gateway.requests[0]["body"]["user"] == "unsaved"
        assert gateway.requests[0]["body"]["messages"] == [{"role": "user", "content": "只回复 OK"}]
        assert "tools" not in gateway.requests[0]["body"]
        assert gateway.requests[0]["body"]["max_tokens"] <= 64
        assert persistence.get_environment(before["id"]) == before
        assert persistence.list_runs(project_id=demo["project"]["id"])["total"] == 0
        assert "PRIVATE" not in first.text + second.text


@pytest.mark.parametrize("status,expected", [(401,"auth_failed"),(403,"auth_failed"),
                                             (404,"platform_error"),(429,"rate_limited")])
def test_http_errors_do_not_retry_or_leak_gateway_body(client, status, expected):
    with _FakeHTTP([(status, {"error": {"message": "PRIVATE_BODY"}})]) as gateway:
        result = client.post(URL, json={"model_config": config(gateway.url, max_retries=5)})
        assert result.status_code == 200
        assert result.json()["ok"] is False
        assert result.json()["error_code"] == expected
        assert "PRIVATE_BODY" not in result.text
        assert len(gateway.requests) == 1


def test_stream_probe_uses_configured_endpoint_and_returns_usage(client):
    chunks = [(_sse({"choices": [{"delta": {"content": "OK"}}]})).encode(),
              (_sse({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})).encode(),
              b"data: [DONE]\n\n"]
    with _HTTPStream([(200, chunks, 0)]) as gateway:
        cfg = config("", url=gateway.url + "/company/invoke", stream=True,
                     headers={"Authorization": "Custom PRIVATE"}, request_template={"user": "tenant"})
        result = client.post(URL, json={"model_config": cfg}).json()
        assert result["ok"] and result["response_preview"] == "OK"
        assert result["usage"]["total_tokens"] == 4
        assert gateway.requests[0]["path"] == "/v1/company/invoke"
        assert gateway.requests[0]["headers"]["Authorization"] == "Custom PRIVATE"
        assert gateway.requests[0]["body"]["user"] == "tenant"
        assert gateway.requests[0]["body"]["stream"] is True


def test_ws_probe_sends_body_credentials_and_preserves_gateway_format(client):
    async def behavior(ws, body, connection, gateway):
        await ws.send(_sse({"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}))
        await ws.send("data: [DONE]\n\n")
    with FakeGateway(behavior) as gateway:
        result = client.post(URL, json={"model_config": {
            "provider": "enterprise_ws", "ws_url": gateway.url, "model": "local-model",
            "request_template": {"api_key": "ServiceID", "user": "tenant"}}}).json()
        assert result["ok"] and result["performed"] and result["response_preview"] == "OK"
        assert gateway.connection_count == 1
        assert gateway.requests[0]["body"]["api_key"] == "ServiceID"
        assert "authorization" not in {k.lower() for k in gateway.requests[0]["headers"]}


def test_invalid_model_response_and_timeout_are_reported(client):
    with _FakeHTTP([(200, {"choices": [{}]})]) as gateway:
        result = client.post(URL, json={"model_config": config(gateway.url)}).json()
        assert not result["ok"] and result["error_code"] == "format_error"
    with _HTTPStream([(200, [json.dumps(answer()).encode()], 0, 0.3)]) as gateway:
        result = client.post(URL, json={"model_config": config(gateway.url, timeout_s=0.05)}).json()
        assert not result["ok"] and result["error_code"] == "timeout"
        assert len(gateway.requests) == 1


def test_probe_does_not_block_health_endpoint(client):
    with _HTTPStream([(200, [json.dumps(answer()).encode()], 0, 0.7)]) as gateway:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, URL, json={"model_config": config(gateway.url)})
            deadline = time.monotonic() + 2
            while not gateway.requests and time.monotonic() < deadline:
                time.sleep(0.005)
            assert gateway.requests
            started = time.monotonic()
            assert client.get("/api/v3/healthz").status_code == 200
            assert time.monotonic() - started < 0.4
            assert pending.result(timeout=3).json()["ok"]


def test_partial_override_keeps_address_but_explicit_clear_does_not_test_old_url(client):
    with _FakeHTTP([(200, answer())]) as gateway:
        demo = create_demo_project(env_overrides={"model_config": config(gateway.url)})
        identifier = demo["environment"]["id"]
        result = client.post(URL, json={"environment_id": identifier, "model_config": {"model": "override"}})
        assert result.json()["ok"]
        assert gateway.requests[0]["body"]["model"] == "override"
        cleared = client.post(URL, json={"environment_id": identifier, "model_config": {"base_url": ""}})
        assert not cleared.json()["ok"]
        assert len(gateway.requests) == 1
        assert client.post(URL, json={"target": "wrong"}).status_code == 422
        masked = client.post(URL, json={"model_config": config(gateway.url, api_key_ref="***")})
        assert masked.status_code == 422


def test_custom_inputs_reach_the_selected_published_models_verbatim(client):
    with _FakeHTTP([(200, answer()), (200, answer())]) as gateway:
        demo = create_demo_project(env_overrides={
            "model_config": config(gateway.url), "optimizer_config": config(gateway.url)})
        identifier = demo["environment"]["id"]
        values = ["  业务问题，保留  内部空格\n第二行。", "优化模型的独立测试问题"]
        for target, value in zip(("business", "optimizer"), values):
            response = client.post(URL, json={"environment_id": identifier, "target": target, "input": value})
            assert response.json()["ok"]
        assert [row["body"]["messages"][0]["content"] for row in gateway.requests] == values
        assert len(persistence.list_environments(demo["project"]["id"])) == 1
        assert persistence.list_runs(project_id=demo["project"]["id"])["total"] == 0


def test_invalid_custom_input_returns_field_error_without_network_request(client):
    with _FakeHTTP([]) as gateway:
        for value in (123, "x" * 16001):
            response = client.post(URL, json={"model_config": config(gateway.url), "input": value})
            assert response.status_code == 422
            assert response.json()["error"]["field_path"] == "input"
        assert gateway.requests == []


def test_output_limit_is_reported_without_mistaking_connectivity_for_completion(client):
    response = answer("")
    response["choices"][0]["finish_reason"] = "length"
    with _FakeHTTP([(200, response)]) as gateway:
        result = client.post(URL, json={"model_config": config(gateway.url), "input": "解释一个概念"}).json()
        assert result["ok"] and result["performed"]
        assert result["finish_reason"] == "length"
        assert result["response_truncated"] is True
        assert "上限" in result["message"]
