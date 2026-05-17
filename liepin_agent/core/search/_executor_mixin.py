"""Auto-generated mixin for LiepinSearchService refactoring."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from playwright.sync_api import Error, Page
except ImportError:  # pragma: no cover
    Error = Exception
    Page = None

logger = logging.getLogger(__name__)

from ._models import (
    LiepinSearchCandidate,
    LiepinSearchControls,
    LiepinFilterFieldSpec,
    LiepinSearchError,
    LiepinSearchPageChangedError,
    LiepinSearchNoResultsError,
)

class _ExecutorMixin:
    """Mixin providing executor functionality."""
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
            candidates = self.extract_candidates_from_page(page)
            page_meta = self._extract_page_meta(page)
            for c in candidates:
                c.page_meta = page_meta
            return candidates

        return self._with_debug_snapshot(
            "search_keyword_{}".format(keyword.strip()),
            lambda: self.browser_manager.run_with_page(_run),
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
    def _extract_page_meta(page: Page) -> Dict[str, Any]:
        """Extract real result-page metadata (total results, pagination, etc.)."""
        try:
            return page.evaluate(
                r"""
                () => {
                    const result = { total_results: null, current_page: 1, has_next_page: false, has_prev_page: false, total_results_text: "" };
                    const bodyText = document.body.innerText || "";
                    const match = bodyText.match(/共\s*(\d+)\s*条|找到\s*(\d+)\s*人|(\d+)\s*条结果/);
                    if (match) {
                        result.total_results_text = match[0];
                        result.total_results = parseInt(match[1] || match[2] || match[3], 10);
                    }
                    const pagination = document.querySelector('.ant-pagination, .pagination, [class*="pagination"]');
                    if (pagination) {
                        const activeItem = pagination.querySelector('.ant-pagination-item-active, .active, .current');
                        if (activeItem) {
                            const text = (activeItem.innerText || activeItem.textContent || '').trim();
                            result.current_page = parseInt(text, 10) || 1;
                        }
                        const nextBtn = pagination.querySelector('.ant-pagination-next, [title="下一页"], .next-page');
                        result.has_next_page = !!nextBtn && !nextBtn.classList.contains('ant-pagination-disabled');
                        const prevBtn = pagination.querySelector('.ant-pagination-prev, [title="上一页"], .prev-page');
                        result.has_prev_page = !!prevBtn && !prevBtn.classList.contains('ant-pagination-disabled');
                    }
                    return result;
                }
                """
            )
        except Exception:
            return {}

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


