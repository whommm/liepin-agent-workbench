import pytest

from liepin_agent.core.search._executor_mixin import _ExecutorMixin
from liepin_agent.core.config import AppConfig
from liepin_agent.core.search._models import (
    AdaptivePaginationPolicy,
    LiepinSearchCandidate,
    PageYieldStats,
    SearchCursorLostError,
)


def _page(prefix: str, count: int = 4):
    return [
        LiepinSearchCandidate(
            name=f"{prefix}-{index}",
            current_title="产品经理",
            current_company=f"公司-{prefix}-{index}",
            profile_url=f"https://h.liepin.com/resume/{prefix}-{index}",
            raw_text="卡片内容",
            result_index=index,
        )
        for index in range(count)
    ]


class _FakePage:
    def __init__(self, url="https://h.liepin.com/search/getConditionItem#session"):
        self.url = url


class _FakeBrowser:
    def __init__(self):
        self.page = _FakePage()

    def run_with_page(self, callback):
        return callback(self.page)

    @staticmethod
    def _is_search_page_url(url):
        return "h.liepin.com/search" in (url or "").lower()


class _FakeSearch(_ExecutorMixin):
    def __init__(self, pages):
        self.pages = pages
        self.page_index = 0
        self.execute_search_calls = 0
        self.browser_manager = _FakeBrowser()

    def open_search_page(self):
        return {}

    def _execute_search(self, page, keyword, **kwargs):
        self.execute_search_calls += 1
        self.page_index = 0

    def _apply_filters_on_page(self, page, filters):
        return None

    def extract_candidates_from_page(self, page):
        return list(self.pages[self.page_index])

    def _extract_page_meta(self, page):
        return {"total_results": 100}

    def _get_current_page_number(self, page):
        return self.page_index + 1

    def go_to_next_result_page(self):
        if self.page_index + 1 >= len(self.pages):
            return False
        self.page_index += 1
        return True

    def _with_debug_snapshot(self, name, callback):
        return callback()


def test_policy_stops_after_consecutive_low_signal_pages():
    policy = AdaptivePaginationPolicy(min_pages=2, max_pages=10, low_yield_patience=2)
    history = [
        PageYieldStats(page_num=1, raw_count=20, new_unique=20),
        PageYieldStats(page_num=2, raw_count=20, new_unique=20),
    ]

    assert policy.assess(history[:1]).continue_paging is True
    decision = policy.assess(history)
    assert decision.continue_paging is False
    assert decision.low_yield_streak == 2


def test_policy_continues_enriched_pages_until_hard_limit():
    policy = AdaptivePaginationPolicy(min_pages=2, max_pages=10)
    history = [
        PageYieldStats(
            page_num=page_num,
            raw_count=20,
            new_unique=18,
            duplicate_count=2,
            potential_count=4,
            validate_count=3,
        )
        for page_num in range(1, 11)
    ]

    assert policy.assess(history[:9]).continue_paging is True
    assert policy.assess(history).continue_paging is False
    assert "硬上限" in policy.assess(history).reason


def test_search_adaptive_policy_stops_low_value_results_at_two_pages():
    service = _FakeSearch([_page(str(index)) for index in range(6)])
    policy = AdaptivePaginationPolicy(min_pages=2, max_pages=6, low_yield_patience=2)

    candidates = service.search(
        "产品",
        max_pages=6,
        pagination_policy=policy,
        candidate_classifier=lambda candidate: "other",
    )

    assert len(candidates) == 8
    assert len(service.last_pagination_stats) == 2
    assert service.last_pagination_stats[-1].potential_count == 0


def test_search_adaptive_policy_continues_enriched_results():
    service = _FakeSearch([_page(str(index)) for index in range(6)])
    policy = AdaptivePaginationPolicy(min_pages=2, max_pages=4)

    candidates = service.search(
        "产品",
        max_pages=10,
        pagination_policy=policy,
        candidate_classifier=lambda candidate: "potential",
    )

    assert len(candidates) == 16
    assert len(service.last_pagination_stats) == 4
    assert all(item.potential_count == 4 for item in service.last_pagination_stats)


def test_search_integer_max_pages_keeps_legacy_fixed_page_behavior():
    service = _FakeSearch([_page(str(index)) for index in range(5)])

    candidates = service.search("产品", max_pages=3)

    assert len(candidates) == 12
    assert len(service.last_pagination_stats) == 3


