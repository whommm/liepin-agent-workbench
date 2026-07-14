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
                    unknowns_json, questions_json, confidence, prompt_version,
                    model_name, model_config_hash, input_hash, resume_hash,
                    match_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    result.prompt_version,
                    result.model_name,
                    result.model_config_hash,
                    result.input_hash,
                    result.resume_hash,
                    int(result.match_score or 0),
                    now_text(),
                ),
            )
        if result.status in {"failed", "needs_review"}:
            candidate_status = CandidateStatus.DEFERRED.value
        elif result.status == "completed" and (result.tier or "").upper() in ("A", "B"):
            candidate_status = CandidateStatus.SHORTLISTED.value
        elif result.status == "completed":
            candidate_status = CandidateStatus.REJECTED.value
        else:
            candidate_status = CandidateStatus.DEFERRED.value
        self.update_candidate_status(result.candidate_id, candidate_status)
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
        criteria = self.get_latest_criteria_version(session_id, "confirmed")
        criteria_version_id = str((criteria or {}).get("id") or "")
        if not criteria_version_id:
            return 0
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM match_results m
                WHERE m.session_id = ?
                  AND m.criteria_version_id = ?
                  AND m.status = 'completed'
                  AND UPPER(COALESCE(m.tier, '')) IN ('A', 'B')
                  AND m.id = (
                      SELECT m2.id
                      FROM match_results m2
                      WHERE m2.session_id = m.session_id
                        AND m2.candidate_id = m.candidate_id
                        AND m2.criteria_version_id = m.criteria_version_id
                      ORDER BY m2.created_at DESC, m2.rowid DESC
                      LIMIT 1
                  )
                """,
                (session_id, criteria_version_id),
            ).fetchone()
        return int(row["count"] if row else 0)


    def find_match_result(
        self,
        candidate_id: str,
        criteria_version_id: str,
        statuses: Optional[Iterable[str]] = None,
        prompt_version: str = "",
        model_config_hash: str = "",
        input_hash: str = "",
        resume_hash: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Return the latest result matching the supplied cache identity."""
        if not candidate_id or not criteria_version_id:
            return None
        allowed = [str(item) for item in (statuses or []) if str(item)]
        sql = """
            SELECT * FROM match_results
            WHERE candidate_id = ? AND criteria_version_id = ?
        """
        params: List[Any] = [candidate_id, criteria_version_id]
        if allowed:
            placeholders = ",".join("?" for _ in allowed)
            sql += " AND status IN ({})".format(placeholders)
            params.extend(allowed)
        if prompt_version:
            sql += " AND prompt_version = ?"
            params.append(prompt_version)
        if model_config_hash:
            sql += " AND model_config_hash = ?"
            params.append(model_config_hash)
        if input_hash:
            sql += " AND input_hash = ?"
            params.append(input_hash)
        if resume_hash:
            sql += " AND resume_hash = ?"
            params.append(resume_hash)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
        if not row:
            return None
        item = dict(row)
        item["matched_evidence"] = from_json(item.get("evidence_json"), [])
        item["missing_or_unclear"] = from_json(item.get("unknowns_json"), [])
        item["questions_to_verify"] = from_json(item.get("questions_json"), [])
        return item


    def count_fetched_details(self, session_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT d.candidate_id) AS count
                FROM candidate_details d
                JOIN candidate_summaries c ON c.id = d.candidate_id
                WHERE c.session_id = ? AND d.capture_status = 'success'
                """,
                (session_id,),
            ).fetchone()
        return int(row["count"] if row else 0)


