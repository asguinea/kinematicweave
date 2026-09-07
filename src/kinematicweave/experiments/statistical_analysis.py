"""Deterministic paired statistical procedures for Phase 4 analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import itertools
import math

import numpy as np
from numpy.typing import NDArray

from kinematicweave.errors import ValidationError
from kinematicweave.seeding import derive_seed

SCHEMA_VERSION = "1.0"
ROOT_SEED = 4707
PILOT_RESAMPLES = 2_000
TEST_RESAMPLES = 10_000
CONFIDENCE_LEVEL = 0.95
PERMUTATION_EXACT_MAX_PAIRS = 16

type OptionalMetric = float | None


def _finite_float(value: object, field_name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValidationError(f"{field_name} must be finite numeric")
    return float(value)


def _values(values: Sequence[float] | NDArray[np.float64]) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0:
        raise ValidationError("paired values must be a nonempty one-dimensional array")
    if not np.isfinite(array).all():
        raise ValidationError("paired values must be finite")
    return array


@dataclass(frozen=True, slots=True)
class PairedAlignment:
    """Scenario-aligned values and explicit missing-value accounting."""

    scenario_ids: tuple[str, ...]
    method_a: NDArray[np.float64]
    method_b: NDArray[np.float64]
    planned_count: int
    missing_method_a_count: int
    missing_method_b_count: int
    missing_either_count: int


def align_paired_scenarios(
    method_a: Mapping[str, OptionalMetric],
    method_b: Mapping[str, OptionalMetric],
) -> PairedAlignment:
    """Align two methods by canonical scenario identifier."""
    planned_ids = tuple(sorted(set(method_a) | set(method_b)))
    if not planned_ids:
        raise ValidationError("paired analysis requires planned scenarios")
    ids: list[str] = []
    values_a: list[float] = []
    values_b: list[float] = []
    missing_a = 0
    missing_b = 0
    missing_either = 0
    for scenario_id in planned_ids:
        value_a = method_a.get(scenario_id)
        value_b = method_b.get(scenario_id)
        if value_a is None:
            missing_a += 1
        if value_b is None:
            missing_b += 1
        if value_a is None or value_b is None:
            missing_either += 1
            continue
        ids.append(scenario_id)
        values_a.append(_finite_float(value_a, "method_a"))
        values_b.append(_finite_float(value_b, "method_b"))
    if not ids:
        raise ValidationError("paired analysis has no valid scenario pairs")
    return PairedAlignment(
        tuple(ids),
        np.asarray(values_a, dtype=np.float64),
        np.asarray(values_b, dtype=np.float64),
        len(planned_ids),
        missing_a,
        missing_b,
        missing_either,
    )


def oriented_differences(
    method_a: Sequence[float] | NDArray[np.float64],
    method_b: Sequence[float] | NDArray[np.float64],
    *,
    lower_is_better: bool,
) -> NDArray[np.float64]:
    """Return paired differences where positive values favor method B."""
    values_a = _values(method_a)
    values_b = _values(method_b)
    if values_a.shape != values_b.shape:
        raise ValidationError("paired value lengths differ")
    return values_a - values_b if lower_is_better else values_b - values_a


def percentile_bootstrap_interval(
    differences: Sequence[float] | NDArray[np.float64],
    *,
    resamples: int,
    seed: int,
    confidence_level: float = CONFIDENCE_LEVEL,
) -> tuple[float, float]:
    """Return a deterministic percentile interval for a paired mean."""
    values = _values(differences)
    if not isinstance(resamples, int) or isinstance(resamples, bool) or resamples <= 0:
        raise ValidationError("resamples must be a positive integer")
    if not 0.0 < confidence_level < 1.0:
        raise ValidationError("confidence_level must be between zero and one")
    generator = np.random.Generator(np.random.PCG64(seed))
    means = np.empty(resamples, dtype=np.float64)
    chunk_size = min(512, resamples)
    for start in range(0, resamples, chunk_size):
        stop = min(start + chunk_size, resamples)
        indices = generator.integers(
            0,
            values.size,
            size=(stop - start, values.size),
            endpoint=False,
        )
        means[start:stop] = values[indices].mean(axis=1)
    tail = (1.0 - confidence_level) / 2.0
    lower, upper = np.quantile(means, (tail, 1.0 - tail), method="linear")
    return float(lower), float(upper)


def paired_permutation_p_value(
    differences: Sequence[float] | NDArray[np.float64],
    *,
    resamples: int,
    seed: int,
) -> tuple[float, str, int]:
    """Return a deterministic two-sided paired sign-flip p-value."""
    values = _values(differences)
    observed = abs(float(values.mean()))
    if observed == 0.0 and np.count_nonzero(values) == 0:
        return 1.0, "exact", 1
    tolerance = np.finfo(np.float64).eps * max(1.0, observed) * 8.0
    if values.size <= PERMUTATION_EXACT_MAX_PAIRS:
        extreme = 0
        count = 1 << values.size
        for mask in range(count):
            signed_sum = 0.0
            for index, value in enumerate(values):
                signed_sum += value if mask & (1 << index) else -value
            if abs(signed_sum / values.size) + tolerance >= observed:
                extreme += 1
        return extreme / count, "exact", count
    if not isinstance(resamples, int) or isinstance(resamples, bool) or resamples <= 0:
        raise ValidationError("resamples must be a positive integer")
    generator = np.random.Generator(np.random.PCG64(seed))
    extreme = 0
    chunk_size = min(512, resamples)
    for start in range(0, resamples, chunk_size):
        count = min(chunk_size, resamples - start)
        sign_bits = generator.integers(0, 2, size=(count, values.size), endpoint=False)
        signs = sign_bits.astype(np.float64) * 2.0 - 1.0
        permuted = np.abs((signs @ values) / values.size)
        extreme += int(np.count_nonzero(permuted + tolerance >= observed))
    return (extreme + 1) / (resamples + 1), "monte_carlo", resamples


def standardized_paired_effect(
    differences: Sequence[float] | NDArray[np.float64],
) -> float | None:
    """Return paired Cohen's dz, or None when paired variance is zero."""
    values = _values(differences)
    if values.size < 2:
        return None
    standard_deviation = float(values.std(ddof=1))
    if standard_deviation == 0.0:
        return None
    return float(values.mean()) / standard_deviation


