"""Known-answer tests for deterministic Phase 4 statistical procedures."""

from __future__ import annotations

from collections.abc import Callable
import math

import numpy as np
import pytest

from kinematicweave.errors import ValidationError
from kinematicweave.experiments.statistical_analysis import (
    EventCounts,
    align_paired_scenarios,
    all_method_pairs,
    combine_event_counts,
    holm_adjust,
    oriented_differences,
    paired_analysis,
    paired_permutation_p_value,
    percentile_bootstrap_interval,
    practical_magnitude,
    rank_biserial_correlation,
    standardized_paired_effect,
    win_tie_loss_counts,
)


def test_scenario_alignment_prevents_nested_observation_inflation() -> None:
    alignment = align_paired_scenarios(
        {"scenario-b": 2.0, "scenario-a": 1.0, "scenario-c": None},
        {"scenario-a": 0.5, "scenario-b": 1.0, "scenario-d": 8.0},
    )
    assert alignment.scenario_ids == ("scenario-a", "scenario-b")
    assert alignment.planned_count == 4
    assert alignment.missing_method_a_count == 2
    assert alignment.missing_method_b_count == 1
    assert alignment.missing_either_count == 2
    assert alignment.method_a.tolist() == [1.0, 2.0]
    assert alignment.method_b.tolist() == [0.5, 1.0]


def test_paired_difference_orientation_and_counts() -> None:
    lower = oriented_differences([3.0, 2.0, 1.0], [2.0, 2.0, 4.0], lower_is_better=True)
    higher = oriented_differences(
        [0.5, 0.8, 0.7],
        [0.7, 0.8, 0.4],
        lower_is_better=False,
    )
    assert lower.tolist() == [1.0, 0.0, -3.0]
    assert higher.tolist() == pytest.approx([0.2, 0.0, -0.3])
    assert win_tie_loss_counts(lower) == {
        "method_b_wins": 1,
        "ties": 1,
        "method_b_losses": 1,
    }


def test_bootstrap_interval_is_deterministic_and_ordered() -> None:
    values = np.asarray([1.0, 2.0, 3.0, 4.0])
    first = percentile_bootstrap_interval(values, resamples=2_000, seed=123)
    second = percentile_bootstrap_interval(values, resamples=2_000, seed=123)
    assert first == second
    assert first[0] <= values.mean() <= first[1]


def test_exact_and_monte_carlo_permutation_are_deterministic() -> None:
    exact = paired_permutation_p_value([1.0, 2.0], resamples=100, seed=1)
    assert exact == (0.5, "exact", 4)
    values = np.linspace(-1.0, 2.0, 20)
    first = paired_permutation_p_value(values, resamples=2_000, seed=99)
    second = paired_permutation_p_value(values, resamples=2_000, seed=99)
    assert first == second
    assert first[1:] == ("monte_carlo", 2_000)
    assert 0.0 < first[0] <= 1.0


def test_effect_sizes_and_practical_magnitude_known_answers() -> None:
    assert standardized_paired_effect([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert standardized_paired_effect([1.0, 1.0]) is None
    assert rank_biserial_correlation([1.0, 2.0, -3.0]) == pytest.approx(0.0)
    assert rank_biserial_correlation([0.0, 0.0]) is None
    assert practical_magnitude(0.1) == "negligible"
    assert practical_magnitude(0.3) == "small"
    assert practical_magnitude(0.6) == "moderate"
    assert practical_magnitude(0.9) == "large"
    assert practical_magnitude(None) == "undefined_zero_variance"


def test_holm_correction_is_monotone_in_sorted_order() -> None:
    adjusted = holm_adjust([0.01, 0.04, 0.03, 0.002])
    assert adjusted == pytest.approx((0.03, 0.06, 0.06, 0.008))
    assert all(0.0 <= value <= 1.0 for value in adjusted)


def test_event_metrics_preserve_undefined_values() -> None:
    absent = EventCounts(source=0, replay=0, matched=0)
    false_positive = EventCounts(source=0, replay=2, matched=0)
    present = EventCounts(source=4, replay=5, matched=3)
    assert absent.precision is None
    assert absent.recall is None
    assert absent.f1 is None
    assert absent.presence_aware_score == 1.0
    assert false_positive.precision == 0.0
    assert false_positive.recall is None
    assert false_positive.f1 is None
    assert false_positive.presence_aware_score == 0.0
    assert present.precision == 0.6
    assert present.recall == 0.75
    assert present.f1 == pytest.approx(2.0 / 3.0)
    combined = combine_event_counts([absent, present])
    assert combined == present


def test_complete_pair_family_and_paired_analysis() -> None:
    assert all_method_pairs(("a", "b", "c")) == (("a", "b"), ("a", "c"), ("b", "c"))
    first = paired_analysis(
        {"s1": 3.0, "s2": 2.0, "s3": 4.0},
        {"s1": 2.0, "s2": 1.0, "s3": 3.0},
        lower_is_better=True,
        resamples=200,
        seed_components=("test", "metric", "a", "b"),
    )
    second = paired_analysis(
        {"s1": 3.0, "s2": 2.0, "s3": 4.0},
        {"s1": 2.0, "s2": 1.0, "s3": 3.0},
        lower_is_better=True,
        resamples=200,
        seed_components=("test", "metric", "a", "b"),
    )
    assert first == second
    assert first["valid_pair_count"] == 3
    assert first["method_b_advantage_mean"] == 1.0
    assert first["method_b_wins"] == 3


@pytest.mark.parametrize(
    "call",
    [
        lambda: align_paired_scenarios({}, {}),
        lambda: oriented_differences([1.0], [1.0, 2.0], lower_is_better=True),
        lambda: percentile_bootstrap_interval([math.nan], resamples=10, seed=1),
        lambda: holm_adjust([1.1]),
        lambda: all_method_pairs(("same", "same")),
        lambda: EventCounts(source=1, replay=0, matched=1),
    ],
)
def test_invalid_statistical_inputs_fail_closed(call: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        call()
