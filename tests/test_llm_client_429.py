"""Tests for LLMClient 429 handling and RPM rate limiting integration."""

from __future__ import annotations

import time

import pytest
from openai import RateLimitError

from liepin_agent.services import rate_limiter
from liepin_agent.services.rate_limiter import reset_rate_limiter
from liepin_agent.tools.llm_client import LLMClient, QuotaExceededError


@pytest.fixture(autouse=True)
def _reset_global():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def _make_rate_limit_error():
    # Construct a minimal RateLimitError-like exception. The openai SDK's
    # constructor signature varies across versions, so we build via __new__
    # and set only the attributes _translate_exception inspects.
    exc = RateLimitError.__new__(RateLimitError)
    exc.message = "rpm exhausted"
    exc.body = {"error": {"message": "rpm exhausted", "code": "8"}}
    exc.status_code = 429
    return exc


def test_translate_recognizes_rate_limit_error():
    err = LLMClient._translate_exception(_make_rate_limit_error())
    assert isinstance(err, QuotaExceededError)


def test_429_triggers_cooldown_and_retries_until_success(monkeypatch):
    # rpm=60 -> 1 token/sec, burst=1, cooldown 0.05s so the test stays fast.
    client = LLMClient(
        api_base_url="https://example.test/v1",
        api_key="sk-x",
        model_name="m",
        timeout=5,
        max_retries=1,
        rpm_limit=60,
        rpm_burst=1,
        rpm_cooldown_seconds=0.05,
    )
    calls = []

    def fake_chat_once(prompt, system_message=""):
        calls.append(time.monotonic())
        if len(calls) < 3:
            raise _make_rate_limit_error()
        return "OK"

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)

    result = client.chat("ping")
    assert result == "OK"
    # first call hit 429, then cooldown, then 429 again, then success
    assert len(calls) == 3
    snap = client._rate_limiter.snapshot()
    # bucket consumed down
    assert snap["tokens"] <= 1.0


def test_429_exhausts_retries_raises_quota_error(monkeypatch):
    client = LLMClient(
        api_base_url="https://example.test/v1",
        api_key="sk-x",
        model_name="m",
        timeout=5,
        max_retries=0,  # 429 path still gets up to max(max_retries,3)=3 retries
        rpm_limit=1000,
        rpm_burst=10,
        rpm_cooldown_seconds=0.0,
    )
    monkeypatch.setattr(
        client,
        "_chat_once",
        lambda prompt, system_message="": (_ for _ in ()).throw(
            _make_rate_limit_error()
        ),
    )
    with pytest.raises(QuotaExceededError):
        client.chat("ping")


def test_non_429_error_uses_normal_backoff(monkeypatch):
    client = LLMClient(
        api_base_url="https://example.test/v1",
        api_key="sk-x",
        model_name="m",
        timeout=5,
        max_retries=2,
        rpm_limit=1000,
        rpm_burst=10,
        rpm_cooldown_seconds=0.0,
    )
    from liepin_agent.tools.llm_client import LLMClientError

    calls = []

    def fake_chat_once(prompt, system_message=""):
        calls.append(1)
        raise LLMClientError("boom")

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)
    # keep the backoff sleeps instant
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(LLMClientError):
        client.chat("ping")
    assert len(calls) == 3  # 1 + 2 retries


def test_auth_error_not_retried(monkeypatch):
    from openai import AuthenticationError

    client = LLMClient(
        api_base_url="https://example.test/v1",
        api_key="sk-x",
        model_name="m",
        timeout=5,
        max_retries=3,
        rpm_limit=1000,
        rpm_burst=10,
        rpm_cooldown_seconds=0.0,
    )

    def fake_chat_once(prompt, system_message=""):
        raise AuthenticationError.__new__(AuthenticationError)

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)
    from liepin_agent.tools.llm_client import AuthError

    with pytest.raises(AuthError):
        client.chat("ping")
