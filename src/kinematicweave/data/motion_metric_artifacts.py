"""Immutable module-owned Parquet artifacts for Phase 4 motion metrics."""

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
from kinematicweave.metrics.motion import METRICS_SCHEMA_VERSION

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
_ROW_GROUP_SIZE = 65_536


class MotionMetricTable(StrEnum):
    """Fixed derived result-table vocabulary."""

    TRAJECTORY_MOTION = "trajectory_motion_metrics"
    TRAJECTORY_EVENT = "trajectory_event_metrics"
    SCENARIO_METHOD = "scenario_method_metrics"
    EVALUATION_FAILURE = "evaluation_failures"


_STATISTICS = pa.struct(
    [
        pa.field("count", pa.int64(), nullable=False),
        pa.field("mean", pa.float64(), nullable=True),
        pa.field("median", pa.float64(), nullable=True),
        pa.field("p95", pa.float64(), nullable=True),
        pa.field("maximum", pa.float64(), nullable=True),
        pa.field("sum", pa.float64(), nullable=False),
        pa.field("sum_of_squares", pa.float64(), nullable=False),
    ]
)
_ARTIFACT_FILE = pa.struct(
    [
        pa.field("name", pa.string(), nullable=False),
        pa.field("size_bytes", pa.int64(), nullable=False),
        pa.field("sha256", pa.string(), nullable=False),
        pa.field("row_count", pa.int64(), nullable=True),
        pa.field("row_group_count", pa.int64(), nullable=True),
    ]
)
_FLOAT_LIST = pa.list_(pa.field("element", pa.float64(), nullable=True))
_INT_LIST = pa.list_(pa.field("element", pa.int64(), nullable=True))
_ARTIFACT_LIST = pa.list_(pa.field("element", _ARTIFACT_FILE, nullable=True))

