"""Tests for agent-driven pagination: brain verdicts, tool contract, runtime loop."""

import json
from types import SimpleNamespace

from liepin_agent.agent.brain import LLMAgentBrain, RuleBasedAgentBrain
from liepin_agent.agent.runtime import AgentRuntime
from liepin_agent.core.config import AppConfig
from liepin_agent.core.search import (
    PageYieldStats,
    SearchCursor,
    SearchCursorLostError,
)
from liepin_agent.core.search._models import LiepinSearchCandidate
from liepin_agent.domain.models import CandidateDetail, CandidateSummary, SearchPlan
from liepin_agent.services.browser_queue import BrowserQueue
from liepin_agent.services.event_bus import EventBus
from liepin_agent.services.match_queue import MatchQueue
from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.tools.real_liepin import RealLiepinTool, SearchRoundResult
from liepin_agent.tools.rule_based_matcher import RuleBasedMatchService


def _plan() -> SearchPlan:
    return SearchPlan(query="潮玩", expected_signal=["潮玩", "IP衍生品"])


def _stats(**overrides):
    stats = {
        "page_num": 3,
        "raw_count": 20,
        "new_unique": 20,
        "duplicate_count": 0,
        "duplicate_rate": 0.0,
        "potential_count": 4,
        "validate_count": 3,
        "promising_count": 7,
        "policy_continue": True,
        "policy_reason": "",
    }
    stats.update(overrides)
    return stats


# ---------------------------------------------------------------------------
# Rule brain fallback
# ---------------------------------------------------------------------------


def test_rule_brain_continues_while_latest_page_yields():
    brain = RuleBasedAgentBrain()

    verdict = brain.decide_pagination(
        plan=_plan(),
        criteria={},
        page_stats=[_stats(new_unique=5)],
        cursor_page_num=3,
        hard_cap=10,
    )

    assert verdict.action == "continue"
    assert verdict.additional_pages == 2


def test_rule_brain_clamps_pages_near_hard_cap():
    brain = RuleBasedAgentBrain()

    verdict = brain.decide_pagination(
        plan=_plan(),
        criteria={},
        page_stats=[_stats(new_unique=5)],
        cursor_page_num=9,
        hard_cap=10,
    )

    assert verdict.action == "continue"
    assert verdict.additional_pages == 1


def test_rule_brain_stops_on_low_yield_or_high_duplicate_rate():
    brain = RuleBasedAgentBrain()

    low_yield = brain.decide_pagination(
        plan=_plan(),
        criteria={},
        page_stats=[_stats(new_unique=1)],
        cursor_page_num=3,
        hard_cap=10,
    )
    duplicates = brain.decide_pagination(
        plan=_plan(),
        criteria={},
        page_stats=[_stats(new_unique=5, duplicate_rate=0.95)],
        cursor_page_num=3,
        hard_cap=10,
    )

    assert low_yield.action == "stop"
    assert duplicates.action == "stop"


def test_rule_brain_stops_at_hard_cap_or_without_next_page():
    brain = RuleBasedAgentBrain()

    at_cap = brain.decide_pagination(
        plan=_plan(),
        criteria={},
        page_stats=[_stats(page_num=10, new_unique=5)],
        cursor_page_num=10,
        hard_cap=10,
    )
    no_next = brain.decide_pagination(
        plan=_plan(),
        criteria={},
        page_stats=[_stats(new_unique=5)],
        cursor_page_num=3,
        hard_cap=10,
        has_next_page=False,
    )

    assert at_cap.action == "stop"
    assert at_cap.additional_pages == 0
    assert no_next.action == "stop"


# ---------------------------------------------------------------------------
# LLM brain
# ---------------------------------------------------------------------------


class _PaginationClient:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def chat(self, prompt, system_message=""):
        self.prompts.append(prompt)
        if "是否继续翻页" in prompt:
            return json.dumps(self.payload, ensure_ascii=False)
        return json.dumps({})


class _InvalidJsonClient:
    def chat(self, prompt, system_message=""):
        return "不是 JSON"


