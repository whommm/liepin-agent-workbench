from liepin_agent.agent.candidate_picker import (
    EXPLORE,
    MUST_FETCH,
    SKIP,
    VALIDATE,
    CandidatePicker,
)
from liepin_agent.core.config import AppConfig
from liepin_agent.domain.models import CandidateSummary, Observation


def _candidate(candidate_id: str, **overrides) -> CandidateSummary:
    values = {
        "id": candidate_id,
        "dedupe_key": f"dedupe-{candidate_id}",
        "name": f"候选人{candidate_id}",
        "current_title": "产品经理",
        "current_company": "常见公司",
        "city": "上海",
        "summary_text": "卡片摘要",
        "result_index": int(candidate_id) if candidate_id.isdigit() else 0,
    }
    values.update(overrides)
    return CandidateSummary(**values)


def _observation(round_type: str = "sample_detail") -> Observation:
    return Observation(
        round_quality="uncertain",
        raw_count=10,
        deduped_count=10,
        estimated_relevant_count=2,
        noise_patterns=[],
        positive_signals=[],
        recommended_round_type=round_type,
        reason="需要用详情验证卡片信号",
    )


def test_default_detail_limits_are_bounded_without_config_file():
    config = AppConfig()

    assert config.sample_detail_limit == 10
    assert config.validate_detail_limit == 20
    assert config.harvest_detail_limit == 40


def test_bucket_candidates_keeps_high_potential_unknown_and_diverse_cards():
    candidates = [
        _candidate("high", card_signals=["消费品", "IP产品"]),
        _candidate("u1"),
        _candidate("u2"),
        _candidate("u3"),
        _candidate(
            "diverse",
            current_title="供应链商品负责人",
            current_company="小众制造企业",
            city="杭州",
        ),
    ]

    choices = CandidatePicker().bucket_candidates(candidates, explore_pool_rate=0.20)
    buckets = {choice.candidate.id: choice.bucket for choice in choices}

    assert buckets["high"] == MUST_FETCH
    assert buckets["diverse"] == EXPLORE
    assert buckets["u1"] == VALIDATE
    assert not any(choice.bucket == SKIP for choice in choices)


def test_unknown_or_legacy_noise_is_not_treated_as_hard_conflict():
    candidate = _candidate(
        "unknown",
        card_decision="noise",
        card_risks=["卡片没有展示目标技能"],
        card_signals=[],
    )

    choice = CandidatePicker().bucket_candidates([candidate])[0]

    assert choice.bucket == VALIDATE
    assert "未知" in choice.reason


def test_skip_is_limited_to_operational_cases_and_explicit_hard_conflict():
    candidates = [
        _candidate("first", dedupe_key="same-person"),
        _candidate("duplicate", dedupe_key="same-person"),
        _candidate("fetched", status="detail_fetched"),
        CandidateSummary(id="invalid"),
        _candidate(
            "conflict",
            page_meta={"hard_conflict": "客户确认的必备资质不符"},
        ),
        _candidate("unknown"),
    ]

    choices = CandidatePicker().bucket_candidates(candidates)
    by_id = {choice.candidate.id: choice for choice in choices}

    assert by_id["first"].bucket != SKIP
    assert by_id["duplicate"].bucket == SKIP
    assert by_id["fetched"].bucket == SKIP
    assert by_id["invalid"].bucket == SKIP
    assert by_id["conflict"].bucket == SKIP
    assert by_id["unknown"].bucket != SKIP


def test_masked_card_text_does_not_merge_distinct_profile_urls():
    candidates = [
        _candidate(
            "first",
            dedupe_key="https://example.com/profile/1",
            profile_url="https://example.com/profile/1",
            name="李**",
            current_company="某科技公司",
            current_title="产品经理",
        ),
        _candidate(
            "second",
            dedupe_key="https://example.com/profile/2",
            profile_url="https://example.com/profile/2",
            name="李**",
            current_company="某科技公司",
            current_title="产品经理",
        ),
    ]

    choices = CandidatePicker().bucket_candidates(candidates)

    assert all(choice.bucket != SKIP for choice in choices)


def test_stratified_selection_reserves_validation_and_exploration_capacity():
    must_fetch = [
        _candidate(f"m{index}", card_signals=["目标行业", "目标职能"])
        for index in range(10)
    ]
    unknown = [_candidate(f"u{index}") for index in range(10)]

    selection = CandidatePicker().select(must_fetch + unknown, limit=5)
    selected_buckets = [
        choice.bucket for choice in selection.choices if choice.selected
    ]

    assert selected_buckets.count(MUST_FETCH) == 2
    assert selected_buckets.count(VALIDATE) == 2
    assert selected_buckets.count(EXPLORE) == 1


def test_explicit_hard_conflicts_are_sampled_for_skip_audit():
    candidates = [_candidate("good", card_signals=["目标行业"])]
    candidates.extend(
        _candidate(
            f"hard{index}",
            page_meta={"hard_conflict": f"已确认硬条件 {index}"},
        )
        for index in range(10)
    )
    strategy = {
        "bucket_weights": {MUST_FETCH: 0.4, VALIDATE: 0.4, EXPLORE: 0.2},
        "explore_pool_rate": 0.2,
        "skip_audit_rate": 0.1,
    }

    selection = CandidatePicker().select(candidates, limit=5, strategy=strategy)

    assert len(selection.audit_candidate_ids) == 1
    assert len(selection.selected) == 2
    assert selection.audit_candidate_ids[0].startswith("hard")
    assert all("抓取" in reason or "不抓取" in reason for reason in selection.reasons().values())


def test_skip_observation_cannot_reject_valid_unknown_cards():
    decision = CandidatePicker().decide(
        _observation("skip_detail"),
        [_candidate("unknown")],
        remaining_detail_budget=2,
    )

    assert decision.action == "fetch_details"
    assert decision.round_type == "sample_detail"
    assert decision.candidate_ids == ["unknown"]
    assert decision.selection_reasons["unknown"].startswith("validate:")
