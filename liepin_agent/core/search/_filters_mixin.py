"""Auto-generated mixin for LiepinSearchService refactoring."""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

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

class _FiltersMixin:
    """Mixin providing filters functionality."""
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
        """Apply the complete desired filter state to an open result page."""
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
        failures = []
        for title, value in normalized_filters.items():
            try:
                self._apply_filter_with_retries(page, title, value)
            except Exception as exc:
                failures.append("{}={} ({})".format(title, value, exc))
                logger.warning("filter apply failed: %s=%s reason=%s", title, value, exc)
                self._dismiss_any_open_modal(page)
        if failures:
            raise LiepinSearchError(
                "筛选条件未完整生效，已停止本轮搜索: {}".format("; ".join(failures))
            )


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


    def _apply_one_filter(self, page: Page, title: str, value: object) -> None:
        spec = self.FILTER_FIELD_SPECS.get(title)
        if spec is None:
            raise LiepinSearchError("暂不支持该筛选字段: {}".format(title))
        self._clear_filter_condition(page, spec.title)
        if self._filter_value_is_clear(value):
            return
        if spec.field_type == "tag":
            for tag_value in self._normalize_tag_filter_values(spec, value):
                self._apply_tag_filter(page, spec, tag_value)
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
            if count and fallback is None and not title_text:
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
                    if fallback is None and not title_text:
                        fallback = container
                except Exception:
                    continue
        if fallback is not None:
            return fallback
        raise LiepinSearchPageChangedError(
            "未找到筛选行或标题不匹配: {}".format(spec.title)
        )


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
        self._wait_for_condition_chip(page, spec.title, normalized_value)


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
        # Do NOT call _dismiss_any_open_modal here — it presses Escape and
        # runs JS that hides .ant-select-dropdown elements, which disrupts
        # the Ant Select focus we are about to establish.  The caller
        # (_apply_filter_with_retries) already dismisses modals before
        # calling us.
        self._focus_dropdown_input(container, input_locator)
        try:
            options = self._open_dropdown_options(page)
            self._select_dropdown_option(options, normalized_value)
        except Exception:
            # Keyboard fallback: close any stale dropdown, re-focus the
            # input to guarantee a known state, then navigate purely by
            # keyboard per the mapping doc (focus input → ArrowDown opens
            # dropdown → more ArrowDowns navigate → Enter selects).
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
            except Exception:
                pass
            try:
                input_locator.focus()
            except Exception:
                pass
            self._select_dropdown_option_by_keyboard(page, normalized_value)
        self._wait_for_filter_apply(page, expected_text=normalized_value)
        self._wait_for_condition_chip(page, spec.title, normalized_value)


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

        self._dismiss_any_open_modal(page)
        low_input = self._resolve_filter_locator(
            page,
            container,
            spec.low_input_selector,
            "未找到{}最低值输入框".format(spec.title),
        )
        high_input = self._resolve_filter_locator(
            page,
            container,
            spec.high_input_selector,
            "未找到{}最高值输入框".format(spec.title),
        )
        # Always write both ends. This prevents an old upper/lower bound from
        # surviving when a later round changes a closed range to a one-sided one.
        self._fill_filter_input(low_input, low_value)
        self._fill_filter_input(high_input, high_value)

        if spec.confirm_selector:
            # The live page hides the submit button with display:none until the
            # range wrapper is hovered. Filling inputs updates the condition chip
            # locally but does not necessarily refresh result cards.
            try:
                container.hover(timeout=3000)
            except Exception as exc:
                raise LiepinSearchPageChangedError(
                    "无法展开{}确认按钮: {}".format(spec.title, exc)
                ) from exc
            confirm = self._resolve_filter_locator(
                page,
                container,
                spec.confirm_selector,
                "未找到{}确认按钮".format(spec.title),
            )
            confirm.click(timeout=5000)
        self._wait_for_filter_apply(page, expected_text=high_value or low_value)
        self._wait_for_condition_chip(
            page,
            spec.title,
            high_value or low_value,
        )


    def _apply_city_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: object
    ) -> None:
        cities = [
            item
            for item in (
                value if isinstance(value, (list, tuple, set)) else [value]
            )
            if str(item).strip()
        ]
        cities = list(dict.fromkeys(str(item).strip() for item in cities))
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
            for city in cities:
                self._wait_for_condition_chip(page, spec.title, city)
        except Exception:
            self._dismiss_any_open_modal(page)
            raise


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
        if spec.title != "公司名称":
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
        self._wait_for_condition_chip(page, spec.title, normalized_value)


    def _apply_single_city_filter(
        self, page: Page, spec: LiepinFilterFieldSpec, value: str
    ) -> None:
        container = self._field_container(page, spec)
        hot_tag = container.locator("label.tag-item:has-text('{}')".format(value)).first
        try:
            if hot_tag.is_visible(timeout=1200):
                hot_tag.click(timeout=5000)
                self._wait_for_filter_apply(page, expected_text=value)
                self._wait_for_condition_chip(page, spec.title, value)
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
            self._wait_for_condition_chip(page, spec.title, value)
        except Exception:
            self._dismiss_any_open_modal(page)
            raise


    def _clear_managed_filter_conditions(self, page: Page) -> None:
        """Reset all filter fields owned by the Agent before a new round."""
        titles = getattr(
            self,
            "MANAGED_FILTER_TITLES",
            (
                "职位名称",
                "期望城市",
                "教育经历",
                "年龄",
                "性别",
                "活跃度",
                "期望年薪",
                "公司名称",
            ),
        )
        for title in dict.fromkeys(titles):
            self._clear_filter_condition(page, title)


    def _clear_filter_condition(self, page: Page, title: str) -> None:
        """Remove every active chip for one field through its own close icon."""
        chips = self._condition_chips(page, title)
        while True:
            try:
                before = chips.count()
            except Exception:
                before = 0
            if before <= 0:
                return
            close = chips.first.locator(".icon-close").first
            try:
                if close.count() <= 0:
                    raise LiepinSearchPageChangedError(
                        "{}条件标签缺少删除按钮".format(title)
                    )
                try:
                    close.click(timeout=5000)
                except Exception:
                    close.click(timeout=3000, force=True)
            except Exception as exc:
                raise LiepinSearchPageChangedError(
                    "无法删除旧{}筛选: {}".format(title, exc)
                ) from exc

            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    if chips.count() < before:
                        break
                except Exception:
                    break
                page.wait_for_timeout(100)
            else:
                raise LiepinSearchPageChangedError(
                    "删除{}筛选后条件标签仍然存在".format(title)
                )
            self._wait_for_loading_cycle(page, timeout=8000)
            self._soft_wait_for_results(page)


    @staticmethod
    def _condition_chips(page: Page, title: str):
        safe_title = (title or "").replace("\\", "\\\\").replace('"', '\\"')
        return page.locator('label[title="{}"]'.format(safe_title))


    def _wait_for_condition_chip(
        self,
        page: Page,
        title: str,
        expected_text: str = "",
        timeout: int = 5000,
    ) -> None:
        """Require the applied-condition chip instead of scanning body text."""
        chips = self._condition_chips(page, title)
        expected = self._normalize_filter_title_text(expected_text)
        deadline = time.time() + timeout / 1000.0
        observed = []
        while time.time() < deadline:
            observed = []
            try:
                count = chips.count()
            except Exception:
                count = 0
            for index in range(count):
                try:
                    observed.append(chips.nth(index).inner_text(timeout=500) or "")
                except Exception:
                    continue
            if observed and (
                not expected
                or any(
                    expected in self._normalize_filter_title_text(text)
                    for text in observed
                )
            ):
                return
            try:
                page.wait_for_timeout(100)
            except Exception:
                break
        raise LiepinSearchPageChangedError(
            "{}筛选未生成有效条件标签，期望={}，实际={}".format(
                title,
                expected_text,
                observed,
            )
        )


    @staticmethod
    def _filter_value_is_clear(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() in {"", "不限"}
        if isinstance(value, dict):
            return not value or all(item in (None, "", [], {}) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return not cleaned or cleaned == ["不限"]
        return False


    def _normalize_tag_filter_values(
        self, spec: LiepinFilterFieldSpec, value: object
    ) -> List[str]:
        if isinstance(value, (list, tuple, set)):
            raw_values = [str(item).strip() for item in value if str(item).strip()]
        else:
            text = str(value or "").strip()
            raw_values = [text] if text else []

        if spec.title == "教育经历" and len(raw_values) == 1:
            compact = re.sub(r"\s+", "", raw_values[0])
            minimum_options = {
                "大专及以上": ["大专", "本科", "硕士", "博士/博士后"],
                "大专以上": ["大专", "本科", "硕士", "博士/博士后"],
                "本科及以上": ["本科", "硕士", "博士/博士后"],
                "本科以上": ["本科", "硕士", "博士/博士后"],
                "硕士及以上": ["硕士", "博士/博士后"],
                "硕士以上": ["硕士", "博士/博士后"],
            }
            if compact in minimum_options:
                raw_values = minimum_options[compact]
            elif re.search(r"[、,，/|]", compact):
                raw_values = [
                    item.strip()
                    for item in re.split(r"[、,，/|]+", compact)
                    if item.strip()
                ]

        normalized_values = []
        for raw_value in raw_values:
            normalized = self._normalize_tag_filter_value(spec, raw_value, None)
            if normalized and normalized not in normalized_values:
                normalized_values.append(normalized)
        return normalized_values


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


    @staticmethod
    def _normalize_filter_title_text(value: str) -> str:
        return re.sub(r"[\s:：]", "", value or "")


    @staticmethod
    def _extract_tag_texts(container) -> List[str]:
        try:
            text = container.inner_text(timeout=1200) or ""
        except Exception:
            return []
        patterns = ["不限", "应届生", "1-3年", "3-5年", "5-10年", "10年以上"]
        return [item for item in patterns if item in text]


    @staticmethod
    def _focus_dropdown_input(container, input_locator) -> None:
        """Focus an Ant Select field by clicking its selector element.

        Clicking the broad container often hits the title text (e.g.
        ``活跃度：``) instead of the actual Ant Select, which neither opens
        the dropdown nor focuses the input.  Targeting
        ``.ant-select-selector`` directly is the reliable way to activate
        the component.
        """
        for selector in (
            ".ant-select-selector",
            "div.ant-select",
            ".sfilter-other-select",
        ):
            try:
                target = container.locator(selector).first
                if target.is_visible(timeout=1000):
                    target.click(timeout=3000)
                    break
            except Exception:
                continue
        try:
            input_locator.focus()
            return
        except Exception:
            pass
        try:
            input_locator.click(timeout=1000, force=True)
        except Exception:
            pass


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
        normalized_value = self._normalize_dropdown_option_text(value)
        fallback_option = None
        for index in range(count):
            option = options.nth(index)
            try:
                text = option.inner_text(timeout=1000) or ""
            except Exception:
                continue
            normalized_text = self._normalize_dropdown_option_text(text)
            if normalized_value == normalized_text:
                option.click(timeout=5000)
                return
            if normalized_value in normalized_text and fallback_option is None:
                fallback_option = option
        if fallback_option is not None:
            fallback_option.click(timeout=5000)
            return
        raise LiepinSearchPageChangedError("未找到下拉选项: {}".format(value))


    @classmethod
    def _normalize_dropdown_option_text(cls, value: str) -> str:
        normalized = cls._normalize_filter_title_text(value)
        if normalized.endswith("内活跃"):
            return normalized[: -len("内活跃")]
        if normalized.endswith("活跃"):
            return normalized[: -len("活跃")]
        return normalized


    @staticmethod
    def _select_dropdown_option_by_keyboard(page: Page, value: str) -> None:
        """Fallback for Ant Select fields where visible option locators are unstable.

        Assumes the input is already focused and the dropdown is CLOSED.
        The first ArrowDown opens the dropdown and highlights option 0;
        each subsequent ArrowDown advances by one.  So ``steps`` total
        ArrowDowns lands on option ``steps - 1`` (0-indexed), which matches
        the ``steps_by_value`` mapping below.
        """
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
        for _ in range(max(0, steps)):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(120)
        page.keyboard.press("Enter")


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
        try:
            locator.click(timeout=3000)
        except Exception:
            # 如果被遮挡，尝试强制点击
            try:
                locator.click(timeout=3000, force=True)
            except Exception:
                locator.focus()
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
        if normalized in ("北京", "上海", "天津", "重庆"):
            whole_city = ["全{}".format(normalized), "全{}市".format(normalized)]
            if self._click_exact_city_option(modal, whole_city):
                return True
            # The modal first drills into a municipality and only then exposes
            # the selectable "全北京/全上海" tag.
            if self._click_exact_city_option(
                modal, [normalized, "{}市".format(normalized)]
            ):
                try:
                    modal.page.wait_for_timeout(300)
                except Exception:
                    pass
                return self._click_exact_city_option(modal, whole_city)

        if self._click_exact_city_option(modal, [normalized]):
            return True
        expected_texts = [normalized, "全{}".format(normalized)]
        # 直辖市常见变体（猎聘城市弹窗中可能显示为"上海市"等）
        if normalized in ("北京", "上海", "天津", "重庆"):
            expected_texts.extend([
                "{}市".format(normalized),
                "全{}市".format(normalized),
            ])
        selectors = [
            "span.ant-tag.ant-tag-checkable",
            "label.tag-item",
            "button",
            "div.suggest-list li",
            ".suggest-list li",
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
                    is_match = text in expected_texts or text.endswith(
                        "·{}".format(normalized)
                    )
                    if not is_match:
                        continue
                    option.click(timeout=5000)
                    return True
                except Exception:
                    continue
        return False


    def _click_exact_city_option(self, modal, expected_texts: List[str]) -> bool:
        expected = {
            self._normalize_filter_title_text(text)
            for text in expected_texts
            if str(text or "").strip()
        }
        selectors = (
            "span.ant-tag.ant-tag-checkable",
            "div.suggest-list li",
            ".suggest-list li",
            "label.tag-item",
            "button",
        )
        for selector in selectors:
            try:
                options = modal.locator(selector)
                count = options.count()
            except Exception:
                continue
            for index in range(count):
                option = options.nth(index)
                try:
                    if not option.is_visible(timeout=250):
                        continue
                    text = self._normalize_filter_title_text(
                        option.inner_text(timeout=500) or ""
                    )
                    if text not in expected:
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


