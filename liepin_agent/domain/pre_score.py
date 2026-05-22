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
    """预打分已降级为信息聚合，不再做规则化评分，避免死程序误杀人才。
    所有智能判断交由 LLM 在 observe_round / decide_fetch 中处理。
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
    """卡片分类已降级为信息聚合，不再做规则化 fetch/maybe/noise 判定。
    避免"客服"/"实习"等硬规则误杀真正有潜力的候选人。
    所有去留决策交由 LLM 在 brain 层处理。
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

    # 全部统一标记为 maybe，不让规则程序决定候选人生死
    return "maybe", signals, [], "待LLM判断"
