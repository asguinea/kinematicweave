"""Deterministic matched-budget motion sweep contracts and checkpointing."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, cast

from kinematicweave.baselines.motion import (
    BASELINE_IMPLEMENTATION_VERSION,
    BaselineMethod,
    MotionBaselineConfig,
    baseline_config_identity,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.codecs.exact import (
    ENCODER_NAME as EXACT_NAME,
)
from kinematicweave.codecs.exact import (
    ENCODER_PARAMETERS_IDENTITY,
)
from kinematicweave.codecs.exact import (
    ENCODER_VERSION as EXACT_VERSION,
)
from kinematicweave.codecs.hermite import (
    ENCODER_NAME as HERMITE_NAME,
)
from kinematicweave.codecs.hermite import (
    ENCODER_VERSION as HERMITE_VERSION,
)
from kinematicweave.codecs.hermite import (
    HermiteCodecConfig,
    hermite_encoder_parameters_identity,
)
from kinematicweave.codecs.piecewise_linear import (
    ENCODER_NAME as LINEAR_NAME,
)
from kinematicweave.codecs.piecewise_linear import (
    ENCODER_VERSION as LINEAR_VERSION,
)
from kinematicweave.codecs.piecewise_linear import (
    PiecewiseLinearCodecConfig,
    encoder_parameters_identity,
)
from kinematicweave.codecs.velocity_bounded import (
    ENCODER_NAME as HYBRID_NAME,
)
from kinematicweave.codecs.velocity_bounded import (
    ENCODER_VERSION as HYBRID_VERSION,
)
from kinematicweave.codecs.velocity_bounded import (
    VelocityBoundedCodecConfig,
    velocity_bounded_encoder_parameters_identity,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.experiments.motion_cohort import CohortRole, MotionCohortUnit

__all__ = [
    "BYTE_RATIO_TARGETS",
    "KEYFRAME_RATIO_TARGETS",
    "BudgetDimension",
    "BudgetRelation",
    "CheckpointStatus",
    "ConfigurationBudgetRecord",
    "MatchedBudgetSelection",
    "ScenarioConfigurationExecution",
    "SweepCheckpointState",
    "SweepConfiguration",
    "SweepFailureRecord",
    "SweepManifest",
    "SweepParameterPoint",
    "SweepResultSummary",
    "checkpoint_path",
    "execution_order",
    "frozen_parameter_grid",
    "load_checkpoint",
    "read_ranked_development_prefix",
    "select_matched_budgets",
    "write_checkpoint",
]

SCHEMA_VERSION = "1.0"
SWEEP_IMPLEMENTATION_VERSION = "1.0"
BYTE_RATIO_TARGETS = (0.25, 0.35, 0.50, 0.75, 1.00)
KEYFRAME_RATIO_TARGETS = (0.05, 0.10, 0.20, 0.40, 0.80)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNIT_FIELDS = frozenset(
    {
        "provider_partition",
        "cohort_role",
        "selection_rank",
        "source_scenario_id",
        "motion_object_path",
        "motion_size_bytes",
        "motion_sha256",
        "map_object_path",
        "map_size_bytes",
        "map_sha256",
        "materialization_cache_key",
        "validation_included",
        "exclusion_reason",
    }
)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty text")
    return value


def _sha256(value: object, field_name: str) -> str:
    normalized = _text(value, field_name)
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValidationError(f"{field_name} must be lowercase SHA-256")
    return normalized


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{field_name} must be a nonnegative integer")
    return value


def _positive_int(value: object, field_name: str) -> int:
    normalized = _nonnegative_int(value, field_name)
    if normalized == 0:
        raise ValidationError(f"{field_name} must be positive")
    return normalized


def _finite_nonnegative(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValidationError(f"{field_name} must be finite and nonnegative")
    return normalized


def _canonical_mapping(text: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(text, str):
        raise ValidationError(f"{field_name} must be canonical JSON text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise ValidationError(f"{field_name} is malformed") from None
    if not isinstance(value, Mapping) or canonical_json_text(value) != text:
        raise ValidationError(f"{field_name} must be a canonical JSON mapping")
    return cast(Mapping[str, object], value)


def _canonical_json(value: Mapping[str, object]) -> str:
    return canonical_json_text(value)


def _float_key(value: float) -> str:
    return format(value, ".12g").replace(".", "p")


class BudgetDimension(StrEnum):
    """Supported independent budget dimensions."""

    BYTE_RATIO = "byte_ratio"
    KEYFRAME_RATIO = "keyframe_ratio"


class BudgetRelation(StrEnum):
    """Relation between an achieved budget and its target."""

    BELOW = "below"
    EQUAL = "equal"
    ABOVE = "above"


class CheckpointStatus(StrEnum):
    """Durable scenario/configuration checkpoint states."""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SweepParameterPoint:
    """One immutable point in the frozen exploratory grid."""

    method_id: str
    family: str
    configuration_json: str
    encoder_configuration_identity: str
    implementation_name: str
    implementation_version: str
    parameter_identity: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("parameter schema_version differs")
        for field_name in (
            "method_id",
            "family",
            "implementation_name",
            "implementation_version",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        _canonical_mapping(self.configuration_json, "configuration_json")
        object.__setattr__(
            self,
            "encoder_configuration_identity",
            _sha256(
                self.encoder_configuration_identity,
                "encoder_configuration_identity",
            ),
        )
        expected = canonical_sha256(
            "phase4-motion-sweep-parameter-v1",
            {
                "method_id": self.method_id,
                "family": self.family,
                "configuration": json.loads(self.configuration_json),
                "encoder_configuration_identity": (self.encoder_configuration_identity),
                "implementation_name": self.implementation_name,
                "implementation_version": self.implementation_version,
            },
        )
        identity = _sha256(self.parameter_identity, "parameter_identity")
        if identity != expected:
            raise ValidationError("parameter_identity differs from parameter content")
        object.__setattr__(self, "parameter_identity", identity)

    @property
    def configuration(self) -> Mapping[str, object]:
        """Return the parsed immutable configuration value."""
        return _canonical_mapping(self.configuration_json, "configuration_json")

    def to_dict(self) -> dict[str, object]:
        """Return the canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "method_id": self.method_id,
            "family": self.family,
            "configuration": dict(self.configuration),
            "encoder_configuration_identity": self.encoder_configuration_identity,
            "implementation_name": self.implementation_name,
            "implementation_version": self.implementation_version,
            "parameter_identity": self.parameter_identity,
        }

    @classmethod
    def create(
        cls,
        method_id: str,
        family: str,
        configuration: Mapping[str, object],
        encoder_configuration_identity: str,
        implementation_name: str,
        implementation_version: str,
    ) -> SweepParameterPoint:
        """Create one point and derive its canonical identity."""
        configuration_json = _canonical_json(configuration)
        identity = canonical_sha256(
            "phase4-motion-sweep-parameter-v1",
            {
                "method_id": method_id,
                "family": family,
                "configuration": json.loads(configuration_json),
                "encoder_configuration_identity": encoder_configuration_identity,
                "implementation_name": implementation_name,
                "implementation_version": implementation_version,
            },
        )
        return cls(
            method_id,
            family,
            configuration_json,
            encoder_configuration_identity,
            implementation_name,
            implementation_version,
            identity,
        )


