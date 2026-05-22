"""Decide whether and which candidate details should be fetched."""

from __future__ import annotations

from collections import defaultdict
from typing import List

from ..domain.models import CandidateSummary, FetchDecision, Observation
from ..domain.states import RoundType


class CandidatePicker:
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

        # 大幅提高抽取上限，不再让死规则限制 LLM 的抓取决策
        if round_type == RoundType.SAMPLE_DETAIL.value:
            limit = min(10, remaining_detail_budget)
            policy = {"mode": "wait_min_results", "min_results": min(3, limit), "timeout_seconds": 300}
            strategy = {"high_confidence": 4, "diversity": 3, "uncertain": 3}
        elif round_type == RoundType.VALIDATE_DETAIL.value:
            limit = min(20, remaining_detail_budget)
            policy = {"mode": "wait_min_results", "min_results": min(8, limit), "timeout_seconds": 300}
            strategy = {"high_confidence": 12, "diversity": 4, "uncertain": 4}
        else:
            limit = min(40, remaining_detail_budget)
            policy = {"mode": "no_wait"}
            strategy = {"high_confidence": 30, "diversity": 6, "uncertain": 4}

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
            if len(picked) >= max(1, int(limit * 0.65)):
                break

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
