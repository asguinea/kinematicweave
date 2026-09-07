"""Deterministic selection primitives for Phase 4 qualitative evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from typing import Any

from kinematicweave.errors import ValidationError

SCHEMA_VERSION = "1.0"
SAFE_ID_NAMESPACE = "kinematicweave-phase4.9"

type Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class SelectionRule:
    """A fixed rank rule over one finite candidate collection."""

    category: str
    metric: str
    direction: str
    quantile: str = "largest"

    def __post_init__(self) -> None:
        if not self.category or not self.metric:
            raise ValidationError("selection category and metric are required")
        if self.direction not in {"ascending", "descending"}:
            raise ValidationError("selection direction must be ascending or descending")
        if self.quantile not in {"largest", "median", "smallest"}:
            raise ValidationError("unsupported selection quantile")


def safe_identifier(kind: str, source_identity: str) -> str:
    """Return a stable release-safe identifier without exposing its source."""
    if not kind or not source_identity:
        raise ValidationError("safe identifier inputs must be nonempty")
    digest = hashlib.sha256(
        f"{SAFE_ID_NAMESPACE}|{kind}|{source_identity}".encode()
    ).hexdigest()
    return f"{kind}-{digest[:16]}"


def stable_rank(
    candidates: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    direction: str,
    identity_field: str = "safe_record_id",
) -> list[Json]:
    """Rank finite numeric records with a stable safe-identifier tie break."""
    if direction not in {"ascending", "descending"}:
        raise ValidationError("rank direction must be ascending or descending")
    ranked: list[Json] = []
    for candidate in candidates:
        if metric not in candidate or identity_field not in candidate:
            raise ValidationError("rank candidate is missing a required field")
        value = candidate[metric]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError("rank metric must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise ValidationError("rank metric must be finite")
        record = dict(candidate)
        record[metric] = number
        ranked.append(record)
    if not ranked:
        raise ValidationError("rank candidate collection must not be empty")
    sign = -1.0 if direction == "descending" else 1.0
    ranked.sort(
        key=lambda row: (
            sign * float(row[metric]),
            str(row[identity_field]),
        )
    )
    for index, record in enumerate(ranked, start=1):
        record["rank"] = index
        record["candidate_count"] = len(ranked)
    return ranked


def select_by_rule(
    candidates: Sequence[Mapping[str, Any]],
    rule: SelectionRule,
) -> Json:
    """Select a largest, median, or smallest ranked record."""
    ranked = stable_rank(
        candidates,
        metric=rule.metric,
        direction=rule.direction,
    )
    index = {
        "largest": 0,
        "median": (len(ranked) - 1) // 2,
        "smallest": len(ranked) - 1,
    }[rule.quantile]
    selected = dict(ranked[index])
    selected.update(
        {
            "category": rule.category,
            "selection_metric": rule.metric,
            "selection_direction": rule.direction,
            "selection_quantile": rule.quantile,
            "tie_break_rule": "safe_record_id ascending",
        }
    )
    return selected


def selection_contract() -> Json:
    """Return the complete pre-rendering rule contract for Batch 4.9."""
    rules = (
        SelectionRule(
            "adaptive_largest_position_improvement",
            "uniform_minus_adaptive_position_mean_m",
            "descending",
        ),
        SelectionRule(
            "adaptive_median_position_improvement",
            "uniform_minus_adaptive_position_mean_m",
            "descending",
            "median",
        ),
        SelectionRule(
            "adaptive_smallest_or_adverse_improvement",
            "uniform_minus_adaptive_position_mean_m",
            "descending",
            "smallest",
        ),
        SelectionRule(
            "rdp_largest_temporal_failure",
            "rdp_minus_bounded_velocity_maximum_mps",
            "descending",
        ),
        SelectionRule(
            "rdp_median_temporal_case",
            "rdp_minus_bounded_velocity_maximum_mps",
            "descending",
            "median",
        ),
        SelectionRule(
            "hermite_largest_velocity_inflation",
            "hermite_minus_bounded_velocity_maximum_mps",
            "descending",
        ),
        SelectionRule(
            "hermite_largest_semantic_loss",
            "bounded_minus_hermite_semantic_f1",
            "descending",
        ),
        SelectionRule(
            "hybrid_largest_velocity_recovery",
            "hermite_minus_hybrid_velocity_maximum_mps",
            "descending",
        ),
        SelectionRule(
            "hybrid_largest_acceleration_or_braking_recovery",
            "hybrid_minus_hermite_event_f1",
            "descending",
        ),
        SelectionRule(
            "hybrid_largest_semantic_tradeoff",
            "hermite_minus_hybrid_semantic_f1",
            "descending",
        ),
        SelectionRule(
            "hybrid_little_advantage",
            "hybrid_total_advantage",
            "ascending",
        ),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": "4.9",
        "cohort_role": "test",
        "scenario_count": 300,
        "safe_identifier": {
            "algorithm": "SHA-256",
            "namespace": SAFE_ID_NAMESPACE,
            "published_hex_characters": 16,
        },
        "ranking": {
            "numeric_order": "declared per rule",
            "tie_break_rule": "safe_record_id ascending",
            "median_index": "floor((candidate_count - 1) / 2) after ranking",
            "manual_identifier_allowlist": False,
        },
        "rules": [
            {
                "category": rule.category,
                "metric": rule.metric,
                "direction": rule.direction,
                "quantile": rule.quantile,
            }
            for rule in rules
        ],
        "semantic_rules": {
            event: {
                "predicate": f"source {event} count > 0",
                "metric": f"semantic_{event}_f1",
                "direction": "ascending",
                "quantile": "largest",
            }
            for event in ("stop", "turn", "acceleration", "braking")
        },
        "overlap_rule": {
            "predicate": "at least two source semantic event types present",
            "metric": "semantic_overall_f1",
            "direction": "ascending",
        },
        "agent_rules": {
            agent_class: {
                "predicate": f"frozen procedural track class is {agent_class}",
                "metric": "position maximum error",
                "direction": "descending",
            }
            for agent_class in ("vehicle", "pedestrian", "cyclist")
        },
        "robustness_rule": {
            "source": "accepted Batch 4.8 test disagreement records",
            "ordering": (
                "stratum kind, contrast family, metric, stratum, then "
                "largest absolute recorded opposite-direction effect"
            ),
            "minimum_count": 2,
        },
    }


__all__ = [
    "SAFE_ID_NAMESPACE",
    "SCHEMA_VERSION",
    "SelectionRule",
    "safe_identifier",
    "select_by_rule",
    "selection_contract",
    "stable_rank",
]
