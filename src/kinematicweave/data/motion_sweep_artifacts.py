"""Immutable module-owned Parquet artifacts for the Phase 4 motion sweep."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import os
from pathlib import Path
import tempfile

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.errors import ArtifactError, SchemaError, ValidationError

SWEEP_ARTIFACT_SCHEMA_VERSION = "1.0"
_ROW_GROUP_SIZE = 65_536
_WRITE_OPTIONS: dict[str, object] = {
    "compression": "zstd",
    "compression_level": 3,
    "use_dictionary": False,
    "write_statistics": True,
    "version": "2.6",
    "data_page_version": "1.0",
    "use_compliant_nested_type": True,
    "store_schema": True,
}


class MotionSweepTable(StrEnum):
    """Fixed vocabulary for derived sweep result tables."""

    SCENARIO_CONFIGURATION = "scenario_configuration_results"
    CONFIGURATION_AGGREGATE = "configuration_results"
    MATCHED_BUDGET = "matched_budget_selections"
    FAILURE = "sweep_failures"


_FIELDS: dict[MotionSweepTable, list[pa.Field]] = {
    MotionSweepTable.SCENARIO_CONFIGURATION: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("parameter_identity", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("family", pa.string(), nullable=False),
        pa.field("source_scenario_id", pa.string(), nullable=False),
        pa.field("scenario_id", pa.string(), nullable=False),
        pa.field("selection_rank", pa.int64(), nullable=False),
        pa.field("trajectory_count", pa.int64(), nullable=False),
        pa.field("source_sample_count", pa.int64(), nullable=False),
        pa.field("valid_sample_count", pa.int64(), nullable=False),
        pa.field("retained_keyframe_count", pa.int64(), nullable=False),
        pa.field("procedural_segment_count", pa.int64(), nullable=False),
        pa.field("hold_segment_count", pa.int64(), nullable=False),
        pa.field("linear_segment_count", pa.int64(), nullable=False),
        pa.field("hermite_segment_count", pa.int64(), nullable=False),
        pa.field("serialized_representation_bytes", pa.int64(), nullable=False),
        pa.field("raw_canonical_bytes", pa.int64(), nullable=False),
        pa.field("exact_adjacent_segments", pa.int64(), nullable=False),
        pa.field("byte_ratio", pa.float64(), nullable=False),
        pa.field("keyframe_ratio", pa.float64(), nullable=False),
        pa.field("segment_ratio", pa.float64(), nullable=False),
        pa.field("position_mean_m", pa.float64(), nullable=True),
        pa.field("position_p95_m", pa.float64(), nullable=True),
        pa.field("heading_mean_rad", pa.float64(), nullable=True),
        pa.field("velocity_mean_mps", pa.float64(), nullable=True),
        pa.field("semantic_micro_f1", pa.float64(), nullable=False),
        pa.field("encoding_seconds", pa.float64(), nullable=False),
        pa.field("artifact_writing_seconds", pa.float64(), nullable=False),
        pa.field("artifact_verification_seconds", pa.float64(), nullable=False),
        pa.field("replay_evaluation_seconds", pa.float64(), nullable=False),
        pa.field("semantic_redetection_seconds", pa.float64(), nullable=False),
        pa.field("total_seconds", pa.float64(), nullable=False),
        pa.field("peak_process_rss_bytes", pa.int64(), nullable=False),
        pa.field("output_disk_bytes", pa.int64(), nullable=False),
        pa.field("artifact_summary_json", pa.string(), nullable=False),
        pa.field("motion_summary_json", pa.string(), nullable=False),
        pa.field("semantic_summary_json", pa.string(), nullable=False),
    ],
    MotionSweepTable.CONFIGURATION_AGGREGATE: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("parameter_identity", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("family", pa.string(), nullable=False),
        pa.field("scenario_count", pa.int64(), nullable=False),
        pa.field("trajectory_count", pa.int64(), nullable=False),
        pa.field("serialized_representation_bytes", pa.int64(), nullable=False),
        pa.field("raw_canonical_bytes", pa.int64(), nullable=False),
        pa.field("retained_keyframe_count", pa.int64(), nullable=False),
        pa.field("valid_sample_count", pa.int64(), nullable=False),
        pa.field("procedural_segment_count", pa.int64(), nullable=False),
        pa.field("exact_adjacent_segments", pa.int64(), nullable=False),
        pa.field("byte_ratio", pa.float64(), nullable=False),
        pa.field("keyframe_ratio", pa.float64(), nullable=False),
        pa.field("segment_ratio", pa.float64(), nullable=False),
        pa.field("position_mean_m", pa.float64(), nullable=True),
        pa.field("position_p95_m", pa.float64(), nullable=True),
        pa.field("heading_mean_rad", pa.float64(), nullable=True),
        pa.field("velocity_mean_mps", pa.float64(), nullable=True),
        pa.field("semantic_micro_f1", pa.float64(), nullable=False),
        pa.field("total_seconds", pa.float64(), nullable=False),
        pa.field("peak_process_rss_bytes", pa.int64(), nullable=False),
        pa.field("output_disk_bytes", pa.int64(), nullable=False),
        pa.field("failure_count", pa.int64(), nullable=False),
    ],
    MotionSweepTable.MATCHED_BUDGET: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("dimension", pa.string(), nullable=False),
        pa.field("family", pa.string(), nullable=False),
        pa.field("target", pa.float64(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("parameter_identity", pa.string(), nullable=False),
        pa.field("actual_budget", pa.float64(), nullable=False),
        pa.field("relation", pa.string(), nullable=False),
        pa.field("absolute_mismatch", pa.float64(), nullable=False),
    ],
    MotionSweepTable.FAILURE: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("parameter_identity", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("source_scenario_id", pa.string(), nullable=False),
        pa.field("selection_rank", pa.int64(), nullable=False),
        pa.field("attempt_count", pa.int64(), nullable=False),
        pa.field("error_type", pa.string(), nullable=False),
        pa.field("error_message", pa.string(), nullable=False),
    ],
}
_PRIMARY_KEYS: dict[MotionSweepTable, tuple[str, ...]] = {
    MotionSweepTable.SCENARIO_CONFIGURATION: (
        "parameter_identity",
        "source_scenario_id",
    ),
    MotionSweepTable.CONFIGURATION_AGGREGATE: ("parameter_identity",),
    MotionSweepTable.MATCHED_BUDGET: ("dimension", "family", "target"),
    MotionSweepTable.FAILURE: (
        "parameter_identity",
        "source_scenario_id",
        "attempt_count",
    ),
}


def sweep_arrow_schema(table_name: MotionSweepTable) -> pa.Schema:
    """Return an exact module-owned sweep schema."""
    if not isinstance(table_name, MotionSweepTable):
        raise ValidationError("table_name must be MotionSweepTable")
    return pa.schema(
        _FIELDS[table_name],
        metadata={
            b"kinematicweave.schema_name": table_name.value.encode(),
            b"kinematicweave.schema_version": SWEEP_ARTIFACT_SCHEMA_VERSION.encode(),
            b"kinematicweave.primary_key": ",".join(_PRIMARY_KEYS[table_name]).encode(),
            b"kinematicweave.schema_owner": b"kinematicweave.data.motion_sweep_artifacts",
        },
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class MotionSweepArtifact:
    """Exact physical identity for one derived sweep Parquet table."""

    table_name: MotionSweepTable
    path: Path
    size_bytes: int
    sha256: str
    row_count: int
    row_group_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.table_name, MotionSweepTable):
            raise ValidationError("table_name must be MotionSweepTable")
        if not isinstance(self.path, Path):
            raise ValidationError("path must be a Path")
        for field_name in ("size_bytes", "row_count", "row_group_count"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError(f"{field_name} must be nonnegative")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValidationError("sha256 must be lowercase SHA-256")

    def to_dict(self) -> dict[str, object]:
        """Return a canonical dictionary representation."""
        return {
            "table_name": self.table_name.value,
            "path": self.path.as_posix(),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "row_group_count": self.row_group_count,
        }


def sweep_records_to_table(
    records: Sequence[Mapping[str, object]],
    table_name: MotionSweepTable,
) -> pa.Table:
    """Build an exact canonically sorted sweep table."""
    schema = sweep_arrow_schema(table_name)
    try:
        table = pa.Table.from_pylist(
            [dict(record) for record in records], schema=schema
        )
        if table.num_rows:
            table = table.sort_by(
                [(field_name, "ascending") for field_name in _PRIMARY_KEYS[table_name]]
            )
        table = table.replace_schema_metadata(schema.metadata)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError(f"cannot build {table_name.value} table") from error
    validate_sweep_table(table, table_name)
    return table


def validate_sweep_table(table: pa.Table, table_name: MotionSweepTable) -> None:
    """Validate schema, metadata, nonnull fields, ordering, and uniqueness."""
    if not isinstance(table, pa.Table):
        raise SchemaError("table must be a pyarrow.Table")
    schema = sweep_arrow_schema(table_name)
    if not table.schema.equals(schema, check_metadata=True):
        raise SchemaError(f"{table_name.value} schema differs")
    for field in schema:
        if not field.nullable and table.column(field.name).null_count:
            raise SchemaError(f"non-nullable field contains nulls: {field.name}")
    if not table.num_rows:
        return
    keys = tuple(
        zip(
            *(table.column(name).to_pylist() for name in _PRIMARY_KEYS[table_name]),
            strict=True,
        )
    )
    if keys != tuple(sorted(keys)):
        raise SchemaError(f"{table_name.value} rows are not canonically sorted")
    if len(keys) != len(set(keys)):
        raise SchemaError(f"{table_name.value} primary keys are not unique")


def write_sweep_parquet(
    path: Path,
    records: Sequence[Mapping[str, object]],
    table_name: MotionSweepTable,
) -> MotionSweepArtifact:
    """Immutably write and verify one derived sweep table."""
    if not isinstance(path, Path):
        raise ValidationError("path must be a Path")
    if path.exists() or path.is_symlink():
        raise ArtifactError(f"sweep artifact already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    table = sweep_records_to_table(records, table_name)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        pq.write_table(
            table,
            temporary,
            row_group_size=_ROW_GROUP_SIZE,
            **_WRITE_OPTIONS,
        )
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ArtifactError(f"sweep artifact already exists: {path.name}") from None
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    parquet = pq.ParquetFile(path)
    artifact = MotionSweepArtifact(
        table_name,
        path,
        path.stat().st_size,
        _sha256(path),
        parquet.metadata.num_rows,
        parquet.metadata.num_row_groups,
    )
    verify_sweep_parquet(artifact)
    return artifact


def read_sweep_parquet(
    path: Path,
    table_name: MotionSweepTable,
    *,
    maximum_rows: int,
    batch_size: int = _ROW_GROUP_SIZE,
) -> pa.Table:
    """Boundedly read and validate one sweep table."""
    if (
        not isinstance(maximum_rows, int)
        or isinstance(maximum_rows, bool)
        or maximum_rows < 0
    ):
        raise ValidationError("maximum_rows must be nonnegative")
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or batch_size <= 0
    ):
        raise ValidationError("batch_size must be positive")
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("sweep artifact is not a regular file")
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows > maximum_rows:
        raise ArtifactError("sweep artifact exceeds bounded row limit")
    batches = tuple(parquet.iter_batches(batch_size=batch_size))
    table = (
        pa.Table.from_batches(batches)
        if batches
        else pa.Table.from_pylist([], schema=sweep_arrow_schema(table_name))
    )
    table = table.replace_schema_metadata(sweep_arrow_schema(table_name).metadata)
    validate_sweep_table(table, table_name)
    return table


def verify_sweep_parquet(artifact: MotionSweepArtifact) -> None:
    """Verify bytes, schema, ordering, rows, and row groups."""
    path = artifact.path
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("sweep artifact is missing or not regular")
    if path.stat().st_size != artifact.size_bytes or _sha256(path) != artifact.sha256:
        raise ArtifactError("sweep artifact bytes differ")
    table = read_sweep_parquet(
        path,
        artifact.table_name,
        maximum_rows=artifact.row_count,
    )
    parquet = pq.ParquetFile(path)
    if (
        table.num_rows != artifact.row_count
        or parquet.metadata.num_row_groups != artifact.row_group_count
    ):
        raise ArtifactError("sweep artifact physical counts differ")
