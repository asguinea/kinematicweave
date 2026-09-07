"""Deterministic laptop-scale AV2 pilot planning and execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, cast
import unicodedata

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_text,
    check_disk_space,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.data import (
    av2_map,
    av2_motion,
    parquet_io,
    registry,
    schemas,
    validation,
)
from kinematicweave.data.materialization import (
    IncompleteEntryPolicy,
    MaterializationPlan,
    MaterializationRunReport,
    MaterializationUnitSpec,
    execute_materialization_plan,
    materialization_plan_identity,
)
from kinematicweave.domain import records
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.paths import normalize_relative_path
from kinematicweave.seeding import derive_seed, validate_root_seed

__all__ = [
    "Av2PilotArtifacts",
    "Av2PilotConfig",
    "Av2PilotExecution",
    "Av2PilotPlan",
    "Av2PilotReport",
    "Av2PilotResourceMeasurements",
    "Av2PilotSourcePair",
    "av2_pilot_config_to_dict",
    "av2_pilot_materialization_plan",
    "av2_pilot_plan_from_dict",
    "av2_pilot_plan_from_json",
    "av2_pilot_plan_identity",
    "av2_pilot_plan_to_canonical_json",
    "av2_pilot_report_from_dict",
    "av2_pilot_report_from_json",
    "av2_pilot_report_to_canonical_json",
    "av2_pilot_report_to_dict",
    "av2_pilot_resource_measurements_to_dict",
    "av2_pilot_source_pair_to_dict",
    "av2_pilot_summary_markdown",
    "build_av2_pilot_plan",
    "discover_av2_pilot_candidates",
    "execute_av2_pilot",
    "materialize_av2_pilot_artifacts",
    "verify_av2_pilot_artifacts",
    "verify_av2_pilot_sources",
]

_SCHEMA_VERSION = "1.0"
_DATASET_ID = "av2_motion"
_READ_CHUNK_SIZE = 1024 * 1024
_INT64_MAX = (1 << 63) - 1
_SHA256_HEX = frozenset("0123456789abcdef")
_MIB = 1_048_576
_OUTPUT_PATHS = (
    Path("motion_adapter_summary.json"),
    Path("map_adapter_summary.json"),
    Path("scenario_manifest.parquet"),
    Path("coordinate_frame_metadata.parquet"),
    Path("agent_metadata.parquet"),
    Path("trajectory_samples.parquet"),
    Path("vector_map_elements.parquet"),
)
_PARQUET_WRITE_OPTIONS: dict[str, object] = {
    "compression": "zstd",
    "compression_level": 3,
    "use_dictionary": False,
    "write_statistics": True,
    "version": "2.6",
    "data_page_version": "1.0",
    "use_compliant_nested_type": True,
    "store_schema": True,
}
_CONFIG_FIELDS = (
    "source_partition",
    "dataset_version",
    "canonical_split_name",
    "scenario_count",
    "root_seed",
    "assignment_namespace",
    "motion_adapter_version",
    "map_adapter_version",
    "inclusion_policy",
    "centerline_point_count",
    "minimum_valid_sample_count",
    "minimum_valid_duration_ns",
    "row_group_size",
    "validation_batch_size",
    "materialization_expansion_factor",
    "reserve_fraction",
)
_SOURCE_PAIR_FIELDS = (
    "source_scenario_id",
    "motion_relative_path",
    "map_relative_path",
    "motion_size_bytes",
    "map_size_bytes",
    "motion_sha256",
    "map_sha256",
)
_PLAN_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "source_partition",
    "source_manifest_identity",
    "config",
    "candidate_count",
    "selected_scenarios",
)
_RESOURCE_FIELDS = (
    "source_verification_seconds",
    "materialization_seconds",
    "validation_seconds",
    "total_seconds",
    "selected_source_bytes",
    "cache_output_bytes",
    "disk_free_before_bytes",
    "disk_free_after_bytes",
)
_REPORT_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "plan_identity",
    "materialization_plan_identity",
    "validation_report_identity",
    "selected_scenario_ids",
    "candidate_scenario_count",
    "selected_scenario_count",
    "materialized_scenario_count",
    "reused_scenario_count",
    "source_scenario_count",
    "source_coordinate_frame_count",
    "source_agent_count",
    "source_trajectory_count",
    "source_sample_count",
    "source_map_element_count",
    "included_scenario_count",
    "included_agent_count",
    "included_trajectory_count",
    "included_map_element_count",
    "exclusion_count",
    "cache_relative_root",
    "cache_entry_directories",
    "resources",
)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty trimmed text")
    return value.strip()


def _source_identifier(value: object, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if ":" in normalized or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise ValidationError(
            f"{field_name} must not contain colons or control characters"
        )
    return normalized


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


def _positive_int(value: object, field_name: str) -> int:
    normalized = _nonnegative_int(value, field_name)
    if normalized == 0:
        raise ValidationError(f"{field_name} must be positive")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_HEX for character in value)
    ):
        raise ValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return value


def _finite_nonnegative_number(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a finite nonnegative number")
    try:
        normalized = float(value)
    except OverflowError:
        raise ValidationError(
            f"{field_name} must be a finite nonnegative number"
        ) from None
    if not math.isfinite(normalized) or normalized < 0:
        raise ValidationError(f"{field_name} must be a finite nonnegative number")
    return normalized


def _reserve_fraction(value: object) -> float:
    normalized = _finite_nonnegative_number(value, "reserve_fraction")
    if normalized >= 1:
        raise ValidationError("reserve_fraction must be less than one")
    return normalized


def _inclusion_policy(value: object) -> av2_motion.Av2AgentInclusionPolicy:
    if isinstance(value, av2_motion.Av2AgentInclusionPolicy):
        return value
    if isinstance(value, bool):
        raise ValidationError("inclusion_policy must use Av2AgentInclusionPolicy")
    try:
        return av2_motion.Av2AgentInclusionPolicy(cast(Any, value))
    except (TypeError, ValueError):
        raise ValidationError(
            "inclusion_policy must use Av2AgentInclusionPolicy"
        ) from None


def _normalized_path(value: object, field_name: str) -> Path:
    try:
        return normalize_relative_path(cast(str | Path, value))
    except (TypeError, ValidationError):
        raise ValidationError(
            f"{field_name} must be a repository-relative path"
        ) from None


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, Path)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a finite non-string sequence")
    return cast(Sequence[object], value)


def _exact_mapping(
    value: object,
    expected_fields: tuple[str, ...],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SchemaError(f"{label} must be a JSON object")
    mapping = cast(Mapping[str, object], value)
    unknown = tuple(key for key in mapping if key not in expected_fields)
    missing = tuple(key for key in expected_fields if key not in mapping)
    if unknown:
        raise SchemaError(f"{label} contains unknown field: {unknown[0]}")
    if missing:
        raise SchemaError(f"{label} is missing field: {missing[0]}")
    return mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class Av2PilotConfig:
    """Validated configuration for one bounded AV2 pilot."""

    source_partition: Path
    dataset_version: str
    canonical_split_name: str = "pilot"
    scenario_count: int
    root_seed: int
    assignment_namespace: str = "av2-real-data-pilot-v1"
    motion_adapter_version: str = "1.0"
    map_adapter_version: str = "1.0"
    inclusion_policy: av2_motion.Av2AgentInclusionPolicy | str = (
        av2_motion.Av2AgentInclusionPolicy.DYNAMIC_ONLY
    )
    centerline_point_count: int = 50
    minimum_valid_sample_count: int = 10
    minimum_valid_duration_ns: int = 1_000_000_000
    row_group_size: int = 65_536
    validation_batch_size: int = 65_536
    materialization_expansion_factor: float = 1.5
    reserve_fraction: float = 0.15

    def __post_init__(self) -> None:
        """Normalize values and reject unsafe resource parameters."""
        object.__setattr__(
            self,
            "source_partition",
            _normalized_path(self.source_partition, "source_partition"),
        )
        for field_name in (
            "dataset_version",
            "canonical_split_name",
            "assignment_namespace",
            "motion_adapter_version",
            "map_adapter_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "scenario_count",
            _positive_int(self.scenario_count, "scenario_count"),
        )
        object.__setattr__(self, "root_seed", validate_root_seed(self.root_seed))
        object.__setattr__(
            self,
            "inclusion_policy",
            _inclusion_policy(self.inclusion_policy),
        )
        point_count = _nonnegative_int(
            self.centerline_point_count,
            "centerline_point_count",
        )
        if point_count < 2:
            raise ValidationError("centerline_point_count must be at least two")
        object.__setattr__(self, "centerline_point_count", point_count)
        object.__setattr__(
            self,
            "minimum_valid_sample_count",
            _positive_int(
                self.minimum_valid_sample_count,
                "minimum_valid_sample_count",
            ),
        )
        duration = _nonnegative_int(
            self.minimum_valid_duration_ns,
            "minimum_valid_duration_ns",
        )
        if duration > _INT64_MAX:
            raise ValidationError("minimum_valid_duration_ns must fit signed int64")
        object.__setattr__(self, "minimum_valid_duration_ns", duration)
        for field_name in ("row_group_size", "validation_batch_size"):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "materialization_expansion_factor",
            _finite_nonnegative_number(
                self.materialization_expansion_factor,
                "materialization_expansion_factor",
            ),
        )
        object.__setattr__(
            self,
            "reserve_fraction",
            _reserve_fraction(self.reserve_fraction),
        )


@dataclass(frozen=True, slots=True)
class Av2PilotSourcePair:
    """One manifest-backed AV2 motion and vector-map source pair."""

    source_scenario_id: str
    motion_relative_path: Path
    map_relative_path: Path
    motion_size_bytes: int
    map_size_bytes: int
    motion_sha256: str
    map_sha256: str

    def __post_init__(self) -> None:
        """Normalize and validate exact paired-source layout."""
        source_id = _source_identifier(
            self.source_scenario_id,
            "source_scenario_id",
        )
        motion = _normalized_path(self.motion_relative_path, "motion_relative_path")
        vector_map = _normalized_path(self.map_relative_path, "map_relative_path")
        if motion == vector_map:
            raise ValidationError("motion and map paths must differ")
        if motion.name != f"scenario_{source_id}.parquet":
            raise ValidationError("motion filename differs from source_scenario_id")
        if vector_map.name != f"log_map_archive_{source_id}.json":
            raise ValidationError("map filename differs from source_scenario_id")
        if motion.parent != vector_map.parent:
            raise ValidationError("motion and map paths must share one parent")
        if motion.parent.name != source_id:
            raise ValidationError(
                "source parent directory must equal source_scenario_id"
            )
        object.__setattr__(self, "source_scenario_id", source_id)
        object.__setattr__(self, "motion_relative_path", motion)
        object.__setattr__(self, "map_relative_path", vector_map)
        object.__setattr__(
            self,
            "motion_size_bytes",
            _nonnegative_int(self.motion_size_bytes, "motion_size_bytes"),
        )
        object.__setattr__(
            self,
            "map_size_bytes",
            _nonnegative_int(self.map_size_bytes, "map_size_bytes"),
        )
        object.__setattr__(
            self,
            "motion_sha256",
            _sha256(self.motion_sha256, "motion_sha256"),
        )
        object.__setattr__(
            self,
            "map_sha256",
            _sha256(self.map_sha256, "map_sha256"),
        )

    @property
    def total_source_bytes(self) -> int:
        """Return exact bytes in both selected source files."""
        return self.motion_size_bytes + self.map_size_bytes


@dataclass(frozen=True, slots=True)
class Av2PilotPlan:
    """Immutable deterministic AV2 pilot selection."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    source_partition: Path
    source_manifest_identity: str
    config: Av2PilotConfig
    candidate_count: int
    selected_scenarios: tuple[Av2PilotSourcePair, ...]

    def __post_init__(self) -> None:
        """Copy selected pairs and enforce plan/config agreement."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        if self.dataset_id != _DATASET_ID:
            raise ValidationError(f"dataset_id must equal {_DATASET_ID!r}")
        if not isinstance(self.config, Av2PilotConfig):
            raise ValidationError("config must be Av2PilotConfig")
        dataset_version = _required_text(self.dataset_version, "dataset_version")
        partition = _normalized_path(self.source_partition, "source_partition")
        if dataset_version != self.config.dataset_version:
            raise ValidationError("dataset_version differs from config")
        if partition != self.config.source_partition:
            raise ValidationError("source_partition differs from config")
        selected = tuple(_sequence(self.selected_scenarios, "selected_scenarios"))
        if not selected or any(
            not isinstance(item, Av2PilotSourcePair) for item in selected
        ):
            raise ValidationError(
                "selected_scenarios must contain Av2PilotSourcePair values"
            )
        typed = cast(tuple[Av2PilotSourcePair, ...], selected)
        if len(typed) != self.config.scenario_count:
            raise ValidationError("selected count must equal config.scenario_count")
        for values, label in (
            (tuple(item.source_scenario_id for item in typed), "scenario IDs"),
            (tuple(item.motion_relative_path for item in typed), "motion paths"),
            (tuple(item.map_relative_path for item in typed), "map paths"),
        ):
            if len(values) != len(set(values)):
                raise ValidationError(f"selected {label} must be unique")
        candidates = _nonnegative_int(self.candidate_count, "candidate_count")
        if candidates < len(typed):
            raise ValidationError("candidate_count must cover selected scenarios")
        object.__setattr__(self, "dataset_version", dataset_version)
        object.__setattr__(self, "source_partition", partition)
        object.__setattr__(
            self,
            "source_manifest_identity",
            _sha256(self.source_manifest_identity, "source_manifest_identity"),
        )
        object.__setattr__(self, "candidate_count", candidates)
        object.__setattr__(self, "selected_scenarios", typed)

    @property
    def selected_scenario_count(self) -> int:
        """Return the exact selected scenario count."""
        return len(self.selected_scenarios)

    @property
    def selected_motion_bytes(self) -> int:
        """Return selected AV2 motion-source bytes."""
        return sum(item.motion_size_bytes for item in self.selected_scenarios)

    @property
    def selected_map_bytes(self) -> int:
        """Return selected AV2 vector-map source bytes."""
        return sum(item.map_size_bytes for item in self.selected_scenarios)

    @property
    def selected_total_source_bytes(self) -> int:
        """Return selected motion and map bytes."""
        return self.selected_motion_bytes + self.selected_map_bytes


@dataclass(frozen=True, slots=True)
class Av2PilotResourceMeasurements:
    """Measured stage durations and exact pilot byte accounting."""

    source_verification_seconds: float
    materialization_seconds: float
    validation_seconds: float
    total_seconds: float
    selected_source_bytes: int
    cache_output_bytes: int
    disk_free_before_bytes: int
    disk_free_after_bytes: int

    def __post_init__(self) -> None:
        """Normalize finite durations and nonnegative byte counts."""
        for field_name in (
            "source_verification_seconds",
            "materialization_seconds",
            "validation_seconds",
            "total_seconds",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_nonnegative_number(getattr(self, field_name), field_name),
            )
        if self.total_seconds < max(
            self.source_verification_seconds,
            self.materialization_seconds,
            self.validation_seconds,
        ):
            raise ValidationError("total_seconds must cover every stage duration")
        for field_name in (
            "selected_source_bytes",
            "cache_output_bytes",
            "disk_free_before_bytes",
            "disk_free_after_bytes",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class Av2PilotReport:
    """Quantitative result of one completed AV2 pilot execution."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    plan_identity: str
    materialization_plan_identity: str
    validation_report_identity: str
    selected_scenario_ids: tuple[str, ...]
    candidate_scenario_count: int
    selected_scenario_count: int
    materialized_scenario_count: int
    reused_scenario_count: int
    source_scenario_count: int
    source_coordinate_frame_count: int
    source_agent_count: int
    source_trajectory_count: int
    source_sample_count: int
    source_map_element_count: int
    included_scenario_count: int
    included_agent_count: int
    included_trajectory_count: int
    included_map_element_count: int
    exclusion_count: int
    cache_relative_root: Path
    cache_entry_directories: tuple[Path, ...]
    resources: Av2PilotResourceMeasurements

    def __post_init__(self) -> None:
        """Validate counts, identities, ordered paths, and measurements."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        if self.dataset_id != _DATASET_ID:
            raise ValidationError(f"dataset_id must equal {_DATASET_ID!r}")
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        for field_name in (
            "plan_identity",
            "materialization_plan_identity",
            "validation_report_identity",
        ):
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), field_name),
            )
        selected_ids = tuple(
            _source_identifier(item, "selected_scenario_ids item")
            for item in _sequence(self.selected_scenario_ids, "selected_scenario_ids")
        )
        if not selected_ids or len(selected_ids) != len(set(selected_ids)):
            raise ValidationError("selected_scenario_ids must be nonempty and unique")
        object.__setattr__(self, "selected_scenario_ids", selected_ids)
        for field_name in (
            "candidate_scenario_count",
            "selected_scenario_count",
            "materialized_scenario_count",
            "reused_scenario_count",
            "source_scenario_count",
            "source_coordinate_frame_count",
            "source_agent_count",
            "source_trajectory_count",
            "source_sample_count",
            "source_map_element_count",
            "included_scenario_count",
            "included_agent_count",
            "included_trajectory_count",
            "included_map_element_count",
            "exclusion_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        if self.selected_scenario_count != len(selected_ids):
            raise ValidationError(
                "selected_scenario_count must equal selected_scenario_ids"
            )
        if self.candidate_scenario_count < self.selected_scenario_count:
            raise ValidationError("candidate count must cover selected scenarios")
        if (
            self.materialized_scenario_count + self.reused_scenario_count
            != self.selected_scenario_count
        ):
            raise ValidationError(
                "materialized and reused counts must equal selected count"
            )
        if (
            self.source_scenario_count != self.selected_scenario_count
            or self.source_coordinate_frame_count != self.selected_scenario_count
        ):
            raise ValidationError(
                "source scenario and frame counts must equal selected count"
            )
        for included, source, label in (
            (
                self.included_scenario_count,
                self.source_scenario_count,
                "scenario",
            ),
            (self.included_agent_count, self.source_agent_count, "agent"),
            (
                self.included_trajectory_count,
                self.source_trajectory_count,
                "trajectory",
            ),
            (
                self.included_map_element_count,
                self.source_map_element_count,
                "map element",
            ),
        ):
            if included > source:
                raise ValidationError(f"included {label} count exceeds source count")
        root = _normalized_path(self.cache_relative_root, "cache_relative_root")
        entries = tuple(
            _normalized_path(item, "cache_entry_directories item")
            for item in _sequence(
                self.cache_entry_directories,
                "cache_entry_directories",
            )
        )
        if len(entries) != len(set(entries)):
            raise ValidationError("cache entry directories must be unique")
        if len(entries) != self.selected_scenario_count:
            raise ValidationError(
                "cache entry directory count must equal selected count"
            )
        if any(not path.is_relative_to(root) for path in entries):
            raise ValidationError("cache entry directories must be beneath cache root")
        if not isinstance(self.resources, Av2PilotResourceMeasurements):
            raise ValidationError("resources must be Av2PilotResourceMeasurements")
        object.__setattr__(self, "cache_relative_root", root)
        object.__setattr__(self, "cache_entry_directories", entries)

    @property
    def is_eligible(self) -> bool:
        """Return whether validated data retains scenarios and trajectories."""
        return bool(self.included_scenario_count and self.included_trajectory_count)

    @property
    def scenarios_per_second(self) -> float | None:
        """Return measured throughput when total duration is positive."""
        if self.resources.total_seconds == 0:
            return None
        return self.selected_scenario_count / self.resources.total_seconds

    @property
    def source_mebibytes(self) -> float:
        """Return selected source volume in mebibytes."""
        return self.resources.selected_source_bytes / _MIB

    @property
    def output_mebibytes(self) -> float:
        """Return immutable cache output volume in mebibytes."""
        return self.resources.cache_output_bytes / _MIB


@dataclass(frozen=True, slots=True)
class Av2PilotExecution:
    """Cross-validated in-memory result of one pilot execution."""

    plan: Av2PilotPlan
    materialization_plan: MaterializationPlan
    materialization_report: MaterializationRunReport
    dataset_paths: validation.CanonicalDatasetPaths
    validation_report: validation.CanonicalValidationReport
    report: Av2PilotReport

    def __post_init__(self) -> None:
        """Enforce agreement among plan, cache, validation, and pilot report."""
        if not isinstance(self.plan, Av2PilotPlan):
            raise ValidationError("plan must be Av2PilotPlan")
        if not isinstance(self.materialization_plan, MaterializationPlan):
            raise ValidationError("materialization_plan must be MaterializationPlan")
        if not isinstance(self.materialization_report, MaterializationRunReport):
            raise ValidationError(
                "materialization_report must be MaterializationRunReport"
            )
        if not isinstance(self.dataset_paths, validation.CanonicalDatasetPaths):
            raise ValidationError("dataset_paths must be CanonicalDatasetPaths")
        if not isinstance(
            self.validation_report,
            validation.CanonicalValidationReport,
        ):
            raise ValidationError("validation_report must be CanonicalValidationReport")
        if not isinstance(self.report, Av2PilotReport):
            raise ValidationError("report must be Av2PilotReport")
        dataset_values = (
            self.plan.dataset_id,
            self.materialization_plan.dataset_id,
            self.materialization_report.dataset_id,
            self.validation_report.dataset_id,
            self.report.dataset_id,
        )
        version_values = (
            self.plan.dataset_version,
            self.materialization_plan.dataset_version,
            self.materialization_report.dataset_version,
            self.validation_report.dataset_version,
            self.report.dataset_version,
        )
        if len(set(dataset_values)) != 1 or len(set(version_values)) != 1:
            raise ValidationError("execution dataset identity is inconsistent")
        if self.materialization_plan != av2_pilot_materialization_plan(self.plan):
            raise ValidationError(
                "materialization_plan differs from the deterministic pilot plan"
            )
        expected_materialization_identity = materialization_plan_identity(
            self.materialization_plan
        )
        if (
            self.materialization_report.plan_identity
            != expected_materialization_identity
            or self.report.materialization_plan_identity
            != expected_materialization_identity
        ):
            raise ValidationError("materialization plan identity is inconsistent")
        expected_units = tuple(unit.unit_id for unit in self.materialization_plan.units)
        result_units = tuple(
            result.unit_id for result in self.materialization_report.results
        )
        if expected_units != result_units:
            raise ValidationError("materialization result order differs from plan")
        entries = tuple(
            normalize_relative_path(result.entry_relative_directory)
            for result in self.materialization_report.results
        )
        expected_paths = _dataset_paths_from_entries(entries)
        if self.dataset_paths != expected_paths:
            raise ValidationError(
                "dataset paths differ from materialization entry order"
            )
        validation_identity = canonical_sha256(
            "canonical-validation-report",
            validation.canonical_validation_report_to_dict(self.validation_report),
        )
        if self.report.plan_identity != av2_pilot_plan_identity(self.plan):
            raise ValidationError("pilot plan identity is inconsistent")
        if self.report.validation_report_identity != validation_identity:
            raise ValidationError("validation report identity is inconsistent")
        selected_ids = tuple(
            item.source_scenario_id for item in self.plan.selected_scenarios
        )
        if self.report.selected_scenario_ids != selected_ids:
            raise ValidationError("selected scenario order is inconsistent")
        if (
            self.report.candidate_scenario_count != self.plan.candidate_count
            or self.report.selected_scenario_count != self.plan.selected_scenario_count
        ):
            raise ValidationError("pilot report selection counts are inconsistent")
        if self.report.resources.selected_source_bytes != (
            self.plan.selected_total_source_bytes
        ):
            raise ValidationError("selected source-byte accounting is inconsistent")
        if (
            self.report.materialized_scenario_count
            != self.materialization_report.materialized_count
            or self.report.reused_scenario_count
            != self.materialization_report.reused_count
            or self.report.resources.cache_output_bytes
            != self.materialization_report.total_output_bytes
            or self.report.cache_relative_root
            != self.materialization_report.cache_relative_root
            or self.report.cache_entry_directories != entries
        ):
            raise ValidationError("materialization report fields are inconsistent")
        expected_validation_config = validation.CanonicalValidationConfig(
            minimum_valid_sample_count=self.plan.config.minimum_valid_sample_count,
            minimum_valid_duration_ns=self.plan.config.minimum_valid_duration_ns,
            allowed_agent_classes=tuple(records.AgentClass),
            require_source_map=True,
        )
        if self.validation_report.config != expected_validation_config:
            raise ValidationError(
                "validation configuration differs from the pilot plan"
            )
        _validate_report_against_validation(self.report, self.validation_report)


@dataclass(frozen=True, slots=True)
class Av2PilotArtifacts:
    """Physical plan, report, and Markdown pilot artifacts."""

    pilot_plan: WrittenArtifact
    pilot_report: WrittenArtifact
    pilot_summary: WrittenArtifact

    def __post_init__(self) -> None:
        """Require unique WrittenArtifact paths."""
        artifacts = (self.pilot_plan, self.pilot_report, self.pilot_summary)
        if any(not isinstance(item, WrittenArtifact) for item in artifacts):
            raise ValidationError("pilot artifacts must use WrittenArtifact")
        if len({item.relative_path for item in artifacts}) != len(artifacts):
            raise ValidationError("pilot artifact paths must be unique")


def av2_pilot_config_to_dict(config: Av2PilotConfig) -> dict[str, object]:
    """Return a fresh ordered plain representation of pilot configuration."""
    if not isinstance(config, Av2PilotConfig):
        raise ValidationError("config must be Av2PilotConfig")
    return {
        "source_partition": config.source_partition.as_posix(),
        "dataset_version": config.dataset_version,
        "canonical_split_name": config.canonical_split_name,
        "scenario_count": config.scenario_count,
        "root_seed": config.root_seed,
        "assignment_namespace": config.assignment_namespace,
        "motion_adapter_version": config.motion_adapter_version,
        "map_adapter_version": config.map_adapter_version,
        "inclusion_policy": _inclusion_policy(config.inclusion_policy).value,
        "centerline_point_count": config.centerline_point_count,
        "minimum_valid_sample_count": config.minimum_valid_sample_count,
        "minimum_valid_duration_ns": config.minimum_valid_duration_ns,
        "row_group_size": config.row_group_size,
        "validation_batch_size": config.validation_batch_size,
        "materialization_expansion_factor": (config.materialization_expansion_factor),
        "reserve_fraction": config.reserve_fraction,
    }


def av2_pilot_source_pair_to_dict(
    pair: Av2PilotSourcePair,
) -> dict[str, object]:
    """Return a fresh ordered plain representation of one source pair."""
    if not isinstance(pair, Av2PilotSourcePair):
        raise ValidationError("pair must be Av2PilotSourcePair")
    return {
        "source_scenario_id": pair.source_scenario_id,
        "motion_relative_path": pair.motion_relative_path.as_posix(),
        "map_relative_path": pair.map_relative_path.as_posix(),
        "motion_size_bytes": pair.motion_size_bytes,
        "map_size_bytes": pair.map_size_bytes,
        "motion_sha256": pair.motion_sha256,
        "map_sha256": pair.map_sha256,
    }


def av2_pilot_plan_to_dict(plan: Av2PilotPlan) -> dict[str, object]:
    """Return a fresh ordered plain representation of a pilot plan."""
    if not isinstance(plan, Av2PilotPlan):
        raise ValidationError("plan must be Av2PilotPlan")
    return {
        "schema_version": plan.schema_version,
        "dataset_id": plan.dataset_id,
        "dataset_version": plan.dataset_version,
        "source_partition": plan.source_partition.as_posix(),
        "source_manifest_identity": plan.source_manifest_identity,
        "config": av2_pilot_config_to_dict(plan.config),
        "candidate_count": plan.candidate_count,
        "selected_scenarios": [
            av2_pilot_source_pair_to_dict(item) for item in plan.selected_scenarios
        ],
    }


def av2_pilot_resource_measurements_to_dict(
    resources: Av2PilotResourceMeasurements,
) -> dict[str, object]:
    """Return a fresh ordered plain representation of measured resources."""
    if not isinstance(resources, Av2PilotResourceMeasurements):
        raise ValidationError("resources must be Av2PilotResourceMeasurements")
    return {
        "source_verification_seconds": resources.source_verification_seconds,
        "materialization_seconds": resources.materialization_seconds,
        "validation_seconds": resources.validation_seconds,
        "total_seconds": resources.total_seconds,
        "selected_source_bytes": resources.selected_source_bytes,
        "cache_output_bytes": resources.cache_output_bytes,
        "disk_free_before_bytes": resources.disk_free_before_bytes,
        "disk_free_after_bytes": resources.disk_free_after_bytes,
    }


def av2_pilot_report_to_dict(report: Av2PilotReport) -> dict[str, object]:
    """Return a fresh ordered plain representation of a pilot report."""
    if not isinstance(report, Av2PilotReport):
        raise ValidationError("report must be Av2PilotReport")
    return {
        "schema_version": report.schema_version,
        "dataset_id": report.dataset_id,
        "dataset_version": report.dataset_version,
        "plan_identity": report.plan_identity,
        "materialization_plan_identity": report.materialization_plan_identity,
        "validation_report_identity": report.validation_report_identity,
        "selected_scenario_ids": list(report.selected_scenario_ids),
        "candidate_scenario_count": report.candidate_scenario_count,
        "selected_scenario_count": report.selected_scenario_count,
        "materialized_scenario_count": report.materialized_scenario_count,
        "reused_scenario_count": report.reused_scenario_count,
        "source_scenario_count": report.source_scenario_count,
        "source_coordinate_frame_count": report.source_coordinate_frame_count,
        "source_agent_count": report.source_agent_count,
        "source_trajectory_count": report.source_trajectory_count,
        "source_sample_count": report.source_sample_count,
        "source_map_element_count": report.source_map_element_count,
        "included_scenario_count": report.included_scenario_count,
        "included_agent_count": report.included_agent_count,
        "included_trajectory_count": report.included_trajectory_count,
        "included_map_element_count": report.included_map_element_count,
        "exclusion_count": report.exclusion_count,
        "cache_relative_root": report.cache_relative_root.as_posix(),
        "cache_entry_directories": [
            path.as_posix() for path in report.cache_entry_directories
        ],
        "resources": av2_pilot_resource_measurements_to_dict(report.resources),
    }


def av2_pilot_plan_to_canonical_json(plan: Av2PilotPlan) -> str:
    """Serialize a pilot plan as canonical JSON with one newline."""
    return canonical_json_text(av2_pilot_plan_to_dict(plan))


def av2_pilot_report_to_canonical_json(report: Av2PilotReport) -> str:
    """Serialize a pilot report as canonical JSON with one newline."""
    return canonical_json_text(av2_pilot_report_to_dict(report))


def _config_from_mapping(value: object) -> Av2PilotConfig:
    mapping = _exact_mapping(value, _CONFIG_FIELDS, "pilot config")
    return Av2PilotConfig(
        source_partition=cast(Any, mapping["source_partition"]),
        dataset_version=cast(Any, mapping["dataset_version"]),
        canonical_split_name=cast(Any, mapping["canonical_split_name"]),
        scenario_count=cast(Any, mapping["scenario_count"]),
        root_seed=cast(Any, mapping["root_seed"]),
        assignment_namespace=cast(Any, mapping["assignment_namespace"]),
        motion_adapter_version=cast(Any, mapping["motion_adapter_version"]),
        map_adapter_version=cast(Any, mapping["map_adapter_version"]),
        inclusion_policy=cast(Any, mapping["inclusion_policy"]),
        centerline_point_count=cast(Any, mapping["centerline_point_count"]),
        minimum_valid_sample_count=cast(
            Any,
            mapping["minimum_valid_sample_count"],
        ),
        minimum_valid_duration_ns=cast(
            Any,
            mapping["minimum_valid_duration_ns"],
        ),
        row_group_size=cast(Any, mapping["row_group_size"]),
        validation_batch_size=cast(Any, mapping["validation_batch_size"]),
        materialization_expansion_factor=cast(
            Any,
            mapping["materialization_expansion_factor"],
        ),
        reserve_fraction=cast(Any, mapping["reserve_fraction"]),
    )


def _source_pair_from_mapping(value: object) -> Av2PilotSourcePair:
    mapping = _exact_mapping(value, _SOURCE_PAIR_FIELDS, "pilot source pair")
    return Av2PilotSourcePair(
        source_scenario_id=cast(Any, mapping["source_scenario_id"]),
        motion_relative_path=cast(Any, mapping["motion_relative_path"]),
        map_relative_path=cast(Any, mapping["map_relative_path"]),
        motion_size_bytes=cast(Any, mapping["motion_size_bytes"]),
        map_size_bytes=cast(Any, mapping["map_size_bytes"]),
        motion_sha256=cast(Any, mapping["motion_sha256"]),
        map_sha256=cast(Any, mapping["map_sha256"]),
    )


def av2_pilot_plan_from_dict(value: Mapping[str, object]) -> Av2PilotPlan:
    """Reconstruct and strictly validate a pilot plan mapping."""
    mapping = _exact_mapping(value, _PLAN_FIELDS, "pilot plan")
    selected = mapping["selected_scenarios"]
    if not isinstance(selected, list):
        raise SchemaError("selected_scenarios must be a JSON array")
    try:
        return Av2PilotPlan(
            schema_version=cast(Any, mapping["schema_version"]),
            dataset_id=cast(Any, mapping["dataset_id"]),
            dataset_version=cast(Any, mapping["dataset_version"]),
            source_partition=cast(Any, mapping["source_partition"]),
            source_manifest_identity=cast(
                Any,
                mapping["source_manifest_identity"],
            ),
            config=_config_from_mapping(mapping["config"]),
            candidate_count=cast(Any, mapping["candidate_count"]),
            selected_scenarios=tuple(
                _source_pair_from_mapping(item) for item in selected
            ),
        )
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def _resources_from_mapping(value: object) -> Av2PilotResourceMeasurements:
    mapping = _exact_mapping(value, _RESOURCE_FIELDS, "pilot resources")
    return Av2PilotResourceMeasurements(
        source_verification_seconds=cast(
            Any,
            mapping["source_verification_seconds"],
        ),
        materialization_seconds=cast(
            Any,
            mapping["materialization_seconds"],
        ),
        validation_seconds=cast(Any, mapping["validation_seconds"]),
        total_seconds=cast(Any, mapping["total_seconds"]),
        selected_source_bytes=cast(Any, mapping["selected_source_bytes"]),
        cache_output_bytes=cast(Any, mapping["cache_output_bytes"]),
        disk_free_before_bytes=cast(Any, mapping["disk_free_before_bytes"]),
        disk_free_after_bytes=cast(Any, mapping["disk_free_after_bytes"]),
    )


def av2_pilot_report_from_dict(
    value: Mapping[str, object],
) -> Av2PilotReport:
    """Reconstruct and strictly validate a pilot report mapping."""
    mapping = _exact_mapping(value, _REPORT_FIELDS, "pilot report")
    try:
        return Av2PilotReport(
            schema_version=cast(Any, mapping["schema_version"]),
            dataset_id=cast(Any, mapping["dataset_id"]),
            dataset_version=cast(Any, mapping["dataset_version"]),
            plan_identity=cast(Any, mapping["plan_identity"]),
            materialization_plan_identity=cast(
                Any,
                mapping["materialization_plan_identity"],
            ),
            validation_report_identity=cast(
                Any,
                mapping["validation_report_identity"],
            ),
            selected_scenario_ids=cast(
                Any,
                mapping["selected_scenario_ids"],
            ),
            candidate_scenario_count=cast(
                Any,
                mapping["candidate_scenario_count"],
            ),
            selected_scenario_count=cast(
                Any,
                mapping["selected_scenario_count"],
            ),
            materialized_scenario_count=cast(
                Any,
                mapping["materialized_scenario_count"],
            ),
            reused_scenario_count=cast(
                Any,
                mapping["reused_scenario_count"],
            ),
            source_scenario_count=cast(
                Any,
                mapping["source_scenario_count"],
            ),
            source_coordinate_frame_count=cast(
                Any,
                mapping["source_coordinate_frame_count"],
            ),
            source_agent_count=cast(Any, mapping["source_agent_count"]),
            source_trajectory_count=cast(
                Any,
                mapping["source_trajectory_count"],
            ),
            source_sample_count=cast(Any, mapping["source_sample_count"]),
            source_map_element_count=cast(
                Any,
                mapping["source_map_element_count"],
            ),
            included_scenario_count=cast(
                Any,
                mapping["included_scenario_count"],
            ),
            included_agent_count=cast(
                Any,
                mapping["included_agent_count"],
            ),
            included_trajectory_count=cast(
                Any,
                mapping["included_trajectory_count"],
            ),
            included_map_element_count=cast(
                Any,
                mapping["included_map_element_count"],
            ),
            exclusion_count=cast(Any, mapping["exclusion_count"]),
            cache_relative_root=cast(Any, mapping["cache_relative_root"]),
            cache_entry_directories=cast(
                Any,
                mapping["cache_entry_directories"],
            ),
            resources=_resources_from_mapping(mapping["resources"]),
        )
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def _json_mapping(text: str, label: str) -> Mapping[str, object]:
    if not isinstance(text, str):
        raise SchemaError(f"{label} JSON must be text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError(f"{label} JSON is malformed") from None
    if not isinstance(value, Mapping):
        raise SchemaError(f"{label} JSON root must be an object")
    return cast(Mapping[str, object], value)


def av2_pilot_plan_from_json(text: str) -> Av2PilotPlan:
    """Parse and strictly validate pilot plan JSON."""
    return av2_pilot_plan_from_dict(_json_mapping(text, "pilot plan"))


def av2_pilot_report_from_json(text: str) -> Av2PilotReport:
    """Parse and strictly validate pilot report JSON."""
    return av2_pilot_report_from_dict(_json_mapping(text, "pilot report"))


def discover_av2_pilot_candidates(
    manifest: registry.DatasetSourceManifest,
    *,
    config: Av2PilotConfig,
) -> tuple[Av2PilotSourcePair, ...]:
    """Return complete manifest-only AV2 pairs in lexical scenario order."""
    if not isinstance(manifest, registry.DatasetSourceManifest):
        raise ValidationError("manifest must be DatasetSourceManifest")
    if not isinstance(config, Av2PilotConfig):
        raise ValidationError("config must be Av2PilotConfig")
    checks = (
        (manifest.dataset_id == _DATASET_ID, "manifest dataset_id must be av2_motion"),
        (
            manifest.dataset_version == config.dataset_version,
            "manifest dataset_version differs from config",
        ),
        (
            manifest.source_kind is registry.DatasetSourceKind.EXTERNAL_DIRECTORY,
            "manifest source_kind must be external_directory",
        ),
        (
            manifest.availability is registry.DatasetAvailability.AVAILABLE,
            "manifest availability must be available",
        ),
        (
            manifest.checksum_mode is registry.SourceChecksumMode.SHA256,
            "manifest checksum_mode must be sha256",
        ),
        (
            all(item.sha256 is not None for item in manifest.files),
            "every manifest file must have a checksum",
        ),
    )
    for valid, message in checks:
        if not valid:
            raise ValidationError(message)

    indexed: dict[str, dict[str, object]] = {}
    partition = config.source_partition
    for source_file in manifest.files:
        path = normalize_relative_path(source_file.relative_path)
        try:
            relative = path.relative_to(partition)
        except ValueError:
            continue
        if len(relative.parts) != 2:
            continue
        scenario_id = _source_identifier(relative.parts[0], "source scenario ID")
        filename = relative.name
        motion_name = f"scenario_{scenario_id}.parquet"
        map_name = f"log_map_archive_{scenario_id}.json"
        kind: str | None = None
        if filename.startswith("scenario_") and filename.endswith(".parquet"):
            if filename != motion_name:
                raise ValidationError(
                    "motion filename differs from its scenario directory"
                )
            kind = "motion"
        elif filename.startswith("log_map_archive_") and filename.endswith(".json"):
            if filename != map_name:
                raise ValidationError(
                    "map filename differs from its scenario directory"
                )
            kind = "map"
        if kind is None:
            continue
        values = indexed.setdefault(scenario_id, {})
        if kind in values:
            raise ValidationError(f"duplicate {kind} source for scenario")
        values[kind] = source_file

    candidates: list[Av2PilotSourcePair] = []
    for scenario_id in sorted(indexed):
        values = indexed[scenario_id]
        if "motion" not in values or "map" not in values:
            continue
        motion = cast(Any, values["motion"])
        vector_map = cast(Any, values["map"])
        candidates.append(
            Av2PilotSourcePair(
                source_scenario_id=scenario_id,
                motion_relative_path=motion.relative_path,
                map_relative_path=vector_map.relative_path,
                motion_size_bytes=motion.size_bytes,
                map_size_bytes=vector_map.size_bytes,
                motion_sha256=cast(str, motion.sha256),
                map_sha256=cast(str, vector_map.sha256),
            )
        )
    return tuple(candidates)


def build_av2_pilot_plan(
    manifest: registry.DatasetSourceManifest,
    *,
    config: Av2PilotConfig,
) -> Av2PilotPlan:
    """Select an exact deterministic manifest-backed AV2 pilot."""
    candidates = discover_av2_pilot_candidates(manifest, config=config)
    if len(candidates) < config.scenario_count:
        raise ValidationError("insufficient complete AV2 pilot candidates")
    ranked = tuple(
        sorted(
            candidates,
            key=lambda pair: (
                derive_seed(
                    config.root_seed,
                    "av2-real-data-pilot",
                    config.assignment_namespace,
                    pair.source_scenario_id,
                ),
                pair.source_scenario_id,
            ),
        )
    )
    return Av2PilotPlan(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=config.dataset_version,
        source_partition=config.source_partition,
        source_manifest_identity=registry.dataset_source_manifest_identity(manifest),
        config=config,
        candidate_count=len(candidates),
        selected_scenarios=ranked[: config.scenario_count],
    )


def av2_pilot_plan_identity(plan: Av2PilotPlan) -> str:
    """Return the canonical logical identity of a pilot plan."""
    return canonical_sha256(
        "av2-real-data-pilot-plan",
        av2_pilot_plan_to_dict(plan),
    )


def _source_file(
    source_root: Path,
    relative_path: Path,
) -> Path:
    candidate = source_root / relative_path
    current = source_root
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise ArtifactError("selected source path contains a symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("selected source file is missing") from error
    if resolved == source_root or not resolved.is_relative_to(source_root):
        raise ArtifactError("selected source path resolves outside source_root")
    if not resolved.is_file():
        raise ArtifactError("selected source path must be a regular file")
    return resolved


def _hash_selected_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_READ_CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise ArtifactError("selected source file cannot be read") from error
    return size, digest.hexdigest()


def _motion_scenario_id(source_root: Path, relative_path: Path) -> str:
    try:
        parquet_file = pq.ParquetFile(source_root / relative_path)
        batches = parquet_file.iter_batches(
            batch_size=65_536,
            columns=["scenario_id"],
        )
        observed: str | None = None
        row_count = 0
        for batch in batches:
            values = batch.column(0).to_pylist()
            row_count += len(values)
            for value in values:
                source_id = _source_identifier(value, "source scenario_id")
                if observed is None:
                    observed = source_id
                elif source_id != observed:
                    raise SchemaError(
                        "motion source contains multiple scenario identifiers"
                    )
        if row_count == 0 or observed is None:
            raise SchemaError("motion source must contain scenario rows")
        return observed
    except (ArtifactError, SchemaError):
        raise
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError("motion source scenario identifier cannot be read") from error


def verify_av2_pilot_sources(
    source_root: Path,
    plan: Av2PilotPlan,
) -> None:
    """Verify only selected AV2 source bytes and adapter structure."""
    if not isinstance(source_root, Path):
        raise ArtifactError("source_root must be a Path")
    if source_root.is_symlink():
        raise ArtifactError("source_root must not be a symbolic link")
    try:
        root = source_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("source_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("source_root must be a directory")
    if not isinstance(plan, Av2PilotPlan):
        raise ValidationError("plan must be Av2PilotPlan")
    map_config = av2_map.Av2VectorMapAdapterConfig(
        adapter_version=plan.config.map_adapter_version,
        centerline_point_count=plan.config.centerline_point_count,
    )
    for pair in plan.selected_scenarios:
        motion_path = _source_file(root, pair.motion_relative_path)
        map_path = _source_file(root, pair.map_relative_path)
        for path, expected_size, expected_digest in (
            (motion_path, pair.motion_size_bytes, pair.motion_sha256),
            (map_path, pair.map_size_bytes, pair.map_sha256),
        ):
            size, digest = _hash_selected_file(path)
            if size != expected_size:
                raise ArtifactError("selected source size differs from pilot plan")
            if digest != expected_digest:
                raise ArtifactError("selected source SHA-256 differs from pilot plan")
        av2_motion.inspect_av2_motion_scenario_file(
            root,
            pair.motion_relative_path,
        )
        motion_source_id = _motion_scenario_id(root, pair.motion_relative_path)
        if motion_source_id != pair.source_scenario_id:
            raise SchemaError("motion source scenario ID differs from pilot plan")
        map_summary = av2_map.inspect_av2_vector_map_file(
            root,
            pair.map_relative_path,
            config=map_config,
        )
        if map_summary.source_map_id != pair.source_scenario_id:
            raise SchemaError("map source scenario ID differs from pilot plan")


def av2_pilot_materialization_plan(
    plan: Av2PilotPlan,
) -> MaterializationPlan:
    """Return one deterministic cache unit per selected AV2 scenario."""
    if not isinstance(plan, Av2PilotPlan):
        raise ValidationError("plan must be Av2PilotPlan")
    parameter_identity = canonical_sha256(
        "av2-pilot-conversion-parameters",
        {
            "dataset_version": plan.config.dataset_version,
            "canonical_split_name": plan.config.canonical_split_name,
            "motion_adapter_version": plan.config.motion_adapter_version,
            "map_adapter_version": plan.config.map_adapter_version,
            "inclusion_policy": _inclusion_policy(plan.config.inclusion_policy).value,
            "centerline_point_count": plan.config.centerline_point_count,
            "row_group_size": plan.config.row_group_size,
        },
    )
    units = tuple(
        MaterializationUnitSpec(
            unit_id=f"pilot-unit:av2:{pair.source_scenario_id}",
            operation_name="av2-pilot-scenario-conversion",
            operation_version="1.0",
            input_identity=canonical_sha256(
                "av2-pilot-source-pair",
                {
                    **av2_pilot_source_pair_to_dict(pair),
                    "source_manifest_identity": plan.source_manifest_identity,
                },
            ),
            parameter_identity=parameter_identity,
            expected_output_paths=_OUTPUT_PATHS,
            estimated_output_bytes=math.ceil(
                pair.total_source_bytes * plan.config.materialization_expansion_factor
            ),
        )
        for pair in plan.selected_scenarios
    )
    return MaterializationPlan(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=plan.dataset_version,
        units=units,
    )


def _write_cache_text(path: Path, text: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except OSError as error:
        raise ArtifactError(f"cannot write cache output: {path.name}") from error


def _write_cache_parquet(
    path: Path,
    table: pa.Table,
    schema_name: schemas.CanonicalSchemaName,
    *,
    row_group_size: int,
) -> None:
    sorted_table = parquet_io.sort_canonical_table(table, schema_name)
    try:
        pq.write_table(
            sorted_table,
            path,
            row_group_size=row_group_size,
            **_PARQUET_WRITE_OPTIONS,
        )
        parquet_file = pq.ParquetFile(path)
        schemas.validate_arrow_schema(parquet_file.schema_arrow, schema_name)
        if parquet_file.metadata.num_rows != sorted_table.num_rows:
            raise SchemaError("cache Parquet row count differs after writing")
        reopened = pq.read_table(path)
        parquet_io.validate_canonical_table(reopened, schema_name)
        if reopened.num_rows != sorted_table.num_rows:
            raise SchemaError("cache Parquet rows differ after reopening")
    except SchemaError:
        raise
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError(
            f"cannot write canonical cache Parquet: {path.name}"
        ) from error


def _pilot_worker(
    source_root: Path,
    plan: Av2PilotPlan,
) -> Callable[[MaterializationUnitSpec, Path], None]:
    pairs = {
        f"pilot-unit:av2:{pair.source_scenario_id}": pair
        for pair in plan.selected_scenarios
    }

    def worker(unit: MaterializationUnitSpec, incomplete: Path) -> None:
        pair = pairs.get(unit.unit_id)
        if pair is None:
            raise ValidationError("materialization unit is not in the pilot plan")
        motion = av2_motion.load_av2_motion_scenario(
            source_root,
            pair.motion_relative_path,
            config=av2_motion.Av2MotionAdapterConfig(
                dataset_version=plan.config.dataset_version,
                split_name=plan.config.canonical_split_name,
                adapter_version=plan.config.motion_adapter_version,
                inclusion_policy=plan.config.inclusion_policy,
                source_map_available=True,
            ),
        )
        if motion.source_scenario_id != pair.source_scenario_id:
            raise SchemaError("motion conversion source ID differs from pilot plan")
        vector_map = av2_map.load_av2_vector_map(
            source_root,
            pair.map_relative_path,
            scenario=motion.scenario,
            coordinate_frame=motion.coordinate_frame,
            config=av2_map.Av2VectorMapAdapterConfig(
                adapter_version=plan.config.map_adapter_version,
                centerline_point_count=plan.config.centerline_point_count,
            ),
        )
        if vector_map.source_map_id != pair.source_scenario_id:
            raise SchemaError("map conversion source ID differs from pilot plan")
        _write_cache_text(
            incomplete / _OUTPUT_PATHS[0],
            canonical_json_text(
                av2_motion.av2_motion_conversion_summary_to_dict(motion)
            ),
        )
        _write_cache_text(
            incomplete / _OUTPUT_PATHS[1],
            canonical_json_text(
                av2_map.av2_vector_map_conversion_summary_to_dict(vector_map)
            ),
        )
        tables = (
            (
                parquet_io.scenario_records_to_table((motion.scenario,)),
                schemas.CanonicalSchemaName.SCENARIO_MANIFEST,
            ),
            (
                parquet_io.coordinate_frame_records_to_table(
                    (motion.coordinate_frame,)
                ),
                schemas.CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            ),
            (
                parquet_io.agent_records_to_table(motion.agents),
                schemas.CanonicalSchemaName.AGENT_METADATA,
            ),
            (
                parquet_io.trajectories_to_table(motion.trajectories),
                schemas.CanonicalSchemaName.TRAJECTORY_SAMPLES,
            ),
            (
                parquet_io.vector_map_elements_to_table(vector_map.elements),
                schemas.CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
            ),
        )
        for relative_path, (table, schema_name) in zip(
            _OUTPUT_PATHS[2:],
            tables,
            strict=True,
        ):
            _write_cache_parquet(
                incomplete / relative_path,
                table,
                schema_name,
                row_group_size=plan.config.row_group_size,
            )

    return worker


def _dataset_paths_from_entries(
    entries: Sequence[str | Path],
) -> validation.CanonicalDatasetPaths:
    normalized = tuple(normalize_relative_path(entry) for entry in entries)
    return validation.CanonicalDatasetPaths(
        scenario_manifest=tuple(
            entry / "scenario_manifest.parquet" for entry in normalized
        ),
        coordinate_frame_metadata=tuple(
            entry / "coordinate_frame_metadata.parquet" for entry in normalized
        ),
        agent_metadata=tuple(entry / "agent_metadata.parquet" for entry in normalized),
        trajectory_samples=tuple(
            entry / "trajectory_samples.parquet" for entry in normalized
        ),
        vector_map_elements=tuple(
            entry / "vector_map_elements.parquet" for entry in normalized
        ),
    )


def _ordered_validation_paths(
    paths: validation.CanonicalDatasetPaths,
    plan: Av2PilotPlan,
) -> validation.CanonicalDatasetPaths:
    order = tuple(
        sorted(
            range(plan.selected_scenario_count),
            key=lambda index: plan.selected_scenarios[index].source_scenario_id,
        )
    )

    def reordered(values: Sequence[str | Path]) -> tuple[Path, ...]:
        return tuple(normalize_relative_path(values[index]) for index in order)

    return validation.CanonicalDatasetPaths(
        scenario_manifest=reordered(paths.scenario_manifest),
        coordinate_frame_metadata=reordered(paths.coordinate_frame_metadata),
        agent_metadata=reordered(paths.agent_metadata),
        trajectory_samples=reordered(paths.trajectory_samples),
        vector_map_elements=reordered(paths.vector_map_elements),
    )


def _validate_report_against_validation(
    report: Av2PilotReport,
    validation_report: validation.CanonicalValidationReport,
) -> None:
    checks = (
        (
            report.source_scenario_count,
            validation_report.source_scenario_count,
        ),
        (
            report.source_coordinate_frame_count,
            validation_report.source_coordinate_frame_count,
        ),
        (report.source_agent_count, validation_report.source_agent_count),
        (
            report.source_trajectory_count,
            validation_report.source_trajectory_count,
        ),
        (report.source_sample_count, validation_report.source_sample_count),
        (
            report.source_map_element_count,
            validation_report.source_map_element_count,
        ),
        (
            report.included_scenario_count,
            validation_report.included_scenario_count,
        ),
        (report.included_agent_count, validation_report.included_agent_count),
        (
            report.included_trajectory_count,
            validation_report.included_trajectory_count,
        ),
        (
            report.included_map_element_count,
            validation_report.included_map_element_count,
        ),
        (report.exclusion_count, validation_report.exclusion_count),
    )
    if any(left != right for left, right in checks):
        raise ValidationError("pilot report counts differ from validation report")


def execute_av2_pilot(
    repository_root: Path,
    source_root: Path,
    cache_relative_root: str | Path,
    plan: Av2PilotPlan,
    *,
    incomplete_policy: IncompleteEntryPolicy | str = IncompleteEntryPolicy.RESTART,
) -> Av2PilotExecution:
    """Verify, sequentially materialize, and boundedly validate an AV2 pilot."""
    if not isinstance(repository_root, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("repository_root must be a directory")
    if not isinstance(plan, Av2PilotPlan):
        raise ValidationError("plan must be Av2PilotPlan")
    cache_root = normalize_relative_path(cache_relative_root)
    started = time.perf_counter()
    before = check_disk_space(
        root,
        required_bytes=0,
        reserve_fraction=plan.config.reserve_fraction,
    )

    source_started = time.perf_counter()
    verify_av2_pilot_sources(source_root, plan)
    source_seconds = time.perf_counter() - source_started

    materialization_plan = av2_pilot_materialization_plan(plan)
    check_disk_space(
        root,
        required_bytes=sum(
            unit.estimated_output_bytes for unit in materialization_plan.units
        ),
        reserve_fraction=plan.config.reserve_fraction,
    )
    materialization_started = time.perf_counter()
    materialization_report = execute_materialization_plan(
        root,
        cache_root,
        materialization_plan,
        _pilot_worker(source_root.resolve(strict=True), plan),
        incomplete_policy=incomplete_policy,
        reserve_fraction=plan.config.reserve_fraction,
    )
    materialization_seconds = time.perf_counter() - materialization_started

    entries = tuple(
        normalize_relative_path(result.entry_relative_directory)
        for result in materialization_report.results
    )
    dataset_paths = _dataset_paths_from_entries(entries)
    validation_started = time.perf_counter()
    validation_report = validation.validate_canonical_parquet_dataset(
        root,
        _ordered_validation_paths(dataset_paths, plan),
        config=validation.CanonicalValidationConfig(
            minimum_valid_sample_count=plan.config.minimum_valid_sample_count,
            minimum_valid_duration_ns=plan.config.minimum_valid_duration_ns,
            allowed_agent_classes=tuple(records.AgentClass),
            require_source_map=True,
        ),
        batch_size=plan.config.validation_batch_size,
    )
    validation_seconds = time.perf_counter() - validation_started
    after = check_disk_space(
        root,
        required_bytes=0,
        reserve_fraction=plan.config.reserve_fraction,
    )
    total_seconds = time.perf_counter() - started
    resources = Av2PilotResourceMeasurements(
        source_verification_seconds=source_seconds,
        materialization_seconds=materialization_seconds,
        validation_seconds=validation_seconds,
        total_seconds=total_seconds,
        selected_source_bytes=plan.selected_total_source_bytes,
        cache_output_bytes=materialization_report.total_output_bytes,
        disk_free_before_bytes=before.free_bytes,
        disk_free_after_bytes=after.free_bytes,
    )
    report = Av2PilotReport(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=plan.dataset_version,
        plan_identity=av2_pilot_plan_identity(plan),
        materialization_plan_identity=materialization_plan_identity(
            materialization_plan
        ),
        validation_report_identity=canonical_sha256(
            "canonical-validation-report",
            validation.canonical_validation_report_to_dict(validation_report),
        ),
        selected_scenario_ids=tuple(
            item.source_scenario_id for item in plan.selected_scenarios
        ),
        candidate_scenario_count=plan.candidate_count,
        selected_scenario_count=plan.selected_scenario_count,
        materialized_scenario_count=materialization_report.materialized_count,
        reused_scenario_count=materialization_report.reused_count,
        source_scenario_count=validation_report.source_scenario_count,
        source_coordinate_frame_count=(validation_report.source_coordinate_frame_count),
        source_agent_count=validation_report.source_agent_count,
        source_trajectory_count=validation_report.source_trajectory_count,
        source_sample_count=validation_report.source_sample_count,
        source_map_element_count=validation_report.source_map_element_count,
        included_scenario_count=validation_report.included_scenario_count,
        included_agent_count=validation_report.included_agent_count,
        included_trajectory_count=validation_report.included_trajectory_count,
        included_map_element_count=validation_report.included_map_element_count,
        exclusion_count=validation_report.exclusion_count,
        cache_relative_root=normalize_relative_path(
            materialization_report.cache_relative_root
        ),
        cache_entry_directories=entries,
        resources=resources,
    )
    return Av2PilotExecution(
        plan=plan,
        materialization_plan=materialization_plan,
        materialization_report=materialization_report,
        dataset_paths=dataset_paths,
        validation_report=validation_report,
        report=report,
    )


def av2_pilot_summary_markdown(report: Av2PilotReport) -> str:
    """Return deterministic human-readable Markdown for a supplied report."""
    if not isinstance(report, Av2PilotReport):
        raise ValidationError("report must be Av2PilotReport")
    throughput = report.scenarios_per_second
    throughput_text = "not available" if throughput is None else f"{throughput:.6f}"
    eligibility = "eligible" if report.is_eligible else "not eligible"
    resources = report.resources
    return "\n".join(
        (
            "# AV2 Laptop-Scale Pilot Summary",
            "",
            f"- Dataset: `{report.dataset_id}` version `{report.dataset_version}`",
            f"- Selected scenarios: {report.selected_scenario_count}",
            f"- Materialized scenarios: {report.materialized_scenario_count}",
            f"- Reused scenarios: {report.reused_scenario_count}",
            (f"- Selected motion/map source bytes: {resources.selected_source_bytes}"),
            f"- Cache output bytes: {resources.cache_output_bytes}",
            f"- Source scenarios: {report.source_scenario_count}",
            (f"- Source coordinate frames: {report.source_coordinate_frame_count}"),
            f"- Source agents: {report.source_agent_count}",
            f"- Source trajectories: {report.source_trajectory_count}",
            f"- Source samples: {report.source_sample_count}",
            f"- Source map elements: {report.source_map_element_count}",
            f"- Included scenarios: {report.included_scenario_count}",
            f"- Included agents: {report.included_agent_count}",
            f"- Included trajectories: {report.included_trajectory_count}",
            f"- Included map elements: {report.included_map_element_count}",
            f"- Exclusions: {report.exclusion_count}",
            (
                "- Source verification seconds: "
                f"{resources.source_verification_seconds:.6f}"
            ),
            (f"- Materialization seconds: {resources.materialization_seconds:.6f}"),
            f"- Validation seconds: {resources.validation_seconds:.6f}",
            f"- Total seconds: {resources.total_seconds:.6f}",
            f"- Scenarios per second: {throughput_text}",
            f"- Final eligibility: {eligibility}",
            "- Execution was sequential and CPU-oriented.",
            "",
        )
    )


def materialize_av2_pilot_artifacts(
    run_directory: RunDirectory,
    execution: Av2PilotExecution,
    *,
    relative_directory: str | Path = "artifacts/av2_real_data_pilot",
) -> Av2PilotArtifacts:
    """Atomically write the exact pilot plan, report, and Markdown summary."""
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be RunDirectory")
    if not isinstance(execution, Av2PilotExecution):
        raise ValidationError("execution must be Av2PilotExecution")
    try:
        directory = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    plan_artifact = atomic_write_text(
        run_directory,
        directory / "pilot_plan.json",
        av2_pilot_plan_to_canonical_json(execution.plan),
    )
    report_artifact = atomic_write_text(
        run_directory,
        directory / "pilot_report.json",
        av2_pilot_report_to_canonical_json(execution.report),
    )
    summary_artifact = atomic_write_text(
        run_directory,
        directory / "pilot_summary.md",
        av2_pilot_summary_markdown(execution.report),
    )
    return Av2PilotArtifacts(
        pilot_plan=plan_artifact,
        pilot_report=report_artifact,
        pilot_summary=summary_artifact,
    )


def _verified_artifact_bytes(
    repository_root: Path,
    artifact: WrittenArtifact,
) -> bytes:
    if not isinstance(repository_root, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("repository_root must be a directory")
    candidate = root / artifact.relative_path
    if candidate.is_symlink():
        raise ArtifactError("pilot artifact must not be a symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("pilot artifact is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("pilot artifact resolves outside repository")
    if not resolved.is_file():
        raise ArtifactError("pilot artifact must be a regular file")
    digest = hashlib.sha256()
    data = bytearray()
    try:
        with resolved.open("rb") as stream:
            while chunk := stream.read(_READ_CHUNK_SIZE):
                digest.update(chunk)
                data.extend(chunk)
    except OSError as error:
        raise ArtifactError("pilot artifact cannot be read") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("pilot artifact size differs")
    if digest.hexdigest() != artifact.content_checksum:
        raise ArtifactError("pilot artifact SHA-256 differs")
    return bytes(data)


def _utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise SchemaError(f"{label} is not valid UTF-8") from None


def verify_av2_pilot_artifacts(
    repository_root: Path,
    artifacts: Av2PilotArtifacts,
) -> tuple[Av2PilotPlan, Av2PilotReport]:
    """Verify exact pilot artifact bytes and cross-object consistency."""
    if not isinstance(artifacts, Av2PilotArtifacts):
        raise ValidationError("artifacts must be Av2PilotArtifacts")
    plan_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.pilot_plan),
        "pilot_plan.json",
    )
    report_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.pilot_report),
        "pilot_report.json",
    )
    summary_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.pilot_summary),
        "pilot_summary.md",
    )
    plan = av2_pilot_plan_from_json(plan_text)
    report = av2_pilot_report_from_json(report_text)
    if plan_text != av2_pilot_plan_to_canonical_json(plan):
        raise SchemaError("pilot_plan.json is not canonical")
    if report_text != av2_pilot_report_to_canonical_json(report):
        raise SchemaError("pilot_report.json is not canonical")
    if report.plan_identity != av2_pilot_plan_identity(plan):
        raise SchemaError("pilot report plan identity differs")
    selected_ids = tuple(item.source_scenario_id for item in plan.selected_scenarios)
    if (
        report.dataset_id != plan.dataset_id
        or report.dataset_version != plan.dataset_version
        or report.selected_scenario_ids != selected_ids
        or report.candidate_scenario_count != plan.candidate_count
        or report.selected_scenario_count != plan.selected_scenario_count
        or report.resources.selected_source_bytes != plan.selected_total_source_bytes
    ):
        raise SchemaError("pilot plan and report content differ")
    if summary_text != av2_pilot_summary_markdown(report):
        raise SchemaError("pilot_summary.md differs from pilot report")
    return plan, report
