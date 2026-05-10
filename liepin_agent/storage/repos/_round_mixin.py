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

class _RoundMixin:
    """Mixin providing round repository functionality."""
    def create_round(
        self,
        session_id: str,
        round_index: int,
        plan: SearchPlan,
        criteria_version_id: str = "",
    ) -> str:
        round_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO search_rounds (
                    id, session_id, round_index, status, query, position_filter,
                    scope, match_mode, filters_json, intent, criteria_version_id,
                    search_hypothesis_type, search_hypothesis_text, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    session_id,
                    int(round_index),
                    RoundStatus.PLANNED.value,
                    plan.query,
                    plan.position_filter,
                    plan.scope,
                    plan.match_mode,
                    to_json(plan.filters),
                    plan.intent,
                    criteria_version_id or "",
                    plan.search_hypothesis_type,
                    plan.search_hypothesis_text,
                    now_text(),
                ),
            )
        return round_id


    def update_round(
        self,
        round_id: str,
        status: Optional[str] = None,
        round_type: Optional[str] = None,
        raw_count: Optional[int] = None,
        deduped_count: Optional[int] = None,
        prequalified_count: Optional[int] = None,
        detail_fetch_count: Optional[int] = None,
        matched_count: Optional[int] = None,
        ab_count: Optional[int] = None,
        mark_finished: bool = False,
    ) -> None:
        fields = []
        params: List[Any] = []
        for column, value in [
            ("status", status),
            ("round_type", round_type),
            ("raw_count", raw_count),
            ("deduped_count", deduped_count),
            ("prequalified_count", prequalified_count),
            ("detail_fetch_count", detail_fetch_count),
            ("matched_count", matched_count),
            ("ab_count", ab_count),
        ]:
            if value is not None:
                fields.append("{} = ?".format(column))
                params.append(value)
        if mark_finished:
            fields.append("finished_at = ?")
            params.append(now_text())
        if not fields:
            return
        params.append(round_id)
        with self.connect() as connection:
            connection.execute(
                "UPDATE search_rounds SET {} WHERE id = ?".format(", ".join(fields)),
                params,
            )


    def list_rounds(self, session_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM search_rounds
                WHERE session_id = ?
                ORDER BY round_index
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]


