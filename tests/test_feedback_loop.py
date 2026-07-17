from __future__ import annotations

from liepin_agent.domain.models import CandidateSummary, MatchResult, SearchPlan
from liepin_agent.storage.sqlite_store import SQLiteStore


def _candidate_with_match(
    store: SQLiteStore,
    session_id: str,
    round_id: str,
    criteria_id: str,
    candidate_id: str,
    recommendation_state: str,
) -> str:
    saved_id = store.save_candidate_summary(
        CandidateSummary(
            id=candidate_id,
            session_id=session_id,
            round_id=round_id,
            name=candidate_id,
            current_title="销售经理",
            profile_url="https://example.com/{}".format(candidate_id),
        )
    )
    store.save_match_result(
        MatchResult(
            candidate_id=saved_id,
            session_id=session_id,
            round_id=round_id,
            tier="",
            status="completed",
            criteria_version_id=criteria_id,
            prompt_version="match-v1",
            model_name="test-model",
            model_config_hash="config-hash",
            match_score=0,
        )
    )
    score = {
        "priority_contact": 90,
        "high_potential_verify": 75,
        "transferable_explore": 60,
        "explicit_mismatch": 10,
    }[recommendation_state]
    store.save_rank_snapshots(
        session_id,
        criteria_id,
        [
            {
                "candidate_id": saved_id,
                "known_fit_score": score,
                "potential_fit_score": score,
                "evidence_coverage_score": 80,
                "recommendation_state": recommendation_state,
                "rank_score": score,
                "rank_position": 1,
            }
        ],
    )
    return saved_id


def _labeled_session(tmp_path):
    store = SQLiteStore(str(tmp_path / "feedback.db"))
    session_id = store.create_session("销售岗位", "工业设备销售")
    criteria_id = store.create_criteria_version(
        session_id,
        "设备销售",
        "有工业设备销售经验",
        created_by="human",
    )
    store.confirm_criteria_version(criteria_id)
    round_id = store.create_round(
        session_id,
        1,
        SearchPlan(query="设备 销售"),
        criteria_id,
    )
    return store, session_id, criteria_id, round_id


def test_candidate_feedback_keeps_history_and_model_snapshot(tmp_path):
    store, session_id, criteria_id, round_id = _labeled_session(tmp_path)
    candidate_id = _candidate_with_match(
        store, session_id, round_id, criteria_id, "candidate-1", "high_potential_verify"
    )

    first_id = store.save_candidate_feedback(
        candidate_id,
        "uncertain",
        reason_codes=["信息不足"],
        note="需要确认客户类型",
    )
    second_id = store.save_candidate_feedback(
        candidate_id,
        "recommended",
        corrected_tier="A",
        note="人工沟通后确认",
    )

    history = store.list_candidate_feedback(candidate_id)
    latest = store.get_latest_candidate_feedback(candidate_id)
    candidate = store.list_candidates(session_id)[0]

    assert [item["id"] for item in history] == [second_id, first_id]
    assert latest["feedback_label"] == "recommended"
    assert latest["model_snapshot"]["recommendation_state"] == "high_potential_verify"
    assert latest["model_snapshot"]["prompt_version"] == "match-v1"
    assert candidate["feedback_label"] == "recommended"
    assert candidate["corrected_tier"] == ""
    assert candidate["feedback_reason_codes"] == []


def test_feedback_summary_reports_labeled_confusion_matrix(tmp_path):
    store, session_id, criteria_id, round_id = _labeled_session(tmp_path)
    cases = [
        ("tp", "priority_contact", "recommended", ""),
        ("fp", "high_potential_verify", "not_suitable", "行业不匹配"),
        ("fn", "explicit_mismatch", "recommended", ""),
        ("tn", "explicit_mismatch", "not_suitable", "核心经验不足"),
    ]
    for candidate_id, state, label, reason in cases:
        _candidate_with_match(
            store, session_id, round_id, criteria_id, candidate_id, state
        )
        store.save_candidate_feedback(
            candidate_id,
            label,
            reason_codes=[reason] if reason else [],
        )

    summary = store.session_feedback_summary(session_id)

    assert summary["labeled_candidate_count"] == 4
    assert summary["comparable_count"] == 4
    assert summary["true_positive"] == 1
    assert summary["false_positive"] == 1
    assert summary["false_negative"] == 1
    assert summary["true_negative"] == 1
    assert summary["precision"] == 0.5
    assert summary["recall"] == 0.5
    assert summary["agreement_rate"] == 0.5
    assert summary["reason_counts"] == {
        "行业不匹配": 1,
        "核心经验不足": 1,
    }


def test_candidate_outcomes_and_pairwise_ranking_are_auditable(tmp_path):
    store, session_id, criteria_id, round_id = _labeled_session(tmp_path)
    preferred_id = _candidate_with_match(
        store, session_id, round_id, criteria_id, "preferred", "high_potential_verify"
    )
    other_id = _candidate_with_match(
        store, session_id, round_id, criteria_id, "other", "high_potential_verify"
    )

    outcome_id = store.save_candidate_outcome(
        preferred_id,
        "interview",
        note="已约一面",
        occurred_at="2026-07-16 12:00:00",
    )
    ranking_id = store.save_ranking_feedback(
        session_id,
        preferred_id,
        other_id,
        reason="项目经验更直接",
    )

    outcomes = store.list_candidate_outcomes(preferred_id)
    rankings = store.list_ranking_feedback(session_id)
    assert outcomes[0]["id"] == outcome_id
    assert outcomes[0]["outcome"] == "interview"
    assert rankings[0]["id"] == ranking_id
    assert rankings[0]["criteria_version_id"] == criteria_id


def test_feedback_http_api_round_trip(tmp_path):
    from fastapi.testclient import TestClient

    from liepin_agent.api import app as app_module

    app = app_module.create_app(str(tmp_path))
    with TestClient(app) as client:
        service = app.state.service
        session_id = service.store.create_session("API 反馈", "销售岗位")
        round_id = service.store.create_round(
            session_id, 1, SearchPlan(query="销售")
        )
        candidate_id = service.store.save_candidate_summary(
            CandidateSummary(
                session_id=session_id,
                round_id=round_id,
                name="API 候选人",
                profile_url="https://example.com/api-candidate",
            )
        )
        response = client.post(
            "/candidates/{}/feedback".format(candidate_id),
            json={
                "feedback_label": "recommended",
                "reason_codes": ["核心经验匹配"],
                "note": "人工复核通过",
            },
        )
        summary_response = client.get(
            "/sessions/{}/feedback-summary".format(session_id)
        )

        assert response.status_code == 200
        assert response.json()["feedback"]["feedback_label"] == "recommended"
        assert summary_response.status_code == 200
        assert summary_response.json()["labeled_candidate_count"] == 1
    assert service._closed is True
