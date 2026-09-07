"""Tests for the strict direct-Parquet AV2 motion adapter."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from kinematicweave import artifact_store
from kinematicweave.artifact_store import WrittenArtifact
from kinematicweave.data import av2_motion
from kinematicweave.data.parquet_io import (
    iter_canonical_parquet_batches,
    scan_canonical_parquet,
)
from kinematicweave.data.registry import (
    discover_dataset_source,
    get_dataset_registry_entry,
    verify_dataset_source,
)
from kinematicweave.data.schemas import CanonicalSchemaName, get_arrow_schema
from kinematicweave.domain.records import AgentClass, validate_scenario_bundle
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "av2_motion" / "scenario_fixture.json"
)
REQUIRED_COLUMNS = (
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
SCENARIO_COLUMNS = (
    "scenario_id",
    "start_timestamp",
    "end_timestamp",
    "num_timestamps",
    "focal_track_id",
    "city",
)
SUMMARY_FIELDS = (
    "schema_version",
    "adapter_name",
    "adapter_version",
    "dataset_id",
    "dataset_version",
    "split_name",
    "source_relative_path",
    "source_checksum",
    "source_scenario_id",
    "source_focal_track_id",
    "source_city_name",
    "source_track_count",
    "source_state_count",
    "included_track_count",
    "excluded_track_count",
    "object_type_counts",
    "category_counts",
    "canonical_scenario_id",
    "canonical_coordinate_frame_id",
    "canonical_agent_ids",
    "canonical_trajectory_ids",
    "track_summaries",
)


def _payload() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    )


def _rows(payload: dict[str, object]) -> list[dict[str, object]]:
    source_rows = cast(list[dict[str, object]], payload["rows"])
    return [
        {
            **row,
            **{name: payload[name] for name in SCENARIO_COLUMNS},
        }
        for row in source_rows
    ]


def _source_schema(
    *,
    replacements: dict[str, pa.DataType] | None = None,
    omitted: str | None = None,
    extras: tuple[pa.Field, ...] = (),
) -> pa.Schema:
    types: dict[str, pa.DataType] = {
        "observed": pa.bool_(),
        "track_id": pa.string(),
        "object_type": pa.string(),
        "object_category": pa.int64(),
        "timestep": pa.int64(),
        "position_x": pa.float64(),
        "position_y": pa.float64(),
        "heading": pa.float64(),
        "velocity_x": pa.float64(),
        "velocity_y": pa.float64(),
        "scenario_id": pa.string(),
        "start_timestamp": pa.int64(),
        "end_timestamp": pa.int64(),
        "num_timestamps": pa.int64(),
        "focal_track_id": pa.string(),
        "city": pa.string(),
    }
    if replacements is not None:
        types.update(replacements)
    return pa.schema(
        [
            pa.field(name, data_type)
            for name, data_type in types.items()
            if name != omitted
        ]
        + list(extras)
    )


def _write_source(
    root: Path,
    *,
    payload: dict[str, object] | None = None,
    rows: list[dict[str, object]] | None = None,
    schema: pa.Schema | None = None,
    name: str = "scenario.parquet",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    selected = _payload() if payload is None else payload
    selected_rows = _rows(selected) if rows is None else rows
    path = root / name
    pq.write_table(
        pa.Table.from_pylist(
            selected_rows,
            schema=_source_schema() if schema is None else schema,
        ),
        path,
    )
    return path


def _config(
    *,
    policy: str = "dynamic_only",
    ego_track_id: str | None = None,
) -> av2_motion.Av2MotionAdapterConfig:
    return av2_motion.Av2MotionAdapterConfig(
        dataset_version="1.1",
        split_name="train",
        inclusion_policy=policy,
        ego_track_id=ego_track_id,
    )


def _conversion(
    tmp_path: Path,
    *,
    policy: str = "dynamic_only",
    ego_track_id: str | None = None,
) -> tuple[Path, Path, av2_motion.Av2MotionScenarioConversion]:
    source_root = tmp_path / "source"
    source_path = _write_source(source_root)
    conversion = av2_motion.load_av2_motion_scenario(
        source_root,
        source_path.name,
        config=_config(policy=policy, ego_track_id=ego_track_id),
    )
    return source_root, source_path, conversion


def _plain_json_value(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(_plain_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _plain_json_value(item)
            for key, item in value.items()
        )
    return False


def test_exact_enums_and_public_api() -> None:
    assert tuple(item.value for item in av2_motion.Av2ObjectType) == (
        "vehicle",
        "pedestrian",
        "motorcyclist",
        "cyclist",
        "bus",
        "static",
        "background",
        "construction",
        "riderless_bicycle",
        "unknown",
    )
    assert tuple((item.name, item.value) for item in av2_motion.Av2TrackCategory) == (
        ("track_fragment", 0),
        ("unscored_track", 1),
        ("scored_track", 2),
        ("focal_track", 3),
    )
    assert tuple(item.value for item in av2_motion.Av2AgentInclusionPolicy) == (
        "dynamic_only",
        "all_tracks",
    )
    assert av2_motion.__all__ == [
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


@pytest.mark.parametrize(
    ("model", "field_names"),
    [
        (
            av2_motion.Av2MotionAdapterConfig,
            (
                "dataset_version",
                "split_name",
                "adapter_version",
                "inclusion_policy",
                "ego_track_id",
                "source_map_available",
            ),
        ),
        (
            av2_motion.Av2SourceTrackSummary,
            (
                "source_track_id",
                "source_object_type",
                "source_category",
                "included",
                "canonical_agent_id",
                "canonical_trajectory_id",
                "source_state_count",
                "exclusion_reason",
            ),
        ),
        (
            av2_motion.Av2MotionScenarioConversion,
            (
                "source_relative_path",
                "source_checksum",
                "source_scenario_id",
                "source_focal_track_id",
                "source_city_name",
                "source_track_count",
                "source_state_count",
                "scenario",
                "coordinate_frame",
                "agents",
                "trajectories",
                "track_summaries",
                "object_type_counts",
                "category_counts",
            ),
        ),
        (
            av2_motion.Av2MotionScenarioArtifacts,
            (
                "adapter_summary",
                "scenario_manifest",
                "coordinate_frame_metadata",
                "agent_metadata",
                "trajectory_samples",
            ),
        ),
    ],
)
def test_models_are_frozen_slotted_with_exact_fields(
    model: type[Any],
    field_names: tuple[str, ...],
) -> None:
    assert tuple(field.name for field in fields(model)) == field_names
    assert "__slots__" in model.__dict__
    assert model.__dataclass_params__.frozen


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"dataset_version": ""}, "dataset_version"),
        ({"split_name": " "}, "split_name"),
        ({"adapter_version": 1}, "adapter_version"),
        ({"inclusion_policy": "sometimes"}, "inclusion_policy"),
        ({"ego_track_id": ""}, "ego_track_id"),
        ({"source_map_available": 1}, "source_map_available"),
    ],
)
def test_config_validation(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "dataset_version": "1.1",
        "split_name": "train",
    }
    values.update(changes)
    with pytest.raises(ValidationError, match=message):
        av2_motion.Av2MotionAdapterConfig(**values)  # type: ignore[arg-type]


def test_config_coercion_trimming_and_immutability() -> None:
    config = av2_motion.Av2MotionAdapterConfig(
        " 1.1 ",
        " train ",
        inclusion_policy="all_tracks",
        ego_track_id=" ego ",
    )
    assert config.dataset_version == "1.1"
    assert config.split_name == "train"
    assert config.inclusion_policy is av2_motion.Av2AgentInclusionPolicy.ALL_TRACKS
    assert config.ego_track_id == "ego"
    with pytest.raises(FrozenInstanceError):
        config.split_name = "val"  # type: ignore[misc]


def test_track_summary_validation() -> None:
    included = av2_motion.Av2SourceTrackSummary(
        " source ",
        "vehicle",
        3,
        True,
        "agent:one",
        "trajectory:one",
        2,
        None,
    )
    assert included.source_track_id == "source"
    assert included.source_object_type is av2_motion.Av2ObjectType.VEHICLE
    assert included.source_category is av2_motion.Av2TrackCategory.focal_track
    with pytest.raises(ValidationError, match="canonical identifiers"):
        replace(included, canonical_agent_id=None)
    with pytest.raises(ValidationError, match="exclusion_reason"):
        replace(included, exclusion_reason="reason")
    with pytest.raises(ValidationError, match="at least one"):
        replace(included, source_state_count=0)
    excluded = replace(
        included,
        included=False,
        canonical_agent_id=None,
        canonical_trajectory_id=None,
        exclusion_reason="excluded",
    )
    with pytest.raises(ValidationError, match="exclusion_reason"):
        replace(excluded, exclusion_reason=None)


def test_source_schema_accepts_required_optional_and_additional_columns() -> None:
    schema = _source_schema(
        replacements={
            "track_id": pa.large_string(),
            "position_x": pa.int32(),
            "heading": pa.float32(),
            "timestep": pa.int16(),
            "start_timestamp": pa.float64(),
            "end_timestamp": pa.float64(),
        },
        extras=(
            pa.field("map_id", pa.string()),
            pa.field("slice_id", pa.string()),
            pa.field("ignored_struct", pa.struct([pa.field("x", pa.int8())])),
        ),
    )
    av2_motion.validate_av2_motion_source_schema(schema)


@pytest.mark.parametrize("column", ("start_timestamp", "end_timestamp"))
@pytest.mark.parametrize(
    "bad_type",
    (
        pa.float32(),
        pa.uint64(),
        pa.decimal128(20, 0),
        pa.string(),
        pa.binary(),
        pa.dictionary(pa.int8(), pa.float64()),
        pa.list_(pa.float64()),
        pa.struct([pa.field("value", pa.float64())]),
        pa.timestamp("ns"),
        pa.duration("ns"),
    ),
)
def test_timestamp_endpoint_schema_rejects_non_int_or_float64_types(
    column: str,
    bad_type: pa.DataType,
) -> None:
    with pytest.raises(
        SchemaError,
        match=rf"{column!r} must use signed integer or float64",
    ):
        av2_motion.validate_av2_motion_source_schema(
            _source_schema(replacements={column: bad_type})
        )


def test_non_endpoint_integer_columns_remain_signed_integer_only() -> None:
    for column, bad_type in (
        ("object_category", pa.float64()),
        ("timestep", pa.float64()),
        ("num_timestamps", pa.float64()),
    ):
        with pytest.raises(SchemaError, match=column):
            av2_motion.validate_av2_motion_source_schema(
                _source_schema(replacements={column: bad_type})
            )


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_source_schema_rejects_each_missing_required_column(column: str) -> None:
    with pytest.raises(SchemaError, match=column):
        av2_motion.validate_av2_motion_source_schema(_source_schema(omitted=column))


@pytest.mark.parametrize(
    ("column", "bad_type"),
    [
        ("observed", pa.int8()),
        ("track_id", pa.binary()),
        ("object_type", pa.dictionary(pa.int8(), pa.string())),
        ("object_category", pa.uint8()),
        ("timestep", pa.float64()),
        ("start_timestamp", pa.list_(pa.int64())),
        ("position_x", pa.uint32()),
        ("heading", pa.binary()),
        ("velocity_y", pa.struct([pa.field("x", pa.float64())])),
    ],
)
def test_source_schema_rejects_incompatible_types(
    column: str,
    bad_type: pa.DataType,
) -> None:
    with pytest.raises(SchemaError, match=column):
        av2_motion.validate_av2_motion_source_schema(
            _source_schema(replacements={column: bad_type})
        )


def test_source_schema_rejects_duplicate_required_column() -> None:
    schema = pa.schema([*_source_schema(), pa.field("track_id", pa.string())])
    with pytest.raises(SchemaError, match="more than once"):
        av2_motion.validate_av2_motion_source_schema(schema)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (0.0, 0),
        (-0.0, 0),
        (7, 7),
        (7.0, 7),
        (float(2**62), 2**62),
        (-(2**63), -(2**63)),
        (float(-(2**63)), -(2**63)),
    ],
)
def test_source_timestamp_normalization_accepts_exact_int64_values(
    value: object,
    expected: int,
) -> None:
    normalize = cast(Any, av2_motion)._source_timestamp_ns
    assert normalize(value, "start_timestamp") == expected


@pytest.mark.parametrize(
    ("value", "column", "message"),
    [
        (True, "start_timestamp", "start_timestamp"),
        ("1", "start_timestamp", "start_timestamp"),
        (object(), "start_timestamp", "start_timestamp"),
        (1.5, "start_timestamp", "start_timestamp"),
        (-2.25, "end_timestamp", "end_timestamp"),
        (math.nan, "start_timestamp", "start_timestamp"),
        (math.inf, "end_timestamp", "end_timestamp"),
        (-math.inf, "end_timestamp", "end_timestamp"),
        (float(-(2**63) - 2048), "start_timestamp", "signed int64"),
        (float(2**63), "end_timestamp", "signed int64"),
        (2**63, "end_timestamp", "signed int64"),
        (-(2**63) - 1, "start_timestamp", "signed int64"),
    ],
)
def test_source_timestamp_normalization_rejects_lossy_or_invalid_values(
    value: object,
    column: str,
    message: str,
) -> None:
    normalize = cast(Any, av2_motion)._source_timestamp_ns
    with pytest.raises(SchemaError, match=message):
        normalize(value, column)


def test_inspection_is_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    _write_source(root)

    def fail_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("full table read was attempted")

    monkeypatch.setattr(pq, "read_table", fail_read)
    schema = av2_motion.inspect_av2_motion_scenario_file(
        root,
        "scenario.parquet",
    )
    assert schema.names[: len(REQUIRED_COLUMNS)] == list(REQUIRED_COLUMNS)


def test_inspection_rejects_malformed_parquet(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "bad.parquet").write_bytes(b"not parquet")
    with pytest.raises(SchemaError, match="valid Parquet"):
        av2_motion.inspect_av2_motion_scenario_file(root, "bad.parquet")


@pytest.mark.parametrize(
    "relative_path",
    ("../outside.parquet", Path("C:/absolute.parquet"), "."),
)
def test_inspection_rejects_unsafe_or_nonfile_paths(
    tmp_path: Path,
    relative_path: str | Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(ArtifactError):
        av2_motion.inspect_av2_motion_scenario_file(root, relative_path)


def test_inspection_rejects_missing_root_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="source_root"):
        av2_motion.inspect_av2_motion_scenario_file(
            tmp_path / "missing",
            "scenario.parquet",
        )
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(ArtifactError, match="missing"):
        av2_motion.inspect_av2_motion_scenario_file(root, "missing.parquet")
    directory = root / "directory"
    directory.mkdir()
    with pytest.raises(ArtifactError, match="regular file"):
        av2_motion.inspect_av2_motion_scenario_file(root, "directory")


def test_inspection_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = _write_source(tmp_path / "outside")
    link = root / "link.parquet"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(ArtifactError, match="symbolic link"):
        av2_motion.inspect_av2_motion_scenario_file(root, link.name)


def test_fixture_conversion_metadata_counts_and_order(tmp_path: Path) -> None:
    _root, source, conversion = _conversion(tmp_path)
    assert conversion.source_relative_path == Path("scenario.parquet")
    assert conversion.source_checksum == hashlib.sha256(source.read_bytes()).hexdigest()
    assert conversion.source_scenario_id == "fixture-scenario-001"
    assert conversion.source_focal_track_id == "focal-vehicle"
    assert conversion.source_city_name == "PIT"
    assert conversion.source_track_count == 4
    assert conversion.source_state_count == 15
    assert tuple(item.source_track_id for item in conversion.track_summaries) == (
        "focal-vehicle",
        "pedestrian-01",
        "scored-bus",
        "static-cone",
    )
    assert tuple(item.source_agent_id for item in conversion.agents) == (
        "focal-vehicle",
        "pedestrian-01",
        "scored-bus",
    )
    assert conversion.object_type_counts == (
        ("vehicle", 1),
        ("pedestrian", 1),
        ("motorcyclist", 0),
        ("cyclist", 0),
        ("bus", 1),
        ("static", 1),
        ("background", 0),
        ("construction", 0),
        ("riderless_bicycle", 0),
        ("unknown", 0),
    )
    assert conversion.category_counts == (
        ("track_fragment", 1),
        ("unscored_track", 1),
        ("scored_track", 1),
        ("focal_track", 1),
    )


@pytest.mark.parametrize("column", SCENARIO_COLUMNS)
def test_conflicting_scenario_columns_are_rejected(
    tmp_path: Path,
    column: str,
) -> None:
    rows = _rows(_payload())
    value = rows[-1][column]
    rows[-1][column] = value + 1 if isinstance(value, int) else f"{value}-other"
    root = tmp_path / "source"
    _write_source(root, rows=rows)
    with pytest.raises(SchemaError, match=column):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


def test_zero_row_source_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _write_source(root, rows=[])
    with pytest.raises(SchemaError, match="at least one row"):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


@pytest.mark.parametrize(
    ("start", "end", "count", "message"),
    [
        (5, 4, 2, "must not exceed"),
        (1, 2, 1, "equal start"),
        (1, 5, 4, "not exactly divisible"),
        (1, 1, 2, "positive duration"),
        (1, 1, 0, "at least one"),
    ],
)
def test_invalid_timestamp_grids(
    tmp_path: Path,
    start: int,
    end: int,
    count: int,
    message: str,
) -> None:
    payload = _payload()
    payload.update(
        start_timestamp=start,
        end_timestamp=end,
        num_timestamps=count,
    )
    rows = _rows(payload)[:1]
    rows[0]["timestep"] = 0
    root = tmp_path / "source"
    _write_source(root, rows=rows)
    with pytest.raises(SchemaError, match=message):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


def test_one_timestamp_scenario_is_supported(tmp_path: Path) -> None:
    payload = _payload()
    payload.update(start_timestamp=7, end_timestamp=7, num_timestamps=1)
    row = _rows(payload)[0]
    root = tmp_path / "source"
    _write_source(root, rows=[row])
    conversion = av2_motion.load_av2_motion_scenario(
        root,
        "scenario.parquet",
        config=_config(),
    )
    assert conversion.trajectories[0].samples[0].timestamp_ns == 7


def test_provider_shaped_float64_timestamp_grid_is_exact(tmp_path: Path) -> None:
    payload = _payload()
    payload.update(
        start_timestamp=1_000_000_000.0,
        end_timestamp=5_000_000_000.0,
        num_timestamps=5,
    )
    rows = _rows(payload)
    schema = _source_schema(
        replacements={
            "start_timestamp": pa.float64(),
            "end_timestamp": pa.float64(),
        }
    )
    root = tmp_path / "source"
    source = _write_source(root, rows=rows, schema=schema)
    source_before = source.read_bytes()
    inspected = av2_motion.inspect_av2_motion_scenario_file(
        root,
        source.name,
    )
    assert inspected.field("start_timestamp").type == pa.float64()
    assert inspected.field("end_timestamp").type == pa.float64()
    assert inspected.field("num_timestamps").type == pa.int64()
    conversion = av2_motion.load_av2_motion_scenario(
        root,
        source.name,
        config=_config(),
    )
    assert conversion.scenario.start_time_ns == 1_000_000_000
    assert conversion.scenario.end_time_ns == 5_000_000_000
    focal = next(
        trajectory
        for trajectory in conversion.trajectories
        if trajectory.trajectory_id.endswith(":focal-vehicle")
    )
    assert [sample.timestamp_ns for sample in focal.samples] == [
        1_000_000_000,
        2_000_000_000,
        3_000_000_000,
        4_000_000_000,
        5_000_000_000,
    ]
    repository = tmp_path / "repository"
    repository.mkdir()
    run = artifact_store.prepare_run_directory(
        repository.resolve(),
        "results",
        "run:provider-shaped-float64",
        reserve_fraction=0.0,
    )
    artifacts = av2_motion.materialize_av2_motion_scenario(run, conversion)
    av2_motion.verify_av2_motion_scenario_artifacts(
        repository,
        conversion,
        artifacts,
    )
    assert source.read_bytes() == source_before


def test_one_timestamp_float64_scenario_is_supported(tmp_path: Path) -> None:
    payload = _payload()
    payload.update(start_timestamp=7.0, end_timestamp=7.0, num_timestamps=1)
    row = _rows(payload)[0]
    root = tmp_path / "source"
    _write_source(
        root,
        rows=[row],
        schema=_source_schema(
            replacements={
                "start_timestamp": pa.float64(),
                "end_timestamp": pa.float64(),
            }
        ),
    )
    conversion = av2_motion.load_av2_motion_scenario(
        root,
        "scenario.parquet",
        config=_config(),
    )
    assert conversion.trajectories[0].samples[0].timestamp_ns == 7


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (1.0, 5.0, "not exactly divisible"),
        (6.0, 5.0, "must not exceed"),
        (1.5, 5.0, "start_timestamp"),
        (1.0, 5.5, "end_timestamp"),
    ],
)
def test_invalid_float64_timestamp_grid_is_rejected(
    tmp_path: Path,
    start: float,
    end: float,
    message: str,
) -> None:
    payload = _payload()
    payload.update(
        start_timestamp=start,
        end_timestamp=end,
        num_timestamps=4,
    )
    row = _rows(payload)[0]
    row["timestep"] = 0
    root = tmp_path / "source"
    _write_source(
        root,
        rows=[row],
        schema=_source_schema(
            replacements={
                "start_timestamp": pa.float64(),
                "end_timestamp": pa.float64(),
            }
        ),
    )
    with pytest.raises(SchemaError, match=message):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


def test_integer_and_float64_endpoints_have_identical_canonical_outputs(
    tmp_path: Path,
) -> None:
    integer_root = tmp_path / "integer"
    float_root = tmp_path / "float"
    _write_source(integer_root)
    _write_source(
        float_root,
        schema=_source_schema(
            replacements={
                "start_timestamp": pa.float64(),
                "end_timestamp": pa.float64(),
            }
        ),
    )
    integer = av2_motion.load_av2_motion_scenario(
        integer_root,
        "scenario.parquet",
        config=_config(),
    )
    floating = av2_motion.load_av2_motion_scenario(
        float_root,
        "scenario.parquet",
        config=_config(),
    )
    assert (
        replace(
            floating.scenario,
            source_checksum=integer.scenario.source_checksum,
        )
        == integer.scenario
    )
    assert floating.coordinate_frame == integer.coordinate_frame
    assert floating.agents == integer.agents
    assert floating.trajectories == integer.trajectories


def test_out_of_range_timestep_is_rejected(tmp_path: Path) -> None:
    rows = _rows(_payload())
    rows[0]["timestep"] = 5
    root = tmp_path / "source"
    _write_source(root, rows=rows)
    with pytest.raises(SchemaError, match="outside"):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("object_type", "spaceship", "Av2ObjectType"),
        ("object_category", 4, "Av2TrackCategory"),
        ("observed", None, "observed"),
        ("position_x", math.nan, "finite"),
        ("heading", math.inf, "finite"),
        ("track_id", "bad:id", "colons"),
        ("scenario_id", "bad:id", "colons"),
    ],
)
def test_invalid_source_rows(
    tmp_path: Path,
    column: str,
    value: object,
    message: str,
) -> None:
    rows = _rows(_payload())
    if column in SCENARIO_COLUMNS:
        for row in rows:
            row[column] = value
    else:
        rows[0][column] = value
    schema = _source_schema()
    if value is None and column == "observed":
        schema = schema.set(
            schema.get_field_index("observed"),
            pa.field("observed", pa.bool_(), nullable=True),
        )
    root = tmp_path / "source"
    _write_source(root, rows=rows, schema=schema)
    with pytest.raises(SchemaError, match=message):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timestep", 0, "duplicate timestep"),
        ("object_type", "pedestrian", "multiple object types"),
        ("object_category", 2, "multiple categories"),
    ],
)
def test_inconsistent_track_rows(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    rows = _rows(_payload())
    rows[1][field] = value
    root = tmp_path / "source"
    _write_source(root, rows=rows)
    with pytest.raises(SchemaError, match=message):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


def test_trimmed_track_identifier_collision_is_rejected(tmp_path: Path) -> None:
    rows = _rows(_payload())
    rows[1]["track_id"] = " focal-vehicle "
    root = tmp_path / "source"
    _write_source(root, rows=rows)
    with pytest.raises(SchemaError, match="unique after trimming"):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


@pytest.mark.parametrize(
    ("focal_id", "row_index", "category", "message"),
    [
        ("missing", None, None, "identify exactly one"),
        ("focal-vehicle", 0, 2, "must use focal_track"),
        ("focal-vehicle", 5, 3, "exactly one"),
    ],
)
def test_focal_track_consistency(
    tmp_path: Path,
    focal_id: str,
    row_index: int | None,
    category: int | None,
    message: str,
) -> None:
    payload = _payload()
    payload["focal_track_id"] = focal_id
    rows = _rows(payload)
    if row_index is not None:
        target = rows[row_index]["track_id"]
        for row in rows:
            if row["track_id"] == target:
                row["object_category"] = category
    root = tmp_path / "source"
    _write_source(root, rows=rows)
    with pytest.raises(SchemaError, match=message):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


def test_dynamic_only_and_all_tracks_inclusion(tmp_path: Path) -> None:
    _root, _source, dynamic = _conversion(tmp_path / "dynamic")
    excluded = dynamic.track_summaries[-1]
    assert not excluded.included
    assert excluded.exclusion_reason == "excluded_static_source_type"
    assert len(dynamic.agents) == 3
    assert all(not agent.is_ego_agent for agent in dynamic.agents)

    _root, _source, all_tracks = _conversion(
        tmp_path / "all",
        policy="all_tracks",
    )
    assert len(all_tracks.agents) == 4
    static_agent = all_tracks.agents[-1]
    static_trajectory = all_tracks.trajectories[-1]
    assert static_agent.quality_flags == (
        "av2_track_fragment",
        "av2_static_source_type",
    )
    assert static_trajectory.quality_flags == static_agent.quality_flags
    assert all(
        sample.quality_flags == static_agent.quality_flags
        for sample in static_trajectory.samples
    )


@pytest.mark.parametrize(
    ("object_type", "expected"),
    [
        ("vehicle", AgentClass.VEHICLE),
        ("bus", AgentClass.VEHICLE),
        ("pedestrian", AgentClass.PEDESTRIAN),
        ("cyclist", AgentClass.CYCLIST),
        ("motorcyclist", AgentClass.OTHER_DYNAMIC),
        ("unknown", AgentClass.UNKNOWN),
        ("static", AgentClass.UNKNOWN),
        ("background", AgentClass.UNKNOWN),
        ("construction", AgentClass.UNKNOWN),
        ("riderless_bicycle", AgentClass.UNKNOWN),
    ],
)
def test_every_object_type_mapping(
    object_type: str,
    expected: AgentClass,
) -> None:
    assert av2_motion.av2_object_type_to_agent_class(object_type) is expected


def test_unknown_object_type_mapping_rejected() -> None:
    with pytest.raises(ValidationError, match="Av2ObjectType"):
        av2_motion.av2_object_type_to_agent_class("spaceship")


def test_optional_ego_assignment_and_errors(tmp_path: Path) -> None:
    _root, _source, conversion = _conversion(
        tmp_path / "valid",
        ego_track_id="scored-bus",
    )
    assert [
        agent.source_agent_id for agent in conversion.agents if agent.is_ego_agent
    ] == ["scored-bus"]
    root = tmp_path / "invalid" / "source"
    _write_source(root)
    with pytest.raises(SchemaError, match="does not identify"):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(ego_track_id="missing"),
        )
    with pytest.raises(SchemaError, match="exclude"):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(ego_track_id="static-cone"),
        )


def test_static_focal_cannot_be_excluded(tmp_path: Path) -> None:
    rows = _rows(_payload())
    for row in rows:
        if row["track_id"] == "focal-vehicle":
            row["object_type"] = "static"
    root = tmp_path / "source"
    _write_source(root, rows=rows)
    with pytest.raises(SchemaError, match="focal"):
        av2_motion.load_av2_motion_scenario(
            root,
            "scenario.parquet",
            config=_config(),
        )


def test_canonical_translation_timestamps_and_samples(tmp_path: Path) -> None:
    _root, _source, conversion = _conversion(tmp_path)
    scenario = conversion.scenario
    frame = conversion.coordinate_frame
    assert scenario.scenario_id == "scenario:av2:fixture-scenario-001"
    assert scenario.coordinate_frame_id == ("frame:av2:fixture-scenario-001:local")
    assert scenario.origin_x_m == frame.origin_x_m == 100.0
    assert scenario.origin_y_m == frame.origin_y_m == 200.0
    assert scenario.origin_z_m is frame.origin_z_m is None
    assert scenario.source_crs == frame.source_crs == "av2_city_map"
    assert scenario.dataset_id == "av2_motion"
    assert scenario.dataset_version == "1.1"
    assert scenario.split_name == "train"
    assert scenario.city_or_region == "PIT"
    assert scenario.start_time_ns == 1_000_000_000
    assert scenario.end_time_ns == 5_000_000_000
    assert not scenario.has_elevation
    assert not scenario.source_map_available
    assert scenario.adapter_name == "av2_motion_adapter"
    assert frame.frame_type == "local_cartesian"
    assert frame.axis_convention == "right_handed_x_y_z_up"
    assert (frame.distance_unit, frame.angle_unit, frame.timestamp_unit) == (
        "m",
        "rad",
        "ns",
    )
    assert frame.parent_frame_id is None
    assert frame.transform_to_parent_4x4 is None

    focal = conversion.trajectories[0]
    assert [sample.x_m for sample in focal.samples] == [
        0.0,
        2.0,
        4.0,
        6.0,
        8.0,
    ]
    assert [sample.y_m for sample in focal.samples] == [0.0] * 5
    assert focal.samples[2].heading_rad == pytest.approx(0.1)
    bus = conversion.trajectories[2]
    assert bus.samples[0].heading_rad == pytest.approx(
        (-3.5 + math.pi) % (2 * math.pi) - math.pi
    )
    pedestrian = conversion.trajectories[1]
    assert [sample.sample_index for sample in pedestrian.samples] == [0, 1, 2]
    assert [sample.timestamp_ns for sample in pedestrian.samples] == [
        1_000_000_000,
        3_000_000_000,
        5_000_000_000,
    ]
    assert [sample.is_observed for sample in pedestrian.samples] == [
        True,
        False,
        True,
    ]
    assert all(sample.is_valid for sample in pedestrian.samples)
    assert all(sample.z_m is None for sample in pedestrian.samples)
    assert all(sample.acceleration_x_mps2 is None for sample in pedestrian.samples)
    assert all(sample.acceleration_y_mps2 is None for sample in pedestrian.samples)
    assert pedestrian.samples[0].velocity_x_mps == 0.0
    assert pedestrian.samples[0].velocity_y_mps == 1.0
    assert pedestrian.samples[0].speed_mps == 1.0
    assert all(
        agent.length_m is agent.width_m is agent.height_m is None
        for agent in conversion.agents
    )
    assert conversion.agents[0].is_focal_agent
    assert not any(agent.is_focal_agent for agent in conversion.agents[1:])
    validate_scenario_bundle(
        scenario,
        frame,
        conversion.agents,
        conversion.trajectories,
    )


def test_repeated_conversion_equal_and_source_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "source"
    source = _write_source(root)
    before = source.read_bytes()
    first = av2_motion.load_av2_motion_scenario(
        root,
        source.name,
        config=_config(),
    )
    second = av2_motion.load_av2_motion_scenario(
        root,
        source.name,
        config=_config(),
    )
    assert first == second
    assert source.read_bytes() == before


def test_conversion_model_copies_collections_and_rejects_bad_counts(
    tmp_path: Path,
) -> None:
    _root, _source, conversion = _conversion(tmp_path)
    agents = list(conversion.agents)
    copied = replace(conversion, agents=agents)  # type: ignore[arg-type]
    agents.clear()
    assert copied.agents == conversion.agents
    with pytest.raises(ValidationError, match="source_track_count"):
        replace(conversion, source_track_count=5)
    with pytest.raises(ValidationError, match="source_state_count"):
        replace(conversion, source_state_count=16)
    with pytest.raises(ValidationError, match="canonical order"):
        replace(
            conversion,
            object_type_counts=tuple(reversed(conversion.object_type_counts)),
        )


def test_summary_is_exact_plain_deterministic_and_detached(tmp_path: Path) -> None:
    _root, _source, conversion = _conversion(tmp_path)
    first = av2_motion.av2_motion_conversion_summary_to_dict(conversion)
    second = av2_motion.av2_motion_conversion_summary_to_dict(conversion)
    assert tuple(first) == SUMMARY_FIELDS
    assert first == second
    assert _plain_json_value(first)
    assert first["source_relative_path"] == "scenario.parquet"
    assert str(tmp_path.resolve()) not in json.dumps(first)
    first["source_track_count"] = 999
    cast(list[object], first["canonical_agent_ids"]).clear()
    fresh = av2_motion.av2_motion_conversion_summary_to_dict(conversion)
    assert fresh["source_track_count"] == 4
    assert len(cast(list[object], fresh["canonical_agent_ids"])) == 3


def _materialized(
    tmp_path: Path,
    *,
    policy: str = "dynamic_only",
    ego_track_id: str | None = None,
    run_id: str = "run:av2",
) -> tuple[
    Path,
    artifact_store.RunDirectory,
    av2_motion.Av2MotionScenarioConversion,
    av2_motion.Av2MotionScenarioArtifacts,
]:
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    source_root = repository / "source"
    _write_source(source_root)
    conversion = av2_motion.load_av2_motion_scenario(
        source_root,
        "scenario.parquet",
        config=_config(policy=policy, ego_track_id=ego_track_id),
    )
    run = artifact_store.prepare_run_directory(
        repository.resolve(),
        "results",
        run_id,
        reserve_fraction=0.0,
    )
    artifacts = av2_motion.materialize_av2_motion_scenario(run, conversion)
    return repository, run, conversion, artifacts


def test_materialization_exact_paths_schemas_rows_and_verification(
    tmp_path: Path,
) -> None:
    repository, run, conversion, artifacts = _materialized(tmp_path)
    relative_paths = (
        artifacts.adapter_summary.relative_path,
        artifacts.scenario_manifest.written_artifact.relative_path,
        artifacts.coordinate_frame_metadata.written_artifact.relative_path,
        artifacts.agent_metadata.written_artifact.relative_path,
        artifacts.trajectory_samples.written_artifact.relative_path,
    )
    run_prefix = run.path.relative_to(repository)
    assert tuple(path.relative_to(run_prefix) for path in relative_paths) == (
        Path("artifacts/av2_motion_scenario/adapter_summary.json"),
        Path("artifacts/av2_motion_scenario/scenario_manifest.parquet"),
        Path("artifacts/av2_motion_scenario/coordinate_frame_metadata.parquet"),
        Path("artifacts/av2_motion_scenario/agent_metadata.parquet"),
        Path("artifacts/av2_motion_scenario/trajectory_samples.parquet"),
    )
    assert (
        artifacts.scenario_manifest.schema_name,
        artifacts.coordinate_frame_metadata.schema_name,
        artifacts.agent_metadata.schema_name,
        artifacts.trajectory_samples.schema_name,
    ) == (
        CanonicalSchemaName.SCENARIO_MANIFEST,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        CanonicalSchemaName.AGENT_METADATA,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    assert (
        artifacts.scenario_manifest.row_count,
        artifacts.coordinate_frame_metadata.row_count,
        artifacts.agent_metadata.row_count,
        artifacts.trajectory_samples.row_count,
    ) == (1, 1, 3, 13)
    assert all(
        artifact.written_artifact.size_bytes > 0
        and len(artifact.written_artifact.content_checksum) == 64
        for artifact in (
            artifacts.scenario_manifest,
            artifacts.coordinate_frame_metadata,
            artifacts.agent_metadata,
            artifacts.trajectory_samples,
        )
    )
    av2_motion.verify_av2_motion_scenario_artifacts(
        repository,
        conversion,
        artifacts,
    )
    assert tuple(run.manifests_path.iterdir()) == ()


def test_materialization_rejects_overwrite_and_finalized_run(
    tmp_path: Path,
) -> None:
    _repository, run, conversion, _artifacts = _materialized(tmp_path)
    with pytest.raises(ArtifactError, match="exists"):
        av2_motion.materialize_av2_motion_scenario(run, conversion)
    artifact_store.finalize_run_directory(run)
    with pytest.raises(ArtifactError, match="immutable"):
        av2_motion.materialize_av2_motion_scenario(
            run,
            conversion,
            relative_directory="artifacts/another",
        )


def test_verification_rejects_altered_summary(tmp_path: Path) -> None:
    repository, _run, conversion, artifacts = _materialized(tmp_path)
    path = repository / artifacts.adapter_summary.relative_path
    changed = path.read_bytes().replace(
        b'"source_track_count":4', b'"source_track_count":9'
    )
    path.write_bytes(changed)
    updated = WrittenArtifact(
        artifacts.adapter_summary.relative_path,
        len(changed),
        hashlib.sha256(changed).hexdigest(),
    )
    with pytest.raises(SchemaError, match="differs"):
        av2_motion.verify_av2_motion_scenario_artifacts(
            repository,
            conversion,
            replace(artifacts, adapter_summary=updated),
        )


def test_verification_rejects_corrupt_parquet(tmp_path: Path) -> None:
    repository, _run, conversion, artifacts = _materialized(tmp_path)
    path = repository / artifacts.agent_metadata.written_artifact.relative_path
    path.write_bytes(b"corrupt")
    with pytest.raises(ArtifactError):
        av2_motion.verify_av2_motion_scenario_artifacts(
            repository,
            conversion,
            artifacts,
        )


def test_equivalent_materializations_have_identical_checksums(
    tmp_path: Path,
) -> None:
    first = _materialized(tmp_path / "first")
    second = _materialized(tmp_path / "second")
    first_artifacts = first[3]
    second_artifacts = second[3]
    assert first_artifacts.adapter_summary.content_checksum == (
        second_artifacts.adapter_summary.content_checksum
    )
    assert [
        artifact.written_artifact.content_checksum
        for artifact in (
            first_artifacts.scenario_manifest,
            first_artifacts.coordinate_frame_metadata,
            first_artifacts.agent_metadata,
            first_artifacts.trajectory_samples,
        )
    ] == [
        artifact.written_artifact.content_checksum
        for artifact in (
            second_artifacts.scenario_manifest,
            second_artifacts.coordinate_frame_metadata,
            second_artifacts.agent_metadata,
            second_artifacts.trajectory_samples,
        )
    ]


def test_module_imports_and_top_level_are_restricted() -> None:
    source = Path(av2_motion.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots.isdisjoint(
        {
            "argoverse",
            "av2",
            "pandas",
            "geopandas",
            "shapely",
            "networkx",
            "rerun",
            "torch",
            "subprocess",
            "requests",
            "urllib",
        }
    )
    assert not any(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        for node in tree.body
    )


@pytest.mark.parametrize(
    ("policy", "ego_track_id", "expected_agents", "expected_samples"),
    [
        ("dynamic_only", None, 3, 13),
        ("all_tracks", "static-cone", 4, 15),
    ],
)
def test_complete_av2_adapter_integration(
    tmp_path: Path,
    policy: str,
    ego_track_id: str | None,
    expected_agents: int,
    expected_samples: int,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source_root = repository / "source"
    source = _write_source(source_root)
    source_before = source.read_bytes()
    manifest = discover_dataset_source(
        get_dataset_registry_entry("av2_motion"),
        dataset_version="1.1",
        adapter_version="1.0",
        source_root=source_root,
        source_root_label="synthetic-av2-fixture",
        checksum_mode="sha256",
    )
    verify_dataset_source(manifest, source_root=source_root)
    conversion = av2_motion.load_av2_motion_scenario(
        source_root,
        source.name,
        config=_config(policy=policy, ego_track_id=ego_track_id),
    )
    validate_scenario_bundle(
        conversion.scenario,
        conversion.coordinate_frame,
        conversion.agents,
        conversion.trajectories,
    )
    run = artifact_store.prepare_run_directory(
        repository.resolve(),
        "results",
        f"run:integration:{policy}",
        reserve_fraction=0.0,
    )
    artifacts = av2_motion.materialize_av2_motion_scenario(
        run,
        conversion,
        row_group_size=2,
    )
    av2_motion.verify_av2_motion_scenario_artifacts(
        repository,
        conversion,
        artifacts,
    )
    artifact_specs = (
        (
            artifacts.scenario_manifest,
            CanonicalSchemaName.SCENARIO_MANIFEST,
            1,
        ),
        (
            artifacts.coordinate_frame_metadata,
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            1,
        ),
        (
            artifacts.agent_metadata,
            CanonicalSchemaName.AGENT_METADATA,
            expected_agents,
        ),
        (
            artifacts.trajectory_samples,
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
            expected_samples,
        ),
    )
    for artifact, schema_name, row_count in artifact_specs:
        path = artifact.written_artifact.relative_path
        batches = tuple(
            iter_canonical_parquet_batches(
                repository,
                (path,),
                schema_name,
                batch_size=2,
            )
        )
        table = pa.Table.from_batches(
            batches,
            schema=get_arrow_schema(schema_name),
        )
        frame = scan_canonical_parquet(
            repository,
            (path,),
            schema_name,
        ).collect()
        assert table.column_names == list(get_arrow_schema(schema_name).names)
        assert frame.columns == list(get_arrow_schema(schema_name).names)
        assert frame.to_dicts() == table.to_pylist()
        assert table.num_rows == row_count
    summary = av2_motion.av2_motion_conversion_summary_to_dict(conversion)
    static_summary = next(
        item
        for item in cast(list[dict[str, object]], summary["track_summaries"])
        if item["source_track_id"] == "static-cone"
    )
    assert static_summary["included"] is (policy == "all_tracks")
    if policy == "dynamic_only":
        assert static_summary["exclusion_reason"] == ("excluded_static_source_type")
    else:
        assert [
            agent.source_agent_id for agent in conversion.agents if agent.is_ego_agent
        ] == ["static-cone"]
    assert source.read_bytes() == source_before
    artifact_store.finalize_run_directory(run)
    assert artifact_store.list_partial_artifacts(run) == ()
