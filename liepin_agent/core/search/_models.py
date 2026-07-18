"""Models and exceptions for Liepin search."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


class LiepinSearchError(Exception):
    """Base error for Liepin search execution."""


class LiepinSearchPageChangedError(LiepinSearchError):
    """Raised when the search page no longer matches expected selectors."""


class LiepinSearchNoResultsError(LiepinSearchError):
    """Raised when search succeeds but the page contains no candidate results."""


class SearchCursorLostError(LiepinSearchError):
    """Raised when a pagination cursor cannot be validated or recovered."""


@dataclass
class LiepinSearchCandidate:
    """Candidate summary captured from the result list page."""

    name: str = ""
    age: str = ""
    gender: str = ""
    current_title: str = ""
    current_company: str = ""
    city: str = ""
    work_years: str = ""
    education: str = ""
    profile_url: str = ""
    summary: str = ""
    raw_text: str = ""
    result_index: int = -1
    page_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PageYieldStats:
    """Marginal value observed after parsing one result page."""

    page_num: int
    raw_count: int
    new_unique: int
    duplicate_count: int = 0
    potential_count: int = 0
    validate_count: int = 0
    # Signal-only verdict from AdaptivePaginationPolicy.assess for this page.
    # None means no policy was active; the policy never terminates pagination
    # directly in agent-driven (checkpoint) mode.
    policy_continue: Optional[bool] = None
    policy_reason: str = ""

    @property
    def duplicate_rate(self) -> float:
        if self.raw_count <= 0:
            return 0.0
        return min(1.0, max(0.0, self.duplicate_count / self.raw_count))

    @property
    def promising_count(self) -> int:
        return self.potential_count + self.validate_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_num": self.page_num,
            "raw_count": self.raw_count,
            "new_unique": self.new_unique,
            "duplicate_count": self.duplicate_count,
            "duplicate_rate": round(self.duplicate_rate, 4),
            "potential_count": self.potential_count,
            "validate_count": self.validate_count,
            "promising_count": self.promising_count,
            "policy_continue": self.policy_continue,
            "policy_reason": self.policy_reason,
        }


@dataclass
class SearchCursor:
    """In-memory pagination cursor handed to the agent between search batches.

    Not persisted and not shared across processes: on restart or session
    recovery the cursor is dropped and the round finishes with already
    persisted candidates (``known_candidate_keys`` replay prevents re-entry).
    """

    query: str
    filters: Dict[str, Any] = field(default_factory=dict)
    match_mode: str = ""
    scope: str = ""
    position_filter: str = ""
    page_num: int = 0  # result page the browser is currently parked on
    seen_keys: Set[str] = field(default_factory=set)  # cross-batch dedupe keys
    history: List[PageYieldStats] = field(default_factory=list)
    total_results: Optional[int] = None  # from page_meta
    exhausted: bool = False  # no next result page available


@dataclass(frozen=True)
class PaginationDecision:
    continue_paging: bool
    reason: str
    low_yield_streak: int = 0


@dataclass(frozen=True)
class AdaptivePaginationPolicy:
    """Bounded policy that continues only while later pages add useful cards."""

    min_pages: int = 3
    max_pages: int = 10
    low_yield_patience: int = 2
    min_new_unique: int = 3
    min_promising: int = 1
    duplicate_rate_threshold: float = 0.80

    @property
    def effective_min_pages(self) -> int:
        return min(self.effective_max_pages, max(1, int(self.min_pages)))

    @property
    def effective_max_pages(self) -> int:
        # Ten pages is a product guardrail, not a selector-dependent assumption.
        return min(10, max(1, int(self.max_pages)))

    def assess(self, history: Sequence[PageYieldStats]) -> PaginationDecision:
        if not history:
            return PaginationDecision(True, "尚未观察结果页")
        latest = history[-1]
        if latest.page_num >= self.effective_max_pages:
            return PaginationDecision(False, "达到分页硬上限")
        if latest.page_num < self.effective_min_pages:
            return PaginationDecision(True, "尚未达到最小观察页数")

        streak = 0
        for stats in reversed(history):
            if not self._is_low_yield(stats):
                break
            streak += 1
        if streak >= max(1, int(self.low_yield_patience)):
            return PaginationDecision(
                False,
                "连续低边际收益页：新增、重复率或潜力密度不足",
                streak,
            )
        return PaginationDecision(
            True,
            "当前页仍有新增潜力候选人" if streak == 0 else "低收益尚未持续，继续验证下一页",
            streak,
        )

    def _is_low_yield(self, stats: PageYieldStats) -> bool:
        if stats.raw_count <= 0 or stats.new_unique <= 0:
            return True
        if stats.duplicate_rate >= max(0.0, self.duplicate_rate_threshold):
            return True
        if stats.promising_count < max(0, int(self.min_promising)):
            return True
        return (
            stats.new_unique < max(1, int(self.min_new_unique))
            and stats.promising_count < max(1, int(self.min_promising) + 1)
        )


@dataclass
class LiepinSearchControls:
    """Resolved primary controls on the Liepin search page."""

    search_input: object = None
    search_button: object = None


@dataclass
class LiepinFilterFieldSpec:
    """One filter field definition resolved from the live search page."""

    title: str
    field_type: str
    container_selector: str
    title_text: str = ""
    fallback_container_selectors: Tuple[str, ...] = ()
    input_selector: str = "input.ant-select-selection-search-input"
    low_input_selector: str = ""
    high_input_selector: str = ""
    confirm_selector: str = ""
    requires_expanded: bool = False

    @property
    def container_selectors(self) -> Tuple[str, ...]:
        return (self.container_selector,) + tuple(self.fallback_container_selectors)
