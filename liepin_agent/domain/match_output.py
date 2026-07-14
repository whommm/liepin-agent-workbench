"""Validated contract for LLM candidate-match output."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Tier = Literal["A", "B", "C", "D"]
Confidence = Literal["high", "medium", "low"]
EvidenceStrength = Literal["strong", "medium", "weak"]
EvidenceSource = Literal["direct", "inferred"]


class MatchEvidence(BaseModel):
    """One requirement and the resume evidence supporting it."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    criterion: str = ""
    evidence: str = Field(min_length=1)
    strength: EvidenceStrength = "medium"
    source_type: EvidenceSource = "direct"

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"evidence": value}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data["criterion"] = (
            data.get("criterion")
            or data.get("requirement")
            or data.get("name")
            or ""
        )
        data["evidence"] = (
            data.get("evidence")
            or data.get("quote")
            or data.get("text")
            or data.get("value")
            or ""
        )
        return data

    @field_validator("strength", mode="before")
    @classmethod
    def normalize_strength(cls, value: Any) -> Any:
        if value in (None, ""):
            return "medium"
        normalized = str(value).strip().lower()
        aliases = {
            "high": "strong",
            "strong": "strong",
            "高": "strong",
            "medium": "medium",
            "moderate": "medium",
            "中": "medium",
            "low": "weak",
            "weak": "weak",
            "低": "weak",
        }
        if normalized not in aliases:
            raise ValueError("evidence strength must be strong/medium/weak")
        return aliases[normalized]

    @field_validator("source_type", mode="before")
    @classmethod
    def normalize_source_type(cls, value: Any) -> Any:
        if value in (None, ""):
            return "direct"
        normalized = str(value).strip().lower()
        aliases = {
            "direct": "direct",
            "quoted": "direct",
            "resume": "direct",
            "原文": "direct",
            "inferred": "inferred",
            "inference": "inferred",
            "推断": "inferred",
        }
        if normalized not in aliases:
            raise ValueError("evidence source_type must be direct/inferred")
        return aliases[normalized]


