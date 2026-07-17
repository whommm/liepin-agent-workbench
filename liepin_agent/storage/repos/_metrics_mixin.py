"""Auto-generated mixin for SQLiteStore refactoring."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ...domain.recommendation import EFFECTIVE_POOL_WEIGHTS

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
        criteria = self.get_latest_criteria_version(session_id, "confirmed") or {}
        criteria_version_id = str(criteria.get("id") or "")
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
                SELECT COUNT(DISTINCT d.candidate_id) AS n
                FROM candidate_details d
                JOIN candidate_summaries c ON c.id = d.candidate_id
                WHERE c.session_id = ? AND d.capture_status = 'success'
                """,
                (session_id,),
            ).fetchone()
            matched = connection.execute(
                """
                SELECT COUNT(*) AS n FROM match_results m
                WHERE m.session_id = ? AND m.criteria_version_id = ?
                  AND m.id = (
                      SELECT m2.id FROM match_results m2
                      WHERE m2.candidate_id = m.candidate_id
                        AND m2.criteria_version_id = m.criteria_version_id
                      ORDER BY m2.created_at DESC, m2.rowid DESC LIMIT 1
                  )
                """,
                (session_id, criteria_version_id),
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
        state_counts: Dict[str, int] = {}
        for ranking in self.list_current_rankings(session_id, criteria_version_id):
            state = str(ranking.get("recommendation_state") or "")
            if state:
                state_counts[state] = state_counts.get(state, 0) + 1
        effective_pool_score = sum(
            state_counts.get(state, 0) * weight
            for state, weight in EFFECTIVE_POOL_WEIGHTS.items()
        )
        return {
            "total_runtime_minutes": runtime_minutes,
            "search_round_count": round_count,
            "raw_candidate_count": int(raw["n"] if raw else 0),
            "unique_candidate_count": int(unique_candidates["n"] if unique_candidates else 0),
            "detail_fetch_count": detail_count,
            "matched_count": int(matched["n"] if matched else 0),
            "manual_intervention_count": int(manual["n"] if manual else 0),
            "recommendation_state_counts": state_counts,
            "effective_pool_score": round(effective_pool_score, 2),
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
                    COALESCE(SUM(ab_count), 0) AS relevant_count
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


    def session_diagnostic_summary(self, session_id: str) -> Dict[str, Any]:
        metrics = self.session_efficiency_metrics(session_id)
        hypothesis = self.search_hypothesis_metrics(session_id)
        criteria = self.get_latest_criteria_version(session_id, "confirmed") or {}
        criteria_version_id = str(criteria.get("id") or "")
        with self.connect() as connection:
            round_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM search_rounds
                WHERE session_id = ?
                GROUP BY status
                """,
                (session_id,),
            ).fetchall()
            card_rows = connection.execute(
                """
                SELECT COALESCE(card_decision, '') AS card_decision, COUNT(*) AS n
                FROM candidate_summaries
                WHERE session_id = ?
                GROUP BY card_decision
                """,
                (session_id,),
            ).fetchall()
            detail_rows = connection.execute(
                """
                SELECT COALESCE(d.capture_status, '') AS capture_status, COUNT(*) AS n
                FROM candidate_details d
                JOIN candidate_summaries c ON c.id = d.candidate_id
                WHERE c.session_id = ?
                GROUP BY d.capture_status
                """,
                (session_id,),
            ).fetchall()
            match_rows = connection.execute(
                """
                SELECT COALESCE(m.status, '') AS status, COUNT(*) AS n
                FROM match_results m
                WHERE m.session_id = ? AND m.criteria_version_id = ?
                  AND m.id = (
                      SELECT m2.id FROM match_results m2
                      WHERE m2.candidate_id = m.candidate_id
                        AND m2.criteria_version_id = m.criteria_version_id
                      ORDER BY m2.created_at DESC, m2.rowid DESC LIMIT 1
                  )
                GROUP BY m.status
                """,
                (session_id, criteria_version_id),
            ).fetchall()
            pending_match = connection.execute(
                """
                SELECT COUNT(DISTINCT d.candidate_id) AS n
                FROM candidate_summaries c
                JOIN candidate_details d ON d.candidate_id = c.id
                LEFT JOIN match_results m ON m.candidate_id = c.id
                    AND m.session_id = c.session_id
                    AND m.criteria_version_id = ?
                WHERE c.session_id = ?
                  AND d.capture_status = 'success'
                  AND m.id IS NULL
                """,
                (criteria_version_id, session_id),
            ).fetchone()
            error_rows = connection.execute(
                """
                SELECT title, message, created_at
                FROM agent_events
                WHERE session_id = ? AND event_type = 'error'
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (session_id,),
            ).fetchall()
        round_status_counts = self._count_rows(round_rows, "status")
        card_decision_counts = self._count_rows(card_rows, "card_decision")
        detail_status_counts = self._count_rows(detail_rows, "capture_status")
        match_status_counts = self._count_rows(match_rows, "status")
        pending_match_count = int(pending_match["n"] if pending_match else 0)
        noise_count = int(card_decision_counts.get("noise") or 0)
        unique_count = int(metrics.get("unique_candidate_count") or 0)
        raw_count = int(metrics.get("raw_candidate_count") or 0)
        detail_count = int(metrics.get("detail_fetch_count") or 0)
        matched_count = int(metrics.get("matched_count") or 0)
        effective_pool_score = float(metrics.get("effective_pool_score") or 0)
        return {
            "metrics": metrics,
            "round_status_counts": round_status_counts,
            "card_decision_counts": card_decision_counts,
            "detail_status_counts": detail_status_counts,
            "match_status_counts": match_status_counts,
            "pending_match_count": pending_match_count,
            "error_count": len(error_rows),
            "recent_errors": [dict(row) for row in error_rows],
            "search_hypothesis_metrics": hypothesis,
            "diagnostic_flags": self._diagnostic_flags(
                raw_count=raw_count,
                unique_count=unique_count,
                detail_count=detail_count,
                matched_count=matched_count,
                effective_pool_score=effective_pool_score,
                noise_count=noise_count,
                pending_match_count=pending_match_count,
                error_count=len(error_rows),
            ),
        }

    @staticmethod
    def _count_rows(rows: Iterable[Any], key: str) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for row in rows:
            result[str(row[key] or "")] = int(row["n"] or 0)
        return result

    @staticmethod
    def _diagnostic_flags(
        raw_count: int,
        unique_count: int,
        detail_count: int,
        matched_count: int,
        effective_pool_score: float,
        noise_count: int,
        pending_match_count: int,
        error_count: int,
    ) -> List[str]:
        flags: List[str] = []
        if raw_count == 0:
            flags.append("未读取到候选人卡片，需检查关键词、登录态或页面结构。")
        if unique_count and noise_count / unique_count >= 0.5:
            flags.append("噪音候选人占比偏高，建议收紧职位栏、行业词或负向词。")
        if detail_count and matched_count < detail_count:
            flags.append("存在已抓详情但未完成匹配的候选人，后台匹配可能仍在进行或已失败。")
        if pending_match_count:
            flags.append("有 {} 位候选人等待匹配结果回写。".format(pending_match_count))
        if detail_count and effective_pool_score == 0:
            flags.append("已抓详情但有效候选池仍为空，建议复盘搜索假设或证据缺失情况。")
        if raw_count and unique_count and unique_count / raw_count <= 0.5:
            flags.append("重复候选人占比较高，建议切换搜索假设或扩大关键词差异。")
        if error_count:
            flags.append("存在运行错误事件，建议查看诊断日志和页面快照。")
        if not flags:
            flags.append("未发现明显阻塞项，可继续按当前策略推进。")
        return flags


