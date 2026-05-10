"""Liepin search execution and result list extraction."""

import logging
import re
from dataclasses import dataclass
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from .liepin_browser import LiepinBrowserManager

try:
    from playwright.sync_api import Error, Page
except ImportError:  # pragma: no cover
    Error = Exception
    Page = None

logger = logging.getLogger(__name__)


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


class LiepinSearchService:
    """Execute keyword searches on Liepin and parse result cards.

    The selectors are intentionally centralized so later site updates only need
    to be fixed in one place.
    """

    SEARCH_INPUT_SELECTORS = [
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
    FILTER_FIELD_SPECS = {
        "目前城市": LiepinFilterFieldSpec(
            title="目前城市",
            field_type="city_modal",
            container_selector="div.search-item.sfilter-city",
            title_text="目前城市",
            fallback_container_selectors=(
                "div.search-item.sfilter-city:has-text('目前城市')",
            ),
            requires_expanded=True,
        ),
        "期望城市": LiepinFilterFieldSpec(
            title="期望城市",
            field_type="city_modal",
            container_selector="div.search-item.sfilter-city",
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
            container_selector="div.age-box",
            title_text="年龄",
            fallback_container_selectors=(
                "div.search-item:has(#ageLow)",
                "div.sfilter-other-condition:has-text('年龄')",
            ),
            low_input_selector="#ageLow, input.age-input[placeholder='岁']",
            high_input_selector="#ageHigh, input.age-input[placeholder='不限']",
            confirm_selector=".age-shadow-box .shadow-box-submit-btn",
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
            requires_expanded=True,
        ),
        "活跃度": LiepinFilterFieldSpec(
            title="活跃度",
            field_type="dropdown",
            container_selector="div.search-item:has(.sfilter-other-select):has-text('活跃度')",
            title_text="活跃度",
            fallback_container_selectors=(
                "div.search-item:has-text('活跃度')",
                "div.search-item:has-text('活跃度') div.ant-select",
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
            confirm_selector=".search-item:has(#wantSalaryLow) .shadow-box-submit-btn",
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
            confirm_selector=".search-item:has(#nowSalaryLow) .shadow-box-submit-btn",
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
    }

    def __init__(self, browser_manager: LiepinBrowserManager):
        self.browser_manager = browser_manager

    def _with_debug_snapshot(
        self, reason: str, func: Callable[[], List[LiepinSearchCandidate]]
    ):
        """Run a search step and export a page snapshot on failure."""
        try:
            return func()
        except Exception as exc:
            snapshot_path = ""
            try:
                snapshot_path = self.browser_manager.export_debug_snapshot(reason)
            except Exception:
                snapshot_path = ""

            if isinstance(exc, LiepinSearchNoResultsError):
                if snapshot_path:
                    raise LiepinSearchNoResultsError(
                        "{}\n已导出页面结构诊断文件: {}".format(str(exc), snapshot_path)
                    )
                raise

            if snapshot_path:
                raise LiepinSearchError(
                    "{}\n已导出页面结构诊断文件: {}".format(str(exc), snapshot_path)
                )
            raise

    def open_search_page(self):
        """Open the Liepin search page and require a logged-in session."""
        self.browser_manager.open_search_page()
        self.browser_manager.ensure_logged_in()
        return self.browser_manager.get_state()

    def search(
        self,
        keyword: str,
        filters: Optional[Dict[str, object]] = None,
        match_mode: str = "",
        scope: str = "",
        position_filter: str = "",
    ) -> List[LiepinSearchCandidate]:
        """Run a keyword search, apply optional filters, and return first page summaries."""
        if not keyword.strip():
            raise LiepinSearchError("搜索关键词不能为空")

        self.open_search_page()

        def _run(page):
            try:
                self._execute_search(
                    page,
                    keyword.strip(),
                    match_mode=match_mode,
                    scope=scope,
                    position_filter=position_filter,
                )
            except TypeError:
                self._execute_search(page, keyword.strip())
            if filters:
                self._apply_filters_on_page(page, filters)
            return self.extract_candidates_from_page(page)

        return self._with_debug_snapshot(
            "search_keyword_{}".format(keyword.strip()),
            lambda: self.browser_manager.run_with_page(_run),
        )

    def apply_filters(self, filters: Dict[str, object]) -> None:
        """Apply a batch of supported filters on the active search page."""
        if not filters:
            return

        def _run(page):
            self._apply_filters_on_page(page, filters)
            return True

        self._with_debug_snapshot(
            "apply_filters",
            lambda: self.browser_manager.run_with_page(_run),
        )

    def _apply_filters_on_page(self, page: Page, filters: Dict[str, object]) -> None:
        """Apply supported filters to an already-open result page."""
        self._dismiss_any_open_modal(page)
        normalized_filters = {
            (key or "").strip(): value
            for key, value in (filters or {}).items()
            if (key or "").strip()
        }
        if not normalized_filters:
            return
        if self._filters_need_more_conditions(normalized_filters):
            self._ensure_more_filter_conditions(page)
        for title, value in normalized_filters.items():
            try:
                self._apply_filter_with_retries(page, title, value)
            except Exception as exc:
                logger.warning("skip filter apply: %s=%s reason=%s", title, value, exc)
                self._dismiss_any_open_modal(page)

    def _filters_need_more_conditions(self, filters: Dict[str, object]) -> bool:
        for title in filters:
            spec = self.FILTER_FIELD_SPECS.get(title)
            if spec is not None and spec.requires_expanded:
                return True
        return False

    def _ensure_more_filter_conditions(self, page: Page) -> None:
        """Expand Liepin's filter panel when the target fields live below the fold."""
        selectors = [
            ".filter-box .search-item:last-child",
            "xpath=//*[contains(normalize-space(.), '展开更多条件')]",
            "text=展开更多条件",
        ]
        for selector in selectors:
            try:
                trigger = page.locator(selector).first
                if not trigger.is_visible(timeout=600):
                    continue
                text = (trigger.inner_text(timeout=600) or "").replace(" ", "")
                if "收起更多条件" in text:
                    return
                if "展开更多条件" not in text:
                    continue
                trigger.click(timeout=3000)
                page.wait_for_timeout(500)
                return
            except Exception:
                continue

    def _apply_filter_with_retries(
        self, page: Page, title: str, value: object, attempts: int = 2
    ) -> None:
        """Apply one filter defensively because Liepin controls are animation-heavy."""
        last_exc = None
        spec = self.FILTER_FIELD_SPECS.get(title)
        max_attempts = max(1, attempts)
        if not spec or spec.field_type not in ("city_modal", "dropdown"):
            max_attempts = 1
        for attempt in range(max_attempts):
            try:
                self._dismiss_any_open_modal(page)
                self._apply_one_filter(page, title, value)
                self._dismiss_any_open_modal(page)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "filter apply attempt failed: %s=%s attempt=%s/%s reason=%s",
                    title,
                    value,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                self._dismiss_any_open_modal(page)
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
        if last_exc:
            raise last_exc

    def extract_current_page_candidates(self) -> List[LiepinSearchCandidate]:
        """Parse candidate summaries from the current page without searching."""

        def _run(p):
            return self.extract_candidates_from_page(p)

        return self._with_debug_snapshot(
            "current_result_page",
            lambda: self.browser_manager.run_with_page(_run),
        )

    def ensure_result_page(self):
        """Return the active result page and validate it still looks like search."""

        def _run(page):
            url = (page.url or "").lower()
            if not self.browser_manager._is_search_page_url(url):
                raise LiepinSearchPageChangedError("当前活动页不是搜索结果页")
            return page

        return self.browser_manager.run_with_page(_run)

    def go_to_next_result_page(self) -> bool:
        """Move to the next result page when pagination is available.

        Note: After successful navigation, the page object may become stale
        due to page reload. Callers should refresh their page reference using
        ensure_result_page() after this method returns True.
        """

        def _run(page):
            return self._go_to_next_result_page_locked(page)

        return self.browser_manager.run_with_page(_run)

    def _go_to_next_result_page_locked(self, page: Page) -> bool:
        """Internal implementation of pagination navigation on the worker thread."""
        import logging

        logger = logging.getLogger(__name__)

        current_page_num = self._get_current_page_number(page)
        target_page_num = current_page_num + 1 if current_page_num > 0 else 2
        logger.warning(
            "go_to_next_result_page: Current page is %s, trying to go to page %s",
            current_page_num,
            target_page_num,
        )

        # Strategy 1: click next page button
        next_button = self._find_next_page_control(page)
        if next_button is not None:
            try:
                next_button.scroll_into_view_if_needed(timeout=1500)
                next_button.click(timeout=4000)
                logger.warning("go_to_next_result_page: Clicked next page button")
                if self._wait_for_page_change(page, current_page_num, timeout=6000):
                    self._soft_wait_for_results(page)
                    self.browser_manager.set_active_page(page)
                    return True
                logger.warning(
                    "go_to_next_result_page: Page number did not change after clicking next button"
                )
            except Exception as exc:
                logger.warning("Next button click failed: %s", exc)

        # Strategy 2: click specific page number
        try:
            logger.warning(
                "go_to_next_result_page: Trying direct page number click for page %s",
                target_page_num,
            )
            if self._click_page_number(page, target_page_num):
                if self._wait_for_page_change(page, current_page_num, timeout=6000):
                    self._soft_wait_for_results(page)
                    self.browser_manager.set_active_page(page)
                    return True
        except Exception as exc:
            logger.warning("Page number click failed: %s", exc)

        # Strategy 3: navigate via URL parameter
        try:
            logger.warning(
                "go_to_next_result_page: Trying URL navigation for page %s",
                target_page_num,
            )
            if self._navigate_to_page_via_url(page, target_page_num):
                self._soft_wait_for_results(page)
                self.browser_manager.set_active_page(page)
                return True
        except Exception as exc:
            logger.warning("URL navigation failed: %s", exc)

        logger.error("go_to_next_result_page: All pagination strategies failed")
        return False

    def _wait_for_page_change(
        self, page: Page, previous_page_num: int, timeout: int = 6000
    ) -> bool:
        """Poll pagination until the active page number changes."""
        import logging
        import time

        logger = logging.getLogger(__name__)
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            try:
                new_num = self._get_current_page_number(page)
                if new_num != previous_page_num and new_num > 0:
                    logger.warning(
                        "_wait_for_page_change: detected page change to %s", new_num
                    )
                    return True
            except Exception as exc:
                logger.debug("_wait_for_page_change error: %s", exc)
            try:
                page.wait_for_timeout(300)
            except Exception:
                pass
        return False

    def _soft_wait_for_results(self, page: Page) -> None:
        """Best-effort wait for results without blocking the whole task.

        If results are not visible within a short window we log a warning
        and return anyway so the caller can attempt DOM fallback extraction.
        """
        import logging

        logger = logging.getLogger(__name__)
        for selector in self.RESULT_CARD_SELECTORS:
            try:
                locator = page.locator(selector)
                # count() is synchronous and fast; skip selectors that don't match at all
                if locator.count() == 0:
                    continue
                locator.first.wait_for(state="visible", timeout=3000)
                logger.warning(
                    "_soft_wait_for_results: results visible via selector %s", selector
                )
                return
            except Exception:
                continue
        logger.warning(
            "_soft_wait_for_results: no result cards visible, proceeding anyway"
        )

    def _execute_search(
        self,
        page: Page,
        keyword: str,
        match_mode: str = "",
        scope: str = "",
        position_filter: str = "",
    ) -> None:
        """Fill the most likely search field and submit the search.

        The live page contains more than one `.search-component-input`, so this
        method tries visible editable candidates one by one and only accepts a
        candidate when the page actually reaches the result state.
        """
        self._apply_search_execution_options(page, match_mode=match_mode, scope=scope)
        self._dismiss_any_open_modal(page)
        self._clear_search_inputs(page)
        controls = self._detect_search_controls(page)
        if controls.search_input is None:
            raise LiepinSearchPageChangedError("未找到猎聘搜索输入框，请检查页面结构")
        self._write_keyword(controls.search_input, keyword, force_focus=True)
        if position_filter:
            self._apply_position_name_filter(page, position_filter)
        self._submit_search(page, controls)
        self._wait_for_results(page)

    def _apply_position_name_filter(self, page: Page, position_filter: str) -> None:
        """Fill Liepin's 职位名称 field as a lightweight title filter."""
        value = (position_filter or "").strip()
        if not value:
            return
        input_locator = self._find_position_name_input(page)
        if input_locator is None:
            logger.warning(
                "position filter skipped: position input not found value=%s", value
            )
            return
        try:
            self._write_keyword(input_locator, value, force_focus=True)
            try:
                input_locator.press("Enter")
            except Exception:
                pass
            confirm = self._find_position_name_confirm_button(page)
            if confirm is not None:
                confirm.click(timeout=3000)
                try:
                    page.wait_for_timeout(300)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("position filter skipped: value=%s reason=%s", value, exc)

    def _find_position_name_input(self, page: Page):
        selectors = [
            "xpath=//*[contains(normalize-space(.), '职位名称')]/following::input[contains(@class,'ant-select-selection-search-input')][1]",
            "xpath=//*[contains(normalize-space(.), '当前职位')]/following::input[contains(@class,'search-component-input') or contains(@class,'ant-select-selection-search-input')][1]",
            "xpath=//*[contains(normalize-space(.), '当前职位')]/following::input[not(@readonly)][1]",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=800):
                    return locator
            except Exception:
                continue
        return None

    def _find_position_name_confirm_button(self, page: Page):
        selectors = [
            "xpath=//*[contains(normalize-space(.), '职位名称')]/following::button[.//span[contains(normalize-space(.),'确 定')]][1]",
            "xpath=//*[contains(normalize-space(.), '当前职位')]/following::button[.//span[contains(normalize-space(.),'确 定')]][1]",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=800):
                    return locator
            except Exception:
                continue
        return None

    def _apply_search_execution_options(
        self, page: Page, match_mode: str = "", scope: str = ""
    ) -> None:
        """Best-effort apply per-round keyword mode and resume scope controls."""
        mode_texts = {
            "all": ["全部关键词", "包含全部关键词"],
            "any": ["任意关键词", "包含任意关键词"],
        }.get((match_mode or "").strip().lower(), [])
        for text in mode_texts:
            if self._click_text_control(page, text):
                break

        normalized_scope = (scope or "").strip()
        scope_aliases = {
            "全部经历": ["全部经历", "全部职位"],
            "全部职位": ["全部职位", "全部经历"],
            "目前职位": ["目前职位", "目前公司"],
            "目前公司": ["目前公司", "目前职位"],
            "过往职位": ["过往职位", "过往公司"],
            "过往公司": ["过往公司", "过往职位"],
        }
        for text in scope_aliases.get(
            normalized_scope, [normalized_scope] if normalized_scope else []
        ):
            if self._click_text_control(page, text):
                break

    @staticmethod
    def _click_text_control(page: Page, text: str) -> bool:
        if not text:
            return False
        selectors = [
            'label:has-text("{}")'.format(text),
            'button:has-text("{}")'.format(text),
            'span:has-text("{}")'.format(text),
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.click(timeout=1500)
                    page.wait_for_timeout(150)
                    return True
            except Exception:
                continue
        return False

    # Regex patterns for structured field extraction from result-card text
    _AGE_PATTERN = re.compile(r"(\d+岁)")
    _EDUCATION_PATTERN = re.compile(
        r"(MBA/EMBA|EMBA|MBA|本科|硕士|博士|大专|中专|高中|初中)"
    )
    _WORK_YEARS_PATTERN = re.compile(r"(?:工作)?(\d+年(?:经验)?)")
    _SALARY_PATTERN = re.compile(r"\d+k(?:-\d+k)?")
    _COMPANY_MARKERS = (
        "有限公司",
        "有限责任公司",
        "股份公司",
        "公司",
        "集团",
        "研究院",
        "研究所",
        "事务所",
        "中心",
    )
    _JOB_KEYWORDS = (
        "工程师",
        "经理",
        "总监",
        "主管",
        "专员",
        "顾问",
        "设计师",
        "开发",
        "运营",
        "产品经理",
        "销售",
        "教师",
        "医生",
        "护士",
        "会计",
        "人事",
        "行政",
        "财务",
        "采购",
        "物流",
        "翻译",
        "记者",
        "律师",
        "研究员",
        "分析师",
        "架构师",
        "测试",
        "运维",
        "前端",
        "后端",
        "算法",
        "数据",
        "市场",
        "品牌",
        "公关",
        "助理",
        "秘书",
        "客服",
        "技术支持",
        "项目管理",
        "生产",
        "质量",
        "工艺",
        "制造",
        "设备",
        "机械",
        "电气",
        "自动化",
        "材料",
        "化工",
    )
    _PERSONAL_TAGS = (
        "男",
        "女",
        "已婚",
        "未婚",
        "共青团员",
        "党员",
        "群众",
        "预备党员",
        "民主党派",
    )
    _INVALID_CITY_WORDS = (
        "工作",
        "经验",
        "求职",
        "期望",
        "求职期望",
        "不限",
        "统招",
        "全日制",
        "MBA/EMBA",
        "EMBA",
        "MBA",
    )
    _COMPANY_TITLE_SEPARATORS = (" · ", "·", "｜", "|")

    def _clean_candidate_lines(self, lines: List[str]) -> tuple:
        """Remove UI noise and extract structured fields from result-card text.

        Returns (cleaned_lines, name, age, title, company, city, work_years, education).
        """
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line or len(line) < 2:
                continue
            if line in self.CANDIDATE_NOISE_MARKERS:
                continue
            if any(marker in line for marker in self.FILTER_CARD_MARKERS):
                continue
            cleaned.append(line)

        if not cleaned:
            return [], "", "", "", "", "", "", ""

        name = cleaned[0]
        age = ""
        education = ""
        work_years = ""
        city = ""
        title = ""
        company = ""

        full_text = " ".join(cleaned)

        # Extract age / education / work_years globally
        m = self._AGE_PATTERN.search(full_text)
        if m:
            age = m.group(1)

        m = self._EDUCATION_PATTERN.search(full_text)
        if m:
            education = m.group(1)

        m = self._WORK_YEARS_PATTERN.search(full_text)
        if m:
            work_years = m.group(1)

        # Identify current company / title from combined rows such as
        # "陕西澜山能源有限责任公司 · 天然气销售".
        company_line_idx = -1
        for i, line in enumerate(cleaned):
            split_company, split_title = self._split_company_title(line)
            if split_company:
                company = split_company
                title = split_title
                company_line_idx = i
                break

        # Identify company name. Company markers belong to the company side; do
        # not split at the marker itself, or "陕西澜山能源有限责任公司 · 天然气销售"
        # becomes "陕西澜山能源" / "有限责任公司 · 天然气销售".
        if company_line_idx == -1:
            for i, line in enumerate(cleaned):
                for marker in self._COMPANY_MARKERS:
                    if marker in line:
                        company = line.strip()
                        company_line_idx = i
                        break
                if company_line_idx != -1:
                    break

        # Fallback: third line is likely the company if no marker matched
        if company_line_idx == -1 and len(cleaned) >= 3:
            candidate = cleaned[2]
            if not re.search(r"\d岁|\d+年(?:经验)?|本科|硕士|博士|大专", candidate):
                company = candidate
                company_line_idx = 2

        # Identify city from the compressed personal-info line
        for line in cleaned:
            has_personal = False
            temp = line
            if age and age in temp:
                temp = temp.replace(age, "")
                has_personal = True
            if education and education in temp:
                temp = temp.replace(education, "")
                has_personal = True
            if self._WORK_YEARS_PATTERN.search(temp):
                temp = re.sub(r"(?:工作)?\d+年(?:经验)?", "", temp)
                has_personal = True
            elif work_years and work_years in temp:
                temp = temp.replace(work_years, "")
                has_personal = True
            if has_personal or self._SALARY_PATTERN.search(temp):
                temp = self._SALARY_PATTERN.sub("", temp)
                temp = re.sub(r"^(男|女)\s*", "", temp)
                for tag in self._PERSONAL_TAGS:
                    temp = temp.replace(tag, "")
                temp = temp.replace(" ", "").strip()
                if self._looks_like_city(temp, name, title, company):
                    city = temp
                    break

        if not city:
            for line in cleaned[1:]:
                temp = line.replace(" ", "").strip()
                if self._looks_like_city(temp, name, title, company):
                    city = temp
                    break

        # Identify title if not already extracted from a combined line
        if not title:
            for i, line in enumerate(cleaned):
                if i == 0 or i == company_line_idx or "求职期望" in line:
                    continue
                temp = line
                for val in (age, education, city):
                    if val:
                        temp = temp.replace(val, "")
                temp = re.sub(r"(?:工作)?\d+年(?:经验)?", "", temp)
                temp = self._SALARY_PATTERN.sub("", temp)
                temp = re.sub(r"^(男|女)\s*", "", temp)
                for tag in self._PERSONAL_TAGS:
                    temp = temp.replace(tag, "")
                temp = temp.strip()
                if len(temp) < 2:
                    continue
                for kw in self._JOB_KEYWORDS:
                    if kw in temp:
                        title = line.strip()
                        break
                if title:
                    break

        # Fallback: first meaningful non-name / non-company line as title
        if not title:
            for i, line in enumerate(cleaned):
                if i == 0 or i == company_line_idx or "求职期望" in line:
                    continue
                temp = line
                for val in (age, education, city):
                    if val:
                        temp = temp.replace(val, "")
                temp = re.sub(r"(?:工作)?\d+年(?:经验)?", "", temp)
                temp = self._SALARY_PATTERN.sub("", temp)
                temp = re.sub(r"^(男|女)\s*", "", temp)
                for tag in self._PERSONAL_TAGS:
                    temp = temp.replace(tag, "")
                temp = temp.strip()
                if len(temp) >= 2 and not self._looks_like_city(temp, name, title, company):
                    title = line.strip()
                    break

        return cleaned, name, age, title, company, city, work_years, education

    def _split_company_title(self, line: str) -> tuple[str, str]:
        value = (line or "").strip()
        if not value or "求职期望" in value:
            return "", ""
        for separator in self._COMPANY_TITLE_SEPARATORS:
            if separator not in value:
                continue
            parts = [part.strip() for part in value.split(separator) if part.strip()]
            if len(parts) < 2:
                continue
            company = separator.join(parts[:-1]).strip()
            title = parts[-1].strip()
            if len(company) < 2 or len(title) < 2:
                continue
            if self._looks_like_title(title) or any(
                marker in company for marker in self._COMPANY_MARKERS
            ):
                return company, title
        return "", ""

    def _looks_like_title(self, value: str) -> bool:
        text = (value or "").strip()
        if len(text) < 2:
            return False
        return any(keyword in text for keyword in self._JOB_KEYWORDS)

    def _looks_like_city(
        self, value: str, name: str = "", title: str = "", company: str = ""
    ) -> bool:
        text = (value or "").strip()
        if (
            not text
            or text in (name, title, company)
            or text in self._INVALID_CITY_WORDS
            or not (2 <= len(text) <= 12)
            or re.search(r"\d", text)
        ):
            return False
        if any(word in text for word in self._INVALID_CITY_WORDS):
            return False
        if any(keyword in text for keyword in self._JOB_KEYWORDS):
            return False
        if any(marker in text for marker in self._COMPANY_MARKERS):
            return False
        if self._EDUCATION_PATTERN.search(text) or self._AGE_PATTERN.search(text):
            return False
        return True

    def extract_candidates_from_page(self, page: Page) -> List[LiepinSearchCandidate]:
        """Parse summary cards from the current result page."""
        url = ""
        try:
            url = page.url or ""
        except Exception:
            pass
        cards, matched_selector = self._locate_result_cards(page)
        logger.warning(
            "extract_candidates_from_page: url=%s selector=%s cards=%s",
            url,
            matched_selector or "none",
            len(cards),
        )

        if not cards:
            if self._page_looks_empty(page):
                raise LiepinSearchNoResultsError(
                    "当前关键词未搜索到候选人，准备尝试下一组关键词"
                )
            logger.warning(
                "extract_candidates_from_page: No cards found via selectors, trying DOM fallback"
            )
            return self._extract_candidates_with_dom_fallback(page)

        candidates = []
        for card in cards:
            try:
                text = card.inner_text(timeout=2000).strip()
            except Exception:
                continue

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            cleaned, name, age, title, company, city, work_years, education = (
                self._clean_candidate_lines(lines)
            )
            if not name:
                logger.warning(
                    "extract_candidates_from_page: skipping card with no valid name"
                )
                continue
            profile_url = self._extract_profile_url(card)
            candidate = LiepinSearchCandidate(
                name=name,
                age=age,
                current_title=title,
                current_company=company,
                city=city,
                work_years=work_years,
                education=education,
                summary="\n".join(cleaned[:12]),
                profile_url=profile_url,
                result_index=len(candidates),
            )
            candidates.append(candidate)
        return candidates

    def _extract_candidates_with_dom_fallback(
        self, page: Page
    ) -> List[LiepinSearchCandidate]:
        """Heuristically extract result rows from the live result page DOM."""
        try:
            rows = page.evaluate(
                r"""
                () => {
                  // Scroll to top first to get consistent element positions
                  window.scrollTo(0, 0);
                  
                  const cleanText = (text) => (text || '')
                    .replace(/\u00a0/g, ' ')
                    .split(/\n+/)
                    .map((line) => line.replace(/\s+/g, ' ').trim())
                    .filter(Boolean);

                  const hrefScore = (href) => {
                    const value = (href || '').toLowerCase();
                    if (!value || value.startsWith('javascript:') || value === '#') {
                      return -1;
                    }
                    if (value.includes('/resume/') || value.includes('res_id_encode=')) {
                      return 5;
                    }
                    if (value.includes('/search/detail') || value.includes('/detail/')) {
                      return 4;
                    }
                    if (value.includes('h.liepin.com')) {
                      return 2;
                    }
                    return 1;
                  };

                  const extractHrefFromElement = (el) => {
                    if (!el) return '';
                    // Direct href
                    let href = el.getAttribute('href') || '';
                    if (href) return href;
                    // Data attributes
                    for (const attr of ['data-href', 'data-url', 'data-link', 'data-resume-url', 'data-detail-url']) {
                      href = el.getAttribute(attr) || '';
                      if (href) return href;
                    }
                    // Onclick with URL
                    const onclick = el.getAttribute('onclick') || '';
                    const urlMatch = onclick.match(/(?:https?:\/\/[^\s'"]+)/);
                    if (urlMatch) return urlMatch[0];
                    return '';
                  };

                  const pickProfileHref = (element) => {
                    // 1. Try the element itself
                    let bestHref = extractHrefFromElement(element);
                    if (bestHref) return bestHref;
                    // 2. Try all descendants, scored
                    const anchors = Array.from(element.querySelectorAll('a[href], [data-href], [data-url], [data-link], [data-resume-url], [data-detail-url]'));
                    anchors.sort((left, right) => hrefScore(extractHrefFromElement(right)) - hrefScore(extractHrefFromElement(left)));
                    if (anchors.length) {
                      return extractHrefFromElement(anchors[0]) || '';
                    }
                    // 3. Look for hidden input with res_id_encode and construct a plausible URL
                    const resIdInput = element.querySelector('input[name="res_id_encode"]');
                    if (resIdInput) {
                      const resId = resIdInput.value || resIdInput.getAttribute('value') || '';
                      if (resId) {
                        return (location.origin || 'https://h.liepin.com') + '/resume/showresumedetail/?res_id_encode=' + encodeURIComponent(resId);
                      }
                    }
                    return '';
                  };

                  // Try multiple selectors for action buttons - Liepin uses various elements
                  let actionButtons = Array.from(document.querySelectorAll('button')).filter((button) => {
                    const text = (button.innerText || button.textContent || '').replace(/\s+/g, ' ').trim();
                    return text.includes('沟通') || text.includes('交换') || text.includes('联系');
                  });
                  
                  // If no buttons found, try links/anchors with action classes
                  if (actionButtons.length === 0) {
                    actionButtons = Array.from(document.querySelectorAll('a, span, div')).filter((el) => {
                      const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                      const hasActionText = text.includes('沟通') || text.includes('交换') || text.includes('联系') || text.includes('意向');
                      const hasActionClass = (el.className || '').toString().includes('action') || 
                                            (el.className || '').toString().includes('btn') ||
                                            (el.className || '').toString().includes('button');
                      return hasActionText && (hasActionClass || text.length < 20);
                    });
                  }
                  
                  // Debug info
                  const debugInfo = { buttonCount: actionButtons.length, containersFound: 0, skippedNoCheckbox: 0, skippedNoSize: 0, windowWidth: window.innerWidth };

                  const containers = [];
                  const seen = new Set();

                  for (const button of actionButtons) {
                    let current = button;
                    let chosen = null;
                    let skipReason = '';
                    while (current && current !== document.body) {
                      const rect = current.getBoundingClientRect ? current.getBoundingClientRect() : null;
                      const text = (current.innerText || current.textContent || '').replace(/\s+/g, ' ').trim();
                      const checkbox = current.querySelector('input[name="res_id_encode"]');
                      // RELAXED: Reduced size requirements for smaller viewports
                      const minWidth = window.innerWidth < 1500 ? 400 : 600;
                      const minHeight = window.innerHeight < 800 ? 80 : 100;
                      if (!checkbox) {
                        skipReason = 'no_checkbox';
                      } else if (!rect || rect.height < minHeight || rect.width < minWidth) {
                        skipReason = 'too_small:' + (rect ? `${rect.width}x${rect.height}(need>${minWidth}x${minHeight})` : 'no_rect');
                      } else if (text.length < 10) {
                        skipReason = 'text_too_short';
                      } else {
                        chosen = current;
                        break;
                      }
                      current = current.parentElement;
                    }

                    if (!chosen) {
                      if (skipReason.includes('no_checkbox')) debugInfo.skippedNoCheckbox++;
                      else if (skipReason.includes('too_small')) debugInfo.skippedNoSize++;
                      continue;
                    }

                    const key = chosen.innerText || chosen.textContent || '';
                    if (!key || seen.has(key)) {
                      continue;
                    }
                    seen.add(key);

                    const rect = chosen.getBoundingClientRect ? chosen.getBoundingClientRect() : { top: 0 };
                    containers.push({
                      index: containers.length,
                      top: rect.top || 0,
                      href: pickProfileHref(chosen),
                      lines: cleanText(chosen.innerText || chosen.textContent || ''),
                      rawHtml: chosen.outerHTML ? chosen.outerHTML.slice(0, 600) : '',
                    });
                  }
                  
                  debugInfo.containersFound = containers.length;
                  
                  containers.sort((left, right) => left.top - right.top);
                  
                  // Return debug info as the last element (will be removed in Python)
                  containers.push({ _debugInfo: debugInfo });
                  return containers;
                }
                """
            )
        except Exception as exc:
            raise LiepinSearchPageChangedError(
                "未找到候选人结果卡片，且结果页启发式提取失败: {}".format(str(exc))
            )

        # Log debug info from JavaScript extraction (last element contains debug info)
        debug_info = None
        if isinstance(rows, list) and rows:
            last_row = rows[-1]
            if isinstance(last_row, dict) and "_debugInfo" in last_row:
                debug_info = last_row["_debugInfo"]
                rows.pop()  # Remove debug element

        if debug_info:
            logger.warning(
                "DOM fallback debug: buttons=%s, containers=%s, skippedNoCheckbox=%s, skippedNoSize=%s",
                debug_info.get("buttonCount"),
                debug_info.get("containersFound"),
                debug_info.get("skippedNoCheckbox"),
                debug_info.get("skippedNoSize"),
            )
        else:
            logger.warning(
                "DOM fallback: rows type=%s, count=%s",
                type(rows).__name__,
                len(rows) if rows else 0,
            )

        candidates = []
        for row in rows or []:
            lines = row.get("lines") or []
            if not lines:
                continue
            cleaned, name, age, title, company, city, work_years, education = (
                self._clean_candidate_lines(lines)
            )
            if not name:
                logger.warning(
                    "DOM fallback: skipping container with no valid name after cleaning, raw_first_line=%s",
                    lines[0] if lines else "",
                )
                continue
            candidates.append(
                LiepinSearchCandidate(
                    name=name,
                    age=age,
                    current_title=title,
                    current_company=company,
                    city=city,
                    work_years=work_years,
                    education=education,
                    summary="\n".join(cleaned[:12]),
                    profile_url=row.get("href") or "",
                    result_index=len(candidates),
                )
            )

        logger.warning("DOM fallback: produced %s valid candidates", len(candidates))
        for i, row in enumerate(rows[:5]):
            logger.warning(
                "DOM fallback raw %s: href=%s raw_html=%s",
                i + 1,
                row.get("href") or "(none)",
                (row.get("rawHtml") or "")[:300],
            )
        for i, c in enumerate(candidates[:5]):
            logger.warning(
                "DOM fallback candidate %s: name=%s href=%s",
                i + 1,
                c.name,
                c.profile_url or "(none)",
            )
        if candidates:
            return candidates
        if self._page_looks_empty(page):
            raise LiepinSearchNoResultsError(
                "当前关键词未搜索到候选人，准备尝试下一组关键词"
            )
        raise LiepinSearchPageChangedError("未找到候选人结果卡片")

    @staticmethod
    def _ensure_absolute_url(url: str) -> str:
        if url and url.startswith("/") and not url.startswith("//"):
            return "https://h.liepin.com" + url
        return url

    @staticmethod
    def _is_detail_page_url(url: str) -> bool:
        normalized = (url or "").lower()
        return "showresumedetail" in normalized or "/resume/" in normalized

    def open_candidate_detail(self, page: Page, candidate: LiepinSearchCandidate):
        """Open one candidate detail page and return the active detail page."""
        import logging
        import time

        logger = logging.getLogger(__name__)
        profile_url = self._ensure_absolute_url(candidate.profile_url or "")
        if profile_url:
            # 优先在新标签页打开，避免覆盖搜索结果页
            start = time.time()
            detail_page = None
            try:
                detail_page = self.browser_manager.new_page()
                detail_page.goto(
                    profile_url, wait_until="domcontentloaded", timeout=15000
                )
                # 校验是否被重定向到找人/搜索页（常见于浏览器启动后的前几次访问）
                if not self._is_detail_page_url(detail_page.url or ""):
                    logger.warning(
                        "open_candidate_detail: redirected after domcontentloaded, waiting for stabilization"
                    )
                    # 短暂等待页面稳定
                    detail_page.wait_for_timeout(1200)
                    if not self._is_detail_page_url(detail_page.url or ""):
                        logger.warning(
                            "open_candidate_detail: still not detail page after wait, retrying with networkidle"
                        )
                        detail_page.goto(
                            profile_url, wait_until="networkidle", timeout=15000
                        )
                logger.warning(
                    "open_candidate_detail: opened in new tab elapsed=%.2fs url=%s",
                    time.time() - start,
                    (detail_page.url or profile_url)[:120],
                )
                candidate.profile_url = detail_page.url or profile_url
                return detail_page
            except Exception as exc:
                logger.warning(
                    "open_candidate_detail: new tab failed elapsed=%.2fs url=%s error=%s",
                    time.time() - start,
                    profile_url[:120],
                    exc,
                )
                if detail_page is not None and detail_page is not page:
                    try:
                        detail_page.close()
                    except Exception:
                        pass
                # 降级：在当前页打开
            start = time.time()
            try:
                page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
                if not self._is_detail_page_url(page.url or ""):
                    page.wait_for_timeout(1200)
                    if not self._is_detail_page_url(page.url or ""):
                        page.goto(profile_url, wait_until="networkidle", timeout=15000)
                logger.warning(
                    "open_candidate_detail: opened in current tab elapsed=%.2fs url=%s",
                    time.time() - start,
                    (page.url or profile_url)[:120],
                )
                candidate.profile_url = page.url or profile_url
            except Exception as exc:
                logger.warning(
                    "open_candidate_detail: current tab also failed elapsed=%.2fs url=%s error=%s",
                    time.time() - start,
                    profile_url[:120],
                    exc,
                )
                raise
            return page

        if candidate.result_index < 0:
            raise LiepinSearchPageChangedError("候选人缺少详情入口，无法打开完整简历")

        try:
            before_pages = list(page.context.pages)
        except Exception:
            before_pages = [page]

        try:
            clicked = page.evaluate(
                r"""
                (targetIndex) => {
                  // Use the same action-button logic as DOM fallback for consistency
                  let actionButtons = Array.from(document.querySelectorAll('button')).filter((button) => {
                    const text = (button.innerText || button.textContent || '').replace(/\s+/g, ' ').trim();
                    return text.includes('沟通') || text.includes('交换') || text.includes('联系');
                  });
                  if (actionButtons.length === 0) {
                    actionButtons = Array.from(document.querySelectorAll('a, span, div')).filter((el) => {
                      const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                      const hasActionText = text.includes('沟通') || text.includes('交换') || text.includes('联系') || text.includes('意向');
                      const hasActionClass = (el.className || '').toString().includes('action') ||
                                            (el.className || '').toString().includes('btn') ||
                                            (el.className || '').toString().includes('button');
                      return hasActionText && (hasActionClass || text.length < 20);
                    });
                  }

                  const containers = [];
                  const seen = new Set();
                  for (const button of actionButtons) {
                    let current = button;
                    let chosen = null;
                    while (current && current !== document.body) {
                      const rect = current.getBoundingClientRect ? current.getBoundingClientRect() : null;
                      const text = (current.innerText || current.textContent || '').replace(/\s+/g, ' ').trim();
                      const checkbox = current.querySelector('input[name="res_id_encode"]');
                      // RELAXED: match DOM fallback size rules
                      const minWidth = window.innerWidth < 1500 ? 400 : 600;
                      const minHeight = window.innerHeight < 800 ? 80 : 100;
                      if (checkbox && rect && rect.height >= minHeight && rect.width >= minWidth && text.length >= 10) {
                        chosen = current;
                        break;
                      }
                      current = current.parentElement;
                    }
                    if (!chosen) {
                      continue;
                    }
                    const key = chosen.innerText || chosen.textContent || '';
                    if (!key || seen.has(key)) {
                      continue;
                    }
                    seen.add(key);
                    containers.push(chosen);
                  }

                  const container = containers[targetIndex];
                  if (!container) {
                    return false;
                  }

                  const clickable =
                    container.querySelector('a[target="_blank"][href]') ||
                    container.querySelector('a[href]') ||
                    container.querySelector('[role="link"]') ||
                    container;

                  if (!(clickable instanceof HTMLElement)) {
                    return false;
                  }
                  clickable.scrollIntoView({ block: 'center' });
                  clickable.click();
                  return true;
                }
                """,
                candidate.result_index,
            )
        except Exception as exc:
            raise LiepinSearchPageChangedError(
                "候选人缺少详情链接，且点击结果行失败: {}".format(str(exc))
            )

        if not clicked:
            raise LiepinSearchPageChangedError("候选人缺少详情入口，无法打开完整简历")

        try:
            page.wait_for_timeout(1200)
        except Exception:
            pass

        detail_page = page
        try:
            current_pages = list(page.context.pages)
            new_pages = [item for item in current_pages if item not in before_pages]
            if new_pages:
                detail_page = self.browser_manager._pick_best_page(new_pages, page)
            else:
                detail_page = self.browser_manager._pick_best_page(current_pages, page)
        except Exception:
            detail_page = page

        detail_page.wait_for_load_state("domcontentloaded", timeout=10000)
        candidate.profile_url = detail_page.url or candidate.profile_url
        return detail_page

    def close_detail_page(self, detail_page: Page, result_page: Page) -> Page:
        """Close transient detail tabs and return focus to the result page."""
        import logging

        logger = logging.getLogger(__name__)
        if detail_page is not None and detail_page is not result_page:
            try:
                detail_page.close()
                logger.warning("close_detail_page: closed transient tab")
            except Exception as exc:
                logger.warning("close_detail_page: close transient tab failed: %s", exc)
        try:
            result_page.bring_to_front()
        except Exception:
            pass
        # Keep browser manager's active page pointer aligned to the result page
        try:
            self.browser_manager.set_active_page(result_page)
        except Exception:
            pass
        return result_page

    def _fill_search_input(self, page: Page, keyword: str) -> None:
        input_locator = self._find_primary_search_input(page)
        if input_locator is None:
            raise LiepinSearchPageChangedError("未找到猎聘搜索输入框，请检查页面结构")

        self._write_keyword(input_locator, keyword)

    def _submit_search(
        self, page: Page, controls: Optional[LiepinSearchControls] = None
    ) -> None:
        controls = controls or self._detect_search_controls(page)
        button_locator = controls.search_button or self._first_visible_locator(
            page, self.SEARCH_BUTTON_SELECTORS
        )
        if button_locator is not None:
            button_locator.click(timeout=5000)
            return

        input_locator = controls.search_input or self._find_primary_search_input(page)
        if input_locator is None:
            raise LiepinSearchPageChangedError("未找到搜索按钮，也无法回退到输入框提交")
        input_locator.press("Enter")

    def _wait_for_results(self, page: Page) -> None:
        import time

        deadline = time.time() + 12
        while time.time() < deadline:
            for selector in self.RESULT_CARD_SELECTORS:
                try:
                    locator = page.locator(selector)
                    if locator.count() == 0:
                        continue
                    if locator.first.is_visible(timeout=300):
                        return
                except Exception:
                    continue

            try:
                if self._page_looks_like_result_list(page):
                    return
            except Exception:
                pass

            try:
                if self._is_loading(page):
                    page.wait_for_timeout(250)
                    continue
            except Exception:
                pass

            try:
                candidates = self._extract_candidates_with_dom_fallback(page)
                if candidates:
                    return
            except Exception:
                pass

            try:
                page.wait_for_timeout(300)
            except Exception:
                break
        if self._page_looks_empty(page):
            raise LiepinSearchNoResultsError(
                "当前关键词未搜索到候选人，准备尝试下一组关键词"
            )
        raise LiepinSearchPageChangedError("搜索完成后未找到结果列表，请检查页面结构")

    def _page_looks_like_result_list(self, page: Page) -> bool:
        """Best-effort detection for result pages before card parsing stabilizes.

        The live Liepin result page may render actionable controls and pagination
        earlier than our card selectors become queryable. Use those stable list
        markers to avoid blocking the whole pipeline when search already landed
        on the candidate page.
        """
        heuristics = [
            'input[name="res_id_encode"]',
            'button:has-text("立即沟通")',
            ".resume-list-pagebar",
            ".ant-pagination.resume-list-pagebar",
        ]
        hits = 0
        for selector in heuristics:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=200):
                    hits += 1
            except Exception:
                continue
        return hits >= 1

    @staticmethod
    def _page_looks_empty(page: Page) -> bool:
        empty_markers = (
            "没找到相关匹配项",
            "没有找到符合条件的简历",
            "没有找到符合条件",
            "暂无相关人选",
            "暂无匹配结果",
            "未找到相关匹配项",
            "抱歉，没有找到",
        )
        try:
            body_text = page.locator("body").inner_text(timeout=1500) or ""
        except Exception:
            return False
        if any(marker in body_text for marker in empty_markers):
            return True

        try:
            has_candidate_checkbox = (
                page.locator('input[name="res_id_encode"]').count() > 0
            )
            has_pagination = (
                page.locator(
                    ".resume-list-pagebar, .ant-pagination.resume-list-pagebar"
                ).count()
                > 0
            )
            has_action_button = page.locator('button:has-text("立即沟通")').count() > 0
            has_batch_view = page.locator('button:has-text("批量查看")').count() > 0
        except Exception:
            return False

        return has_batch_view and not (
            has_candidate_checkbox or has_pagination or has_action_button
        )

    # Keywords that indicate text lines are UI noise rather than candidate data
    FILTER_CARD_MARKERS = (
        "包含全部关键词",
        "没找到相关匹配项",
        "查看全部",
        "不限",
        "全选",
    )
    CANDIDATE_NOISE_MARKERS = (
        "在线",
        "今天活跃",
        "3天内活跃",
        "7天内活跃",
        "活跃状态",
        "隐藏",
        "查看联系方式",
        "立即沟通",
        "交换电话",
        "收藏",
        "举报",
    )

    def _locate_result_cards(self, page: Page):
        if not hasattr(page, "locator"):
            return [], ""
        for selector in self.RESULT_CARD_SELECTORS:
            locator = page.locator(selector)
            try:
                count = locator.count()
            except Error:
                continue
            if count > 0:
                # Validate first card text doesn't look like a filter widget
                try:
                    first_text = locator.first.inner_text(timeout=1500).strip()
                    if first_text and any(
                        marker in first_text for marker in self.FILTER_CARD_MARKERS
                    ):
                        logger.warning(
                            "_locate_result_cards: selector=%s matched filter widget, skipping. text=%s",
                            selector,
                            first_text[:60],
                        )
                        continue
                except Exception:
                    pass
                return [locator.nth(index) for index in range(count)], selector
        return [], ""

    def _extract_profile_url(self, card) -> str:
        for selector in self.PROFILE_LINK_SELECTORS:
            try:
                locator = card.locator(selector).first
                if locator.count() == 0:
                    continue
                href = locator.get_attribute("href", timeout=300)
            except Exception:
                continue
            if href:
                return self._ensure_absolute_url(href)
        try:
            href = card.evaluate(
                r"""
                (el) => {
                  const hrefScore = (href) => {
                    const value = (href || '').toLowerCase();
                    if (!value || value.startsWith('javascript:') || value === '#') return -1;
                    if (value.includes('/resume/') || value.includes('res_id_encode=')) return 5;
                    if (value.includes('/search/detail') || value.includes('/detail/')) return 4;
                    if (value.includes('h.liepin.com')) return 2;
                    return 1;
                  };
                  const extractHref = (node) => {
                    if (!node) return '';
                    let href = node.getAttribute('href') || '';
                    if (href) return href;
                    for (const attr of ['data-href', 'data-url', 'data-link', 'data-resume-url', 'data-detail-url']) {
                      href = node.getAttribute(attr) || '';
                      if (href) return href;
                    }
                    const onclick = node.getAttribute('onclick') || '';
                    const urlMatch = onclick.match(/(?:https?:\/\/[^\s'"]+)/);
                    if (urlMatch) return urlMatch[0];
                    return '';
                  };
                  const direct = extractHref(el);
                  if (direct) return direct;
                  const nodes = Array.from(el.querySelectorAll('a[href], [data-href], [data-url], [data-link], [data-resume-url], [data-detail-url]'));
                  nodes.sort((left, right) => hrefScore(extractHref(right)) - hrefScore(extractHref(left)));
                  if (nodes.length) return extractHref(nodes[0]) || '';
                  const input = el.querySelector('input[name="res_id_encode"]');
                  if (input) {
                    const resId = input.value || input.getAttribute('value') || '';
                    if (resId) {
                      return (location.origin || 'https://h.liepin.com') + '/resume/showresumedetail/?res_id_encode=' + encodeURIComponent(resId);
                    }
                  }
                  return '';
                }
                """
            )
        except Exception:
            href = ""
        if href:
            return self._ensure_absolute_url(str(href))
        return ""

    def _find_next_page_control(self, page: Page):
        """Find the next page button with comprehensive logging and fallback strategies."""
        import logging

        logger = logging.getLogger(__name__)

        # Scroll to bottom to ensure pagination is visible
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
        except Exception:
            pass

        # Strategy 1: Try standard selectors targeting the li element
        for selector in self.NEXT_PAGE_SELECTORS:
            try:
                locator = page.locator(selector).first
                if not locator.is_visible(timeout=1500):
                    continue
                if self._is_disabled_pagination(locator):
                    logger.debug("Next page button found but disabled: %s", selector)
                    continue
                # Prefer inner clickable element over the container
                for inner_sel in ["a", "button", '[role="button"]']:
                    try:
                        inner = locator.locator(inner_sel).first
                        if inner.is_visible(
                            timeout=1000
                        ) and not self._is_disabled_pagination(inner):
                            logger.debug(
                                "Next page button found via selector %s (inner %s)",
                                selector,
                                inner_sel,
                            )
                            return inner
                    except Exception:
                        continue
                logger.debug("Next page button found via selector: %s", selector)
                return locator
            except Exception:
                continue

        # Strategy 2: JavaScript fallback to find pagination
        try:
            js_result = page.evaluate(
                r"""
                () => {
                    const strategies = [
                        () => document.querySelector('.ant-pagination-next:not(.ant-pagination-disabled)'),
                        () => document.querySelector('li[title="下一页"]:not(.ant-pagination-disabled)'),
                        () => {
                            const pagination = document.querySelector('.ant-pagination');
                            if (!pagination) return null;
                            const items = Array.from(pagination.querySelectorAll('li'));
                            for (let i = items.length - 1; i >= 0; i--) {
                                const item = items[i];
                                const text = (item.innerText || item.textContent || '').trim();
                                if (/^\d+$/.test(text)) continue;
                                if (item.classList.contains('ant-pagination-disabled')) continue;
                                if (item.classList.contains('ant-pagination-prev')) continue;
                                return item;
                            }
                            return null;
                        },
                    ];
                    for (let i = 0; i < strategies.length; i++) {
                        const btn = strategies[i]();
                        if (btn) {
                            return {
                                found: true,
                                strategy: i,
                                className: btn.className,
                                title: btn.getAttribute('title') || '',
                                text: (btn.innerText || btn.textContent || '').trim().slice(0, 50),
                            };
                        }
                    }
                    return { found: false };
                }
                """
            )
            if js_result and js_result.get("found"):
                logger.debug(
                    "Next page found via JavaScript strategy %s: %s",
                    js_result.get("strategy"),
                    js_result.get("className"),
                )
                selectors_to_try = [
                    ".ant-pagination-next:not(.ant-pagination-disabled)",
                    'li[title="下一页"]:not(.ant-pagination-disabled)',
                    ".ant-pagination > li:nth-last-child(1):not(.ant-pagination-disabled)",
                    ".ant-pagination > li:nth-last-child(2):not(.ant-pagination-disabled)",
                ]
                for selector in selectors_to_try:
                    try:
                        locator = page.locator(selector).first
                        if locator.is_visible(timeout=1500):
                            if not self._is_disabled_pagination(locator):
                                for inner_sel in ["a", "button", '[role="button"]']:
                                    try:
                                        inner = locator.locator(inner_sel).first
                                        if inner.is_visible(
                                            timeout=1000
                                        ) and not self._is_disabled_pagination(inner):
                                            return inner
                                    except Exception:
                                        continue
                                return locator
                    except Exception:
                        continue
        except Exception as exc:
            logger.debug("JavaScript fallback for next page button failed: %s", exc)

        logger.warning("Could not find next page button with any selector")
        return None

    def _get_current_page_number(self, page: Page) -> int:
        """Get the current active page number from pagination."""
        try:
            active_page = page.locator(".ant-pagination-item-active").first
            if active_page.is_visible(timeout=1000):
                page_text = active_page.inner_text(timeout=1000)
                try:
                    return int(page_text.strip())
                except ValueError:
                    pass
        except Exception:
            pass
        return 0

    def _click_page_number(self, page: Page, target_page: int) -> bool:
        """Click on a specific page number as fallback navigation."""
        import logging

        logger = logging.getLogger(__name__)

        selectors = [
            f".ant-pagination-item-{target_page}",
            f'.ant-pagination-item[title="{target_page}"]',
            f'li[title="{target_page}"]',
        ]

        for selector in selectors:
            try:
                logger.debug("_click_page_number: Trying selector %s", selector)
                page_link = page.locator(selector).first
                if page_link.is_visible(timeout=1500):
                    # Prefer inner clickable element
                    clicked_inner = False
                    for inner_sel in ["a", "button"]:
                        try:
                            inner = page_link.locator(inner_sel).first
                            if inner.is_visible(timeout=1000):
                                inner.click(timeout=5000)
                                clicked_inner = True
                                break
                        except Exception:
                            continue
                    if not clicked_inner:
                        page_link.click(timeout=5000)
                    logger.info(
                        "_click_page_number: Clicked page %s using selector %s",
                        target_page,
                        selector,
                    )
                    return True
            except Exception as exc:
                logger.debug(
                    "_click_page_number: Selector %s failed: %s", selector, exc
                )
                continue

        logger.error(
            "_click_page_number: All selectors failed for page %s", target_page
        )
        return False

    def _navigate_to_page_via_url(self, page: Page, target_page: int) -> bool:
        """Navigate to a specific result page by modifying the URL parameter."""
        current_url = page.url or ""
        if not current_url:
            return False

        import re

        new_url = current_url
        # Liepin uses curPage starting from 0
        page_param_value = target_page - 1

        if re.search(r"[?&]curPage=\d+", current_url):
            new_url = re.sub(
                r"([?&]curPage=)\d+",
                lambda m: m.group(1) + str(page_param_value),
                current_url,
            )
        elif re.search(r"[?&]page=\d+", current_url):
            new_url = re.sub(
                r"([?&]page=)\d+", lambda m: m.group(1) + str(target_page), current_url
            )
        elif "?" in current_url:
            new_url = current_url + "&curPage=" + str(page_param_value)
        else:
            new_url = current_url + "?curPage=" + str(page_param_value)

        if new_url == current_url:
            return False

        try:
            page.goto(new_url, wait_until="domcontentloaded", timeout=15000)
            return True
        except Exception:
            return False

    @staticmethod
    def _is_disabled_pagination(locator) -> bool:
        try:
            disabled = locator.get_attribute("disabled")
            aria_disabled = (locator.get_attribute("aria-disabled") or "").lower()
            class_name = (locator.get_attribute("class") or "").lower()
        except Exception:
            return False

        return (
            disabled is not None
            or aria_disabled == "true"
            or "ant-pagination-disabled" in class_name
            or "disabled" in class_name
        )

    def _first_visible_locator(self, page: Page, selectors: List[str]):
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=2500):
                    return locator
            except Exception:
                continue
        return None

    def _find_primary_search_input(self, page: Page):
        """Find the main free-text search input on the resume search page."""
        return self._detect_search_controls(page).search_input

    def _detect_search_controls(self, page: Page) -> LiepinSearchControls:
        """Resolve the top search input and the matching search button."""
        button_locator = self._find_search_button(page)
        if button_locator is not None:
            input_locator = self._find_search_input_near_button(page, button_locator)
            if input_locator is not None:
                return LiepinSearchControls(
                    search_input=input_locator,
                    search_button=button_locator,
                )

        candidates = self._find_candidate_search_inputs(page)
        return LiepinSearchControls(
            search_input=candidates[0] if candidates else None,
            search_button=button_locator,
        )

    def _find_search_button(self, page: Page):
        primary = self._first_visible_locator(page, ["button.search-btn"])
        if primary is not None:
            return primary
        return self._first_visible_locator(page, self.SEARCH_BUTTON_SELECTORS)

    def _find_search_input_near_button(self, page: Page, button_locator):
        """Prefer the verified main search container near `button.search-btn`."""
        try:
            container = page.locator("div.search-auto-complete-box").first
            container.wait_for(state="visible", timeout=1500)
            input_locator = self._find_primary_input_in_search_container(container)
            if input_locator is not None:
                return input_locator
        except Exception:
            pass

        try:
            button_box = button_locator.bounding_box()
        except Exception:
            button_box = None
        if not button_box:
            return None

        best_candidate = None
        best_score = None
        for candidate in self._find_candidate_search_inputs(page):
            try:
                box = candidate.bounding_box()
            except Exception:
                box = None
            if not box:
                continue
            width = box.get("width") or 0
            horizontal_gap = abs(
                (button_box.get("x") or 0) - ((box.get("x") or 0) + width)
            )
            vertical_gap = abs((button_box.get("y") or 0) - (box.get("y") or 0))
            score = (0 if width >= 500 else 1, vertical_gap, horizontal_gap)
            if best_score is None or score < best_score:
                best_score = score
                best_candidate = candidate
        return best_candidate

    def _find_candidate_search_inputs(self, page: Page):
        """Return candidate search inputs ordered by likelihood.

        On the live page there are multiple search-like inputs. The user
        confirmed that the primary keyword field is the top-most one in the
        filter area, so we sort visible editable candidates by vertical
        position, top to bottom.
        """
        candidates = []
        try:
            container = page.locator("div.search-auto-complete-box").first
            container.wait_for(state="visible", timeout=1200)
            primary_input = self._find_primary_input_in_search_container(container)
            if primary_input is not None:
                candidates.append((0, 0, primary_input))
        except Exception:
            pass

        try:
            direct = page.locator("input.search-component-input")
            count = direct.count()
            for index in range(count):
                candidate = direct.nth(index)
                if self._is_editable_input(candidate):
                    top = self._locator_top(candidate)
                    candidates.append((top + 1000, index, candidate))
        except Exception:
            pass

        try:
            direct = page.locator("input.ant-select-selection-search-input")
            count = direct.count()
            for index in range(count):
                candidate = direct.nth(index)
                if not self._is_editable_input(candidate):
                    continue
                top = self._locator_top(candidate)
                candidates.append((top + 100, index, candidate))
        except Exception:
            pass

        if candidates:
            deduped = []
            seen_ids = set()
            for _, _, locator in sorted(
                candidates, key=lambda item: (item[0], item[1])
            ):
                locator_id = id(locator)
                if locator_id in seen_ids:
                    continue
                seen_ids.add(locator_id)
                deduped.append(locator)
            return deduped

        fallback = self._first_visible_locator(page, self.SEARCH_INPUT_SELECTORS)
        return [fallback] if fallback is not None else []

    def _find_primary_input_in_search_container(self, container):
        """Resolve the real keyword input inside the top search container.

        The live page renders more than one Ant Select input in the top bar.
        The left-most one belongs to the keyword logic switch, while the actual
        keyword field lives inside the auto-complete wrapper. Prefer structural
        selectors first, then fall back to the widest editable input inside the
        same container so the logic stays stable across viewport sizes.
        """
        preferred_selectors = [
            "div.auto-input-wrap-v3 input.ant-select-selection-search-input",
            "div.ant-select-auto-complete input.ant-select-selection-search-input",
        ]
        for selector in preferred_selectors:
            try:
                locator = container.locator(selector).first
                if self._is_editable_input(locator):
                    return locator
            except Exception:
                continue

        try:
            inputs = container.locator("input.ant-select-selection-search-input")
            count = inputs.count()
        except Exception:
            return None

        best_candidate = None
        best_width = -1.0
        for index in range(count):
            candidate = inputs.nth(index)
            if not self._is_editable_input(candidate):
                continue
            try:
                box = candidate.bounding_box() or {}
                width = float(box.get("width") or 0)
            except Exception:
                width = 0.0
            if width > best_width:
                best_width = width
                best_candidate = candidate
        return best_candidate

    def _apply_one_filter(self, page: Page, title: str, value: object) -> None:
        spec = self.FILTER_FIELD_SPECS.get(title)
        if spec is None:
            raise LiepinSearchError("暂不支持该筛选字段: {}".format(title))
        if spec.field_type == "tag":
            self._apply_tag_filter(page, spec, str(value))
            return
        if spec.field_type == "dropdown":
            self._apply_dropdown_filter(page, spec, str(value))
            return
        if spec.field_type == "range":
            self._apply_range_filter(page, spec, value)
            return
        if spec.field_type == "autocomplete":
            self._apply_autocomplete_filter(page, spec, str(value))
            return
        if spec.field_type == "city_modal":
            self._apply_city_filter(page, spec, value)
            return
        raise LiepinSearchError("未实现的筛选字段类型: {}".format(spec.field_type))

    def _field_container(self, page: Page, spec: LiepinFilterFieldSpec):
        """Resolve one filter row by selector plus title text.

        Several live fields share the same container selector, especially
        current city and expected city. The mapping document says to bind fields
        by title text + parent container, so do not blindly use `.first`.
        """
        fallback = None
        title_text = self._normalize_filter_title_text(
            spec.title_text or spec.title or ""
        )
        for selector in spec.container_selectors:
            try:
                containers = page.locator(selector)
            except Exception:
                continue
            try:
                count = containers.count()
            except Exception:
                count = 0
            if count and fallback is None:
                try:
                    fallback = containers.first
                except Exception:
                    fallback = containers
            for index in range(count):
                container = containers.nth(index)
                try:
                    if not container.is_visible(timeout=1200):
                        continue
                    text = self._normalize_filter_title_text(
                        container.inner_text(timeout=1200) or ""
                    )
                    if title_text and title_text in text:
                        return container
                    if fallback is None:
                        fallback = container
                except Exception:
                    continue
        if fallback is not None:
            return fallback
        return page.locator(spec.container_selector).first

    @staticmethod
    def _normalize_filter_title_text(value: str) -> str:
        return re.sub(r"[\s:：]", "", value or "")

    def _apply_tag_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: str
    ) -> None:
        container = self._field_container(page, spec)
        normalized_value = self._normalize_tag_filter_value(spec, value, container)
        locator = container.locator(
            "label.tag-item:has-text('{}')".format(normalized_value)
        ).first
        if not locator.is_visible(timeout=3000):
            raise LiepinSearchPageChangedError(
                "未找到标签筛选项: {} -> {}".format(spec.title, normalized_value)
            )
        self._dismiss_any_open_modal(page)
        locator.click(timeout=5000)
        self._wait_for_filter_apply(page, expected_text=normalized_value)

    def _normalize_tag_filter_value(
        self, spec: LiepinFilterFieldSpec, value: str, container
    ) -> str:
        """Map abstract filter values to the concrete tag text shown on Liepin."""
        normalized = (value or "").strip()
        if spec.title == "教育经历":
            if "不限" in normalized:
                return "不限"
            if "博士" in normalized:
                return "博士/博士后"
            if "硕士" in normalized or "研究生" in normalized:
                return "硕士"
            if "本科" in normalized:
                return "本科"
            if "大专" in normalized or "专科" in normalized:
                return "大专"
            if "中专" in normalized or "中技" in normalized:
                return "中专/中技"
            if "高中" in normalized or "初中" in normalized:
                return "高中及以下"
            return normalized
        if spec.title == "语言":
            if "普通话" in normalized or "中文" in normalized or "汉语" in normalized:
                return "普通话"
            if "英语" in normalized or "英文" in normalized:
                return "英语"
            if "日语" in normalized:
                return "日语"
            if "法语" in normalized:
                return "法语"
            if "粤语" in normalized:
                return "粤语"
            if "不限" in normalized:
                return "不限"
            return normalized
        if spec.title != "工作年限":
            return normalized
        if not normalized:
            return normalized
        if normalized in ("不限", "应届生", "1-3年", "3-5年", "5-10年", "10年以上"):
            return normalized

        match = re.search(r"(\d+)\s*年\s*(?:及)?以上", normalized)
        if not match:
            match = re.search(r"(\d+)\s*年以上", normalized)
        if not match:
            return normalized

        years = int(match.group(1))
        options = self._extract_tag_texts(container)
        if years <= 1 and "应届生" in options:
            return "应届生"
        if years < 3 and "1-3年" in options:
            return "1-3年"
        if years < 5 and "3-5年" in options:
            return "3-5年"
        if years <= 10 and "5-10年" in options:
            return "5-10年"
        if "10年以上" in options:
            return "10年以上"
        return normalized

    @staticmethod
    def _extract_tag_texts(container) -> List[str]:
        try:
            text = container.inner_text(timeout=1200) or ""
        except Exception:
            return []
        patterns = ["不限", "应届生", "1-3年", "3-5年", "5-10年", "10年以上"]
        return [item for item in patterns if item in text]

    def _apply_dropdown_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: str
    ) -> None:
        container = self._field_container(page, spec)
        if not container.is_visible(timeout=3000):
            raise LiepinSearchPageChangedError(
                "未找到下拉筛选控件: {}".format(spec.title)
            )
        input_locator = container.locator(spec.input_selector).first
        normalized_value = self._normalize_dropdown_filter_value(spec, value)
        self._dismiss_any_open_modal(page)
        self._focus_dropdown_input(container, input_locator)
        try:
            input_locator.press("ArrowDown")
        except Exception:
            page.keyboard.press("ArrowDown")
        page.wait_for_timeout(200)
        try:
            options = self._open_dropdown_options(page)
            self._select_dropdown_option(options, normalized_value)
        except Exception:
            self._select_dropdown_option_by_keyboard(page, normalized_value)
        self._wait_for_filter_apply(page, expected_text=normalized_value)

    @staticmethod
    def _normalize_dropdown_filter_value(
        spec: LiepinFilterFieldSpec, value: str
    ) -> str:
        normalized = (value or "").strip()
        if spec.title == "活跃度":
            if "不限" in normalized:
                return "不限"
            if "今天" in normalized or "今日" in normalized:
                return "今天活跃"
            if "30" in normalized or "一月" in normalized or "一个月" in normalized:
                return "30天内活跃"
            if "15" in normalized or "半月" in normalized:
                return "30天内活跃"
            if "三个月" in normalized or "3个月" in normalized:
                return "最近三个月活跃"
            if "半年" in normalized:
                return "最近半年活跃"
            if "一年" in normalized or "1年" in normalized:
                return "最近一年活跃"
            if "周" in normalized or "7" in normalized or "一周" in normalized:
                return "7天内活跃"
            if "3" in normalized or "三天" in normalized:
                return "3天内活跃"
            if "活跃" not in normalized:
                return "{}活跃".format(normalized)
            return normalized
        if spec.title == "跳槽频率":
            if "不限" in normalized:
                return "不限"
            if "5" in normalized and "3" in normalized:
                return "近5年不超过3段"
            if "3" in normalized and "2" in normalized:
                return "近3年不超过2段"
            if "2段" in normalized or "两段" in normalized:
                return "近2段均不低于2年"
            return normalized
        if spec.title != "性别":
            return normalized
        if "男" in normalized:
            return "男"
        if "女" in normalized:
            return "女"
        if "不限" in normalized:
            return "不限"
        return normalized

    @staticmethod
    def _focus_dropdown_input(container, input_locator) -> None:
        """Focus an Ant Select field without clicking through its display text."""
        try:
            container.click(timeout=3000)
        except Exception:
            pass
        try:
            input_locator.focus()
            return
        except Exception:
            pass
        try:
            input_locator.click(timeout=1000, force=True)
        except Exception:
            pass

    def _apply_range_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: object
    ) -> None:
        low_value, high_value = self._normalize_range_filter_value(value)
        if not low_value and not high_value:
            return
        container = self._field_container(page, spec)
        if not container.is_visible(timeout=3000):
            raise LiepinSearchPageChangedError(
                "未找到区间筛选控件: {}".format(spec.title)
            )

        if low_value:
            low_input = self._resolve_filter_locator(
                page,
                container,
                spec.low_input_selector,
                "未找到{}最低值输入框".format(spec.title),
            )
            self._fill_filter_input(low_input, low_value)
        if high_value:
            high_input = self._resolve_filter_locator(
                page,
                container,
                spec.high_input_selector,
                "未找到{}最高值输入框".format(spec.title),
            )
            self._fill_filter_input(high_input, high_value)

        if spec.confirm_selector:
            confirm = self._resolve_filter_locator(
                page,
                container,
                spec.confirm_selector,
                "未找到{}确认按钮".format(spec.title),
            )
            confirm.click(timeout=5000)
        self._wait_for_filter_apply(page, expected_text=high_value or low_value)

    def _apply_autocomplete_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: str
    ) -> None:
        normalized_value = (value or "").strip()
        if not normalized_value:
            return
        container = self._field_container(page, spec)
        if not container.is_visible(timeout=3000):
            raise LiepinSearchPageChangedError(
                "未找到输入筛选控件: {}".format(spec.title)
            )
        input_locator = self._resolve_filter_locator(
            page, container, spec.input_selector, "未找到{}输入框".format(spec.title)
        )
        self._write_keyword(input_locator, normalized_value)
        try:
            input_locator.press("Enter")
        except Exception:
            pass
        if spec.confirm_selector:
            confirm = self._resolve_filter_locator(
                page,
                container,
                spec.confirm_selector,
                "未找到{}确认按钮".format(spec.title),
            )
            confirm.click(timeout=5000)
        self._wait_for_filter_apply(page, expected_text=normalized_value)

    @staticmethod
    def _normalize_range_filter_value(value: object) -> Tuple[str, str]:
        if isinstance(value, dict):
            low_keys = (
                "min",
                "low",
                "from",
                "最低",
                "下限",
                "最低值",
                "最低年龄",
                "最低年薪",
            )
            high_keys = (
                "max",
                "high",
                "to",
                "最高",
                "上限",
                "最高值",
                "最高年龄",
                "最高年薪",
            )
            low = next(
                (
                    value.get(key)
                    for key in low_keys
                    if value.get(key) not in (None, "")
                ),
                "",
            )
            high = next(
                (
                    value.get(key)
                    for key in high_keys
                    if value.get(key) not in (None, "")
                ),
                "",
            )
            return str(low).strip(), str(high).strip()
        if isinstance(value, (list, tuple)):
            low = value[0] if len(value) > 0 else ""
            high = value[1] if len(value) > 1 else ""
            return str(low or "").strip(), str(high or "").strip()

        text = str(value or "").strip()
        if not text or text == "不限":
            return "", ""
        range_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:-|~|至|到|,|，)\s*(\d+(?:\.\d+)?)", text
        )
        if range_match:
            return range_match.group(1), range_match.group(2)
        high_match = re.search(
            r"(?:不超过|不高于|以内|以下|小于|<=|≤)\s*(\d+(?:\.\d+)?)", text
        )
        if not high_match:
            high_match = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:岁|万|k|K)?\s*(?:以内|以下)", text
            )
        if high_match:
            return "", high_match.group(1)
        low_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:岁|万|k|K)?\s*(?:以上|及以上|\+)", text
        )
        if low_match:
            return low_match.group(1), ""
        single_match = re.search(r"(\d+(?:\.\d+)?)", text)
        if single_match:
            return single_match.group(1), ""
        return "", ""

    def _resolve_filter_locator(
        self, page: Page, container, selector: str, error_message: str
    ):
        selectors = [
            item.strip() for item in (selector or "").split("||") if item.strip()
        ]
        if not selectors:
            raise LiepinSearchPageChangedError(error_message)
        for owner in (container, page):
            for candidate_selector in selectors:
                try:
                    locator = owner.locator(candidate_selector)
                    count = locator.count()
                except Exception:
                    count = 0
                for index in range(count):
                    try:
                        candidate = locator.nth(index)
                        if candidate.is_visible(timeout=700):
                            return candidate
                    except Exception:
                        continue
                try:
                    candidate = locator.first
                    if candidate.is_visible(timeout=700):
                        return candidate
                except Exception:
                    continue
        raise LiepinSearchPageChangedError(error_message)

    @staticmethod
    def _fill_filter_input(locator, value: str) -> None:
        locator.click(timeout=3000)
        try:
            locator.fill("")
            locator.fill(value)
            return
        except Exception:
            pass
        try:
            locator.press("Control+A")
            locator.press("Backspace")
            locator.type(value, delay=30)
        except Exception:
            locator.fill(value)

    def _apply_city_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: object
    ) -> None:
        cities = [
            item
            for item in (value if isinstance(value, list) else [value])
            if str(item).strip()
        ]
        cities = [str(item).strip() for item in cities]
        if not cities:
            return
        if len(cities) == 1:
            self._apply_single_city_filter(page, spec, cities[0])
            return

        container = self._field_container(page, spec)
        trigger = container.locator("span.btn-choose:has-text('其他')").first
        if not trigger.is_visible(timeout=3000):
            raise LiepinSearchPageChangedError(
                "未找到城市其他入口: {}".format(spec.title)
            )
        trigger.click(timeout=5000)
        modal = self._resolve_city_modal(page)
        try:
            for city in cities:
                self._select_city_in_modal(modal, city)
            confirm = self._resolve_city_modal_confirm_button(modal)
            self._click_city_modal_confirm(modal, confirm)
            page.wait_for_timeout(500)
            self._wait_for_city_modal_closed(page, modal, timeout=8000)
            page.wait_for_timeout(300)
            self._wait_for_filter_apply(page, expected_text=cities[0])
        except Exception:
            self._dismiss_any_open_modal(page)
            raise

    def _apply_single_city_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: str
    ) -> None:
        container = self._field_container(page, spec)
        hot_tag = container.locator("label.tag-item:has-text('{}')".format(value)).first
        try:
            if hot_tag.is_visible(timeout=1200):
                hot_tag.click(timeout=5000)
                self._wait_for_filter_apply(page, expected_text=value)
                return
        except Exception:
            pass

        trigger = container.locator("span.btn-choose:has-text('其他')").first
        if not trigger.is_visible(timeout=3000):
            raise LiepinSearchPageChangedError(
                "未找到城市其他入口: {}".format(spec.title)
            )
        trigger.click(timeout=5000)
        modal = self._resolve_city_modal(page)

        try:
            self._select_city_in_modal(modal, value)
            confirm = self._resolve_city_modal_confirm_button(modal)
            self._click_city_modal_confirm(modal, confirm)
            page.wait_for_timeout(500)
            self._wait_for_city_modal_closed(page, modal, timeout=8000)
            page.wait_for_timeout(300)
            self._wait_for_filter_apply(page, expected_text=value)
        except Exception:
            self._dismiss_any_open_modal(page)
            raise

    def _dismiss_any_open_modal(self, page: Page) -> None:
        """关闭页面上可能残留的城市选择模态框/遮罩层，防止遮挡后续交互。"""
        try:
            modal = page.locator(
                "div.ant-modal.city-modal, div.ant-modal-wrap.antd-fd-city-modal"
            ).first
            is_visible = modal.is_visible(timeout=700)
        except Exception:
            is_visible = False

        if is_visible:
            logger.warning("检测到残留模态框，尝试关闭...")
            for selector in (
                'button:has-text("取消")',
                "button.ant-modal-close",
                "span.ant-modal-close-x",
            ):
                try:
                    close_btn = modal.locator(selector).first
                    if close_btn.is_visible(timeout=500):
                        close_btn.click(timeout=3000)
                        page.wait_for_timeout(300)
                        break
                except Exception:
                    continue
            try:
                if modal.is_visible(timeout=500):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
            except Exception:
                pass

        try:
            page.evaluate("""() => {
                document.querySelectorAll('.ant-select-dropdown').forEach(function(el) {
                    var style = window.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        el.style.display = 'none';
                        el.style.pointerEvents = 'none';
                    }
                });
                document.querySelectorAll('.ant-modal-wrap, .ant-modal-mask, .ant-city-menu-list').forEach(function(el) {
                    var style = window.getComputedStyle(el);
                    var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    var visible = style.display !== 'none' && style.visibility !== 'hidden' && rect && rect.width > 0 && rect.height > 0;
                    if (!visible) {
                        el.style.pointerEvents = 'none';
                    }
                });
            }""")
        except Exception as exc:
            logger.debug("JS dismiss modal failed: %s", exc)

    @staticmethod
    def _first_visible_locator(owner, selectors: List[str], timeout: int = 500):
        fallback = None
        for selector in selectors:
            locator = owner.locator(selector)
            if fallback is None:
                try:
                    fallback = locator.first
                except Exception:
                    fallback = locator
            try:
                count = locator.count()
            except Exception:
                count = 0
            if count:
                for index in range(count):
                    try:
                        candidate = locator.nth(index)
                        if candidate.is_visible(timeout=timeout):
                            return candidate
                    except Exception:
                        continue
            try:
                candidate = locator.first
                if candidate.is_visible(timeout=timeout):
                    return candidate
            except Exception:
                continue
        return fallback or owner.locator(selectors[0]).first

    def _resolve_city_modal(self, page: Page):
        selectors = [
            "div.ant-modal.city-modal",
            "div.ant-modal-wrap.antd-fd-city-modal div.ant-modal",
            "div.ant-modal:has(input.ant-input)",
            "div.ant-modal:has(.suggest-list)",
        ]
        import time

        deadline = time.time() + 5
        last_exc = None
        while time.time() < deadline:
            for selector in selectors:
                try:
                    locators = page.locator(selector)
                    count = locators.count()
                except Exception as exc:
                    last_exc = exc
                    continue
                for index in range(count):
                    modal = locators.nth(index)
                    try:
                        if modal.is_visible(timeout=300):
                            return modal
                    except Exception as exc:
                        last_exc = exc
                        continue
            try:
                page.wait_for_timeout(200)
            except Exception:
                break
        raise LiepinSearchPageChangedError(
            "城市选择弹窗未打开: {}".format(last_exc or "未找到可见弹窗")
        )

    def _wait_for_city_modal_closed(
        self, page: Page, modal, timeout: int = 8000
    ) -> None:
        try:
            modal.wait_for(state="hidden", timeout=timeout)
            return
        except Exception:
            pass
        self._dismiss_any_open_modal(page)
        try:
            modal.wait_for(state="hidden", timeout=1000)
        except Exception:
            pass

    def _select_city_in_modal(self, modal, value: str) -> None:
        if self._click_city_option_in_modal(modal, value):
            return

        city_input = self._first_visible_locator(
            modal,
            [
                'input.ant-input[placeholder="搜索城市"]',
                'input[placeholder*="城市"]',
                "input.ant-input",
                'input[type="text"]',
            ],
            timeout=500,
        )
        city_input.click(timeout=3000)
        city_input.fill(value)
        try:
            city_input.press("Enter")
        except Exception:
            pass
        try:
            modal.page.wait_for_timeout(500)
        except Exception:
            pass
        if self._click_city_option_in_modal(modal, value):
            return
        raise LiepinSearchPageChangedError("未找到城市选项: {}".format(value))

    def _click_city_option_in_modal(self, modal, value: str) -> bool:
        """Click a city choice, including Liepin's `全上海` style choices.

        Supports both regular cities ("广东 · 深圳") and municipalities ("中国 · 上海").
        """
        normalized = (value or "").strip()
        if not normalized:
            return False
        expected_texts = (normalized, "全{}".format(normalized))
        selectors = [
            "span.ant-tag.ant-tag-checkable",
            "label.tag-item",
            "button",
            "div.suggest-list li",
            ".suggest-list li",
            ".ant-city-menu-list li",
            ".ant-city-menu-list span",
            "[class*='city'] li",
            "[class*='city'] span",
            "li",
            "span",
        ]
        for selector in selectors:
            try:
                locators = modal.locator(selector)
                count = locators.count()
            except Exception:
                continue
            for index in range(count):
                option = locators.nth(index)
                try:
                    if not option.is_visible(timeout=250):
                        continue
                    text = self._normalize_filter_title_text(
                        option.inner_text(timeout=500) or ""
                    )
                    # Support both exact match and contains match
                    # e.g., "上海" should match "中国 · 上海" or "全上海"
                    is_match = text in expected_texts or any(
                        expected in text for expected in expected_texts
                    )
                    if not is_match:
                        continue
                    option.click(timeout=5000)
                    return True
                except Exception:
                    continue
        return False

    def _click_city_modal_confirm(self, modal, confirm) -> None:
        try:
            self._wait_for_enabled_locator(confirm, timeout=2500)
            confirm.click(timeout=5000)
            return
        except Exception:
            pass
        try:
            clicked = modal.evaluate(
                r"""(root) => {
                    const buttons = Array.from(root.querySelectorAll('button'));
                    const target = buttons.find((btn) => {
                        const text = (btn.innerText || btn.textContent || '').replace(/\s+/g, '');
                        return text.includes('确认') || text.includes('确定');
                    });
                    if (!target) return false;
                    target.removeAttribute('disabled');
                    target.classList.remove('ant-btn-disabled');
                    target.click();
                    return true;
                }"""
            )
        except Exception:
            clicked = False
        if clicked:
            return
        raise LiepinSearchPageChangedError("城市筛选确认按钮未启用")

    def _resolve_city_modal_confirm_button(self, modal):
        confirm = modal.locator('button:has-text("确认")').first
        try:
            if confirm.is_visible(timeout=1200):
                return confirm
        except Exception:
            pass
        return modal.locator("button.ant-btn.ant-btn-primary").first

    def _wait_for_enabled_locator(self, locator, timeout: int = 5000) -> None:
        import time

        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            if self._is_enabled_locator(locator):
                return
            try:
                locator.wait_for(state="visible", timeout=300)
            except Exception:
                pass
        raise LiepinSearchPageChangedError("城市筛选确认按钮未启用")

    @staticmethod
    def _is_enabled_locator(locator) -> bool:
        try:
            disabled = locator.get_attribute("disabled")
            aria_disabled = (locator.get_attribute("aria-disabled") or "").lower()
            class_name = locator.get_attribute("class") or ""
            return (
                disabled is None
                and aria_disabled != "true"
                and "disabled" not in class_name
            )
        except Exception:
            return False

    def _open_dropdown_options(self, page: Page):
        selectors = [
            "div.ant-select-dropdown.search-select",
            "div.ant-select-dropdown:not(.ant-select-dropdown-hidden)",
            "div.ant-select-dropdown",
        ]
        dropdown = self._first_visible_locator(page, selectors, timeout=500)
        try:
            dropdown.wait_for(state="visible", timeout=1500)
        except Exception:
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(200)
            dropdown = self._first_visible_locator(page, selectors, timeout=500)
            dropdown.wait_for(state="visible", timeout=3000)
        return dropdown.locator("div.ant-select-item.ant-select-item-option")

    def _select_dropdown_option(self, options, value: str) -> None:
        count = options.count()
        for index in range(count):
            option = options.nth(index)
            try:
                text = (option.inner_text(timeout=1000) or "").strip()
            except Exception:
                continue
            if value == text or value in text:
                option.click(timeout=5000)
                return
        raise LiepinSearchPageChangedError("未找到下拉选项: {}".format(value))

    @staticmethod
    def _select_dropdown_option_by_keyboard(page: Page, value: str) -> None:
        """Fallback for Ant Select fields where visible option locators are unstable."""
        steps_by_value = {
            "不限": 1,
            "男": 2,
            "女": 3,
            "今天活跃": 2,
            "3天内活跃": 3,
            "7天内活跃": 4,
            "30天内活跃": 5,
            "最近三个月活跃": 6,
            "最近半年活跃": 7,
            "最近一年活跃": 8,
            "近5年不超过3段": 2,
            "近3年不超过2段": 3,
            "近2段均不低于2年": 4,
        }
        steps = steps_by_value.get((value or "").strip())
        if steps is None:
            raise LiepinSearchPageChangedError("未找到下拉选项: {}".format(value))
        for _ in range(max(0, steps - 1)):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(120)
        page.keyboard.press("Enter")

    def _wait_for_filter_apply(
        self, page: Page, expected_text: str = "", timeout: int = 12000
    ) -> None:
        """Wait until the filter-driven refresh cycle settles."""
        import time

        self._wait_for_loading_cycle(page, timeout=timeout)
        self._soft_wait_for_results(page)
        if not expected_text:
            return
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            try:
                body_text = page.locator("body").inner_text(timeout=1500)
                if expected_text in (body_text or ""):
                    return
            except Exception:
                pass
            try:
                page.wait_for_timeout(300)
            except Exception:
                break

    def _wait_for_loading_cycle(self, page: Page, timeout: int = 12000) -> None:
        import time

        deadline = time.time() + timeout / 1000.0
        saw_loading = False
        while time.time() < deadline:
            loading = self._is_loading(page)
            if loading:
                saw_loading = True
            if saw_loading and not loading:
                return
            try:
                page.wait_for_timeout(250)
            except Exception:
                break

    def _is_loading(self, page: Page) -> bool:
        for selector in self.LOADING_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _locator_top(locator) -> float:
        """Return the vertical position for one locator."""
        try:
            box = locator.bounding_box()
            if box and "y" in box:
                return float(box["y"])
        except Exception:
            pass
        return float("inf")

    def _clear_search_inputs(self, page: Page) -> None:
        """Clear all candidate search inputs before a new attempt."""
        for locator in self._find_candidate_search_inputs(page):
            try:
                locator.click(timeout=1000)
                locator.fill("")
            except Exception:
                continue

    def _write_keyword(self, locator, keyword: str, force_focus: bool = False) -> None:
        """Write a keyword into an input and verify that the value stuck."""
        try:
            locator.click(timeout=5000, force=force_focus)
        except Exception:
            if not force_focus:
                raise
            locator.focus()
        try:
            locator.fill("")
        except Exception:
            pass
        locator.press("Control+A")
        locator.press("Backspace")
        locator.type(keyword, delay=40)

        value = ""
        try:
            value = locator.input_value(timeout=1500)
        except Exception:
            try:
                value = locator.get_attribute("value") or ""
            except Exception:
                value = ""

        if keyword not in value:
            try:
                locator.fill(keyword)
                value = locator.input_value(timeout=1500)
            except Exception:
                pass

        if keyword not in (value or ""):
            try:
                locator.evaluate(
                    """(element, value) => {
                        element.value = value;
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    keyword,
                )
                value = locator.input_value(timeout=1500)
            except Exception:
                pass

        if keyword not in (value or ""):
            raise LiepinSearchPageChangedError("关键词未能写入搜索输入框")

    @staticmethod
    def _is_editable_input(locator) -> bool:
        """Return whether a locator points to a visible, enabled text input."""
        try:
            if not locator.is_visible(timeout=1500):
                return False
            disabled = locator.get_attribute("disabled")
            readonly = locator.get_attribute("readonly")
            input_type = (locator.get_attribute("type") or "text").lower()
            return (
                disabled is None
                and readonly is None
                and input_type in ("text", "search")
            )
        except Exception:
            return False
