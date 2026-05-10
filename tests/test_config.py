import json

from liepin_agent.core.config import ConfigManager


def test_api_config_persists_to_project_env_not_config_json(tmp_path):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(
        api_base_url="https://api.example.com/v1",
        api_key="secret-key",
        model_name="demo-model",
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

    reloaded = ConfigManager(str(config_path))

    assert reloaded.config.api_base_url == "https://api.example.com/v1"
    assert reloaded.config.api_key == "secret-key"
    assert reloaded.config.model_name == "demo-model"


def test_os_env_overrides_project_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    manager = ConfigManager(str(config_path))
    manager.update(api_key="file-secret")
    assert manager.save_config() is True

    monkeypatch.setenv("LIEPIN_AGENT_API_KEY", "env-secret")
    reloaded = ConfigManager(str(config_path))

    assert reloaded.config.api_key == "env-secret"
