"""Real Liepin tool adapter used by the Agent runtime."""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from ..core.config import ConfigManager
from ..core.liepin_browser import LiepinBrowserManager
from ..core.liepin_resume_extractor import LiepinResumeExtractor
from ..core.liepin_search_service import LiepinSearchCandidate, LiepinSearchService
from ..domain.models import CandidateDetail, CandidateSummary, SearchPlan

logger = logging.getLogger(__name__)


class RealLiepinTool:
    """Adapter that exposes real Liepin browser automation to AgentRuntime."""

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_manager = config_manager or ConfigManager()
        self.browser_manager = LiepinBrowserManager(self.config_manager)
        self.search_service = LiepinSearchService(self.browser_manager)
        self.resume_extractor = LiepinResumeExtractor()

    def open_browser(self):
        self.browser_manager.launch()
        state = self.search_service.open_search_page()
        if not self.browser_manager.is_logged_in():
            return self.browser_manager.open_home()
        return state

    def open_for_login_or_search(self):
        """Open Liepin to a useful page before a session starts."""
        self.browser_manager.launch()
        if self.browser_manager.is_logged_in():
            return self.search_service.open_search_page()
        return self.browser_manager.open_home()

    def check_login(self) -> bool:
        self.browser_manager.launch()
        return self.browser_manager.is_logged_in()

    def close(self) -> None:
        self.browser_manager.close()

    def close_browser(self) -> None:
        self.browser_manager.close_browser()

    def run_search_round(
        self, session_id: str, round_id: str, plan: SearchPlan
    ) -> List[CandidateSummary]:
        """Run one real Liepin search and return only result-card summaries."""
        _ = session_id, round_id
        filters = self._map_filters(plan.filters or {})
        logger.warning(
            "RealLiepinTool search query=%s position=%s filters=%s",
            plan.query,
            plan.position_filter,
            filters,
        )
        candidates = self.search_service.search(
            plan.query,
            filters=filters,
            match_mode=plan.match_mode,
            scope=plan.scope,
            position_filter=plan.position_filter,
        )
        return [
            self._to_candidate_summary(item, index)
            for index, item in enumerate(candidates or [])
        ]

    def fetch_candidate_detail(self, candidate: Dict[str, object]) -> CandidateDetail:
        """Open a real candidate detail page and extract the normalized resume."""
        summary = LiepinSearchCandidate(
            name=str(candidate.get("name") or ""),
            age=str(candidate.get("age") or ""),
            current_title=str(candidate.get("current_title") or ""),
            current_company=str(candidate.get("current_company") or ""),
            city=str(candidate.get("city") or ""),
            work_years=str(candidate.get("work_years") or ""),
            education=str(candidate.get("education") or ""),
            profile_url=str(candidate.get("profile_url") or ""),
            summary=str(candidate.get("summary_text") or ""),
            result_index=int(candidate.get("result_index") or 0),
        )

        def _extract(page):
            detail_page = self.search_service.open_candidate_detail(page, summary)
            try:
                time.sleep(0.6)
                extracted = self.resume_extractor.extract_candidate(detail_page, summary)
                is_gold_collar = self._is_gold_collar_detail_page(detail_page)
                return extracted, is_gold_collar
            finally:
                self.search_service.close_detail_page(detail_page, page)

        extracted, is_gold_collar = self.browser_manager.run_with_page(_extract)
        return CandidateDetail(
            candidate_id=str(candidate.get("id") or ""),
            resume_text=extracted.resume_text or "",
            resume_summary=extracted.resume_summary or "",
            raw_payload={
                "raw_payload_json": extracted.raw_payload_json or "",
                "profile_url": extracted.profile_url or summary.profile_url or "",
                "is_gold_collar": is_gold_collar,
            },
            capture_status="success"
            if (extracted.resume_text or "").strip()
            else "partial",
            is_gold_collar=is_gold_collar,
        )

    def greet_candidate(
        self, candidate: Dict[str, object], message_template: str = ""
    ) -> Dict[str, str]:
        """Send a Liepin greeting from a candidate detail page.

        The workbench calls this only from a user-triggered UI action after the
        task is no longer running, so this method focuses on safe page execution.
        """
        profile_url = str(candidate.get("profile_url") or "")
        if not profile_url:
            return {"status": "failed", "message": "", "error": "缺少简历链接"}

        def _run(page):
            detail_page = self.search_service.open_candidate_detail(
                page,
                LiepinSearchCandidate(
                    name=str(candidate.get("name") or ""),
                    current_title=str(candidate.get("current_title") or ""),
                    current_company=str(candidate.get("current_company") or ""),
                    profile_url=profile_url,
                    result_index=int(candidate.get("result_index") or -1),
                ),
            )
            try:
                if not self._is_gold_collar_detail_page(detail_page):
                    return {
                        "status": "skipped",
                        "message": "非金领候选人，跳过打招呼",
                        "error": "",
                    }
                body_text = self._safe_body_text(detail_page)
                if any(marker in body_text for marker in self.ALREADY_GREETED_MARKERS):
                    return {
                        "status": "already_greeted",
                        "message": "已打过招呼",
                        "error": "",
                    }
                if not self._click_greeting_button(detail_page):
                    if self._has_continue_chat_button(detail_page):
                        return {
                            "status": "already_greeted",
                            "message": "已打过招呼",
                            "error": "",
                        }
                    return {"status": "failed", "message": "", "error": "未找到沟通按钮"}
                if self._handle_greeting_dialog(detail_page, message_template):
                    return {
                        "status": "success",
                        "message": message_template or "已发送打招呼",
                        "error": "",
                    }
                return {"status": "failed", "message": "", "error": "打招呼弹窗处理失败"}
            finally:
                self._close_any_dialog(detail_page)
                self.search_service.close_detail_page(detail_page, page)

        return self.browser_manager.run_with_page(_run)

    GREETING_BUTTON_SELECTORS = [
        'button:has-text("立即沟通")',
        'button:has-text("打招呼")',
        'button:has-text("在线沟通")',
    ]
    ALREADY_GREETED_MARKERS = ["已沟通", "已打招呼", "继续沟通", "继续聊聊"]

    @staticmethod
    def _is_gold_collar_detail_page(page) -> bool:
        try:
            return page.locator(".elite-tag-gold").count() > 0
        except Exception:
            return False

    @staticmethod
    def _safe_body_text(page) -> str:
        try:
            return page.locator("body").inner_text(timeout=5000) or ""
        except Exception:
            try:
                return page.evaluate("() => document.body.innerText") or ""
            except Exception:
                return ""

    def _click_greeting_button(self, page) -> bool:
        for selector in self.GREETING_BUTTON_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=2000):
                    locator.first.click()
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _has_continue_chat_button(page) -> bool:
        for selector in (
            'button:has-text("继续沟通")',
            'button:has-text("继续聊聊")',
            'a:has-text("继续沟通")',
            '[role="button"]:has-text("继续沟通")',
        ):
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue
        return False

    def _handle_greeting_dialog(self, page, message_template: str) -> bool:
        time.sleep(1.5)
        try:
            no_job_btn = page.locator('button:has-text("不选择职位开聊")')
            if no_job_btn.count() > 0 and no_job_btn.first.is_visible(timeout=2000):
                no_job_btn.first.click()
                time.sleep(1.5)
        except Exception:
            pass
        if message_template:
            return self._send_chat_message(page, message_template)
        return True

    @staticmethod
    def _send_chat_message(page, message: str) -> bool:
        for selector in (
            'textarea[placeholder*="请输入文字"]',
            'input[placeholder*="请输入文字"]',
            '[contenteditable="true"]',
            '[role="textbox"]',
            'dialog textarea',
            'dialog input[type="text"]',
        ):
            try:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 3)):
                    chat_input = locator.nth(index)
                    if not chat_input.is_visible(timeout=1000):
                        continue
                    chat_input.fill(message)
                    time.sleep(0.5)
                    for send_selector in (
                        'button:has-text("发送")',
                        'button[type="submit"]',
                        '[role="button"]:has-text("发送")',
                    ):
                        try:
                            send_btn = page.locator(send_selector).first
                            if send_btn.is_visible(timeout=1000) and send_btn.is_enabled():
                                send_btn.click()
                                time.sleep(1)
                                return True
                        except Exception:
                            continue
                    try:
                        chat_input.press("Enter")
                        time.sleep(1)
                        return True
                    except Exception:
                        return False
            except Exception:
                continue
        return False

    @staticmethod
    def _close_any_dialog(page) -> None:
        try:
            page.keyboard.press("Escape")
            time.sleep(0.2)
            page.keyboard.press("Escape")
        except Exception:
            pass
        for selector in (
            'button[class*="close"]',
            'button[aria-label="Close"]',
            ".ant-modal-close",
            "[class*='close-btn']",
            'button:has-text("关闭")',
            'button:has-text("取消")',
        ):
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=300):
                    locator.first.click()
                    time.sleep(0.2)
                    return
            except Exception:
                continue

    @staticmethod
    def _to_candidate_summary(
        candidate: LiepinSearchCandidate, index: int
    ) -> CandidateSummary:
        return CandidateSummary(
            profile_url=candidate.profile_url or "",
            name=candidate.name or "",
            age=candidate.age or "",
            current_title=candidate.current_title or "",
            current_company=candidate.current_company or "",
            city=candidate.city or "",
            work_years=candidate.work_years or "",
            education=candidate.education or "",
            summary_text=candidate.summary or "",
            result_index=candidate.result_index
            if candidate.result_index >= 0
            else index,
        )

    @staticmethod
    def _map_filters(filters: Dict[str, object]) -> Dict[str, object]:
        mapped: Dict[str, object] = {}
        cities = filters.get("city") or filters.get("目前城市")
        if cities:
            mapped["目前城市"] = cities
        work_years = filters.get("work_years") or filters.get("工作年限")
        if work_years:
            mapped["工作年限"] = work_years
        education = filters.get("education") or filters.get("教育经历")
        if education:
            mapped["教育经历"] = education
        active_days = filters.get("active_days") or filters.get("活跃度")
        if active_days:
            if isinstance(active_days, int):
                if active_days <= 1:
                    mapped["活跃度"] = "今天活跃"
                elif active_days <= 3:
                    mapped["活跃度"] = "3天内活跃"
                elif active_days <= 7:
                    mapped["活跃度"] = "7天内活跃"
                elif active_days <= 30:
                    mapped["活跃度"] = "30天内活跃"
                elif active_days <= 90:
                    mapped["活跃度"] = "最近三个月活跃"
                elif active_days <= 180:
                    mapped["活跃度"] = "最近半年活跃"
                else:
                    mapped["活跃度"] = "最近一年活跃"
            else:
                mapped["活跃度"] = active_days
        return mapped
