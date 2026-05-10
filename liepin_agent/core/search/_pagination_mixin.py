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

class _PaginationMixin:
    """Mixin providing pagination functionality."""
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