def _llm_kwargs(**overrides):
    kwargs = {
        "plan": _plan(),
        "criteria": {"core_terms": ["潮玩"], "negative_terms": ["实习"]},
        "page_stats": [_stats()],
        "cursor_page_num": 3,
        "hard_cap": 10,
        "total_results": 200,
        "has_next_page": True,
        "new_card_samples": [
            {"name": "张三", "current_title": "产品经理", "current_company": "泡泡玛特", "city": "深圳"}
        ],
    }
    kwargs.update(overrides)
    return kwargs


def test_llm_decide_pagination_continue_clamps_to_hard_cap():
    client = _PaginationClient(
        {"action": "continue", "additional_pages": 9, "reason": "产出仍好"}
    )
    brain = LLMAgentBrain(client)

    verdict = brain.decide_pagination(**_llm_kwargs(cursor_page_num=3, hard_cap=6))

    assert verdict.action == "continue"
    assert verdict.additional_pages == 3  # 6 - 3
    assert verdict.reason == "产出仍好"
    assert "是否继续翻页" in client.prompts[0]


def test_llm_decide_pagination_stop():
    brain = LLMAgentBrain(
        _PaginationClient({"action": "stop", "reason": "重复率过高"})
    )

    verdict = brain.decide_pagination(**_llm_kwargs())

    assert verdict.action == "stop"
    assert verdict.additional_pages == 0
    assert verdict.reason == "重复率过高"


def test_llm_decide_pagination_invalid_continue_defaults_one_page():
    brain = LLMAgentBrain(
        _PaginationClient({"action": "continue", "additional_pages": "many"})
    )

    verdict = brain.decide_pagination(**_llm_kwargs())

    assert verdict.action == "continue"
    assert verdict.additional_pages == 1


def test_llm_decide_pagination_failure_falls_back_to_rule_brain():
    brain = LLMAgentBrain(_InvalidJsonClient())

    continue_verdict = brain.decide_pagination(
        **_llm_kwargs(page_stats=[_stats(new_unique=20)])
    )
    stop_verdict = brain.decide_pagination(
        **_llm_kwargs(page_stats=[_stats(new_unique=0, duplicate_rate=1.0)])
    )

    # 失败回退到规则脑，不直接停。
    assert continue_verdict.action == "continue"
    assert continue_verdict.additional_pages == 2
    assert stop_verdict.action == "stop"
    assert brain.last_fallback["operation"] == "decide_pagination"


def test_llm_decide_pagination_at_hard_cap_skips_llm_call():
    client = _PaginationClient({"action": "continue", "additional_pages": 3})
    brain = LLMAgentBrain(client)

    verdict = brain.decide_pagination(
        **_llm_kwargs(cursor_page_num=10, hard_cap=10)
    )

    assert verdict.action == "stop"
    assert client.prompts == []


# ---------------------------------------------------------------------------
# RealLiepinTool contract
# ---------------------------------------------------------------------------


class _FakeSearchService:
    def __init__(self):
        self.last_search_cursor = None
        self.search_calls = []
        self.resume_calls = []

    def search(self, keyword, **kwargs):
        self.search_calls.append(kwargs)
        cursor = SearchCursor(query=keyword, page_num=3, total_results=42)
        cursor.history.append(
            PageYieldStats(page_num=1, raw_count=4, new_unique=4)
        )
        self.last_search_cursor = cursor
        return [
            LiepinSearchCandidate(
                name="张三", profile_url="https://h.liepin.com/resume/1"
            )
        ]

    def resume_pagination(self, cursor, additional_pages, **kwargs):
        self.resume_calls.append((additional_pages, kwargs))
        cursor.page_num += additional_pages
        cursor.history.append(
            PageYieldStats(
                page_num=cursor.page_num, raw_count=4, new_unique=4
            )
        )
        return [
            LiepinSearchCandidate(
                name="李四", profile_url="https://h.liepin.com/resume/2"
            )
        ]


def _bare_tool(config=None) -> RealLiepinTool:
    tool = RealLiepinTool.__new__(RealLiepinTool)
    tool.config_manager = SimpleNamespace(config=config or AppConfig())
    tool.search_service = _FakeSearchService()
    return tool


def test_run_search_round_returns_search_round_result_with_cursor():
    tool = _bare_tool()

    result = tool.run_search_round("s", "r", _plan())

    assert isinstance(result, SearchRoundResult)
    assert result.cursor is tool.search_service.last_search_cursor
    assert [item.name for item in result.candidates] == ["张三"]
    assert isinstance(result.candidates[0], CandidateSummary)
    assert result.page_stats[0]["new_unique"] == 4
    assert tool.search_service.search_calls[0]["checkpoint_pages"] == 3


