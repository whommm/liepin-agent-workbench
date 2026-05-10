"""Excel export from SQLite candidate data."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill

from ..storage.sqlite_store import SQLiteStore, from_json, now_text


class ExportService:
    _JOB_KEYWORDS = (
        "工程师",
        "经理",
        "总监",
        "主管",
        "专员",
        "顾问",
        "设计师",
        "开发",
        "运营",
        "产品经理",
        "销售",
        "会计",
        "人事",
        "行政",
        "财务",
        "采购",
        "物流",
        "分析师",
        "架构师",
        "测试",
        "运维",
        "市场",
        "品牌",
        "助理",
        "客服",
        "技术支持",
        "项目管理",
        "生产",
        "质量",
        "工艺",
        "制造",
        "设备",
        "机械",
        "电气",
        "自动化",
        "材料",
        "化工",
    )
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
    _INVALID_CITY_WORDS = (
        "工作",
        "经验",
        "求职",
        "期望",
        "求职期望",
        "不限",
        "统招",
        "全日制",
        "MBA/EMBA",
        "EMBA",
        "MBA",
    )
    _COMPANY_TITLE_SEPARATORS = (" · ", "·", "｜", "|")

    HEADERS = [
        "姓名",
        "公司",
        "职位",
        "城市",
        "年限",
        "学历",
        "来源",
        "卡片判断",
        "详情状态",
        "匹配档位",
        "匹配摘要",
        "风险",
        "命中证据",
        "缺口/未知",
        "待确认问题",
        "置信度",
        "基准版本",
        "简历链接",
        "候选人ID",
        "原始卡片摘要",
    ]

    def __init__(self, store: SQLiteStore, export_dir: str | Path):
        self.store = store
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def export_session(self, session_id: str) -> Path:
        session = self.store.get_session(session_id) or {}
        candidates = self.store.list_candidates(session_id)
        filename = "{}_{}.xlsx".format(
            self._safe_filename(str(session.get("title") or "候选人")),
            now_text().replace(":", "").replace(" ", "_"),
        )
        path = self.export_dir / filename
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "候选人"
        sheet.append(self.HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="3B82F6")
        for item in candidates:
            sources = self.store.list_candidate_sources(str(item.get("id") or ""))
            source_text = "\n".join(
                "{} / {} / 排名{}".format(
                    source.get("query") or "",
                    source.get("search_hypothesis_type") or "",
                    source.get("result_index") or 0,
                )
                for source in sources
            )
            evidence = item.get("matched_evidence") or []
            evidence_text = "\n".join(
                "{}: {}".format(
                    evidence_item.get("criterion") or "",
                    evidence_item.get("evidence") or "",
                )
                for evidence_item in evidence
                if isinstance(evidence_item, dict)
            )
            fields = self._display_candidate_fields(item)
            profile_url = self._candidate_profile_url(item)
            sheet.append(
                [
                    fields.get("name") or "",
                    fields.get("current_company") or "",
                    fields.get("current_title") or "",
                    fields.get("city") or "",
                    fields.get("work_years") or "",
                    fields.get("education") or "",
                    source_text,
                    self._card_decision_label(item.get("card_decision") or ""),
                    item.get("detail_capture_status") or "",
                    item.get("match_tier") or "",
                    item.get("match_summary") or "",
                    item.get("match_risks") or "",
                    evidence_text,
                    "\n".join(item.get("missing_or_unclear") or []),
                    "\n".join(item.get("questions_to_verify") or []),
                    item.get("confidence") or "",
                    item.get("criteria_version_id") or "",
                    profile_url,
                    item.get("id") or "",
                    fields.get("summary_text") or "",
                ]
            )
            if profile_url:
                link_column = self.HEADERS.index("简历链接") + 1
                link_cell = sheet.cell(row=sheet.max_row, column=link_column)
                link_cell.hyperlink = profile_url
                link_cell.style = "Hyperlink"
        long_headers = {"来源", "匹配摘要", "风险", "命中证据", "缺口/未知", "待确认问题", "原始卡片摘要"}
        for column, header in enumerate(self.HEADERS, start=1):
            if header in long_headers:
                width = 48
            elif header == "简历链接":
                width = 56
            elif header == "候选人ID":
                width = 34
            else:
                width = 18
            sheet.column_dimensions[get_column_letter(column)].width = width
        self._write_criteria_sheet(workbook, session_id)
        self._write_metrics_sheet(workbook, session_id)
        workbook.save(path)
        workbook.close()
        return path

    def _display_candidate_fields(self, item: Dict[str, Any]) -> Dict[str, str]:
        fields = {
            "name": str(item.get("name") or ""),
            "current_company": str(item.get("current_company") or ""),
            "current_title": str(item.get("current_title") or ""),
            "city": str(item.get("city") or ""),
            "work_years": str(item.get("work_years") or ""),
            "education": str(item.get("education") or ""),
            "summary_text": str(item.get("summary_text") or ""),
        }
        parsed = self._parse_summary_fields(fields["summary_text"])
        if parsed.get("name") and not fields["name"]:
            fields["name"] = parsed["name"]
        split_company, split_title = self._split_company_title(
            fields["current_company"]
        )
        if split_company and split_title:
            if fields["current_title"] and split_company in self._COMPANY_MARKERS:
                fields["current_company"] = fields["current_title"] + split_company
                fields["current_title"] = split_title
            elif not fields["current_title"] or "·" in fields["current_company"]:
                fields["current_company"] = split_company
                fields["current_title"] = split_title
        split_company, split_title = self._split_company_title(fields["current_title"])
        if split_company and split_title and not fields["current_company"]:
            fields["current_company"] = split_company
            fields["current_title"] = split_title
        if parsed.get("current_company") and parsed.get("current_title"):
            fields["current_company"] = parsed["current_company"]
            fields["current_title"] = parsed["current_title"]
        for key in ("city", "work_years", "education"):
            if not parsed.get(key):
                continue
            if key == "city":
                if not self._looks_like_city(fields["city"]):
                    fields["city"] = parsed[key]
            elif not fields[key]:
                fields[key] = parsed[key]
        return fields

    def _candidate_profile_url(self, item: Dict[str, Any]) -> str:
        profile_url = str(item.get("profile_url") or "")
        if profile_url:
            return profile_url
        candidate_id = str(item.get("id") or "")
        if not candidate_id:
            return ""
        detail = self.store.get_candidate_detail(candidate_id) or {}
        raw_payload = from_json(detail.get("raw_payload_json"), {}) or {}
        return self._find_url(raw_payload)

    @classmethod
    def _parse_summary_fields(cls, summary_text: str) -> Dict[str, str]:
        lines = [
            line.strip()
            for line in str(summary_text or "").splitlines()
            if line.strip()
        ]
        parsed = {
            "name": lines[0] if lines else "",
            "current_company": "",
            "current_title": "",
            "city": "",
            "work_years": "",
            "education": "",
        }
        full_text = " ".join(lines)
        work_match = re.search(r"(?:工作)?(\d+年(?:经验)?)", full_text)
        if work_match:
            parsed["work_years"] = work_match.group(1)
        education_match = re.search(
            r"(MBA/EMBA|EMBA|MBA|本科|硕士|博士|大专|中专|高中|初中)",
            full_text,
        )
        if education_match:
            parsed["education"] = education_match.group(1)
        for line in reversed(lines[1:]):
            company, title = cls._split_company_title(line)
            if company:
                parsed["current_company"] = company
                parsed["current_title"] = title
                break
        for line in lines[1:]:
            city = cls._strip_personal_line(line)
            if cls._looks_like_city(city):
                parsed["city"] = city
                break
        return parsed

    @classmethod
    def _split_company_title(cls, line: str) -> tuple[str, str]:
        value = (line or "").strip()
        if not value or "求职期望" in value:
            return "", ""
        for separator in cls._COMPANY_TITLE_SEPARATORS:
            if separator not in value:
                continue
            parts = [part.strip() for part in value.split(separator) if part.strip()]
            if len(parts) < 2:
                continue
            company = separator.join(parts[:-1]).strip()
            title = parts[-1].strip()
            if len(company) < 2 or len(title) < 2:
                continue
            if any(keyword in title for keyword in cls._JOB_KEYWORDS) or any(
                marker in company for marker in cls._COMPANY_MARKERS
            ):
                return company, title
        return "", ""

    @classmethod
    def _strip_personal_line(cls, line: str) -> str:
        text = (line or "").strip()
        text = re.sub(r"\d+岁", "", text)
        text = re.sub(r"(?:工作)?\d+年(?:经验)?", "", text)
        text = re.sub(
            r"(MBA/EMBA|EMBA|MBA|本科|硕士|博士|大专|中专|高中|初中)", "", text
        )
        text = re.sub(r"\d+k(?:-\d+k)?", "", text)
        text = re.sub(r"^(男|女)\s*", "", text)
        for tag in ("男", "女", "已婚", "未婚", "共青团员", "党员", "群众"):
            text = text.replace(tag, "")
        return text.replace(" ", "").strip()

    @classmethod
    def _looks_like_city(cls, value: str) -> bool:
        text = (value or "").strip()
        if (
            not text
            or text in cls._INVALID_CITY_WORDS
            or not (2 <= len(text) <= 12)
            or re.search(r"\d", text)
        ):
            return False
        if any(word in text for word in cls._INVALID_CITY_WORDS):
            return False
        if any(keyword in text for keyword in cls._JOB_KEYWORDS):
            return False
        if any(marker in text for marker in cls._COMPANY_MARKERS):
            return False
        if re.search(
            r"(MBA/EMBA|EMBA|MBA|本科|硕士|博士|大专|中专|高中|初中|\d+岁)",
            text,
        ):
            return False
        return True

    @classmethod
    def _find_url(cls, value: Any) -> str:
        if isinstance(value, str):
            if value.startswith(("http://", "https://")):
                return value
            nested = from_json(value, None)
            return cls._find_url(nested) if nested is not None else ""
        if isinstance(value, dict):
            for key in ("profile_url", "resume_url", "detail_url", "url", "href"):
                url = value.get(key)
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
            for child in value.values():
                url = cls._find_url(child)
                if url:
                    return url
        if isinstance(value, list):
            for child in value:
                url = cls._find_url(child)
                if url:
                    return url
        return ""

    def _write_criteria_sheet(self, workbook: Workbook, session_id: str) -> None:
        criteria = self.store.get_latest_criteria_version(session_id, "confirmed") or {}
        sheet = workbook.create_sheet("寻访基准")
        sheet.append(["版本", criteria.get("version") or ""])
        sheet.append(["关键词", criteria.get("keywords_text") or ""])
        sheet.append(["岗位要求", criteria.get("requirements_text") or ""])
        sheet.column_dimensions["A"].width = 16
        sheet.column_dimensions["B"].width = 80

    def _write_metrics_sheet(self, workbook: Workbook, session_id: str) -> None:
        sheet = workbook.create_sheet("效率总结")
        metrics = self.store.session_efficiency_metrics(session_id)
        for key, value in metrics.items():
            sheet.append([key, value])
        sheet.append([])
        sheet.append(["搜索假设", "描述", "轮次", "读卡片", "去重候选", "抓详情", "A/B", "噪音", "重复"])
        for item in self.store.search_hypothesis_metrics(session_id):
            sheet.append(
                [
                    item.get("search_hypothesis_type") or "",
                    item.get("search_hypothesis_text") or "",
                    item.get("round_count") or 0,
                    item.get("raw_count") or 0,
                    item.get("unique_count") or 0,
                    item.get("detail_fetch_count") or 0,
                    item.get("ab_count") or 0,
                    item.get("noise_count") or 0,
                    item.get("duplicate_count") or 0,
                ]
            )
        sheet.column_dimensions["A"].width = 28
        sheet.column_dimensions["B"].width = 56

    @staticmethod
    def _safe_filename(value: str) -> str:
        for ch in '\\/:*?"<>|':
            value = value.replace(ch, "_")
        return value.strip(" .")[:60] or "候选人"

    @staticmethod
    def _card_decision_label(value: object) -> str:
        return {
            "fetch": "值得抓详情",
            "maybe": "信息不足",
            "noise": "明显噪音",
        }.get(str(value or ""), "信息不足")
