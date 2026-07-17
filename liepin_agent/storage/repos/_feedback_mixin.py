"""Human feedback persistence and labeled-sample quality metrics."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any, Dict, Iterable, List

from ._base_mixin import from_json, now_text, to_json


FEEDBACK_LABELS = {"recommended", "uncertain", "not_suitable"}
OUTCOME_TYPES = {
    "greeted",
    "replied",
    "interview",
    "rejected",
    "hired",
    "no_response",
}


class _FeedbackMixin:
    """Store immutable recruiter judgments for audit and future learning."""

    def save_candidate_feedback(
        self,
        candidate_id: str,
        feedback_label: str,
        *,
        corrected_tier: str = "",
        reason_codes: Iterable[str] | None = None,
        note: str = "",
        source: str = "human",
    ) -> str:
        label = str(feedback_label or "").strip().lower()
        if label not in FEEDBACK_LABELS:
            raise ValueError("feedback_label must be recommended/uncertain/not_suitable")
        _ = corrected_tier
        reasons = list(dict.fromkeys(str(item).strip() for item in (reason_codes or []) if str(item).strip()))

        with self.connect() as connection:
            candidate = connection.execute(
                "SELECT id, session_id FROM candidate_summaries WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if not candidate:
                raise ValueError("candidate not found")
            criteria = connection.execute(
                """
                SELECT id FROM match_criteria_versions
                WHERE session_id = ? AND status = 'confirmed'
                ORDER BY version DESC LIMIT 1
                """,
                (candidate["session_id"],),
            ).fetchone()
            match = connection.execute(
                """
                SELECT id, confidence, prompt_version,
                       model_name, model_config_hash, criteria_version_id
                FROM match_results
                WHERE candidate_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            snapshot = dict(match) if match else {}
            ranking = connection.execute(
                """
                SELECT recommendation_state, known_fit_score,
                       potential_fit_score, evidence_coverage_score, rank_score,
                       ranker_version
                FROM candidate_rank_snapshots
                WHERE candidate_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            if ranking:
                snapshot.update(dict(ranking))
            feedback_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO candidate_feedback (
                    id, candidate_id, session_id, criteria_version_id,
                    feedback_label, corrected_tier, reason_codes_json, note,
                    source, model_snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    candidate_id,
                    candidate["session_id"],
                    criteria["id"] if criteria else "",
                    label,
                    "",
                    to_json(reasons),
                    str(note or "").strip(),
                    str(source or "human").strip() or "human",
                    to_json(snapshot),
                    now_text(),
                ),
            )
        return feedback_id

    def list_candidate_feedback(self, candidate_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM candidate_feedback
                WHERE candidate_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (candidate_id,),
            ).fetchall()
        return [self._decode_feedback(dict(row)) for row in rows]

    def get_latest_candidate_feedback(self, candidate_id: str) -> Dict[str, Any] | None:
        rows = self.list_candidate_feedback(candidate_id)
        return rows[0] if rows else None

    def save_candidate_outcome(
        self,
        candidate_id: str,
        outcome: str,
        *,
        note: str = "",
        occurred_at: str = "",
    ) -> str:
        normalized = str(outcome or "").strip().lower()
        if normalized not in OUTCOME_TYPES:
            raise ValueError("unsupported candidate outcome")
        with self.connect() as connection:
            candidate = connection.execute(
                "SELECT session_id FROM candidate_summaries WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if not candidate:
                raise ValueError("candidate not found")
            outcome_id = uuid.uuid4().hex
            timestamp = str(occurred_at or "").strip() or now_text()
            connection.execute(
                """
                INSERT INTO candidate_outcomes (
                    id, candidate_id, session_id, outcome, note, occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id,
                    candidate_id,
                    candidate["session_id"],
                    normalized,
                    str(note or "").strip(),
                    timestamp,
                    now_text(),
                ),
            )
        return outcome_id

    def list_candidate_outcomes(self, candidate_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM candidate_outcomes
                WHERE candidate_id = ?
                ORDER BY occurred_at DESC, rowid DESC
                """,
                (candidate_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_ranking_feedback(
        self,
        session_id: str,
        preferred_candidate_id: str,
        other_candidate_id: str,
        *,
        reason: str = "",
    ) -> str:
        if preferred_candidate_id == other_candidate_id:
            raise ValueError("ranking candidates must be different")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id FROM candidate_summaries
                WHERE id IN (?, ?)
                """,
                (preferred_candidate_id, other_candidate_id),
            ).fetchall()
            if len(rows) != 2 or any(row["session_id"] != session_id for row in rows):
                raise ValueError("ranking candidates must belong to the session")
            criteria = connection.execute(
                """
                SELECT id FROM match_criteria_versions
                WHERE session_id = ? AND status = 'confirmed'
                ORDER BY version DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            feedback_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO ranking_feedback (
                    id, session_id, criteria_version_id, preferred_candidate_id,
                    other_candidate_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    session_id,
                    criteria["id"] if criteria else "",
                    preferred_candidate_id,
                    other_candidate_id,
                    str(reason or "").strip(),
                    now_text(),
                ),
            )
        return feedback_id

    def list_ranking_feedback(self, session_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ranking_feedback
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def session_feedback_summary(self, session_id: str) -> Dict[str, Any]:
        candidates = self.list_candidates(session_id)
        labeled = [item for item in candidates if item.get("feedback_label")]
        decisive = [
            item
            for item in labeled
            if item.get("feedback_label") in {"recommended", "not_suitable"}
            and str(item.get("recommendation_state") or "")
            in {
                "priority_contact",
                "high_potential_verify",
                "transferable_explore",
                "explicit_mismatch",
            }
        ]
        true_positive = sum(
            1
            for item in decisive
            if item["feedback_label"] == "recommended"
            and str(item.get("recommendation_state") or "")
            in {"priority_contact", "high_potential_verify", "transferable_explore"}
        )
        false_positive = sum(
            1
            for item in decisive
            if item["feedback_label"] == "not_suitable"
            and str(item.get("recommendation_state") or "")
            in {"priority_contact", "high_potential_verify", "transferable_explore"}
        )
        false_negative = sum(
            1
            for item in decisive
            if item["feedback_label"] == "recommended"
            and str(item.get("recommendation_state") or "") == "explicit_mismatch"
        )
        true_negative = len(decisive) - true_positive - false_positive - false_negative
        predicted_positive = true_positive + false_positive
        human_positive = true_positive + false_negative
        reason_counts = Counter(
            reason
            for item in labeled
            for reason in (item.get("feedback_reason_codes") or [])
        )
        return {
            "session_id": session_id,
            "candidate_count": len(candidates),
            "labeled_candidate_count": len(labeled),
            "label_counts": dict(Counter(item["feedback_label"] for item in labeled)),
            "reason_counts": dict(reason_counts.most_common()),
            "comparable_count": len(decisive),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_negative": true_negative,
            "precision": round(true_positive / predicted_positive, 4) if predicted_positive else None,
            "recall": round(true_positive / human_positive, 4) if human_positive else None,
            "agreement_rate": round((true_positive + true_negative) / len(decisive), 4) if decisive else None,
            "scope": "latest human feedback on labeled candidates only",
        }

    @staticmethod
    def _decode_feedback(item: Dict[str, Any]) -> Dict[str, Any]:
        item["reason_codes"] = from_json(item.get("reason_codes_json"), [])
        item["model_snapshot"] = from_json(item.get("model_snapshot_json"), {})
        return item
