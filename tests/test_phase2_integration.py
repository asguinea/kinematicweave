"""Repository-wide integration tests for the completed Phase 2 data foundation."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, cast

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from shapely import get_coordinates  # type: ignore[import-untyped]

from kinematicweave import artifact_store
from kinematicweave.data import (
    av2_map,
    av2_motion,
    parquet_io,
    pilot,
    registry,
    schemas,
    splits,
    synthetic,
    tiling,
    validation,
)
import kinematicweave.data.materialization as materialization
from kinematicweave.data.materialization import (
    scan_materialization_cache,
)
from kinematicweave.domain import map_records, records

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOTION_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "av2_motion" / "scenario_fixture.json"
)
MAP_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "av2_map"
    / "log_map_archive_fixture-scenario-001.json"
)
PILOT_CACHE_ROOT = Path("data/cache/av2-pilot")
PILOT_OUTPUTS = (
    Path("motion_adapter_summary.json"),
    Path("map_adapter_summary.json"),
    Path("scenario_manifest.parquet"),
    Path("coordinate_frame_metadata.parquet"),
    Path("agent_metadata.parquet"),
    Path("trajectory_samples.parquet"),
    Path("vector_map_elements.parquet"),
)
DATA_COMMANDS = {
    "registry",
    "source-discover",
    "source-verify",
    "synthetic-check",
    "validate",
    "cache-scan",
    "cache-prune",
}
SCHEMA_FINGERPRINTS = {
    "scenario_manifest": (
        "e59b221b1bf720832d90a19340f773c31b461292323d6ddb0a12fb8096d7d03b"
    ),
    "coordinate_frame_metadata": (
        "ab8668ac6775de47623281bbe178e88202c0715cbb964057df7ece53c4b2f2ac"
    ),
    "agent_metadata": (
        "7527dd3653e46f82ac835c81150c57677cd23c3a4eba2a705bd6a2dde3c0ab2b"
    ),
    "trajectory_samples": (
        "24433aa9c49be2fc95be4fd6f8a30ad163cf1a2a6e116d42b7960be2a2714cfd"
    ),
    "vector_map_elements": (
        "5f837f27a693002d9c43b9e9101d999a61f0ab53aa4a402c9bc1ce0d79ed0998"
    ),
}
PROVIDER_EVIDENCE_ROOT = PROJECT_ROOT / "results" / "phase2" / "av2_provider_pilot"
PROVIDER_EVIDENCE_FILES = {
    "acquisition_plan.json",
    "acquisition_report.json",
    "source_manifest.json",
    "pilot_plan.json",
    "pilot_report_first_run.json",
    "pilot_report_reuse_run.json",
    "validation_report.json",
    "evidence.json",
    "summary.md",
}
BATCH_COMMITS = {
    "2.1": "439da08474da03105572d0beb5e730a58d574b2f",
    "2.2": "387454c9ec5a6db8a1e2e240f92840ba53db76ae",
    "2.3": "e3e202d427772ab5ed304f4ed9e4c7eb5d91bb1b",
    "2.4": "dedff082dbca5b7daed2e0c3b33cbd1b2bd67110",
    "2.5": "e0d72d4f49e71c3696af75e2d8988881c975a817",
    "2.6": "a644fedd5c16b21f4b1a178d22aa267300a78a43",
    "2.7": "de68b2af56864c7fcc90a429da4e72ff47b1518a",
    "2.8": "5ae8c3e7cf44aac659076bb6b7fe3b33361d7aef",
    "2.9": "2a0713700dedf10f1ca0fc9634f9b2f0372eea5b",
    "2.10": "56c4f4d2043449bf41029af8dccefb410a988ba0",
    "2.11": "6e4859071819ba45c3e665be3a099b3bf6330663",
    "2.12": "ab2ada78c3a3d377194d09a869f3a2f3a6ab40df",
    "2.13": "71e149ddba289763c042d72a491e452bb70bca1a",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_genuine_av2_provider_evidence_contract() -> None:
    actual = {path.name for path in PROVIDER_EVIDENCE_ROOT.iterdir() if path.is_file()}
    assert actual == PROVIDER_EVIDENCE_FILES
    evidence = json.loads(
        (PROVIDER_EVIDENCE_ROOT / "evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["official_remote_root"] == (
        "s3://argoverse/datasets/av2/motion-forecasting/"
    )
    assert evidence["provider_partition"] == "val"
    assert evidence["milestone_decision"] == "achieved"
    selected = evidence["selected_scenario_ids"]
    assert len(selected) == len(set(selected)) == 10
    assert evidence["acquisition_measurements"]["selected_scenario_count"] == 10
    assert evidence["first_run_pilot_measurements"]["materialized_scenario_count"] == 10
    assert evidence["first_run_pilot_measurements"]["reused_scenario_count"] == 0
    assert evidence["second_run_reuse_measurements"]["materialized_scenario_count"] == 0
    assert evidence["second_run_reuse_measurements"]["reused_scenario_count"] == 10
    assert all(value > 0 for value in evidence["source_counts"].values())
    assert all(value > 0 for value in evidence["included_counts"].values())
    assert evidence["exclusions"]["count"] == 10
    assert evidence["resources"]["peak_process_memory_bytes"] > 0
    assert evidence["resources"]["cpu_only"] is True
    assert evidence["resources"]["gpu_use_count"] == 0
    for measurements in (
        evidence["acquisition_measurements"],
        evidence["first_run_pilot_measurements"],
        evidence["second_run_reuse_measurements"],
    ):
        assert all(
            value >= 0
            for key, value in measurements.items()
            if key.endswith("_seconds")
        )
    assert evidence["provider_timestamp_physical_schema"] == {
        "end_timestamp": "double",
        "num_timestamps": "int64",
        "start_timestamp": "double",
    }
    map_handling = evidence["provider_map_external_reference_handling"]
    assert map_handling["total_omitted_external_lane_reference_count"] == 158
    assert map_handling["canonical_topology_references_are_local"] is True
    assert map_handling["fabricated_lane_nodes"] == 0
    assert [item["scenario_id"] for item in map_handling["scenario_counts"]] == selected
    checksums = evidence["evidence_file_sha256"]
    assert set(checksums) == PROVIDER_EVIDENCE_FILES - {"evidence.json"}
    assert all(
        _digest(PROVIDER_EVIDENCE_ROOT / name) == checksum
        for name, checksum in checksums.items()
    )
    summary = (PROVIDER_EVIDENCE_ROOT / "summary.md").read_text(encoding="utf-8")
    assert "M2 is achieved with genuine AV2 provider-data evidence." in summary
    assert "fixture" not in evidence["official_remote_root"]


def _file_snapshot(root: Path) -> tuple[tuple[str, int, str], ...]:
    if not root.exists():
        return ()
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_size,
            _digest(path),
        )
        for path in sorted(
            (candidate for candidate in root.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        )
    )


def _project_generated_snapshot() -> tuple[
    tuple[str, tuple[tuple[str, int, str], ...]], ...
]:
    return tuple(
        (name, _file_snapshot(PROJECT_ROOT / name))
        for name in ("data", "results", "cache")
    )


@pytest.fixture(autouse=True)
def _real_project_generated_paths_remain_unchanged() -> Iterator[None]:
    before = _project_generated_snapshot()
    yield
    assert _project_generated_snapshot() == before


@pytest.fixture(scope="module", autouse=True)
def _refresh_phase2_consumers_after_import_safety_tests() -> None:
    """Bind integration consumers to classes recreated by reload tests."""
    importlib.reload(parquet_io)
    importlib.reload(synthetic)
    importlib.reload(av2_motion)
    importlib.reload(av2_map)
    importlib.reload(validation)
    importlib.reload(tiling)
    importlib.reload(splits)
    importlib.reload(pilot)


def _no_partial_state(repository: Path) -> None:
    assert not tuple(
        path
        for path in repository.rglob("*")
        if ".partial" in path.name or (".temporary" in path.parts and path.is_file())
    )


def _canonical_paths(
    artifacts: synthetic.SyntheticDatasetArtifacts,
) -> validation.CanonicalDatasetPaths:
    return validation.CanonicalDatasetPaths(
        scenario_manifest=(artifacts.scenario_manifest.written_artifact.relative_path,),
        coordinate_frame_metadata=(
            artifacts.coordinate_frame_metadata.written_artifact.relative_path,
        ),
        agent_metadata=(artifacts.agent_metadata.written_artifact.relative_path,),
        trajectory_samples=(
            artifacts.trajectory_samples.written_artifact.relative_path,
        ),
        vector_map_elements=(),
    )


def _expected_polars_type(arrow_type: pa.DataType) -> pl.DataType:
    mapping = (
        (pa.string(), pl.String()),
        (pa.bool_(), pl.Boolean()),
        (pa.int32(), pl.Int32()),
        (pa.int64(), pl.Int64()),
        (pa.float32(), pl.Float32()),
        (pa.float64(), pl.Float64()),
        (pa.binary(), pl.Binary()),
        (pa.list_(pa.string()), pl.List(pl.String)),
        (pa.list_(pa.float64()), pl.List(pl.Float64)),
    )
    return next(
        polars_type
        for expected_arrow, polars_type in mapping
        if arrow_type.equals(expected_arrow)
    )


def _split_config(namespace: str) -> splits.SplitGenerationConfig:
    return splits.SplitGenerationConfig(
        root_seed=0,
        assignment_namespace=namespace,
        smoke_count=0,
        development_count=0,
        pilot_count=0,
        test_count=None,
        held_out_city=None,
    )


def _all_units_are_test(manifest: splits.CanonicalSplitManifest) -> None:
    for membership in manifest.memberships:
        if membership.split_name is splits.SplitName.TEST:
            assert len(membership.unit_ids) == manifest.source_unit_count
        else:
            assert membership.unit_ids == ()
            assert membership.leakage_group_ids == ()


@dataclass(frozen=True, slots=True)
class _SyntheticEvidence:
    canonical_checksums: tuple[str, ...]
    validation_json_checksum: str
    tile_checksums: tuple[str, str]
    split_checksums: tuple[str, str]
    tile_count: int


def _run_synthetic_workflow(repository: Path) -> _SyntheticEvidence:
    dataset = synthetic.build_synthetic_dataset()
    scenarios = synthetic.synthetic_scenarios(dataset)
    frames = synthetic.synthetic_coordinate_frames(dataset)
    agents = synthetic.synthetic_agents(dataset)
    trajectories = synthetic.synthetic_trajectories(dataset)
    assert (
        len(scenarios),
        len(frames),
        len(agents),
        len(trajectories),
        sum(trajectory.sample_count for trajectory in trajectories),
    ) == (16, 16, 27, 27, 293)
    for item in dataset.scenarios:
        records.validate_scenario_bundle(
            item.scenario,
            item.coordinate_frame,
            item.agents,
            item.trajectories,
        )

    scenario_table = parquet_io.scenario_records_to_table(scenarios)
    agent_table = parquet_io.agent_records_to_table(agents)
    sample_table = parquet_io.trajectories_to_table(trajectories)
    assert sample_table.column("is_valid").to_pylist().count(False) == 2
    assert sample_table.num_rows == 293

    run = artifact_store.prepare_run_directory(
        repository,
        "results",
        "run:phase2:synthetic",
        reserve_fraction=0.0,
    )
    dataset_artifacts = synthetic.materialize_synthetic_dataset(
        run,
        dataset,
        row_group_size=3,
    )
    synthetic.verify_synthetic_dataset_artifacts(repository, dataset_artifacts)
    paths = _canonical_paths(dataset_artifacts)

    parquet_artifacts = (
        dataset_artifacts.scenario_manifest,
        dataset_artifacts.coordinate_frame_metadata,
        dataset_artifacts.agent_metadata,
        dataset_artifacts.trajectory_samples,
    )
    for canonical_artifact in parquet_artifacts:
        batches = tuple(
            parquet_io.iter_canonical_parquet_batches(
                repository,
                (canonical_artifact.written_artifact.relative_path,),
                canonical_artifact.schema_name,
                batch_size=3,
            )
        )
        assert sum(batch.num_rows for batch in batches) == canonical_artifact.row_count
        assert all(batch.num_rows <= 3 for batch in batches)

    validation_config = validation.CanonicalValidationConfig(
        minimum_valid_sample_count=1,
        minimum_valid_duration_ns=0,
        allowed_agent_classes=tuple(records.AgentClass),
        require_source_map=False,
    )
    report, validation_artifacts = (
        validation.validate_and_materialize_canonical_parquet_dataset(
            run,
            paths,
            config=validation_config,
            batch_size=3,
        )
    )
    assert report.is_eligible
    assert report.exclusions == ()
    assert (
        report.source_scenario_count,
        report.source_coordinate_frame_count,
        report.source_agent_count,
        report.source_trajectory_count,
        report.source_sample_count,
    ) == (16, 16, 27, 27, 293)
    assert (
        validation.verify_canonical_validation_artifacts(
            repository,
            validation_artifacts,
        )
        == report
    )

    tile_config = tiling.GeographicTileConfig(
        tile_size_m=5.0,
        context_buffer_m=1.0,
    )
    in_memory_tiles = tiling.build_geographic_tiles(
        scenario_table,
        agent_table,
        sample_table,
        report,
        config=tile_config,
    )
    bounded_tiles = tiling.build_geographic_tiles_from_parquet(
        repository,
        paths,
        report,
        config=tile_config,
        batch_size=2,
    )
    assert bounded_tiles == in_memory_tiles
    assert bounded_tiles.tile_count > 1
    tile_artifacts = tiling.materialize_geographic_tiles(run, bounded_tiles)
    assert (
        tiling.verify_geographic_tile_artifacts(repository, tile_artifacts)
        == bounded_tiles
    )

    motion_manifest = splits.generate_motion_split_manifest(
        scenario_table,
        report,
        config=_split_config("phase2-synthetic-motion"),
    )
    layout_manifest = splits.generate_layout_split_manifest(
        bounded_tiles,
        scenario_table,
        report,
        config=_split_config("phase2-synthetic-layout"),
    )
    splits.validate_split_manifest(
        motion_manifest,
        scenario_manifest=scenario_table,
        validation_report=report,
    )
    splits.validate_split_manifest(
        layout_manifest,
        scenario_manifest=scenario_table,
        validation_report=report,
        tile_collection=bounded_tiles,
    )
    _all_units_are_test(motion_manifest)
    _all_units_are_test(layout_manifest)
    split_artifacts = splits.materialize_canonical_split_manifests(
        run,
        motion_manifest,
        layout_manifest,
    )
    assert splits.verify_canonical_split_artifacts(
        repository,
        split_artifacts,
    ) == (motion_manifest, layout_manifest)

    assert artifact_store.list_partial_artifacts(run) == ()
    artifact_store.finalize_run_directory(run)
    assert (
        artifact_store.inspect_run_directory(
            repository,
            "results",
            "run:phase2:synthetic",
        )
        is artifact_store.RunDirectoryState.COMPLETE
    )
    _no_partial_state(repository)
    return _SyntheticEvidence(
        canonical_checksums=tuple(
            artifact.written_artifact.content_checksum for artifact in parquet_artifacts
        ),
        validation_json_checksum=(
            validation_artifacts.validation_report.content_checksum
        ),
        tile_checksums=(
            tile_artifacts.tile_index.content_checksum,
            tile_artifacts.tile_manifests.content_checksum,
        ),
        split_checksums=(
            split_artifacts.motion_manifest.content_checksum,
            split_artifacts.layout_manifest.content_checksum,
        ),
        tile_count=bounded_tiles.tile_count,
    )


def _motion_rows(
    *,
    scenario_id: str = "fixture-scenario-001",
    coordinate_offset: float = 0.0,
) -> list[dict[str, object]]:
    payload = cast(
        dict[str, object],
        json.loads(MOTION_FIXTURE.read_text(encoding="utf-8")),
    )
    rows = cast(list[dict[str, object]], payload["rows"])
    constants = (
        "start_timestamp",
        "end_timestamp",
        "num_timestamps",
        "focal_track_id",
        "city",
    )
    return [
        {
            **row,
            "position_x": cast(float, row["position_x"]) + coordinate_offset,
            "position_y": cast(float, row["position_y"]) + coordinate_offset,
            "scenario_id": scenario_id,
            **{field: payload[field] for field in constants},
        }
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class _Av2Evidence:
    canonical_checksums: tuple[str, ...]
    tile_count: int
    layout_group_count: int


def _run_av2_workflow(repository: Path, source_root: Path) -> _Av2Evidence:
    motion_root = source_root / "motion"
    map_root = source_root / "map"
    motion_root.mkdir(parents=True)
    map_root.mkdir(parents=True)
    motion_path = motion_root / "scenario.parquet"
    pq.write_table(pa.Table.from_pylist(_motion_rows()), motion_path)
    map_path = map_root / MAP_FIXTURE.name
    shutil.copyfile(MAP_FIXTURE, map_path)
    source_before = (motion_path.read_bytes(), map_path.read_bytes())

    motion = av2_motion.load_av2_motion_scenario(
        motion_root,
        motion_path.name,
        config=av2_motion.Av2MotionAdapterConfig(
            dataset_version="1.1",
            split_name="train",
            source_map_available=True,
        ),
    )
    vector_map = av2_map.load_av2_vector_map(
        map_root,
        map_path.name,
        scenario=motion.scenario,
        coordinate_frame=motion.coordinate_frame,
        config=av2_map.Av2VectorMapAdapterConfig(centerline_point_count=20),
    )
    included_tracks = sum(item.included for item in motion.track_summaries)
    assert (
        motion.source_track_count,
        motion.source_state_count,
        included_tracks,
        motion.source_track_count - included_tracks,
    ) == (4, 15, 3, 1)
    assert (
        vector_map.lane_segment_count,
        vector_map.drivable_area_count,
        vector_map.pedestrian_crossing_count,
        len(vector_map.elements),
    ) == (3, 1, 1, 11)
    assert vector_map.element_type_counts[:4] == (
        ("lane_centerline", 3),
        ("lane_boundary", 6),
        ("road_area", 1),
        ("crosswalk", 1),
    )
    assert vector_map.source_map_id == motion.source_scenario_id
    assert vector_map.scenario_id == motion.scenario.scenario_id
    assert vector_map.coordinate_frame_id == motion.coordinate_frame.coordinate_frame_id
    first_sample = motion.trajectories[0].samples[0]
    first_map_geometry = map_records.geometry_from_canonical_wkb(
        vector_map.elements[0].geometry_wkb
    )
    assert (first_sample.x_m, first_sample.y_m) == (0.0, 0.0)
    assert next(iter(first_map_geometry.coords))[:2] == (0.0, 0.0)

    map_ids = {element.map_element_id for element in vector_map.elements}
    for element in vector_map.elements:
        references = {
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
        }
        assert references <= map_ids
        geometry = map_records.geometry_from_canonical_wkb(element.geometry_wkb)
        assert geometry.is_valid and not geometry.is_empty
        coordinates = get_coordinates(geometry, include_z=geometry.has_z)
        assert coordinates.size > 0
        assert all(math.isfinite(float(value)) for value in coordinates.flat)

    run = artifact_store.prepare_run_directory(
        repository,
        "results",
        "run:phase2:av2",
        reserve_fraction=0.0,
    )
    motion_artifacts = av2_motion.materialize_av2_motion_scenario(run, motion)
    map_artifacts = av2_map.materialize_av2_vector_map(run, vector_map)
    av2_motion.verify_av2_motion_scenario_artifacts(
        repository,
        motion,
        motion_artifacts,
    )
    av2_map.verify_av2_vector_map_artifacts(
        repository,
        vector_map,
        map_artifacts,
    )
    paths = validation.CanonicalDatasetPaths(
        scenario_manifest=(
            motion_artifacts.scenario_manifest.written_artifact.relative_path,
        ),
        coordinate_frame_metadata=(
            motion_artifacts.coordinate_frame_metadata.written_artifact.relative_path,
        ),
        agent_metadata=(
            motion_artifacts.agent_metadata.written_artifact.relative_path,
        ),
        trajectory_samples=(
            motion_artifacts.trajectory_samples.written_artifact.relative_path,
        ),
        vector_map_elements=(
            map_artifacts.vector_map_elements.written_artifact.relative_path,
        ),
    )
    report, validation_artifacts = (
        validation.validate_and_materialize_canonical_parquet_dataset(
            run,
            paths,
            config=validation.CanonicalValidationConfig(
                minimum_valid_sample_count=1,
                minimum_valid_duration_ns=0,
                allowed_agent_classes=tuple(records.AgentClass),
                require_source_map=True,
            ),
            batch_size=2,
        )
    )
    assert report.is_eligible
    assert report.source_map_element_count == 11
    assert report.included_map_element_count == 11
    assert set(report.included_map_element_ids) == map_ids
    assert (
        validation.verify_canonical_validation_artifacts(
            repository,
            validation_artifacts,
        )
        == report
    )

    scenario_table = parquet_io.scenario_records_to_table((motion.scenario,))
    agent_table = parquet_io.agent_records_to_table(motion.agents)
    sample_table = parquet_io.trajectories_to_table(motion.trajectories)
    map_table = parquet_io.vector_map_elements_to_table(vector_map.elements)
    tiles = tiling.build_geographic_tiles(
        scenario_table,
        agent_table,
        sample_table,
        report,
        map_table,
        config=tiling.GeographicTileConfig(
            tile_size_m=20.0,
            context_buffer_m=5.0,
        ),
    )
    tile_artifacts = tiling.materialize_geographic_tiles(run, tiles)
    assert tiling.verify_geographic_tile_artifacts(repository, tile_artifacts) == tiles

    motion_manifest = splits.generate_motion_split_manifest(
        scenario_table,
        report,
        config=_split_config("phase2-av2-motion"),
    )
    layout_manifest = splits.generate_layout_split_manifest(
        tiles,
        scenario_table,
        report,
        config=_split_config("phase2-av2-layout"),
    )
    splits.validate_split_manifest(
        motion_manifest,
        scenario_manifest=scenario_table,
        validation_report=report,
    )
    splits.validate_split_manifest(
        layout_manifest,
        scenario_manifest=scenario_table,
        validation_report=report,
        tile_collection=tiles,
    )
    _all_units_are_test(motion_manifest)
    _all_units_are_test(layout_manifest)
    assigned_group_ids = tuple(
        group_id
        for membership in layout_manifest.memberships
        for group_id in membership.leakage_group_ids
    )
    assert set(assigned_group_ids) == {
        group.leakage_group_id for group in layout_manifest.leakage_groups
    }
    split_artifacts = splits.materialize_canonical_split_manifests(
        run,
        motion_manifest,
        layout_manifest,
    )
    assert splits.verify_canonical_split_artifacts(
        repository,
        split_artifacts,
    ) == (motion_manifest, layout_manifest)

    assert (motion_path.read_bytes(), map_path.read_bytes()) == source_before
    canonical_artifacts = (
        motion_artifacts.scenario_manifest,
        motion_artifacts.coordinate_frame_metadata,
        motion_artifacts.agent_metadata,
        motion_artifacts.trajectory_samples,
        map_artifacts.vector_map_elements,
    )
    assert artifact_store.list_partial_artifacts(run) == ()
    artifact_store.finalize_run_directory(run)
    _no_partial_state(repository)
    return _Av2Evidence(
        canonical_checksums=tuple(
            artifact.written_artifact.content_checksum
            for artifact in canonical_artifacts
        ),
        tile_count=tiles.tile_count,
        layout_group_count=len(layout_manifest.leakage_groups),
    )


def _motion_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("observed", pa.bool_()),
            pa.field("track_id", pa.string()),
            pa.field("object_type", pa.string()),
            pa.field("object_category", pa.int64()),
            pa.field("timestep", pa.int64()),
            pa.field("position_x", pa.float64()),
            pa.field("position_y", pa.float64()),
            pa.field("heading", pa.float64()),
            pa.field("velocity_x", pa.float64()),
            pa.field("velocity_y", pa.float64()),
            pa.field("scenario_id", pa.string()),
            pa.field("start_timestamp", pa.int64()),
            pa.field("end_timestamp", pa.int64()),
            pa.field("num_timestamps", pa.int64()),
            pa.field("focal_track_id", pa.string()),
            pa.field("city", pa.string()),
        ]
    )


def _write_pilot_scenario(
    source_root: Path,
    scenario_id: str,
    coordinate_offset: float,
) -> tuple[Path, Path]:
    directory = source_root / "train" / scenario_id
    directory.mkdir(parents=True)
    motion_path = directory / f"scenario_{scenario_id}.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            _motion_rows(
                scenario_id=scenario_id,
                coordinate_offset=coordinate_offset,
            ),
            schema=_motion_schema(),
        ),
        motion_path,
    )
    map_path = directory / f"log_map_archive_{scenario_id}.json"
    shutil.copyfile(MAP_FIXTURE, map_path)
    return motion_path, map_path


def _pilot_manifest(
    source_root: Path,
    paths: tuple[Path, ...],
) -> registry.DatasetSourceManifest:
    files = tuple(
        sorted(
            (
                registry.DatasetSourceFile(
                    relative_path=path.relative_to(source_root),
                    size_bytes=path.stat().st_size,
                    sha256=_digest(path),
                )
                for path in paths
            ),
            key=lambda item: item.relative_path.as_posix(),
        )
    )
    return registry.DatasetSourceManifest(
        schema_version="1.0",
        dataset_id="av2_motion",
        dataset_version="1.1",
        adapter_name="av2_motion_adapter",
        adapter_version="1.0",
        source_kind=registry.DatasetSourceKind.EXTERNAL_DIRECTORY,
        source_root_label="phase2-integration-source",
        availability=registry.DatasetAvailability.AVAILABLE,
        checksum_mode=registry.SourceChecksumMode.SHA256,
        file_count=len(files),
        total_bytes=sum(item.size_bytes for item in files),
        files=files,
        license_reference="project-created fixtures",
        citation_reference="project-created fixtures",
        discovered_at_utc="2026-01-01T00:00:00.000000Z",
    )


def _temporary_doctor_repository(tmp_path: Path) -> Path:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir()
    shutil.copytree(PROJECT_ROOT / "definitions", repository / "definitions")
    (repository / "configs").mkdir()
    shutil.copy2(
        PROJECT_ROOT / "configs" / "project.toml",
        repository / "configs" / "project.toml",
    )
    for file_name in ("README.md", "LICENSE", "pyproject.toml", "uv.lock"):
        shutil.copy2(PROJECT_ROOT / file_name, repository / file_name)
    for directory in (
        "scripts",
        "src/kinematicweave",
        "tests",
        "data",
        "results",
        "reports",
        "figures",
        "qualitative",
        "source",
    ):
        (repository / directory).mkdir(parents=True)
    return repository


def _entry_point(
    executable: str,
    arguments: tuple[str, ...],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (executable, *arguments),
        cwd=cwd,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_phase2_canonical_contract_snapshot(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    before = tuple(repository.rglob("*"))
    names = schemas.canonical_schema_names()
    phase2_names = names[:5]
    assert tuple(name.value for name in phase2_names) == tuple(SCHEMA_FINGERPRINTS)
    for schema_name in phase2_names:
        arrow_schema = schemas.get_arrow_schema(schema_name)
        schemas.validate_arrow_schema(arrow_schema, schema_name)
        empty = pa.Table.from_batches([], schema=arrow_schema)
        schemas.validate_arrow_schema(empty.schema, schema_name)
        assert empty.num_rows == 0
        assert schemas.get_polars_schema(schema_name) == {
            field.name: _expected_polars_type(field.type) for field in arrow_schema
        }
        fingerprint = schemas.schema_fingerprint(schema_name)
        assert fingerprint == SCHEMA_FINGERPRINTS[schema_name.value]
        assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    assert tuple(repository.rglob("*")) == before
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    for target in (
        "docs/phase2_milestone_review.md",
        "docs/phase2_data_runbook.md",
    ):
        assert f"]({target})" in readme
        assert (PROJECT_ROOT / target).is_file()

    milestone = (PROJECT_ROOT / "docs" / "phase2_milestone_review.md").read_text(
        encoding="utf-8"
    )
    assert milestone.startswith("# Phase 2 Milestone Review — Canonical Data Ready\n")
    for index, heading in enumerate(
        (
            "Milestone decision",
            "Phase 2 scope delivered",
            "Accepted batch and commit ledger",
            "Canonical contract snapshot",
            "End-to-end workflows verified",
            "Quantitative acceptance results",
            "Determinism and reproducibility evidence",
            "ASUS laptop resource posture",
            "Real-provider data status",
            "Known limitations and deferred work",
            "Phase 3 entry criteria",
            "Reproduction commands",
        ),
        start=1,
    ):
        assert f"## {index}. {heading}" in milestone
    for batch, commit in BATCH_COMMITS.items():
        assert f"| {batch} | `{commit}` |" in milestone
    for schema_label, fingerprint in SCHEMA_FINGERPRINTS.items():
        assert f"| `{schema_label}` | `{fingerprint}` |" in milestone
    assert re.search(r"(?:\b[A-Za-z]:[\\/]|/(?:home|Users|tmp)/)", milestone) is None

    runbook = (PROJECT_ROOT / "docs" / "phase2_data_runbook.md").read_text(
        encoding="utf-8"
    )
    assert runbook.startswith("# Phase 2 Data Foundation Runbook\n")
    for command in (
        "uv sync --frozen",
        "kinematicweave doctor",
        "kinematicweave data registry",
        "kinematicweave data synthetic-check",
        "kinematicweave data source-discover",
        "kinematicweave data source-verify",
        "kinematicweave data validate",
        "kinematicweave data cache-scan",
        "kinematicweave data cache-prune",
        "pytest tests/test_phase2_integration.py -q",
        "python scripts/quality.py",
        "python scripts/ci.py",
    ):
        assert command in runbook
    for variable in (
        "KINEMATICWEAVE_AV2_PILOT_SOURCE_ROOT",
        "KINEMATICWEAVE_AV2_PILOT_SOURCE_MANIFEST",
        "KINEMATICWEAVE_AV2_PILOT_SOURCE_PARTITION",
        "KINEMATICWEAVE_AV2_PILOT_DATASET_VERSION",
    ):
        assert variable in runbook
    assert re.search(r"(?:\b[A-Za-z]:[\\/]|/(?:home|Users|tmp)/)", runbook) is None


def test_complete_synthetic_phase2_workflow_is_deterministic(tmp_path: Path) -> None:
    first_repository = (tmp_path / "first-repository").resolve()
    second_repository = (tmp_path / "second-repository").resolve()
    first_repository.mkdir()
    second_repository.mkdir()
    first = _run_synthetic_workflow(first_repository)
    second = _run_synthetic_workflow(second_repository)
    assert first == second
    assert first.tile_count > 1


def test_av2_motion_map_phase2_workflow_is_deterministic(tmp_path: Path) -> None:
    first_repository = (tmp_path / "first-repository").resolve()
    second_repository = (tmp_path / "second-repository").resolve()
    first_source = (tmp_path / "first-source").resolve()
    second_source = (tmp_path / "second-source").resolve()
    first_repository.mkdir()
    second_repository.mkdir()
    first_source.mkdir()
    second_source.mkdir()
    first = _run_av2_workflow(first_repository, first_source)
    second = _run_av2_workflow(second_repository, second_source)
    assert first == second
    assert first.tile_count > 0
    assert first.layout_group_count > 0


def test_laptop_pilot_materialization_and_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = (tmp_path / "repository").resolve()
    source_root = (tmp_path / "source").resolve()
    repository.mkdir()
    source_root.mkdir()
    scenario_ids = tuple(f"fixture-scenario-{index:03d}" for index in range(1, 5))
    source_paths = tuple(
        path
        for index, scenario_id in enumerate(scenario_ids)
        for path in _write_pilot_scenario(
            source_root,
            scenario_id,
            float(index),
        )
    )
    source_checksums = {path: _digest(path) for path in source_paths}
    manifest = _pilot_manifest(source_root, source_paths)
    config = pilot.Av2PilotConfig(
        source_partition=Path("train"),
        dataset_version="1.1",
        scenario_count=3,
        root_seed=0,
        assignment_namespace="phase2-integration-pilot",
        minimum_valid_sample_count=1,
        minimum_valid_duration_ns=0,
        validation_batch_size=2,
        materialization_expansion_factor=0.0,
        reserve_fraction=0.0,
    )
    plan = pilot.build_av2_pilot_plan(manifest, config=config)
    repeated_plan = pilot.build_av2_pilot_plan(manifest, config=config)
    assert plan == repeated_plan
    assert plan.candidate_count == 4
    assert plan.selected_scenario_count == 3
    assert pilot.av2_pilot_plan_identity(plan) == pilot.av2_pilot_plan_identity(
        repeated_plan
    )
    pilot.verify_av2_pilot_sources(source_root, plan)
    materialization_plan = pilot.av2_pilot_materialization_plan(plan)
    assert len(materialization_plan.units) == 3
    assert all(
        tuple(unit.expected_output_paths) == PILOT_OUTPUTS
        for unit in materialization_plan.units
    )

    first = pilot.execute_av2_pilot(
        repository,
        source_root,
        PILOT_CACHE_ROOT,
        plan,
    )
    assert first.report.materialized_scenario_count == 3
    assert first.report.reused_scenario_count == 0
    assert first.report.selected_scenario_ids == tuple(
        item.source_scenario_id for item in plan.selected_scenarios
    )
    assert first.validation_report.config.require_source_map
    assert first.validation_report.is_eligible
    assert all(
        math.isfinite(value) and value >= 0.0
        for value in (
            first.report.resources.source_verification_seconds,
            first.report.resources.materialization_seconds,
            first.report.resources.validation_seconds,
            first.report.resources.total_seconds,
        )
    )
    assert first.report.resources.selected_source_bytes == (
        plan.selected_total_source_bytes
    )
    assert first.report.resources.cache_output_bytes >= 0
    assert len(first.materialization_report.results) == 3
    assert all(
        len(result.outputs) == 7
        and result.disposition
        is materialization.MaterializationDisposition.MATERIALIZED
        for result in first.materialization_report.results
    )
    first_cache_keys = tuple(
        result.cache_key for result in first.materialization_report.results
    )
    output_checksums = {
        repository / result.entry_relative_directory / output.relative_path: (
            output.size_bytes,
            output.sha256,
        )
        for result in first.materialization_report.results
        for output in result.outputs
    }
    inventory = scan_materialization_cache(repository, PILOT_CACHE_ROOT)
    assert inventory.complete_entry_count == 3
    assert inventory.incomplete_entry_count == 0

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("conversion worker must not run for immutable cache hits")

    monkeypatch.setattr(
        cast(Any, pilot).av2_motion,
        "load_av2_motion_scenario",
        forbidden,
    )
    monkeypatch.setattr(
        cast(Any, pilot).av2_map,
        "load_av2_vector_map",
        forbidden,
    )
    second = pilot.execute_av2_pilot(
        repository,
        source_root,
        PILOT_CACHE_ROOT,
        plan,
    )
    assert second.report.selected_scenario_ids == first.report.selected_scenario_ids
    assert second.report.materialized_scenario_count == 0
    assert second.report.reused_scenario_count == 3
    assert (
        tuple(result.cache_key for result in second.materialization_report.results)
        == first_cache_keys
    )
    assert all(
        result.disposition is materialization.MaterializationDisposition.REUSED
        for result in second.materialization_report.results
    )
    assert all(
        path.stat().st_size == size and _digest(path) == digest
        for path, (size, digest) in output_checksums.items()
    )

    run = artifact_store.prepare_run_directory(
        repository,
        "results",
        "run:phase2:pilot",
        reserve_fraction=0.0,
    )
    pilot_artifacts = pilot.materialize_av2_pilot_artifacts(run, second)
    verified_plan, verified_report = pilot.verify_av2_pilot_artifacts(
        repository,
        pilot_artifacts,
    )
    assert verified_plan == plan
    assert verified_report == second.report
    artifact_store.finalize_run_directory(run)
    assert all(_digest(path) == digest for path, digest in source_checksums.items())
    assert (
        scan_materialization_cache(
            repository,
            PILOT_CACHE_ROOT,
        ).incomplete_entry_count
        == 0
    )
    _no_partial_state(repository)


def test_phase2_cli_and_doctor_public_integration(tmp_path: Path) -> None:
    repository = _temporary_doctor_repository(tmp_path)
    executable = shutil.which("kinematicweave")
    assert executable is not None
    before = _file_snapshot(repository)

    root_help = _entry_point(executable, ("--help",), cwd=repository)
    assert root_help.returncode == 0
    assert root_help.stderr == ""
    assert "data" in root_help.stdout

    data_help = _entry_point(executable, ("data", "--help"), cwd=repository)
    assert data_help.returncode == 0
    assert data_help.stderr == ""
    command_sets = {
        frozenset(group.split(","))
        for group in re.findall(r"\{([^{}]+)\}", data_help.stdout)
    }
    assert command_sets == {frozenset(DATA_COMMANDS)}

    registry_result = _entry_point(executable, ("data", "registry"), cwd=repository)
    assert registry_result.returncode == 0
    assert registry_result.stderr == ""
    assert [
        entry["dataset_id"] for entry in json.loads(registry_result.stdout)["entries"]
    ] == ["synthetic_kinematicweave", "av2_motion"]

    synthetic_result = _entry_point(
        executable,
        ("data", "synthetic-check"),
        cwd=repository,
    )
    assert synthetic_result.returncode == 0
    assert synthetic_result.stderr == ""
    synthetic_summary = json.loads(synthetic_result.stdout)
    assert (
        synthetic_summary["scenario_count"],
        synthetic_summary["coordinate_frame_count"],
        synthetic_summary["agent_count"],
        synthetic_summary["trajectory_count"],
        synthetic_summary["sample_count"],
    ) == (16, 16, 27, 27, 293)

    cache_result = _entry_point(
        executable,
        (
            "data",
            "cache-scan",
            "--repository-root",
            str(repository),
        ),
        cwd=repository,
    )
    assert cache_result.returncode == 0
    assert cache_result.stderr == ""
    assert json.loads(cache_result.stdout)["entries"] == []

    doctor_result = _entry_point(
        executable,
        (
            "doctor",
            "--repo-root",
            str(repository),
            "--required-free-fraction",
            "0",
            "--json",
        ),
        cwd=repository,
    )
    assert doctor_result.returncode == 0
    assert doctor_result.stderr == ""
    doctor_report = json.loads(doctor_result.stdout)
    data_check = next(
        check
        for check in doctor_report["checks"]
        if check["check_id"] == "data_foundation"
    )
    assert data_check["status"] == "pass"
    details = {item["key"]: item["value"] for item in data_check["details"]}
    assert details["schema_count"] == "13"
    assert details["schema_names"].split(", ")[:5] == list(SCHEMA_FINGERPRINTS)

    failure = _entry_point(
        executable,
        ("data", "source-discover", "--dataset-id", "unknown"),
        cwd=repository,
    )
    assert failure.returncode == 2
    assert failure.stdout == ""
    assert failure.stderr.startswith("error:")
    assert "Traceback" not in failure.stderr
    assert _file_snapshot(repository) == before
