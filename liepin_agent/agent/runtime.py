"""Main Agent runtime orchestration."""

from __future__ import annotations

import hashlib
import logging
import inspect
import json
import threading
import time
from concurrent.futures import Future
from typing import Dict, List, Optional, Set, Tuple

from ..domain.models import CandidateDetail, CandidateSummary, MatchResult, SearchPlan
from ..domain.pre_score import classify_candidate_card, pre_score_candidate
from ..domain.recommendation import (
    HIGH_POTENTIAL_VERIFY,
    PRIORITY_CONTACT,
    TRANSFERABLE_EXPLORE,
)
from ..domain.states import (
    AgentEventType,
    CandidateStatus,
    RoundStatus,
    RoundType,
    SessionStatus,
)
from ..domain.stop_conditions import evaluate_stop_conditions
from ..services.browser_queue import BrowserQueue
from ..services.event_bus import EventBus
from ..services.match_queue import MatchQueue
from ..services.candidate_intelligence import CandidateIntelligenceService
from ..services.candidate_ranking import CandidateRankingService
from ..storage.sqlite_store import SQLiteStore
from ..tools.real_liepin import RealLiepinTool
from ..tools.real_matcher import RealMatchService
from ..tools.web_search import WebSearchTool
from .brain import LLMAgentBrain
from .context import build_match_review_context


logger = logging.getLogger(__name__)


