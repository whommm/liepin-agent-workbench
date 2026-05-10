"""Unit tests for search extraction logic without a real browser."""

import re

import pytest

from liepin_agent.core.search._extraction_mixin import _ExtractionMixin


class _TestableExtractionMixin(_ExtractionMixin):
    """Testable subclass that provides constants normally found on LiepinSearchService."""

    _AGE_PATTERN = re.compile(r"(\d+岁)")
    _EDUCATION_PATTERN = re.compile(
        r"(MBA/EMBA|EMBA|MBA|本科|硕士|博士|大专|中专|高中|初中)"
    )
    _WORK_YEARS_PATTERN = re.compile(r"(?:工作)?(\d+年(?:经验)?)")
    _SALARY_PATTERN = re.compile(r"\d+k(?:-\d+k)?")
    _COMPANY_MARKERS = (
        "有限公司",
        "有限责任公司",
        "股份公司",
        "公司",
        "集团",
        "研究院",
        "研究所",
        "事务所",
        "中心",
    )
    _JOB_KEYWORDS = (
        "工程师", "经理", "总监", "主管", "专员", "顾问", "设计师", "开发",
        "运营", "产品经理", "销售", "教师", "医生", "护士", "会计", "人事",
        "行政", "财务", "采购", "物流", "翻译", "记者", "律师", "研究员",
        "分析师", "架构师", "测试", "运维", "前端", "后端", "算法", "数据",
        "市场", "品牌", "公关", "助理", "秘书", "客服", "技术支持",
        "项目管理", "生产", "质量", "工艺", "制造", "设备", "机械", "电气",
        "自动化", "材料", "化工",
    )
    _PERSONAL_TAGS = (
        "男", "女", "已婚", "未婚", "共青团员", "党员", "群众", "预备党员", "民主党派",
    )
    _INVALID_CITY_WORDS = (
        "工作", "经验", "求职", "期望", "求职期望", "不限", "统招", "全日制",
        "MBA/EMBA", "EMBA", "MBA",
    )
    _COMPANY_TITLE_SEPARATORS = (" · ", "·", " | ", "|")
    FILTER_CARD_MARKERS = (
        "包含全部关键词", "没找到相关匹配项", "查看全部", "不限", "全部",
    )
    CANDIDATE_NOISE_MARKERS = (
        "在线", "今天活跃", "3天内活跃", "7天内活跃", "活跃状态", "隐藏",
        "查看联系方式", "立即沟通", "交换电话", "收藏", "举报",
    )


class TestExtractionMixin:
    @pytest.fixture
    def mixin(self):
        return _TestableExtractionMixin()

    def test_clean_candidate_lines_basic(self, mixin):
        lines = [
            "张三",
            "32岁",
            "本科 | 8年经验",
            "北京",
        ]
        cleaned, name, age, title, company, city, work_years, education = mixin._clean_candidate_lines(lines)
        assert name == "张三"
        assert age == "32岁"
        assert education == "本科"
        assert work_years == "8年经验"
        assert city == "北京"

    def test_clean_candidate_lines_filters_noise(self, mixin):
        lines = [
            "张三",
            "在线",
            "今天活跃",
            "立即沟通",
            "32岁",
        ]
        cleaned, name, age, title, company, city, work_years, education = mixin._clean_candidate_lines(lines)
        assert name == "张三"
        assert "在线" not in cleaned
        assert "立即沟通" not in cleaned

    def test_split_company_title_with_separator(self, mixin):
        # Title looks like a job title (contains manager keyword)
        company, title = mixin._split_company_title("字节跳动 | 产品经理")
        assert company == "字节跳动"
        assert title == "产品经理"

    def test_split_company_title_no_separator(self, mixin):
        company, title = mixin._split_company_title("销售总监")
        assert company == ""
        assert title == ""

    def test_looks_like_title(self, mixin):
        assert mixin._looks_like_title("产品经理") is True
        assert mixin._looks_like_title("工程师") is True
        assert mixin._looks_like_title("北京") is False
        assert mixin._looks_like_title("本科") is False
        assert mixin._looks_like_title("") is False

    def test_looks_like_city(self, mixin):
        assert mixin._looks_like_city("北京") is True
        assert mixin._looks_like_city("上海") is True
        assert mixin._looks_like_city("本科") is False
        assert mixin._looks_like_city("8年经验") is False
        assert mixin._looks_like_city("") is False
