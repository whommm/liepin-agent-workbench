"""Concurrent queue for LLM candidate matching tasks with cancellation support."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)


class MatchQueue:
    def __init__(self, max_workers: int = 3):
        self.max_workers = max(1, int(max_workers or 3))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="MatchQueue",
        )
        logger.info("MatchQueue initialized with max_workers=%s", self.max_workers)

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        logger.debug(
            "MatchQueue.submit: %s args=%s kwargs=%s",
            getattr(fn, "__name__", fn),
            args[:2] if args else args,
            list(kwargs.keys()) if kwargs else kwargs,
        )
        future = self._executor.submit(fn, *args, **kwargs)
        logger.debug("MatchQueue.submit: future created id=%s", id(future))
        return future

    @staticmethod
    def wait_min_results(
        futures: Iterable[Future],
        min_results: int,
        timeout_seconds: int,
        cancel_event: Optional[threading.Event] = None,
    ) -> int:
        future_list: List[Future] = list(futures or [])
        if not future_list:
            logger.info("wait_min_results: empty future list, returning 0")
            return 0
        deadline = time.time() + max(1, int(timeout_seconds or 1))
        min_results = max(1, int(min_results or 1))
        logger.info(
            "wait_min_results: waiting for %s/%s futures, timeout=%ss",
            min_results,
            len(future_list),
            int(timeout_seconds or 1),
        )
        report_interval = 5.0
        last_report = time.time()
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                logger.warning("wait_min_results: cancel_event set, cancelling futures")
                for f in future_list:
                    f.cancel()
                completed = sum(1 for item in future_list if item.done())
                logger.info("wait_min_results: cancelled, completed=%s", completed)
                return completed
            completed = sum(1 for item in future_list if item.done())
            if completed >= min_results or completed >= len(future_list):
                logger.info(
                    "wait_min_results: done %s/%s", completed, len(future_list)
                )
                return completed
            if time.time() - last_report >= report_interval:
                logger.info(
                    "wait_min_results: progress %s/%s (%.0fs remaining)",
                    completed,
                    len(future_list),
                    deadline - time.time(),
                )
                last_report = time.time()
            time.sleep(0.2)
        completed = sum(1 for item in future_list if item.done())
        logger.warning(
            "wait_min_results: timeout after %ss, completed=%s/%s",
            int(timeout_seconds or 1),
            completed,
            len(future_list),
        )
        return completed

    @staticmethod
    def wait_all(
        futures: Iterable[Future],
        timeout_seconds: int = 600,
        cancel_event: Optional[threading.Event] = None,
    ) -> int:
        future_list = list(futures or [])
        deadline = time.time() + max(1, int(timeout_seconds or 1))
        logger.info(
            "wait_all: waiting for %s futures, timeout=%ss",
            len(future_list),
            int(timeout_seconds or 1),
        )
        report_interval = 5.0
        last_report = time.time()
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                logger.warning("wait_all: cancel_event set, cancelling futures")
                for f in future_list:
                    f.cancel()
                completed = sum(1 for item in future_list if item.done())
                logger.info("wait_all: cancelled, completed=%s", completed)
                return completed
            completed = sum(1 for item in future_list if item.done())
            if completed >= len(future_list):
                logger.info("wait_all: all %s futures done", completed)
                return completed
            if time.time() - last_report >= report_interval:
                logger.info(
                    "wait_all: progress %s/%s (%.0fs remaining)",
                    completed,
                    len(future_list),
                    deadline - time.time(),
                )
                last_report = time.time()
            time.sleep(0.2)
        completed = sum(1 for item in future_list if item.done())
        logger.warning(
            "wait_all: timeout after %ss, completed=%s/%s",
            int(timeout_seconds or 1),
            completed,
            len(future_list),
        )
        return completed

    def shutdown(self) -> None:
        logger.info("MatchQueue.shutdown called")
        self._executor.shutdown(wait=False, cancel_futures=True)
