"""Candidate dedupe helpers."""

from __future__ import annotations

import re

from .models import CandidateSummary


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", "", value)
    return value


def build_candidate_dedupe_key(candidate: CandidateSummary) -> str:
    profile_url = normalize_text(candidate.profile_url)
    if profile_url:
        return profile_url
    parts = [
        normalize_text(candidate.name),
        normalize_text(candidate.current_company),
        normalize_text(candidate.current_title),
    ]
    return "|".join(part for part in parts if part)

