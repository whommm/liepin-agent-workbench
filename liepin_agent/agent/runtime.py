"""Main Agent runtime orchestration."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from typing import Dict, List, Optional

from ..domain.models import CandidateDetail, CandidateSummary, MatchResult, SearchPlan
from ..domain.pre_score import classify_candidate_card, pre_score_candidate
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
from ..storage.sqlite_store import SQLiteStore
from ..tools.real_liepin import RealLiepinTool
from ..tools.real_matcher import RealMatchService
from .brain import LLMAgentBrain


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
    ):
        self.store = store
        self.event_bus = event_bus or EventBus()
        self.browser_queue = browser_queue or BrowserQueue()
        self.match_queue = match_queue or MatchQueue(max_workers=3)
        self.liepin_tool = liepin_tool or RealLiepinTool()
        self.matcher = matcher or RealMatchService.from_config()
        self.brain = agent_brain or LLMAgentBrain.from_config()
        self._threads: Dict[str, threading.Thread] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._pause_events: Dict[str, threading.Event] = {}

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

            used_queries: List[str] = [
                str(item.get("query") or "")
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
                recovery_review = self.brain.review_round(
                    previous_plan=previous_plan,
                    jd_text=jd_text,
                    used_queries=used_queries,
                    match_results=match_results,
                    noise_patterns=[],
                    target_met=False,
                    should_stop=False,
                    stop_reason="",
                )
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
            consecutive_low_yield_rounds = 0
            max_rounds = int(session.get("max_rounds") or 6)
            max_detail_fetches = int(session.get("max_detail_fetches") or 50)
            target_ab_count = int(session.get("target_ab_count") or 10)
            max_runtime_minutes = int(session.get("max_runtime_minutes") or 0)
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
                ab_count = self.store.count_ab_matches(session_id)
                stop = evaluate_stop_conditions(
                    round_index=round_index - 1,
                    max_rounds=max_rounds,
                    fetched_details=fetched_count,
                    max_detail_fetches=max_detail_fetches,
                    ab_count=ab_count,
                    target_ab_count=target_ab_count,
                    consecutive_low_yield_rounds=consecutive_low_yield_rounds,
                    elapsed_minutes=(time.monotonic() - started_monotonic) / 60,
                    max_runtime_minutes=max_runtime_minutes,
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
                    raw_candidates = self.browser_queue.run(
                        self.liepin_tool.run_search_round,
                        session_id,
                        round_id,
                        plan,
                        cancel_event=cancel_event,
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
                observation = self.brain.observe_round(
                    candidates=candidates, plan=plan, criteria=criteria
                )
                self._respect_control_flags(session_id, cancel_event, pause_event)
                if (
                    observation.recommended_round_type == RoundType.HARVEST_DETAIL.value
                    and self.store.count_ab_matches(session_id) < 2
                ):
                    observation.recommended_round_type = RoundType.VALIDATE_DETAIL.value
                    observation.reason += " 但当前还没有足够 A/B 样本，先降级为验证轮。"
                self.store.update_round(
                    round_id, round_type=observation.recommended_round_type
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.RESULT_OBSERVED.value,
                    "结果池观察",
                    observation.reason,
                    observation.to_dict(),
                )

                remaining_budget = max(
                    0, max_detail_fetches - self.store.count_fetched_details(session_id)
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.DETAIL_DECISION.value,
                    "AI 正在决定抓取策略",
                    "正在调用模型决定跳过、抽样、验证或收割，并选择候选人。",
                    {"remaining_detail_budget": remaining_budget},
                )
                decision = self.brain.decide_fetch(
                    observation, candidates, remaining_budget
                )
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
                    futures = self._fetch_and_match_candidates(
                        session_id,
                        round_id,
                        decision.candidate_ids,
                        criteria,
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
                    )
                else:
                    self.store.update_round(round_id, status=RoundStatus.SKIPPED.value)

                match_results = self.store.list_match_results(session_id, round_id)
                round_ab_count = sum(
                    1
                    for item in match_results
                    if str(item.get("tier") or "").upper() in ("A", "B")
                )
                self.store.update_round(
                    round_id,
                    matched_count=len(match_results),
                    ab_count=round_ab_count,
                )

                fetched_count = self.store.count_fetched_details(session_id)
                total_ab_count = self.store.count_ab_matches(session_id)
                if decision.action == "fetch_details" and round_ab_count == 0:
                    consecutive_low_yield_rounds += 1
                elif decision.action == "fetch_details":
                    consecutive_low_yield_rounds = 0

                stop = evaluate_stop_conditions(
                    round_index=round_index,
                    max_rounds=max_rounds,
                    fetched_details=fetched_count,
                    max_detail_fetches=max_detail_fetches,
                    ab_count=total_ab_count,
                    target_ab_count=target_ab_count,
                    consecutive_low_yield_rounds=consecutive_low_yield_rounds,
                    elapsed_minutes=(time.monotonic() - started_monotonic) / 60,
                    max_runtime_minutes=max_runtime_minutes,
                )
                self._event(
                    session_id,
                    round_id,
                    AgentEventType.ROUND_REVIEW.value,
                    "AI 正在复盘本轮",
                    "正在调用模型综合匹配结果，决定下一轮搜索或停止。",
                    {"matched_count": len(match_results)},
                )
                review = self.brain.review_round(
                    previous_plan=plan,
                    jd_text=jd_text,
                    used_queries=used_queries,
                    match_results=match_results,
                    noise_patterns=observation.noise_patterns,
                    target_met=total_ab_count >= target_ab_count,
                    should_stop=stop.should_stop,
                    stop_reason=stop.reason,
                )
                self._respect_control_flags(session_id, cancel_event, pause_event)
                self.store.update_round(
                    round_id,
                    status=RoundStatus.REVIEWED.value,
                    mark_finished=True,
                )
                self.store.save_decision(
                    session_id,
                    round_id,
                    "round_review",
                    review.action,
                    {
                        "match_results": match_results,
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
                    review.to_dict(),
                )

                if review.action == "stop" or not review.next_plan:
                    break
                plan = review.next_plan

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
            self.store.update_session_status(session_id, SessionStatus.COMPLETED.value)
            self._event(
                session_id,
                None,
                AgentEventType.SESSION_COMPLETED.value,
                "寻访完成",
                "Agent 已完成当前 Session。",
                {
                    "ab_count": self.store.count_ab_matches(session_id),
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
        )

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
        candidates = self.store.get_candidates_by_ids(candidate_ids)
        for candidate in candidates:
            self._respect_control_flags(session_id, cancel_event, pause_event)
            candidate_id = str(candidate.get("id"))
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
                        "duration_ms": int((time.monotonic() - detail_started) * 1000),
                    },
                )
                continue
            self.store.save_candidate_detail(detail)
            profile_url = ""
            if isinstance(detail.raw_payload, dict):
                profile_url = str(detail.raw_payload.get("profile_url") or "")
            if profile_url:
                self.store.update_candidate_profile_url(candidate_id, profile_url)
            self._event(
                session_id,
                round_id,
                AgentEventType.DETAIL_FETCHED.value,
                "简历详情已抓取",
                "{} / {}".format(
                    candidate.get("name") or "候选人",
                    candidate.get("current_title") or "",
                ),
                {"candidate_id": candidate_id},
            )
            self.store.update_candidate_status(
                candidate_id, CandidateStatus.MATCH_QUEUED.value
            )
            future = self.match_queue.submit(
                self._match_and_persist,
                session_id,
                round_id,
                candidate_id,
                detail.resume_text,
                criteria,
            )
            futures.append(future)
        self._notify(session_id)
        return futures

    def _match_and_persist(
        self,
        session_id: str,
        round_id: str,
        candidate_id: str,
        resume_text: str,
        criteria: Dict[str, object],
    ) -> MatchResult:
        if not self._session_allows_background_write(session_id):
            raise RuntimeError("任务已取消，跳过后台匹配写入")
        self.store.update_candidate_status(candidate_id, CandidateStatus.MATCHING.value)
        result = self.matcher.match_candidate(
            session_id=session_id,
            round_id=round_id,
            candidate_id=candidate_id,
            resume_text=resume_text,
            criteria=criteria,
        )
        if not self._session_allows_background_write(session_id):
            raise RuntimeError("任务已取消，跳过后台匹配写入")
        self.store.save_match_result(result)
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
        self._notify(session_id)
        return result

    def _wait_for_policy(
        self,
        futures: List[Future],
        policy: Dict[str, object],
        cancel_event: threading.Event,
        pause_event: threading.Event,
        session_id: str,
    ) -> None:
        mode = str((policy or {}).get("mode") or "no_wait")
        if not futures:
            return
        timeout_seconds = int(
            policy.get("timeout_seconds")
            or (600 if mode in ("wait_all", "no_wait") else 180)
        )
        # The matching workers are concurrent, but round review must see the
        # actual resume-match results. "no_wait" used to bypass this barrier and
        # made the AI review reason over an empty/partial result set.
        min_results = len(futures)
        deadline = time.time() + max(1, timeout_seconds)
        while time.time() < deadline:
            if cancel_event.is_set():
                for future in futures:
                    if not future.done():
                        future.cancel()
                break
            self._respect_control_flags(session_id, cancel_event, pause_event)
            completed = sum(1 for item in futures if item.done())
            if completed >= min_results or completed >= len(futures):
                break
            time.sleep(0.2)
        for future in futures:
            if not future.done():
                continue
            try:
                future.result()
            except Exception:
                pass

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
