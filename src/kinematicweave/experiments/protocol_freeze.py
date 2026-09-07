"""Deterministic contracts for the Phase 4 motion evaluation protocol freeze."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import cast

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import ValidationError
from kinematicweave.experiments.motion_sweep import (
    BudgetDimension,
    ConfigurationBudgetRecord,
    MatchedBudgetSelection,
    select_matched_budgets,
)

SCHEMA_VERSION = "1.0"
PRIMARY_FAMILIES = (
    "fixed_interval_linear",
    "position_bounded_linear",
    "position_velocity_hybrid",
    "rdp_linear",
    "unconstrained_hermite",
    "uniform_hermite",
    "uniform_linear",
)
REFERENCE_FAMILIES = ("exact_adjacent", "raw_samples")
ALL_FAMILIES = (*REFERENCE_FAMILIES, *PRIMARY_FAMILIES)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValidationError(f"{field_name} must be nonempty stripped text")
    return value


def _sha256(value: object, field_name: str) -> str:
    normalized = _text(value, field_name)
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValidationError(f"{field_name} must be lowercase SHA-256")
    return normalized


def _positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{field_name} must be a nonnegative integer")
    return value


def _finite_nonnegative(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValidationError(f"{field_name} must be finite and nonnegative")
    return normalized


def _mapping_json(value: object, field_name: str) -> str:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be a mapping")
    return canonical_json_text(dict(value))


class ComparisonPriority(StrEnum):
    """Scientific role of one frozen budget comparison."""

    PRIMARY = "primary"
    DIAGNOSTIC = "diagnostic"


class CampaignRole(StrEnum):
    """Role of one configuration in the final campaign."""

    REFERENCE = "reference"
    PRIMARY = "primary"
    DIAGNOSTIC_ABLATION = "diagnostic_ablation"


@dataclass(frozen=True, slots=True)
class FamilyBudgetRange:
    """Observed aggregate budget support for one method family."""

    family: str
    byte_ratio_minimum: float
    byte_ratio_maximum: float
    keyframe_ratio_minimum: float
    keyframe_ratio_maximum: float
    configuration_count: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("range schema_version differs")
        object.__setattr__(self, "family", _text(self.family, "family"))
        for field_name in (
            "byte_ratio_minimum",
            "byte_ratio_maximum",
            "keyframe_ratio_minimum",
            "keyframe_ratio_maximum",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_nonnegative(getattr(self, field_name), field_name),
            )
        if self.byte_ratio_minimum > self.byte_ratio_maximum:
            raise ValidationError("byte ratio range is reversed")
        if self.keyframe_ratio_minimum > self.keyframe_ratio_maximum:
            raise ValidationError("keyframe ratio range is reversed")
        object.__setattr__(
            self,
            "configuration_count",
            _positive_int(self.configuration_count, "configuration_count"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical range representation."""
        return {
            "schema_version": self.schema_version,
            "family": self.family,
            "byte_ratio": {
                "minimum": self.byte_ratio_minimum,
                "maximum": self.byte_ratio_maximum,
            },
            "keyframe_ratio": {
                "minimum": self.keyframe_ratio_minimum,
                "maximum": self.keyframe_ratio_maximum,
            },
            "configuration_count": self.configuration_count,
        }


def reconstruct_budget_ranges(
    records: Sequence[ConfigurationBudgetRecord],
) -> tuple[FamilyBudgetRange, ...]:
    """Reconstruct deterministic achieved ranges from aggregate budget records."""
    if not records:
        raise ValidationError("range reconstruction requires budget records")
    grouped: defaultdict[str, list[ConfigurationBudgetRecord]] = defaultdict(list)
    for record in records:
        if not isinstance(record, ConfigurationBudgetRecord):
            raise ValidationError("records must contain ConfigurationBudgetRecord")
        grouped[record.family].append(record)
    return tuple(
        FamilyBudgetRange(
            family,
            min(record.byte_ratio for record in grouped[family]),
            max(record.byte_ratio for record in grouped[family]),
            min(record.keyframe_ratio for record in grouped[family]),
            max(record.keyframe_ratio for record in grouped[family]),
            len(grouped[family]),
        )
        for family in sorted(grouped)
    )


