"""Focused tests for deterministic split generation and leakage control."""

from dataclasses import FrozenInstanceError, fields, replace
import importlib
import json
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import kinematicweave.artifact_store as artifact_store
from kinematicweave.canonical import canonical_sha256
from kinematicweave.data import (
    av2_map,
    av2_motion,
    parquet_io,
    splits,
    synthetic,
    tiling,
    validation,
)
from kinematicweave.data.av2_motion import Av2MotionAdapterConfig
from kinematicweave.data.splits import (
    CanonicalSplitArtifacts,
    CanonicalSplitManifest,
    GeographicLeakageGroup,
    SplitGenerationConfig,
    SplitMembership,
    SplitName,
    SplitUnitType,
)
from kinematicweave.domain.map_records import geometry_from_canonical_wkb
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

EXPECTED_PUBLIC_API = [
    "CanonicalSplitArtifacts",
    "CanonicalSplitManifest",
    "GeographicLeakageGroup",
    "SplitGenerationConfig",
    "SplitMembership",
    "SplitName",
    "SplitUnitType",
    "build_geographic_leakage_groups",
    "canonical_split_manifest_from_dict",
    "canonical_split_manifest_from_json",
    "canonical_split_manifest_identity",
    "canonical_split_manifest_to_canonical_json",
    "canonical_split_manifest_to_dict",
    "canonical_split_summary_markdown",
    "generate_layout_split_manifest",
    "generate_motion_split_manifest",
    "geographic_leakage_group_to_dict",
    "materialize_canonical_split_manifests",
    "split_generation_config_to_dict",
    "validate_split_manifest",
    "verify_canonical_split_artifacts",
]


@pytest.fixture(scope="module", autouse=True)
def _refresh_modules_after_import_safety_tests() -> None:
    """Bind tests to classes recreated by earlier module-reload checks."""
    importlib.reload(parquet_io)
    importlib.reload(synthetic)
    importlib.reload(av2_motion)
    importlib.reload(av2_map)
    importlib.reload(validation)
    importlib.reload(tiling)
    importlib.reload(splits)
    globals().update(
        {
            "AgentClass": records_module.AgentClass,
            "AgentRecord": records_module.AgentRecord,
            "CoordinateFrameRecord": records_module.CoordinateFrameRecord,
            "OriginType": records_module.OriginType,
            "ScenarioRecord": records_module.ScenarioRecord,
            "Trajectory": records_module.Trajectory,
            "TrajectorySampleRecord": records_module.TrajectorySampleRecord,
            "CanonicalSplitArtifacts": splits.CanonicalSplitArtifacts,
            "CanonicalSplitManifest": splits.CanonicalSplitManifest,
            "GeographicLeakageGroup": splits.GeographicLeakageGroup,
            "SplitGenerationConfig": splits.SplitGenerationConfig,
            "SplitMembership": splits.SplitMembership,
            "SplitName": splits.SplitName,
            "SplitUnitType": splits.SplitUnitType,
            "Av2MotionAdapterConfig": av2_motion.Av2MotionAdapterConfig,
        }
    )


