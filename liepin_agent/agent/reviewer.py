"""Review a round after matching and decide the next action."""

from __future__ import annotations

from typing import Dict, List

from ..domain.models import RoundReview, SearchPlan
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
        tier_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        for item in match_results or []:
            tier = str(item.get("tier") or "").upper()
            if tier in tier_counts:
                tier_counts[tier] += 1
        ab_count = tier_counts["A"] + tier_counts["B"]
        evidence = {
            "matched_count": len(match_results or []),
            "a_count": tier_counts["A"],
            "b_count": tier_counts["B"],
            "c_count": tier_counts["C"],
            "d_count": tier_counts["D"],
            "ab_count": ab_count,
            "noise_patterns": noise_patterns,
        }
        if should_stop or target_met:
            return RoundReview(
                action="stop",
                summary=stop_reason or "当前候选人池已经达到停止条件，结束寻访。",
                evidence=evidence,
            )
        if ab_count >= 3:
            next_plan = self.planner.next_plan(
                previous_plan,
                jd_text,
                used_queries,
                noise_patterns,
            )
            return RoundReview(
                action="continue",
                summary="本轮 A/B 产出较好，沿相邻高相关场景继续扩展。",
                next_plan=next_plan,
                evidence=evidence,
            )
        if ab_count == 0 and len(match_results or []) >= 3:
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

