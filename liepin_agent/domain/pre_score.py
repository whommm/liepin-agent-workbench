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
    text = "\n".join(
        [
            candidate.current_title,
            candidate.current_company,
            candidate.city,
            candidate.work_years,
            candidate.education,
            candidate.summary_text,
        ]
    )
    score = 40
    reasons: List[str] = []

    if position_filter and position_filter in candidate.current_title:
        score += 18
        reasons.append("当前职位命中职位栏: {}".format(position_filter))

    hits = []
    for term in expected_terms or []:
        term = (term or "").strip()
        if term and term in text and term not in hits:
            hits.append(term)
    if hits:
        score += min(30, len(hits) * 10)
        reasons.append("卡片命中核心词: {}".format("、".join(hits[:4])))

    if candidate.current_company:
        score += 6
    if candidate.work_years:
        score += 4
    if candidate.education:
        score += 3

    position_text = (position_filter or "").strip()
    negative_hits = []
    for term in negative_terms or []:
        term = (term or "").strip()
        if not term:
            continue
        # A role's own position filter must never become a global noise term.
        if position_text and (term in position_text or position_text in term):
            continue
        if term in text:
            negative_hits.append(term)
    if negative_hits:
        score -= min(35, len(negative_hits) * 15)
        reasons.append("疑似噪音词: {}".format("、".join(negative_hits[:4])))

    score = max(0, min(100, score))
    if not reasons:
        reasons.append("卡片信息有限，保留观察")
    return score, reasons


def classify_candidate_card(
    candidate: CandidateSummary,
    expected_terms: Iterable[str],
    position_filter: str = "",
    negative_terms: Iterable[str] = DEFAULT_NEGATIVE_TERMS,
) -> Tuple[str, List[str], List[str], str]:
    """Return a human-readable card decision instead of a product score."""
    text = "\n".join(
        [
            candidate.current_title,
            candidate.current_company,
            candidate.city,
            candidate.work_years,
            candidate.education,
            candidate.summary_text,
        ]
    )
    signals: List[str] = []
    risks: List[str] = []
    position_text = (position_filter or "").strip()

    if position_text and position_text in candidate.current_title:
        signals.append("职位命中: {}".format(position_text))
    for term in expected_terms or []:
        term = (term or "").strip()
        if term and term in text and term not in signals:
            signals.append(term)

    for term in negative_terms or []:
        term = (term or "").strip()
        if not term:
            continue
        if position_text and (term in position_text or position_text in term):
            continue
        if term in text and term not in risks:
            risks.append(term)

    if risks and not signals:
        return "noise", signals, risks, "卡片命中明显噪音，且未看到已确认基准信号。"
    if signals:
        return "fetch", signals[:8], risks[:8], "卡片出现已确认基准信号，值得抓详情验证。"
    return "maybe", signals, risks[:8], "卡片信息不足，保留为潜在样本。"
