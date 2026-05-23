from liepin_agent.agent.brain import LLMAgentBrain
from liepin_agent.agent.runtime import AgentRuntime
from liepin_agent.core.liepin_search_service import LiepinSearchService
from liepin_agent.domain.models import CandidateDetail, CandidateSummary, MatchResult
from liepin_agent.domain.stop_conditions import evaluate_stop_conditions
from liepin_agent.services.browser_queue import BrowserQueue, BrowserTaskTimeoutError
from liepin_agent.services.event_bus import EventBus
from liepin_agent.services.match_queue import MatchQueue
from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.tools.rule_based_matcher import RuleBasedMatchService
import pytest
import time


def confirm_test_criteria(store, session_id, keywords="文创\n潮玩\nIP衍生品\n量产\n供应链"):
    criteria_id = store.create_criteria_version(
        session_id,
        keywords,
        "候选人需要具备已确认关键词相关经验，并能提供简历证据。",
        created_by="human",
    )
    store.confirm_criteria_version(criteria_id)


class InvalidJsonClient:
    def chat(self, prompt, system_message=""):
        return "不是 JSON"


class FakeLiepinTool:
    def run_search_round(self, session_id, round_id, plan):
        terms = plan.query.split() or ["文创", "潮玩"]
        return [
            CandidateSummary(
                profile_url="https://example.com/{}".format(index),
                name="候选人{}".format(index),
                current_title="文创产品经理",
                current_company="泡泡玛特",
                city="深圳",
                work_years="8年",
                education="本科",
                summary_text="{} IP衍生品 量产 供应链".format(" ".join(terms)),
                result_index=index,
            )
            for index in range(8)
        ]

    def fetch_candidate_detail(self, candidate):
        return CandidateDetail(
            candidate_id=candidate["id"],
            resume_text="文创 潮玩 IP衍生品 量产 供应链 从0到1",
            resume_summary="测试简历",
            capture_status="success",
        )


class FakeNoResultsThenResultsTool(FakeLiepinTool):
    def __init__(self):
        self.calls = 0

    def run_search_round(self, session_id, round_id, plan):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("当前关键词未搜索到候选人，准备尝试下一组关键词")
        return super().run_search_round(session_id, round_id, plan)


def test_llm_agent_brain_falls_back_on_invalid_json():
    brain = LLMAgentBrain(InvalidJsonClient())

    criteria = brain.build_criteria("岗位名称：文创产品经理\n深圳，本科，5年以上", "")
    plan = brain.initial_plan(
        "岗位名称：文创产品经理\n深圳，本科，5年以上", "", criteria
    )
    observation = brain.observe_round(
        plan,
        [
            CandidateSummary(
                id="c1",
                current_title="文创产品经理",
                current_company="泡泡玛特",
                summary_text="文创 潮玩 IP衍生品 量产",
                pre_score=82,
            )
        ],
        criteria,
    )

    assert criteria["position_filter"]
    assert plan.query
    assert observation.recommended_round_type


def test_stop_conditions_honor_runtime_limit():
    decision = evaluate_stop_conditions(
        round_index=0,
        max_rounds=10,
        fetched_details=0,
        max_detail_fetches=50,
        ab_count=0,
        target_ab_count=10,
        consecutive_low_yield_rounds=0,
        elapsed_minutes=91,
        max_runtime_minutes=90,
    )

    assert decision.should_stop is True
    assert "运行时长" in decision.reason


