"""Frozen contrast definitions for Phase 4 representation attribution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from kinematicweave.errors import ValidationError
from kinematicweave.experiments.statistical_analysis import holm_adjust

SCHEMA_VERSION = "1.0"

CONFIGURATION_IDS = {
    "raw_samples": "8949a585c070e67f704c6bbb305125fe8199e371c05745de82fd60e3a86f406e",
    "exact_adjacent": "240c14241426be333cad88c4acb7c93780bb95ad5ec4b29742381898e4fadb8d",
    "uniform_linear_stride_2": (
        "c4bb5d454914fa88cb46340f2fe97ed5a6c38836bcddab53eee994922a0201f2"
    ),
    "uniform_hermite_stride_2": (
        "42f8ee7b7cd7f9d34bd5bcbc451ba6acc8d244fb65530c868e75a00718b4edcf"
    ),
    "uniform_linear_stride_5": (
        "96c7ed066a25f4118986404063268a1fb9f1debd316cb2216aee2b8188114ee9"
    ),
    "uniform_hermite_stride_5": (
        "32e2968c6774f29ac782dcaaccc37bf19583e6f421ae45901f07fce360fafe49"
    ),
    "uniform_linear_stride_10": (
        "57b51eb84b3b0aa55eee12379f5fe17028ece04c40a66a4a84763c5042530417"
    ),
    "uniform_hermite_stride_10": (
        "544f452632718a275ff7d880d3b5695709e708804f374aa713b2e511399a461b"
    ),
    "fixed_interval_1000_ms": (
        "a454385455fbcf5dd7f0aadad3da49dfed630269ba1520237d507f4fa16c9fef"
    ),
    "rdp_linear_0_05_m": (
        "c635af54e3a613dcced883953818734baf0be0f414f69f099cbe06458bc4ee88"
    ),
    "position_bounded_linear_0_10_m": (
        "5eb2151f8a4a7639948f1a07ad909c14a4cabbcf99054354a754232cc69dafa6"
    ),
    "unconstrained_hermite_0_10_m": (
        "a1ed5d36e8e737358d9b341b2b2897f4f67aaa96a465b6ac49acbec7fb01a4fb"
    ),
    "position_velocity_hybrid_0_10_m_1_00_mps": (
        "8751a132bf42c226a9e8e2f005c7174bb0cf3d2c0d605051c62348d70fcebb46"
    ),
}


@dataclass(frozen=True, slots=True)
class ContrastPair:
    """One ordered method pair where positive effects favor method B."""

    method_a: str
    method_b: str
    identical_keyframes_required: bool = False

    def __post_init__(self) -> None:
        if self.method_a == self.method_b:
            raise ValidationError("ablation pair methods must differ")
        if (
            self.method_a not in CONFIGURATION_IDS
            or self.method_b not in CONFIGURATION_IDS
        ):
            raise ValidationError("ablation pair uses an unknown frozen configuration")


@dataclass(frozen=True, slots=True)
class AblationFamily:
    """A predeclared multiplicity family and its ordered pairs."""

    name: str
    pairs: tuple[ContrastPair, ...]
    context: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.pairs:
            raise ValidationError("ablation family must have a name and pairs")


def frozen_ablation_families() -> tuple[AblationFamily, ...]:
    """Return the complete predeclared inferential contrast plan."""
    interpolation = AblationFamily(
        "interpolation_primitive",
        tuple(
            ContrastPair(
                f"uniform_linear_stride_{stride}",
                f"uniform_hermite_stride_{stride}",
                identical_keyframes_required=True,
            )
            for stride in (2, 5, 10)
        ),
    )
    adaptive_pairs = (
        ContrastPair(
            "position_bounded_linear_0_10_m",
            "uniform_linear_stride_10",
        ),
        ContrastPair(
            "position_bounded_linear_0_10_m",
            "fixed_interval_1000_ms",
        ),
        ContrastPair(
            "uniform_linear_stride_10",
            "fixed_interval_1000_ms",
        ),
    )
    return (
        interpolation,
        AblationFamily(
            "adaptive_segmentation_primary_byte",
            adaptive_pairs,
            context="primary_byte_0.48",
        ),
        AblationFamily(
            "adaptive_segmentation_primary_keyframe",
            adaptive_pairs,
            context="primary_keyframe_0.12",
        ),
        AblationFamily(
            "temporal_vs_geometric",
            (
                ContrastPair(
                    "position_bounded_linear_0_10_m",
                    "rdp_linear_0_05_m",
                ),
            ),
        ),
        AblationFamily(
            "primitive_vocabulary",
            (
                ContrastPair(
                    "position_bounded_linear_0_10_m",
                    "unconstrained_hermite_0_10_m",
                ),
            ),
        ),
        AblationFamily(
            "velocity_constraint",
            (
                ContrastPair(
                    "unconstrained_hermite_0_10_m",
                    "position_velocity_hybrid_0_10_m_1_00_mps",
                ),
            ),
        ),
        AblationFamily(
            "bounded_method_tradeoff",
            (
                ContrastPair(
                    "position_bounded_linear_0_10_m",
                    "position_velocity_hybrid_0_10_m_1_00_mps",
                ),
            ),
        ),
    )


def validate_frozen_configuration_ids(observed: Mapping[str, str]) -> None:
    """Require every named ablation configuration to retain its frozen identity."""
    if dict(observed) != CONFIGURATION_IDS:
        raise ValidationError("frozen ablation configuration identities differ")


def validate_identical_keyframes(
    method_a: Mapping[str, int],
    method_b: Mapping[str, int],
) -> None:
    """Require the same scenario membership and keyframe count in each pair."""
    if not method_a or set(method_a) != set(method_b):
        raise ValidationError("identical-keyframe contrast membership differs")
    mismatches = [
        scenario_id
        for scenario_id in sorted(method_a)
        if method_a[scenario_id] != method_b[scenario_id]
    ]
    if mismatches:
        raise ValidationError(
            f"identical-keyframe contrast differs in {len(mismatches)} scenarios"
        )


def retained_budget_mismatch(method_a: float, method_b: float) -> dict[str, float]:
    """Return both achieved budgets and their signed and absolute mismatch."""
    if not all(math.isfinite(value) and value >= 0.0 for value in (method_a, method_b)):
        raise ValidationError("achieved budgets must be finite and nonnegative")
    return {
        "method_a": method_a,
        "method_b": method_b,
        "method_b_minus_method_a": method_b - method_a,
        "absolute_mismatch": abs(method_b - method_a),
    }


def apply_ablation_holm(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply Holm correction within family, cohort role, and metric domain."""
    adjusted = [dict(record) for record in records]
    groups = {
        (
            str(record["contrast_family"]),
            str(record["cohort_role"]),
            str(record["metric_domain"]),
        )
        for record in adjusted
    }
    for group in sorted(groups):
        indices = [
            index
            for index, record in enumerate(adjusted)
            if (
                record["contrast_family"],
                record["cohort_role"],
                record["metric_domain"],
            )
            == group
        ]
        values = holm_adjust(
            [float(adjusted[index]["p_value_raw"]) for index in indices]
        )
        family_name = ":".join(group)
        for index, value in zip(indices, values, strict=True):
            adjusted[index]["holm_family"] = family_name
            adjusted[index]["holm_family_size"] = len(indices)
            adjusted[index]["adjusted_p_value"] = value
            adjusted[index]["reject_at_alpha_0_05"] = value <= 0.05
    return adjusted


def attribution_claim_label(
    *,
    adjusted_p_value: float | None,
    invariant: bool = False,
) -> str:
    """Distinguish exact guarantees from inferential and descriptive findings."""
    if invariant:
        return "invariant_guarantee"
    if adjusted_p_value is None:
        return "descriptive_tendency"
    if not math.isfinite(adjusted_p_value) or not 0.0 <= adjusted_p_value <= 1.0:
        raise ValidationError("adjusted p-value must be within [0, 1]")
    return "significant_result" if adjusted_p_value <= 0.05 else "descriptive_tendency"


__all__ = [
    "CONFIGURATION_IDS",
    "SCHEMA_VERSION",
    "AblationFamily",
    "ContrastPair",
    "apply_ablation_holm",
    "attribution_claim_label",
    "frozen_ablation_families",
    "retained_budget_mismatch",
    "validate_frozen_configuration_ids",
    "validate_identical_keyframes",
]
