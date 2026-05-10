#!/usr/bin/env python3
"""Refactor script to split liepin_search_service.py into mixins."""

import os

src_path = "liepin_agent/core/liepin_search_service.py"
with open(src_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# 1. Find class definition
class_start = None
for i, line in enumerate(lines):
    if "class LiepinSearchService:" in line:
        class_start = i
        break

module_header = "".join(lines[:class_start])

# 2. Parse method ranges
method_ranges = []
current_name = None
current_start = None

for i in range(class_start + 1, len(lines)):
    line = lines[i]
    stripped = line.rstrip()
    # Detect end of class (next top-level def/class)
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
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            current_name = next_line.split("(")[0].replace("def ", "")
        else:
            current_start = i
            current_name = stripped.split("(")[0].replace("    def ", "")

if current_name is not None:
    method_ranges.append((current_name, current_start, len(lines)))

first_method_start = method_ranges[0][1] if method_ranges else len(lines)
class_vars = "".join(lines[class_start + 1 : first_method_start])

# 3. Define mixin groups
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
}

# Build lookup: method_name -> group
method_to_group = {}
for group, names in GROUPS.items():
    for name in names:
        method_to_group[name] = group

# 4. Group method ranges
name_to_range = {name: (start, end) for name, start, end in method_ranges}

# Methods not in any group -> "remaining"
remaining = [name for name, _, _ in method_ranges if name not in method_to_group]
if remaining:
    print("WARNING: ungrouped methods:", remaining)

# 5. Build mixin contents
mixin_dir = "liepin_agent/core/search"
os.makedirs(mixin_dir, exist_ok=True)

# Common mixin header
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

'''

def write_mixin(group_name, method_names):
    if not method_names:
        return
    filepath = os.path.join(mixin_dir, f"_{group_name}_mixin.py")
    content = [mixin_header]
    # Add class definition
    content.append(f"class _{group_name.title().replace('_', '')}Mixin:\n")
    content.append('    """Mixin providing {} functionality."""\n'.format(group_name.replace("_", " ")))
    for name in method_names:
        if name not in name_to_range:
            print(f"  SKIP {name} (not found)")
            continue
        start, end = name_to_range[name]
        method_lines = lines[start:end]
        # Unindent by 4 spaces
        for ml in method_lines:
            if ml.startswith("        ") or ml.startswith("    "):
                content.append(ml[4:])
            else:
                content.append(ml)
        content.append("\n")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("".join(content))
    print(f"Wrote {filepath} ({len(method_names)} methods)")

for group, names in GROUPS.items():
    write_mixin(group, names)

# 6. Write remaining methods to a separate file if any
if remaining:
    write_mixin("remaining", remaining)

# 7. Build the new liepin_search_service.py
new_service_lines = []
new_service_lines.append('"""Liepin search execution and result list extraction.\n')
new_service_lines.append('\nThis module provides a facade class that composes functionality from\n')
new_service_lines.append('specialized mixins in the search/ package.\n')
new_service_lines.append('"""\n')
new_service_lines.append('\n')
new_service_lines.append('from __future__ import annotations\n')
new_service_lines.append('\n')
new_service_lines.append('from .search._base_mixin import _BaseMixin\n')
new_service_lines.append('from .search._controls_mixin import _ControlsMixin\n')
new_service_lines.append('from .search._executor_mixin import _ExecutorMixin\n')
new_service_lines.append('from .search._position_filter_mixin import _PositionFilterMixin\n')
new_service_lines.append('from .search._filters_mixin import _FiltersMixin\n')
new_service_lines.append('from .search._pagination_mixin import _PaginationMixin\n')
new_service_lines.append('from .search._extraction_mixin import _ExtractionMixin\n')
new_service_lines.append('from .search._detail_mixin import _DetailMixin\n')
new_service_lines.append('\n')

# Keep models and exceptions here (or move later)
new_service_lines.append('from .search._models import (\n')
new_service_lines.append('    LiepinSearchCandidate,\n')
new_service_lines.append('    LiepinSearchControls,\n')
new_service_lines.append('    LiepinFilterFieldSpec,\n')
new_service_lines.append('    LiepinSearchError,\n')
new_service_lines.append('    LiepinSearchPageChangedError,\n')
new_service_lines.append('    LiepinSearchNoResultsError,\n')
new_service_lines.append(')\n')
new_service_lines.append('\n')

# Class definition with mixins
new_service_lines.append('class LiepinSearchService(\n')
new_service_lines.append('    _BaseMixin,\n')
new_service_lines.append('    _ControlsMixin,\n')
new_service_lines.append('    _ExecutorMixin,\n')
new_service_lines.append('    _PositionFilterMixin,\n')
new_service_lines.append('    _FiltersMixin,\n')
new_service_lines.append('    _PaginationMixin,\n')
new_service_lines.append('    _ExtractionMixin,\n')
new_service_lines.append('    _DetailMixin,\n')
new_service_lines.append('):\n')
new_service_lines.append('    """Execute keyword searches on Liepin and parse result cards."""\n')
new_service_lines.append('\n')
new_service_lines.append(class_vars)
new_service_lines.append('\n')

# Add only __init__ and any remaining public methods
remaining_methods = [name for name, _, _ in method_ranges if name not in method_to_group]
for name in remaining_methods:
    start, end = name_to_range[name]
    method_lines = lines[start:end]
    for ml in method_lines:
        new_service_lines.append(ml)
    new_service_lines.append("\n")

# Also add __init__ if it was in a group (it shouldn't be)
if "__init__" in name_to_range and "__init__" not in method_to_group:
    start, end = name_to_range["__init__"]
    method_lines = lines[start:end]
    for ml in method_lines:
        new_service_lines.append(ml)
    new_service_lines.append("\n")

with open(src_path, "w", encoding="utf-8") as f:
    f.write("".join(new_service_lines))

print(f"Rewrote {src_path}")
print("Done.")
