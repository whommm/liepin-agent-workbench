from liepin_agent.agent.candidate_picker import CandidatePicker
from liepin_agent.agent.observer import Observer
from liepin_agent.domain.models import CandidateSummary
from liepin_agent.domain.pre_score import classify_candidate_card, pre_score_candidate


def test_observer_and_picker_validate_promising_round():
    candidates = []
    for index in range(8):
        candidates.append(
            CandidateSummary(
                id=str(index),
                current_title="文创产品经理",
                current_company="泡泡玛特",
                summary_text="文创 潮玩 IP衍生品 量产",
                pre_score=82,
                result_index=index,
            )
        )

    observation = Observer().observe(candidates, ["文创", "潮玩", "IP衍生品"])
    decision = CandidatePicker().decide(observation, candidates, remaining_detail_budget=10)

    assert observation.recommended_round_type in {"validate_detail", "harvest_detail"}
    assert decision.action == "fetch_details"
    assert decision.candidate_ids


def test_sales_position_is_not_treated_as_default_noise():
    candidate = CandidateSummary(
        current_title="销售总监",
        current_company="天然气设备公司",
        summary_text="天然气 LNG 项目型销售",
    )
    score, reasons = pre_score_candidate(
        candidate,
        expected_terms=["天然气", "LNG"],
        position_filter="销售",
        negative_terms=["销售", "客服"],
    )
    decision, signals, risks, _reason = classify_candidate_card(
        candidate,
        expected_terms=["天然气", "LNG"],
        position_filter="销售",
        negative_terms=["销售", "客服"],
    )

    assert score >= 45  # pre_score 对销售岗已降级为固定 50
    assert not any("销售" in reason and "疑似噪音" in reason for reason in reasons)
    assert decision in ("fetch", "maybe")  # 销售岗 pre_score 降级后可能为 maybe
    assert "客服" not in risks
    assert signals
