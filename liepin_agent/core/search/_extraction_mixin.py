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

class _ExtractionMixin:
    """Mixin providing extraction functionality."""
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
                raw_text=text,
                profile_url=profile_url,
                result_index=len(candidates),
            )
            candidates.append(candidate)
        return candidates


    def extract_current_page_candidates(self) -> List[LiepinSearchCandidate]:
        """Parse candidate summaries from the current page without searching."""

        def _run(p):
            return self.extract_candidates_from_page(p)

        return self._with_debug_snapshot(
            "current_result_page",
            lambda: self.browser_manager.run_with_page(_run),
        )


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
                    raw_text="\n".join(lines),
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


