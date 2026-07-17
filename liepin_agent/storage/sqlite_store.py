"""SQLite persistence for the Agent workbench.

This module provides a facade class that composes functionality from
specialized mixins in the repos/ package.
"""

from __future__ import annotations

from .repos._base_mixin import _BaseMixin
from .repos._session_mixin import _SessionMixin
from .repos._criteria_mixin import _CriteriaMixin
from .repos._round_mixin import _RoundMixin
from .repos._candidate_mixin import _CandidateMixin
from .repos._match_mixin import _MatchMixin
from .repos._metrics_mixin import _MetricsMixin
from .repos._event_mixin import _EventMixin
from .repos._pool_mixin import _PoolMixin
from .repos._feedback_mixin import _FeedbackMixin
from .repos._profile_mixin import _ProfileMixin
from .repos._evaluation_mixin import _EvaluationMixin
from .repos._search_intelligence_mixin import _SearchIntelligenceMixin
from .repos._ranking_mixin import _RankingMixin
from .repos._base_mixin import now_text, to_json, from_json

__all__ = ["SQLiteStore", "now_text", "to_json", "from_json"]

class SQLiteStore(
    _BaseMixin,
    _SessionMixin,
    _CriteriaMixin,
    _RoundMixin,
    _CandidateMixin,
    _MatchMixin,
    _MetricsMixin,
    _EventMixin,
    _PoolMixin,
    _FeedbackMixin,
    _ProfileMixin,
    _EvaluationMixin,
    _SearchIntelligenceMixin,
    _RankingMixin,
):
    """Repository facade around the workbench SQLite database."""
