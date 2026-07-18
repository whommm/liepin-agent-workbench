"""State constants for sourcing sessions, rounds, and candidates."""

from __future__ import annotations

from enum import Enum


class SessionStatus(str, Enum):
    DRAFT = "draft"
    CRITERIA_DRAFT = "criteria_draft"
    CRITERIA_CONFIRMED = "criteria_confirmed"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    USER_DIALOG = "user_dialog"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RoundStatus(str, Enum):
    PLANNED = "planned"
    SEARCHING = "searching"
    OBSERVED = "observed"
    DETAIL_DECISION_MADE = "detail_decision_made"
    FETCHING_DETAILS = "fetching_details"
    MATCHING = "matching"
    REVIEWED = "reviewed"
    SKIPPED = "skipped"
    FAILED = "failed"


class CandidateStatus(str, Enum):
    SUMMARY_SEEN = "summary_seen"
    PRE_SCORED = "pre_scored"
    DETAIL_QUEUED = "detail_queued"
    DETAIL_FETCHING = "detail_fetching"
    DETAIL_FETCHED = "detail_fetched"
    DETAIL_FAILED = "detail_failed"
    QUICK_CHECKED = "quick_checked"
    MATCH_QUEUED = "match_queued"
    MATCHING = "matching"
    MATCHED = "matched"
    SHORTLISTED = "shortlisted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class RoundType(str, Enum):
    SKIP_DETAIL = "skip_detail"
    SAMPLE_DETAIL = "sample_detail"
    VALIDATE_DETAIL = "validate_detail"
    HARVEST_DETAIL = "harvest_detail"


class AgentEventType(str, Enum):
    SESSION_CREATED = "session_created"
    JOB_UNDERSTANDING = "job_understanding"
    SEARCH_PLAN = "search_plan"
    SEARCH_EXECUTED = "search_executed"
    PAGINATION_DECISION = "pagination_decision"
    RESULT_OBSERVED = "result_observed"
    DETAIL_DECISION = "detail_decision"
    DETAIL_FETCHED = "detail_fetched"
    MATCH_RESULT = "match_result"
    ROUND_REVIEW = "round_review"
    SESSION_COMPLETED = "session_completed"
    ERROR = "error"
