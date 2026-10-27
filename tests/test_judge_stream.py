"""Local gateway regressions for streaming semantic judge requests."""

from __future__ import annotations

import pytest

from app.adapters.openai_http import OpenAIHTTPAdapter
from app.judge import JudgeError, ValueJudge
from app import services
from tests.test_http_gateway_review import _HTTPStream, _sse


def _config(base_url: str, *, stream: bool) -> dict:
    return {
        "provider": "openai_http",
        "base_url": base_url,
        "model": "local-judge",
        "api_key": "local-secret",
        "stream": stream,
        "max_retries": 2,
        "retry_backoff_s": 0,
        "timeout_s": 2,
    }


def _item(key: str = "judge-stream-case") -> dict:
    return {
        "key": key,
        "path": "$.args.color",
        "expected": "蓝色",
        "actual": "蓝色",
        "criteria": {"requirement": "颜色一致"},
    }


def _stream_response():
    return [
        _sse({"choices": [{"delta": {"content": '{"results":['}}]}),
        _sse({"choices": [{"delta": {"content": '{"id":"v1","passed":true,"reason":"一致"}]}'}}]}),
        _sse({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 5}}),
        _sse("[DONE]"),
    ]


def test_value_judge_streaming_gateway_returns_verdict_and_usage(monkeypatch):
    # Avoid coupling this transport test to the persistent judgment cache.
    monkeypatch.setattr("app.judge.persistence.get_judgments", lambda _keys: {})
    monkeypatch.setattr("app.judge.persistence.save_judgments", lambda *_args: None)
    with _HTTPStream([(200, _stream_response(), 0)]) as gateway:
        config = _config(gateway.url, stream=True)
        result, stats = ValueJudge(config, adapter=OpenAIHTTPAdapter(config)).judge([_item()])

    assert result["judge-stream-case"]["passed"] is True
    assert stats["judged"] == 1
    assert stats["usage"]["total_tokens"] == 12
    assert gateway.requests[0]["body"]["stream"] is True
    assert len(gateway.requests) == 1


def test_non_streaming_judge_hits_unregistered_route_without_retry(monkeypatch):
    monkeypatch.setattr("app.judge.persistence.get_judgments", lambda _keys: {})
    with _HTTPStream([
        (404, [b'{"error":{"message":"model route not registered"}}'], 0),
        (404, [b'{}'], 0),
    ]) as gateway:
        config = _config(gateway.url, stream=False)
        with pytest.raises(JudgeError, match="HTTP 404") as raised:
            ValueJudge(config, adapter=OpenAIHTTPAdapter(config)).judge([_item("judge-http-404")])

    assert raised.value.code == "platform_error"
    assert len(gateway.requests) == 1
    assert gateway.requests[0]["body"]["stream"] is False


def test_judge_connection_probe_forwards_stream_setting():
    with _HTTPStream([(200, _stream_response(), 0)]) as gateway:
        config = _config(gateway.url, stream=True)
        result = services.probe_connection({"target": "judge", "judge_config": config})
    assert result["ok"] is True
    assert result["target"] == "judge"
    assert gateway.requests[0]["body"]["stream"] is True
