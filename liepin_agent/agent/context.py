"""Bounded, task-specific context builders for Agent LLM calls.

The functions in this module are deliberately pure.  They turn large runtime
objects into small JSON-compatible payloads without retaining resume bodies,
raw model responses, or database-only fields.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Sequence

from ..domain.models import CandidateSummary, Observation


OBSERVE_SAMPLE_LIMIT = 12
FETCH_RANKED_LIMIT = 40
FETCH_DISPUTED_LIMIT = 10
REVIEW_REPRESENTATIVE_LIMIT = 8
STRATEGY_QUERY_LIMIT = 12
STRATEGY_ROUND_LIMIT = 8

OBSERVE_CONTEXT_CHAR_BUDGET = 7_000
FETCH_CONTEXT_CHAR_BUDGET = 12_000
REVIEW_CONTEXT_CHAR_BUDGET = 8_000

OBSERVE_PROMPT_CHAR_BUDGET = 14_000
FETCH_PROMPT_CHAR_BUDGET = 16_000
REVIEW_PROMPT_CHAR_BUDGET = 18_000
PAGINATION_PROMPT_CHAR_BUDGET = 6_000


def compact_text(value: Any, limit: int) -> str:
    """Normalize whitespace and cap a free-text value deterministically."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def compact_candidate_card(
    candidate: CandidateSummary | Mapping[str, Any],
    *,
    summary_limit: int = 160,
    sample_bucket: str = "",
) -> Dict[str, Any]:
    """Return only card facts useful to a sourcing decision.

    ``raw_text``, profile URLs, names and persistence metadata are intentionally
    excluded.  Missing card data remains missing; it is never converted into a
    negative signal.
    """

    signals = _string_items(_field(candidate, "card_signals"), limit=3, item_limit=56)
    risks = _string_items(_field(candidate, "card_risks"), limit=3, item_limit=56)
    result: Dict[str, Any] = {
        "id": compact_text(_field(candidate, "id"), 80),
        "title": compact_text(_field(candidate, "current_title", "title"), 72),
        "company": compact_text(_field(candidate, "current_company", "company"), 72),
        "city": compact_text(_field(candidate, "city"), 32),
        "work_years": compact_text(_field(candidate, "work_years"), 24),
        "education": compact_text(_field(candidate, "education"), 24),
        "summary": compact_text(
            _field(candidate, "summary_text", "summary"), summary_limit
        ),
        "signals": signals,
        "risks": risks,
        "card_state": compact_text(
            _field(candidate, "card_decision", "decision") or "unknown", 16
        ),
        "result_index": _safe_int(_field(candidate, "result_index")),
    }
    if sample_bucket:
        result["sample_bucket"] = sample_bucket
    return result


