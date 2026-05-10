"""Search planning heuristics for the Agent."""

from __future__ import annotations

import re
from typing import Dict, List

from ..domain.models import SearchPlan


CITY_NAMES = ["北京", "上海", "深圳", "广州", "杭州", "成都", "苏州", "南京", "武汉", "东莞", "惠州"]
POSITION_HINTS = ["产品", "研发", "算法", "结构", "设计", "运营", "销售", "市场", "财务", "人力"]
DOMAIN_TERMS = [
    "文创",
    "潮玩",
    "IP衍生品",
    "玩具",
    "3D打印",
    "供应链",
    "量产",
    "推荐系统",
    "搜索排序",
    "算法",
    "大模型",
    "结构设计",
    "照明",
    "灯具",
    "跨境",
    "SaaS",
    "增长",
    "商业化",
]


class Planner:
    """Generate first and follow-up search plans.

    This is intentionally deterministic at the planning layer. Later it can call an LLM and
    still return the same SearchPlan schema.
    """

    def build_criteria(self, jd_text: str, user_notes: str = "") -> Dict[str, object]:
        text = "{}\n{}".format(jd_text or "", user_notes or "")
        core_terms = self.extract_domain_terms(text)
        position_filter = self.infer_position_filter(text)
        requirements_text = self.build_requirements_text(text, core_terms, position_filter)
        return {
            "position_filter": position_filter,
            "core_terms": core_terms[:8],
            "negative_terms": ["实习", "应届", "客服", "行政"],
            "hard_requirements": self.extract_hard_requirements(text),
            "city_scope": self.extract_city_scope(text),
            "keywords_text": "\n".join(core_terms[:12]),
            "requirements_text": requirements_text,
        }

    def initial_plan(
        self, jd_text: str, user_notes: str = "", criteria: Dict[str, object] | None = None
    ) -> SearchPlan:
        text = "{}\n{}".format(jd_text or "", user_notes or "")
        position_filter = self.infer_position_filter(text)
        criteria = criteria or {}
        domain_terms = self._criteria_terms(criteria) or self.extract_domain_terms(text)
        if criteria.get("position_filter"):
            position_filter = str(criteria.get("position_filter") or position_filter)
        if len(domain_terms) >= 2:
            query = "{} {}".format(domain_terms[0], domain_terms[1])
        elif domain_terms:
            query = domain_terms[0]
        else:
            query = position_filter or "产品"
        filters = {
            "city": self.extract_city_scope(text),
            "active_days": 7,
        }
        work_years = self.extract_work_years(text)
        if work_years:
            filters["work_years"] = work_years
        education = self.extract_education(text)
        if education:
            filters["education"] = education
        return SearchPlan(
            query=query,
            position_filter=position_filter,
            scope="全部经历",
            match_mode="all",
            filters=filters,
            intent="第一轮验证核心业务场景是否存在可用候选人",
            expected_signal=domain_terms[:8],
            risk="第一轮可能混入泛岗位候选人，需要先看结果卡片质量",
            search_hypothesis_type="core_background",
            search_hypothesis_text="验证核心背景词：{}".format("、".join(domain_terms[:5])),
        )

    def next_plan(
        self,
        previous_plan: SearchPlan,
        jd_text: str,
        used_queries: List[str],
        noise_patterns: List[str],
    ) -> SearchPlan:
        text = jd_text or ""
        terms = self.extract_domain_terms(text)
        candidates = self._build_query_candidates(terms, previous_plan.position_filter)
        used = {query.strip() for query in used_queries or []}
        next_query = ""
        for query in candidates:
            if query and query not in used:
                next_query = query
                break
        if not next_query:
            next_query = "{} {}".format(previous_plan.query, "量产").strip()
        intent = "根据上一轮结果调整关键词，减少噪音并继续验证相邻场景"
        if noise_patterns:
            intent += "；重点规避：{}".format("、".join(noise_patterns[:3]))
        return SearchPlan(
            query=next_query,
            position_filter=previous_plan.position_filter,
            scope=previous_plan.scope,
            match_mode=previous_plan.match_mode,
            filters=dict(previous_plan.filters or {}),
            intent=intent,
            expected_signal=terms[:8],
            risk="后续轮次可能边际收益下降，需要观察 A/B 产出",
            search_hypothesis_type=self._infer_hypothesis_type(next_query, terms),
            search_hypothesis_text="验证搜索假设：{}".format(next_query),
        )

    @staticmethod
    def infer_title(jd_text: str) -> str:
        for pattern in [r"岗位名称[:：]\s*([^\n，,。]+)", r"职位[:：]\s*([^\n，,。]+)"]:
            match = re.search(pattern, jd_text or "")
            if match:
                return match.group(1).strip()
        first_line = (jd_text or "").strip().splitlines()[0:1]
        return first_line[0][:24] if first_line else "未命名岗位"

    @staticmethod
    def infer_position_filter(text: str) -> str:
        text = text or ""
        for hint in POSITION_HINTS:
            if hint in text:
                return hint
        return "产品"

    @staticmethod
    def extract_domain_terms(text: str) -> List[str]:
        result: List[str] = []
        for term in DOMAIN_TERMS:
            if term in (text or "") and term not in result:
                result.append(term)
        if not result:
            # Keep one sensible default so sparse JD text still produces a search hypothesis.
            result.extend(["文创", "潮玩", "IP衍生品", "量产"])
        return result

    @staticmethod
    def extract_city_scope(text: str) -> List[str]:
        for city in CITY_NAMES:
            if city in (text or ""):
                if city == "深圳":
                    return ["深圳", "广州", "东莞", "惠州"]
                return [city]
        return []

    @staticmethod
    def extract_work_years(text: str) -> str:
        match = re.search(r"(\d+)\s*[-~到至]\s*(\d+)\s*年", text or "")
        if match:
            return "{}-{}年".format(match.group(1), match.group(2))
        match = re.search(r"(\d+)\s*年\s*(?:以上|\+)", text or "")
        if match:
            return "{}年以上".format(match.group(1))
        return ""

    @staticmethod
    def extract_education(text: str) -> str:
        for item in ["博士", "硕士", "本科", "大专"]:
            if item in (text or ""):
                return item
        return ""

    @staticmethod
    def extract_hard_requirements(text: str) -> List[str]:
        result = []
        work_years = Planner.extract_work_years(text)
        education = Planner.extract_education(text)
        if work_years:
            result.append(work_years)
        if education:
            result.append(education)
        return result

    @staticmethod
    def build_requirements_text(
        text: str, core_terms: List[str], position_filter: str
    ) -> str:
        parts = []
        if core_terms:
            parts.append("重点关注候选人是否具备{}相关经验。".format("、".join(core_terms[:6])))
        if position_filter:
            parts.append("岗位方向收口为{}，需要结合简历证据判断是否真正做过相关工作。".format(position_filter))
        hard = Planner.extract_hard_requirements(text)
        if hard:
            parts.append("基础要求：{}。".format("、".join(hard)))
        return "".join(parts) or "请根据已确认关键词判断候选人与岗位要求的真实匹配度。"

    @staticmethod
    def _criteria_terms(criteria: Dict[str, object]) -> List[str]:
        text = str(criteria.get("keywords_text") or "")
        terms = []
        for line in text.replace("，", "\n").replace("、", "\n").splitlines():
            item = line.strip(" -\t,;；")
            if item and item not in terms:
                terms.append(item)
        if not terms:
            terms = [str(item) for item in criteria.get("core_terms", []) if str(item).strip()]
        return terms

    @staticmethod
    def _infer_hypothesis_type(query: str, terms: List[str]) -> str:
        if any(term in query for term in terms[:4]):
            return "core_background"
        if any(marker in query for marker in ["公司", "竞品", "客户", "供应商"]):
            return "target_company"
        return "transferable_scene"

    @staticmethod
    def _build_query_candidates(terms: List[str], position_filter: str) -> List[str]:
        pairs = []
        for i in range(len(terms)):
            if i + 1 < len(terms):
                pairs.append("{} {}".format(terms[i], terms[i + 1]))
        pairs.extend(terms)
        if position_filter == "产品":
            pairs.extend(["IP衍生品 文创衍生品", "文创产品 量产", "潮玩 供应链"])
        return pairs
