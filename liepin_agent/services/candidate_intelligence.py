"""Resume fact extraction and evidence-first criterion evaluation."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List


class CandidateIntelligenceService:
    extractor_version = "facts-v1"
    evaluator_version = "criterion-v2"

    def extract_facts(
        self, resume_text: str, structured_facts: Dict[str, Any] | None = None
    ) -> List[Dict[str, Any]]:
        resume = str(resume_text or "")
        result: List[Dict[str, Any]] = []
        for key, raw_value in (structured_facts or {}).items():
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                text = str(value or "").strip()
                if not text:
                    continue
                start = resume.find(text)
                result.append(
                    self._fact(
                        key,
                        text,
                        "structured",
                        start if start >= 0 else None,
                        0.95 if start >= 0 else 0.8,
                    )
                )
        cursor = 0
        section = "resume"
        headings = {
            "基本信息": "basic_info",
            "求职期望": "job_intention",
            "求职意向": "job_intention",
            "工作经历": "experience",
            "项目经历": "projects",
            "教育经历": "education",
            "自我评价": "summary",
            "技能": "skills",
        }
        for raw_line in resume.splitlines():
            line = raw_line.strip()
            position = resume.find(raw_line, cursor)
            cursor = max(cursor, position + len(raw_line))
            if not line:
                continue
            heading = next((value for key, value in headings.items() if key in line and len(line) <= 12), None)
            if heading:
                section = heading
                continue
            if len(line) < 4:
                continue
            result.append(self._fact("resume_line", line, section, position, 0.9))
            if len(result) >= 160:
                break
        deduped = []
        seen = set()
        for item in result:
            key = (item["fact_type"], self._norm(item["fact_value"]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def evaluate(
        self,
        criteria_items: Iterable[Dict[str, Any]],
        facts: Iterable[Dict[str, Any]],
        match_result: Any = None,
    ) -> List[Dict[str, Any]]:
        fact_list = list(facts or [])
        model_evidence = list(getattr(match_result, "matched_evidence", []) or [])
        direct_evidence = [
            item for item in model_evidence
            if str(item.get("source_type") or "direct") != "inferred"
        ]
        inferred_evidence = [
            item for item in model_evidence
            if str(item.get("source_type") or "direct") == "inferred"
        ]
        missing = " ".join(getattr(match_result, "missing_or_unclear", []) or [])
        risks = str(getattr(match_result, "risks", "") or "")
        dealbreaker_hit = bool(getattr(match_result, "dealbreaker_hit", False))
        result = []
        for item in criteria_items or []:
            criterion_id = str(item.get("id") or "")
            criterion_text = str(item.get("criterion_text") or "")
            criterion_type = str(item.get("criterion_type") or "preferred")
            observability = str(item.get("observability") or "resume")
            primary_terms = self._terms(
                [criterion_text, *(item.get("search_aliases") or [])]
            )
            alternative_terms = self._terms(item.get("alternatives") or [])
            evidence = self._model_evidence(direct_evidence, primary_terms, criterion_text)
            if not evidence:
                evidence = self._fact_evidence(fact_list, primary_terms)
            inferred = self._model_evidence(inferred_evidence, primary_terms, criterion_text)
            alternative_evidence = self._fact_evidence(fact_list, alternative_terms)

            if evidence:
                polarity, verdict_sourced = self._evidence_polarity(evidence)
                if polarity == "unknown" and inferred and criterion_type != "dealbreaker":
                    status = "inferred_met"
                    confidence = 0.65
                    evidence = inferred
                    reason = "存在有依据的间接证据，仍建议沟通确认"
                elif polarity == "not_met":
                    status = (
                        "conflict" if criterion_type == "dealbreaker" else "explicit_not_met"
                    )
                    confidence = 0.85 if verdict_sourced else 0.6
                    reason = (
                        "模型判定简历事实与该条件冲突"
                        if verdict_sourced
                        else "证据文本显示该条件不满足，建议人工复核"
                    )
                elif polarity == "unknown":
                    status = "unknown"
                    confidence = 0.45
                    reason = "模型判定简历未提供该条件相关信息，缺失不能判断为不符合"
                else:
                    status = "direct_met"
                    confidence = 0.9
                    reason = "命中可定位的简历事实证据"
            elif inferred and criterion_type != "dealbreaker":
                status = "inferred_met"
                confidence = 0.65
                evidence = inferred
                reason = "存在有依据的间接证据，仍建议沟通确认"
            elif alternative_evidence:
                status = "partial"
                confidence = 0.75
                evidence = alternative_evidence
                reason = "命中人工允许的替代背景"
            elif self._overlap(primary_terms, self._norm(risks)):
                status = (
                    "conflict"
                    if criterion_type == "dealbreaker" and dealbreaker_hit
                    else "explicit_not_met"
                )
                confidence = 0.65
                reason = "模型风险项明确指出该条件存在冲突"
            else:
                status = "unknown"
                confidence = 0.45 if self._overlap(primary_terms, self._norm(missing)) else 0.35
                reason = "该条件需要通过{}核实，缺失不能判断为不符合".format(
                    self._observability_label(observability)
                )
            verification_question = ""
            if status in {"unknown", "inferred_met", "partial"}:
                verification_question = "请确认候选人是否满足：{}".format(
                    criterion_text
                )
            result.append(
                {
                    "criterion_id": criterion_id,
                    "status": status,
                    "confidence": confidence,
                    "evidence": evidence[:5],
                    "reason": reason,
                    "verification_question": verification_question,
                    "evaluator_version": self.evaluator_version,
                }
            )
        return result

    _CONFLICT_PATTERNS = (
        "远超", "超出", "不满足", "不符合", "不符", "超标", "偏高", "过低",
    )
    _MISSING_PATTERNS = (
        "未显示", "未提及", "未见", "无法确认", "未填写", "信息缺失", "不详",
    )

    def _evidence_polarity(self, evidence: Iterable[Dict[str, Any]]) -> tuple:
        """Return (polarity, verdict_sourced) for a list of evidence entries.

        Model verdicts are authoritative. For legacy verdict-less model
        summaries, fall back to conservative text patterns; raw resume facts
        stay polarity-blind and default to "met".
        """
        items = list(evidence or [])
        for item in items:
            verdict = str(item.get("verdict") or "")
            if verdict in ("met", "not_met", "unknown"):
                return verdict, True
        model_text = " ".join(
            str(item.get("quote") or "")
            for item in items
            if item.get("section") == "model_grounded_evidence"
        )
        if model_text:
            if any(pattern in model_text for pattern in self._CONFLICT_PATTERNS):
                return "not_met", False
            if any(
                pattern in model_text for pattern in self._MISSING_PATTERNS
            ) or re.search(r"无.{0,12}证据", model_text):
                return "unknown", False
        return "met", False

    @staticmethod
    def _observability_label(value: str) -> str:
        return {
            "card": "候选人卡片或基础信息",
            "resume": "完整简历",
            "conversation": "沟通",
            "background_check": "背调",
        }.get(value, "完整简历")

    def _fact(self, fact_type: str, value: str, section: str, start: int | None, confidence: float) -> Dict[str, Any]:
        return {
            "fact_type": fact_type,
            "fact_value": value,
            "normalized_value": self._norm(value),
            "section": section,
            "evidence_quote": value,
            "evidence_start": start,
            "evidence_end": start + len(value) if start is not None else None,
            "confidence": confidence,
            "extractor_version": self.extractor_version,
        }

    def _model_evidence(
        self,
        values: Iterable[Dict[str, Any]],
        terms: List[str],
        criterion_text: str = "",
    ) -> List[Dict[str, Any]]:
        exact = []
        fuzzy = []
        criterion_norm = self._norm(criterion_text)
        for value in values:
            criterion = self._norm(value.get("criterion") or "")
            evidence = str(value.get("evidence") or "").strip()
            if not evidence:
                continue
            entry = {
                "quote": evidence,
                "section": "model_grounded_evidence",
                "start": None,
                "end": None,
                "grounding_status": value.get("grounding_status"),
                "verdict": value.get("verdict"),
            }
            if (
                criterion
                and criterion_norm
                and (criterion in criterion_norm or criterion_norm in criterion)
            ):
                exact.append(entry)
            elif self._overlap(terms, criterion + self._norm(evidence)):
                fuzzy.append(entry)
        return exact or fuzzy

    def _fact_evidence(self, facts: Iterable[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
        if not terms:
            return []
        ranked = []
        for fact in facts:
            text = self._norm(fact.get("fact_value") or "")
            hits = sum(1 for term in terms if term and term in text)
            if hits:
                ranked.append((hits, fact))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].get("evidence_start") or 10**9))
        return [
            {
                "quote": fact.get("evidence_quote") or fact.get("fact_value"),
                "section": fact.get("section") or "",
                "start": fact.get("evidence_start"),
                "end": fact.get("evidence_end"),
                "grounding_status": "exact",
            }
            for _, fact in ranked[:5]
        ]

    @classmethod
    def _terms(cls, values: Iterable[Any]) -> List[str]:
        result = []
        stop_terms = {
            "经验", "相关", "工作", "能力", "要求", "负责", "具备", "接受",
            "期望", "以内", "以上", "优先", "考虑", "熟悉", "了解", "地点",
            "城市", "学历", "性别", "籍贯", "年龄", "薪资", "周岁", "不限",
        }
        for value in values or []:
            normalized = cls._norm(value)
            if len(normalized) >= 2:
                result.append(normalized)
            for token in re.findall(r"[a-z0-9+#.]{2,}|[\u4e00-\u9fff]{2,8}", str(value or "").lower()):
                token = cls._norm(token)
                if token and len(token) >= 3 and token not in result and token not in stop_terms:
                    result.append(token)
                if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                    for size in (4, 3):
                        for start in range(0, len(token) - size + 1):
                            phrase = token[start : start + size]
                            if phrase not in stop_terms and phrase not in result:
                                result.append(phrase)
        return result[:20]

    @staticmethod
    def _overlap(terms: Iterable[str], text: str) -> bool:
        return any(term and term in text for term in terms)

    @staticmethod
    def _norm(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(character for character in normalized if character.isalnum() or character in "+#.")
