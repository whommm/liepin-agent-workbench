"""配置管理模块"""

import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import Optional


API_ENV_NAMES = {
    "api_base_url": "LIEPIN_AGENT_API_BASE_URL",
    "api_key": "LIEPIN_AGENT_API_KEY",
    "model_name": "LIEPIN_AGENT_MODEL_NAME",
    "timeout": "LIEPIN_AGENT_TIMEOUT",
}


@dataclass
class AppConfig:
    """应用配置数据类"""

    api_base_url: str = ""
    api_key: str = ""
    api_key_env: str = "LIEPIN_AGENT_API_KEY"
    model_name: str = "deepseek-chat"
    tavily_api_key: str = ""
    timeout: int = 120
    theme: str = "light"
    liepin_browser_channel: str = "msedge"  # 默认使用 Edge 浏览器
    liepin_browser_headless: bool = False
    liepin_browser_profile_dir: str = "browser_profile/liepin"
    greeting_template: str = ""
    debug_snapshots_enabled: bool = False


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            # 配置文件放在程序同级目录
            self.config_path = self._get_default_config_path()
        else:
            self.config_path = config_path
        self.env_path = os.path.join(os.path.dirname(self.config_path), ".env")
        self.config = self._load_config()

    def _get_default_config_path(self) -> str:
        """获取默认配置文件路径"""
        # 支持打包后的路径
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        return os.path.join(base_dir, "config.json")

    def _load_config(self) -> AppConfig:
        """从文件加载配置"""
        config = AppConfig()
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                valid_fields = {f.name for f in AppConfig.__dataclass_fields__.values()}
                filtered = {k: v for k, v in data.items() if k in valid_fields}
                config = AppConfig(**filtered)
            except (json.JSONDecodeError, TypeError, KeyError):
                config = AppConfig()
        self._apply_env_config(config)
        return config

    def save_config(self) -> bool:
        """保存配置到文件"""
        try:
            self._save_env_config()
            data = asdict(self.config)
            # API settings live in .env. Keep config.json for non-sensitive UI
            # and browser options so restarting does not require retyping keys.
            data["api_base_url"] = ""
            data["api_key"] = ""
            data["model_name"] = ""
            data["timeout"] = AppConfig.timeout
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except IOError:
            return False

    def update(self, **kwargs) -> None:
        """更新配置项"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def _apply_env_config(self, config: AppConfig) -> None:
        """Load API settings from project .env, then let OS env override them."""
        env_values = self._read_env_file()
        api_base_url = env_values.get(API_ENV_NAMES["api_base_url"], "")
        api_key = env_values.get(config.api_key_env or API_ENV_NAMES["api_key"], "")
        model_name = env_values.get(API_ENV_NAMES["model_name"], "")
        timeout = env_values.get(API_ENV_NAMES["timeout"], "")

        config.api_base_url = api_base_url or config.api_base_url
        config.api_key = api_key or config.api_key
        config.model_name = model_name or config.model_name
        if timeout:
            try:
                config.timeout = int(timeout)
            except ValueError:
                pass

        config.api_base_url = os.environ.get(
            API_ENV_NAMES["api_base_url"], config.api_base_url
        )
        config.api_key = os.environ.get(
            config.api_key_env or API_ENV_NAMES["api_key"], config.api_key
        )
        config.model_name = os.environ.get(
            API_ENV_NAMES["model_name"], config.model_name
        )
        env_timeout = os.environ.get(API_ENV_NAMES["timeout"], "")
        if env_timeout:
            try:
                config.timeout = int(env_timeout)
            except ValueError:
                pass

    def _save_env_config(self) -> None:
        env_values = self._read_env_file()
        env_values[API_ENV_NAMES["api_base_url"]] = self.config.api_base_url or ""
        env_values[self.config.api_key_env or API_ENV_NAMES["api_key"]] = (
            self.config.api_key or ""
        )
        env_values[API_ENV_NAMES["model_name"]] = self.config.model_name or ""
        env_values[API_ENV_NAMES["timeout"]] = str(int(self.config.timeout or 120))
        self._write_env_file(env_values)

    def _read_env_file(self) -> dict:
        if not os.path.exists(self.env_path):
            return {}
        result = {}
        try:
            with open(self.env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, value = stripped.split("=", 1)
                    result[key.strip()] = self._unquote_env_value(value.strip())
        except IOError:
            return {}
        return result

    def _write_env_file(self, values: dict) -> None:
        os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
        ordered_keys = [
            API_ENV_NAMES["api_base_url"],
            self.config.api_key_env or API_ENV_NAMES["api_key"],
            API_ENV_NAMES["model_name"],
            API_ENV_NAMES["timeout"],
        ]
        written = set()
        lines = ["# Liepin Agent API configuration"]
        for key in ordered_keys:
            lines.append(
                "{}={}".format(key, self._quote_env_value(values.get(key, "")))
            )
            written.add(key)
        for key in sorted(k for k in values if k not in written):
            lines.append(
                "{}={}".format(key, self._quote_env_value(values.get(key, "")))
            )
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    @staticmethod
    def _quote_env_value(value: object) -> str:
        text = str(value or "")
        if (
            not text
            or any(ch.isspace() for ch in text)
            or any(ch in text for ch in ('"', "#", "="))
        ):
            return '"{}"'.format(text.replace("\\", "\\\\").replace('"', '\\"'))
        return text

    @staticmethod
    def _unquote_env_value(value: str) -> str:
        text = value or ""
        if len(text) >= 2 and text[0] == text[-1] == '"':
            text = text[1:-1]
            return text.replace('\\"', '"').replace("\\\\", "\\")
        return text
