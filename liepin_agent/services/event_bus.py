"""Small in-process event bus for UI refresh and runtime updates."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List


class EventBus:
    def __init__(self):
        self._subscribers: List[Callable[[str, Dict[str, Any]], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, event_type: str, payload: Dict[str, Any] | None = None) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event_type, payload or {})
            except Exception:
                continue

