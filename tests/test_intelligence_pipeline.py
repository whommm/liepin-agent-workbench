from __future__ import annotations

from liepin_agent.domain.job_profile import normalize_job_profile
from liepin_agent.domain.models import CandidateSummary, MatchResult, SearchPlan
from liepin_agent.services.candidate_intelligence import CandidateIntelligenceService
from liepin_agent.services.candidate_ranking import CandidateRankingService
from liepin_agent.storage.sqlite_store import SQLiteStore


def _profile_store(tmp_path):
    store = SQLiteStore(str(tmp_path / "intelligence.db"))
    session_id = store.create_session("工业销售", "负责压缩机大客户销售，深圳")
    criteria_id = store.create_criteria_version(
        session_id,
        "压缩机\n大客户销售",
        "必须有工业压缩机销售经验",
        created_by="human",
    )
    items, personas = normalize_job_profile(
        {
            "requirements_text": "必须有工业压缩机销售经验",
            "criteria_items": [
                {
                    "type": "must",
                    "criterion": "有工业压缩机销售经验",
                    "weight": 0.9,
                    "search_aliases": ["压缩机销售", "工业设备销售"],
                    "evidence_policy": "需要工作经历中的产品和销售事实",
                },
                {
                    "type": "verify",
                    "criterion": "接受深圳工作地点",
                    "weight": 0.4,
                    "search_aliases": ["深圳"],
                    "observability": "conversation",
                },
            ],
            "personas": [
                {
                    "name": "直接对口",
                    "description": "压缩机销售",
                    "titles": ["销售经理"],
                    "skills": ["压缩机", "大客户销售"],
                    "priority": 0.9,
                },
                {
                    "name": "流体设备迁移",
                    "description": "相邻设备销售",
                    "titles": ["区域销售"],
                    "skills": ["流体设备", "项目销售"],
                    "priority": 0.55,
                },
            ],
        }
    )
    store.replace_job_profile(criteria_id, items, personas)
    store.confirm_criteria_version(criteria_id)
    round_id = store.create_round(
        session_id, 1, SearchPlan(query="压缩机 销售"), criteria_id
    )
    return store, session_id, criteria_id, round_id


def test_structured_job_profile_round_trip_and_runtime_resolution(tmp_path):
    store, session_id, criteria_id, _ = _profile_store(tmp_path)

    version = store.get_latest_criteria_version(session_id, "confirmed")
    resolved = store.get_latest_criteria(session_id)

    assert version["id"] == criteria_id
    assert [item["criterion_type"] for item in version["criteria_items"]] == [
        "must",
        "verify",
    ]
    assert version["criteria_items"][0]["search_aliases"] == [
        "压缩机销售",
        "工业设备销售",
    ]
    assert version["criteria_items"][1]["observability"] == "conversation"
    assert [item["name"] for item in version["personas"]] == [
        "直接对口",
        "流体设备迁移",
    ]
    assert resolved["criteria_items"][0]["criterion_text"] == "有工业压缩机销售经验"


def test_fact_extraction_and_criterion_evaluation_preserve_unknown(tmp_path):
    store, session_id, criteria_id, round_id = _profile_store(tmp_path)
    candidate_id = store.save_candidate_summary(
        CandidateSummary(
            session_id=session_id,
            round_id=round_id,
            name="候选人",
            profile_url="https://example.com/facts",
        )
    )
    resume = "工作经历\n某机械公司\n负责工业压缩机大客户销售和项目投标\n教育经历\n本科"
    service = CandidateIntelligenceService()
    facts = service.extract_facts(resume, {"current_city": "广州"})
    criteria_items = store.list_job_criteria(criteria_id)
    evaluations = service.evaluate(criteria_items, facts)
    store.replace_candidate_facts(candidate_id, facts)
    store.replace_criterion_evaluations(
        candidate_id, session_id, criteria_id, evaluations
    )

    saved = store.list_criterion_evaluations(candidate_id, criteria_id)
    by_text = {item["criterion_text"]: item for item in saved}
    assert by_text["有工业压缩机销售经验"]["status"] == "direct_met"
    assert by_text["有工业压缩机销售经验"]["evidence"][0]["start"] is not None
    assert by_text["接受深圳工作地点"]["status"] == "unknown"
    assert "沟通核实" in by_text["接受深圳工作地点"]["reason"]
    assert by_text["接受深圳工作地点"]["verification_question"]


