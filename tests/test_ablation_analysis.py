"""Known-answer tests for frozen Phase 4 ablation construction."""

from __future__ import annotations

import importlib

import pytest

from kinematicweave.errors import ValidationError
from kinematicweave.experiments.ablation_analysis import (
    CONFIGURATION_IDS,
    AblationFamily,
    ContrastPair,
    apply_ablation_holm,
    attribution_claim_label,
    frozen_ablation_families,
    retained_budget_mismatch,
    validate_frozen_configuration_ids,
    validate_identical_keyframes,
)


def test_exact_frozen_configuration_identities_and_family_plan() -> None:
    assert len(CONFIGURATION_IDS) == 13
    assert CONFIGURATION_IDS["position_bounded_linear_0_10_m"] == (
        "5eb2151f8a4a7639948f1a07ad909c14a4cabbcf99054354a754232cc69dafa6"
    )
    assert CONFIGURATION_IDS["position_velocity_hybrid_0_10_m_1_00_mps"] == (
        "8751a132bf42c226a9e8e2f005c7174bb0cf3d2c0d605051c62348d70fcebb46"
    )
    families = frozen_ablation_families()
    assert len(families) == 7
    assert [family.name for family in families] == [
        "interpolation_primitive",
        "adaptive_segmentation_primary_byte",
        "adaptive_segmentation_primary_keyframe",
        "temporal_vs_geometric",
        "primitive_vocabulary",
        "velocity_constraint",
        "bounded_method_tradeoff",
    ]
    interpolation = families[0]
    assert len(interpolation.pairs) == 3
    assert all(pair.identical_keyframes_required for pair in interpolation.pairs)
    assert sum(len(family.pairs) for family in families) == 13


def test_frozen_configuration_validation_is_exact() -> None:
    validate_frozen_configuration_ids(CONFIGURATION_IDS)
    changed = dict(CONFIGURATION_IDS)
    changed["uniform_linear_stride_2"] = "0" * 64
    with pytest.raises(ValidationError, match="identities differ"):
        validate_frozen_configuration_ids(changed)
    missing = dict(CONFIGURATION_IDS)
    missing.pop("exact_adjacent")
    with pytest.raises(ValidationError, match="identities differ"):
        validate_frozen_configuration_ids(missing)


def test_identical_keyframe_contrast_validation() -> None:
    validate_identical_keyframes({"s1": 4, "s2": 8}, {"s2": 8, "s1": 4})
    with pytest.raises(ValidationError, match="membership differs"):
        validate_identical_keyframes({"s1": 4}, {"s2": 4})
    with pytest.raises(ValidationError, match="differs in 1 scenarios"):
        validate_identical_keyframes({"s1": 4}, {"s1": 5})


def test_budget_mismatch_is_retained_without_rematching() -> None:
    assert retained_budget_mismatch(0.497, 0.478) == pytest.approx(
        {
            "method_a": 0.497,
            "method_b": 0.478,
            "method_b_minus_method_a": -0.019,
            "absolute_mismatch": 0.019,
        }
    )
    with pytest.raises(ValidationError):
        retained_budget_mismatch(-0.1, 0.2)


def test_holm_correction_stays_within_ablation_role_and_domain() -> None:
    records = [
        {
            "contrast_family": "family-a",
            "cohort_role": "test",
            "metric_domain": "position",
            "p_value_raw": value,
        }
        for value in (0.01, 0.04, 0.03)
    ]
    records.append(
        {
            "contrast_family": "family-a",
            "cohort_role": "pilot",
            "metric_domain": "position",
            "p_value_raw": 0.04,
        }
    )
    adjusted = apply_ablation_holm(records)
    assert [row["adjusted_p_value"] for row in adjusted[:3]] == pytest.approx(
        [0.03, 0.06, 0.06]
    )
    assert adjusted[3]["adjusted_p_value"] == pytest.approx(0.04)
    assert adjusted[3]["holm_family_size"] == 1
    assert records[0].get("adjusted_p_value") is None


def test_invariant_and_statistical_claim_labels_are_distinct() -> None:
    assert (
        attribution_claim_label(adjusted_p_value=None, invariant=True)
        == "invariant_guarantee"
    )
    assert attribution_claim_label(adjusted_p_value=0.01) == "significant_result"
    assert attribution_claim_label(adjusted_p_value=0.2) == "descriptive_tendency"
    with pytest.raises(ValidationError):
        attribution_claim_label(adjusted_p_value=1.1)


def test_invalid_contrast_and_family_fail_closed() -> None:
    with pytest.raises(ValidationError):
        ContrastPair("raw_samples", "raw_samples")
    with pytest.raises(ValidationError):
        ContrastPair("unknown", "raw_samples")
    with pytest.raises(ValidationError):
        AblationFamily("", (ContrastPair("raw_samples", "exact_adjacent"),))
    with pytest.raises(ValidationError):
        AblationFamily("empty", ())


def test_campaign_module_import_is_safe() -> None:
    module = importlib.import_module(
        "kinematicweave.experiments.representation_ablation_campaign"
    )
    assert callable(module.run_representation_ablation_campaign)
