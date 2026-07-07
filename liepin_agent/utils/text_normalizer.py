"""Text normalization helpers for candidate resume extraction."""

import re
from typing import Iterable, List


NOISE_LINES = {
    # 操作按钮
    "在线沟通", "立即沟通", "交换电话", "意向沟通", "发消息",
    "收藏", "举报", "下载简历", "查看联系方式", "登录后查看",
    "关注", "不感兴趣", "投递简历", "申请职位", "预约面试",
    "邀请投递", "推荐给朋友", "分享简历", "返回顶部", "转发",
    # 导航/头部
    "我的主页", "个人中心", "安全中心", "账户资源", "用户规则",
    "通话管理", "安全退出", "候选人基础信息", "招聘官", "招聘者版",
    "我的职位", "我的简历", "消息", "通知", "设置",
    "每日任务", "中文简历", "查看大图",
    # 通用 UI
    "筛选条件", "找人", "全选", "包含全部关键词", "上一页",
    "下一页", "确定", "取消", "提交", "保存",
    "编辑", "删除", "更多", "展开", "收起",
    "加载中", "暂无数据", "重新加载", "刷新",
    "超级聊聊", "推荐职位", "开抢", "已上传个人作品",
    "简历备注", "添加备注", "简历洞察", "去查看", "简历信息",
    # 页面底部
    "关于猎聘", "联系我们", "帮助中心", "隐私政策", "用户协议",
    "加入猎聘", "友情链接",
    "算法备案信息", "投诉举报制度", "算法推荐服务说明",
    "All Rights Reserved",
    # 其他常见
    "您可能感兴趣", "相似推荐", "看过该简历的人还看了",
    "今日新投递", "活跃人才", "找人才就用猎聘",
    "TA上传了个人作品", "点击开聊向TA索要", "向TA索要",
}

NOISE_CONTAINS = {
    "猎聘 ©", "liepin.com", "lpt.liepin.com", "版权所有",
    "客服热线", "违法和不良信息举报", "营业执照",
    "人力资源服务许可证", "增值电信业务经营许可证",
    "京公网安备", "京ICP证", "京ICP备",
    "我知道了", "暂不提醒", "稍后查看",
    # Resume-detail page noise
    "简历编号：", "方便联系时间：", "最后一次登录时间：",
    "超级聊聊", "金领券", "已售出", "仅供公司招聘使用",
    "简历洞察", "一秒洞察", "去查看", "算法备案",
    "算法推荐", "违法和不良信息举报", "未成年人举报",
    "Copyright", "All Rights Reserved", "人才服务许可证",
    "ICP备", "ICP经营许可证", "ICP证", "公网安备", "营业执照",
}

NOISE_PATTERNS = [
    re.compile(r"^\d+$"),                       # 纯数字
    re.compile(r"^\d+条?$"),                     # "3条"
    re.compile(r"^第\d+页$"),                    # "第2页"
    re.compile(r"^[\d\.]+分$"),                  # "4.5分"
    re.compile(r"^\d+/\d+$"),                   # "1/10"
    re.compile(r"^[\-\*\•\·\s]+$"),             # 纯标点符号
    re.compile(r"^男\d+岁"),                     # "男40岁常州本科工作17年16k"
    re.compile(r"^\d+岁"),                       # "40岁"
    re.compile(r"\d+k.*")                      # "16k 共青团员"
]


# 薪资相关关键词，包含这些的行不应被噪音过滤误伤
_SALARY_KEYWORDS = ("年薪", "月薪", "薪资", "薪金", "期望", "目前", "k", "万", "元", "面议", "税前", "税后", "薪")

# 薪资数字模式：支持 "16k" "25k-35k" "16K·14薪" "年薪30万" "30-50万/年" "25000元/月" 等
# 匹配带单位/币种后缀的薪资表达，用于从简历行里结构化提取薪资信息。
_SALARY_VALUE_PATTERN = re.compile(
    r"(?:(?:年薪|月薪|目前|期望|当前|薪资|薪金)\s*[:：]?\s*)?"  # 可选前缀
    r"(\d+(?:\.\d+)?)"                                          # 数字
    r"\s*(k|K|万|w|W|元)"                                        # 单位
    r"(?:\s*[-~至到]\s*(\d+(?:\.\d+)?)"                          # 可选区间上限
    r"\s*(k|K|万|w|W|元)?)?"                                     # 可选上限单位
    r"(?:\s*[·•x×*]\s*\d+\s*薪)?"                               # 可选 "·14薪"
)

# 归一化薪资为"万元/年"的统一表示，便于和 JD 薪资区间对比。
# 输入：数字 + 单位（k/K/万/w/W/元）。k 视为千元/月，万视为万元/年（简历惯例），
# 元视为元/月。返回 (min_wan_year, max_wan_year, raw_text)，无法换算时 raw_text 保留原文。
_MONTHLY_K_TO_YEAR_WAN = 12 / 10  # 1k/月 ≈ 1.2万/年