def _sample(
    scenario_id: str,
    agent_id: str,
    trajectory_id: str,
    index: int,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id=scenario_id,
        agent_id=agent_id,
        trajectory_id=trajectory_id,
        sample_index=index,
        timestamp_ns=index * 1_000_000_000,
        x_m=float(index * 4),
        y_m=1.0,
        z_m=None,
        heading_rad=0.0,
        velocity_x_mps=4.0,
        velocity_y_mps=0.0,
        speed_mps=4.0,
        acceleration_x_mps2=None,
        acceleration_y_mps2=None,
        is_observed=True,
        is_valid=True,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _canonical_dataset(
    cities: tuple[str | None, ...] = ("A", "A", "B", "B", None),
) -> tuple[pa.Table, pa.Table, pa.Table, pa.Table, Any]:
    scenarios: list[ScenarioRecord] = []
    frames: list[CoordinateFrameRecord] = []
    agents: list[AgentRecord] = []
    trajectories: list[Trajectory] = []
    for index, city in enumerate(cities):
        suffix = f"{index:03d}"
        scenario_id = f"scenario:split:{suffix}"
        frame_id = f"frame:split:{suffix}"
        agent_id = f"agent:split:{suffix}"
        trajectory_id = f"trajectory:split:{suffix}"
        trajectory = Trajectory(
            scenario_id=scenario_id,
            agent_id=agent_id,
            trajectory_id=trajectory_id,
            samples=(
                _sample(scenario_id, agent_id, trajectory_id, 0),
                _sample(scenario_id, agent_id, trajectory_id, 1),
            ),
            origin_type=OriginType.SYNTHETIC,
            quality_flags=(),
        )
        scenarios.append(
            ScenarioRecord(
                scenario_id=scenario_id,
                dataset_id="split_data",
                dataset_version="1.0",
                split_name="development",
                city_or_region=city,
                source_scenario_id=f"source-{suffix}",
                start_time_ns=0,
                end_time_ns=1_000_000_000,
                coordinate_frame_id=frame_id,
                origin_x_m=float(index * 100),
                origin_y_m=0.0,
                origin_z_m=None,
                source_crs=None,
                has_elevation=False,
                agent_count=1,
                source_map_available=False,
                quality_flags=(),
                adapter_name="split_test",
                adapter_version="1.0",
                source_checksum=None,
            )
        )
        frames.append(
            CoordinateFrameRecord(
                scenario_id=scenario_id,
                coordinate_frame_id=frame_id,
                parent_frame_id=None,
                frame_type="local_cartesian",
                origin_x_m=float(index * 100),
                origin_y_m=0.0,
                origin_z_m=None,
                axis_convention="right_handed_x_y_z_up",
                distance_unit="m",
                angle_unit="rad",
                timestamp_unit="ns",
                source_crs=None,
                has_elevation=False,
                transform_to_parent_4x4=None,
                origin_type=OriginType.SYNTHETIC,
                quality_flags=(),
            )
        )
        agents.append(
            AgentRecord(
                scenario_id=scenario_id,
                agent_id=agent_id,
                source_agent_id=suffix,
                agent_class=AgentClass.VEHICLE,
                length_m=4.0,
                width_m=2.0,
                height_m=None,
                first_time_ns=0,
                last_time_ns=1_000_000_000,
                sample_count=2,
                is_focal_agent=True,
                is_ego_agent=False,
                origin_type=OriginType.SYNTHETIC,
                quality_flags=(),
            )
        )
        trajectories.append(trajectory)
    scenario_table = parquet_io.scenario_records_to_table(scenarios)
    frame_table = parquet_io.coordinate_frame_records_to_table(frames)
    agent_table = parquet_io.agent_records_to_table(agents)
    sample_table = parquet_io.trajectories_to_table(trajectories)
    report = validation.validate_canonical_tables(
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        config=validation.CanonicalValidationConfig(
            minimum_valid_sample_count=2,
            minimum_valid_duration_ns=0,
        ),
    )
    return scenario_table, frame_table, agent_table, sample_table, report


def _config(
    *,
    seed: int = 17,
    namespace: str = "test",
    smoke: int = 1,
    development: int = 1,
    pilot: int = 1,
    test: int | None = None,
    held_out_city: str | None = None,
) -> SplitGenerationConfig:
    return SplitGenerationConfig(
        root_seed=seed,
        assignment_namespace=namespace,
        smoke_count=smoke,
        development_count=development,
        pilot_count=pilot,
        test_count=test,
        held_out_city=held_out_city,
    )


def _tile_collection(
    scenario_table: pa.Table,
    report: Any,
    coordinates: tuple[tuple[int, int, int], ...],
    *,
    buffer_m: float = 1.0,
) -> tiling.GeographicTileCollection:
    config = tiling.GeographicTileConfig(
        tile_size_m=10.0,
        context_buffer_m=buffer_m,
    )
    scenario_ids = tuple(
        cast(str, value) for value in scenario_table.column("scenario_id").to_pylist()
    )
    tiles: list[tiling.GeographicTile] = []
    for grid_x, grid_y, scenario_index in coordinates:
        scenario_id = scenario_ids[scenario_index]
        spec = tiling.geographic_tile_spec(
            "split_data",
            "1.0",
            grid_x,
            grid_y,
            config=config,
        )
        trajectory_id = f"trajectory:tile:{grid_x}:{grid_y}"
        agent_id = f"agent:tile:{grid_x}:{grid_y}"
        tiles.append(
            tiling.GeographicTile(
                spec=spec,
                membership=tiling.GeographicTileMembership(
                    interior_scenario_ids=(scenario_id,),
                    interior_agent_ids=(agent_id,),
                    interior_trajectory_ids=(trajectory_id,),
                    context_scenario_ids=(scenario_id,),
                    context_agent_ids=(agent_id,),
                    context_trajectory_ids=(trajectory_id,),
                    interior_map_element_ids=(),
                    context_map_element_ids=(),
                ),
                coverage=tiling.GeographicTileCoverage(
                    interior_valid_sample_count=2,
                    context_valid_sample_count=2,
                    interior_trajectory_length_m=1.0,
                    context_trajectory_length_m=1.0,
                    interior_map_length_m=0.0,
                    context_map_length_m=0.0,
                    interior_map_area_m2=0.0,
                    context_map_area_m2=0.0,
                ),
            )
        )
    identity = canonical_sha256(
        "canonical-validation-report",
        validation.canonical_validation_report_to_dict(report),
    )
    return tiling.GeographicTileCollection(
        schema_version="1.0",
        dataset_id="split_data",
        dataset_version="1.0",
        config=config,
        validation_report_identity=identity,
        tiles=tuple(
            sorted(
                tiles,
                key=lambda tile: (
                    tile.spec.grid_x,
                    tile.spec.grid_y,
                    tile.spec.tile_id,
                ),
            )
        ),
    )


def test_exact_public_api_enums_and_model_fields() -> None:
    assert splits.__all__ == EXPECTED_PUBLIC_API
    assert tuple(item.value for item in SplitName) == (
        "smoke",
        "development",
        "pilot",
        "test",
        "held_out_city",
    )
    assert tuple(item.value for item in SplitUnitType) == (
        "motion_scenario",
        "layout_tile",
    )
    assert tuple(field.name for field in fields(SplitGenerationConfig)) == (
        "root_seed",
        "assignment_namespace",
        "smoke_count",
        "development_count",
        "pilot_count",
        "test_count",
        "held_out_city",
    )
    assert tuple(field.name for field in fields(CanonicalSplitArtifacts)) == (
        "motion_manifest",
        "layout_manifest",
        "split_summary",
    )
    assert tuple(field.name for field in fields(SplitMembership)) == (
        "split_name",
        "unit_ids",
        "leakage_group_ids",
    )
    assert tuple(field.name for field in fields(GeographicLeakageGroup)) == (
        "leakage_group_id",
        "tile_ids",
        "scenario_ids",
        "city_or_regions",
        "context_min_x_m",
        "context_min_y_m",
        "context_max_x_m",
        "context_max_y_m",
    )


def test_models_are_frozen_slotted_and_normalized() -> None:
    config = _config(namespace=" namespace ", held_out_city=" City ")
    assert config.assignment_namespace == "namespace"
    assert config.held_out_city == "City"
    assert hasattr(type(config), "__slots__")
    with pytest.raises(FrozenInstanceError):
        config.root_seed = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("root_seed", True),
        ("root_seed", -1),
        ("assignment_namespace", " "),
        ("smoke_count", True),
        ("development_count", -1),
        ("pilot_count", False),
        ("test_count", -1),
        ("held_out_city", ""),
    ),
)
def test_config_rejects_invalid_values(field_name: str, value: object) -> None:
    values: dict[str, object] = {
        "root_seed": 1,
        "assignment_namespace": "test",
        "smoke_count": 0,
        "development_count": 0,
        "pilot_count": 0,
        "test_count": None,
        "held_out_city": None,
    }
    values[field_name] = value
    with pytest.raises(ValidationError):
        SplitGenerationConfig(**cast(Any, values))


