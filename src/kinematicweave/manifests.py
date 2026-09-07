"""Immutable experiment and artifact manifests with canonical serialization."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import json
from pathlib import Path
import re
from typing import cast

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import ExperimentError, SchemaError, ValidationError
from kinematicweave.identifiers import validate_identifier
from kinematicweave.paths import normalize_relative_path, relative_path_text
from kinematicweave.seeding import validate_root_seed

__all__ = [
    "ArtifactManifest",
    "ArtifactType",
    "ExperimentManifest",
    "ExperimentalUnitType",
    "RunStatus",
    "artifact_manifest_from_dict",
    "artifact_manifest_from_json",
    "artifact_manifest_identity",
    "artifact_manifest_to_canonical_json",
    "artifact_manifest_to_dict",
    "experiment_manifest_from_dict",
    "experiment_manifest_from_json",
    "experiment_manifest_to_canonical_json",
    "experiment_manifest_to_dict",
    "transition_experiment_manifest",
    "validate_run_status_transition",
]

_SCHEMA_VERSION = "1.0"
_GIT_OBJECT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_UTC_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
_UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class RunStatus(StrEnum):
    """Approved experiment-run lifecycle states."""

    PLANNED = "planned"
    RUNNING = "running"
    PARTIAL = "partial"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExperimentalUnitType(StrEnum):
    """Approved experiment work-unit categories."""

    TRAJECTORY = "trajectory"
    SCENARIO = "scenario"
    TILE = "tile"
    EDIT_SCENARIO = "edit_scenario"
    TAPE = "tape"
    WORKLOAD = "workload"


class ArtifactType(StrEnum):
    """Approved persistent artifact categories."""

    DATA = "data"
    TAPE = "tape"
    RAW_RESULT = "raw_result"
    AGGREGATE = "aggregate"
    FIGURE = "figure"
    TABLE = "table"
    REPORT = "report"
    QUALITATIVE_IMAGE = "qualitative_image"
    QUALITATIVE_VIDEO = "qualitative_video"
    LOG = "log"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


def _git_object_id(value: object) -> str:
    normalized = _required_text(value, "git_commit")
    if _GIT_OBJECT_PATTERN.fullmatch(normalized) is None:
        raise ValidationError(
            "git_commit must be a lowercase hexadecimal identifier of length 40 or 64"
        )
    return normalized


def _sha256_digest(value: object, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return normalized


def _optional_sha256_digest(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _sha256_digest(value, field_name)


def _canonical_object_json(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a JSON string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValidationError(f"{field_name} is invalid JSON: {error.msg}") from None
    if not isinstance(parsed, Mapping):
        raise ValidationError(f"{field_name} must contain a JSON object")
    return canonical_json_text(parsed, trailing_newline=False)


def _optional_canonical_object_json(
    value: object,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _canonical_object_json(value, field_name)


def _optional_timestamp(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must use YYYY-MM-DDTHH:MM:SS.ffffffZ")
    try:
        datetime.strptime(value, _UTC_TIMESTAMP_FORMAT)
    except ValueError:
        raise ValidationError(f"{field_name} is not a valid UTC timestamp") from None
    return value


def _timestamp_value(value: str) -> datetime:
    return datetime.strptime(value, _UTC_TIMESTAMP_FORMAT)


def _unique_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValidationError(f"{field_name} must be an immutable tuple")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _required_text(item, f"{field_name} item")
        if text in seen:
            raise ValidationError(f"{field_name} must not contain duplicates")
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _unique_seeds(value: object) -> tuple[int, ...]:
    if not isinstance(value, tuple):
        raise ValidationError("seeds must be an immutable tuple")
    normalized: list[int] = []
    seen: set[int] = set()
    for item in value:
        seed = validate_root_seed(item)
        if seed in seen:
            raise ValidationError("seeds must not contain duplicates")
        seen.add(seed)
        normalized.append(seed)
    return tuple(normalized)


def _unique_paths(value: object, field_name: str) -> tuple[Path, ...]:
    if not isinstance(value, tuple):
        raise ValidationError(f"{field_name} must be an immutable tuple")
    normalized: list[Path] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, (str, Path)):
            raise ValidationError(f"{field_name} items must be path strings or Path")
        path = normalize_relative_path(item)
        text = relative_path_text(path)
        if text in seen:
            raise ValidationError(f"{field_name} must not contain duplicates")
        seen.add(text)
        normalized.append(path)
    return tuple(normalized)


def _unique_identifiers(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValidationError(f"{field_name} must be an immutable tuple")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValidationError(f"{field_name} items must be strings")
        identifier = validate_identifier(item)
        if identifier in seen:
            raise ValidationError(f"{field_name} must not contain duplicates")
        seen.add(identifier)
        normalized.append(identifier)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    """Validated immutable experiment-run manifest."""

    schema_version: str
    run_id: str
    experiment_id: str
    experiment_version: str
    status: RunStatus
    config_path: Path
    resolved_config_json: str
    dataset_id: str
    split_name: str
    unit_type: ExperimentalUnitType
    planned_unit_count: int
    completed_unit_count: int
    failed_unit_count: int
    method_ids: tuple[str, ...]
    metric_names: tuple[str, ...]
    seeds: tuple[int, ...]
    git_commit: str
    git_dirty: bool
    python_version: str
    environment_lock_id: str
    host_id: str
    start_time_utc: str | None
    end_time_utc: str | None
    raw_result_paths: tuple[Path, ...]
    log_paths: tuple[Path, ...]
    failure_summary: str | None

    def __post_init__(self) -> None:
        """Normalize fields and enforce run-state invariants."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(self, "run_id", validate_identifier(self.run_id))
        object.__setattr__(
            self,
            "experiment_id",
            validate_identifier(self.experiment_id),
        )
        for field_name in (
            "experiment_version",
            "dataset_id",
            "split_name",
            "python_version",
            "host_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.status, RunStatus):
            raise ValidationError("status must be a RunStatus")
        if not isinstance(self.unit_type, ExperimentalUnitType):
            raise ValidationError("unit_type must be an ExperimentalUnitType")
        object.__setattr__(
            self,
            "config_path",
            normalize_relative_path(self.config_path),
        )
        object.__setattr__(
            self,
            "resolved_config_json",
            _canonical_object_json(
                self.resolved_config_json,
                "resolved_config_json",
            ),
        )
        for field_name in (
            "planned_unit_count",
            "completed_unit_count",
            "failed_unit_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        if self.completed_unit_count + self.failed_unit_count > self.planned_unit_count:
            raise ValidationError(
                "completed_unit_count + failed_unit_count must not exceed "
                "planned_unit_count"
            )
        object.__setattr__(
            self,
            "method_ids",
            _unique_strings(self.method_ids, "method_ids"),
        )
        object.__setattr__(
            self,
            "metric_names",
            _unique_strings(self.metric_names, "metric_names"),
        )
        object.__setattr__(self, "seeds", _unique_seeds(self.seeds))
        object.__setattr__(self, "git_commit", _git_object_id(self.git_commit))
        if not isinstance(self.git_dirty, bool):
            raise ValidationError("git_dirty must be a Boolean")
        object.__setattr__(
            self,
            "environment_lock_id",
            _sha256_digest(self.environment_lock_id, "environment_lock_id"),
        )
        object.__setattr__(
            self,
            "start_time_utc",
            _optional_timestamp(self.start_time_utc, "start_time_utc"),
        )
        object.__setattr__(
            self,
            "end_time_utc",
            _optional_timestamp(self.end_time_utc, "end_time_utc"),
        )
        if (
            self.start_time_utc is not None
            and self.end_time_utc is not None
            and _timestamp_value(self.start_time_utc)
            > _timestamp_value(self.end_time_utc)
        ):
            raise ValidationError("start_time_utc must not be later than end_time_utc")
        object.__setattr__(
            self,
            "raw_result_paths",
            _unique_paths(self.raw_result_paths, "raw_result_paths"),
        )
        object.__setattr__(
            self,
            "log_paths",
            _unique_paths(self.log_paths, "log_paths"),
        )
        failure_summary = self.failure_summary
        if failure_summary is not None:
            if not isinstance(failure_summary, str):
                raise ValidationError("failure_summary must be a string or None")
            failure_summary = failure_summary.strip() or None
        object.__setattr__(self, "failure_summary", failure_summary)
        self._validate_status_invariants()

    def _validate_status_invariants(self) -> None:
        if self.status is RunStatus.PLANNED:
            if (
                self.completed_unit_count != 0
                or self.failed_unit_count != 0
                or self.start_time_utc is not None
                or self.end_time_utc is not None
            ):
                raise ValidationError(
                    "planned status requires zero completed and failed counts "
                    "and no timestamps"
                )
        elif self.status is RunStatus.RUNNING:
            if self.start_time_utc is None or self.end_time_utc is not None:
                raise ValidationError(
                    "running status requires start_time_utc and forbids end_time_utc"
                )
        elif self.status is RunStatus.PARTIAL:
            if self.start_time_utc is None or self.end_time_utc is not None:
                raise ValidationError(
                    "partial status requires start_time_utc and forbids end_time_utc"
                )
        elif self.status is RunStatus.COMPLETE:
            if (
                self.completed_unit_count + self.failed_unit_count
                != self.planned_unit_count
            ):
                raise ValidationError(
                    "complete status requires all planned units to be accounted for"
                )
            if self.failure_summary is not None:
                raise ValidationError("complete status must not have a failure_summary")
        elif self.status is RunStatus.FAILED:
            if self.end_time_utc is None:
                raise ValidationError("failed status requires end_time_utc")
            if self.failure_summary is None:
                raise ValidationError(
                    "failed status requires a nonempty failure_summary"
                )
        elif self.status is RunStatus.CANCELLED and self.end_time_utc is None:
            raise ValidationError("cancelled status requires end_time_utc")


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Validated immutable artifact provenance manifest."""

    schema_version: str
    artifact_id: str
    artifact_type: ArtifactType
    path: Path
    producer: str
    producer_version: str
    run_id: str | None
    source_artifact_ids: tuple[str, ...]
    config_id: str | None
    git_commit: str
    content_checksum: str | None
    size_bytes: int
    created_time_utc: str | None
    metadata_json: str | None

    def __post_init__(self) -> None:
        """Normalize fields and enforce artifact validation rules."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self,
            "artifact_id",
            validate_identifier(self.artifact_id),
        )
        if not isinstance(self.artifact_type, ArtifactType):
            raise ValidationError("artifact_type must be an ArtifactType")
        object.__setattr__(self, "path", normalize_relative_path(self.path))
        object.__setattr__(
            self,
            "producer",
            _required_text(self.producer, "producer"),
        )
        object.__setattr__(
            self,
            "producer_version",
            _required_text(self.producer_version, "producer_version"),
        )
        if self.run_id is not None:
            object.__setattr__(self, "run_id", validate_identifier(self.run_id))
        object.__setattr__(
            self,
            "source_artifact_ids",
            _unique_identifiers(
                self.source_artifact_ids,
                "source_artifact_ids",
            ),
        )
        object.__setattr__(
            self,
            "config_id",
            _optional_text(self.config_id, "config_id"),
        )
        object.__setattr__(self, "git_commit", _git_object_id(self.git_commit))
        object.__setattr__(
            self,
            "content_checksum",
            _optional_sha256_digest(
                self.content_checksum,
                "content_checksum",
            ),
        )
        object.__setattr__(
            self,
            "size_bytes",
            _nonnegative_int(self.size_bytes, "size_bytes"),
        )
        object.__setattr__(
            self,
            "created_time_utc",
            _optional_timestamp(self.created_time_utc, "created_time_utc"),
        )
        object.__setattr__(
            self,
            "metadata_json",
            _optional_canonical_object_json(self.metadata_json, "metadata_json"),
        )


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PLANNED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.PARTIAL,
            RunStatus.COMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.PARTIAL: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.COMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.COMPLETE: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def validate_run_status_transition(
    current: RunStatus,
    target: RunStatus,
) -> None:
    """Validate one explicit experiment-run status transition.

    Raises:
        ExperimentError: If either state or the requested transition is invalid.
    """
    if not isinstance(current, RunStatus) or not isinstance(target, RunStatus):
        raise ExperimentError("run status transitions require RunStatus values")
    if current is target:
        raise ExperimentError(f"same-state transition {current.value!r} is invalid")
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ExperimentError(
            f"run status transition {current.value!r} -> {target.value!r} is invalid"
        )


def transition_experiment_manifest(
    manifest: ExperimentManifest,
    target: RunStatus,
    *,
    completed_unit_count: int | None = None,
    failed_unit_count: int | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
    failure_summary: str | None = None,
    raw_result_paths: tuple[str | Path, ...] | None = None,
    log_paths: tuple[str | Path, ...] | None = None,
) -> ExperimentManifest:
    """Return a new manifest after validating an explicit status transition.

    Omitted values retain their current values. No timestamps are invented.
    """
    if not isinstance(manifest, ExperimentManifest):
        raise ValidationError("manifest must be an ExperimentManifest")
    validate_run_status_transition(manifest.status, target)
    return replace(
        manifest,
        status=target,
        completed_unit_count=(
            manifest.completed_unit_count
            if completed_unit_count is None
            else completed_unit_count
        ),
        failed_unit_count=(
            manifest.failed_unit_count
            if failed_unit_count is None
            else failed_unit_count
        ),
        start_time_utc=(
            manifest.start_time_utc if start_time_utc is None else start_time_utc
        ),
        end_time_utc=(manifest.end_time_utc if end_time_utc is None else end_time_utc),
        failure_summary=(
            manifest.failure_summary if failure_summary is None else failure_summary
        ),
        raw_result_paths=cast(
            tuple[Path, ...],
            (
                manifest.raw_result_paths
                if raw_result_paths is None
                else raw_result_paths
            ),
        ),
        log_paths=cast(
            tuple[Path, ...],
            manifest.log_paths if log_paths is None else log_paths,
        ),
    )


def experiment_manifest_to_dict(
    manifest: ExperimentManifest,
) -> dict[str, object]:
    """Return a JSON-compatible experiment manifest in declared field order."""
    return {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "experiment_id": manifest.experiment_id,
        "experiment_version": manifest.experiment_version,
        "status": manifest.status.value,
        "config_path": relative_path_text(manifest.config_path),
        "resolved_config_json": manifest.resolved_config_json,
        "dataset_id": manifest.dataset_id,
        "split_name": manifest.split_name,
        "unit_type": manifest.unit_type.value,
        "planned_unit_count": manifest.planned_unit_count,
        "completed_unit_count": manifest.completed_unit_count,
        "failed_unit_count": manifest.failed_unit_count,
        "method_ids": list(manifest.method_ids),
        "metric_names": list(manifest.metric_names),
        "seeds": list(manifest.seeds),
        "git_commit": manifest.git_commit,
        "git_dirty": manifest.git_dirty,
        "python_version": manifest.python_version,
        "environment_lock_id": manifest.environment_lock_id,
        "host_id": manifest.host_id,
        "start_time_utc": manifest.start_time_utc,
        "end_time_utc": manifest.end_time_utc,
        "raw_result_paths": [
            relative_path_text(path) for path in manifest.raw_result_paths
        ],
        "log_paths": [relative_path_text(path) for path in manifest.log_paths],
        "failure_summary": manifest.failure_summary,
    }


def artifact_manifest_to_dict(manifest: ArtifactManifest) -> dict[str, object]:
    """Return a JSON-compatible artifact manifest in declared field order."""
    return {
        "schema_version": manifest.schema_version,
        "artifact_id": manifest.artifact_id,
        "artifact_type": manifest.artifact_type.value,
        "path": relative_path_text(manifest.path),
        "producer": manifest.producer,
        "producer_version": manifest.producer_version,
        "run_id": manifest.run_id,
        "source_artifact_ids": list(manifest.source_artifact_ids),
        "config_id": manifest.config_id,
        "git_commit": manifest.git_commit,
        "content_checksum": manifest.content_checksum,
        "size_bytes": manifest.size_bytes,
        "created_time_utc": manifest.created_time_utc,
        "metadata_json": manifest.metadata_json,
    }


def experiment_manifest_to_canonical_json(manifest: ExperimentManifest) -> str:
    """Serialize an experiment manifest with exactly one final newline."""
    return canonical_json_text(
        experiment_manifest_to_dict(manifest),
        trailing_newline=True,
    )


def artifact_manifest_to_canonical_json(manifest: ArtifactManifest) -> str:
    """Serialize an artifact manifest with exactly one final newline."""
    return canonical_json_text(
        artifact_manifest_to_dict(manifest),
        trailing_newline=True,
    )


_EXPERIMENT_FIELDS = tuple(ExperimentManifest.__dataclass_fields__)
_ARTIFACT_FIELDS = tuple(ArtifactManifest.__dataclass_fields__)


def _validate_mapping_fields(
    value: Mapping[object, object],
    expected_fields: tuple[str, ...],
    manifest_name: str,
) -> None:
    supplied = set(value)
    expected = set(expected_fields)
    unknown = sorted(repr(field) for field in supplied - expected)
    missing = sorted(expected - supplied)
    if unknown:
        raise SchemaError(f"Unknown {manifest_name} field(s): {', '.join(unknown)}")
    if missing:
        raise SchemaError(
            f"Missing {manifest_name} field(s): "
            + ", ".join(repr(field) for field in missing)
        )


def _sequence_tuple(value: object, field_name: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValidationError(f"{field_name} must be an array")
    return tuple(value)


def _enum_value[EnumType: StrEnum](
    enum_type: type[EnumType],
    value: object,
    field_name: str,
) -> EnumType:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError:
        raise ValidationError(f"{field_name} has invalid value {value!r}") from None


def experiment_manifest_from_dict(
    value: Mapping[str, object],
) -> ExperimentManifest:
    """Deserialize and validate an experiment manifest dictionary.

    Raises:
        SchemaError: If fields, types, or values violate the schema.
    """
    if not isinstance(value, Mapping):
        raise SchemaError("experiment manifest must be a mapping")
    raw = cast(Mapping[object, object], value)
    _validate_mapping_fields(raw, _EXPERIMENT_FIELDS, "experiment manifest")
    try:
        return ExperimentManifest(
            schema_version=cast(str, raw["schema_version"]),
            run_id=cast(str, raw["run_id"]),
            experiment_id=cast(str, raw["experiment_id"]),
            experiment_version=cast(str, raw["experiment_version"]),
            status=_enum_value(RunStatus, raw["status"], "status"),
            config_path=cast(Path, raw["config_path"]),
            resolved_config_json=cast(str, raw["resolved_config_json"]),
            dataset_id=cast(str, raw["dataset_id"]),
            split_name=cast(str, raw["split_name"]),
            unit_type=_enum_value(
                ExperimentalUnitType,
                raw["unit_type"],
                "unit_type",
            ),
            planned_unit_count=cast(int, raw["planned_unit_count"]),
            completed_unit_count=cast(int, raw["completed_unit_count"]),
            failed_unit_count=cast(int, raw["failed_unit_count"]),
            method_ids=cast(
                tuple[str, ...],
                _sequence_tuple(raw["method_ids"], "method_ids"),
            ),
            metric_names=cast(
                tuple[str, ...],
                _sequence_tuple(raw["metric_names"], "metric_names"),
            ),
            seeds=cast(
                tuple[int, ...],
                _sequence_tuple(raw["seeds"], "seeds"),
            ),
            git_commit=cast(str, raw["git_commit"]),
            git_dirty=cast(bool, raw["git_dirty"]),
            python_version=cast(str, raw["python_version"]),
            environment_lock_id=cast(str, raw["environment_lock_id"]),
            host_id=cast(str, raw["host_id"]),
            start_time_utc=cast(str | None, raw["start_time_utc"]),
            end_time_utc=cast(str | None, raw["end_time_utc"]),
            raw_result_paths=cast(
                tuple[Path, ...],
                _sequence_tuple(raw["raw_result_paths"], "raw_result_paths"),
            ),
            log_paths=cast(
                tuple[Path, ...],
                _sequence_tuple(raw["log_paths"], "log_paths"),
            ),
            failure_summary=cast(str | None, raw["failure_summary"]),
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def artifact_manifest_from_dict(
    value: Mapping[str, object],
) -> ArtifactManifest:
    """Deserialize and validate an artifact manifest dictionary.

    Raises:
        SchemaError: If fields, types, or values violate the schema.
    """
    if not isinstance(value, Mapping):
        raise SchemaError("artifact manifest must be a mapping")
    raw = cast(Mapping[object, object], value)
    _validate_mapping_fields(raw, _ARTIFACT_FIELDS, "artifact manifest")
    try:
        return ArtifactManifest(
            schema_version=cast(str, raw["schema_version"]),
            artifact_id=cast(str, raw["artifact_id"]),
            artifact_type=_enum_value(
                ArtifactType,
                raw["artifact_type"],
                "artifact_type",
            ),
            path=cast(Path, raw["path"]),
            producer=cast(str, raw["producer"]),
            producer_version=cast(str, raw["producer_version"]),
            run_id=cast(str | None, raw["run_id"]),
            source_artifact_ids=cast(
                tuple[str, ...],
                _sequence_tuple(
                    raw["source_artifact_ids"],
                    "source_artifact_ids",
                ),
            ),
            config_id=cast(str | None, raw["config_id"]),
            git_commit=cast(str, raw["git_commit"]),
            content_checksum=cast(str | None, raw["content_checksum"]),
            size_bytes=cast(int, raw["size_bytes"]),
            created_time_utc=cast(str | None, raw["created_time_utc"]),
            metadata_json=cast(str | None, raw["metadata_json"]),
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def _manifest_json_object(text: str, manifest_name: str) -> Mapping[str, object]:
    if not isinstance(text, str):
        raise SchemaError(f"{manifest_name} JSON must be text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise SchemaError(f"Invalid {manifest_name} JSON: {error.msg}") from None
    if not isinstance(parsed, Mapping):
        raise SchemaError(f"{manifest_name} JSON root must be an object")
    return cast(Mapping[str, object], parsed)


def experiment_manifest_from_json(text: str) -> ExperimentManifest:
    """Deserialize and validate experiment-manifest JSON text."""
    return experiment_manifest_from_dict(
        _manifest_json_object(text, "experiment manifest")
    )


def artifact_manifest_from_json(text: str) -> ArtifactManifest:
    """Deserialize and validate artifact-manifest JSON text."""
    return artifact_manifest_from_dict(_manifest_json_object(text, "artifact manifest"))


def artifact_manifest_identity(manifest: ArtifactManifest) -> str:
    """Return stable logical identity excluding path and creation time."""
    identity_payload = {
        "schema_version": manifest.schema_version,
        "artifact_id": manifest.artifact_id,
        "artifact_type": manifest.artifact_type.value,
        "producer": manifest.producer,
        "producer_version": manifest.producer_version,
        "run_id": manifest.run_id,
        "source_artifact_ids": list(manifest.source_artifact_ids),
        "config_id": manifest.config_id,
        "git_commit": manifest.git_commit,
        "content_checksum": manifest.content_checksum,
        "size_bytes": manifest.size_bytes,
        "metadata_json": manifest.metadata_json,
    }
    return canonical_sha256("artifact-manifest", identity_payload)
