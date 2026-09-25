"""Job queue (spec v2 §4): Atlas enqueues jobs, workers run them.

Phases 0-6 run workers in-process behind this interface; Phase 7 swaps in a real queue
without changing employee code.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Protocol

log = logging.getLogger(__name__)


class JobQueue(Protocol):
    def submit(self, fn: Callable[[], None]) -> None: ...
    def drain(self) -> None: ...


class InlineQueue:
    """Runs each job immediately. Deterministic: used by tests, `wots tick` and trials."""

    def submit(self, fn: Callable[[], None]) -> None:
        fn()

    def drain(self) -> None:
        pass


class ThreadQueue:
    """A small in-process worker pool, used by `wots run`."""

    def __init__(self, workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wots-job")
        self._futures: list[Future] = []

    def submit(self, fn: Callable[[], None]) -> None:
        self._futures = [f for f in self._futures if not f.done()]
        self._futures.append(self._pool.submit(self._safe, fn))

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - job code handles its own failures; this is a last resort
            log.exception("Job crashed outside its own error handling")

    def drain(self) -> None:
        for future in list(self._futures):
            future.result()
        self._futures = []