def test_motion_assignment_counts_order_and_determinism() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    config = _config()
    first = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=config,
    )
    second = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=config,
    )
    assert first == second
    assert first.source_unit_count == 5
    assert first.assigned_unit_count == 5
    assert (
        first.smoke_unit_count,
        first.development_unit_count,
        first.pilot_unit_count,
        first.test_unit_count,
        first.held_out_unit_count,
    ) == (1, 1, 1, 2, 0)
    canonical_order = cast(list[str], scenarios.column("scenario_id").to_pylist())
    for membership in first.memberships:
        assert list(membership.unit_ids) == [
            identifier
            for identifier in canonical_order
            if identifier in membership.unit_ids
        ]


def test_seed_namespace_and_explicit_counts_change_assignment() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset(("A",) * 12)
    first = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=_config(smoke=3, development=3, pilot=3, test=3),
    )
    changed_seed = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=_config(seed=99, smoke=3, development=3, pilot=3, test=3),
    )
    changed_namespace = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=_config(
            namespace="other",
            smoke=3,
            development=3,
            pilot=3,
            test=3,
        ),
    )
    assert first.memberships != changed_seed.memberships
    assert first.memberships != changed_namespace.memberships
    with pytest.raises(ValidationError):
        splits.generate_motion_split_manifest(
            scenarios,
            report,
            config=_config(smoke=3, development=3, pilot=3, test=2),
        )


