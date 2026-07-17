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

class _CriteriaMixin:
    """Mixin providing criteria repository functionality."""
    def save_match_criteria(self, session_id: str, criteria: Dict[str, Any]) -> str:
        criteria_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO match_criteria (
                    id, session_id, criteria_json, created_by, confirmed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    criteria_id,
                    session_id,
                    to_json(criteria),
                    "agent",
                    1,
                    now_text(),
                ),
            )
        return criteria_id


    def create_criteria_version(
        self,
        session_id: str,
        keywords_text: str,
        requirements_text: str,
        source_jd_text: str = "",
        source_user_notes: str = "",
        ai_raw_response: Optional[Dict[str, Any]] = None,
        created_by: str = "ai",
        status: str = "draft",
    ) -> str:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM match_criteria_versions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            version = int(row["next_version"] if row else 1)
            criteria_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO match_criteria_versions (
                    id, session_id, version, status, keywords_text,
                    requirements_text, source_jd_text, source_user_notes,
                    ai_raw_response_json, created_by, confirmed_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    criteria_id,
                    session_id,
                    version,
                    status,
                    keywords_text or "",
                    requirements_text or "",
                    source_jd_text or "",
                    source_user_notes or "",
                    to_json(ai_raw_response or {}),
                    created_by or "ai",
                    "human" if status == "confirmed" else "",
                    now_text(),
                ),
            )
            if status == "confirmed":
                connection.execute(
                    """
                    UPDATE match_criteria_versions
                    SET status = 'archived'
                    WHERE session_id = ? AND id <> ? AND status = 'confirmed'
                    """,
                    (session_id, criteria_id),
                )
                connection.execute(
                    """
                    UPDATE match_criteria_versions
                    SET confirmed_at = ?, confirmed_by = ?
                    WHERE id = ?
                    """,
                    (now_text(), "human", criteria_id),
                )
                session_status = SessionStatus.CRITERIA_CONFIRMED.value
            else:
                session_status = SessionStatus.CRITERIA_DRAFT.value
            connection.execute(
                """
                UPDATE search_sessions
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (session_status, now_text(), session_id),
            )
        return criteria_id


    def update_criteria_version(
        self,
        criteria_id: str,
        keywords_text: str,
        requirements_text: str,
        status: str = "draft",
    ) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM match_criteria_versions WHERE id = ?",
                (criteria_id,),
            ).fetchone()
            if not row:
                return
            connection.execute(
                """
                UPDATE match_criteria_versions
                SET keywords_text = ?, requirements_text = ?, status = ?
                WHERE id = ?
                """,
                (keywords_text or "", requirements_text or "", status, criteria_id),
            )
            connection.execute(
                """
                UPDATE search_sessions
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (SessionStatus.CRITERIA_DRAFT.value, now_text(), row["session_id"]),
            )


    def confirm_criteria_version(self, criteria_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM match_criteria_versions WHERE id = ?",
                (criteria_id,),
            ).fetchone()
            if not row:
                return False
            session_id = row["session_id"]
            connection.execute(
                """
                UPDATE match_criteria_versions
                SET status = 'archived'
                WHERE session_id = ? AND id <> ? AND status = 'confirmed'
                """,
                (session_id, criteria_id),
            )
            connection.execute(
                """
                UPDATE match_criteria_versions
                SET status = 'confirmed', confirmed_by = ?, confirmed_at = ?
                WHERE id = ?
                """,
                ("human", now_text(), criteria_id),
            )
            connection.execute(
                """
                UPDATE search_sessions
                SET status = ?, updated_at = ?, error_message = ''
                WHERE id = ?
                """,
                (SessionStatus.CRITERIA_CONFIRMED.value, now_text(), session_id),
            )
        return True


    def get_latest_criteria_version(
        self, session_id: str, status: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT * FROM match_criteria_versions
            WHERE session_id = ?
        """
        params: List[Any] = [session_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY version DESC LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
        if not row:
            return None
        item = dict(row)
        item["ai_raw_response"] = from_json(item.get("ai_raw_response_json"), {})
        item["criteria_items"] = self.list_job_criteria(str(item.get("id") or ""))
        item["personas"] = self.list_candidate_personas(str(item.get("id") or ""))
        return item


    def get_latest_criteria(self, session_id: str) -> Dict[str, Any]:
        version = self.get_latest_criteria_version(session_id, "confirmed")
        if version:
            keywords = self._keywords_from_text(version.get("keywords_text") or "")
            ai_raw = version.get("ai_raw_response") or {}
            if not isinstance(ai_raw, dict):
                ai_raw = {}
            return {
                "criteria_version_id": version.get("id") or "",
                "version": version.get("version") or 0,
                "keywords_text": version.get("keywords_text") or "",
                "requirements_text": version.get("requirements_text") or "",
                "core_terms": keywords,
                "negative_terms": ai_raw.get("negative_terms") or [],
                "hard_requirements": ai_raw.get("hard_requirements") or [],
                "city_scope": ai_raw.get("city_scope") or [],
                "city_requirement": str(ai_raw.get("city_requirement") or "").strip(),
                "position_filter": str(ai_raw.get("position_filter") or "").strip(),
                "selected_direction": str(ai_raw.get("selected_direction") or "").strip(),
                # 性别要求只传给 matcher 作为已确认条件，不自动转成猎聘硬筛选。
                "gender_requirement": str(ai_raw.get("gender_requirement") or "").strip(),
                "criteria_items": version.get("criteria_items") or [],
                "personas": version.get("personas") or [],
            }
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT criteria_json FROM match_criteria
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return from_json(row["criteria_json"], {}) if row else {}


    @staticmethod
    def _keywords_from_text(value: str) -> List[str]:
        result: List[str] = []
        for line in str(value or "").replace("，", "\n").replace("、", "\n").splitlines():
            item = line.strip(" -\t,;；")
            if item and item not in result:
                result.append(item)
        return result


