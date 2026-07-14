"""Agent decision brains.

The product path uses ``LLMAgentBrain``. ``RuleBasedAgentBrain`` exists for
tests and emergency fallback only; it is not wired as the default runtime brain.
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Sequence

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
from .context import (
    OBSERVE_PROMPT_CHAR_BUDGET,
    REVIEW_PROMPT_CHAR_BUDGET,
    build_match_review_context,
    build_observation_context,
    compact_page_metadata,
    compact_strategy_history,
    compact_text,
    json_text,
    shrink_prompt_value,
)
from .observer import Observer
from .planner import Planner
from .reviewer import Reviewer

logger = logging.getLogger(__name__)

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
        already_fetched_ids: Optional[Sequence[str]] = None,
    ) -> FetchDecision:
        return self.picker.decide(
            observation,
            candidates,
            remaining_detail_budget,
            already_fetched_ids=already_fetched_ids,
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

    搜索策略类调用在模型连续返回非法结果时会显式记录并降级到规则引擎，
    避免单次模型抖动让整个寻访任务失败。候选人匹配不经过这里，仍使用
    严格的结构化结果契约，不会把失败结果伪装成业务档位。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_loader: PromptLoader | None = None,
        candidate_picker: CandidatePicker | None = None,
    ):
        self.llm_client = llm_client
        self._prompt = prompt_loader or get_prompt_loader()
        self.picker = candidate_picker or CandidatePicker()
        self.last_prompt_metrics: Dict[str, Dict[str, int]] = {}
        self._fallback_brain = RuleBasedAgentBrain()
        self.last_fallback: Dict[str, str] = {}

    @classmethod
    def from_config(
        cls, config_manager: Optional[ConfigManager] = None
    ) -> "LLMAgentBrain":
        manager = config_manager or ConfigManager()
        config = manager.config
        strategies = {
            RoundType.SAMPLE_DETAIL.value: {
                "limit": config.sample_detail_limit,
                "min_results": config.sample_detail_min_results,
                "timeout_seconds": config.match_wait_timeout_seconds,
                "skip_audit_rate": config.candidate_skip_audit_rate,
            },
            RoundType.VALIDATE_DETAIL.value: {
                "limit": config.validate_detail_limit,
                "min_results": config.validate_detail_min_results,
                "timeout_seconds": config.match_wait_timeout_seconds,
                "skip_audit_rate": config.candidate_skip_audit_rate,
            },
            RoundType.HARVEST_DETAIL.value: {
                "limit": config.harvest_detail_limit,
                "timeout_seconds": config.match_wait_timeout_seconds,
                "skip_audit_rate": min(config.candidate_skip_audit_rate, 0.05),
            },
        }
        return cls(
            LLMClient(
                api_base_url=config.api_base_url,
                api_key=config.api_key,
                model_name=config.model_name,
                timeout=config.timeout,
                provider=config.llm_provider or "openai",
                max_retries=config.llm_max_retries,
                max_tokens=config.llm_max_tokens,
                temperature=config.llm_temperature,
            ),
            candidate_picker=CandidatePicker(strategies),
        )

    def build_criteria(self, jd_text: str, user_notes: str) -> Dict[str, object]:
        prompt = self._prompt.get(
            "build_criteria",
            jd=jd_text or "",
            notes=user_notes or "",
        )
        try:
            data = self._chat_json(prompt)
        except Exception as exc:
            self._record_fallback("build_criteria", exc)
            return self._fallback_brain.build_criteria(jd_text, user_notes)
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
        try:
            return self._plan_from_data(self._chat_json(prompt), criteria)
        except Exception as exc:
            self._record_fallback("initial_plan", exc)
            return self._fallback_brain.initial_plan(jd_text, user_notes, criteria)

    def observe_round(
        self,
        plan: SearchPlan,
        candidates: List[CandidateSummary],
        criteria: Dict[str, object],
        page_meta: Dict[str, object] | None = None,
    ) -> Observation:
        expected_terms = plan.expected_signal or self._string_list(
            (criteria or {}).get("core_terms")
        )
        cards = build_observation_context(candidates or [], expected_terms)
        prompt = self._render_budgeted_prompt(
            "observe_round",
            char_budget=OBSERVE_PROMPT_CHAR_BUDGET,
            protected_keys=("criteria",),
            shrink_order=("cards", "page_meta"),
            plan=json_text(plan.to_dict()),
            criteria=json_text(criteria or {}),
            page_meta=json_text(compact_page_metadata(page_meta)),
            cards=json_text(cards),
        )
        try:
            data = self._chat_json(prompt)
        except Exception as exc:
            self._record_fallback("observe_round", exc)
            return self._fallback_brain.observe_round(
                plan,
                candidates,
                criteria,
                page_meta=page_meta,
            )
        round_type = str(
            data.get("recommended_round_type") or RoundType.SAMPLE_DETAIL.value
        )
        if round_type not in {item.value for item in RoundType}:
            round_type = RoundType.SAMPLE_DETAIL.value
        return Observation(
            round_quality=str(data.get("round_quality") or "uncertain"),
            raw_count=int(cards.get("pool_stats", {}).get("raw_count") or 0),
            deduped_count=int(cards.get("pool_stats", {}).get("unique_count") or 0),
            estimated_relevant_count=max(
                0,
                min(
                    int(cards.get("pool_stats", {}).get("unique_count") or 0),
                    int(data.get("estimated_relevant_count") or 0),
                ),
            ),
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
        already_fetched_ids: Optional[Sequence[str]] = None,
    ) -> FetchDecision:
        # Card routing is deterministic and recall-first. The LLM observes the
        # pool and recommends a round type, but it no longer re-reads every card
        # or acts as the only gate for who receives a detail fetch.
        return self.picker.decide(
            observation,
            candidates,
            remaining_detail_budget,
            already_fetched_ids=already_fetched_ids,
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
        round_digest: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> RoundReview:
        history = compact_strategy_history(
            used_queries,
            round_digest or kwargs.get("round_history") or kwargs.get("strategy_history"),
        )
        matches = build_match_review_context(match_results or [])
        prompt = self._render_budgeted_prompt(
            "review_round",
            char_budget=REVIEW_PROMPT_CHAR_BUDGET,
            protected_keys=("criteria",),
            shrink_order=("matches", "used_queries", "jd", "noise"),
            should_stop=should_stop,
            stop_reason=compact_text(stop_reason, 500),
            target_met=target_met,
            plan=json_text(previous_plan.to_dict()),
            used_queries=json_text(history),
            matches=json_text(matches),
            noise=json_text([compact_text(item, 80) for item in (noise_patterns or [])[:8]]),
            jd=compact_text(jd_text, 1_200),
            criteria=json_text(criteria or {}),
        )
        try:
            data = self._chat_json(prompt)
        except Exception as exc:
            self._record_fallback("review_round", exc)
            return self._fallback_brain.review_round(
                previous_plan=previous_plan,
                jd_text=jd_text,
                used_queries=used_queries,
                match_results=match_results,
                noise_patterns=noise_patterns,
                target_met=target_met,
                should_stop=should_stop,
                stop_reason=stop_reason,
                criteria=criteria,
            )
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
                fallback = self._fallback_nonduplicate_plan(
                    previous_plan, signatures
                )
                if fallback is not None:
                    next_plan = fallback
                    action = "continue"
                    data["summary"] = "{} 已将重复计划改为单变量放宽。".format(
                        str(data.get("summary") or "模型给出了重复计划。").strip()
                    )
                else:
                    action = "stop"
        return RoundReview(
            action=action if action in {"continue", "stop"} else "continue",
            summary=str(data.get("summary") or stop_reason or "Agent 已完成本轮复盘。"),
            next_plan=next_plan,
            evidence=data.get("evidence")
            if isinstance(data.get("evidence"), dict)
            else {},
        )

    def _render_budgeted_prompt(
        self,
        name: str,
        *,
        char_budget: int,
        protected_keys: Sequence[str] = (),
        shrink_order: Sequence[str] = (),
        **values: object,
    ) -> str:
        """Render a prompt with a hard cap on non-protected context.

        Confirmed criteria are protected verbatim.  If that protected block and
        the static template alone exceed the normal budget, the effective budget
        grows to that irreducible floor instead of truncating a hard condition.
        """

        rendered_values = {key: str(value) for key, value in values.items()}
        protected = set(protected_keys)
        shrinkable = [key for key in shrink_order if key not in protected]

        minimum_values = dict(rendered_values)
        for key in shrinkable:
            minimum_values[key] = self._empty_prompt_section(minimum_values.get(key, ""))
        minimum_prompt = self._prompt.get(name, **minimum_values)
        effective_budget = max(int(char_budget), len(minimum_prompt))

        prompt = self._prompt.get(name, **rendered_values)
        for key in shrinkable:
            if len(prompt) <= effective_budget:
                break
            current = rendered_values.get(key, "")
            excess = len(prompt) - effective_budget
            target = max(2, len(current) - excess - 32)
            rendered_values[key] = shrink_prompt_value(current, target)
            prompt = self._prompt.get(name, **rendered_values)

        if len(prompt) > effective_budget:
            for key in shrinkable:
                rendered_values[key] = self._empty_prompt_section(
                    rendered_values.get(key, "")
                )
                prompt = self._prompt.get(name, **rendered_values)
                if len(prompt) <= effective_budget:
                    break

        if len(prompt) > effective_budget:
            logger.warning(
                "prompt_budget: %s irreducible prompt has %s chars (budget %s)",
                name,
                len(prompt),
                effective_budget,
            )
        self.last_prompt_metrics[name] = {
            "chars": len(prompt),
            "budget": effective_budget,
            "protected_chars": sum(
                len(rendered_values.get(key, "")) for key in protected
            ),
        }
        return prompt

    def _record_fallback(self, operation: str, exc: Exception) -> None:
        """Expose strategy degradation in logs and prompt diagnostics."""
        reason = "{}: {}".format(type(exc).__name__, exc)
        self.last_fallback = {"operation": operation, "reason": reason}
        metrics = self.last_prompt_metrics.setdefault(operation, {})
        metrics["fallback"] = 1
        logger.error(
            "agent strategy fallback: operation=%s reason=%s",
            operation,
            reason,
        )

    @staticmethod
    def _empty_prompt_section(value: str) -> str:
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return ""
        return "[]" if isinstance(decoded, list) else "{}"

    @staticmethod
    def _plan_signature(plan: SearchPlan) -> str:
        terms = sorted(
            {item.casefold() for item in _normalize_query_terms(plan.query) if item}
        )
        excludes = sorted(
            {
                item.strip().casefold()
                for item in (plan.query or "").split()
                if item.strip().startswith("-")
            }
        )
        parts = ["query={}".format(" ".join([*terms, *excludes]))]
        if plan.position_filter:
            parts.append("pos={}".format(plan.position_filter.strip().casefold()))
        if plan.scope and plan.scope != "全部经历":
            parts.append("scope={}".format(plan.scope))
        filters = plan.filters or {}
        if filters:
            normalized_filters: Dict[str, object] = {}
            for key, value in sorted(filters.items()):
                if value in (None, "", [], {}):
                    continue
                if isinstance(value, (list, tuple, set)):
                    normalized_filters[str(key)] = sorted(
                        str(item).strip().casefold()
                        for item in value
                        if str(item).strip()
                    )
                elif isinstance(value, str):
                    normalized_filters[str(key)] = value.strip().casefold()
                else:
                    normalized_filters[str(key)] = value
            if normalized_filters:
                parts.append("filters={}".format(json_text(normalized_filters)))
        return " | ".join(parts)

    @classmethod
    def _fallback_nonduplicate_plan(
        cls, previous_plan: SearchPlan, used_signatures: set[str]
    ) -> Optional[SearchPlan]:
        """Try one controlled relaxation when the model repeats a plan."""
        candidates: List[SearchPlan] = []

        for filter_name in ("city", "company", "education", "age", "gender"):
            if not (previous_plan.filters or {}).get(filter_name):
                continue
            plan = deepcopy(previous_plan)
            plan.filters = dict(plan.filters or {})
            plan.filters.pop(filter_name, None)
            plan.intent = "只放宽 {}，验证筛选条件是否压缩召回".format(filter_name)
            plan.search_hypothesis_text = plan.intent
            candidates.append(plan)

        active_days = (previous_plan.filters or {}).get("active_days")
        try:
            active_days_value = int(active_days or 0)
        except (TypeError, ValueError):
            active_days_value = 0
        if active_days_value and active_days_value < 30:
            plan = deepcopy(previous_plan)
            plan.filters = dict(plan.filters or {})
            plan.filters["active_days"] = 30
            plan.intent = "只把活跃范围放宽到 30 天，验证新增召回"
            plan.search_hypothesis_text = plan.intent
            candidates.append(plan)

        if previous_plan.position_filter:
            plan = deepcopy(previous_plan)
            plan.position_filter = ""
            plan.intent = "只移除职位栏硬筛选，保持关键词验证召回"
            plan.search_hypothesis_text = plan.intent
            candidates.append(plan)

        if previous_plan.scope and previous_plan.scope != "全部经历":
            plan = deepcopy(previous_plan)
            plan.scope = "全部经历"
            plan.intent = "只把搜索范围放宽到全部经历"
            plan.search_hypothesis_text = plan.intent
            candidates.append(plan)

        query_terms = _normalize_query_terms(previous_plan.query)
        if len(query_terms) > 1:
            plan = deepcopy(previous_plan)
            plan.query = " ".join(query_terms[:-1])
            plan.intent = "只减少一个关键词，验证是否因 AND 条件过窄"
            plan.search_hypothesis_text = plan.intent
            candidates.append(plan)

        for plan in candidates:
            if cls._plan_signature(plan) not in used_signatures:
                return plan
        return None

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
        history = compact_strategy_history(used_queries)
        prompt = self._render_budgeted_prompt(
            "generate_web_search_queries",
            char_budget=12_000,
            protected_keys=("criteria",),
            shrink_order=("matches", "jd", "used_queries", "noise_patterns"),
            jd=compact_text(jd_text, 1_200),
            current_query=compact_text(current_query, 160),
            used_queries=json_text(history),
            noise_patterns=json_text(
                [compact_text(item, 80) for item in (noise_patterns or [])[:8]]
            ),
            matches=json_text(build_match_review_context(match_results or [])),
            criteria=json_text(criteria or {}),
        )
        try:
            data = self._chat_json(prompt)
        except Exception as exc:
            self._record_fallback("generate_web_search_queries", exc)
            return []
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

        history = compact_strategy_history(used_queries)
        prompt = self._render_budgeted_prompt(
            "enhance_plan",
            char_budget=12_000,
            protected_keys=("criteria",),
            shrink_order=("web_search_results", "jd", "used_queries"),
            jd=compact_text(jd_text, 1_200),
            used_queries=json_text(history),
            noise_patterns=json_text(
                [compact_text(item, 80) for item in (noise_patterns or [])[:8]]
            ),
            current_plan=json_text(current_plan.to_dict()),
            web_search_results=compact_text(web_search_intel.get("summary", ""), 3_000),
            criteria=json_text(criteria or {}),
        )
        try:
            data = self._chat_json(prompt)
        except Exception as exc:
            self._record_fallback("enhance_plan_with_web_search", exc)
            return None
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
        try:
            data = self._chat_json(prompt)
        except Exception as exc:
            self._record_fallback("apply_user_command", exc)
            return current_plan
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
        filters = dict(data.get("filters")) if isinstance(data.get("filters"), dict) else {}
        query = str(data.get("query") or "").strip()
        position_filter = str(data.get("position_filter") or "").strip()
        hypothesis_type = str(
            data.get("search_hypothesis_type") or "core_background"
        )

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

        filters = LLMAgentBrain._guard_search_filters(
            filters, criteria or {}, hypothesis_type
        )

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
            search_hypothesis_type=hypothesis_type,
            search_hypothesis_text=str(
                data.get("search_hypothesis_text") or data.get("intent") or ""
            ),
        )

    @staticmethod
    def _guard_search_filters(
        filters: Dict[str, object],
        criteria: Dict[str, object],
        hypothesis_type: str,
    ) -> Dict[str, object]:
        """Keep filters recall-safe and tied to confirmed job context."""
        allowed = {"city", "active_days", "education", "company", "age", "gender"}
        guarded = {
            key: value
            for key, value in filters.items()
            if key in allowed and value not in (None, "", [], {})
        }

        confirmed_text = "\n".join(
            str(criteria.get(key) or "")
            for key in ("requirements_text", "city_requirement", "keywords_text")
        )
        city_scope = {
            str(item).strip()
            for item in (criteria.get("city_scope") or [])
            if str(item).strip()
        }
        city_requirement = str(criteria.get("city_requirement") or "").strip()
        if "city" in guarded:
            requested = guarded["city"]
            requested_cities = (
                [str(item).strip() for item in requested if str(item).strip()]
                if isinstance(requested, list)
                else [str(requested).strip()]
            )
            confirmed_cities = city_scope or (
                {city_requirement}
                if city_requirement and city_requirement not in {"不限", "全国"}
                else set()
            )
            if not confirmed_cities:
                guarded.pop("city", None)
            else:
                kept = [city for city in requested_cities if city in confirmed_cities]
                if kept:
                    guarded["city"] = kept
                else:
                    guarded.pop("city", None)

        education = str(guarded.get("education") or "").strip()
        if education and education not in confirmed_text:
            guarded.pop("education", None)

        # Protected attributes are never promoted from model prose into hard
        # platform filters. A future UI may explicitly opt in after compliance
        # review by setting this flag on the confirmed criteria snapshot.
        if criteria.get("allow_protected_attribute_filters") is not True:
            guarded.pop("age", None)
            guarded.pop("gender", None)

        if hypothesis_type != "target_company":
            guarded.pop("company", None)

        return guarded

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