def test_motion_held_out_city_and_missing_city() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    manifest = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=_config(
            smoke=1,
            development=1,
            pilot=0,
            held_out_city="B",
        ),
    )
    held_out = manifest.memberships[-1]
    assert held_out.unit_ids == (
        "scenario:split:002",
        "scenario:split:003",
    )
    assert manifest.test_unit_count == 1
    with pytest.raises(ValidationError):
        splits.generate_motion_split_manifest(
            scenarios,
            report,
            config=_config(
                smoke=0,
                development=0,
                pilot=0,
                held_out_city="missing",
            ),
        )


def test_duplicate_source_scenario_is_rejected() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    values = scenarios.to_pylist()
    values[1]["source_scenario_id"] = values[0]["source_scenario_id"]
    duplicate = pa.Table.from_pylist(values, schema=scenarios.schema)
    with pytest.raises(SchemaError, match="duplicate source"):
        splits.generate_motion_split_manifest(
            duplicate,
            report,
            config=_config(),
        )


def test_leakage_groups_overlap_boundary_transitivity_and_negative_grid() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    isolated = _tile_collection(scenarios, report, ((0, 0, 0),), buffer_m=0)
    assert len(splits.build_geographic_leakage_groups(isolated, scenarios)) == 1
    boundary = _tile_collection(
        scenarios,
        report,
        ((-1, 0, 0), (0, 0, 1), (1, 0, 2)),
        buffer_m=0,
    )
    boundary_groups = splits.build_geographic_leakage_groups(boundary, scenarios)
    assert len(boundary_groups) == 3
    overlapping = _tile_collection(
        scenarios,
        report,
        ((-1, 0, 0), (0, 0, 1), (1, 0, 2)),
        buffer_m=1,
    )
    groups = splits.build_geographic_leakage_groups(overlapping, scenarios)
    assert len(groups) == 1
    assert len(groups[0].tile_ids) == 3
    assert groups[0].scenario_ids == (
        "scenario:split:000",
        "scenario:split:001",
        "scenario:split:002",
    )
    assert groups[0].city_or_regions == ("A", "B")
    assert groups == splits.build_geographic_leakage_groups(overlapping, scenarios)


