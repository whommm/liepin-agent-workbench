"""Generate an offline quality report from recruiter feedback.

Usage:
    uv run python scripts/evaluate_quality.py --db liepin_agent_workbench.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from liepin_agent.services.candidate_ranking import CandidateRankingService
from liepin_agent.storage.sqlite_store import SQLiteStore


def build_report(db_path: str | Path, refresh: bool = True) -> dict:
    store = SQLiteStore(str(db_path))
    ranking = CandidateRankingService(store)
    sessions = []
    for session in store.list_sessions():
        session_id = str(session.get("id") or "")
        if refresh:
            ranking.refresh_session(session_id)
        dashboard = ranking.quality_dashboard(session_id)
        dashboard["title"] = str(session.get("title") or "")
        sessions.append(dashboard)
    labeled = sum(
        int(item.get("feedback", {}).get("labeled_candidate_count") or 0)
        for item in sessions
    )
    comparable = sum(
        int(item.get("feedback", {}).get("comparable_count") or 0)
        for item in sessions
    )
    return {
        "database": str(Path(db_path).resolve()),
        "session_count": len(sessions),
        "labeled_candidate_count": labeled,
        "comparable_candidate_count": comparable,
        "sessions": sessions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate sourcing and ranking quality")
    parser.add_argument("--db", default="liepin_agent_workbench.db")
    parser.add_argument("--output", default="")
    parser.add_argument("--no-refresh", action="store_true")
    args = parser.parse_args()
    report = build_report(args.db, refresh=not args.no_refresh)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