def test_unknown_conditions_reduce_coverage_without_reducing_known_fit():
    ranking = CandidateRankingService(store=None)
    score = ranking._score(
        [
            {
                "criterion_type": "must",
                "criterion_text": "工业设备销售",
                "weight": 0.5,
                "status": "direct_met",
                "confidence": 0.9,
            },
            {
                "criterion_type": "verify",
                "criterion_text": "接受长期出差",
                "weight": 0.5,
                "status": "unknown",
                "confidence": 0.35,
            },
        ],
        {"match_score": 60, "match_tier": "C", "confidence": "medium"},
    )

    assert score["known_fit_score"] == 100
    assert score["potential_fit_score"] == 100
    assert score["evidence_coverage_score"] == 50
    assert score["recommendation_state"] == "high_potential_verify"


def test_confirmed_hard_conflict_is_the_only_hard_mismatch_path():
    ranking = CandidateRankingService(store=None)
    score = ranking._score(
        [
            {
                "criterion_type": "must",
                "criterion_text": "工业设备销售",
                "weight": 1.0,
                "status": "explicit_not_met",
                "confidence": 0.9,
            }
        ],
        {"match_score": 20, "match_tier": "D", "confidence": "high"},
    )

    assert score["recommendation_state"] == "explicit_mismatch"
    assert score["conflict_count"] == 1


def test_search_portfolio_explores_then_records_yield(tmp_path):
    store, session_id, criteria_id, _ = _profile_store(tmp_path)
    store.create_round(
        session_id,
        2,
        SearchPlan(query="压缩机 大客户销售", position_filter="销售经理"),
        criteria_id,
    )
    criteria = store.get_latest_criteria(session_id)
    hypotheses = store.ensure_search_hypotheses(session_id, criteria)
    baseline_coverage = store.search_coverage_summary(session_id)

    first = store.select_search_hypothesis_plan(session_id, criteria_id)
    assert len(hypotheses) >= 4
    assert first is not None
    assert first.search_hypothesis_id
    store.record_search_hypothesis_result(
        first.search_hypothesis_id,
        round_id="round-result",
        page_count=2,
        raw_count=30,
        new_count=20,
        detail_count=8,
        relevant_count=3,
        duplicate_rate=0.2,
    )
    second = store.select_search_hypothesis_plan(session_id, criteria_id)
    coverage = store.search_coverage_summary(session_id)

    assert second is not None
    assert second.search_hypothesis_id != first.search_hypothesis_id
    assert baseline_coverage["completed"] >= 1
    assert coverage["completed"] == baseline_coverage["completed"] + 1
    assert coverage["new_count"] == 20
    assert coverage["relevant_count"] == 3


