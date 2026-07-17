import sqlite3

from liepin_agent.storage.sqlite_store import SQLiteStore
from liepin_agent.domain.states import SessionStatus
from liepin_agent.domain.models import (
    CandidateDetail,
    CandidateSummary,
    MatchResult,
    SearchPlan,
)


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


def test_store_configures_wal_once_and_foreign_keys_per_connection(tmp_path):
    db_path = tmp_path / "connection-settings.db"
    store = SQLiteStore(str(db_path))

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    with store.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_store_migrates_uncertainty_aware_ranking_columns(tmp_path):
    store = SQLiteStore(str(tmp_path / "ranking-migration.db"))

    with store.connect() as connection:
        criterion_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(job_criteria_items)")
        }
        evaluation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(criterion_evaluations)")
        }
        ranking_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(candidate_rank_snapshots)")
        }
        ranking_indexes = {
            row["name"]
            for row in connection.execute(
                "PRAGMA index_list(candidate_rank_snapshots)"
            )
        }

    assert "observability" in criterion_columns
    assert "verification_question" in evaluation_columns
    assert {
        "known_fit_score",
        "potential_fit_score",
        "evidence_coverage_score",
        "recommendation_state",
        "conflict_count",
    }.issubset(ranking_columns)
    assert "idx_rank_snapshots_candidate_latest" in ranking_indexes


