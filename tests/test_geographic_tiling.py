"""Focused tests for deterministic geographic tiling."""

from dataclasses import FrozenInstanceError, fields, replace
import importlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from shapely.geometry import LineString, Point, Polygon  # type: ignore[import-untyped]

import kinematicweave.artifact_store as artifact_store_module
from kinematicweave.artifact_store import (
    WrittenArtifact,
    finalize_run_directory,
    prepare_run_directory,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.data import (
    av2_map,
    av2_motion,
    parquet_io,
    synthetic,
    tiling,
    validation,
)
from kinematicweave.data.av2_motion import (
    Av2MotionAdapterConfig,
    load_av2_motion_scenario,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    trajectories_to_table,
    vector_map_elements_to_table,
)
from kinematicweave.data.synthetic import (
    build_synthetic_dataset,
    materialize_synthetic_dataset,
    synthetic_agents,
    synthetic_coordinate_frames,
    synthetic_scenarios,
    synthetic_trajectories,
)
from kinematicweave.data.validation import (
    CanonicalDatasetPaths,
    CanonicalValidationConfig,
    canonical_validation_report_to_dict,
    validate_canonical_parquet_dataset,
    validate_canonical_tables,
)
import kinematicweave.domain.map_records as map_records_module
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
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AV2_MOTION_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "av2_motion" / "scenario_fixture.json"
)
AV2_MAP_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "av2_map"
    / "log_map_archive_fixture-scenario-001.json"
)


@pytest.fixture(scope="module", autouse=True)
def _refresh_modules_after_import_safety_tests() -> None:
    """Bind tiling tests to classes recreated by earlier reload checks."""
    importlib.reload(parquet_io)
    importlib.reload(synthetic)
    importlib.reload(av2_motion)
    importlib.reload(av2_map)
    importlib.reload(validation)
    importlib.reload(tiling)
    globals().update(
        {
            "AgentClass": records_module.AgentClass,
            "AgentRecord": records_module.AgentRecord,
            "CoordinateFrameRecord": records_module.CoordinateFrameRecord,
            "OriginType": records_module.OriginType,
            "ScenarioRecord": records_module.ScenarioRecord,
            "Trajectory": records_module.Trajectory,
            "TrajectorySampleRecord": records_module.TrajectorySampleRecord,
            "Directionality": map_records_module.Directionality,
            "MapElementType": map_records_module.MapElementType,
            "MapGeometryType": map_records_module.MapGeometryType,
            "VectorMapElementRecord": map_records_module.VectorMapElementRecord,
            "geometry_to_canonical_wkb": (map_records_module.geometry_to_canonical_wkb),
            "WrittenArtifact": artifact_store_module.WrittenArtifact,
            "finalize_run_directory": artifact_store_module.finalize_run_directory,
            "prepare_run_directory": artifact_store_module.prepare_run_directory,
            "agent_records_to_table": parquet_io.agent_records_to_table,
            "coordinate_frame_records_to_table": (
                parquet_io.coordinate_frame_records_to_table
            ),
            "scenario_records_to_table": parquet_io.scenario_records_to_table,
            "trajectories_to_table": parquet_io.trajectories_to_table,
            "vector_map_elements_to_table": (parquet_io.vector_map_elements_to_table),
            "CanonicalDatasetPaths": validation.CanonicalDatasetPaths,
            "CanonicalValidationConfig": validation.CanonicalValidationConfig,
            "canonical_validation_report_to_dict": (
                validation.canonical_validation_report_to_dict
            ),
            "validate_canonical_parquet_dataset": (
                validation.validate_canonical_parquet_dataset
            ),
            "validate_canonical_tables": validation.validate_canonical_tables,
            "build_synthetic_dataset": synthetic.build_synthetic_dataset,
            "materialize_synthetic_dataset": (synthetic.materialize_synthetic_dataset),
            "synthetic_agents": synthetic.synthetic_agents,
            "synthetic_coordinate_frames": synthetic.synthetic_coordinate_frames,
            "synthetic_scenarios": synthetic.synthetic_scenarios,
            "synthetic_trajectories": synthetic.synthetic_trajectories,
            "Av2MotionAdapterConfig": av2_motion.Av2MotionAdapterConfig,
            "load_av2_motion_scenario": av2_motion.load_av2_motion_scenario,
        }
    )


