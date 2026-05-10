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

class _BaseMixin:
    """Mixin providing base functionality."""
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


