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

class _MetricsMixin:
    """Mixin providing metrics repository functionality."""
    def session_efficiency_metrics(self, session_id: str) -> Dict[str, Any]:
        session = self.get_session(session_id) or {}
        with self.connect() as connection:
            raw = connection.execute(
                "SELECT COALESCE(SUM(raw_count), 0) AS n FROM search_rounds WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            rounds = connection.execute(
                "SELECT COUNT(*) AS n FROM search_rounds WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            unique_candidates = connection.execute(
                "SELECT COUNT(*) AS n FROM candidate_summaries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            details = connection.execute(
                """
                SELECT COUNT(*) AS n
                FROM candidate_details d
                JOIN candidate_summaries c ON c.id = d.candidate_id
                WHERE c.session_id = ? AND d.capture_status = 'success'
                """,
                (session_id,),
            ).fetchone()
            matched = connection.execute(
                "SELECT COUNT(*) AS n FROM match_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            ab = connection.execute(
                """
                SELECT COUNT(*) AS n FROM match_results
                WHERE session_id = ? AND UPPER(COALESCE(tier, '')) IN ('A', 'B')
                """,
                (session_id,),
            ).fetchone()
            manual = connection.execute(
                """
                SELECT COUNT(*) AS n FROM agent_events
                WHERE session_id = ? AND event_type IN (
                    'criteria_confirmed', 'manual_stop', 'criteria_draft'
                )
                """,
                (session_id,),
            ).fetchone()
        detail_count = int(details["n"] if details else 0)
        round_count = int(rounds["n"] if rounds else 0)
        ab_count = int(ab["n"] if ab else 0)
        runtime_minutes = 0
        try:
            start = session.get("started_at")
            end = session.get("finished_at") or now_text()
            if start:
                runtime_minutes = round(
                    (
                        datetime.strptime(str(end), "%Y-%m-%d %H:%M:%S")
                        - datetime.strptime(str(start), "%Y-%m-%d %H:%M:%S")
                    ).total_seconds()
                    / 60,
                    2,
                )
        except ValueError:
            runtime_minutes = 0
        return {
            "total_runtime_minutes": runtime_minutes,
            "search_round_count": round_count,
            "raw_candidate_count": int(raw["n"] if raw else 0),
            "unique_candidate_count": int(unique_candidates["n"] if unique_candidates else 0),
            "detail_fetch_count": detail_count,
            "matched_count": int(matched["n"] if matched else 0),
            "ab_count": ab_count,
            "ab_per_detail_fetch": round(ab_count / detail_count, 3) if detail_count else 0,
            "ab_per_round": round(ab_count / round_count, 3) if round_count else 0,
            "detail_fetch_to_ab_rate": round(ab_count / detail_count, 3) if detail_count else 0,
            "manual_intervention_count": int(manual["n"] if manual else 0),
            "status": session.get("status") or "",
        }


    def search_hypothesis_metrics(self, session_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    COALESCE(search_hypothesis_type, '') AS search_hypothesis_type,
                    COALESCE(search_hypothesis_text, '') AS search_hypothesis_text,
                    COUNT(*) AS round_count,
                    COALESCE(SUM(raw_count), 0) AS raw_count,
                    COALESCE(SUM(detail_fetch_count), 0) AS detail_fetch_count,
                    COALESCE(SUM(ab_count), 0) AS ab_count
                FROM search_rounds
                WHERE session_id = ?
                GROUP BY search_hypothesis_type, search_hypothesis_text
                ORDER BY round_count DESC
                """,
                (session_id,),
            ).fetchall()
            source_rows = connection.execute(
                """
                SELECT
                    COALESCE(search_hypothesis_type, '') AS search_hypothesis_type,
                    COALESCE(search_hypothesis_text, '') AS search_hypothesis_text,
                    COUNT(DISTINCT candidate_id) AS unique_count,
                    SUM(CASE WHEN card_decision = 'noise' THEN 1 ELSE 0 END) AS noise_count,
                    COUNT(*) - COUNT(DISTINCT candidate_id) AS duplicate_count
                FROM candidate_sources
                WHERE session_id = ?
                GROUP BY search_hypothesis_type, search_hypothesis_text
                """,
                (session_id,),
            ).fetchall()
        by_key = {
            (
                row["search_hypothesis_type"],
                row["search_hypothesis_text"],
            ): dict(row)
            for row in source_rows
        }
        result = []
        for row in rows:
            item = dict(row)
            extras = by_key.get(
                (item.get("search_hypothesis_type"), item.get("search_hypothesis_text")),
                {},
            )
            item["unique_count"] = int(extras.get("unique_count") or 0)
            item["noise_count"] = int(extras.get("noise_count") or 0)
            item["duplicate_count"] = int(extras.get("duplicate_count") or 0)
            result.append(item)
        return result