def _baseline_point(config: MotionBaselineConfig) -> SweepParameterPoint:
    family = cast(BaselineMethod, config.method).value
    return SweepParameterPoint.create(
        config.key,
        family,
        {
            "method": family,
            "stride": config.stride,
            "maximum_perpendicular_error_m": (config.maximum_perpendicular_error_m),
            "interval_ns": config.interval_ns,
        },
        baseline_config_identity(config),
        f"phase4.{family}",
        BASELINE_IMPLEMENTATION_VERSION,
    )


def frozen_parameter_grid() -> tuple[SweepParameterPoint, ...]:
    """Return the exact 39-point Batch 4.4 exploratory grid."""
    points: list[SweepParameterPoint] = [
        _baseline_point(MotionBaselineConfig(BaselineMethod.RAW_SAMPLES)),
        SweepParameterPoint.create(
            "exact_adjacent_sample",
            "exact_adjacent",
            {"method": "exact_adjacent"},
            ENCODER_PARAMETERS_IDENTITY,
            EXACT_NAME,
            EXACT_VERSION,
        ),
    ]
    for method in (BaselineMethod.UNIFORM_LINEAR, BaselineMethod.UNIFORM_HERMITE):
        points.extend(
            _baseline_point(MotionBaselineConfig(method, stride=stride))
            for stride in (2, 3, 5, 8, 10)
        )
    points.extend(
        _baseline_point(
            MotionBaselineConfig(
                BaselineMethod.FIXED_INTERVAL_LINEAR,
                interval_ns=interval_ns,
            )
        )
        for interval_ns in (
            200_000_000,
            300_000_000,
            500_000_000,
            750_000_000,
            1_000_000_000,
        )
    )
    points.extend(
        _baseline_point(
            MotionBaselineConfig(
                BaselineMethod.RDP_LINEAR,
                maximum_perpendicular_error_m=error_m,
            )
        )
        for error_m in (0.05, 0.10, 0.25, 0.50, 1.00)
    )
    for error_m in (0.025, 0.05, 0.10, 0.20):
        linear_config = PiecewiseLinearCodecConfig(error_m)
        points.append(
            SweepParameterPoint.create(
                f"position_bounded_linear-error-{_float_key(error_m)}",
                "position_bounded_linear",
                {
                    "method": "position_bounded_linear",
                    "maximum_position_error_m": error_m,
                },
                encoder_parameters_identity(linear_config),
                LINEAR_NAME,
                LINEAR_VERSION,
            )
        )
    for error_m in (0.025, 0.05, 0.10, 0.20):
        hermite_config = HermiteCodecConfig(error_m)
        points.append(
            SweepParameterPoint.create(
                f"unconstrained_hermite-error-{_float_key(error_m)}",
                "unconstrained_hermite",
                {
                    "method": "unconstrained_hermite",
                    "maximum_position_error_m": error_m,
                },
                hermite_encoder_parameters_identity(hermite_config),
                HERMITE_NAME,
                HERMITE_VERSION,
            )
        )
    for position_m in (0.05, 0.10, 0.20):
        for velocity_mps in (0.50, 1.00, 2.00):
            hybrid_config = VelocityBoundedCodecConfig(position_m, velocity_mps)
            points.append(
                SweepParameterPoint.create(
                    (
                        "position_velocity_hybrid-error-"
                        f"{_float_key(position_m)}-velocity-{_float_key(velocity_mps)}"
                    ),
                    "position_velocity_hybrid",
                    {
                        "method": "position_velocity_hybrid",
                        "maximum_position_error_m": position_m,
                        "maximum_velocity_error_mps": velocity_mps,
                    },
                    velocity_bounded_encoder_parameters_identity(hybrid_config),
                    HYBRID_NAME,
                    HYBRID_VERSION,
                )
            )
    if len(points) != 39:
        raise ValidationError("frozen sweep grid must contain exactly 39 points")
    return tuple(points)


