"""Persistence for structured job criteria and sourcing personas."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List

from ._base_mixin import from_json, now_text, to_json


class _ProfileMixin:
    def replace_job_profile(
        self,
        criteria_version_id: str,
        criteria_items: Iterable[Dict[str, Any]],
        personas: Iterable[Dict[str, Any]],
    ) -> None:
        with self.connect() as connection:
            version = connection.execute(
                "SELECT session_id FROM match_criteria_versions WHERE id = ?",
                (criteria_version_id,),
            ).fetchone()
            if not version:
                raise ValueError("criteria version not found")
            session_id = version["session_id"]
            connection.execute(
                "DELETE FROM job_criteria_items WHERE criteria_version_id = ?",
                (criteria_version_id,),
            )
            connection.execute(
                "DELETE FROM candidate_personas WHERE criteria_version_id = ?",
                (criteria_version_id,),
            )
            for ordinal, item in enumerate(criteria_items or []):
                text = str(item.get("criterion_text") or item.get("criterion") or "").strip()
                if not text:
                    continue
                connection.execute(
                    """
                    INSERT INTO job_criteria_items (
                        id, session_id, criteria_version_id, ordinal,
                        criterion_type, criterion_text, weight, alternatives_json,
                        search_aliases_json, time_window_years, evidence_policy,
                        observability, source_quote, confidence, human_confirmed,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        session_id,
                        criteria_version_id,
                        ordinal,
                        str(item.get("criterion_type") or item.get("type") or "preferred"),
                        text,
                        float(item.get("weight") or 0.5),
                        to_json(item.get("alternatives") or item.get("acceptable_alternatives") or []),
                        to_json(item.get("search_aliases") or []),
                        item.get("time_window_years"),
                        str(item.get("evidence_policy") or ""),
                        str(item.get("observability") or "resume"),
                        str(item.get("source_quote") or ""),
                        float(item.get("confidence") or 0.5),
                        1 if item.get("human_confirmed") else 0,
                        now_text(),
                    ),
                )
            for persona in personas or []:
                name = str(persona.get("name") or "").strip()
                if not name:
                    continue
                connection.execute(
                    """
                    INSERT INTO candidate_personas (
                        id, session_id, criteria_version_id, name, description,
                        titles_json, skills_json, company_patterns_json,
                        transfer_rationale, priority, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        session_id,
                        criteria_version_id,
                        name,
                        str(persona.get("description") or ""),
                        to_json(persona.get("titles") or []),
                        to_json(persona.get("skills") or []),
                        to_json(persona.get("company_patterns") or []),
                        str(persona.get("transfer_rationale") or ""),
                        float(persona.get("priority") or 0.5),
                        str(persona.get("status") or "active"),
                        now_text(),
                    ),
                )

    def list_job_criteria(self, criteria_version_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_criteria_items
                WHERE criteria_version_id = ? ORDER BY ordinal, rowid
                """,
                (criteria_version_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["alternatives"] = from_json(item.get("alternatives_json"), [])
            item["search_aliases"] = from_json(item.get("search_aliases_json"), [])
            item["human_confirmed"] = bool(item.get("human_confirmed"))
            result.append(item)
        return result

    def list_candidate_personas(self, criteria_version_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM candidate_personas
                WHERE criteria_version_id = ? ORDER BY priority DESC, rowid
                """,
                (criteria_version_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["titles"] = from_json(item.get("titles_json"), [])
            item["skills"] = from_json(item.get("skills_json"), [])
            item["company_patterns"] = from_json(item.get("company_patterns_json"), [])
            result.append(item)
        return result

    def clone_job_profile(self, source_version_id: str, target_version_id: str) -> None:
        self.replace_job_profile(
            target_version_id,
            self.list_job_criteria(source_version_id),
            self.list_candidate_personas(source_version_id),
        )
