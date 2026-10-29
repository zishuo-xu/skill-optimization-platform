"""A stream-only HTTP route checks both optimizer probing and patch generation."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import optimizer, persistence, services
from app.adapters.registry import build_adapter
from tests.test_a06_optimizer import _context, _model_candidate, _routing_failure, FakeEmit
from tests.conftest import create_demo_project


class StreamOnlyGateway:
    def __init__(self, chunk_delay=0):
        self.chunk_delay = chunk_delay

    def __enter__(self):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                if not body.get('stream'):
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"route not registered"}}')
                    return
                messages = body['messages']
                content = json.dumps(_model_candidate(), ensure_ascii=False) if messages[0]['role'] == 'system' else 'OK'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunks = [
                    {'model': 'stream-only', 'choices': [{'delta': {'content': content[:20]}}]},
                    {'model': 'stream-only', 'choices': [{'delta': {'content': content[20:]}, 'finish_reason': 'stop'}]},
                    {'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 5}},
                ]
                for chunk in chunks:
                    time.sleep(owner.chunk_delay)
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                    self.wfile.flush()
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


def test_optimizer_stream_total_timeout_and_longer_config_recovery():
    with StreamOnlyGateway(chunk_delay=0.12) as gateway:
        cfg = {'provider': 'openai_http', 'base_url': gateway.url, 'model': 'stream-only',
               'stream': True, 'timeout_s': 0.2, 'max_retries': 0}
        emit = FakeEmit()
        result = optimizer.generate_candidate(_context([_routing_failure()], optimizer_config=cfg),
                                              build_adapter(cfg), emit)
        assert result['used_fallback'] is True
        issue = emit.by_type('optimizer_call_failed')[0]['payload']['issue']
        infos = emit.by_type('optimizer_request_info')
        assert infos and all(e['level'] == 'info' for e in infos)
        assert len({e['call_id'] for e in infos + emit.by_type('optimizer_call_started')}) == 1
        last = infos[-1]['payload']
        assert last['phase'] == 'attempt_failed' and last['error_code'] == 'timeout'
        assert last['response_status'] == 200 and last['first_byte_ms'] is not None
        assert last['received_bytes'] > 0
        assert issue['title'] == '优化模型请求超时'
        assert issue['technical_detail']['adapter_error_code'] == 'timeout'
        assert issue['technical_detail']['request_timeout_s'] == 0.2
        assert '发布' in issue['next_action']
        assert emit.types.index('optimizer_call_failed') < emit.types.index('fallback_started')
        longer = {**cfg, 'timeout_s': 2}
        recovered = optimizer.generate_candidate(_context([_routing_failure()], optimizer_config=longer),
                                                 build_adapter(longer), FakeEmit())
        assert recovered['status'] == 'ok' and recovered['used_fallback'] is False
        assert len(gateway.requests) == 2


def test_optimizer_probe_and_generation_use_published_stream_setting(client):
    with StreamOnlyGateway() as gateway:
        cfg = {'provider': 'openai_http', 'base_url': gateway.url, 'model': 'stream-only',
               'stream': True, 'max_tokens': 4096, 'timeout_s': 2, 'max_retries': 0}
        demo = create_demo_project(env_overrides={'optimizer_config': cfg})
        original = demo['environment']
        # Explicit false remains false through saving and publishing a draft.
        draft = services.create_environment(demo['project']['id'], {
            **original, 'optimizer_config': {**cfg, 'stream': False},
        }, status='draft', parent_id=original['id'])
        disabled = services.publish_environment(draft['id'])
        failed = client.post('/api/v3/environments/test-connection', json={
            'environment_id': disabled['id'], 'target': 'optimizer'}).json()
        assert not failed['ok'] and failed['error_code'] == 'platform_error'
        assert len(gateway.requests) == 1 and gateway.requests[-1]['stream'] is False
        # Enabling the same connection changes both probe and real optimizer calls.
        draft = services.create_environment(demo['project']['id'], {
            **disabled, 'optimizer_config': cfg,
        }, status='draft', parent_id=disabled['id'])
        enabled = services.publish_environment(draft['id'])
        probe = client.post('/api/v3/environments/test-connection', json={
            'environment_id': enabled['id'], 'target': 'optimizer'}).json()
        assert probe['ok'] and probe['response_preview'] == 'OK'
        saved_config = persistence.get_environment(enabled['id'])['optimizer_config']
        emit = FakeEmit()
        result = optimizer.generate_candidate(_context([_routing_failure()], optimizer_config=saved_config),
                                              build_adapter(saved_config), emit)
        assert result['status'] == 'ok' and not result['used_fallback']
        assert result['patch']['edits'] == _model_candidate()['patch']['edits']
        assert len(gateway.requests) == 3
        assert [r['stream'] for r in gateway.requests] == [False, True, True]
        assert gateway.requests[1]['model'] == gateway.requests[2]['model'] == 'stream-only'
        assert persistence.get_environment(disabled['id'])['optimizer_config']['stream'] is False
        assert emit.by_type('optimizer_call_finished')[0]['payload']['usage']['total_tokens'] == 12
