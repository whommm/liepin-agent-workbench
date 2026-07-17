import json

from liepin_agent.core.config import ConfigManager


def test_api_config_persists_to_project_env_not_config_json(tmp_path):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(
        api_base_url="https://api.example.com/v1",
        api_key="secret-key",
        model_name="demo-model",
        auto_greeting_enabled=True,
        greeting_template="您好，方便沟通吗？",
    )

    assert manager.save_config() is True
    data = json.loads(config_path.read_text(encoding="utf-8"))
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")

    assert data["api_base_url"] == ""
    assert data["api_key"] == ""
    assert data["model_name"] == ""
    assert "LIEPIN_AGENT_API_BASE_URL=https://api.example.com/v1" in env_text
    assert "LIEPIN_AGENT_API_KEY=secret-key" in env_text
    assert "LIEPIN_AGENT_MODEL_NAME=demo-model" in env_text
    assert data["auto_greeting_enabled"] is True
    assert data["greeting_template"] == "您好，方便沟通吗？"

    reloaded = ConfigManager(str(config_path))

    assert reloaded.config.api_base_url == "https://api.example.com/v1"
    assert reloaded.config.api_key == "secret-key"
    assert reloaded.config.model_name == "demo-model"
    assert reloaded.config.auto_greeting_enabled is True
    assert reloaded.config.greeting_template == "您好，方便沟通吗？"


def test_os_env_overrides_project_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(api_key="file-secret")
    assert manager.save_config() is True

    monkeypatch.setenv("LIEPIN_AGENT_API_KEY", "env-secret")
    reloaded = ConfigManager(str(config_path))

    assert reloaded.config.api_key == "env-secret"


def test_llm_connection_specs_resolve_backend_fallback(tmp_path):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(
        api_base_url="https://api.example.com/v1",
        api_key="secret-key",
        model_name="agent-model",
        backend_model_name="matcher-model",
    )

    specs = manager.llm_connection_specs()

    assert specs["default"]["model_name"] == "agent-model"
    assert specs["backend"]["api_base_url"] == "https://api.example.com/v1"
    assert specs["backend"]["api_key"] == "secret-key"
    assert specs["backend"]["model_name"] == "matcher-model"
    assert specs["backend"]["source"]["api_base_url"] == "default"


def test_llm_connection_specs_resolve_chat_fallback(tmp_path):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(
        api_base_url="https://api.example.com/v1",
        api_key="secret-key",
        model_name="agent-model",
    )

    specs = manager.llm_connection_specs()

    assert specs["chat"]["api_base_url"] == "https://api.example.com/v1"
    assert specs["chat"]["api_key"] == "secret-key"
    assert specs["chat"]["model_name"] == "agent-model"
    assert specs["chat"]["provider"] == "openai"
    assert specs["chat"]["source"]["model_name"] == "default"


def test_chat_config_partial_override_and_env_roundtrip(tmp_path):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(
        api_base_url="https://api.example.com/v1",
        api_key="secret-key",
        model_name="agent-model",
        chat_model_name="chat-pro-model",
    )

    specs = manager.llm_connection_specs()
    assert specs["chat"]["model_name"] == "chat-pro-model"
    assert specs["chat"]["api_base_url"] == "https://api.example.com/v1"
    assert specs["chat"]["source"]["model_name"] == "config.json"

    assert manager.save_config() is True
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LIEPIN_AGENT_CHAT_MODEL_NAME=chat-pro-model" in env_text

    reloaded = ConfigManager(str(config_path))
    assert reloaded.config.chat_model_name == "chat-pro-model"


def test_chat_config_os_env_overrides(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(chat_model_name="file-model")
    assert manager.save_config() is True

    monkeypatch.setenv("LIEPIN_AGENT_CHAT_MODEL_NAME", "env-model")
    reloaded = ConfigManager(str(config_path))

    assert reloaded.config.chat_model_name == "env-model"
