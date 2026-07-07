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

## 薪资匹配（重要）
- **你必须自行从 JD 原文中读取岗位薪资范围，从简历中读取候选人目前/期望薪资，然后判断两者是否匹配。**
- 如果 JD 中有明确薪资范围（如年薪 30-50万、月薪 25-35k 等），候选人期望/目前薪资严重超出该范围的，**不得评为 A 档**
- 候选人薪资明显高于岗位上限的（如岗位 30-50万，候选人目前 80万+或期望 100万），应视为核心风险，至少降一档至 B 或 C
- 候选人薪资明显低于岗位下限的（如岗位 30-50万，候选人目前 15万），除非有明确理由（如转行、地域差异），否则也需标注风险
- 如果 JD 中未提及具体薪资范围（或只有"面议""竞争力薪资"等模糊表述），不做薪资判断，正常评估其他维度

## 地点匹配（重要）
- **你必须自行从简历中读取候选人的当前所在地和期望工作地**（通常在"求职期望""基本信息"部分）
- **如果岗位有明确城市/地点要求，候选人当前城市或期望城市与岗位要求严重不符的，不得评为 A 档**
- 例如：岗位在深圳，候选人当前在北京且期望城市也是北京 → 地点严重不匹配，至少降一档
- 例如：岗位在深圳，候选人当前在广州但期望城市包含深圳 → 地点匹配，不影响评级
- 如果 JD 未明确地点要求（如"无明确要求""全国"），不做地点判断，正常评估其他维度
- **地点不匹配是一个重要风险点，必须在 risks 中明确标注**

## 性别匹配（重要）
- **如果岗位匹配标准中 gender_requirement 为"男"或"女"，候选人简历中显示的性别与之不符的，不得评为 A 档，必须在 risks 中明确标注"性别不符"。**
- 如果 gender_requirement 为"不限"或未填写，不做性别判断。
- 猎聘简历摘要中通常包含"男"或"女"标签，请据此判断。

## 档位
A = 核心技能明确 + 近期相关 + 薪资匹配 + 地点匹配 + 性别匹配（或岗位未限这些维度）
B = 核心技能有但非近期，或行业有偏差但可迁移，或薪资/地点/性别略有不匹配但其他维度优秀
C = 有相关背景但核心技能不明确，或薪资/地点/性别严重不匹配，需沟通确认
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
        jd_section = ""
        jd_text = str(criteria.get("jd_text") or "").strip()
        if jd_text:
            # JD 薪资信息通常在后半部分，不要简单截断前 N 字。
            # 如果过长，保留头部（职位描述）+ 尾部（薪资、要求、福利）。
            max_len = 4000
            if len(jd_text) > max_len:
                head = jd_text[:1500]
                tail = jd_text[-(max_len - 1500):]
                jd_trimmed = "{}\n\n...（中间省略）...\n\n{}".format(head, tail)
            else:
                jd_trimmed = jd_text
            jd_section = """

【岗位 JD 原文】
{jd}

请从 JD 原文中自行识别薪资范围（如有），并与简历中的候选人薪资信息对比。""".format(
                jd=jd_trimmed
            )

        notes_section = ""
        user_notes = str(criteria.get("user_notes") or "").strip()
        if user_notes:
            notes_section = """

【项目要点 / 客户备注】
{notes}

以上是客户或项目经理额外强调的要求（如学历院校要求、英语等级、背调、经验区间、加班/休假制度等），匹配时必须重点参考，和 JD 要求同等重要。""".format(
                notes=user_notes[:2000]
            )

        return """【岗位匹配标准】
{criteria}

【候选人简历】
{resume}
{jd_section}
{notes_section}

请判断候选人与岗位的匹配档位：
A = 核心技能明确 + 近期相关 + 薪资匹配 + 地点匹配（或岗位未限薪资/地点）
B = 核心技能有但非近期，或行业有偏差但可迁移，或薪资/地点略有不匹配但其他维度优秀
C = 有相关背景但核心技能不明确，或薪资/地点严重不匹配，需沟通确认
D = 不相关或明显不符

要求：
1. 判断必须围绕岗位匹配标准中的核心要求。
2. evidence 尽量引用简历原文证据。
3. 合理推断要有依据，不确定的放入 questions。
4. A/B/C/D 只是标签，核心是证据、推断、风险和待确认问题。
5. **薪资匹配：你必须自行从 JD 原文中读取岗位薪资范围，从简历中读取候选人薪资，然后判断。严重不匹配时必须在 risks 中明确标注，且不得给 A 档。**
6. **地点匹配：你必须自行从简历中读取候选人的当前所在地和期望工作地，与岗位要求对比。严重不匹配时必须在 risks 中明确标注，且不得给 A 档。**
7. **项目要点中的额外要求（如 CET4、211 硕士、背调接受度、经验区间等）必须重点评估，不符合的不得给 A 档。**
""".format(
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
            resume=resume_text or "",
            jd_section=jd_section,
            notes_section=notes_section,
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
