"""Tests for the strict direct-JSON AV2 vector-map adapter."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import importlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from kinematicweave import artifact_store
from kinematicweave.artifact_store import WrittenArtifact
from kinematicweave.data import av2_map
from kinematicweave.data.av2_motion import (
    Av2MotionAdapterConfig,
    load_av2_motion_scenario,
)
from kinematicweave.data.parquet_io import (
    iter_canonical_parquet_batches,
    scan_canonical_parquet,
)
from kinematicweave.data.schemas import CanonicalSchemaName, get_arrow_schema
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    geometry_from_canonical_wkb,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAP_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "av2_map"
    / "log_map_archive_fixture-scenario-001.json"
)
MOTION_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "av2_motion" / "scenario_fixture.json"
)
MAP_FILENAME = MAP_FIXTURE.name
SUMMARY_FIELDS = (
    "schema_version",
    "adapter_name",
    "adapter_version",
    "dataset_id",
    "dataset_version",
    "source_relative_path",
    "source_checksum",
    "source_map_id",
    "canonical_scenario_id",
    "canonical_coordinate_frame_id",
    "centerline_point_count",
    "source_lane_segment_count",
    "source_drivable_area_count",
    "source_pedestrian_crossing_count",
    "omitted_external_lane_reference_count",
    "canonical_element_count",
    "element_type_counts",
    "canonical_map_element_ids",
)


@pytest.fixture(autouse=True)
def _refresh_adapter_after_reload_tests() -> None:
    """Keep adapter artifact classes aligned with reloaded shared modules."""
    importlib.reload(av2_map)


def _map_payload() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(MAP_FIXTURE.read_text(encoding="utf-8")),
    )


def _write_map(
    root: Path,
    payload: object | None = None,
    *,
    filename: str = MAP_FILENAME,
    content: bytes | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    if content is None:
        selected = _map_payload() if payload is None else payload
        content = json.dumps(
            selected,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    path.write_bytes(content)
    return path


def _motion_rows() -> list[dict[str, object]]:
    payload = cast(
        dict[str, object],
        json.loads(MOTION_FIXTURE.read_text(encoding="utf-8")),
    )
    scenario_fields = (
        "scenario_id",
        "start_timestamp",
        "end_timestamp",
        "num_timestamps",
        "focal_track_id",
        "city",
    )
    rows = cast(list[dict[str, object]], payload["rows"])
    return [
        {
            **row,
            **{field: payload[field] for field in scenario_fields},
        }
        for row in rows
    ]


def _motion_conversion(
    root: Path,
    *,
    source_map_available: bool = True,
) -> Any:
    root.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(_motion_rows()), root / "scenario.parquet")
    return load_av2_motion_scenario(
        root,
        "scenario.parquet",
        config=Av2MotionAdapterConfig(
            "1.1",
            "train",
            source_map_available=source_map_available,
        ),
    )


def _conversion(
    tmp_path: Path,
    *,
    payload: dict[str, Any] | None = None,
    point_count: int = 50,
) -> tuple[Path, Path, Any, av2_map.Av2VectorMapConversion]:
    motion = _motion_conversion(tmp_path / "motion")
    source_root = tmp_path / "map"
    source = _write_map(source_root, payload)
    conversion = av2_map.load_av2_vector_map(
        source_root,
        source.name,
        scenario=motion.scenario,
        coordinate_frame=motion.coordinate_frame,
        config=av2_map.Av2VectorMapAdapterConfig(centerline_point_count=point_count),
    )
    return source_root, source, motion, conversion


def test_exact_source_enums_and_public_api() -> None:
    assert tuple(item.value for item in av2_map.Av2LaneType) == (
        "VEHICLE",
        "BIKE",
        "BUS",
    )
    assert tuple(item.value for item in av2_map.Av2LaneMarkType) == (
        "DASH_SOLID_YELLOW",
        "DASH_SOLID_WHITE",
        "DASHED_WHITE",
        "DASHED_YELLOW",
        "DOUBLE_SOLID_YELLOW",
        "DOUBLE_SOLID_WHITE",
        "DOUBLE_DASH_YELLOW",
        "DOUBLE_DASH_WHITE",
        "SOLID_YELLOW",
        "SOLID_WHITE",
        "SOLID_DASH_WHITE",
        "SOLID_DASH_YELLOW",
        "SOLID_BLUE",
        "NONE",
        "UNKNOWN",
    )
    assert av2_map.__all__ == [
        "Av2LaneMarkType",
        "Av2LaneType",
        "Av2VectorMapAdapterConfig",
        "Av2VectorMapArtifacts",
        "Av2VectorMapConversion",
        "Av2VectorMapSourceSummary",
        "av2_vector_map_conversion_summary_to_dict",
        "inspect_av2_vector_map_file",
        "load_av2_vector_map",
        "materialize_av2_vector_map",
        "verify_av2_vector_map_artifacts",
    ]


@pytest.mark.parametrize(
    ("model", "field_names"),
    [
        (
            av2_map.Av2VectorMapAdapterConfig,
            ("adapter_version", "centerline_point_count", "max_source_bytes"),
        ),
        (
            av2_map.Av2VectorMapSourceSummary,
            (
                "source_relative_path",
                "source_checksum",
                "source_map_id",
                "lane_segment_count",
                "drivable_area_count",
                "pedestrian_crossing_count",
                "omitted_external_lane_reference_count",
            ),
        ),
        (
            av2_map.Av2VectorMapArtifacts,
            ("adapter_summary", "vector_map_elements"),
        ),
    ],
)
def test_models_are_frozen_slotted_with_expected_fields(
    model: type[Any],
    field_names: tuple[str, ...],
) -> None:
    assert tuple(field.name for field in fields(model)) == field_names
    assert "__slots__" in model.__dict__
    assert model.__dataclass_params__.frozen


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"adapter_version": ""}, "adapter_version"),
        ({"centerline_point_count": 1}, "at least two"),
        ({"centerline_point_count": True}, "non-Boolean"),
        ({"max_source_bytes": 0}, "positive"),
        ({"max_source_bytes": False}, "non-Boolean"),
    ],
)
def test_config_validation(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        av2_map.Av2VectorMapAdapterConfig(**kwargs)  # type: ignore[arg-type]


def test_config_trims_and_is_immutable() -> None:
    config = av2_map.Av2VectorMapAdapterConfig(" 2.0 ", 2, 1)
    assert config.adapter_version == "2.0"
    with pytest.raises(FrozenInstanceError):
        config.adapter_version = "3.0"  # type: ignore[misc]


def test_inspection_validates_entire_fixture(tmp_path: Path) -> None:
    root = tmp_path / "map"
    source = _write_map(root)
    before = source.read_bytes()
    summary = av2_map.inspect_av2_vector_map_file(root, source.name)
    assert summary.source_relative_path == Path(MAP_FILENAME)
    assert summary.source_checksum == hashlib.sha256(before).hexdigest()
    assert summary.source_map_id == "fixture-scenario-001"
    assert (
        summary.lane_segment_count,
        summary.drivable_area_count,
        summary.pedestrian_crossing_count,
    ) == (3, 1, 1)
    assert summary.omitted_external_lane_reference_count == 0
    assert source.read_bytes() == before


def test_absent_crossings_and_additional_root_fields_are_tolerated(
    tmp_path: Path,
) -> None:
    payload = _map_payload()
    payload.pop("pedestrian_crossings")
    payload["additional"] = {"ignored": True}
    root = tmp_path / "map"
    source = _write_map(root, payload)
    summary = av2_map.inspect_av2_vector_map_file(root, source.name)
    assert summary.pedestrian_crossing_count == 0


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("wrong.json", b"{}", "filename"),
        (MAP_FILENAME, b"\xff", "UTF-8"),
        (MAP_FILENAME, b"{", "malformed"),
        (MAP_FILENAME, b"[]", "JSON object"),
    ],
)
def test_file_and_json_failures(
    tmp_path: Path,
    filename: str,
    content: bytes,
    message: str,
) -> None:
    root = tmp_path / "map"
    source = _write_map(root, filename=filename, content=content)
    with pytest.raises((ArtifactError, SchemaError), match=message):
        av2_map.inspect_av2_vector_map_file(root, source.name)


def test_path_failures_and_source_size_limit(tmp_path: Path) -> None:
    root = tmp_path / "map"
    root.mkdir()
    with pytest.raises(ArtifactError, match="missing"):
        av2_map.inspect_av2_vector_map_file(root, MAP_FILENAME)
    with pytest.raises(ArtifactError):
        av2_map.inspect_av2_vector_map_file(root, "../" + MAP_FILENAME)
    with pytest.raises(ArtifactError):
        av2_map.inspect_av2_vector_map_file(root, Path("C:/absolute.json"))
    directory = root / MAP_FILENAME
    directory.mkdir()
    with pytest.raises(ArtifactError, match="regular file"):
        av2_map.inspect_av2_vector_map_file(root, directory.name)
    directory.rmdir()
    source = _write_map(root)
    with pytest.raises(ArtifactError, match="max_source_bytes"):
        av2_map.inspect_av2_vector_map_file(
            root,
            source.name,
            config=av2_map.Av2VectorMapAdapterConfig(
                max_source_bytes=source.stat().st_size - 1
            ),
        )


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "map"
    root.mkdir()
    target = _write_map(tmp_path / "outside")
    link = root / MAP_FILENAME
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(ArtifactError, match="symbolic link"):
        av2_map.inspect_av2_vector_map_file(root, link.name)


@pytest.mark.parametrize("missing", ("lane_segments", "drivable_areas"))
def test_missing_required_root_collection(
    tmp_path: Path,
    missing: str,
) -> None:
    payload = _map_payload()
    payload.pop(missing)
    root = tmp_path / "map"
    source = _write_map(root, payload)
    with pytest.raises(SchemaError, match=missing):
        av2_map.inspect_av2_vector_map_file(root, source.name)


def _invalid_source(
    tmp_path: Path,
    payload: dict[str, Any],
    message: str,
) -> None:
    root = tmp_path / "map"
    source = _write_map(root, payload)
    with pytest.raises(SchemaError, match=message):
        av2_map.inspect_av2_vector_map_file(root, source.name)


def test_invalid_source_entity_ids_and_references(tmp_path: Path) -> None:
    payload = _map_payload()
    payload["lane_segments"]["1"]["id"] = True
    _invalid_source(tmp_path / "bool", payload, "non-Boolean")

    payload = _map_payload()
    payload["lane_segments"]["1"]["id"] = 2
    _invalid_source(tmp_path / "mismatch", payload, "differ")

    payload = _map_payload()
    payload["lane_segments"]["1"]["successors"] = [2, 2]
    _invalid_source(tmp_path / "duplicate-ref", payload, "duplicates")

    payload = _map_payload()
    payload["lane_segments"]["1"]["successors"] = [1]
    _invalid_source(tmp_path / "self-ref", payload, "self references")

    payload = _map_payload()
    payload["lane_segments"]["1"]["left_neighbor_id"] = 3
    payload["lane_segments"]["1"]["right_neighbor_id"] = 3
    _invalid_source(tmp_path / "neighbors", payload, "must differ")


def test_external_lane_references_are_omitted_with_provenance(
    tmp_path: Path,
) -> None:
    payload = _map_payload()
    lane = payload["lane_segments"]["1"]
    lane["predecessors"] = [98]
    lane["successors"] = [99, 2, 88]
    lane["left_neighbor_id"] = 3
    lane["right_neighbor_id"] = 97

    root, _source, _motion, conversion = _conversion(tmp_path, payload=payload)
    summary = av2_map.inspect_av2_vector_map_file(root, MAP_FILENAME)
    assert summary.omitted_external_lane_reference_count == 4
    assert conversion.omitted_external_lane_reference_count == 4

    centerlines = {
        element.map_element_id: element
        for element in conversion.elements
        if element.element_type is MapElementType.LANE_CENTERLINE
    }
    centerline = centerlines["map:av2:fixture-scenario-001:lane:1:centerline"]
    assert centerline.predecessor_ids == ()
    assert centerline.successor_ids == (
        "map:av2:fixture-scenario-001:lane:2:centerline",
    )
    assert centerline.left_neighbor_id == (
        "map:av2:fixture-scenario-001:lane:3:centerline"
    )
    assert centerline.right_neighbor_id is None
    assert centerline.quality_flags == (
        "source_external_predecessors_omitted",
        "source_external_successors_omitted",
        "source_external_right_neighbor_omitted",
    )
    assert json.loads(centerline.semantic_attributes_json or "") == {
        "centerline_point_count": 50,
        "is_intersection": False,
        "lane_type": "VEHICLE",
        "omitted_external_predecessor_ids": [98],
        "omitted_external_right_neighbor_id": 97,
        "omitted_external_successor_ids": [88, 99],
        "source_geometry_has_z": True,
        "source_kind": "lane_centerline",
        "source_lane_id": 1,
    }
    assert all(
        reference in centerlines
        for element in centerlines.values()
        for reference in (
            *element.predecessor_ids,
            *element.successor_ids,
            element.left_neighbor_id,
            element.right_neighbor_id,
        )
        if reference is not None
    )
    assert not any(
        suffix in centerlines
        for suffix in (
            "map:av2:fixture-scenario-001:lane:88:centerline",
            "map:av2:fixture-scenario-001:lane:97:centerline",
            "map:av2:fixture-scenario-001:lane:98:centerline",
            "map:av2:fixture-scenario-001:lane:99:centerline",
        )
    )


def test_external_neighbors_and_retained_reference_order_are_deterministic(
    tmp_path: Path,
) -> None:
    payload = _map_payload()
    payload["lane_segments"]["1"]["successors"] = [3, 99, 2, 88]
    payload["lane_segments"]["2"]["left_neighbor_id"] = 77
    payload["lane_segments"]["3"]["right_neighbor_id"] = 66

    _root, _source, _motion, first = _conversion(
        tmp_path / "first",
        payload=payload,
    )
    reordered = cast(dict[str, Any], json.loads(json.dumps(payload)))
    reordered["lane_segments"]["1"]["successors"] = [88, 2, 99, 3]
    _root, _source, _motion, second = _conversion(
        tmp_path / "second",
        payload=reordered,
    )
    first_lane = first.elements[0]
    second_lane = second.elements[0]
    assert (
        first_lane.successor_ids
        == second_lane.successor_ids
        == (
            "map:av2:fixture-scenario-001:lane:2:centerline",
            "map:av2:fixture-scenario-001:lane:3:centerline",
        )
    )
    first_attributes = json.loads(first_lane.semantic_attributes_json or "")
    second_attributes = json.loads(second_lane.semantic_attributes_json or "")
    assert first_attributes["omitted_external_successor_ids"] == [88, 99]
    assert first_attributes == second_attributes
    assert first.omitted_external_lane_reference_count == 4
    assert second.omitted_external_lane_reference_count == 4


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lane_type", "CAR", "Av2LaneType"),
        ("left_lane_mark_type", "PAINT", "Av2LaneMarkType"),
        ("is_intersection", 1, "bool"),
    ],
)
def test_invalid_lane_semantics(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _map_payload()
    payload["lane_segments"]["1"][field] = value
    _invalid_source(tmp_path, payload, message)


def test_invalid_points_boundaries_and_polygons(tmp_path: Path) -> None:
    payload = _map_payload()
    del payload["lane_segments"]["1"]["left_lane_boundary"][0]["z"]
    _invalid_source(tmp_path / "point", payload, "missing 'z'")

    payload = _map_payload()
    payload["lane_segments"]["1"]["left_lane_boundary"][0]["x"] = math.nan
    _invalid_source(tmp_path / "nonfinite", payload, "finite")

    payload = _map_payload()
    payload["lane_segments"]["1"]["left_lane_boundary"] = [
        {"x": 1.0, "y": 1.0, "z": 1.0}
    ]
    _invalid_source(tmp_path / "short", payload, "at least two")

    payload = _map_payload()
    point = {"x": 1.0, "y": 1.0, "z": 1.0}
    payload["lane_segments"]["1"]["left_lane_boundary"] = [point, point]
    _invalid_source(tmp_path / "zero", payload, "positive 3D length")

    payload = _map_payload()
    payload["drivable_areas"]["10"]["area_boundary"] = [
        {"x": 0.0, "y": 0.0, "z": 1.0},
        {"x": 1.0, "y": 1.0, "z": 1.0},
        {"x": 0.0, "y": 1.0, "z": 1.0},
        {"x": 1.0, "y": 0.0, "z": 1.0},
    ]
    _invalid_source(tmp_path / "area", payload, "valid polygon")

    payload = _map_payload()
    payload["pedestrian_crossings"]["20"]["edge1"] = [{"x": 0.0, "y": 0.0, "z": 1.0}]
    _invalid_source(tmp_path / "crossing", payload, "two points")


@pytest.mark.parametrize(
    ("scenario_change", "frame_change", "message"),
    [
        ({"dataset_id": "other"}, {}, "dataset"),
        ({"source_scenario_id": "other"}, {}, "source ID"),
        ({"source_map_available": False}, {}, "not available"),
        ({}, {"scenario_id": "scenario:other"}, "scenario differs"),
        ({}, {"coordinate_frame_id": "frame:other"}, "identifier differs"),
        ({"source_crs": "other"}, {}, "source CRS"),
        ({}, {"source_crs": "other"}, "source CRS"),
        ({}, {"frame_type": "global"}, "frame type"),
        ({}, {"origin_x_m": 101.0}, "origin differs"),
    ],
)
def test_coordinate_frame_alignment_failures(
    tmp_path: Path,
    scenario_change: dict[str, object],
    frame_change: dict[str, object],
    message: str,
) -> None:
    motion = _motion_conversion(tmp_path / "motion")
    scenario = replace(motion.scenario, **scenario_change)
    frame = replace(motion.coordinate_frame, **frame_change)
    root = tmp_path / "map"
    source = _write_map(root)
    with pytest.raises(SchemaError, match=message):
        av2_map.load_av2_vector_map(
            root,
            source.name,
            scenario=scenario,
            coordinate_frame=frame,
        )


def test_conversion_geometry_topology_semantics_and_order(tmp_path: Path) -> None:
    _root, source, motion, conversion = _conversion(tmp_path)
    assert conversion.source_checksum == hashlib.sha256(source.read_bytes()).hexdigest()
    assert conversion.source_map_id == motion.scenario.source_scenario_id
    assert conversion.dataset_version == "1.1"
    assert conversion.centerline_point_count == 50
    assert (
        conversion.lane_segment_count,
        conversion.drivable_area_count,
        conversion.pedestrian_crossing_count,
    ) == (3, 1, 1)
    assert len(conversion.elements) == 11
    assert conversion.element_type_counts == (
        ("lane_centerline", 3),
        ("lane_boundary", 6),
        ("road_area", 1),
        ("crosswalk", 1),
        ("walkway", 0),
        ("junction_area", 0),
        ("access_zone", 0),
        ("other", 0),
    )
    assert [item.map_element_id for item in conversion.elements[:6]] == [
        "map:av2:fixture-scenario-001:lane:1:centerline",
        "map:av2:fixture-scenario-001:lane:1:left-boundary",
        "map:av2:fixture-scenario-001:lane:1:right-boundary",
        "map:av2:fixture-scenario-001:lane:2:centerline",
        "map:av2:fixture-scenario-001:lane:2:left-boundary",
        "map:av2:fixture-scenario-001:lane:2:right-boundary",
    ]
    centerline = conversion.elements[0]
    left = conversion.elements[1]
    assert centerline.directionality is Directionality.DIRECTED
    assert centerline.successor_ids == (
        "map:av2:fixture-scenario-001:lane:2:centerline",
    )
    assert centerline.right_neighbor_id == (
        "map:av2:fixture-scenario-001:lane:3:centerline"
    )
    assert left.parent_element_id == centerline.map_element_id
    assert left.directionality is Directionality.NOT_APPLICABLE
    centerline_geometry = geometry_from_canonical_wkb(centerline.geometry_wkb)
    left_geometry = geometry_from_canonical_wkb(left.geometry_wkb)
    assert len(centerline_geometry.coords) == 50
    assert next(iter(left_geometry.coords)) == (0.0, 1.0, 1.0)
    assert list(left_geometry.coords)[-1] == (20.0, 2.0, 1.3)
    assert next(iter(centerline_geometry.coords)) == (0.0, 0.0, 1.0)
    attributes = json.loads(centerline.semantic_attributes_json or "")
    assert attributes == {
        "centerline_point_count": 50,
        "is_intersection": False,
        "lane_type": "VEHICLE",
        "source_geometry_has_z": True,
        "source_kind": "lane_centerline",
        "source_lane_id": 1,
    }
    assert all(
        geometry_from_canonical_wkb(item.geometry_wkb).has_z
        for item in conversion.elements
    )
    assert all(item.quality_flags == () for item in conversion.elements)


def test_road_closure_crosswalk_order_and_repeated_equality(tmp_path: Path) -> None:
    root, source, motion, first = _conversion(tmp_path)
    second = av2_map.load_av2_vector_map(
        root,
        source.name,
        scenario=motion.scenario,
        coordinate_frame=motion.coordinate_frame,
    )
    assert first == second
    road = next(
        item for item in first.elements if item.element_type is MapElementType.ROAD_AREA
    )
    crossing = next(
        item for item in first.elements if item.element_type is MapElementType.CROSSWALK
    )
    road_coords = list(geometry_from_canonical_wkb(road.geometry_wkb).exterior.coords)
    crossing_coords = list(
        geometry_from_canonical_wkb(crossing.geometry_wkb).exterior.coords
    )
    assert road_coords[0] == road_coords[-1]
    assert crossing_coords == [
        (15.0, -2.0, 1.1),
        (15.0, 4.0, 1.2),
        (18.0, 4.0, 1.25),
        (18.0, -2.0, 1.15),
        (15.0, -2.0, 1.1),
    ]


def test_conversion_model_rejects_bad_references_and_counts(tmp_path: Path) -> None:
    _root, _source, _motion, conversion = _conversion(tmp_path)
    with pytest.raises(ValidationError, match="total"):
        replace(
            conversion,
            element_type_counts=tuple(
                (key, 0) for key, _count in conversion.element_type_counts
            ),
        )
    bad = replace(
        conversion.elements[0],
        successor_ids=("map:av2:fixture-scenario-001:lane:99:centerline",),
    )
    with pytest.raises(ValidationError, match="does not resolve"):
        replace(conversion, elements=(bad, *conversion.elements[1:]))


def test_summary_exact_plain_relative_and_deterministic(tmp_path: Path) -> None:
    _root, _source, _motion, conversion = _conversion(tmp_path)
    first = av2_map.av2_vector_map_conversion_summary_to_dict(conversion)
    second = av2_map.av2_vector_map_conversion_summary_to_dict(conversion)
    assert tuple(first) == SUMMARY_FIELDS
    assert first == second
    assert first["dataset_id"] == "av2_motion"
    assert first["dataset_version"] == "1.1"
    assert first["source_relative_path"] == MAP_FILENAME
    assert first["canonical_element_count"] == 11
    assert str(tmp_path.resolve()) not in json.dumps(first)
    assert "geometry_wkb" not in json.dumps(first)


def _materialized(
    tmp_path: Path,
    *,
    payload: dict[str, Any] | None = None,
    run_id: str = "run:av2:map",
) -> tuple[Path, Any, av2_map.Av2VectorMapConversion, Any]:
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    motion = _motion_conversion(repository / "motion")
    source_root = repository / "map"
    source = _write_map(source_root, payload)
    conversion = av2_map.load_av2_vector_map(
        source_root,
        source.name,
        scenario=motion.scenario,
        coordinate_frame=motion.coordinate_frame,
    )
    run = artifact_store.prepare_run_directory(
        repository.resolve(),
        "results",
        run_id,
        reserve_fraction=0,
    )
    artifacts = av2_map.materialize_av2_vector_map(
        run,
        conversion,
        row_group_size=2,
    )
    return repository, run, conversion, artifacts


def test_materialization_paths_rows_checksums_and_verification(
    tmp_path: Path,
) -> None:
    repository, run, conversion, artifacts = _materialized(tmp_path)
    prefix = run.path.relative_to(repository)
    assert artifacts.adapter_summary.relative_path.relative_to(prefix) == Path(
        "artifacts/av2_vector_map/adapter_summary.json"
    )
    assert artifacts.vector_map_elements.written_artifact.relative_path.relative_to(
        prefix
    ) == Path("artifacts/av2_vector_map/vector_map_elements.parquet")
    assert (
        artifacts.vector_map_elements.schema_name
        == CanonicalSchemaName.VECTOR_MAP_ELEMENTS
    )
    assert artifacts.vector_map_elements.row_count == 11
    assert artifacts.adapter_summary.size_bytes > 0
    assert artifacts.vector_map_elements.written_artifact.size_bytes > 0
    av2_map.verify_av2_vector_map_artifacts(
        repository,
        conversion,
        artifacts,
    )
    assert tuple(run.manifests_path.iterdir()) == ()


def test_materialization_rejects_overwrite_finalized_and_altered(
    tmp_path: Path,
) -> None:
    repository, run, conversion, artifacts = _materialized(tmp_path)
    with pytest.raises(ArtifactError, match="exists"):
        av2_map.materialize_av2_vector_map(run, conversion)
    summary_path = repository / artifacts.adapter_summary.relative_path
    changed = summary_path.read_bytes().replace(
        b'"canonical_element_count":11',
        b'"canonical_element_count":10',
    )
    summary_path.write_bytes(changed)
    updated = WrittenArtifact(
        artifacts.adapter_summary.relative_path,
        len(changed),
        hashlib.sha256(changed).hexdigest(),
    )
    with pytest.raises(SchemaError, match="differs"):
        av2_map.verify_av2_vector_map_artifacts(
            repository,
            conversion,
            replace(artifacts, adapter_summary=updated),
        )
    artifact_store.finalize_run_directory(run)
    with pytest.raises(ArtifactError, match="immutable"):
        av2_map.materialize_av2_vector_map(
            run,
            conversion,
            relative_directory="artifacts/another-map",
        )


def test_verification_rejects_corrupt_parquet(tmp_path: Path) -> None:
    repository, _run, conversion, artifacts = _materialized(tmp_path)
    path = repository / artifacts.vector_map_elements.written_artifact.relative_path
    path.write_bytes(b"corrupt")
    with pytest.raises(ArtifactError):
        av2_map.verify_av2_vector_map_artifacts(
            repository,
            conversion,
            artifacts,
        )


def test_deterministic_equivalent_artifact_checksums(tmp_path: Path) -> None:
    first = _materialized(tmp_path / "first")
    second = _materialized(tmp_path / "second")
    assert first[3].adapter_summary.content_checksum == (
        second[3].adapter_summary.content_checksum
    )
    assert (
        first[3].vector_map_elements.written_artifact.content_checksum
        == second[3].vector_map_elements.written_artifact.content_checksum
    )


@pytest.mark.parametrize("omit_crossings", (False, True))
def test_motion_map_complete_integration(
    tmp_path: Path,
    omit_crossings: bool,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    motion_source = repository / "motion"
    motion = _motion_conversion(motion_source)
    payload = _map_payload()
    if omit_crossings:
        payload.pop("pedestrian_crossings")
    map_root = repository / "map"
    map_source = _write_map(map_root, payload)
    motion_before = (motion_source / "scenario.parquet").read_bytes()
    map_before = map_source.read_bytes()
    conversion = av2_map.load_av2_vector_map(
        map_root,
        map_source.name,
        scenario=motion.scenario,
        coordinate_frame=motion.coordinate_frame,
    )
    assert conversion.source_map_id == motion.scenario.source_scenario_id
    assert conversion.scenario_id == motion.scenario.scenario_id
    assert conversion.coordinate_frame_id == motion.coordinate_frame.coordinate_frame_id
    run = artifact_store.prepare_run_directory(
        repository.resolve(),
        "results",
        f"run:integration:{omit_crossings}",
        reserve_fraction=0,
    )
    artifacts = av2_map.materialize_av2_vector_map(run, conversion, row_group_size=2)
    av2_map.verify_av2_vector_map_artifacts(
        repository,
        conversion,
        artifacts,
    )
    path = artifacts.vector_map_elements.written_artifact.relative_path
    batches = tuple(
        iter_canonical_parquet_batches(
            repository,
            (path,),
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
            batch_size=2,
        )
    )
    table = pa.Table.from_batches(
        batches,
        schema=get_arrow_schema(CanonicalSchemaName.VECTOR_MAP_ELEMENTS),
    )
    frame = scan_canonical_parquet(
        repository,
        (path,),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    ).collect()
    assert frame.columns == list(
        get_arrow_schema(CanonicalSchemaName.VECTOR_MAP_ELEMENTS).names
    )
    assert frame.to_dicts() == table.to_pylist()
    for value in table.column("geometry_wkb").to_pylist():
        geometry = geometry_from_canonical_wkb(value)
        assert geometry.is_valid and not geometry.is_empty
    ids = set(table.column("map_element_id").to_pylist())
    for element in conversion.elements:
        assert set(
            (
                *element.successor_ids,
                *element.predecessor_ids,
                *(
                    reference
                    for reference in (
                        element.parent_element_id,
                        element.left_neighbor_id,
                        element.right_neighbor_id,
                    )
                    if reference is not None
                ),
            )
        ).issubset(ids)
    assert (motion_source / "scenario.parquet").read_bytes() == motion_before
    assert map_source.read_bytes() == map_before
    artifact_store.finalize_run_directory(run)
    assert artifact_store.list_partial_artifacts(run) == ()


def test_adapter_import_surface_is_safe() -> None:
    source = Path(av2_map.__file__).read_text(encoding="utf-8")
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
            "fiona",
            "osgeo",
            "networkx",
            "torch",
            "rerun",
            "subprocess",
            "requests",
            "urllib",
        }
    )
    assert not any(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        for node in tree.body
    )
