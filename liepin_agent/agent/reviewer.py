"""Review a round after matching and decide the next action."""

from __future__ import annotations

from typing import Dict, List

from ..domain.models import RoundReview, SearchPlan
from ..domain.recommendation import (
    EFFECTIVE_POOL_WEIGHTS,
    HIGH_POTENTIAL_VERIFY,
    PRIORITY_CONTACT,
    TRANSFERABLE_EXPLORE,
)
from .planner import Planner


class Reviewer:
    def __init__(self, planner: Planner | None = None):
        self.planner = planner or Planner()

    def review(
        self,
        previous_plan: SearchPlan,
        jd_text: str,
        used_queries: List[str],
        match_results: List[Dict[str, object]],
        noise_patterns: List[str],
        target_met: bool,
        should_stop: bool,
        stop_reason: str = "",
    ) -> RoundReview:
        state_counts: Dict[str, int] = {}
        for item in match_results or []:
            state = str(item.get("recommendation_state") or "")
            if state:
                state_counts[state] = state_counts.get(state, 0) + 1
        viable_count = sum(
            state_counts.get(state, 0)
            for state in (
                PRIORITY_CONTACT,
                HIGH_POTENTIAL_VERIFY,
                TRANSFERABLE_EXPLORE,
            )
        )
        effective_pool_score = sum(
            state_counts.get(state, 0) * weight
            for state, weight in EFFECTIVE_POOL_WEIGHTS.items()
        )
        evidence = {
            "matched_count": len(match_results or []),
            "recommendation_state_counts": state_counts,
            "viable_count": viable_count,
            "effective_pool_score": round(effective_pool_score, 2),
            "noise_patterns": noise_patterns,
        }
        if should_stop or target_met:
            return RoundReview(
                action="stop",
                summary=stop_reason or "当前候选人池已经达到停止条件，结束寻访。",
                evidence=evidence,
            )
        if viable_count >= 3:
            next_plan = self.planner.next_plan(
                previous_plan,
                jd_text,
                used_queries,
                noise_patterns,
            )
            return RoundReview(
                action="continue",
                summary="本轮有效候选池产出较好，沿相邻高相关场景继续扩展。",
                next_plan=next_plan,
                evidence=evidence,
            )
        if viable_count == 0 and len(match_results or []) >= 3:
            next_plan = self.planner.next_plan(
                previous_plan,
                jd_text,
                used_queries,
                noise_patterns + ["本轮详情匹配偏低"],
            )
            return RoundReview(
                action="continue",
                summary="本轮详情匹配偏低，需要换关键词或收紧场景。",
                next_plan=next_plan,
                evidence=evidence,
            )
        next_plan = self.planner.next_plan(previous_plan, jd_text, used_queries, noise_patterns)
        return RoundReview(
            action="continue",
            summary="样本量仍不足，继续用相邻关键词验证。",
            next_plan=next_plan,
            evidence=evidence,
        )