def test_historical_candidate_keys_count_as_duplicates_and_remain_in_raw_results():
    page = _page("history")
    service = _FakeSearch([page])
    known_keys = [service._candidate_dedupe_key(item) for item in page[:3]]

    candidates = service.search(
        "产品",
        max_pages=1,
        known_candidate_keys=known_keys,
    )

    assert [item.name for item in candidates] == [
        "history-0",
        "history-1",
        "history-2",
        "history-3",
    ]
    assert [item.result_index for item in candidates] == [0, 1, 2, 3]
    assert all(
        item.page_meta.get("seen_in_previous_round") is True
        for item in candidates[:3]
    )
    assert candidates[3].page_meta.get("duplicate_in_search") is not True
    stats = service.last_pagination_stats[0]
    assert stats.raw_count == 4
    assert stats.new_unique == 1
    assert stats.duplicate_count == 3
    assert stats.duplicate_rate == 0.75


def test_historical_composite_key_uses_storage_normalization():
    candidate = LiepinSearchCandidate(
        name=" 张 三 ",
        current_company="ACME Energy",
        current_title="产品 经理",
        raw_text="卡片内容",
    )
    service = _FakeSearch([[candidate]])

    candidates = service.search(
        "产品",
        max_pages=1,
        known_candidate_keys=["张三|acmeenergy|产品经理"],
    )

    assert candidates == [candidate]
    assert candidate.page_meta["seen_in_previous_round"] is True
    assert service.last_pagination_stats[0].new_unique == 0
    assert service.last_pagination_stats[0].duplicate_count == 1


def test_duplicate_from_an_earlier_page_reduces_later_page_yield():
    first_page = _page("first", count=2)
    second_page = [_page("first", count=1)[0], _page("second", count=1)[0]]
    service = _FakeSearch([first_page, second_page])

    candidates = service.search("产品", max_pages=2)

    assert len(candidates) == 4
    assert candidates[2].page_meta["duplicate_in_search"] is True
    assert candidates[2].result_index == 0
    assert service.last_pagination_stats[0].new_unique == 2
    assert service.last_pagination_stats[1].new_unique == 1
    assert service.last_pagination_stats[1].duplicate_count == 1
    assert service.last_pagination_stats[1].duplicate_rate == 0.5


def test_default_policy_observes_three_pages_and_caps_at_ten():
    config = AppConfig()
    policy = AdaptivePaginationPolicy()
    pages = [_page(f"known-{index}") for index in range(5)]
    known_keys = [
        _FakeSearch._candidate_dedupe_key(candidate)
        for page in pages
        for candidate in page
    ]
    service = _FakeSearch(pages)

    candidates = service.search(
        "产品",
        max_pages=policy,
        known_candidate_keys=known_keys,
    )

    assert config.search_min_pages_per_round == 3
    assert config.search_max_pages_per_round == 10
    assert policy.effective_min_pages == 3
    assert policy.effective_max_pages == 10
    assert len(candidates) == 12
    assert len(service.last_pagination_stats) == 3
    assert all(stats.new_unique == 0 for stats in service.last_pagination_stats)


def test_checkpoint_search_exports_cursor_and_stops_at_checkpoint():
    service = _FakeSearch([_page(str(index)) for index in range(8)])

    candidates = service.search(
        "产品",
        filters={"城市": "上海"},
        match_mode="all",
        scope="目前职位",
        position_filter="产品经理",
        max_pages=8,
        checkpoint_pages=3,
    )

    assert len(candidates) == 12
    cursor = service.last_search_cursor
    assert cursor.query == "产品"
    assert cursor.filters == {"城市": "上海"}
    assert cursor.match_mode == "all"
    assert cursor.scope == "目前职位"
    assert cursor.position_filter == "产品经理"
    assert cursor.page_num == 3
    assert cursor.total_results == 100
    assert cursor.exhausted is False
    assert len(cursor.history) == 3
    assert len(cursor.seen_keys) == 12
    assert service.last_pagination_stats is cursor.history