class AgentRuntime:
    """Run a full sourcing session.

    The runtime is tool-agnostic for tests, but the product path uses the real
    Liepin Playwright adapter and real LLM matcher by default.
    """

    def __init__(
        self,
        store: SQLiteStore,
        event_bus: Optional[EventBus] = None,
        browser_queue: Optional[BrowserQueue] = None,
        match_queue: Optional[MatchQueue] = None,
        liepin_tool: Optional[object] = None,
        matcher: Optional[object] = None,
        agent_brain: Optional[object] = None,
        web_search_tool: Optional[WebSearchTool] = None,
        config: Optional[object] = None,
    ):
        self.store = store
        self.event_bus = event_bus or EventBus()
        self.browser_queue = browser_queue or BrowserQueue()
        self._config = config or getattr(store, "config", None)
        self.match_queue = match_queue or MatchQueue(
            max_workers=getattr(self._config, "match_queue_workers", None) or 3
        )
        self.liepin_tool = liepin_tool or RealLiepinTool()
        self.matcher = matcher or RealMatchService.from_config()
        self.brain = agent_brain or LLMAgentBrain.from_config()
        self.web_search_tool = web_search_tool or WebSearchTool()
        self.candidate_intelligence = CandidateIntelligenceService()
        self.ranking_service = CandidateRankingService(store)
        self.config = (
            getattr(getattr(self.liepin_tool, "config_manager", None), "config", None)
            or getattr(self.store, "config", None)
        )
        self._threads: Dict[str, threading.Thread] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._pause_events: Dict[str, threading.Event] = {}
        self._web_search_count: Dict[str, int] = {}
        self._pending_match_keys: Set[Tuple[str, str]] = set()
        self._pending_match_keys_lock = threading.Lock()
        self._session_match_futures: Dict[str, List[Future]] = {}
        self._round_match_futures: Dict[Tuple[str, str], List[Future]] = {}

    def start_session(self, session_id: str) -> None:
        if session_id in self._threads and self._threads[session_id].is_alive():
            return
        cancel_event = threading.Event()
        pause_event = threading.Event()
        self._cancel_events[session_id] = cancel_event
        self._pause_events[session_id] = pause_event
        thread = threading.Thread(
            target=self.run_session,
            args=(session_id, cancel_event, pause_event),
            name="AgentRuntime-{}".format(session_id[:8]),
            daemon=True,
        )
        self._threads[session_id] = thread
        thread.start()

    def is_active(self, session_id: str) -> bool:
        thread = self._threads.get(session_id)
        return bool(thread and thread.is_alive())

    def active_session_ids(self) -> list[str]:
        """Return a list of session IDs that currently have live threads."""
        return [sid for sid, t in self._threads.items() if t.is_alive()]

    def cancel_session(self, session_id: str) -> None:
        event = self._cancel_events.get(session_id)
        if event:
            event.set()
        self.store.update_session_status(session_id, SessionStatus.CANCELLED.value)
        self._notify(session_id)

    def pause_session(self, session_id: str) -> None:
        event = self._pause_events.get(session_id)
        if event:
            event.set()
            self.store.update_session_status(session_id, SessionStatus.PAUSED.value)
            self._notify(session_id)

    def resume_session(self, session_id: str) -> None:
        event = self._pause_events.get(session_id)
        if event:
            event.clear()
            self.store.update_session_status(session_id, SessionStatus.RUNNING.value)
            self._notify(session_id)

    def run_session(
        self,
        session_id: str,
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> None:
        cancel_event = cancel_event or threading.Event()
        pause_event = pause_event or threading.Event()
        session = self.store.get_session(session_id)
        if not session:
            return
        if str(session.get("status") or "") == SessionStatus.CANCELLED.value:
            return

        try:
            self.store.update_session_status(session_id, SessionStatus.RUNNING.value)
            self._notify(session_id)
            started_monotonic = time.monotonic()

            jd_text = str(session.get("jd_text") or "")
            user_notes = str(session.get("user_notes") or "")
            existing_rounds = self.store.list_rounds(session_id)
            self._event(
                session_id,
                None,
                AgentEventType.SEARCH_PLAN.value,
                "Agent 启动",
                "正在调用模型理解岗位并生成搜索计划。",
                {},
            )
            criteria_version = self.store.get_latest_criteria_version(
                session_id, "confirmed"
            )
            if not criteria_version:
                self.store.update_session_status(
                    session_id,
                    SessionStatus.CRITERIA_DRAFT.value,
                    "请先确认匹配词与岗位要求，再开始寻访。",
                )
                self._event(
                    session_id,
                    None,
                    AgentEventType.JOB_UNDERSTANDING.value,
                    "等待确认寻访基准",
                    "请先确认匹配词与岗位要求，Agent 才会开始搜索。",
                    {},
                )
                self._notify(session_id)
                return
            criteria = self.store.get_latest_criteria(session_id)
            # Rebuild recommendation snapshots on resume so all sessions use
            # the current uncertainty-aware pool logic.
            self.ranking_service.refresh_session(session_id)

            used_queries: List[str] = [
                str(item.get("query") or "")
                for item in existing_rounds
                if item.get("query")
            ]
            used_query_signatures: List[str] = [
                self._query_signature_from_round(item)
                for item in existing_rounds
                if item.get("query")
            ]
            if existing_rounds:
                previous_plan = self._plan_from_round(existing_rounds[-1])
                match_results = self.store.list_match_results(
                    session_id, existing_rounds[-1]["id"]
                )
                self._event(
                    session_id,
                    existing_rounds[-1]["id"],
                    AgentEventType.ROUND_REVIEW.value,
                    "AI 正在恢复任务",
                    "正在基于已完成轮次和匹配结果生成下一轮计划。",
                    {"used_queries": used_queries},
                )
                recovery_kwargs = {
                    "previous_plan": previous_plan,
                    "jd_text": jd_text,
                    "used_queries": used_queries,
                    "match_results": match_results,
                    "noise_patterns": [],
                    "target_met": False,
                    "should_stop": False,
                    "stop_reason": "",
                    "criteria": criteria,
                }
                recovery_parameters = inspect.signature(
                    self.brain.review_round
                ).parameters
                if "used_query_signatures" in recovery_parameters:
                    recovery_kwargs["used_query_signatures"] = used_query_signatures
                if "round_digest" in recovery_parameters:
                    recovery_kwargs["round_digest"] = self.store.list_round_digests(
                        session_id
                    )
                recovery_review = self.brain.review_round(**recovery_kwargs)
                if recovery_review.action == "stop" and not recovery_review.next_plan:
                    self.store.update_session_status(
                        session_id, SessionStatus.COMPLETED.value
                    )
                    self._event(
                        session_id,
                        existing_rounds[-1]["id"],
                        AgentEventType.SESSION_COMPLETED.value,
                        "恢复检查后结束寻访",
                        recovery_review.summary,
                        recovery_review.to_dict(),
                    )
                    self._notify(session_id)
                    return
                plan = recovery_review.next_plan or self.brain.initial_plan(
                    jd_text, user_notes, criteria
                )
                self._event(
                    session_id,
                    existing_rounds[-1]["id"],
                    AgentEventType.ROUND_REVIEW.value,
                    "任务已恢复",
                    recovery_review.summary,
                    recovery_review.to_dict(),
                )
            else:
                self._event(
                    session_id,
                    None,
                    AgentEventType.SEARCH_PLAN.value,
                    "AI 正在生成第一轮搜索计划",
                    "正在调用模型决定第一轮搜索栏、职位栏和筛选条件。",
                    {},
                )
                plan = self.brain.initial_plan(jd_text, user_notes, criteria)
            self.store.ensure_search_hypotheses(session_id, criteria)
            portfolio_plan = self.store.select_search_hypothesis_plan(
                session_id, str(criteria.get("criteria_version_id") or "")
            )
            if portfolio_plan is not None:
                plan = portfolio_plan
            consecutive_low_yield_rounds = 0
            config_max_rounds = getattr(
                getattr(self.store, "config", None), "max_rounds_default", 20
            )
            config_low_yield = getattr(
                getattr(self.store, "config", None), "consecutive_low_yield_threshold", 4
            )
            max_rounds = int(session.get("max_rounds") or config_max_rounds)
            max_detail_fetches = int(session.get("max_detail_fetches") or 999)
            target_ab_count = int(session.get("target_ab_count") or 999)
            max_runtime_minutes = int(session.get("max_runtime_minutes") or 0)
            low_yield_threshold = int(session.get("consecutive_low_yield_threshold") or config_low_yield)
            run_mode = str(session.get("mode") or "自动")

            start_round_index = len(existing_rounds) + 1
            for round_index in range(start_round_index, max_rounds + 1):
                criteria_version = self.store.get_latest_criteria_version(
                    session_id, "confirmed"
                )
                if not criteria_version:
                    self.store.update_session_status(
                        session_id,
                        SessionStatus.CRITERIA_DRAFT.value,
                        "寻访基准已变更，请确认后继续。",
                    )
                    self._notify(session_id)
                    return
                criteria = self.store.get_latest_criteria(session_id)
                if criteria.get("core_terms"):
                    plan.expected_signal = list(criteria.get("core_terms") or [])
                    if not plan.search_hypothesis_text:
                        plan.search_hypothesis_text = "验证搜索假设：{}".format(
                            plan.query
                        )
                self._respect_control_flags(session_id, cancel_event, pause_event)
                fetched_count = self.store.count_fetched_details(session_id)
                effective_pool_score = float(
                    self.ranking_service.pool_summary(session_id).get(
                        "effective_pool_score"
                    )
                    or 0
                )
                stop = evaluate_stop_conditions(
                    round_index=round_index - 1,
                    max_rounds=max_rounds,
                    fetched_details=fetched_count,
                    max_detail_fetches=max_detail_fetches,
                    ab_count=effective_pool_score,
                    target_ab_count=target_ab_count,
                    consecutive_low_yield_rounds=consecutive_low_yield_rounds,
                    elapsed_minutes=(time.monotonic() - started_monotonic) / 60,
                    max_runtime_minutes=max_runtime_minutes,
                    low_yield_threshold=low_yield_threshold,
                )
                if stop.should_stop:
                    self._event(
                        session_id,
                        None,
                        AgentEventType.SESSION_COMPLETED.value,
                        "寻访停止",
                        stop.reason,
                        {},
                    )
                    break

                round_id = self.store.create_round(
                    session_id,
                    round_index,
                    plan,
                    criteria_version_id=str(criteria.get("criteria_version_id") or ""),
                )
                used_queries.append(plan.query)
                used_query_signatures.append(self._plan_signature(plan))
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.SEARCH_PLAN.value,
                    "第{}轮搜索计划".format(round_index),
                    "{} / 职位栏：{}".format(
                        plan.query, plan.position_filter or "不限"
                    ),
                    {
                        **plan.to_dict(),
                        "criteria_version_id": str(
                            criteria.get("criteria_version_id") or ""
                        ),
                    },
                )
                self.store.save_decision(
                    session_id,
                    round_id,
                    "search_plan",
                    "run_search",
                    {"jd_text": jd_text[:800], "used_queries": used_queries[:-1]},
                    plan.to_dict(),
                    reason=plan.intent,
                    risk=plan.risk,
                )

                self.store.update_round(round_id, status=RoundStatus.SEARCHING.value)
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.SEARCH_EXECUTED.value,
                    "正在执行猎聘搜索",
                    "正在打开/操作猎聘搜索页，读取结果卡片。",
                    {"query": plan.query, "position_filter": plan.position_filter},
                )
                search_started = time.monotonic()
                try:
                    search_kwargs = {}
                    if "known_candidate_keys" in inspect.signature(
                        self.liepin_tool.run_search_round
                    ).parameters:
                        search_kwargs["known_candidate_keys"] = (
                            self.store.list_candidate_dedupe_keys(session_id)
                        )
                    raw_candidates = self.browser_queue.run(
                        self.liepin_tool.run_search_round,
                        session_id,
                        round_id,
                        plan,
                        cancel_event=cancel_event,
                        **search_kwargs,
                    )
                except Exception as exc:
                    if not self._is_no_results_error(exc):
                        raise
                    raw_candidates = []
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.SEARCH_EXECUTED.value,
                        "本轮无搜索结果",
                        "当前关键词未搜索到候选人，Agent 将复盘并尝试下一组关键词。",
                        {"duration_ms": int((time.monotonic() - search_started) * 1000)},
                    )
                self._respect_control_flags(session_id, cancel_event, pause_event)
                candidates = self._persist_round_candidates(
                    session_id,
                    round_id,
                    raw_candidates,
                    plan,
                    criteria,
                )
                self._respect_control_flags(session_id, cancel_event, pause_event)
                deduped_count = len({item.id for item in candidates})
                prequalified_count = sum(
                    1 for item in candidates if item.card_decision == "fetch"
                )
                self.store.update_round(
                    round_id,
                    status=RoundStatus.OBSERVED.value,
                    raw_count=len(raw_candidates),
                    deduped_count=deduped_count,
                    prequalified_count=prequalified_count,
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.SEARCH_EXECUTED.value,
                    "搜索完成",
                    "读取 {} 张候选人卡片，建议抓详情 {} 位。".format(
                        len(raw_candidates),
                        prequalified_count,
                    ),
                    {
                        "raw_count": len(raw_candidates),
                        "prequalified_count": prequalified_count,
                        "duration_ms": int((time.monotonic() - search_started) * 1000),
                    },
                )

                self._event(
                    session_id,
                    round_id,
                    AgentEventType.RESULT_OBSERVED.value,
                    "AI 正在观察结果池",
                    "正在调用模型分析本轮搜索质量、噪音和是否抓详情。",
                    {"candidate_count": len(candidates)},
                )
                page_meta = candidates[0].page_meta if candidates else {}
                observe_kwargs = {
                    "candidates": candidates,
                    "plan": plan,
                    "criteria": criteria,
                }
                if "page_meta" in inspect.signature(
                    self.brain.observe_round
                ).parameters:
                    observe_kwargs["page_meta"] = page_meta
                observation = self.brain.observe_round(**observe_kwargs)
                self._respect_control_flags(session_id, cancel_event, pause_event)
                if (
                    observation.recommended_round_type == RoundType.HARVEST_DETAIL.value
                    and float(
                        self.ranking_service.pool_summary(session_id).get(
                            "effective_pool_score", 0
                        )
                    )
                    < 2
                ):
                    observation.recommended_round_type = RoundType.VALIDATE_DETAIL.value
                    observation.reason += " 但当前还没有足够有效候选，先降级为验证轮。"
                self.store.update_round(
                    round_id, round_type=observation.recommended_round_type
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.RESULT_OBSERVED.value,
                    "结果池观察",
                    observation.reason,
                    {
                        **observation.to_dict(),
                        "prompt_metrics": dict(
                            getattr(self.brain, "last_prompt_metrics", {}).get(
                                "observe_round", {}
                            )
                        ),
                    },
                )

                remaining_budget = max(
                    0, max_detail_fetches - self.store.count_fetched_details(session_id)
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.DETAIL_DECISION.value,
                    "正在执行抓取策略",
                    "正在按必抓、验证、探索和跳过抽查分层选择候选人。",
                    {"remaining_detail_budget": remaining_budget},
                )
                decide_kwargs = {
                    "observation": observation,
                    "candidates": candidates,
                    "remaining_detail_budget": remaining_budget,
                }
                if "already_fetched_ids" in inspect.signature(
                    self.brain.decide_fetch
                ).parameters:
                    decide_kwargs["already_fetched_ids"] = sorted(
                        self.store.get_successful_detail_candidate_ids(
                            (item.id for item in candidates),
                            min_resume_chars=int(
                                getattr(self.config, "detail_min_resume_chars", 300)
                                or 300
                            ),
                        )
                    )
                decision = self.brain.decide_fetch(**decide_kwargs)
                self._respect_control_flags(session_id, cancel_event, pause_event)
                self.store.update_round(
                    round_id, status=RoundStatus.DETAIL_DECISION_MADE.value
                )
                self.store.save_decision(
                    session_id,
                    round_id,
                    "detail_decision",
                    decision.action,
                    observation.to_dict(),
                    decision.to_dict(),
                    reason=decision.reason,
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.DETAIL_DECISION.value,
                    "详情抓取决策",
                    decision.reason,
                    decision.to_dict(),
                )

                futures: List[Future] = []
                if decision.action == "fetch_details":
                    self.store.update_round(
                        round_id, status=RoundStatus.FETCHING_DETAILS.value
                    )
                    match_criteria = dict(criteria or {})
                    match_criteria["jd_text"] = jd_text
                    match_criteria["user_notes"] = user_notes
                    futures = self._fetch_and_match_candidates(
                        session_id,
                        round_id,
                        decision.candidate_ids,
                        match_criteria,
                        cancel_event,
                        pause_event,
                    )
                    self.store.update_round(
                        round_id,
                        status=RoundStatus.MATCHING.value,
                        detail_fetch_count=len(futures),
                    )
                    self._wait_for_policy(
                        futures,
                        decision.match_wait_policy,
                        cancel_event,
                        pause_event,
                        session_id,
                        round_id,
                    )
                    self._respect_control_flags(session_id, cancel_event, pause_event)
                else:
                    self.store.update_round(round_id, status=RoundStatus.SKIPPED.value)

                match_results = self.store.list_match_results(session_id, round_id)
                valid_match_results = [
                    item
                    for item in match_results
                    if str(item.get("status") or "") == "completed"
                ]
                fetched_count = self.store.count_fetched_details(session_id)
                rankings = self.store.list_current_rankings(
                    session_id, str(criteria.get("criteria_version_id") or "")
                )
                rankings_by_candidate = {
                    str(item.get("candidate_id") or ""): item for item in rankings
                }
                enriched_match_results: List[Dict[str, object]] = []
                for item in match_results:
                    enriched = dict(item)
                    ranking = rankings_by_candidate.get(
                        str(item.get("candidate_id") or ""), {}
                    )
                    for key in (
                        "recommendation_state",
                        "known_fit_score",
                        "potential_fit_score",
                        "evidence_coverage_score",
                        "conflict_count",
                    ):
                        enriched[key] = ranking.get(key)
                    enriched_match_results.append(enriched)
                round_state_counts: Dict[str, int] = {}
                for item in enriched_match_results:
                    state = str(item.get("recommendation_state") or "")
                    if state:
                        round_state_counts[state] = round_state_counts.get(state, 0) + 1
                round_viable_count = sum(
                    round_state_counts.get(state, 0)
                    for state in (
                        PRIORITY_CONTACT,
                        HIGH_POTENTIAL_VERIFY,
                        TRANSFERABLE_EXPLORE,
                    )
                )
                self.store.update_round(
                    round_id,
                    matched_count=len(match_results),
                    # Legacy column name retained in SQLite; value is now the
                    # five-state viable candidate count.
                    ab_count=round_viable_count,
                )
                effective_pool_score = float(
                    self.ranking_service.pool_summary(session_id).get(
                        "effective_pool_score"
                    )
                    or 0
                )
                pending_match_count = sum(1 for future in futures if not future.done())
                if (
                    decision.action == "fetch_details"
                    and valid_match_results
                    and round_viable_count == 0
                    and pending_match_count == 0
                ):
                    consecutive_low_yield_rounds += 1
                elif decision.action == "fetch_details" and round_viable_count > 0:
                    consecutive_low_yield_rounds = 0

                stop = evaluate_stop_conditions(
                    round_index=round_index,
                    max_rounds=max_rounds,
                    fetched_details=fetched_count,
                    max_detail_fetches=max_detail_fetches,
                    ab_count=effective_pool_score,
                    target_ab_count=target_ab_count,
                    consecutive_low_yield_rounds=consecutive_low_yield_rounds,
                    elapsed_minutes=(time.monotonic() - started_monotonic) / 60,
                    max_runtime_minutes=max_runtime_minutes,
                    low_yield_threshold=low_yield_threshold,
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.ROUND_REVIEW.value,
                    "AI 正在复盘本轮",
                    "正在调用模型综合匹配结果，决定下一轮搜索或停止。",
                    {"matched_count": len(match_results)},
                )
                review_criteria = dict(criteria or {})
                if pending_match_count:
                    pending_hint = (
                        "本轮仍有 {} 个匹配任务在后台执行，不能把当前暂时为空的有效候选池 "
                        "视为低产出，也不要据此停止或大幅改变策略。"
                    ).format(pending_match_count)
                    current_hint = str(review_criteria.get("_strategy_hint") or "").strip()
                    review_criteria["_strategy_hint"] = " ".join(
                        item for item in (current_hint, pending_hint) if item
                    )
                if (
                    plan.filters
                    and plan.filters.get("city")
                    and len(match_results) < 3
                ):
                    review_criteria["_strategy_hint"] = (
                        "当前搜索设置了城市限制（{}）但结果极少，"
                        "建议优先去掉 city 限制，保持搜索关键词不变，"
                        "扩大地理范围后再搜一轮。".format(
                            "、".join(str(c) for c in plan.filters.get("city") if c)
                        )
                    )
                review_kwargs = {
                    "previous_plan": plan,
                    "jd_text": jd_text,
                    "used_queries": used_queries,
                    "match_results": enriched_match_results,
                    "noise_patterns": observation.noise_patterns,
                    "target_met": effective_pool_score >= target_ab_count,
                    "should_stop": stop.should_stop,
                    "stop_reason": stop.reason,
                    "criteria": review_criteria,
                }
                if "used_query_signatures" in inspect.signature(
                    self.brain.review_round
                ).parameters:
                    review_kwargs["used_query_signatures"] = used_query_signatures
                if "round_digest" in inspect.signature(
                    self.brain.review_round
                ).parameters:
                    review_kwargs["round_digest"] = self.store.list_round_digests(
                        session_id
                    )
                review = self.brain.review_round(**review_kwargs)
                self._respect_control_flags(session_id, cancel_event, pause_event)
                observed_pages = sorted(
                    {
                        int(item.page_meta.get("page_num") or 1)
                        for item in candidates
                        if isinstance(item.page_meta, dict)
                    }
                )
                new_candidate_count = self.store.count_round_new_candidates(round_id)
                round_digest = {
                    "round_index": round_index,
                    "stage": decision.round_type,
                    "query": plan.query,
                    "search_hypothesis_type": plan.search_hypothesis_type,
                    "filters": dict(plan.filters or {}),
                    "page_count": max(observed_pages) if observed_pages else 0,
                    "raw_count": len(raw_candidates),
                    "new_count": new_candidate_count,
                    "duplicate_rate": round(
                        max(0, len(raw_candidates) - new_candidate_count)
                        / len(raw_candidates),
                        3,
                    )
                    if raw_candidates
                    else 0,
                    "selection_counts": {
                        key: len(value or [])
                        for key, value in (decision.selection_buckets or {}).items()
                    },
                    "detail_fetch_count": len(futures),
                    "matched_count": len(valid_match_results),
                    "pending_match_count": pending_match_count,
                    "recommendation_state_counts": round_state_counts,
                    "viable_count": round_viable_count,
                    "effective_pool_score": effective_pool_score,
                    "conclusion": review.summary,
                }
                self.store.record_search_hypothesis_result(
                    plan.search_hypothesis_id,
                    round_id=round_id,
                    page_count=round_digest["page_count"],
                    raw_count=round_digest["raw_count"],
                    new_count=round_digest["new_count"],
                    detail_count=round_digest["detail_fetch_count"],
                    relevant_count=round_digest["viable_count"],
                    duplicate_rate=round_digest["duplicate_rate"],
                )
                self.store.update_round(
                    round_id,
                    status=RoundStatus.REVIEWED.value,
                    round_digest=round_digest,
                    mark_finished=True,
                )
                # A no-wait future may have completed while the review snapshot
                # was being assembled. Reconcile the persisted digest once more
                # so resume context never carries a stale pending count.
                self._refresh_round_match_metrics(session_id, round_id)
                self.store.save_decision(
                    session_id,
                    round_id,
                    "round_review",
                    review.action,
                    {
                        "match_results": build_match_review_context(enriched_match_results),
                        "observation": observation.to_dict(),
                    },
                    review.to_dict(),
                    reason=review.summary,
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.ROUND_REVIEW.value,
                    "本轮复盘",
                    review.summary,
                    {
                        **review.to_dict(),
                        "prompt_metrics": dict(
                            getattr(self.brain, "last_prompt_metrics", {}).get(
                                "review_round", {}
                            )
                        ),
                        "round_digest": round_digest,
                    },
                )

                # Web Search 增强：联网查询补充情报
                web_search_max_per_session = 3
                should_web_search = (
                    review.action != "stop"
                    and review.next_plan
                    and self.web_search_tool.enabled
                    and self._web_search_count.get(session_id, 0) < web_search_max_per_session
                    and (
                        consecutive_low_yield_rounds >= 1
                        or (observation.noise_patterns and round_index >= 1)
                        or round_index == 1
                    )
                )
                if should_web_search:
                    self._web_search_count[session_id] = (
                        self._web_search_count.get(session_id, 0) + 1
                    )
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.ROUND_REVIEW.value,
                        "正在联网查询补充情报",
                        "Agent 正在通过搜索引擎获取行业情报辅助优化搜索策略。",
                        {"web_search_count": self._web_search_count[session_id]},
                    )
                    try:
                        custom_queries = self.brain.generate_web_search_queries(
                            jd_text=jd_text,
                            current_query=plan.query,
                            used_queries=used_queries,
                            noise_patterns=observation.noise_patterns,
                            match_results=match_results,
                            criteria=criteria,
                        )
                        self._event(
                            session_id,
                            round_id,
                            AgentEventType.ROUND_REVIEW.value,
                            "LLM 生成联网查询",
                            "",
                            {"custom_queries": custom_queries},
                        )
                        intel = self.web_search_tool.gather_intelligence(
                            jd_text=jd_text,
                            current_query=plan.query,
                            used_queries=used_queries,
                            noise_patterns=observation.noise_patterns,
                            custom_queries=custom_queries if custom_queries else None,
                        )
                        if intel.summary:
                            enhanced_plan = self.brain.enhance_plan_with_web_search(
                                current_plan=review.next_plan,
                                jd_text=jd_text,
                                used_queries=used_queries,
                                noise_patterns=observation.noise_patterns,
                                web_search_intel=intel.to_dict(),
                                criteria=criteria,
                            )
                            if enhanced_plan:
                                self._event(
                                    session_id,
                                    round_id,
                                    AgentEventType.ROUND_REVIEW.value,
                                    "已根据联网情报修正搜索策略",
                                    enhanced_plan.intent
                                    or "搜索方向已根据外部情报调整。",
                                    {
                                        "original_plan": review.next_plan.to_dict(),
                                        "enhanced_plan": enhanced_plan.to_dict(),
                                        "intel_summary": intel.summary[:300],
                                    },
                                )
                                review.next_plan = enhanced_plan
                    except Exception as exc:
                        logger.warning("Web search enhancement failed: %s", exc)
                        self._event(
                            session_id,
                            round_id,
                            AgentEventType.ERROR.value,
                            "联网查询失败",
                            str(exc),
                            {},
                        )

                if review.action == "stop" or not review.next_plan:
                    break
                plan = review.next_plan
                portfolio_plan = self.store.select_search_hypothesis_plan(
                    session_id, str(criteria.get("criteria_version_id") or "")
                )
                if portfolio_plan is not None:
                    plan = portfolio_plan

                user_cmd = self.store.consume_pending_user_command(session_id)
                if user_cmd:
                    adjusted = self.brain.apply_user_command(
                        user_cmd, plan, criteria
                    )
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.JOB_UNDERSTANDING.value,
                        "已采纳用户指令",
                        user_cmd,
                        {
                            "original_query": plan.query,
                            "adjusted_query": adjusted.query,
                        },
                    )
                    plan = adjusted

                if run_mode in {"单步", "监督"} and round_index < max_rounds:
                    self.store.update_session_status(
                        session_id,
                        SessionStatus.WAITING_APPROVAL.value,
                        "{}模式：本轮已完成，点击继续后执行下一轮。".format(run_mode),
                    )
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.ROUND_REVIEW.value,
                        "等待人工确认",
                        "{}模式下已暂停，确认后可继续下一轮。".format(run_mode),
                        {"mode": run_mode, "next_plan": plan.to_dict()},
                    )
                    self._notify(session_id)
                    return

            if (
                cancel_event.is_set()
                or self._session_status(session_id) == SessionStatus.CANCELLED.value
            ):
                self.store.update_session_status(
                    session_id, SessionStatus.CANCELLED.value
                )
                self._notify(session_id)
                return
            self._drain_session_matches(
                session_id,
                cancel_event,
                pause_event,
            )
            self.store.update_session_status(session_id, SessionStatus.COMPLETED.value)
            self._event(
                session_id,
                None,
                AgentEventType.SESSION_COMPLETED.value,
                "寻访完成",
                "Agent 已完成当前 Session。",
                {
                    "effective_pool_score": self.ranking_service.pool_summary(
                        session_id
                    ).get("effective_pool_score", 0),
                    "detail_count": self.store.count_fetched_details(session_id),
                },
            )
            self._notify(session_id)
        except RuntimeError as exc:
            if "取消" in str(exc):
                self.store.update_session_status(
                    session_id, SessionStatus.CANCELLED.value
                )
            else:
                self._fail_session(session_id, exc)
        except Exception as exc:
            self._fail_session(session_id, exc)

    def _persist_round_candidates(
        self,
        session_id: str,
        round_id: str,
        raw_candidates: List[CandidateSummary],
        plan: SearchPlan,
        criteria: Dict[str, object],
    ) -> List[CandidateSummary]:
        saved = []
        for candidate in raw_candidates:
            candidate.session_id = session_id
            candidate.round_id = round_id
            score, reasons = pre_score_candidate(
                candidate,
                expected_terms=plan.expected_signal,
                position_filter=plan.position_filter,
            )
            decision, signals, risks, reason = classify_candidate_card(
                candidate,
                expected_terms=plan.expected_signal
                or criteria.get("core_terms", []),
                position_filter=plan.position_filter,
                negative_terms=criteria.get("negative_terms", []),
            )
            candidate.pre_score = score
            candidate.pre_score_reasons = reasons
            candidate.card_decision = decision
            candidate.card_signals = signals
            candidate.card_risks = risks
            candidate.card_reason = reason
            candidate.status = CandidateStatus.PRE_SCORED.value
            candidate.id = self.store.save_candidate_summary(candidate)
            self.store.save_candidate_source(
                candidate_id=candidate.id,
                session_id=session_id,
                round_id=round_id,
                criteria_version_id=str(criteria.get("criteria_version_id") or ""),
                plan=plan,
                result_index=candidate.result_index,
                card_decision=decision,
                card_signals=signals,
                card_risks=risks,
            )
            saved.append(candidate)
        self._notify(session_id)
        return saved

    @staticmethod
    def _plan_from_round(row: Dict[str, object]) -> SearchPlan:
        from ..storage.sqlite_store import from_json

        return SearchPlan(
            query=str(row.get("query") or ""),
            position_filter=str(row.get("position_filter") or ""),
            scope=str(row.get("scope") or "全部经历"),
            match_mode=str(row.get("match_mode") or "all"),
            filters=from_json(row.get("filters_json"), {}) or {},
            intent=str(row.get("intent") or ""),
            expected_signal=[],
            risk="",
            search_hypothesis_type=str(
                row.get("search_hypothesis_type") or "core_background"
            ),
            search_hypothesis_text=str(row.get("search_hypothesis_text") or ""),
            search_hypothesis_id=str(row.get("search_hypothesis_id") or ""),
        )

    @staticmethod
    def _query_signature_from_round(row: Dict[str, object]) -> str:
        return LLMAgentBrain._plan_signature(AgentRuntime._plan_from_round(row))

    @staticmethod
    def _plan_signature(plan: SearchPlan) -> str:
        return LLMAgentBrain._plan_signature(plan)

    def _fetch_and_match_candidates(
        self,
        session_id: str,
        round_id: str,
        candidate_ids: List[str],
        criteria: Dict[str, object],
        cancel_event: threading.Event,
        pause_event: threading.Event,
    ) -> List[Future]:
        futures: List[Future] = []
        unique_candidate_ids = list(dict.fromkeys(item for item in candidate_ids if item))
        candidates = self.store.get_candidates_by_ids(unique_candidate_ids)
        criteria_version_id = str(criteria.get("criteria_version_id") or "")
        cache_identity = getattr(self.matcher, "cache_identity", {})
        if not isinstance(cache_identity, dict):
            cache_identity = {}
        detail_min_chars = int(
            getattr(self.config, "detail_min_resume_chars", 300) or 300
        )
        for candidate in candidates:
            self._respect_control_flags(session_id, cancel_event, pause_event)
            candidate_id = str(candidate.get("id"))
            match_key = (candidate_id, criteria_version_id)
            if not self._reserve_match_key(match_key):
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.MATCH_RESULT.value,
                    "跳过重复任务",
                    "该候选人的当前基准匹配已在后台执行。",
                    {"candidate_id": candidate_id},
                )
                continue

            future_submitted = False
            try:
                stored_detail = self.store.get_successful_candidate_detail(
                    candidate_id, min_resume_chars=detail_min_chars
                )
                if stored_detail:
                    resume_text = str(stored_detail.get("resume_text") or "")
                    detail_payload = self._json_dict(
                        stored_detail.get("raw_payload_json")
                    )
                    capture_status = str(
                        stored_detail.get("capture_status") or "success"
                    )
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.DETAIL_FETCHED.value,
                        "复用已抓取详情",
                        "该候选人已有完整详情，本轮不再重复打开页面。",
                        {"candidate_id": candidate_id, "reused": True},
                    )
                else:
                    self.store.update_candidate_status(
                        candidate_id, CandidateStatus.DETAIL_FETCHING.value
                    )
                    detail_started = time.monotonic()
                    try:
                        detail = self.browser_queue.run(
                            self.liepin_tool.fetch_candidate_detail,
                            candidate,
                            cancel_event=cancel_event,
                        )
                    except Exception as exc:
                        detail = CandidateDetail(
                            candidate_id=candidate_id,
                            resume_text="",
                            resume_summary="",
                            capture_status="failed",
                            error_message=str(exc),
                        )
                        self.store.save_candidate_detail(detail)
                        self._event(
                            session_id,
                            round_id,
                            AgentEventType.ERROR.value,
                            "简历详情抓取失败",
                            "{}：{}".format(candidate.get("name") or "候选人", exc),
                            {
                                "candidate_id": candidate_id,
                                "duration_ms": int(
                                    (time.monotonic() - detail_started) * 1000
                                ),
                            },
                        )
                        continue

                    profile_url = ""
                    if isinstance(detail.raw_payload, dict):
                        profile_url = str(detail.raw_payload.get("profile_url") or "")
                    if profile_url:
                        self.store.update_candidate_profile_url(candidate_id, profile_url)
                    resume_text = str(detail.resume_text or "")
                    detail_payload = dict(detail.raw_payload or {})
                    if (
                        detail.capture_status == "success"
                        and len(resume_text.strip()) < detail_min_chars
                    ):
                        detail.capture_status = "partial"
                        detail.error_message = (
                            "详情正文仅 {} 字，低于自动匹配门槛 {} 字"
                        ).format(len(resume_text.strip()), detail_min_chars)
                    capture_status = detail.capture_status
                    self.store.save_candidate_detail(detail)
                    if detail.capture_status != "success" or not resume_text.strip():
                        self._event(
                            session_id,
                            round_id,
                            AgentEventType.ERROR.value,
                            "简历详情不完整",
                            "详情信息不足，已保留记录但不会进入自动匹配。",
                            {
                                "candidate_id": candidate_id,
                                "capture_status": detail.capture_status,
                                "resume_chars": len(resume_text.strip()),
                            },
                        )
                        continue
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.DETAIL_FETCHED.value,
                        "简历详情已抓取",
                        "{} / {}".format(
                            candidate.get("name") or "候选人",
                            candidate.get("current_title") or "",
                        ),
                        {
                            "candidate_id": candidate_id,
                            "reused": False,
                            "extract_attempts": int(
                                detail_payload.get("detail_extract_attempts") or 1
                            ),
                        },
                    )

                prompt_version = str(
                    cache_identity.get("prompt_version") or ""
                )
                model_config_hash = str(
                    cache_identity.get("model_config_hash") or ""
                )
                strict_cache_identity = bool(
                    prompt_version or model_config_hash
                )
                resume_hash = (
                    hashlib.sha256(
                        resume_text.strip().encode("utf-8")
                    ).hexdigest()
                    if strict_cache_identity
                    else ""
                )
                existing_match = self.store.find_match_result(
                    candidate_id,
                    criteria_version_id,
                    statuses=("completed", "needs_review"),
                    prompt_version=prompt_version,
                    model_config_hash=model_config_hash,
                    resume_hash=resume_hash,
                )
                if existing_match:
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.MATCH_RESULT.value,
                        "跳过重复匹配",
                        "该候选人的简历、寻访基准和匹配配置均未变化，直接复用已有结果。",
                        {
                            "candidate_id": candidate_id,
                            "criteria_version_id": criteria_version_id,
                            "cached_status": existing_match.get("status") or "",
                            "cached_tier": existing_match.get("tier") or "",
                            "resume_hash": resume_hash,
                        },
                    )
                    continue

                self.store.update_candidate_status(
                    candidate_id, CandidateStatus.MATCH_QUEUED.value
                )
                structured_facts, capture_quality = self._detail_match_context(
                    detail_payload,
                    resume_text,
                    capture_status,
                )
                # 匹配并发控制：未完成的匹配任务不超过阈值，超出的放进下一批次。
                # 真正的限流由 LLMClient 的 RPM 令牌桶负责（实测 5 RPM），
                # 这里只控制同时在跑的任务数，避免堆积。默认 3（可在 config 配 match_concurrency_limit）。
                active_count = sum(1 for f in futures if not f.done())
                concurrency_limit = int(
                    getattr(self.config, "match_concurrency_limit", None) or 3
                )
                if active_count >= concurrency_limit:
                    self._event(
                        session_id,
                        round_id,
                        AgentEventType.MATCH_RESULT.value,
                        "匹配批次控制",
                        "当前有 {} 个匹配任务在进行中，等待空位后再提交下一批。".format(
                            active_count
                        ),
                        {"active_matches": active_count, "total_queued": len(futures)},
                    )
                while active_count >= concurrency_limit:
                    self._respect_control_flags(session_id, cancel_event, pause_event)
                    time.sleep(0.3)
                    active_count = sum(1 for f in futures if not f.done())
                future = self.match_queue.submit(
                    self._match_and_persist,
                    session_id,
                    round_id,
                    candidate_id,
                    resume_text,
                    criteria,
                    cancel_event,
                    pause_event,
                    structured_facts,
                    capture_quality,
                )
                self._session_match_futures.setdefault(session_id, []).append(future)
                self._round_match_futures.setdefault(
                    (session_id, round_id), []
                ).append(future)
                future.add_done_callback(
                    lambda _future,
                    key=match_key,
                    sid=session_id,
                    rid=round_id: self._on_match_future_done(
                        sid, rid, key
                    )
                )
                futures.append(future)
                future_submitted = True
            finally:
                if not future_submitted:
                    self._release_match_key(match_key)
        self._notify(session_id)
        return futures

    def _reserve_match_key(self, key: Tuple[str, str]) -> bool:
        """Reserve a candidate/criteria pair so concurrent rounds cannot duplicate it."""
        with self._pending_match_keys_lock:
            if key in self._pending_match_keys:
                return False
            self._pending_match_keys.add(key)
            return True

    def _release_match_key(self, key: Tuple[str, str]) -> None:
        with self._pending_match_keys_lock:
            self._pending_match_keys.discard(key)

    def _on_match_future_done(
        self,
        session_id: str,
        round_id: str,
        match_key: Tuple[str, str],
    ) -> None:
        self._release_match_key(match_key)
        try:
            self._refresh_round_match_metrics(session_id, round_id)
        except Exception:
            logger.exception(
                "failed to reconcile round metrics after match completion: %s/%s",
                session_id,
                round_id,
            )

    @staticmethod
    def _json_dict(value: object) -> Dict[str, object]:
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}

    @classmethod
    def _detail_match_context(
        cls,
        raw_payload: Dict[str, object],
        resume_text: str,
        capture_status: str,
    ) -> Tuple[Dict[str, object], Dict[str, object]]:
        payload = dict(raw_payload or {})
        extracted_payload = cls._json_dict(payload.get("raw_payload_json"))
        structured = payload.get("structured_facts")
        if not isinstance(structured, dict):
            structured = extracted_payload.get("_structured")
        if not isinstance(structured, dict):
            structured = {}

        section_names = (
            "basic_info",
            "summary",
            "experience",
            "projects",
            "education",
            "job_intention",
        )
        present_sections = [
            name
            for name in section_names
            if isinstance(extracted_payload.get(name), list)
            and bool(extracted_payload.get(name))
        ]
        quality = {
            "capture_status": capture_status or "",
            "resume_chars": len((resume_text or "").strip()),
            "present_sections": present_sections,
            "missing_sections": [
                name for name in section_names if name not in present_sections
            ],
        }
        return dict(structured), quality

    def _drain_session_matches(
        self,
        session_id: str,
        cancel_event: threading.Event,
        pause_event: threading.Event,
    ) -> None:
        """Settle background matches before a session is declared completed."""
        futures = list(self._session_match_futures.get(session_id, []))
        pending = [future for future in futures if not future.done()]
        if not pending:
            self._finalize_session_match_tracking(session_id)
            return
        timeout_seconds = int(
            getattr(self.config, "match_wait_timeout_seconds", 300) or 300
        )
        self._event(
            session_id,
            None,
            AgentEventType.MATCH_RESULT.value,
            "正在收拢后台匹配",
            "搜索已结束，等待剩余 {} 个候选人匹配完成后再结束任务。".format(
                len(pending)
            ),
            {"pending_count": len(pending), "timeout_seconds": timeout_seconds},
        )
        self._wait_for_policy(
            pending,
            {"mode": "wait_all", "timeout_seconds": timeout_seconds},
            cancel_event,
            pause_event,
            session_id,
            None,
        )
        self._respect_control_flags(session_id, cancel_event, pause_event)
        remaining = [future for future in pending if not future.done()]
        if remaining:
            raise RuntimeError(
                "仍有 {} 个后台匹配任务超时未完成，任务不会被误标为已完成。".format(
                    len(remaining)
                )
            )
        self._finalize_session_match_tracking(session_id)

    def _finalize_session_match_tracking(self, session_id: str) -> None:
        round_keys = [
            key for key in self._round_match_futures if key[0] == session_id
        ]
        for _, round_id in round_keys:
            self._refresh_round_match_metrics(session_id, round_id)
            self._round_match_futures.pop((session_id, round_id), None)
        self._session_match_futures.pop(session_id, None)

    def _match_and_persist(
        self,
        session_id: str,
        round_id: str,
        candidate_id: str,
        resume_text: str,
        criteria: Dict[str, object],
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        structured_facts: Optional[Dict[str, object]] = None,
        capture_quality: Optional[Dict[str, object]] = None,
    ) -> MatchResult:
        logger.info(
            "_match_and_persist START: session=%s round=%s candidate=%s thread=%s",
            session_id,
            round_id,
            candidate_id,
            threading.current_thread().name,
        )
        if not self._session_allows_background_write(session_id):
            logger.warning("_match_and_persist: session %s cancelled, aborting", session_id)
            raise RuntimeError("任务已取消，跳过后台匹配写入")
        self.store.update_candidate_status(candidate_id, CandidateStatus.MATCHING.value)
        match_started = time.monotonic()
        try:
            logger.info("_match_and_persist: calling match_candidate for %s", candidate_id)
            match_kwargs = {
                "session_id": session_id,
                "round_id": round_id,
                "candidate_id": candidate_id,
                "resume_text": resume_text,
                "criteria": criteria,
            }
            matcher_parameters = inspect.signature(
                self.matcher.match_candidate
            ).parameters
            if "structured_facts" in matcher_parameters:
                match_kwargs["structured_facts"] = structured_facts or {}
            if "capture_quality" in matcher_parameters:
                match_kwargs["capture_quality"] = capture_quality or {}
            result = self.matcher.match_candidate(**match_kwargs)
            logger.info(
                "_match_and_persist: match_candidate OK for %s in %.1fs tier=%s",
                candidate_id,
                time.monotonic() - match_started,
                result.tier,
            )
        except Exception as exc:
            logger.exception(
                "_match_and_persist: match_candidate FAILED for %s after %.1fs: %s",
                candidate_id,
                time.monotonic() - match_started,
                exc,
            )
            if not self._session_allows_background_write(session_id):
                raise
            result = MatchResult(
                candidate_id=candidate_id,
                session_id=session_id,
                round_id=round_id,
                tier="",
                summary="匹配失败，需人工复核。",
                risks=str(exc),
                recommendation="人工复核后再决定是否推进。",
                detail=str(exc),
                status="failed",
                criteria_version_id=str(criteria.get("criteria_version_id") or ""),
                missing_or_unclear=["模型匹配任务失败"],
                questions_to_verify=["请人工复核该候选人与岗位要求的匹配度"],
                confidence="low",
            )
            logger.info("_match_and_persist: saving failed match result for %s", candidate_id)
            self.store.save_match_result(result)
            self._refresh_round_match_metrics(session_id, round_id)
            self._event(
                session_id,
                round_id,
                AgentEventType.ERROR.value,
                "候选人匹配失败",
                str(exc),
                {"candidate_id": candidate_id},
            )
            self._notify(session_id)
            logger.info("_match_and_persist DONE (failed): %s", candidate_id)
            return result
        if not self._session_allows_background_write(session_id):
            logger.warning("_match_and_persist: session %s cancelled after match, aborting save", session_id)
            raise RuntimeError("任务已取消，跳过后台匹配写入")
        logger.info(
            "_match_and_persist: saving match result for %s status=%s tier=%s",
            candidate_id,
            result.status,
            result.tier,
        )
        self.store.save_match_result(result)
        try:
            facts = self.candidate_intelligence.extract_facts(
                resume_text, structured_facts or {}
            )
            self.store.replace_candidate_facts(candidate_id, facts)
            criteria_items = list(criteria.get("criteria_items") or [])
            if criteria_items:
                evaluations = self.candidate_intelligence.evaluate(
                    criteria_items, facts, result
                )
                self.store.replace_criterion_evaluations(
                    candidate_id,
                    session_id,
                    str(criteria.get("criteria_version_id") or ""),
                    evaluations,
                )
        except Exception:
            logger.exception(
                "Failed to persist candidate intelligence: candidate=%s", candidate_id
            )
        try:
            self.ranking_service.refresh_session(session_id)
        except Exception:
            logger.exception("Failed to refresh candidate ranking: session=%s", session_id)
        self._refresh_round_match_metrics(session_id, round_id)
        if result.status == "completed":
            self._event(
                session_id,
                round_id,
                AgentEventType.MATCH_RESULT.value,
                "匹配完成：{}档".format(result.tier),
                result.summary,
                {
                    "candidate_id": candidate_id,
                    "tier": result.tier,
                    "recommendation": result.recommendation,
                },
            )
        elif result.status == "needs_review":
            self._event(
                session_id,
                round_id,
                AgentEventType.MATCH_RESULT.value,
                "匹配结果待人工复核",
                result.summary or "模型结果证据不足，未生成业务档位。",
                {
                    "candidate_id": candidate_id,
                    "status": result.status,
                    "questions": list(result.questions_to_verify or []),
                },
            )
        else:
            self._event(
                session_id,
                round_id,
                AgentEventType.ERROR.value,
                "候选人匹配失败",
                result.risks or result.summary or "模型调用失败。",
                {"candidate_id": candidate_id, "status": result.status},
            )
        self._notify(session_id)
        logger.info("_match_and_persist DONE (success): %s", candidate_id)
        return result

    def _wait_for_policy(
        self,
        futures: List[Future],
        policy: Dict[str, object],
        cancel_event: threading.Event,
        pause_event: threading.Event,
        session_id: str,
        round_id: Optional[str] = None,
    ) -> None:
        mode = str((policy or {}).get("mode") or "no_wait")
        if not futures:
            logger.info("_wait_for_policy: no futures to wait for")
            return
        if mode == "no_wait":
            logger.info("_wait_for_policy: mode=no_wait, %s futures queued", len(futures))
            self._event(
                session_id,
                round_id,
                AgentEventType.MATCH_RESULT.value,
                "后台匹配已提交",
                "本轮匹配将在后台继续完成，不阻塞后续搜索和复盘。",
                {"policy": mode, "queued_count": len(futures)},
            )
            return
        timeout_seconds = int(policy.get("timeout_seconds") or 600)
        if mode == "wait_min_results":
            min_results = int(policy.get("min_results") or 1)
            min_results = max(1, min(min_results, len(futures)))
        else:
            mode = "wait_all"
            min_results = len(futures)
        logger.info(
            "_wait_for_policy: mode=%s min_results=%s timeout=%ss futures=%s",
            mode,
            min_results,
            timeout_seconds,
            len(futures),
        )
        deadline = time.time() + max(1, timeout_seconds)
        report_interval = 5.0
        last_report = time.time()
        loop_count = 0
        while time.time() < deadline:
            loop_count += 1
            if cancel_event.is_set():
                logger.warning("_wait_for_policy: cancel_event set, cancelling futures")
                for future in futures:
                    if not future.done():
                        future.cancel()
                break
            self._respect_control_flags(session_id, cancel_event, pause_event)
            completed = sum(1 for item in futures if item.done())
            if completed >= min_results or completed >= len(futures):
                logger.info("_wait_for_policy: target reached %s/%s", completed, len(futures))
                break
            if time.time() - last_report >= report_interval:
                logger.info(
                    "_wait_for_policy: waiting %s/%s done (%.0fs left, %s loops)",
                    completed,
                    len(futures),
                    deadline - time.time(),
                    loop_count,
                )
                last_report = time.time()
            time.sleep(0.2)
        completed = sum(1 for item in futures if item.done())
        logger.info(
            "_wait_for_policy: finished %s/%s completed after %s loops",
            completed,
            len(futures),
            loop_count,
        )
        self._event(
            session_id,
            round_id,
            AgentEventType.MATCH_RESULT.value,
            "匹配等待完成",
            "等待策略 {} 已完成 {}/{} 个匹配任务。".format(
                mode, completed, len(futures)
            ),
            {
                "policy": mode,
                "completed_count": completed,
                "queued_count": len(futures),
                "min_results": min_results,
                "timeout_seconds": timeout_seconds,
            },
        )
        for future in futures:
            if not future.done():
                continue
            try:
                future.result()
            except Exception as exc:
                if "取消" in str(exc):
                    continue
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.ERROR.value,
                    "后台匹配任务异常",
                    str(exc),
                    {},
                )

    def _refresh_round_match_metrics(self, session_id: str, round_id: str) -> None:
        match_results = self.store.list_match_results(session_id, round_id)
        completed_results = [
            item
            for item in match_results
            if str(item.get("status") or "") == "completed"
        ]
        update_kwargs: Dict[str, object] = {
            "matched_count": len(match_results),
        }
        round_row = next(
            (
                item
                for item in self.store.list_rounds(session_id)
                if str(item.get("id") or "") == round_id
            ),
            None,
        )
        digest = self._json_dict((round_row or {}).get("round_digest_json"))
        if digest:
            rankings = self.store.list_current_rankings(
                session_id, str((round_row or {}).get("criteria_version_id") or "")
            )
            matched_candidate_ids = {
                str(item.get("candidate_id") or "") for item in completed_results
            }
            state_counts: Dict[str, int] = {}
            for item in rankings:
                if str(item.get("candidate_id") or "") not in matched_candidate_ids:
                    continue
                state = str(item.get("recommendation_state") or "")
                if state:
                    state_counts[state] = state_counts.get(state, 0) + 1
            viable_count = sum(
                state_counts.get(state, 0)
                for state in (
                    PRIORITY_CONTACT,
                    HIGH_POTENTIAL_VERIFY,
                    TRANSFERABLE_EXPLORE,
                )
            )
            update_kwargs["ab_count"] = viable_count
            pending_count = sum(
                1
                for future in self._round_match_futures.get(
                    (session_id, round_id), []
                )
                if not future.done()
            )
            digest.update(
                {
                    "matched_count": len(completed_results),
                    "pending_match_count": pending_count,
                    "recommendation_state_counts": state_counts,
                    "viable_count": viable_count,
                    "effective_pool_score": self.ranking_service.pool_summary(
                        session_id
                    ).get("effective_pool_score", 0),
                    "needs_review_count": sum(
                        1
                        for item in match_results
                        if str(item.get("status") or "") == "needs_review"
                    ),
                    "failed_count": sum(
                        1
                        for item in match_results
                        if str(item.get("status") or "") == "failed"
                    ),
                }
            )
            update_kwargs["round_digest"] = digest
        self.store.update_round(round_id, **update_kwargs)

    def _session_status(self, session_id: str) -> str:
        session = self.store.get_session(session_id) or {}
        return str(session.get("status") or "")

    def _session_allows_background_write(self, session_id: str) -> bool:
        status = self._session_status(session_id)
        return bool(status) and status not in {
            SessionStatus.CANCELLED.value,
            SessionStatus.FAILED.value,
        }

    @staticmethod
    def _is_no_results_error(exc: Exception) -> bool:
        name = exc.__class__.__name__
        text = str(exc)
        return (
            name == "LiepinSearchNoResultsError"
            or "未搜索到候选人" in text
            or "没找到相关匹配项" in text
            or "没有找到符合条件" in text
            or "暂无匹配结果" in text
        )

    def _respect_control_flags(
        self,
        session_id: str,
        cancel_event: threading.Event,
        pause_event: threading.Event,
    ) -> None:
        if (
            cancel_event.is_set()
            or self._session_status(session_id) == SessionStatus.CANCELLED.value
        ):
            raise RuntimeError("用户已取消任务")
        while pause_event.is_set():
            self.store.update_session_status(session_id, SessionStatus.PAUSED.value)
            self._notify(session_id)
            time.sleep(0.3)
            if cancel_event.is_set():
                raise RuntimeError("用户已取消任务")
        current = self.store.get_session(session_id)
        if current and current.get("status") == SessionStatus.PAUSED.value:
            self.store.update_session_status(session_id, SessionStatus.RUNNING.value)
            self._notify(session_id)

    def _event(
        self,
        session_id: str,
        round_id: Optional[str],
        event_type: str,
        title: str,
        message: str,
        payload: Dict[str, object],
    ) -> None:
        self.store.add_event(session_id, round_id, event_type, title, message, payload)
        self.event_bus.publish(
            "event_added", {"session_id": session_id, "event_type": event_type}
        )

    def _notify(self, session_id: str) -> None:
        self.event_bus.publish("session_updated", {"session_id": session_id})

    def _fail_session(self, session_id: str, exc: Exception) -> None:
        if self._session_status(session_id) == SessionStatus.CANCELLED.value:
            self._notify(session_id)
            return
        self.store.update_session_status(
            session_id, SessionStatus.FAILED.value, str(exc)
        )
        self._event(
            session_id,
            None,
            AgentEventType.ERROR.value,
            "任务失败",
            str(exc),
            {},
        )
        self._notify(session_id)
