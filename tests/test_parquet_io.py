"""Tests for deterministic canonical Parquet construction and I/O."""

import ast
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
from pathlib import Path
import subprocess
import sys
from typing import Any

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from shapely.geometry import LineString  # type: ignore[import-untyped]

import kinematicweave.artifact_store as artifact_store_module
from kinematicweave.artifact_store import (
    WrittenArtifact,
    finalize_run_directory,
    list_partial_artifacts,
    prepare_run_directory,
)
from kinematicweave.data import parquet_io
from kinematicweave.data.parquet_io import (
    CanonicalParquetArtifact,
    CanonicalParquetDataset,
    agent_records_to_table,
    atomic_write_canonical_parquet,
    atomic_write_canonical_parquet_parts,
    coordinate_frame_records_to_table,
    iter_canonical_parquet_batches,
    read_canonical_parquet_table,
    scan_canonical_parquet,
    scenario_records_to_table,
    sort_canonical_table,
    trajectories_to_table,
    trajectory_samples_to_table,
    validate_canonical_table,
    vector_map_elements_to_table,
    verify_canonical_parquet_artifact,
)
import kinematicweave.data.schemas as schemas_module
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    get_arrow_schema,
    schema_fingerprint,
    validate_arrow_schema,
)
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    MapGeometryType,
    VectorMapElementRecord,
    geometry_to_canonical_wkb,
)
import kinematicweave.domain.records as records_module
from kinematicweave.domain.records import (
    AgentClass,
    AgentRecord,
    CoordinateFrameRecord,
    OriginType,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
    scenario_record_to_dict,
    trajectory_to_sample_dicts,
    validate_scenario_bundle,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "kinematicweave" / "data" / "parquet_io.py"
SCENARIO_ID = "scenario:parquet"
FRAME_ID = "frame:parquet"
AGENT_ID = "agent:parquet:001"
TRAJECTORY_ID = "trajectory:parquet:001"


@pytest.fixture(autouse=True)
def _refresh_symbols_after_import_safety_tests() -> None:
    """Bind tests to classes recreated by earlier module-reload checks."""
    importlib.reload(parquet_io)
    globals().update(
        {
            "AgentClass": records_module.AgentClass,
            "AgentRecord": records_module.AgentRecord,
            "CoordinateFrameRecord": records_module.CoordinateFrameRecord,
            "OriginType": records_module.OriginType,
            "ScenarioRecord": records_module.ScenarioRecord,
            "Trajectory": records_module.Trajectory,
            "TrajectorySampleRecord": records_module.TrajectorySampleRecord,
            "agent_record_to_dict": records_module.agent_record_to_dict,
            "coordinate_frame_record_to_dict": (
                records_module.coordinate_frame_record_to_dict
            ),
            "scenario_record_to_dict": records_module.scenario_record_to_dict,
            "trajectory_to_sample_dicts": (records_module.trajectory_to_sample_dicts),
            "validate_scenario_bundle": records_module.validate_scenario_bundle,
            "CanonicalSchemaName": schemas_module.CanonicalSchemaName,
            "get_arrow_schema": schemas_module.get_arrow_schema,
            "schema_fingerprint": schemas_module.schema_fingerprint,
            "validate_arrow_schema": schemas_module.validate_arrow_schema,
            "WrittenArtifact": artifact_store_module.WrittenArtifact,
            "CanonicalParquetArtifact": parquet_io.CanonicalParquetArtifact,
            "CanonicalParquetDataset": parquet_io.CanonicalParquetDataset,
            "agent_records_to_table": parquet_io.agent_records_to_table,
            "atomic_write_canonical_parquet": (
                parquet_io.atomic_write_canonical_parquet
            ),
            "atomic_write_canonical_parquet_parts": (
                parquet_io.atomic_write_canonical_parquet_parts
            ),
            "coordinate_frame_records_to_table": (
                parquet_io.coordinate_frame_records_to_table
            ),
            "iter_canonical_parquet_batches": (
                parquet_io.iter_canonical_parquet_batches
            ),
            "read_canonical_parquet_table": (parquet_io.read_canonical_parquet_table),
            "scan_canonical_parquet": parquet_io.scan_canonical_parquet,
            "scenario_records_to_table": parquet_io.scenario_records_to_table,
            "sort_canonical_table": parquet_io.sort_canonical_table,
            "trajectories_to_table": parquet_io.trajectories_to_table,
            "trajectory_samples_to_table": parquet_io.trajectory_samples_to_table,
            "validate_canonical_table": parquet_io.validate_canonical_table,
            "vector_map_elements_to_table": (parquet_io.vector_map_elements_to_table),
            "verify_canonical_parquet_artifact": (
                parquet_io.verify_canonical_parquet_artifact
            ),
        }
    )


def _scenario(**overrides: Any) -> ScenarioRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "dataset_id": "synthetic",
        "dataset_version": "1.0",
        "split_name": "smoke",
        "city_or_region": None,
        "source_scenario_id": "source-001",
        "start_time_ns": 0,
        "end_time_ns": 20,
        "coordinate_frame_id": FRAME_ID,
        "origin_x_m": 0.0,
        "origin_y_m": 0.0,
        "origin_z_m": None,
        "source_crs": None,
        "has_elevation": False,
        "agent_count": 1,
        "source_map_available": False,
        "quality_flags": (),
        "adapter_name": "test_adapter",
        "adapter_version": "1.0",
        "source_checksum": None,
    }
    values.update(overrides)
    return ScenarioRecord(**values)


