"""Regression tests for per-criterion verdict polarity in the match pipeline.

Covers the bug where any keyword-overlapping evidence was treated as proof of
satisfaction, producing "直接满足" for failed dealbreakers and contradicting
the LLM's own summary/risks/recommendation.
"""

from __future__ import annotations

from liepin_agent.core.liepin_resume_extractor import LiepinResumeExtractor
from liepin_agent.core.liepin_search_service import LiepinSearchService
from liepin_agent.core.search._models import LiepinSearchCandidate
from liepin_agent.domain.match_output import MatchOutput
from liepin_agent.services.candidate_intelligence import CandidateIntelligenceService
from liepin_agent.services.candidate_ranking import CandidateRankingService


class _FakeMatch:
    def __init__(self, matched_evidence, inferred_evidence=None, risks=None,
                 missing=None, dealbreaker_hit=False):
        self.matched_evidence = matched_evidence
        self.inferred_evidence = inferred_evidence or []
        self.risks = risks or []
        self.missing_or_unclear = missing or []
        self.dealbreaker_hit = dealbreaker_hit


# ---------------------------------------------------------------------------
# Contract: verdict parsing
# ---------------------------------------------------------------------------

def test_match_output_parses_verdict_aliases():
    output = MatchOutput.model_validate(
        {
            "summary": "s",
            "matched_evidence": [
                {"criterion": "年龄32岁以内", "evidence": "44岁", "verdict": "不满足"},
                {"criterion": "学历本科", "evidence": "南京师范大学本科", "verdict": "met"},
                {"criterion": "性别女", "evidence": "未显示", "verdict": "无法确认"},
            ],
            "inferred_evidence": [
                {"criterion": "行业背景", "evidence": "新能源猎头经验"}
            ],
        }
    )
    assert [item.verdict for item in output.matched_evidence] == [
        "not_met", "met", "unknown",
    ]
    assert output.inferred_evidence[0].verdict == "inferred"
    dumped = output.evidence_for_match_result()
    assert dumped[0]["verdict"] == "not_met"


def test_deterministic_score_ignores_not_met_evidence():
    payload = {
        "summary": "s",
        "confidence": "high",
        "matched_evidence": [
            {"criterion": "年龄32岁以内", "evidence": "44岁，远超32岁",
             "strength": "strong", "verdict": "not_met"},
        ],
    }
    score = MatchOutput.model_validate(payload).deterministic_score()
    assert score == 0  # 唯一证据为 not_met，不应贡献任何质量/覆盖分


# ---------------------------------------------------------------------------
# Evaluator polarity
# ---------------------------------------------------------------------------

def test_verdict_not_met_on_must_is_explicit_not_met():
    svc = CandidateIntelligenceService()
    evaluations = svc.evaluate(
        [{"id": "1", "criterion_text": "年龄32岁以内", "criterion_type": "must"}],
        [],
        _FakeMatch([
            {"criterion": "年龄32岁以内", "evidence": "结构化数据年龄44岁，远超32岁",
             "verdict": "not_met"},
        ]),
    )
    assert evaluations[0]["status"] == "explicit_not_met"
    assert evaluations[0]["confidence"] == 0.85


def test_verdict_not_met_on_dealbreaker_is_conflict():
    svc = CandidateIntelligenceService()
    evaluations = svc.evaluate(
        [{"id": "1", "criterion_text": "年龄32岁以内", "criterion_type": "dealbreaker"}],
        [],
        _FakeMatch([
            {"criterion": "年龄32岁以内", "evidence": "44岁", "verdict": "not_met"},
        ]),
    )
    assert evaluations[0]["status"] == "conflict"


def test_verdict_met_on_dealbreaker_is_not_conflict():
    """Old code flagged ANY evidence on a dealbreaker criterion as conflict."""
    svc = CandidateIntelligenceService()
    evaluations = svc.evaluate(
        [{"id": "1", "criterion_text": "年龄32岁以内", "criterion_type": "dealbreaker"}],
        [],
        _FakeMatch([
            {"criterion": "年龄32岁以内", "evidence": "28岁", "verdict": "met"},
        ]),
    )
    assert evaluations[0]["status"] == "direct_met"


def test_verdict_unknown_stays_unknown():
    svc = CandidateIntelligenceService()
    evaluations = svc.evaluate(
        [{"id": "1", "criterion_text": "性别为女性（不要男生）", "criterion_type": "must"}],
        [],
        _FakeMatch([
            {"criterion": "性别为女性", "evidence": "简历未显示性别字段，无法确认",
             "verdict": "unknown"},
        ]),
    )
    assert evaluations[0]["status"] == "unknown"
    assert evaluations[0]["verification_question"]


def test_legacy_evidence_text_polarity_fallback():
    """Verdict-less legacy model summaries use conservative text patterns."""
    svc = CandidateIntelligenceService()
    evaluations = svc.evaluate(
        [
            {"id": "1", "criterion_text": "年龄32岁以内", "criterion_type": "must"},
            {"id": "2", "criterion_text": "性别为女性（不要男生）", "criterion_type": "must"},
        ],
        [],
        _FakeMatch([
            {"criterion": "年龄32岁以内", "evidence": "结构化数据年龄44岁，远超32岁"},
            {"criterion": "性别为女性", "evidence": "简历未显示性别字段，无法确认"},
        ]),
    )
    assert evaluations[0]["status"] == "explicit_not_met"
    assert evaluations[0]["confidence"] == 0.6
    assert evaluations[1]["status"] == "unknown"


