import json

from app.adapters import request_diagnostics
from app.adapters.openai_http import OpenAIHTTPAdapter
from app.adapters.request_diagnostics import RequestDiagnostics
from tests.test_http_gateway_review import _HTTPStream, _sse


def test_counts_only_progress_is_throttled_and_partial_output_survives(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(request_diagnostics.time, 'monotonic', lambda: clock[0])
    observations = []
    diag = RequestDiagnostics(observations.append, 1, 3)
    diag.emit('attempt_started')
    clock[0] = 101
    diag.response(200)
    clock[0] = 102
    diag.received(b'PRIVATE_FRAME')
    diag.parsed([{'json': {'choices': [{'delta': {'reasoning_content': 'PRIVATE_REASONING'}}]}}])
    assert diag.stats['first_content_ms'] is None
    assert diag.stats['reasoning_chars'] == len('PRIVATE_REASONING')
    clock[0] = 103
    diag.parsed([{'json': {'choices': [{'delta': {'content': 'PRIVATE_CONTENT'}}]}}])
    assert diag.stats['first_content_ms'] == 3000
    clock[0] = 104
    diag.parsed([{'json': {'choices': [{'delta': {'tool_calls': [
        {'function': {'arguments': 'PRIVATE_ARGUMENT'}}]}}]}}])
    assert diag.stats['first_tool_ms'] == 4000
    for tick in range(105, 125):
        clock[0] = tick
        diag.received(b':heartbeat')
        diag.parsed([])
    diag.emit('attempt_failed', error_code='timeout')
    assert len([o for o in observations if o['phase'] == 'stream_progress']) == 2
    assert observations[-1]['content_chars'] == len('PRIVATE_CONTENT')
    assert observations[-1]['tool_argument_chars'] == len('PRIVATE_ARGUMENT')
    assert observations[-1]['done_seen'] is False
    assert 'PRIVATE_' not in json.dumps(observations)


def test_observer_failure_does_not_change_request():
    def broken(_):
        raise RuntimeError('cannot persist')
    RequestDiagnostics(broken, 1, 1).emit('attempt_started')


def test_http_retry_diagnostics_are_correlated_and_do_not_copy_content():
    responses = [(429, [b'{"error":{"message":"PRIVATE_ERROR"}}'], 0),
                 (200, [_sse({'choices': [{'delta': {'content': 'PRIVATE_OUTPUT'},
                                           'finish_reason': 'stop'}]}), _sse('[DONE]')], 0)]
    with _HTTPStream(responses) as gateway:
        cfg = {'base_url': gateway.url, 'stream': True, 'model': 'local-test',
               'max_retries': 1, 'retry_backoff_s': 0, 'timeout_s': 3,
               'api_key_ref': 'PRIVATE_KEY'}
        events = []
        result = OpenAIHTTPAdapter(cfg).invoke(
            [{'role': 'user', 'content': 'PRIVATE_INPUT'}], {'call_id': 'test-call'}, [], emit=events.append)
        infos = [e for e in events if e['type'] == 'model_request_info']
        assert result['content'] == 'PRIVATE_OUTPUT'
        assert all(e['call_id'] == 'test-call' and e['level'] == 'info' for e in infos)
        attempts = [e['payload'] for e in infos if e['payload']['phase'] == 'attempt_started']
        assert [a['attempt'] for a in attempts] == [1, 2]
        failed = next(e['payload'] for e in infos if e['payload']['phase'] == 'attempt_failed')
        assert failed['response_status'] == 429 and failed['retry_planned']
        assert failed['error_code'] == 'rate_limited'
        last = infos[-1]['payload']
        assert last['phase'] == 'attempt_completed' and last['done_seen']
        assert last['first_byte_ms'] is not None and last['first_content_ms'] is not None
        assert last['received_bytes'] > 0
        assert 'PRIVATE_' not in json.dumps(infos)
        assert all('_request_observer' not in r['body'] for r in gateway.requests)
        assert '_request_diagnostics' not in cfg


def test_nonstream_http_records_response_and_complete_body_without_fake_first_token():
    response = json.dumps({'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]}).encode()
    with _HTTPStream([(200, [response], 0)]) as gateway:
        events = []
        result = OpenAIHTTPAdapter({'base_url': gateway.url, 'stream': False}).invoke(
            [{'role': 'user', 'content': 'ping'}], {}, [], emit=events.append)
        infos = [e['payload'] for e in events if e['type'] == 'model_request_info']
        assert result['content'] == 'OK'
        assert [e['phase'] for e in infos] == ['attempt_started', 'response_received', 'attempt_completed']
        assert infos[-1]['received_bytes'] == len(response)
        assert infos[-1]['first_content_ms'] is None
        assert infos[-1]['content_chars'] == 2
