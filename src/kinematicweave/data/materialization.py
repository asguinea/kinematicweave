"""Deterministic resumable materialization and immutable cache management."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from enum import StrEnum
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any, cast

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_text,
    check_disk_space,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import validate_identifier
from kinematicweave.paths import normalize_relative_path

__all__ = [
    "IncompleteEntryPolicy",
    "MaterializationArtifacts",
    "MaterializationCacheEntry",
    "MaterializationCacheInventory",
    "MaterializationCacheInventoryEntry",
    "MaterializationDisposition",
    "MaterializationOutput",
    "MaterializationPlan",
    "MaterializationRunReport",
    "MaterializationUnitResult",
    "MaterializationUnitSpec",
    "execute_materialization_plan",
    "materialization_cache_entry_from_dict",
    "materialization_cache_entry_from_json",
    "materialization_cache_entry_to_canonical_json",
    "materialization_cache_entry_to_dict",
    "materialization_cache_inventory_from_dict",
    "materialization_cache_inventory_from_json",
    "materialization_cache_inventory_to_canonical_json",
    "materialization_cache_inventory_to_dict",
    "materialization_output_to_dict",
    "materialization_plan_from_dict",
    "materialization_plan_from_json",
    "materialization_plan_identity",
    "materialization_plan_to_canonical_json",
    "materialization_plan_to_dict",
    "materialization_run_report_from_dict",
    "materialization_run_report_from_json",
    "materialization_run_report_to_canonical_json",
    "materialization_run_report_to_dict",
    "materialization_unit_cache_key",
    "materialization_unit_spec_to_dict",
    "materialize_cached_unit",
    "materialize_materialization_artifacts",
    "prune_incomplete_materialization_cache",
    "scan_materialization_cache",
    "verify_materialization_artifacts",
    "verify_materialization_cache_entry",
]

_SCHEMA_VERSION = "1.0"
_CACHE_MANIFEST = "cache_entry.json"
_ENTRIES_DIRECTORY = "entries"
_TEMPORARY_DIRECTORY = ".temporary"
_PARTIAL_SUFFIX = ".partial"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_RESERVED_OUTPUT_COMPONENTS = {
    ".run.partial",
    ".run.complete",
    _TEMPORARY_DIRECTORY,
}
_DIRECTORY_FSYNC_UNSUPPORTED = {
    errno.EACCES,
    errno.EBADF,
    errno.EINVAL,
    errno.EISDIR,
    getattr(errno, "ENOTSUP", errno.EINVAL),
}


class MaterializationDisposition(StrEnum):
    """Outcome of one cache-backed materialization unit."""

    MATERIALIZED = "materialized"
    REUSED = "reused"


class IncompleteEntryPolicy(StrEnum):
    """Handling policy for an existing deterministic partial entry."""

    RESTART = "restart"
    ERROR = "error"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty trimmed text")
    return value.strip()


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


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


def _path_tuple(value: object, field_name: str) -> tuple[Path, ...]:
    if isinstance(value, (str, bytes, Path)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    paths = tuple(
        normalize_relative_path(cast(str | Path, item))
        for item in cast(Sequence[object], value)
    )
    if not paths:
        raise ValidationError(f"{field_name} must not be empty")
    if len(paths) != len(set(paths)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    for path in paths:
        if any(part in _RESERVED_OUTPUT_COMPONENTS for part in path.parts):
            raise ValidationError(f"{field_name} contains a reserved marker path")
        if path.name == _CACHE_MANIFEST:
            raise ValidationError(f"{field_name} contains reserved cache manifest name")
    part_sets = tuple(path.parts for path in paths)
    for index, left in enumerate(part_sets):
        for right in part_sets[index + 1 :]:
            shorter, longer = (left, right) if len(left) < len(right) else (right, left)
            if longer[: len(shorter)] == shorter:
                raise ValidationError(f"{field_name} contains a path-prefix conflict")
    return paths


def _output_path(value: object, field_name: str) -> Path:
    path = normalize_relative_path(cast(str | Path, value))
    if any(part == _TEMPORARY_DIRECTORY for part in path.parts):
        raise ValidationError(f"{field_name} must not contain .temporary")
    if path.name == _CACHE_MANIFEST:
        raise ValidationError(f"{field_name} must not use cache_entry.json")
    return path


def _path_text(value: str | Path) -> str:
    return normalize_relative_path(value).as_posix()


@dataclass(frozen=True, slots=True)
class MaterializationUnitSpec:
    """One deterministic cacheable materialization operation."""

    unit_id: str
    operation_name: str
    operation_version: str
    input_identity: str
    parameter_identity: str
    expected_output_paths: Sequence[str | Path]
    estimated_output_bytes: int

    def __post_init__(self) -> None:
        """Copy and validate the complete unit contract."""
        object.__setattr__(self, "unit_id", validate_identifier(self.unit_id))
        object.__setattr__(
            self,
            "operation_name",
            _required_text(self.operation_name, "operation_name"),
        )
        object.__setattr__(
            self,
            "operation_version",
            _required_text(self.operation_version, "operation_version"),
        )
        object.__setattr__(
            self,
            "input_identity",
            _sha256(self.input_identity, "input_identity"),
        )
        object.__setattr__(
            self,
            "parameter_identity",
            _sha256(self.parameter_identity, "parameter_identity"),
        )
        object.__setattr__(
            self,
            "expected_output_paths",
            _path_tuple(self.expected_output_paths, "expected_output_paths"),
        )
        object.__setattr__(
            self,
            "estimated_output_bytes",
            _nonnegative_int(self.estimated_output_bytes, "estimated_output_bytes"),
        )


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    """Ordered deterministic materialization units for one dataset."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    units: Sequence[MaterializationUnitSpec]

    def __post_init__(self) -> None:
        """Copy units and reject duplicate labels or logical work."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self, "dataset_id", _required_text(self.dataset_id, "dataset_id")
        )
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        value: object = self.units
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("units must be a non-string sequence")
        units = tuple(cast(Sequence[object], value))
        if not units or any(
            not isinstance(unit, MaterializationUnitSpec) for unit in units
        ):
            raise ValidationError(
                "units must be a nonempty sequence of MaterializationUnitSpec"
            )
        typed_units = cast(tuple[MaterializationUnitSpec, ...], units)
        unit_ids = tuple(unit.unit_id for unit in typed_units)
        if len(unit_ids) != len(set(unit_ids)):
            raise ValidationError("unit identifiers must be unique")
        cache_keys = tuple(materialization_unit_cache_key(unit) for unit in typed_units)
        if len(cache_keys) != len(set(cache_keys)):
            raise ValidationError("unit cache keys must be unique")
        object.__setattr__(self, "units", typed_units)


@dataclass(frozen=True, slots=True)
class MaterializationOutput:
    """Verified output metadata relative to one cache-entry root."""

    relative_path: str | Path
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        """Normalize output metadata."""
        object.__setattr__(
            self,
            "relative_path",
            _output_path(self.relative_path, "relative_path"),
        )
        object.__setattr__(
            self, "size_bytes", _nonnegative_int(self.size_bytes, "size_bytes")
        )
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class MaterializationCacheEntry:
    """Immutable completion manifest for one cache entry."""

    schema_version: str
    cache_key: str
    unit: MaterializationUnitSpec
    outputs: Sequence[MaterializationOutput]

    def __post_init__(self) -> None:
        """Enforce cache identity and exact ordered output contract."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(self, "cache_key", _sha256(self.cache_key, "cache_key"))
        if not isinstance(self.unit, MaterializationUnitSpec):
            raise ValidationError("unit must be MaterializationUnitSpec")
        if self.cache_key != materialization_unit_cache_key(self.unit):
            raise ValidationError("cache_key differs from the unit cache key")
        value: object = self.outputs
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("outputs must be a non-string sequence")
        outputs = tuple(cast(Sequence[object], value))
        if not outputs or any(
            not isinstance(item, MaterializationOutput) for item in outputs
        ):
            raise ValidationError(
                "outputs must be a nonempty sequence of MaterializationOutput"
            )
        typed_outputs = cast(tuple[MaterializationOutput, ...], outputs)
        paths = tuple(item.relative_path for item in typed_outputs)
        if len(paths) != len(set(paths)):
            raise ValidationError("output paths must be unique")
        if paths != self.unit.expected_output_paths:
            raise ValidationError("outputs must exactly match expected_output_paths")
        object.__setattr__(self, "outputs", typed_outputs)

    @property
    def output_count(self) -> int:
        """Return the number of declared output files."""
        return len(self.outputs)

    @property
    def total_output_bytes(self) -> int:
        """Return the exact total output size."""
        return sum(output.size_bytes for output in self.outputs)


