"""Stateful filter tests that do not require a real browser."""

import pytest

from liepin_agent.core.search._base_mixin import _BaseMixin
from liepin_agent.core.search._filters_mixin import _FiltersMixin
from liepin_agent.core.search._executor_mixin import _ExecutorMixin
from liepin_agent.core.search._models import LiepinFilterFieldSpec, LiepinSearchError
from liepin_agent.core.search._models import LiepinSearchControls
from liepin_agent.tools.real_liepin import RealLiepinTool


EDUCATION_SPEC = LiepinFilterFieldSpec(
    title="教育经历",
    field_type="tag",
    container_selector="div.search-item.sfilter-edu",
)


class _FilterHarness(_FiltersMixin):
    FILTER_FIELD_SPECS = {"教育经历": EDUCATION_SPEC}

    def _dismiss_any_open_modal(self, page):
        return None


def test_map_filters_routes_city_current_city_and_expected_salary():
    mapped = RealLiepinTool._map_filters(
        {
            "city": ["深圳", "上海"],
            "current_city": ["杭州"],
            "expected_salary": {"min": 30, "max": 50},
            "age": "35岁以内",
        }
    )

    assert mapped == {
        "期望城市": ["深圳", "上海"],
        "目前城市": ["杭州"],
        "期望年薪": {"min": 30, "max": 50},
        "年龄": "35岁以内",
    }


def test_education_minimum_expands_to_all_supported_higher_degrees():
    values = _FilterHarness()._normalize_tag_filter_values(
        EDUCATION_SPEC, "本科及以上"
    )

    assert values == ["本科", "硕士", "博士/博士后"]


def test_single_age_bound_is_not_silently_relaxed():
    assert RealLiepinTool._map_filters({"age": "35"})["年龄"] == {"max": 35}


class _CloseButton:
    def __init__(self, page):
        self.page = page

    def is_visible(self, timeout=0):
        return True

    def count(self):
        return 1

    def click(self, timeout=0):
        self.page.chips.pop(0)
        self.page.close_clicks += 1


class _CloseLocator:
    def __init__(self, page):
        self.first = _CloseButton(page)


class _Chip:
    def __init__(self, page):
        self.page = page

    def locator(self, selector):
        assert selector == ".icon-close"
        return _CloseLocator(self.page)


class _ChipCollection:
    def __init__(self, page):
        self.page = page

    def count(self):
        return len(self.page.chips)

    @property
    def first(self):
        return _Chip(self.page)


class _ChipPage:
    def __init__(self, title, count):
        self.title = title
        self.chips = [object() for _ in range(count)]
        self.close_clicks = 0
        self.requested_selectors = []

    def locator(self, selector):
        self.requested_selectors.append(selector)
        assert selector == 'label[title="{}"]'.format(self.title)
        return _ChipCollection(self)

    def wait_for_timeout(self, timeout):
        return None


class _ClearHarness(_FilterHarness):
    def _wait_for_loading_cycle(self, page, timeout=0):
        return None

    def _soft_wait_for_results(self, page):
        return None


def test_clear_filter_condition_clicks_each_active_chip_close_button():
    page = _ChipPage("期望城市", count=3)

    _ClearHarness()._clear_filter_condition(page, "期望城市")

    assert page.close_clicks == 3
    assert page.chips == []
    assert page.requested_selectors == ['label[title="期望城市"]']


class _Input:
    def __init__(self, name, events):
        self.name = name
        self.events = events
        self.value = None

    def click(self, timeout=0, force=False):
        return None

    def fill(self, value):
        self.value = value
        self.events.append("fill:{}:{}".format(self.name, value))


class _Confirm:
    def __init__(self, container, events):
        self.container = container
        self.events = events

    def click(self, timeout=0):
        assert self.container.hovered is True
        self.events.append("confirm")


class _RangeContainer:
    def __init__(self, events):
        self.events = events
        self.hovered = False

    def is_visible(self, timeout=0):
        return True

    def hover(self, timeout=0):
        self.hovered = True
        self.events.append("hover")


class _RangeHarness(_FilterHarness):
    def __init__(self):
        self.events = []
        self.container = _RangeContainer(self.events)
        self.low = _Input("low", self.events)
        self.high = _Input("high", self.events)
        self.confirm = _Confirm(self.container, self.events)

    def _field_container(self, page, spec):
        return self.container

    def _resolve_filter_locator(self, page, container, selector, error_message):
        if selector == "confirm":
            assert container.hovered is True
            return self.confirm
        return {"low": self.low, "high": self.high}[selector]

    def _wait_for_filter_apply(self, page, expected_text="", timeout=0):
        return None

    def _wait_for_condition_chip(self, page, title, expected_text="", timeout=0):
        return None