def test_single_step_mode_waits_for_approval(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="文创产品经理",
        jd_text="岗位名称：文创产品经理\n深圳，本科，5年以上，负责文创、潮玩、IP衍生品、量产和供应链协同。",
        user_notes="不要纯内容运营，优先从0到1产品经验。",
        mode="单步",
        max_rounds=2,
        max_detail_fetches=10,
        target_ab_count=99,
    )
    confirm_test_criteria(store, session_id)
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=3),
        liepin_tool=FakeLiepinTool(),
        matcher=RuleBasedMatchService(),
        agent_brain=LLMAgentBrain(InvalidJsonClient()),
    )

    runtime.run_session(session_id)
    session = store.get_session(session_id)
    events = store.list_events(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert session["status"] == "waiting_approval"
    assert any(event["title"] == "等待人工确认" for event in events)


def test_cancelled_session_is_not_marked_completed(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="文创产品经理",
        jd_text="岗位名称：文创产品经理\n深圳，本科，5年以上，负责文创、潮玩、IP衍生品、量产和供应链协同。",
        mode="自动",
        max_rounds=1,
        max_detail_fetches=10,
        target_ab_count=99,
    )
    confirm_test_criteria(store, session_id)
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=3),
        liepin_tool=FakeLiepinTool(),
        matcher=RuleBasedMatchService(),
        agent_brain=LLMAgentBrain(InvalidJsonClient()),
    )

    store.update_session_status(session_id, "cancelled")
    runtime.run_session(session_id)
    session = store.get_session(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert session["status"] == "cancelled"


def test_no_results_round_does_not_fail_session(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="销售总监",
        jd_text="岗位名称：销售总监\n天然气 LNG BOG 螺杆压缩机 销售。",
        mode="自动",
        max_rounds=2,
        max_detail_fetches=10,
        target_ab_count=99,
    )
    confirm_test_criteria(store, session_id, "天然气\nLNG\nBOG\n螺杆压缩机\n销售")
    tool = FakeNoResultsThenResultsTool()
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=3),
        liepin_tool=tool,
        matcher=RuleBasedMatchService(),
        agent_brain=LLMAgentBrain(InvalidJsonClient()),
    )

    runtime.run_session(session_id)
    session = store.get_session(session_id)
    rounds = store.list_rounds(session_id)
    events = store.list_events(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert session["status"] == "completed"
    assert len(rounds) == 2
    assert tool.calls == 2
    assert any(event["title"] == "本轮无搜索结果" for event in events)


class NoWaitBrain:
    def build_criteria(self, jd_text, user_notes):
        return {"position_filter": "销售", "core_terms": ["天然气"], "city_scope": []}

    def initial_plan(self, jd_text, user_notes, criteria):
        from liepin_agent.domain.models import SearchPlan

        return SearchPlan(
            query="天然气", position_filter="销售", expected_signal=["天然气"]
        )

    def observe_round(self, candidates, plan, criteria):
        from liepin_agent.domain.models import Observation

        return Observation(
            round_quality="medium",
            raw_count=len(candidates),
            deduped_count=len(candidates),
            estimated_relevant_count=len(candidates),
            noise_patterns=[],
            positive_signals=["天然气"],
            recommended_round_type="validate_detail",
            reason="测试抓取",
        )

    def decide_fetch(self, observation, candidates, remaining_detail_budget):
        from liepin_agent.domain.models import FetchDecision

        return FetchDecision(
            action="fetch_details",
            round_type="validate_detail",
            candidate_ids=[item.id for item in candidates[:1]],
            fetch_limit=1,
            match_wait_policy={"mode": "no_wait"},
            reason="测试 no_wait",
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
    ):
        from liepin_agent.domain.models import RoundReview

        assert len(match_results) == 0
        return RoundReview(action="stop", summary="复盘不等待后台匹配结果")


class SlowMatcher:
    def match_candidate(
        self, session_id, round_id, candidate_id, resume_text, criteria
    ):
        time.sleep(0.4)
        return MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="B",
            summary="慢匹配完成",
        )


class GreetingLiepinTool(FakeLiepinTool):
    def __init__(self, gold=True):
        self.gold = gold
        self.greeted = []
        self.config_manager = type(
            "ConfigManagerStub",
            (),
            {
                "config": type(
                    "ConfigStub",
                    (),
                    {"auto_greeting_enabled": True, "greeting_template": ""},
                )()
            },
        )()

    def fetch_candidate_detail(self, candidate):
        detail = super().fetch_candidate_detail(candidate)
        detail.is_gold_collar = self.gold
        detail.raw_payload["is_gold_collar"] = self.gold
        return detail

    def greet_candidate(self, candidate, message_template=""):
        self.greeted.append(candidate["id"])
        return {"status": "success", "message": "已发送打招呼", "error": ""}


class TierBrain(NoWaitBrain):
    def decide_fetch(self, observation, candidates, remaining_detail_budget):
        from liepin_agent.domain.models import FetchDecision

        return FetchDecision(
            action="fetch_details",
            round_type="validate_detail",
            candidate_ids=[item.id for item in candidates[:1]],
            fetch_limit=1,
            match_wait_policy={"mode": "wait_all"},
            reason="测试手动打招呼边界",
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
    ):
        from liepin_agent.domain.models import RoundReview

        return RoundReview(action="stop", summary="完成")


