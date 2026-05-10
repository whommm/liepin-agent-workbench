"""Agent decision brains.

The product path uses ``LLMAgentBrain``. ``RuleBasedAgentBrain`` exists for
tests and emergency fallback only; it is not wired as the default runtime brain.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ..core.config import ConfigManager
from ..domain.models import (
    CandidateSummary,
    FetchDecision,
    Observation,
    RoundReview,
    SearchPlan,
)
from ..domain.states import RoundType
from ..tools.llm_client import LLMClient
from .candidate_picker import CandidatePicker
from .observer import Observer
from .planner import Planner
from .reviewer import Reviewer


AGENT_SYSTEM_PROMPT = """你是一位资深猎头寻访 Agent。你必须用结构化 JSON 做决策。
原则：
1. 搜索栏放候选人简历真实会出现的行业/业务/项目短词。
2. 职位栏只放岗位收口词，例如 产品、算法、结构、运营。
3. 每轮搜索后先观察结果池，不要机械抓详情。
4. 可以选择 skip_detail、sample_detail、validate_detail、harvest_detail。
5. 抓详情要混合高置信样本、多样性样本、不确定样本。
6. 前期验证轮需要等待足够匹配结果再决定下一轮；收割轮可以后台匹配。
7. 不要编造页面结果，只能基于输入数据判断。
只输出 JSON，不要 Markdown。"""


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
        stop_reason: str,
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


class LLMAgentBrain:
    """LLM-backed Agent brain for real sourcing decisions."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.fallback = RuleBasedAgentBrain()

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
            )
        )

    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        prompt = """请从 JD 中提取“匹配词与岗位要求”草案，输出 JSON：
{{
  "keywords_text": "每行一个关键词，5-12个",
  "requirements_text": "一段简洁岗位要求描述",
  "position_filter": "职位栏收口词"
}}

要求：
1. 只提取寻访真正需要的关键技能、行业、业务、产品、客户或场景背景。
2. 不输出评分权重。
3. 不拆复杂硬性/软性结构。
4. 不要把 JD 里的所有词都搬出来。
5. requirements_text 要短、清楚、方便人类编辑确认。

【JD】
{jd}

【补充说明】
{notes}
""".format(jd=jd_text or "", notes=user_notes or "")
        data = self._chat_json(
            prompt, self.fallback.build_criteria(jd_text, user_notes)
        )
        keywords = str(data.get("keywords_text") or "").strip()
        if not keywords:
            keywords = "\n".join(self._string_list(data.get("core_terms"))[:12])
        keyword_terms = self._string_list(keywords)[:12]
        return {
            "position_filter": str(data.get("position_filter") or "产品"),
            "core_terms": keyword_terms,
            "negative_terms": self._string_list(data.get("negative_terms"))[:12],
            "hard_requirements": self._string_list(data.get("hard_requirements"))[:12],
            "city_scope": self._string_list(data.get("city_scope"))[:8],
            "keywords_text": "\n".join(keyword_terms),
            "requirements_text": str(data.get("requirements_text") or "").strip()
            or self.fallback.build_criteria(jd_text, user_notes).get(
                "requirements_text", ""
            ),
        }

    def initial_plan(
        self, jd_text: str, user_notes: str, criteria: Dict[str, object]
    ) -> SearchPlan:
        prompt = """请为猎聘找人生成第一轮搜索计划，输出 JSON：
{{
  "query": "搜索栏关键词，2-4个短词，用空格分隔",
  "position_filter": "职位栏收口词",
  "scope": "全部经历/目前职位",
  "match_mode": "all/any",
  "filters": {{"city": [], "active_days": 7, "work_years": "", "education": ""}},
  "intent": "本轮搜索目的",
  "expected_signal": ["期待在候选人卡片中看到的信号"],
  "risk": "本轮可能噪音",
  "search_hypothesis_type": "core_background/target_company/transferable_scene",
  "search_hypothesis_text": "本轮验证的搜索假设"
}}

要求：
1. 第一轮不要用泛词单搜，例如 产品、设计、管理。
2. 搜索栏优先放业务场景词或项目词。
3. 职位栏只放岗位收口词。
4. 只能基于已确认匹配词与岗位要求生成搜索假设，不要发明新的岗位要求。

【JD】
{jd}

【补充说明】
{notes}

【已确认匹配词与岗位要求】
{criteria}
""".format(
            jd=jd_text or "",
            notes=user_notes or "",
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
        )
        fallback_plan = self.fallback.initial_plan(
            jd_text, user_notes, criteria
        ).to_dict()
        return self._plan_from_data(self._chat_json(prompt, fallback_plan), criteria)

    def observe_round(
        self,
        plan: SearchPlan,
        candidates: List[CandidateSummary],
        criteria: Dict[str, object],
    ) -> Observation:
        cards = [
            self._candidate_card(item)
            for item in sorted(candidates, key=lambda c: -c.pre_score)[:40]
        ]
        prompt = """请观察本轮猎聘搜索结果池，输出 JSON：
{{
  "round_quality": "empty/low/uncertain/medium/high",
  "raw_count": 数字,
  "deduped_count": 数字,
  "estimated_relevant_count": 数字,
  "noise_patterns": ["噪音类型"],
  "positive_signals": ["正向信号"],
  "recommended_round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "reason": "判断依据"
}}

【本轮搜索计划】
{plan}

【匹配标准】
{criteria}

【候选人卡片样本】
{cards}
""".format(
            plan=json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            criteria=json.dumps(criteria or {}, ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )
        fallback_observation = self.fallback.observe_round(
            plan, candidates, criteria
        ).to_dict()
        data = self._chat_json(prompt, fallback_observation)
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
        cards = [
            self._candidate_card(item)
            for item in sorted(candidates, key=lambda c: -c.pre_score)[:50]
        ]
        valid_ids = {item.id for item in candidates}
        prompt = """请决定本轮是否抓取候选人详情，输出 JSON：
{{
  "action": "skip_detail/fetch_details",
  "round_type": "skip_detail/sample_detail/validate_detail/harvest_detail",
  "candidate_ids": ["候选人ID"],
  "fetch_limit": 数字,
  "sampling_strategy": {{"high_confidence": 数字, "diversity": 数字, "uncertain": 数字}},
  "match_wait_policy": {{"mode": "no_wait/wait_min_results/wait_all", "min_results": 数字, "timeout_seconds": 数字}},
  "reason": "为什么这么抓"
}}

要求：
1. 明显低质轮可以不抓。
2. 探测轮抓 2-4 个，验证轮抓 5-10 个，收割轮最多 15 个。
3. 不要只抓 Top N，要混入多样性和不确定样本。
4. candidate_ids 必须来自候选人卡片列表。
5. 剩余总预算：{budget}

【观察结论】
{observation}

【候选人卡片】
{cards}
""".format(
            budget=remaining_detail_budget,
            observation=json.dumps(observation.to_dict(), ensure_ascii=False, indent=2),
            cards=json.dumps(cards, ensure_ascii=False, indent=2),
        )
        fallback_decision = self.fallback.decide_fetch(
            observation, candidates, remaining_detail_budget
        ).to_dict()
        data = self._chat_json(prompt, fallback_decision)
        action = str(data.get("action") or "skip_detail")
        candidate_ids = [
            item
            for item in self._string_list(data.get("candidate_ids"))
            if item in valid_ids
        ]
        fetch_limit = min(
            int(data.get("fetch_limit") or len(candidate_ids)),
            remaining_detail_budget,
            15,
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
                "timeout_seconds": 180,
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
    ) -> RoundReview:
        prompt = """请复盘本轮猎聘寻访，并决定停止或下一轮搜索，输出 JSON：
{{
  "action": "continue/stop",
  "summary": "复盘结论",
  "next_plan": {{
    "query": "下一轮搜索栏",
    "position_filter": "职位栏",
    "scope": "全部经历/目前职位",
    "match_mode": "all/any",
    "filters": {{}},
    "intent": "下一轮目的",
    "expected_signal": [],
    "risk": "风险",
    "search_hypothesis_type": "core_background/target_company/transferable_scene",
    "search_hypothesis_text": "下一轮验证的搜索假设"
  }},
  "evidence": {{}}
}}

如果 should_stop 为 true，必须 action=stop。
下一轮 query 不要重复 used_queries。
下一轮只能围绕已确认匹配词与岗位要求组合、放宽或收紧，不要发明新的岗位要求。

【should_stop】{should_stop}
【stop_reason】{stop_reason}
【target_met】{target_met}
【上一轮计划】
{plan}
【已用 query】
{used_queries}
【匹配结果】
{matches}
【噪音】
{noise}
【JD】
{jd}
""".format(
            should_stop=should_stop,
            stop_reason=stop_reason,
            target_met=target_met,
            plan=json.dumps(previous_plan.to_dict(), ensure_ascii=False, indent=2),
            used_queries=json.dumps(used_queries or [], ensure_ascii=False),
            matches=json.dumps(match_results or [], ensure_ascii=False, indent=2),
            noise=json.dumps(noise_patterns or [], ensure_ascii=False),
            jd=jd_text or "",
        )
        fallback_review = self.fallback.review_round(
            previous_plan=previous_plan,
            jd_text=jd_text,
            used_queries=used_queries,
            match_results=match_results,
            noise_patterns=noise_patterns,
            target_met=target_met,
            should_stop=should_stop,
            stop_reason=stop_reason,
        ).to_dict()
        data = self._chat_json(prompt, fallback_review)
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
            next_plan = self._plan_from_data(data["next_plan"], {})
            if next_plan.query in set(used_queries or []):
                action = "stop"
        return RoundReview(
            action=action if action in {"continue", "stop"} else "continue",
            summary=str(data.get("summary") or stop_reason or "Agent 已完成本轮复盘。"),
            next_plan=next_plan,
            evidence=data.get("evidence")
            if isinstance(data.get("evidence"), dict)
            else {},
        )

    def _chat_json(
        self, prompt: str, fallback: Optional[Dict[str, object]] = None
    ) -> Dict[str, object]:
        try:
            raw = self.llm_client.chat(prompt, system_message=AGENT_SYSTEM_PROMPT)
            return self._parse_json(raw)
        except Exception:
            return dict(fallback or {})

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
        if "city" not in filters and criteria.get("city_scope"):
            filters["city"] = criteria.get("city_scope")
        if "active_days" not in filters:
            filters["active_days"] = 7
        return SearchPlan(
            query=str(data.get("query") or "").strip(),
            position_filter=str(
                data.get("position_filter") or criteria.get("position_filter") or "产品"
            ),
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
