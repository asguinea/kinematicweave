"""Deterministic canonical Arrow-table and Parquet I/O."""

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
import hashlib
from itertools import pairwise
from pathlib import Path
import re

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_generated_file,
)
from kinematicweave.data.schemas import (
    CanonicalSchemaDefinition,
    CanonicalSchemaName,
    get_arrow_schema,
    get_schema_definition,
    schema_fingerprint,
    validate_arrow_schema,
)
from kinematicweave.domain.map_records import (
    VectorMapElementRecord,
    vector_map_element_record_to_dict,
)
from kinematicweave.domain.procedural import (
    ProceduralSegment,
    ProceduralTape,
    ProceduralTrack,
    procedural_segment_to_dict,
    procedural_tape_manifest_to_dict,
    procedural_track_to_dict,
)
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
    agent_record_to_dict,
    coordinate_frame_record_to_dict,
    scenario_record_to_dict,
    trajectory_sample_record_to_dict,
    trajectory_to_sample_dicts,
)
from kinematicweave.domain.semantic import (
    MotionEvent,
    SemanticWaypoint,
    motion_event_to_dict,
    semantic_waypoint_to_dict,
)
from kinematicweave.domain.shared_motion import (
    MotionCategory,
    RouteTemplate,
    RouteTemplateMembership,
    motion_category_to_dict,
    route_template_membership_to_dict,
    route_template_to_dict,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.paths import normalize_relative_path

__all__ = [
    "CanonicalParquetArtifact",
    "CanonicalParquetDataset",
    "agent_records_to_table",
    "atomic_write_canonical_parquet",
    "atomic_write_canonical_parquet_parts",
    "coordinate_frame_records_to_table",
    "iter_canonical_parquet_batches",
    "motion_categories_to_table",
    "motion_events_to_table",
    "procedural_segments_to_table",
    "procedural_tape_manifest_to_table",
    "procedural_tracks_to_table",
    "read_canonical_parquet_table",
    "route_template_memberships_to_table",
    "route_templates_to_table",
    "scan_canonical_parquet",
    "scenario_records_to_table",
    "semantic_waypoints_to_table",
    "sort_canonical_table",
    "trajectories_to_table",
    "trajectory_samples_to_table",
    "validate_canonical_table",
    "vector_map_elements_to_table",
    "verify_canonical_parquet_artifact",
]

_SCHEMA_VERSION = "1.0"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_DEFAULT_ROW_GROUP_SIZE = 65_536
_DEFAULT_MAX_ROWS_PER_PART = 100_000
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
_RESERVED_PATH_PARTS = {".run.partial", ".run.complete", ".temporary"}


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


def _positive_int(value: object, field_name: str) -> int:
    normalized = _nonnegative_int(value, field_name)
    if normalized == 0:
        raise ValidationError(f"{field_name} must be greater than zero")
    return normalized


def _validate_fingerprint(value: object) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(
            "schema_fingerprint must be a lowercase 64-character SHA-256 digest"
        )
    return value


@dataclass(frozen=True, slots=True)
class CanonicalParquetArtifact:
    """Validated identity and physical metadata for one canonical Parquet file."""

    schema_name: CanonicalSchemaName
    schema_version: str
    schema_fingerprint: str
    written_artifact: WrittenArtifact
    row_count: int
    row_group_count: int

    def __post_init__(self) -> None:
        """Validate persistent artifact metadata."""
        if not isinstance(self.schema_name, CanonicalSchemaName):
            raise ValidationError("schema_name must use CanonicalSchemaName")
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self,
            "schema_fingerprint",
            _validate_fingerprint(self.schema_fingerprint),
        )
        if not isinstance(self.written_artifact, WrittenArtifact):
            raise ValidationError("written_artifact must be a WrittenArtifact")
        object.__setattr__(
            self,
            "row_count",
            _nonnegative_int(self.row_count, "row_count"),
        )
        object.__setattr__(
            self,
            "row_group_count",
            _nonnegative_int(self.row_group_count, "row_group_count"),
        )
        if self.written_artifact.size_bytes > 0 and self.row_group_count == 0:
            raise ValidationError("a nonempty file must have at least one row group")


