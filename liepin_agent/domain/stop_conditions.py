"""Stop-condition evaluation for agent sessions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StopDecision:
    should_stop: bool
    reason: str = ""


_UNLIMITED_DETAIL_FETCHES = 9999
_UNLIMITED_TARGET_AB = 9999


def evaluate_stop_conditions(
    round_index: int,
    max_rounds: int,
    fetched_details: int,
    max_detail_fetches: int,
    ab_count: int,
    target_ab_count: int,
    consecutive_low_yield_rounds: int,
    elapsed_minutes: float = 0,
    max_runtime_minutes: int = 0,
    low_yield_threshold: int = 2,
) -> StopDecision:
    if max_runtime_minutes and elapsed_minutes >= max_runtime_minutes:
        return StopDecision(True, "已达到最大运行时长")
    if round_index >= max_rounds:
        return StopDecision(True, "已达到最大搜索轮次")
    if 0 < max_detail_fetches < _UNLIMITED_DETAIL_FETCHES and fetched_details >= max_detail_fetches:
        return StopDecision(True, "已达到最大详情抓取数量")
    if 0 < target_ab_count < _UNLIMITED_TARGET_AB and ab_count >= target_ab_count:
        return StopDecision(True, "有效候选池已达到目标")
    if consecutive_low_yield_rounds >= max(1, low_yield_threshold):
        return StopDecision(True, "连续低产出轮次过多，建议人工校准")
    return StopDecision(False, "")
