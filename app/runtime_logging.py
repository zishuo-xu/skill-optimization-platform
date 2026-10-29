"""Console/file logging and content-free summaries of persisted run events."""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

from . import config
from .model_output import redact_output

log = logging.getLogger(__name__)
run_log = logging.getLogger('app.run')
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return _safe_text(super().format(record))


def _safe_text(text):
    # Mask the entire Bearer value before generic key/value masking can consume
    # just the word "Bearer" and leave the credential behind.
    text = re.sub(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer ***', text)
    return redact_output(text)


def configure_logging():
    """Called by the server entry point; never changes logging on module import."""
    raw_level = os.environ.get('SKILLOPT_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, raw_level, None)
    if not isinstance(level, int):
        level = logging.INFO
    formatter = SafeFormatter('%(asctime)s %(levelname)s pid=%(process)d %(name)s %(message)s',
                              datefmt='%Y-%m-%d %H:%M:%S')
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    handlers = [console]
    path = Path(os.environ.get('SKILLOPT_LOG_DIR') or config.DATA_DIR / 'logs').expanduser().resolve() / 'server.log'
    file_error = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES,
                                           backupCount=LOG_BACKUPS, encoding='utf-8',
                                           errors='backslashreplace')
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError as exc:
        file_error = type(exc).__name__
    logging.basicConfig(level=level, handlers=handlers, force=True)
    # Route Uvicorn lifecycle/errors through the same handlers; do not duplicate
    # browser polling/access lines or third-party request URLs/headers.
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)
    for name in ('httpx', 'httpcore', 'websockets'):
        logging.getLogger(name).setLevel(logging.WARNING)
    if file_error:
        log.warning('无法写入日志文件，继续输出到终端 path=%s error_type=%s', path, file_error)
    else:
        log.info('日志已启用 file=%s max_bytes=%s backups=%s level=%s',
                 path, LOG_MAX_BYTES, LOG_BACKUPS, logging.getLevelName(level))
    if not isinstance(getattr(logging, raw_level, None), int):
        log.warning('日志级别无效，已使用 INFO')
    log.info('运行环境 python=%s executable=%s platform=%s code_root=%s',
             platform.python_version(), sys.executable, platform.system(), config.REPO_ROOT)
    log.info('数据位置 data_dir=%s db_path=%s', config.DATA_DIR, config.DB_PATH)
    return path if file_error is None else None


@contextmanager
def startup_step(name):
    started = time.monotonic()
    log.info('启动阶段开始 step=%s', name)
    try:
        yield
    except Exception as exc:
        log.error('启动阶段失败 step=%s elapsed_ms=%s exception_type=%s',
                  name, round((time.monotonic() - started) * 1000), type(exc).__name__)
        raise
    else:
        log.info('启动阶段完成 step=%s elapsed_ms=%s',
                 name, round((time.monotonic() - started) * 1000))


_FIELDS = frozenset('''phase purpose provider model stream mode name ok status decision
error_code adapter_error_code exception_type strategy candidate_source
failure_count passing_count file_count input_chars skill_chars message_count
max_output_tokens request_timeout_s remaining_ms attempt max_attempts
response_status response_ms first_byte_ms first_content_ms first_tool_ms
received_bytes chunk_count sse_event_count content_chars reasoning_chars
tool_argument_chars tool_delta_count done_seen elapsed_ms duration_ms retry_planned
retry_in_s retrying retryable partial_output input_tokens output_tokens total_tokens
first_packet_timeout_s idle_timeout_s finish_reason tool_call_count edit_count
patch_apply_ms planned_executions repeats case_count error_count errors_truncated
cache_hit used_fallback fallback_reason candidate_skill_version_id baseline_skill_version_id
baseline_run_id source_run_id source_phase skill_version_id reused_count execute_count planned_count rule skill_id path tool tool_name'''.split())
_SKIP_EVENTS = frozenset(('model_delta', 'progress_updated', 'tool_result'))


def _scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value.replace('\r', ' ').replace('\n', ' '))[:240]
    return None


def event_summary(event: Mapping):
    """Whitelist metadata, never serialize a whole payload/config/response."""
    summary = {key: _scalar(event.get(key)) for key in
               ('run_id', 'seq', 'type', 'stage', 'case_id', 'call_id', 'attempt')}
    payload = event.get('payload') or {}
    for key in _FIELDS:
        if key in payload and isinstance(payload[key], (str, int, float, bool, type(None))):
            summary[key] = _scalar(payload[key])
    issue = payload.get('issue') if isinstance(payload.get('issue'), Mapping) else payload
    if issue.get('error_code'):
        summary['error_code'] = _scalar(issue['error_code'])
    detail = issue.get('technical_detail') or {}
    if isinstance(detail, Mapping):
        for key in ('adapter_error_code', 'exception_type', 'elapsed_ms', 'request_timeout_s'):
            if key in detail:
                summary[key] = _scalar(detail[key])
    errors = payload.get('errors') or payload.get('issues')
    if isinstance(errors, list):
        summary['error_count'] = payload.get('error_count', len(errors))
        summary['errors'] = [{key: _scalar(item[key]) for key in
                             ('code', 'path', 'line', 'reference_kind', 'resolved_path') if key in item}
                            for item in errors[:5] if isinstance(item, Mapping)]
    return summary


def log_run_event(event):
    if event.get('type') in _SKIP_EVENTS:
        return
    level = {'debug': logging.DEBUG, 'warning': logging.WARNING,
             'error': logging.ERROR}.get(event.get('level'), logging.INFO)
    if not run_log.isEnabledFor(level):
        return
    try:
        run_log.log(level, '运行事件 %s', json.dumps(event_summary(event), ensure_ascii=False, sort_keys=True))
    except Exception:
        # A log destination must not turn a completed database write into a
        # failed model call or change the run outcome.
        pass