@dataclass(frozen=True, slots=True)
class CanonicalParquetDataset:
    """Ordered metadata for deterministic canonical Parquet part files."""

    schema_name: CanonicalSchemaName
    schema_version: str
    schema_fingerprint: str
    row_count: int
    parts: tuple[CanonicalParquetArtifact, ...]

    def __post_init__(self) -> None:
        """Copy and validate part metadata and aggregate counts."""
        if not isinstance(self.schema_name, CanonicalSchemaName):
            raise ValidationError("schema_name must use CanonicalSchemaName")
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        fingerprint = _validate_fingerprint(self.schema_fingerprint)
        object.__setattr__(self, "schema_fingerprint", fingerprint)
        row_count = _nonnegative_int(self.row_count, "row_count")
        object.__setattr__(self, "row_count", row_count)

        parts_value: object = self.parts
        if isinstance(parts_value, (str, bytes)) or not isinstance(
            parts_value, Sequence
        ):
            raise ValidationError("parts must be a finite non-string sequence")
        parts = tuple(part for part in parts_value)
        if not parts:
            raise ValidationError("parts must contain at least one artifact")
        if any(not isinstance(part, CanonicalParquetArtifact) for part in parts):
            raise ValidationError("parts must contain CanonicalParquetArtifact values")
        object.__setattr__(self, "parts", parts)

        for part in parts:
            if (
                part.schema_name is not self.schema_name
                or part.schema_version != self.schema_version
                or part.schema_fingerprint != fingerprint
            ):
                raise ValidationError("all parts must use the dataset schema identity")
        paths = tuple(part.written_artifact.relative_path for part in parts)
        if len(paths) != len(set(paths)):
            raise ValidationError("part paths must be unique")
        if row_count != sum(part.row_count for part in parts):
            raise ValidationError("row_count must equal the sum of part row counts")


def _record_sequence[RecordT](
    records: object,
    record_type: type[RecordT],
    converter: Callable[[RecordT], dict[str, object]],
    expected: CanonicalSchemaName,
) -> pa.Table:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise SchemaError("records must be a finite non-string sequence")
    copied = tuple(record for record in records)
    if any(not isinstance(record, record_type) for record in copied):
        raise SchemaError(f"records must contain {record_type.__name__} values")
    rows = [converter(record) for record in copied]
    try:
        table = pa.Table.from_pylist(rows, schema=get_arrow_schema(expected))
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise SchemaError(f"record conversion failed for {expected.value}") from error
    return sort_canonical_table(table, expected)


def scenario_records_to_table(records: Sequence[ScenarioRecord]) -> pa.Table:
    """Return a canonically sorted scenario-manifest table without writing files."""
    return _record_sequence(
        records,
        ScenarioRecord,
        scenario_record_to_dict,
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )


def coordinate_frame_records_to_table(
    records: Sequence[CoordinateFrameRecord],
) -> pa.Table:
    """Return sorted canonical coordinate-frame metadata without writing files."""
    return _record_sequence(
        records,
        CoordinateFrameRecord,
        coordinate_frame_record_to_dict,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
    )


def agent_records_to_table(records: Sequence[AgentRecord]) -> pa.Table:
    """Return canonically sorted agent metadata without writing files."""
    return _record_sequence(
        records,
        AgentRecord,
        agent_record_to_dict,
        CanonicalSchemaName.AGENT_METADATA,
    )


def trajectory_samples_to_table(
    records: Sequence[TrajectorySampleRecord],
) -> pa.Table:
    """Return canonically sorted trajectory samples without writing files."""
    return _record_sequence(
        records,
        TrajectorySampleRecord,
        trajectory_sample_record_to_dict,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )


def trajectories_to_table(trajectories: Sequence[Trajectory]) -> pa.Table:
    """Flatten copied trajectories and return sorted canonical sample rows."""
    trajectories_value: object = trajectories
    if isinstance(trajectories_value, (str, bytes)) or not isinstance(
        trajectories_value, Sequence
    ):
        raise SchemaError("trajectories must be a finite non-string sequence")
    copied = tuple(trajectory for trajectory in trajectories_value)
    if any(not isinstance(trajectory, Trajectory) for trajectory in copied):
        raise SchemaError("trajectories must contain Trajectory values")
    rows = [
        row for trajectory in copied for row in trajectory_to_sample_dicts(trajectory)
    ]
    expected = CanonicalSchemaName.TRAJECTORY_SAMPLES
    try:
        table = pa.Table.from_pylist(rows, schema=get_arrow_schema(expected))
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise SchemaError("trajectory conversion failed") from error
    return sort_canonical_table(table, expected)


def vector_map_elements_to_table(
    records: Sequence[VectorMapElementRecord],
) -> pa.Table:
    """Return canonically sorted vector-map elements without writing files."""
    return _record_sequence(
        records,
        VectorMapElementRecord,
        vector_map_element_record_to_dict,
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    )


