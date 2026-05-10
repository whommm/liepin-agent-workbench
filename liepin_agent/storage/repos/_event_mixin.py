"""Auto-generated mixin for SQLiteStore refactoring."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ...domain.dedupe import build_candidate_dedupe_key
from ...domain.models import CandidateDetail, CandidateSummary, MatchResult, SearchPlan
from ...domain.states import CandidateStatus, RoundStatus, SessionStatus


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def from_json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default

class _EventMixin:
    """Mixin providing event repository functionality."""
    def add_event(
        self,
        session_id: str,
        round_id: Optional[str],
        event_type: str,
        title: str,
        message: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_events (
                    id, session_id, round_id, event_type, title, message,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    round_id,
                    event_type,
                    title,
                    message,
                    to_json(payload or {}),
                    now_text(),
                ),
            )
            connection.execute(
                "UPDATE search_sessions SET updated_at = ? WHERE id = ?",
                (now_text(), session_id),
            )
        return event_id


    def list_events(self, session_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_events
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = from_json(item.get("payload_json"), {})
            result.append(item)
        return result


    def save_decision(
        self,
        session_id: str,
        round_id: Optional[str],
        decision_type: str,
        action: str,
        input_snapshot: Dict[str, Any],
        decision: Dict[str, Any],
        reason: str = "",
        risk: str = "",
    ) -> str:
        decision_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_decisions (
                    id, session_id, round_id, decision_type, action,
                    input_snapshot_json, decision_json, reason, risk, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    session_id,
                    round_id,
                    decision_type,
                    action,
                    to_json(input_snapshot),
                    to_json(decision),
                    reason,
                    risk,
                    now_text(),
                ),
            )
        return decision_id

