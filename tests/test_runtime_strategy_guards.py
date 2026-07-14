import hashlib
import threading
from concurrent.futures import Future

from liepin_agent.agent.runtime import AgentRuntime
from liepin_agent.domain.models import (
    CandidateDetail,
    CandidateSummary,
    FetchDecision,
    MatchResult,
    Observation,
    RoundReview,
    SearchPlan,
)
from liepin_agent.storage.sqlite_store import SQLiteStore


class _InlineQueue:
    """Run browser and matcher work inline so call counts are deterministic."""

    def run(self, fn, *args, cancel_event=None, **kwargs):
        return fn(*args, **kwargs)

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # Future captures worker failures the same way.
            future.set_exception(exc)
        return future

    def shutdown(self):
        return None


class _ToolConfig:
    def __init__(self, detail_min_resume_chars=100):
        self.detail_min_resume_chars = detail_min_resume_chars
        self.match_concurrency_limit = 10
        self.match_wait_timeout_seconds = 30


class _CountingTool:
    def __init__(self, detail_text="", detail_min_resume_chars=100):
        self.detail_text = detail_text
        self.fetch_calls = 0
        self.config_manager = type(
            "ConfigManagerStub",
            (),
            {"config": _ToolConfig(detail_min_resume_chars)},
        )()

    def fetch_candidate_detail(self, candidate):
        self.fetch_calls += 1
        return CandidateDetail(
            candidate_id=str(candidate["id"]),
            resume_text=self.detail_text,
            resume_summary="测试详情",
            capture_status="success",
        )


class _CountingMatcher:
    cache_identity = {}

    def __init__(self, tiers=None):
        self.calls = []
        self.tiers = dict(tiers or {})

    def match_candidate(
        self,
        session_id,
        round_id,
        candidate_id,
        resume_text,
        criteria,
    ):
        self.calls.append(
            {
                "candidate_id": candidate_id,
                "round_id": round_id,
                "resume_text": resume_text,
            }
        )
        tier = self.tiers.get(candidate_id, "B")
        return MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier=tier,
            status="completed",
            criteria_version_id=str(criteria.get("criteria_version_id") or ""),
            summary=f"{tier} 档测试结果",
            matched_evidence=[{"evidence": "测试证据"}],
        )


class _AuditedCountingMatcher(_CountingMatcher):
    cache_identity = {
        "prompt_version": "test-prompt-v1",
        "model_config_hash": "test-model-v1",
    }

    def match_candidate(
        self,
        session_id,
        round_id,
        candidate_id,
        resume_text,
        criteria,
    ):
        result = super().match_candidate(
            session_id,
            round_id,
            candidate_id,
            resume_text,
            criteria,
        )
        result.prompt_version = self.cache_identity["prompt_version"]
        result.model_config_hash = self.cache_identity["model_config_hash"]
        result.resume_hash = hashlib.sha256(
            resume_text.strip().encode("utf-8")
        ).hexdigest()
        return result


def _create_confirmed_session(store, *, max_rounds=3):
    session_id = store.create_session(
        title="天然气销售负责人",
        jd_text="负责 LNG 天然气项目销售和重点客户开发。",
        mode="自动",
        max_rounds=max_rounds,
        max_detail_fetches=50,
        target_ab_count=99,
    )
    criteria_id = store.create_criteria_version(
        session_id,
        "LNG\n天然气\n项目销售",
        "必须有天然气项目销售经验。",
        created_by="human",
    )
    store.confirm_criteria_version(criteria_id)
    return session_id, criteria_id


def _candidate(candidate_id, session_id, round_id, *, profile_url=None):
    return CandidateSummary(
        id=candidate_id,
        session_id=session_id,
        round_id=round_id,
        profile_url=profile_url or f"https://example.com/{candidate_id}",
        name=f"候选人{candidate_id}",
        current_title="天然气销售经理",
        current_company="能源设备公司",
        city="上海",
        work_years="8年",
        education="本科",
        summary_text="负责 LNG 天然气项目销售和重点客户开发",
        result_index=0,
    )


def _runtime(store, tool, matcher, brain=None):
    queue = _InlineQueue()
    return AgentRuntime(
        store=store,
        browser_queue=queue,
        match_queue=queue,
        liepin_tool=tool,
        matcher=matcher,
        agent_brain=brain or object(),
    )