@dataclass(frozen=True, slots=True)
class SweepConfiguration:
    """Resolved deterministic sweep configuration."""

    parameter_points: tuple[SweepParameterPoint, ...]
    subset_size: int = 25
    byte_ratio_targets: tuple[float, ...] = BYTE_RATIO_TARGETS
    keyframe_ratio_targets: tuple[float, ...] = KEYFRAME_RATIO_TARGETS
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("sweep schema_version differs")
        object.__setattr__(
            self, "subset_size", _positive_int(self.subset_size, "subset_size")
        )
        points = tuple(self.parameter_points)
        if not points or any(
            not isinstance(point, SweepParameterPoint) for point in points
        ):
            raise ValidationError("parameter_points must contain sweep points")
        if len({point.parameter_identity for point in points}) != len(points):
            raise ValidationError("duplicate parameter identity")
        if len({point.method_id for point in points}) != len(points):
            raise ValidationError("duplicate method_id")
        object.__setattr__(self, "parameter_points", points)
        for field_name in ("byte_ratio_targets", "keyframe_ratio_targets"):
            values = tuple(
                _finite_nonnegative(value, field_name)
                for value in getattr(self, field_name)
            )
            if not values or tuple(sorted(set(values))) != values:
                raise ValidationError(f"{field_name} must be sorted and unique")
            object.__setattr__(self, field_name, values)

    @property
    def identity(self) -> str:
        """Return the identity of the complete resolved sweep contract."""
        return canonical_sha256(
            "phase4-motion-sweep-configuration-v1",
            self.to_dict(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "subset_size": self.subset_size,
            "byte_ratio_targets": list(self.byte_ratio_targets),
            "keyframe_ratio_targets": list(self.keyframe_ratio_targets),
            "parameter_points": [point.to_dict() for point in self.parameter_points],
            "execution_policy": "parameter_major_then_selection_rank",
            "random_search": False,
            "per_trajectory_tuning": False,
            "map_assisted_encoding": False,
        }


def execution_order(
    configuration: SweepConfiguration,
    units: Sequence[MotionCohortUnit],
) -> tuple[tuple[SweepParameterPoint, MotionCohortUnit], ...]:
    """Return stable parameter-major then rank-major execution order."""
    if not isinstance(configuration, SweepConfiguration):
        raise ValidationError("configuration must be a SweepConfiguration")
    ordered_units = tuple(
        sorted(units, key=lambda unit: (unit.selection_rank, unit.source_scenario_id))
    )
    if len(ordered_units) != configuration.subset_size:
        raise ValidationError("unit count differs from sweep subset_size")
    return tuple(
        (point, unit)
        for point in configuration.parameter_points
        for unit in ordered_units
    )


@dataclass(frozen=True, slots=True)
class ConfigurationBudgetRecord:
    """One aggregate configuration budget available for matching."""

    family: str
    method_id: str
    parameter_identity: str
    byte_ratio: float
    keyframe_ratio: float

    def __post_init__(self) -> None:
        for field_name in ("family", "method_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "parameter_identity",
            _sha256(self.parameter_identity, "parameter_identity"),
        )
        for field_name in ("byte_ratio", "keyframe_ratio"):
            object.__setattr__(
                self,
                field_name,
                _finite_nonnegative(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class MatchedBudgetSelection:
    """A deterministic method/target budget selection."""

    dimension: BudgetDimension | str
    family: str
    target: float
    method_id: str
    parameter_identity: str
    actual_budget: float
    relation: BudgetRelation | str
    absolute_mismatch: float
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("selection schema_version differs")
        try:
            dimension = BudgetDimension(self.dimension)
            relation = BudgetRelation(self.relation)
        except (TypeError, ValueError):
            raise ValidationError("selection enum value is invalid") from None
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "relation", relation)
        for field_name in ("family", "method_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "parameter_identity",
            _sha256(self.parameter_identity, "parameter_identity"),
        )
        target = _finite_nonnegative(self.target, "target")
        actual = _finite_nonnegative(self.actual_budget, "actual_budget")
        mismatch = _finite_nonnegative(self.absolute_mismatch, "absolute_mismatch")
        if mismatch != abs(actual - target):
            raise ValidationError("absolute_mismatch differs from budget values")
        expected = (
            BudgetRelation.EQUAL
            if actual == target
            else BudgetRelation.BELOW
            if actual < target
            else BudgetRelation.ABOVE
        )
        if relation is not expected:
            raise ValidationError("budget relation differs from values")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "actual_budget", actual)
        object.__setattr__(self, "absolute_mismatch", mismatch)

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "dimension": cast(BudgetDimension, self.dimension).value,
            "family": self.family,
            "target": self.target,
            "method_id": self.method_id,
            "parameter_identity": self.parameter_identity,
            "actual_budget": self.actual_budget,
            "relation": cast(BudgetRelation, self.relation).value,
            "absolute_mismatch": self.absolute_mismatch,
        }


