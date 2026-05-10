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

class _ControlsMixin:
    """Mixin providing controls functionality."""
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


    def _find_primary_search_input(self, page: Page):
        """Find the main free-text search input on the resume search page."""
        return self._detect_search_controls(page).search_input


