"""配置管理模块（支持 Pydantic 校验）"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator


API_ENV_NAMES = {
    "api_base_url": "LIEPIN_AGENT_API_BASE_URL",
    "api_key": "LIEPIN_AGENT_API_KEY",
    "model_name": "LIEPIN_AGENT_MODEL_NAME",
    "timeout": "LIEPIN_AGENT_TIMEOUT",
}

BACKEND_API_ENV_NAMES = {
    "api_base_url": "LIEPIN_AGENT_BACKEND_API_BASE_URL",
    "api_key": "LIEPIN_AGENT_BACKEND_API_KEY",
    "model_name": "LIEPIN_AGENT_BACKEND_MODEL_NAME",
}


class AppConfig(BaseModel):
    """应用配置数据类（Pydantic 校验）"""

    # 默认/前端 LLM 配置（Agent Brain：规划、观察、复盘等）
    api_base_url: str = ""
    api_key: str = ""
    api_key_env: str = "LIEPIN_AGENT_API_KEY"
    model_name: str = "deepseek-chat"

    # 后端 LLM 配置（Matcher：候选人匹配等重任务）
    # 留空时自动 fallback 到默认配置
    backend_api_base_url: str = ""
    backend_api_key: str = ""
    backend_model_name: str = ""

    tavily_api_key: str = ""
    timeout: int = Field(default=120, ge=1, le=3600)
    theme: str = "light"
    liepin_browser_channel: str = "msedge"
    liepin_browser_headless: bool = False
    liepin_browser_profile_dir: str = "browser_profile/liepin"
    greeting_template: str = ""
    auto_greeting_enabled: bool = False
    debug_snapshots_enabled: bool = False

    @field_validator("timeout", mode="before")
    @classmethod
    def _coerce_timeout(cls, v):
        if v is None or v == "":
            return 120
        return int(v)


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            self.config_path = self._get_default_config_path()
        else:
            self.config_path = config_path
        self.env_path = os.path.join(os.path.dirname(self.config_path), ".env")
        self.config = self._load_config()

    def _get_default_config_path(self) -> str:
        """获取默认配置文件路径"""
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        return os.path.join(base_dir, "config.json")

    def _load_config(self) -> AppConfig:
        """从文件加载配置"""
        data = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                if isinstance(file_data, dict):
                    data = file_data
            except (json.JSONDecodeError, TypeError, OSError):
                pass
        config = AppConfig.model_validate(data)
        self._apply_env_config(config)
        return config

    def save_config(self) -> bool:
        """保存配置到文件"""
        try:
            self._save_env_config()
            data = self.config.model_dump()
            # API settings live in .env. Keep config.json for non-sensitive UI
            # and browser options so restarting does not require retyping keys.
            data["api_base_url"] = ""
            data["api_key"] = ""
            data["model_name"] = ""
            data["timeout"] = AppConfig.model_fields["timeout"].default
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

    def llm_connection_specs(self) -> Dict[str, Dict[str, object]]:
        """Return resolved Agent Brain and Matcher LLM connection specs."""
        default_spec = {
            "api_base_url": self.config.api_base_url,
            "api_key": self.config.api_key,
            "model_name": self.config.model_name or "deepseek-chat",
            "timeout": int(self.config.timeout or 120),
            "source": self.config_source_summary("default"),
        }
        backend_spec = {
            "api_base_url": self.config.backend_api_base_url or self.config.api_base_url,
            "api_key": self.config.backend_api_key or self.config.api_key,
            "model_name": self.config.backend_model_name
            or self.config.model_name
            or "deepseek-chat",
            "timeout": int(self.config.timeout or 120),
            "source": self.config_source_summary("backend"),
        }
        return {"default": default_spec, "backend": backend_spec}

    def config_source_summary(self, profile: str = "default") -> Dict[str, str]:
        env_values = self._read_env_file()
        if profile == "backend":
            return {
                "api_base_url": self._value_source(
                    BACKEND_API_ENV_NAMES["api_base_url"],
                    env_values,
                    bool(self.config.backend_api_base_url),
                    fallback="default",
                ),
                "api_key": self._value_source(
                    BACKEND_API_ENV_NAMES["api_key"],
                    env_values,
                    bool(self.config.backend_api_key),
                    fallback="default",
                ),
                "model_name": self._value_source(
                    BACKEND_API_ENV_NAMES["model_name"],
                    env_values,
                    bool(self.config.backend_model_name),
                    fallback="default",
                ),
            }
        return {
            "api_base_url": self._value_source(
                API_ENV_NAMES["api_base_url"], env_values, bool(self.config.api_base_url)
            ),
            "api_key": self._value_source(
                self.config.api_key_env or API_ENV_NAMES["api_key"],
                env_values,
                bool(self.config.api_key),
            ),
            "model_name": self._value_source(
                API_ENV_NAMES["model_name"], env_values, bool(self.config.model_name)
            ),
            "timeout": self._value_source(
                API_ENV_NAMES["timeout"], env_values, bool(self.config.timeout)
            ),
        }

    def test_llm_connection(self, profile: str = "default") -> Dict[str, object]:
        from ..tools.llm_client import LLMClient

        specs = self.llm_connection_specs()
        spec = specs["backend" if profile == "backend" else "default"]
        client = LLMClient(
            str(spec.get("api_base_url") or ""),
            str(spec.get("api_key") or ""),
            str(spec.get("model_name") or ""),
            int(spec.get("timeout") or 120),
        )
        result = client.test_connection()
        result["profile"] = "backend" if profile == "backend" else "default"
        result["source"] = spec.get("source") or {}
        return result

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

        # Backend LLM env overrides
        backend_url = env_values.get(BACKEND_API_ENV_NAMES["api_base_url"], "")
        backend_key = env_values.get(BACKEND_API_ENV_NAMES["api_key"], "")
        backend_model = env_values.get(BACKEND_API_ENV_NAMES["model_name"], "")
        config.backend_api_base_url = backend_url or config.backend_api_base_url
        config.backend_api_key = backend_key or config.backend_api_key
        config.backend_model_name = backend_model or config.backend_model_name

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

        config.backend_api_base_url = os.environ.get(
            BACKEND_API_ENV_NAMES["api_base_url"], config.backend_api_base_url
        )
        config.backend_api_key = os.environ.get(
            BACKEND_API_ENV_NAMES["api_key"], config.backend_api_key
        )
        config.backend_model_name = os.environ.get(
            BACKEND_API_ENV_NAMES["model_name"], config.backend_model_name
        )

    @staticmethod
    def _value_source(
        env_name: str,
        env_values: dict,
        has_config_value: bool,
        fallback: str = "default",
    ) -> str:
        if os.environ.get(env_name, ""):
            return "os_env:{}".format(env_name)
        if env_values.get(env_name, ""):
            return ".env:{}".format(env_name)
        if has_config_value:
            return "config.json"
        return fallback

    def _save_env_config(self) -> None:
        env_values = self._read_env_file()
        env_values[API_ENV_NAMES["api_base_url"]] = self.config.api_base_url or ""
        env_values[self.config.api_key_env or API_ENV_NAMES["api_key"]] = (
            self.config.api_key or ""
        )
        env_values[API_ENV_NAMES["model_name"]] = self.config.model_name or ""
        env_values[API_ENV_NAMES["timeout"]] = str(int(self.config.timeout or 120))
        env_values[BACKEND_API_ENV_NAMES["api_base_url"]] = self.config.backend_api_base_url or ""
        env_values[BACKEND_API_ENV_NAMES["api_key"]] = self.config.backend_api_key or ""
        env_values[BACKEND_API_ENV_NAMES["model_name"]] = self.config.backend_model_name or ""
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
            BACKEND_API_ENV_NAMES["api_base_url"],
            BACKEND_API_ENV_NAMES["api_key"],
            BACKEND_API_ENV_NAMES["model_name"],
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