def test_continue_search_round_resumes_via_service():
    tool = _bare_tool()
    result = tool.run_search_round("s", "r", _plan())

    more = tool.continue_search_round(_plan(), result.cursor, 2)

    assert tool.search_service.resume_calls[0][0] == 2
    assert [item.name for item in more.candidates] == ["李四"]
    assert more.cursor.page_num == 5
    assert len(more.page_stats) == 2


def test_run_search_round_legacy_path_without_checkpoint():
    tool = _bare_tool(AppConfig(search_agent_pagination_enabled=False))

    tool.run_search_round("s", "r", _plan())

    assert tool.search_service.search_calls[0]["checkpoint_pages"] is None


# ---------------------------------------------------------------------------
# Runtime batch loop
# ---------------------------------------------------------------------------


class PagingFakeTool:
    """Fake tool speaking the SearchRoundResult contract with page yields."""

    def __init__(self, page_yields, config=None, fail_on_batch=None):
        self.page_yields = dict(page_yields)
        self.fail_on_batch = fail_on_batch
        self.batch_calls = []
        if config is not None:
            self.config_manager = SimpleNamespace(config=config)

    def _cards(self, page_num):
        return [
            CandidateSummary(
                profile_url="https://example.com/p{}-{}".format(page_num, index),
                name="候选人{}-{}".format(page_num, index),
                current_title="文创产品经理",
                current_company="泡泡玛特",
                city="深圳",
                work_years="8年",
                education="本科",
                summary_text="文创 潮玩 IP衍生品 量产 供应链",
                result_index=index,
            )
            for index in range(self.page_yields.get(page_num, 0))
        ]

    @staticmethod
    def _result(candidates, cursor):
        return SearchRoundResult(
            candidates=candidates,
            cursor=cursor,
            page_stats=[stats.to_dict() for stats in cursor.history],
        )

    def run_search_round(self, session_id, round_id, plan, known_candidate_keys=None):
        cursor = SearchCursor(query=plan.query, total_results=100)
        candidates = []
        for page_num in (1, 2, 3):
            page_cards = self._cards(page_num)
            candidates.extend(page_cards)
            cursor.history.append(
                PageYieldStats(
                    page_num=page_num,
                    raw_count=len(page_cards),
                    new_unique=len(page_cards),
                )
            )
            for card in page_cards:
                cursor.seen_keys.add(card.profile_url)
        cursor.page_num = 3
        if cursor.page_num >= max(self.page_yields):
            cursor.exhausted = True
        return self._result(candidates, cursor)

    def continue_search_round(self, plan, cursor, additional_pages):
        self.batch_calls.append(additional_pages)
        if (
            self.fail_on_batch is not None
            and len(self.batch_calls) == self.fail_on_batch
        ):
            raise SearchCursorLostError("模拟游标丢失")
        max_page = max(self.page_yields)
        candidates = []
        target = min(cursor.page_num + additional_pages, max_page)
        for page_num in range(cursor.page_num + 1, target + 1):
            page_cards = self._cards(page_num)
            candidates.extend(page_cards)
            cursor.history.append(
                PageYieldStats(
                    page_num=page_num,
                    raw_count=len(page_cards),
                    new_unique=len(page_cards),
                )
            )
            for card in page_cards:
                cursor.seen_keys.add(card.profile_url)
            cursor.page_num = page_num
        if cursor.page_num >= max_page:
            cursor.exhausted = True
        return self._result(candidates, cursor)

    def fetch_candidate_detail(self, candidate):
        return CandidateDetail(
            candidate_id=candidate["id"],
            resume_text=(
                "负责文创潮玩产品从0到1规划，推进IP衍生品量产并协同供应链。"
                "覆盖用户研究、产品定义、打样、成本控制和上市复盘。"
            )
            * 8,
            resume_summary="测试简历",
            capture_status="success",
        )


class CountingRuleBrain(RuleBasedAgentBrain):
    def __init__(self):
        super().__init__()
        self.pagination_decisions = 0

    def decide_pagination(self, **kwargs):
        self.pagination_decisions += 1
        return super().decide_pagination(**kwargs)


