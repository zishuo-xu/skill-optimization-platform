"""Check live delivery while execution is blocked, cancellation and bounded traces."""
import asyncio
import json
import threading
from starlette.requests import Request
from app import api_v3, trial


def request():
    async def receive():
        return {'type': 'http.request', 'body': b'{"query":"test"}', 'more_body': False}
    return Request({'type': 'http', 'method': 'POST', 'path': '/',
                    'headers': [(b'accept', b'application/x-ndjson')],
                    'path_params': {'project_id': 'test'}}, receive)


def test_event_delivered_before_model_finishes(monkeypatch):
    release = threading.Event()
    done = threading.Event()
    def blocked(body, *, on_event, **kwargs):
        on_event({'seq': 1, 'type': 'model_call_started'})
        assert release.wait(3)
        done.set()
        return {'trial': True}
    monkeypatch.setattr(trial, 'try_case', blocked)
    async def check():
        response = await api_v3.debug_case(request())
        stream = response.body_iterator
        try:
            frames = [json.loads(await asyncio.wait_for(anext(stream), 1)) for _ in range(2)]
            assert frames[1]['event']['type'] == 'model_call_started'
            assert not done.is_set()
            release.set()
            final = json.loads(await asyncio.wait_for(anext(stream), 1))
            assert final == {'type': 'result', 'result': {'trial': True}}
        finally:
            release.set()
            await stream.aclose()
    asyncio.run(check())


def test_close_signals_worker_cancellation(monkeypatch):
    stopped = threading.Event()
    def blocked(body, *, cancel_event, **kwargs):
        assert cancel_event.wait(3)
        stopped.set()
        return {'trial': True}
    monkeypatch.setattr(trial, 'try_case', blocked)
    async def check():
        response = await api_v3.debug_case(request())
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert await asyncio.to_thread(stopped.wait, 1)
    asyncio.run(check())


def test_terminal_survives_full_queue(monkeypatch):
    done = threading.Event()
    def flood(body, *, on_event, **kwargs):
        for seq in range(1000):
            on_event({'seq': seq + 1, 'type': 'event'})
        done.set()
        return {'trial': True}
    monkeypatch.setattr(trial, 'try_case', flood)
    async def check():
        response = await api_v3.debug_case(request())
        assert await asyncio.to_thread(done.wait, 2)
        await asyncio.sleep(.05)
        frames = [json.loads(frame) async for frame in response.body_iterator]
        assert len(frames) <= 256
        assert frames[-1]['type'] == 'result'
    asyncio.run(asyncio.wait_for(check(), 3))


def test_emitter_keeps_recent_events_and_unique_sequences():
    seen = []
    emit = trial._TrialEmitter(seen.append)
    for i in range(230):
        emit({'type': 'event', 'payload': {'index': i}})
    assert len(emit.events) == 200
    assert [e['seq'] for e in seen] == list(range(1, 231))
    assert emit.events[0]['seq'] == 31


def test_validation_error_has_single_terminal_frame(client, demo):
    response = client.post(f"/api/v3/projects/{demo['project']['id']}/debug-cases",
                           json={}, headers={'Accept': 'application/x-ndjson'})
    frames = [json.loads(line) for line in response.text.splitlines()]
    assert frames[-1]['type'] == 'error'
    assert frames[-1]['error']['message']
    assert sum(f['type'] in ('error', 'result') for f in frames) == 1


def test_cancelled_trial_skips_scoring_and_redacts_wire_data(monkeypatch, demo):
    from app import persistence
    secret = 'private-trial-credential-123'
    env = persistence.get_environment(demo['environment']['id'])
    env['model_config']['api_key_ref'] = {'value': secret}
    monkeypatch.setattr(persistence, 'get_environment', lambda _: env)
    cancellation = threading.Event()
    def execute(request, adapter, emit):
        emit({'type': 'model_call_finished', 'payload': {'message': f'echo {secret}'}})
        cancellation.set()
        return {'action': 'no_action', 'raw_output': f'echo {secret}'}
    monkeypatch.setattr(trial.execution, 'run_case', execute)
    monkeypatch.setattr(trial, '_score', lambda *args: (_ for _ in ()).throw(AssertionError('scoring after cancellation')))
    events = []
    result = trial.try_case({'case_id': 'opt_001'}, project_id=demo['project']['id'],
                            on_event=events.append, cancel_event=cancellation)
    assert result['result']['error_code'] == 'cancelled'
    assert secret not in json.dumps(events)
    assert secret not in json.dumps(result)
