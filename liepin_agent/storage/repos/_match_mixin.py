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

class _MatchMixin:
    """Mixin providing match repository functionality."""
    def save_match_result(self, result: MatchResult) -> str:
        match_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO match_results (
                    id, candidate_id, session_id, round_id, tier, core_met_count,
                    core_total, dealbreaker_hit, summary, risks, recommendation,
                    detail, raw_response, status, criteria_version_id, evidence_json,
                    unknowns_json, questions_json, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    result.candidate_id,
                    result.session_id,
                    result.round_id,
                    result.tier,
                    result.core_met_count,
                    result.core_total,
                    1 if result.dealbreaker_hit else 0,
                    result.summary,
                    result.risks,
                    result.recommendation,
                    result.detail,
                    result.raw_response,
                    result.status,
                    result.criteria_version_id,
                    to_json(result.matched_evidence),
                    to_json(result.missing_or_unclear),
                    to_json(result.questions_to_verify),
                    result.confidence,
                    now_text(),
                ),
            )
        self.update_candidate_status(
            result.candidate_id,
            CandidateStatus.SHORTLISTED.value
            if (result.tier or "").upper() in ("A", "B")
            else CandidateStatus.REJECTED.value,
        )
        return match_id


    def list_match_results(
        self, session_id: str, round_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM match_results WHERE session_id = ?"
        params: List[Any] = [session_id]
        if round_id:
            sql += " AND round_id = ?"
            params.append(round_id)
        sql += " ORDER BY created_at DESC"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["matched_evidence"] = from_json(item.get("evidence_json"), [])
            item["missing_or_unclear"] = from_json(item.get("unknowns_json"), [])
            item["questions_to_verify"] = from_json(item.get("questions_json"), [])
            result.append(item)
        return result


    def count_ab_matches(self, session_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM match_results
                WHERE session_id = ? AND UPPER(COALESCE(tier, '')) IN ('A', 'B')
                """,
                (session_id,),
            ).fetchone()
        return int(row["count"] if row else 0)


    def count_fetched_details(self, session_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM candidate_details d
                JOIN candidate_summaries c ON c.id = d.candidate_id
                WHERE c.session_id = ? AND d.capture_status = 'success'
                """,
                (session_id,),
            ).fetchone()
        return int(row["count"] if row else 0)