def _make_runtime(tmp_path, tool, brain):
    store = SQLiteStore(str(tmp_path / "pagination.db"))
    session_id = store.create_session(
        title="文创产品经理",
        jd_text="岗位名称：文创产品经理\n深圳，本科，5年以上，负责文创、潮玩、IP衍生品、量产和供应链协同。",
        user_notes="",
        mode="自动",
        max_rounds=1,
        max_detail_fetches=10,
        target_ab_count=4,
    )
    criteria_id = store.create_criteria_version(
        session_id,
        "文创\n潮玩\nIP衍生品\n量产\n供应链",
        "候选人需要具备文创、潮玩、IP衍生品、量产或供应链相关经验。",
        created_by="human",
    )
    store.confirm_criteria_version(criteria_id)
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=2),
        liepin_tool=tool,
        matcher=RuleBasedMatchService(),
        agent_brain=brain,
    )
    return store, session_id, runtime


def _pagination_events(store, session_id):
    return [
        event
        for event in store.list_events(session_id)
        if event["event_type"] == "pagination_decision"
    ]


def test_runtime_multi_batch_continue_until_exhausted(tmp_path):
    tool = PagingFakeTool({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
    store, session_id, runtime = _make_runtime(
        tmp_path, tool, RuleBasedAgentBrain()
    )

    runtime.run_session(session_id)
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert tool.batch_calls == [2]
    assert len(store.list_candidates(session_id)) == 20
    events = _pagination_events(store, session_id)
    assert events
    assert events[0]["payload"]["action"] == "continue"
    assert events[0]["payload"]["page_stats"]


def test_runtime_brain_stops_after_low_yield_batch(tmp_path):
    # 第 6、7 页新增骤降，但后面仍有页可翻，由 brain 主动叫停。
    tool = PagingFakeTool(
        {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 1, 7: 1, 8: 4, 9: 4, 10: 4}
    )
    store, session_id, runtime = _make_runtime(
        tmp_path, tool, RuleBasedAgentBrain()
    )

    runtime.run_session(session_id)
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert tool.batch_calls == [2, 2]
    assert len(store.list_candidates(session_id)) == 22
    events = _pagination_events(store, session_id)
    assert events[-1]["payload"]["action"] == "stop"


def test_runtime_cursor_lost_finishes_round_with_persisted(tmp_path):
    tool = PagingFakeTool({1: 4, 2: 4, 3: 4, 4: 4, 5: 4}, fail_on_batch=1)
    store, session_id, runtime = _make_runtime(
        tmp_path, tool, RuleBasedAgentBrain()
    )

    runtime.run_session(session_id)
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert tool.batch_calls == [2]
    assert len(store.list_candidates(session_id)) == 12
    assert store.get_session(session_id)["status"] == "completed"
    events = _pagination_events(store, session_id)
    assert any(event["title"] == "搜索游标丢失" for event in events)


def test_runtime_agent_pagination_disabled_keeps_legacy_path(tmp_path):
    config = SimpleNamespace(
        search_agent_pagination_enabled=False,
        search_max_pages_per_round=10,
        search_pagination_batch_max_pages=3,
    )
    tool = PagingFakeTool({1: 4, 2: 4, 3: 4, 4: 4, 5: 4}, config=config)
    store, session_id, runtime = _make_runtime(
        tmp_path, tool, RuleBasedAgentBrain()
    )

    runtime.run_session(session_id)
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert tool.batch_calls == []
    assert len(store.list_candidates(session_id)) == 12
    assert _pagination_events(store, session_id) == []


def test_runtime_decision_grant_carries_across_batches(tmp_path):
    config = SimpleNamespace(
        search_agent_pagination_enabled=True,
        search_max_pages_per_round=10,
        search_pagination_batch_max_pages=1,
    )
    tool = PagingFakeTool({1: 4, 2: 4, 3: 4, 4: 4, 5: 4}, config=config)
    brain = CountingRuleBrain()
    store, session_id, runtime = _make_runtime(tmp_path, tool, brain)

    runtime.run_session(session_id)
    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    # 一次 continue 2 页的决策被拆成两个单页批次，不重复询问 brain。
    assert tool.batch_calls == [1, 1]
    assert brain.pagination_decisions == 1
    assert len(store.list_candidates(session_id)) == 20
