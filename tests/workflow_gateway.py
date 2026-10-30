"""Local scripted HTTP optimizer for workflow acceptance, never a real LLM."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import optimizer


class WorkflowGateway:
    def __enter__(self):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                payload = json.loads(body['messages'][-1]['content'].split('\n', 1)[1])
                result = optimizer.heuristic_candidate(payload)
                assert result is not None
                mode = body.get('model', 'success')
                if mode == 'invalid-patch':
                    result['patch']['edits'][0]['search'] = 'SYNTHETIC MISSING SOURCE'
                elif mode == 'dataset-issue':
                    result['diagnosis']['attribution'] = 'dataset_issue'
                    result['diagnosis']['summary'] = '模拟诊断：预期调用缺少必要的能力查询，请核对测试数据。'
                    result['changed_files'] = []
                    result['patch']['edits'] = []
                content = json.dumps(result, ensure_ascii=False)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                try:
                    def emit(delta, finish=None):
                        chunk = {'choices': [{'delta': delta, 'finish_reason': finish}]}
                        self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                        self.wfile.flush()
                    emit({'reasoning_content': 'Scripted local gateway; not a real model.'})
                    if mode == 'timeout':
                        time.sleep(0.4)
                    emit({'content': content[:100]})
                    emit({'content': content[100:]}, 'stop')
                    self.wfile.write(b'data: [DONE]\n\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        return self

    def config(self, model='success', timeout_s=5):
        return {'provider': 'openai_http', 'base_url': self.url, 'model': model,
                'stream': True, 'timeout_s': timeout_s, 'max_retries': 0}

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