def procedural_tape_manifest_to_table(tape: ProceduralTape) -> pa.Table:
    """Return the one-row canonical procedural tape manifest."""
    return _record_sequence(
        (tape,),
        ProceduralTape,
        procedural_tape_manifest_to_dict,
        CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST,
    )


def procedural_tracks_to_table(
    records: Sequence[ProceduralTrack],
) -> pa.Table:
    """Return canonically sorted procedural track rows."""
    return _record_sequence(
        records,
        ProceduralTrack,
        procedural_track_to_dict,
        CanonicalSchemaName.PROCEDURAL_TRACKS,
    )


def procedural_segments_to_table(
    records: Sequence[ProceduralSegment],
) -> pa.Table:
    """Return canonically sorted procedural segment rows."""
    return _record_sequence(
        records,
        ProceduralSegment,
        procedural_segment_to_dict,
        CanonicalSchemaName.PROCEDURAL_SEGMENTS,
    )


def semantic_waypoints_to_table(
    records: Sequence[SemanticWaypoint],
) -> pa.Table:
    """Return canonically sorted semantic waypoint rows."""
    return _record_sequence(
        records,
        SemanticWaypoint,
        semantic_waypoint_to_dict,
        CanonicalSchemaName.SEMANTIC_WAYPOINTS,
    )


def motion_events_to_table(records: Sequence[MotionEvent]) -> pa.Table:
    """Return canonically sorted motion event rows."""
    return _record_sequence(
        records,
        MotionEvent,
        motion_event_to_dict,
        CanonicalSchemaName.MOTION_EVENTS,
    )


def motion_categories_to_table(records: Sequence[MotionCategory]) -> pa.Table:
    """Return canonically sorted shared-motion category rows."""
    return _record_sequence(
        records,
        MotionCategory,
        motion_category_to_dict,
        CanonicalSchemaName.MOTION_CATEGORIES,
    )


def route_templates_to_table(records: Sequence[RouteTemplate]) -> pa.Table:
    """Return canonically sorted route-template rows."""
    return _record_sequence(
        records,
        RouteTemplate,
        route_template_to_dict,
        CanonicalSchemaName.ROUTE_TEMPLATES,
    )


def route_template_memberships_to_table(
    records: Sequence[RouteTemplateMembership],
) -> pa.Table:
    """Return canonically sorted route-template membership rows."""
    return _record_sequence(
        records,
        RouteTemplateMembership,
        route_template_membership_to_dict,
        CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS,
    )


def _validate_field_schema(
    schema: pa.Schema,
    definition: CanonicalSchemaDefinition,
) -> None:
    canonical = definition.arrow_schema
    if len(schema) != len(canonical):
        raise SchemaError(
            f"field count differs for {definition.name.value}: "
            f"expected {len(canonical)}, received {len(schema)}"
        )
    for index, (actual, expected) in enumerate(zip(schema, canonical, strict=True)):
        if actual.name != expected.name:
            raise SchemaError(
                f"field {index} name differs: expected {expected.name!r}, "
                f"received {actual.name!r}"
            )
        if not actual.type.equals(expected.type):
            raise SchemaError(
                f"field {actual.name!r} type differs: expected {expected.type}, "
                f"received {actual.type}"
            )
        if actual.nullable != expected.nullable:
            raise SchemaError(
                f"field {actual.name!r} nullability differs: "
                f"expected {expected.nullable}, received {actual.nullable}"
            )


def _ordering_keys(
    table: pa.Table,
    definition: CanonicalSchemaDefinition,
) -> tuple[tuple[object, ...], ...]:
    columns = [
        table.column(field_name).to_pylist()
        for field_name in definition.canonical_order
    ]
    return tuple(zip(*columns, strict=True))


def _validate_sorted(
    table: pa.Table,
    definition: CanonicalSchemaDefinition,
) -> None:
    keys = _ordering_keys(table, definition)
    for previous, current in pairwise(keys):
        if previous > current:
            raise SchemaError(
                f"rows are not in canonical order for {definition.name.value}"
            )


def validate_canonical_table(
    table: pa.Table,
    expected: CanonicalSchemaName | str,
    *,
    require_sorted: bool = True,
) -> None:
    """Validate exact canonical schema, nullability, and optional row ordering."""
    if not isinstance(table, pa.Table):
        raise SchemaError("table must be a pyarrow.Table")
    if not isinstance(require_sorted, bool):
        raise SchemaError("require_sorted must be a Boolean")
    definition = get_schema_definition(expected)
    validate_arrow_schema(table.schema, definition.name)
    for field in definition.arrow_schema:
        if not field.nullable and table.column(field.name).null_count:
            raise SchemaError(f"non-nullable field contains nulls: {field.name}")
    if require_sorted:
        _validate_sorted(table, definition)