def select_matched_budgets(
    records: Sequence[ConfigurationBudgetRecord],
    targets: Sequence[float],
    dimension: BudgetDimension | str,
) -> tuple[MatchedBudgetSelection, ...]:
    """Select configurations by budget alone using the frozen nearest-under rule."""
    try:
        normalized_dimension = BudgetDimension(dimension)
    except (TypeError, ValueError):
        raise ValidationError("invalid budget dimension") from None
    normalized_targets = tuple(
        _finite_nonnegative(value, "target") for value in targets
    )
    if not records or not normalized_targets:
        raise ValidationError("budget matching requires records and targets")
    grouped: defaultdict[str, list[ConfigurationBudgetRecord]] = defaultdict(list)
    for record in records:
        if not isinstance(record, ConfigurationBudgetRecord):
            raise ValidationError("records must contain ConfigurationBudgetRecord")
        grouped[record.family].append(record)
    selections: list[MatchedBudgetSelection] = []
    field_name = normalized_dimension.value
    for family in sorted(grouped):
        candidates = grouped[family]
        for target in normalized_targets:
            below = [
                record
                for record in candidates
                if cast(float, getattr(record, field_name)) <= target
            ]
            if below:
                budget = max(
                    cast(float, getattr(record, field_name)) for record in below
                )
                eligible = [
                    record
                    for record in below
                    if cast(float, getattr(record, field_name)) == budget
                ]
            else:
                budget = min(
                    cast(float, getattr(record, field_name)) for record in candidates
                )
                eligible = [
                    record
                    for record in candidates
                    if cast(float, getattr(record, field_name)) == budget
                ]
            selected = min(eligible, key=lambda record: record.parameter_identity)
            relation = (
                BudgetRelation.EQUAL
                if budget == target
                else BudgetRelation.BELOW
                if budget < target
                else BudgetRelation.ABOVE
            )
            selections.append(
                MatchedBudgetSelection(
                    normalized_dimension,
                    family,
                    target,
                    selected.method_id,
                    selected.parameter_identity,
                    budget,
                    relation,
                    abs(budget - target),
                )
            )
    return tuple(selections)


