"""SQLite persistence for the Agent workbench."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ..domain.dedupe import build_candidate_dedupe_key
from ..domain.models import CandidateDetail, CandidateSummary, MatchResult, SearchPlan
from ..domain.states import CandidateStatus, RoundStatus, SessionStatus


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


class SQLiteStore:
    """Repository facade around the workbench SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS search_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    jd_text TEXT NOT NULL,
                    user_notes TEXT,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    max_rounds INTEGER NOT NULL,
                    max_detail_fetches INTEGER NOT NULL,
                    max_runtime_minutes INTEGER NOT NULL,
                    target_ab_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS match_criteria (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    criteria_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_rounds (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    round_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    query TEXT NOT NULL,
                    position_filter TEXT,
                    scope TEXT,
                    match_mode TEXT,
                    filters_json TEXT,
                    intent TEXT,
                    round_type TEXT,
                    criteria_version_id TEXT,
                    search_hypothesis_type TEXT,
                    search_hypothesis_text TEXT,
                    raw_count INTEGER NOT NULL DEFAULT 0,
                    deduped_count INTEGER NOT NULL DEFAULT 0,
                    prequalified_count INTEGER NOT NULL DEFAULT 0,
                    detail_fetch_count INTEGER NOT NULL DEFAULT 0,
                    matched_count INTEGER NOT NULL DEFAULT 0,
                    ab_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS candidate_summaries (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    round_id TEXT NOT NULL REFERENCES search_rounds(id) ON DELETE CASCADE,
                    profile_url TEXT,
                    dedupe_key TEXT NOT NULL,
                    name TEXT,
                    age TEXT,
                    current_title TEXT,
                    current_company TEXT,
                    city TEXT,
                    work_years TEXT,
                    education TEXT,
                    summary_text TEXT,
                    result_index INTEGER NOT NULL DEFAULT 0,
                    pre_score INTEGER NOT NULL DEFAULT 0,
                    pre_score_reasons_json TEXT,
                    card_decision TEXT,
                    card_signals_json TEXT,
                    card_risks_json TEXT,
                    card_reason TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, dedupe_key)
                );

                CREATE TABLE IF NOT EXISTS candidate_details (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidate_summaries(id) ON DELETE CASCADE,
                    resume_text TEXT,
                    resume_summary TEXT,
                    raw_payload_json TEXT,
                    capture_status TEXT NOT NULL,
                    error_message TEXT,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS match_results (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidate_summaries(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    round_id TEXT NOT NULL REFERENCES search_rounds(id) ON DELETE CASCADE,
                    tier TEXT,
                    core_met_count INTEGER,
                    core_total INTEGER,
                    dealbreaker_hit INTEGER,
                    summary TEXT,
                    risks TEXT,
                    recommendation TEXT,
                    detail TEXT,
                    raw_response TEXT,
                    status TEXT NOT NULL,
                    criteria_version_id TEXT,
                    evidence_json TEXT,
                    unknowns_json TEXT,
                    questions_json TEXT,
                    confidence TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS match_criteria_versions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    keywords_text TEXT,
                    requirements_text TEXT,
                    source_jd_text TEXT,
                    source_user_notes TEXT,
                    ai_raw_response_json TEXT,
                    created_by TEXT NOT NULL,
                    confirmed_by TEXT,
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    UNIQUE(session_id, version)
                );

                CREATE TABLE IF NOT EXISTS candidate_sources (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES candidate_summaries(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    round_id TEXT NOT NULL REFERENCES search_rounds(id) ON DELETE CASCADE,
                    criteria_version_id TEXT,
                    query TEXT,
                    position_filter TEXT,
                    search_hypothesis_type TEXT,
                    search_hypothesis_text TEXT,
                    result_index INTEGER NOT NULL DEFAULT 0,
                    card_decision TEXT,
                    card_signals_json TEXT,
                    card_risks_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    round_id TEXT,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_decisions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    round_id TEXT,
                    decision_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    input_snapshot_json TEXT,
                    decision_json TEXT,
                    reason TEXT,
                    risk TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    round_id TEXT,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    message TEXT,
                    error_message TEXT,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON search_sessions(updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_rounds_session
                ON search_rounds(session_id, round_index);

                CREATE INDEX IF NOT EXISTS idx_candidates_session
                ON candidate_summaries(session_id, pre_score DESC);

                CREATE INDEX IF NOT EXISTS idx_events_session
                ON agent_events(session_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_criteria_versions_session
                ON match_criteria_versions(session_id, version DESC);

                CREATE INDEX IF NOT EXISTS idx_candidate_sources_candidate
                ON candidate_sources(candidate_id, created_at);
                """
            )
            self._ensure_column(connection, "search_rounds", "criteria_version_id", "TEXT")
            self._ensure_column(connection, "search_rounds", "search_hypothesis_type", "TEXT")
            self._ensure_column(connection, "search_rounds", "search_hypothesis_text", "TEXT")
            self._ensure_column(connection, "candidate_summaries", "card_decision", "TEXT")
            self._ensure_column(connection, "candidate_summaries", "card_signals_json", "TEXT")
            self._ensure_column(connection, "candidate_summaries", "card_risks_json", "TEXT")
            self._ensure_column(connection, "candidate_summaries", "card_reason", "TEXT")
            self._ensure_column(connection, "match_results", "criteria_version_id", "TEXT")
            self._ensure_column(connection, "match_results", "evidence_json", "TEXT")
            self._ensure_column(connection, "match_results", "unknowns_json", "TEXT")
            self._ensure_column(connection, "match_results", "questions_json", "TEXT")
            self._ensure_column(connection, "match_results", "confidence", "TEXT")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, column_type: str
    ) -> None:
        rows = connection.execute("PRAGMA table_info({})".format(table)).fetchall()
        existing = {row["name"] for row in rows}
        if column not in existing:
            connection.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, column_type)
            )

    def create_session(
        self,
        title: str,
        jd_text: str,
        user_notes: str = "",
        mode: str = "单步",
        max_rounds: int = 6,
        max_detail_fetches: int = 50,
        max_runtime_minutes: int = 90,
        target_ab_count: int = 10,
    ) -> str:
        session_id = uuid.uuid4().hex
        ts = now_text()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO search_sessions (
                    id, title, jd_text, user_notes, status, mode, max_rounds,
                    max_detail_fetches, max_runtime_minutes, target_ab_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    title or "未命名岗位",
                    jd_text,
                    user_notes,
                    SessionStatus.CRITERIA_DRAFT.value,
                    mode or "单步",
                    int(max_rounds or 6),
                    int(max_detail_fetches or 50),
                    int(max_runtime_minutes or 90),
                    int(target_ab_count or 10),
                    ts,
                    ts,
                ),
            )
        self.add_event(
            session_id,
            None,
            "session_created",
            "新建寻访任务",
            "任务已创建，等待 Agent 开始理解岗位。",
            {"title": title, "mode": mode},
        )
        return session_id

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                       COALESCE((SELECT COUNT(*) FROM candidate_summaries c WHERE c.session_id = s.id), 0) AS candidate_count,
                       COALESCE((SELECT COUNT(*) FROM candidate_details d JOIN candidate_summaries c ON c.id = d.candidate_id WHERE c.session_id = s.id AND d.capture_status = 'success'), 0) AS detail_count,
                       COALESCE((SELECT COUNT(*) FROM match_results m WHERE m.session_id = s.id AND UPPER(COALESCE(m.tier, '')) IN ('A', 'B')), 0) AS ab_count
                FROM search_sessions s
                ORDER BY s.updated_at DESC, s.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_interrupted_sessions(self) -> int:
        """Mark sessions left running by a previous app process as paused."""
        interrupted_statuses = (
            SessionStatus.RUNNING.value,
            SessionStatus.WAITING_APPROVAL.value,
        )
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title FROM search_sessions
                WHERE status IN (?, ?)
                """,
                interrupted_statuses,
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE search_sessions
                    SET status = ?, updated_at = ?, error_message = ?
                    WHERE id = ?
                    """,
                    (
                        SessionStatus.PAUSED.value,
                        now_text(),
                        "上次程序关闭时任务仍在运行，已自动暂停。可点击继续恢复后续轮次。",
                        row["id"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO agent_events (
                        id, session_id, round_id, event_type, title, message,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        row["id"],
                        None,
                        "interrupted",
                        "运行中断已恢复",
                        "检测到上次关闭时任务仍在运行，已自动转为暂停状态。点击继续可从下一轮恢复。",
                        to_json({"previous_status": "running"}),
                        now_text(),
                    ),
                )
        return len(rows)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM search_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, session_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM search_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM search_sessions WHERE id = ?", (session_id,))
        return True

    def update_session_status(
        self, session_id: str, status: str, error_message: str = ""
    ) -> None:
        ts = now_text()
        updates = {
            "status": status,
            "updated_at": ts,
            "error_message": error_message,
        }
        if status == SessionStatus.RUNNING.value:
            started_sql = ", started_at = COALESCE(started_at, ?)"
            params = [updates["status"], updates["updated_at"], updates["error_message"], ts, session_id]
        elif status in (
            SessionStatus.COMPLETED.value,
            SessionStatus.FAILED.value,
            SessionStatus.CANCELLED.value,
        ):
            started_sql = ", finished_at = COALESCE(finished_at, ?)"
            params = [updates["status"], updates["updated_at"], updates["error_message"], ts, session_id]
        else:
            started_sql = ""
            params = [updates["status"], updates["updated_at"], updates["error_message"], session_id]

        with self.connect() as connection:
            connection.execute(
                """
                UPDATE search_sessions
                SET status = ?, updated_at = ?, error_message = ?
                {}
                WHERE id = ?
                """.format(started_sql),
                params,
            )

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
        return item

    def get_latest_criteria(self, session_id: str) -> Dict[str, Any]:
        version = self.get_latest_criteria_version(session_id, "confirmed")
        if version:
            keywords = self._keywords_from_text(version.get("keywords_text") or "")
            return {
                "criteria_version_id": version.get("id") or "",
                "version": version.get("version") or 0,
                "keywords_text": version.get("keywords_text") or "",
                "requirements_text": version.get("requirements_text") or "",
                "core_terms": keywords,
                "negative_terms": [],
                "hard_requirements": [],
                "city_scope": [],
                "position_filter": "",
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
                   mr.summary AS match_summary,
                   mr.risks AS match_risks,
                   mr.evidence_json AS evidence_json,
                   mr.unknowns_json AS unknowns_json,
                   mr.questions_json AS questions_json,
                   mr.confidence AS confidence,
                   mr.criteria_version_id AS criteria_version_id,
                   CASE WHEN d.id IS NULL THEN '' ELSE d.capture_status END AS detail_capture_status
            FROM candidate_summaries c
            LEFT JOIN (
                SELECT m1.*
                FROM match_results m1
                INNER JOIN (
                    SELECT candidate_id, MAX(created_at) AS created_at
                    FROM match_results
                    GROUP BY candidate_id
                ) latest
                ON latest.candidate_id = m1.candidate_id
                AND latest.created_at = m1.created_at
            ) mr ON mr.candidate_id = c.id
            LEFT JOIN candidate_details d ON d.candidate_id = c.id
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
                    capture_status, error_message, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detail_id,
                    detail.candidate_id,
                    detail.resume_text,
                    detail.resume_summary,
                    to_json(detail.raw_payload),
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