def test_large_context_checks_two_grid_cells_away() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    collection = _tile_collection(
        scenarios,
        report,
        ((0, 0, 0), (2, 0, 1)),
        buffer_m=6,
    )
    groups = splits.build_geographic_leakage_groups(collection, scenarios)
    assert len(groups) == 1


def test_buffer_changes_groups_without_changing_tile_ids() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    coordinates = ((0, 0, 0), (1, 0, 1))
    unbuffered = _tile_collection(
        scenarios,
        report,
        coordinates,
        buffer_m=0,
    )
    buffered = _tile_collection(
        scenarios,
        report,
        coordinates,
        buffer_m=1,
    )
    assert [tile.spec.tile_id for tile in unbuffered.tiles] == [
        tile.spec.tile_id for tile in buffered.tiles
    ]
    assert len(splits.build_geographic_leakage_groups(unbuffered, scenarios)) == 2
    assert len(splits.build_geographic_leakage_groups(buffered, scenarios)) == 1


def test_layout_assignment_keeps_groups_indivisible_and_validates() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    collection = _tile_collection(
        scenarios,
        report,
        ((0, 0, 0), (1, 0, 0), (4, 0, 1), (8, 0, 2)),
        buffer_m=1,
    )
    groups = splits.build_geographic_leakage_groups(collection, scenarios)
    assert [len(group.tile_ids) for group in groups] == [2, 1, 1]
    manifest = splits.generate_layout_split_manifest(
        collection,
        scenarios,
        report,
        config=_config(smoke=1, development=1, pilot=0),
    )
    splits.validate_split_manifest(
        manifest,
        scenario_manifest=scenarios,
        validation_report=report,
        tile_collection=collection,
    )
    assert sum(
        len(membership.leakage_group_ids) for membership in manifest.memberships
    ) == len(groups)
    group_split = {
        group_id: membership.split_name
        for membership in manifest.memberships
        for group_id in membership.leakage_group_ids
    }
    assert len(group_split) == len(groups)


def test_layout_held_out_group_and_mixed_city_rejection() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    collection = _tile_collection(
        scenarios,
        report,
        ((0, 0, 2), (4, 0, 0)),
        buffer_m=1,
    )
    manifest = splits.generate_layout_split_manifest(
        collection,
        scenarios,
        report,
        config=_config(
            smoke=0,
            development=0,
            pilot=0,
            held_out_city="B",
        ),
    )
    assert len(manifest.memberships[-1].unit_ids) == 1
    mixed = _tile_collection(
        scenarios,
        report,
        ((0, 0, 0), (1, 0, 2)),
        buffer_m=1,
    )
    with pytest.raises(ValidationError, match="another city"):
        splits.generate_layout_split_manifest(
            mixed,
            scenarios,
            report,
            config=_config(
                smoke=0,
                development=0,
                pilot=0,
                held_out_city="B",
            ),
        )


def test_serialization_identity_summary_and_round_trip() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    motion = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=_config(),
    )
    collection = _tile_collection(
        scenarios,
        report,
        ((0, 0, 0), (4, 0, 1), (8, 0, 2)),
    )
    layout = splits.generate_layout_split_manifest(
        collection,
        scenarios,
        report,
        config=_config(),
    )
    value = splits.canonical_split_manifest_to_dict(layout)
    assert tuple(value) == tuple(field.name for field in fields(CanonicalSplitManifest))
    text = splits.canonical_split_manifest_to_canonical_json(layout)
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert splits.canonical_split_manifest_from_json(text) == layout
    assert splits.canonical_split_manifest_identity(
        layout
    ) == splits.canonical_split_manifest_identity(layout)
    assert splits.canonical_split_manifest_identity(layout) != (
        splits.canonical_split_manifest_identity(
            replace(layout, config=replace(layout.config, root_seed=99))
        )
    )
    summary = splits.canonical_split_summary_markdown(motion, layout)
    assert summary.endswith("\n") and not summary.endswith("\n\n")
    assert "Canonical Split Summary" in summary
    assert "indivisible" in summary
    with pytest.raises(SchemaError):
        splits.canonical_split_manifest_from_json("[]")
    with pytest.raises(SchemaError):
        splits.canonical_split_manifest_from_json("{")
    unknown = dict(value)
    unknown["unknown"] = True
    with pytest.raises(SchemaError):
        splits.canonical_split_manifest_from_dict(unknown)


