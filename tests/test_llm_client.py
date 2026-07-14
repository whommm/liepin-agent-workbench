from types import SimpleNamespace

from liepin_agent.tools import llm_client as llm_module
from liepin_agent.tools.llm_client import LLMClient


class CallRecorder:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def build_client(**overrides):
    kwargs = {
        "api_base_url": "https://api.example/v1",
        "api_key": "secret",
        "model_name": "demo-model",
        "max_retries": 3,
        "max_tokens": 1234,
        "temperature": 0.65,
    }
    kwargs.update(overrides)
    return LLMClient(**kwargs)


def test_openai_request_uses_configured_generation_settings():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    )
    recorder = CallRecorder(response)
    api = SimpleNamespace(chat=SimpleNamespace(completions=recorder))
    client = build_client(provider="openai")
    client._get_openai_client = lambda: api

    result = client._chat_once_openai("prompt", "system")

    assert result == "ok"
    assert recorder.kwargs["max_tokens"] == 1234
    assert recorder.kwargs["temperature"] == 0.65


def test_anthropic_request_uses_configured_generation_settings():
    response = SimpleNamespace(content=[SimpleNamespace(text="ok")])
    recorder = CallRecorder(response)
    api = SimpleNamespace(messages=recorder)
    client = build_client(provider="anthropic")
    client._get_anthropic_client = lambda: api

    result = client._chat_once_anthropic("prompt", "system")

    assert result == "ok"
    assert recorder.kwargs["max_tokens"] == 1234
    assert recorder.kwargs["temperature"] == 0.65


def test_reasoning_models_keep_configured_tokens_but_omit_temperature():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    )
    recorder = CallRecorder(response)
    api = SimpleNamespace(chat=SimpleNamespace(completions=recorder))
    client = build_client(provider="openai", model_name="demo-thinking-model")
    client._get_openai_client = lambda: api

    client._chat_once_openai("prompt")

    assert recorder.kwargs["max_tokens"] == 1234
    assert "temperature" not in recorder.kwargs


def test_retry_count_uses_configured_max_retries(monkeypatch):
    client = build_client(max_retries=2)
    attempts = []
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: None)

    def flaky_call():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("temporary failure")
        return "ok"

    assert client._execute_with_retry(flaky_call) == "ok"
    assert len(attempts) == 3
