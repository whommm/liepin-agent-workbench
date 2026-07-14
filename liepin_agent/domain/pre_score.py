"""Lightweight card-level candidate pre-scoring."""

from __future__ import annotations

from typing import Iterable, List, Tuple

from .models import CandidateSummary


DEFAULT_NEGATIVE_TERMS = ["客服", "实习", "应届", "行政"]


def pre_score_candidate(
    candidate: CandidateSummary,
    expected_terms: Iterable[str],
    position_filter: str = "",
    negative_terms: Iterable[str] = DEFAULT_NEGATIVE_TERMS,
) -> Tuple[int, List[str]]:
    """聚合正向卡片信号，不在卡片层做淘汰判断。

    下游 CandidatePicker 使用这些可解释信号做召回优先分桶；字段缺失
    始终表示未知，不表示候选人不符合。
    """
    # 保留基础信息提取，但不再加减分
    reasons: List[str] = []
    text = "\n".join(
        [
            candidate.current_title or "",
            candidate.current_company or "",
            candidate.city or "",
            candidate.work_years or "",
            candidate.education or "",
            candidate.summary_text or "",
        ]
    )

    hits = []
    for term in expected_terms or []:
        term = (term or "").strip()
        if term and term in text and term not in hits:
            hits.append(term)
    if hits:
        reasons.append("卡片提及: {}".format("、".join(hits[:4])))

    # 返回中性分数，不决定候选人生死
    return 50, reasons or ["待LLM判断"]


def classify_candidate_card(
    candidate: CandidateSummary,
    expected_terms: Iterable[str],
    position_filter: str = "",
    negative_terms: Iterable[str] = DEFAULT_NEGATIVE_TERMS,
) -> Tuple[str, List[str], List[str], str]:
    """提取卡片正向信号，不用通用负面词直接淘汰候选人。

    明确硬冲突必须来自已确认条件并以结构化字段传给 CandidatePicker，
    不能从卡片未展示某项信息推断。
    """
    text = "\n".join(
        [
            candidate.current_title or "",
            candidate.current_company or "",
            candidate.city or "",
            candidate.work_years or "",
            candidate.education or "",
            candidate.summary_text or "",
        ]
    )
    signals: List[str] = []

    for term in expected_terms or []:
        term = (term or "").strip()
        if term and term in text and term not in signals:
            signals.append(term)

    # 全部统一标记为 maybe，由召回优先的四桶策略决定详情抓取。
    return "maybe", signals, [], "待详情策略判断"
