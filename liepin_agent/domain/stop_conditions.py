"""Stop-condition evaluation for agent sessions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StopDecision:
    should_stop: bool
    reason: str = ""


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
) -> StopDecision:
    if max_runtime_minutes and elapsed_minutes >= max_runtime_minutes:
        return StopDecision(True, "已达到最大运行时长")
    if round_index >= max_rounds:
        return StopDecision(True, "已达到最大搜索轮次")
    if fetched_details >= max_detail_fetches:
        return StopDecision(True, "已达到最大详情抓取数量")
    if target_ab_count and ab_count >= target_ab_count:
        return StopDecision(True, "A/B 候选人数量已达到目标")
    if consecutive_low_yield_rounds >= 2:
        return StopDecision(True, "连续低产出轮次过多，建议人工校准")
    return StopDecision(False, "")