def test_complete_detail_is_reused_and_current_criteria_match_is_not_repeated(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id, criteria_id = _create_confirmed_session(store)
    plan = SearchPlan(query="LNG 销售")
    source_round = store.create_round(session_id, 1, plan, criteria_id)
    candidate = _candidate("candidate-1", session_id, source_round)
    candidate_id = store.save_candidate_summary(candidate)
    full_resume = "负责天然气项目销售、客户开发和解决方案交付。" * 20
    store.save_candidate_detail(
        CandidateDetail(
            candidate_id=candidate_id,
            resume_text=full_resume,
            capture_status="success",
        )
    )

    tool = _CountingTool(detail_text="不应调用浏览器", detail_min_resume_chars=100)
    matcher = _CountingMatcher()
    runtime = _runtime(store, tool, matcher)
    criteria = store.get_latest_criteria(session_id)
    cancel_event = threading.Event()
    pause_event = threading.Event()

    matching_round = store.create_round(session_id, 2, plan, criteria_id)
    first_futures = runtime._fetch_and_match_candidates(
        session_id,
        matching_round,
        [candidate_id, candidate_id],
        criteria,
        cancel_event,
        pause_event,
    )
    assert len(first_futures) == 1
    assert first_futures[0].result().status == "completed"
    assert tool.fetch_calls == 0
    assert [item["candidate_id"] for item in matcher.calls] == [candidate_id]

    repeated_round = store.create_round(session_id, 3, plan, criteria_id)
    repeated_futures = runtime._fetch_and_match_candidates(
        session_id,
        repeated_round,
        [candidate_id],
        criteria,
        cancel_event,
        pause_event,
    )

    assert repeated_futures == []
    assert tool.fetch_calls == 0
    assert len(matcher.calls) == 1
    assert len(store.list_match_results(session_id)) == 1
    event_titles = [item["title"] for item in store.list_events(session_id)]
    assert "复用已抓取详情" in event_titles
    assert "跳过重复匹配" in event_titles


def test_updated_resume_invalidates_strict_match_cache(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id, criteria_id = _create_confirmed_session(store)
    plan = SearchPlan(query="LNG 销售")
    source_round = store.create_round(session_id, 1, plan, criteria_id)
    candidate_id = store.save_candidate_summary(
        _candidate("candidate-updated", session_id, source_round)
    )
    first_resume = "负责天然气项目销售和客户开发。" * 20
    store.save_candidate_detail(
        CandidateDetail(
            candidate_id=candidate_id,
            resume_text=first_resume,
            capture_status="success",
        )
    )
    tool = _CountingTool(detail_text="不应调用浏览器", detail_min_resume_chars=100)
    matcher = _AuditedCountingMatcher()
    runtime = _runtime(store, tool, matcher)
    criteria = store.get_latest_criteria(session_id)

    first_round = store.create_round(session_id, 2, plan, criteria_id)
    first = runtime._fetch_and_match_candidates(
        session_id,
        first_round,
        [candidate_id],
        criteria,
        threading.Event(),
        threading.Event(),
    )
    assert len(first) == 1
    assert first[0].result().resume_hash

    updated_resume = "负责 LNG 解决方案销售并主导重点客户投标。" * 20
    store.save_candidate_detail(
        CandidateDetail(
            candidate_id=candidate_id,
            resume_text=updated_resume,
            capture_status="success",
        )
    )
    updated_round = store.create_round(session_id, 3, plan, criteria_id)
    updated = runtime._fetch_and_match_candidates(
        session_id,
        updated_round,
        [candidate_id],
        criteria,
        threading.Event(),
        threading.Event(),
    )
    assert len(updated) == 1
    assert updated[0].result().resume_hash != first[0].result().resume_hash

    unchanged_round = store.create_round(session_id, 4, plan, criteria_id)
    unchanged = runtime._fetch_and_match_candidates(
        session_id,
        unchanged_round,
        [candidate_id],
        criteria,
        threading.Event(),
        threading.Event(),
    )

    assert unchanged == []
    assert tool.fetch_calls == 0
    assert [item["resume_text"] for item in matcher.calls] == [
        first_resume,
        updated_resume,
    ]


def test_short_detail_is_partial_and_never_submitted_to_matcher(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id, criteria_id = _create_confirmed_session(store)
    plan = SearchPlan(query="LNG 销售")
    round_id = store.create_round(session_id, 1, plan, criteria_id)
    candidate_id = store.save_candidate_summary(
        _candidate("candidate-short", session_id, round_id)
    )
    threshold = 120
    tool = _CountingTool(
        detail_text="只有很短的详情正文",
        detail_min_resume_chars=threshold,
    )
    matcher = _CountingMatcher()
    runtime = _runtime(store, tool, matcher)

    futures = runtime._fetch_and_match_candidates(
        session_id,
        round_id,
        [candidate_id],
        store.get_latest_criteria(session_id),
        threading.Event(),
        threading.Event(),
    )

    detail = store.get_candidate_detail(candidate_id)
    assert futures == []
    assert tool.fetch_calls == 1
    assert matcher.calls == []
    assert detail is not None
    assert detail["capture_status"] == "partial"
    assert f"低于自动匹配门槛 {threshold} 字" in detail["error_message"]
    assert store.list_match_results(session_id) == []
    assert store.count_fetched_details(session_id) == 0


class _DigestTool(_CountingTool):
    def __init__(self):
        super().__init__(
            detail_text="负责 LNG 天然气项目销售、客户开发和解决方案交付。" * 20,
            detail_min_resume_chars=100,
        )

    def run_search_round(self, session_id, round_id, plan):
        first = _candidate("candidate-a", session_id, round_id)
        first.result_index = 0
        first.page_meta = {"page_num": 1}
        duplicate = _candidate(
            "candidate-a-duplicate",
            session_id,
            round_id,
            profile_url=first.profile_url,
        )
        duplicate.name = first.name
        duplicate.result_index = 1
        duplicate.page_meta = {"page_num": 2}
        second = _candidate("candidate-b", session_id, round_id)
        second.result_index = 2
        second.page_meta = {"page_num": 2}
        third = _candidate("candidate-c", session_id, round_id)
        third.result_index = 3
        third.page_meta = {"page_num": 3}
        return [first, duplicate, second, third]


class _DigestBrain:
    last_prompt_metrics = {}

    def initial_plan(self, jd_text, user_notes, criteria):
        return SearchPlan(
            query="LNG 销售",
            filters={
                "city": ["上海"],
                "active_days": 30,
                "company": "能源设备公司",
            },
            search_hypothesis_type="core_background",
            search_hypothesis_text="验证天然气项目销售背景",
        )

    def observe_round(self, plan, candidates, criteria, page_meta=None):
        return Observation(
            round_quality="medium",
            raw_count=len(candidates),
            deduped_count=len({item.id for item in candidates}),
            estimated_relevant_count=3,
            noise_patterns=[],
            positive_signals=["LNG", "项目销售"],
            recommended_round_type="validate_detail",
            reason="三类候选人值得抓详情验证",
        )

    def decide_fetch(
        self,
        observation,
        candidates,
        remaining_detail_budget,
        already_fetched_ids=None,
    ):
        unique_ids = list(dict.fromkeys(item.id for item in candidates))
        return FetchDecision(
            action="fetch_details",
            round_type="validate_detail",
            candidate_ids=unique_ids,
            fetch_limit=len(unique_ids),
            sampling_strategy={"must_fetch": 1, "validate": 1, "explore": 1},
            match_wait_policy={"mode": "wait_all", "timeout_seconds": 30},
            selection_buckets={
                "must_fetch": ["candidate-a"],
                "validate": ["candidate-b"],
                "explore": ["candidate-c"],
                "skip": ["candidate-a"],
            },
            reason="覆盖必抓、验证、探索和重复跳过桶",
        )

    def review_round(
        self,
        previous_plan,
        jd_text,
        used_queries,
        match_results,
        noise_patterns,
        target_met,
        should_stop,
        stop_reason,
        criteria=None,
        used_query_signatures=None,
        round_digest=None,
    ):
        assert round_digest == []
        return RoundReview(action="stop", summary="单轮 digest 回归完成")


def test_round_digest_persists_strategy_funnel_and_full_tier_distribution(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id, _criteria_id = _create_confirmed_session(store, max_rounds=1)
    tool = _DigestTool()
    matcher = _CountingMatcher(
        {"candidate-a": "A", "candidate-b": "B", "candidate-c": "C"}
    )
    runtime = _runtime(store, tool, matcher, brain=_DigestBrain())

    runtime.run_session(session_id)

    digests = store.list_round_digests(session_id)
    assert len(digests) == 1
    digest = digests[0]
    assert digest["filters"] == {
        "city": ["上海"],
        "active_days": 30,
        "company": "能源设备公司",
    }
    assert digest["selection_counts"] == {
        "must_fetch": 1,
        "validate": 1,
        "explore": 1,
        "skip": 1,
    }
    assert digest["page_count"] == 3
    assert digest["raw_count"] == 4
    assert digest["new_count"] == 3
    assert digest["duplicate_rate"] == 0.25
    assert digest["matched_count"] == 3
    assert digest["pending_match_count"] == 0
    assert digest["tier_counts"] == {"A": 1, "B": 1, "C": 1, "D": 0}
    assert digest["a_count"] == 1
    assert digest["b_count"] == 1
    assert digest["ab_count"] == 2
    assert digest["needs_review_count"] == 0
    assert digest["failed_count"] == 0
    assert digest["conclusion"] == "单轮 digest 回归完成"

    stored_round = store.list_rounds(session_id)[0]
    assert stored_round["round_digest_json"]
    assert store.get_session(session_id)["status"] == "completed"
