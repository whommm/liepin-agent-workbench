"""Serial queue for browser-like tasks."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from typing import Any, Callable, Optional


class BrowserTaskTimeoutError(RuntimeError):
    """Raised when a serialized browser task does not finish in time."""


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

    def run(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        future = self.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=self.timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            name = getattr(fn, "__name__", fn.__class__.__name__)
            raise BrowserTaskTimeoutError(
                "浏览器任务 {} 超过 {} 秒未返回，可能卡在猎聘页面操作或 Playwright 调用".format(
                    name, self.timeout_seconds
                )
            ) from exc

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
