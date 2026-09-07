"""Deterministic validation and exclusion reporting for canonical datasets."""

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_text,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.data.parquet_io import (
    iter_canonical_parquet_batches,
    read_canonical_parquet_table,
    validate_canonical_table,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    MapGeometryType,
    geometry_from_canonical_wkb,
)
from kinematicweave.domain.records import (
    AgentClass,
    AgentRecord,
    OriginType,
    ScenarioRecord,
    TrajectorySampleRecord,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import validate_identifier
from kinematicweave.paths import normalize_relative_path

__all__ = [
    "CanonicalDatasetPaths",
    "CanonicalExclusionRecord",
    "CanonicalValidationArtifacts",
    "CanonicalValidationConfig",
    "CanonicalValidationReport",
    "ExclusionReason",
    "ValidationUnitType",
    "canonical_exclusion_record_to_dict",
    "canonical_exclusions_jsonl",
    "canonical_validation_config_to_dict",
    "canonical_validation_report_from_dict",
    "canonical_validation_report_from_json",
    "canonical_validation_report_to_canonical_json",
    "canonical_validation_report_to_dict",
    "canonical_validation_summary_markdown",
    "materialize_canonical_validation_report",
    "validate_and_materialize_canonical_parquet_dataset",
    "validate_canonical_parquet_dataset",
    "validate_canonical_tables",
    "verify_canonical_validation_artifacts",
]

_SCHEMA_VERSION = "1.0"
_INT64_MAX = 2**63 - 1
_LOCAL_FRAME_TYPE = "local_cartesian"
_AXIS_CONVENTION = "right_handed_x_y_z_up"


class ValidationUnitType(StrEnum):
    """Units that may participate in canonical validation reporting."""

    DATASET = "dataset"
    SCENARIO = "scenario"
    COORDINATE_FRAME = "coordinate_frame"
    AGENT = "agent"
    TRAJECTORY = "trajectory"
    MAP_ELEMENT = "map_element"


class ExclusionReason(StrEnum):
    """Approved canonical exclusion vocabulary in policy order."""

    ADAPTER_FAILURE = "adapter_failure"
    INVALID_TIMESTAMPS = "invalid_timestamps"
    INSUFFICIENT_SAMPLES = "insufficient_samples"
    INSUFFICIENT_DURATION = "insufficient_duration"
    UNSUPPORTED_AGENT_CLASS = "unsupported_agent_class"
    NO_USABLE_MAP = "no_usable_map"
    INSUFFICIENT_TRAJECTORY_COVERAGE = "insufficient_trajectory_coverage"
    COORDINATE_FRAME_MISMATCH = "coordinate_frame_mismatch"
    DUPLICATE = "duplicate"
    GEOGRAPHIC_LEAKAGE_CONFLICT = "geographic_leakage_conflict"
    INVALID_GEOMETRY = "invalid_geometry"
    RESOURCE_LIMIT_EXCLUSION = "resource_limit_exclusion"
    OTHER_DOCUMENTED_REASON = "other_documented_reason"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty trimmed text")
    return value.strip()


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


def _int64_duration(value: object) -> int:
    normalized = _nonnegative_int(value, "minimum_valid_duration_ns")
    if normalized > _INT64_MAX:
        raise ValidationError("minimum_valid_duration_ns exceeds signed int64")
    return normalized


def _enum_value[EnumT: StrEnum](
    enum_type: type[EnumT],
    value: object,
    field_name: str,
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValidationError(f"{field_name} has invalid value {value!r}")


def _ordered_unique_text(
    value: object,
    field_name: str,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    normalized = tuple(_required_text(item, f"{field_name} item") for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return normalized


def _identifier_tuple(value: object, field_name: str) -> tuple[str, ...]:
    return tuple(
        validate_identifier(item) for item in _ordered_unique_text(value, field_name)
    )


@dataclass(frozen=True, slots=True)
class CanonicalValidationConfig:
    """Configurable eligibility thresholds for canonical validation."""

    minimum_valid_sample_count: int = 10
    minimum_valid_duration_ns: int = 1_000_000_000
    allowed_agent_classes: Sequence[AgentClass | str] = tuple(AgentClass)
    require_source_map: bool = False

    def __post_init__(self) -> None:
        """Normalize and validate eligibility configuration."""
        object.__setattr__(
            self,
            "minimum_valid_sample_count",
            _positive_int(
                self.minimum_valid_sample_count,
                "minimum_valid_sample_count",
            ),
        )
        object.__setattr__(
            self,
            "minimum_valid_duration_ns",
            _int64_duration(self.minimum_valid_duration_ns),
        )
        value: object = self.allowed_agent_classes
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("allowed_agent_classes must be a non-string sequence")
        classes = tuple(
            _enum_value(AgentClass, item, "allowed_agent_classes item")
            for item in value
        )
        if not classes:
            raise ValidationError("allowed_agent_classes must not be empty")
        if len(classes) != len(set(classes)):
            raise ValidationError("allowed_agent_classes must be unique")
        object.__setattr__(self, "allowed_agent_classes", classes)
        if not isinstance(self.require_source_map, bool):
            raise ValidationError("require_source_map must be a Boolean")


_DEFAULT_CONFIG = CanonicalValidationConfig()


def _path_tuple(
    value: object,
    field_name: str,
    *,
    required: bool,
) -> tuple[Path, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    try:
        normalized = tuple(normalize_relative_path(item) for item in value)
    except ValidationError:
        raise
    if required and not normalized:
        raise ValidationError(f"{field_name} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return normalized


@dataclass(frozen=True, slots=True)
class CanonicalDatasetPaths:
    """Repository-relative canonical Parquet paths grouped by schema."""

    scenario_manifest: Sequence[str | Path]
    coordinate_frame_metadata: Sequence[str | Path]
    agent_metadata: Sequence[str | Path]
    trajectory_samples: Sequence[str | Path]
    vector_map_elements: Sequence[str | Path]

    def __post_init__(self) -> None:
        """Normalize, copy, and validate all path groups."""
        normalized: dict[str, tuple[Path, ...]] = {}
        for field_name in (
            "scenario_manifest",
            "coordinate_frame_metadata",
            "agent_metadata",
            "trajectory_samples",
            "vector_map_elements",
        ):
            paths = _path_tuple(
                getattr(self, field_name),
                field_name,
                required=field_name != "vector_map_elements",
            )
            normalized[field_name] = paths
            object.__setattr__(self, field_name, paths)
        flattened = tuple(path for paths in normalized.values() for path in paths)
        if len(flattened) != len(set(flattened)):
            raise ValidationError("the same path may not appear in two fields")


def _exclusion_payload(
    unit_type: ValidationUnitType,
    unit_id: str,
    reason: ExclusionReason,
    message: str,
    related_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "unit_type": unit_type.value,
        "unit_id": unit_id,
        "reason": reason.value,
        "message": message,
        "related_ids": list(related_ids),
    }


def _exclusion_identifier(
    unit_type: ValidationUnitType,
    unit_id: str,
    reason: ExclusionReason,
    message: str,
    related_ids: tuple[str, ...],
) -> str:
    digest = canonical_sha256(
        "canonical-exclusion",
        _exclusion_payload(unit_type, unit_id, reason, message, related_ids),
    )
    return f"exclusion:{unit_type.value}:{digest[:24]}"


@dataclass(frozen=True, slots=True)
class CanonicalExclusionRecord:
    """One deterministic primary exclusion for a canonical unit."""

    exclusion_id: str
    unit_type: ValidationUnitType | str
    unit_id: str
    reason: ExclusionReason | str
    message: str
    related_ids: Sequence[str]

    def __post_init__(self) -> None:
        """Normalize and validate exclusion identity and content."""
        unit_type = _enum_value(
            ValidationUnitType,
            self.unit_type,
            "unit_type",
        )
        reason = _enum_value(ExclusionReason, self.reason, "reason")
        unit_id = _required_text(self.unit_id, "unit_id")
        if unit_type is not ValidationUnitType.DATASET:
            unit_id = validate_identifier(unit_id)
        message = _required_text(self.message, "message")
        related_ids = _ordered_unique_text(self.related_ids, "related_ids")
        expected = _exclusion_identifier(
            unit_type,
            unit_id,
            reason,
            message,
            related_ids,
        )
        exclusion_id = validate_identifier(self.exclusion_id)
        if exclusion_id != expected:
            raise ValidationError("exclusion_id does not match exclusion content")
        object.__setattr__(self, "unit_type", unit_type)
        object.__setattr__(self, "unit_id", unit_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "related_ids", related_ids)
        object.__setattr__(self, "exclusion_id", exclusion_id)


def _new_exclusion(
    unit_type: ValidationUnitType,
    unit_id: str,
    reason: ExclusionReason,
    message: str,
    related_ids: Sequence[str] = (),
) -> CanonicalExclusionRecord:
    normalized_related = _ordered_unique_text(related_ids, "related_ids")
    normalized_unit_id = _required_text(unit_id, "unit_id")
    normalized_message = _required_text(message, "message")
    exclusion_id = _exclusion_identifier(
        unit_type,
        normalized_unit_id,
        reason,
        normalized_message,
        normalized_related,
    )
    return CanonicalExclusionRecord(
        exclusion_id=exclusion_id,
        unit_type=unit_type,
        unit_id=normalized_unit_id,
        reason=reason,
        message=normalized_message,
        related_ids=normalized_related,
    )


_UNIT_ORDER = {value: index for index, value in enumerate(ValidationUnitType)}
_REASON_ORDER = {value: index for index, value in enumerate(ExclusionReason)}


def _exclusion_sort_key(
    record: CanonicalExclusionRecord,
) -> tuple[int, str, int, str]:
    unit_type = _enum_value(
        ValidationUnitType,
        record.unit_type,
        "unit_type",
    )
    reason = _enum_value(ExclusionReason, record.reason, "reason")
    return (
        _UNIT_ORDER[unit_type],
        record.unit_id,
        _REASON_ORDER[reason],
        record.exclusion_id,
    )


def _reason_counts(
    value: object,
) -> tuple[tuple[str, int], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("exclusion_reason_counts must be a sequence")
    normalized: list[tuple[str, int]] = []
    for item in value:
        if (
            isinstance(item, (str, bytes))
            or not isinstance(item, Sequence)
            or len(item) != 2
        ):
            raise ValidationError("exclusion_reason_counts entries must be pairs")
        reason, count = item
        if not isinstance(reason, str):
            raise ValidationError("exclusion reason-count key must be a string")
        normalized.append(
            (
                _enum_value(ExclusionReason, reason, "exclusion reason").value,
                _nonnegative_int(count, "exclusion reason count"),
            )
        )
    if tuple(reason for reason, _count in normalized) != tuple(
        reason.value for reason in ExclusionReason
    ):
        raise ValidationError(
            "exclusion_reason_counts must contain every reason in enum order"
        )
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class CanonicalValidationReport:
    """Deterministic validation result for one canonical dataset."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    config: CanonicalValidationConfig
    source_scenario_count: int
    source_coordinate_frame_count: int
    source_agent_count: int
    source_trajectory_count: int
    source_sample_count: int
    source_map_element_count: int
    included_scenario_ids: Sequence[str]
    included_agent_ids: Sequence[str]
    included_trajectory_ids: Sequence[str]
    included_map_element_ids: Sequence[str]
    exclusions: Sequence[CanonicalExclusionRecord]
    exclusion_reason_counts: Sequence[Sequence[object]]

    def __post_init__(self) -> None:
        """Copy collections and enforce report consistency."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self,
            "dataset_id",
            _required_text(self.dataset_id, "dataset_id"),
        )
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        if not isinstance(self.config, CanonicalValidationConfig):
            raise ValidationError("config must be CanonicalValidationConfig")
        for field_name in (
            "source_scenario_count",
            "source_coordinate_frame_count",
            "source_agent_count",
            "source_trajectory_count",
            "source_sample_count",
            "source_map_element_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        included_fields = (
            ("included_scenario_ids", self.source_scenario_count),
            ("included_agent_ids", self.source_agent_count),
            ("included_trajectory_ids", self.source_trajectory_count),
            ("included_map_element_ids", self.source_map_element_count),
        )
        for field_name, maximum in included_fields:
            identifiers = _identifier_tuple(getattr(self, field_name), field_name)
            if len(identifiers) > maximum:
                raise ValidationError(f"{field_name} exceeds its source count")
            object.__setattr__(self, field_name, identifiers)
        value: object = self.exclusions
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("exclusions must be a sequence")
        exclusions = tuple(value)
        if any(
            not isinstance(record, CanonicalExclusionRecord) for record in exclusions
        ):
            raise ValidationError(
                "exclusions must contain CanonicalExclusionRecord values"
            )
        typed_exclusions = cast(tuple[CanonicalExclusionRecord, ...], exclusions)
        if len({item.exclusion_id for item in typed_exclusions}) != len(
            typed_exclusions
        ):
            raise ValidationError("exclusion identifiers must be unique")
        if typed_exclusions != tuple(sorted(typed_exclusions, key=_exclusion_sort_key)):
            raise ValidationError("exclusions are not in canonical order")
        object.__setattr__(self, "exclusions", typed_exclusions)
        counts = _reason_counts(self.exclusion_reason_counts)
        observed = {
            reason.value: sum(record.reason is reason for record in typed_exclusions)
            for reason in ExclusionReason
        }
        if dict(counts) != observed:
            raise ValidationError("exclusion_reason_counts do not match exclusions")
        object.__setattr__(self, "exclusion_reason_counts", counts)

    @property
    def included_scenario_count(self) -> int:
        """Return the number of included scenarios."""
        return len(self.included_scenario_ids)

    @property
    def included_agent_count(self) -> int:
        """Return the number of included agents."""
        return len(self.included_agent_ids)

    @property
    def included_trajectory_count(self) -> int:
        """Return the number of included trajectories."""
        return len(self.included_trajectory_ids)

    @property
    def included_map_element_count(self) -> int:
        """Return the number of included map elements."""
        return len(self.included_map_element_ids)

    @property
    def exclusion_count(self) -> int:
        """Return the number of primary exclusions."""
        return len(self.exclusions)

    @property
    def is_eligible(self) -> bool:
        """Return whether the dataset retains a scenario and trajectory."""
        return bool(self.included_scenario_ids and self.included_trajectory_ids)


@dataclass(frozen=True, slots=True)
class CanonicalValidationArtifacts:
    """Physical validation report artifacts."""

    validation_report: WrittenArtifact
    exclusions_jsonl: WrittenArtifact
    summary_report: WrittenArtifact

    def __post_init__(self) -> None:
        """Validate artifact types and unique paths."""
        artifacts = (
            self.validation_report,
            self.exclusions_jsonl,
            self.summary_report,
        )
        if any(not isinstance(item, WrittenArtifact) for item in artifacts):
            raise ValidationError("validation artifacts must use WrittenArtifact")
        if len({item.relative_path for item in artifacts}) != len(artifacts):
            raise ValidationError("validation artifact paths must be unique")


def canonical_validation_config_to_dict(
    config: CanonicalValidationConfig,
) -> dict[str, object]:
    """Return an ordered JSON-compatible validation configuration."""
    if not isinstance(config, CanonicalValidationConfig):
        raise ValidationError("config must be CanonicalValidationConfig")
    return {
        "minimum_valid_sample_count": config.minimum_valid_sample_count,
        "minimum_valid_duration_ns": config.minimum_valid_duration_ns,
        "allowed_agent_classes": [
            _enum_value(
                AgentClass,
                value,
                "allowed_agent_classes item",
            ).value
            for value in config.allowed_agent_classes
        ],
        "require_source_map": config.require_source_map,
    }


def canonical_exclusion_record_to_dict(
    record: CanonicalExclusionRecord,
) -> dict[str, object]:
    """Return an ordered JSON-compatible exclusion record."""
    if not isinstance(record, CanonicalExclusionRecord):
        raise ValidationError("record must be CanonicalExclusionRecord")
    return {
        "exclusion_id": record.exclusion_id,
        "unit_type": _enum_value(
            ValidationUnitType,
            record.unit_type,
            "unit_type",
        ).value,
        "unit_id": record.unit_id,
        "reason": _enum_value(
            ExclusionReason,
            record.reason,
            "reason",
        ).value,
        "message": record.message,
        "related_ids": list(record.related_ids),
    }


def canonical_validation_report_to_dict(
    report: CanonicalValidationReport,
) -> dict[str, object]:
    """Return the complete ordered JSON-compatible validation report."""
    if not isinstance(report, CanonicalValidationReport):
        raise ValidationError("report must be CanonicalValidationReport")
    return {
        "schema_version": report.schema_version,
        "dataset_id": report.dataset_id,
        "dataset_version": report.dataset_version,
        "config": canonical_validation_config_to_dict(report.config),
        "source_scenario_count": report.source_scenario_count,
        "source_coordinate_frame_count": (report.source_coordinate_frame_count),
        "source_agent_count": report.source_agent_count,
        "source_trajectory_count": report.source_trajectory_count,
        "source_sample_count": report.source_sample_count,
        "source_map_element_count": report.source_map_element_count,
        "included_scenario_ids": list(report.included_scenario_ids),
        "included_agent_ids": list(report.included_agent_ids),
        "included_trajectory_ids": list(report.included_trajectory_ids),
        "included_map_element_ids": list(report.included_map_element_ids),
        "exclusions": [
            canonical_exclusion_record_to_dict(record) for record in report.exclusions
        ],
        "exclusion_reason_counts": [
            [reason, count] for reason, count in report.exclusion_reason_counts
        ],
    }


def canonical_validation_report_to_canonical_json(
    report: CanonicalValidationReport,
) -> str:
    """Serialize a validation report as canonical JSON with one newline."""
    return canonical_json_text(canonical_validation_report_to_dict(report))


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


def canonical_validation_report_from_dict(
    value: Mapping[str, object],
) -> CanonicalValidationReport:
    """Reconstruct and validate a report from an exact JSON mapping."""
    report_fields = (
        "schema_version",
        "dataset_id",
        "dataset_version",
        "config",
        "source_scenario_count",
        "source_coordinate_frame_count",
        "source_agent_count",
        "source_trajectory_count",
        "source_sample_count",
        "source_map_element_count",
        "included_scenario_ids",
        "included_agent_ids",
        "included_trajectory_ids",
        "included_map_element_ids",
        "exclusions",
        "exclusion_reason_counts",
    )
    config_fields = (
        "minimum_valid_sample_count",
        "minimum_valid_duration_ns",
        "allowed_agent_classes",
        "require_source_map",
    )
    exclusion_fields = (
        "exclusion_id",
        "unit_type",
        "unit_id",
        "reason",
        "message",
        "related_ids",
    )
    mapping = _exact_mapping(value, report_fields, "validation report")
    config_mapping = _exact_mapping(
        mapping["config"],
        config_fields,
        "validation config",
    )
    raw_exclusions = mapping["exclusions"]
    if isinstance(raw_exclusions, (str, bytes)) or not isinstance(
        raw_exclusions, Sequence
    ):
        raise SchemaError("exclusions must be a JSON array")
    try:
        config = CanonicalValidationConfig(
            minimum_valid_sample_count=cast(
                Any,
                config_mapping["minimum_valid_sample_count"],
            ),
            minimum_valid_duration_ns=cast(
                Any,
                config_mapping["minimum_valid_duration_ns"],
            ),
            allowed_agent_classes=cast(
                Any,
                config_mapping["allowed_agent_classes"],
            ),
            require_source_map=cast(
                Any,
                config_mapping["require_source_map"],
            ),
        )
        exclusions = tuple(
            CanonicalExclusionRecord(
                **cast(
                    Any,
                    _exact_mapping(item, exclusion_fields, "exclusion"),
                )
            )
            for item in raw_exclusions
        )
        return CanonicalValidationReport(
            schema_version=cast(Any, mapping["schema_version"]),
            dataset_id=cast(Any, mapping["dataset_id"]),
            dataset_version=cast(Any, mapping["dataset_version"]),
            config=config,
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
            included_scenario_ids=cast(
                Any,
                mapping["included_scenario_ids"],
            ),
            included_agent_ids=cast(
                Any,
                mapping["included_agent_ids"],
            ),
            included_trajectory_ids=cast(
                Any,
                mapping["included_trajectory_ids"],
            ),
            included_map_element_ids=cast(
                Any,
                mapping["included_map_element_ids"],
            ),
            exclusions=exclusions,
            exclusion_reason_counts=cast(
                Any,
                mapping["exclusion_reason_counts"],
            ),
        )
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def canonical_validation_report_from_json(
    text: str,
) -> CanonicalValidationReport:
    """Parse and validate a canonical validation report JSON document."""
    if not isinstance(text, str):
        raise SchemaError("validation report JSON must be text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError("validation report JSON is malformed") from None
    if not isinstance(value, Mapping):
        raise SchemaError("validation report JSON root must be an object")
    return canonical_validation_report_from_dict(cast(Mapping[str, object], value))


def canonical_exclusions_jsonl(report: CanonicalValidationReport) -> str:
    """Serialize exclusions as deterministic compact JSON Lines."""
    if not isinstance(report, CanonicalValidationReport):
        raise ValidationError("report must be CanonicalValidationReport")
    if not report.exclusions:
        return ""
    return "".join(
        canonical_json_text(canonical_exclusion_record_to_dict(record))
        for record in report.exclusions
    )


def canonical_validation_summary_markdown(
    report: CanonicalValidationReport,
) -> str:
    """Return the deterministic human-readable validation summary."""
    if not isinstance(report, CanonicalValidationReport):
        raise ValidationError("report must be CanonicalValidationReport")
    config = report.config
    lines = [
        "# Canonical Data Validation Summary",
        "",
        f"- Dataset: `{report.dataset_id}`",
        f"- Version: `{report.dataset_version}`",
        (f"- Minimum valid samples: {config.minimum_valid_sample_count}"),
        (f"- Minimum valid duration (ns): {config.minimum_valid_duration_ns}"),
        (
            "- Allowed agent classes: "
            + ", ".join(
                (f"`{_enum_value(AgentClass, value, 'allowed class').value}`")
                for value in config.allowed_agent_classes
            )
        ),
        f"- Source map required: {'yes' if config.require_source_map else 'no'}",
        "",
        "## Counts",
        "",
        "| Unit | Source | Included |",
        "|---|---:|---:|",
        (
            f"| Scenarios | {report.source_scenario_count} | "
            f"{report.included_scenario_count} |"
        ),
        (
            f"| Coordinate frames | {report.source_coordinate_frame_count} | "
            f"{report.included_scenario_count} |"
        ),
        (f"| Agents | {report.source_agent_count} | {report.included_agent_count} |"),
        (
            f"| Trajectories | {report.source_trajectory_count} | "
            f"{report.included_trajectory_count} |"
        ),
        f"| Samples | {report.source_sample_count} | n/a |",
        (
            f"| Map elements | {report.source_map_element_count} | "
            f"{report.included_map_element_count} |"
        ),
        "",
        f"Total exclusions: **{report.exclusion_count}**",
        "",
        "## Exclusion Reasons",
        "",
        "| Reason | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{reason}` | {count} |" for reason, count in report.exclusion_reason_counts
    )
    lines.extend(
        (
            "",
            (
                "**Eligibility: eligible.**"
                if report.is_eligible
                else "**Eligibility: not eligible.**"
            ),
            "",
        )
    )
    return "\n".join(lines)


def _batch_rows(batch: pa.RecordBatch) -> Iterator[dict[str, object]]:
    names = tuple(batch.schema.names)
    columns = tuple(batch.column(index) for index in range(batch.num_columns))
    for row_index in range(batch.num_rows):
        yield {
            name: column[row_index].as_py()
            for name, column in zip(names, columns, strict=True)
        }


def _table_rows(table: pa.Table) -> Iterator[dict[str, object]]:
    for batch in table.to_batches(max_chunksize=65_536):
        yield from _batch_rows(batch)


def _schema_error(error: Exception) -> SchemaError:
    return SchemaError(str(error))


def _scenario_records(
    table: pa.Table,
) -> tuple[ScenarioRecord, ...]:
    records: list[ScenarioRecord] = []
    try:
        for row in _table_rows(table):
            records.append(ScenarioRecord(**cast(Any, row)))
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    return tuple(records)


@dataclass(frozen=True, slots=True)
class _Frame:
    scenario_id: str
    coordinate_frame_id: str
    frame_type: str
    origin_x_m: float
    origin_y_m: float
    origin_z_m: float | None
    axis_convention: str
    distance_unit: str
    angle_unit: str
    timestamp_unit: str
    source_crs: str | None
    has_elevation: bool


def _finite(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SchemaError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise SchemaError(f"{field_name} must be finite")
    return normalized


def _optional_finite(value: object, field_name: str) -> float | None:
    return None if value is None else _finite(value, field_name)


def _quality_flags(value: object) -> tuple[str, ...]:
    return _ordered_unique_text(value, "quality_flags")


def _frame_records(table: pa.Table) -> tuple[_Frame, ...]:
    frames: list[_Frame] = []
    try:
        for row in _table_rows(table):
            scenario_id = validate_identifier(cast(str, row["scenario_id"]))
            frame_id = validate_identifier(cast(str, row["coordinate_frame_id"]))
            parent = row["parent_frame_id"]
            if parent is not None:
                parent_id = validate_identifier(cast(str, parent))
                if parent_id == frame_id:
                    raise ValidationError("coordinate frame may not parent itself")
            frame_type = _required_text(row["frame_type"], "frame_type")
            axis = _required_text(
                row["axis_convention"],
                "axis_convention",
            )
            distance = _required_text(
                row["distance_unit"],
                "distance_unit",
            )
            angle = _required_text(row["angle_unit"], "angle_unit")
            timestamp = _required_text(
                row["timestamp_unit"],
                "timestamp_unit",
            )
            source_crs = (
                None
                if row["source_crs"] is None
                else _required_text(row["source_crs"], "source_crs")
            )
            has_elevation = row["has_elevation"]
            if not isinstance(has_elevation, bool):
                raise ValidationError("has_elevation must be a Boolean")
            origin_z = _optional_finite(row["origin_z_m"], "origin_z_m")
            if not has_elevation and origin_z is not None:
                raise ValidationError("origin_z_m must be None without elevation")
            transform = row["transform_to_parent_4x4"]
            if transform is not None:
                if isinstance(transform, (str, bytes)) or not isinstance(
                    transform, Sequence
                ):
                    raise ValidationError("transform_to_parent_4x4 must be a sequence")
                values = tuple(
                    _finite(item, "transform_to_parent_4x4 item") for item in transform
                )
                if len(values) != 16:
                    raise ValidationError(
                        "transform_to_parent_4x4 must contain 16 values"
                    )
            _enum_value(OriginType, row["origin_type"], "origin_type")
            _quality_flags(row["quality_flags"])
            frames.append(
                _Frame(
                    scenario_id=scenario_id,
                    coordinate_frame_id=frame_id,
                    frame_type=frame_type,
                    origin_x_m=_finite(row["origin_x_m"], "origin_x_m"),
                    origin_y_m=_finite(row["origin_y_m"], "origin_y_m"),
                    origin_z_m=origin_z,
                    axis_convention=axis,
                    distance_unit=distance,
                    angle_unit=angle,
                    timestamp_unit=timestamp,
                    source_crs=source_crs,
                    has_elevation=has_elevation,
                )
            )
    except (TypeError, KeyError, ValidationError) as error:
        raise _schema_error(error) from None
    return tuple(frames)


def _agent_records(table: pa.Table) -> tuple[AgentRecord, ...]:
    records: list[AgentRecord] = []
    try:
        for row in _table_rows(table):
            records.append(AgentRecord(**cast(Any, row)))
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    return tuple(records)


@dataclass(frozen=True, slots=True)
class _TrajectorySummary:
    scenario_id: str
    agent_id: str
    trajectory_id: str
    sample_count: int
    first_time_ns: int
    last_time_ns: int
    valid_sample_count: int
    first_valid_time_ns: int | None
    last_valid_time_ns: int | None
    invalid_timestamps: bool


@dataclass(slots=True)
class _TrajectoryAccumulator:
    scenario_id: str
    agent_id: str
    trajectory_id: str
    sample_count: int = 0
    first_time_ns: int | None = None
    last_time_ns: int | None = None
    previous_time_ns: int | None = None
    valid_sample_count: int = 0
    first_valid_time_ns: int | None = None
    last_valid_time_ns: int | None = None
    invalid_timestamps: bool = False
    seen_sample_indices: set[int] | None = None


def _finish_trajectory(
    state: _TrajectoryAccumulator,
) -> _TrajectorySummary:
    if state.first_time_ns is None or state.last_time_ns is None:
        raise SchemaError("trajectory contains no samples")
    return _TrajectorySummary(
        scenario_id=state.scenario_id,
        agent_id=state.agent_id,
        trajectory_id=state.trajectory_id,
        sample_count=state.sample_count,
        first_time_ns=state.first_time_ns,
        last_time_ns=state.last_time_ns,
        valid_sample_count=state.valid_sample_count,
        first_valid_time_ns=state.first_valid_time_ns,
        last_valid_time_ns=state.last_valid_time_ns,
        invalid_timestamps=state.invalid_timestamps,
    )


def _trajectory_summaries(
    batches: Iterable[pa.RecordBatch],
) -> tuple[tuple[_TrajectorySummary, ...], int]:
    summaries: list[_TrajectorySummary] = []
    current: _TrajectoryAccumulator | None = None
    source_sample_count = 0
    previous_key: tuple[str, str] | None = None
    try:
        for batch in batches:
            for row in _batch_rows(batch):
                sample = TrajectorySampleRecord(**cast(Any, row))
                source_sample_count += 1
                key = (sample.scenario_id, sample.trajectory_id)
                if current is None or key != (
                    current.scenario_id,
                    current.trajectory_id,
                ):
                    if current is not None:
                        summaries.append(_finish_trajectory(current))
                    if previous_key is not None and previous_key > key:
                        raise SchemaError(
                            "trajectory rows are not in canonical group order"
                        )
                    previous_key = key
                    current = _TrajectoryAccumulator(
                        scenario_id=sample.scenario_id,
                        agent_id=sample.agent_id,
                        trajectory_id=sample.trajectory_id,
                        seen_sample_indices=set(),
                    )
                if sample.agent_id != current.agent_id:
                    raise SchemaError("one trajectory references multiple agents")
                seen_indices = current.seen_sample_indices
                if seen_indices is None:
                    raise SchemaError("trajectory index state is unavailable")
                if sample.sample_index in seen_indices:
                    raise SchemaError("duplicate trajectory-sample primary key")
                seen_indices.add(sample.sample_index)
                if sample.sample_index != current.sample_count:
                    current.invalid_timestamps = True
                if (
                    current.previous_time_ns is not None
                    and sample.timestamp_ns <= current.previous_time_ns
                ):
                    current.invalid_timestamps = True
                if current.first_time_ns is None:
                    current.first_time_ns = sample.timestamp_ns
                current.last_time_ns = sample.timestamp_ns
                current.previous_time_ns = sample.timestamp_ns
                current.sample_count += 1
                if sample.is_valid:
                    current.valid_sample_count += 1
                    if current.first_valid_time_ns is None:
                        current.first_valid_time_ns = sample.timestamp_ns
                    current.last_valid_time_ns = sample.timestamp_ns
        if current is not None:
            summaries.append(_finish_trajectory(current))
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    return tuple(summaries), source_sample_count


@dataclass(frozen=True, slots=True)
class _MapElement:
    scenario_id: str
    map_element_id: str
    element_type: MapElementType
    geometry_type: MapGeometryType
    parent_element_id: str | None
    successor_ids: tuple[str, ...]
    predecessor_ids: tuple[str, ...]
    left_neighbor_id: str | None
    right_neighbor_id: str | None
    geometry_valid: bool


_GEOMETRY_NAME = {
    "Point": MapGeometryType.POINT,
    "LineString": MapGeometryType.LINESTRING,
    "Polygon": MapGeometryType.POLYGON,
    "MultiLineString": MapGeometryType.MULTILINESTRING,
    "MultiPolygon": MapGeometryType.MULTIPOLYGON,
}


def _reference(
    value: object,
    field_name: str,
    owner_id: str,
) -> str | None:
    if value is None:
        return None
    reference = validate_identifier(_required_text(value, field_name))
    if reference == owner_id:
        raise SchemaError(f"{field_name} may not reference itself")
    return reference


def _references(
    value: object,
    field_name: str,
    owner_id: str,
) -> tuple[str, ...]:
    references = _identifier_tuple(value, field_name)
    if owner_id in references:
        raise SchemaError(f"{field_name} may not reference itself")
    return references


def _semantic_json(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise SchemaError("semantic_attributes_json must be text or null")
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError("semantic_attributes_json is malformed") from None
    if not isinstance(decoded, Mapping):
        raise SchemaError("semantic_attributes_json must contain an object")
    try:
        canonical_json_text(decoded, trailing_newline=False)
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def _map_elements(
    batches: Iterable[pa.RecordBatch],
) -> tuple[_MapElement, ...]:
    elements: list[_MapElement] = []
    keys: set[tuple[str, str]] = set()
    try:
        for batch in batches:
            for row in _batch_rows(batch):
                scenario_id = validate_identifier(cast(str, row["scenario_id"]))
                element_id = validate_identifier(cast(str, row["map_element_id"]))
                key = (scenario_id, element_id)
                if key in keys:
                    raise SchemaError("duplicate vector-map primary key")
                keys.add(key)
                element_type = _enum_value(
                    MapElementType,
                    row["element_type"],
                    "element_type",
                )
                geometry_type = _enum_value(
                    MapGeometryType,
                    row["geometry_type"],
                    "geometry_type",
                )
                _enum_value(
                    Directionality,
                    row["directionality"],
                    "directionality",
                )
                _enum_value(OriginType, row["origin_type"], "origin_type")
                _quality_flags(row["quality_flags"])
                _semantic_json(row["semantic_attributes_json"])
                parent = _reference(
                    row["parent_element_id"],
                    "parent_element_id",
                    element_id,
                )
                successors = _references(
                    row["successor_ids"],
                    "successor_ids",
                    element_id,
                )
                predecessors = _references(
                    row["predecessor_ids"],
                    "predecessor_ids",
                    element_id,
                )
                left = _reference(
                    row["left_neighbor_id"],
                    "left_neighbor_id",
                    element_id,
                )
                right = _reference(
                    row["right_neighbor_id"],
                    "right_neighbor_id",
                    element_id,
                )
                if left is not None and left == right:
                    raise SchemaError("left and right map neighbors must differ")
                geometry_valid = True
                try:
                    geometry = geometry_from_canonical_wkb(
                        cast(bytes, row["geometry_wkb"])
                    )
                    actual_type = _GEOMETRY_NAME.get(geometry.geom_type)
                    if actual_type is not geometry_type:
                        geometry_valid = False
                except ValidationError:
                    geometry_valid = False
                elements.append(
                    _MapElement(
                        scenario_id=scenario_id,
                        map_element_id=element_id,
                        element_type=element_type,
                        geometry_type=geometry_type,
                        parent_element_id=parent,
                        successor_ids=successors,
                        predecessor_ids=predecessors,
                        left_neighbor_id=left,
                        right_neighbor_id=right,
                        geometry_valid=geometry_valid,
                    )
                )
    except SchemaError:
        raise
    except (TypeError, KeyError, ValidationError) as error:
        raise _schema_error(error) from None
    return tuple(elements)


def _primary_keys_unique(
    rows: Sequence[object],
    key: Any,
    label: str,
) -> None:
    keys = tuple(key(row) for row in rows)
    if len(keys) != len(set(keys)):
        raise SchemaError(f"duplicate {label} primary key")


def _frame_matches(scenario: ScenarioRecord, frame: _Frame) -> bool:
    return (
        scenario.scenario_id == frame.scenario_id
        and scenario.coordinate_frame_id == frame.coordinate_frame_id
        and scenario.origin_x_m == frame.origin_x_m
        and scenario.origin_y_m == frame.origin_y_m
        and scenario.origin_z_m == frame.origin_z_m
        and scenario.source_crs == frame.source_crs
        and scenario.has_elevation == frame.has_elevation
        and frame.frame_type == _LOCAL_FRAME_TYPE
        and frame.axis_convention == _AXIS_CONVENTION
        and frame.distance_unit == "m"
        and frame.angle_unit == "rad"
        and frame.timestamp_unit == "ns"
    )


def _put_exclusion(
    exclusions: dict[tuple[ValidationUnitType, str], CanonicalExclusionRecord],
    record: CanonicalExclusionRecord,
) -> None:
    unit_type = _enum_value(
        ValidationUnitType,
        record.unit_type,
        "unit_type",
    )
    exclusions.setdefault((unit_type, record.unit_id), record)


def _validation_report(
    scenarios: tuple[ScenarioRecord, ...],
    frames: tuple[_Frame, ...],
    agents: tuple[AgentRecord, ...],
    trajectories: tuple[_TrajectorySummary, ...],
    source_sample_count: int,
    map_elements: tuple[_MapElement, ...],
    config: CanonicalValidationConfig,
) -> CanonicalValidationReport:
    if not scenarios:
        raise SchemaError("scenario_manifest must contain at least one row")
    _primary_keys_unique(
        scenarios,
        lambda item: item.scenario_id,
        "scenario",
    )
    _primary_keys_unique(
        frames,
        lambda item: (item.scenario_id, item.coordinate_frame_id),
        "coordinate-frame",
    )
    _primary_keys_unique(
        agents,
        lambda item: (item.scenario_id, item.agent_id),
        "agent",
    )
    dataset_ids = {item.dataset_id for item in scenarios}
    dataset_versions = {item.dataset_version for item in scenarios}
    if len(dataset_ids) != 1:
        raise SchemaError("scenario rows contain mixed dataset identifiers")
    if len(dataset_versions) != 1:
        raise SchemaError("scenario rows contain mixed dataset versions")
    dataset_id = next(iter(dataset_ids))
    dataset_version = next(iter(dataset_versions))
    scenario_by_id = {item.scenario_id: item for item in scenarios}

    frames_by_scenario: dict[str, list[_Frame]] = defaultdict(list)
    for frame in frames:
        if frame.scenario_id not in scenario_by_id:
            raise SchemaError("coordinate frame references a missing scenario")
        frames_by_scenario[frame.scenario_id].append(frame)
    for scenario in scenarios:
        owned_frames = frames_by_scenario.get(scenario.scenario_id, [])
        if len(owned_frames) != 1:
            raise SchemaError("each scenario must have exactly one coordinate frame")
        if owned_frames[0].coordinate_frame_id != scenario.coordinate_frame_id:
            raise SchemaError("coordinate frame identity does not match scenario")

    agents_by_scenario: dict[str, list[AgentRecord]] = defaultdict(list)
    agent_by_key: dict[tuple[str, str], AgentRecord] = {}
    for agent in agents:
        if agent.scenario_id not in scenario_by_id:
            raise SchemaError("agent references a missing scenario")
        agents_by_scenario[agent.scenario_id].append(agent)
        agent_by_key[(agent.scenario_id, agent.agent_id)] = agent
    for scenario in scenarios:
        if scenario.agent_count != len(
            agents_by_scenario.get(scenario.scenario_id, ())
        ):
            raise SchemaError("scenario agent_count differs from agent rows")

    trajectory_by_agent: dict[tuple[str, str], list[_TrajectorySummary]] = defaultdict(
        list
    )
    for trajectory in trajectories:
        key = (trajectory.scenario_id, trajectory.agent_id)
        owning_agent = agent_by_key.get(key)
        if owning_agent is None:
            raise SchemaError("trajectory references a missing agent")
        trajectory_by_agent[key].append(trajectory)
        if (
            owning_agent.sample_count != trajectory.sample_count
            or owning_agent.first_time_ns != trajectory.first_time_ns
            or owning_agent.last_time_ns != trajectory.last_time_ns
        ):
            raise SchemaError("agent metadata disagrees with trajectory samples")
    for key, agent in agent_by_key.items():
        owned = trajectory_by_agent.get(key, ())
        if len(owned) != 1:
            raise SchemaError("each canonical agent must own exactly one trajectory")
        if owned[0].sample_count != agent.sample_count:
            raise SchemaError("agent sample_count differs from samples")

    map_by_key = {
        (item.scenario_id, item.map_element_id): item for item in map_elements
    }
    for element in map_elements:
        owning_scenario = scenario_by_id.get(element.scenario_id)
        if owning_scenario is None:
            raise SchemaError("map element references a missing scenario")
        if not owning_scenario.source_map_available:
            raise SchemaError("map rows exist when source_map_available is false")
        references = (
            *((value, "parent") for value in (element.parent_element_id,)),
            *((value, "topology") for value in element.successor_ids),
            *((value, "topology") for value in element.predecessor_ids),
            *((value, "topology") for value in (element.left_neighbor_id,)),
            *((value, "topology") for value in (element.right_neighbor_id,)),
        )
        for reference, kind in references:
            if reference is None:
                continue
            target = map_by_key.get((element.scenario_id, reference))
            if target is None:
                raise SchemaError("map element reference does not resolve")
            if (
                kind == "topology"
                and target.element_type is not MapElementType.LANE_CENTERLINE
            ):
                raise SchemaError(
                    "lane topology reference must target a lane centerline"
                )
        has_topology = bool(
            element.successor_ids
            or element.predecessor_ids
            or element.left_neighbor_id
            or element.right_neighbor_id
        )
        if has_topology and element.element_type is not MapElementType.LANE_CENTERLINE:
            raise SchemaError("non-centerline map element contains lane topology")
        if element.element_type is MapElementType.LANE_BOUNDARY:
            if element.parent_element_id is None:
                raise SchemaError("lane boundary must reference a parent centerline")
            parent = map_by_key[(element.scenario_id, element.parent_element_id)]
            if parent.element_type is not MapElementType.LANE_CENTERLINE:
                raise SchemaError("lane-boundary parent must be a lane centerline")

    exclusions: dict[
        tuple[ValidationUnitType, str],
        CanonicalExclusionRecord,
    ] = {}

    source_scenario_groups: dict[
        tuple[str, str],
        list[ScenarioRecord],
    ] = defaultdict(list)
    for scenario in scenarios:
        if scenario.source_scenario_id is not None:
            source_scenario_groups[
                (scenario.dataset_id, scenario.source_scenario_id)
            ].append(scenario)
    for scenario_group in source_scenario_groups.values():
        if len(scenario_group) > 1:
            ids = sorted(item.scenario_id for item in scenario_group)
            for duplicate_scenario in scenario_group:
                _put_exclusion(
                    exclusions,
                    _new_exclusion(
                        ValidationUnitType.SCENARIO,
                        duplicate_scenario.scenario_id,
                        ExclusionReason.DUPLICATE,
                        "Duplicate source scenario identifier.",
                        tuple(
                            identifier
                            for identifier in ids
                            if identifier != duplicate_scenario.scenario_id
                        ),
                    ),
                )

    coordinate_mismatch_scenarios: set[str] = set()
    for scenario in scenarios:
        frame = frames_by_scenario[scenario.scenario_id][0]
        if not _frame_matches(scenario, frame):
            coordinate_mismatch_scenarios.add(scenario.scenario_id)
            _put_exclusion(
                exclusions,
                _new_exclusion(
                    ValidationUnitType.SCENARIO,
                    scenario.scenario_id,
                    ExclusionReason.COORDINATE_FRAME_MISMATCH,
                    "Coordinate frame metadata does not match the scenario.",
                    (frame.coordinate_frame_id,),
                ),
            )

    duplicate_agents: set[tuple[str, str]] = set()
    source_agent_groups: dict[
        tuple[str, str],
        list[AgentRecord],
    ] = defaultdict(list)
    for agent in agents:
        if agent.source_agent_id is not None:
            source_agent_groups[(agent.scenario_id, agent.source_agent_id)].append(
                agent
            )
    for agent_group in source_agent_groups.values():
        if len(agent_group) > 1:
            ids = sorted(item.agent_id for item in agent_group)
            for duplicate_agent in agent_group:
                duplicate_agents.add(
                    (
                        duplicate_agent.scenario_id,
                        duplicate_agent.agent_id,
                    )
                )
                _put_exclusion(
                    exclusions,
                    _new_exclusion(
                        ValidationUnitType.AGENT,
                        duplicate_agent.agent_id,
                        ExclusionReason.DUPLICATE,
                        "Duplicate source agent identifier.",
                        tuple(
                            identifier
                            for identifier in ids
                            if identifier != duplicate_agent.agent_id
                        ),
                    ),
                )

    unsupported_agents: set[tuple[str, str]] = set()
    for agent in agents:
        if agent.agent_class not in config.allowed_agent_classes:
            unsupported_agents.add((agent.scenario_id, agent.agent_id))
            _put_exclusion(
                exclusions,
                _new_exclusion(
                    ValidationUnitType.AGENT,
                    agent.agent_id,
                    ExclusionReason.UNSUPPORTED_AGENT_CLASS,
                    ("Agent class is not enabled by the validation configuration."),
                    (
                        _enum_value(
                            AgentClass,
                            agent.agent_class,
                            "agent_class",
                        ).value,
                    ),
                ),
            )

    trajectory_eligible: dict[str, bool] = {}
    trajectory_agent: dict[str, tuple[str, str]] = {}
    for trajectory in trajectories:
        scenario = scenario_by_id[trajectory.scenario_id]
        agent_key = (trajectory.scenario_id, trajectory.agent_id)
        trajectory_agent[trajectory.trajectory_id] = agent_key
        eligible = True
        reason: ExclusionReason | None = None
        message = ""
        if (
            trajectory.invalid_timestamps
            or trajectory.first_time_ns < scenario.start_time_ns
            or trajectory.last_time_ns > scenario.end_time_ns
        ):
            eligible = False
            reason = ExclusionReason.INVALID_TIMESTAMPS
            message = (
                "Trajectory timestamps, sample indices, or support interval "
                "are invalid."
            )
        elif trajectory.valid_sample_count < config.minimum_valid_sample_count or (
            trajectory.valid_sample_count < 2 and config.minimum_valid_duration_ns > 0
        ):
            eligible = False
            reason = ExclusionReason.INSUFFICIENT_SAMPLES
            message = "Trajectory has too few valid samples."
        elif trajectory.valid_sample_count >= 2:
            first_valid = trajectory.first_valid_time_ns
            last_valid = trajectory.last_valid_time_ns
            if first_valid is None or last_valid is None:
                raise SchemaError("valid-sample timing state is inconsistent")
            if last_valid - first_valid < config.minimum_valid_duration_ns:
                eligible = False
                reason = ExclusionReason.INSUFFICIENT_DURATION
                message = "Trajectory valid duration is below the threshold."
        if agent_key in duplicate_agents or agent_key in unsupported_agents:
            eligible = False
            reason = None
        trajectory_eligible[trajectory.trajectory_id] = eligible
        if reason is not None:
            _put_exclusion(
                exclusions,
                _new_exclusion(
                    ValidationUnitType.TRAJECTORY,
                    trajectory.trajectory_id,
                    reason,
                    message,
                    (trajectory.agent_id,),
                ),
            )

    valid_map_ids: set[tuple[str, str]] = set()
    for element in map_elements:
        key = (element.scenario_id, element.map_element_id)
        if element.geometry_valid:
            valid_map_ids.add(key)
        else:
            _put_exclusion(
                exclusions,
                _new_exclusion(
                    ValidationUnitType.MAP_ELEMENT,
                    element.map_element_id,
                    ExclusionReason.INVALID_GEOMETRY,
                    "Map geometry is malformed, invalid, or type-inconsistent.",
                    (element.scenario_id,),
                ),
            )

    if config.require_source_map:
        for scenario in scenarios:
            usable = any(
                scenario_id == scenario.scenario_id
                for scenario_id, _element_id in valid_map_ids
            )
            if not scenario.source_map_available or not usable:
                _put_exclusion(
                    exclusions,
                    _new_exclusion(
                        ValidationUnitType.SCENARIO,
                        scenario.scenario_id,
                        ExclusionReason.NO_USABLE_MAP,
                        "Scenario does not have a usable canonical source map.",
                    ),
                )

    scenario_excluded = {
        unit_id
        for (unit_type, unit_id), _record in exclusions.items()
        if unit_type is ValidationUnitType.SCENARIO
    }
    included_trajectory_ids = [
        trajectory.trajectory_id
        for trajectory in trajectories
        if trajectory.scenario_id not in scenario_excluded
        and trajectory_eligible[trajectory.trajectory_id]
    ]
    included_trajectory_id_set = set(included_trajectory_ids)
    included_by_scenario = {
        trajectory.scenario_id
        for trajectory in trajectories
        if trajectory.trajectory_id in included_trajectory_id_set
    }
    for scenario in scenarios:
        if (
            scenario.scenario_id not in scenario_excluded
            and scenario.scenario_id not in included_by_scenario
        ):
            _put_exclusion(
                exclusions,
                _new_exclusion(
                    ValidationUnitType.SCENARIO,
                    scenario.scenario_id,
                    ExclusionReason.INSUFFICIENT_TRAJECTORY_COVERAGE,
                    "Scenario has no eligible canonical trajectory.",
                ),
            )

    scenario_excluded = {
        unit_id
        for (unit_type, unit_id), _record in exclusions.items()
        if unit_type is ValidationUnitType.SCENARIO
    }
    included_trajectory_set = {
        trajectory.trajectory_id
        for trajectory in trajectories
        if trajectory.scenario_id not in scenario_excluded
        and trajectory_eligible[trajectory.trajectory_id]
    }
    included_agent_keys = {
        trajectory_agent[trajectory_id] for trajectory_id in included_trajectory_set
    }

    for key, record in tuple(exclusions.items()):
        if record.unit_type in (
            ValidationUnitType.AGENT,
            ValidationUnitType.TRAJECTORY,
            ValidationUnitType.MAP_ELEMENT,
        ):
            owning_scenario_id: str | None = None
            if record.unit_type is ValidationUnitType.AGENT:
                owning_scenario_id = next(
                    (
                        item.scenario_id
                        for item in agents
                        if item.agent_id == record.unit_id
                    ),
                    None,
                )
            elif record.unit_type is ValidationUnitType.TRAJECTORY:
                owning_scenario_id = trajectory_agent.get(
                    record.unit_id,
                    (None, ""),
                )[0]
            else:
                owning_scenario_id = next(
                    (
                        item.scenario_id
                        for item in map_elements
                        if item.map_element_id == record.unit_id
                    ),
                    None,
                )
            if owning_scenario_id in coordinate_mismatch_scenarios:
                del exclusions[key]

    ordered_exclusions = tuple(sorted(exclusions.values(), key=_exclusion_sort_key))
    counts = tuple(
        (
            reason.value,
            sum(record.reason is reason for record in ordered_exclusions),
        )
        for reason in ExclusionReason
    )
    included_scenarios = tuple(
        scenario.scenario_id
        for scenario in scenarios
        if scenario.scenario_id not in scenario_excluded
    )
    included_agents = tuple(
        agent.agent_id
        for agent in agents
        if (agent.scenario_id, agent.agent_id) in included_agent_keys
        and agent.scenario_id not in scenario_excluded
    )
    included_trajectories = tuple(
        trajectory.trajectory_id
        for trajectory in sorted(
            trajectories,
            key=lambda item: (item.scenario_id, item.trajectory_id),
        )
        if trajectory.trajectory_id in included_trajectory_set
        and trajectory.scenario_id not in scenario_excluded
    )
    included_maps = tuple(
        element.map_element_id
        for element in map_elements
        if (element.scenario_id, element.map_element_id) in valid_map_ids
        and element.scenario_id not in scenario_excluded
    )
    return CanonicalValidationReport(
        schema_version=_SCHEMA_VERSION,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        config=config,
        source_scenario_count=len(scenarios),
        source_coordinate_frame_count=len(frames),
        source_agent_count=len(agents),
        source_trajectory_count=len(trajectories),
        source_sample_count=source_sample_count,
        source_map_element_count=len(map_elements),
        included_scenario_ids=included_scenarios,
        included_agent_ids=included_agents,
        included_trajectory_ids=included_trajectories,
        included_map_element_ids=included_maps,
        exclusions=ordered_exclusions,
        exclusion_reason_counts=counts,
    )


def validate_canonical_tables(
    scenario_manifest: pa.Table,
    coordinate_frame_metadata: pa.Table,
    agent_metadata: pa.Table,
    trajectory_samples: pa.Table,
    vector_map_elements: pa.Table | None = None,
    *,
    config: CanonicalValidationConfig = _DEFAULT_CONFIG,
) -> CanonicalValidationReport:
    """Validate canonical in-memory tables and return eligibility reporting."""
    if not isinstance(config, CanonicalValidationConfig):
        raise ValidationError("config must be CanonicalValidationConfig")
    validate_canonical_table(
        scenario_manifest,
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )
    validate_canonical_table(
        coordinate_frame_metadata,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
    )
    validate_canonical_table(
        agent_metadata,
        CanonicalSchemaName.AGENT_METADATA,
    )
    validate_canonical_table(
        trajectory_samples,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    if vector_map_elements is not None:
        validate_canonical_table(
            vector_map_elements,
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        )
    scenarios = _scenario_records(scenario_manifest)
    frames = _frame_records(coordinate_frame_metadata)
    agents = _agent_records(agent_metadata)
    trajectories, sample_count = _trajectory_summaries(
        trajectory_samples.to_batches(max_chunksize=65_536)
    )
    maps = (
        ()
        if vector_map_elements is None
        else _map_elements(vector_map_elements.to_batches(max_chunksize=65_536))
    )
    return _validation_report(
        scenarios,
        frames,
        agents,
        trajectories,
        sample_count,
        maps,
        config,
    )


def validate_canonical_parquet_dataset(
    repository_root: Path,
    paths: CanonicalDatasetPaths,
    *,
    config: CanonicalValidationConfig = _DEFAULT_CONFIG,
    batch_size: int = 65_536,
) -> CanonicalValidationReport:
    """Validate canonical Parquet with bounded trajectory and map batches."""
    if not isinstance(repository_root, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("repository_root must be a directory")
    if not isinstance(paths, CanonicalDatasetPaths):
        raise ValidationError("paths must be CanonicalDatasetPaths")
    if not isinstance(config, CanonicalValidationConfig):
        raise ValidationError("config must be CanonicalValidationConfig")
    normalized_batch_size = _positive_int(batch_size, "batch_size")
    scenario_table = read_canonical_parquet_table(
        root,
        paths.scenario_manifest,
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )
    frame_table = read_canonical_parquet_table(
        root,
        paths.coordinate_frame_metadata,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
    )
    agent_table = read_canonical_parquet_table(
        root,
        paths.agent_metadata,
        CanonicalSchemaName.AGENT_METADATA,
    )
    trajectory_batches = iter_canonical_parquet_batches(
        root,
        paths.trajectory_samples,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        batch_size=normalized_batch_size,
    )
    map_batches: Iterable[pa.RecordBatch]
    if paths.vector_map_elements:
        map_batches = iter_canonical_parquet_batches(
            root,
            paths.vector_map_elements,
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
            batch_size=normalized_batch_size,
        )
    else:
        map_batches = ()
    scenarios = _scenario_records(scenario_table)
    frames = _frame_records(frame_table)
    agents = _agent_records(agent_table)
    trajectories, sample_count = _trajectory_summaries(trajectory_batches)
    maps = _map_elements(map_batches)
    return _validation_report(
        scenarios,
        frames,
        agents,
        trajectories,
        sample_count,
        maps,
        config,
    )


def materialize_canonical_validation_report(
    run_directory: RunDirectory,
    report: CanonicalValidationReport,
    *,
    relative_directory: str | Path = "artifacts/canonical_validation",
) -> CanonicalValidationArtifacts:
    """Atomically write the three deterministic validation artifacts."""
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be RunDirectory")
    if not isinstance(report, CanonicalValidationReport):
        raise ValidationError("report must be CanonicalValidationReport")
    try:
        directory = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    validation_report = atomic_write_text(
        run_directory,
        directory / "validation_report.json",
        canonical_validation_report_to_canonical_json(report),
    )
    exclusions = atomic_write_text(
        run_directory,
        directory / "exclusions.jsonl",
        canonical_exclusions_jsonl(report),
    )
    summary = atomic_write_text(
        run_directory,
        directory / "validation_summary.md",
        canonical_validation_summary_markdown(report),
    )
    return CanonicalValidationArtifacts(
        validation_report=validation_report,
        exclusions_jsonl=exclusions,
        summary_report=summary,
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
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("validation artifact is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("validation artifact resolves outside repository")
    if candidate.is_symlink() or not resolved.is_file():
        raise ArtifactError("validation artifact must be a regular file")
    digest = hashlib.sha256()
    data = bytearray()
    try:
        with resolved.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                data.extend(chunk)
    except OSError as error:
        raise ArtifactError("cannot read validation artifact") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("validation artifact size differs")
    if digest.hexdigest() != artifact.content_checksum:
        raise ArtifactError("validation artifact SHA-256 differs")
    return bytes(data)


def _utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise SchemaError(f"{label} is not valid UTF-8") from None


def verify_canonical_validation_artifacts(
    repository_root: Path,
    artifacts: CanonicalValidationArtifacts,
) -> CanonicalValidationReport:
    """Verify validation artifact bytes and return the parsed report."""
    if not isinstance(artifacts, CanonicalValidationArtifacts):
        raise ValidationError("artifacts must be CanonicalValidationArtifacts")
    report_text = _utf8(
        _verified_artifact_bytes(
            repository_root,
            artifacts.validation_report,
        ),
        "validation_report.json",
    )
    report = canonical_validation_report_from_json(report_text)
    if report_text != canonical_validation_report_to_canonical_json(report):
        raise SchemaError("validation_report.json is not canonical")
    exclusions_text = _utf8(
        _verified_artifact_bytes(
            repository_root,
            artifacts.exclusions_jsonl,
        ),
        "exclusions.jsonl",
    )
    if exclusions_text != canonical_exclusions_jsonl(report):
        raise SchemaError("exclusions.jsonl differs from validation report")
    summary_text = _utf8(
        _verified_artifact_bytes(
            repository_root,
            artifacts.summary_report,
        ),
        "validation_summary.md",
    )
    if summary_text != canonical_validation_summary_markdown(report):
        raise SchemaError("validation_summary.md differs from validation report")
    return report


def validate_and_materialize_canonical_parquet_dataset(
    run_directory: RunDirectory,
    paths: CanonicalDatasetPaths,
    *,
    config: CanonicalValidationConfig = _DEFAULT_CONFIG,
    batch_size: int = 65_536,
    relative_directory: str | Path = "artifacts/canonical_validation",
) -> tuple[CanonicalValidationReport, CanonicalValidationArtifacts]:
    """Validate canonical Parquet and then materialize its validation report."""
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be RunDirectory")
    report = validate_canonical_parquet_dataset(
        run_directory.repository_root,
        paths,
        config=config,
        batch_size=batch_size,
    )
    artifacts = materialize_canonical_validation_report(
        run_directory,
        report,
        relative_directory=relative_directory,
    )
    return report, artifacts