def _frame(**overrides: Any) -> CoordinateFrameRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "coordinate_frame_id": FRAME_ID,
        "parent_frame_id": None,
        "frame_type": "local_cartesian",
        "origin_x_m": 0.0,
        "origin_y_m": 0.0,
        "origin_z_m": None,
        "axis_convention": "x_forward_y_left_z_up",
        "distance_unit": "m",
        "angle_unit": "rad",
        "timestamp_unit": "ns",
        "source_crs": None,
        "has_elevation": False,
        "transform_to_parent_4x4": None,
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return CoordinateFrameRecord(**values)


def _agent(**overrides: Any) -> AgentRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "agent_id": AGENT_ID,
        "source_agent_id": None,
        "agent_class": AgentClass.VEHICLE,
        "length_m": 4.5,
        "width_m": 1.8,
        "height_m": None,
        "first_time_ns": 0,
        "last_time_ns": 20,
        "sample_count": 5,
        "is_focal_agent": True,
        "is_ego_agent": False,
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return AgentRecord(**values)


def _sample(index: int = 0, **overrides: Any) -> TrajectorySampleRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "agent_id": AGENT_ID,
        "trajectory_id": TRAJECTORY_ID,
        "sample_index": index,
        "timestamp_ns": index * 5,
        "x_m": float(index),
        "y_m": float(index * 2),
        "z_m": None,
        "heading_rad": None,
        "velocity_x_mps": None,
        "velocity_y_mps": None,
        "speed_mps": None,
        "acceleration_x_mps2": None,
        "acceleration_y_mps2": None,
        "is_observed": True,
        "is_valid": True,
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return TrajectorySampleRecord(**values)


def _trajectory(count: int = 5, **overrides: Any) -> Trajectory:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "agent_id": AGENT_ID,
        "trajectory_id": TRAJECTORY_ID,
        "samples": tuple(_sample(index) for index in range(count)),
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return Trajectory(**values)


def _map_element(
    identifier: str = "map:test:lane:1",
    **overrides: Any,
) -> VectorMapElementRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "map_element_id": identifier,
        "element_type": MapElementType.LANE_CENTERLINE,
        "geometry_type": MapGeometryType.LINESTRING,
        "geometry_wkb": geometry_to_canonical_wkb(LineString([(0.0, 0.0), (1.0, 1.0)])),
        "directionality": Directionality.DIRECTED,
        "parent_element_id": None,
        "successor_ids": (),
        "predecessor_ids": (),
        "left_neighbor_id": None,
        "right_neighbor_id": None,
        "semantic_attributes_json": None,
        "origin_type": "synthetic",
        "quality_flags": (),
    }
    values.update(overrides)
    return VectorMapElementRecord(**values)


def _prepared(tmp_path: Path, run_id: str = "run:parquet") -> Any:
    repository = tmp_path / "repository"
    repository.mkdir(exist_ok=True)
    return prepare_run_directory(
        repository.resolve(),
        "results",
        run_id,
        reserve_fraction=0,
    )


def _fake_artifact(
    *,
    schema_name: CanonicalSchemaName | None = None,
    path: str = "results/part.parquet",
    size_bytes: int = 10,
    row_count: int = 2,
    row_group_count: int = 1,
) -> CanonicalParquetArtifact:
    if schema_name is None:
        schema_name = CanonicalSchemaName.TRAJECTORY_SAMPLES
    return CanonicalParquetArtifact(
        schema_name=schema_name,
        schema_version="1.0",
        schema_fingerprint=schema_fingerprint(schema_name),
        written_artifact=WrittenArtifact(
            Path(path),
            size_bytes,
            "a" * 64,
        ),
        row_count=row_count,
        row_group_count=row_group_count,
    )


def _write_samples(
    tmp_path: Path,
    *,
    count: int = 5,
    run_id: str = "run:reader",
) -> tuple[Any, pa.Table, CanonicalParquetArtifact]:
    run_directory = _prepared(tmp_path, run_id)
    table = trajectory_samples_to_table([_sample(index) for index in range(count)])
    artifact = atomic_write_canonical_parquet(
        run_directory,
        "artifacts/samples.parquet",
        table,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        row_group_size=2,
    )
    return run_directory, table, artifact