@dataclass(frozen=True, slots=True)
class MaterializationUnitResult:
    """One ordered plan-execution result."""

    unit_id: str
    cache_key: str
    disposition: MaterializationDisposition | str
    entry_relative_directory: str | Path
    outputs: Sequence[MaterializationOutput]

    def __post_init__(self) -> None:
        """Normalize result metadata and copy outputs."""
        object.__setattr__(self, "unit_id", validate_identifier(self.unit_id))
        object.__setattr__(self, "cache_key", _sha256(self.cache_key, "cache_key"))
        object.__setattr__(
            self,
            "disposition",
            _enum_value(MaterializationDisposition, self.disposition, "disposition"),
        )
        object.__setattr__(
            self,
            "entry_relative_directory",
            normalize_relative_path(self.entry_relative_directory),
        )
        value: object = self.outputs
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("outputs must be a non-string sequence")
        outputs = tuple(cast(Sequence[object], value))
        if not outputs or any(
            not isinstance(item, MaterializationOutput) for item in outputs
        ):
            raise ValidationError(
                "outputs must be a nonempty sequence of MaterializationOutput"
            )
        typed_outputs = cast(tuple[MaterializationOutput, ...], outputs)
        paths = tuple(item.relative_path for item in typed_outputs)
        if len(paths) != len(set(paths)):
            raise ValidationError("output paths must be unique")
        object.__setattr__(self, "outputs", typed_outputs)

    @property
    def total_output_bytes(self) -> int:
        """Return the exact total output size."""
        return sum(output.size_bytes for output in self.outputs)


