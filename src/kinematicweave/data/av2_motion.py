"""Strict direct-Parquet adapter for one AV2 motion scenario."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, IntEnum, StrEnum
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, cast
import unicodedata

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_canonical_json,
)
from kinematicweave.data.parquet_io import (
    CanonicalParquetArtifact,
    agent_records_to_table,
    atomic_write_canonical_parquet,
    coordinate_frame_records_to_table,
    read_canonical_parquet_table,
    scenario_records_to_table,
    trajectories_to_table,
    verify_canonical_parquet_artifact,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.records import (
    AgentClass,
    AgentRecord,
    CoordinateFrameRecord,
    OriginType,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
    validate_scenario_bundle,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import make_identifier, validate_identifier
from kinematicweave.paths import normalize_relative_path, relative_path_text

__all__ = [
    "Av2AgentInclusionPolicy",
    "Av2MotionAdapterConfig",
    "Av2MotionScenarioArtifacts",
    "Av2MotionScenarioConversion",
    "Av2ObjectType",
    "Av2SourceTrackSummary",
    "Av2TrackCategory",
    "av2_motion_conversion_summary_to_dict",
    "av2_object_type_to_agent_class",
    "inspect_av2_motion_scenario_file",
    "load_av2_motion_scenario",
    "materialize_av2_motion_scenario",
    "validate_av2_motion_source_schema",
    "verify_av2_motion_scenario_artifacts",
]

_SCHEMA_VERSION = "1.0"
_DATASET_ID = "av2_motion"
_ADAPTER_NAME = "av2_motion_adapter"
_SOURCE_CRS = "av2_city_map"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_READ_CHUNK_SIZE = 1024 * 1024
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_REQUIRED_COLUMNS = (
    "observed",
    "track_id",
    "object_type",
    "object_category",
    "timestep",
    "position_x",
    "position_y",
    "heading",
    "velocity_x",
    "velocity_y",
    "scenario_id",
    "start_timestamp",
    "end_timestamp",
    "num_timestamps",
    "focal_track_id",
    "city",
)
_SCENARIO_COLUMNS = (
    "scenario_id",
    "start_timestamp",
    "end_timestamp",
    "num_timestamps",
    "focal_track_id",
    "city",
)
_TEXT_COLUMNS = (
    "track_id",
    "object_type",
    "scenario_id",
    "focal_track_id",
    "city",
)
_INTEGER_COLUMNS = (
    "object_category",
    "timestep",
    "num_timestamps",
)
_TIMESTAMP_ENDPOINT_COLUMNS = (
    "start_timestamp",
    "end_timestamp",
)
_NUMERIC_COLUMNS = (
    "position_x",
    "position_y",
    "heading",
    "velocity_x",
    "velocity_y",
)
_DYNAMIC_TYPES = frozenset(
    {
        "vehicle",
        "pedestrian",
        "motorcyclist",
        "cyclist",
        "bus",
        "unknown",
    }
)
_STATIC_TYPES = frozenset(
    {
        "static",
        "background",
        "construction",
        "riderless_bicycle",
    }
)
_CATEGORY_FLAGS = {
    0: "av2_track_fragment",
    1: "av2_unscored_track",
    2: "av2_scored_track",
    3: "av2_focal_track",
}


class Av2ObjectType(StrEnum):
    """AV2 motion object types in canonical source order."""

    VEHICLE = "vehicle"
    PEDESTRIAN = "pedestrian"
    MOTORCYCLIST = "motorcyclist"
    CYCLIST = "cyclist"
    BUS = "bus"
    STATIC = "static"
    BACKGROUND = "background"
    CONSTRUCTION = "construction"
    RIDERLESS_BICYCLE = "riderless_bicycle"
    UNKNOWN = "unknown"


class Av2TrackCategory(IntEnum):
    """AV2 motion track categories."""

    track_fragment = 0
    unscored_track = 1
    scored_track = 2
    focal_track = 3


class Av2AgentInclusionPolicy(StrEnum):
    """Approved source-track inclusion policies."""

    DYNAMIC_ONLY = "dynamic_only"
    ALL_TRACKS = "all_tracks"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _actual_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a bool")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


def _positive_int(value: object, field_name: str) -> int:
    normalized = _nonnegative_int(value, field_name)
    if normalized == 0:
        raise ValidationError(f"{field_name} must be at least one")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return value


def _enum_value[EnumT: Enum](
    enum_type: type[EnumT],
    value: object,
    field_name: str,
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must use {enum_type.__name__}")
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must use {enum_type.__name__}") from None


def _count_summary(
    value: object,
    field_name: str,
    expected_keys: tuple[str, ...],
) -> tuple[tuple[str, int], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a finite sequence")
    copied = tuple(item for item in value)
    normalized: list[tuple[str, int]] = []
    for item in copied:
        if (
            not isinstance(item, Sequence)
            or isinstance(item, (str, bytes))
            or len(item) != 2
        ):
            raise ValidationError(f"{field_name} items must be (str, int) pairs")
        key, count = item
        if not isinstance(key, str):
            raise ValidationError(f"{field_name} keys must be strings")
        normalized.append((key, _nonnegative_int(count, f"{field_name} count")))
    if tuple(key for key, _count in normalized) != expected_keys:
        raise ValidationError(f"{field_name} keys are out of canonical order")
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class Av2MotionAdapterConfig:
    """Configuration for one strict AV2 scenario conversion."""

    dataset_version: str
    split_name: str
    adapter_version: str = "1.0"
    inclusion_policy: Av2AgentInclusionPolicy | str = (
        Av2AgentInclusionPolicy.DYNAMIC_ONLY
    )
    ego_track_id: str | None = None
    source_map_available: bool = False

    def __post_init__(self) -> None:
        """Normalize and validate adapter configuration."""
        for field_name in ("dataset_version", "split_name", "adapter_version"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "inclusion_policy",
            _enum_value(
                Av2AgentInclusionPolicy,
                self.inclusion_policy,
                "inclusion_policy",
            ),
        )
        object.__setattr__(
            self,
            "ego_track_id",
            _optional_text(self.ego_track_id, "ego_track_id"),
        )
        object.__setattr__(
            self,
            "source_map_available",
            _actual_bool(self.source_map_available, "source_map_available"),
        )


@dataclass(frozen=True, slots=True)
class Av2SourceTrackSummary:
    """Source-track inclusion result and canonical identifiers."""

    source_track_id: str
    source_object_type: Av2ObjectType | str
    source_category: Av2TrackCategory | int
    included: bool
    canonical_agent_id: str | None
    canonical_trajectory_id: str | None
    source_state_count: int
    exclusion_reason: str | None

    def __post_init__(self) -> None:
        """Normalize and validate one source-track summary."""
        object.__setattr__(
            self,
            "source_track_id",
            _required_text(self.source_track_id, "source_track_id"),
        )
        object.__setattr__(
            self,
            "source_object_type",
            _enum_value(
                Av2ObjectType,
                self.source_object_type,
                "source_object_type",
            ),
        )
        object.__setattr__(
            self,
            "source_category",
            _enum_value(
                Av2TrackCategory,
                self.source_category,
                "source_category",
            ),
        )
        object.__setattr__(
            self,
            "included",
            _actual_bool(self.included, "included"),
        )
        object.__setattr__(
            self,
            "source_state_count",
            _positive_int(self.source_state_count, "source_state_count"),
        )
        if self.included:
            if self.canonical_agent_id is None or self.canonical_trajectory_id is None:
                raise ValidationError(
                    "included summaries require canonical identifiers"
                )
            object.__setattr__(
                self,
                "canonical_agent_id",
                validate_identifier(self.canonical_agent_id),
            )
            object.__setattr__(
                self,
                "canonical_trajectory_id",
                validate_identifier(self.canonical_trajectory_id),
            )
            if self.exclusion_reason is not None:
                raise ValidationError(
                    "included summaries must not have an exclusion_reason"
                )
        else:
            if (
                self.canonical_agent_id is not None
                or self.canonical_trajectory_id is not None
            ):
                raise ValidationError(
                    "excluded summaries must not have canonical identifiers"
                )
            object.__setattr__(
                self,
                "exclusion_reason",
                _required_text(self.exclusion_reason, "exclusion_reason"),
            )


@dataclass(frozen=True, slots=True)
class Av2MotionScenarioConversion:
    """Canonical records and source provenance for one AV2 scenario."""

    source_relative_path: Path
    source_checksum: str
    source_scenario_id: str
    source_focal_track_id: str
    source_city_name: str
    source_track_count: int
    source_state_count: int
    scenario: ScenarioRecord
    coordinate_frame: CoordinateFrameRecord
    agents: tuple[AgentRecord, ...]
    trajectories: tuple[Trajectory, ...]
    track_summaries: tuple[Av2SourceTrackSummary, ...]
    object_type_counts: tuple[tuple[str, int], ...]
    category_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        """Copy child collections and enforce source/canonical consistency."""
        object.__setattr__(
            self,
            "source_relative_path",
            normalize_relative_path(self.source_relative_path),
        )
        object.__setattr__(
            self,
            "source_checksum",
            _sha256(self.source_checksum, "source_checksum"),
        )
        for field_name in (
            "source_scenario_id",
            "source_focal_track_id",
            "source_city_name",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "source_track_count",
            _nonnegative_int(self.source_track_count, "source_track_count"),
        )
        object.__setattr__(
            self,
            "source_state_count",
            _nonnegative_int(self.source_state_count, "source_state_count"),
        )
        if not isinstance(self.scenario, ScenarioRecord):
            raise ValidationError("scenario must be a ScenarioRecord")
        if not isinstance(self.coordinate_frame, CoordinateFrameRecord):
            raise ValidationError("coordinate_frame must be a CoordinateFrameRecord")
        for field_name, expected_type in (
            ("agents", AgentRecord),
            ("trajectories", Trajectory),
            ("track_summaries", Av2SourceTrackSummary),
        ):
            value: object = getattr(self, field_name)
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValidationError(f"{field_name} must be a finite sequence")
            copied = tuple(item for item in value)
            if any(not isinstance(item, expected_type) for item in copied):
                raise ValidationError(f"{field_name} contains an incompatible value")
            object.__setattr__(self, field_name, copied)
        object_type_keys = tuple(item.value for item in Av2ObjectType)
        category_keys = tuple(item.name for item in Av2TrackCategory)
        object.__setattr__(
            self,
            "object_type_counts",
            _count_summary(
                self.object_type_counts,
                "object_type_counts",
                object_type_keys,
            ),
        )
        object.__setattr__(
            self,
            "category_counts",
            _count_summary(
                self.category_counts,
                "category_counts",
                category_keys,
            ),
        )
        summaries = self.track_summaries
        if self.source_track_count != len(summaries):
            raise ValidationError("source_track_count must equal len(track_summaries)")
        if self.source_state_count != sum(
            item.source_state_count for item in summaries
        ):
            raise ValidationError(
                "source_state_count must equal summarized source states"
            )
        if sum(count for _key, count in self.object_type_counts) != (
            self.source_track_count
        ):
            raise ValidationError("object_type_counts must sum to source_track_count")
        if sum(count for _key, count in self.category_counts) != (
            self.source_track_count
        ):
            raise ValidationError("category_counts must sum to source_track_count")
        included = tuple(item for item in summaries if item.included)
        if len(included) != len(self.agents) or len(included) != len(self.trajectories):
            raise ValidationError(
                "included summary count must equal agents and trajectories"
            )
        if tuple(item.canonical_agent_id for item in included) != tuple(
            item.agent_id for item in self.agents
        ):
            raise ValidationError("summary agent identifiers differ")
        if tuple(item.canonical_trajectory_id for item in included) != tuple(
            item.trajectory_id for item in self.trajectories
        ):
            raise ValidationError("summary trajectory identifiers differ")
        if self.scenario.source_scenario_id != self.source_scenario_id:
            raise ValidationError("scenario source identifier differs")
        if self.scenario.city_or_region != self.source_city_name:
            raise ValidationError("scenario source city differs")
        if self.scenario.source_checksum != self.source_checksum:
            raise ValidationError("scenario source checksum differs")
        validate_scenario_bundle(
            self.scenario,
            self.coordinate_frame,
            self.agents,
            self.trajectories,
        )


@dataclass(frozen=True, slots=True)
class Av2MotionScenarioArtifacts:
    """Physical artifacts for one canonical AV2 scenario conversion."""

    adapter_summary: WrittenArtifact
    scenario_manifest: CanonicalParquetArtifact
    coordinate_frame_metadata: CanonicalParquetArtifact
    agent_metadata: CanonicalParquetArtifact
    trajectory_samples: CanonicalParquetArtifact

    def __post_init__(self) -> None:
        """Validate physical artifact types and canonical schema identities."""
        if not isinstance(self.adapter_summary, WrittenArtifact):
            raise ValidationError("adapter_summary must be a WrittenArtifact")
        expected = (
            (
                "scenario_manifest",
                self.scenario_manifest,
                CanonicalSchemaName.SCENARIO_MANIFEST,
            ),
            (
                "coordinate_frame_metadata",
                self.coordinate_frame_metadata,
                CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            ),
            (
                "agent_metadata",
                self.agent_metadata,
                CanonicalSchemaName.AGENT_METADATA,
            ),
            (
                "trajectory_samples",
                self.trajectory_samples,
                CanonicalSchemaName.TRAJECTORY_SAMPLES,
            ),
        )
        for field_name, artifact, schema_name in expected:
            if not isinstance(artifact, CanonicalParquetArtifact):
                raise ValidationError(
                    f"{field_name} must be a CanonicalParquetArtifact"
                )
            if (
                artifact.schema_name is not schema_name
                or artifact.schema_version != _SCHEMA_VERSION
            ):
                raise ValidationError(
                    f"{field_name} uses an incompatible canonical schema"
                )


def av2_object_type_to_agent_class(
    value: Av2ObjectType | str,
) -> AgentClass:
    """Map one AV2 object type to the canonical agent class."""
    object_type = _enum_value(Av2ObjectType, value, "value")
    mapping = {
        Av2ObjectType.VEHICLE: AgentClass.VEHICLE,
        Av2ObjectType.BUS: AgentClass.VEHICLE,
        Av2ObjectType.PEDESTRIAN: AgentClass.PEDESTRIAN,
        Av2ObjectType.CYCLIST: AgentClass.CYCLIST,
        Av2ObjectType.MOTORCYCLIST: AgentClass.OTHER_DYNAMIC,
        Av2ObjectType.UNKNOWN: AgentClass.UNKNOWN,
        Av2ObjectType.STATIC: AgentClass.UNKNOWN,
        Av2ObjectType.BACKGROUND: AgentClass.UNKNOWN,
        Av2ObjectType.CONSTRUCTION: AgentClass.UNKNOWN,
        Av2ObjectType.RIDERLESS_BICYCLE: AgentClass.UNKNOWN,
    }
    return mapping[object_type]


def validate_av2_motion_source_schema(schema: pa.Schema) -> None:
    """Validate required AV2 source columns and compatible logical types."""
    if not isinstance(schema, pa.Schema):
        raise SchemaError("source schema must be a pyarrow.Schema")
    for column_name in _REQUIRED_COLUMNS:
        count = schema.names.count(column_name)
        if count == 0:
            raise SchemaError(f"missing required source column: {column_name}")
        if count != 1:
            raise SchemaError(f"source column appears more than once: {column_name}")
    for column_name in _REQUIRED_COLUMNS:
        field = schema.field(column_name)
        data_type = field.type
        compatible = False
        expected = ""
        if column_name == "observed":
            compatible = pa.types.is_boolean(data_type)
            expected = "Boolean"
        elif column_name in _TEXT_COLUMNS:
            compatible = pa.types.is_string(data_type) or pa.types.is_large_string(
                data_type
            )
            expected = "string or large_string"
        elif column_name in _TIMESTAMP_ENDPOINT_COLUMNS:
            compatible = (
                pa.types.is_signed_integer(data_type) or data_type == pa.float64()
            )
            expected = "signed integer or float64"
        elif column_name in _INTEGER_COLUMNS:
            compatible = pa.types.is_signed_integer(data_type)
            expected = "signed integer"
        elif column_name in _NUMERIC_COLUMNS:
            compatible = pa.types.is_floating(data_type) or (
                pa.types.is_signed_integer(data_type)
            )
            expected = "floating-point or signed integer"
        if not compatible:
            raise SchemaError(f"source column {column_name!r} must use {expected}")


def _validated_source_path(
    source_root: Path,
    relative_path: str | Path,
) -> tuple[Path, Path]:
    if not isinstance(source_root, Path):
        raise ArtifactError("source_root must be a Path")
    try:
        root = source_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("source_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("source_root must be a directory")
    try:
        normalized = normalize_relative_path(relative_path)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    candidate = root / normalized
    if candidate.is_symlink():
        raise ArtifactError("source scenario file must not be a symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("source scenario file is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("source scenario file resolves outside source_root")
    if not resolved.is_file():
        raise ArtifactError("source scenario path must identify a regular file")
    return normalized, resolved


def _parquet_schema(path: Path) -> pa.Schema:
    try:
        schema = pq.ParquetFile(path).schema_arrow
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError("source scenario is not valid Parquet") from error
    validate_av2_motion_source_schema(schema)
    return schema


def inspect_av2_motion_scenario_file(
    source_root: Path,
    relative_path: str | Path,
) -> pa.Schema:
    """Inspect and validate AV2 scenario Parquet metadata without reading rows."""
    _normalized, path = _validated_source_path(source_root, relative_path)
    return _parquet_schema(path)


def _source_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_READ_CHUNK_SIZE):
                digest.update(chunk)
    except OSError as error:
        raise ArtifactError("source scenario file cannot be read") from error
    return digest.hexdigest()


def _source_text(value: object, field_name: str) -> str:
    try:
        return _required_text(value, field_name)
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def _safe_source_identifier(value: object, field_name: str) -> str:
    normalized = _source_text(value, field_name)
    if ":" in normalized or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise SchemaError(f"{field_name} must not contain colons or control characters")
    return normalized


def _source_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SchemaError(f"{field_name} must be a non-Boolean integer")
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise SchemaError(f"{field_name} must fit signed int64")
    return value


def _source_timestamp_ns(value: object, column_name: str) -> int:
    if isinstance(value, bool):
        raise SchemaError(f"{column_name} must be an exact int64 nanosecond")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise SchemaError(f"{column_name} must be an exact int64 nanosecond")
        try:
            normalized = int(value)
        except (OverflowError, ValueError):
            raise SchemaError(
                f"{column_name} must be an exact int64 nanosecond"
            ) from None
        if float(normalized) != value:
            raise SchemaError(f"{column_name} must be an exact int64 nanosecond")
    else:
        raise SchemaError(f"{column_name} must be an exact int64 nanosecond")
    if not _INT64_MIN <= normalized <= _INT64_MAX:
        raise SchemaError(f"{column_name} must fit signed int64")
    return normalized


def _source_float(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SchemaError(f"{field_name} must be numeric")
    try:
        normalized = float(value)
    except OverflowError:
        raise SchemaError(f"{field_name} must be finite") from None
    if not math.isfinite(normalized):
        raise SchemaError(f"{field_name} must be finite")
    return normalized


def _source_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaError(f"{field_name} must be a bool")
    return value


def _constant_source_value(
    rows: Sequence[Mapping[str, object]],
    field_name: str,
) -> object:
    first = rows[0][field_name]
    if any(row[field_name] != first for row in rows[1:]):
        raise SchemaError(f"source scenario column {field_name!r} is not constant")
    return first


def _timestamp_grid(
    start_timestamp: object,
    end_timestamp: object,
    num_timestamps: object,
) -> tuple[int, int, int, int]:
    start = _source_timestamp_ns(start_timestamp, "start_timestamp")
    end = _source_timestamp_ns(end_timestamp, "end_timestamp")
    count = _source_int(num_timestamps, "num_timestamps")
    if count < 1:
        raise SchemaError("num_timestamps must be at least one")
    if start > end:
        raise SchemaError("start_timestamp must not exceed end_timestamp")
    if count == 1:
        if start != end:
            raise SchemaError(
                "one-timestamp scenarios require equal start and end timestamps"
            )
        return start, end, count, 0
    difference = end - start
    if difference == 0:
        raise SchemaError("multi-timestamp scenarios require positive duration")
    divisor = count - 1
    if difference % divisor != 0:
        raise SchemaError("source timestamp grid is not exactly divisible")
    return start, end, count, difference // divisor


@dataclass(frozen=True, slots=True)
class _SourceState:
    timestep: int
    observed: bool
    position_x: float
    position_y: float
    heading: float
    velocity_x: float
    velocity_y: float


@dataclass(frozen=True, slots=True)
class _SourceTrack:
    track_id: str
    object_type: Av2ObjectType
    category: Av2TrackCategory
    states: tuple[_SourceState, ...]


def _parse_source_tracks(
    rows: Sequence[Mapping[str, object]],
    *,
    num_timestamps: int,
) -> tuple[_SourceTrack, ...]:
    grouped: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
    raw_by_trimmed: dict[str, str] = {}
    for row_index, row in enumerate(rows):
        raw_track_id = row["track_id"]
        track_id = _safe_source_identifier(
            raw_track_id,
            f"row {row_index} track_id",
        )
        raw_text = cast(str, raw_track_id)
        previous_raw = raw_by_trimmed.setdefault(track_id, raw_text)
        if raw_text != previous_raw:
            raise SchemaError("source track identifiers are not unique after trimming")
        grouped.setdefault(track_id, []).append((raw_text, row))
    tracks: list[_SourceTrack] = []
    for track_id in sorted(grouped):
        track_rows = grouped[track_id]
        object_types: list[Av2ObjectType] = []
        categories: list[Av2TrackCategory] = []
        states: list[_SourceState] = []
        timesteps: set[int] = set()
        for _raw_id, row in track_rows:
            try:
                object_type = _enum_value(
                    Av2ObjectType,
                    row["object_type"],
                    "object_type",
                )
                category = _enum_value(
                    Av2TrackCategory,
                    row["object_category"],
                    "object_category",
                )
            except ValidationError as error:
                raise SchemaError(str(error)) from None
            timestep = _source_int(row["timestep"], "timestep")
            if not 0 <= timestep < num_timestamps:
                raise SchemaError("timestep is outside the source scenario grid")
            if timestep in timesteps:
                raise SchemaError(
                    f"duplicate timestep {timestep} for source track {track_id!r}"
                )
            timesteps.add(timestep)
            object_types.append(object_type)
            categories.append(category)
            states.append(
                _SourceState(
                    timestep=timestep,
                    observed=_source_bool(row["observed"], "observed"),
                    position_x=_source_float(row["position_x"], "position_x"),
                    position_y=_source_float(row["position_y"], "position_y"),
                    heading=_source_float(row["heading"], "heading"),
                    velocity_x=_source_float(row["velocity_x"], "velocity_x"),
                    velocity_y=_source_float(row["velocity_y"], "velocity_y"),
                )
            )
        if len(set(object_types)) != 1:
            raise SchemaError(f"source track {track_id!r} uses multiple object types")
        if len(set(categories)) != 1:
            raise SchemaError(f"source track {track_id!r} uses multiple categories")
        tracks.append(
            _SourceTrack(
                track_id=track_id,
                object_type=object_types[0],
                category=categories[0],
                states=tuple(sorted(states, key=lambda item: item.timestep)),
            )
        )
    return tuple(tracks)


def _validate_focal_track(
    tracks: tuple[_SourceTrack, ...],
    focal_track_id: str,
) -> _SourceTrack:
    matches = tuple(track for track in tracks if track.track_id == focal_track_id)
    if len(matches) != 1:
        raise SchemaError("focal_track_id must identify exactly one source track")
    focal = matches[0]
    if focal.category is not Av2TrackCategory.focal_track:
        raise SchemaError("focal source track must use focal_track category")
    focal_categories = tuple(
        track for track in tracks if track.category is Av2TrackCategory.focal_track
    )
    if focal_categories != (focal,):
        raise SchemaError("exactly one source track must use focal_track category")
    return focal


def _quality_flags(track: _SourceTrack) -> tuple[str, ...]:
    flags = [_CATEGORY_FLAGS[track.category.value]]
    if track.object_type.value in _STATIC_TYPES:
        flags.append("av2_static_source_type")
    return tuple(flags)


def _wrapped_heading(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def _canonical_ids(
    source_scenario_id: str,
    source_track_id: str | None = None,
) -> tuple[str, ...]:
    try:
        if source_track_id is None:
            return (
                make_identifier("scenario", "av2", source_scenario_id),
                make_identifier("frame", "av2", source_scenario_id, "local"),
            )
        return (
            make_identifier("agent", "av2", source_scenario_id, source_track_id),
            make_identifier(
                "trajectory",
                "av2",
                source_scenario_id,
                source_track_id,
            ),
        )
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def _convert_track(
    track: _SourceTrack,
    *,
    scenario_id: str,
    source_scenario_id: str,
    focal_track_id: str,
    ego_track_id: str | None,
    origin_x: float,
    origin_y: float,
    start_timestamp: int,
    timestamp_step: int,
) -> tuple[AgentRecord, Trajectory, str, str]:
    agent_id, trajectory_id = _canonical_ids(
        source_scenario_id,
        track.track_id,
    )
    flags = _quality_flags(track)
    try:
        samples = tuple(
            TrajectorySampleRecord(
                scenario_id=scenario_id,
                agent_id=agent_id,
                trajectory_id=trajectory_id,
                sample_index=index,
                timestamp_ns=start_timestamp + state.timestep * timestamp_step,
                x_m=state.position_x - origin_x,
                y_m=state.position_y - origin_y,
                z_m=None,
                heading_rad=_wrapped_heading(state.heading),
                velocity_x_mps=state.velocity_x,
                velocity_y_mps=state.velocity_y,
                speed_mps=math.hypot(state.velocity_x, state.velocity_y),
                acceleration_x_mps2=None,
                acceleration_y_mps2=None,
                is_observed=state.observed,
                is_valid=True,
                origin_type=OriginType.SOURCE_OBSERVATION,
                quality_flags=flags,
            )
            for index, state in enumerate(track.states)
        )
        trajectory = Trajectory(
            scenario_id=scenario_id,
            agent_id=agent_id,
            trajectory_id=trajectory_id,
            samples=samples,
            origin_type=OriginType.SOURCE_OBSERVATION,
            quality_flags=flags,
        )
        agent = AgentRecord(
            scenario_id=scenario_id,
            agent_id=agent_id,
            source_agent_id=track.track_id,
            agent_class=av2_object_type_to_agent_class(track.object_type),
            length_m=None,
            width_m=None,
            height_m=None,
            first_time_ns=trajectory.start_time_ns,
            last_time_ns=trajectory.end_time_ns,
            sample_count=trajectory.sample_count,
            is_focal_agent=track.track_id == focal_track_id,
            is_ego_agent=track.track_id == ego_track_id,
            origin_type=OriginType.SOURCE_OBSERVATION,
            quality_flags=flags,
        )
    except ValidationError as error:
        raise SchemaError(str(error)) from None
    return agent, trajectory, agent_id, trajectory_id


def _count_tracks_by_object_type(
    tracks: tuple[_SourceTrack, ...],
) -> tuple[tuple[str, int], ...]:
    return tuple(
        (
            object_type.value,
            sum(track.object_type is object_type for track in tracks),
        )
        for object_type in Av2ObjectType
    )


def _count_tracks_by_category(
    tracks: tuple[_SourceTrack, ...],
) -> tuple[tuple[str, int], ...]:
    return tuple(
        (
            category.name,
            sum(track.category is category for track in tracks),
        )
        for category in Av2TrackCategory
    )


def load_av2_motion_scenario(
    source_root: Path,
    relative_path: str | Path,
    *,
    config: Av2MotionAdapterConfig,
) -> Av2MotionScenarioConversion:
    """Read and strictly convert one AV2 scenario Parquet file."""
    if not isinstance(config, Av2MotionAdapterConfig):
        raise ValidationError("config must be an Av2MotionAdapterConfig")
    normalized_path, path = _validated_source_path(source_root, relative_path)
    _parquet_schema(path)
    checksum = _source_checksum(path)
    try:
        table = pq.read_table(path, columns=list(_REQUIRED_COLUMNS))
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError("source scenario rows cannot be read") from error
    if table.num_rows == 0:
        raise SchemaError("source scenario must contain at least one row")
    rows = cast(list[Mapping[str, object]], table.to_pylist())
    constants = {name: _constant_source_value(rows, name) for name in _SCENARIO_COLUMNS}
    source_scenario_id = _safe_source_identifier(
        constants["scenario_id"],
        "scenario_id",
    )
    focal_track_id = _safe_source_identifier(
        constants["focal_track_id"],
        "focal_track_id",
    )
    city = _source_text(constants["city"], "city")
    start_timestamp, end_timestamp, num_timestamps, timestamp_step = _timestamp_grid(
        constants["start_timestamp"],
        constants["end_timestamp"],
        constants["num_timestamps"],
    )
    tracks = _parse_source_tracks(rows, num_timestamps=num_timestamps)
    focal_track = _validate_focal_track(tracks, focal_track_id)
    policy = _enum_value(
        Av2AgentInclusionPolicy,
        config.inclusion_policy,
        "inclusion_policy",
    )
    if (
        policy is Av2AgentInclusionPolicy.DYNAMIC_ONLY
        and focal_track.object_type.value in _STATIC_TYPES
    ):
        raise SchemaError("dynamic_only cannot exclude the focal source track")
    ego_track_id = config.ego_track_id
    if ego_track_id is not None:
        ego_track_id = _safe_source_identifier(ego_track_id, "ego_track_id")
        ego = next(
            (track for track in tracks if track.track_id == ego_track_id),
            None,
        )
        if ego is None:
            raise SchemaError("ego_track_id does not identify a source track")
        if (
            policy is Av2AgentInclusionPolicy.DYNAMIC_ONLY
            and ego.object_type.value in _STATIC_TYPES
        ):
            raise SchemaError("dynamic_only would exclude the configured ego track")
    origin_state = focal_track.states[0]
    origin_x = origin_state.position_x
    origin_y = origin_state.position_y
    scenario_id, frame_id = _canonical_ids(source_scenario_id)

    agents: list[AgentRecord] = []
    trajectories: list[Trajectory] = []
    summaries: list[Av2SourceTrackSummary] = []
    for track in tracks:
        included = (
            policy is Av2AgentInclusionPolicy.ALL_TRACKS
            or track.object_type.value in _DYNAMIC_TYPES
        )
        if included:
            agent, trajectory, agent_id, trajectory_id = _convert_track(
                track,
                scenario_id=scenario_id,
                source_scenario_id=source_scenario_id,
                focal_track_id=focal_track_id,
                ego_track_id=ego_track_id,
                origin_x=origin_x,
                origin_y=origin_y,
                start_timestamp=start_timestamp,
                timestamp_step=timestamp_step,
            )
            agents.append(agent)
            trajectories.append(trajectory)
            summaries.append(
                Av2SourceTrackSummary(
                    source_track_id=track.track_id,
                    source_object_type=track.object_type,
                    source_category=track.category,
                    included=True,
                    canonical_agent_id=agent_id,
                    canonical_trajectory_id=trajectory_id,
                    source_state_count=len(track.states),
                    exclusion_reason=None,
                )
            )
        else:
            summaries.append(
                Av2SourceTrackSummary(
                    source_track_id=track.track_id,
                    source_object_type=track.object_type,
                    source_category=track.category,
                    included=False,
                    canonical_agent_id=None,
                    canonical_trajectory_id=None,
                    source_state_count=len(track.states),
                    exclusion_reason="excluded_static_source_type",
                )
            )
    try:
        scenario = ScenarioRecord(
            scenario_id=scenario_id,
            dataset_id=_DATASET_ID,
            dataset_version=config.dataset_version,
            split_name=config.split_name,
            city_or_region=city,
            source_scenario_id=source_scenario_id,
            start_time_ns=start_timestamp,
            end_time_ns=end_timestamp,
            coordinate_frame_id=frame_id,
            origin_x_m=origin_x,
            origin_y_m=origin_y,
            origin_z_m=None,
            source_crs=_SOURCE_CRS,
            has_elevation=False,
            agent_count=len(agents),
            source_map_available=config.source_map_available,
            quality_flags=(),
            adapter_name=_ADAPTER_NAME,
            adapter_version=config.adapter_version,
            source_checksum=checksum,
        )
        coordinate_frame = CoordinateFrameRecord(
            scenario_id=scenario_id,
            coordinate_frame_id=frame_id,
            parent_frame_id=None,
            frame_type="local_cartesian",
            origin_x_m=origin_x,
            origin_y_m=origin_y,
            origin_z_m=None,
            axis_convention="right_handed_x_y_z_up",
            distance_unit="m",
            angle_unit="rad",
            timestamp_unit="ns",
            source_crs=_SOURCE_CRS,
            has_elevation=False,
            transform_to_parent_4x4=None,
            origin_type=OriginType.SOURCE_OBSERVATION,
            quality_flags=(),
        )
        return Av2MotionScenarioConversion(
            source_relative_path=normalized_path,
            source_checksum=checksum,
            source_scenario_id=source_scenario_id,
            source_focal_track_id=focal_track_id,
            source_city_name=city,
            source_track_count=len(tracks),
            source_state_count=len(rows),
            scenario=scenario,
            coordinate_frame=coordinate_frame,
            agents=tuple(agents),
            trajectories=tuple(trajectories),
            track_summaries=tuple(summaries),
            object_type_counts=_count_tracks_by_object_type(tracks),
            category_counts=_count_tracks_by_category(tracks),
        )
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def av2_motion_conversion_summary_to_dict(
    conversion: Av2MotionScenarioConversion,
) -> dict[str, object]:
    """Return the ordered JSON-compatible adapter conversion summary."""
    if not isinstance(conversion, Av2MotionScenarioConversion):
        raise ValidationError("conversion must be an Av2MotionScenarioConversion")
    included_count = sum(item.included for item in conversion.track_summaries)
    return {
        "schema_version": _SCHEMA_VERSION,
        "adapter_name": conversion.scenario.adapter_name,
        "adapter_version": conversion.scenario.adapter_version,
        "dataset_id": conversion.scenario.dataset_id,
        "dataset_version": conversion.scenario.dataset_version,
        "split_name": conversion.scenario.split_name,
        "source_relative_path": relative_path_text(conversion.source_relative_path),
        "source_checksum": conversion.source_checksum,
        "source_scenario_id": conversion.source_scenario_id,
        "source_focal_track_id": conversion.source_focal_track_id,
        "source_city_name": conversion.source_city_name,
        "source_track_count": conversion.source_track_count,
        "source_state_count": conversion.source_state_count,
        "included_track_count": included_count,
        "excluded_track_count": conversion.source_track_count - included_count,
        "object_type_counts": [
            [key, count] for key, count in conversion.object_type_counts
        ],
        "category_counts": [[key, count] for key, count in conversion.category_counts],
        "canonical_scenario_id": conversion.scenario.scenario_id,
        "canonical_coordinate_frame_id": (
            conversion.coordinate_frame.coordinate_frame_id
        ),
        "canonical_agent_ids": [item.agent_id for item in conversion.agents],
        "canonical_trajectory_ids": [
            item.trajectory_id for item in conversion.trajectories
        ],
        "track_summaries": [
            {
                "source_track_id": item.source_track_id,
                "source_object_type": _enum_value(
                    Av2ObjectType,
                    item.source_object_type,
                    "source_object_type",
                ).value,
                "source_category": _enum_value(
                    Av2TrackCategory,
                    item.source_category,
                    "source_category",
                ).value,
                "included": item.included,
                "canonical_agent_id": item.canonical_agent_id,
                "canonical_trajectory_id": item.canonical_trajectory_id,
                "source_state_count": item.source_state_count,
                "exclusion_reason": item.exclusion_reason,
            }
            for item in conversion.track_summaries
        ],
    }


def materialize_av2_motion_scenario(
    run_directory: RunDirectory,
    conversion: Av2MotionScenarioConversion,
    *,
    relative_directory: str | Path = "artifacts/av2_motion_scenario",
    row_group_size: int = 65_536,
) -> Av2MotionScenarioArtifacts:
    """Materialize one conversion as a summary and four canonical tables."""
    if not isinstance(conversion, Av2MotionScenarioConversion):
        raise ValidationError("conversion must be an Av2MotionScenarioConversion")
    try:
        directory = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    summary = atomic_write_canonical_json(
        run_directory,
        directory / "adapter_summary.json",
        av2_motion_conversion_summary_to_dict(conversion),
    )
    scenario_manifest = atomic_write_canonical_parquet(
        run_directory,
        directory / "scenario_manifest.parquet",
        scenario_records_to_table((conversion.scenario,)),
        CanonicalSchemaName.SCENARIO_MANIFEST,
        row_group_size=row_group_size,
    )
    coordinate_frames = atomic_write_canonical_parquet(
        run_directory,
        directory / "coordinate_frame_metadata.parquet",
        coordinate_frame_records_to_table((conversion.coordinate_frame,)),
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        row_group_size=row_group_size,
    )
    agents = atomic_write_canonical_parquet(
        run_directory,
        directory / "agent_metadata.parquet",
        agent_records_to_table(conversion.agents),
        CanonicalSchemaName.AGENT_METADATA,
        row_group_size=row_group_size,
    )
    samples = atomic_write_canonical_parquet(
        run_directory,
        directory / "trajectory_samples.parquet",
        trajectories_to_table(conversion.trajectories),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        row_group_size=row_group_size,
    )
    return Av2MotionScenarioArtifacts(
        adapter_summary=summary,
        scenario_manifest=scenario_manifest,
        coordinate_frame_metadata=coordinate_frames,
        agent_metadata=agents,
        trajectory_samples=samples,
    )


def _verified_summary_bytes(
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
    path = root / artifact.relative_path
    if path.is_symlink():
        raise ArtifactError("adapter summary must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("adapter summary is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("adapter summary resolves outside repository")
    if not resolved.is_file():
        raise ArtifactError("adapter summary is not a regular file")
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise ArtifactError("adapter summary cannot be read") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("adapter summary size differs")
    if hashlib.sha256(data).hexdigest() != artifact.content_checksum:
        raise ArtifactError("adapter summary checksum differs")
    return data


def _table_rows(
    repository_root: Path,
    artifact: CanonicalParquetArtifact,
) -> list[dict[str, object]]:
    table = read_canonical_parquet_table(
        repository_root,
        (artifact.written_artifact.relative_path,),
        artifact.schema_name,
    )
    return cast(list[dict[str, object]], table.to_pylist())


def _reconstruct_bundle(
    scenario_rows: list[dict[str, object]],
    frame_rows: list[dict[str, object]],
    agent_rows: list[dict[str, object]],
    sample_rows: list[dict[str, object]],
) -> None:
    if len(scenario_rows) != 1 or len(frame_rows) != 1:
        raise SchemaError("AV2 artifact bundle must contain one scenario and frame")
    try:
        scenario = ScenarioRecord(**cast(Any, scenario_rows[0]))
        frame = CoordinateFrameRecord(**cast(Any, frame_rows[0]))
        agents = tuple(AgentRecord(**cast(Any, row)) for row in agent_rows)
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in sample_rows:
            trajectory_id = row.get("trajectory_id")
            if not isinstance(trajectory_id, str):
                raise SchemaError("trajectory sample identifier is invalid")
            grouped.setdefault(trajectory_id, []).append(row)
        trajectories: list[Trajectory] = []
        for trajectory_id in sorted(grouped):
            rows = grouped[trajectory_id]
            samples = tuple(TrajectorySampleRecord(**cast(Any, row)) for row in rows)
            first = samples[0]
            trajectories.append(
                Trajectory(
                    scenario_id=first.scenario_id,
                    agent_id=first.agent_id,
                    trajectory_id=trajectory_id,
                    samples=samples,
                    origin_type=first.origin_type,
                    quality_flags=first.quality_flags,
                )
            )
        validate_scenario_bundle(
            scenario,
            frame,
            agents,
            tuple(trajectories),
        )
    except SchemaError:
        raise
    except (TypeError, ValueError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def verify_av2_motion_scenario_artifacts(
    repository_root: Path,
    conversion: Av2MotionScenarioConversion,
    artifacts: Av2MotionScenarioArtifacts,
) -> None:
    """Verify summary and canonical tables against one AV2 conversion."""
    if not isinstance(conversion, Av2MotionScenarioConversion):
        raise ValidationError("conversion must be an Av2MotionScenarioConversion")
    if not isinstance(artifacts, Av2MotionScenarioArtifacts):
        raise ValidationError("artifacts must be Av2MotionScenarioArtifacts")
    data = _verified_summary_bytes(repository_root, artifacts.adapter_summary)
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SchemaError("adapter summary is malformed") from error
    expected_summary = av2_motion_conversion_summary_to_dict(conversion)
    if not isinstance(decoded, Mapping) or decoded != expected_summary:
        raise SchemaError("adapter summary content differs from conversion")
    parquet_artifacts = (
        artifacts.scenario_manifest,
        artifacts.coordinate_frame_metadata,
        artifacts.agent_metadata,
        artifacts.trajectory_samples,
    )
    for artifact in parquet_artifacts:
        verify_canonical_parquet_artifact(repository_root, artifact)
    actual_rows = tuple(
        _table_rows(repository_root, artifact) for artifact in parquet_artifacts
    )
    expected_rows = (
        cast(
            list[dict[str, object]],
            scenario_records_to_table((conversion.scenario,)).to_pylist(),
        ),
        cast(
            list[dict[str, object]],
            coordinate_frame_records_to_table(
                (conversion.coordinate_frame,)
            ).to_pylist(),
        ),
        cast(
            list[dict[str, object]],
            agent_records_to_table(conversion.agents).to_pylist(),
        ),
        cast(
            list[dict[str, object]],
            trajectories_to_table(conversion.trajectories).to_pylist(),
        ),
    )
    if actual_rows != expected_rows:
        raise SchemaError("canonical AV2 artifact table content differs")
    _reconstruct_bundle(*actual_rows)