def sort_canonical_table(
    table: pa.Table,
    expected: CanonicalSchemaName | str,
) -> pa.Table:
    """Return a newly sorted table with exact canonical schema metadata."""
    validate_canonical_table(table, expected, require_sorted=False)
    definition = get_schema_definition(expected)
    try:
        sorted_table = table.sort_by(
            [(field_name, "ascending") for field_name in definition.canonical_order]
        )
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError(
            f"canonical sorting failed for {definition.name.value}"
        ) from error
    sorted_table = sorted_table.replace_schema_metadata(
        definition.arrow_schema.metadata
    )
    validate_canonical_table(sorted_table, definition.name)
    return sorted_table


def _inspect_parquet(
    path: Path,
    expected: CanonicalSchemaName,
) -> tuple[int, int]:
    try:
        with path.open("rb") as stream:
            parquet_file = pq.ParquetFile(stream)
            validate_arrow_schema(parquet_file.schema_arrow, expected)
            metadata = parquet_file.metadata
            return metadata.num_rows, metadata.num_row_groups
    except SchemaError:
        raise
    except FileNotFoundError:
        raise ArtifactError(f"Parquet artifact is missing: {path.name}") from None
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError(f"invalid canonical Parquet file: {path.name}") from error


def atomic_write_canonical_parquet(
    run_directory: RunDirectory,
    relative_path: str | Path,
    table: pa.Table,
    expected: CanonicalSchemaName | str,
    *,
    row_group_size: int = _DEFAULT_ROW_GROUP_SIZE,
) -> CanonicalParquetArtifact:
    """Sort, atomically write, reopen, and verify one canonical Parquet table."""
    normalized_row_group_size = _positive_int(row_group_size, "row_group_size")
    definition = get_schema_definition(expected)
    sorted_table = sort_canonical_table(table, definition.name)

    def write_parquet(path: Path) -> None:
        try:
            pq.write_table(
                sorted_table,
                path,
                row_group_size=normalized_row_group_size,
                **_PARQUET_WRITE_OPTIONS,
            )
        except (OSError, pa.ArrowException, TypeError, ValueError) as error:
            raise SchemaError(
                f"failed to write canonical Parquet for {definition.name.value}"
            ) from error

    written = atomic_write_generated_file(
        run_directory,
        relative_path,
        write_parquet,
    )
    final_path = run_directory.repository_root / written.relative_path
    row_count, row_group_count = _inspect_parquet(final_path, definition.name)
    if row_count != sorted_table.num_rows:
        raise SchemaError(
            f"Parquet row count differs: expected {sorted_table.num_rows}, "
            f"received {row_count}"
        )
    return CanonicalParquetArtifact(
        schema_name=definition.name,
        schema_version=definition.version,
        schema_fingerprint=schema_fingerprint(definition.name),
        written_artifact=written,
        row_count=row_count,
        row_group_count=row_group_count,
    )


def _normalized_relative_directory(relative_directory: str | Path) -> Path:
    try:
        normalized = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    if any(part in _RESERVED_PATH_PARTS for part in normalized.parts):
        raise ArtifactError("relative_directory uses a reserved run path")
    return normalized


def _normalize_batch(
    batch: object,
    definition: CanonicalSchemaDefinition,
) -> pa.RecordBatch:
    if not isinstance(batch, pa.RecordBatch):
        raise SchemaError("batches must contain pyarrow.RecordBatch values")
    _validate_field_schema(batch.schema, definition)
    normalized = pa.RecordBatch.from_arrays(
        batch.columns,
        schema=definition.arrow_schema,
    )
    table = pa.Table.from_batches([normalized], schema=definition.arrow_schema)
    validate_canonical_table(table, definition.name)
    return normalized


