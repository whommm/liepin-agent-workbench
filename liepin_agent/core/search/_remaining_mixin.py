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
from ..liepin_browser import LiepinBrowserManager

class _RemainingMixin:
    """Mixin providing remaining functionality."""
    def __init__(self, browser_manager: LiepinBrowserManager):
        self.browser_manager = browser_manager


    def _fill_search_input(self, page: Page, keyword: str) -> None:
        input_locator = self._find_primary_search_input(page)
        if input_locator is None:
            raise LiepinSearchPageChangedError("未找到猎聘搜索输入框，请检查页面结构")

        self._write_keyword(input_locator, keyword)


