#!/usr/bin/env python3
"""Regenerate mixins with correct indentation."""

import os
import subprocess

# Get original file content
original = subprocess.check_output(
    ['git', 'show', 'HEAD:liepin_agent/core/liepin_search_service.py'],
    encoding='utf-8'
)
lines = original.split('\n')

# Parse method ranges (same logic as before)
class_start = None
for i, line in enumerate(lines):
    if "class LiepinSearchService:" in line:
        class_start = i
        break

method_ranges = []
current_name = None
current_start = None

for i in range(class_start + 1, len(lines)):
    line = lines[i]
    stripped = line.rstrip()
    if stripped and not stripped.startswith(" ") and not stripped.startswith("\t"):
        if stripped.startswith("def ") or stripped.startswith("class "):
            break
    if stripped.startswith("    def ") or (
        stripped.startswith("    @")
        and not stripped.startswith("    @dataclass")
    ):
        if current_name is not None:
            method_ranges.append((current_name, current_start, i))
        if stripped.startswith("    @"):
            current_start = i
            next_line = lines[i + 1].rstrip() if i + 1 < len(lines) else ""
            current_name = next_line.split("(")[0].replace("    def ", "")
        else:
            current_start = i
            current_name = stripped.split("(")[0].replace("    def ", "")

if current_name is not None:
    method_ranges.append((current_name, current_start, len(lines)))

name_to_range = {name: (start, end) for name, start, end in method_ranges}

GROUPS = {
    "base": [
        "_with_debug_snapshot",
        "_dismiss_any_open_modal",
        "_first_visible_locator",
        "_wait_for_enabled_locator",
        "_is_enabled_locator",
        "_locator_top",
        "_clear_search_inputs",
        "_write_keyword",
        "_is_editable_input",
        "_soft_wait_for_results",
        "_wait_for_loading_cycle",
        "_is_loading",
    ],
    "controls": [
        "_detect_search_controls",
        "_find_search_button",
        "_find_search_input_near_button",
        "_find_candidate_search_inputs",
        "_find_primary_input_in_search_container",
        "_find_primary_search_input",
    ],
    "executor": [
        "open_search_page",
        "search",
        "_execute_search",
        "_submit_search",
        "_wait_for_results",
        "_apply_search_execution_options",
        "_click_text_control",
        "_page_looks_like_result_list",
        "_page_looks_empty",
    ],
    "position_filter": [
        "_apply_position_name_filter",
        "_find_position_name_input",
        "_find_position_name_confirm_button",
    ],
    "filters": [
        "apply_filters",
        "_apply_filters_on_page",
        "_filters_need_more_conditions",
        "_ensure_more_filter_conditions",
        "_apply_filter_with_retries",
        "_apply_one_filter",
        "_field_container",
        "_apply_tag_filter",
        "_apply_dropdown_filter",
        "_apply_range_filter",
        "_apply_city_filter",
        "_apply_autocomplete_filter",
        "_apply_single_city_filter",
        "_normalize_tag_filter_value",
        "_normalize_dropdown_filter_value",
        "_normalize_range_filter_value",
        "_normalize_filter_title_text",
        "_extract_tag_texts",
        "_focus_dropdown_input",
        "_open_dropdown_options",
        "_select_dropdown_option",
        "_select_dropdown_option_by_keyboard",
        "_resolve_filter_locator",
        "_fill_filter_input",
        "_wait_for_filter_apply",
        "_resolve_city_modal",
        "_wait_for_city_modal_closed",
        "_select_city_in_modal",
        "_click_city_option_in_modal",
        "_click_city_modal_confirm",
        "_resolve_city_modal_confirm_button",
    ],
    "pagination": [
        "go_to_next_result_page",
        "_go_to_next_result_page_locked",
        "_wait_for_page_change",
        "_find_next_page_control",
        "_get_current_page_number",
        "_click_page_number",
        "_navigate_to_page_via_url",
        "_is_disabled_pagination",
    ],
    "extraction": [
        "extract_candidates_from_page",
        "extract_current_page_candidates",
        "_extract_candidates_with_dom_fallback",
        "_clean_candidate_lines",
        "_split_company_title",
        "_looks_like_title",
        "_looks_like_city",
        "_locate_result_cards",
        "_extract_profile_url",
    ],
    "detail": [
        "ensure_result_page",
        "open_candidate_detail",
        "close_detail_page",
        "_is_detail_page_url",
        "_ensure_absolute_url",
    ],
    "remaining": [
        "__init__",
        "_fill_search_input",
    ],
}

mixin_header = '''"""Auto-generated mixin for LiepinSearchService refactoring."""

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

'''

mixin_dir = "liepin_agent/core/search"

for group_name, method_names in GROUPS.items():
    filepath = os.path.join(mixin_dir, f"_{group_name}_mixin.py")
    content = [mixin_header]
    content.append(f"class _{group_name.title().replace('_', '')}Mixin:\n")
    desc = group_name.replace('_', ' ')
    content.append(f'    \"\"\"Mixin providing {desc} functionality.\"\"\"\n')
    for name in method_names:
        if name not in name_to_range:
            print(f"  SKIP {name} (not found)")
            continue
        start, end = name_to_range[name]
        method_lines = lines[start:end]
        for ml in method_lines:
            content.append(ml + "\n" if not ml.endswith("\n") else ml)
        content.append("\n")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("".join(content))
    print(f"Wrote {filepath} ({len(method_names)} methods)")

print("Done regenerating mixins.")
