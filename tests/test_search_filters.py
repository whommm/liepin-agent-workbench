"""Unit tests for search filter normalization without a real browser."""

import pytest

from liepin_agent.core.search._filters_mixin import _FiltersMixin
from liepin_agent.core.search._models import LiepinFilterFieldSpec


class TestFiltersMixin:
    @pytest.fixture
    def mixin(self):
        return _FiltersMixin()

    def test_normalize_tag_filter_value_string(self, mixin):
        spec = LiepinFilterFieldSpec(title="教育经历", field_type="tag", container_selector="div")
        result = mixin._normalize_tag_filter_value(spec, "本科", None)
        assert result == "本科"

    def test_normalize_range_filter_value_tuple(self, mixin):
        low, high = mixin._normalize_range_filter_value((25, 35))
        assert low == "25"
        assert high == "35"

    def test_normalize_range_filter_value_dict(self, mixin):
        low, high = mixin._normalize_range_filter_value({"low": 10, "high": 20})
        assert low == "10"
        assert high == "20"

    def test_normalize_dropdown_filter_value_string(self, mixin):
        spec = LiepinFilterFieldSpec(title="性别", field_type="dropdown", container_selector="div")
        result = mixin._normalize_dropdown_filter_value(spec, "男")
        assert result == "男"


