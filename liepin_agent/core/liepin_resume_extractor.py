"""Candidate detail extraction and resume normalization for Liepin."""

import json
import logging
from typing import Dict, List

from .liepin_search_service import LiepinSearchCandidate
from ..models import Candidate
from ..utils.text_normalizer import build_resume_summary, build_resume_text

try:
    from playwright.sync_api import Error, Page
except ImportError:  # pragma: no cover
    Error = Exception
    Page = None

logger = logging.getLogger(__name__)


class LiepinResumeExtractionError(Exception):
    """Raised when candidate detail extraction fails."""


class LiepinResumeExtractor:
    """Extract candidate detail sections from a Liepin detail page."""

    # Updated selectors based on live page inspection (May 2026)
    # Primary: heading text-based selectors (most reliable)
    # Fallback: class-based selectors (backward compatibility)
    BASIC_INFO_SELECTORS = [
        # Primary: section following the basic info heading (first heading in resume)
        "xpath=//heading[contains(text(), '简历编号')]/following-sibling::*[1]",
        # Fallback: look for the first content block after header
        ".resume-header",
        ".resume-info",
        ".basic-info",
        ".base-info",
        ".candidate-info",
    ]
    SUMMARY_SELECTORS = [
        # Primary: heading text-based
        'heading:has-text("自我评价")',
        'xpath=//*[contains(text(), "自我评价")]/following-sibling::*[1]',
        # Fallback: class-based
        ".self-evaluation",
        ".summary-section",
        ".resume-summary",
        ".summary-info",
    ]
    EXPERIENCE_SELECTORS = [
        # Primary: heading text-based
        'heading:has-text("工作经历")',
        'xpath=//*[contains(text(), "工作经历")]/following-sibling::*[1]',
        # Fallback: class-based
        ".work-experience",
        ".resume-work",
        ".work-section",
        ".experience-section",
    ]
    PROJECT_SELECTORS = [
        # Primary: heading text-based
        'heading:has-text("项目经历")',
        'xpath=//*[contains(text(), "项目经历")]/following-sibling::*[1]',
        # Fallback: class-based
        ".project-experience",
        ".resume-project",
        ".project-section",
    ]
    EDUCATION_SELECTORS = [
        # Primary: heading text-based
        'heading:has-text("教育经历")',
        'xpath=//*[contains(text(), "教育经历")]/following-sibling::*[1]',
        # Fallback: class-based
        ".education-experience",
        ".resume-education",
        ".education-section",
    ]
    EXTRA_SELECTORS = [
        # Primary: heading text-based
        'heading:has-text("语言能力")',
        'heading:has-text("技能")',
        'xpath=//*[contains(text(), "语言能力") or contains(text(), "技能")]/following-sibling::*[1]',
        # Fallback: class-based
        ".skill-section",
        ".resume-extra",
        ".additional-info",
        ".skill-info",
    ]
    JOB_INTENTION_SELECTORS = [
        # Primary: heading text-based
        'heading:has-text("求职期望")',
        'heading:has-text("求职意向")',
        'heading:has-text("期望工作")',
        'xpath=//*[contains(text(), "求职期望") or contains(text(), "求职意向") or contains(text(), "期望工作")]/following-sibling::*[1]',
        # Fallback: class-based
        ".job-intention",
        ".intention-section",
        ".expectation-section",
        ".resume-intention",
    ]

    def extract_candidate(
        self, page: Page, summary: LiepinSearchCandidate
    ) -> Candidate:
        """Extract a normalized candidate from the current detail page."""
        sections = self.extract_sections(page)
        resume_text = build_resume_text(
            basic_lines=sections.get("basic_info", []),
            summary_lines=sections.get("summary", []),
            experience_lines=sections.get("experience", []),
            project_lines=sections.get("projects", []),
            education_lines=sections.get("education", []),
            extra_lines=sections.get("extra", []),
            job_intention_lines=sections.get("job_intention", []),
        )
        summary_lines = (
            sections.get("basic_info", [])
            + sections.get("summary", [])
            + sections.get("experience", [])[:3]
        )
        return Candidate(
            id="",
            profile_url=summary.profile_url,
            name=summary.name,
            current_title=summary.current_title,
            current_company=summary.current_company,
            city=summary.city,
            work_years=summary.work_years,
            education=summary.education,
            resume_text=resume_text,
            resume_summary=build_resume_summary(summary_lines or [summary.summary]),
            raw_payload_json=json.dumps(sections, ensure_ascii=False),
        )

    SEARCH_PAGE_MARKERS = (
        "筛选条件",
        "找人",
        "交换电话",
        "立即沟通",
        "意向沟通",
        "全选",
        "包含全部关键词",
        "没找到相关匹配项",
        "上一页",
        "下一页",
        "页",
    )
    RESUME_PAGE_MARKERS = (
        "工作经历",
        "教育经历",
        "项目经历",
        "自我评价",
        "工作职责",
        "工作业绩",
        "在职时间",
        "本科",
        "硕士",
        "博士",
        "大专",
        "中专",
    )

    def _looks_like_search_page(self, lines: List[str]) -> bool:
        """Heuristic to detect whether the detail page was redirected to search."""
        text = "\n".join(lines)
        search_hits = sum(1 for marker in self.SEARCH_PAGE_MARKERS if marker in text)
        resume_hits = sum(1 for marker in self.RESUME_PAGE_MARKERS if marker in text)
        # If it looks like a search page and lacks resume markers, treat as redirect
        return search_hits >= 3 and resume_hits < 2

    def extract_sections(self, page: Page) -> Dict[str, List[str]]:
        """Extract all structured sections from the current page."""
        sections = {
            "basic_info": self._extract_first_section(page, self.BASIC_INFO_SELECTORS),
            "summary": self._extract_first_section(page, self.SUMMARY_SELECTORS),
            "experience": self._extract_first_section(page, self.EXPERIENCE_SELECTORS),
            "projects": self._extract_first_section(page, self.PROJECT_SELECTORS),
            "education": self._extract_first_section(page, self.EDUCATION_SELECTORS),
            "extra": self._extract_first_section(page, self.EXTRA_SELECTORS),
            "job_intention": self._extract_first_section(page, self.JOB_INTENTION_SELECTORS),
        }
        if not any(sections.values()):
            # Fallback: extract full body text as basic_info so we don't lose everything
            logger.warning(
                "liepin_resume_extractor: structured selectors returned empty, falling back to body text"
            )
            fallback = self._extract_first_section(page, ["body"])
            if fallback:
                if self._looks_like_search_page(fallback):
                    raise LiepinResumeExtractionError(
                        "当前页面被重定向到找人/搜索页，未获取到简历内容"
                    )
                sections["basic_info"] = fallback
                return sections
            raise LiepinResumeExtractionError("未提取到简历详情内容，请检查详情页结构")
        return sections

    def _extract_first_section(self, page: Page, selectors: List[str]) -> List[str]:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    text = locator.inner_text(timeout=1500)
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    if lines:
                        return lines
            except Exception:
                continue
        return []