def atomic_write_canonical_parquet_parts(
    run_directory: RunDirectory,
    relative_directory: str | Path,
    batches: Iterable[pa.RecordBatch],
    expected: CanonicalSchemaName | str,
    *,
    max_rows_per_part: int = _DEFAULT_MAX_ROWS_PER_PART,
    row_group_size: int = _DEFAULT_ROW_GROUP_SIZE,
) -> CanonicalParquetDataset:
    """Consume sorted batches once and atomically write deterministic part files."""
    maximum_rows = _positive_int(max_rows_per_part, "max_rows_per_part")
    normalized_row_group_size = _positive_int(row_group_size, "row_group_size")
    definition = get_schema_definition(expected)
    directory = _normalized_relative_directory(relative_directory)
    try:
        batch_iterator = iter(batches)
    except TypeError:
        raise ValidationError("batches must be iterable") from None

    parts: list[CanonicalParquetArtifact] = []
    accumulated: list[pa.RecordBatch] = []
    accumulated_rows = 0
    previous_key: tuple[object, ...] | None = None

    def write_part() -> None:
        nonlocal accumulated, accumulated_rows
        table = pa.Table.from_batches(
            accumulated,
            schema=definition.arrow_schema,
        )
        part_path = directory / f"part-{len(parts):05d}.parquet"
        parts.append(
            atomic_write_canonical_parquet(
                run_directory,
                part_path,
                table,
                definition.name,
                row_group_size=normalized_row_group_size,
            )
        )
        accumulated = []
        accumulated_rows = 0

    for raw_batch in batch_iterator:
        batch = _normalize_batch(raw_batch, definition)
        batch_table = pa.Table.from_batches(
            [batch],
            schema=definition.arrow_schema,
        )
        keys = _ordering_keys(batch_table, definition)
        if keys and previous_key is not None and previous_key > keys[0]:
            raise SchemaError("batches are not in global canonical order")
        if keys:
            previous_key = keys[-1]

        offset = 0
        while offset < batch.num_rows:
            available = maximum_rows - accumulated_rows
            row_count = min(available, batch.num_rows - offset)
            accumulated.append(batch.slice(offset, row_count))
            accumulated_rows += row_count
            offset += row_count
            if accumulated_rows == maximum_rows:
                write_part()

    if accumulated_rows or not parts:
        write_part()

    fingerprint = schema_fingerprint(definition.name)
    return CanonicalParquetDataset(
        schema_name=definition.name,
        schema_version=definition.version,
        schema_fingerprint=fingerprint,
        row_count=sum(part.row_count for part in parts),
        parts=tuple(parts),
    )


