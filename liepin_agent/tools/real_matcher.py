"""Real LLM-backed candidate matcher."""

from __future__ import annotations

import json
import hashlib
import logging
import re
import unicodedata
from typing import Dict, Optional

from pydantic import ValidationError

from ..core.config import ConfigManager
from ..domain.match_output import MatchOutput
from ..domain.models import MatchResult
from .llm_client import LLMClient


logger = logging.getLogger(__name__)

MATCH_PROMPT_VERSION = "match-contract-v7-verdict-20260717"
MIN_GROUNDED_EVIDENCE_CHARS = 4


class MatchOutputParseError(ValueError):
    """The model response did not contain one valid JSON object."""


class MatchEvidenceValidationError(ValueError):
    """Direct evidence could not be anchored to the captured resume."""


MATCH_SYSTEM_PROMPT = """你是资深猎头顾问，判断候选人与岗位的匹配度。

## 核心原则
1. 抓重点：只有人工确认的 must/dealbreaker 才是硬条件；简历未写只能算未知
2. 合理推断：
   - 简历写"电机结构设计" → 可推断会用 CAD/SolidWorks
   - 简历写"无刷电机驱动开发" → 可推断懂 FOC/SVPWM
   - 简历写"美的/格力电机研发" → 可推断有小家电背景
3. 看近期：近 3 年经验权重更高
4. 不瞎编：推断要有依据，不确定的标注出来

## 薪资匹配（重要）
- **你必须自行从 JD 原文中读取岗位薪资范围，从简历中读取候选人目前/期望薪资，然后判断两者是否匹配。**
- 如果 JD 中有明确薪资范围（如年薪 30-50万、月薪 25-35k 等），候选人期望/目前薪资严重超出该范围，必须记录为明确风险
- 候选人薪资明显高于岗位上限的（如岗位 30-50万，候选人目前 80万+或期望 100万），应视为核心风险
- 候选人薪资明显低于岗位下限的（如岗位 30-50万，候选人目前 15万），除非有明确理由（如转行、地域差异），否则也需标注风险
- 如果 JD 中未提及具体薪资范围（或只有"面议""竞争力薪资"等模糊表述），不做薪资判断，正常评估其他维度

## 地点匹配（重要）
- **你必须自行从简历中读取候选人的当前所在地和期望工作地**（通常在"求职期望""基本信息"部分）
- **如果岗位有明确城市/地点要求，候选人当前城市或期望城市与岗位要求严重不符，必须记录为明确风险**
- 例如：岗位在深圳，候选人当前在北京且期望城市也是北京 → 地点严重不匹配
- 例如：岗位在深圳，候选人当前在广州但期望城市包含深圳 → 地点匹配
- 如果 JD 未明确地点要求（如"无明确要求""全国"），不做地点判断，正常评估其他维度
- **地点不匹配是一个重要风险点，必须在 risks 中明确标注**

## 性别匹配（重要）
- **如果岗位匹配标准中 gender_requirement 为"男"或"女"，候选人简历中显示的性别与之不符，必须在 risks 中明确标注"性别不符"。**
- 如果 gender_requirement 为"不限"或未填写，不做性别判断。
- 猎聘简历摘要中通常包含"男"或"女"标签，请据此判断。
- 若【已解析的结构化事实】中 gender 为空且简历正文无性别信息，性别相关条件判 unknown，不得当作不符合。

只输出一个 JSON 对象，不要 Markdown 或额外说明。必须使用以下字段：
{
  "summary": "一句话说明匹配证据和主要不确定性",
  "core_met_count": 0,
  "core_total": 0,
  "dealbreaker_hit": false,
  "matched_evidence": [
    {"criterion": "岗位条件", "verdict": "met/not_met/unknown", "evidence": "简历事实依据", "strength": "strong/medium/weak"}
  ],
  "inferred_evidence": [
    {"criterion": "推断能力", "evidence": "推断及其依据", "strength": "strong/medium/weak"}
  ],
  "missing_or_unclear": ["缺失或无法确认的信息"],
  "risks": ["风险点"],
  "questions_to_verify": ["电话要确认的问题"],
  "recommendation": "后续建议",
  "confidence": "high/medium/low"
}

matched_evidence 放简历中明确提供的事实，允许忠实概括或汇总，但不得新增或
改变姓名、公司、技术、数字、日期、薪资、职责范围和管理层级等关键信息。推断必须
单独放在 inferred_evidence。缺失信息只能写入 missing_or_unclear，不能当作不符合。

verdict 是对 criterion 指向的岗位条件的判定，必须按实际证据如实填写：
- met：简历事实明确满足该条件
- not_met：简历事实明确不满足该条件（该条件必须同时写入 risks）
- unknown：简历未提供该条件相关信息（该条件必须写入 missing_or_unclear）
无论 met / not_met / unknown，evidence 都要写明你实际读到的简历事实
（例如"年龄44岁，超过32岁要求"），禁止把不满足或未知标成 met。
"""


