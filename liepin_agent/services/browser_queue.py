"""Serial queue for browser-like tasks with cooperative cancellation."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from typing import Any, Callable, Optional


class BrowserTaskTimeoutError(RuntimeError):
    """Raised when a serialized browser task does not finish in time."""


class BrowserTaskCancelledError(RuntimeError):
    """Raised when a browser task is cancelled via cancel_event."""


class BrowserQueue:
    """Run browser actions one at a time.

    All Playwright operations go through this queue so one browser session is
    never manipulated concurrently.
    """

    def __init__(self, timeout_seconds: Optional[int] = 180):
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="BrowserQueue"
        )

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        return self._executor.submit(fn, *args, **kwargs)

    def run(
        self,
        fn: Callable[..., Any],
        *args,
        cancel_event: Optional[threading.Event] = None,
        **kwargs,
    ) -> Any:
        future = self.submit(fn, *args, **kwargs)
        try:
            if cancel_event is not None:
                return self._result_with_cancellation(future, cancel_event)
            return future.result(timeout=self.timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            name = getattr(fn, "__name__", fn.__class__.__name__)
            raise BrowserTaskTimeoutError(
                "浏览器任务 {} 超过 {} 秒未返回，可能卡在猎聘页面操作或 Playwright 调用".format(
                    name, self.timeout_seconds
                )
            ) from exc

    def _result_with_cancellation(
        self, future: Future, cancel_event: threading.Event
    ) -> Any:
        """Poll the future with short intervals so we can react to cancellation."""
        if self.timeout_seconds is None:
            deadline = None
        else:
            import time

            deadline = time.time() + self.timeout_seconds
        interval = 1.0
        while True:
            if cancel_event.is_set():
                future.cancel()
                raise BrowserTaskCancelledError("浏览器任务已取消")
            remaining = (
                (deadline - time.time())
                if deadline is not None
                else interval
            )
            if remaining is not None and remaining <= 0:
                raise TimeoutError()
            try:
                return future.result(timeout=min(interval, remaining))
            except TimeoutError:
                continue

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