def _validated_repository_root(repository_root: Path) -> Path:
    if not isinstance(repository_root, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        resolved = repository_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not resolved.is_dir():
        raise ArtifactError("repository_root must be a directory")
    return resolved


def _validated_parquet_paths(
    repository_root: Path,
    relative_paths: Sequence[str | Path],
) -> tuple[Path, ...]:
    root = _validated_repository_root(repository_root)
    if isinstance(relative_paths, (str, bytes)) or not isinstance(
        relative_paths, Sequence
    ):
        raise ArtifactError("relative_paths must be a finite non-string sequence")
    normalized_paths: list[Path] = []
    for value in relative_paths:
        try:
            normalized = normalize_relative_path(value)
        except ValidationError as error:
            raise ArtifactError(str(error)) from None
        normalized_paths.append(normalized)
    if not normalized_paths:
        raise ArtifactError("at least one Parquet path is required")
    if len(normalized_paths) != len(set(normalized_paths)):
        raise ArtifactError("Parquet paths must be unique")

    resolved_paths: list[Path] = []
    for relative_path in normalized_paths:
        candidate = root / relative_path
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise ArtifactError(
                f"Parquet path does not exist: {relative_path.as_posix()}"
            ) from error
        if resolved == root or not resolved.is_relative_to(root):
            raise ArtifactError("Parquet path resolves outside repository_root")
        if not resolved.is_file():
            raise ArtifactError(
                f"Parquet path is not a file: {relative_path.as_posix()}"
            )
        resolved_paths.append(resolved)
    return tuple(resolved_paths)


def _canonical_batch_table(
    batch: pa.RecordBatch,
    definition: CanonicalSchemaDefinition,
) -> tuple[pa.RecordBatch, pa.Table]:
    _validate_field_schema(batch.schema, definition)
    normalized = pa.RecordBatch.from_arrays(
        batch.columns,
        schema=definition.arrow_schema,
    )
    table = pa.Table.from_batches([normalized], schema=definition.arrow_schema)
    validate_canonical_table(table, definition.name)
    return normalized, table


def iter_canonical_parquet_batches(
    repository_root: Path,
    relative_paths: Sequence[str | Path],
    expected: CanonicalSchemaName | str,
    *,
    batch_size: int = _DEFAULT_ROW_GROUP_SIZE,
) -> Iterator[pa.RecordBatch]:
    """Yield bounded canonical batches while opening one Parquet file at a time."""
    normalized_batch_size = _positive_int(batch_size, "batch_size")
    definition = get_schema_definition(expected)
    paths = _validated_parquet_paths(repository_root, relative_paths)

    def iterate() -> Iterator[pa.RecordBatch]:
        previous_key: tuple[object, ...] | None = None
        for path in paths:
            try:
                stream = path.open("rb")
            except FileNotFoundError:
                raise ArtifactError(
                    f"Parquet artifact is missing: {path.name}"
                ) from None
            except OSError as error:
                raise SchemaError(f"cannot open Parquet file: {path.name}") from error
            with stream:
                try:
                    parquet_file = pq.ParquetFile(stream)
                    validate_arrow_schema(parquet_file.schema_arrow, definition.name)
                    batches = parquet_file.iter_batches(
                        batch_size=normalized_batch_size
                    )
                except SchemaError:
                    raise
                except (OSError, pa.ArrowException, TypeError, ValueError) as error:
                    raise SchemaError(
                        f"invalid canonical Parquet file: {path.name}"
                    ) from error

                while True:
                    try:
                        raw_batch = next(batches)
                    except StopIteration:
                        break
                    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
                        raise SchemaError(
                            f"failed to read canonical Parquet file: {path.name}"
                        ) from error
                    batch, table = _canonical_batch_table(raw_batch, definition)
                    keys = _ordering_keys(table, definition)
                    if keys and previous_key is not None and previous_key > keys[0]:
                        raise SchemaError(
                            "Parquet files are not in global canonical order"
                        )
                    if keys:
                        previous_key = keys[-1]
                    yield batch

    return iterate()


def read_canonical_parquet_table(
    repository_root: Path,
    relative_paths: Sequence[str | Path],
    expected: CanonicalSchemaName | str,
) -> pa.Table:
    """Collect canonical Parquet files for small datasets and tests only."""
    definition = get_schema_definition(expected)
    batches = tuple(
        iter_canonical_parquet_batches(
            repository_root,
            relative_paths,
            definition.name,
        )
    )
    table = pa.Table.from_batches(batches, schema=definition.arrow_schema)
    validate_canonical_table(table, definition.name)
    return table


def scan_canonical_parquet(
    repository_root: Path,
    relative_paths: Sequence[str | Path],
    expected: CanonicalSchemaName | str,
) -> pl.LazyFrame:
    """Return a fresh ordered lazy Polars scan after path and schema validation."""
    definition = get_schema_definition(expected)
    paths = _validated_parquet_paths(repository_root, relative_paths)
    for path in paths:
        _inspect_parquet(path, definition.name)
    columns = list(definition.arrow_schema.names)
    scans = [pl.scan_parquet(path).select(columns) for path in paths]
    return pl.concat(scans, how="vertical")


def _hash_path(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
    except OSError as error:
        raise ArtifactError(f"cannot read artifact bytes: {path.name}") from error
    return size_bytes, digest.hexdigest()


def verify_canonical_parquet_artifact(
    repository_root: Path,
    artifact: CanonicalParquetArtifact,
) -> None:
    """Verify path, bytes, canonical schema, and physical Parquet counts."""
    if not isinstance(artifact, CanonicalParquetArtifact):
        raise ValidationError("artifact must be a CanonicalParquetArtifact")
    paths = _validated_parquet_paths(
        repository_root,
        (artifact.written_artifact.relative_path,),
    )
    path = paths[0]
    size_bytes, checksum = _hash_path(path)
    if size_bytes != artifact.written_artifact.size_bytes:
        raise ArtifactError(
            f"artifact size differs: expected {artifact.written_artifact.size_bytes}, "
            f"received {size_bytes}"
        )
    if checksum != artifact.written_artifact.content_checksum:
        raise ArtifactError("artifact SHA-256 checksum differs")
    expected_fingerprint = schema_fingerprint(artifact.schema_name)
    if artifact.schema_fingerprint != expected_fingerprint:
        raise SchemaError("artifact schema fingerprint differs")
    row_count, row_group_count = _inspect_parquet(path, artifact.schema_name)
    if row_count != artifact.row_count:
        raise SchemaError(
            f"artifact row count differs: expected {artifact.row_count}, "
            f"received {row_count}"
        )
    if row_group_count != artifact.row_group_count:
        raise SchemaError(
            "artifact row-group count differs: "
            f"expected {artifact.row_group_count}, received {row_group_count}"
        )
