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

class _CandidateMixin:
    """Mixin providing candidate repository functionality."""
    def save_candidate_summary(self, candidate: CandidateSummary) -> str:
        candidate.id = candidate.id or uuid.uuid4().hex
        candidate.dedupe_key = candidate.dedupe_key or build_candidate_dedupe_key(candidate)
        candidate.status = candidate.status or CandidateStatus.SUMMARY_SEEN.value
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT id FROM candidate_summaries
                WHERE session_id = ? AND dedupe_key = ?
                """,
                (candidate.session_id, candidate.dedupe_key),
            ).fetchone()
            if existing:
                if candidate.profile_url:
                    connection.execute(
                        """
                        UPDATE candidate_summaries
                        SET profile_url = ?
                        WHERE id = ?
                          AND (profile_url IS NULL OR profile_url = '')
                        """,
                        (candidate.profile_url, existing["id"]),
                    )
                return existing["id"]
            connection.execute(
                """
                INSERT INTO candidate_summaries (
                    id, session_id, round_id, profile_url, dedupe_key, name, age,
                    current_title, current_company, city, work_years, education,
                    summary_text, result_index, pre_score, pre_score_reasons_json,
                    card_decision, card_signals_json, card_risks_json, card_reason,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.id,
                    candidate.session_id,
                    candidate.round_id,
                    candidate.profile_url,
                    candidate.dedupe_key,
                    candidate.name,
                    candidate.age,
                    candidate.current_title,
                    candidate.current_company,
                    candidate.city,
                    candidate.work_years,
                    candidate.education,
                    candidate.summary_text,
                    candidate.result_index,
                    int(candidate.pre_score or 0),
                    to_json(candidate.pre_score_reasons),
                    candidate.card_decision,
                    to_json(candidate.card_signals),
                    to_json(candidate.card_risks),
                    candidate.card_reason,
                    candidate.status,
                    now_text(),
                ),
            )
        return candidate.id


    def update_candidate_profile_url(self, candidate_id: str, profile_url: str) -> None:
        if not candidate_id or not profile_url:
            return
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE candidate_summaries
                SET profile_url = ?
                WHERE id = ?
                """,
                (profile_url, candidate_id),
            )


    def list_candidates(
        self, session_id: str, round_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = """
            SELECT c.*,
                   mr.tier AS match_tier,
                   mr.match_score AS match_score,
                   mr.summary AS match_summary,
                   mr.risks AS match_risks,
                   mr.evidence_json AS evidence_json,
                   mr.unknowns_json AS unknowns_json,
                   mr.questions_json AS questions_json,
                    mr.confidence AS confidence,
                    mr.criteria_version_id AS criteria_version_id,
                    CASE WHEN d.id IS NULL THEN '' ELSE d.capture_status END AS detail_capture_status,
                    CASE WHEN d.id IS NULL THEN 0 ELSE COALESCE(d.is_gold_collar, 0) END AS is_gold_collar,
                    COALESCE(d.greeting_status, '') AS greeting_status,
                    COALESCE(d.greeting_message, '') AS greeting_message,
                    COALESCE(d.greeting_error, '') AS greeting_error,
                    COALESCE(d.greeted_at, '') AS greeted_at
            FROM candidate_summaries c
            LEFT JOIN match_results mr
              ON mr.id = (
                  SELECT m2.id
                  FROM match_results m2
                  WHERE m2.candidate_id = c.id
                    AND m2.criteria_version_id = COALESCE(
                        (
                            SELECT cv.id
                            FROM match_criteria_versions cv
                            WHERE cv.session_id = c.session_id
                              AND cv.status = 'confirmed'
                            ORDER BY cv.version DESC
                            LIMIT 1
                        ),
                        ''
                    )
                  ORDER BY
                      m2.created_at DESC,
                      m2.rowid DESC
                  LIMIT 1
              )
            LEFT JOIN candidate_details d
              ON d.id = (
                  SELECT d2.id
                  FROM candidate_details d2
                  WHERE d2.candidate_id = c.id
                  ORDER BY d2.fetched_at DESC, d2.id DESC
                  LIMIT 1
              )
            WHERE c.session_id = ?
        """
        params: List[Any] = [session_id]
        if round_id:
            sql += " AND c.round_id = ?"
            params.append(round_id)
        sql += " ORDER BY c.pre_score DESC, c.result_index ASC"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["pre_score_reasons"] = from_json(item.get("pre_score_reasons_json"), [])
            item["card_signals"] = from_json(item.get("card_signals_json"), [])
            item["card_risks"] = from_json(item.get("card_risks_json"), [])
            item["matched_evidence"] = from_json(item.get("evidence_json"), [])
            item["missing_or_unclear"] = from_json(item.get("unknowns_json"), [])
            item["questions_to_verify"] = from_json(item.get("questions_json"), [])
            result.append(item)
        return result


    def get_candidates_by_ids(self, candidate_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = [item for item in candidate_ids if item]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidate_summaries WHERE id IN ({})".format(placeholders),
                ids,
            ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[item] for item in ids if item in by_id]


    def list_candidate_dedupe_keys(self, session_id: str) -> List[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT dedupe_key
                FROM candidate_summaries
                WHERE session_id = ? AND dedupe_key IS NOT NULL AND dedupe_key != ''
                """,
                (session_id,),
            ).fetchall()
        return [str(row["dedupe_key"]) for row in rows]


    def save_candidate_source(
        self,
        candidate_id: str,
        session_id: str,
        round_id: str,
        criteria_version_id: str,
        plan: SearchPlan,
        result_index: int,
        card_decision: str = "",
        card_signals: Optional[List[str]] = None,
        card_risks: Optional[List[str]] = None,
    ) -> str:
        source_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO candidate_sources (
                    id, candidate_id, session_id, round_id, criteria_version_id,
                    query, position_filter, search_hypothesis_type,
                    search_hypothesis_text, result_index, card_decision,
                    card_signals_json, card_risks_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    candidate_id,
                    session_id,
                    round_id,
                    criteria_version_id or "",
                    plan.query,
                    plan.position_filter,
                    plan.search_hypothesis_type,
                    plan.search_hypothesis_text,
                    int(result_index or 0),
                    card_decision or "",
                    to_json(card_signals or []),
                    to_json(card_risks or []),
                    now_text(),
                ),
            )
        return source_id


    def list_candidate_sources(self, candidate_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM candidate_sources
                WHERE candidate_id = ?
                ORDER BY created_at ASC
                """,
                (candidate_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["card_signals"] = from_json(item.get("card_signals_json"), [])
            item["card_risks"] = from_json(item.get("card_risks_json"), [])
            result.append(item)
        return result


    def update_candidate_status(self, candidate_id: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE candidate_summaries SET status = ? WHERE id = ?",
                (status, candidate_id),
            )


    def save_candidate_detail(self, detail: CandidateDetail) -> str:
        detail_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO candidate_details (
                    id, candidate_id, resume_text, resume_summary, raw_payload_json,
                    is_gold_collar, capture_status, error_message, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detail_id,
                    detail.candidate_id,
                    detail.resume_text,
                    detail.resume_summary,
                    to_json(detail.raw_payload),
                    1 if detail.is_gold_collar else 0,
                    detail.capture_status,
                    detail.error_message,
                    now_text(),
                ),
            )
        self.update_candidate_status(
            detail.candidate_id,
            CandidateStatus.DETAIL_FETCHED.value
            if detail.capture_status == "success"
            else CandidateStatus.DETAIL_FAILED.value,
        )
        return detail_id


    def update_candidate_greeting_status(
        self,
        candidate_id: str,
        status: str,
        message: str = "",
        error: str = "",
    ) -> None:
        if not candidate_id:
            return
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM candidate_details
                WHERE candidate_id = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            if not row:
                return
            connection.execute(
                """
                UPDATE candidate_details
                SET greeting_status = ?, greeting_message = ?, greeting_error = ?, greeted_at = ?
                WHERE id = ?
                """,
                (status or "", message or "", error or "", now_text(), row["id"]),
            )


    def get_candidate_detail(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM candidate_details
                WHERE candidate_id = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
        return dict(row) if row else None


    def get_successful_candidate_detail(
        self, candidate_id: str, min_resume_chars: int = 1
    ) -> Optional[Dict[str, Any]]:
        """Return the latest reusable, non-empty detail for a candidate."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM candidate_details
                WHERE candidate_id = ?
                  AND capture_status = 'success'
                  AND LENGTH(TRIM(COALESCE(resume_text, ''))) >= ?
                ORDER BY fetched_at DESC, rowid DESC
                LIMIT 1
                """,
                (candidate_id, max(1, int(min_resume_chars or 1))),
            ).fetchone()
        return dict(row) if row else None


    def get_successful_detail_candidate_ids(
        self, candidate_ids: Iterable[str], min_resume_chars: int = 1
    ) -> set[str]:
        ids = list(dict.fromkeys(str(item) for item in candidate_ids if item))
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT candidate_id
                FROM candidate_details
                WHERE capture_status = 'success'
                  AND LENGTH(TRIM(COALESCE(resume_text, ''))) >= ?
                  AND candidate_id IN ({})
                """.format(placeholders),
                [max(1, int(min_resume_chars or 1)), *ids],
            ).fetchall()
        return {str(row["candidate_id"]) for row in rows}