EXPECTED_PUBLIC_API = [
    "GeographicTile",
    "GeographicTileArtifacts",
    "GeographicTileCollection",
    "GeographicTileConfig",
    "GeographicTileCoverage",
    "GeographicTileMembership",
    "GeographicTileSpec",
    "TileCoordinateSpace",
    "build_geographic_tiles",
    "build_geographic_tiles_from_parquet",
    "candidate_tile_indices_for_bounds",
    "clip_map_element_to_tile",
    "clip_trajectory_to_tile",
    "geographic_tile_collection_from_dict",
    "geographic_tile_collection_from_json",
    "geographic_tile_collection_to_dict",
    "geographic_tile_config_to_dict",
    "geographic_tile_spec",
    "geographic_tile_summary_markdown",
    "geographic_tile_to_dict",
    "geographic_tiles_jsonl",
    "map_geometry_in_source_frame",
    "materialize_geographic_tiles",
    "tile_indices_for_point",
    "trajectory_geometry_in_source_frame",
    "verify_geographic_tile_artifacts",
]


def _scenario(*, source_map_available: bool = False) -> ScenarioRecord:
    return ScenarioRecord(
        scenario_id="scenario:test:001",
        dataset_id="test_data",
        dataset_version="1.0",
        split_name="development",
        city_or_region=None,
        source_scenario_id="001",
        start_time_ns=0,
        end_time_ns=4_000_000_000,
        coordinate_frame_id="frame:test:001",
        origin_x_m=100.0,
        origin_y_m=200.0,
        origin_z_m=5.0,
        source_crs="test_source",
        has_elevation=True,
        agent_count=1,
        source_map_available=source_map_available,
        quality_flags=(),
        adapter_name="test_adapter",
        adapter_version="1.0",
        source_checksum=None,
    )