_FIELDS: dict[MotionMetricTable, list[pa.Field]] = {
    MotionMetricTable.TRAJECTORY_MOTION: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("configuration_id", pa.string(), nullable=False),
        pa.field("scenario_id", pa.string(), nullable=False),
        pa.field("trajectory_id", pa.string(), nullable=False),
        pa.field("replay_hash", pa.string(), nullable=False),
        pa.field("source_sample_count", pa.int64(), nullable=False),
        pa.field("valid_sample_count", pa.int64(), nullable=False),
        pa.field("valid_run_count", pa.int64(), nullable=False),
        pa.field("retained_keyframe_count", pa.int64(), nullable=False),
        pa.field("segment_count", pa.int64(), nullable=False),
        pa.field("hold_count", pa.int64(), nullable=False),
        pa.field("linear_count", pa.int64(), nullable=False),
        pa.field("hermite_count", pa.int64(), nullable=False),
        pa.field("position_errors_m", _FLOAT_LIST, nullable=False),
        pa.field("position_statistics", _STATISTICS, nullable=False),
        pa.field("heading_errors_rad", _FLOAT_LIST, nullable=False),
        pa.field("heading_statistics", _STATISTICS, nullable=False),
        pa.field("heading_compared_count", pa.int64(), nullable=False),
        pa.field("heading_missing_count", pa.int64(), nullable=False),
        pa.field("velocity_errors_mps", _FLOAT_LIST, nullable=False),
        pa.field("velocity_statistics", _STATISTICS, nullable=False),
        pa.field("velocity_compared_count", pa.int64(), nullable=False),
        pa.field("velocity_missing_count", pa.int64(), nullable=False),
        pa.field("endpoint_position_errors_m", _FLOAT_LIST, nullable=False),
        pa.field("endpoint_position_maximum_m", pa.float64(), nullable=True),
        pa.field("endpoint_heading_errors_rad", _FLOAT_LIST, nullable=False),
        pa.field("endpoint_heading_maximum_rad", pa.float64(), nullable=True),
        pa.field("endpoint_velocity_errors_mps", _FLOAT_LIST, nullable=False),
        pa.field("endpoint_velocity_maximum_mps", pa.float64(), nullable=True),
        pa.field("invalid_source_timestamp_count", pa.int64(), nullable=False),
        pa.field("midpoint_probe_count", pa.int64(), nullable=False),
        pa.field("unexpected_replay_state_count", pa.int64(), nullable=False),
        pa.field("gap_preservation_passed", pa.bool_(), nullable=False),
    ],
    MotionMetricTable.TRAJECTORY_EVENT: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("configuration_id", pa.string(), nullable=False),
        pa.field("scenario_id", pa.string(), nullable=False),
        pa.field("trajectory_id", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("source_event_count", pa.int64(), nullable=False),
        pa.field("replay_event_count", pa.int64(), nullable=False),
        pa.field("matched_event_count", pa.int64(), nullable=False),
        pa.field("precision", pa.float64(), nullable=False),
        pa.field("recall", pa.float64(), nullable=False),
        pa.field("f1", pa.float64(), nullable=False),
        pa.field("start_boundary_errors_ns", _INT_LIST, nullable=False),
        pa.field("start_boundary_statistics_ns", _STATISTICS, nullable=False),
        pa.field("end_boundary_errors_ns", _INT_LIST, nullable=False),
        pa.field("end_boundary_statistics_ns", _STATISTICS, nullable=False),
        pa.field("anchor_time_errors_ns", _INT_LIST, nullable=False),
        pa.field("anchor_time_statistics_ns", _STATISTICS, nullable=False),
        pa.field("unmatched_source_count", pa.int64(), nullable=False),
        pa.field("unmatched_replay_count", pa.int64(), nullable=False),
    ],
    MotionMetricTable.SCENARIO_METHOD: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("configuration_id", pa.string(), nullable=False),
        pa.field("scenario_id", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("artifact_files", _ARTIFACT_LIST, nullable=False),
        pa.field("serialized_representation_bytes", pa.int64(), nullable=False),
        pa.field("summary_manifest_bytes", pa.int64(), nullable=False),
        pa.field("artifact_bundle_bytes", pa.int64(), nullable=False),
        pa.field("output_disk_bytes", pa.int64(), nullable=False),
        pa.field("encoding_seconds", pa.float64(), nullable=False),
        pa.field("artifact_writing_seconds", pa.float64(), nullable=False),
        pa.field("artifact_verification_seconds", pa.float64(), nullable=False),
        pa.field("replay_evaluation_seconds", pa.float64(), nullable=False),
        pa.field("semantic_redetection_seconds", pa.float64(), nullable=False),
        pa.field("total_seconds", pa.float64(), nullable=False),
        pa.field("peak_process_rss_bytes", pa.int64(), nullable=False),
    ],
    MotionMetricTable.EVALUATION_FAILURE: [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("configuration_id", pa.string(), nullable=False),
        pa.field("scenario_id", pa.string(), nullable=False),
        pa.field("trajectory_id", pa.string(), nullable=True),
        pa.field("failure_stage", pa.string(), nullable=False),
        pa.field("error_type", pa.string(), nullable=False),
        pa.field("error_message", pa.string(), nullable=False),
    ],
}
_PRIMARY_KEYS: dict[MotionMetricTable, tuple[str, ...]] = {
    MotionMetricTable.TRAJECTORY_MOTION: (
        "method_id",
        "configuration_id",
        "scenario_id",
        "trajectory_id",
    ),
    MotionMetricTable.TRAJECTORY_EVENT: (
        "method_id",
        "configuration_id",
        "scenario_id",
        "trajectory_id",
        "event_type",
    ),
    MotionMetricTable.SCENARIO_METHOD: (
        "method_id",
        "configuration_id",
        "scenario_id",
    ),
    MotionMetricTable.EVALUATION_FAILURE: (
        "method_id",
        "configuration_id",
        "scenario_id",
        "trajectory_id",
        "failure_stage",
    ),
}