def test_criterion_mapping_does_not_steal_unrelated_evidence():
    """薪资条件不得再通过“期望”碎片词命中年龄/地点证据。"""
    svc = CandidateIntelligenceService()
    evaluations = svc.evaluate(
        [{"id": "1", "criterion_text": "薪资期望在20K以内", "criterion_type": "must"}],
        [],
        _FakeMatch([
            {"criterion": "年龄32岁以内", "evidence": "结构化数据年龄44岁，远超32岁",
             "verdict": "not_met"},
            {"criterion": "工作地点在杭州余杭区",
             "evidence": "当前所在地无锡，期望城市未填写，无杭州base证据"},
        ]),
    )
    assert evaluations[0]["status"] == "unknown"
    assert evaluations[0]["evidence"] == []


def test_raw_resume_facts_stay_polarity_blind():
    """简历原文里的“超出”等词不应触发误判（兜底路径保持旧行为）。"""
    svc = CandidateIntelligenceService()
    facts = svc.extract_facts("工作经历\n某机械公司\n负责工业压缩机大客户销售，业绩超出目标30%")
    evaluations = svc.evaluate(
        [{"id": "1", "criterion_text": "有工业压缩机销售经验", "criterion_type": "must"}],
        facts,
    )
    assert evaluations[0]["status"] == "direct_met"


# ---------------------------------------------------------------------------
# Ranker integration: hard mismatch reaches recommendation state
# ---------------------------------------------------------------------------

def test_hard_not_met_drives_explicit_mismatch_and_low_rank():
    ranking = CandidateRankingService(store=None)
    base_candidate = {"match_score": 0, "confidence": "high"}
    met = ranking._score(
        [
            {"criterion_type": "must", "criterion_text": "学历本科", "weight": 0.9,
             "status": "direct_met", "confidence": 0.9},
            {"criterion_type": "must", "criterion_text": "年龄32岁以内", "weight": 0.9,
             "status": "direct_met", "confidence": 0.9},
        ],
        base_candidate,
    )
    failed = ranking._score(
        [
            {"criterion_type": "must", "criterion_text": "学历本科", "weight": 0.9,
             "status": "direct_met", "confidence": 0.9},
            {"criterion_type": "must", "criterion_text": "年龄32岁以内", "weight": 0.9,
             "status": "explicit_not_met", "confidence": 0.85},
        ],
        base_candidate,
    )
    assert failed["recommendation_state"] == "explicit_mismatch"
    assert failed["rank_score"] < met["rank_score"]


# ---------------------------------------------------------------------------
# Gender capture
# ---------------------------------------------------------------------------

def test_card_gender_token_extracted():
    service = LiepinSearchService.__new__(LiepinSearchService)
    _, _, _, _, _, _, _, _, gender = service._clean_candidate_lines(
        ["蒋**", "女 44岁 工作21年 本科 无锡", "某集团 · 招聘经理"]
    )
    assert gender == "女"


def test_card_gender_token_not_misparsed():
    service = LiepinSearchService.__new__(LiepinSearchService)
    lines = ["李**", "男女不限 27岁 本科 上海", "某公司 · 销售经理", "照顾子女"]
    assert service._clean_candidate_lines(lines)[8] == ""


def test_detect_gender_from_basic_lines_and_label():
    extractor = LiepinResumeExtractor()
    assert extractor._detect_gender("", ["女 44岁 工作21年 本科 无锡"]) == "女"
    assert extractor._detect_gender("", ["男 | 本科 | 上海"]) == "男"
    assert extractor._detect_gender("基本信息\n性别：男\n其他", []) == "男"
    assert extractor._detect_gender("【求职期望】\n招聘经理", []) == ""


def test_structured_attributes_gender_fallback_chain():
    extractor = LiepinResumeExtractor()
    sections = {"job_intention": [], "basic_info": [], "summary": []}
    # 1) 卡片字段优先
    summary = LiepinSearchCandidate(gender="男")
    assert extractor._extract_structured_attributes(sections, summary, "")["gender"] == "男"
    # 2) 详情页 body 头部行（姓名旁的性别标签）
    summary = LiepinSearchCandidate()
    header = ["猎聘", "找人", "蒋**", "女", "44岁", "无锡", "21年经验", "本科"]
    assert extractor._extract_structured_attributes(
        sections, summary, "", header_lines=header
    )["gender"] == "女"
    # 3) 卡片文本兜底
    summary = LiepinSearchCandidate(summary="蒋**\n女 44岁 工作21年 本科 无锡")
    assert extractor._extract_structured_attributes(sections, summary, "")["gender"] == "女"
    # 4) 都没有则为空（匹配时按 unknown 处理，而不是编造）
    summary = LiepinSearchCandidate(summary="蒋**\n44岁 工作21年 本科 无锡")
    assert extractor._extract_structured_attributes(sections, summary, "")["gender"] == ""
