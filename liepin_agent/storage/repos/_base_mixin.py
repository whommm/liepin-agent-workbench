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

class _BaseMixin:
    """Mixin providing base repository functionality."""
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