@dataclass(frozen=True, slots=True)
class MaterializationRunReport:
    """Complete deterministic report for one executed plan."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    plan_identity: str
    cache_relative_root: str | Path
    results: Sequence[MaterializationUnitResult]

    def __post_init__(self) -> None:
        """Copy results and enforce report-level uniqueness."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self, "dataset_id", _required_text(self.dataset_id, "dataset_id")
        )
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        object.__setattr__(
            self, "plan_identity", _sha256(self.plan_identity, "plan_identity")
        )
        object.__setattr__(
            self,
            "cache_relative_root",
            normalize_relative_path(self.cache_relative_root),
        )
        value: object = self.results
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("results must be a non-string sequence")
        results = tuple(cast(Sequence[object], value))
        if not results or any(
            not isinstance(item, MaterializationUnitResult) for item in results
        ):
            raise ValidationError(
                "results must be a nonempty sequence of MaterializationUnitResult"
            )
        typed_results = cast(tuple[MaterializationUnitResult, ...], results)
        for values, label in (
            (tuple(item.unit_id for item in typed_results), "unit identifiers"),
            (tuple(item.cache_key for item in typed_results), "cache keys"),
            (
                tuple(item.entry_relative_directory for item in typed_results),
                "entry directories",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValidationError(f"result {label} must be unique")
        object.__setattr__(self, "results", typed_results)

    @property
    def unit_count(self) -> int:
        """Return the number of completed units."""
        return len(self.results)

    @property
    def materialized_count(self) -> int:
        """Return newly materialized unit count."""
        return sum(
            result.disposition is MaterializationDisposition.MATERIALIZED
            for result in self.results
        )

    @property
    def reused_count(self) -> int:
        """Return verified cache-hit count."""
        return sum(
            result.disposition is MaterializationDisposition.REUSED
            for result in self.results
        )

    @property
    def total_output_bytes(self) -> int:
        """Return total bytes referenced by all results."""
        return sum(result.total_output_bytes for result in self.results)


@dataclass(frozen=True, slots=True)
class MaterializationCacheInventoryEntry:
    """Summary of one verified complete cache entry."""

    cache_key: str
    unit_id: str
    entry_relative_directory: str | Path
    output_count: int
    total_output_bytes: int

    def __post_init__(self) -> None:
        """Normalize one inventory row."""
        object.__setattr__(self, "cache_key", _sha256(self.cache_key, "cache_key"))
        object.__setattr__(self, "unit_id", validate_identifier(self.unit_id))
        object.__setattr__(
            self,
            "entry_relative_directory",
            normalize_relative_path(self.entry_relative_directory),
        )
        object.__setattr__(
            self, "output_count", _nonnegative_int(self.output_count, "output_count")
        )
        object.__setattr__(
            self,
            "total_output_bytes",
            _nonnegative_int(self.total_output_bytes, "total_output_bytes"),
        )


@dataclass(frozen=True, slots=True)
class MaterializationCacheInventory:
    """Deterministically ordered complete and incomplete cache inventory."""

    schema_version: str
    cache_relative_root: str | Path
    entries: Sequence[MaterializationCacheInventoryEntry]
    incomplete_entry_directories: Sequence[str | Path]

    def __post_init__(self) -> None:
        """Copy inventory rows and enforce canonical ordering and paths."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        cache_root = normalize_relative_path(self.cache_relative_root)
        object.__setattr__(self, "cache_relative_root", cache_root)

        entries_value: object = self.entries
        if isinstance(entries_value, (str, bytes)) or not isinstance(
            entries_value, Sequence
        ):
            raise ValidationError("entries must be a non-string sequence")
        entries = tuple(cast(Sequence[object], entries_value))
        if any(
            not isinstance(item, MaterializationCacheInventoryEntry) for item in entries
        ):
            raise ValidationError(
                "entries must contain MaterializationCacheInventoryEntry values"
            )
        typed_entries = cast(tuple[MaterializationCacheInventoryEntry, ...], entries)
        if tuple(item.cache_key for item in typed_entries) != tuple(
            sorted(item.cache_key for item in typed_entries)
        ):
            raise ValidationError(
                "entries must be ordered lexicographically by cache_key"
            )
        for values, label in (
            (tuple(item.cache_key for item in typed_entries), "cache keys"),
            (tuple(item.unit_id for item in typed_entries), "unit identifiers"),
            (
                tuple(item.entry_relative_directory for item in typed_entries),
                "entry directories",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValidationError(f"inventory {label} must be unique")
        object.__setattr__(self, "entries", typed_entries)

        incomplete_value: object = self.incomplete_entry_directories
        if isinstance(incomplete_value, (str, bytes, Path)) or not isinstance(
            incomplete_value, Sequence
        ):
            raise ValidationError(
                "incomplete_entry_directories must be a non-string sequence"
            )
        incomplete = tuple(
            normalize_relative_path(cast(str | Path, item))
            for item in cast(Sequence[object], incomplete_value)
        )
        if incomplete != tuple(sorted(incomplete, key=Path.as_posix)):
            raise ValidationError(
                "incomplete_entry_directories must be lexicographically ordered"
            )
        if len(incomplete) != len(set(incomplete)):
            raise ValidationError(
                "incomplete_entry_directories must not contain duplicates"
            )
        temporary_root = cache_root / _TEMPORARY_DIRECTORY
        for path in incomplete:
            if (
                not path.is_relative_to(temporary_root)
                or path.parent != temporary_root
                or not path.name.endswith(_PARTIAL_SUFFIX)
            ):
                raise ValidationError(
                    "incomplete entry paths must be direct .partial children "
                    "of the cache .temporary directory"
                )
        complete_paths = {item.entry_relative_directory for item in typed_entries}
        if complete_paths.intersection(incomplete):
            raise ValidationError("complete and incomplete paths must not overlap")
        object.__setattr__(self, "incomplete_entry_directories", incomplete)

    @property
    def complete_entry_count(self) -> int:
        """Return verified complete cache-entry count."""
        return len(self.entries)

    @property
    def incomplete_entry_count(self) -> int:
        """Return detected incomplete cache-entry count."""
        return len(self.incomplete_entry_directories)

    @property
    def total_output_bytes(self) -> int:
        """Return bytes in all verified completed outputs."""
        return sum(entry.total_output_bytes for entry in self.entries)


@dataclass(frozen=True, slots=True)
class MaterializationArtifacts:
    """Operational plan, report, and inventory artifacts."""

    plan_manifest: WrittenArtifact
    run_report: WrittenArtifact
    cache_inventory: WrittenArtifact

    def __post_init__(self) -> None:
        """Require WrittenArtifact values at unique paths."""
        artifacts = (self.plan_manifest, self.run_report, self.cache_inventory)
        if any(not isinstance(item, WrittenArtifact) for item in artifacts):
            raise ValidationError("materialization artifacts must use WrittenArtifact")
        if len({item.relative_path for item in artifacts}) != len(artifacts):
            raise ValidationError("materialization artifact paths must be unique")


def materialization_unit_spec_to_dict(
    unit: MaterializationUnitSpec,
) -> dict[str, object]:
    """Return an ordered JSON-compatible unit representation."""
    if not isinstance(unit, MaterializationUnitSpec):
        raise ValidationError("unit must be MaterializationUnitSpec")
    return {
        "unit_id": unit.unit_id,
        "operation_name": unit.operation_name,
        "operation_version": unit.operation_version,
        "input_identity": unit.input_identity,
        "parameter_identity": unit.parameter_identity,
        "expected_output_paths": [
            _path_text(path) for path in unit.expected_output_paths
        ],
        "estimated_output_bytes": unit.estimated_output_bytes,
    }


def materialization_plan_to_dict(plan: MaterializationPlan) -> dict[str, object]:
    """Return an ordered JSON-compatible plan representation."""
    if not isinstance(plan, MaterializationPlan):
        raise ValidationError("plan must be MaterializationPlan")
    return {
        "schema_version": plan.schema_version,
        "dataset_id": plan.dataset_id,
        "dataset_version": plan.dataset_version,
        "units": [materialization_unit_spec_to_dict(unit) for unit in plan.units],
    }


def materialization_output_to_dict(
    output: MaterializationOutput,
) -> dict[str, object]:
    """Return ordered JSON-compatible output metadata."""
    if not isinstance(output, MaterializationOutput):
        raise ValidationError("output must be MaterializationOutput")
    return {
        "relative_path": _path_text(output.relative_path),
        "size_bytes": output.size_bytes,
        "sha256": output.sha256,
    }


def materialization_cache_entry_to_dict(
    entry: MaterializationCacheEntry,
) -> dict[str, object]:
    """Return an ordered JSON-compatible cache manifest."""
    if not isinstance(entry, MaterializationCacheEntry):
        raise ValidationError("entry must be MaterializationCacheEntry")
    return {
        "schema_version": entry.schema_version,
        "cache_key": entry.cache_key,
        "unit": materialization_unit_spec_to_dict(entry.unit),
        "outputs": [materialization_output_to_dict(item) for item in entry.outputs],
    }


def _unit_result_to_dict(result: MaterializationUnitResult) -> dict[str, object]:
    return {
        "unit_id": result.unit_id,
        "cache_key": result.cache_key,
        "disposition": _enum_value(
            MaterializationDisposition, result.disposition, "disposition"
        ).value,
        "entry_relative_directory": _path_text(result.entry_relative_directory),
        "outputs": [materialization_output_to_dict(item) for item in result.outputs],
    }


def materialization_run_report_to_dict(
    report: MaterializationRunReport,
) -> dict[str, object]:
    """Return an ordered JSON-compatible plan execution report."""
    if not isinstance(report, MaterializationRunReport):
        raise ValidationError("report must be MaterializationRunReport")
    return {
        "schema_version": report.schema_version,
        "dataset_id": report.dataset_id,
        "dataset_version": report.dataset_version,
        "plan_identity": report.plan_identity,
        "cache_relative_root": _path_text(report.cache_relative_root),
        "results": [_unit_result_to_dict(result) for result in report.results],
    }


def _inventory_entry_to_dict(
    entry: MaterializationCacheInventoryEntry,
) -> dict[str, object]:
    return {
        "cache_key": entry.cache_key,
        "unit_id": entry.unit_id,
        "entry_relative_directory": _path_text(entry.entry_relative_directory),
        "output_count": entry.output_count,
        "total_output_bytes": entry.total_output_bytes,
    }


def materialization_cache_inventory_to_dict(
    inventory: MaterializationCacheInventory,
) -> dict[str, object]:
    """Return an ordered JSON-compatible cache inventory."""
    if not isinstance(inventory, MaterializationCacheInventory):
        raise ValidationError("inventory must be MaterializationCacheInventory")
    return {
        "schema_version": inventory.schema_version,
        "cache_relative_root": _path_text(inventory.cache_relative_root),
        "entries": [_inventory_entry_to_dict(item) for item in inventory.entries],
        "incomplete_entry_directories": [
            _path_text(path) for path in inventory.incomplete_entry_directories
        ],
    }


def materialization_unit_cache_key(unit: MaterializationUnitSpec) -> str:
    """Return the logical work identity for one materialization unit."""
    if not isinstance(unit, MaterializationUnitSpec):
        raise ValidationError("unit must be MaterializationUnitSpec")
    return canonical_sha256(
        "materialization-unit-cache-key",
        [
            unit.operation_name,
            unit.operation_version,
            unit.input_identity,
            unit.parameter_identity,
            [_path_text(path) for path in unit.expected_output_paths],
        ],
    )


def materialization_plan_identity(plan: MaterializationPlan) -> str:
    """Return the deterministic identity of a complete ordered plan."""
    return canonical_sha256("materialization-plan", materialization_plan_to_dict(plan))


def materialization_plan_to_canonical_json(plan: MaterializationPlan) -> str:
    """Serialize a plan as canonical JSON with exactly one newline."""
    return canonical_json_text(materialization_plan_to_dict(plan))


def materialization_cache_entry_to_canonical_json(
    entry: MaterializationCacheEntry,
) -> str:
    """Serialize a cache entry as canonical JSON with exactly one newline."""
    return canonical_json_text(materialization_cache_entry_to_dict(entry))


def materialization_run_report_to_canonical_json(
    report: MaterializationRunReport,
) -> str:
    """Serialize a run report as canonical JSON with exactly one newline."""
    return canonical_json_text(materialization_run_report_to_dict(report))


def materialization_cache_inventory_to_canonical_json(
    inventory: MaterializationCacheInventory,
) -> str:
    """Serialize a cache inventory as canonical JSON with exactly one newline."""
    return canonical_json_text(materialization_cache_inventory_to_dict(inventory))


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


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SchemaError(f"{label} must be an array")
    return cast(Sequence[object], value)


_UNIT_FIELDS = tuple(field.name for field in fields(MaterializationUnitSpec))
_PLAN_FIELDS = tuple(field.name for field in fields(MaterializationPlan))
_OUTPUT_FIELDS = tuple(field.name for field in fields(MaterializationOutput))
_CACHE_ENTRY_FIELDS = tuple(field.name for field in fields(MaterializationCacheEntry))
_UNIT_RESULT_FIELDS = tuple(field.name for field in fields(MaterializationUnitResult))
_RUN_REPORT_FIELDS = tuple(field.name for field in fields(MaterializationRunReport))
_INVENTORY_ENTRY_FIELDS = tuple(
    field.name for field in fields(MaterializationCacheInventoryEntry)
)
_INVENTORY_FIELDS = tuple(field.name for field in fields(MaterializationCacheInventory))


def _unit_from_value(
    value: object, label: str = "materialization unit"
) -> MaterializationUnitSpec:
    mapping = _exact_mapping(value, _UNIT_FIELDS, label)
    return MaterializationUnitSpec(**cast(Any, dict(mapping)))


def _output_from_value(value: object) -> MaterializationOutput:
    mapping = _exact_mapping(value, _OUTPUT_FIELDS, "materialization output")
    return MaterializationOutput(**cast(Any, dict(mapping)))


def materialization_plan_from_dict(
    value: Mapping[str, object],
) -> MaterializationPlan:
    """Reconstruct a plan from one exact JSON mapping."""
    mapping = _exact_mapping(value, _PLAN_FIELDS, "materialization plan")
    try:
        units = tuple(
            _unit_from_value(item) for item in _sequence(mapping["units"], "units")
        )
        return MaterializationPlan(
            schema_version=cast(str, mapping["schema_version"]),
            dataset_id=cast(str, mapping["dataset_id"]),
            dataset_version=cast(str, mapping["dataset_version"]),
            units=units,
        )
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def materialization_cache_entry_from_dict(
    value: Mapping[str, object],
) -> MaterializationCacheEntry:
    """Reconstruct a cache manifest from one exact JSON mapping."""
    mapping = _exact_mapping(value, _CACHE_ENTRY_FIELDS, "materialization cache entry")
    try:
        unit = _unit_from_value(mapping["unit"])
        outputs = tuple(
            _output_from_value(item)
            for item in _sequence(mapping["outputs"], "outputs")
        )
        return MaterializationCacheEntry(
            schema_version=cast(str, mapping["schema_version"]),
            cache_key=cast(str, mapping["cache_key"]),
            unit=unit,
            outputs=outputs,
        )
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def materialization_run_report_from_dict(
    value: Mapping[str, object],
) -> MaterializationRunReport:
    """Reconstruct a run report from one exact JSON mapping."""
    mapping = _exact_mapping(value, _RUN_REPORT_FIELDS, "materialization run report")
    try:
        results = []
        for item in _sequence(mapping["results"], "results"):
            result_mapping = _exact_mapping(
                item, _UNIT_RESULT_FIELDS, "materialization unit result"
            )
            outputs = tuple(
                _output_from_value(output)
                for output in _sequence(result_mapping["outputs"], "outputs")
            )
            results.append(
                MaterializationUnitResult(
                    unit_id=cast(str, result_mapping["unit_id"]),
                    cache_key=cast(str, result_mapping["cache_key"]),
                    disposition=cast(str, result_mapping["disposition"]),
                    entry_relative_directory=cast(
                        str, result_mapping["entry_relative_directory"]
                    ),
                    outputs=outputs,
                )
            )
        return MaterializationRunReport(
            schema_version=cast(str, mapping["schema_version"]),
            dataset_id=cast(str, mapping["dataset_id"]),
            dataset_version=cast(str, mapping["dataset_version"]),
            plan_identity=cast(str, mapping["plan_identity"]),
            cache_relative_root=cast(str, mapping["cache_relative_root"]),
            results=tuple(results),
        )
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def materialization_cache_inventory_from_dict(
    value: Mapping[str, object],
) -> MaterializationCacheInventory:
    """Reconstruct a cache inventory from one exact JSON mapping."""
    mapping = _exact_mapping(
        value, _INVENTORY_FIELDS, "materialization cache inventory"
    )
    try:
        entries = tuple(
            MaterializationCacheInventoryEntry(
                **cast(
                    Any,
                    dict(
                        _exact_mapping(
                            item,
                            _INVENTORY_ENTRY_FIELDS,
                            "materialization cache inventory entry",
                        )
                    ),
                )
            )
            for item in _sequence(mapping["entries"], "entries")
        )
        incomplete = tuple(
            cast(str, item)
            for item in _sequence(
                mapping["incomplete_entry_directories"],
                "incomplete_entry_directories",
            )
        )
        return MaterializationCacheInventory(
            schema_version=cast(str, mapping["schema_version"]),
            cache_relative_root=cast(str, mapping["cache_relative_root"]),
            entries=entries,
            incomplete_entry_directories=incomplete,
        )
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def _from_json(
    text: str,
    label: str,
    loader: Callable[[Mapping[str, object]], object],
) -> object:
    if not isinstance(text, str):
        raise SchemaError(f"{label} JSON must be text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError(f"{label} JSON is malformed") from None
    if not isinstance(value, Mapping):
        raise SchemaError(f"{label} JSON root must be an object")
    return loader(cast(Mapping[str, object], value))


def materialization_plan_from_json(text: str) -> MaterializationPlan:
    """Parse one strict materialization plan JSON document."""
    return cast(
        MaterializationPlan,
        _from_json(text, "materialization plan", materialization_plan_from_dict),
    )


def materialization_cache_entry_from_json(text: str) -> MaterializationCacheEntry:
    """Parse one strict materialization cache-entry JSON document."""
    return cast(
        MaterializationCacheEntry,
        _from_json(
            text,
            "materialization cache entry",
            materialization_cache_entry_from_dict,
        ),
    )


def materialization_run_report_from_json(text: str) -> MaterializationRunReport:
    """Parse one strict materialization run-report JSON document."""
    return cast(
        MaterializationRunReport,
        _from_json(
            text,
            "materialization run report",
            materialization_run_report_from_dict,
        ),
    )


def materialization_cache_inventory_from_json(
    text: str,
) -> MaterializationCacheInventory:
    """Parse one strict materialization cache-inventory JSON document."""
    return cast(
        MaterializationCacheInventory,
        _from_json(
            text,
            "materialization cache inventory",
            materialization_cache_inventory_from_dict,
        ),
    )


def _repository_root(value: object) -> Path:
    if not isinstance(value, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        root = value.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("repository_root must be a directory")
    return root


def _cache_paths(
    repository_root: Path,
    cache_relative_root: str | Path,
    cache_key: str,
) -> tuple[Path, Path, Path]:
    root = _repository_root(repository_root)
    try:
        relative_root = normalize_relative_path(cache_relative_root)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    cache_root = root / relative_root
    resolved_cache_root = cache_root.resolve(strict=False)
    if resolved_cache_root == root or not resolved_cache_root.is_relative_to(root):
        raise ArtifactError("cache root resolves outside repository_root")
    complete = cache_root / _ENTRIES_DIRECTORY / cache_key[:2] / cache_key
    incomplete = cache_root / _TEMPORARY_DIRECTORY / f"{cache_key}{_PARTIAL_SUFFIX}"
    return cache_root, complete, incomplete


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _repository_relative(root: Path, path: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ArtifactError("path is outside repository_root") from None
    return normalize_relative_path(relative)


def _assert_no_symlink_components(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ArtifactError("path is outside repository_root") from None
    current = root
    for part in relative.parts:
        current = current / part
        if _lexists(current) and current.is_symlink():
            raise ArtifactError("cache path contains a symbolic link")


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise ArtifactError(f"cannot read cache output: {path.name}") from error
    return size, digest.hexdigest()


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SchemaError(f"{label} is not valid UTF-8") from None
    except OSError as error:
        raise ArtifactError(f"cannot read {label}") from error


def _required_parent_directories(paths: Sequence[Path]) -> set[Path]:
    required: set[Path] = set()
    for path in paths:
        parent = path.parent
        while parent != Path("."):
            required.add(parent)
            parent = parent.parent
    return required


def _inspect_entry_tree(
    entry_root: Path,
    expected_files: Sequence[Path],
    *,
    include_manifest: bool,
) -> None:
    allowed_files = set(expected_files)
    if include_manifest:
        allowed_files.add(Path(_CACHE_MANIFEST))
    allowed_directories = _required_parent_directories(expected_files)
    observed_files: set[Path] = set()
    observed_directories: set[Path] = set()
    try:
        for current_text, directory_names, file_names in os.walk(
            entry_root, topdown=True, followlinks=False
        ):
            current = Path(current_text)
            for name in tuple(directory_names):
                candidate = current / name
                if candidate.is_symlink():
                    raise ArtifactError("cache entry contains a symbolic link")
                if not candidate.is_dir():
                    raise ArtifactError("cache entry contains a non-directory node")
                observed_directories.add(candidate.relative_to(entry_root))
            for name in file_names:
                candidate = current / name
                if candidate.is_symlink():
                    raise ArtifactError("cache entry contains a symbolic link")
                if not candidate.is_file():
                    raise ArtifactError("cache entry contains a non-regular file")
                observed_files.add(candidate.relative_to(entry_root))
    except OSError as error:
        raise ArtifactError("cannot inspect cache entry") from error
    if observed_files != allowed_files:
        missing = allowed_files - observed_files
        if missing:
            raise ArtifactError("cache entry is missing a declared output")
        raise ArtifactError("cache entry contains an unexpected regular file")
    if observed_directories != allowed_directories:
        missing = allowed_directories - observed_directories
        if missing:
            raise ArtifactError("cache entry is missing an output parent directory")
        raise ArtifactError("cache entry contains an unexpected directory")


def verify_materialization_cache_entry(
    repository_root: Path,
    entry_relative_directory: str | Path,
    *,
    expected_unit: MaterializationUnitSpec | None = None,
) -> MaterializationCacheEntry:
    """Verify one immutable cache entry without mutation or repair."""
    root = _repository_root(repository_root)
    try:
        relative = normalize_relative_path(entry_relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    candidate = root / relative
    _assert_no_symlink_components(root, candidate)
    if not _lexists(candidate):
        raise ArtifactError("materialization cache entry is missing")
    if candidate.is_symlink() or not candidate.is_dir():
        raise ArtifactError("materialization cache entry must be a regular directory")
    manifest_path = candidate / _CACHE_MANIFEST
    if (
        not _lexists(manifest_path)
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        raise ArtifactError("cache_entry.json is missing or not a regular file")
    text = _read_utf8(manifest_path, _CACHE_MANIFEST)
    entry = materialization_cache_entry_from_json(text)
    if text != materialization_cache_entry_to_canonical_json(entry):
        raise SchemaError("cache_entry.json is not canonical")
    if candidate.name != entry.cache_key:
        raise SchemaError("cache-entry directory name differs from cache key")
    if candidate.parent.name != entry.cache_key[:2]:
        raise SchemaError("cache-entry fan-out directory differs from cache key")
    if expected_unit is not None:
        if not isinstance(expected_unit, MaterializationUnitSpec):
            raise ValidationError("expected_unit must be MaterializationUnitSpec")
        if entry.unit != expected_unit:
            raise SchemaError("cache-entry unit differs from expected_unit")
    _inspect_entry_tree(
        candidate,
        tuple(
            normalize_relative_path(output.relative_path) for output in entry.outputs
        ),
        include_manifest=True,
    )
    for output in entry.outputs:
        size, digest = _hash_file(candidate / output.relative_path)
        if size != output.size_bytes:
            raise ArtifactError("cache output size differs from manifest")
        if digest != output.sha256:
            raise ArtifactError("cache output SHA-256 differs from manifest")
    return entry


def _reserve_fraction(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(
            "reserve_fraction must be a finite non-Boolean number in [0, 1)"
        )
    try:
        normalized = float(value)
    except OverflowError:
        raise ValidationError(
            "reserve_fraction must be a finite non-Boolean number in [0, 1)"
        ) from None
    if not math.isfinite(normalized) or normalized < 0 or normalized >= 1:
        raise ValidationError(
            "reserve_fraction must be a finite non-Boolean number in [0, 1)"
        )
    return normalized


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as error:
        if error.errno in _DIRECTORY_FSYNC_UNSUPPORTED:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in _DIRECTORY_FSYNC_UNSUPPORTED:
                raise
    finally:
        os.close(descriptor)


def _fsync_output_tree(root: Path, outputs: Sequence[Path]) -> None:
    for output in outputs:
        _fsync_file(root / output)
    directories = sorted(
        _required_parent_directories(outputs),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        _fsync_directory(root / directory)
    _fsync_directory(root)


def _safe_remove_incomplete(path: Path, expected_parent: Path) -> None:
    if path.parent != expected_parent or not path.name.endswith(_PARTIAL_SUFFIX):
        raise ArtifactError("refusing to remove a non-cache incomplete path")
    if not _lexists(path):
        return
    if path.is_symlink():
        raise ArtifactError("incomplete cache entry must not be a symbolic link")
    if not path.is_dir():
        raise ArtifactError("incomplete cache entry must be a directory")
    shutil.rmtree(path)


def _result(
    root: Path,
    entry_directory: Path,
    entry: MaterializationCacheEntry,
    disposition: MaterializationDisposition,
) -> MaterializationUnitResult:
    return MaterializationUnitResult(
        unit_id=entry.unit.unit_id,
        cache_key=entry.cache_key,
        disposition=disposition,
        entry_relative_directory=_repository_relative(root, entry_directory),
        outputs=entry.outputs,
    )


def materialize_cached_unit(
    repository_root: Path,
    cache_relative_root: str | Path,
    unit: MaterializationUnitSpec,
    worker: Callable[[MaterializationUnitSpec, Path], None],
    *,
    incomplete_policy: IncompleteEntryPolicy | str = IncompleteEntryPolicy.RESTART,
    reserve_fraction: float = 0.15,
) -> MaterializationUnitResult:
    """Materialize or strictly reuse one deterministic immutable cache entry."""
    root = _repository_root(repository_root)
    if not isinstance(unit, MaterializationUnitSpec):
        raise ValidationError("unit must be MaterializationUnitSpec")
    if not callable(worker):
        raise ValidationError("worker must be callable")
    policy = _enum_value(IncompleteEntryPolicy, incomplete_policy, "incomplete_policy")
    reserve = _reserve_fraction(reserve_fraction)
    cache_key = materialization_unit_cache_key(unit)
    cache_root, complete, incomplete = _cache_paths(
        root, cache_relative_root, cache_key
    )
    _assert_no_symlink_components(root, cache_root)

    if _lexists(complete):
        entry = verify_materialization_cache_entry(
            root,
            _repository_relative(root, complete),
            expected_unit=unit,
        )
        return _result(root, complete, entry, MaterializationDisposition.REUSED)

    check_disk_space(
        root,
        required_bytes=unit.estimated_output_bytes,
        reserve_fraction=reserve,
    )
    temporary_root = cache_root / _TEMPORARY_DIRECTORY
    if _lexists(incomplete):
        if incomplete.is_symlink():
            raise ArtifactError("incomplete cache entry must not be a symbolic link")
        if policy is IncompleteEntryPolicy.ERROR:
            raise ArtifactError("incomplete cache entry already exists")
        _safe_remove_incomplete(incomplete, temporary_root)

    temporary_root.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(root, temporary_root)
    try:
        incomplete.mkdir(exist_ok=False)
    except FileExistsError:
        raise ArtifactError("incomplete cache entry was created concurrently") from None

    promoted = False
    try:
        worker_result = cast(Callable[[MaterializationUnitSpec, Path], object], worker)(
            unit, incomplete
        )
        if worker_result is not None:
            raise ArtifactError("worker must return None")
        expected_paths = cast(tuple[Path, ...], unit.expected_output_paths)
        _inspect_entry_tree(incomplete, expected_paths, include_manifest=False)
        _fsync_output_tree(incomplete, expected_paths)
        outputs = tuple(
            MaterializationOutput(
                relative_path=path,
                size_bytes=size,
                sha256=digest,
            )
            for path in expected_paths
            for size, digest in (_hash_file(incomplete / path),)
        )
        entry = MaterializationCacheEntry(
            schema_version=_SCHEMA_VERSION,
            cache_key=cache_key,
            unit=unit,
            outputs=outputs,
        )
        manifest_path = incomplete / _CACHE_MANIFEST
        manifest_text = materialization_cache_entry_to_canonical_json(entry)
        with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(manifest_text)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(incomplete)

        complete.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(root, complete.parent)
        try:
            os.rename(incomplete, complete)
            promoted = True
            _fsync_directory(complete.parent)
        except OSError as error:
            if error.errno not in {
                errno.EEXIST,
                errno.ENOTEMPTY,
                errno.EACCES,
            } or not _lexists(complete):
                raise
            competing = verify_materialization_cache_entry(
                root,
                _repository_relative(root, complete),
                expected_unit=unit,
            )
            _safe_remove_incomplete(incomplete, temporary_root)
            return _result(root, complete, competing, MaterializationDisposition.REUSED)

        verified = verify_materialization_cache_entry(
            root,
            _repository_relative(root, complete),
            expected_unit=unit,
        )
        return _result(
            root, complete, verified, MaterializationDisposition.MATERIALIZED
        )
    except BaseException as error:
        if not promoted and _lexists(incomplete):
            try:
                _safe_remove_incomplete(incomplete, temporary_root)
            except OSError as cleanup_error:
                error.add_note(f"incomplete-entry cleanup also failed: {cleanup_error}")
        raise


def execute_materialization_plan(
    repository_root: Path,
    cache_relative_root: str | Path,
    plan: MaterializationPlan,
    worker: Callable[[MaterializationUnitSpec, Path], None],
    *,
    incomplete_policy: IncompleteEntryPolicy | str = IncompleteEntryPolicy.RESTART,
    reserve_fraction: float = 0.15,
) -> MaterializationRunReport:
    """Execute a plan sequentially, retaining every completed unit checkpoint."""
    if not isinstance(plan, MaterializationPlan):
        raise ValidationError("plan must be MaterializationPlan")
    cache_root = normalize_relative_path(cache_relative_root)
    results = tuple(
        materialize_cached_unit(
            repository_root,
            cache_root,
            unit,
            worker,
            incomplete_policy=incomplete_policy,
            reserve_fraction=reserve_fraction,
        )
        for unit in plan.units
    )
    return MaterializationRunReport(
        schema_version=_SCHEMA_VERSION,
        dataset_id=plan.dataset_id,
        dataset_version=plan.dataset_version,
        plan_identity=materialization_plan_identity(plan),
        cache_relative_root=cache_root,
        results=results,
    )


def _validate_partial_tree(path: Path) -> None:
    try:
        for current_text, directory_names, file_names in os.walk(
            path, topdown=True, followlinks=False
        ):
            current = Path(current_text)
            for name in tuple(directory_names) + tuple(file_names):
                if (current / name).is_symlink():
                    raise ArtifactError(
                        "incomplete cache entry contains a symbolic link"
                    )
    except OSError as error:
        raise ArtifactError("cannot inspect incomplete cache entry") from error


def scan_materialization_cache(
    repository_root: Path,
    cache_relative_root: str | Path,
) -> MaterializationCacheInventory:
    """Strictly inspect complete and incomplete cache state without writes."""
    root = _repository_root(repository_root)
    relative_root = normalize_relative_path(cache_relative_root)
    cache_root = root / relative_root
    _assert_no_symlink_components(root, cache_root)
    if not _lexists(cache_root):
        return MaterializationCacheInventory(
            schema_version=_SCHEMA_VERSION,
            cache_relative_root=relative_root,
            entries=(),
            incomplete_entry_directories=(),
        )
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise ArtifactError("cache root must be a regular directory")
    allowed_root_names = {_ENTRIES_DIRECTORY, _TEMPORARY_DIRECTORY}
    for child in cache_root.iterdir():
        if child.name not in allowed_root_names:
            raise ArtifactError("cache root contains unexpected structure")
        if child.is_symlink():
            raise ArtifactError("cache structural path contains a symbolic link")
        if not child.is_dir():
            raise ArtifactError("cache structural path must be a regular directory")

    entries_root = cache_root / _ENTRIES_DIRECTORY
    inventory_entries: list[MaterializationCacheInventoryEntry] = []
    if _lexists(entries_root):
        for fanout in sorted(entries_root.iterdir(), key=lambda path: path.name):
            if (
                fanout.is_symlink()
                or not fanout.is_dir()
                or re.fullmatch(r"[0-9a-f]{2}", fanout.name) is None
            ):
                raise ArtifactError("cache entries contain invalid fan-out structure")
            children = tuple(sorted(fanout.iterdir(), key=lambda path: path.name))
            if not children:
                raise ArtifactError("cache entries contain an empty fan-out directory")
            for entry_directory in children:
                if (
                    entry_directory.is_symlink()
                    or not entry_directory.is_dir()
                    or _SHA256_PATTERN.fullmatch(entry_directory.name) is None
                    or entry_directory.name[:2] != fanout.name
                ):
                    raise ArtifactError("cache entries contain invalid entry structure")
                entry = verify_materialization_cache_entry(
                    root, _repository_relative(root, entry_directory)
                )
                inventory_entries.append(
                    MaterializationCacheInventoryEntry(
                        cache_key=entry.cache_key,
                        unit_id=entry.unit.unit_id,
                        entry_relative_directory=_repository_relative(
                            root, entry_directory
                        ),
                        output_count=entry.output_count,
                        total_output_bytes=entry.total_output_bytes,
                    )
                )

    incomplete_paths: list[Path] = []
    temporary_root = cache_root / _TEMPORARY_DIRECTORY
    if _lexists(temporary_root):
        for partial in sorted(temporary_root.iterdir(), key=lambda path: path.name):
            key_text = partial.name.removesuffix(_PARTIAL_SUFFIX)
            if (
                partial.is_symlink()
                or not partial.is_dir()
                or not partial.name.endswith(_PARTIAL_SUFFIX)
                or _SHA256_PATTERN.fullmatch(key_text) is None
            ):
                raise ArtifactError("cache .temporary contains unexpected structure")
            _validate_partial_tree(partial)
            incomplete_paths.append(_repository_relative(root, partial))

    inventory_entries.sort(key=lambda item: item.cache_key)
    incomplete_paths.sort(key=Path.as_posix)
    return MaterializationCacheInventory(
        schema_version=_SCHEMA_VERSION,
        cache_relative_root=relative_root,
        entries=tuple(inventory_entries),
        incomplete_entry_directories=tuple(incomplete_paths),
    )


def prune_incomplete_materialization_cache(
    repository_root: Path,
    cache_relative_root: str | Path,
    *,
    dry_run: bool = True,
) -> tuple[str, ...]:
    """List or explicitly remove only validated incomplete cache entries."""
    if not isinstance(dry_run, bool):
        raise ValidationError("dry_run must be a Boolean")
    root = _repository_root(repository_root)
    inventory = scan_materialization_cache(root, cache_relative_root)
    paths = tuple(_path_text(path) for path in inventory.incomplete_entry_directories)
    if dry_run:
        return paths
    cache_root = root / inventory.cache_relative_root
    temporary_root = cache_root / _TEMPORARY_DIRECTORY
    for relative in inventory.incomplete_entry_directories:
        _safe_remove_incomplete(root / relative, temporary_root)
    return paths


def _validate_artifact_objects(
    plan: MaterializationPlan,
    report: MaterializationRunReport,
    inventory: MaterializationCacheInventory,
) -> None:
    if not isinstance(plan, MaterializationPlan):
        raise ValidationError("plan must be MaterializationPlan")
    if not isinstance(report, MaterializationRunReport):
        raise ValidationError("report must be MaterializationRunReport")
    if not isinstance(inventory, MaterializationCacheInventory):
        raise ValidationError("inventory must be MaterializationCacheInventory")
    if (
        plan.dataset_id != report.dataset_id
        or plan.dataset_version != report.dataset_version
    ):
        raise ValidationError("plan and report dataset metadata differ")
    if report.plan_identity != materialization_plan_identity(plan):
        raise ValidationError("report plan_identity differs from plan")
    plan_unit_ids = tuple(unit.unit_id for unit in plan.units)
    result_unit_ids = tuple(result.unit_id for result in report.results)
    if result_unit_ids != plan_unit_ids:
        raise ValidationError("report result units or order differ from plan")
    if report.cache_relative_root != inventory.cache_relative_root:
        raise ValidationError("report and inventory cache roots differ")
    inventory_by_directory = {
        entry.entry_relative_directory: entry for entry in inventory.entries
    }
    for result in report.results:
        inventory_entry = inventory_by_directory.get(result.entry_relative_directory)
        if inventory_entry is None:
            raise ValidationError("report entry directory is absent from inventory")
        if inventory_entry.cache_key != result.cache_key:
            raise ValidationError("report and inventory cache keys differ")


def materialize_materialization_artifacts(
    run_directory: RunDirectory,
    plan: MaterializationPlan,
    report: MaterializationRunReport,
    inventory: MaterializationCacheInventory,
    *,
    relative_directory: str | Path = "artifacts/materialization",
) -> MaterializationArtifacts:
    """Atomically write the exact operational materialization artifacts."""
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be RunDirectory")
    _validate_artifact_objects(plan, report, inventory)
    directory = normalize_relative_path(relative_directory)
    plan_artifact = atomic_write_text(
        run_directory,
        directory / "materialization_plan.json",
        materialization_plan_to_canonical_json(plan),
    )
    report_artifact = atomic_write_text(
        run_directory,
        directory / "materialization_report.json",
        materialization_run_report_to_canonical_json(report),
    )
    inventory_artifact = atomic_write_text(
        run_directory,
        directory / "cache_inventory.json",
        materialization_cache_inventory_to_canonical_json(inventory),
    )
    return MaterializationArtifacts(
        plan_manifest=plan_artifact,
        run_report=report_artifact,
        cache_inventory=inventory_artifact,
    )


def _verified_artifact_text(
    repository_root: Path,
    artifact: WrittenArtifact,
    label: str,
) -> str:
    root = _repository_root(repository_root)
    if not isinstance(artifact, WrittenArtifact):
        raise ValidationError("artifact must be WrittenArtifact")
    candidate = root / artifact.relative_path
    _assert_no_symlink_components(root, candidate)
    if not _lexists(candidate) or candidate.is_symlink() or not candidate.is_file():
        raise ArtifactError(f"{label} is missing or not a regular file")
    size, digest = _hash_file(candidate)
    if size != artifact.size_bytes:
        raise ArtifactError(f"{label} size differs")
    if digest != artifact.content_checksum:
        raise ArtifactError(f"{label} SHA-256 differs")
    return _read_utf8(candidate, label)


def verify_materialization_artifacts(
    repository_root: Path,
    artifacts: MaterializationArtifacts,
) -> tuple[
    MaterializationPlan,
    MaterializationRunReport,
    MaterializationCacheInventory,
]:
    """Verify operational bytes, canonical encoding, and cross-object consistency."""
    if not isinstance(artifacts, MaterializationArtifacts):
        raise ValidationError("artifacts must be MaterializationArtifacts")
    plan_text = _verified_artifact_text(
        repository_root, artifacts.plan_manifest, "materialization_plan.json"
    )
    report_text = _verified_artifact_text(
        repository_root, artifacts.run_report, "materialization_report.json"
    )
    inventory_text = _verified_artifact_text(
        repository_root, artifacts.cache_inventory, "cache_inventory.json"
    )
    plan = materialization_plan_from_json(plan_text)
    report = materialization_run_report_from_json(report_text)
    inventory = materialization_cache_inventory_from_json(inventory_text)
    if plan_text != materialization_plan_to_canonical_json(plan):
        raise SchemaError("materialization_plan.json is not canonical")
    if report_text != materialization_run_report_to_canonical_json(report):
        raise SchemaError("materialization_report.json is not canonical")
    if inventory_text != materialization_cache_inventory_to_canonical_json(inventory):
        raise SchemaError("cache_inventory.json is not canonical")
    try:
        _validate_artifact_objects(plan, report, inventory)
    except ValidationError as error:
        raise SchemaError(str(error)) from None
    return plan, report, inventory
