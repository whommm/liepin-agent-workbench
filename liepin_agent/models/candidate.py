"""Candidate models for Liepin automation."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Candidate:
    """A candidate record stored in the local repository."""

    id: str
    platform: str = "liepin"
    platform_candidate_id: Optional[str] = None
    profile_url: str = ""
    name: str = ""
    current_title: str = ""
    current_company: str = ""
    city: str = ""
    work_years: str = ""
    education: str = ""
    resume_text: str = ""
    resume_summary: str = ""
    raw_payload_json: str = ""
    capture_status: str = "summary_only"
    workflow_status: str = "new"
    notes: str = ""
    last_source_at: str = ""
    last_enriched_at: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CandidateSource:
    """The relationship between a candidate and a search task."""

    id: str
    candidate_id: str
    search_task_id: str
    keyword: str
    page_number: Optional[int] = None
    rank_index: Optional[int] = None
    fetched_at: str = ""