def test_artifacts_verify_overwrite_finalize_and_tamper(tmp_path: Path) -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    motion = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=_config(),
    )
    collection = _tile_collection(
        scenarios,
        report,
        ((0, 0, 0), (4, 0, 1), (8, 0, 2)),
    )
    layout = splits.generate_layout_split_manifest(
        collection,
        scenarios,
        report,
        config=_config(),
    )
    run = artifact_store.prepare_run_directory(
        tmp_path,
        "results",
        "run:splits:001",
        reserve_fraction=0.0,
    )
    artifacts = splits.materialize_canonical_split_manifests(
        run,
        motion,
        layout,
    )
    assert tuple(
        artifact.relative_path.name
        for artifact in (
            artifacts.motion_manifest,
            artifacts.layout_manifest,
            artifacts.split_summary,
        )
    ) == (
        "motion_splits.json",
        "layout_splits.json",
        "split_summary.md",
    )
    assert splits.verify_canonical_split_artifacts(tmp_path, artifacts) == (
        motion,
        layout,
    )
    with pytest.raises(ArtifactError):
        splits.materialize_canonical_split_manifests(run, motion, layout)
    summary_path = tmp_path / artifacts.split_summary.relative_path
    summary_path.write_text("altered\n", encoding="utf-8")
    with pytest.raises(ArtifactError):
        splits.verify_canonical_split_artifacts(tmp_path, artifacts)
    artifact_store.finalize_run_directory(run)
    with pytest.raises(ArtifactError):
        splits.materialize_canonical_split_manifests(run, motion, layout)


def test_validation_rejects_altered_deterministic_membership() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    manifest = splits.generate_motion_split_manifest(
        scenarios,
        report,
        config=_config(),
    )
    memberships = list(manifest.memberships)
    smoke = memberships[0]
    development = memberships[1]
    memberships[0] = replace(smoke, unit_ids=development.unit_ids)
    memberships[1] = replace(development, unit_ids=smoke.unit_ids)
    altered = replace(manifest, memberships=tuple(memberships))
    with pytest.raises(SchemaError, match="regeneration"):
        splits.validate_split_manifest(
            altered,
            scenario_manifest=scenarios,
            validation_report=report,
        )


