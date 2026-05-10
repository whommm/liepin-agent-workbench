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
                return self.resume_extractor.extract_candidate(detail_page, summary)
            finally:
                self.search_service.close_detail_page(detail_page, page)

        extracted = self.browser_manager.run_with_page(_extract)
        return CandidateDetail(
            candidate_id=str(candidate.get("id") or ""),
            resume_text=extracted.resume_text or "",
            resume_summary=extracted.resume_summary or "",
            raw_payload={
                "raw_payload_json": extracted.raw_payload_json or "",
                "profile_url": extracted.profile_url or summary.profile_url or "",
            },
            capture_status="success"
            if (extracted.resume_text or "").strip()
            else "partial",
        )

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
