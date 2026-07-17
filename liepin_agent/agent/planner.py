"""Search planning heuristics for the Agent."""

from __future__ import annotations

import re
from typing import Dict, List

from ..domain.job_profile import normalize_job_profile

from ..domain.models import SearchPlan


import json
from pathlib import Path


def _load_terms():
    """Load planner term lists from JSON so users can edit them without touching code."""
    default = {
        "city_names": [
            "北京", "上海", "深圳", "广州", "杭州", "成都", "苏州", "南京", "武汉",
            "东莞", "惠州", "西安", "长沙", "重庆", "天津", "宁波", "青岛", "厦门",
            "无锡", "佛山", "福州", "济南", "合肥", "昆明", "郑州", "大连",
        ],
        "position_hints": [
            "电机", "算法", "结构", "研发", "设计", "运营", "销售", "市场", "财务", "人力", "产品",
            "车间主任", "生产经理", "生产厂长", "制造经理", "生产主管", "工艺", "质量", "设备",
        ],
        "domain_terms": [
            "文创", "潮玩", "IP衍生品", "玩具", "3D打印", "供应链", "量产",
            "推荐系统", "搜索排序", "算法", "大模型", "结构设计", "照明", "灯具",
            "跨境", "SaaS", "增长", "商业化", "无刷电机", "电机", "轨道交通",
            "天然气", "小家电", "水泵", "压缩机", "制冷", "LNG", "BOG",
            "销售总监", "销售经理", "研发经理",
        ],
    }
    try:
        path = Path(__file__).with_name("config") / "planner_terms.json"
        if not path.exists():
            path = Path(__file__).parent.parent / "config" / "planner_terms.json"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return (
                data.get("city_names") or default["city_names"],
                data.get("position_hints") or default["position_hints"],
                data.get("domain_terms") or default["domain_terms"],
            )
    except Exception:
        pass
    return default["city_names"], default["position_hints"], default["domain_terms"]


