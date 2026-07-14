"""Candidate dedupe helpers."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import CandidateSummary


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", "", value)
    return value


_TRACKING_QUERY_KEYS = {
    "from",
    "source",
    "traceid",
    "ckid",
    "d_ckid",
    "d_sfrom",
    "d_curpage",
    "d_pagesize",
    "d_headid",
}


def normalize_profile_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        raw = "https://h.liepin.com" + raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return normalize_text(raw)
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
    )
    return urlunsplit(
        (
            (parsed.scheme or "https").casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/"),
            urlencode(query),
            "",
        )
    )


def build_candidate_dedupe_key(candidate: CandidateSummary) -> str:
    profile_url = normalize_profile_url(candidate.profile_url)
    if profile_url:
        return profile_url
    parts = [
        normalize_text(candidate.name),
        normalize_text(candidate.current_company),
        normalize_text(candidate.current_title),
    ]
    return "|".join(part for part in parts if part)