def select_representative_candidates(
    candidates: Sequence[CandidateSummary | Mapping[str, Any]],
    expected_terms: Sequence[str] | None = None,
    *,
    limit: int = OBSERVE_SAMPLE_LIMIT,
) -> List[Dict[str, Any]]:
    """Select deterministic strong, uncertain and diverse card samples.

    The sample is for pool observation, not candidate rejection.  A card with
    missing information is routed to the uncertain stratum rather than treated
    as a mismatch.
    """

    if limit <= 0:
        return []
    unique = _dedupe_candidates(candidates)
    terms = [compact_text(item, 48).lower() for item in expected_terms or [] if item]
    strong_quota = max(1, limit // 3)
    uncertain_quota = max(1, limit // 3)

    ranked_strong = sorted(
        unique,
        key=lambda item: _strong_rank(item, terms),
    )
    strong_pool = [item for item in ranked_strong if _strong_signal_count(item, terms) > 0]

    selected: List[tuple[CandidateSummary | Mapping[str, Any], str]] = []
    seen: set[str] = set()
    _take(selected, seen, strong_pool, strong_quota, "strong_signal")

    uncertain_pool = sorted(
        [item for item in unique if _candidate_key(item) not in seen],
        key=_uncertain_rank,
    )
    uncertain_pool = [item for item in uncertain_pool if _is_uncertain(item)]
    _take(selected, seen, uncertain_pool, uncertain_quota, "uncertain")

    diversity_pool = _diversity_order(
        [item for item in unique if _candidate_key(item) not in seen]
    )
    _take(selected, seen, diversity_pool, limit - len(selected), "diversity")

    if len(selected) < limit:
        remaining = sorted(
            [item for item in unique if _candidate_key(item) not in seen],
            key=_stable_position,
        )
        _take(selected, seen, remaining, limit - len(selected), "coverage")

    return [
        compact_candidate_card(item, sample_bucket=bucket)
        for item, bucket in selected[:limit]
    ]


def candidate_pool_stats(
    candidates: Sequence[CandidateSummary | Mapping[str, Any]],
) -> Dict[str, Any]:
    """Aggregate a candidate pool without including candidate-level text."""

    unique = _dedupe_candidates(candidates)
    decisions = Counter(
        compact_text(_field(item, "card_decision", "decision") or "unknown", 24)
        for item in unique
    )
    pages = {
        str(page)
        for item in unique
        for page in [_page_number(_field(item, "page_meta"))]
        if page not in (None, "")
    }
    return {
        "raw_count": len(candidates or []),
        "unique_count": len(unique),
        "decision_counts": dict(sorted(decisions.items())),
        "with_card_signals": sum(
            1 for item in unique if _string_items(_field(item, "card_signals"), limit=1)
        ),
        "with_card_risks": sum(
            1 for item in unique if _string_items(_field(item, "card_risks"), limit=1)
        ),
        "distinct_companies": len(
            {compact_text(_field(item, "current_company", "company"), 72) for item in unique}
            - {""}
        ),
        "distinct_titles": len(
            {compact_text(_field(item, "current_title", "title"), 72) for item in unique}
            - {""}
        ),
        "distinct_cities": len(
            {compact_text(_field(item, "city"), 32) for item in unique} - {""}
        ),
        "observed_pages": sorted(pages),
    }


def build_observation_context(
    candidates: Sequence[CandidateSummary | Mapping[str, Any]],
    expected_terms: Sequence[str] | None = None,
    *,
    sample_limit: int = OBSERVE_SAMPLE_LIMIT,
    char_budget: int = OBSERVE_CONTEXT_CHAR_BUDGET,
) -> Dict[str, Any]:
    """Build the bounded pool context used by ``observe_round``."""

    samples = select_representative_candidates(
        candidates, expected_terms, limit=min(sample_limit, OBSERVE_SAMPLE_LIMIT)
    )
    payload: Dict[str, Any] = {
        "pool_stats": candidate_pool_stats(candidates),
        "sampling_note": (
            "representative_samples are deterministic strata, not the full pool; "
            "use pool_stats for counts and treat missing fields as unknown"
        ),
        "representative_samples": samples,
    }
    _fit_payload(payload, char_budget, [("representative_samples", 3)])
    return payload


def build_fetch_context(
    candidates: Sequence[CandidateSummary | Mapping[str, Any]],
    observation: Observation | Mapping[str, Any] | None = None,
    *,
    ranked_limit: int = FETCH_RANKED_LIMIT,
    disputed_limit: int = FETCH_DISPUTED_LIMIT,
    char_budget: int = FETCH_CONTEXT_CHAR_BUDGET,
) -> Dict[str, Any]:
    """Build a compact ranking plus a small set needing LLM arbitration."""

    unique = _dedupe_candidates(candidates)
    positive_terms = _string_items(
        _field(observation, "positive_signals"), limit=8, item_limit=48
    )
    terms = [item.lower() for item in positive_terms]
    ranked = sorted(unique, key=lambda item: _strong_rank(item, terms))
    ranked = ranked[: max(0, min(ranked_limit, FETCH_RANKED_LIMIT))]

    ranked_cards: List[Dict[str, Any]] = []
    for rank, item in enumerate(ranked, start=1):
        card = compact_candidate_card(item, summary_limit=88)
        ranked_cards.append(
            {
                "rank": rank,
                "id": card["id"],
                "title": card["title"],
                "company": card["company"],
                "city": card["city"],
                "summary_hint": card["summary"],
                "signals": card["signals"][:2],
                "risks": card["risks"][:2],
                "routing": _routing_bucket(item, terms),
            }
        )

    disputed_source = [
        item
        for item in ranked
        if _is_uncertain(item)
        or (
            bool(_string_items(_field(item, "card_signals"), limit=1))
            and bool(_string_items(_field(item, "card_risks"), limit=1))
        )
    ]
    disputed_cards = [
        compact_candidate_card(item, summary_limit=180, sample_bucket="needs_review")
        for item in disputed_source[: max(0, min(disputed_limit, FETCH_DISPUTED_LIMIT))]
    ]

    payload: Dict[str, Any] = {
        "pool_stats": candidate_pool_stats(candidates),
        "ranking_note": (
            "routing is prioritization only, not a match verdict; missing card facts are unknown"
        ),
        "included_count": len(ranked_cards),
        "omitted_count": max(0, len(unique) - len(ranked_cards)),
        "ranked_candidates": ranked_cards,
        "disputed_candidates": disputed_cards,
    }
    _fit_payload(
        payload,
        char_budget,
        [("disputed_candidates", 0), ("ranked_candidates", min(8, len(ranked_cards)))],
    )
    payload["included_count"] = len(payload["ranked_candidates"])
    payload["omitted_count"] = max(0, len(unique) - payload["included_count"])
    return payload


def compact_match_result(match: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep match facts and short evidence while excluding raw/detail payloads."""

    evidence = _structured_items(match.get("matched_evidence"), limit=3)
    return {
        "candidate_id": compact_text(match.get("candidate_id"), 80),
        "tier": compact_text(match.get("tier"), 8).upper(),
        "status": compact_text(match.get("status") or "completed", 24),
        "core_met_count": _safe_int(match.get("core_met_count")),
        "core_total": _safe_int(match.get("core_total")),
        "dealbreaker_hit": _safe_bool(match.get("dealbreaker_hit")),
        "confidence": compact_text(match.get("confidence"), 24),
        "recommendation_state": compact_text(
            match.get("recommendation_state"), 32
        ),
        "known_fit_score": _safe_int(match.get("known_fit_score")),
        "potential_fit_score": _safe_int(match.get("potential_fit_score")),
        "evidence_coverage_score": _safe_int(
            match.get("evidence_coverage_score")
        ),
        "summary": compact_text(match.get("summary"), 180),
        "risks": compact_text(match.get("risks"), 120),
        "recommendation": compact_text(match.get("recommendation"), 120),
        "matched_evidence": [_compact_evidence(item) for item in evidence],
        "missing_or_unclear": _string_items(
            match.get("missing_or_unclear"), limit=3, item_limit=72
        ),
        "questions_to_verify": _string_items(
            match.get("questions_to_verify"), limit=2, item_limit=88
        ),
    }


def build_match_review_context(
    match_results: Sequence[Mapping[str, Any]],
    *,
    representative_limit: int = REVIEW_REPRESENTATIVE_LIMIT,
    char_budget: int = REVIEW_CONTEXT_CHAR_BUDGET,
) -> Dict[str, Any]:
    """Aggregate current-round matches and retain only representative evidence."""

    results = list(match_results or [])
    status_counts = Counter(
        compact_text(item.get("status") or "completed", 24) for item in results
    )
    confidence_counts = Counter(
        compact_text(item.get("confidence"), 24) or "unknown" for item in results
    )
    recommendation_state_counts = Counter(
        compact_text(item.get("recommendation_state"), 32) or "unclassified"
        for item in results
    )
    missing = Counter(
        text
        for item in results
        for text in _string_items(item.get("missing_or_unclear"), limit=8, item_limit=72)
    )
    ordered = sorted(results, key=_match_representative_rank)
    representatives = [
        compact_match_result(item)
        for item in ordered[: max(0, min(representative_limit, REVIEW_REPRESENTATIVE_LIMIT))]
    ]
    payload: Dict[str, Any] = {
        "aggregate": {
            "match_count": len(results),
            "status_counts": dict(sorted(status_counts.items())),
            "dealbreaker_count": sum(
                1 for item in results if _safe_bool(item.get("dealbreaker_hit"))
            ),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "recommendation_state_counts": dict(
                sorted(recommendation_state_counts.items())
            ),
        },
        "top_evidence_gaps": [
            {"item": item, "count": count} for item, count in missing.most_common(6)
        ],
        "representative_matches": representatives,
        "omitted_match_count": max(0, len(results) - len(representatives)),
    }
    _fit_payload(
        payload,
        char_budget,
        [("representative_matches", min(3, len(representatives))), ("top_evidence_gaps", 0)],
    )
    payload["omitted_match_count"] = max(
        0, len(results) - len(payload["representative_matches"])
    )
    return payload


def compact_strategy_history(
    used_queries: Sequence[str] | None,
    round_history: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    *,
    query_limit: int = STRATEGY_QUERY_LIMIT,
    round_limit: int = STRATEGY_ROUND_LIMIT,
) -> Dict[str, Any]:
    """Return bounded query history and optional ``RoundDigest``-like records."""

    queries: List[str] = []
    for item in used_queries or []:
        query = compact_text(item, 120)
        if query and query not in queries:
            queries.append(query)
    bounded_query_limit = max(0, min(query_limit, STRATEGY_QUERY_LIMIT))
    kept_queries = queries[-bounded_query_limit:] if bounded_query_limit else []

    if isinstance(round_history, Mapping):
        raw_rounds = round_history.get("rounds") or round_history.get("recent_rounds") or []
        if not raw_rounds and any(
            key in round_history for key in ("round_index", "query", "conclusion")
        ):
            raw_rounds = [round_history]
    else:
        raw_rounds = round_history or []
    rounds = [_compact_round_digest(item) for item in raw_rounds]
    rounds = [item for item in rounds if item]
    bounded_round_limit = max(0, min(round_limit, STRATEGY_ROUND_LIMIT))
    kept_rounds = rounds[-bounded_round_limit:] if bounded_round_limit else []

    return {
        "total_query_count": len(queries),
        "omitted_query_count": max(0, len(queries) - len(kept_queries)),
        "recent_queries": kept_queries,
        "total_round_digest_count": len(rounds),
        "omitted_round_digest_count": max(0, len(rounds) - len(kept_rounds)),
        "recent_round_digests": kept_rounds,
        "note": "full duplicate-query protection remains deterministic outside this prompt",
    }


def compact_page_metadata(page_meta: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Allow-list page statistics and discard DOM/debug payloads."""

    if not isinstance(page_meta, Mapping):
        return {}
    allowed = (
        "page",
        "page_index",
        "current_page",
        "page_count",
        "observed_pages",
        "result_count",
        "total_count",
        "total_results",
        "new_count",
        "duplicate_count",
        "duplicate_rate",
        "has_next",
        "stop_reason",
    )
    result: Dict[str, Any] = {}
    for key in allowed:
        if key not in page_meta:
            continue
        value = page_meta[key]
        if isinstance(value, (bool, int, float)) or value is None:
            result[key] = value
        elif isinstance(value, (list, tuple)):
            result[key] = [compact_text(item, 40) for item in value[:12]]
        else:
            result[key] = compact_text(value, 160)
    return result


def json_text(value: Any) -> str:
    """Stable compact JSON used by prompts and budget measurements."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def shrink_prompt_value(value: str, char_budget: int) -> str:
    """Shrink a prompt section while keeping JSON sections valid."""

    if len(value) <= char_budget:
        return value
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return compact_text(value, char_budget)
    _shrink_structure(payload, char_budget)
    return json_text(payload)


def _field(value: Any, *names: str) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
        return None
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value.get(name)
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


def _string_items(value: Any, *, limit: int, item_limit: int = 80) -> List[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = re.split(r"[、,，;；\n]+", value)
        value = decoded
    if not isinstance(value, (list, tuple, set)):
        return []
    result: List[str] = []
    for item in value:
        if isinstance(item, Mapping):
            text = item.get("item") or item.get("requirement") or item.get("evidence") or ""
        else:
            text = item
        text = compact_text(text, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _structured_items(value: Any, *, limit: int) -> List[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]
    if not isinstance(value, list):
        return []
    return value[:limit]


def _compact_evidence(value: Any) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {"evidence": compact_text(value, 120)}
    result: Dict[str, str] = {}
    for key in ("requirement", "criterion", "status", "evidence", "source", "strength"):
        if value.get(key) not in (None, ""):
            result[key] = compact_text(value.get(key), 120 if key == "evidence" else 64)
    return result


def _candidate_key(candidate: Any) -> str:
    candidate_id = compact_text(_field(candidate, "id"), 100)
    if candidate_id:
        return "id:" + candidate_id
    return "fallback:{}|{}|{}|{}".format(
        compact_text(_field(candidate, "current_title", "title"), 72),
        compact_text(_field(candidate, "current_company", "company"), 72),
        compact_text(_field(candidate, "city"), 32),
        _safe_int(_field(candidate, "result_index")),
    )


def _dedupe_candidates(candidates: Sequence[Any]) -> List[Any]:
    result: List[Any] = []
    seen: set[str] = set()
    for item in candidates or []:
        key = _candidate_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _candidate_text(candidate: Any) -> str:
    return " ".join(
        compact_text(_field(candidate, name), 240)
        for name in ("current_title", "current_company", "summary_text")
    ).lower()


def _strong_signal_count(candidate: Any, terms: Sequence[str]) -> int:
    text = _candidate_text(candidate)
    term_hits = sum(1 for term in terms if term and term in text)
    explicit = len(_string_items(_field(candidate, "card_signals"), limit=6))
    fetch_state = 1 if str(_field(candidate, "card_decision") or "").lower() == "fetch" else 0
    return term_hits + explicit + fetch_state


def _strong_rank(candidate: Any, terms: Sequence[str]) -> tuple[Any, ...]:
    return (
        -_strong_signal_count(candidate, terms),
        len(_string_items(_field(candidate, "card_risks"), limit=6)),
        _stable_position(candidate),
    )


def _is_uncertain(candidate: Any) -> bool:
    state = str(_field(candidate, "card_decision") or "").lower()
    summary = compact_text(_field(candidate, "summary_text", "summary"), 200)
    signals = _string_items(_field(candidate, "card_signals"), limit=2)
    risks = _string_items(_field(candidate, "card_risks"), limit=2)
    return state in {"", "maybe", "unknown"} or len(summary) < 48 or (signals and risks)


def _uncertain_rank(candidate: Any) -> tuple[Any, ...]:
    state = str(_field(candidate, "card_decision") or "").lower()
    signals = len(_string_items(_field(candidate, "card_signals"), limit=6))
    summary_len = len(compact_text(_field(candidate, "summary_text", "summary"), 240))
    return (
        0 if state == "maybe" else 1,
        -signals,
        summary_len,
        _stable_position(candidate),
    )


def _stable_position(candidate: Any) -> tuple[int, str]:
    return (_safe_int(_field(candidate, "result_index")), _candidate_key(candidate))


def _diversity_order(candidates: Sequence[Any]) -> List[Any]:
    remaining = sorted(candidates, key=_stable_position)
    result: List[Any] = []
    seen_companies: set[str] = set()
    seen_titles: set[str] = set()
    seen_cities: set[str] = set()
    while remaining:
        best = min(
            remaining,
            key=lambda item: (
                -sum(
                    (
                        compact_text(_field(item, "current_company", "company"), 72)
                        not in seen_companies,
                        compact_text(_field(item, "current_title", "title"), 72)
                        not in seen_titles,
                        compact_text(_field(item, "city"), 32) not in seen_cities,
                    )
                ),
                _stable_position(item),
            ),
        )
        remaining.remove(best)
        result.append(best)
        seen_companies.add(compact_text(_field(best, "current_company", "company"), 72))
        seen_titles.add(compact_text(_field(best, "current_title", "title"), 72))
        seen_cities.add(compact_text(_field(best, "city"), 32))
    return result


def _take(
    selected: MutableSequence[tuple[Any, str]],
    seen: set[str],
    source: Iterable[Any],
    limit: int,
    bucket: str,
) -> None:
    if limit <= 0:
        return
    taken = 0
    for item in source:
        key = _candidate_key(item)
        if key in seen:
            continue
        selected.append((item, bucket))
        seen.add(key)
        taken += 1
        if taken >= limit:
            break


def _routing_bucket(candidate: Any, terms: Sequence[str]) -> str:
    if _strong_signal_count(candidate, terms) > 0:
        return "strong_signal"
    if _is_uncertain(candidate):
        return "uncertain"
    return "diversity"


def _page_number(page_meta: Any) -> Any:
    if not isinstance(page_meta, Mapping):
        return None
    return page_meta.get("page") or page_meta.get("page_index") or page_meta.get("current_page")


def _match_representative_rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
    tier_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    tier = compact_text(item.get("tier"), 8).upper()
    status = compact_text(item.get("status") or "completed", 24)
    evidence_count = len(_structured_items(item.get("matched_evidence"), limit=8))
    return (
        0 if status == "completed" else 1,
        tier_order.get(tier, 4),
        -evidence_count,
        compact_text(item.get("candidate_id"), 80),
    )


def _compact_round_digest(value: Any) -> Dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    elif not isinstance(value, Mapping) and hasattr(value, "__dict__"):
        value = vars(value)
    if not isinstance(value, Mapping):
        return {}
    scalar_fields = (
        "round_index",
        "stage",
        "query",
        "search_hypothesis_type",
        "page_count",
        "raw_count",
        "new_count",
        "duplicate_rate",
        "detail_fetch_count",
        "matched_count",
        "pending_match_count",
        "viable_count",
        "effective_pool_score",
        "conclusion",
    )
    result: Dict[str, Any] = {}
    for key in scalar_fields:
        if key not in value or value[key] in (None, ""):
            continue
        item = value[key]
        result[key] = item if isinstance(item, (int, float, bool)) else compact_text(item, 180)
    filters = value.get("filters")
    if isinstance(filters, Mapping):
        result["filters"] = {
            compact_text(key, 32): compact_text(item, 80)
            if not isinstance(item, (int, float, bool, list, tuple))
            else ([compact_text(v, 40) for v in item[:8]] if isinstance(item, (list, tuple)) else item)
            for key, item in list(filters.items())[:8]
        }
    selection_counts = value.get("selection_counts")
    if isinstance(selection_counts, Mapping):
        result["selection_counts"] = {
            compact_text(key, 32): _safe_int(item)
            for key, item in list(selection_counts.items())[:8]
        }
    state_counts = value.get("recommendation_state_counts")
    if isinstance(state_counts, Mapping):
        result["recommendation_state_counts"] = {
            compact_text(key, 40): _safe_int(item)
            for key, item in list(state_counts.items())[:8]
        }
    return result


def _fit_payload(
    payload: Dict[str, Any],
    char_budget: int,
    removable_lists: Sequence[tuple[str, int]],
) -> None:
    if char_budget <= 0:
        return
    while len(json_text(payload)) > char_budget:
        changed = False
        for key, minimum in removable_lists:
            values = payload.get(key)
            if isinstance(values, list) and len(values) > minimum:
                values.pop()
                changed = True
                break
        if not changed:
            _shrink_structure(payload, char_budget)
            break


def _shrink_structure(value: Any, char_budget: int) -> None:
    """Mutate a JSON-compatible structure until its serialized form fits."""

    guard = 0
    while len(json_text(value)) > max(2, char_budget) and guard < 2_000:
        guard += 1
        lists: List[List[Any]] = []
        strings: List[tuple[Mapping[str, Any] | List[Any], Any, str]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, item in node.items():
                    if isinstance(item, list):
                        lists.append(item)
                        walk(item)
                    elif isinstance(item, dict):
                        walk(item)
                    elif isinstance(item, str):
                        strings.append((node, key, item))
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    if isinstance(item, (dict, list)):
                        walk(item)
                    elif isinstance(item, str):
                        strings.append((node, index, item))

        walk(value)
        droppable = [items for items in lists if len(items) > 1]
        if droppable:
            max(droppable, key=len).pop()
            continue
        long_strings = [entry for entry in strings if len(entry[2]) > 16]
        if long_strings:
            container, key, text = max(long_strings, key=lambda entry: len(entry[2]))
            container[key] = compact_text(text, max(16, len(text) // 2))  # type: ignore[index]
            continue
        break
