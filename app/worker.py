"""Background worker: claims queued runs, executes the single-round pipeline."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Callable

from . import config, event_types, ids, persistence

log = logging.getLogger(__name__)

LEASE_SECONDS = 60


class Worker:
    def __init__(self, executor: Callable[[str], None] | None = None,
                 poll_interval: float = 0.3, lease_seconds: int = LEASE_SECONDS):
        self.worker_id = ids.new_id("worker")
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self._executor = executor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_run_id: str | None = None
        self._heartbeat_thread: threading.Thread | None = None

    # -- public API -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="skillopt-worker",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def process_once(self) -> bool:
        """Claim and execute one queued run synchronously. Returns True if ran."""
        run = persistence.claim_run(self.worker_id, self.lease_seconds)
        if not run:
            return False
        self._execute(run["id"])
        return True

    # -- internals --------------------------------------------------------
    def _loop(self) -> None:
        persistence.recover_stale_runs()
        while not self._stop.is_set():
            try:
                run = persistence.claim_run(self.worker_id, self.lease_seconds)
            except Exception:
                log.exception("claim_run failed")
                run = None
            if not run:
                self._stop.wait(self.poll_interval)
                continue
            self._execute(run["id"])
        self._stop.clear()

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set() and self._current_run_id:
            try:
                persistence.heartbeat_run(self._current_run_id, self.worker_id,
                                          self.lease_seconds)
            except Exception:
                log.exception("heartbeat failed")
            time.sleep(max(1.0, self.lease_seconds / 3))

    def _execute(self, run_id: str) -> None:
        started = time.monotonic()
        log.info('Worker 开始运行 run_id=%s worker_id=%s', run_id, self.worker_id)
        self._current_run_id = run_id
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name=f"heartbeat-{run_id}", daemon=True)
        self._heartbeat_thread.start()
        try:
            executor = self._executor or _default_executor()
            executor(run_id)
        except Exception as exc:  # unexpected programming error
            log.exception("run execution crashed: %s", run_id)
            try:
                persistence.finish_run(
                    run_id, status="failed",
                    error_code="worker_crashed",
                    error_message=f"{type(exc).__name__}: {exc}",
                    decision_reason="后台执行器异常终止")
                persistence.append_event(
                    run_id, event_types.RUN_FAILED, level="error",
                    payload={"issue": {
                        "error_code": "worker_crashed",
                        "title": "后台执行器异常",
                        "message": f"{type(exc).__name__}: {exc}",
                        "impact": "本次运行已终止，未产生有效结论",
                        "next_action": "查看技术详情后重试；如重复出现请提交缺陷",
                        "cause_event_seq": None,
                        "technical_detail": {"traceback": traceback.format_exc(limit=8)},
                    }})
            except Exception:
                log.exception("failed to persist crash state")
        finally:
            log.info('Worker 结束处理 run_id=%s elapsed_ms=%s',
                     run_id, round((time.monotonic() - started) * 1000))
            self._current_run_id = None


def _default_executor() -> Callable[[str], None]:
    from . import single_round

    return single_round.execute_run


_worker: Worker | None = None
_lock = threading.Lock()


def get_worker() -> Worker:
    global _worker
    with _lock:
        if _worker is None:
            _worker = Worker()
        return _worker


def start_worker() -> Worker:
    worker = get_worker()
    worker.start()
    return worker


def stop_worker() -> None:
    if _worker:
        _worker.stop()