def test_no_wait_policy_does_not_block_round_review(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="销售总监",
        jd_text="岗位名称：销售总监\n天然气销售。",
        mode="自动",
        max_rounds=1,
        max_detail_fetches=2,
        target_ab_count=99,
    )
    confirm_test_criteria(store, session_id, "天然气\n销售")
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=1),
        liepin_tool=FakeLiepinTool(),
        matcher=SlowMatcher(),
        agent_brain=NoWaitBrain(),
    )

    runtime.run_session(session_id)
    session = store.get_session(session_id)
    immediate_matches = store.list_match_results(session_id)
    deadline = time.time() + 2
    matches = immediate_matches
    rounds = store.list_rounds(session_id)
    while time.time() < deadline:
        matches = store.list_match_results(session_id)
        rounds = store.list_rounds(session_id)
        if matches and rounds[0]["matched_count"] == 1:
            break
        time.sleep(0.05)
    events = store.list_events(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert session["status"] == "completed"
    assert immediate_matches == []
    assert len(matches) == 1
    assert matches[0]["tier"] == "B"
    assert rounds[0]["matched_count"] == 1
    assert rounds[0]["ab_count"] == 1
    assert any(event["title"] == "后台匹配已提交" for event in events)


def test_runtime_does_not_greet_gold_ab_candidates_automatically(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="销售总监",
        jd_text="岗位名称：销售总监\n天然气销售。",
        mode="自动",
        max_rounds=1,
        max_detail_fetches=2,
        target_ab_count=99,
    )
    confirm_test_criteria(store, session_id, "天然气\n销售")
    tool = GreetingLiepinTool(gold=True)
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=1),
        liepin_tool=tool,
        matcher=SlowMatcher(),
        agent_brain=TierBrain(),
    )

    runtime.run_session(session_id)
    candidates = store.list_candidates(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert tool.greeted == []
    assert candidates[0]["is_gold_collar"] == 1
    assert candidates[0]["greeting_status"] == ""


def test_runtime_does_not_greet_non_gold_candidates_automatically(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="销售总监",
        jd_text="岗位名称：销售总监\n天然气销售。",
        mode="自动",
        max_rounds=1,
        max_detail_fetches=2,
        target_ab_count=99,
    )
    confirm_test_criteria(store, session_id, "天然气\n销售")
    tool = GreetingLiepinTool(gold=False)
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=1),
        liepin_tool=tool,
        matcher=SlowMatcher(),
        agent_brain=TierBrain(),
    )

    runtime.run_session(session_id)
    candidates = store.list_candidates(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert tool.greeted == []
    assert candidates[0]["is_gold_collar"] == 0
    assert candidates[0]["greeting_status"] == ""


def test_runtime_greeting_is_manual_even_when_legacy_config_is_disabled(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="销售总监",
        jd_text="岗位名称：销售总监\n天然气销售。",
        mode="自动",
        max_rounds=1,
        max_detail_fetches=2,
        target_ab_count=99,
    )
    confirm_test_criteria(store, session_id, "天然气\n销售")
    tool = GreetingLiepinTool(gold=True)
    tool.config_manager.config.auto_greeting_enabled = False
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=1),
        liepin_tool=tool,
        matcher=SlowMatcher(),
        agent_brain=TierBrain(),
    )

    runtime.run_session(session_id)
    candidates = store.list_candidates(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert tool.greeted == []
    assert candidates[0]["is_gold_collar"] == 1
    assert candidates[0]["greeting_status"] == ""


def test_greeting_template_replaces_candidate_variables():
    from liepin_agent.tools.real_liepin import RealLiepinTool

    message = RealLiepinTool._render_greeting_template(
        "您好{name}，看到您在{current_company}做{current_title}，匹配点：{matched_evidence}。岗位：{job_title}",
        {
            "name": "张三",
            "current_company": "能源公司",
            "current_title": "销售总监",
            "session_title": "天然气销售负责人",
            "matched_evidence": [{"evidence": "负责 LNG 客户开发"}],
        },
    )

    assert "张三" in message
    assert "能源公司" in message
    assert "负责 LNG 客户开发" in message
    assert "天然气销售负责人" in message


def test_gold_collar_detection_falls_back_to_body_text():
    from liepin_agent.tools.real_liepin import RealLiepinTool

    class EmptyLocator:
        def count(self):
            return 0

    class Page:
        def locator(self, selector):
            if selector == "body":
                return type("Body", (), {"inner_text": lambda self, timeout=0: "金领人才"})()
            return EmptyLocator()

    tool = RealLiepinTool()
    assert tool._is_gold_collar_detail_page(Page()) is True


def test_gold_collar_detection_waits_for_delayed_elite_tag():
    from liepin_agent.tools.real_liepin import RealLiepinTool

    class Locator:
        def __init__(self, page, selector):
            self.page = page
            self.selector = selector

        def count(self):
            self.page.calls += 1
            if self.selector == ".name-box .elite-tag-gold" and self.page.calls >= 3:
                return 1
            return 0

        def inner_text(self, timeout=0):
            return "赵**"

    class Page:
        def __init__(self):
            self.calls = 0

        def locator(self, selector):
            return Locator(self, selector)

    tool = RealLiepinTool()
    assert tool._is_gold_collar_detail_page(Page(), wait_seconds=1.0) is True


def test_active_days_normalizes_half_month():
    spec = LiepinSearchService.FILTER_FIELD_SPECS["活跃度"]

    assert (
        LiepinSearchService._normalize_dropdown_filter_value(spec, "15天内活跃")
        == "30天内活跃"
    )
    assert (
        LiepinSearchService._normalize_dropdown_filter_value(spec, "半月")
        == "30天内活跃"
    )


def test_browser_queue_times_out_stuck_task():
    queue = BrowserQueue(timeout_seconds=0.01)
    try:
        with pytest.raises(BrowserTaskTimeoutError):
            queue.run(time.sleep, 0.2)
    finally:
        queue.shutdown()