class MatchOutput(BaseModel):
    """The sole accepted structured response from the matching model.

    The before-validator translates the historical ``evidence``, ``inferred``
    and ``questions`` fields into the canonical contract. Downstream code only
    consumes canonical fields.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    tier: Tier
    summary: str = Field(min_length=1)
    core_met_count: int = Field(default=0, ge=0)
    core_total: int = Field(default=0, ge=0)
    dealbreaker_hit: bool = False
    matched_evidence: List[MatchEvidence] = Field(default_factory=list)
    inferred_evidence: List[MatchEvidence] = Field(default_factory=list)
    missing_or_unclear: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    questions_to_verify: List[str] = Field(default_factory=list)
    recommendation: str = ""
    confidence: Confidence = "medium"
    detail: str = ""

    @model_validator(mode="before")
    @classmethod
    def translate_legacy_contract(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)

        if "matched_evidence" not in data:
            data["matched_evidence"] = data.get("evidence")
        if "inferred_evidence" not in data:
            data["inferred_evidence"] = data.get("inferred")
        if "questions_to_verify" not in data:
            data["questions_to_verify"] = data.get("questions")
        if "missing_or_unclear" not in data:
            data["missing_or_unclear"] = data.get("unknowns", data.get("missing"))

        data["matched_evidence"] = cls._normalize_evidence_collection(
            data.get("matched_evidence"), source_type="direct"
        )
        data["inferred_evidence"] = cls._normalize_evidence_collection(
            data.get("inferred_evidence"), source_type="inferred"
        )
        return data

    @staticmethod
    def _normalize_evidence_collection(value: Any, source_type: str) -> Any:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [{"evidence": value, "source_type": source_type}]
        if isinstance(value, dict):
            evidence_keys = {"criterion", "requirement", "evidence", "quote", "text", "value"}
            if evidence_keys.intersection(value):
                item = dict(value)
                item["source_type"] = source_type
                return [item]
            return [
                {
                    "criterion": str(criterion),
                    "evidence": evidence,
                    "source_type": source_type,
                }
                for criterion, evidence in value.items()
            ]
        if not isinstance(value, list):
            return value

        normalized = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"evidence": item, "source_type": source_type})
            elif isinstance(item, dict):
                entry = dict(item)
                entry["source_type"] = source_type
                normalized.append(entry)
            else:
                normalized.append(item)
        return normalized

    @field_validator("tier", mode="before")
    @classmethod
    def normalize_tier(cls, value: Any) -> Any:
        text = str(value or "").strip().upper()
        match = re.fullmatch(r"(?:TIER\s*[:：-]?\s*)?([ABCD])(?:档|级)?", text)
        if not match:
            raise ValueError("tier must be exactly A, B, C or D")
        return match.group(1)

    @field_validator("dealbreaker_hit", mode="before")
    @classmethod
    def normalize_boolean(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "y", "1", "是", "命中"}:
                return True
            if normalized in {"false", "no", "n", "0", "否", "未命中"}:
                return False
        raise ValueError("dealbreaker_hit must be a boolean")

    @field_validator("core_met_count", "core_total", mode="before")
    @classmethod
    def normalize_count(cls, value: Any) -> Any:
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            raise ValueError("core counts must be integers")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        raise ValueError("core counts must be integers")

    @field_validator(
        "missing_or_unclear", "risks", "questions_to_verify", mode="before"
    )
    @classmethod
    def normalize_string_list(cls, value: Any) -> Any:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            return value
        return [item for item in value if item not in (None, "")]

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> Any:
        if value in (None, ""):
            return "medium"
        normalized = str(value).strip().lower()
        aliases = {
            "high": "high",
            "高": "high",
            "medium": "medium",
            "中": "medium",
            "low": "low",
            "低": "low",
        }
        if normalized not in aliases:
            raise ValueError("confidence must be high/medium/low")
        return aliases[normalized]

    @field_validator("detail", mode="before")
    @classmethod
    def normalize_detail(cls, value: Any) -> Any:
        if value in (None, ""):
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value

    @model_validator(mode="after")
    def validate_business_evidence(self) -> "MatchOutput":
        if self.core_met_count > self.core_total:
            raise ValueError("core_met_count cannot exceed core_total")
        if self.tier in {"A", "B"} and not self.matched_evidence:
            raise ValueError("A/B results require direct resume evidence")
        if self.dealbreaker_hit and self.tier in {"A", "B"}:
            raise ValueError("dealbreaker results cannot receive A/B tier")
        if self.core_total > 0:
            coverage = self.core_met_count / self.core_total
            if self.tier == "A" and coverage < 0.8:
                raise ValueError("A tier requires at least 80% core coverage")
            if self.tier == "B" and coverage < 0.5:
                raise ValueError("B tier requires at least 50% core coverage")
        return self

    def evidence_for_match_result(self) -> List[Dict[str, Any]]:
        """Keep inferred evidence visible while preserving its source label."""

        return [
            item.model_dump()
            for item in [*self.matched_evidence, *self.inferred_evidence]
        ]

    def canonical_json(self) -> str:
        return self.model_dump_json(exclude={"detail"})

    def deterministic_score(self) -> int:
        """Return a stable evidence score; it is a ranking aid, not a hiring verdict."""
        if self.core_total > 0:
            coverage = self.core_met_count / self.core_total
        else:
            coverage = min(1.0, len(self.matched_evidence) / 2)

        strength_weight = {"strong": 1.0, "medium": 0.7, "weak": 0.4}
        evidence_quality = (
            sum(strength_weight[item.strength] for item in self.matched_evidence)
            / len(self.matched_evidence)
            if self.matched_evidence
            else 0.0
        )
        confidence_weight = {"high": 1.0, "medium": 0.9, "low": 0.75}
        score = (
            (coverage * 0.7 + evidence_quality * 0.3)
            * 100
            * confidence_weight[self.confidence]
        )
        score -= min(15, len(self.missing_or_unclear) * 3)
        if self.dealbreaker_hit:
            score = min(score, 39)
        return max(0, min(100, round(score)))
