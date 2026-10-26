"""Bounded local regressions for the standard HTTP gateway adapter."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.adapters.base import AdapterError
from app.adapters.openai_http import OpenAIHTTPAdapter


def _sse(value) -> bytes:
    if isinstance(value, str):
        data = value
    else:
        data = json.dumps(value, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


class _HTTPStream:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        self.server = None
        self.thread = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                owner.requests.append({"body": body, "headers": dict(self.headers), "path": self.path})
                index = min(len(owner.requests) - 1, len(owner.responses) - 1)
                response = owner.responses[index]
                status, chunks, pause = response[:3]
                initial_pause = response[3] if len(response) > 3 else 0
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if initial_pause:
                    time.sleep(initial_pause)
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if pause:
                        time.sleep(pause)
                if len(response) > 4:
                    time.sleep(response[4])

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


def _adapter(base_url, **extra):
    config = {
        "provider": "openai_http", "base_url": base_url, "model": "local",
        "api_key": "local-secret", "stream": True, "timeout_s": 2,
        "max_retries": 0, "retry_backoff_s": 0,
    }
    config.update(extra)
    return OpenAIHTTPAdapter(config)


def test_done_stops_open_stream_and_keeps_usage_before_done():
    chunks = [
        _sse({"choices": [{"delta": {"content": "ok"}}]}),
        _sse({"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}),
        _sse("[DONE]"),
    ]
    with _HTTPStream([(200, chunks, 0.0, 0.0, 0.8)]) as gateway:
        started = time.monotonic()
        result = _adapter(gateway.url).invoke([], {}, [])
        assert time.monotonic() - started < 0.7
    assert result["content"] == "ok"
    assert result["usage"] == {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}


def test_normal_delayed_first_chunk_and_inter_chunk_gap_do_not_timeout():
    chunks = [_sse({"choices": [{"delta": {"content": "a"}}]}), _sse("[DONE]")]
    with _HTTPStream([(200, chunks, 0.35, 0.35)]) as gateway:
        result = _adapter(gateway.url, timeout_s=2).invoke([], {}, [])
    assert result["content"] == "a"


def test_extra_body_cannot_replace_frozen_execution_fields():
    with _HTTPStream([(200, [_sse({"choices": [{"delta": {"content": "ok"}}]}), _sse("[DONE]")], 0)]) as gateway:
        adapter = _adapter(gateway.url, extra_body={
            "messages": [{"role": "system", "content": "spoof"}],
            "tools": [{"name": "spoof"}], "model": "spoof", "stream": False,
            "custom_gateway_flag": True,
        })
        adapter.invoke([{"role": "user", "content": "real"}], {}, [])
    body = gateway.requests[0]["body"]
    assert body["model"] == "local"
    assert body["messages"] == [{"role": "user", "content": "real"}]
    assert body["stream"] is True
    assert body["custom_gateway_flag"] is True


def test_missing_explicit_env_secret_is_auth_failure_before_request():
    with _HTTPStream([]) as gateway:
        adapter = _adapter(gateway.url, api_key_ref="env:HTTP_REVIEW_MISSING")
        with pytest.raises(AdapterError) as exc:
            adapter.invoke([], {}, [])
    assert exc.value.code == "auth_failed"
    assert gateway.requests == []


def test_partial_stream_rate_limit_is_not_retried():
    chunks = [
        _sse({"choices": [{"delta": {"content": "partial"}}]}),
        _sse({"error": {"code": 429, "message": "late limit"}}),
    ]
    with _HTTPStream([(200, chunks, 0.0), (200, chunks, 0.0)]) as gateway:
        adapter = _adapter(gateway.url, max_retries=2)
        with pytest.raises(AdapterError) as exc:
            adapter.invoke([], {}, [])
    assert exc.value.code == "rate_limited"
    assert len(gateway.requests) == 1


def test_partial_stream_read_timeout_is_not_retried():
    chunks = [_sse({"choices": [{"delta": {"content": "partial"}}]})]
    with _HTTPStream([(200, chunks, 1.0), (200, chunks, 0.0)]) as gateway:
        adapter = _adapter(gateway.url, timeout_s=0.25, max_retries=2)
        with pytest.raises(AdapterError) as exc:
            adapter.invoke([], {}, [])
    assert exc.value.code == "timeout"
    assert len(gateway.requests) == 1


def test_total_deadline_covers_retry_backoff():
    with _HTTPStream([(503, [b'{"error":"busy"}'], 0.0)]) as gateway:
        adapter = _adapter(gateway.url, stream=False, timeout_s=3,
                           max_retries=3, retry_backoff_s=1)
        started = time.monotonic()
        with pytest.raises(AdapterError) as exc:
            adapter.invoke([], {"deadline_monotonic": started + 0.15}, [])
    assert exc.value.code == "timeout"
    assert time.monotonic() - started < 0.6
    assert len(gateway.requests) == 1


def test_active_stream_does_not_bypass_global_deadline():
    chunks = [_sse({"choices": [{"delta": {"content": "x"}}]})] * 20
    with _HTTPStream([(200, chunks, 0.03)]) as gateway:
        started = time.monotonic()
        with pytest.raises(AdapterError) as exc:
            _adapter(gateway.url).invoke([], {"deadline_monotonic": started + 0.18}, [])
        assert exc.value.code == "timeout"
        assert time.monotonic() - started < 0.7
    assert len(gateway.requests) == 1


def test_http_request_preserves_gateway_body_headers_and_full_endpoint():
    chunks = [_sse({"choices": [{"delta": {"content": "ok"}}]}), _sse("[DONE]")]
    with _HTTPStream([(200, chunks, 0)]) as gateway:
        adapter = _adapter(gateway.url, url=gateway.url + "/company/custom-chat",
            headers={"Authorization": "Custom LOCAL_ONLY", "X-Company": "tenant-a"},
            api_key_ref="env:NOT_NEEDED_WHEN_AUTH_HEADER_IS_EXPLICIT",
            request_template={"api_key": "ServiceName", "user": "local-user",
                              "extra_body": {"enable_thinking": False},
                              "stream_options": {"include_usage": False, "custom": True}})
        assert adapter.url.endswith("/company/custom-chat")
        result = adapter.invoke([], {}, [])
    request = gateway.requests[0]
    assert request["path"] == "/v1/company/custom-chat"
    assert request["headers"]["Authorization"] == "Custom LOCAL_ONLY"
    assert request["headers"]["X-Company"] == "tenant-a"
    assert request["body"]["api_key"] == "ServiceName"
    assert request["body"]["extra_body"] == {"enable_thinking": False}
    assert request["body"]["stream_options"] == {"include_usage": False, "custom": True}
    assert result["content"] == "ok"


def test_template_cannot_inject_tools_when_executor_supplies_none():
    adapter = _adapter("http://127.0.0.1:1", request_template={
        "tools": [{"function": {"name": "unapproved"}}], "tool_choice": "required"})
    body = adapter._build_body([], [], adapter.model_config)
    assert "tools" not in body
    assert "tool_choice" not in body
