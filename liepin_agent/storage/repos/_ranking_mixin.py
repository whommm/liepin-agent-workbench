"""Persistence for candidate ranking snapshots and calibration models."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List

from ._base_mixin import from_json, now_text, to_json


class _RankingMixin:
    def save_rank_snapshots(
        self,
        session_id: str,
        criteria_version_id: str,
        snapshots: Iterable[Dict[str, Any]],
    ) -> None:
        timestamp = now_text()
        with self.connect() as connection:
            for item in snapshots or []:
                # A refresh rebuilds the current ranking. Keeping every rebuild
                # made latest-row lookups quadratic and inflated long-running DBs.
                connection.execute(
                    """
                    DELETE FROM candidate_rank_snapshots
                    WHERE candidate_id = ? AND criteria_version_id = ?
                    """,
                    (item["candidate_id"], criteria_version_id),
                )
                connection.execute(
                    """
                    INSERT INTO candidate_rank_snapshots (
                        id, candidate_id, session_id, criteria_version_id,
                        fit_score, confidence_score, known_fit_score,
                        potential_fit_score, evidence_coverage_score,
                        recommendation_state, conflict_count, rank_score,
                        calibrated_probability, rank_position, explanation_json,
                        ranker_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        item["candidate_id"],
                        session_id,
                        criteria_version_id,
                        float(item.get("fit_score") or 0),
                        float(item.get("confidence_score") or 0),
                        float(item.get("known_fit_score") or 0),
                        float(item.get("potential_fit_score") or 0),
                        float(item.get("evidence_coverage_score") or 0),
                        str(item.get("recommendation_state") or ""),
                        int(item.get("conflict_count") or 0),
                        float(item.get("rank_score") or 0),
                        item.get("calibrated_probability"),
                        int(item.get("rank_position") or 0),
                        to_json(item.get("explanation") or {}),
                        str(item.get("ranker_version") or "ranker-v1"),
                        timestamp,
                    ),
                )

    def list_current_rankings(
        self, session_id: str, criteria_version_id: str = ""
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [session_id]
        version_filter = ""
        if criteria_version_id:
            version_filter = " AND r.criteria_version_id = ?"
            params.append(criteria_version_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*, c.name, c.current_title, c.current_company
                FROM candidate_rank_snapshots r
                JOIN candidate_summaries c ON c.id = r.candidate_id
                WHERE r.session_id = ? {}
                  AND r.id = (
                      SELECT r2.id FROM candidate_rank_snapshots r2
                      WHERE r2.candidate_id = r.candidate_id
                        AND r2.criteria_version_id = r.criteria_version_id
                      ORDER BY r2.created_at DESC, r2.rowid DESC LIMIT 1
                  )
                ORDER BY r.rank_position, r.rank_score DESC
                """.format(version_filter),
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["explanation"] = from_json(item.get("explanation_json"), {})
            result.append(item)
        return result

    def save_calibration_model(
        self,
        *,
        scope: str,
        session_id: str,
        parameters: Dict[str, Any],
        sample_count: int,
        metrics: Dict[str, Any],
        version: str,
    ) -> str:
        model_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO calibration_models (
                    id, scope, session_id, model_type, parameters_json,
                    sample_count, metrics_json, version, created_at
                ) VALUES (?, ?, ?, 'isotonic', ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    scope,
                    session_id,
                    to_json(parameters),
                    int(sample_count),
                    to_json(metrics),
                    version,
                    now_text(),
                ),
            )
        return model_id

    def get_latest_calibration_model(self, session_id: str) -> Dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM calibration_models
                WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["parameters"] = from_json(item.get("parameters_json"), {})
        item["metrics"] = from_json(item.get("metrics_json"), {})
        return item