def _frame() -> CoordinateFrameRecord:
    return CoordinateFrameRecord(
        scenario_id="scenario:test:001",
        coordinate_frame_id="frame:test:001",
        parent_frame_id=None,
        frame_type="local_cartesian",
        origin_x_m=100.0,
        origin_y_m=200.0,
        origin_z_m=5.0,
        axis_convention="right_handed_x_y_z_up",
        distance_unit="m",
        angle_unit="rad",
        timestamp_unit="ns",
        source_crs="test_source",
        has_elevation=True,
        transform_to_parent_4x4=None,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _sample(
    index: int,
    x_m: float,
    y_m: float,
    *,
    z_m: float | None = 1.0,
    is_valid: bool = True,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id="scenario:test:001",
        agent_id="agent:test:001",
        trajectory_id="trajectory:test:001",
        sample_index=index,
        timestamp_ns=index * 1_000_000_000,
        x_m=x_m,
        y_m=y_m,
        z_m=z_m,
        heading_rad=0.0,
        velocity_x_mps=1.0,
        velocity_y_mps=0.0,
        speed_mps=1.0,
        acceleration_x_mps2=None,
        acceleration_y_mps2=None,
        is_observed=True,
        is_valid=is_valid,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _trajectory(
    coordinates: tuple[tuple[float, float], ...] = (
        (-5.0, 1.0),
        (5.0, 1.0),
        (15.0, 1.0),
        (25.0, 1.0),
    ),
) -> Trajectory:
    return Trajectory(
        scenario_id="scenario:test:001",
        agent_id="agent:test:001",
        trajectory_id="trajectory:test:001",
        samples=tuple(
            _sample(index, x_m, y_m) for index, (x_m, y_m) in enumerate(coordinates)
        ),
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _agent(trajectory: Trajectory) -> AgentRecord:
    return AgentRecord(
        scenario_id=trajectory.scenario_id,
        agent_id=trajectory.agent_id,
        source_agent_id="001",
        agent_class=AgentClass.VEHICLE,
        length_m=4.0,
        width_m=2.0,
        height_m=1.5,
        first_time_ns=trajectory.start_time_ns,
        last_time_ns=trajectory.end_time_ns,
        sample_count=trajectory.sample_count,
        is_focal_agent=True,
        is_ego_agent=False,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _map_element(
    geometry: Any, geometry_type: MapGeometryType
) -> VectorMapElementRecord:
    return VectorMapElementRecord(
        scenario_id="scenario:test:001",
        map_element_id=f"map:test:{geometry_type.value}",
        element_type=(
            MapElementType.ROAD_AREA
            if geometry_type is MapGeometryType.POLYGON
            else MapElementType.LANE_CENTERLINE
        ),
        geometry_type=geometry_type,
        geometry_wkb=geometry_to_canonical_wkb(geometry),
        directionality=Directionality.NOT_APPLICABLE,
        parent_element_id=None,
        successor_ids=(),
        predecessor_ids=(),
        left_neighbor_id=None,
        right_neighbor_id=None,
        semantic_attributes_json=None,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _tables(
    *,
    with_map: bool = False,
) -> tuple[pa.Table, pa.Table, pa.Table, pa.Table, pa.Table | None, Any]:
    trajectory = _trajectory()
    scenario = _scenario(source_map_available=with_map)
    scenario_table = scenario_records_to_table((scenario,))
    frame_table = coordinate_frame_records_to_table((_frame(),))
    agent_table = agent_records_to_table((_agent(trajectory),))
    sample_table = trajectories_to_table((trajectory,))
    map_table = (
        vector_map_elements_to_table(
            (
                _map_element(
                    LineString([(-10.0, 0.0, 2.0), (30.0, 0.0, 2.0)]),
                    MapGeometryType.LINESTRING,
                ),
                _map_element(
                    Polygon([(0.0, -2.0), (10.0, -2.0), (10.0, 2.0), (0.0, 2.0)]),
                    MapGeometryType.POLYGON,
                ),
            )
        )
        if with_map
        else None
    )
    report = validate_canonical_tables(
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        map_table,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=2,
            minimum_valid_duration_ns=0,
            require_source_map=with_map,
        ),
    )
    return (
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        map_table,
        report,
    )


def _collection(*, with_map: bool = False) -> tiling.GeographicTileCollection:
    scenario_table, _frames, agents, samples, maps, report = _tables(with_map=with_map)
    return tiling.build_geographic_tiles(
        scenario_table,
        agents,
        samples,
        report,
        maps,
        config=tiling.GeographicTileConfig(
            tile_size_m=10.0,
            context_buffer_m=2.0,
            grid_origin_x_m=100.0,
            grid_origin_y_m=200.0,
        ),
    )


def test_exact_public_api_enum_and_fields() -> None:
    assert tiling.__all__ == EXPECTED_PUBLIC_API
    assert tuple(item.value for item in tiling.TileCoordinateSpace) == ("source_xy",)
    assert tuple(field.name for field in fields(tiling.GeographicTileConfig)) == (
        "tile_size_m",
        "context_buffer_m",
        "grid_origin_x_m",
        "grid_origin_y_m",
        "minimum_trajectory_length_m",
    )
    assert tuple(field.name for field in fields(tiling.GeographicTileArtifacts)) == (
        "tile_index",
        "tile_manifests",
        "tile_summary",
    )


def test_records_are_frozen_and_slotted() -> None:
    config = tiling.GeographicTileConfig()
    assert hasattr(type(config), "__slots__")
    with pytest.raises(FrozenInstanceError):
        config.tile_size_m = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("tile_size_m", 0),
        ("tile_size_m", -1),
        ("tile_size_m", True),
        ("context_buffer_m", -1),
        ("context_buffer_m", 100),
        ("context_buffer_m", False),
        ("grid_origin_x_m", math.nan),
        ("grid_origin_y_m", math.inf),
        ("minimum_trajectory_length_m", -1),
    ),
)
def test_config_rejects_invalid_values(field_name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        tiling.GeographicTileConfig(**cast(Any, {field_name: value}))


def test_grid_indices_boundaries_negatives_and_origin() -> None:
    config = tiling.GeographicTileConfig(
        tile_size_m=10,
        context_buffer_m=2,
        grid_origin_x_m=5,
        grid_origin_y_m=-5,
    )
    assert tiling.tile_indices_for_point(5, -5, config=config) == (0, 0)
    assert tiling.tile_indices_for_point(14.999, 4.999, config=config) == (0, 0)
    assert tiling.tile_indices_for_point(15, 5, config=config) == (1, 1)
    assert tiling.tile_indices_for_point(-5.001, -15.001, config=config) == (-2, -2)


def test_candidate_bounds_include_touching_and_context_tiles() -> None:
    config = tiling.GeographicTileConfig(
        tile_size_m=10,
        context_buffer_m=2,
    )
    assert tiling.candidate_tile_indices_for_bounds(10, 5, 10, 5, config=config) == (
        (0, 0),
        (1, 0),
    )
    assert tiling.candidate_tile_indices_for_bounds(
        2, 2, 2, 2, config=config, include_context=True
    ) == ((-1, -1), (-1, 0), (0, -1), (0, 0))


def test_stable_tile_identity_excludes_buffer() -> None:
    first = tiling.geographic_tile_spec(
        "dataset",
        "1.0",
        -1,
        2,
        config=tiling.GeographicTileConfig(context_buffer_m=1),
    )
    second = tiling.geographic_tile_spec(
        "dataset",
        "1.0",
        -1,
        2,
        config=tiling.GeographicTileConfig(context_buffer_m=20),
    )
    assert first.tile_id == second.tile_id
    assert first.tile_id.startswith("tile:dataset:")
    changed = tiling.geographic_tile_spec(
        "dataset",
        "1.0",
        -1,
        2,
        config=tiling.GeographicTileConfig(tile_size_m=50),
    )
    assert changed.tile_id != first.tile_id


def test_trajectory_translation_run_splitting_and_mixed_z() -> None:
    trajectory = Trajectory(
        scenario_id="scenario:test:001",
        agent_id="agent:test:001",
        trajectory_id="trajectory:test:001",
        samples=(
            _sample(0, 0, 0, z_m=1),
            _sample(1, 1, 0, z_m=2),
            _sample(2, 2, 0, is_valid=False),
            _sample(3, 3, 0, z_m=3),
            _sample(4, 4, 0, z_m=None),
        ),
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )
    geometry = tiling.trajectory_geometry_in_source_frame(_scenario(), trajectory)
    assert geometry is not None
    assert geometry.geom_type == "MultiLineString"
    first, second = geometry.geoms
    assert first.has_z
    assert not second.has_z
    assert next(iter(first.coords)) == (100.0, 200.0, 1.0)
    assert first.length == pytest.approx(1.0)
    assert second.length == pytest.approx(1.0)


def test_stationary_and_point_contact_do_not_create_motion() -> None:
    stationary = _trajectory(((0, 0), (0, 0)))
    assert tiling.trajectory_geometry_in_source_frame(_scenario(), stationary) is None
    touching = _trajectory(((-1, 0), (0, 0)))
    tile = tiling.geographic_tile_spec(
        "test_data",
        "1.0",
        1,
        0,
        config=tiling.GeographicTileConfig(
            tile_size_m=10,
            context_buffer_m=2,
            grid_origin_x_m=90,
            grid_origin_y_m=200,
        ),
    )
    assert tiling.clip_trajectory_to_tile(_scenario(), touching, tile) is None
    assert (
        tiling.clip_trajectory_to_tile(_scenario(), touching, tile, use_context=True)
        is not None
    )


def test_map_translation_preserves_z_and_clips_dimensions() -> None:
    line = _map_element(
        LineString([(0, 0, 7), (20, 0, 8)]),
        MapGeometryType.LINESTRING,
    )
    translated = tiling.map_geometry_in_source_frame(_scenario(), line)
    assert translated.has_z
    assert next(iter(translated.coords)) == (100.0, 200.0, 7.0)
    tile = tiling.geographic_tile_spec(
        "test_data",
        "1.0",
        0,
        0,
        config=tiling.GeographicTileConfig(
            tile_size_m=10,
            context_buffer_m=2,
            grid_origin_x_m=100,
            grid_origin_y_m=200,
        ),
    )
    clipped = tiling.clip_map_element_to_tile(_scenario(), line, tile)
    assert clipped is not None
    assert clipped.geom_type == "LineString"
    assert clipped.length == pytest.approx(10.0)
    point = _map_element(Point(30, 30), MapGeometryType.POINT)
    assert tiling.clip_map_element_to_tile(_scenario(), point, tile) is None


def test_aggregation_membership_coverage_order_and_identity() -> None:
    collection = _collection(with_map=True)
    assert collection.tile_count == 4
    assert [tile.spec.grid_x for tile in collection.tiles] == [-1, 0, 1, 2]
    assert (
        sum(tile.coverage.interior_valid_sample_count for tile in collection.tiles) == 4
    )
    assert collection.total_interior_trajectory_count == 4
    assert collection.total_context_trajectory_count >= 4
    assert any(tile.membership.interior_map_element_ids for tile in collection.tiles)
    assert sum(
        tile.coverage.interior_trajectory_length_m for tile in collection.tiles
    ) == pytest.approx(30.0)
    expected_identity = canonical_sha256(
        "canonical-validation-report",
        canonical_validation_report_to_dict(_tables(with_map=True)[-1]),
    )
    assert collection.validation_report_identity == expected_identity
    for tile in collection.tiles:
        assert set(tile.membership.interior_trajectory_ids).issubset(
            tile.membership.context_trajectory_ids
        )


def test_context_only_trajectory_membership_occurs_at_buffer_boundary() -> None:
    first = _trajectory(((1, 1), (5, 1)))
    second = Trajectory(
        scenario_id=first.scenario_id,
        agent_id="agent:test:002",
        trajectory_id="trajectory:test:002",
        samples=tuple(
            replace(
                _sample(index, x_m, 1),
                agent_id="agent:test:002",
                trajectory_id="trajectory:test:002",
            )
            for index, x_m in enumerate((11.0, 15.0))
        ),
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )
    first_agent = _agent(first)
    second_agent = replace(
        _agent(second),
        source_agent_id="002",
        is_focal_agent=False,
    )
    scenario_table = scenario_records_to_table((replace(_scenario(), agent_count=2),))
    frame_table = coordinate_frame_records_to_table((_frame(),))
    agent_table = agent_records_to_table((first_agent, second_agent))
    sample_table = trajectories_to_table((first, second))
    report = validate_canonical_tables(
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=2,
            minimum_valid_duration_ns=0,
        ),
    )
    collection = tiling.build_geographic_tiles(
        scenario_table,
        agent_table,
        sample_table,
        report,
        config=tiling.GeographicTileConfig(
            tile_size_m=10,
            context_buffer_m=2,
            grid_origin_x_m=100,
            grid_origin_y_m=200,
        ),
    )
    first_tile = next(tile for tile in collection.tiles if tile.spec.grid_x == 0)
    assert first_tile.membership.interior_trajectory_ids == (first.trajectory_id,)
    assert first_tile.membership.context_trajectory_ids == (
        first.trajectory_id,
        second.trajectory_id,
    )


def test_minimum_length_is_strict() -> None:
    scenario_table, _frames, agents, samples, maps, report = _tables()
    with pytest.raises(SchemaError, match="does not create"):
        tiling.build_geographic_tiles(
            scenario_table,
            agents,
            samples,
            report,
            maps,
            config=tiling.GeographicTileConfig(minimum_trajectory_length_m=30.0),
        )


def test_serialization_exact_order_round_trip_and_failures() -> None:
    collection = _collection(with_map=True)
    value = tiling.geographic_tile_collection_to_dict(collection)
    assert tuple(value) == (
        "schema_version",
        "dataset_id",
        "dataset_version",
        "config",
        "validation_report_identity",
        "tile_count",
        "total_interior_trajectory_count",
        "total_context_trajectory_count",
        "tile_ids",
        "tiles",
    )
    tile = cast(list[dict[str, object]], value["tiles"])[0]
    assert tuple(tile) == (
        "tile_id",
        "coordinate_space",
        "grid_x",
        "grid_y",
        "tile_size_m",
        "context_buffer_m",
        "interior_bounds",
        "context_bounds",
        "membership",
        "coverage",
    )
    text = canonical_json_text(value)
    assert tiling.geographic_tile_collection_from_json(text) == collection
    assert tiling.geographic_tiles_jsonl(collection).endswith("\n")
    assert "\n\n" not in tiling.geographic_tiles_jsonl(collection)
    summary = tiling.geographic_tile_summary_markdown(collection)
    assert summary.endswith("\n") and not summary.endswith("\n\n")
    assert "Geographic Tile Summary" in summary
    with pytest.raises(SchemaError):
        tiling.geographic_tile_collection_from_json("[]")
    with pytest.raises(SchemaError):
        tiling.geographic_tile_collection_from_json("{")
    broken = dict(value)
    broken["unknown"] = 1
    with pytest.raises(SchemaError):
        tiling.geographic_tile_collection_from_dict(broken)


def _write_tables(root: Path) -> tuple[CanonicalDatasetPaths, Any]:
    scenario_table, frame_table, agents, samples, maps, report = _tables(with_map=True)
    directory = root / "canonical"
    directory.mkdir()
    tables = {
        "scenario.parquet": scenario_table,
        "frames.parquet": frame_table,
        "agents.parquet": agents,
        "samples-a.parquet": samples.slice(0, 2),
        "samples-b.parquet": samples.slice(2),
        "maps-a.parquet": cast(pa.Table, maps).slice(0, 1),
        "maps-b.parquet": cast(pa.Table, maps).slice(1),
    }
    for name, table in tables.items():
        pq.write_table(table, directory / name, row_group_size=1)
    return (
        CanonicalDatasetPaths(
            scenario_manifest=("canonical/scenario.parquet",),
            coordinate_frame_metadata=("canonical/frames.parquet",),
            agent_metadata=("canonical/agents.parquet",),
            trajectory_samples=(
                "canonical/samples-a.parquet",
                "canonical/samples-b.parquet",
            ),
            vector_map_elements=(
                "canonical/maps-a.parquet",
                "canonical/maps-b.parquet",
            ),
        ),
        report,
    )


def test_bounded_parquet_equals_in_memory_across_files(tmp_path: Path) -> None:
    paths, report = _write_tables(tmp_path)
    config = tiling.GeographicTileConfig(
        tile_size_m=10,
        context_buffer_m=2,
        grid_origin_x_m=100,
        grid_origin_y_m=200,
    )
    bounded = tiling.build_geographic_tiles_from_parquet(
        tmp_path,
        paths,
        report,
        config=config,
        batch_size=1,
    )
    assert bounded == _collection(with_map=True)
    with pytest.raises(ValidationError):
        tiling.build_geographic_tiles_from_parquet(
            tmp_path,
            paths,
            report,
            config=config,
            batch_size=False,
        )


def test_artifact_materialization_verification_and_overwrite(
    tmp_path: Path,
) -> None:
    collection = _collection(with_map=True)
    run = prepare_run_directory(
        tmp_path,
        "results",
        "run:tiling:001",
        reserve_fraction=0.0,
    )
    artifacts = tiling.materialize_geographic_tiles(run, collection)
    assert tuple(
        artifact.relative_path.name
        for artifact in (
            artifacts.tile_index,
            artifacts.tile_manifests,
            artifacts.tile_summary,
        )
    ) == (
        "tile_index.json",
        "tile_manifests.jsonl",
        "tile_summary.md",
    )
    assert tiling.verify_geographic_tile_artifacts(tmp_path, artifacts) == collection
    with pytest.raises(ArtifactError):
        tiling.materialize_geographic_tiles(run, collection)
    finalize_run_directory(run)
    with pytest.raises(ArtifactError):
        tiling.materialize_geographic_tiles(run, collection)


def test_artifact_tamper_is_detected(tmp_path: Path) -> None:
    collection = _collection()
    run = prepare_run_directory(
        tmp_path,
        "results",
        "run:tiling:002",
        reserve_fraction=0.0,
    )
    artifacts = tiling.materialize_geographic_tiles(run, collection)
    path = tmp_path / artifacts.tile_summary.relative_path
    path.write_text("altered\n", encoding="utf-8")
    with pytest.raises(ArtifactError):
        tiling.verify_geographic_tile_artifacts(tmp_path, artifacts)
    altered = replace(
        artifacts,
        tile_summary=WrittenArtifact(
            relative_path=artifacts.tile_summary.relative_path,
            size_bytes=8,
            content_checksum=canonical_sha256("not-file-bytes", {}),
        ),
    )
    with pytest.raises(ArtifactError):
        tiling.verify_geographic_tile_artifacts(tmp_path, altered)


def test_membership_coverage_and_collection_invariants() -> None:
    with pytest.raises(ValidationError):
        tiling.GeographicTileMembership(
            interior_scenario_ids=("scenario:test:001",),
            interior_agent_ids=(),
            interior_trajectory_ids=(),
            context_scenario_ids=(),
            context_agent_ids=(),
            context_trajectory_ids=(),
            interior_map_element_ids=(),
            context_map_element_ids=(),
        )
    with pytest.raises(ValidationError):
        tiling.GeographicTileCoverage(
            interior_valid_sample_count=2,
            context_valid_sample_count=1,
            interior_trajectory_length_m=0,
            context_trajectory_length_m=0,
            interior_map_length_m=0,
            context_map_length_m=0,
            interior_map_area_m2=0,
            context_map_area_m2=0,
        )
    collection = _collection()
    with pytest.raises(ValidationError):
        replace(collection, tiles=(collection.tiles[0], collection.tiles[0]))


def test_complete_synthetic_dataset_integration_is_deterministic(
    tmp_path: Path,
) -> None:
    dataset = build_synthetic_dataset()
    scenario_table = scenario_records_to_table(synthetic_scenarios(dataset))
    frame_table = coordinate_frame_records_to_table(
        synthetic_coordinate_frames(dataset)
    )
    agent_table = agent_records_to_table(synthetic_agents(dataset))
    sample_table = trajectories_to_table(synthetic_trajectories(dataset))
    validation_config = CanonicalValidationConfig(
        minimum_valid_sample_count=2,
        minimum_valid_duration_ns=0,
    )
    report = validate_canonical_tables(
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        config=validation_config,
    )
    config = tiling.GeographicTileConfig(
        tile_size_m=5,
        context_buffer_m=1,
    )
    in_memory = tiling.build_geographic_tiles(
        scenario_table,
        agent_table,
        sample_table,
        report,
        config=config,
    )
    assert in_memory.tile_count > 1
    assert len({tile.spec.tile_id for tile in in_memory.tiles}) == (
        in_memory.tile_count
    )
    assert in_memory.total_context_trajectory_count > len(
        report.included_trajectory_ids
    )

    first_run = prepare_run_directory(
        tmp_path,
        "results",
        "run:synthetic:tiling:001",
        reserve_fraction=0.0,
    )
    dataset_artifacts = materialize_synthetic_dataset(
        first_run,
        dataset,
        row_group_size=3,
    )
    paths = CanonicalDatasetPaths(
        scenario_manifest=(
            dataset_artifacts.scenario_manifest.written_artifact.relative_path,
        ),
        coordinate_frame_metadata=(
            dataset_artifacts.coordinate_frame_metadata.written_artifact.relative_path,
        ),
        agent_metadata=(
            dataset_artifacts.agent_metadata.written_artifact.relative_path,
        ),
        trajectory_samples=(
            dataset_artifacts.trajectory_samples.written_artifact.relative_path,
        ),
        vector_map_elements=(),
    )
    bounded_report = validate_canonical_parquet_dataset(
        tmp_path,
        paths,
        config=validation_config,
        batch_size=3,
    )
    bounded = tiling.build_geographic_tiles_from_parquet(
        tmp_path,
        paths,
        bounded_report,
        config=config,
        batch_size=2,
    )
    assert bounded == in_memory
    first_artifacts = tiling.materialize_geographic_tiles(first_run, bounded)
    assert tiling.verify_geographic_tile_artifacts(tmp_path, first_artifacts) == bounded
    finalize_run_directory(first_run)

    second_run = prepare_run_directory(
        tmp_path,
        "results",
        "run:synthetic:tiling:002",
        reserve_fraction=0.0,
    )
    second_artifacts = tiling.materialize_geographic_tiles(second_run, in_memory)
    assert (
        first_artifacts.tile_index.content_checksum
        == second_artifacts.tile_index.content_checksum
    )
    assert (
        first_artifacts.tile_manifests.content_checksum
        == second_artifacts.tile_manifests.content_checksum
    )
    assert (
        first_artifacts.tile_summary.content_checksum
        == second_artifacts.tile_summary.content_checksum
    )
    finalize_run_directory(second_run)


def _av2_motion_rows() -> list[dict[str, object]]:
    payload = cast(
        dict[str, object],
        json.loads(AV2_MOTION_FIXTURE.read_text(encoding="utf-8")),
    )
    scenario_fields = (
        "scenario_id",
        "start_timestamp",
        "end_timestamp",
        "num_timestamps",
        "focal_track_id",
        "city",
    )
    return [
        {
            **row,
            **{field: payload[field] for field in scenario_fields},
        }
        for row in cast(list[dict[str, object]], payload["rows"])
    ]


def test_av2_motion_map_source_frame_integration(tmp_path: Path) -> None:
    motion_root = tmp_path / "motion"
    motion_root.mkdir()
    pq.write_table(
        pa.Table.from_pylist(_av2_motion_rows()),
        motion_root / "scenario.parquet",
    )
    motion = load_av2_motion_scenario(
        motion_root,
        "scenario.parquet",
        config=Av2MotionAdapterConfig(
            dataset_version="1.1",
            split_name="train",
            source_map_available=True,
        ),
    )
    map_root = tmp_path / "map"
    map_root.mkdir()
    map_source = map_root / AV2_MAP_FIXTURE.name
    map_source.write_bytes(AV2_MAP_FIXTURE.read_bytes())
    map_conversion = av2_map.load_av2_vector_map(
        map_root,
        map_source.name,
        scenario=motion.scenario,
        coordinate_frame=motion.coordinate_frame,
        config=av2_map.Av2VectorMapAdapterConfig(centerline_point_count=20),
    )
    scenario_table = scenario_records_to_table((motion.scenario,))
    frame_table = coordinate_frame_records_to_table((motion.coordinate_frame,))
    agent_table = agent_records_to_table(motion.agents)
    sample_table = trajectories_to_table(motion.trajectories)
    map_table = vector_map_elements_to_table(map_conversion.elements)
    report = validate_canonical_tables(
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        map_table,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=2,
            minimum_valid_duration_ns=0,
            require_source_map=True,
        ),
    )
    collection = tiling.build_geographic_tiles(
        scenario_table,
        agent_table,
        sample_table,
        report,
        map_table,
        config=tiling.GeographicTileConfig(
            tile_size_m=20,
            context_buffer_m=5,
        ),
    )
    assert any(
        tile.membership.interior_trajectory_ids
        and tile.membership.context_map_element_ids
        for tile in collection.tiles
    )
    z_elements = [
        element
        for element in map_conversion.elements
        if tiling.map_geometry_in_source_frame(motion.scenario, element).has_z
    ]
    assert z_elements
    run = prepare_run_directory(
        tmp_path,
        "results",
        "run:av2:tiling:001",
        reserve_fraction=0.0,
    )
    artifacts = tiling.materialize_geographic_tiles(run, collection)
    assert tiling.verify_geographic_tile_artifacts(tmp_path, artifacts) == collection
    finalize_run_directory(run)
