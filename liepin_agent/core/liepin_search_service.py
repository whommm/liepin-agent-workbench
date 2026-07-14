"""Liepin search execution and result list extraction.

This module provides a facade class that composes functionality from
specialized mixins in the search/ package.
"""

from __future__ import annotations

from .search._base_mixin import _BaseMixin
from .search._controls_mixin import _ControlsMixin
from .search._executor_mixin import _ExecutorMixin
from .search._position_filter_mixin import _PositionFilterMixin
from .search._filters_mixin import _FiltersMixin
from .search._pagination_mixin import _PaginationMixin
from .search._extraction_mixin import _ExtractionMixin
from .search._detail_mixin import _DetailMixin
from .search._remaining_mixin import _RemainingMixin

from .search._models import (
    AdaptivePaginationPolicy,
    LiepinSearchCandidate,
    LiepinSearchControls,
    LiepinFilterFieldSpec,
    LiepinSearchError,
    LiepinSearchPageChangedError,
    LiepinSearchNoResultsError,
    PageYieldStats,
    PaginationDecision,
)

from .liepin_browser import LiepinBrowserManager


class LiepinSearchService(
    _BaseMixin,
    _ControlsMixin,
    _ExecutorMixin,
    _PositionFilterMixin,
    _FiltersMixin,
    _PaginationMixin,
    _ExtractionMixin,
    _DetailMixin,
    _RemainingMixin,
):
    """Execute keyword searches on Liepin and parse result cards.

    The selectors are intentionally centralized so later site updates only need
    to be fixed in one place.
    """

    SEARCH_INPUT_SELECTORS = [
        "div.search-auto-complete-box div.auto-input-wrap-v3 "
        "input.ant-select-selection-search-input",
        "input.search-component-input",
        ".search-component-input input",
        'input[placeholder*="搜索"]',
        'input[placeholder*="职位"]',
        'input[placeholder*="关键词"]',
        'input[type="text"]',
    ]
    SEARCH_BUTTON_SELECTORS = [
        "button.search-btn",
        'button:has-text("搜索")',
        'button:has-text("找人")',
        'button[type="submit"]',
    ]
    RESULT_CARD_SELECTORS = [
        # Primary: Live Liepin result list uses a table with new-resume-card class
        "table.new-resume-card tbody tr",
        ".new-resume-card tbody tr",
        "table[class*='resume'] tbody tr",
        # Legacy fallback selectors (kept for backward compatibility)
        '[data-selector="jobseeker-item"]',
        ".sojob-item-main",
        ".candidate-card",
        ".sojob-list li",
        ".resume-list-item",
        ".ant-list-item",
        ".resume-item",
        ".ant-list-items > li",
        ".resume-list > div",
        ".resume-card",
        ".jobseeker-item",
        ".resume-list-content > div",
        "[class*='resume-item']",
        "[class*='candidate']",
    ]
    PROFILE_LINK_SELECTORS = [
        'a[href*="/resume/"]',
        'a[href*="/search/detail"]',
        'a[href*="/candidates/"]',
        "a",
    ]
    NEXT_PAGE_SELECTORS = [
        # Primary: Ant Design pagination next button (li element)
        ".ant-pagination-next:not(.ant-pagination-disabled)",
        'li[title="下一页"]:not(.ant-pagination-disabled)',
        # Secondary: button inside the next page li
        ".ant-pagination-next button",
        ".ant-pagination-next a",
        # Tertiary: generic pagination link (used by Liepin)
        'li[title="下一页"]:not(.ant-pagination-disabled) .ant-pagination-item-link',
        'li[title="下一页"] .ant-pagination-item-link',
        'li[title="下一页"] button',
        # Alternative: look for the last pagination item link that's not disabled
        ".ant-pagination > li:not(.ant-pagination-disabled):last-child .ant-pagination-item-link",
        ".ant-pagination > li:nth-last-child(1):not(.ant-pagination-disabled) .ant-pagination-item-link",
        ".ant-pagination > li:nth-last-child(2):not(.ant-pagination-disabled) .ant-pagination-item-link",
        # Alternative pagination class names
        ".pagination-next:not(.disabled)",
        ".lp-pagination-next",
        # Attribute-based selectors
        '[aria-label*="下一页"]',
        '[title="下一页"]',
        '[title*="下一页"]',
    ]
    LOADING_SELECTORS = [
        ".ant-spin.ant-spin-spinning",
        ".resume-spin-box",
        ".loading",
        "[class*='loading']",
    ]
    MANAGED_FILTER_TITLES = (
        "职位名称",
        "目前城市",
        "期望城市",
        "工作年限",
        "教育经历",
        "年龄",
        "性别",
        "活跃度",
        "期望年薪",
        "公司名称",
    )
    FILTER_FIELD_SPECS = {
        "目前城市": LiepinFilterFieldSpec(
            title="目前城市",
            field_type="city_modal",
            container_selector=(
                "div.search-item.sfilter-city:has("
                "span.search-item-title:has-text('目前城市'))"
            ),
            title_text="目前城市",
            fallback_container_selectors=(
                "div.search-item.sfilter-city:has-text('目前城市')",
            ),
            requires_expanded=True,
        ),
        "期望城市": LiepinFilterFieldSpec(
            title="期望城市",
            field_type="city_modal",
            container_selector=(
                "div.search-item.sfilter-city:has("
                "span.search-item-title:has-text('期望城市'))"
            ),
            title_text="期望城市",
            fallback_container_selectors=(
                "div.search-item.sfilter-city:has-text('期望城市')",
            ),
            requires_expanded=True,
        ),
        "工作年限": LiepinFilterFieldSpec(
            title="工作年限",
            field_type="tag",
            container_selector="div.search-item.sfilter-work-year",
            title_text="工作年限",
            requires_expanded=True,
        ),
        "教育经历": LiepinFilterFieldSpec(
            title="教育经历",
            field_type="tag",
            container_selector="div.search-item.sfilter-edu",
            title_text="教育经历",
            requires_expanded=True,
        ),
        "院校要求": LiepinFilterFieldSpec(
            title="院校要求",
            field_type="tag",
            container_selector="div.search-item.sfilter-additional",
            title_text="院校要求",
            requires_expanded=True,
        ),
        "年龄": LiepinFilterFieldSpec(
            title="年龄",
            field_type="range",
            container_selector="div.search-item.age-box:has(#ageLow):has(#ageHigh)",
            title_text="年龄",
            fallback_container_selectors=(
                "div.search-item:has(#ageLow)",
                "div.sfilter-other-condition:has-text('年龄')",
            ),
            low_input_selector="#ageLow, input.age-input[placeholder='岁']",
            high_input_selector="#ageHigh, input.age-input[placeholder='不限']",
            confirm_selector=".shadow-box-submit-btn",
            requires_expanded=True,
        ),
        "性别": LiepinFilterFieldSpec(
            title="性别",
            field_type="dropdown",
            container_selector="div.search-item:has(.sexSelectStyle)",
            title_text="性别",
            fallback_container_selectors=(
                "div.search-item:has-text('性别')",
                "div.ant-select.ant-select-lg.h-select.sexSelectStyle.gray.ant-select-single.ant-select-show-arrow",
            ),
            input_selector=(
                ".sexSelectStyle input.ant-select-selection-search-input"
            ),
            requires_expanded=True,
        ),
        "活跃度": LiepinFilterFieldSpec(
            title="活跃度",
            field_type="dropdown",
            container_selector=(
                "div.search-item:has(.sfilter-other-select):has("
                "span.search-item-title:has-text('活跃度'))"
            ),
            title_text="活跃度",
            fallback_container_selectors=(
                "div.search-item:has-text('活跃度')",
                "div.search-item:has-text('活跃度') div.ant-select",
            ),
            input_selector=(
                ".sfilter-other-select input.ant-select-selection-search-input"
            ),
            requires_expanded=True,
        ),
        "跳槽频率": LiepinFilterFieldSpec(
            title="跳槽频率",
            field_type="dropdown",
            container_selector="div.search-item:has-text('跳槽频率')",
            title_text="跳槽频率",
            fallback_container_selectors=(
                "div.search-item.line-wrap:has-text('跳槽频率')",
                "div.search-item:has(#rc_select_10)",
            ),
            requires_expanded=True,
        ),
        "语言": LiepinFilterFieldSpec(
            title="语言",
            field_type="tag",
            container_selector="div.search-item.sfilter-lang",
            title_text="语言",
            requires_expanded=True,
        ),
        "期望年薪": LiepinFilterFieldSpec(
            title="期望年薪",
            field_type="range",
            container_selector="div.search-item:has(#wantSalaryLow)",
            title_text="期望年薪",
            fallback_container_selectors=("div.search-item:has-text('期望年薪')",),
            low_input_selector="#wantSalaryLow",
            high_input_selector="#wantSalaryHigh",
            confirm_selector=".shadow-box-submit-btn",
            requires_expanded=True,
        ),
        "目前年薪": LiepinFilterFieldSpec(
            title="目前年薪",
            field_type="range",
            container_selector="div.search-item:has(#nowSalaryLow)",
            title_text="目前年薪",
            fallback_container_selectors=("div.search-item:has-text('目前年薪')",),
            low_input_selector="#nowSalaryLow",
            high_input_selector="#nowSalaryHigh",
            confirm_selector=".shadow-box-submit-btn",
            requires_expanded=True,
        ),
        "毕业院校": LiepinFilterFieldSpec(
            title="毕业院校",
            field_type="autocomplete",
            container_selector="div.search-item.sfilter-school-box",
            title_text="毕业院校",
            confirm_selector=".sfilter-school-box .shadow-box-submit-btn",
            requires_expanded=True,
        ),
        "专业名称": LiepinFilterFieldSpec(
            title="专业名称",
            field_type="autocomplete",
            container_selector="div.search-item.sfilter-speciality-box",
            title_text="专业名称",
            confirm_selector=".sfilter-speciality-box .shadow-box-submit-btn",
            requires_expanded=True,
        ),
        "公司名称": LiepinFilterFieldSpec(
            title="公司名称",
            field_type="autocomplete",
            container_selector='div.search-item:has(span.search-item-title:has-text("公司名称"))',
            title_text="公司名称",
            fallback_container_selectors=(
                "div.search-item:has-text('公司名称')",
            ),
            input_selector=(
                ".auto-select-comp-shadow-box .auto-select-base "
                "input.ant-select-selection-search-input"
            ),
            confirm_selector="",
            requires_expanded=False,
        ),
    }

    def __init__(self, browser_manager: LiepinBrowserManager):
        self.browser_manager = browser_manager