def test_evidence_ranking_and_feedback_calibration(tmp_path):
    store, session_id, criteria_id, round_id = _profile_store(tmp_path)
    intelligence = CandidateIntelligenceService()
    criterion = store.list_job_criteria(criteria_id)[0]
    candidates = []
    for candidate_id, tier, resume, label in (
        ("strong", "A", "负责工业压缩机销售和大客户项目", "recommended"),
        ("unknown", "C", "负责企业客户服务", "uncertain"),
        ("weak", "D", "从事行政管理", "not_suitable"),
    ):
        store.save_candidate_summary(
            CandidateSummary(
                id=candidate_id,
                session_id=session_id,
                round_id=round_id,
                name=candidate_id,
                profile_url="https://example.com/{}".format(candidate_id),
            )
        )
        store.save_match_result(
            MatchResult(
                candidate_id=candidate_id,
                session_id=session_id,
                round_id=round_id,
                tier=tier,
                criteria_version_id=criteria_id,
                status="completed",
                match_score={"A": 90, "C": 45, "D": 10}[tier],
                confidence="medium",
            )
        )
        facts = intelligence.extract_facts(resume)
        evaluations = intelligence.evaluate([criterion], facts)
        store.replace_candidate_facts(candidate_id, facts)
        store.replace_criterion_evaluations(
            candidate_id, session_id, criteria_id, evaluations
        )
        store.save_candidate_feedback(candidate_id, label)
        candidates.append(candidate_id)

    rankings = CandidateRankingService(store).refresh_session(session_id)
    model = store.get_latest_calibration_model(session_id)

    assert rankings[0]["candidate_id"] == "strong"
    assert rankings[-1]["candidate_id"] == "weak"
    assert rankings[0]["fit_score"] > rankings[-1]["fit_score"]
    assert rankings[0]["calibrated_probability"] is not None
    assert model["sample_count"] == 2
    assert "brier_score" in model["metrics"]


def test_intelligence_http_contract_end_to_end(tmp_path):
    from fastapi.testclient import TestClient

    from liepin_agent.api import app as app_module

    app = app_module.create_app(str(tmp_path))
    with TestClient(app) as client:
        service = app.state.service
        create_response = client.post(
            "/sessions",
            json={"title": "智能接口", "jd_text": "工业压缩机销售"},
        )
        session_id = create_response.json()["session_id"]
        profile_response = client.put(
            "/sessions/{}/job-profile".format(session_id),
            json={
                "requirements_text": "必须有压缩机销售经验",
                "criteria_items": [
                    {
                        "criterion_type": "must",
                        "criterion_text": "有压缩机销售经验",
                        "weight": 0.9,
                        "search_aliases": ["压缩机销售"],
                    }
                ],
                "personas": [
                    {
                        "name": "直接对口",
                        "titles": ["销售经理"],
                        "skills": ["压缩机", "销售"],
                        "priority": 0.9,
                    }
                ],
                "confirm": True,
            },
        )
        coverage_response = client.get(
            "/sessions/{}/search-coverage".format(session_id)
        )
        criteria_id = profile_response.json()["criteria_version_id"]
        round_id = service.store.create_round(
            session_id, 1, SearchPlan(query="压缩机 销售"), criteria_id
        )
        candidate_id = service.store.save_candidate_summary(
            CandidateSummary(
                session_id=session_id,
                round_id=round_id,
                name="接口候选人",
                profile_url="https://example.com/http-intelligence",
            )
        )
        service.store.save_match_result(
            MatchResult(
                candidate_id=candidate_id,
                session_id=session_id,
                round_id=round_id,
                tier="B",
                criteria_version_id=criteria_id,
                status="completed",
                match_score=75,
            )
        )
        facts = CandidateIntelligenceService().extract_facts(
            "负责工业压缩机销售"
        )
        evaluations = CandidateIntelligenceService().evaluate(
            service.store.list_job_criteria(criteria_id), facts
        )
        service.store.replace_candidate_facts(candidate_id, facts)
        service.store.replace_criterion_evaluations(
            candidate_id, session_id, criteria_id, evaluations
        )
        feedback_response = client.post(
            "/candidates/{}/feedback".format(candidate_id),
            json={"feedback_label": "recommended"},
        )
        ranking_response = client.post(
            "/sessions/{}/ranking/refresh".format(session_id)
        )
        dashboard_response = client.get(
            "/sessions/{}/quality-dashboard".format(session_id)
        )

        assert create_response.status_code == 200
        assert profile_response.status_code == 200
        assert coverage_response.json()["total"] >= 2
        assert feedback_response.status_code == 200
        assert ranking_response.json()["rankings"][0]["candidate_id"] == candidate_id
        assert ranking_response.json()["rankings"][0]["recommendation_state"]
        assert dashboard_response.json()["feedback"]["labeled_candidate_count"] == 1
        assert "effective_pool_score" in dashboard_response.json()["candidate_pool"]
    assert service._closed is True
