"""Tests for the shared RPM rate limiter."""

from __future__ import annotations

import threading
import time

import pytest

from liepin_agent.services import rate_limiter
from liepin_agent.services.rate_limiter import RateLimiter, reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_global():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def test_token_bucket_allows_burst_then_throttles():
    lim = RateLimiter(rpm=5, burst=3, cooldown_seconds=0)
    # burst of 3 acquires immediately
    t0 = time.monotonic()
    for _ in range(3):
        assert lim.acquire(timeout=0.1)
    assert time.monotonic() - t0 < 0.05
    # 4th must wait ~ (1 / (5/60)) = 12s for next token -> times out quickly
    assert lim.acquire(timeout=0.2) is False


def test_trigger_cooldown_blocks_all_callers():
    lim = RateLimiter(rpm=60, burst=1, cooldown_seconds=0.05)
    # consume the single token
    assert lim.acquire(timeout=0.1)
    # trigger a 50ms cooldown; even though a new token would arrive in 1s,
    # the cooldown gate is what we wait for
    lim.trigger_cooldown(0.05)
    t0 = time.monotonic()
    # cooldown of 0.05s blocks; after it the bucket still empty (no refill yet
    # because rpm=60 -> 1 token/s, 0.05s only yields 0.05 tokens). Configure
    # higher rpm so a token is ready right after cooldown.
    lim.configure(rpm=1000, burst=1)
    assert lim.acquire(timeout=2.0) is True
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.04  # waited at least the cooldown


def test_shared_singleton_is_process_wide():
    a = rate_limiter.get_rate_limiter()
    b = rate_limiter.get_rate_limiter()
    assert a is b
    rate_limiter.configure_rate_limiter(rpm=42, burst=7, cooldown_seconds=3.3)
    snap = a.snapshot()
    assert snap["rpm"] == 42
    assert snap["burst"] == 7
    assert snap["cooldown_seconds"] == pytest.approx(3.3)


def test_concurrent_acquires_are_serialized():
    # 1 token only; many threads should mostly block/timout except one
    lim = RateLimiter(rpm=6, burst=1, cooldown_seconds=0)
    got = []
    lock = threading.Lock()

    def worker():
        if lim.acquire(timeout=0.05):
            with lock:
                got.append(threading.current_thread().name)

    threads = [threading.Thread(target=worker, name=f"w{i}") for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # exactly one thread got the token (burst=1, and 0.05s is far less than
    # the 10s needed for the next refill)
    assert len(got) == 1


def test_cooldown_only_moves_forward():
    lim = RateLimiter(rpm=60, burst=5, cooldown_seconds=0.2)
    lim.trigger_cooldown(0.2)
    snap1 = lim.snapshot()
    assert snap1["cooldown_remaining"] > 0
    # a shorter cooldown must not shorten the existing one
    lim.trigger_cooldown(0.001)
    snap2 = lim.snapshot()
    assert snap2["cooldown_remaining"] >= snap1["cooldown_remaining"] - 0.01
