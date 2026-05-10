"""Concurrent queue for LLM candidate matching tasks with cancellation support."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Iterable, List, Optional


class MatchQueue:
    def __init__(self, max_workers: int = 3):
        self.max_workers = max(1, int(max_workers or 3))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="MatchQueue",
        )

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        return self._executor.submit(fn, *args, **kwargs)

    @staticmethod
    def wait_min_results(
        futures: Iterable[Future],
        min_results: int,
        timeout_seconds: int,
        cancel_event: Optional[threading.Event] = None,
    ) -> int:
        future_list: List[Future] = list(futures or [])
        if not future_list:
            return 0
        deadline = time.time() + max(1, int(timeout_seconds or 1))
        min_results = max(1, int(min_results or 1))
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                for f in future_list:
                    f.cancel()
                return sum(1 for item in future_list if item.done())
            completed = sum(1 for item in future_list if item.done())
            if completed >= min_results or completed >= len(future_list):
                return completed
            time.sleep(0.2)
        return sum(1 for item in future_list if item.done())

    @staticmethod
    def wait_all(
        futures: Iterable[Future],
        timeout_seconds: int = 600,
        cancel_event: Optional[threading.Event] = None,
    ) -> int:
        future_list = list(futures or [])
        deadline = time.time() + max(1, int(timeout_seconds or 1))
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                for f in future_list:
                    f.cancel()
                return sum(1 for item in future_list if item.done())
            completed = sum(1 for item in future_list if item.done())
            if completed >= len(future_list):
                return completed
            time.sleep(0.2)
        return sum(1 for item in future_list if item.done())

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