@dataclass(frozen=True, slots=True)
class ScenarioConfigurationExecution:
    """One completed scenario/configuration execution record."""

    parameter_identity: str
    method_id: str
    family: str
    source_scenario_id: str
    scenario_id: str
    selection_rank: int
    trajectory_ids: tuple[str, ...]
    source_sample_count: int
    valid_sample_count: int
    retained_keyframe_count: int
    procedural_segment_count: int
    serialized_representation_bytes: int
    raw_canonical_bytes: int
    exact_adjacent_segments: int
    motion_summary_json: str
    semantic_summary_json: str
    artifact_summary_json: str
    resource_summary_json: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("execution schema_version differs")
        object.__setattr__(
            self,
            "parameter_identity",
            _sha256(self.parameter_identity, "parameter_identity"),
        )
        for field_name in ("method_id", "family", "source_scenario_id", "scenario_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self, "selection_rank", _positive_int(self.selection_rank, "selection_rank")
        )
        trajectories = tuple(
            _text(value, "trajectory_id") for value in self.trajectory_ids
        )
        if not trajectories or len(set(trajectories)) != len(trajectories):
            raise ValidationError("trajectory_ids must be nonempty and unique")
        object.__setattr__(self, "trajectory_ids", trajectories)
        for field_name in (
            "source_sample_count",
            "valid_sample_count",
            "retained_keyframe_count",
            "procedural_segment_count",
            "serialized_representation_bytes",
            "raw_canonical_bytes",
            "exact_adjacent_segments",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        if self.raw_canonical_bytes == 0 or self.valid_sample_count == 0:
            raise ValidationError("execution budget denominators must be positive")
        for field_name in (
            "motion_summary_json",
            "semantic_summary_json",
            "artifact_summary_json",
            "resource_summary_json",
        ):
            _canonical_mapping(getattr(self, field_name), field_name)

    @property
    def byte_ratio(self) -> float:
        """Return actual serialized bytes relative to raw canonical bytes."""
        return self.serialized_representation_bytes / self.raw_canonical_bytes

    @property
    def keyframe_ratio(self) -> float:
        """Return retained keyframes relative to valid source samples."""
        return self.retained_keyframe_count / self.valid_sample_count

    @property
    def segment_ratio(self) -> float:
        """Return procedural segments relative to exact adjacent segments."""
        if self.exact_adjacent_segments == 0:
            return 0.0
        return self.procedural_segment_count / self.exact_adjacent_segments

    def to_dict(self) -> dict[str, object]:
        """Return the canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "parameter_identity": self.parameter_identity,
            "method_id": self.method_id,
            "family": self.family,
            "source_scenario_id": self.source_scenario_id,
            "scenario_id": self.scenario_id,
            "selection_rank": self.selection_rank,
            "trajectory_ids": list(self.trajectory_ids),
            "trajectory_count": len(self.trajectory_ids),
            "source_sample_count": self.source_sample_count,
            "valid_sample_count": self.valid_sample_count,
            "retained_keyframe_count": self.retained_keyframe_count,
            "procedural_segment_count": self.procedural_segment_count,
            "serialized_representation_bytes": self.serialized_representation_bytes,
            "raw_canonical_bytes": self.raw_canonical_bytes,
            "exact_adjacent_segments": self.exact_adjacent_segments,
            "byte_ratio": self.byte_ratio,
            "keyframe_ratio": self.keyframe_ratio,
            "segment_ratio": self.segment_ratio,
            "motion_summary": dict(
                _canonical_mapping(self.motion_summary_json, "motion_summary_json")
            ),
            "semantic_summary": dict(
                _canonical_mapping(self.semantic_summary_json, "semantic_summary_json")
            ),
            "artifact_summary": dict(
                _canonical_mapping(self.artifact_summary_json, "artifact_summary_json")
            ),
            "resource_summary": dict(
                _canonical_mapping(self.resource_summary_json, "resource_summary_json")
            ),
        }


@dataclass(frozen=True, slots=True)
class SweepFailureRecord:
    """One explicit failed sweep execution."""

    parameter_identity: str
    method_id: str
    source_scenario_id: str
    selection_rank: int
    attempt_count: int
    error_type: str
    error_message: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("failure schema_version differs")
        object.__setattr__(
            self,
            "parameter_identity",
            _sha256(self.parameter_identity, "parameter_identity"),
        )
        for field_name in (
            "method_id",
            "source_scenario_id",
            "error_type",
            "error_message",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        for field_name in ("selection_rank", "attempt_count"):
            object.__setattr__(
                self, field_name, _positive_int(getattr(self, field_name), field_name)
            )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "parameter_identity": self.parameter_identity,
            "method_id": self.method_id,
            "source_scenario_id": self.source_scenario_id,
            "selection_rank": self.selection_rank,
            "attempt_count": self.attempt_count,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class SweepCheckpointState:
    """One immutable completed or retryable failed checkpoint."""

    parameter_identity: str
    method_id: str
    source_scenario_id: str
    selection_rank: int
    status: CheckpointStatus | str
    attempt_count: int
    payload_json: str
    payload_sha256: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("checkpoint schema_version differs")
        object.__setattr__(
            self,
            "parameter_identity",
            _sha256(self.parameter_identity, "parameter_identity"),
        )
        for field_name in ("method_id", "source_scenario_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self, "selection_rank", _positive_int(self.selection_rank, "selection_rank")
        )
        object.__setattr__(
            self, "attempt_count", _positive_int(self.attempt_count, "attempt_count")
        )
        try:
            status = CheckpointStatus(self.status)
        except (TypeError, ValueError):
            raise ValidationError("checkpoint status is invalid") from None
        object.__setattr__(self, "status", status)
        _canonical_mapping(self.payload_json, "payload_json")
        expected = hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()
        checksum = _sha256(self.payload_sha256, "payload_sha256")
        if checksum != expected:
            raise ValidationError("payload_sha256 differs from payload_json")
        object.__setattr__(self, "payload_sha256", checksum)

    @classmethod
    def create(
        cls,
        point: SweepParameterPoint,
        unit: MotionCohortUnit,
        status: CheckpointStatus,
        attempt_count: int,
        payload: Mapping[str, object],
    ) -> SweepCheckpointState:
        """Create a checkpoint from its canonical payload."""
        payload_json = _canonical_json(payload)
        return cls(
            point.parameter_identity,
            point.method_id,
            unit.source_scenario_id,
            unit.selection_rank,
            status,
            attempt_count,
            payload_json,
            hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "parameter_identity": self.parameter_identity,
            "method_id": self.method_id,
            "source_scenario_id": self.source_scenario_id,
            "selection_rank": self.selection_rank,
            "status": cast(CheckpointStatus, self.status).value,
            "attempt_count": self.attempt_count,
            "payload": dict(_canonical_mapping(self.payload_json, "payload_json")),
            "payload_sha256": self.payload_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SweepCheckpointState:
        """Strictly parse a checkpoint dictionary."""
        expected = {
            "schema_version",
            "parameter_identity",
            "method_id",
            "source_scenario_id",
            "selection_rank",
            "status",
            "attempt_count",
            "payload",
            "payload_sha256",
        }
        if set(value) != expected or not isinstance(value["payload"], Mapping):
            raise SchemaError("checkpoint fields differ from contract")
        try:
            return cls(
                schema_version=cast(Any, value["schema_version"]),
                parameter_identity=cast(Any, value["parameter_identity"]),
                method_id=cast(Any, value["method_id"]),
                source_scenario_id=cast(Any, value["source_scenario_id"]),
                selection_rank=cast(Any, value["selection_rank"]),
                status=cast(Any, value["status"]),
                attempt_count=cast(Any, value["attempt_count"]),
                payload_json=_canonical_json(
                    cast(Mapping[str, object], value["payload"])
                ),
                payload_sha256=cast(Any, value["payload_sha256"]),
            )
        except (TypeError, ValidationError) as error:
            raise SchemaError(str(error)) from None


def checkpoint_path(
    root: Path,
    point: SweepParameterPoint,
    source_scenario_id: str,
) -> Path:
    """Return the deterministic checkpoint path for one execution unit."""
    return (
        root
        / "checkpoints-v1"
        / point.parameter_identity
        / f"{_text(source_scenario_id, 'source_scenario_id')}.json"
    )


def load_checkpoint(path: Path) -> SweepCheckpointState:
    """Load and strictly verify one checkpoint."""
    if path.is_symlink() or not path.is_file():
        raise ArtifactError(f"checkpoint is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ArtifactError(f"checkpoint is unreadable: {path}") from error
    if not isinstance(value, Mapping):
        raise ArtifactError("checkpoint root must be a mapping")
    try:
        checkpoint = SweepCheckpointState.from_dict(cast(Mapping[str, object], value))
    except SchemaError as error:
        raise ArtifactError(str(error)) from error
    if path.read_text(encoding="utf-8") != canonical_json_text(checkpoint.to_dict()):
        raise ArtifactError("checkpoint is not canonical")
    return checkpoint


def write_checkpoint(path: Path, checkpoint: SweepCheckpointState) -> None:
    """Atomically install a checkpoint without replacing completed work."""
    if not isinstance(checkpoint, SweepCheckpointState):
        raise ValidationError("checkpoint must be a SweepCheckpointState")
    if path.exists():
        existing = load_checkpoint(path)
        if cast(CheckpointStatus, existing.status) is CheckpointStatus.COMPLETED:
            if existing != checkpoint:
                raise ArtifactError("completed checkpoint is immutable")
            return
        if checkpoint.attempt_count <= existing.attempt_count:
            raise ArtifactError("failed checkpoint retry must advance attempt_count")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    if temporary.exists():
        temporary.unlink()
    text = canonical_json_text(checkpoint.to_dict())
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as error:
        raise ArtifactError(f"unable to install checkpoint: {path}") from error


@dataclass(frozen=True, slots=True)
class SweepManifest:
    """Strict immutable identity-bearing sweep manifest."""

    configuration_identity: str
    cohort_identity: str
    validation_identity: str
    subset_identity: str
    trajectory_membership_identity: str
    scenario_ids: tuple[str, ...]
    parameter_identities: tuple[str, ...]
    execution_count: int
    completed_count: int
    failure_count: int
    repeat_reuse_count: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("manifest schema_version differs")
        for field_name in (
            "configuration_identity",
            "cohort_identity",
            "validation_identity",
            "subset_identity",
            "trajectory_membership_identity",
        ):
            object.__setattr__(
                self, field_name, _sha256(getattr(self, field_name), field_name)
            )
        scenarios = tuple(_text(value, "scenario_id") for value in self.scenario_ids)
        parameters = tuple(
            _sha256(value, "parameter_identity") for value in self.parameter_identities
        )
        if len(set(scenarios)) != len(scenarios):
            raise ValidationError("manifest scenario_ids must be unique")
        if len(set(parameters)) != len(parameters):
            raise ValidationError("manifest parameter_identities must be unique")
        object.__setattr__(self, "scenario_ids", scenarios)
        object.__setattr__(self, "parameter_identities", parameters)
        for field_name in (
            "execution_count",
            "completed_count",
            "failure_count",
            "repeat_reuse_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        if self.completed_count + self.failure_count != self.execution_count:
            raise ValidationError("manifest completion accounting differs")

    @property
    def identity(self) -> str:
        """Return the deterministic scientific manifest identity."""
        return canonical_sha256(
            "phase4-motion-sweep-manifest-v1",
            self.to_dict(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "configuration_identity": self.configuration_identity,
            "cohort_identity": self.cohort_identity,
            "validation_identity": self.validation_identity,
            "subset_identity": self.subset_identity,
            "trajectory_membership_identity": (self.trajectory_membership_identity),
            "scenario_ids": list(self.scenario_ids),
            "parameter_identities": list(self.parameter_identities),
            "execution_count": self.execution_count,
            "completed_count": self.completed_count,
            "failure_count": self.failure_count,
            "repeat_reuse_count": self.repeat_reuse_count,
        }


@dataclass(frozen=True, slots=True)
class SweepResultSummary:
    """Strict immutable aggregate sweep result summary."""

    manifest_identity: str
    configuration_count: int
    scenario_execution_count: int
    matched_byte_selection_count: int
    matched_keyframe_selection_count: int
    failure_count: int
    payload_json: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("summary schema_version differs")
        object.__setattr__(
            self,
            "manifest_identity",
            _sha256(self.manifest_identity, "manifest_identity"),
        )
        for field_name in (
            "configuration_count",
            "scenario_execution_count",
            "matched_byte_selection_count",
            "matched_keyframe_selection_count",
            "failure_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        _canonical_mapping(self.payload_json, "payload_json")

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "manifest_identity": self.manifest_identity,
            "configuration_count": self.configuration_count,
            "scenario_execution_count": self.scenario_execution_count,
            "matched_byte_selection_count": self.matched_byte_selection_count,
            "matched_keyframe_selection_count": (self.matched_keyframe_selection_count),
            "failure_count": self.failure_count,
            "payload": dict(_canonical_mapping(self.payload_json, "payload_json")),
        }


def _unit_from_mapping(value: Mapping[str, object]) -> MotionCohortUnit:
    if set(value) != _UNIT_FIELDS:
        raise SchemaError("cohort unit fields differ from contract")
    try:
        return MotionCohortUnit(
            provider_partition=cast(Any, value["provider_partition"]),
            cohort_role=cast(Any, value["cohort_role"]),
            selection_rank=cast(Any, value["selection_rank"]),
            source_scenario_id=cast(Any, value["source_scenario_id"]),
            motion_object_path=Path(cast(str, value["motion_object_path"])),
            motion_size_bytes=cast(Any, value["motion_size_bytes"]),
            motion_sha256=cast(Any, value["motion_sha256"]),
            map_object_path=Path(cast(str, value["map_object_path"])),
            map_size_bytes=cast(Any, value["map_size_bytes"]),
            map_sha256=cast(Any, value["map_sha256"]),
            materialization_cache_key=cast(Any, value["materialization_cache_key"]),
            validation_included=cast(Any, value["validation_included"]),
            exclusion_reason=cast(Any, value["exclusion_reason"]),
        )
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def read_ranked_development_prefix(
    path: Path,
    count: int = 25,
    *,
    chunk_size: int = 4096,
) -> tuple[MotionCohortUnit, ...]:
    """Parse only the requested leading development units from a cohort manifest."""
    normalized_count = _positive_int(count, "count")
    normalized_chunk = _positive_int(chunk_size, "chunk_size")
    decoder = json.JSONDecoder()
    marker = re.compile(r'"units"\s*:\s*\[')
    buffer = ""
    units_started = False
    units: list[MotionCohortUnit] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            while len(units) < normalized_count:
                if not units_started:
                    match = marker.search(buffer)
                    if match is None:
                        chunk = stream.read(normalized_chunk)
                        if not chunk:
                            raise SchemaError("cohort manifest has no units array")
                        buffer += chunk
                        continue
                    buffer = buffer[match.end() :]
                    units_started = True
                buffer = buffer.lstrip()
                if buffer.startswith(","):
                    buffer = buffer[1:].lstrip()
                try:
                    value, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    chunk = stream.read(normalized_chunk)
                    if not chunk:
                        raise SchemaError("cohort manifest unit is truncated") from None
                    buffer += chunk
                    continue
                if not isinstance(value, Mapping):
                    raise SchemaError("cohort manifest unit must be a mapping")
                unit = _unit_from_mapping(cast(Mapping[str, object], value))
                if (
                    cast(CohortRole, unit.cohort_role).value != "development"
                    or unit.selection_rank != len(units) + 1
                    or not unit.validation_included
                ):
                    raise SchemaError("development prefix role or rank differs")
                units.append(unit)
                buffer = buffer[end:]
    except (OSError, UnicodeError) as error:
        raise ArtifactError(f"unable to read cohort manifest prefix: {path}") from error
    return tuple(units)


def read_ranked_cohort_role(
    path: Path,
    role: CohortRole | str,
    count: int,
    *,
    chunk_size: int = 4096,
) -> tuple[MotionCohortUnit, ...]:
    """Parse one complete frozen role without reading any following role."""
    try:
        normalized_role = CohortRole(role)
    except (TypeError, ValueError):
        raise ValidationError("role is invalid") from None
    normalized_count = _positive_int(count, "count")
    normalized_chunk = _positive_int(chunk_size, "chunk_size")
    decoder = json.JSONDecoder()
    marker = re.compile(r'"units"\s*:\s*\[')
    buffer = ""
    units_started = False
    selected: list[MotionCohortUnit] = []
    seen_target = False
    role_order = {
        CohortRole.DEVELOPMENT: 0,
        CohortRole.PILOT: 1,
        CohortRole.TEST: 2,
    }
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            while len(selected) < normalized_count:
                if not units_started:
                    match = marker.search(buffer)
                    if match is None:
                        chunk = stream.read(normalized_chunk)
                        if not chunk:
                            raise SchemaError("cohort manifest has no units array")
                        buffer += chunk
                        continue
                    buffer = buffer[match.end() :]
                    units_started = True
                buffer = buffer.lstrip()
                if buffer.startswith(","):
                    buffer = buffer[1:].lstrip()
                try:
                    value, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    chunk = stream.read(normalized_chunk)
                    if not chunk:
                        raise SchemaError("cohort manifest unit is truncated") from None
                    buffer += chunk
                    continue
                if not isinstance(value, Mapping):
                    raise SchemaError("cohort manifest unit must be a mapping")
                unit = _unit_from_mapping(cast(Mapping[str, object], value))
                unit_role = cast(CohortRole, unit.cohort_role)
                if role_order[unit_role] > role_order[normalized_role]:
                    raise SchemaError("cohort role has fewer units than required")
                if unit_role is normalized_role:
                    seen_target = True
                    if not unit.validation_included:
                        raise SchemaError("withheld cohort unit is not included")
                    selected.append(unit)
                elif seen_target:
                    raise SchemaError("cohort role ordering differs")
                buffer = buffer[end:]
    except (OSError, UnicodeError) as error:
        raise ArtifactError(f"unable to read cohort manifest role: {path}") from error
    if len({unit.source_scenario_id for unit in selected}) != normalized_count:
        raise SchemaError("cohort role contains duplicate scenario IDs")
    if tuple(selected) != tuple(
        sorted(
            selected,
            key=lambda unit: (unit.selection_rank, unit.source_scenario_id),
        )
    ):
        raise SchemaError("cohort role rank order differs")
    return tuple(selected)
