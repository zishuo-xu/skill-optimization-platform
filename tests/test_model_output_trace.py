from __future__ import annotations

import json

from app import event_types
from app.model_output import capture_model_response, redact_output
from app.run_events import EventEmitter


def test_text_response_is_whitelisted_and_summary_safe():
    result = capture_model_response({"content": "hello", "raw": "reasoning", "hidden_reasoning": "x"})
    assert result == {"content": "hello"}


def test_tool_only_and_internal_tool_arguments_are_visible():
    result = capture_model_response({"tool_calls": [{"id": "c", "name": "_internal", "arguments": {"x": 1}}],
                                     "finish_reason": "tool_calls"})
    assert result["tool_calls"][0]["name"] == "_internal"
    assert result["finish_reason"] == "tool_calls"


def test_reasoning_raw_are_excluded_and_usage_counts_survive():
    result = capture_model_response({"content": "x", "reasoning": "private", "raw": {"x": 1},
                                     "usage": {"input_tokens": 4, "output_tokens": 2}})
    assert "reasoning" not in result and "raw" not in result
    assert result["usage"] == {"input_tokens": 4, "output_tokens": 2}


def test_literal_env_bare_name_and_bearer_token_are_redacted(monkeypatch):
    monkeypatch.setenv("TRACE_KEY", "env-secret")
    config = {"api_key_ref": "TRACE_KEY", "headers": {"Authorization": "Bearer header-secret"},
              "monkey_keyword": "should-remain"}
    result = capture_model_response({"content": "env-secret header-secret should-remain"}, config)
    assert result["content"] == "*** *** should-remain"


def test_nested_legacy_result_redaction_preserves_token_counts(monkeypatch):
    monkeypatch.setenv("TRACE_KEY", "env-secret")
    result = redact_output({"response": "env-secret", "usage": {"input_tokens": 3},
                            "credential": "env-secret"}, {"model_config": {"api_key_ref": "TRACE_KEY"}})
    assert result["response"] == "***"
    assert result["usage"]["input_tokens"] == 3
    assert result["credential"] == "***"


def test_large_response_offloads_direct_response_json(monkeypatch):
    saved = {}

    def save(run_id, kind, data, **kwargs):
        saved.update(run_id=run_id, kind=kind, data=data, kwargs=kwargs)
        return {"id": "artifact_1", "summary": kwargs.get("summary", "")}

    monkeypatch.setattr("app.persistence.save_artifact", save)
    emitter = EventEmitter("run_trace")
    event = emitter.emit(event_types.MODEL_CALL_FINISHED,
                         {"response": {"content": "x" * 9000}, "response_summary": "long"},
                         call_id="call_1")
    assert "response" not in event["payload"]
    assert event["payload"]["artifacts"][0]["id"] == "artifact_1"
    assert json.loads(saved["data"]) == {"content": "x" * 9000}


def test_artifact_failure_keeps_safe_preview_and_does_not_raise(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.persistence.save_artifact", fail)
    emitter = EventEmitter("run_trace")
    event = emitter.emit(event_types.OPTIMIZER_CALL_FINISHED,
                         {"response": {"content": "x" * 9000}, "response_summary": "long"})
    assert event["payload"]["response"]["content"] == "x" * 500
    assert "response_note" in event["payload"]


def test_small_response_stays_inline_and_call_id_is_preserved():
    emitter = EventEmitter("run_trace")
    event = emitter.emit(event_types.MODEL_CALL_FINISHED,
                         {"response": {"content": "ok"}}, call_id="call_same")
    assert event["payload"]["response"] == {"content": "ok"}
    assert event["call_id"] == "call_same"


def test_unconfigured_secrets_in_output_text_are_masked():
    response = capture_model_response({"content": 'api_key=OUTPUT_PRIVATE Bearer ACCESS_PRIVATE'})
    assert "OUTPUT_PRIVATE" not in response["content"]
    assert "ACCESS_PRIVATE" not in response["content"]
