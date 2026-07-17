import json

import pytest

from liepin_agent.core.config import ConfigManager
from liepin_agent.tools.real_matcher import RealMatchService


class StaticClient:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def chat(self, prompt, system_message=""):
        self.prompts.append(prompt)
        return self.response


class FailingClient:
    def chat(self, prompt, system_message=""):
        raise RuntimeError("service unavailable")


def match_with(response, resume_text=None):
    return RealMatchService(StaticClient(response)).match_candidate(
        session_id="s1",
        round_id="r1",
        candidate_id="c1",
        resume_text=resume_text
        or "候选人简历：近三年负责电机结构设计；负责无刷电机结构设计。",
        criteria={"criteria_version_id": "v1"},
    )


def test_legacy_match_fields_are_translated_to_canonical_result():
    response = json.dumps(
        {
            "tier": "b档",
            "summary": "核心经历匹配",
            "evidence": "近三年负责电机结构设计",
            "inferred": ["可能熟悉 SolidWorks"],
            "risks": "薪资待确认",
            "questions": "目前期望薪资是多少？",
            "dealbreaker_hit": "false",
            "confidence": "HIGH",
        },
        ensure_ascii=False,
    )

    result = match_with(response)

    assert result.tier == ""
    assert result.status == "completed"
    assert result.dealbreaker_hit is False
    assert result.risks == "薪资待确认"
    assert result.questions_to_verify == ["目前期望薪资是多少？"]
    assert result.matched_evidence == [
        {
            "criterion": "",
            "evidence": "近三年负责电机结构设计",
            "strength": "medium",
            "source_type": "direct",
            "grounding_status": "exact",
        },
        {
            "criterion": "",
            "evidence": "可能熟悉 SolidWorks",
            "strength": "medium",
            "source_type": "inferred",
        },
    ]
    assert result.confidence == "high"


def test_canonical_output_normalizes_counts_boolean_and_lists():
    response = json.dumps(
        {
            "tier": "A",
            "summary": "核心要求均有直接证据",
            "core_met_count": "2",
            "core_total": "2",
            "dealbreaker_hit": 0,
            "matched_evidence": [
                {
                    "criterion": "电机设计",
                    "evidence": "负责无刷电机结构设计",
                    "strength": "strong",
                }
            ],
            "missing_or_unclear": "英语能力未说明",
            "risks": [],
            "questions_to_verify": [],
            "confidence": "medium",
        },
        ensure_ascii=False,
    )

    result = match_with(response)

    assert result.status == "completed"
    assert result.tier == ""
    assert result.core_met_count == 2
    assert result.core_total == 2
    assert result.dealbreaker_hit is False
    assert result.missing_or_unclear == ["英语能力未说明"]
    assert result.match_score == 87


@pytest.mark.parametrize(
    "payload",
    [
        {"tier": "C", "summary": "信息不足", "dealbreaker_hit": "unknown"},
        {"tier": "C", "summary": "信息不足", "questions_to_verify": {"q": "x"}},
        {"tier": "C", "summary": "信息不足", "core_met_count": 2, "core_total": 1},
    ],
)
def test_invalid_contract_is_needs_review_without_business_tier(payload):
    result = match_with(json.dumps(payload, ensure_ascii=False))

    assert result.tier == ""
    assert result.status == "needs_review"
    assert result.confidence == "low"
    assert result.missing_or_unclear


@pytest.mark.parametrize("tier", ["A", "B", "C", "D", "maybe"])
def test_retired_tier_is_ignored(tier):
    result = match_with(
        json.dumps(
            {
                "tier": tier,
                "summary": "根据推断判断匹配",
                "inferred": ["可能熟悉相关工具"],
            },
            ensure_ascii=False,
        )
    )

    assert result.tier == ""
    assert result.status == "completed"
    assert result.matched_evidence[0]["source_type"] == "inferred"


