"""Real LLM-backed candidate matcher."""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from ..core.config import ConfigManager
from ..domain.models import MatchResult
from .llm_client import LLMClient


MATCH_SYSTEM_PROMPT = """你是一位资深猎头顾问。你需要基于岗位匹配标准和候选人简历，输出严格 JSON。
只输出 JSON，不要 Markdown。字段：
tier: A/B/C/D
core_met_count: 数字
core_total: 数字
dealbreaker_hit: true/false
summary: 一句话概括匹配点
risks: 主要风险
recommendation: 推进建议
detail: 结构化中文说明
matched_evidence: 数组，每项包含 criterion/evidence/strength
missing_or_unclear: 数组，列出缺口或未知项
questions_to_verify: 数组，列出电话中应确认的问题
confidence: high/medium/low
"""


class RealMatchService:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    @classmethod
    def from_config(cls, config_manager: Optional[ConfigManager] = None) -> "RealMatchService":
        manager = config_manager or ConfigManager()
        config = manager.config
        return cls(
            LLMClient(
                api_base_url=config.api_base_url,
                api_key=config.api_key,
                model_name=config.model_name,
                timeout=config.timeout,
            )
        )

    def match_candidate(
        self,
        session_id: str,
        round_id: str,
        candidate_id: str,
        resume_text: str,
        criteria: Dict[str, object],
    ) -> MatchResult:
        prompt = self._build_prompt(criteria, resume_text)
        raw = self.llm_client.chat(prompt, system_message=MATCH_SYSTEM_PROMPT)
        payload = self._parse_json(raw)
        tier = self._normalize_tier(payload.get("tier"))
        return MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier=tier,
            core_met_count=int(payload.get("core_met_count") or 0),
            core_total=int(payload.get("core_total") or 0),
            dealbreaker_hit=bool(payload.get("dealbreaker_hit")),
            summary=str(payload.get("summary") or ""),
            risks=str(payload.get("risks") or ""),
            recommendation=str(payload.get("recommendation") or ""),
            detail=str(payload.get("detail") or raw),
            raw_response=raw,
            criteria_version_id=str(criteria.get("criteria_version_id") or ""),
            matched_evidence=self._list_of_dicts(payload.get("matched_evidence")),
            missing_or_unclear=self._string_list(payload.get("missing_or_unclear")),
            questions_to_verify=self._string_list(payload.get("questions_to_verify")),
            confidence=str(payload.get("confidence") or "medium"),
        )

    @staticmethod
    def _build_prompt(criteria: Dict[str, object], resume_text: str) -> str:
        return """【岗位匹配标准】
{criteria}

【候选人简历】
{resume}

请判断候选人与岗位的匹配档位：
A = 强匹配，建议优先推进
B = 基本匹配，建议沟通
C = 有局部相关但风险明显，备选
D = 不匹配或命中硬伤

要求：
1. 判断必须围绕岗位匹配标准中的关键词和岗位要求描述。
2. matched_evidence 尽量引用简历原文证据。
3. 不确定的信息放入 missing_or_unclear 或 questions_to_verify，不要脑补。
4. A/B/C/D 只是标签，核心是证据、缺口、风险和待确认问题。
""".format(
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
            resume=resume_text or "",
        )

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, object]:
        text = (raw or "").strip()
        block = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if block:
            text = block.group(1)
        elif not (text.startswith("{") and text.endswith("}")):
            match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
            if match:
                text = match.group(1)
        try:
            data = json.loads(text)
        except ValueError:
            data = {
                "tier": "C",
                "summary": "模型未返回标准 JSON，需人工复核。",
                "risks": "解析失败",
                "recommendation": "人工复核",
                "detail": raw,
                "matched_evidence": [],
                "missing_or_unclear": ["模型输出无法解析"],
                "questions_to_verify": ["请人工复核该候选人与岗位要求的匹配度"],
                "confidence": "low",
            }
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _normalize_tier(value: object) -> str:
        tier = str(value or "").strip().upper()
        return tier if tier in {"A", "B", "C", "D"} else "C"

    @staticmethod
    def _string_list(value: object) -> list:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _list_of_dicts(value: object) -> list:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            if isinstance(item, dict):
                result.append(
                    {
                        "criterion": str(item.get("criterion") or ""),
                        "evidence": str(item.get("evidence") or ""),
                        "strength": str(item.get("strength") or ""),
                    }
                )
        return result
