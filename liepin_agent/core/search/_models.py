"""Models and exceptions for Liepin search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


class LiepinSearchError(Exception):
    """Base error for Liepin search execution."""


class LiepinSearchPageChangedError(LiepinSearchError):
    """Raised when the search page no longer matches expected selectors."""


class LiepinSearchNoResultsError(LiepinSearchError):
    """Raised when search succeeds but the page contains no candidate results."""


@dataclass
class LiepinSearchCandidate:
    """Candidate summary captured from the result list page."""

    name: str = ""
    age: str = ""
    current_title: str = ""
    current_company: str = ""
    city: str = ""
    work_years: str = ""
    education: str = ""
    profile_url: str = ""
    summary: str = ""
    result_index: int = -1


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
