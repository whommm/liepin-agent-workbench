from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.domain.states import SessionStatus
from liepin_agent.domain.models import CandidateSummary, SearchPlan


def test_create_and_list_session(tmp_path):
    store = SQLiteStore(str(tmp_path / "workbench.db"))

    session_id = store.create_session(
        title="文创产品经理",
        jd_text="岗位名称：文创产品经理\n深圳，本科，5年以上",
        user_notes="优先潮玩和IP衍生品",
    )

    session = store.get_session(session_id)
    sessions = store.list_sessions()
    events = store.list_events(session_id)

    assert session["title"] == "文创产品经理"
    assert sessions[0]["id"] == session_id
    assert events[0]["event_type"] == "session_created"


def test_recover_interrupted_sessions_and_delete(tmp_path):
    store = SQLiteStore(str(tmp_path / "workbench.db"))
    session_id = store.create_session(
        title="恢复测试",
        jd_text="岗位名称：产品经理",
    )
    store.update_session_status(session_id, SessionStatus.RUNNING.value)

    recovered = store.recover_interrupted_sessions()
    session = store.get_session(session_id)
    events = store.list_events(session_id)

    assert recovered == 1
    assert session["status"] == SessionStatus.PAUSED.value
    assert any(event["event_type"] == "interrupted" for event in events)
    assert store.delete_session(session_id) is True
    assert store.get_session(session_id) is None


def test_criteria_version_and_candidate_sources(tmp_path):
    store = SQLiteStore(str(tmp_path / "workbench.db"))
    session_id = store.create_session("销售总监", "天然气销售")
    criteria_id = store.create_criteria_version(
        session_id,
        "天然气\n销售",
        "候选人需要有天然气行业销售经验。",
        created_by="human",
    )
    assert store.confirm_criteria_version(criteria_id) is True
    criteria = store.get_latest_criteria(session_id)
    assert criteria["criteria_version_id"] == criteria_id

    plan = SearchPlan(
        query="天然气 销售",
        position_filter="销售",
        search_hypothesis_type="core_background",
        search_hypothesis_text="验证天然气销售背景",
    )
    round_id = store.create_round(session_id, 1, plan, criteria_id)
    candidate = CandidateSummary(
        session_id=session_id,
        round_id=round_id,
        profile_url="https://example.com/a",
        name="张三",
        current_title="销售总监",
        current_company="设备公司",
        result_index=1,
        card_decision="fetch",
        card_signals=["天然气", "销售"],
    )
    candidate_id = store.save_candidate_summary(candidate)
    store.save_candidate_source(
        candidate_id,
        session_id,
        round_id,
        criteria_id,
        plan,
        result_index=1,
        card_decision="fetch",
        card_signals=["天然气", "销售"],
    )
    sources = store.list_candidate_sources(candidate_id)

    assert len(sources) == 1
    assert sources[0]["search_hypothesis_type"] == "core_background"