def test_validation_rejects_cross_split_buffered_overlap() -> None:
    scenarios, _frames, _agents, _samples, report = _canonical_dataset()
    collection = _tile_collection(
        scenarios,
        report,
        ((0, 0, 0), (1, 0, 1)),
        buffer_m=1,
    )
    first_tile, second_tile = collection.tiles
    first_group = GeographicLeakageGroup(
        leakage_group_id="leakage-group:test:first",
        tile_ids=(first_tile.spec.tile_id,),
        scenario_ids=first_tile.membership.context_scenario_ids,
        city_or_regions=("A",),
        context_min_x_m=first_tile.spec.context_min_x_m,
        context_min_y_m=first_tile.spec.context_min_y_m,
        context_max_x_m=first_tile.spec.context_max_x_m,
        context_max_y_m=first_tile.spec.context_max_y_m,
    )
    second_group = GeographicLeakageGroup(
        leakage_group_id="leakage-group:test:second",
        tile_ids=(second_tile.spec.tile_id,),
        scenario_ids=second_tile.membership.context_scenario_ids,
        city_or_regions=("A",),
        context_min_x_m=second_tile.spec.context_min_x_m,
        context_min_y_m=second_tile.spec.context_min_y_m,
        context_max_x_m=second_tile.spec.context_max_x_m,
        context_max_y_m=second_tile.spec.context_max_y_m,
    )
    memberships = (
        SplitMembership(
            SplitName.SMOKE,
            first_group.tile_ids,
            (first_group.leakage_group_id,),
        ),
        SplitMembership(
            SplitName.DEVELOPMENT,
            second_group.tile_ids,
            (second_group.leakage_group_id,),
        ),
        SplitMembership(SplitName.PILOT, (), ()),
        SplitMembership(SplitName.TEST, (), ()),
        SplitMembership(SplitName.HELD_OUT_CITY, (), ()),
    )
    identity = canonical_sha256(
        "canonical-validation-report",
        validation.canonical_validation_report_to_dict(report),
    )
    forged = CanonicalSplitManifest(
        schema_version="1.0",
        dataset_id=collection.dataset_id,
        dataset_version=collection.dataset_version,
        unit_type=SplitUnitType.LAYOUT_TILE,
        config=_config(
            smoke=1,
            development=0,
            pilot=0,
            test=0,
        ),
        source_validation_report_identity=identity,
        source_tile_collection_identity=canonical_sha256(
            "geographic-tile-collection",
            tiling.geographic_tile_collection_to_dict(collection),
        ),
        source_unit_count=2,
        memberships=memberships,
        leakage_groups=(first_group, second_group),
    )
    with pytest.raises(SchemaError, match="buffered overlap"):
        splits.validate_split_manifest(
            forged,
            scenario_manifest=scenarios,
            validation_report=report,
            tile_collection=collection,
        )