def test_rank_snapshot_refresh_replaces_previous_candidate_version(tmp_path):
    store = SQLiteStore(str(tmp_path / "ranking-current.db"))
    session_id = store.create_session("排序快照", "测试岗位")
    criteria_id = store.create_criteria_version(
        session_id, "测试", "测试条件", created_by="human"
    )
    store.confirm_criteria_version(criteria_id)
    round_id = store.create_round(
        session_id, 1, SearchPlan(query="测试"), criteria_id
    )
    candidate_id = store.save_candidate_summary(
        CandidateSummary(
            session_id=session_id,
            round_id=round_id,
            name="候选人",
            profile_url="https://example.com/ranking-current",
        )
    )

    for score, state in ((60, "transferable_explore"), (88, "priority_contact")):
        store.save_rank_snapshots(
            session_id,
            criteria_id,
            [
                {
                    "candidate_id": candidate_id,
                    "known_fit_score": score,
                    "potential_fit_score": score,
                    "evidence_coverage_score": 80,
                    "recommendation_state": state,
                    "rank_score": score,
                    "rank_position": 1,
                }
            ],
        )

    with store.connect() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) AS n FROM candidate_rank_snapshots
            WHERE candidate_id = ? AND criteria_version_id = ?
            """,
            (candidate_id, criteria_id),
        ).fetchone()["n"]

    rankings = store.list_current_rankings(session_id, criteria_id)
    assert count == 1
    assert rankings[0]["recommendation_state"] == "priority_contact"
    assert rankings[0]["rank_score"] == 88


def test_list_candidates_detail_only_excludes_cards_without_resume(tmp_path):
    store = SQLiteStore(str(tmp_path / "detail-only.db"))
    session_id = store.create_session("详情预览", "产品经理")
    round_id = store.create_round(session_id, 1, SearchPlan(query="产品经理"))
    hidden_id = store.save_candidate_summary(
        CandidateSummary(
            id="card-only",
            session_id=session_id,
            round_id=round_id,
            name="仅卡片",
            profile_url="https://example.com/card-only",
        )
    )
    visible_id = store.save_candidate_summary(
        CandidateSummary(
            id="with-detail",
            session_id=session_id,
            round_id=round_id,
            name="有详情",
            profile_url="https://example.com/with-detail",
        )
    )
    store.save_candidate_detail(
        CandidateDetail(
            candidate_id=visible_id,
            resume_text="完整简历正文",
            capture_status="success",
        )
    )
    store.save_candidate_detail(
        CandidateDetail(
            candidate_id=hidden_id,
            resume_text="",
            capture_status="failed",
        )
    )

    assert {item["id"] for item in store.list_candidates(session_id)} == {
        hidden_id,
        visible_id,
    }
    assert [
        item["id"] for item in store.list_candidates(session_id, detail_only=True)
    ] == [visible_id]


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


def test_session_diagnostic_summary_flags_pending_match(tmp_path):
    store = SQLiteStore(str(tmp_path / "workbench.db"))
    session_id = store.create_session("诊断测试", "天然气销售")
    criteria_id = store.create_criteria_version(
        session_id,
        "天然气\n销售",
        "候选人需要有天然气行业销售经验。",
        created_by="human",
    )
    store.confirm_criteria_version(criteria_id)
    plan = SearchPlan(query="天然气 销售", position_filter="销售")
    round_id = store.create_round(session_id, 1, plan, criteria_id)
    candidate = CandidateSummary(
        session_id=session_id,
        round_id=round_id,
        profile_url="https://example.com/a",
        name="张三",
        current_title="销售总监",
        current_company="设备公司",
        card_decision="noise",
    )
    candidate_id = store.save_candidate_summary(candidate)
    store.save_candidate_detail(
        CandidateDetail(
            candidate_id=candidate_id,
            resume_text="天然气销售简历",
            capture_status="success",
        )
    )

    summary = store.session_diagnostic_summary(session_id)

    assert summary["pending_match_count"] == 1
    assert summary["card_decision_counts"]["noise"] == 1
    assert any("等待匹配结果回写" in item for item in summary["diagnostic_flags"])


def test_current_criteria_uses_latest_result_and_distinct_detail_count(tmp_path):
    store = SQLiteStore(str(tmp_path / "workbench.db"))
    session_id = store.create_session("版本投影测试", "天然气销售")
    criteria_v1 = store.create_criteria_version(
        session_id, "天然气\n销售", "天然气销售经验", created_by="human"
    )
    store.confirm_criteria_version(criteria_v1)
    round_id = store.create_round(
        session_id,
        1,
        SearchPlan(query="天然气 销售"),
        criteria_v1,
    )
    candidate_id = store.save_candidate_summary(
        CandidateSummary(
            session_id=session_id,
            round_id=round_id,
            profile_url="https://example.com/versioned",
            name="候选人",
        )
    )
    for text in ("第一份详情", "同一候选人的重复详情"):
        store.save_candidate_detail(
            CandidateDetail(
                candidate_id=candidate_id,
                resume_text=text,
                capture_status="success",
            )
        )

    store.save_match_result(
        MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            summary="v1-first",
            status="completed",
            criteria_version_id=criteria_v1,
            matched_evidence=[{"criterion": "行业", "evidence": "天然气", "strength": "强"}],
        )
    )
    store.save_match_result(
        MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            summary="v1-latest",
            status="completed",
            criteria_version_id=criteria_v1,
        )
    )

    assert store.list_candidates(session_id)[0]["match_summary"] == "v1-latest"
    assert store.count_fetched_details(session_id) == 1

    criteria_v2 = store.create_criteria_version(
        session_id, "LNG\n销售", "必须有 LNG 销售证据", created_by="human"
    )
    store.confirm_criteria_version(criteria_v2)

    assert store.list_candidates(session_id)[0]["match_summary"] is None

    store.save_match_result(
        MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            summary="v2-current",
            status="completed",
            criteria_version_id=criteria_v2,
            matched_evidence=[{"criterion": "LNG", "evidence": "LNG 销售", "strength": "强"}],
        )
    )
    assert store.list_candidates(session_id)[0]["match_summary"] == "v2-current"
    assert store.session_efficiency_metrics(session_id)["matched_count"] == 1

    store.save_match_result(
        MatchResult(
            candidate_id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            status="needs_review",
            criteria_version_id=criteria_v2,
        )
    )
    assert "match_tier" not in store.list_candidates(session_id)[0]
    metrics = store.session_efficiency_metrics(session_id)
    diagnostic = store.session_diagnostic_summary(session_id)
    assert metrics["matched_count"] == 1
    assert diagnostic["match_status_counts"] == {"needs_review": 1}
    assert "tier_counts" not in diagnostic


def test_match_audit_fields_round_trip_and_cache_identity_is_conjunctive(tmp_path):
    store = SQLiteStore(str(tmp_path / "workbench.db"))
    session_id = store.create_session("缓存测试", "电机研发")
    criteria_id = store.create_criteria_version(
        session_id, "电机\n研发", "具备电机研发经验", created_by="human"
    )
    store.confirm_criteria_version(criteria_id)
    round_id = store.create_round(
        session_id, 1, SearchPlan(query="电机 研发"), criteria_id
    )
    candidate_id = store.save_candidate_summary(
        CandidateSummary(
            session_id=session_id,
            round_id=round_id,
            profile_url="https://example.com/cache",
            name="候选人",
        )
    )
    result = MatchResult(
        candidate_id=candidate_id,
        session_id=session_id,
        round_id=round_id,
        tier="A",
        status="completed",
        criteria_version_id=criteria_id,
        matched_evidence=[
            {
                "criterion": "电机研发",
                "evidence": "负责无刷电机研发",
                "strength": "strong",
                "source_type": "direct",
                "grounding_status": "model_summary",
            }
        ],
        missing_or_unclear=["薪资待确认"],
        questions_to_verify=["期望薪资是多少？"],
        confidence="high",
        prompt_version="match-v2",
        model_name="matcher-model",
        model_config_hash="model-hash-v2",
        input_hash="input-hash-v2",
        resume_hash="resume-hash-v2",
        match_score=93,
    )

    match_id = store.save_match_result(result)
    rows = store.list_match_results(session_id)

    assert rows[0]["id"] == match_id
    assert rows[0]["match_score"] == 93
    assert rows[0]["prompt_version"] == "match-v2"
    assert rows[0]["model_name"] == "matcher-model"
    assert rows[0]["model_config_hash"] == "model-hash-v2"
    assert rows[0]["input_hash"] == "input-hash-v2"
    assert rows[0]["resume_hash"] == "resume-hash-v2"
    assert rows[0]["matched_evidence"] == result.matched_evidence
    assert rows[0]["missing_or_unclear"] == ["薪资待确认"]
    assert rows[0]["questions_to_verify"] == ["期望薪资是多少？"]

    cached = store.find_match_result(
        candidate_id,
        criteria_id,
        statuses=("completed",),
        prompt_version="match-v2",
        model_config_hash="model-hash-v2",
        input_hash="input-hash-v2",
        resume_hash="resume-hash-v2",
    )
    assert cached and cached["id"] == match_id
    assert (
        store.find_match_result(
            candidate_id,
            criteria_id,
            statuses=("completed",),
            prompt_version="match-v1",
            model_config_hash="model-hash-v2",
        )
        is None
    )
    assert (
        store.find_match_result(
            candidate_id,
            criteria_id,
            statuses=("completed",),
            prompt_version="match-v2",
            model_config_hash="model-hash-v2",
            resume_hash="changed-resume-hash",
        )
        is None
    )


def test_legacy_match_table_migrates_audit_columns_and_remains_writable(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE match_results (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                tier TEXT,
                core_met_count INTEGER,
                core_total INTEGER,
                dealbreaker_hit INTEGER,
                summary TEXT,
                risks TEXT,
                recommendation TEXT,
                detail TEXT,
                raw_response TEXT,
                status TEXT NOT NULL,
                criteria_version_id TEXT,
                evidence_json TEXT,
                unknowns_json TEXT,
                questions_json TEXT,
                confidence TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO match_results (
                id, candidate_id, session_id, round_id, tier, status,
                criteria_version_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy-match", "candidate-1", "session-1", "round-1", "B", "completed", "criteria-1", "2026-01-01 00:00:00"),
        )

    store = SQLiteStore(str(db_path))
    with store.connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(match_results)")
        }
        indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(match_results)")
        }

    assert {
        "prompt_version",
        "model_name",
        "model_config_hash",
        "input_hash",
        "resume_hash",
        "match_score",
    }.issubset(columns)
    assert "idx_match_results_current" in indexes
    assert "idx_match_results_cache" in indexes
    legacy = store.list_match_results("session-1")[0]
    assert legacy["id"] == "legacy-match"
    assert legacy["match_score"] == 0

    new_id = store.save_match_result(
        MatchResult(
            candidate_id="candidate-1",
            session_id="session-1",
            round_id="round-1",
            tier="C",
            status="completed",
            criteria_version_id="criteria-1",
            prompt_version="match-v2",
            model_name="matcher-model",
            model_config_hash="model-hash-v2",
            input_hash="input-hash-v2",
            resume_hash="resume-hash-v2",
            match_score=42,
        )
    )
    saved = next(
        item
        for item in store.list_match_results("session-1")
        if item["id"] == new_id
    )
    assert saved["match_score"] == 42
    assert saved["prompt_version"] == "match-v2"