def test_evidence_contract_can_complete_without_positive_evidence():
    result = match_with(
        json.dumps(
            {"tier": "D", "summary": "经历与岗位无关", "risks": ["方向不符"]},
            ensure_ascii=False,
        )
    )

    assert result.tier == ""
    assert result.status == "completed"


def test_non_json_output_is_needs_review_not_c_or_d():
    result = match_with("not json")

    assert result.tier == ""
    assert result.status == "needs_review"
    assert result.raw_response == "not json"


def test_transport_failure_has_failed_status_and_no_business_tier():
    result = RealMatchService(FailingClient()).match_candidate(
        session_id="s1",
        round_id="r1",
        candidate_id="c1",
        resume_text="候选人简历",
        criteria={"criteria_version_id": "v1"},
    )

    assert result.tier == ""
    assert result.status == "failed"
    assert result.criteria_version_id == "v1"


def test_real_matcher_from_config_forwards_llm_runtime_settings(tmp_path):
    manager = ConfigManager(str(tmp_path / "config.json"))
    manager.update(
        api_base_url="https://default.example/v1",
        api_key="default-key",
        model_name="default-model",
        backend_api_base_url="https://backend.example/v1",
        backend_api_key="backend-key",
        backend_model_name="backend-model",
        backend_llm_provider="anthropic",
        llm_max_retries=4,
        llm_max_tokens=2345,
        llm_temperature=0.73,
        backend_llm_temperature=0.0,
    )

    matcher = RealMatchService.from_config(manager)
    client = matcher.llm_client

    assert client.api_base_url == "https://backend.example/v1"
    assert client.api_key == "backend-key"
    assert client.model_name == "backend-model"
    assert client.provider == "anthropic"
    assert client.max_retries == 4
    assert client.max_tokens == 2345
    assert client.temperature == 0.0


def test_unlocated_direct_evidence_completes_with_low_confidence_warning():
    result = match_with(
        json.dumps(
            {
                "tier": "A",
                "summary": "声称完全匹配",
                "core_met_count": 1,
                "core_total": 1,
                "matched_evidence": [
                    {
                        "criterion": "无刷电机",
                        "evidence": "主导无刷电机量产项目",
                        "strength": "strong",
                    }
                ],
                "confidence": "high",
            },
            ensure_ascii=False,
        ),
        resume_text="候选人一直从事餐饮门店运营工作。",
    )

    assert result.status == "completed"
    assert result.tier == ""
    assert result.confidence == "low"
    assert "模型概括，未逐字定位" in result.risks
    assert result.matched_evidence[0]["grounding_status"] == "model_summary"


def test_partially_located_evidence_caps_high_confidence_at_medium():
    response = json.dumps(
        {
            "tier": "B",
            "summary": "存在直接事实和汇总判断",
            "core_met_count": 2,
            "core_total": 2,
            "matched_evidence": [
                {
                    "criterion": "结构设计",
                    "evidence": "近三年负责电机结构设计",
                    "strength": "strong",
                },
                {
                    "criterion": "项目经验",
                    "evidence": "累计负责多个无刷电机项目",
                    "strength": "medium",
                },
            ],
            "confidence": "high",
        },
        ensure_ascii=False,
    )

    result = match_with(response)

    assert result.status == "completed"
    assert result.confidence == "medium"
    assert [
        item["grounding_status"] for item in result.matched_evidence
    ] == ["exact", "model_summary"]
    assert "1 条匹配证据为模型概括" in result.risks


def test_model_cannot_self_report_grounding_status():
    response = json.dumps(
        {
            "tier": "B",
            "summary": "证据需要系统重新定位",
            "core_met_count": 1,
            "core_total": 1,
            "matched_evidence": [
                {
                    "evidence": "模型自行编写的概括",
                    "grounding_status": "exact",
                }
            ],
            "confidence": "high",
        },
        ensure_ascii=False,
    )

    result = match_with(response)

    assert result.status == "completed"
    assert result.matched_evidence[0]["grounding_status"] == "model_summary"
    assert result.confidence == "low"