class RealMatchService:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        config_payload = {
            "provider": getattr(llm_client, "provider", ""),
            "model": getattr(llm_client, "model_name", ""),
            "temperature": getattr(llm_client, "temperature", None),
            "max_tokens": getattr(llm_client, "max_tokens", None),
        }
        self.prompt_version = MATCH_PROMPT_VERSION
        self.model_config_hash = hashlib.sha256(
            json.dumps(
                config_payload, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()

    @property
    def cache_identity(self) -> Dict[str, str]:
        return {
            "prompt_version": self.prompt_version,
            "model_config_hash": self.model_config_hash,
        }

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
                max_retries=config.llm_max_retries,
                max_tokens=config.llm_max_tokens,
temperature=config.backend_llm_temperature,
                rpm_limit=config.llm_rpm_limit,
                rpm_burst=config.llm_rpm_burst,
                rpm_cooldown_seconds=config.llm_rpm_cooldown_seconds,
            )
        )

    def match_candidate(
        self,
        session_id: str,
        round_id: str,
        candidate_id: str,
        resume_text: str,
        criteria: Dict[str, object],
        structured_facts: Optional[Dict[str, object]] = None,
        capture_quality: Optional[Dict[str, object]] = None,
    ) -> MatchResult:
        criteria = criteria or {}
        prompt = self._build_prompt(
            criteria,
            resume_text,
            structured_facts=structured_facts,
            capture_quality=capture_quality,
        )
        try:
            raw = self.llm_client.chat(prompt, system_message=MATCH_SYSTEM_PROMPT)
        except Exception as exc:
            logger.exception("Candidate match request failed: candidate=%s", candidate_id)
            result = self._technical_failure_result(
                session_id=session_id,
                round_id=round_id,
                candidate_id=candidate_id,
                criteria=criteria,
                error=exc,
            )
            return self._attach_audit_metadata(result, prompt, resume_text)

        try:
            payload = self._parse_json(raw)
            output = MatchOutput.model_validate(payload)
            summary_count = self._assess_direct_evidence(output, resume_text)
            grounding_risk = self._apply_grounding_confidence(output, summary_count)
            match_score = output.deterministic_score()
        except (
            MatchOutputParseError,
            MatchEvidenceValidationError,
            ValidationError,
        ) as exc:
            logger.warning(
                "Candidate match output needs review: candidate=%s error=%s",
                candidate_id,
                self._validation_message(exc),
            )
            result = self._review_result(
                session_id=session_id,
                round_id=round_id,
                candidate_id=candidate_id,
                criteria=criteria,
                raw=raw,
                error=exc,
            )
            return self._attach_audit_metadata(result, prompt, resume_text)

        result = MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            core_met_count=output.core_met_count,
            core_total=output.core_total,
            dealbreaker_hit=output.dealbreaker_hit,
            summary=output.summary,
            risks="；".join(
                [
                    *output.risks,
                    *([grounding_risk] if grounding_risk else []),
                ]
            ),
            recommendation=output.recommendation,
            detail=output.detail or output.canonical_json(),
            raw_response=raw,
            status="completed",
            criteria_version_id=str(criteria.get("criteria_version_id") or ""),
            matched_evidence=output.evidence_for_match_result(),
            missing_or_unclear=output.missing_or_unclear,
            questions_to_verify=output.questions_to_verify,
            confidence=output.confidence,
            match_score=match_score,
        )
        return self._attach_audit_metadata(result, prompt, resume_text)

    @classmethod
    def _assess_direct_evidence(
        cls, output: MatchOutput, resume_text: str
    ) -> int:
        """Annotate whether direct evidence is verbatim or a model summary."""
        normalized_resume = cls._normalize_evidence_text(resume_text)
        summary_count = 0
        for item in output.matched_evidence:
            quote = cls._normalize_evidence_text(item.evidence)
            if (
                len(quote) >= MIN_GROUNDED_EVIDENCE_CHARS
                and quote in normalized_resume
            ):
                item.grounding_status = "exact"
            else:
                item.grounding_status = "model_summary"
                summary_count += 1
        return summary_count

    @staticmethod
    def _apply_grounding_confidence(
        output: MatchOutput, summary_count: int
    ) -> str:
        if summary_count <= 0:
            return ""
        if summary_count == len(output.matched_evidence):
            output.confidence = "low"
        elif output.confidence == "high":
            output.confidence = "medium"
        return "{} 条匹配证据为模型概括，未逐字定位，建议结合简历复核".format(
            summary_count
        )

    @staticmethod
    def _normalize_evidence_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(
            character for character in normalized if character.isalnum()
        )

    def _attach_audit_metadata(
        self, result: MatchResult, prompt: str, resume_text: str
    ) -> MatchResult:
        result.prompt_version = self.prompt_version
        result.model_name = str(getattr(self.llm_client, "model_name", "") or "")
        result.model_config_hash = self.model_config_hash
        result.input_hash = hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()
        result.resume_hash = hashlib.sha256(
            (resume_text or "").strip().encode("utf-8")
        ).hexdigest()
        return result

    @classmethod
    def _review_result(
        cls,
        session_id: str,
        round_id: str,
        candidate_id: str,
        criteria: Dict[str, object],
        raw: str,
        error: Exception,
    ) -> MatchResult:
        reason = cls._validation_message(error)
        return MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            summary="模型匹配结果无法可靠校验，需人工复核。",
            risks="匹配结果校验失败：{}".format(reason),
            recommendation="人工复核后再决定是否推进。",
            detail=raw,
            raw_response=raw,
            status="needs_review",
            criteria_version_id=str(criteria.get("criteria_version_id") or ""),
            missing_or_unclear=["模型输出未通过匹配契约校验"],
            questions_to_verify=["请人工复核该候选人与岗位要求的匹配度"],
            confidence="low",
        )

    @staticmethod
    def _technical_failure_result(
        session_id: str,
        round_id: str,
        candidate_id: str,
        criteria: Dict[str, object],
        error: Exception,
    ) -> MatchResult:
        return MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            summary="模型匹配请求失败，未产生业务评级。",
            risks=str(error),
            recommendation="排查模型服务后重试，或转人工复核。",
            detail=str(error),
            raw_response="",
            status="failed",
            criteria_version_id=str(criteria.get("criteria_version_id") or ""),
            missing_or_unclear=["模型匹配请求失败"],
            questions_to_verify=["请在模型服务恢复后重新匹配"],
            confidence="low",
        )

    @staticmethod
    def _validation_message(error: Exception) -> str:
        if isinstance(error, ValidationError):
            messages = []
            for item in error.errors(include_input=False):
                location = ".".join(str(part) for part in item.get("loc", ()))
                message = str(item.get("msg") or "invalid value")
                messages.append("{}: {}".format(location or "output", message))
            return "; ".join(messages) or "输出未通过校验"
        return str(error) or "输出无法解析"

    @staticmethod
    def _build_prompt(
        criteria: Dict[str, object],
        resume_text: str,
        structured_facts: Optional[Dict[str, object]] = None,
        capture_quality: Optional[Dict[str, object]] = None,
    ) -> str:
        compact_criteria = {
            key: value
            for key, value in (criteria or {}).items()
            if key not in {"jd_text", "user_notes", "source_jd_text", "source_user_notes"}
        }
        jd_section = ""
        jd_text = str(criteria.get("jd_text") or "").strip()
        if jd_text:
            # JD 薪资信息通常在后半部分，不要简单截断前 N 字。
            # 如果过长，保留头部（职位描述）+ 尾部（薪资、要求、福利）。
            max_len = 3000
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

        structured_section = ""
        if structured_facts:
            structured_section = """

【已解析的结构化事实】
{structured}

这些字段来自抓取器。缺失字段只能判断为 unknown，不能判断为不符合。""".format(
                structured=json.dumps(
                    structured_facts, ensure_ascii=False, separators=(",", ":")
                )
            )

        quality_section = ""
        if capture_quality:
            quality_section = """

【抓取完整度】
{quality}

如果关键区段缺失，应降低 confidence 并写入 missing_or_unclear。""".format(
                quality=json.dumps(
                    capture_quality, ensure_ascii=False, separators=(",", ":")
                )
            )

        resume = resume_text or ""
        if len(resume) > 12000:
            resume = "{}\n\n...（中间省略）...\n\n{}".format(
                resume[:7000], resume[-4500:]
            )

        return """【岗位匹配标准】
{criteria}

【候选人简历】
{resume}
{structured_section}
{quality_section}
{jd_section}
{notes_section}

要求：
1. 判断必须围绕岗位匹配标准中的核心要求。
2. matched_evidence 只能写简历明确提供的事实，不能把推断当作直接证据。
   允许忠实概括或跨段汇总，但不得新增或改变姓名、公司、技术、数字、日期、薪资、职责范围和管理层级等关键信息；
   每个核心条件分别给出 verdict（met/not_met/unknown）和证据。
3. 合理推断放入 inferred_evidence，不确定的信息放入 missing_or_unclear，
   需要沟通的问题放入 questions_to_verify。
4. 简历没有写某项要求时只能标记为未知，禁止因为缺失信息写成明确不符合。
5. **薪资匹配：必须从 JD 原文读取岗位薪资范围并与简历薪资对比，严重不匹配时写入 risks。**
6. **地点匹配：必须对比候选人当前所在地、期望工作地与岗位要求，严重不匹配时写入 risks。**
7. **项目要点中的额外要求（如 CET4、211 硕士、背调接受度、经验区间等）必须重点评估。**
""".format(
            criteria=json.dumps(compact_criteria, ensure_ascii=False, indent=2),
            resume=resume,
            structured_section=structured_section,
            quality_section=quality_section,
            jd_section=jd_section,
            notes_section=notes_section,
        )

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, object]:
        text = (raw or "").strip()
        if not text:
            raise MatchOutputParseError("模型返回为空")

        block = re.search(
            r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE
        )
        if block:
            text = block.group(1)

        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            decoder = json.JSONDecoder()
            data = None
            for index, character in enumerate(text):
                if character != "{":
                    continue
                try:
                    candidate, _ = decoder.raw_decode(text[index:])
                except ValueError:
                    continue
                if isinstance(candidate, dict):
                    data = candidate
                    break
        if not isinstance(data, dict):
            raise MatchOutputParseError("模型未返回有效的 JSON 对象")
        return data

    @staticmethod
    def _string_list(value: object) -> list:
        return MatchOutput.model_validate(
            {
                "summary": "compatibility validation",
                "missing_or_unclear": value,
            }
        ).missing_or_unclear

    @staticmethod
    def _list_of_dicts(value: object) -> list:
        output = MatchOutput.model_validate(
            {
                "summary": "compatibility validation",
                "matched_evidence": value,
            }
        )
        return output.evidence_for_match_result()
