"""Search hypothesis portfolio, coverage metrics, and adaptive selection."""

from __future__ import annotations

import math
import uuid
from typing import Any, Dict, List

from ...domain.models import SearchPlan
from ._base_mixin import from_json, now_text, to_json


class _SearchIntelligenceMixin:
    def ensure_search_hypotheses(
        self, session_id: str, criteria: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        criteria_version_id = str(criteria.get("criteria_version_id") or "")
        existing = self.list_search_hypotheses(session_id, criteria_version_id)
        if existing:
            return existing
        personas = list(criteria.get("personas") or [])
        if not personas:
            return []
        seen = set()
        with self.connect() as connection:
            historical_rows = connection.execute(
                """
                SELECT id, query, position_filter, raw_count, detail_fetch_count,
                       ab_count, round_digest_json
                FROM search_rounds WHERE session_id = ?
                ORDER BY round_index
                """,
                (session_id,),
            ).fetchall()
            history = {}
            for row in historical_rows:
                digest = from_json(row["round_digest_json"], {}) or {}
                signature = (
                    str(row["query"] or "").strip().casefold(),
                    str(row["position_filter"] or "").strip().casefold(),
                )
                history[signature] = {
                    "round_id": row["id"],
                    "raw_count": int(row["raw_count"] or 0),
                    "new_count": int(digest.get("new_count") or 0),
                    "detail_count": int(row["detail_fetch_count"] or 0),
                    "relevant_count": int(row["ab_count"] or 0),
                    "page_count": int(digest.get("page_count") or 0),
                    "duplicate_rate": float(digest.get("duplicate_rate") or 0),
                }
            for persona in personas:
                skills = [str(item).strip() for item in persona.get("skills") or [] if str(item).strip()]
                titles = [str(item).strip() for item in persona.get("titles") or [] if str(item).strip()]
                companies = [str(item).strip() for item in persona.get("company_patterns") or [] if str(item).strip()]
                variants = []
                if skills:
                    variants.append(("core_background", " ".join(skills[:2]), titles[0] if titles else ""))
                    variants.extend(("skill", skill, titles[0] if titles else "") for skill in skills[:3])
                if titles:
                    variants.append(("title", titles[0], ""))
                if companies and skills:
                    variants.append(("target_company", "{} {}".format(companies[0], skills[0]), ""))
                for hypothesis_type, query, position_filter in variants:
                    query = " ".join(query.split()[:3]).strip()
                    signature = (query.casefold(), position_filter.casefold())
                    if not query or signature in seen:
                        continue
                    seen.add(signature)
                    previous = history.get(signature) or {}
                    initial_status = "completed" if previous else "pending"
                    hypothesis_id = uuid.uuid4().hex
                    filters = {"active_days": 30}
                    cities = [str(item) for item in criteria.get("city_scope") or [] if str(item)]
                    if cities:
                        filters["city"] = cities
                    connection.execute(
                        """
                        INSERT INTO search_hypotheses (
                            id, session_id, criteria_version_id, persona_id,
                            hypothesis_type, title, query, position_filter,
                            filters_json, expected_signals_json, status, priority,
                            attempt_count, page_count, raw_count, new_count,
                            detail_count, relevant_count, duplicate_rate,
                            last_round_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            hypothesis_id,
                            session_id,
                            criteria_version_id,
                            str(persona.get("id") or ""),
                            hypothesis_type,
                            "{}：{}".format(persona.get("name") or "人才原型", query),
                            query,
                            position_filter,
                            to_json(filters),
                            to_json(skills[:8]),
                            initial_status,
                            float(persona.get("priority") or 0.5),
                            1 if previous else 0,
                            previous.get("page_count") or 0,
                            previous.get("raw_count") or 0,
                            previous.get("new_count") or 0,
                            previous.get("detail_count") or 0,
                            previous.get("relevant_count") or 0,
                            previous.get("duplicate_rate") or 0,
                            previous.get("round_id") or "",
                            now_text(),
                            now_text(),
                        ),
                    )
        return self.list_search_hypotheses(session_id, criteria_version_id)

    def list_search_hypotheses(
        self, session_id: str, criteria_version_id: str = ""
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [session_id]
        where = "session_id = ?"
        if criteria_version_id:
            where += " AND criteria_version_id = ?"
            params.append(criteria_version_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM search_hypotheses WHERE {} ORDER BY priority DESC, created_at".format(where),
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["filters"] = from_json(item.get("filters_json"), {})
            item["expected_signals"] = from_json(item.get("expected_signals_json"), [])
            item["yield_rate"] = round(
                int(item.get("relevant_count") or 0) / max(1, int(item.get("detail_count") or 0)),
                4,
            )
            result.append(item)
        return result

    def select_search_hypothesis_plan(
        self, session_id: str, criteria_version_id: str
    ) -> SearchPlan | None:
        hypotheses = [
            item
            for item in self.list_search_hypotheses(session_id, criteria_version_id)
            if item.get("status") not in {"paused", "disabled"}
            and int(item.get("attempt_count") or 0) < 3
        ]
        if not hypotheses:
            return None
        total_attempts = sum(int(item.get("attempt_count") or 0) for item in hypotheses)

        def score(item: Dict[str, Any]) -> float:
            attempts = int(item.get("attempt_count") or 0)
            if attempts == 0:
                return 10.0 + float(item.get("priority") or 0)
            yield_rate = int(item.get("relevant_count") or 0) / max(1, int(item.get("detail_count") or 0))
            exploration = math.sqrt(2 * math.log(total_attempts + 1.0) / attempts)
            return (
                float(item.get("priority") or 0)
                + yield_rate * 0.7
                + exploration * 0.25
                - float(item.get("duplicate_rate") or 0) * 0.35
            )

        selected = max(hypotheses, key=score)
        with self.connect() as connection:
            connection.execute(
                "UPDATE search_hypotheses SET status = 'active', updated_at = ? WHERE id = ?",
                (now_text(), selected["id"]),
            )
        return SearchPlan(
            query=str(selected.get("query") or ""),
            position_filter=str(selected.get("position_filter") or ""),
            filters=dict(selected.get("filters") or {}),
            intent="按人才原型覆盖计划验证：{}".format(selected.get("title") or ""),
            expected_signal=list(selected.get("expected_signals") or []),
            risk="组合池探索可能包含可迁移背景，需要用详情证据验证",
            search_hypothesis_type=str(selected.get("hypothesis_type") or "core_background"),
            search_hypothesis_text=str(selected.get("title") or ""),
            search_hypothesis_id=str(selected.get("id") or ""),
        )

    def record_search_hypothesis_result(
        self,
        hypothesis_id: str,
        *,
        round_id: str,
        page_count: int,
        raw_count: int,
        new_count: int,
        detail_count: int,
        relevant_count: int,
        duplicate_rate: float,
    ) -> None:
        if not hypothesis_id:
            return
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE search_hypotheses
                SET status = 'completed', attempt_count = attempt_count + 1,
                    page_count = page_count + ?, raw_count = raw_count + ?,
                    new_count = new_count + ?, detail_count = detail_count + ?,
                    relevant_count = relevant_count + ?, duplicate_rate = ?,
                    last_round_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    int(page_count or 0),
                    int(raw_count or 0),
                    int(new_count or 0),
                    int(detail_count or 0),
                    int(relevant_count or 0),
                    float(duplicate_rate or 0),
                    round_id,
                    now_text(),
                    hypothesis_id,
                ),
            )

    def update_search_hypothesis(
        self, hypothesis_id: str, *, status: str | None = None, priority: float | None = None
    ) -> bool:
        fields = []
        params: List[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if priority is not None:
            fields.append("priority = ?")
            params.append(max(0.0, min(1.0, float(priority))))
        if not fields:
            return False
        fields.append("updated_at = ?")
        params.append(now_text())
        params.append(hypothesis_id)
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE search_hypotheses SET {} WHERE id = ?".format(", ".join(fields)),
                params,
            )
        return cursor.rowcount > 0

    def search_coverage_summary(self, session_id: str) -> Dict[str, Any]:
        items = self.list_search_hypotheses(session_id)
        return {
            "session_id": session_id,
            "total": len(items),
            "pending": sum(1 for item in items if item.get("status") == "pending"),
            "completed": sum(1 for item in items if int(item.get("attempt_count") or 0) > 0),
            "paused": sum(1 for item in items if item.get("status") == "paused"),
            "raw_count": sum(int(item.get("raw_count") or 0) for item in items),
            "new_count": sum(int(item.get("new_count") or 0) for item in items),
            "relevant_count": sum(int(item.get("relevant_count") or 0) for item in items),
            "hypotheses": items,
        }