def rank_biserial_correlation(
    differences: Sequence[float] | NDArray[np.float64],
) -> float | None:
    """Return signed-rank biserial correlation with average tie ranks."""
    values = _values(differences)
    nonzero = values[values != 0.0]
    if nonzero.size == 0:
        return None
    absolute = np.abs(nonzero)
    order = np.argsort(absolute, kind="stable")
    ranks = np.empty(nonzero.size, dtype=np.float64)
    sorted_values = absolute[order]
    start = 0
    while start < sorted_values.size:
        stop = start + 1
        while stop < sorted_values.size and sorted_values[stop] == sorted_values[start]:
            stop += 1
        average_rank = ((start + 1) + stop) / 2.0
        ranks[order[start:stop]] = average_rank
        start = stop
    positive = float(ranks[nonzero > 0.0].sum())
    negative = float(ranks[nonzero < 0.0].sum())
    return (positive - negative) / (positive + negative)


def win_tie_loss_counts(
    differences: Sequence[float] | NDArray[np.float64],
) -> dict[str, int]:
    """Count scenario wins, ties, and losses for method B."""
    values = _values(differences)
    return {
        "method_b_wins": int(np.count_nonzero(values > 0.0)),
        "ties": int(np.count_nonzero(values == 0.0)),
        "method_b_losses": int(np.count_nonzero(values < 0.0)),
    }


def practical_magnitude(standardized_effect: float | None) -> str:
    """Classify absolute paired standardized effect using frozen thresholds."""
    if standardized_effect is None:
        return "undefined_zero_variance"
    magnitude = abs(standardized_effect)
    if magnitude < 0.2:
        return "negligible"
    if magnitude < 0.5:
        return "small"
    if magnitude < 0.8:
        return "moderate"
    return "large"


def holm_adjust(p_values: Sequence[float]) -> tuple[float, ...]:
    """Return Holm family-wise-error adjusted p-values."""
    if not p_values:
        raise ValidationError("Holm correction requires p-values")
    values = np.asarray(p_values, dtype=np.float64)
    if (
        values.ndim != 1
        or not np.isfinite(values).all()
        or np.any(values < 0.0)
        or np.any(values > 1.0)
    ):
        raise ValidationError("p-values must be finite values in [0, 1]")
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    adjusted = np.empty(len(values), dtype=np.float64)
    running = 0.0
    count = len(values)
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (count - rank) * float(values[index])))
        adjusted[index] = running
    return tuple(float(value) for value in adjusted)


