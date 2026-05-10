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

class _PositionFilterMixin:
    """Mixin providing position filter functionality."""
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