def test_checkpoint_policy_verdict_becomes_signal_without_stopping():
    service = _FakeSearch([_page(str(index)) for index in range(6)])
    policy = AdaptivePaginationPolicy(min_pages=2, max_pages=6, low_yield_patience=2)

    candidates = service.search(
        "产品",
        max_pages=6,
        pagination_policy=policy,
        candidate_classifier=lambda candidate: "other",
        checkpoint_pages=5,
    )

    # The same low-yield setup stops the legacy path at page 2; in checkpoint
    # mode the policy verdict is recorded as signal and pagination continues.
    assert len(candidates) == 20
    history = service.last_search_cursor.history
    assert len(history) == 5
    assert history[0].policy_continue is True
    assert all(item.policy_continue is False for item in history[1:])
    assert all(item.policy_reason for item in history)


def test_checkpoint_search_marks_exhausted_when_no_next_page():
    service = _FakeSearch([_page(str(index)) for index in range(2)])

    service.search("产品", max_pages=6, checkpoint_pages=5)

    cursor = service.last_search_cursor
    assert cursor.page_num == 2
    assert cursor.exhausted is True
    assert service.resume_pagination(cursor, 2) == []


def test_resume_pagination_continues_with_cumulative_dedupe():
    service = _FakeSearch([_page(str(index)) for index in range(8)])
    service.search("产品", max_pages=8, checkpoint_pages=3)
    cursor = service.last_search_cursor

    batch = service.resume_pagination(cursor, 2)

    assert len(batch) == 8
    assert {item.page_meta["page_num"] for item in batch} == {4, 5}
    assert service.execute_search_calls == 1  # no rebuild needed
    assert cursor.page_num == 5
    assert len(cursor.history) == 5
    assert len(cursor.seen_keys) == 20
    assert all(item.new_unique == 4 for item in cursor.history)


def test_resume_pagination_recovers_lost_cursor_by_rebuilding_search():
    service = _FakeSearch([_page(str(index)) for index in range(8)])
    service.search("产品", max_pages=8, checkpoint_pages=3)
    cursor = service.last_search_cursor

    # Simulate SPA state loss between batches: DOM is back on page 1.
    service.page_index = 0

    batch = service.resume_pagination(cursor, 2)

    assert service.execute_search_calls == 2  # search rebuilt
    # After rebuilding and click-advancing back to page 3, the batch turns to
    # the next pages, so only fresh pages 4 and 5 are collected.
    assert {item.page_meta["page_num"] for item in batch} == {4, 5}
    assert all(
        item.page_meta.get("duplicate_in_search") is not True for item in batch
    )
    assert cursor.page_num == 5
    assert len(cursor.history) == 5
    assert all(item.new_unique == 4 for item in cursor.history)
    assert len(cursor.seen_keys) == 20


def test_resume_pagination_recovers_from_card_fingerprint_mismatch():
    service = _FakeSearch([_page(str(index)) for index in range(8)])
    service.search("产品", max_pages=8, checkpoint_pages=3)
    cursor = service.last_search_cursor

    # Same URL and page number, but the page content no longer matches.
    service.pages[2] = _page("intruder")

    batch = service.resume_pagination(cursor, 1)

    # The fingerprint mismatch triggers a rebuild; after click-advancing back
    # to page 3 the batch turns to page 4 and collects its cards.
    assert service.execute_search_calls == 2
    assert [item.name for item in batch] == ["3-0", "3-1", "3-2", "3-3"]


def test_resume_pagination_raises_cursor_lost_when_recovery_fails():
    service = _FakeSearch([_page(str(index)) for index in range(8)])
    service.search("产品", max_pages=8, checkpoint_pages=3)
    cursor = service.last_search_cursor

    # SPA state lost and the rebuilt search can no longer reach page 3.
    service.page_index = 0
    service.pages = service.pages[:2]

    with pytest.raises(SearchCursorLostError):
        service.resume_pagination(cursor, 2)


def test_resume_pagination_clamps_to_page_cap():
    service = _FakeSearch([_page(str(index)) for index in range(8)])
    service.search("产品", max_pages=8, checkpoint_pages=3)
    cursor = service.last_search_cursor

    batch = service.resume_pagination(cursor, 5, page_cap=4)

    assert len(batch) == 4
    assert cursor.page_num == 4
    assert service.resume_pagination(cursor, 1, page_cap=4) == []
