"""Preflight checks for the real desktop runtime."""

from __future__ import annotations

from pathlib import Path

from .core.config import ConfigManager
from .storage.sqlite_store import SQLiteStore
from .tools.real_liepin import RealLiepinTool
from .tools.real_matcher import RealMatchService
from .agent.brain import LLMAgentBrain


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    store = SQLiteStore(str(root / "liepin_agent_workbench.db"))
    config = ConfigManager()
    print("database=ok path={}".format(store.db_path))
    print("config_path={}".format(config.config_path))
    print("api_configured={}".format(bool(config.config.api_base_url and config.config.api_key)))
    print("browser_profile={}".format(config.config.liepin_browser_profile_dir))
    _ = RealLiepinTool(config)
    _ = RealMatchService.from_config(config)
    _ = LLMAgentBrain.from_config(config)
    print("real_runtime_imports=ok")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

