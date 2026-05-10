"""OpenAI-compatible LLM client for the real matcher."""

from __future__ import annotations

import re
import time
from typing import Optional

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI


class LLMClientError(Exception):
    pass


class AuthError(LLMClientError):
    pass


class NetworkError(LLMClientError):
    pass


class TimeoutError(LLMClientError):
    pass


class LLMClient:
    MAX_RETRIES = 2

    def __init__(self, api_base_url: str, api_key: str, model_name: str, timeout: int = 120):
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model_name = model_name or "deepseek-chat"
        self.timeout = int(timeout or 120)
        self._client: Optional[OpenAI] = None

    def chat(self, prompt: str, system_message: str = "") -> str:
        return self._execute_with_retry(self._chat_once, prompt, system_message)

    @property
    def configured(self) -> bool:
        return bool(self.api_base_url and self.api_key and self.model_name)

    @property
    def _is_reasoning_model(self) -> bool:
        name = self.model_name.lower()
        return any(item in name for item in ("reasoner", "thinking", "k1", "coding"))

    def _get_client(self) -> OpenAI:
        if not self.configured:
            raise AuthError("请先配置 API Base URL、API Key 和模型名称。")
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base_url,
                timeout=self.timeout,
            )
        return self._client

    def _chat_once(self, prompt: str, system_message: str = "") -> str:
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": 4096,
        }
        if not self._is_reasoning_model:
            kwargs["temperature"] = 0.2
        response = self._get_client().chat.completions.create(**kwargs)
        if not response.choices:
            raise LLMClientError("API 返回为空。")
        return self._clean_content(response.choices[0].message.content or "")

    def _execute_with_retry(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                translated = self._translate_exception(exc)
                if isinstance(translated, AuthError):
                    raise translated
                last_error = translated
                if attempt < self.MAX_RETRIES:
                    time.sleep(1.0 * (2**attempt))
        raise last_error or LLMClientError("请求失败")

    @staticmethod
    def _clean_content(content: str) -> str:
        content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL)
        return re.sub(r"\n{3,}", "\n\n", content).strip()

    @staticmethod
    def _translate_exception(exc: Exception) -> LLMClientError:
        if isinstance(exc, AuthenticationError):
            return AuthError("API 密钥无效或账户余额不足。")
        if isinstance(exc, APITimeoutError):
            return TimeoutError("请求超时。")
        if isinstance(exc, APIConnectionError):
            return NetworkError("无法连接到大模型服务。")
        if isinstance(exc, APIError):
            return LLMClientError("API 请求失败: {}".format(exc))
        if isinstance(exc, LLMClientError):
            return exc
        return LLMClientError("未知错误: {}".format(exc))