def test_range_filter_hovers_container_before_clicking_confirm():
    spec = LiepinFilterFieldSpec(
        title="期望年薪",
        field_type="range",
        container_selector="div.salary",
        low_input_selector="low",
        high_input_selector="high",
        confirm_selector="confirm",
    )
    harness = _RangeHarness()

    harness._apply_range_filter(object(), spec, {"min": 30, "max": 50})

    assert harness.low.value == "30"
    assert harness.high.value == "50"
    assert harness.events.index("hover") < harness.events.index("confirm")


class _FailingApplyHarness(_FilterHarness):
    def _apply_filter_with_retries(self, page, title, value, attempts=2):
        raise RuntimeError("selector changed")


def test_apply_filters_on_page_raises_when_any_filter_fails():
    with pytest.raises(LiepinSearchError, match="筛选条件未完整生效") as exc_info:
        _FailingApplyHarness()._apply_filters_on_page(
            object(), {"教育经历": "本科"}
        )

    assert "selector changed" in str(exc_info.value)


class _ExecutorHarness(_ExecutorMixin):
    def __init__(self):
        self.events = []

    def _apply_search_execution_options(self, page, match_mode="", scope=""):
        self.events.append("options")

    def _dismiss_any_open_modal(self, page):
        self.events.append("dismiss")

    def _clear_managed_filter_conditions(self, page):
        self.events.append("clear-filters")

    def _clear_search_inputs(self, page):
        self.events.append("clear-keyword")

    def _detect_search_controls(self, page):
        return LiepinSearchControls(search_input=object(), search_button=object())

    def _write_keyword(self, locator, keyword, force_focus=False):
        self.events.append("write:{}".format(keyword))

    def _submit_search(self, page, controls=None):
        self.events.append("submit")

    def _wait_for_results(self, page):
        self.events.append("results")


def test_execute_search_always_resets_managed_filters_before_keyword():
    harness = _ExecutorHarness()

    harness._execute_search(object(), "Java")

    assert harness.events == [
        "options",
        "dismiss",
        "clear-filters",
        "clear-keyword",
        "write:Java",
        "submit",
        "results",
    ]


class _CityOption:
    def __init__(self, modal, text):
        self.modal = modal
        self.text = text

    def is_visible(self, timeout=0):
        return True

    def inner_text(self, timeout=0):
        return self.text

    def click(self, timeout=0):
        self.modal.clicks.append(self.text)
        if self.text == "北京":
            self.modal.drilled = True


class _CityOptions:
    def __init__(self, modal, texts):
        self.modal = modal
        self.texts = texts

    def count(self):
        return len(self.texts)

    def nth(self, index):
        return _CityOption(self.modal, self.texts[index])


class _CityModal:
    def __init__(self):
        self.page = self
        self.drilled = False
        self.clicks = []

    def locator(self, selector):
        if selector != "span.ant-tag.ant-tag-checkable":
            return _CityOptions(self, [])
        return _CityOptions(self, ["全北京"] if self.drilled else ["北京"])

    def wait_for_timeout(self, timeout):
        return None


def test_municipality_city_selection_drills_down_to_whole_city():
    modal = _CityModal()

    selected = _FilterHarness()._click_city_option_in_modal(modal, "北京")

    assert selected is True
    assert modal.clicks == ["北京", "全北京"]


class _KeywordInput:
    def __init__(self):
        self.value = "旧关键词"
        self.selected = False
        self.keys = []

    def click(self, timeout=0, force=False):
        return None

    def focus(self):
        return None

    def press(self, key):
        self.keys.append(key)
        if key == "Control+A":
            self.selected = True
        elif key == "Backspace" and self.selected:
            self.value = ""

    def fill(self, value):
        # Model Liepin's controlled input where fill("") alone is restored.
        if value:
            self.value = value

    def input_value(self, timeout=0):
        return self.value


class _KeywordHarness(_BaseMixin):
    def __init__(self, locator):
        self.locator = locator

    def _find_primary_search_input(self, page):
        return self.locator


def test_keyword_clear_uses_select_all_and_backspace_when_empty_fill_is_unstable():
    locator = _KeywordInput()

    _KeywordHarness(locator)._clear_search_inputs(object())

    assert locator.value == ""
    assert locator.keys[:2] == ["Control+A", "Backspace"]


class _LoadingHarness(_BaseMixin):
    def __init__(self, states):
        self.states = iter(states)

    def _is_loading(self, page):
        return next(self.states, False)


class _WaitPage:
    def __init__(self):
        self.waits = 0

    def wait_for_timeout(self, timeout):
        self.waits += 1


def test_loading_wait_returns_after_short_stable_no_loading_window():
    page = _WaitPage()

    _LoadingHarness([False, False, False])._wait_for_loading_cycle(page)

    assert page.waits == 2
