"""Real Liepin tool adapter used by the Agent runtime."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from ..core.config import ConfigManager
from ..core.liepin_browser import LiepinBrowserManager
from ..core.liepin_resume_extractor import (
    LiepinResumeExtractionError,
    LiepinResumeExtractor,
)
from ..core.liepin_search_service import (
    AdaptivePaginationPolicy,
    LiepinSearchCandidate,
    LiepinSearchService,
)
from ..core.search import SearchCursor
from ..domain.models import CandidateDetail, CandidateSummary, SearchPlan

logger = logging.getLogger(__name__)


@dataclass
class SearchRoundResult:
    """One search batch: candidate cards plus the pagination cursor/state."""

    candidates: List[CandidateSummary] = field(default_factory=list)
    cursor: Optional[SearchCursor] = None
    page_stats: List[Dict[str, object]] = field(default_factory=list)


def _load_json_config(name: str) -> Dict[str, object]:
    """Load a JSON config file from liepin_agent/config/ with built-in fallback."""
    paths = [
        Path(__file__).with_name("config") / name,
        Path(__file__).parent.parent / "config" / name,
    ]
    for path in paths:
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return {}


class RealLiepinTool:
    """Adapter that exposes real Liepin browser automation to AgentRuntime."""

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_manager = config_manager or ConfigManager()
        self.browser_manager = LiepinBrowserManager(self.config_manager)
        self.search_service = LiepinSearchService(self.browser_manager)
        self.resume_extractor = LiepinResumeExtractor()
        self._load_page_configs()

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
        self,
        session_id: str,
        round_id: str,
        plan: SearchPlan,
        known_candidate_keys: Optional[List[str]] = None,
    ) -> SearchRoundResult:
        """Run one real Liepin search and return cards plus pagination state."""
        _ = session_id, round_id
        filters = self._map_filters(plan.filters or {})

        query = plan.query

        max_pages = self._page_cap()
        pagination_policy = self._build_pagination_policy(max_pages)
        candidate_classifier = (
            self._pagination_classifier(plan) if pagination_policy is not None else None
        )
        checkpoint_pages = None
        if getattr(
            self.config_manager.config, "search_agent_pagination_enabled", True
        ):
            checkpoint_pages = getattr(
                self.config_manager.config, "search_pagination_checkpoint_pages", 3
            )
        logger.warning(
            "RealLiepinTool search query=%s position=%s filters=%s max_pages=%s adaptive=%s agent_pagination=%s",
            query,
            plan.position_filter,
            filters,
            max_pages,
            pagination_policy is not None,
            checkpoint_pages is not None,
        )
        candidates = self.search_service.search(
            query,
            filters=filters,
            match_mode=plan.match_mode,
            scope=plan.scope,
            position_filter=plan.position_filter,
            max_pages=max_pages,
            pagination_policy=pagination_policy,
            candidate_classifier=candidate_classifier,
            known_candidate_keys=known_candidate_keys,
            checkpoint_pages=checkpoint_pages,
        )
        return self._to_search_round_result(candidates)

    def continue_search_round(
        self,
        plan: SearchPlan,
        cursor: SearchCursor,
        additional_pages: int,
    ) -> SearchRoundResult:
        """Continue paging from ``cursor`` after an agent pagination decision."""
        pagination_policy = self._build_pagination_policy(self._page_cap())
        candidate_classifier = (
            self._pagination_classifier(plan) if pagination_policy is not None else None
        )
        candidates = self.search_service.resume_pagination(
            cursor,
            additional_pages,
            page_cap=self._page_cap(),
            pagination_policy=pagination_policy,
            candidate_classifier=candidate_classifier,
        )
        return self._to_search_round_result(candidates, cursor=cursor)

    def _page_cap(self) -> int:
        return getattr(self.config_manager.config, "search_max_pages_per_round", 10)

    def _build_pagination_policy(
        self, max_pages: int
    ) -> Optional[AdaptivePaginationPolicy]:
        """Build the bounded policy used as pagination signal/rule fallback."""
        if not getattr(
            self.config_manager.config, "search_adaptive_pagination_enabled", True
        ):
            return None
        return AdaptivePaginationPolicy(
            min_pages=getattr(
                self.config_manager.config, "search_min_pages_per_round", 3
            ),
            max_pages=max_pages,
            low_yield_patience=getattr(
                self.config_manager.config,
                "search_low_yield_page_patience",
                2,
            ),
            min_new_unique=getattr(
                self.config_manager.config,
                "search_min_new_unique_per_page",
                3,
            ),
            min_promising=getattr(
                self.config_manager.config,
                "search_min_promising_per_page",
                1,
            ),
            duplicate_rate_threshold=getattr(
                self.config_manager.config,
                "search_duplicate_rate_threshold",
                0.8,
            ),
        )

    def _to_search_round_result(
        self,
        candidates: Optional[List[LiepinSearchCandidate]],
        cursor: Optional[SearchCursor] = None,
    ) -> SearchRoundResult:
        cursor = cursor or getattr(self.search_service, "last_search_cursor", None)
        history = list(getattr(cursor, "history", None) or [])
        return SearchRoundResult(
            candidates=[
                self._to_candidate_summary(item, index)
                for index, item in enumerate(candidates or [])
            ],
            cursor=cursor,
            page_stats=[stats.to_dict() for stats in history],
        )

    @staticmethod
    def _pagination_classifier(
        plan: SearchPlan,
    ) -> Callable[[LiepinSearchCandidate], str]:
        """Build a local card classifier used only for pagination yield.

        It never rejects a candidate or determines the final detail decision;
        it only tells the bounded pagination policy whether another page is
        likely to add signal. Empty card fields therefore remain unknown.
        """
        expected_terms = [
            str(value).strip().lower()
            for value in (plan.expected_signal or [])
            if str(value).strip()
        ]
        position_filter = (plan.position_filter or "").strip().lower()
        if not expected_terms:
            expected_terms = [
                value.strip().lower()
                for value in (plan.query or "").split()
                if value.strip() and not value.strip().startswith("-")
            ]

        def _classify(candidate: LiepinSearchCandidate) -> str:
            text = "\n".join(
                str(value or "").lower()
                for value in (
                    candidate.current_title,
                    candidate.current_company,
                    candidate.city,
                    candidate.work_years,
                    candidate.education,
                    candidate.summary,
                    candidate.raw_text,
                )
            )
            hits = sum(1 for term in expected_terms if term and term in text)
            position_hit = bool(position_filter and position_filter in text)
            if hits >= 2 or (hits >= 1 and position_hit):
                return "potential"
            if hits >= 1 or position_hit or not expected_terms:
                return "validate"
            return "other"

        return _classify

    def fetch_candidate_detail(self, candidate: Dict[str, object]) -> CandidateDetail:
        """Open a real candidate detail page and extract the normalized resume."""
        summary = LiepinSearchCandidate(
            name=str(candidate.get("name") or ""),
            age=str(candidate.get("age") or ""),
            gender=str(candidate.get("gender") or ""),
            current_title=str(candidate.get("current_title") or ""),
            current_company=str(candidate.get("current_company") or ""),
            city=str(candidate.get("city") or ""),
            work_years=str(candidate.get("work_years") or ""),
            education=str(candidate.get("education") or ""),
            profile_url=str(candidate.get("profile_url") or ""),
            summary=str(candidate.get("summary_text") or ""),
            result_index=int(candidate.get("result_index") or 0),
            page_meta=dict(candidate.get("page_meta") or {}),
        )

        def _extract(page):
            detail_page = self.search_service.open_candidate_detail(page, summary)
            try:
                time.sleep(self.timing_detail_page_wait)
                self._wait_for_resume_content(detail_page)
                extracted = None
                extraction_errors = []
                attempts = max(
                    1,
                    int(
                        getattr(
                            self.config_manager.config,
                            "detail_extract_max_attempts",
                            2,
                        )
                        or 2
                    ),
                )
                for attempt in range(1, attempts + 1):
                    try:
                        extracted = self.resume_extractor.extract_candidate(
                            detail_page, summary
                        )
                        break
                    except LiepinResumeExtractionError as exc:
                        extraction_errors.append(str(exc))
                        if attempt >= attempts:
                            raise LiepinResumeExtractionError(
                                "详情提取连续 {} 次失败：{}".format(
                                    attempts, "；".join(extraction_errors)
                                )
                            ) from exc
                        logger.warning(
                            "candidate detail extraction empty; retrying attempt=%s/%s url=%s error=%s",
                            attempt,
                            attempts,
                            (getattr(detail_page, "url", "") or summary.profile_url)[:120],
                            exc,
                        )
                        self._page_wait(
                            detail_page,
                            float(
                                getattr(
                                    self.config_manager.config,
                                    "detail_extract_retry_wait_seconds",
                                    1.5,
                                )
                                or 1.5
                            ),
                        )
                        self._wait_for_resume_content(detail_page)
                if extracted is None:
                    raise LiepinResumeExtractionError("详情提取未返回结果")
                is_gold_collar = self._is_gold_collar_detail_page(detail_page)
                return extracted, is_gold_collar, len(extraction_errors) + 1
            finally:
                self.search_service.close_detail_page(detail_page, page)

        extracted, is_gold_collar, extract_attempts = self.browser_manager.run_with_page(_extract)
        return CandidateDetail(
            candidate_id=str(candidate.get("id") or ""),
            resume_text=extracted.resume_text or "",
            resume_summary=extracted.resume_summary or "",
            raw_payload={
                "raw_payload_json": extracted.raw_payload_json or "",
                "profile_url": extracted.profile_url or summary.profile_url or "",
                "is_gold_collar": is_gold_collar,
                "detail_extract_attempts": extract_attempts,
            },
            capture_status="success"
            if (extracted.resume_text or "").strip()
            else "partial",
            is_gold_collar=is_gold_collar,
        )

    def _wait_for_resume_content(self, page) -> None:
        timeout_seconds = float(
            getattr(
                self.config_manager.config,
                "detail_page_ready_timeout_seconds",
                4.0,
            )
            or 4.0
        )
        try:
            page.wait_for_function(
                """() => {
                    const text = (document.body && document.body.innerText) || '';
                    const markers = ['工作经历', '教育经历', '项目经历', '自我评价', '求职期望'];
                    return text.length >= 120 && markers.some((marker) => text.includes(marker));
                }""",
                timeout=int(timeout_seconds * 1000),
            )
        except Exception:
            # The extractor still has body-text fallbacks for short or unusual resumes.
            logger.warning(
                "candidate detail readiness marker timed out url=%s timeout=%.1fs",
                (getattr(page, "url", "") or "")[:120],
                timeout_seconds,
            )

    @staticmethod
    def _page_wait(page, seconds: float) -> None:
        milliseconds = max(0, int(float(seconds or 0) * 1000))
        try:
            page.wait_for_timeout(milliseconds)
        except Exception:
            time.sleep(max(0.0, float(seconds or 0)))

    def greet_candidate(
        self, candidate: Dict[str, object], message_template: str = "", request_resume: bool = False
    ) -> Dict[str, str]:
        """Send a Liepin greeting from a candidate detail page.

        The workbench calls this only from a user-triggered UI action after the
        task is no longer running, so this method focuses on safe page execution.
        """
        profile_url = str(candidate.get("profile_url") or "")
        if not profile_url:
            return {"status": "failed", "message": "", "error": "缺少简历链接"}
        message = self._render_greeting_template(message_template, candidate)

        def _run(page):
            summary = LiepinSearchCandidate(
                name=str(candidate.get("name") or ""),
                current_title=str(candidate.get("current_title") or ""),
                current_company=str(candidate.get("current_company") or ""),
                profile_url=profile_url,
                result_index=int(candidate.get("result_index") or -1),
            )
            detail_page = self._open_greeting_detail_page(page, summary)
            try:
                logger.warning(
                    "manual_greeting: opened detail name=%s url=%s",
                    summary.name or candidate.get("id") or "候选人",
                    (getattr(detail_page, "url", "") or profile_url)[:160],
                )
                self._wait_for_greeting_page_ready(detail_page)
                if not candidate.get("skip_gold_check") and not self._is_gold_collar_detail_page(detail_page, wait_seconds=6.0):
                    logger.warning(
                        "manual_greeting: skipped non-gold name=%s url=%s body=%s",
                        summary.name or candidate.get("id") or "候选人",
                        (getattr(detail_page, "url", "") or profile_url)[:160],
                        self._safe_body_text(detail_page)[:200].replace("\n", " "),
                    )
                    return {
                        "status": "skipped",
                        "message": self.STATUS_SKIPPED_TEXT,
                        "error": "",
                    }
                body_text = self._safe_body_text(detail_page)
                if any(marker in body_text for marker in self.ALREADY_GREETED_MARKERS):
                    logger.warning(
                        "manual_greeting: already greeted name=%s",
                        summary.name or candidate.get("id") or "候选人",
                    )
                    return {
                        "status": "already_greeted",
                        "message": self.STATUS_ALREADY_TEXT,
                        "error": "",
                    }
                if not self._click_greeting_button(detail_page):
                    if self._has_continue_chat_button(detail_page):
                        logger.warning(
                            "manual_greeting: continue chat button found name=%s",
                            summary.name or candidate.get("id") or "候选人",
                        )
                        return {
                            "status": "already_greeted",
                            "message": self.STATUS_ALREADY_TEXT,
                            "error": "",
                        }
                    logger.warning(
                        "manual_greeting: greeting button not found name=%s body=%s",
                        summary.name or candidate.get("id") or "候选人",
                        body_text[:200].replace("\n", " "),
                    )
                    return {"status": "failed", "message": "", "error": self.ERROR_NO_BUTTON_TEXT}
                if self._handle_greeting_dialog(detail_page, message):
                    request_resume_status = ""
                    if request_resume:
                        request_resume_status = self._request_resume(detail_page)
                    logger.warning(
                        "manual_greeting: success name=%s custom_message=%s request_resume=%s",
                        summary.name or candidate.get("id") or "候选人",
                        bool(message),
                        request_resume_status,
                    )
                    return {
                        "status": "success",
                        "message": message or "已发送打招呼",
                        "error": "",
                        "request_resume_status": request_resume_status,
                    }
                logger.warning(
                    "manual_greeting: dialog handling failed name=%s",
                    summary.name or candidate.get("id") or "候选人",
                )
                return {"status": "failed", "message": "", "error": self.ERROR_DIALOG_TEXT}
            finally:
                self._close_any_dialog(detail_page)
                self.search_service.close_detail_page(detail_page, page)

        return self.browser_manager.run_with_page(_run)

    def _load_page_configs(self) -> None:
        """Load selectors and text markers from JSON so users can adapt to page changes."""
        selectors = _load_json_config("liepin_selectors.json")
        markers = _load_json_config("liepin_markers.json")

        self.GREETING_BUTTON_SELECTORS = selectors.get("greeting_button", [
            'button:has-text("立即沟通")', 'button:has-text("打招呼")',
            'button:has-text("在线沟通")', '.chat-btn', 'button.chat-btn',
            '[role="button"]:has-text("立即沟通")', '[role="button"]:has-text("打招呼")',
            'a:has-text("立即沟通")', 'a:has-text("打招呼")',
        ])
        self.CONTINUE_CHAT_SELECTORS = selectors.get("continue_chat_button", [
            'button:has-text("继续沟通")', 'button:has-text("继续聊聊")',
            '.chat-btn', 'button.chat-btn', 'a:has-text("继续沟通")',
            '[role="button"]:has-text("继续沟通")',
        ])
        self.GOLD_COLLAR_SELECTORS = selectors.get("gold_collar", [
            ".name-box .elite-tag-gold", ".elite-tag-gold",
            '[class*="elite-tag-gold"]', '.name-box [class*="gold"]',
            '[class*="gold"]:has-text("金领")',
        ])
        self.PAGE_READY_SELECTORS = selectors.get("page_ready", [
            ".name-box", ".elite-tag-gold",
            'button:has-text("立即沟通")', 'button:has-text("打招呼")',
            'button:has-text("继续沟通")', '.chat-btn', 'button.chat-btn',
            '[role="button"]:has-text("立即沟通")', '[role="button"]:has-text("打招呼")',
            'a:has-text("立即沟通")',
        ])
        self.CHAT_INPUT_SELECTORS = selectors.get("chat_input", [
            'textarea[placeholder*="请输入文字"]', 'input[placeholder*="请输入文字"]',
            '[contenteditable="true"]', '[role="textbox"]',
            'dialog textarea', 'dialog input[type="text"]',
            '.im-ui-chat-input [contenteditable="true"]',
        ])
        self.SEND_BUTTON_SELECTORS = selectors.get("send_button", [
            'button.im-ui-basic-send-btn', '.im-ui-chat-input button:has-text("发送")',
            'button:has-text("发送")', 'button[type="submit"]',
            '[role="button"]:has-text("发送")', '.im-ui-send-btn',
            'button:has-text("发送消息")',
        ])
        self.REQUEST_RESUME_SELECTORS = selectors.get("request_resume", [
            'span.im-ui-action-button.action-item.action-resume',
            '[class*="action-resume"]', 'button:has-text("索要简历")',
            'span:has-text("索要简历")', '[class*="resume"]:has-text("索要")',
            'button:has-text("要简历")',
        ])
        self.CONFIRM_BUTTON_SELECTORS = selectors.get("confirm_button", [
            '.ant-im-modal-confirm-btns button.ant-im-btn-primary',
            '.ant-modal-confirm-btns button.ant-btn-primary',
            '.ant-modal button.ant-btn-primary', 'button:has-text("确认")',
            'button:has-text("确定")', '.ant-btn-primary',
        ])
        self.CLOSE_DIALOG_SELECTORS = selectors.get("close_dialog", [
            'button[class*="close"]', 'button[aria-label="Close"]',
            '.ant-modal-close', "[class*='close-btn']",
            'button:has-text("关闭")', 'button:has-text("取消")',
        ])
        self.SELECT_JOB_DROPDOWN_SELECTORS = selectors.get("select_job_dropdown", [
            '.ant-select:has-text("选择职位")', '[class*="select"]:has-text("选择职位")',
            'input[placeholder*="选择职位"]',
        ])
        self.SELECT_JOB_OPTION_SELECTORS = selectors.get("select_job_option", [
            '.ant-select-item', '[role="option"]',
        ])

        self.ALREADY_GREETED_MARKERS = markers.get("already_greeted", ["已沟通", "已打招呼", "继续沟通", "继续聊聊"])
        self.GOLD_COLLAR_TEXT_MARKERS = markers.get("gold_collar_text", ["金领人才", "金领简历"])
        self.NO_JOB_CHAT_TEXT = markers.get("no_job_chat", "不选择职位开聊")
        self.REQUEST_RESUME_TEXT = markers.get("request_resume", "索要简历")
        self.CONFIRM_TEXT = markers.get("confirm", "确认")
        self.CONFIRM_ALT_TEXT = markers.get("confirm_alt", "确定")
        self.STATUS_SKIPPED_TEXT = markers.get("status_skipped", "非金领候选人，跳过打招呼")
        self.STATUS_ALREADY_TEXT = markers.get("status_already", "已打过招呼")
        self.ERROR_NO_BUTTON_TEXT = markers.get("error_no_button", "未找到沟通按钮")
        self.ERROR_DIALOG_TEXT = markers.get("error_dialog", "打招呼弹窗处理失败")
        self.ERROR_RESUME_BUTTON_TEXT = markers.get("error_resume_button", "未找到索要简历按钮")
        self.ERROR_CONFIRM_BUTTON_TEXT = markers.get("error_confirm_button", "未找到确认按钮")

        timing = _load_json_config("liepin_timing.json")
        self.timing_detail_page_wait = timing.get("detail_page_wait", 0.6)
        self.timing_greeting_page_wait = timing.get("greeting_page_wait", 1.5)
        self.timing_js_navigation_wait = timing.get("js_navigation_wait", 2.5)
        self.timing_poll_interval = timing.get("poll_interval", 0.25)
        self.timing_click_settle = timing.get("click_settle", 1.0)
        self.timing_dialog_settle = timing.get("dialog_settle", 0.8)
        self.timing_short_settle = timing.get("short_settle", 0.2)
        self.timing_typing_delay = timing.get("typing_delay", 0.2)

    def _open_greeting_detail_page(self, page, summary: LiepinSearchCandidate):
        try:
            return self.search_service.open_candidate_detail(page, summary)
        except Exception:
            if not self._navigate_to_profile(page, summary.profile_url):
                raise
            summary.profile_url = page.url or summary.profile_url
            return page

    def _wait_for_greeting_page_ready(self, page, timeout_ms: int = 15000) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        deadline = time.time() + max(1, timeout_ms / 1000)
        while time.time() < deadline:
            for selector in self.PAGE_READY_SELECTORS:
                try:
                    locator = page.locator(selector)
                    if locator.count() > 0 and locator.first.is_visible(timeout=500):
                        return
                except Exception:
                    continue
            time.sleep(self.timing_poll_interval)

    def _navigate_to_profile(self, page, url: str) -> bool:
        profile_url = RealLiepinTool._ensure_absolute_url(url)
        if not profile_url:
            return False
        for wait_until, timeout in (
            ("domcontentloaded", 10000),
            ("commit", 8000),
        ):
            try:
                page.goto(profile_url, wait_until=wait_until, timeout=timeout)
                time.sleep(self.timing_greeting_page_wait)
                if RealLiepinTool._looks_like_detail_url(page.url or profile_url):
                    return True
            except Exception:
                pass
        try:
            page.evaluate("(url) => { window.location.href = url; }", profile_url)
            time.sleep(self.timing_js_navigation_wait)
            return RealLiepinTool._looks_like_detail_url(page.url or profile_url)
        except Exception:
            return False

    def _is_gold_collar_detail_page(self, page, wait_seconds: float = 0.0) -> bool:
        deadline = time.time() + max(0.0, wait_seconds)
        while True:
            for selector in self.GOLD_COLLAR_SELECTORS:
                try:
                    if page.locator(selector).count() > 0:
                        return True
                except Exception:
                    continue
            body_text = RealLiepinTool._safe_body_text(page)
            if any(marker in body_text for marker in self.GOLD_COLLAR_TEXT_MARKERS):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(self.timing_poll_interval)

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
                    locator.first.click(force=True)
                    logger.warning("manual_greeting: clicked greeting button selector=%s", selector)
                    time.sleep(self.timing_click_settle)
                    return True
            except Exception:
                continue
        return False

    def _has_continue_chat_button(self, page) -> bool:
        for selector in self.CONTINUE_CHAT_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue
        return False

    def _handle_greeting_dialog(self, page, message_template: str) -> bool:
        time.sleep(self.timing_greeting_page_wait)
        clicked_no_job = False
        try:
            no_job_btn = page.locator(f'button:has-text("{self.NO_JOB_CHAT_TEXT}")')
            if no_job_btn.count() > 0 and no_job_btn.first.is_visible(timeout=2000):
                no_job_btn.first.click()
                clicked_no_job = True
                time.sleep(self.timing_greeting_page_wait)
        except Exception:
            pass
        if not clicked_no_job:
            self._select_job_if_needed(page)
        if message_template:
            return self._send_chat_message(page, message_template)
        return True

    def _select_job_if_needed(self, page) -> bool:
        for selector in self.SELECT_JOB_DROPDOWN_SELECTORS:
            try:
                dropdown = page.locator(selector)
                if dropdown.count() <= 0 or not dropdown.first.is_visible(timeout=1000):
                    continue
                dropdown.first.click()
                time.sleep(self.timing_dialog_settle)
                options = page.locator(", ".join(self.SELECT_JOB_OPTION_SELECTORS))
                if options.count() > 0:
                    options.first.click()
                    time.sleep(self.timing_dialog_settle)
                    return True
            except Exception:
                continue
        return False

    def _request_resume(self, page) -> str:
        """在已打开的聊天窗口中点击"索要简历"并确认。"""
        try:
            clicked = False
            for selector in self.REQUEST_RESUME_SELECTORS:
                try:
                    resume_btn = page.locator(selector)
                    if resume_btn.count() > 0 and resume_btn.first.is_visible(timeout=2000):
                        resume_btn.first.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                logger.warning("request_resume: resume button not found")
                return self.ERROR_RESUME_BUTTON_TEXT
            time.sleep(self.timing_greeting_page_wait)

            confirm_clicked = False
            for selector in self.CONFIRM_BUTTON_SELECTORS:
                try:
                    confirm_btn = page.locator(selector)
                    if confirm_btn.count() > 0 and confirm_btn.first.is_visible(timeout=2000):
                        confirm_btn.first.click()
                        confirm_clicked = True
                        break
                except Exception:
                    continue
            if not confirm_clicked:
                logger.warning("request_resume: confirm button not found")
                return self.ERROR_CONFIRM_BUTTON_TEXT
            time.sleep(self.timing_greeting_page_wait)
            logger.warning("request_resume: success")
            return "已发送索要简历"
        except Exception as exc:
            logger.warning("request_resume: failed %s", exc)
            return "索要简历失败: {}".format(exc)

    def _send_chat_message(self, page, message: str) -> bool:
        """在聊天弹窗中填写并发送消息，返回是否成功。"""
        if not message:
            return True
        for selector in self.CHAT_INPUT_SELECTORS:
            try:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 3)):
                    chat_input = locator.nth(index)
                    if not chat_input.is_visible(timeout=1000):
                        continue
                    # contenteditable 用 type 更能触发 input 事件，激活发送按钮
                    if 'contenteditable' in selector:
                        chat_input.click()
                        time.sleep(self.timing_short_settle)
                        chat_input.type(message, delay=10)
                    else:
                        chat_input.fill(message)
                    time.sleep(self.timing_dialog_settle)
                    for send_selector in self.SEND_BUTTON_SELECTORS:
                        try:
                            send_btn = page.locator(send_selector).first
                            if send_btn.is_visible(timeout=1000) and send_btn.is_enabled():
                                send_btn.click()
                                time.sleep(self.timing_click_settle)
                                return True
                        except Exception:
                            continue
                    # 回退：按 Enter 发送，但验证输入框是否被清空，避免只是换行
                    try:
                        chat_input.press("Enter")
                        time.sleep(self.timing_click_settle)
                        try:
                            remaining = chat_input.input_value(timeout=500)
                        except Exception:
                            remaining = chat_input.inner_text(timeout=500) or ""
                        if message.strip() in str(remaining).strip():
                            # 消息还在，说明 Enter 没有触发发送（可能只是换行）
                            return False
                        return True
                    except Exception:
                        return False
            except Exception:
                continue
        return False

    def _close_any_dialog(self, page) -> None:
        """关闭弹窗，但避免隐藏聊天主界面等正常交互元素。"""
        try:
            page.evaluate(
                """
                () => {
                    // 只针对明确的弹窗层，避免误杀聊天窗口（可能含 dialog 类名）
                    const selectors = [
                        'dialog', '[role="dialog"]', '.ant-modal', '.ant-modal-wrap',
                        '.ant-modal-mask', '.modal-overlay', '.modal-backdrop'
                    ];
                    selectors.forEach((selector) => {
                        document.querySelectorAll(selector).forEach((el) => {
                            const style = window.getComputedStyle(el);
                            // 只隐藏 fixed/absolute 定位的覆盖层，避免隐藏正常文档流元素
                            if (style.position === 'fixed' || style.position === 'absolute') {
                                el.style.display = 'none';
                            } else if (el.tagName.toLowerCase() === 'dialog' || el.getAttribute('role') === 'dialog') {
                                el.style.display = 'none';
                            }
                        });
                    });
                    document.dispatchEvent(new KeyboardEvent('keydown', {
                        key: 'Escape', keyCode: 27, bubbles: true
                    }));
                }
                """
            )
            time.sleep(self.timing_short_settle)
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
            time.sleep(self.timing_short_settle)
            page.keyboard.press("Escape")
        except Exception:
            pass
        for selector in self.CLOSE_DIALOG_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=300):
                    locator.first.click()
                    time.sleep(self.timing_short_settle)
                    return
            except Exception:
                continue

    @staticmethod
    def _render_greeting_template(template: str, candidate: Dict[str, object]) -> str:
        text = str(template or "").strip()
        if not text:
            return ""
        values = defaultdict(str)
        evidence = candidate.get("matched_evidence") or []
        if isinstance(evidence, list):
            evidence_text = "；".join(
                str(item.get("evidence") or item.get("criterion") or "")
                for item in evidence
                if isinstance(item, dict)
            )
        else:
            evidence_text = str(evidence or "")
        questions = candidate.get("questions_to_verify") or []
        if isinstance(questions, list):
            risk_to_verify = "；".join(str(item) for item in questions if item)
        else:
            risk_to_verify = str(questions or "")
        values.update(
            {
                "name": str(candidate.get("name") or ""),
                "current_company": str(candidate.get("current_company") or ""),
                "current_title": str(candidate.get("current_title") or ""),
                "job_title": str(candidate.get("job_title") or candidate.get("session_title") or ""),
                "matched_evidence": evidence_text,
                "risk_to_verify": risk_to_verify or str(candidate.get("match_risks") or ""),
            }
        )
        try:
            return text.format_map(values)
        except Exception:
            return text

    @staticmethod
    def _looks_like_detail_url(url: str) -> bool:
        normalized = (url or "").lower()
        return "showresumedetail" in normalized or "/resume/" in normalized

    @staticmethod
    def _ensure_absolute_url(url: str) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        if value.startswith("/") and not value.startswith("//"):
            value = "https://h.liepin.com" + value
        elif value.startswith("//"):
            value = "https:" + value
        try:
            parsed = urlsplit(value)
        except ValueError:
            return ""
        if parsed.scheme not in {"http", "https"}:
            return ""
        host = (parsed.hostname or "").lower()
        if host != "liepin.com" and not host.endswith(".liepin.com"):
            return ""
        if not RealLiepinTool._looks_like_detail_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))):
            return ""
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))

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
            raw_text=candidate.raw_text or candidate.summary or "",
            result_index=candidate.result_index
            if candidate.result_index >= 0
            else index,
            page_meta=candidate.page_meta or {},
        )

    @staticmethod
    def _map_filters(filters: Dict[str, object]) -> Dict[str, object]:
        mapped: Dict[str, object] = {}
        expected_cities = (
            filters.get("expected_city")
            or filters.get("期望城市")
            or filters.get("city")
        )
        if expected_cities:
            mapped["期望城市"] = expected_cities
        current_cities = filters.get("current_city") or filters.get("目前城市")
        if current_cities:
            mapped["目前城市"] = current_cities
        work_years = filters.get("work_years") or filters.get("工作年限")
        if work_years:
            mapped["工作年限"] = work_years
        education = filters.get("education") or filters.get("教育经历")
        if education:
            mapped["教育经历"] = education
        age = filters.get("age") or filters.get("年龄")
        if age:
            age_str = str(age).strip()
            # 如果是区间格式（如 25-35），按原样传递
            if re.search(r"\d+\s*(?:-|~|至|到|,|，)\s*\d+", age_str) or re.search(
                r"(?:以上|及以上|以下|以内|不超过|不低于|\+)", age_str
            ):
                mapped["年龄"] = age_str
            else:
                # A single number is an explicit upper bound. Strategy-level
                # relaxation must be recorded by the planner, not hidden here.
                match = re.search(r"(\d+)", age_str)
                if match:
                    mapped["年龄"] = {"max": int(match.group(1))}
                else:
                    mapped["年龄"] = age_str
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
        company = filters.get("company") or filters.get("公司名称")
        if company:
            mapped["公司名称"] = company
        gender = filters.get("gender") or filters.get("性别")
        if gender:
            mapped["性别"] = str(gender).strip()
        expected_salary = (
            filters.get("expected_salary")
            or filters.get("salary")
            or filters.get("期望年薪")
        )
        if expected_salary:
            mapped["期望年薪"] = expected_salary
        return mapped
