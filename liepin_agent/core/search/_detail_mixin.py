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

class _DetailMixin:
    """Mixin providing detail functionality."""
    def ensure_result_page(self):
        """Return the active result page and validate it still looks like search."""

        def _run(page):
            url = (page.url or "").lower()
            if not self.browser_manager._is_search_page_url(url):
                raise LiepinSearchPageChangedError("当前活动页不是搜索结果页")
            return page

        return self.browser_manager.run_with_page(_run)


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


    def _is_detail_page_url(url: str) -> bool:
        normalized = (url or "").lower()
        return "showresumedetail" in normalized or "/resume/" in normalized


    def _ensure_absolute_url(url: str) -> str:
        if url and url.startswith("/") and not url.startswith("//"):
            return "https://h.liepin.com" + url
        return url


