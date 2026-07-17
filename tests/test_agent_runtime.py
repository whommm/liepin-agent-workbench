from liepin_agent.agent.runtime import AgentRuntime
from liepin_agent.agent.brain import RuleBasedAgentBrain
from liepin_agent.domain.models import CandidateDetail, CandidateSummary
from liepin_agent.services.browser_queue import BrowserQueue
from liepin_agent.services.event_bus import EventBus
from liepin_agent.services.match_queue import MatchQueue
from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.tools.rule_based_matcher import RuleBasedMatchService


def confirm_test_criteria(store, session_id):
    criteria_id = store.create_criteria_version(
        session_id,
        "文创\n潮玩\nIP衍生品\n量产\n供应链",
        "候选人需要具备文创、潮玩、IP衍生品、量产或供应链相关经验。",
        created_by="human",
    )
    store.confirm_criteria_version(criteria_id)


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
            for index in range(4)
        ]

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


def test_agent_runtime_completes_demo_session(tmp_path):
    store = SQLiteStore(str(tmp_path / "runtime.db"))
    session_id = store.create_session(
        title="文创产品经理",
        jd_text="岗位名称：文创产品经理\n深圳，本科，5年以上，负责文创、潮玩、IP衍生品、量产和供应链协同。",
        user_notes="不要纯内容运营，优先从0到1产品经验。",
        mode="自动",
        max_rounds=2,
        max_detail_fetches=10,
        target_ab_count=4,
    )
    confirm_test_criteria(store, session_id)
    runtime = AgentRuntime(
        store=store,
        event_bus=EventBus(),
        browser_queue=BrowserQueue(),
        match_queue=MatchQueue(max_workers=3),
        liepin_tool=FakeLiepinTool(),
        matcher=RuleBasedMatchService(),
        agent_brain=RuleBasedAgentBrain(),
    )

    runtime.run_session(session_id)

    session = store.get_session(session_id)
    rounds = store.list_rounds(session_id)
    candidates = store.list_candidates(session_id)
    events = store.list_events(session_id)
    matches = store.list_match_results(session_id)

    runtime.browser_queue.shutdown()
    runtime.match_queue.shutdown()

    assert session["status"] == "completed"
    assert rounds
    assert candidates
    assert events
    assert any(event["event_type"] == "result_observed" for event in events)
    assert matches