@dataclass(frozen=True, slots=True)
class SupplementalGridPoint:
    """One coverage-only configuration proposed after the accepted sweep."""

    method_id: str
    family: str
    configuration_json: str
    configuration_identity: str
    justification: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("supplemental point schema_version differs")
        for field_name in ("method_id", "family", "justification"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "configuration_identity",
            _sha256(self.configuration_identity, "configuration_identity"),
        )
        try:
            configuration = cast(
                Mapping[str, object],
                __import__("json").loads(self.configuration_json),
            )
        except (TypeError, ValueError, RecursionError):
            raise ValidationError("configuration_json is malformed") from None
        object.__setattr__(
            self,
            "configuration_json",
            _mapping_json(configuration, "configuration"),
        )

    @property
    def identity(self) -> str:
        """Return the canonical supplemental point identity."""
        return canonical_sha256(
            "phase4-protocol-supplemental-point-v1",
            self.to_dict(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary."""
        return {
            "schema_version": self.schema_version,
            "method_id": self.method_id,
            "family": self.family,
            "configuration": __import__("json").loads(self.configuration_json),
            "configuration_identity": self.configuration_identity,
            "justification": self.justification,
        }


@dataclass(frozen=True, slots=True)
class SupplementalGrid:
    """Frozen deterministic coverage-only supplemental grid."""

    accepted_configuration_identities: tuple[str, ...]
    points: tuple[SupplementalGridPoint, ...]
    frozen_before_execution: bool
    no_points_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("supplemental grid schema_version differs")
        accepted = tuple(
            _sha256(value, "accepted_configuration_identity")
            for value in self.accepted_configuration_identities
        )
        if len(set(accepted)) != len(accepted):
            raise ValidationError("duplicate accepted configuration identity")
        points = tuple(self.points)
        if any(not isinstance(point, SupplementalGridPoint) for point in points):
            raise ValidationError("supplemental grid contains an invalid point")
        if len({point.identity for point in points}) != len(points):
            raise ValidationError("duplicate supplemental point")
        if len({point.method_id for point in points}) != len(points):
            raise ValidationError("duplicate supplemental method_id")
        if any(point.configuration_identity in accepted for point in points):
            raise ValidationError("supplemental point duplicates an accepted point")
        if not isinstance(self.frozen_before_execution, bool):
            raise ValidationError("frozen_before_execution must be boolean")
        reason = self.no_points_reason
        if points and reason is not None:
            raise ValidationError(
                "nonempty supplemental grid cannot have no-point reason"
            )
        if not points:
            object.__setattr__(
                self,
                "no_points_reason",
                _text(reason, "no_points_reason"),
            )
        object.__setattr__(self, "accepted_configuration_identities", accepted)
        object.__setattr__(self, "points", points)

    @property
    def identity(self) -> str:
        """Return the complete supplemental-grid identity."""
        return canonical_sha256(
            "phase4-protocol-supplemental-grid-v1",
            self.to_dict(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary."""
        return {
            "schema_version": self.schema_version,
            "accepted_configuration_identities": list(
                self.accepted_configuration_identities
            ),
            "points": [point.to_dict() for point in self.points],
            "point_identities": [point.identity for point in self.points],
            "frozen_before_execution": self.frozen_before_execution,
            "no_points_reason": self.no_points_reason,
        }


@dataclass(frozen=True, slots=True)
class FrozenBudgetTarget:
    """One outcome-independent final comparison budget."""

    dimension: BudgetDimension | str
    target: float
    priority: ComparisonPriority | str
    included_families: tuple[str, ...]
    maximum_permitted_mismatch: float
    selections: tuple[MatchedBudgetSelection, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("budget target schema_version differs")
        try:
            dimension = BudgetDimension(self.dimension)
            priority = ComparisonPriority(self.priority)
        except (TypeError, ValueError):
            raise ValidationError("budget target enum value is invalid") from None
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "target", _finite_nonnegative(self.target, "target"))
        object.__setattr__(
            self,
            "maximum_permitted_mismatch",
            _finite_nonnegative(
                self.maximum_permitted_mismatch,
                "maximum_permitted_mismatch",
            ),
        )
        families = tuple(
            sorted(
                _text(family, "included_family") for family in self.included_families
            )
        )
        if not families or len(set(families)) != len(families):
            raise ValidationError("included_families must be unique and nonempty")
        selections = tuple(self.selections)
        if any(
            not isinstance(selection, MatchedBudgetSelection)
            for selection in selections
        ):
            raise ValidationError("budget target contains invalid selections")
        if tuple(selection.family for selection in selections) != families:
            raise ValidationError("budget selections differ from included families")
        if any(
            selection.dimension is not dimension
            or selection.target != self.target
            or selection.absolute_mismatch > self.maximum_permitted_mismatch
            for selection in selections
        ):
            raise ValidationError("budget selection violates target coverage")
        object.__setattr__(self, "included_families", families)
        object.__setattr__(self, "selections", selections)

    @property
    def identity(self) -> str:
        """Return the target and selection identity."""
        return canonical_sha256("phase4-final-budget-target-v1", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary."""
        return {
            "schema_version": self.schema_version,
            "dimension": cast(BudgetDimension, self.dimension).value,
            "target": self.target,
            "priority": cast(ComparisonPriority, self.priority).value,
            "included_families": list(self.included_families),
            "maximum_permitted_mismatch": self.maximum_permitted_mismatch,
            "matching_policy": "largest_at_or_below_else_smallest_above",
            "selection_input": "relevant_budget_only",
            "tie_break": "canonical_configuration_identity",
            "metric_interpolation": False,
            "selections": [selection.to_dict() for selection in self.selections],
        }


def freeze_budget_target(
    records: Sequence[ConfigurationBudgetRecord],
    dimension: BudgetDimension | str,
    target: float,
    families: Sequence[str],
    maximum_permitted_mismatch: float,
    priority: ComparisonPriority | str,
) -> FrozenBudgetTarget:
    """Select and validate one final target without outcome values."""
    normalized_families = tuple(sorted(_text(family, "family") for family in families))
    selected_records = tuple(
        record for record in records if record.family in normalized_families
    )
    if {record.family for record in selected_records} != set(normalized_families):
        raise ValidationError("budget target lacks an included family")
    selections = select_matched_budgets(
        selected_records,
        (_finite_nonnegative(target, "target"),),
        dimension,
    )
    return FrozenBudgetTarget(
        dimension,
        target,
        priority,
        normalized_families,
        maximum_permitted_mismatch,
        selections,
    )


@dataclass(frozen=True, slots=True)
class CampaignMethod:
    """One exact configuration retained in the pilot and test matrix."""

    method_id: str
    family: str
    configuration_id: str
    configuration_json: str
    role: CampaignRole | str
    retained_reason: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("campaign method schema_version differs")
        for field_name in ("method_id", "family", "retained_reason"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "configuration_id",
            _sha256(self.configuration_id, "configuration_id"),
        )
        try:
            role = CampaignRole(self.role)
            configuration = cast(
                Mapping[str, object],
                __import__("json").loads(self.configuration_json),
            )
        except (TypeError, ValueError, RecursionError):
            raise ValidationError("campaign method value is invalid") from None
        object.__setattr__(self, "role", role)
        object.__setattr__(
            self,
            "configuration_json",
            _mapping_json(configuration, "configuration"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary."""
        return {
            "schema_version": self.schema_version,
            "method_id": self.method_id,
            "family": self.family,
            "configuration_id": self.configuration_id,
            "configuration": __import__("json").loads(self.configuration_json),
            "role": cast(CampaignRole, self.role).value,
            "retained_reason": self.retained_reason,
        }


@dataclass(frozen=True, slots=True)
class FinalCampaignMatrix:
    """Exact pilot/test motion campaign matrix frozen on development data."""

    methods: tuple[CampaignMethod, ...]
    budget_targets: tuple[FrozenBudgetTarget, ...]
    cohort_identity: str
    development_validation_identity: str
    motion_metric_names: tuple[str, ...]
    semantic_metric_names: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("campaign matrix schema_version differs")
        methods = tuple(self.methods)
        if not methods or any(
            not isinstance(method, CampaignMethod) for method in methods
        ):
            raise ValidationError("campaign matrix methods are invalid")
        if len({method.method_id for method in methods}) != len(methods):
            raise ValidationError("duplicate campaign method_id")
        if len({method.configuration_id for method in methods}) != len(methods):
            raise ValidationError("duplicate campaign configuration_id")
        families = {method.family for method in methods}
        if not set(ALL_FAMILIES).issubset(families):
            raise ValidationError("campaign matrix omits a required family")
        references = {
            method.family for method in methods if method.role is CampaignRole.REFERENCE
        }
        if references != set(REFERENCE_FAMILIES):
            raise ValidationError("campaign matrix reference roles differ")
        targets = tuple(self.budget_targets)
        if not targets or any(
            not isinstance(target, FrozenBudgetTarget) for target in targets
        ):
            raise ValidationError("campaign matrix budget targets are invalid")
        target_keys = {
            (cast(BudgetDimension, target.dimension).value, target.target)
            for target in targets
        }
        if len(target_keys) != len(targets):
            raise ValidationError("duplicate campaign budget target")
        if not any(
            target.priority is ComparisonPriority.PRIMARY
            and target.dimension is BudgetDimension.BYTE_RATIO
            for target in targets
        ) or not any(
            target.priority is ComparisonPriority.PRIMARY
            and target.dimension is BudgetDimension.KEYFRAME_RATIO
            for target in targets
        ):
            raise ValidationError("campaign matrix lacks primary budget dimensions")
        for field_name in ("motion_metric_names", "semantic_metric_names"):
            values = tuple(
                _text(value, field_name) for value in getattr(self, field_name)
            )
            if not values or len(set(values)) != len(values):
                raise ValidationError(f"{field_name} must be unique and nonempty")
            object.__setattr__(self, field_name, values)
        object.__setattr__(
            self, "cohort_identity", _sha256(self.cohort_identity, "cohort_identity")
        )
        object.__setattr__(
            self,
            "development_validation_identity",
            _sha256(
                self.development_validation_identity,
                "development_validation_identity",
            ),
        )
        object.__setattr__(self, "methods", methods)
        object.__setattr__(self, "budget_targets", targets)

    @property
    def identity(self) -> str:
        """Return the complete immutable final-matrix identity."""
        return canonical_sha256("phase4-final-campaign-matrix-v1", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        """Return the full pilot/test execution contract."""
        return {
            "schema_version": self.schema_version,
            "cohort_identity": self.cohort_identity,
            "development_validation_identity": self.development_validation_identity,
            "pilot_scenario_count": 50,
            "test_scenario_count": 300,
            "methods": [method.to_dict() for method in self.methods],
            "configuration_ids": [method.configuration_id for method in self.methods],
            "budget_targets": [target.to_dict() for target in self.budget_targets],
            "motion_metrics": list(self.motion_metric_names),
            "semantic_metrics": list(self.semantic_metric_names),
            "execution_order": "method_order_then_deterministic_cohort_rank",
            "execution_policy": "sequential_bounded_one_scenario",
            "checkpoint_policy": {
                "unit": "scenario_configuration",
                "atomic": True,
                "completed_immutable": True,
                "verify_before_reuse": True,
                "failed_units_retained": True,
            },
            "repeat_run_policy": (
                "verify every completed checkpoint with zero representation "
                "recomputation and identical deterministic output identities"
            ),
            "resource_limits": {
                "workers": 1,
                "normal_peak_rss_bytes": 24 * 1024**3,
                "warning_peak_rss_bytes": 22 * 1024**3,
                "hard_stop_peak_rss_bytes": 26 * 1024**3,
                "minimum_free_disk_fraction": 0.15,
                "maximum_uninterrupted_job_seconds": 12 * 60 * 60,
            },
            "statistical_units": {
                "raw_motion_unit": "trajectory",
                "top_level_resampling_unit": "scenario",
                "paired_comparison_unit": "scenario",
                "resource_unit": "workload",
            },
            "aggregation_policy": {
                "motion": "scenario-aware aggregation of per-trajectory metrics",
                "events": "micro counts plus scenario-aware macro summaries",
                "runtime": "report each workload scale separately",
                "failures": "report separately and never hide by pairwise deletion",
            },
            "exclusion_policy": (
                "only predeclared invalid/corrupt/precondition exclusions; "
                "preserve extreme valid values and all failure records"
            ),
            "pilot_decisions_permitted": [
                "confirm runtime, memory, storage, failure, CI, and figure feasibility",
                "stop for external resource or implementation failure",
                "make operational scheduling changes that do not alter the matrix",
            ],
            "pilot_decisions_prohibited": [
                "method selection",
                "configuration, budget, metric, threshold, aggregation, or exclusion changes",
                "test-matrix changes after any pilot execution",
            ],
            "final_method_selected": False,
        }


@dataclass(frozen=True, slots=True)
class ResourceProjection:
    """Measured development scaling projection for the frozen matrix."""

    development_scenario_count: int
    method_count: int
    development_runtime_seconds: float
    development_representation_bytes: int
    development_metric_bytes: int
    measured_peak_rss_bytes: int
    filesystem_total_bytes: int
    filesystem_free_bytes: int
    bounded_validation_seconds: float
    bounded_validation_checkpoint_count: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("resource projection schema_version differs")
        for field_name in ("development_scenario_count", "method_count"):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), field_name),
            )
        for field_name in (
            "development_representation_bytes",
            "development_metric_bytes",
            "measured_peak_rss_bytes",
            "filesystem_total_bytes",
            "filesystem_free_bytes",
            "bounded_validation_checkpoint_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        for field_name in (
            "development_runtime_seconds",
            "bounded_validation_seconds",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_nonnegative(getattr(self, field_name), field_name),
            )
        if self.filesystem_free_bytes > self.filesystem_total_bytes:
            raise ValidationError("filesystem free bytes exceed total bytes")

    @staticmethod
    def _scaled_integer(value: int, numerator: int, denominator: int) -> int:
        return (value * numerator + denominator - 1) // denominator

    @property
    def pilot_runtime_seconds(self) -> float:
        return self.development_runtime_seconds * 50 / self.development_scenario_count

    @property
    def test_runtime_seconds(self) -> float:
        return self.development_runtime_seconds * 300 / self.development_scenario_count

    @property
    def pilot_representation_bytes(self) -> int:
        return self._scaled_integer(
            self.development_representation_bytes,
            50,
            self.development_scenario_count,
        )

    @property
    def test_representation_bytes(self) -> int:
        return self._scaled_integer(
            self.development_representation_bytes,
            300,
            self.development_scenario_count,
        )

    @property
    def pilot_metric_bytes(self) -> int:
        return self._scaled_integer(
            self.development_metric_bytes,
            50,
            self.development_scenario_count,
        )

    @property
    def test_metric_bytes(self) -> int:
        return self._scaled_integer(
            self.development_metric_bytes,
            300,
            self.development_scenario_count,
        )

    @property
    def required_free_disk_reserve_bytes(self) -> int:
        policy_reserve = math.ceil(self.filesystem_total_bytes * 0.15)
        campaign_working_reserve = 2 * (
            self.test_representation_bytes + self.test_metric_bytes
        )
        return max(policy_reserve, campaign_working_reserve)

    @property
    def feasible(self) -> bool:
        return (
            self.measured_peak_rss_bytes < 22 * 1024**3
            and self.pilot_runtime_seconds <= 3 * 60 * 60
            and self.test_runtime_seconds <= 12 * 60 * 60
            and self.filesystem_free_bytes >= self.required_free_disk_reserve_bytes
        )

    def to_dict(self) -> dict[str, object]:
        """Return measured inputs, exact projections, and feasibility gates."""
        return {
            "schema_version": self.schema_version,
            "projection_policy": "linear scaling by frozen scenario count",
            "development": {
                "scenario_count": self.development_scenario_count,
                "method_count": self.method_count,
                "checkpoint_count": (
                    self.development_scenario_count * self.method_count
                ),
                "runtime_seconds": self.development_runtime_seconds,
                "representation_bytes": self.development_representation_bytes,
                "metric_artifact_bytes": self.development_metric_bytes,
                "measured_peak_rss_bytes": self.measured_peak_rss_bytes,
            },
            "pilot": {
                "scenario_count": 50,
                "checkpoint_count": 50 * self.method_count,
                "runtime_seconds": self.pilot_runtime_seconds,
                "representation_bytes": self.pilot_representation_bytes,
                "metric_artifact_bytes": self.pilot_metric_bytes,
            },
            "test": {
                "scenario_count": 300,
                "checkpoint_count": 300 * self.method_count,
                "runtime_seconds": self.test_runtime_seconds,
                "representation_bytes": self.test_representation_bytes,
                "metric_artifact_bytes": self.test_metric_bytes,
            },
            "bounded_development_validation": {
                "checkpoint_count": self.bounded_validation_checkpoint_count,
                "runtime_seconds": self.bounded_validation_seconds,
                "sequential": True,
                "worker_count": 1,
            },
            "filesystem": {
                "total_bytes": self.filesystem_total_bytes,
                "free_bytes": self.filesystem_free_bytes,
                "required_free_disk_reserve_bytes": (
                    self.required_free_disk_reserve_bytes
                ),
            },
            "feasibility": {
                "peak_rss_below_warning_threshold": (
                    self.measured_peak_rss_bytes < 22 * 1024**3
                ),
                "pilot_within_three_hours": (self.pilot_runtime_seconds <= 3 * 60 * 60),
                "test_job_within_twelve_hours": (
                    self.test_runtime_seconds <= 12 * 60 * 60
                ),
                "disk_reserve_available": (
                    self.filesystem_free_bytes >= self.required_free_disk_reserve_bytes
                ),
                "overall": self.feasible,
            },
        }
