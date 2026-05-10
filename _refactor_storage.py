#!/usr/bin/env python3
"""Refactor sqlite_store.py into mixins."""

import os

src_path = "liepin_agent/storage/sqlite_store.py"
with open(src_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find class definition
class_start = None
for i, line in enumerate(lines):
    if "class SQLiteStore:" in line:
        class_start = i
        break

module_header = "".join(lines[:class_start])

# Parse method ranges
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
        and not stripped.startswith("    @contextmanager")
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
        "__init__",
        "connect",
        "initialize",
        "_ensure_column",
    ],
    "session": [
        "create_session",
        "list_sessions",
        "recover_interrupted_sessions",
        "get_session",
        "delete_session",
        "update_session_status",
    ],
    "criteria": [
        "save_match_criteria",
        "create_criteria_version",
        "update_criteria_version",
        "confirm_criteria_version",
        "get_latest_criteria_version",
        "get_latest_criteria",
        "_keywords_from_text",
    ],
    "round": [
        "create_round",
        "update_round",
        "list_rounds",
    ],
    "candidate": [
        "save_candidate_summary",
        "update_candidate_profile_url",
        "list_candidates",
        "get_candidates_by_ids",
        "save_candidate_source",
        "list_candidate_sources",
        "update_candidate_status",
        "save_candidate_detail",
        "get_candidate_detail",
    ],
    "match": [
        "save_match_result",
        "list_match_results",
        "count_ab_matches",
        "count_fetched_details",
    ],
    "metrics": [
        "session_efficiency_metrics",
        "search_hypothesis_metrics",
    ],
    "event": [
        "add_event",
        "list_events",
        "save_decision",
    ],
}

mixin_dir = "liepin_agent/storage/repos"
os.makedirs(mixin_dir, exist_ok=True)

mixin_header = '''"""Auto-generated mixin for SQLiteStore refactoring."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ...domain.dedupe import build_candidate_dedupe_key
from ...domain.models import CandidateDetail, CandidateSummary, MatchResult, SearchPlan
from ...domain.states import CandidateStatus, RoundStatus, SessionStatus


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def from_json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default

'''

for group_name, method_names in GROUPS.items():
    filepath = os.path.join(mixin_dir, f"_{group_name}_mixin.py")
    content = [mixin_header]
    content.append(f"class _{group_name.title().replace('_', '')}Mixin:\n")
    content.append(f'    """Mixin providing {group_name.replace(chr(95), chr(32))} repository functionality."""\n')
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

# Rewrite sqlite_store.py as facade
new_lines = []
new_lines.append('"""SQLite persistence for the Agent workbench.\n\n')
new_lines.append('This module provides a facade class that composes functionality from\n')
new_lines.append('specialized mixins in the repos/ package.\n')
new_lines.append('"""\n\n')
new_lines.append('from __future__ import annotations\n\n')
new_lines.append('from .repos._base_mixin import _BaseMixin\n')
new_lines.append('from .repos._session_mixin import _SessionMixin\n')
new_lines.append('from .repos._criteria_mixin import _CriteriaMixin\n')
new_lines.append('from .repos._round_mixin import _RoundMixin\n')
new_lines.append('from .repos._candidate_mixin import _CandidateMixin\n')
new_lines.append('from .repos._match_mixin import _MatchMixin\n')
new_lines.append('from .repos._metrics_mixin import _MetricsMixin\n')
new_lines.append('from .repos._event_mixin import _EventMixin\n')
new_lines.append('\n')
new_lines.append('class SQLiteStore(\n')
new_lines.append('    _BaseMixin,\n')
new_lines.append('    _SessionMixin,\n')
new_lines.append('    _CriteriaMixin,\n')
new_lines.append('    _RoundMixin,\n')
new_lines.append('    _CandidateMixin,\n')
new_lines.append('    _MatchMixin,\n')
new_lines.append('    _MetricsMixin,\n')
new_lines.append('    _EventMixin,\n')
new_lines.append('):\n')
new_lines.append('    """Repository facade around the workbench SQLite database."""\n')
new_lines.append('\n')

with open(src_path, "w", encoding="utf-8") as f:
    f.write("".join(new_lines))

print(f"Rewrote {src_path}")
print("Done.")
