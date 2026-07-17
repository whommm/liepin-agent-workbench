"""Rule-based matcher used only by automated tests."""

from __future__ import annotations

import time
from typing import Dict

from ..domain.models import MatchResult


class RuleBasedMatchService:
    def match_candidate(
        self,
        session_id: str,
        round_id: str,
        candidate_id: str,
        resume_text: str,
        criteria: Dict[str, object],
    ) -> MatchResult:
        time.sleep(0.05)
        core_terms = [str(item) for item in criteria.get("core_terms", []) if item]
        negative_terms = [str(item) for item in criteria.get("negative_terms", []) if item]
        core_hits = [term for term in core_terms if term in (resume_text or "")]
        negative_hits = [term for term in negative_terms if term in (resume_text or "")]
        core_total = max(1, min(5, len(core_terms) or 5))
        core_met = min(core_total, len(core_hits))
        summary = "命中核心词：{}".format("、".join(core_hits[:5]) if core_hits else "未明显命中")
        risks = "命中排除词：{}".format("、".join(negative_hits)) if negative_hits else ""
        recommendation = "结合逐条件证据评估决定后续动作"
        detail = "\n".join(
            [
                "核心命中：{}/{}".format(core_met, core_total),
                summary,
                "风险：{}".format(risks or "未见明显硬伤"),
                "建议：{}".format(recommendation),
            ]
        )
        return MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            core_met_count=core_met,
            core_total=core_total,
            dealbreaker_hit=bool(negative_hits),
            summary=summary,
            risks=risks,
            recommendation=recommendation,
            detail=detail,
            raw_response=detail,
            criteria_version_id=str(criteria.get("criteria_version_id") or ""),
            matched_evidence=[
                {
                    "criterion": term,
                    "evidence": "简历文本命中关键词：{}".format(term),
                    "strength": "medium",
                }
                for term in core_hits[:5]
            ],
            missing_or_unclear=[
                "未明显命中更多核心词"
            ]
            if core_met < core_total
            else [],
            questions_to_verify=["请确认候选人是否真实承担过相关岗位职责"],
            confidence="medium" if core_hits else "low",
        )
