"""Shared recommendation-state policy for greeting workflows."""

from __future__ import annotations

from typing import Iterable

from .recommendation import (
    EXPLICIT_MISMATCH,
    HIGH_POTENTIAL_VERIFY,
    INFORMATION_INSUFFICIENT,
    PRIORITY_CONTACT,
    RECOMMENDATION_LABELS,
    TRANSFERABLE_EXPLORE,
)


GREETING_SELECTABLE_STATES = (
    PRIORITY_CONTACT,
    HIGH_POTENTIAL_VERIFY,
    TRANSFERABLE_EXPLORE,
    INFORMATION_INSUFFICIENT,
)

DEFAULT_GREETING_STATES = (
    PRIORITY_CONTACT,
    HIGH_POTENTIAL_VERIFY,
)

GREETING_BLOCKED_STATES = (EXPLICIT_MISMATCH,)

_LABEL_TO_STATE = {label: state for state, label in RECOMMENDATION_LABELS.items()}


def parse_recommendation_state(value: object) -> str:
    """Accept the persisted code or the Chinese label used in exported workbooks."""
    text = str(value or "").strip()
    if text in RECOMMENDATION_LABELS:
        return text
    return _LABEL_TO_STATE.get(text, "")


def normalize_greeting_states(values: Iterable[object] | None) -> tuple[str, ...]:
    if values is None:
        return DEFAULT_GREETING_STATES
    requested = {parse_recommendation_state(value) for value in values}
    return tuple(state for state in GREETING_SELECTABLE_STATES if state in requested)
