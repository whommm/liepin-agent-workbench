"""Agent decision brains.

The product path uses ``LLMAgentBrain``. ``RuleBasedAgentBrain`` exists for
tests and emergency fallback only; it is not wired as the default runtime brain.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from ..core.config import ConfigManager
from ..domain.models import (
    CandidateSummary,
    FetchDecision,
    Observation,
    RoundReview,
    SearchPlan,
)
from ..domain.states import RoundType
from ..prompts.loader import PromptLoader, get_prompt_loader, system_prompt
from ..tools.llm_client import LLMClient
from .candidate_picker import CandidatePicker
from .observer import Observer
from .planner import Planner
from .reviewer import Reviewer

# 程序护栏常量：LLM 偶尔违反搜索规则，用代码兜底避免零产出 / 重复搜索。

# query 词数上限（按空格分词后的词数）。第一轮探测或常规轮次，超过 3 个词
# AND 在猎聘上几乎一定零产出，截断到 MAX_QUERY_TERMS。
MAX_QUERY_TERMS = 3

# 常见职位方向后缀/关键词。query 里出现这些词时，说明 query 已自带职位方向，
# 此时再填 position_filter 会形成双重 AND 过滤、把结果挤压到归零，应清空它。
_POSITION_TITLE_TOKENS = (
    "总监", "经理", "主管", "工程师", "设计师", "专员", "顾问", "主任", "厂长",
    "架构师", "分析师", "产品经理", "设计师", "开发", "设计师", "插画师", "工程师",
    "研究员", "设计师", "美术",
)
# 常见职位方向词根（不带后缀，单独出现也算职位方向）
_POSITION_TITLE_ROOTS = (
    "设计", "研发", "产品", "运营", "算法", "前端", "后端", "测试", "运维",
    "插画", "原画", "美宣", "视觉", "平面设计", "结构", "机械",
)


def _query_has_position_intent(query: str) -> bool:
    """query 是否已经自带职位方向（用于双重过滤检测）。"""
    text = (query or "").strip()
    if not text:
        return False
    if any(token in text for token in _POSITION_TITLE_TOKENS):
        return True
    return any(root in text for root in _POSITION_TITLE_ROOTS)


def _normalize_query_terms(query: str) -> List[str]:
    """把 query 拆成词列表，去掉排除词（-xxx）后返回，用于词集去重和词数统计。"""
    terms: List[str] = []
    for part in (query or "").split():
        part = part.strip()
        if part and not part.startswith("-"):
            terms.append(part)
    return terms


class RuleBasedAgentBrain:
    """Deterministic brain used by tests."""

    def __init__(self):
        self.planner = Planner()
        self.observer = Observer()
        self.picker = CandidatePicker()
        self.reviewer = Reviewer(self.planner)

    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        return self.planner.build_criteria(jd_text, user_notes)

    def initial_plan(
        self, jd_text: str, user_notes: str, criteria: Dict[str, object]
    ) -> SearchPlan:
        return self.planner.initial_plan(jd_text, user_notes, criteria)

    def observe_round(
        self,
        plan: SearchPlan,
        candidates: List[CandidateSummary],
        criteria: Dict[str, object],
        page_meta: Dict[str, object] | None = None,
    ) -> Observation:
        return self.observer.observe(
            candidates, plan.expected_signal or criteria.get("core_terms", [])
        )

    def decide_fetch(
        self,
        observation: Observation,
        candidates: List[CandidateSummary],
        remaining_detail_budget: int,
    ) -> FetchDecision:
        return self.picker.decide(observation, candidates, remaining_detail_budget)

    def review_round(
        self,
        previous_plan: SearchPlan,
        jd_text: str,
        used_queries: List[str],
        match_results: List[Dict[str, object]],
        noise_patterns: List[str],
        target_met: bool,
        should_stop: bool,
        stop_reason: str = "",
        criteria: Dict[str, object] | None = None,
        **kwargs,
    ) -> RoundReview:
        return self.reviewer.review(
            previous_plan=previous_plan,
            jd_text=jd_text,
            used_queries=used_queries,
            match_results=match_results,
            noise_patterns=noise_patterns,
            target_met=target_met,
            should_stop=should_stop,
            stop_reason=stop_reason,
        )

    def apply_user_command(
        self,
        user_command: str,
        current_plan: SearchPlan,
        criteria: Dict[str, object] | None = None,
    ) -> SearchPlan:
        return current_plan


class LLMAgentBrain:
    """LLM-backed Agent brain for real sourcing decisions.

    LLM 调用失败时直接抛异常，不再静默 fallback 到规则引擎，
    避免用户被低质量 fallback 结果误导。
    """

    def __init__(self, llm_client: LLMClient, prompt_loader: PromptLoader | None = None):
        self.llm_client = llm_client
        self._prompt = prompt_loader or get_prompt_loader()

    @classmethod
    def from_config(
        cls, config_manager: Optional[ConfigManager] = None
    ) -> "LLMAgentBrain":
        manager = config_manager or ConfigManager()
        config = manager.config
        return cls(
            LLMClient(
                api_base_url=config.api_base_url,
                api_key=config.api_key,
                model_name=config.model_name,
                timeout=config.timeout,
                provider=config.llm_provider or "openai",
            )
        )

    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        prompt = self._prompt.get(
            "build_criteria",
            jd=jd_text or "",
            notes=user_notes or "",
        )
        data = self._chat_json(prompt)
        return {
            "position_filter": str(data.get("position_filter") or "").strip(),
            "core_requirement": str(data.get("core_requirement") or "").strip(),
            "requirements_text": str(data.get("core_requirement") or "").strip(),
            "keywords_text": str(data.get("core_requirement") or "").strip(),
            "search_direction": str(data.get("search_direction") or "").strip(),
            "target_companies": self._string_list(data.get("target_companies"))[:8],
            "city_requirement": str(data.get("city_requirement") or "").strip(),
            "city_scope": self._string_list(data.get("city_scope"))[:8],
            # gender_requirement 直接影响猎聘搜索的性别筛选，必须透传，
            # 否则下游 initial_plan / matcher 都拿不到 JD 的性别要求。
            "gender_requirement": str(data.get("gender_requirement") or "").strip(),
        }

    def initial_plan(
        self, jd_text: str, user_notes: str, criteria: Dict[str, object]
    ) -> SearchPlan:
        prompt = self._prompt.get(
            "initial_plan",
            jd=jd_text or "",
            notes=user_notes or "",
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        return self._plan_from_data(self._chat_json(prompt), criteria)

    def observe_round(
        self,
        plan: SearchPlan,
        candidates: List[CandidateSummary],
        criteria: Dict[str, object],
        page_meta: Dict[str, object] | None = None,
    ) -> Observation:
        # 不再按系统预评分截断，全部候选人交给 LLM 做智能观察
        cards = [
            self._candidate_card(item)
            for item in (candidates or [])
        ]
        prompt = self._prompt.get(
            "observe_round",
            plan=json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
            page_meta=json.dumps(page_meta or {}, ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        round_type = str(
            data.get("recommended_round_type") or RoundType.SAMPLE_DETAIL.value
        )
        if round_type not in {item.value for item in RoundType}:
            round_type = RoundType.SAMPLE_DETAIL.value
        return Observation(
            round_quality=str(data.get("round_quality") or "uncertain"),
            raw_count=int(data.get("raw_count") or len(candidates)),
            deduped_count=int(data.get("deduped_count") or len(candidates)),
            estimated_relevant_count=int(data.get("estimated_relevant_count") or 0),
            noise_patterns=self._string_list(data.get("noise_patterns"))[:8],
            positive_signals=self._string_list(data.get("positive_signals"))[:8],
            recommended_round_type=round_type,
            reason=str(data.get("reason") or "Agent 已完成本轮观察。"),
        )

    def decide_fetch(
        self,
        observation: Observation,
        candidates: List[CandidateSummary],
        remaining_detail_budget: int,
    ) -> FetchDecision:
        # 不再按系统预评分截断，全部候选人交给 LLM 决定抓取策略
        cards = [
            self._candidate_card(item)
            for item in (candidates or [])
        ]
        valid_ids = {item.id for item in candidates}
        prompt = self._prompt.get(
            "decide_fetch",
            budget=remaining_detail_budget,
            observation=json.dumps(observation.to_dict(), ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        action = str(data.get("action") or "skip_detail")
        candidate_ids = [
            item
            for item in self._string_list(data.get("candidate_ids"))
            if item in valid_ids
        ]
        # 不再硬上限 15，完全信任 LLM 的抓取决策（用户明确不在乎成本）
        # 以 LLM 实际返回的 candidate_ids 长度为准，不受 fetch_limit 字段额外约束
        fetch_limit = min(
            len(candidate_ids),
            remaining_detail_budget,
        )
        candidate_ids = candidate_ids[:fetch_limit]
        if not candidate_ids:
            action = "skip_detail"
        round_type = str(data.get("round_type") or observation.recommended_round_type)
        if round_type not in {item.value for item in RoundType}:
            round_type = observation.recommended_round_type
        policy = (
            data.get("match_wait_policy")
            if isinstance(data.get("match_wait_policy"), dict)
            else {}
        )
        if action == "fetch_details" and not policy:
            policy = {
                "mode": "wait_min_results",
                "min_results": min(5, len(candidate_ids)),
                "timeout_seconds": 300,
            }
        return FetchDecision(
            action=action,
            round_type=round_type,
            candidate_ids=candidate_ids,
            fetch_limit=len(candidate_ids),
            sampling_strategy=data.get("sampling_strategy")
            if isinstance(data.get("sampling_strategy"), dict)
            else {},
            match_wait_policy=policy,
            reason=str(data.get("reason") or observation.reason),
        )

    def review_round(
        self,
        previous_plan: SearchPlan,
        jd_text: str,
        used_queries: List[str],
        match_results: List[Dict[str, object]],
        noise_patterns: List[str],
        target_met: bool,
        should_stop: bool,
        stop_reason: str,
        criteria: Dict[str, object] | None = None,
        used_query_signatures: List[str] | None = None,
    ) -> RoundReview:
        prompt = self._prompt.get(
            "review_round",
            should_stop=should_stop,
            stop_reason=stop_reason,
            target_met=target_met,
            plan=json.dumps(previous_plan.to_dict(), ensure_ascii=False, indent=2),
            used_queries=json.dumps(used_queries or [], ensure_ascii=False),
            matches=json.dumps(match_results or [], ensure_ascii=False, indent=2),
            noise=json.dumps(noise_patterns or [], ensure_ascii=False),
            jd=jd_text or "",
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        action = "stop" if should_stop else str(data.get("action") or "continue")
        if should_stop:
            return RoundReview(
                action="stop",
                summary=str(
                    stop_reason or data.get("summary") or "Agent 已达到停止条件。"
                ),
                next_plan=None,
                evidence=data.get("evidence")
                if isinstance(data.get("evidence"), dict)
                else {},
            )
        next_plan = None
        if action != "stop" and isinstance(data.get("next_plan"), dict):
            next_plan = self._plan_from_data(data["next_plan"], criteria or {})
            signatures = set(used_query_signatures or used_queries or [])
            if self._plan_signature(next_plan) in signatures:
                action = "stop"
        return RoundReview(
            action=action if action in {"continue", "stop"} else "continue",
            summary=str(data.get("summary") or stop_reason or "Agent 已完成本轮复盘。"),
            next_plan=next_plan,
            evidence=data.get("evidence")
            if isinstance(data.get("evidence"), dict)
            else {},
        )

    @staticmethod
    def _plan_signature(plan: SearchPlan) -> str:
        # query 用词集去重，让 "潮玩 插画" 和 "插画 潮玩" 识别为同一签名，
        # 避免模型靠调换词序绕过 used_query 检查而反复搜同一组合。
        terms = sorted(_normalize_query_terms(plan.query))
        parts = [" ".join(terms)]
        if plan.position_filter:
            parts.append("pos={}".format(plan.position_filter))
        if plan.scope and plan.scope != "全部经历":
            parts.append("scope={}".format(plan.scope))
        filters = plan.filters or {}
        if filters.get("city"):
            parts.append("city={}".format(",".join(str(c) for c in filters["city"] if c)))
        if filters.get("company"):
            parts.append("company={}".format(filters["company"]))
        return " | ".join(parts)

    def generate_web_search_queries(
        self,
        jd_text: str,
        current_query: str,
        used_queries: List[str],
        noise_patterns: List[str],
        match_results: List[Dict[str, object]],
        criteria: Dict[str, object] | None = None,
    ) -> List[str]:
        """让 LLM 根据当前寻访困境生成有针对性的联网搜索查询。"""
        prompt = self._prompt.get(
            "generate_web_search_queries",
            jd=jd_text or "",
            current_query=current_query or "",
            used_queries=json.dumps(used_queries or [], ensure_ascii=False),
            noise_patterns=json.dumps(noise_patterns or [], ensure_ascii=False),
            matches=json.dumps(match_results or [], ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        return self._string_list(data.get("queries"))[:3]

    def enhance_plan_with_web_search(
        self,
        current_plan: SearchPlan,
        jd_text: str,
        used_queries: List[str],
        noise_patterns: List[str],
        web_search_intel: Dict[str, object],
        criteria: Dict[str, object] | None = None,
    ) -> Optional[SearchPlan]:
        """Enhance next search plan with web search intelligence.

        Returns a new SearchPlan if the web intelligence yields a useful
        adjustment, otherwise None so the caller falls back to the
        original plan.
        """
        if not web_search_intel or not web_search_intel.get("summary"):
            return None

        prompt = self._prompt.get(
            "enhance_plan",
            jd=jd_text or "",
            used_queries=json.dumps(used_queries or [], ensure_ascii=False),
            noise_patterns=json.dumps(noise_patterns or [], ensure_ascii=False),
            current_plan=json.dumps(current_plan.to_dict(), ensure_ascii=False, indent=2),
            web_search_results=str(web_search_intel.get("summary", "")),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        if not data.get("should_enhance"):
            return None
        enhanced = self._plan_from_data(data, criteria or {})
        if enhanced.query in set(used_queries or []):
            return None
        return enhanced

    def apply_user_command(
        self,
        user_command: str,
        current_plan: SearchPlan,
        criteria: Dict[str, object] | None = None,
    ) -> SearchPlan:
        prompt = self._prompt.get(
            "apply_user_command",
            user_command=user_command,
            current_plan=json.dumps(current_plan.to_dict(), ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        data = self._chat_json(prompt)
        if isinstance(data, dict) and data.get("query"):
            return self._plan_from_data(data, criteria)
        return current_plan

    # JSON 解析重试上限：LLM 偶尔输出不规范的 JSON（被截断、夹带说明文字、
    # 空回复等），直接抛异常会让整个寻访 session 失败。这里在解析失败时把
    # 上次的错误输出反馈给模型，给它 1-2 次自我纠正的机会，避免一次抖动
    # 导致全盘皆输。
    JSON_PARSE_MAX_RETRIES = 2

    def _chat_json(self, prompt: str) -> Dict[str, object]:
        last_error: Optional[Exception] = None
        last_raw = ""
        for attempt in range(self.JSON_PARSE_MAX_RETRIES + 1):
            if attempt == 0:
                full_prompt = prompt
            else:
                # 把上一次的错误输出和解析失败原因反馈给模型，要求严格重出 JSON。
                full_prompt = self._build_retry_prompt(prompt, last_raw, last_error)
                logger.warning(
                    "brain._chat_json: JSON parse retry %s/%s after error: %s",
                    attempt,
                    self.JSON_PARSE_MAX_RETRIES,
                    last_error,
                )
            raw = self.llm_client.chat(full_prompt, system_message=system_prompt())
            last_raw = raw or ""
            try:
                return self._parse_json(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning(
                    "brain._chat_json: parse failed on attempt %s, raw_len=%s, head=%r",
                    attempt + 1,
                    len(last_raw),
                    last_raw[:200],
                )
        # 重试用尽仍失败，抛出最后一次的解析异常，由 runtime 兜底处理。
        raise last_error or ValueError("Agent JSON 解析失败")

    @staticmethod
    def _build_retry_prompt(
        original_prompt: str, bad_raw: str, error: Optional[Exception]
    ) -> str:
        """构造解析重试的 prompt：附上上次错误输出并要求严格只输出 JSON。"""
        error_text = str(error or "JSON 解析失败")
        bad_sample = (bad_raw or "").strip()
        if len(bad_sample) > 1500:
            bad_sample = bad_sample[:1500] + "\n...（已截断）"
        return (
            "{original}\n\n"
            "====================\n"
            "【重要纠错】你上一轮的输出无法被解析为合法 JSON，错误原因：{error}\n"
            "你上次的原始输出（供参考，请勿重复同样的错误）：\n"
            "-----\n"
            "{bad}\n"
            "-----\n"
            "请重新输出，并严格遵守：\n"
            "1. 只输出一个 JSON 对象，不要任何 Markdown 标记、不要前后说明文字。\n"
            "2. 不要用 ```json 代码块，直接以 {{ 开头、以 }} 结尾。\n"
            "3. 字符串值不要包含未转义的换行或引号。\n"
            "4. 保持字段结构与你被要求的输出 schema 完全一致。"
        ).format(
            original=original_prompt,
            error=error_text,
            bad=bad_sample,
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
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Agent JSON 必须是对象")
        return data

    @staticmethod
    def _plan_from_data(
        data: Dict[str, object], criteria: Dict[str, object]
    ) -> SearchPlan:
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        query = str(data.get("query") or "").strip()
        position_filter = str(data.get("position_filter") or "").strip()

        # 护栏 1：query 词数上限。超过 MAX_QUERY_TERMS 个词 AND 在猎聘几乎一定
        # 零产出，截断保留前 MAX_QUERY_TERMS 个词（保留排除词 -xxx 不计入上限）。
        terms = _normalize_query_terms(query)
        excludes = [p for p in query.split() if p.startswith("-")]
        if len(terms) > MAX_QUERY_TERMS:
            kept = " ".join(terms[:MAX_QUERY_TERMS])
            if excludes:
                kept = "{} {}".format(kept, " ".join(excludes))
            logger.warning(
                "plan_guard: query terms %s > %s, truncated to '%s'",
                len(terms), MAX_QUERY_TERMS, kept,
            )
            query = kept

        # 护栏 2：双重过滤检测。query 已自带职位方向时，position_filter 会叠加
        # AND 把结果挤压归零，自动清空 position_filter。
        if position_filter and _query_has_position_intent(query):
            logger.warning(
                "plan_guard: query '%s' already has position intent, "
                "cleared position_filter '%s' to avoid double filtering",
                query, position_filter,
            )
            position_filter = ""

        # 护栏 3：性别筛选强制透传。LLM 在 initial_plan / next_plan 里经常漏填
        # filters.gender，导致 JD 明确"限男/限女"时猎聘搜索却没有勾选性别。
        # 只要 criteria 里识别到 gender_requirement（且不是"不限"），就在这里
        # 硬性补进 filters.gender，不依赖 LLM 自觉。
        if not filters.get("gender"):
            gender_req = str((criteria or {}).get("gender_requirement") or "").strip()
            if gender_req and gender_req != "不限":
                filters["gender"] = gender_req

        return SearchPlan(
            query=query,
            position_filter=position_filter,
            scope=str(data.get("scope") or "全部经历"),
            match_mode=str(data.get("match_mode") or "all"),
            filters=filters,
            intent=str(data.get("intent") or "Agent 生成的搜索计划"),
            expected_signal=LLMAgentBrain._string_list(
                data.get("expected_signal") or criteria.get("core_terms")
            )[:12],
            risk=str(data.get("risk") or ""),
            search_hypothesis_type=str(
                data.get("search_hypothesis_type") or "core_background"
            ),
            search_hypothesis_text=str(
                data.get("search_hypothesis_text") or data.get("intent") or ""
            ),
        )

    @staticmethod
    def _candidate_card(candidate: CandidateSummary) -> Dict[str, object]:
        return {
            "id": candidate.id,
            "name": candidate.name,
            "title": candidate.current_title,
            "company": candidate.current_company,
            "city": candidate.city,
            "work_years": candidate.work_years,
            "education": candidate.education,
            "summary": candidate.summary_text,
            "raw_text": candidate.raw_text or candidate.summary_text,
            "pre_score": candidate.pre_score,
            "pre_score_reasons": candidate.pre_score_reasons,
            "card_decision": candidate.card_decision,
            "card_signals": candidate.card_signals,
            "card_risks": candidate.card_risks,
            "card_reason": candidate.card_reason,
            "result_index": candidate.result_index,
        }

    @staticmethod
    def _string_list(value: object) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [
                item.strip() for item in re.split(r"[、,，;\n]+", value) if item.strip()
            ]
        return []
