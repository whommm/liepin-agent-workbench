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
_SALARY_KEYWORDS = ("年薪", "月薪", "薪资", "期望", "目前", "k", "万", "元")


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
