"""Persistence for resume facts and per-criterion evaluations."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List

from ._base_mixin import from_json, now_text, to_json


class _EvaluationMixin:
    def replace_candidate_facts(
        self, candidate_id: str, facts: Iterable[Dict[str, Any]]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM candidate_facts WHERE candidate_id = ?", (candidate_id,)
            )
            for fact in facts or []:
                value = str(fact.get("fact_value") or "").strip()
                if not value:
                    continue
                connection.execute(
                    """
                    INSERT INTO candidate_facts (
                        id, candidate_id, fact_type, fact_value, normalized_value,
                        section, evidence_quote, evidence_start, evidence_end,
                        confidence, extractor_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        candidate_id,
                        str(fact.get("fact_type") or "resume_fact"),
                        value,
                        str(fact.get("normalized_value") or value),
                        str(fact.get("section") or ""),
                        str(fact.get("evidence_quote") or value),
                        fact.get("evidence_start"),
                        fact.get("evidence_end"),
                        float(fact.get("confidence") or 0.5),
                        str(fact.get("extractor_version") or "facts-v1"),
                        now_text(),
                    ),
                )

    def list_candidate_facts(self, candidate_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM candidate_facts
                WHERE candidate_id = ? ORDER BY evidence_start, rowid
                """,
                (candidate_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_criterion_evaluations(
        self,
        candidate_id: str,
        session_id: str,
        criteria_version_id: str,
        evaluations: Iterable[Dict[str, Any]],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM criterion_evaluations
                WHERE candidate_id = ? AND criteria_version_id = ?
                """,
                (candidate_id, criteria_version_id),
            )
            for evaluation in evaluations or []:
                criterion_id = str(evaluation.get("criterion_id") or "")
                if not criterion_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO criterion_evaluations (
                        id, candidate_id, session_id, criteria_version_id,
                        criterion_id, status, confidence, evidence_json, reason,
                        verification_question, evaluator_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        candidate_id,
                        session_id,
                        criteria_version_id,
                        criterion_id,
                        str(evaluation.get("status") or "unknown"),
                        float(evaluation.get("confidence") or 0.5),
                        to_json(evaluation.get("evidence") or []),
                        str(evaluation.get("reason") or ""),
                        str(evaluation.get("verification_question") or ""),
                        str(evaluation.get("evaluator_version") or "criterion-v1"),
                        now_text(),
                    ),
                )

    def list_criterion_evaluations(
        self, candidate_id: str, criteria_version_id: str = ""
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [candidate_id]
        where = "e.candidate_id = ?"
        if criteria_version_id:
            where += " AND e.criteria_version_id = ?"
            params.append(criteria_version_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*, i.criterion_type, i.criterion_text, i.weight,
                       i.evidence_policy, i.observability
                FROM criterion_evaluations e
                JOIN job_criteria_items i ON i.id = e.criterion_id
                WHERE {}
                ORDER BY i.ordinal, e.created_at DESC
                """.format(where),
                params,
            ).fetchall()
        result = []
        seen = set()
        for row in rows:
            item = dict(row)
            key = (item.get("criteria_version_id"), item.get("criterion_id"))
            if key in seen:
                continue
            seen.add(key)
            item["evidence"] = from_json(item.get("evidence_json"), [])
            result.append(item)
        return result
