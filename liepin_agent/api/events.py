"""Thread-safe event bridge from the in-process EventBus to SSE clients."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from datetime import datetime
from typing import Any, Dict, Iterator


class EventBroadcaster:
    def __init__(self):
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def publish(self, event_type: str, payload: Dict[str, Any] | None = None) -> None:
        event = {
            "type": "runtime_event",
            "event_type": event_type,
            "payload": payload or {},
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except Exception:
                continue

    async def stream(self) -> Iterator[str]:
        subscriber: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
        try:
            yield self._format_event({"type": "connected", "event_type": "connected", "payload": {}, "created_at": datetime.now().isoformat(timespec="seconds")})
            while True:
                event = await asyncio.to_thread(subscriber.get)
                yield self._format_event(event)
        finally:
            with self._lock:
                self._subscribers.discard(subscriber)

    @staticmethod
    def _format_event(event: Dict[str, Any]) -> str:
        return "data: {}\n\n".format(json.dumps(event, ensure_ascii=False))
