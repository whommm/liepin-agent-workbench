from liepin_agent.core.search._executor_mixin import _ExecutorMixin
from liepin_agent.core.config import AppConfig
from liepin_agent.core.search._models import (
    AdaptivePaginationPolicy,
    LiepinSearchCandidate,
    PageYieldStats,
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


class _FakeBrowser:
    def __init__(self):
        self.page = object()

    def run_with_page(self, callback):
        return callback(self.page)


class _FakeSearch(_ExecutorMixin):
    def __init__(self, pages):
        self.pages = pages
        self.page_index = 0
        self.browser_manager = _FakeBrowser()

    def open_search_page(self):
        return {}

    def _execute_search(self, page, keyword, **kwargs):
        self.page_index = 0

    def _apply_filters_on_page(self, page, filters):
        return None

    def extract_candidates_from_page(self, page):
        return list(self.pages[self.page_index])

    def _extract_page_meta(self, page):
        return {}

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
