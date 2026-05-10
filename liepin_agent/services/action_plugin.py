"""Action plugin interface for extending browser capabilities.

Example: registering a custom greeting action that reuses the same
BrowserQueue without contending with the main search flow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List


class ActionPlugin(ABC):
    """Base class for browser actions that can be registered dynamically."""

    name: str = ""

    @abstractmethod
    def run(self, browser_manager: Any, **kwargs: Any) -> Any:
        """Execute the plugin action.

        Parameters
        ----------
        browser_manager:
            The live LiepinBrowserManager instance.
        **kwargs:
            Action-specific arguments.
        """
        raise NotImplementedError


class PluginRegistry:
    """Simple registry for action plugins."""

    def __init__(self):
        self._plugins: Dict[str, ActionPlugin] = {}

    def register(self, plugin: ActionPlugin) -> None:
        if not plugin.name:
            raise ValueError("Plugin must have a non-empty name")
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)

    def get(self, name: str) -> ActionPlugin:
        if name not in self._plugins:
            raise KeyError(f"Plugin '{name}' not found")
        return self._plugins[name]

    def list_plugins(self) -> List[str]:
        return list(self._plugins.keys())

    def run(self, name: str, browser_manager: Any, **kwargs: Any) -> Any:
        plugin = self.get(name)
        return plugin.run(browser_manager, **kwargs)
