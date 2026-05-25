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
from ..prompts.loader import PromptLoader, get_prompt_loader, system_prompt
from ..tools.llm_client import LLMClient
from .candidate_picker import CandidatePicker
from .observer import Observer
from .planner import Planner
from .reviewer import Reviewer


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
        stop_reason: str,
        criteria: Dict[str, object] | None = None,
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
        fetch_limit = min(
            int(data.get("fetch_limit") or len(candidate_ids)),
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

    def _chat_json(self, prompt: str) -> Dict[str, object]:
        raw = self.llm_client.chat(prompt, system_message=system_prompt())
        return self._parse_json(raw)

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
        return SearchPlan(
            query=str(data.get("query") or "").strip(),
            position_filter=str(data.get("position_filter") or ""),
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
