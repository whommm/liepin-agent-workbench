"""Candidate recommendation states derived from evidence, not missing fields."""

from __future__ import annotations


PRIORITY_CONTACT = "priority_contact"
HIGH_POTENTIAL_VERIFY = "high_potential_verify"
TRANSFERABLE_EXPLORE = "transferable_explore"
INFORMATION_INSUFFICIENT = "information_insufficient"
EXPLICIT_MISMATCH = "explicit_mismatch"

RECOMMENDATION_LABELS = {
    PRIORITY_CONTACT: "优先沟通",
    HIGH_POTENTIAL_VERIFY: "高潜待确认",
    TRANSFERABLE_EXPLORE: "可迁移探索",
    INFORMATION_INSUFFICIENT: "信息不足",
    EXPLICIT_MISMATCH: "明确不匹配",
}

EFFECTIVE_POOL_WEIGHTS = {
    PRIORITY_CONTACT: 1.0,
    HIGH_POTENTIAL_VERIFY: 0.5,
    TRANSFERABLE_EXPLORE: 0.25,
    INFORMATION_INSUFFICIENT: 0.0,
    EXPLICIT_MISMATCH: 0.0,
}


def recommendation_label(value: object) -> str:
    text = str(value or "")
    return RECOMMENDATION_LABELS.get(text, text or "待评估")