def test_complete_synthetic_split_integration_is_deterministic(
    tmp_path: Path,
) -> None:
    dataset = synthetic.build_synthetic_dataset()
    scenario_table = parquet_io.scenario_records_to_table(
        synthetic.synthetic_scenarios(dataset)
    )
    frame_table = parquet_io.coordinate_frame_records_to_table(
        synthetic.synthetic_coordinate_frames(dataset)
    )
    agent_table = parquet_io.agent_records_to_table(synthetic.synthetic_agents(dataset))
    sample_table = parquet_io.trajectories_to_table(
        synthetic.synthetic_trajectories(dataset)
    )
    validation_config = validation.CanonicalValidationConfig(
        minimum_valid_sample_count=2,
        minimum_valid_duration_ns=0,
    )
    report = validation.validate_canonical_tables(
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        config=validation_config,
    )
    tile_config = tiling.GeographicTileConfig(
        tile_size_m=5,
        context_buffer_m=1,
    )
    tile_collection = tiling.build_geographic_tiles(
        scenario_table,
        agent_table,
        sample_table,
        report,
        config=tile_config,
    )
    groups = splits.build_geographic_leakage_groups(
        tile_collection,
        scenario_table,
    )
    motion_count = len(report.included_scenario_ids)
    motion_config = _config(
        seed=123,
        namespace="synthetic-motion",
        smoke=2,
        development=4,
        pilot=3,
        test=motion_count - 9,
    )
    layout_config = _config(
        seed=456,
        namespace="synthetic-layout",
        smoke=1 if len(groups) >= 1 else 0,
        development=1 if len(groups) >= 2 else 0,
        pilot=1 if len(groups) >= 3 else 0,
        test=None,
    )
    motion_manifest = splits.generate_motion_split_manifest(
        scenario_table,
        report,
        config=motion_config,
    )
    layout_manifest = splits.generate_layout_split_manifest(
        tile_collection,
        scenario_table,
        report,
        config=layout_config,
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
        tile_collection=tile_collection,
    )

    first_run = artifact_store.prepare_run_directory(
        tmp_path,
        "results",
        "run:synthetic:splits:001",
        reserve_fraction=0.0,
    )
    dataset_artifacts = synthetic.materialize_synthetic_dataset(
        first_run,
        dataset,
        row_group_size=3,
    )
    paths = validation.CanonicalDatasetPaths(
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
    assert (
        validation.validate_canonical_parquet_dataset(
            tmp_path,
            paths,
            config=validation_config,
            batch_size=2,
        )
        == report
    )
    first_artifacts = splits.materialize_canonical_split_manifests(
        first_run,
        motion_manifest,
        layout_manifest,
    )
    assert splits.verify_canonical_split_artifacts(
        tmp_path,
        first_artifacts,
    ) == (motion_manifest, layout_manifest)
    assert artifact_store.list_partial_artifacts(first_run) == ()
    artifact_store.finalize_run_directory(first_run)

    second_run = artifact_store.prepare_run_directory(
        tmp_path,
        "results",
        "run:synthetic:splits:002",
        reserve_fraction=0.0,
    )
    second_artifacts = splits.materialize_canonical_split_manifests(
        second_run,
        motion_manifest,
        layout_manifest,
    )
    assert (
        first_artifacts.motion_manifest.content_checksum
        == second_artifacts.motion_manifest.content_checksum
    )
    assert (
        first_artifacts.layout_manifest.content_checksum
        == second_artifacts.layout_manifest.content_checksum
    )
    assert (
        first_artifacts.split_summary.content_checksum
        == second_artifacts.split_summary.content_checksum
    )
    assert artifact_store.list_partial_artifacts(second_run) == ()
    artifact_store.finalize_run_directory(second_run)


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


def test_av2_split_integration_keeps_overlap_groups_indivisible(
    tmp_path: Path,
) -> None:
    motion_root = tmp_path / "motion"
    motion_root.mkdir()
    pq.write_table(
        pa.Table.from_pylist(_av2_motion_rows()),
        motion_root / "scenario.parquet",
    )
    motion = av2_motion.load_av2_motion_scenario(
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
    scenario_table = parquet_io.scenario_records_to_table((motion.scenario,))
    frame_table = parquet_io.coordinate_frame_records_to_table(
        (motion.coordinate_frame,)
    )
    agent_table = parquet_io.agent_records_to_table(motion.agents)
    sample_table = parquet_io.trajectories_to_table(motion.trajectories)
    map_table = parquet_io.vector_map_elements_to_table(map_conversion.elements)
    report = validation.validate_canonical_tables(
        scenario_table,
        frame_table,
        agent_table,
        sample_table,
        map_table,
        config=validation.CanonicalValidationConfig(
            minimum_valid_sample_count=2,
            minimum_valid_duration_ns=0,
            require_source_map=True,
        ),
    )
    tile_collection = tiling.build_geographic_tiles(
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
    motion_manifest = splits.generate_motion_split_manifest(
        scenario_table,
        report,
        config=_config(
            seed=4,
            namespace="av2-motion",
            smoke=0,
            development=0,
            pilot=0,
        ),
    )
    layout_manifest = splits.generate_layout_split_manifest(
        tile_collection,
        scenario_table,
        report,
        config=_config(
            seed=5,
            namespace="av2-layout",
            smoke=0,
            development=0,
            pilot=0,
        ),
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
        tile_collection=tile_collection,
    )
    assigned_group_ids = tuple(
        group_id
        for membership in layout_manifest.memberships
        for group_id in membership.leakage_group_ids
    )
    assert set(assigned_group_ids) == {
        group.leakage_group_id for group in layout_manifest.leakage_groups
    }
    assert any(
        geometry_from_canonical_wkb(element.geometry_wkb).has_z
        for element in map_conversion.elements
    )
    run = artifact_store.prepare_run_directory(
        tmp_path,
        "results",
        "run:av2:splits:001",
        reserve_fraction=0.0,
    )
    artifacts = splits.materialize_canonical_split_manifests(
        run,
        motion_manifest,
        layout_manifest,
    )
    assert splits.verify_canonical_split_artifacts(tmp_path, artifacts) == (
        motion_manifest,
        layout_manifest,
    )
    artifact_store.finalize_run_directory(run)
