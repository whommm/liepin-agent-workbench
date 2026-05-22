"""Observe and classify search-round result quality."""

from __future__ import annotations

from typing import Iterable, List

from ..domain.models import CandidateSummary, Observation
from ..domain.states import RoundType


class Observer:
    def observe(
        self,
        candidates: Iterable[CandidateSummary],
        expected_terms: List[str],
    ) -> Observation:
        candidate_list = list(candidates or [])
        raw_count = len(candidate_list)
        # 系统预打分已禁用，不再用 card_decision/pre_score 做规则筛选
        # 全部交给 LLM 做智能观察，此处仅做宽松的基数统计
        relevant = candidate_list
        strong = candidate_list
        noise_patterns = self._detect_noise(candidate_list)
        positive_signals = self._detect_positive_signals(candidate_list, expected_terms)

        if raw_count == 0:
            return Observation(
                round_quality="empty",
                raw_count=0,
                deduped_count=0,
                estimated_relevant_count=0,
                noise_patterns=noise_patterns,
                positive_signals=positive_signals,
                recommended_round_type=RoundType.SKIP_DETAIL.value,
                reason="搜索结果为空，当前关键词或筛选条件过窄。",
            )
        if len(relevant) == 0 and raw_count >= 8:
            return Observation(
                round_quality="low",
                raw_count=raw_count,
                deduped_count=raw_count,
                estimated_relevant_count=0,
                noise_patterns=noise_patterns,
                positive_signals=positive_signals,
                recommended_round_type=RoundType.SKIP_DETAIL.value,
                reason="结果池数量不低，但卡片层面没有明显有效信号，先不消耗详情抓取预算。",
            )
        if len(strong) >= 10:
            return Observation(
                round_quality="high",
                raw_count=raw_count,
                deduped_count=raw_count,
                estimated_relevant_count=len(relevant),
                noise_patterns=noise_patterns,
                positive_signals=positive_signals,
                recommended_round_type=RoundType.HARVEST_DETAIL.value,
                reason="高预评分候选人较多，该关键词方向可进入收割。",
            )
        if len(relevant) >= 5:
            return Observation(
                round_quality="medium",
                raw_count=raw_count,
                deduped_count=raw_count,
                estimated_relevant_count=len(relevant),
                noise_patterns=noise_patterns,
                positive_signals=positive_signals,
                recommended_round_type=RoundType.VALIDATE_DETAIL.value,
                reason="卡片层面出现多个有效信号，需要抓详情验证真实匹配度。",
            )
        return Observation(
            round_quality="uncertain",
            raw_count=raw_count,
            deduped_count=raw_count,
            estimated_relevant_count=len(relevant),
            noise_patterns=noise_patterns,
            positive_signals=positive_signals,
            recommended_round_type=RoundType.SAMPLE_DETAIL.value,
            reason="结果池有少量潜在信号但不确定，建议抽样抓详情校准。",
        )

    @staticmethod
    def _detect_noise(candidates: List[CandidateSummary]) -> List[str]:
        text = "\n".join(
            "{}\n{}\n{}".format(item.current_title, item.current_company, item.summary_text)
            for item in candidates
        )
        patterns = []
        for term, label in [
            ("销售", "销售岗位"),
            ("运营", "运营背景偏多"),
            ("客服", "客服/服务类岗位"),
            ("实习", "低年限或实习候选人"),
            ("内容", "内容产品噪音"),
        ]:
            if term in text:
                patterns.append(label)
        return patterns[:4]

    @staticmethod
    def _detect_positive_signals(
        candidates: List[CandidateSummary], expected_terms: List[str]
    ) -> List[str]:
        text = "\n".join(
            "{}\n{}\n{}".format(item.current_title, item.current_company, item.summary_text)
            for item in candidates
        )
        result = []
        for term in expected_terms or []:
            if term and term in text and term not in result:
                result.append(term)
        return result[:6]
