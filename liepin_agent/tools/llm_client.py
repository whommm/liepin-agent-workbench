"""LLM client supporting both OpenAI and Anthropic API formats."""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from ..services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


class LLMClientError(Exception):
    pass


class AuthError(LLMClientError):
    pass


class NetworkError(LLMClientError):
    pass


class TimeoutError(LLMClientError):
    pass


class QuotaExceededError(LLMClientError):
    """Upstream per-minute (RPM) quota exhausted — HTTP 429 rpm exhausted."""
    pass


class LLMClient:
    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
        timeout: int = 300,
        provider: str = "openai",
        max_retries: int = 2,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        rpm_limit: int = 5,
        rpm_burst: int = 5,
        rpm_cooldown_seconds: float = 12.0,
    ):
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model_name = model_name or "deepseek-v4-flash"
        self.timeout = int(timeout or 300)
        self.provider = (provider or "openai").lower()
        self.max_retries = max(0, int(max_retries))
        self.max_tokens = max(1, int(max_tokens))
        self.temperature = float(temperature)
        self._client: Optional[OpenAI] = None
        self._anthropic_client = None
        # Shared process-wide RPM limiter. SenseNova deepseek-v4-flash 实测 5 RPM
        # (令牌桶容量 5，每 12s 补 1 个令牌)，超过即 429 rpm exhausted。
        self._rate_limiter = get_rate_limiter()
        self._rate_limiter.configure(
            rpm=max(1, int(rpm_limit)),
            burst=max(1, int(rpm_burst)),
            cooldown_seconds=max(0.0, float(rpm_cooldown_seconds)),
        )

    def chat(self, prompt: str, system_message: str = "") -> str:
        logger.info(
            "LLMClient.chat START: model=%s provider=%s prompt_len=%s",
            self.model_name,
            self.provider,
            len(prompt),
        )
        started = time.monotonic()
        try:
            result = self._execute_with_retry(self._chat_once, prompt, system_message)
            logger.info(
                "LLMClient.chat OK: model=%s elapsed=%.1fs result_len=%s",
                self.model_name,
                time.monotonic() - started,
                len(result),
            )
            return result
        except Exception as exc:
            logger.error(
                "LLMClient.chat FAILED: model=%s elapsed=%.1fs error=%s",
                self.model_name,
                time.monotonic() - started,
                exc,
            )
            raise

    def test_connection(self) -> dict:
        started = time.monotonic()
        try:
            content = self.chat(
                "请只回复 ok，用于连接测试。",
                system_message="你是连接测试助手。",
            )
        except LLMClientError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "model": self.model_name,
                "api_base_url": self.api_base_url,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        return {
            "ok": True,
            "error": "",
            "model": self.model_name,
            "api_base_url": self.api_base_url,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "sample": content[:80],
        }

    @property
    def configured(self) -> bool:
        return bool(self.api_base_url and self.api_key and self.model_name)

    @property
    def _is_reasoning_model(self) -> bool:
        name = self.model_name.lower()
        return any(item in name for item in ("reasoner", "thinking", "k1", "coding"))

    def _get_openai_client(self) -> OpenAI:
        if not self.configured:
            raise AuthError("请先配置 API Base URL、API Key 和模型名称。")
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base_url,
                timeout=self.timeout,
                # Retries are owned by _execute_with_retry so the configured
                # attempt count is predictable across providers.
                max_retries=0,
            )
        return self._client

    def _get_anthropic_client(self):
        if not self.configured:
            raise AuthError("请先配置 API Base URL、API Key 和模型名称。")
        if self._anthropic_client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise LLMClientError(
                    "当前环境未安装 anthropic 包，无法使用 Anthropic API 格式。"
                    "请运行: pip install anthropic"
                ) from exc
            self._anthropic_client = Anthropic(
                api_key=self.api_key,
                base_url=self.api_base_url,
                timeout=self.timeout,
                max_retries=0,
            )
        return self._anthropic_client

    def _chat_once(self, prompt: str, system_message: str = "") -> str:
        if self.provider == "anthropic":
            return self._chat_once_anthropic(prompt, system_message)
        return self._chat_once_openai(prompt, system_message)

    def _chat_once_openai(self, prompt: str, system_message: str = "") -> str:
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if not self._is_reasoning_model:
            kwargs["temperature"] = self.temperature
        logger.debug("_chat_once_openai: calling create with model=%s", self.model_name)
        started = time.monotonic()
        response = self._get_openai_client().chat.completions.create(**kwargs)
        elapsed = time.monotonic() - started
        logger.debug(
            "_chat_once_openai: response received in %.1fs choices=%s",
            elapsed,
            len(response.choices) if response.choices else 0,
        )
        if not response.choices:
            raise LLMClientError("API 返回为空。")
        return self._clean_content(response.choices[0].message.content or "")

    def _chat_once_anthropic(self, prompt: str, system_message: str = "") -> str:
        client = self._get_anthropic_client()
        kwargs = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_message:
            kwargs["system"] = system_message
        if not self._is_reasoning_model:
            kwargs["temperature"] = self.temperature
        logger.debug("_chat_once_anthropic: calling create with model=%s", self.model_name)
        started = time.monotonic()
        response = client.messages.create(**kwargs)
        elapsed = time.monotonic() - started
        logger.debug(
            "_chat_once_anthropic: response received in %.1fs content_blocks=%s",
            elapsed,
            len(response.content) if response.content else 0,
        )
        if not response.content:
            raise LLMClientError("API 返回为空。")
        return self._clean_content(response.content[0].text or "")

    def _execute_with_retry(self, func, *args, **kwargs):
        # 两条独立的重试预算：
        # - 普通错误：最多 self.max_retries 次（指数退避）
        # - 429 限流：最多 max_429_retries 次，靠限流器冷却+令牌自然等待，
        #   预算独立，避免被小的 max_retries 提前掐断。
        max_429_retries = max(self.max_retries, 3)
        rate_429_retries = 0
        normal_retries = 0
        last_error = None
        while True:
            # 限流 gate：阻塞直到拿到令牌且冷却期结束，避免连续撞 429。
            acquired = self._rate_limiter.acquire(timeout=float(self.timeout))
            if not acquired:
                logger.warning(
                    "_execute_with_retry: rate limiter acquire timed out after %ss",
                    self.timeout,
                )
                raise TimeoutError("等待 RPM 令牌超时，上游限流持续。")
            logger.debug(
                "_execute_with_retry: token acquired, 429_retries=%s normal_retries=%s",
                rate_429_retries,
                normal_retries,
            )
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                translated = self._translate_exception(exc)
                logger.warning("_execute_with_retry: failed: %s", translated)
                if isinstance(translated, AuthError):
                    raise translated
                last_error = translated
                if isinstance(translated, QuotaExceededError):
                    if rate_429_retries >= max_429_retries:
                        logger.error(
                            "_execute_with_retry: 429 retries exhausted (%s)",
                            rate_429_retries,
                        )
                        raise translated
                    rate_429_retries += 1
                    # 触发全局冷却，本线程和其他线程的下一次 acquire 都会等到冷却结束。
                    self._rate_limiter.trigger_cooldown()
                    continue
                # 普通错误：指数退避
                if normal_retries >= self.max_retries:
                    logger.error("_execute_with_retry: normal retries exhausted (%s)", normal_retries)
                    raise translated
                normal_retries += 1
                sleep_sec = 1.0 * (2 ** (normal_retries - 1))
                logger.info("_execute_with_retry: sleeping %.1fs before retry", sleep_sec)
                time.sleep(sleep_sec)

    @staticmethod
    def _clean_content(content: str) -> str:
        content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL)
        return re.sub(r"\n{3,}", "\n\n", content).strip()

    @staticmethod
    def _translate_exception(exc: Exception) -> LLMClientError:
        # OpenAI exceptions
        if isinstance(exc, RateLimitError):
            # 上游 RPM 配额耗尽（SenseNova 返回 429 + message:"rpm exhausted"）
            return QuotaExceededError("API 请求被限流: {}".format(exc))
        if isinstance(exc, AuthenticationError):
            return AuthError("API 密钥无效或账户余额不足。")
        if isinstance(exc, APITimeoutError):
            return TimeoutError("请求超时。")
        if isinstance(exc, APIConnectionError):
            return NetworkError("无法连接到大模型服务。")
        if isinstance(exc, APIError):
            # 兜底：按状态码识别 429（某些代理/SDK 分支可能不抛 RateLimitError）
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            if status == 429:
                return QuotaExceededError("API 请求被限流: {}".format(exc))
            return LLMClientError("API 请求失败: {}".format(exc))
        # Anthropic exceptions (best-effort via module names to avoid hard import)
        exc_module = type(exc).__module__
        exc_name = type(exc).__name__
        if "anthropic" in exc_module:
            if exc_name == "AuthenticationError":
                return AuthError("API 密钥无效或账户余额不足。")
            if exc_name in ("APITimeoutError", "TimeoutError"):
                return TimeoutError("请求超时。")
            if exc_name == "APIConnectionError":
                return NetworkError("无法连接到大模型服务。")
            if exc_name == "RateLimitError":
                return QuotaExceededError("API 请求被限流: {}".format(exc))
            if "APIError" in exc_name:
                status = getattr(exc, "status_code", None)
                if status == 429:
                    return QuotaExceededError("API 请求被限流: {}".format(exc))
                return LLMClientError("API 请求失败: {}".format(exc))
        if isinstance(exc, LLMClientError):
            return exc
        return LLMClientError("未知错误: {}".format(exc))