def parse_salary_lines(lines):
    """从简历文本行里提取结构化薪资信息。

    返回 dict：
      {
        "current_salary": "原始文本，如 '28k·14薪'",
        "current_salary_wan_year": "估计的目前年薪万元区间，如 '33.6' 或 '30-40'",
        "expected_salary": "原始文本，如 '年薪30-50万'",
        "expected_salary_wan_year": "估计的期望年薪万元区间",
        "salary_lines": [命中的薪资行原文列表],
      }
    找不到的字段为空字符串/空列表。本函数只做正则提取与轻度换算，
    不做语义判断（"目前 vs 期望"靠行内/邻近关键词识别）。
    """
    result = {
        "current_salary": "",
        "current_salary_wan_year": "",
        "expected_salary": "",
        "expected_salary_wan_year": "",
        "salary_lines": [],
    }
    if not lines:
        return result
    full = "\n".join(lines)
    # 收集所有命中的薪资行
    hit_lines = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        if _SALARY_VALUE_PATTERN.search(line) or any(
            kw in line for kw in ("年薪", "月薪", "薪资", "期望薪资", "目前薪资")
        ):
            # 必须真的含数字薪资才算命中，避免 "薪资面议" 这种无数字的也乱抓
            if re.search(r"\d", line) and _SALARY_VALUE_PATTERN.search(line):
                hit_lines.append(line)
    result["salary_lines"] = hit_lines

    def _classify_and_fill(text_chunk):
        m = _SALARY_VALUE_PATTERN.search(text_chunk)
        if not m:
            return None
        low, unit_low, high, unit_high = m.group(1), m.group(2), m.group(3), m.group(4)
        wan_range = _salary_to_wan_year(low, unit_low, high, unit_high)
        return m.group(0).strip(), wan_range

    # 优先按"期望/期望薪资"识别 expected；按"目前/当前"识别 current。
    # 简历"求职期望"段里的薪资算期望，"目前"关键词算目前。
    expected_filled = False
    current_filled = False
    for line in hit_lines:
        if not expected_filled and any(
            kw in line for kw in ("期望", "求职期望", "意向")
        ):
            parsed = _classify_and_fill(line)
            if parsed:
                result["expected_salary"], result["expected_salary_wan_year"] = parsed
                expected_filled = True
                continue
        if not current_filled and any(kw in line for kw in ("目前", "当前", "现", "在职")):
            parsed = _classify_and_fill(line)
            if parsed:
                result["current_salary"], result["current_salary_wan_year"] = parsed
                current_filled = True
                continue
    # 兜底：未分类的命中，按出现顺序第一个填 expected、第二个填 current
    remaining = [ln for ln in hit_lines if ln not in (result["expected_salary"], result["current_salary"])]
    for line in remaining:
        parsed = _classify_and_fill(line)
        if not parsed:
            continue
        if not expected_filled:
            result["expected_salary"], result["expected_salary_wan_year"] = parsed
            expected_filled = True
        elif not current_filled:
            result["current_salary"], result["current_salary_wan_year"] = parsed
            current_filled = True
    return result


def _salary_to_wan_year(low, unit_low, high, unit_high):
    """把薪资数字+单位换算成万元/年的字符串区间。无法换算返回空串。"""
    def _one(value, unit):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        unit = (unit or "").lower()
        if unit in ("k",):
            return round(v * _MONTHLY_K_TO_YEAR_WAN, 1)  # k/月 → 万/年
        if unit in ("万", "w"):
            return v  # 已是万/年
        if unit in ("元",):
            return round(v * 12 / 10000, 1)  # 元/月 → 万/年
        return None

    low_w = _one(low, unit_low)
    high_w = _one(high, unit_high or unit_low) if high else None
    if low_w is None and high_w is None:
        return ""
    if high_w is None:
        return "{:.1f}".format(low_w)
    return "{:.1f}-{:.1f}".format(low_w, high_w)


def is_noise_line(line: str) -> bool:
    """Heuristic to decide whether a single line is page UI noise."""
    text = line.strip()
    if not text:
        return True
    # Exact match
    if text in NOISE_LINES:
        return True
    # Lines containing known noise substrings (relaxed length limit for detail-page noise)
    if len(text) <= 120:
        for keyword in NOISE_CONTAINS:
            if keyword in text:
                return True
    # Pattern-based noise — 但保护薪资相关信息
    is_salary_related = any(kw in text for kw in _SALARY_KEYWORDS)
    if not is_salary_related:
        for pattern in NOISE_PATTERNS:
            if pattern.match(text):
                return True
    # Very short lines that are not meaningful Chinese words
    if len(text) <= 2 and not re.search(r"[\u4e00-\u9fa5]{2}", text):
        return True
    return False


def clean_text_lines(lines: Iterable[str]) -> List[str]:
    """Normalize a sequence of text lines and remove obvious UI noise."""
    normalized = []
    for line in lines:
        line = re.sub(r"\s+", " ", (line or "").strip())
        if is_noise_line(line):
            continue
        normalized.append(line)
    return normalized


def build_resume_text(
    basic_lines: Iterable[str],
    summary_lines: Iterable[str],
    experience_lines: Iterable[str],
    project_lines: Iterable[str],
    education_lines: Iterable[str],
    extra_lines: Iterable[str],
    job_intention_lines: Iterable[str] = None,
) -> str:
    """Build a stable resume text structure for downstream matching."""

    sections = [
        ("候选人基础信息", clean_text_lines(basic_lines)),
        ("求职期望", clean_text_lines(job_intention_lines or [])),
        ("个人概述", clean_text_lines(summary_lines)),
        ("工作经历", clean_text_lines(experience_lines)),
        ("项目经历", clean_text_lines(project_lines)),
        ("教育经历", clean_text_lines(education_lines)),
        ("补充信息", clean_text_lines(extra_lines)),
    ]

    rendered = []
    for title, lines in sections:
        if not lines:
            continue
        rendered.append("【{}】".format(title))
        rendered.extend(lines)
        rendered.append("")
    return "\n".join(rendered).strip()


def build_resume_summary(lines: Iterable[str], limit: int = 220) -> str:
    """Build a compact summary for candidate list previews."""
    summary = " ".join(clean_text_lines(lines))
    if len(summary) > limit:
        return summary[:limit] + "..."
    return summary