def test_low_evidence_score_remains_audit_only_without_tier_rejection():
    quote = "参与相关产品需求整理和会议记录"
    result = match_with(
        json.dumps(
            {
                "tier": "A",
                "summary": "证据很弱却声称 A 档",
                "matched_evidence": [
                    {
                        "criterion": "产品经验",
                        "evidence": quote,
                        "strength": "weak",
                    }
                ],
                "missing_or_unclear": [
                    "缺口1",
                    "缺口2",
                    "缺口3",
                    "缺口4",
                    "缺口5",
                ],
                "confidence": "low",
            },
            ensure_ascii=False,
        ),
        resume_text="候选人{}。".format(quote),
    )

    assert result.status == "completed"
    assert result.tier == ""
    assert result.match_score < 45


def test_match_prompt_sends_raw_job_context_once_and_includes_capture_facts():
    client = StaticClient(
        json.dumps(
            {"tier": "C", "summary": "关键事实仍需确认"},
            ensure_ascii=False,
        )
    )
    matcher = RealMatchService(client)

    matcher.match_candidate(
        session_id="s1",
        round_id="r1",
        candidate_id="c1",
        resume_text="候选人简历",
        criteria={
            "criteria_version_id": "v1",
            "core_terms": ["LNG"],
            "jd_text": "JD_UNIQUE_MARKER",
            "user_notes": "NOTES_UNIQUE_MARKER",
        },
        structured_facts={"city": "深圳", "expected_salary": "40万"},
        capture_quality={
            "capture_status": "success",
            "resume_chars": 1200,
            "missing_sections": ["projects"],
        },
    )

    prompt = client.prompts[0]
    assert prompt.count("JD_UNIQUE_MARKER") == 1
    assert prompt.count("NOTES_UNIQUE_MARKER") == 1
    assert '"jd_text"' not in prompt
    assert "已解析的结构化事实" in prompt
    assert "expected_salary" in prompt
    assert "抓取完整度" in prompt
    assert "A/B/C/D" not in prompt
    assert "匹配档位" not in prompt


def test_match_result_attaches_stable_audit_and_cache_identity():
    response = json.dumps(
        {"tier": "C", "summary": "关键事实仍需确认"}, ensure_ascii=False
    )
    client = StaticClient(response)
    client.provider = "openai"
    client.model_name = "matcher-model"
    client.temperature = 0.35
    client.max_tokens = 2048
    matcher = RealMatchService(client)

    first = matcher.match_candidate(
        session_id="s1",
        round_id="r1",
        candidate_id="c1",
        resume_text="负责无刷电机研发",
        criteria={"criteria_version_id": "v1", "core_terms": ["无刷电机"]},
    )
    second = matcher.match_candidate(
        session_id="s1",
        round_id="r2",
        candidate_id="c1",
        resume_text="负责无刷电机研发",
        criteria={"criteria_version_id": "v1", "core_terms": ["无刷电机"]},
    )

    assert first.prompt_version == matcher.cache_identity["prompt_version"]
    assert first.model_name == "matcher-model"
    assert first.model_config_hash == matcher.cache_identity["model_config_hash"]
    assert len(first.model_config_hash) == 64
    assert len(first.input_hash) == 64
    assert len(first.resume_hash) == 64
    assert first.input_hash == second.input_hash
    assert first.resume_hash == second.resume_hash


def test_cache_identity_changes_with_model_generation_config():
    response = json.dumps({"tier": "C", "summary": "待确认"}, ensure_ascii=False)

    def identity(model_name, temperature):
        client = StaticClient(response)
        client.provider = "openai"
        client.model_name = model_name
        client.temperature = temperature
        client.max_tokens = 2048
        return RealMatchService(client).cache_identity

    baseline = identity("matcher-model", 0.2)

    assert identity("matcher-model-v2", 0.2) != baseline
    assert identity("matcher-model", 0.6) != baseline
