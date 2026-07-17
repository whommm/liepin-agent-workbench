"""Pydantic schemas for the workbench API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    title: str = Field(default="未命名岗位")
    jd_text: str
    user_notes: str = ""
    mode: str = "自动"
    max_rounds: int = 10
    max_detail_fetches: int = 999
    max_runtime_minutes: int = 90
    target_effective_count: int = 999
    add_to_pool: bool = False


class CriteriaConfirmRequest(BaseModel):
    requirements_text: str
    selected_direction: str = ""
    keywords_text: str = ""
    criteria_items: list[dict] = Field(default_factory=list)
    personas: list[dict] = Field(default_factory=list)


class JobProfileUpdateRequest(BaseModel):
    requirements_text: str
    keywords_text: str = ""
    selected_direction: str = ""
    criteria_items: list[dict] = Field(default_factory=list)
    personas: list[dict] = Field(default_factory=list)
    confirm: bool = False


class ConfigUpdateRequest(BaseModel):
    api_base_url: str = ""
    api_key: str = ""
    model_name: str = "deepseek-chat"
    llm_provider: str = "openai"
    timeout: int = 300
    backend_api_base_url: str = ""
    backend_api_key: str = ""
    backend_model_name: str = ""
    backend_llm_provider: str = "openai"
    liepin_browser_channel: str = "msedge"
    liepin_browser_profile_dir: str = "browser_profile/liepin"
    greeting_template: str = ""
    greet_gold_only: bool = False


class PoolReorderRequest(BaseModel):
    session_ids: list[str]


class CandidateFeedbackRequest(BaseModel):
    feedback_label: str
    corrected_tier: str = ""
    reason_codes: list[str] = Field(default_factory=list)
    note: str = ""


class CandidateOutcomeRequest(BaseModel):
    outcome: str
    note: str = ""
    occurred_at: str = ""


class RankingFeedbackRequest(BaseModel):
    preferred_candidate_id: str
    other_candidate_id: str
    reason: str = ""


class SearchHypothesisUpdateRequest(BaseModel):
    status: str | None = None
    priority: float | None = None


class ApiResult(BaseModel):
    ok: bool = True
    error_code: str = ""
    message: str = ""
