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
            raise LiepinSearchPageChangedError(
                "未找到职位名称输入框，无法应用职位筛选: {}".format(value)
            )
        try:
            self._write_keyword(input_locator, value, force_focus=True)
            self._wait_for_condition_chip(page, "职位名称", value, timeout=3000)
        except Exception as exc:
            raise LiepinSearchPageChangedError(
                "职位名称筛选未生效: {} ({})".format(value, exc)
            ) from exc


    def _find_position_name_input(self, page: Page):
        selectors = [
            ".auto-select-job-shadow-box .auto-select-base "
            "input.ant-select-selection-search-input",
            "div.search-item:has(span.search-item-title:has-text('职位名称')) "
            ".auto-select-base input.ant-select-selection-search-input",
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