@dataclass(frozen=True, slots=True)
class EventCounts:
    """Source, replay, and matched event counts with strict undefined behavior."""

    source: int
    replay: int
    matched: int

    def __post_init__(self) -> None:
        for field_name in ("source", "replay", "matched"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError(f"{field_name} must be nonnegative integer")
        if self.matched > self.source or self.matched > self.replay:
            raise ValidationError(
                "matched events cannot exceed source or replay counts"
            )

    @property
    def precision(self) -> float | None:
        return self.matched / self.replay if self.replay else None

    @property
    def recall(self) -> float | None:
        return self.matched / self.source if self.source else None

    @property
    def f1(self) -> float | None:
        precision = self.precision
        recall = self.recall
        if precision is None or recall is None or precision + recall == 0.0:
            return None
        return 2.0 * precision * recall / (precision + recall)

    @property
    def presence_aware_score(self) -> float:
        if self.source == 0:
            return 1.0 if self.replay == 0 else 0.0
        return 0.0 if self.f1 is None else self.f1

    def to_dict(self) -> dict[str, object]:
        return {
            "source_event_count": self.source,
            "replay_event_count": self.replay,
            "matched_event_count": self.matched,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "presence_aware_score": self.presence_aware_score,
        }


def combine_event_counts(values: Sequence[EventCounts]) -> EventCounts:
    """Combine event types or nested records by summing count numerators."""
    if not values:
        raise ValidationError("event aggregation requires at least one record")
    return EventCounts(
        sum(value.source for value in values),
        sum(value.replay for value in values),
        sum(value.matched for value in values),
    )


def paired_analysis(
    method_a: Mapping[str, OptionalMetric],
    method_b: Mapping[str, OptionalMetric],
    *,
    lower_is_better: bool,
    resamples: int,
    seed_components: Sequence[str | int],
) -> dict[str, object]:
    """Run the frozen scenario-level paired analysis."""
    alignment = align_paired_scenarios(method_a, method_b)
    differences = oriented_differences(
        alignment.method_a,
        alignment.method_b,
        lower_is_better=lower_is_better,
    )
    seed = derive_seed(ROOT_SEED, *seed_components)
    interval = percentile_bootstrap_interval(
        differences,
        resamples=resamples,
        seed=derive_seed(seed, "bootstrap"),
    )
    p_value, permutation_mode, permutation_count = paired_permutation_p_value(
        differences,
        resamples=resamples,
        seed=derive_seed(seed, "permutation"),
    )
    standardized = standardized_paired_effect(differences)
    return {
        "planned_scenario_count": alignment.planned_count,
        "valid_pair_count": len(alignment.scenario_ids),
        "missing_method_a_count": alignment.missing_method_a_count,
        "missing_method_b_count": alignment.missing_method_b_count,
        "missing_either_count": alignment.missing_either_count,
        "method_a_mean": float(alignment.method_a.mean()),
        "method_b_mean": float(alignment.method_b.mean()),
        "method_b_advantage_mean": float(differences.mean()),
        "method_b_advantage_median": float(np.median(differences)),
        "confidence_level": CONFIDENCE_LEVEL,
        "confidence_interval_method": "paired_percentile_bootstrap",
        "confidence_interval_lower": interval[0],
        "confidence_interval_upper": interval[1],
        "bootstrap_resamples": resamples,
        "permutation_test": "two_sided_paired_sign_flip",
        "permutation_mode": permutation_mode,
        "permutation_count": permutation_count,
        "p_value_raw": p_value,
        "standardized_paired_effect_dz": standardized,
        "rank_biserial_correlation": rank_biserial_correlation(differences),
        "practical_magnitude": practical_magnitude(standardized),
        **win_tie_loss_counts(differences),
    }


def all_method_pairs(method_ids: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Return every canonical pair from a unique ordered method sequence."""
    if not method_ids or len(set(method_ids)) != len(method_ids):
        raise ValidationError("method identifiers must be nonempty and unique")
    return tuple(itertools.combinations(method_ids, 2))


__all__ = [
    "CONFIDENCE_LEVEL",
    "PILOT_RESAMPLES",
    "ROOT_SEED",
    "SCHEMA_VERSION",
    "TEST_RESAMPLES",
    "EventCounts",
    "align_paired_scenarios",
    "all_method_pairs",
    "combine_event_counts",
    "holm_adjust",
    "oriented_differences",
    "paired_analysis",
    "paired_permutation_p_value",
    "percentile_bootstrap_interval",
    "practical_magnitude",
    "rank_biserial_correlation",
    "standardized_paired_effect",
    "win_tie_loss_counts",
]
