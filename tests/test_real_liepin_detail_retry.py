from types import SimpleNamespace

import pytest

from liepin_agent.core.liepin_resume_extractor import LiepinResumeExtractionError
from liepin_agent.tools.real_liepin import RealLiepinTool


class FakePage:
    url = "https://h.liepin.com/resume/showresumedetail/?res_id=1"

    def __init__(self):
        self.waits = []

    def wait_for_function(self, expression, timeout=0):
        self.waits.append(("ready", timeout))

    def wait_for_timeout(self, milliseconds):
        self.waits.append(("retry", milliseconds))


class FakeBrowserManager:
    def __init__(self, page):
        self.page = page

    def run_with_page(self, callback):
        return callback(self.page)


class FakeSearchService:
    def __init__(self, page):
        self.page = page
        self.close_count = 0

    def open_candidate_detail(self, page, summary):
        return self.page

    def close_detail_page(self, detail_page, result_page):
        self.close_count += 1


class RetryExtractor:
    def __init__(self, always_fail=False):
        self.calls = 0
        self.always_fail = always_fail

    def extract_candidate(self, page, summary):
        self.calls += 1
        if self.always_fail or self.calls == 1:
            raise LiepinResumeExtractionError("页面内容尚未就绪")
        return SimpleNamespace(
            resume_text="工作经历\n负责机械结构设计" * 20,
            resume_summary="机械结构设计",
            raw_payload_json="{}",
            profile_url=page.url,
        )


def build_tool(extractor):
    page = FakePage()
    tool = object.__new__(RealLiepinTool)
    tool.config_manager = SimpleNamespace(
        config=SimpleNamespace(
            detail_extract_max_attempts=2,
            detail_extract_retry_wait_seconds=0.2,
            detail_page_ready_timeout_seconds=1.0,
        )
    )
    tool.browser_manager = FakeBrowserManager(page)
    tool.search_service = FakeSearchService(page)
    tool.resume_extractor = extractor
    tool.timing_detail_page_wait = 0
    tool._is_gold_collar_detail_page = lambda _page: False
    return tool, page


def candidate_payload():
    return {
        "id": "candidate-1",
        "name": "候选人",
        "profile_url": "https://h.liepin.com/resume/showresumedetail/?res_id=1",
        "result_index": 0,
    }


def test_candidate_detail_retries_when_first_extraction_is_empty():
    extractor = RetryExtractor()
    tool, page = build_tool(extractor)

    detail = tool.fetch_candidate_detail(candidate_payload())

    assert detail.capture_status == "success"
    assert detail.raw_payload["detail_extract_attempts"] == 2
    assert extractor.calls == 2
    assert ("retry", 200) in page.waits
    assert tool.search_service.close_count == 1


def test_candidate_detail_reports_all_attempts_after_retry_failure():
    extractor = RetryExtractor(always_fail=True)
    tool, _ = build_tool(extractor)

    with pytest.raises(LiepinResumeExtractionError, match="连续 2 次失败"):
        tool.fetch_candidate_detail(candidate_payload())

    assert extractor.calls == 2
    assert tool.search_service.close_count == 1
