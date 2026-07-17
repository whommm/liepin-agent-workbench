import json

from liepin_agent.agent.brain import LLMAgentBrain
from liepin_agent.agent.context import (
    FETCH_CONTEXT_CHAR_BUDGET,
    FETCH_DISPUTED_LIMIT,
    FETCH_RANKED_LIMIT,
    OBSERVE_CONTEXT_CHAR_BUDGET,
    OBSERVE_SAMPLE_LIMIT,
    REVIEW_CONTEXT_CHAR_BUDGET,
    REVIEW_REPRESENTATIVE_LIMIT,
    STRATEGY_QUERY_LIMIT,
    STRATEGY_ROUND_LIMIT,
    build_fetch_context,
    build_match_review_context,
    build_observation_context,
    compact_strategy_history,
    json_text,
)
from liepin_agent.domain.models import CandidateSummary, Observation, SearchPlan


def _candidate(index: int, group: str) -> CandidateSummary:
    if group == "strong":
        summary = "LNG 天然气项目销售，负责重点客户和压缩机解决方案"
        decision = "fetch"
        signals = ["LNG", "项目销售"]
    elif group == "uncertain":
        summary = "能源行业经历"
        decision = "maybe"
        signals = []
    else:
        summary = "负责复杂项目从需求分析到跨部门交付，经历描述完整但行业线索有限"
        decision = "skip"
        signals = []
    return CandidateSummary(
        id=f"c{index:03d}",
        name=f"候选人{index}",
        current_title=f"职位{index % 9}",
        current_company=f"公司{index % 13}",
        city=f"城市{index % 6}",
        work_years=f"{index % 12 + 1}年",
        education="本科",
        summary_text=summary,
        raw_text="RAW_CARD_BODY_DO_NOT_SEND" * 500,
        result_index=index,
        card_decision=decision,
        card_signals=signals,
        card_risks=["信息待核实"] if group == "uncertain" else [],
        page_meta={"page": index // 20 + 1, "dom_dump": "DOM_SECRET" * 500},
    )


def test_observation_context_is_deterministic_stratified_and_bounded():
    candidates = [
        *[_candidate(index, "strong") for index in range(8)],
        *[_candidate(index, "uncertain") for index in range(8, 20)],
        *[_candidate(index, "diversity") for index in range(20, 60)],
    ]

    first = build_observation_context(candidates, ["LNG", "天然气"])
    second = build_observation_context(candidates, ["LNG", "天然气"])
    samples = first["representative_samples"]
    buckets = {item["sample_bucket"] for item in samples}

    assert first == second
    assert len(samples) <= OBSERVE_SAMPLE_LIMIT
    assert {"strong_signal", "uncertain", "diversity"} <= buckets
    assert first["pool_stats"]["raw_count"] == 60
    assert len(json_text(first)) <= OBSERVE_CONTEXT_CHAR_BUDGET
    assert "RAW_CARD_BODY_DO_NOT_SEND" not in json_text(first)
    assert "raw_text" not in json_text(first)


def test_fetch_context_has_compact_ranking_and_bounded_disputed_set():
    candidates = [
        _candidate(index, "strong" if index < 12 else "uncertain" if index < 70 else "diversity")
        for index in range(100)
    ]
    observation = Observation(
        round_quality="medium",
        raw_count=100,
        deduped_count=100,
        estimated_relevant_count=30,
        noise_patterns=[],
        positive_signals=["LNG", "天然气"],
        recommended_round_type="validate_detail",
        reason="存在可验证信号",
    )

    context = build_fetch_context(candidates, observation)
    rendered = json_text(context)

    assert len(context["ranked_candidates"]) <= FETCH_RANKED_LIMIT
    assert len(context["disputed_candidates"]) <= FETCH_DISPUTED_LIMIT
    assert context["omitted_count"] == 100 - context["included_count"]
    assert len(rendered) <= FETCH_CONTEXT_CHAR_BUDGET
    assert "RAW_CARD_BODY_DO_NOT_SEND" not in rendered
    assert all("raw_text" not in item for item in context["ranked_candidates"])


def test_match_review_context_aggregates_and_drops_raw_database_fields():
    matches = []
    states = [
        "priority_contact",
        "high_potential_verify",
        "transferable_explore",
        "information_insufficient",
    ]
    for index in range(24):
        matches.append(
            {
                "id": f"match-{index}",
                "candidate_id": f"c{index}",
                "session_id": "session-secret",
                "round_id": "round-secret",
                "recommendation_state": states[index % 4],
                "status": "completed",
                "core_met_count": 3,
                "core_total": 4,
                "summary": "具备目标行业和项目经验",
                "matched_evidence": [
                    {
                        "requirement": "行业经验",
                        "evidence": "负责 LNG 项目" + "证据" * 200,
                    }
                ],
                "missing_or_unclear": ["团队规模"],
                "raw_response": "RAW_MODEL_RESPONSE_SECRET" * 500,
                "detail": "FULL_RESUME_DETAIL_SECRET" * 500,
                "evidence_json": "DATABASE_JSON_SECRET" * 500,
            }
        )

    context = build_match_review_context(matches)
    rendered = json_text(context)

    assert context["aggregate"]["match_count"] == 24
    assert "ab_count" not in context["aggregate"]
    assert context["aggregate"]["recommendation_state_counts"] == {
        state: 6 for state in states
    }
    assert len(context["representative_matches"]) <= REVIEW_REPRESENTATIVE_LIMIT
    assert len(rendered) <= REVIEW_CONTEXT_CHAR_BUDGET
    assert "RAW_MODEL_RESPONSE_SECRET" not in rendered
    assert "FULL_RESUME_DETAIL_SECRET" not in rendered
    assert "DATABASE_JSON_SECRET" not in rendered
    assert "raw_response" not in rendered
    assert "detail" not in rendered


def test_strategy_history_keeps_only_recent_bounded_digests():
    queries = [f"query-{index:02d}" for index in range(30)]
    rounds = [
        {
            "round_index": index,
            "query": queries[index],
            "raw_count": index * 2,
            "viable_count": index % 3,
            "conclusion": "继续验证",
            "raw_response": "ROUND_RAW_SECRET" * 100,
        }
        for index in range(15)
    ]

    history = compact_strategy_history(queries, rounds)
    rendered = json_text(history)

    assert len(history["recent_queries"]) == STRATEGY_QUERY_LIMIT
    assert history["recent_queries"][0] == "query-18"
    assert history["omitted_query_count"] == 18
    assert len(history["recent_round_digests"]) == STRATEGY_ROUND_LIMIT
    assert history["recent_round_digests"][0]["round_index"] == 7
    assert "ROUND_RAW_SECRET" not in rendered
    assert "raw_response" not in rendered

    empty = compact_strategy_history(queries, rounds, query_limit=0, round_limit=0)
    assert empty["recent_queries"] == []
    assert empty["recent_round_digests"] == []


class _RecordingClient:
    def __init__(self):
        self.prompts = []

    def chat(self, prompt, system_message=""):
        self.prompts.append(prompt)
        if "观察本轮猎聘搜索结果池" in prompt:
            return json.dumps(
                {
                    "round_quality": "medium",
                    "raw_count": 80,
                    "deduped_count": 80,
                    "estimated_relevant_count": 24,
                    "noise_patterns": [],
                    "positive_signals": ["LNG"],
                    "recommended_round_type": "validate_detail",
                    "reason": "有明确行业信号",
                },
                ensure_ascii=False,
            )
        if "决定本轮是否抓取候选人详情" in prompt:
            return json.dumps(
                {
                    "action": "fetch_details",
                    "round_type": "validate_detail",
                    "candidate_ids": ["c000"],
                    "fetch_limit": 1,
                    "match_wait_policy": {
                        "mode": "wait_all",
                        "min_results": 1,
                        "timeout_seconds": 60,
                    },
                    "reason": "抓取强信号样本",
                },
                ensure_ascii=False,
            )
        return json.dumps({"action": "stop", "summary": "达到停止条件"}, ensure_ascii=False)


def test_brain_prompts_keep_criteria_and_isolate_large_repeated_context():
    client = _RecordingClient()
    brain = LLMAgentBrain(client)
    hard_requirement = "必须具备天然气项目销售经验" + "硬条件" * 1_500
    criteria = {
        "hard_requirements": [hard_requirement],
        "core_terms": ["LNG", "天然气"],
    }
    plan = SearchPlan(query="LNG 销售", expected_signal=["LNG", "天然气"])
    candidates = [
        _candidate(index, "strong" if index < 20 else "uncertain")
        for index in range(80)
    ]

    observation = brain.observe_round(
        plan,
        candidates,
        criteria,
        page_meta={"page_count": 10, "dom_dump": "DOM_SECRET" * 10_000},
    )
    observe_prompt = client.prompts[-1]
    assert hard_requirement in observe_prompt
    assert "RAW_CARD_BODY_DO_NOT_SEND" not in observe_prompt
    assert "DOM_SECRET" not in observe_prompt
    assert brain.last_prompt_metrics["observe_round"]["chars"] <= brain.last_prompt_metrics[
        "observe_round"
    ]["budget"]

    prompt_count = len(client.prompts)
    decision = brain.decide_fetch(observation, candidates, remaining_detail_budget=20)
    assert len(client.prompts) == prompt_count
    assert "c000" in decision.candidate_ids
    assert len(decision.candidate_ids) == 20
    assert decision.selection_buckets

    matches = [
        {
            "candidate_id": "c000",
            "tier": "A",
            "status": "completed",
            "summary": "天然气项目销售匹配",
            "matched_evidence": [{"evidence": "负责 LNG 客户开发"}],
            "raw_response": "RAW_REVIEW_SECRET" * 10_000,
            "detail": "DETAIL_REVIEW_SECRET" * 10_000,
        }
    ]
    brain.review_round(
        previous_plan=plan,
        jd_text="很长的 JD " * 5_000,
        used_queries=[f"old-query-{index:02d}" for index in range(30)],
        match_results=matches,
        noise_patterns=["行业噪音"],
        target_met=True,
        should_stop=True,
        stop_reason="达到目标",
        criteria=criteria,
        round_digest=[
            {
                "round_index": index,
                "query": f"old-query-{index:02d}",
                "raw_count": index,
                "raw_response": "ROUND_DIGEST_RAW_SECRET" * 100,
            }
            for index in range(20)
        ],
    )
    review_prompt = client.prompts[-1]

    assert hard_requirement in review_prompt
    assert "RAW_REVIEW_SECRET" not in review_prompt
    assert "DETAIL_REVIEW_SECRET" not in review_prompt
    assert "ROUND_DIGEST_RAW_SECRET" not in review_prompt
    assert "old-query-00" not in review_prompt
    assert "old-query-29" in review_prompt
    assert brain.last_prompt_metrics["review_round"]["chars"] <= brain.last_prompt_metrics[
        "review_round"
    ]["budget"]


def test_plan_filter_guard_keeps_only_confirmed_recall_safe_filters():
    data = {
        "query": "LNG 销售",
        "filters": {
            "city": ["深圳", "上海"],
            "education": "本科",
            "age": "35",
            "gender": "男",
            "company": "目标公司",
            "work_years": "5",
        },
        "search_hypothesis_type": "core_background",
    }
    criteria = {
        "city_scope": ["深圳"],
        "city_requirement": "深圳",
        "requirements_text": "本科及以上",
        "core_terms": ["LNG"],
    }

    plan = LLMAgentBrain._plan_from_data(data, criteria)

    assert plan.filters == {"city": ["深圳"], "education": "本科"}


def test_plan_signature_and_duplicate_fallback_are_deterministic():
    first = SearchPlan(
        query="LNG 销售 -客服",
        position_filter="销售",
        filters={"city": ["深圳"], "active_days": 30},
    )
    reordered = SearchPlan(
        query="销售 LNG -客服",
        position_filter="销售",
        filters={"active_days": 30, "city": ["深圳"]},
    )
    without_exclusion = SearchPlan(
        query="销售 LNG",
        position_filter="销售",
        filters={"active_days": 30, "city": ["深圳"]},
    )

    signature = LLMAgentBrain._plan_signature(first)
    assert signature == LLMAgentBrain._plan_signature(reordered)
    assert signature != LLMAgentBrain._plan_signature(without_exclusion)

    fallback = LLMAgentBrain._fallback_nonduplicate_plan(first, {signature})
    assert fallback is not None
    assert fallback.filters.get("city") is None
    assert fallback.query == first.query
