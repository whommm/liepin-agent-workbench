"""Normalization helpers for versioned job criteria and sourcing personas."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple


CRITERION_TYPES = {"must", "preferred", "dealbreaker", "verify"}
OBSERVABILITY_TYPES = {"card", "resume", "conversation", "background_check"}


def normalize_job_profile(payload: Dict[str, Any] | None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    data = dict(payload or {})
    items = _normalize_items(data.get("criteria_items") or data.get("criteria") or [])
    if not items:
        items = _derive_items(data)
    personas = _normalize_personas(data.get("personas") or data.get("candidate_personas") or [])
    if not personas:
        personas = _derive_personas(data, items)
    return items[:20], personas[:8]


def _normalize_items(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if isinstance(value, str):
            value = {"criterion": value}
        if not isinstance(value, dict):
            continue
        text = str(
            value.get("criterion")
            or value.get("criterion_text")
            or value.get("text")
            or ""
        ).strip()
        if not text:
            continue
        kind = str(value.get("type") or value.get("criterion_type") or "preferred").lower()
        if kind not in CRITERION_TYPES:
            kind = "preferred"
        observability = str(value.get("observability") or "resume").strip().lower()
        if observability not in OBSERVABILITY_TYPES:
            observability = "resume"
        result.append(
            {
                "criterion_type": kind,
                "criterion_text": text,
                "weight": _clamp(value.get("weight"), 0.9 if kind == "must" else 0.6),
                "alternatives": _strings(value.get("acceptable_alternatives") or value.get("alternatives")),
                "search_aliases": _strings(value.get("search_aliases") or value.get("aliases")),
                "time_window_years": _positive_int(value.get("time_window_years")),
                "evidence_policy": str(value.get("evidence_policy") or "需要简历中的直接事实证据").strip(),
                "observability": observability,
                "source_quote": str(value.get("source_quote") or "").strip(),
                "confidence": _clamp(value.get("confidence"), 0.7),
                "human_confirmed": bool(value.get("human_confirmed", False)),
            }
        )
    return result


def _derive_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    requirements = str(data.get("requirements_text") or data.get("core_requirement") or "").strip()
    aliases = _strings(data.get("core_terms")) or _split_terms(data.get("keywords_text"))
    if requirements:
        result.append(
            {
                "criterion_type": "must",
                "criterion_text": requirements,
                "weight": 0.9,
                "alternatives": [],
                "search_aliases": aliases[:8],
                "time_window_years": None,
                "evidence_policy": "需要简历中的直接事实证据；缺失只能标记为未知",
                "observability": "resume",
                "source_quote": requirements,
                "confidence": 0.65,
                "human_confirmed": False,
            }
        )
    for text in _strings(data.get("hard_requirements")):
        if requirements and text in requirements:
            continue
        result.append(
            {
                "criterion_type": "must",
                "criterion_text": text,
                "weight": 0.85,
                "alternatives": [],
                "search_aliases": [],
                "time_window_years": None,
                "evidence_policy": "需要简历中的明确字段或原文证据",
                "observability": "resume",
                "source_quote": text,
                "confidence": 0.7,
                "human_confirmed": False,
            }
        )
    city = str(data.get("city_requirement") or "").strip()
    if city and city != "无明确要求":
        result.append(
            {
                "criterion_type": "verify",
                "criterion_text": "工作地点要求：{}".format(city),
                "weight": 0.5,
                "alternatives": [],
                "search_aliases": _strings(data.get("city_scope")),
                "time_window_years": None,
                "evidence_policy": "核对当前城市和期望工作地",
                "observability": "card",
                "source_quote": city,
                "confidence": 0.8,
                "human_confirmed": False,
            }
        )
    return result


def _normalize_personas(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    result = []
    for index, value in enumerate(values):
        if isinstance(value, str):
            value = {"name": value, "description": value}
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "人才原型{}".format(index + 1)).strip()
        result.append(
            {
                "name": name,
                "description": str(value.get("description") or "").strip(),
                "titles": _strings(value.get("titles")),
                "skills": _strings(value.get("skills")),
                "company_patterns": _strings(value.get("company_patterns") or value.get("companies")),
                "transfer_rationale": str(value.get("transfer_rationale") or "").strip(),
                "priority": _clamp(value.get("priority"), max(0.3, 0.9 - index * 0.15)),
                "status": str(value.get("status") or "active").strip(),
            }
        )
    return result


def _derive_personas(data: Dict[str, Any], items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    titles = _strings(data.get("position_filter"))
    skills = []
    for item in items:
        skills.extend(item.get("search_aliases") or [])
    skills = list(dict.fromkeys(skills))[:10]
    companies = _strings(data.get("target_companies"))
    direction = str(data.get("search_direction") or data.get("selected_direction") or "").strip()
    direct = {
        "name": "直接对口",
        "description": direction or "职位、核心技能和行业背景直接匹配",
        "titles": titles,
        "skills": skills,
        "company_patterns": companies,
        "transfer_rationale": "直接验证岗位核心要求",
        "priority": 0.9,
        "status": "active",
    }
    transferable = {
        "name": "相邻可迁移",
        "description": "核心能力相近，但职位名称、行业或业务场景可能不同",
        "titles": titles,
        "skills": skills[1:] if len(skills) > 1 else skills,
        "company_patterns": [],
        "transfer_rationale": "扩大召回并发现可迁移背景",
        "priority": 0.55,
        "status": "active",
    }
    return [direct, transferable]


def _strings(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return _split_terms(value)
    if not isinstance(value, Iterable) or isinstance(value, dict):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _split_terms(value: Any) -> List[str]:
    return list(
        dict.fromkeys(
            item.strip(" -\t")
            for item in re.split(r"[\n,，、;；]+", str(value or ""))
            if item.strip(" -\t")
        )
    )


def _clamp(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
