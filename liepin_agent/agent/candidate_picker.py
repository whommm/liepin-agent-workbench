"""Recall-first, deterministic candidate detail selection."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from ..domain.models import CandidateSummary, FetchDecision, Observation
from ..domain.states import RoundType


MUST_FETCH = "must_fetch"
VALIDATE = "validate"
EXPLORE = "explore"
SKIP = "skip"
FETCH_BUCKETS = (MUST_FETCH, VALIDATE, EXPLORE)

_ALREADY_FETCHED_STATUSES = {
    "detail_queued",
    "detail_fetching",
    "detail_fetched",
    "quick_checked",
    "match_queued",
    "matching",
    "matched",
    "shortlisted",
    "rejected",
}
_HARD_CONFLICT_PREFIXES = (
    "hard_conflict:",
    "hard-conflict:",
    "硬冲突:",
    "硬冲突：",
    "明确硬冲突:",
    "明确硬冲突：",
)


@dataclass
class CandidateChoice:
    """One card's deterministic bucket and the evidence for that decision."""

    candidate: CandidateSummary
    candidate_key: str
    bucket: str
    reason: str
    selected: bool = False
    audit: bool = False
    diversity_score: float = 0.0


@dataclass
class CandidateSelection:
    """Full selection result, including candidates that were not fetched."""

    choices: List[CandidateChoice] = field(default_factory=list)

    @property
    def selected(self) -> List[CandidateSummary]:
        return [choice.candidate for choice in self.choices if choice.selected]

    @property
    def audit_candidate_ids(self) -> List[str]:
        return [
            choice.candidate.id
            for choice in self.choices
            if choice.selected and choice.audit and choice.candidate.id
        ]

    def bucket_ids(self) -> Dict[str, List[str]]:
        result = {bucket: [] for bucket in (*FETCH_BUCKETS, SKIP)}
        for choice in self.choices:
            result.setdefault(choice.bucket, []).append(
                choice.candidate.id or choice.candidate_key
            )
        return result

    def reasons(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        counts: Counter[str] = Counter()
        for choice in self.choices:
            base_key = choice.candidate.id or choice.candidate_key
            counts[base_key] += 1
            key = base_key if counts[base_key] == 1 else f"{base_key}#{counts[base_key]}"
            selected = "抓取" if choice.selected else "不抓取"
            audit = "，跳过桶抽查" if choice.audit else ""
            result[key] = f"{choice.bucket}: {choice.reason}；{selected}{audit}"
        return result


class CandidatePicker:
    """Choose detail fetches without asking an LLM to re-read result cards.

    Cards are split into four buckets. Missing card information is treated as
    uncertainty (``validate``), never as evidence of a mismatch. ``skip`` is
    reserved for operational duplicates/invalid cards/already fetched records
    and explicitly marked hard conflicts.
    """

    DEFAULT_STRATEGIES: Dict[str, Dict[str, object]] = {
        RoundType.SAMPLE_DETAIL.value: {
            "limit": 10,
            "min_results": 3,
            "timeout_seconds": 300,
            "bucket_weights": {MUST_FETCH: 0.40, VALIDATE: 0.40, EXPLORE: 0.20},
            "explore_pool_rate": 0.20,
            "skip_audit_rate": 0.08,
        },
        RoundType.VALIDATE_DETAIL.value: {
            "limit": 20,
            "min_results": 8,
            "timeout_seconds": 300,
            "bucket_weights": {MUST_FETCH: 0.60, VALIDATE: 0.25, EXPLORE: 0.15},
            "explore_pool_rate": 0.15,
            "skip_audit_rate": 0.08,
        },
        RoundType.HARVEST_DETAIL.value: {
            "limit": 40,
            "min_results": 5,
            "timeout_seconds": 300,
            "bucket_weights": {MUST_FETCH: 0.70, VALIDATE: 0.20, EXPLORE: 0.10},
            "explore_pool_rate": 0.10,
            "skip_audit_rate": 0.05,
        },
    }

    def __init__(self, strategies: Optional[Dict[str, Dict[str, object]]] = None):
        self.strategies = deepcopy(self.DEFAULT_STRATEGIES)
        for key, values in (strategies or {}).items():
            if key not in self.strategies or not isinstance(values, dict):
                continue
            bucket_weights = values.get("bucket_weights")
            self.strategies[key].update(values)
            if isinstance(bucket_weights, dict):
                merged_weights = dict(
                    self.DEFAULT_STRATEGIES[key].get("bucket_weights") or {}
                )
                merged_weights.update(bucket_weights)
                self.strategies[key]["bucket_weights"] = merged_weights

    def decide(
        self,
        observation: Observation,
        candidates: List[CandidateSummary],
        remaining_detail_budget: int,
        *,
        already_fetched_ids: Optional[Iterable[str]] = None,
        hard_conflict_predicate: Optional[
            Callable[[CandidateSummary], bool]
        ] = None,
    ) -> FetchDecision:
        if remaining_detail_budget <= 0:
            return FetchDecision(
                action="skip_detail",
                round_type=RoundType.SKIP_DETAIL.value,
                reason="详情预算已用尽",
            )

        requested_round_type = observation.recommended_round_type
        # A weak observation cannot turn incomplete cards into rejection evidence.
        # Use a small validation sample when valid unknown cards still exist.
        effective_round_type = (
            RoundType.SAMPLE_DETAIL.value
            if requested_round_type == RoundType.SKIP_DETAIL.value
            else requested_round_type
        )
        cfg = self.strategies.get(
            effective_round_type,
            self.strategies[RoundType.HARVEST_DETAIL.value],
        )
        limit = min(int(cfg.get("limit") or 0), remaining_detail_budget)
        selection = self.select(
            candidates,
            limit,
            strategy=cfg,
            already_fetched_ids=already_fetched_ids,
            hard_conflict_predicate=hard_conflict_predicate,
        )
        picked = selection.selected
        if not picked:
            return FetchDecision(
                action="skip_detail",
                round_type=RoundType.SKIP_DETAIL.value,
                reason="没有可抓取的新卡片：仅存在重复、已抓取、无效或明确硬冲突",
                selection_buckets=selection.bucket_ids(),
                selection_reasons=selection.reasons(),
            )

        min_results = min(int(cfg.get("min_results") or 1), len(picked))
        wait_mode = (
            "no_wait"
            if effective_round_type == RoundType.HARVEST_DETAIL.value
            else "wait_min_results"
        )
        selected_counts = Counter(
            choice.bucket for choice in selection.choices if choice.selected
        )
        strategy_summary = {
            MUST_FETCH: selected_counts[MUST_FETCH],
            VALIDATE: selected_counts[VALIDATE],
            EXPLORE: selected_counts[EXPLORE],
            "skip_audit": len(selection.audit_candidate_ids),
        }
        return FetchDecision(
            action="fetch_details",
            round_type=effective_round_type,
            candidate_ids=[item.id for item in picked if item.id],
            fetch_limit=len(picked),
            sampling_strategy=strategy_summary,
            match_wait_policy={
                "mode": wait_mode,
                "min_results": min_results,
                "timeout_seconds": int(cfg.get("timeout_seconds") or 300),
            },
            reason=(
                "按召回优先分层策略抓取 {} 人：必抓 {}、验证 {}、探索 {}、"
                "跳过桶抽查 {}。{}"
            ).format(
                len(picked),
                selected_counts[MUST_FETCH],
                selected_counts[VALIDATE],
                selected_counts[EXPLORE],
                len(selection.audit_candidate_ids),
                observation.reason,
            ),
            selection_buckets=selection.bucket_ids(),
            selection_reasons=selection.reasons(),
            audit_candidate_ids=selection.audit_candidate_ids,
        )

    def select(
        self,
        candidates: Sequence[CandidateSummary],
        limit: int,
        *,
        strategy: Optional[Mapping[str, object]] = None,
        already_fetched_ids: Optional[Iterable[str]] = None,
        hard_conflict_predicate: Optional[
            Callable[[CandidateSummary], bool]
        ] = None,
    ) -> CandidateSelection:
        """Bucket and select cards deterministically within ``limit``.

        This method is deliberately pure with respect to storage and the LLM so
        production runtime and tests can use the same policy.
        """
        cfg = dict(strategy or self.DEFAULT_STRATEGIES[RoundType.SAMPLE_DETAIL.value])
        choices = self.bucket_candidates(
            candidates,
            already_fetched_ids=already_fetched_ids,
            hard_conflict_predicate=hard_conflict_predicate,
            explore_pool_rate=float(cfg.get("explore_pool_rate") or 0.20),
        )
        if limit <= 0:
            return CandidateSelection(choices=choices)

        hard_conflict_choices = [
            choice
            for choice in choices
            if choice.bucket == SKIP and choice.reason.startswith("明确硬冲突")
        ]
        audit_rate = max(0.0, min(float(cfg.get("skip_audit_rate") or 0.0), 0.25))
        audit_count = math.ceil(len(hard_conflict_choices) * audit_rate) if audit_rate else 0
        primary_exists = any(choice.bucket in FETCH_BUCKETS for choice in choices)
        max_audits = max(0, limit - 1) if primary_exists else limit
        audit_count = min(audit_count, max_audits)
        audits = sorted(hard_conflict_choices, key=self._stable_audit_key)[:audit_count]
        for choice in audits:
            choice.selected = True
            choice.audit = True

        primary_limit = max(0, limit - len(audits))
        weights = self._normalized_weights(cfg.get("bucket_weights"))
        selected_primary = self._stratified_pick(choices, primary_limit, weights)
        for choice in selected_primary:
            choice.selected = True
        return CandidateSelection(choices=choices)

    def bucket_candidates(
        self,
        candidates: Sequence[CandidateSummary],
        *,
        already_fetched_ids: Optional[Iterable[str]] = None,
        hard_conflict_predicate: Optional[
            Callable[[CandidateSummary], bool]
        ] = None,
        explore_pool_rate: float = 0.20,
    ) -> List[CandidateChoice]:
        """Return all four buckets; unknown facts always remain fetchable."""
        already_fetched: Set[str] = {
            self._norm(value)
            for value in (already_fetched_ids or [])
            if self._norm(value)
        }
        seen_identities: Set[str] = set()
        choices: List[CandidateChoice] = []

        for index, candidate in enumerate(candidates or []):
            key = self._candidate_key(candidate, index)
            aliases = self._candidate_identity_aliases(candidate)
            if self._is_invalid(candidate):
                choices.append(CandidateChoice(candidate, key, SKIP, "无效卡片：缺少身份或有效摘要"))
                continue
            if (
                any(alias in already_fetched for alias in aliases)
                or (candidate.status or "").lower() in _ALREADY_FETCHED_STATUSES
            ):
                choices.append(CandidateChoice(candidate, key, SKIP, "已经抓取或正在抓取详情"))
                seen_identities.update(aliases)
                continue
            if any(alias in seen_identities for alias in aliases):
                choices.append(CandidateChoice(candidate, key, SKIP, "重复候选人卡片"))
                continue
            seen_identities.update(aliases)

            hard_conflict, conflict_reason = self._explicit_hard_conflict(
                candidate, hard_conflict_predicate
            )
            if hard_conflict:
                choices.append(
                    CandidateChoice(
                        candidate,
                        key,
                        SKIP,
                        f"明确硬冲突：{conflict_reason}",
                    )
                )
                continue

            signals = [
                str(value).strip()
                for value in (candidate.card_signals or [])
                if str(value).strip()
            ]
            explicit_decision = (candidate.card_decision or "").strip().lower()
            if explicit_decision in {MUST_FETCH, "fetch"}:
                reason = candidate.card_reason or "卡片被明确标记为高潜"
                choices.append(CandidateChoice(candidate, key, MUST_FETCH, reason))
            elif signals:
                choices.append(
                    CandidateChoice(
                        candidate,
                        key,
                        MUST_FETCH,
                        "命中正向卡片信号：{}".format("、".join(signals[:4])),
                    )
                )
            elif int(candidate.pre_score or 0) >= 75:
                choices.append(
                    CandidateChoice(candidate, key, MUST_FETCH, "已有高置信卡片信号")
                )
            elif explicit_decision == EXPLORE or (candidate.page_meta or {}).get("explore") is True:
                choices.append(
                    CandidateChoice(candidate, key, EXPLORE, "显式探索样本")
                )
            else:
                choices.append(
                    CandidateChoice(
                        candidate,
                        key,
                        VALIDATE,
                        "卡片信息不足，缺失视为未知，需抓详情验证",
                    )
                )

        self._promote_diverse_unknowns(choices, explore_pool_rate)
        return choices

    @staticmethod
    def _normalized_weights(value: object) -> Dict[str, float]:
        raw = value if isinstance(value, Mapping) else {}
        weights = {
            bucket: max(0.0, float(raw.get(bucket, 0.0)))
            for bucket in FETCH_BUCKETS
        }
        total = sum(weights.values())
        if total <= 0:
            return {MUST_FETCH: 0.5, VALIDATE: 0.3, EXPLORE: 0.2}
        return {bucket: weight / total for bucket, weight in weights.items()}

    @staticmethod
    def _stratified_pick(
        choices: Sequence[CandidateChoice],
        limit: int,
        weights: Mapping[str, float],
    ) -> List[CandidateChoice]:
        if limit <= 0:
            return []
        pools: Dict[str, List[CandidateChoice]] = {
            MUST_FETCH: sorted(
                (choice for choice in choices if choice.bucket == MUST_FETCH),
                key=lambda choice: (
                    -len(choice.candidate.card_signals or []),
                    -int(choice.candidate.pre_score or 0),
                    choice.candidate.result_index,
                ),
            ),
            VALIDATE: sorted(
                (choice for choice in choices if choice.bucket == VALIDATE),
                key=lambda choice: choice.candidate.result_index,
            ),
            EXPLORE: sorted(
                (choice for choice in choices if choice.bucket == EXPLORE),
                key=lambda choice: (-choice.diversity_score, choice.candidate.result_index),
            ),
        }
        active = [bucket for bucket in FETCH_BUCKETS if pools[bucket]]
        selected: List[CandidateChoice] = []
        offsets = {bucket: 0 for bucket in FETCH_BUCKETS}
        if limit >= len(active):
            for bucket in active:
                selected.append(pools[bucket][0])
                offsets[bucket] = 1

        # Fill toward each bucket's weighted target. The one-per-active-bucket
        # reservation above prevents a large high-signal pool from erasing
        # validation and exploration coverage.
        while len(selected) < limit:
            available = [
                bucket
                for bucket in FETCH_BUCKETS
                if offsets[bucket] < len(pools[bucket])
            ]
            if not available:
                break
            bucket = max(
                available,
                key=lambda value: (
                    limit * weights.get(value, 0.0) - offsets[value],
                    -FETCH_BUCKETS.index(value),
                ),
            )
            selected.append(pools[bucket][offsets[bucket]])
            offsets[bucket] += 1
        return selected

    @classmethod
    def _promote_diverse_unknowns(
        cls, choices: Sequence[CandidateChoice], explore_pool_rate: float
    ) -> None:
        unknown = [choice for choice in choices if choice.bucket == VALIDATE]
        if len(unknown) < 3 or explore_pool_rate <= 0:
            return
        companies = Counter(cls._norm(choice.candidate.current_company) for choice in unknown)
        titles = Counter(cls._norm(choice.candidate.current_title) for choice in unknown)
        cities = Counter(cls._norm(choice.candidate.city) for choice in unknown)
        for choice in unknown:
            candidate = choice.candidate
            company = cls._norm(candidate.current_company)
            title = cls._norm(candidate.current_title)
            city = cls._norm(candidate.city)
            choice.diversity_score = sum(
                weight / frequencies[value]
                for value, frequencies, weight in (
                    (company, companies, 2.0),
                    (title, titles, 1.0),
                    (city, cities, 0.5),
                )
                if value
            )
        count = max(1, math.ceil(len(unknown) * min(explore_pool_rate, 0.5)))
        promotable = [
            choice
            for choice in unknown
            if choice.diversity_score > 0
            and any(
                (
                    choice.candidate.current_company,
                    choice.candidate.current_title,
                    choice.candidate.city,
                )
            )
        ]
        for choice in sorted(
            promotable,
            key=lambda item: (-item.diversity_score, item.candidate.result_index),
        )[:count]:
            choice.bucket = EXPLORE
            choice.reason = "与主流卡片背景不同，作为多样性样本验证"

    @classmethod
    def _explicit_hard_conflict(
        cls,
        candidate: CandidateSummary,
        predicate: Optional[Callable[[CandidateSummary], bool]],
    ) -> tuple[bool, str]:
        if predicate is not None and predicate(candidate):
            return True, candidate.card_reason or "命中已确认的硬条件"
        page_meta = candidate.page_meta or {}
        meta_value = page_meta.get("hard_conflict")
        if meta_value is True:
            return True, str(page_meta.get("hard_conflict_reason") or candidate.card_reason or "命中已确认的硬条件")
        if isinstance(meta_value, str) and meta_value.strip():
            return True, meta_value.strip()
        if (candidate.card_decision or "").strip().lower() == "hard_conflict":
            return True, candidate.card_reason or "命中已确认的硬条件"
        for risk in candidate.card_risks or []:
            normalized = str(risk).strip()
            lowered = normalized.lower()
            for prefix in _HARD_CONFLICT_PREFIXES:
                if lowered.startswith(prefix.lower()):
                    return True, normalized[len(prefix) :].strip() or normalized
        return False, ""

    @classmethod
    def _is_invalid(cls, candidate: CandidateSummary) -> bool:
        identity = cls._candidate_identity(candidate)
        useful_content = any(
            cls._norm(value)
            for value in (
                candidate.name,
                candidate.current_title,
                candidate.current_company,
                candidate.summary_text,
                candidate.raw_text,
            )
        )
        return not identity or not useful_content

    @classmethod
    def _candidate_identity(cls, candidate: CandidateSummary) -> str:
        aliases = cls._candidate_identity_aliases(candidate)
        if aliases:
            return aliases[0]
        return ""

    @classmethod
    def _candidate_identity_aliases(cls, candidate: CandidateSummary) -> List[str]:
        aliases: List[str] = []
        for value in (candidate.dedupe_key, candidate.profile_url, candidate.id):
            normalized = cls._norm(value)
            if normalized and normalized not in aliases:
                aliases.append(normalized)
        if not aliases:
            composite = "|".join(
                cls._norm(value)
                for value in (
                    candidate.name,
                    candidate.current_company,
                    candidate.current_title,
                )
            ).strip("|")
            if composite:
                aliases.append(composite)
        return aliases

    @classmethod
    def _candidate_key(cls, candidate: CandidateSummary, index: int) -> str:
        return cls._candidate_identity(candidate) or f"invalid-card-{index}"

    @staticmethod
    def _norm(value: object) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @classmethod
    def _stable_audit_key(cls, choice: CandidateChoice) -> str:
        return hashlib.sha256(choice.candidate_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _pick_candidates(
        candidates: List[CandidateSummary], limit: int
    ) -> List[CandidateSummary]:
        """Backward-compatible helper retained for callers outside the runtime."""
        selection = CandidatePicker().select(candidates, limit)
        return selection.selected
