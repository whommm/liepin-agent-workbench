"""Evidence-based ranking and feedback calibration."""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple

from ..domain.recommendation import (
    EFFECTIVE_POOL_WEIGHTS,
    EXPLICIT_MISMATCH,
    HIGH_POTENTIAL_VERIFY,
    INFORMATION_INSUFFICIENT,
    PRIORITY_CONTACT,
    TRANSFERABLE_EXPLORE,
)


class CandidateRankingService:
    version = "ranker-v2"

    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()

    def refresh_session(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            criteria = self.store.get_latest_criteria(session_id)
            criteria_version_id = str(criteria.get("criteria_version_id") or "")
            if not criteria_version_id:
                return []
            candidates = self.store.list_candidates(session_id)
            preferences = self.store.list_ranking_feedback(session_id)
            preference_adjustments = Counter()
            for item in preferences:
                preference_adjustments[str(item.get("preferred_candidate_id") or "")] += 2.0
                preference_adjustments[str(item.get("other_candidate_id") or "")] -= 2.0

            snapshots = []
            for candidate in candidates:
                candidate_id = str(candidate.get("id") or "")
                evaluations = self.store.list_criterion_evaluations(
                    candidate_id, criteria_version_id
                )
                score = self._score(evaluations, candidate)
                adjustment = float(preference_adjustments[candidate_id])
                score["rank_score"] = max(0.0, min(100.0, score["rank_score"] + adjustment))
                score["explanation"]["preference_adjustment"] = adjustment
                score["candidate_id"] = candidate_id
                score["_source_position"] = int(candidate.get("result_index") or 0)
                snapshots.append(score)

            samples = []
            by_id = {item["candidate_id"]: item for item in snapshots}
            for candidate in candidates:
                label = str(candidate.get("feedback_label") or "")
                if label not in {"recommended", "not_suitable"}:
                    continue
                item = by_id.get(str(candidate.get("id") or ""))
                if item:
                    samples.append((item["rank_score"], 1 if label == "recommended" else 0))
            parameters, metrics = self._fit_isotonic(samples)
            if samples:
                self.store.save_calibration_model(
                    scope="session",
                    session_id=session_id,
                    parameters=parameters,
                    sample_count=len(samples),
                    metrics=metrics,
                    version=self.version,
                )
            for item in snapshots:
                item["calibrated_probability"] = self._calibrate(
                    item["rank_score"], parameters
                )
            snapshots.sort(
                key=lambda item: (
                    -(item.get("calibrated_probability") or -1),
                    -item["rank_score"],
                    item["_source_position"],
                    item["candidate_id"],
                )
            )
            for index, item in enumerate(snapshots, 1):
                item["rank_position"] = index
                item["ranker_version"] = self.version
                item.pop("_source_position", None)
            self.store.save_rank_snapshots(
                session_id, criteria_version_id, snapshots
            )
            return snapshots

    def pool_summary(self, session_id: str) -> Dict[str, Any]:
        rankings = self.store.list_current_rankings(session_id)
        if not rankings and self.store.list_candidates(session_id):
            rankings = self.refresh_session(session_id)
        if rankings and any(
            not str(item.get("recommendation_state") or "") for item in rankings
        ):
            rankings = self.refresh_session(session_id)
        state_counts = Counter(
            str(item.get("recommendation_state") or INFORMATION_INSUFFICIENT)
            for item in rankings
        )
        effective_score = sum(
            float(EFFECTIVE_POOL_WEIGHTS.get(state, 0.0)) * count
            for state, count in state_counts.items()
        )
        return {
            "candidate_count": len(rankings),
            "state_counts": dict(state_counts),
            "effective_pool_score": round(effective_score, 2),
            "viable_count": (
                state_counts[PRIORITY_CONTACT]
                + state_counts[HIGH_POTENTIAL_VERIFY]
                + state_counts[TRANSFERABLE_EXPLORE]
            ),
        }

    def quality_dashboard(self, session_id: str) -> Dict[str, Any]:
        feedback = self.store.session_feedback_summary(session_id)
        coverage = self.store.search_coverage_summary(session_id)
        calibration = self.store.get_latest_calibration_model(session_id)
        rankings = self.store.list_current_rankings(session_id)
        pool = self.pool_summary(session_id)
        return {
            "session_id": session_id,
            "feedback": feedback,
            "search_coverage": {
                key: value for key, value in coverage.items() if key != "hypotheses"
            },
            "calibration": calibration,
            "ranking_count": len(rankings),
            "candidate_pool": pool,
            "top_rankings": rankings[:10],
        }

    def _score(
        self, evaluations: Iterable[Dict[str, Any]], candidate: Dict[str, Any]
    ) -> Dict[str, Any]:
        values = list(evaluations or [])
        status_value = {
            "direct_met": 1.0,
            "met": 1.0,
            "inferred_met": 0.8,
            "partial": 0.6,
            "unknown": None,
            "explicit_not_met": 0.0,
            "not_met": 0.0,
            "conflict": 0.0,
        }
        if values:
            total_weight = sum(max(0.05, float(item.get("weight") or 0.5)) for item in values)
            known = [
                item
                for item in values
                if status_value.get(str(item.get("status") or "unknown")) is not None
            ]
            known_weight = sum(max(0.05, float(item.get("weight") or 0.5)) for item in known)
            known_fit = (
                sum(
                    max(0.05, float(item.get("weight") or 0.5))
                    * float(status_value.get(str(item.get("status") or "unknown")) or 0)
                    for item in known
                )
                / known_weight
                if known_weight
                else 0.0
            )
            unknown_weight = max(0.0, total_weight - known_weight)
            potential_fit = (
                known_fit * known_weight + unknown_weight
            ) / total_weight
            coverage = known_weight / total_weight
            evidence_confidence = (
                sum(float(item.get("confidence") or 0) for item in known) / len(known)
                if known
                else 0.0
            )
            hard_risks = [
                item.get("criterion_text")
                for item in values
                if str(item.get("status") or "") == "conflict"
                or (
                    str(item.get("status") or "") in {"explicit_not_met", "not_met"}
                    and item.get("criterion_type") in {"must", "dealbreaker"}
                )
            ]
            explicit_negative_count = sum(
                1
                for item in values
                if str(item.get("status") or "")
                in {"explicit_not_met", "not_met", "conflict"}
            )
            critical_unknown_count = sum(
                1
                for item in values
                if str(item.get("status") or "") == "unknown"
                and item.get("criterion_type") in {"must", "dealbreaker", "verify"}
            )
        else:
            known_fit = 0.0
            potential_fit = 0.0
            coverage = 0.0
            evidence_confidence = 0.0
            hard_risks = []
            explicit_negative_count = 0
            critical_unknown_count = 0

        known_fit_score = round(known_fit * 100, 2)
        potential_fit_score = round(potential_fit * 100, 2)
        evidence_coverage_score = round(coverage * 100, 2)
        evidence_confidence_score = round(evidence_confidence * 100, 2)
        recommendation_state = self._recommendation_state(
            values=values,
            known_fit=known_fit,
            potential_fit=potential_fit,
            coverage=coverage,
            hard_risks=hard_risks,
            explicit_negative_count=explicit_negative_count,
            critical_unknown_count=critical_unknown_count,
        )
        rank_score = round(
            known_fit_score * 0.60
            + potential_fit_score * 0.20
            + evidence_coverage_score * 0.15
            + evidence_confidence_score * 0.05,
            2,
        )
        return {
            "fit_score": known_fit_score,
            "confidence_score": evidence_coverage_score,
            "known_fit_score": known_fit_score,
            "potential_fit_score": potential_fit_score,
            "evidence_coverage_score": evidence_coverage_score,
            "recommendation_state": recommendation_state,
            "conflict_count": len([item for item in hard_risks if item]),
            "rank_score": rank_score,
            "explanation": {
                "criterion_status_counts": dict(Counter(str(item.get("status") or "unknown") for item in values)),
                "hard_risks": [item for item in hard_risks if item],
                "critical_unknown_count": critical_unknown_count,
                "known_fit_score": known_fit_score,
                "potential_fit_score": potential_fit_score,
                "evidence_coverage_score": evidence_coverage_score,
                "formula": "known_fit*0.60 + potential*0.20 + coverage*0.15 + evidence_confidence*0.05",
            },
        }

    @staticmethod
    def _recommendation_state(
        *,
        values: List[Dict[str, Any]],
        known_fit: float,
        potential_fit: float,
        coverage: float,
        hard_risks: List[Any],
        explicit_negative_count: int,
        critical_unknown_count: int,
    ) -> str:
        if hard_risks:
            return EXPLICIT_MISMATCH
        if not values:
            return INFORMATION_INSUFFICIENT
        if coverage <= 0:
            return INFORMATION_INSUFFICIENT
        if known_fit >= 0.75 and coverage >= 0.65 and critical_unknown_count == 0:
            return PRIORITY_CONTACT
        if known_fit >= 0.75 and potential_fit >= 0.85:
            return HIGH_POTENTIAL_VERIFY
        if known_fit >= 0.55 or potential_fit >= 0.75:
            return TRANSFERABLE_EXPLORE
        if explicit_negative_count and coverage >= 0.5:
            return EXPLICIT_MISMATCH
        return INFORMATION_INSUFFICIENT

    def _fit_isotonic(
        self, samples: Iterable[Tuple[float, int]]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        ordered = sorted((float(score), int(label)) for score, label in samples)
        if not ordered:
            return {"blocks": []}, {"sample_count": 0}
        blocks = []
        for score, label in ordered:
            blocks.append({"min": score, "max": score, "count": 1, "positive": label})
            while len(blocks) >= 2:
                left = blocks[-2]
                right = blocks[-1]
                left_rate = (left["positive"] + 1) / (left["count"] + 2)
                right_rate = (right["positive"] + 1) / (right["count"] + 2)
                if left_rate <= right_rate:
                    break
                blocks[-2:] = [
                    {
                        "min": left["min"],
                        "max": right["max"],
                        "count": left["count"] + right["count"],
                        "positive": left["positive"] + right["positive"],
                    }
                ]
        for block in blocks:
            block["probability"] = round(
                (block["positive"] + 1) / (block["count"] + 2), 6
            )
        predictions = [self._calibrate(score, {"blocks": blocks}) or 0.5 for score, _ in ordered]
        brier = sum((prediction - label) ** 2 for prediction, (_, label) in zip(predictions, ordered)) / len(ordered)
        accuracy = sum((prediction >= 0.5) == bool(label) for prediction, (_, label) in zip(predictions, ordered)) / len(ordered)
        return {"blocks": blocks}, {
            "sample_count": len(ordered),
            "brier_score": round(brier, 6),
            "accuracy": round(accuracy, 6),
        }

    @staticmethod
    def _calibrate(score: float, parameters: Dict[str, Any]) -> float | None:
        blocks = list(parameters.get("blocks") or [])
        if not blocks:
            return None
        for block in blocks:
            if float(score) <= float(block["max"]):
                return float(block["probability"])
        return float(blocks[-1]["probability"])
