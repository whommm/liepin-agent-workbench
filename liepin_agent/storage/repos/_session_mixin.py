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

class _SessionMixin:
    """Mixin providing session repository functionality."""
    def create_session(
        self,
        title: str,
        jd_text: str,
        user_notes: str = "",
        mode: str = "单步",
        max_rounds: int = 10,
        max_detail_fetches: int = 999,
        max_runtime_minutes: int = 90,
        target_ab_count: int = 999,
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
                    int(max_rounds or 10),
                    int(max_detail_fetches or 999),
                    int(max_runtime_minutes or 90),
                    int(target_ab_count or 999),
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
                       COALESCE((SELECT COUNT(DISTINCT d.candidate_id) FROM candidate_details d JOIN candidate_summaries c ON c.id = d.candidate_id WHERE c.session_id = s.id AND d.capture_status = 'success'), 0) AS detail_count
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
            SessionStatus.USER_DIALOG.value,
        )
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title FROM search_sessions
                WHERE status IN (?, ?, ?)
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


    def set_pending_user_command(self, session_id: str, command: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE search_sessions
                SET pending_user_command = ?, updated_at = ?
                WHERE id = ?
                """,
                (command, now_text(), session_id),
            )

    def consume_pending_user_command(self, session_id: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT pending_user_command FROM search_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            command = row["pending_user_command"] if row else None
            if command:
                connection.execute(
                    """
                    UPDATE search_sessions
                    SET pending_user_command = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now_text(), session_id),
                )
            return command

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


