"""Decide whether and which candidate details should be fetched."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from ..domain.models import CandidateSummary, FetchDecision, Observation
from ..domain.states import RoundType


class CandidatePicker:
    """Decide whether and which candidate details should be fetched.

    Strategy numbers can be overridden via the ``strategies`` constructor argument
    so users can tune them without touching code.
    """

    DEFAULT_STRATEGIES = {
        RoundType.SAMPLE_DETAIL.value: {
            "limit": 999,
            "min_results": 3,
            "timeout_seconds": 300,
            "high_confidence": 999,
            "diversity": 999,
            "uncertain": 999,
        },
        RoundType.VALIDATE_DETAIL.value: {
            "limit": 999,
            "min_results": 8,
            "timeout_seconds": 300,
            "high_confidence": 999,
            "diversity": 999,
            "uncertain": 999,
        },
        RoundType.HARVEST_DETAIL.value: {
            "limit": 999,
            "min_results": 5,
            "timeout_seconds": 300,
            "high_confidence": 999,
            "diversity": 999,
            "uncertain": 999,
        },
    }

    def __init__(self, strategies: Dict[str, Dict[str, int]] | None = None):
        self.strategies = dict(self.DEFAULT_STRATEGIES)
        if strategies:
            for key, values in strategies.items():
                if key in self.strategies and isinstance(values, dict):
                    self.strategies[key].update(values)

    def decide(
        self,
        observation: Observation,
        candidates: List[CandidateSummary],
        remaining_detail_budget: int,
    ) -> FetchDecision:
        round_type = observation.recommended_round_type
        if round_type == RoundType.SKIP_DETAIL.value or remaining_detail_budget <= 0:
            return FetchDecision(
                action="skip_detail",
                round_type=RoundType.SKIP_DETAIL.value,
                reason=observation.reason,
            )

        cfg = self.strategies.get(round_type, self.strategies[RoundType.HARVEST_DETAIL.value])
        limit = min(cfg["limit"], remaining_detail_budget)
        if round_type == RoundType.SAMPLE_DETAIL.value:
            policy = {"mode": "wait_min_results", "min_results": min(cfg["min_results"], limit), "timeout_seconds": cfg["timeout_seconds"]}
            strategy = {"high_confidence": cfg["high_confidence"], "diversity": cfg["diversity"], "uncertain": cfg["uncertain"]}
        elif round_type == RoundType.VALIDATE_DETAIL.value:
            policy = {"mode": "wait_min_results", "min_results": min(cfg["min_results"], limit), "timeout_seconds": cfg["timeout_seconds"]}
            strategy = {"high_confidence": cfg["high_confidence"], "diversity": cfg["diversity"], "uncertain": cfg["uncertain"]}
        else:
            policy = {"mode": "no_wait", "min_results": min(cfg.get("min_results", 5), limit), "timeout_seconds": cfg["timeout_seconds"]}
            strategy = {"high_confidence": cfg["high_confidence"], "diversity": cfg["diversity"], "uncertain": cfg["uncertain"]}

        picked = self._pick_candidates(candidates, limit)
        return FetchDecision(
            action="fetch_details" if picked else "skip_detail",
            round_type=round_type,
            candidate_ids=[item.id for item in picked],
            fetch_limit=len(picked),
            sampling_strategy=strategy,
            match_wait_policy=policy,
            reason="按{}策略选择 {} 位候选人抓详情。{}".format(
                round_type,
                len(picked),
                observation.reason,
            ),
        )

    @staticmethod
    def _pick_candidates(
        candidates: List[CandidateSummary], limit: int
    ) -> List[CandidateSummary]:
        if limit <= 0:
            return []
        sorted_candidates = sorted(
            candidates or [],
            key=lambda item: (
                0 if item.card_decision == "fetch" else 1 if item.card_decision == "maybe" else 2,
                -len(item.card_signals or []),
                item.result_index,
            ),
        )
        picked: List[CandidateSummary] = []
        seen = set()

        for item in sorted_candidates:
            if item.id not in seen and item.card_decision == "fetch":
                picked.append(item)
                seen.add(item.id)

        by_company = defaultdict(list)
        for item in sorted_candidates:
            by_company[item.current_company or "未知公司"].append(item)
        for _, group in by_company.items():
            for item in group:
                if item.id not in seen:
                    picked.append(item)
                    seen.add(item.id)
                    break
            if len(picked) >= limit:
                return picked[:limit]

        for item in sorted_candidates:
            if item.id not in seen:
                picked.append(item)
                seen.add(item.id)
            if len(picked) >= limit:
                break
        return picked[:limit]
