"""Project pool queue mixin for SQLiteStore."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from ._base_mixin import now_text


class _PoolMixin:
    """Mixin providing project pool queue functionality."""

    def add_session_to_pool(
        self, session_id: str, order_index: Optional[int] = None
    ) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM project_pool WHERE session_id = ?", (session_id,)
            ).fetchone()
            if existing:
                return
            if order_index is None:
                row = connection.execute(
                    "SELECT COALESCE(MAX(order_index), 0) + 1 FROM project_pool"
                ).fetchone()
                order_index = row[0] if row else 1
            connection.execute(
                """
                INSERT INTO project_pool (id, session_id, order_index, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, session_id, order_index, "queued", now_text()),
            )

    def remove_session_from_pool(self, session_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM project_pool WHERE session_id = ?", (session_id,)
            )

    def list_pool_entries(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*, s.title, s.status AS session_status
                FROM project_pool p
                JOIN search_sessions s ON s.id = p.session_id
                ORDER BY p.order_index ASC, p.created_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_pool_entry(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT p.*, s.title, s.status AS session_status
                FROM project_pool p
                JOIN search_sessions s ON s.id = p.session_id
                WHERE p.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_pool_order(self, session_id: str, order_index: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE project_pool SET order_index = ? WHERE session_id = ?",
                (order_index, session_id),
            )

    def update_pool_status(self, session_id: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE project_pool SET status = ? WHERE session_id = ?",
                (status, session_id),
            )

    def get_next_queued_session(self) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT p.session_id, s.title, s.status AS session_status
                FROM project_pool p
                JOIN search_sessions s ON s.id = p.session_id
                WHERE p.status = 'queued'
                ORDER BY p.order_index ASC, p.created_at ASC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def get_active_pool_session(self) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT p.session_id, s.title, s.status AS session_status
                FROM project_pool p
                JOIN search_sessions s ON s.id = p.session_id
                WHERE p.status = 'active'
                ORDER BY p.order_index ASC, p.created_at ASC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def reorder_pool(self, ordered_session_ids: List[str]) -> None:
        with self.connect() as connection:
            for index, session_id in enumerate(ordered_session_ids, start=1):
                connection.execute(
                    "UPDATE project_pool SET order_index = ? WHERE session_id = ?",
                    (index, session_id),
                )

    def clear_pool_by_status(self, statuses: List[str]) -> int:
        if not statuses:
            return 0
        placeholders = ",".join("?" * len(statuses))
        with self.connect() as connection:
            result = connection.execute(
                f"DELETE FROM project_pool WHERE status IN ({placeholders})",
                statuses,
            )
            return result.rowcount
