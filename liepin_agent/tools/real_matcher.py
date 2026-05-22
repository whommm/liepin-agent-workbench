"""Real LLM-backed candidate matcher."""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from ..core.config import ConfigManager
from ..domain.models import MatchResult
from .llm_client import LLMClient


MATCH_SYSTEM_PROMPT = """你是资深猎头顾问，判断候选人与岗位的匹配度。

## 核心原则
1. 抓重点：匹配条件里的核心技能是硬门槛
2. 合理推断：
   - 简历写"电机结构设计" → 可推断会用 CAD/SolidWorks
   - 简历写"无刷电机驱动开发" → 可推断懂 FOC/SVPWM
   - 简历写"美的/格力电机研发" → 可推断有小家电背景
3. 看近期：近 3 年经验权重更高
4. 不瞎编：推断要有依据，不确定的标注出来

## 档位
A = 核心技能明确 + 近期相关
B = 核心技能有但非近期，或行业有偏差但可迁移
C = 有相关背景但核心技能不明确，需沟通确认
D = 不相关或明显不符

只输出 JSON，不要 Markdown。字段：
tier: A/B/C/D
summary: 一句话：为什么给这个档位
evidence: 简历中的关键证据
inferred: 合理推断的技能（如有）
risks: 风险点
questions: ["电话要确认的问题"]
confidence: high/medium/low
"""


class RealMatchService:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    @classmethod
    def from_config(cls, config_manager: Optional[ConfigManager] = None) -> "RealMatchService":
        manager = config_manager or ConfigManager()
        config = manager.config
        # Backend LLM config: fallback to default if any field is empty
        backend_url = config.backend_api_base_url or config.api_base_url
        backend_key = config.backend_api_key or config.api_key
        backend_model = config.backend_model_name or config.model_name
        return cls(
            LLMClient(
                api_base_url=backend_url,
                api_key=backend_key,
                model_name=backend_model,
                timeout=config.timeout,
                provider=config.backend_llm_provider or "openai",
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
A = 核心技能明确 + 近期相关
B = 核心技能有但非近期，或行业有偏差但可迁移
C = 有相关背景但核心技能不明确，需沟通确认
D = 不相关或明显不符

要求：
1. 判断必须围绕岗位匹配标准中的核心要求。
2. evidence 尽量引用简历原文证据。
3. 合理推断要有依据，不确定的放入 questions。
4. A/B/C/D 只是标签，核心是证据、推断、风险和待确认问题。
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
