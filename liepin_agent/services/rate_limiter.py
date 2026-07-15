"""Process-wide rate limiter for upstream LLM APIs.

Upstream providers (e.g. SenseNova) enforce a per-minute request quota (RPM)
that is independent of concurrency: even with only a few in-flight requests,
sending too many requests within 60s triggers HTTP 429 ``rpm exhausted``.

This module provides a shared token-bucket rate limiter plus a global
"cooldown" gate so that every LLM call across the process acquires a token
before hitting the network, and every 429 forces all callers to pause until
the bucket has refilled enough tokens.

Usage::

    from liepin_agent.services.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    if limiter.acquire(timeout=120):
        # ... perform the API call ...
    # on 429:
    limiter.trigger_cooldown()  # blocks all callers for cooldown_seconds
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe token bucket with a global cooldown gate.

    The bucket holds at most ``burst`` tokens, refilling at ``rpm / 60``
    tokens per second. ``acquire`` blocks until one token is available and
    the cooldown deadline has passed. ``trigger_cooldown`` pushes the
    deadline forward (e.g. after a 429) so all callers wait together.
    """

    def __init__(
        self,
        rpm: int = 5,
        burst: Optional[int] = None,
        cooldown_seconds: float = 12.0,
    ) -> None:
        self.rpm = max(1, int(rpm))
        self.burst = max(1, int(burst if burst is not None else rpm))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._cond = threading.Condition()
        self._tokens = float(self.burst)
        self._last_refill = time.monotonic()
        self._cooldown_until = 0.0
        logger.info(
            "RateLimiter initialized: rpm=%s burst=%s cooldown=%.1fs",
            self.rpm,
            self.burst,
            self.cooldown_seconds,
        )

    def configure(
        self,
        rpm: Optional[int] = None,
        burst: Optional[int] = None,
        cooldown_seconds: Optional[float] = None,
    ) -> None:
        with self._cond:
            if rpm is not None:
                self.rpm = max(1, int(rpm))
            if burst is not None:
                self.burst = max(1, int(burst))
            if cooldown_seconds is not None:
                self.cooldown_seconds = max(0.0, float(cooldown_seconds))
            if self._tokens > self.burst:
                self._tokens = float(self.burst)
            self._cond.notify_all()
        logger.info(
            "RateLimiter reconfigured: rpm=%s burst=%s cooldown=%.1fs",
            self.rpm,
            self.burst,
            self.cooldown_seconds,
        )

    def _refill_locked(self, now: float) -> None:
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        rate = self.rpm / 60.0
        if rate > 0:
            self._tokens = min(float(self.burst), self._tokens + elapsed * rate)
        self._last_refill = now

    def acquire(self, timeout: float = 300.0) -> bool:
        """Block until a token is available and the cooldown has passed.

        Returns True if a token was acquired, False if ``timeout`` elapsed.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    return False
                cd_remaining = self._cooldown_until - now
                if cd_remaining > 0:
                    wait_s = min(cd_remaining, deadline - now)
                    logger.debug(
                        "RateLimiter.acquire: cooling down %.1fs remaining",
                        cd_remaining,
                    )
                    self._cond.wait(timeout=wait_s)
                    continue
                self._refill_locked(now)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                rate = self.rpm / 60.0
                need = 1.0 - self._tokens
                wait_s = need / rate if rate > 0 else 1.0
                wait_s = min(wait_s, deadline - now)
                self._cond.wait(timeout=wait_s)

    def trigger_cooldown(self, seconds: Optional[float] = None) -> None:
        """Push the global cooldown deadline forward so all callers pause.

        ``seconds`` defaults to ``self.cooldown_seconds``. The new deadline
        only moves forward (never shortens an existing cooldown).
        """
        secs = self.cooldown_seconds if seconds is None else float(seconds)
        secs = max(0.0, secs)
        with self._cond:
            new_until = time.monotonic() + secs
            if new_until > self._cooldown_until:
                self._cooldown_until = new_until
                logger.info(
                    "RateLimiter: cooldown triggered for %.1fs (429 backoff)",
                    secs,
                )
            self._cond.notify_all()

    def snapshot(self) -> dict:
        with self._cond:
            self._refill_locked(time.monotonic())
            return {
                "rpm": self.rpm,
                "burst": self.burst,
                "tokens": round(self._tokens, 3),
                "cooldown_remaining": max(0.0, self._cooldown_until - time.monotonic()),
                "cooldown_seconds": self.cooldown_seconds,
            }


_GLOBAL_LIMITER: Optional[RateLimiter] = None
_GLOBAL_LOCK = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """Return the process-wide shared RateLimiter (lazy singleton)."""
    global _GLOBAL_LIMITER
    if _GLOBAL_LIMITER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_LIMITER is None:
                _GLOBAL_LIMITER = RateLimiter()
    return _GLOBAL_LIMITER


def configure_rate_limiter(
    rpm: Optional[int] = None,
    burst: Optional[int] = None,
    cooldown_seconds: Optional[float] = None,
) -> RateLimiter:
    """Configure the shared RateLimiter (creates it if needed)."""
    limiter = get_rate_limiter()
    limiter.configure(rpm=rpm, burst=burst, cooldown_seconds=cooldown_seconds)
    return limiter


def reset_rate_limiter() -> None:
    """Drop the shared limiter (mainly for tests)."""
    global _GLOBAL_LIMITER
    with _GLOBAL_LOCK:
        _GLOBAL_LIMITER = None