def test_module_all_is_exact() -> None:
    assert parquet_io.__all__ == [
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


def test_artifact_dataclasses_are_frozen_slotted_and_copy_parts() -> None:
    first = _fake_artifact()
    second = _fake_artifact(
        path="results/part-2.parquet",
        row_count=3,
    )
    source_parts = [first, second]
    dataset = CanonicalParquetDataset(
        schema_name=CanonicalSchemaName.TRAJECTORY_SAMPLES,
        schema_version="1.0",
        schema_fingerprint=schema_fingerprint(CanonicalSchemaName.TRAJECTORY_SAMPLES),
        row_count=5,
        parts=source_parts,  # type: ignore[arg-type]
    )
    source_parts.clear()

    assert dataset.parts == (first, second)
    assert not hasattr(first, "__dict__")
    assert not hasattr(dataset, "__dict__")
    first_value: Any = first
    dataset_value: Any = dataset
    with pytest.raises(FrozenInstanceError):
        first_value.row_count = 9
    with pytest.raises(FrozenInstanceError):
        dataset_value.row_count = 9


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_name": "trajectory_samples"},
        {"schema_version": "2.0"},
        {"schema_fingerprint": "A" * 64},
        {"written_artifact": object()},
        {"row_count": True},
        {"row_count": -1},
        {"row_group_count": True},
        {"row_group_count": -1},
        {"row_group_count": 0},
    ],
)
def test_artifact_rejects_invalid_fields(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "schema_name": CanonicalSchemaName.TRAJECTORY_SAMPLES,
        "schema_version": "1.0",
        "schema_fingerprint": schema_fingerprint(
            CanonicalSchemaName.TRAJECTORY_SAMPLES
        ),
        "written_artifact": WrittenArtifact(Path("results/a"), 10, "a" * 64),
        "row_count": 1,
        "row_group_count": 1,
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        CanonicalParquetArtifact(**values)  # type: ignore[arg-type]


def test_zero_size_artifact_may_have_zero_row_groups() -> None:
    artifact = _fake_artifact(size_bytes=0, row_count=0, row_group_count=0)
    assert artifact.row_group_count == 0


def test_dataset_rejects_part_inconsistency_counts_and_duplicates() -> None:
    part = _fake_artifact()
    fingerprint = schema_fingerprint(CanonicalSchemaName.TRAJECTORY_SAMPLES)

    with pytest.raises(ValidationError, match="at least one"):
        CanonicalParquetDataset(
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            "1.0",
            fingerprint,
            0,
            (),
        )
    with pytest.raises(ValidationError, match="sum"):
        CanonicalParquetDataset(
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            "1.0",
            fingerprint,
            3,
            (part,),
        )
    with pytest.raises(ValidationError, match="unique"):
        CanonicalParquetDataset(
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            "1.0",
            fingerprint,
            4,
            (part, part),
        )
    wrong = _fake_artifact(schema_name=CanonicalSchemaName.AGENT_METADATA)
    with pytest.raises(ValidationError, match="schema identity"):
        CanonicalParquetDataset(
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            "1.0",
            fingerprint,
            wrong.row_count,
            (wrong,),
        )


@pytest.mark.parametrize(
    "case",
    [
        "scenario",
        "coordinate_frame",
        "agent",
        "trajectory_sample",
    ],
)
def test_record_converters_use_exact_schema_and_preserve_nulls(
    case: str,
) -> None:
    cases: dict[str, tuple[Any, object, CanonicalSchemaName]] = {
        "scenario": (
            scenario_records_to_table,
            _scenario(),
            CanonicalSchemaName.SCENARIO_MANIFEST,
        ),
        "coordinate_frame": (
            coordinate_frame_records_to_table,
            _frame(),
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        ),
        "agent": (
            agent_records_to_table,
            _agent(),
            CanonicalSchemaName.AGENT_METADATA,
        ),
        "trajectory_sample": (
            trajectory_samples_to_table,
            _sample(),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        ),
    }
    converter, record, expected = cases[case]
    records = [record]
    before = list(records)
    table = converter(records)
    validate_canonical_table(table, expected)
    assert table.schema.equals(get_arrow_schema(expected), check_metadata=True)
    assert records == before
    assert any(value is None for value in table.to_pylist()[0].values())


def test_trajectories_to_table_flattens_then_sorts_without_mutation() -> None:
    first = _trajectory(
        count=2,
        scenario_id="scenario:z",
        agent_id="agent:z",
        trajectory_id="trajectory:z",
        samples=(
            _sample(
                0,
                scenario_id="scenario:z",
                agent_id="agent:z",
                trajectory_id="trajectory:z",
            ),
            _sample(
                1,
                scenario_id="scenario:z",
                agent_id="agent:z",
                trajectory_id="trajectory:z",
            ),
        ),
    )
    second = _trajectory(
        count=1,
        scenario_id="scenario:a",
        agent_id="agent:a",
        trajectory_id="trajectory:a",
        samples=(
            _sample(
                0,
                scenario_id="scenario:a",
                agent_id="agent:a",
                trajectory_id="trajectory:a",
            ),
        ),
    )
    source = [first, second]
    before = list(source)
    table = trajectories_to_table(source)

    assert source == before
    assert table.column("scenario_id").to_pylist() == [
        "scenario:a",
        "scenario:z",
        "scenario:z",
    ]
    validate_canonical_table(table, CanonicalSchemaName.TRAJECTORY_SAMPLES)


@pytest.mark.parametrize(
    ("converter", "expected"),
    [
        (scenario_records_to_table, CanonicalSchemaName.SCENARIO_MANIFEST),
        (
            coordinate_frame_records_to_table,
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        ),
        (agent_records_to_table, CanonicalSchemaName.AGENT_METADATA),
        (
            trajectory_samples_to_table,
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        ),
        (trajectories_to_table, CanonicalSchemaName.TRAJECTORY_SAMPLES),
    ],
)
def test_record_converters_return_exact_empty_tables(
    converter: Any,
    expected: CanonicalSchemaName,
) -> None:
    table = converter([])
    assert table.num_rows == 0
    assert table.schema.equals(get_arrow_schema(expected), check_metadata=True)
    validate_canonical_table(table, expected)


def test_record_conversion_translates_arrow_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_row(record: ScenarioRecord) -> dict[str, object]:
        row = scenario_record_to_dict(record)
        row["start_time_ns"] = "not-an-integer"
        return row

    monkeypatch.setattr(parquet_io, "scenario_record_to_dict", invalid_row)
    with pytest.raises(SchemaError, match="record conversion failed"):
        scenario_records_to_table([_scenario()])


def test_table_validation_sorting_and_idempotence() -> None:
    table = trajectory_samples_to_table([_sample(1), _sample(0)])
    validate_canonical_table(table, CanonicalSchemaName.TRAJECTORY_SAMPLES)
    unsorted = table.take(pa.array([1, 0]))
    with pytest.raises(SchemaError, match="canonical order"):
        validate_canonical_table(
            unsorted,
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )

    first = sort_canonical_table(
        unsorted,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    second = sort_canonical_table(
        first,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    assert first.equals(second, check_metadata=True)
    assert first is not table


def test_table_validation_accepts_empty_and_single_row() -> None:
    for records in ([], [_sample()]):
        table = trajectory_samples_to_table(records)
        validate_canonical_table(table, CanonicalSchemaName.TRAJECTORY_SAMPLES)


def test_table_validation_rejects_null_in_nonnullable_field() -> None:
    row = trajectory_to_sample_dicts(_trajectory(count=1))[0]
    row["scenario_id"] = None
    table = pa.Table.from_pylist(
        [row],
        schema=get_arrow_schema(CanonicalSchemaName.TRAJECTORY_SAMPLES),
    )
    with pytest.raises(SchemaError, match="contains nulls"):
        validate_canonical_table(
            table,
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )


def test_table_validation_rejects_changed_schema_metadata_and_nullability() -> None:
    table = trajectory_samples_to_table([_sample()])
    expected = CanonicalSchemaName.TRAJECTORY_SAMPLES

    with pytest.raises(SchemaError):
        validate_canonical_table(table.select(reversed(table.schema.names)), expected)

    changed_type = table.set_column(
        table.schema.get_field_index("timestamp_ns"),
        pa.field("timestamp_ns", pa.float64(), nullable=False),
        pa.array([0.0]),
    )
    with pytest.raises(SchemaError, match="type differs"):
        validate_canonical_table(changed_type, expected)

    fields = list(table.schema)
    fields[0] = pa.field(fields[0].name, fields[0].type, nullable=True)
    changed_nullability = pa.Table.from_arrays(
        table.columns,
        schema=pa.schema(fields, metadata=table.schema.metadata),
    )
    with pytest.raises(SchemaError, match="nullability"):
        validate_canonical_table(changed_nullability, expected)

    changed_metadata = table.replace_schema_metadata({})
    with pytest.raises(SchemaError, match="metadata"):
        validate_canonical_table(changed_metadata, expected)


def test_table_validation_rejects_non_table_and_non_bool_sort_flag() -> None:
    with pytest.raises(SchemaError, match="pyarrow"):
        validate_canonical_table(
            object(),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )
    with pytest.raises(SchemaError, match="Boolean"):
        validate_canonical_table(
            trajectory_samples_to_table([]),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            require_sorted=1,  # type: ignore[arg-type]
        )


def test_single_file_write_records_exact_physical_metadata(tmp_path: Path) -> None:
    run_directory, table, artifact = _write_samples(tmp_path)
    path = run_directory.repository_root / artifact.written_artifact.relative_path

    assert artifact.row_count == 5
    assert artifact.row_group_count == 3
    assert artifact.schema_version == "1.0"
    assert artifact.schema_fingerprint == schema_fingerprint(
        CanonicalSchemaName.TRAJECTORY_SAMPLES
    )
    assert artifact.written_artifact.size_bytes == path.stat().st_size
    assert (
        artifact.written_artifact.content_checksum
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )

    with path.open("rb") as stream:
        parquet_file = pq.ParquetFile(stream)
        validate_arrow_schema(
            parquet_file.schema_arrow,
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )
        column = parquet_file.metadata.row_group(0).column(0)
        assert column.compression == "ZSTD"
        assert "RLE_DICTIONARY" not in column.encodings
        assert "PLAIN_DICTIONARY" not in column.encodings
        assert column.statistics is not None
        assert parquet_file.metadata.format_version == "2.6"
    assert read_canonical_parquet_table(
        run_directory.repository_root,
        [artifact.written_artifact.relative_path],
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    ).equals(table, check_metadata=True)


def test_single_file_write_sorts_input_and_is_deterministic(tmp_path: Path) -> None:
    table = trajectory_samples_to_table([_sample(0), _sample(1)])
    unsorted = table.take(pa.array([1, 0]))
    first_run = _prepared(tmp_path, "run:deterministic:first")
    second_run = _prepared(tmp_path, "run:deterministic:second")
    first = atomic_write_canonical_parquet(
        first_run,
        "artifacts/table.parquet",
        unsorted,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    second = atomic_write_canonical_parquet(
        second_run,
        "artifacts/table.parquet",
        table,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    assert first.written_artifact.content_checksum == (
        second.written_artifact.content_checksum
    )
    assert first.written_artifact.size_bytes == second.written_artifact.size_bytes


def test_single_file_write_rejects_overwrite_and_complete_run(
    tmp_path: Path,
) -> None:
    run_directory, table, artifact = _write_samples(tmp_path)
    relative_path = artifact.written_artifact.relative_path.relative_to(
        run_directory.path.relative_to(run_directory.repository_root)
    )
    with pytest.raises(ArtifactError, match="already exists"):
        atomic_write_canonical_parquet(
            run_directory,
            relative_path,
            table,
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )

    finalize_run_directory(run_directory)
    with pytest.raises(ArtifactError, match="immutable"):
        atomic_write_canonical_parquet(
            run_directory,
            "artifacts/late.parquet",
            table,
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )


@pytest.mark.parametrize("row_group_size", [0, -1, True, 1.5])
def test_single_file_write_rejects_invalid_row_group_size(
    tmp_path: Path,
    row_group_size: object,
) -> None:
    run_directory = _prepared(tmp_path)
    with pytest.raises(ValidationError, match="row_group_size"):
        atomic_write_canonical_parquet(
            run_directory,
            "artifacts/value.parquet",
            trajectory_samples_to_table([]),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            row_group_size=row_group_size,  # type: ignore[arg-type]
        )


def test_single_file_write_translates_corrupt_writer_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = _prepared(tmp_path)

    def corrupt_write(
        table: pa.Table,
        where: Path,
        **kwargs: object,
    ) -> None:
        where.write_bytes(b"not parquet")

    parquet_module: Any = parquet_io
    monkeypatch.setattr(parquet_module.pq, "write_table", corrupt_write)
    with pytest.raises(SchemaError, match="invalid canonical Parquet"):
        atomic_write_canonical_parquet(
            run_directory,
            "artifacts/corrupt.parquet",
            trajectory_samples_to_table([]),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )


def test_partitioned_write_uses_deterministic_names_and_row_limits(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)
    table = trajectory_samples_to_table([_sample(index) for index in range(7)])
    dataset = atomic_write_canonical_parquet_parts(
        run_directory,
        "artifacts/samples",
        table.to_batches(),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        max_rows_per_part=3,
        row_group_size=2,
    )

    assert dataset.row_count == 7
    assert [part.row_count for part in dataset.parts] == [3, 3, 1]
    assert [part.written_artifact.relative_path.name for part in dataset.parts] == [
        "part-00000.parquet",
        "part-00001.parquet",
        "part-00002.parquet",
    ]
    assert not any(run_directory.manifests_path.iterdir())


def test_partitioned_write_handles_multiple_batches_and_missing_metadata(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)
    table = trajectory_samples_to_table([_sample(index) for index in range(5)])
    batches = list(table.to_batches(max_chunksize=2))
    batches[0] = batches[0].replace_schema_metadata(None)
    dataset = atomic_write_canonical_parquet_parts(
        run_directory,
        "artifacts/samples",
        batches,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        max_rows_per_part=4,
    )
    round_trip = read_canonical_parquet_table(
        run_directory.repository_root,
        [part.written_artifact.relative_path for part in dataset.parts],
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    assert round_trip.equals(table, check_metadata=True)


def test_partitioned_write_rejects_unsorted_and_out_of_order_batches(
    tmp_path: Path,
) -> None:
    table = trajectory_samples_to_table([_sample(0), _sample(1)])
    unsorted = table.take(pa.array([1, 0])).to_batches()[0]
    with pytest.raises(SchemaError, match="canonical order"):
        atomic_write_canonical_parquet_parts(
            _prepared(tmp_path, "run:unsorted"),
            "artifacts/samples",
            [unsorted],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )

    first = table.slice(1, 1).to_batches()[0]
    second = table.slice(0, 1).to_batches()[0]
    with pytest.raises(SchemaError, match="global canonical order"):
        atomic_write_canonical_parquet_parts(
            _prepared(tmp_path, "run:boundary"),
            "artifacts/samples",
            [first, second],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )


def test_partitioned_write_rejects_wrong_batch_schema_and_limits(
    tmp_path: Path,
) -> None:
    scenario_batch = scenario_records_to_table([_scenario()]).to_batches()[0]
    with pytest.raises(SchemaError, match="field count"):
        atomic_write_canonical_parquet_parts(
            _prepared(tmp_path, "run:wrong-schema"),
            "artifacts/samples",
            [scenario_batch],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )
    for field_name, kwargs in (
        ("max_rows_per_part", {"max_rows_per_part": 0}),
        ("row_group_size", {"row_group_size": True}),
    ):
        with pytest.raises(ValidationError, match=field_name):
            atomic_write_canonical_parquet_parts(
                _prepared(tmp_path, f"run:{field_name}"),
                "artifacts/samples",
                [],
                CanonicalSchemaName.TRAJECTORY_SAMPLES,
                **kwargs,
            )


def test_partitioned_empty_iterable_writes_one_empty_part(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    dataset = atomic_write_canonical_parquet_parts(
        run_directory,
        "artifacts/samples",
        [],
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    assert dataset.row_count == 0
    assert len(dataset.parts) == 1
    assert dataset.parts[0].row_count == 0
    assert dataset.parts[0].written_artifact.relative_path.name == (
        "part-00000.parquet"
    )


def test_partitioned_iterable_is_consumed_once(tmp_path: Path) -> None:
    table = trajectory_samples_to_table([_sample()])

    class SingleUse:
        calls = 0

        def __iter__(self) -> Any:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("iterable consumed more than once")
            yield from table.to_batches()

    source = SingleUse()
    atomic_write_canonical_parquet_parts(
        _prepared(tmp_path),
        "artifacts/samples",
        source,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    assert source.calls == 1


def test_partitioned_later_failure_preserves_completed_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = _prepared(tmp_path)
    table = trajectory_samples_to_table([_sample(index) for index in range(3)])
    original = parquet_io.atomic_write_canonical_parquet
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> CanonicalParquetArtifact:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated later-part failure")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(parquet_io, "atomic_write_canonical_parquet", fail_second)
    with pytest.raises(OSError, match="later-part"):
        atomic_write_canonical_parquet_parts(
            run_directory,
            "artifacts/samples",
            table.to_batches(),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            max_rows_per_part=2,
        )
    first_path = run_directory.artifacts_path / "samples" / "part-00000.parquet"
    assert first_path.is_file()

    with pytest.raises(ArtifactError, match="already exists"):
        atomic_write_canonical_parquet_parts(
            run_directory,
            "artifacts/samples",
            table.to_batches(),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            max_rows_per_part=2,
        )


@pytest.mark.parametrize(
    "relative_directory",
    ["../outside", ".temporary", "artifacts/.temporary/nested"],
)
def test_partitioned_write_rejects_unsafe_directories(
    tmp_path: Path,
    relative_directory: str,
) -> None:
    with pytest.raises(ArtifactError):
        atomic_write_canonical_parquet_parts(
            _prepared(tmp_path),
            relative_directory,
            [],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )


def test_bounded_reader_round_trip_and_batch_sizes(tmp_path: Path) -> None:
    run_directory, table, artifact = _write_samples(tmp_path)
    batches = tuple(
        iter_canonical_parquet_batches(
            run_directory.repository_root,
            [artifact.written_artifact.relative_path],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            batch_size=2,
        )
    )
    assert [batch.num_rows for batch in batches] == [2, 2, 1]
    assert pa.Table.from_batches(
        batches,
        schema=get_arrow_schema(CanonicalSchemaName.TRAJECTORY_SAMPLES),
    ).equals(table, check_metadata=True)
    assert read_canonical_parquet_table(
        run_directory.repository_root,
        [artifact.written_artifact.relative_path],
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    ).equals(table, check_metadata=True)


def test_reader_handles_empty_canonical_file(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    artifact = atomic_write_canonical_parquet(
        run_directory,
        "artifacts/empty.parquet",
        trajectory_samples_to_table([]),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    batches = tuple(
        iter_canonical_parquet_batches(
            run_directory.repository_root,
            [artifact.written_artifact.relative_path],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )
    )
    assert batches == ()
    table = read_canonical_parquet_table(
        run_directory.repository_root,
        [artifact.written_artifact.relative_path],
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    assert table.num_rows == 0
    assert table.schema.equals(
        get_arrow_schema(CanonicalSchemaName.TRAJECTORY_SAMPLES),
        check_metadata=True,
    )


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_reader_rejects_invalid_batch_size(
    tmp_path: Path,
    batch_size: object,
) -> None:
    repository = tmp_path.resolve()
    with pytest.raises(ValidationError, match="batch_size"):
        iter_canonical_parquet_batches(
            repository,
            ["missing.parquet"],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            batch_size=batch_size,  # type: ignore[arg-type]
        )


def test_reader_rejects_missing_duplicate_outside_and_directory_paths(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "directory").mkdir()
    for paths, message in (
        (["missing.parquet"], "does not exist"),
        (["directory"], "not a file"),
        (["../outside.parquet"], "parent traversal"),
        (["same.parquet", "same.parquet"], "unique"),
        ([], "at least one"),
    ):
        with pytest.raises(ArtifactError, match=message):
            iter_canonical_parquet_batches(
                repository,
                paths,
                CanonicalSchemaName.TRAJECTORY_SAMPLES,
            )


def test_reader_rejects_wrong_schema_and_truncated_file(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    scenario_artifact = atomic_write_canonical_parquet(
        run_directory,
        "artifacts/scenarios.parquet",
        scenario_records_to_table([_scenario()]),
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )
    with pytest.raises(SchemaError):
        tuple(
            iter_canonical_parquet_batches(
                run_directory.repository_root,
                [scenario_artifact.written_artifact.relative_path],
                CanonicalSchemaName.TRAJECTORY_SAMPLES,
            )
        )

    truncated = run_directory.artifacts_path / "truncated.parquet"
    truncated.write_bytes(b"PAR1truncated")
    relative = truncated.relative_to(run_directory.repository_root)
    with pytest.raises(SchemaError, match="invalid canonical Parquet"):
        tuple(
            iter_canonical_parquet_batches(
                run_directory.repository_root,
                [relative],
                CanonicalSchemaName.TRAJECTORY_SAMPLES,
            )
        )


def test_reader_rejects_global_order_failure_across_files(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    table = trajectory_samples_to_table([_sample(0), _sample(1)])
    later = atomic_write_canonical_parquet(
        run_directory,
        "artifacts/later.parquet",
        table.slice(1, 1),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    earlier = atomic_write_canonical_parquet(
        run_directory,
        "artifacts/earlier.parquet",
        table.slice(0, 1),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    with pytest.raises(SchemaError, match="global canonical order"):
        tuple(
            iter_canonical_parquet_batches(
                run_directory.repository_root,
                [
                    later.written_artifact.relative_path,
                    earlier.written_artifact.relative_path,
                ],
                CanonicalSchemaName.TRAJECTORY_SAMPLES,
            )
        )


def test_reader_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"outside")
    link = repository / "linked.parquet"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    with pytest.raises(ArtifactError, match="outside"):
        iter_canonical_parquet_batches(
            repository,
            ["linked.parquet"],
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )


def test_polars_scan_is_lazy_fresh_ordered_and_writes_nothing(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)
    table = trajectory_samples_to_table([_sample(index) for index in range(4)])
    dataset = atomic_write_canonical_parquet_parts(
        run_directory,
        "artifacts/samples",
        table.to_batches(),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        max_rows_per_part=2,
    )
    paths = [part.written_artifact.relative_path for part in dataset.parts]
    before = tuple(
        sorted(
            path.relative_to(run_directory.repository_root)
            for path in run_directory.path.rglob("*")
        )
    )
    first = scan_canonical_parquet(
        run_directory.repository_root,
        paths,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    second = scan_canonical_parquet(
        run_directory.repository_root,
        paths,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )

    assert isinstance(first, pl.LazyFrame)
    assert first is not second
    assert first.collect_schema().names() == table.schema.names
    assert first.collect().to_dicts() == table.to_pylist()
    after = tuple(
        sorted(
            path.relative_to(run_directory.repository_root)
            for path in run_directory.path.rglob("*")
        )
    )
    assert after == before


def test_verify_valid_artifact_and_metadata_mismatches(tmp_path: Path) -> None:
    run_directory, _, artifact = _write_samples(tmp_path)
    verify_canonical_parquet_artifact(run_directory.repository_root, artifact)

    with pytest.raises(ArtifactError, match="size differs"):
        verify_canonical_parquet_artifact(
            run_directory.repository_root,
            replace(
                artifact,
                written_artifact=replace(
                    artifact.written_artifact,
                    size_bytes=artifact.written_artifact.size_bytes + 1,
                ),
            ),
        )
    with pytest.raises(ArtifactError, match="checksum"):
        verify_canonical_parquet_artifact(
            run_directory.repository_root,
            replace(
                artifact,
                written_artifact=replace(
                    artifact.written_artifact,
                    content_checksum="b" * 64,
                ),
            ),
        )
    with pytest.raises(SchemaError, match="fingerprint"):
        verify_canonical_parquet_artifact(
            run_directory.repository_root,
            replace(artifact, schema_fingerprint="b" * 64),
        )
    with pytest.raises(SchemaError, match="row count"):
        verify_canonical_parquet_artifact(
            run_directory.repository_root,
            replace(artifact, row_count=artifact.row_count + 1),
        )
    with pytest.raises(SchemaError, match="row-group"):
        verify_canonical_parquet_artifact(
            run_directory.repository_root,
            replace(artifact, row_group_count=artifact.row_group_count + 1),
        )


def test_verify_rejects_wrong_schema_and_missing_file(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    scenario_artifact = atomic_write_canonical_parquet(
        run_directory,
        "artifacts/scenario.parquet",
        scenario_records_to_table([_scenario()]),
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )
    wrong_schema = CanonicalParquetArtifact(
        schema_name=CanonicalSchemaName.AGENT_METADATA,
        schema_version="1.0",
        schema_fingerprint=schema_fingerprint(CanonicalSchemaName.AGENT_METADATA),
        written_artifact=scenario_artifact.written_artifact,
        row_count=scenario_artifact.row_count,
        row_group_count=scenario_artifact.row_group_count,
    )
    with pytest.raises(SchemaError):
        verify_canonical_parquet_artifact(
            run_directory.repository_root,
            wrong_schema,
        )

    path = (
        run_directory.repository_root / scenario_artifact.written_artifact.relative_path
    )
    path.unlink()
    with pytest.raises(ArtifactError, match="does not exist"):
        verify_canonical_parquet_artifact(
            run_directory.repository_root,
            scenario_artifact,
        )


def test_imports_only_approved_library_families() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert not imported_roots.intersection(
        {
            "argoverse",
            "av2",
            "geopandas",
            "networkx",
            "rerun",
            "shapely",
            "torch",
        }
    )


def test_import_performs_no_writes_or_subprocess_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbidden_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("parquet_io import attempted a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden_call)
    monkeypatch.setattr(subprocess, "Popen", forbidden_call)
    before = tuple(tmp_path.iterdir())
    monkeypatch.chdir(tmp_path)
    importlib.reload(parquet_io)
    assert tuple(tmp_path.iterdir()) == before


def test_isolated_import_loads_no_forbidden_library() -> None:
    code = (
        "import sys; import kinematicweave.data.parquet_io; "
        "forbidden=('argoverse','av2','geopandas','networkx','rerun',"
        "'torch'); "
        "print(','.join(name for name in forbidden if name in sys.modules))"
    )
    completed = subprocess.run(
        (sys.executable, "-c", code),
        check=True,
        shell=False,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == "\n"
    assert completed.stderr == ""


def test_complete_canonical_parquet_integration(tmp_path: Path) -> None:
    repository = tmp_path / "integration-repository"
    repository.mkdir()
    run_directory = prepare_run_directory(
        repository.resolve(),
        "results",
        "run:parquet:integration",
        reserve_fraction=0,
    )
    scenario = _scenario()
    frame = _frame()
    agent = _agent()
    trajectory = _trajectory()
    validate_scenario_bundle(scenario, frame, [agent], [trajectory])

    scenario_table = scenario_records_to_table([scenario])
    frame_table = coordinate_frame_records_to_table([frame])
    agent_table = agent_records_to_table([agent])
    sample_table = trajectories_to_table([trajectory])
    single_tables = (
        (
            "artifacts/scenarios.parquet",
            scenario_table,
            CanonicalSchemaName.SCENARIO_MANIFEST,
            scenario_table.to_pylist(),
        ),
        (
            "artifacts/frames.parquet",
            frame_table,
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            frame_table.to_pylist(),
        ),
        (
            "artifacts/agents.parquet",
            agent_table,
            CanonicalSchemaName.AGENT_METADATA,
            agent_table.to_pylist(),
        ),
    )
    artifacts: list[CanonicalParquetArtifact] = []
    for relative_path, table, schema_name, expected_rows in single_tables:
        artifact = atomic_write_canonical_parquet(
            run_directory,
            relative_path,
            table,
            schema_name,
        )
        artifacts.append(artifact)
        verify_canonical_parquet_artifact(repository, artifact)
        batches = tuple(
            iter_canonical_parquet_batches(
                repository,
                [artifact.written_artifact.relative_path],
                schema_name,
                batch_size=1,
            )
        )
        assert (
            pa.Table.from_batches(
                batches,
                schema=get_arrow_schema(schema_name),
            ).to_pylist()
            == expected_rows
        )
        assert (
            scan_canonical_parquet(
                repository,
                [artifact.written_artifact.relative_path],
                schema_name,
            )
            .collect()
            .to_dicts()
            == expected_rows
        )

    dataset = atomic_write_canonical_parquet_parts(
        run_directory,
        "artifacts/trajectory-samples",
        sample_table.to_batches(max_chunksize=2),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        max_rows_per_part=3,
        row_group_size=2,
    )
    for part in dataset.parts:
        verify_canonical_parquet_artifact(repository, part)
    part_paths = [part.written_artifact.relative_path for part in dataset.parts]
    assert read_canonical_parquet_table(
        repository,
        part_paths,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    ).to_pylist() == list(trajectory_to_sample_dicts(trajectory))
    assert scan_canonical_parquet(
        repository,
        part_paths,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    ).collect().to_dicts() == list(trajectory_to_sample_dicts(trajectory))

    assert list_partial_artifacts(run_directory) == ()
    finalize_run_directory(run_directory)
    assert list_partial_artifacts(run_directory) == ()
    with pytest.raises(ArtifactError, match="immutable"):
        atomic_write_canonical_parquet(
            run_directory,
            "artifacts/late.parquet",
            scenario_table,
            CanonicalSchemaName.SCENARIO_MANIFEST,
        )
    assert all(
        artifact.written_artifact.relative_path.is_relative_to(Path("results"))
        for artifact in artifacts
    )


def test_vector_map_table_and_parquet_integration(tmp_path: Path) -> None:
    first = _map_element("map:test:lane:2", semantic_attributes_json=None)
    second = _map_element("map:test:lane:1", parent_element_id=None)
    table = vector_map_elements_to_table([first, second])
    assert table.schema == get_arrow_schema(CanonicalSchemaName.VECTOR_MAP_ELEMENTS)
    assert table.column("map_element_id").to_pylist() == [
        "map:test:lane:1",
        "map:test:lane:2",
    ]
    assert table.column("geometry_wkb").to_pylist() == [
        second.geometry_wkb,
        first.geometry_wkb,
    ]
    assert table.column("parent_element_id").null_count == 2
    empty = vector_map_elements_to_table([])
    assert empty.num_rows == 0
    assert empty.schema == get_arrow_schema(CanonicalSchemaName.VECTOR_MAP_ELEMENTS)

    repository = tmp_path / "map-repository"
    repository.mkdir()
    run = prepare_run_directory(
        repository.resolve(),
        "results",
        "run:vector-map",
        reserve_fraction=0,
    )
    artifact = atomic_write_canonical_parquet(
        run,
        "artifacts/vector-map.parquet",
        table,
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        row_group_size=1,
    )
    verify_canonical_parquet_artifact(repository, artifact)
    batches = tuple(
        iter_canonical_parquet_batches(
            repository,
            (artifact.written_artifact.relative_path,),
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
            batch_size=1,
        )
    )
    assert sum(batch.num_rows for batch in batches) == 2
    assert (
        scan_canonical_parquet(
            repository,
            (artifact.written_artifact.relative_path,),
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        )
        .collect()
        .to_dicts()
        == table.to_pylist()
    )

    dataset = atomic_write_canonical_parquet_parts(
        run,
        "artifacts/vector-map-parts",
        table.to_batches(max_chunksize=1),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        max_rows_per_part=1,
        row_group_size=1,
    )
    assert dataset.row_count == 2
    assert len(dataset.parts) == 2
    for part in dataset.parts:
        verify_canonical_parquet_artifact(repository, part)


def test_vector_map_converter_and_schema_reject_invalid_values() -> None:
    with pytest.raises(SchemaError, match="VectorMapElementRecord"):
        vector_map_elements_to_table([object()])  # type: ignore[list-item]
    schema = get_arrow_schema(CanonicalSchemaName.VECTOR_MAP_ELEMENTS)
    index = schema.get_field_index("geometry_wkb")
    fields = list(schema)
    fields[index] = pa.field("geometry_wkb", pa.string(), nullable=False)
    wrong = pa.schema(fields, metadata=schema.metadata)
    table = pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in wrong],
        schema=wrong,
    )
    with pytest.raises(SchemaError, match=r"geometry_wkb.*type differs"):
        validate_canonical_table(
            table,
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        )