def metric_arrow_schema(table_name: MotionMetricTable) -> pa.Schema:
    """Return one exact module-owned schema with persistent metadata."""
    if not isinstance(table_name, MotionMetricTable):
        raise ValidationError("table_name must be MotionMetricTable")
    primary_key = ",".join(_PRIMARY_KEYS[table_name])
    return pa.schema(
        _FIELDS[table_name],
        metadata={
            b"kinematicweave.schema_name": table_name.value.encode(),
            b"kinematicweave.schema_version": METRICS_SCHEMA_VERSION.encode(),
            b"kinematicweave.primary_key": primary_key.encode(),
            b"kinematicweave.schema_owner": b"kinematicweave.data.motion_metric_artifacts",
        },
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class MotionMetricArtifact:
    """Exact physical identity for one derived metric Parquet table."""

    table_name: MotionMetricTable
    path: Path
    size_bytes: int
    sha256: str
    row_count: int
    row_group_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.table_name, MotionMetricTable):
            raise ValidationError("table_name must be MotionMetricTable")
        if not isinstance(self.path, Path):
            raise ValidationError("path must be a Path")
        for name in ("size_bytes", "row_count", "row_group_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError(f"{name} must be nonnegative")
        if not isinstance(self.sha256, str) or len(self.sha256) != 64:
            raise ValidationError("sha256 must be a lowercase digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "table_name": self.table_name.value,
            "path": self.path.as_posix(),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "row_group_count": self.row_group_count,
        }


def metric_records_to_table(
    records: Sequence[Mapping[str, object]],
    table_name: MotionMetricTable,
) -> pa.Table:
    """Convert records to the exact schema and canonical primary-key order."""
    schema = metric_arrow_schema(table_name)
    rows = [dict(record) for record in records]
    try:
        table = pa.Table.from_pylist(rows, schema=schema)
        if table.num_rows:
            table = table.sort_by(
                [(name, "ascending") for name in _PRIMARY_KEYS[table_name]]
            )
        table = table.replace_schema_metadata(schema.metadata)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise SchemaError(f"cannot build {table_name.value} table") from error
    validate_metric_table(table, table_name)
    return table


def validate_metric_table(table: pa.Table, table_name: MotionMetricTable) -> None:
    """Validate exact fields, metadata, nullability, and primary-key ordering."""
    if not isinstance(table, pa.Table):
        raise SchemaError("table must be a pyarrow.Table")
    expected = metric_arrow_schema(table_name)
    if not table.schema.equals(expected, check_metadata=True):
        raise SchemaError(f"{table_name.value} schema differs")
    for field in expected:
        if not field.nullable and table.column(field.name).null_count:
            raise SchemaError(f"non-nullable field contains nulls: {field.name}")
    if table.num_rows:
        keys = tuple(
            zip(
                *(table.column(name).to_pylist() for name in _PRIMARY_KEYS[table_name]),
                strict=True,
            )
        )
        normalized = tuple(
            tuple("" if value is None else value for value in key) for key in keys
        )
        if normalized != tuple(sorted(normalized)):
            raise SchemaError(f"{table_name.value} rows are not canonically sorted")
        if len(normalized) != len(set(normalized)):
            raise SchemaError(f"{table_name.value} primary keys are not unique")


def write_metric_parquet(
    path: Path,
    records: Sequence[Mapping[str, object]],
    table_name: MotionMetricTable,
) -> MotionMetricArtifact:
    """Immutably write one canonically sorted derived result table."""
    if not isinstance(path, Path):
        raise ValidationError("path must be a Path")
    if path.exists() or path.is_symlink():
        raise ArtifactError(f"metric artifact already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    table = metric_records_to_table(records, table_name)
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
            raise ArtifactError(
                f"metric artifact already exists: {path.name}"
            ) from None
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    parquet = pq.ParquetFile(path)
    artifact = MotionMetricArtifact(
        table_name=table_name,
        path=path,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        row_count=parquet.metadata.num_rows,
        row_group_count=parquet.metadata.num_row_groups,
    )
    verify_metric_parquet(artifact)
    return artifact


def read_metric_parquet(
    path: Path,
    table_name: MotionMetricTable,
    *,
    maximum_rows: int,
    batch_size: int = 65_536,
) -> pa.Table:
    """Boundedly read and validate one derived metric table."""
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
        raise ArtifactError("metric artifact is not a regular file")
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows > maximum_rows:
        raise ArtifactError("metric artifact exceeds bounded row limit")
    batches = tuple(parquet.iter_batches(batch_size=batch_size))
    table = (
        pa.Table.from_batches(batches)
        if batches
        else pa.Table.from_pylist([], schema=metric_arrow_schema(table_name))
    )
    table = table.replace_schema_metadata(metric_arrow_schema(table_name).metadata)
    validate_metric_table(table, table_name)
    return table


def verify_metric_parquet(artifact: MotionMetricArtifact) -> None:
    """Verify exact bytes, row counts, row groups, schema, and ordering."""
    path = artifact.path
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("metric artifact is missing or not a regular file")
    if path.stat().st_size != artifact.size_bytes or _sha256(path) != artifact.sha256:
        raise ArtifactError("metric artifact bytes differ")
    table = read_metric_parquet(
        path,
        artifact.table_name,
        maximum_rows=artifact.row_count,
    )
    parquet = pq.ParquetFile(path)
    if (
        table.num_rows != artifact.row_count
        or parquet.metadata.num_row_groups != artifact.row_group_count
    ):
        raise ArtifactError("metric artifact physical counts differ")