CITY_NAMES, POSITION_HINTS, DOMAIN_TERMS = _load_terms()


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
        result = {
            "position_filter": position_filter,
            "core_terms": core_terms[:8],
            "negative_terms": ["实习", "应届", "客服", "行政"],
            "hard_requirements": self.extract_hard_requirements(text),
            "city_scope": self.extract_city_scope(text),
            "keywords_text": "\n".join(core_terms[:12]),
            "requirements_text": requirements_text,
            "gender_requirement": self.extract_gender_requirement(text),
        }
        criteria_items, personas = normalize_job_profile(result)
        result["criteria_items"] = criteria_items
        result["personas"] = personas
        return result

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
        # Emergency fallback must preserve the same recall safeguards as the
        # LLM plan path. Only a city scope carried by the confirmed criteria is
        # eligible for a platform filter; education, age and gender remain
        # matching facts rather than automatic search exclusions.
        filters = {"active_days": 30}
        confirmed_cities = [
            str(item).strip()
            for item in (criteria.get("city_scope") or [])
            if str(item).strip()
        ]
        if confirmed_cities:
            filters["city"] = confirmed_cities
        if position_filter and position_filter in query:
            position_filter = ""
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
            risk="后续轮次可能边际收益下降，需要观察有效候选产出",
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
        # Try to extract position from title patterns
        import re
        for pattern in [
            r'(?:岗位|职位|招聘)[：:\s]*([\u4e00-\u9fa5]{2,10})',
            r'(?:招聘|拟聘|岗位)[\s:：]+([\u4e00-\u9fa5]{2,10})(?:\d+|$)',
            r'^([\u4e00-\u9fa5]{2,8})(?:经理|工程师|总监|主管|专员|顾问|销售|开发|主任|厂长)',
        ]:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def extract_domain_terms(text: str) -> List[str]:
        result: List[str] = []
        for term in DOMAIN_TERMS:
            if term in (text or "") and term not in result:
                result.append(term)
        if not result:
            # Fallback: extract meaningful terms from the JD text itself
            # instead of hard-coding a specific industry.
            result = Planner._fallback_terms_from_text(text or "")
        return result

    @staticmethod
    def _fallback_terms_from_text(text: str) -> List[str]:
        """Extract candidate keywords from raw JD text when no DOMAIN_TERMS match."""
        import re
        terms: List[str] = []
        seen = set()

        # Stop-words / verb prefixes that usually produce sentence fragments
        STOP_PREFIXES = (
            "负责", "建立", "完成", "进行", "开展", "组织", "协调", "参与", "协助",
            "配合", "根据", "按照", "依据", "通过", "需要", "要求", "必须", "具备",
            "具有", "拥有", "熟悉", "了解", "掌握", "能够", "可以", "独立", "主导",
            "带领", "岗位", "职位", "职责", "任职", "工作", "相关", "优先", "以上",
            "以下", "以内", "左右", "至少", "不少于", "就是", "必须", "最大",
            "负责所有", "建立产品", "负责项目", "负责新", "公司目前",
        )

        def _is_clean(t: str) -> bool:
            if not t or len(t) < 2 or len(t) > 8:
                return False
            if any(t.startswith(p) for p in STOP_PREFIXES):
                return False
            # Avoid fragments that end with grammatical particles
            if t.endswith(("的", "了", "和", "与", "或", "等", "及", "以", "要")):
                return False
            return True

        # 1. Extract quoted phrases
        for match in re.finditer(r'[""]([^""]{2,20})[""]', text):
            t = match.group(1).strip()
            if _is_clean(t) and t not in seen:
                terms.append(t)
                seen.add(t)

        # 2. Extract title/position mentions (e.g. "岗位：研发经理")
        for pattern in [
            r'(?:岗位|职位|招聘|拟聘)[：:\s]*([\u4e00-\u9fa5]{2,10})',
        ]:
            for match in re.finditer(pattern, text):
                t = match.group(1).strip()
                if _is_clean(t) and t not in seen:
                    terms.append(t)
                    seen.add(t)

        # 3. Extract clean noun phrases before顿号/逗号 (2-8 CJK chars)
        for match in re.finditer(r'([\u4e00-\u9fa5]{2,8})(?=[、，,；;])', text):
            t = match.group(1).strip()
            if _is_clean(t) and t not in seen:
                terms.append(t)
                seen.add(t)

        # 4. Extract core skill phrases (X经验 / Y背景 / Z技能)
        for match in re.finditer(r'([\u4e00-\u9fa5]{2,8})(?:经验|背景|技能|能力)', text):
            t = match.group(1).strip()
            if _is_clean(t) and t not in seen:
                terms.append(t)
                seen.add(t)

        # 5. If still too few, extract 2-6 char phrases before common separators
        if len(terms) < 3:
            for match in re.finditer(r'([\u4e00-\u9fa5]{2,6})(?:设计|开发|管理|制造|生产|测试|维护|优化)', text):
                t = match.group(1).strip()
                if _is_clean(t) and t not in seen:
                    terms.append(t)
                    seen.add(t)

        return terms[:12] if terms else []

    @staticmethod
    def extract_city_scope(text: str) -> List[str]:
        # 先尝试县级市归一化：JD 写"浙江金华义乌"时也要能识别到地级市"""
        # 而非因为 CITY_NAMES 里没有"义乌"就把整段地点漏掉。
        # _COUNTY_TO_PREFECTURE 的值本身就是合法地级市，直接信任即可。
        from .city_normalizer import _COUNTY_TO_PREFECTURE

        for county, prefecture in _COUNTY_TO_PREFECTURE.items():
            if county in (text or ""):
                return [prefecture]
        for city in CITY_NAMES:
            if city in (text or ""):
                if city == "深圳":
                    return ["深圳", "广州", "东莞", "惠州"]
                return [city]
        return []

    @staticmethod
    def extract_gender_requirement(text: str) -> str:
        """Extract gender requirement from JD text.

        支持两种 JD 写法：
        1. 连写词：限男 / 男士优先 / 限女性 / 男女不限 …
        2. 结构化"标签: 值"格式，值常常另起一行，例如
              性别要求：
              男
           或 性别要求：男 / 性别:女 / 性别 男

        注意：不能简单匹配单字"男""女"——它们在正文里太常见（如"男女搭配"
        "男装产品经理"）。必须把单字识别限制在"性别"标签上下文里，否则会误伤。
        """
        text = text or ""

        # 1) 结构化"性别[要求] : 男/女"格式（兼容中英文冒号、空格、换行）。
        #    只取标签后 30 个字符的窗口，避免一路扫到正文里的"男/女"字误伤。
        gender_label = re.search(
            r"性别(?:要求)?\s*[:：]?\s*([^\n]{0,30})",
            text,
        )
        if gender_label:
            window = gender_label.group(1)
            # 窗口里出现"男"且没有并列的"女"→ 男；反之→ 女
            has_male = "男" in window
            has_female = "女" in window
            if has_male and not has_female:
                return "男"
            if has_female and not has_male:
                return "女"
            # 同时出现（如"男女均可"）或都不在窗口 → 落到下面"不限"判断

        # 2) 连写词匹配（原有的语义，保留兼容散文式 JD）
        # Male-only patterns
        if re.search(r"限男|限男性|只要男|仅男|仅限男|男士优先|男性优先|要求男|需要男|必须是男", text):
            return "男"
        # Female-only patterns
        if re.search(r"限女|限女性|只要女|仅女|仅限女|女士优先|女性优先|要求女|需要女|必须是女", text):
            return "女"
        # Gender-neutral / unlimited
        if re.search(r"男女不限|性别不限|不限性别|男女均可|男女皆可", text):
            return "不限"
        return ""

    @staticmethod
    def extract_age(text: str) -> str:
        """Extract age upper limit or range from JD text."""
        text = text or ""
        # Match patterns like "40岁以内", "40周岁以下", "不超过40岁", "年龄40岁以下"
        match = re.search(r"(?:年龄|年龄要求|年龄限制)?[:：\s]*(?:不超过|不大于|小于|低于|)?\s*(\d+)\s*周岁?\s*(?:以内|以下|之内|上限|下|内)", text)
        if match:
            return match.group(1)
        # Match "年龄：25-35岁" or "年龄25~35"
        match = re.search(r"(?:年龄|年龄要求)[:：\s]*(\d+)\s*(?:-|~|到|至|,)\s*(\d+)\s*周岁?", text)
        if match:
            return "{}-{}".format(match.group(1), match.group(2))
        # Match "40岁" near age-related keywords
        match = re.search(r"年龄(?:要求|限制)?[:：\s]*(\d+)\s*周岁?", text)
        if match:
            return match.group(1)
        return ""

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
        age = Planner.extract_age(text)
        if work_years:
            result.append(work_years)
        if education:
            result.append(education)
        if age:
            result.append("{}岁以内".format(age) if "-" not in age else "年龄{}".format(age))
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
        if position_filter and position_filter not in pairs:
            pairs.append(position_filter)
        return pairs
