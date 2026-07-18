"""Domain dataclasses used by the agent and storage layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SearchPlan:
    query: str
    position_filter: str = ""
    scope: str = "全部经历"
    match_mode: str = "all"
    filters: Dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    expected_signal: List[str] = field(default_factory=list)
    risk: str = ""
    search_hypothesis_type: str = "core_background"
    search_hypothesis_text: str = ""
    search_hypothesis_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "position_filter": self.position_filter,
            "scope": self.scope,
            "match_mode": self.match_mode,
            "filters": dict(self.filters or {}),
            "intent": self.intent,
            "expected_signal": list(self.expected_signal or []),
            "risk": self.risk,
            "search_hypothesis_type": self.search_hypothesis_type,
            "search_hypothesis_text": self.search_hypothesis_text,
            "search_hypothesis_id": self.search_hypothesis_id,
        }


@dataclass
class CandidateSummary:
    id: str = ""
    session_id: str = ""
    round_id: str = ""
    profile_url: str = ""
    dedupe_key: str = ""
    name: str = ""
    age: str = ""
    current_title: str = ""
    current_company: str = ""
    city: str = ""
    work_years: str = ""
    education: str = ""
    summary_text: str = ""
    raw_text: str = ""
    result_index: int = 0
    pre_score: int = 0
    pre_score_reasons: List[str] = field(default_factory=list)
    status: str = "summary_seen"
    card_decision: str = "maybe"
    card_signals: List[str] = field(default_factory=list)
    card_risks: List[str] = field(default_factory=list)
    card_reason: str = ""
    page_meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "round_id": self.round_id,
            "profile_url": self.profile_url,
            "dedupe_key": self.dedupe_key,
            "name": self.name,
            "age": self.age,
            "current_title": self.current_title,
            "current_company": self.current_company,
            "city": self.city,
            "work_years": self.work_years,
            "education": self.education,
            "summary_text": self.summary_text,
            "raw_text": self.raw_text,
            "result_index": self.result_index,
            "pre_score": self.pre_score,
            "pre_score_reasons": list(self.pre_score_reasons or []),
            "status": self.status,
            "card_decision": self.card_decision,
            "card_signals": list(self.card_signals or []),
            "card_risks": list(self.card_risks or []),
            "card_reason": self.card_reason,
            "page_meta": dict(self.page_meta or {}),
        }


@dataclass
class CandidateDetail:
    candidate_id: str
    resume_text: str
    resume_summary: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    capture_status: str = "success"
    error_message: str = ""
    is_gold_collar: bool = False


@dataclass
class MatchResult:
    candidate_id: str
    session_id: str
    round_id: str
    tier: str
    core_met_count: int = 0
    core_total: int = 0
    dealbreaker_hit: bool = False
    summary: str = ""
    risks: str = ""
    recommendation: str = ""
    detail: str = ""
    raw_response: str = ""
    status: str = "completed"
    criteria_version_id: str = ""
    matched_evidence: List[Dict[str, Any]] = field(default_factory=list)
    missing_or_unclear: List[str] = field(default_factory=list)
    questions_to_verify: List[str] = field(default_factory=list)
    confidence: str = ""
    prompt_version: str = ""
    model_name: str = ""
    model_config_hash: str = ""
    input_hash: str = ""
    resume_hash: str = ""
    match_score: int = 0


@dataclass
class CriteriaVersion:
    id: str = ""
    session_id: str = ""
    version: int = 1
    status: str = "draft"
    keywords_text: str = ""
    requirements_text: str = ""
    source_jd_text: str = ""
    source_user_notes: str = ""
    ai_raw_response: Dict[str, Any] = field(default_factory=dict)
    created_by: str = "ai"
    confirmed_by: str = ""


@dataclass
class Observation:
    round_quality: str
    raw_count: int
    deduped_count: int
    estimated_relevant_count: int
    noise_patterns: List[str]
    positive_signals: List[str]
    recommended_round_type: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_quality": self.round_quality,
            "raw_count": self.raw_count,
            "deduped_count": self.deduped_count,
            "estimated_relevant_count": self.estimated_relevant_count,
            "noise_patterns": list(self.noise_patterns or []),
            "positive_signals": list(self.positive_signals or []),
            "recommended_round_type": self.recommended_round_type,
            "reason": self.reason,
        }


@dataclass
class FetchDecision:
    action: str
    round_type: str
    candidate_ids: List[str] = field(default_factory=list)
    fetch_limit: int = 0
    sampling_strategy: Dict[str, int] = field(default_factory=dict)
    match_wait_policy: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    selection_buckets: Dict[str, List[str]] = field(default_factory=dict)
    selection_reasons: Dict[str, str] = field(default_factory=dict)
    audit_candidate_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "round_type": self.round_type,
            "candidate_ids": list(self.candidate_ids or []),
            "fetch_limit": self.fetch_limit,
            "sampling_strategy": dict(self.sampling_strategy or {}),
            "match_wait_policy": dict(self.match_wait_policy or {}),
            "reason": self.reason,
            "selection_buckets": {
                key: list(value or [])
                for key, value in (self.selection_buckets or {}).items()
            },
            "selection_reasons": dict(self.selection_reasons or {}),
            "audit_candidate_ids": list(self.audit_candidate_ids or []),
        }


@dataclass
class RoundReview:
    action: str
    summary: str
    next_plan: Optional[SearchPlan] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "summary": self.summary,
            "next_plan": self.next_plan.to_dict() if self.next_plan else None,
            "evidence": dict(self.evidence or {}),
        }


@dataclass
class PaginationVerdict:
    """Agent decision on whether to keep paging the current search."""

    action: str  # "continue" | "stop"
    additional_pages: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "additional_pages": int(self.additional_pages or 0),
            "reason": self.reason,
        }
