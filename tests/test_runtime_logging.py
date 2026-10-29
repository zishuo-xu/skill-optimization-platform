import json
import logging
import os
from pathlib import Path
import subprocess
import sys

from app import persistence, runtime_logging
from app.run_events import EventEmitter
from tests.conftest import start_run


def test_event_summary_excludes_prompts_responses_headers_and_error_bodies():
    event = {'run_id': 'run1', 'call_id': 'opt1', 'seq': 23, 'type': 'optimizer_request_info',
             'payload': {'phase': 'attempt_failed', 'attempt': 1, 'elapsed_ms': 60003,
                         'reasoning_chars': 17933, 'content_chars': 0,
                         'request': 'PRIVATE_REQUEST', 'response': 'PRIVATE_RESPONSE',
                         'headers': {'X-Custom': 'PRIVATE_HEADER'}, 'reason': 'PRIVATE_REASON',
                         'issue': {'error_code': 'timeout', 'technical_detail': {
                             'exception_type': 'AdapterError', 'exception_message': 'PRIVATE_ERROR'}}}}
    summary = runtime_logging.event_summary(event)
    assert summary['elapsed_ms'] == 60003 and summary['reasoning_chars'] == 17933
    assert summary['call_id'] == 'opt1' and summary['error_code'] == 'timeout'
    assert 'PRIVATE_' not in json.dumps(summary)


def test_persisted_request_events_are_mirrored_with_ids(demo, caplog):
    run = start_run(demo, 'optimize')
    with caplog.at_level(logging.INFO, logger='app.run'):
        event = EventEmitter(run['id'], 'candidate_generation').emit(
            'optimizer_request_info', {'phase': 'first_byte', 'elapsed_ms': 8000,
                                       'received_bytes': 40, 'content': 'PRIVATE_BODY'}, call_id='opt1')
    assert run['id'] in caplog.text and 'opt1' in caplog.text and 'first_byte' in caplog.text
    assert 'PRIVATE_BODY' not in caplog.text
    assert persistence.get_event(run['id'], event['seq'])['payload']['content'] == 'PRIVATE_BODY'


def test_runtime_logging_writes_console_and_rotating_files_without_duplicate_setup(tmp_path):
    env = {**os.environ, 'SKILLOPT_LOG_DIR': str(tmp_path / 'logs')}
    code = '''
import logging
from app import runtime_logging as r
r.LOG_MAX_BYTES = 1500
r.configure_logging()
r.configure_logging()
logging.getLogger('app.test').info('UNIQUE_MARKER')
logging.getLogger('app.test').warning('Authorization: Bearer PRIVATE_CREDENTIAL')
for index in range(100):
    logging.getLogger('app.test').info('row=%s %s', index, 'x' * 100)
logging.getLogger('uvicorn.error').info('UVICORN_MARKER')
logging.shutdown()
'''
    completed = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)
    assert completed.stderr.count('UNIQUE_MARKER') == 1
    assert 'PRIVATE_CREDENTIAL' not in completed.stderr
    files = list((tmp_path / 'logs').glob('server.log*'))
    assert len(files) == 6
    assert all(file.stat().st_size <= 1500 for file in files)
    assert 'UVICORN_MARKER' in (tmp_path / 'logs/server.log').read_text()


def test_unwritable_log_path_keeps_console_available(tmp_path):
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('keep')
    completed = subprocess.run([sys.executable, '-c',
        'from app.runtime_logging import configure_logging; assert configure_logging() is None'],
        env={**os.environ, 'SKILLOPT_LOG_DIR': str(blocked)}, capture_output=True, text=True, check=True)
    assert '继续输出到终端' in completed.stderr
    assert blocked.read_text() == 'keep'


def test_model_probe_logs_metadata_but_not_custom_headers(caplog):
    from app.connection_probe import probe_connection
    from tests.test_optimizer_stream import StreamOnlyGateway
    with StreamOnlyGateway() as gateway, caplog.at_level(logging.INFO):
        result = probe_connection({'provider': 'openai_http', 'model': 'local-test',
            'base_url': gateway.url, 'stream': True,
            'headers': {'X-Custom': 'PRIVATE_HEADER'}, 'api_key_ref': 'PRIVATE_API_KEY'},
            input_text='PRIVATE_PROMPT')
    assert result['ok']
    assert 'connection_probe:business' in caplog.text
    assert 'attempt_completed' in caplog.text
    assert 'PRIVATE_' not in caplog.text


def test_windows_launcher_keeps_crlf_and_unbuffered_server():
    content = Path('start.bat').read_bytes()
    assert b'\n' not in content.replace(b'\r\n', b'')
    assert b'"%VENV_PY%" -u -m app.server\r\n' in content
    assert b'set "PYTHONUNBUFFERED=1"' in content
