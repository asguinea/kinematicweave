"""Tests for deterministic synthetic canonical motion data."""

import ast
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import importlib
from itertools import pairwise
import json
import math
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

import kinematicweave.artifact_store as artifact_store
from kinematicweave.canonical import canonical_json_bytes
from kinematicweave.data import parquet_io, synthetic
from kinematicweave.data.schemas import CanonicalSchemaName, get_arrow_schema
from kinematicweave.domain.records import (
    OriginType,
    Trajectory,
    validate_scenario_bundle,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "kinematicweave" / "data" / "synthetic.py"
KINDS = (
    "straight_constant_speed",
    "acceleration_deceleration",
    "stop",
    "left_turn",
    "right_turn",
    "irregular_sampling",
    "missing_gap",
    "t_junction",
    "four_way_junction",
    "merge",
    "split",
    "disconnected_paths",
    "crossing_disconnected",
    "grade_separated_crossing",
    "reroute_available",
    "reroute_unavailable",
)


@pytest.fixture(autouse=True)
def _refresh_synthetic_module() -> None:
    """Use classes current after import-safety tests reload dependencies."""
    importlib.reload(synthetic)


def _run_directory(
    tmp_path: Path,
    run_id: str = "synthetic-test",
) -> artifact_store.RunDirectory:
    return artifact_store.prepare_run_directory(
        tmp_path,
        "results",
        f"test:{run_id}",
        reserve_fraction=0.0,
    )


def _scenario(kind: str) -> synthetic.SyntheticScenario:
    return synthetic.build_synthetic_scenario(kind)


def _trajectory(kind: str, index: int = 0) -> Trajectory:
    return _scenario(kind).trajectories[index]


def _artifact_paths(artifacts: Any) -> tuple[Path, ...]:
    return (
        artifacts.scenario_index.relative_path,
        artifacts.scenario_manifest.written_artifact.relative_path,
        artifacts.coordinate_frame_metadata.written_artifact.relative_path,
        artifacts.agent_metadata.written_artifact.relative_path,
        artifacts.trajectory_samples.written_artifact.relative_path,
    )


def _materialize(
    tmp_path: Path,
    run_id: str = "synthetic-test",
) -> tuple[
    artifact_store.RunDirectory,
    synthetic.SyntheticDataset,
    synthetic.SyntheticDatasetArtifacts,
]:
    run = _run_directory(tmp_path, run_id)
    dataset = synthetic.build_synthetic_dataset()
    artifacts = synthetic.materialize_synthetic_dataset(run, dataset)
    return run, dataset, artifacts


def test_public_api_and_exact_enum_order() -> None:
    assert synthetic.__all__ == [
        "SyntheticDataset",
        "SyntheticDatasetArtifacts",
        "SyntheticScenario",
        "SyntheticScenarioKind",
        "build_synthetic_dataset",
        "build_synthetic_scenario",
        "materialize_synthetic_dataset",
        "synthetic_agents",
        "synthetic_coordinate_frames",
        "synthetic_dataset_index_to_dict",
        "synthetic_scenario_kinds",
        "synthetic_scenarios",
        "synthetic_trajectories",
        "verify_synthetic_dataset_artifacts",
    ]
    assert tuple(item.value for item in synthetic.SyntheticScenarioKind) == KINDS
    assert synthetic.synthetic_scenario_kinds() == tuple(
        synthetic.SyntheticScenarioKind
    )


@pytest.mark.parametrize(
    "model_name,field_names",
    [
        (
            "SyntheticScenario",
            ("kind", "scenario", "coordinate_frame", "agents", "trajectories"),
        ),
        (
            "SyntheticDataset",
            ("schema_version", "dataset_id", "dataset_version", "scenarios"),
        ),
        (
            "SyntheticDatasetArtifacts",
            (
                "dataset_id",
                "dataset_version",
                "scenario_index",
                "scenario_manifest",
                "coordinate_frame_metadata",
                "agent_metadata",
                "trajectory_samples",
            ),
        ),
    ],
)
def test_models_are_frozen_slotted_and_have_exact_fields(
    model_name: str,
    field_names: tuple[str, ...],
) -> None:
    model = getattr(synthetic, model_name)
    assert tuple(field.name for field in fields(model)) == field_names
    assert "__slots__" in model.__dict__

    instance = (
        synthetic.build_synthetic_scenario(KINDS[0])
        if model_name == "SyntheticScenario"
        else synthetic.build_synthetic_dataset((KINDS[0],))
    )
    if model_name != "SyntheticDatasetArtifacts":
        with pytest.raises(FrozenInstanceError):
            if model_name == "SyntheticScenario":
                instance.kind = synthetic.SyntheticScenarioKind.STOP  # type: ignore[misc,union-attr]
            else:
                instance.dataset_id = "changed"  # type: ignore[misc,union-attr]


def test_model_validation_and_sequence_copying() -> None:
    first = _scenario(KINDS[0])
    supplied: Any = [first]
    dataset = synthetic.SyntheticDataset(
        "1.0", "synthetic_kinematicweave", "1.0", supplied
    )
    supplied.clear()
    assert dataset.scenarios == (first,)
    assert isinstance(dataset.scenarios, tuple)

    for values in (
        ("2.0", "synthetic_kinematicweave", "1.0"),
        ("1.0", "other", "1.0"),
        ("1.0", "synthetic_kinematicweave", "2.0"),
    ):
        with pytest.raises(ValidationError):
            synthetic.SyntheticDataset(*values, (first,))
    with pytest.raises(ValidationError):
        synthetic.SyntheticDataset("1.0", "synthetic_kinematicweave", "1.0", ())
    with pytest.raises(ValidationError):
        invalid: Any = "bad"
        synthetic.SyntheticScenario(
            invalid,
            first.scenario,
            first.coordinate_frame,
            cast(Any, []),
            cast(Any, []),
        )


def test_duplicate_kind_identifier_and_mixed_metadata_are_rejected() -> None:
    first = _scenario(KINDS[0])
    with pytest.raises(ValidationError, match="kinds"):
        synthetic.SyntheticDataset(
            "1.0", "synthetic_kinematicweave", "1.0", (first, first)
        )

    malformed = object.__new__(synthetic.SyntheticScenario)
    for field in fields(synthetic.SyntheticScenario):
        object.__setattr__(malformed, field.name, getattr(first, field.name))
    object.__setattr__(
        malformed,
        "kind",
        synthetic.SyntheticScenarioKind.ACCELERATION_DECELERATION,
    )
    with pytest.raises(ValidationError, match="identifiers"):
        synthetic.SyntheticDataset(
            "1.0", "synthetic_kinematicweave", "1.0", (first, malformed)
        )

    mixed_record = replace(first.scenario, dataset_version="2.0")
    mixed = object.__new__(synthetic.SyntheticScenario)
    for name, value in (
        ("kind", first.kind),
        ("scenario", mixed_record),
        ("coordinate_frame", first.coordinate_frame),
        ("agents", first.agents),
        ("trajectories", first.trajectories),
    ):
        object.__setattr__(mixed, name, value)
    with pytest.raises(ValidationError, match="dataset identifier and version"):
        synthetic.SyntheticDataset("1.0", "synthetic_kinematicweave", "1.0", (mixed,))


@pytest.mark.parametrize("kind", KINDS)
def test_each_scenario_builds_deterministically_with_exact_identity(kind: str) -> None:
    first = synthetic.build_synthetic_scenario(kind)
    second = synthetic.build_synthetic_scenario(synthetic.SyntheticScenarioKind(kind))
    assert first == second
    assert first is not second
    assert first.scenario is not second.scenario
    assert first.scenario.scenario_id == f"scenario:synthetic:{kind}"
    assert first.coordinate_frame.coordinate_frame_id == (
        f"frame:synthetic:{kind}:local"
    )
    assert tuple(agent.agent_id for agent in first.agents) == tuple(
        f"agent:synthetic:{kind}:{agent.agent_id.rsplit(':', 1)[-1]}"
        for agent in first.agents
    )
    assert tuple(
        trajectory.trajectory_id for trajectory in first.trajectories
    ) == tuple(
        f"trajectory:synthetic:{kind}:{agent.agent_id.rsplit(':', 1)[-1]}"
        for agent in first.agents
    )
    validate_scenario_bundle(
        first.scenario,
        first.coordinate_frame,
        first.agents,
        first.trajectories,
    )


def test_shared_metadata_and_coordinate_frame_conventions() -> None:
    for item in synthetic.build_synthetic_dataset().scenarios:
        record = item.scenario
        frame = item.coordinate_frame
        assert (
            record.dataset_id,
            record.dataset_version,
            record.split_name,
            record.city_or_region,
            record.adapter_name,
            record.adapter_version,
        ) == (
            "synthetic_kinematicweave",
            "1.0",
            "synthetic",
            "synthetic",
            "synthetic_generator",
            "1.0",
        )
        assert record.source_scenario_id is None
        assert record.source_crs is None
        assert record.source_checksum is None
        assert not record.source_map_available
        assert (
            frame.frame_type,
            frame.axis_convention,
            frame.distance_unit,
            frame.angle_unit,
            frame.timestamp_unit,
        ) == (
            "local_cartesian",
            "right_handed_x_y_z_up",
            "m",
            "rad",
            "ns",
        )
        assert frame.parent_frame_id is None
        assert frame.transform_to_parent_4x4 is None
        assert (frame.origin_x_m, frame.origin_y_m) == (0.0, 0.0)
        assert str(frame.origin_type) == OriginType.SYNTHETIC.value
        elevated = item.kind.value == "grade_separated_crossing"
        assert record.has_elevation is elevated
        assert frame.has_elevation is elevated
        assert frame.origin_z_m == (0.0 if elevated else None)


def test_full_dataset_counts_subset_order_and_invalid_inputs() -> None:
    dataset = synthetic.build_synthetic_dataset()
    assert tuple(item.kind.value for item in dataset.scenarios) == KINDS
    assert len(dataset.scenarios) == 16
    assert len(synthetic.synthetic_agents(dataset)) == 27
    assert len(synthetic.synthetic_trajectories(dataset)) == 27
    assert (
        sum(
            trajectory.sample_count
            for trajectory in synthetic.synthetic_trajectories(dataset)
        )
        == 293
    )

    selected = [KINDS[4], KINDS[0], KINDS[8]]
    subset = synthetic.build_synthetic_dataset(selected)
    selected.reverse()
    assert tuple(item.kind.value for item in subset.scenarios) == (
        KINDS[4],
        KINDS[0],
        KINDS[8],
    )
    for invalid in ([], [KINDS[0], KINDS[0]], ["unknown"]):
        with pytest.raises(ValidationError):
            synthetic.build_synthetic_dataset(invalid)
    with pytest.raises(ValidationError):
        synthetic.build_synthetic_dataset("straight_constant_speed")
    with pytest.raises(ValidationError):
        synthetic.build_synthetic_scenario("unknown")


def test_straight_constant_speed_oracle() -> None:
    samples = _trajectory("straight_constant_speed").samples
    assert [sample.timestamp_ns for sample in samples] == [
        index * 1_000_000_000 for index in range(11)
    ]
    assert [sample.x_m for sample in samples] == pytest.approx(range(11))
    assert {sample.y_m for sample in samples} == {0.0}
    assert {sample.heading_rad for sample in samples} == {0.0}
    assert {sample.speed_mps for sample in samples} == {1.0}
    assert {sample.acceleration_x_mps2 for sample in samples} == {0.0}


def test_acceleration_deceleration_and_stop_oracles() -> None:
    accelerating = _trajectory("acceleration_deceleration").samples
    speeds = [sample.speed_mps for sample in accelerating]
    assert speeds == pytest.approx((0, 0.4, 0.8, 1.2, 1.6, 2, 1.6, 1.2, 0.8, 0.4, 0))
    assert all(
        current.x_m >= previous.x_m for previous, current in pairwise(accelerating)
    )
    accelerations = [sample.acceleration_x_mps2 for sample in accelerating]
    assert all(value is not None for value in accelerations)
    numeric_accelerations = [cast(float, value) for value in accelerations]
    assert max(numeric_accelerations) > 0
    assert min(numeric_accelerations) < 0

    stopped = _trajectory("stop").samples
    stationary = stopped[3:7]
    assert {sample.x_m for sample in stationary} == {3.0}
    assert {sample.speed_mps for sample in stationary} == {0.0}
    assert {sample.heading_rad for sample in stationary} == {0.0}
    assert stopped[7].x_m > stopped[6].x_m
    assert stopped[7].speed_mps == 1.0


@pytest.mark.parametrize(
    "kind,expected_y,expected_heading",
    [
        ("left_turn", 5.0, math.pi / 2),
        ("right_turn", -5.0, -math.pi / 2),
    ],
)
def test_turn_oracles(
    kind: str,
    expected_y: float,
    expected_heading: float,
) -> None:
    samples = _trajectory(kind).samples
    assert (samples[0].x_m, samples[0].y_m, samples[0].heading_rad) == (
        0.0,
        0.0,
        0.0,
    )
    assert samples[-1].x_m == pytest.approx(5.0)
    assert samples[-1].y_m == pytest.approx(expected_y)
    assert samples[-1].heading_rad == pytest.approx(expected_heading)
    assert all(sample.heading_rad is not None for sample in samples)
    assert all(
        math.isfinite(value)
        for sample in samples
        for value in (
            sample.x_m,
            sample.y_m,
            cast(float, sample.heading_rad),
        )
    )


def test_irregular_sampling_and_missing_gap_oracles() -> None:
    irregular = _trajectory("irregular_sampling").samples
    assert [sample.timestamp_ns for sample in irregular] == [
        0,
        400_000_000,
        1_100_000_000,
        2_500_000_000,
        4_000_000_000,
        6_200_000_000,
        9_000_000_000,
    ]
    assert [sample.sample_index for sample in irregular] == list(range(7))
    assert {sample.speed_mps for sample in irregular} == {1.0}

    missing = _trajectory("missing_gap").samples
    assert len(missing) == 11
    for index, sample in enumerate(missing):
        if index in {4, 5}:
            assert not sample.is_observed
            assert not sample.is_valid
            assert sample.quality_flags == ("missing_observation",)
            assert math.isfinite(sample.x_m)
        else:
            assert sample.is_observed
            assert sample.is_valid
            assert sample.quality_flags == ()


def test_junction_oracles() -> None:
    t_tracks = _scenario("t_junction").trajectories
    assert len(t_tracks) == 3
    endpoints = [
        (
            track.samples[0].x_m,
            track.samples[0].y_m,
            track.samples[-1].x_m,
            track.samples[-1].y_m,
        )
        for track in t_tracks
    ]
    assert endpoints == [
        (0.0, -5.0, -5.0, 0.0),
        (0.0, -5.0, 5.0, 0.0),
        (-5.0, 0.0, 5.0, 0.0),
    ]

    four_way = _scenario("four_way_junction").trajectories
    assert len(four_way) == 4
    assert {
        (
            track.samples[0].x_m,
            track.samples[0].y_m,
            track.samples[-1].x_m,
            track.samples[-1].y_m,
        )
        for track in four_way
    } == {
        (0.0, -5.0, 0.0, 5.0),
        (0.0, 5.0, 0.0, -5.0),
        (-5.0, 0.0, 5.0, 0.0),
        (5.0, 0.0, -5.0, 0.0),
    }
    assert all(
        any(sample.x_m == sample.y_m == 0.0 for sample in track.samples)
        for track in four_way
    )


def test_merge_split_and_disconnected_oracles() -> None:
    merged = _scenario("merge").trajectories
    assert {track.samples[0].y_m for track in merged} == {-2.0, 2.0}
    assert {track.samples[-1].y_m for track in merged} == {0.0}
    assert merged[0].trajectory_id != merged[1].trajectory_id

    split = _scenario("split").trajectories
    assert {track.samples[0].y_m for track in split} == {0.0}
    assert {track.samples[-1].y_m for track in split} == {-2.0, 2.0}

    disconnected = _scenario("disconnected_paths").trajectories
    assert {sample.y_m for sample in disconnected[0].samples} == {-5.0}
    assert {sample.y_m for sample in disconnected[1].samples} == {5.0}


def test_crossing_elevation_and_reroute_oracles() -> None:
    crossing = _scenario("crossing_disconnected")
    assert all(
        sample.z_m is None
        for track in crossing.trajectories
        for sample in track.samples
    )
    assert all(
        any(sample.x_m == sample.y_m == 0.0 for sample in track.samples)
        for track in crossing.trajectories
    )

    elevated = _scenario("grade_separated_crossing")
    assert elevated.scenario.has_elevation
    assert [
        {sample.z_m for sample in track.samples} for track in elevated.trajectories
    ] == [{0.0}, {5.0}]
    assert all(
        "grade_separation_ambiguous" not in sample.quality_flags
        for track in elevated.trajectories
        for sample in track.samples
    )

    available = _scenario("reroute_available").trajectories
    assert len(available) == 2
    assert tuple((sample.x_m, sample.y_m) for sample in available[0].samples) != tuple(
        (sample.x_m, sample.y_m) for sample in available[1].samples
    )
    assert len(_scenario("reroute_unavailable").trajectories) == 1


def test_all_bundle_metadata_intervals_and_elevation_are_consistent() -> None:
    for item in synthetic.build_synthetic_dataset().scenarios:
        validate_scenario_bundle(
            item.scenario,
            item.coordinate_frame,
            item.agents,
            item.trajectories,
        )
        assert item.scenario.agent_count == len(item.agents)
        for agent, trajectory in zip(item.agents, item.trajectories, strict=True):
            assert agent.agent_id == trajectory.agent_id
            assert agent.sample_count == trajectory.sample_count
            assert agent.first_time_ns == trajectory.start_time_ns
            assert agent.last_time_ns == trajectory.end_time_ns
            assert item.scenario.start_time_ns <= trajectory.start_time_ns
            assert trajectory.end_time_ns <= item.scenario.end_time_ns
            assert str(agent.origin_type) == OriginType.SYNTHETIC.value
            assert str(trajectory.origin_type) == OriginType.SYNTHETIC.value
            assert all(
                str(sample.origin_type) == OriginType.SYNTHETIC.value
                for sample in trajectory.samples
            )
            if item.scenario.has_elevation:
                assert all(sample.z_m is not None for sample in trajectory.samples)
            else:
                assert all(sample.z_m is None for sample in trajectory.samples)


def test_flattening_preserves_order_and_returns_fresh_tuples() -> None:
    dataset = synthetic.build_synthetic_dataset(
        ("merge", "straight_constant_speed", "t_junction")
    )
    expected_agents = tuple(
        agent for item in dataset.scenarios for agent in item.agents
    )
    expected_trajectories = tuple(
        trajectory for item in dataset.scenarios for trajectory in item.trajectories
    )
    assert synthetic.synthetic_scenarios(dataset) == tuple(
        item.scenario for item in dataset.scenarios
    )
    assert synthetic.synthetic_coordinate_frames(dataset) == tuple(
        item.coordinate_frame for item in dataset.scenarios
    )
    assert synthetic.synthetic_agents(dataset) == expected_agents
    assert synthetic.synthetic_trajectories(dataset) == expected_trajectories
    assert synthetic.synthetic_agents(dataset) is not synthetic.synthetic_agents(
        dataset
    )
    functions: tuple[Any, ...] = (
        synthetic.synthetic_scenarios,
        synthetic.synthetic_coordinate_frames,
        synthetic.synthetic_agents,
        synthetic.synthetic_trajectories,
    )
    for function in functions:
        with pytest.raises(ValidationError):
            function("invalid")


def test_index_is_ordered_json_compatible_deterministic_and_detached() -> None:
    dataset = synthetic.build_synthetic_dataset()
    index = synthetic.synthetic_dataset_index_to_dict(dataset)
    assert tuple(index) == (
        "schema_version",
        "dataset_id",
        "dataset_version",
        "scenario_count",
        "agent_count",
        "trajectory_count",
        "scenarios",
    )
    assert (
        index["scenario_count"],
        index["agent_count"],
        index["trajectory_count"],
    ) == (
        16,
        27,
        27,
    )
    scenarios = index["scenarios"]
    assert isinstance(scenarios, list)
    assert tuple(scenarios[0]) == (
        "kind",
        "scenario_id",
        "coordinate_frame_id",
        "agent_ids",
        "trajectory_ids",
        "start_time_ns",
        "end_time_ns",
        "has_elevation",
    )
    assert json.loads(json.dumps(index)) == index
    assert synthetic.synthetic_dataset_index_to_dict(dataset) == index
    scenarios[0]["agent_ids"].clear()
    assert len(dataset.scenarios[0].agents) == 1
    assert synthetic.synthetic_dataset_index_to_dict(dataset)["agent_count"] == 27


def test_materialization_paths_schemas_rows_and_verification(tmp_path: Path) -> None:
    run, dataset, artifacts = _materialize(tmp_path)
    expected_paths = tuple(
        Path("results")
        / "runs"
        / run.path.name
        / "artifacts"
        / "synthetic_dataset"
        / name
        for name in (
            "scenario_index.json",
            "scenario_manifest.parquet",
            "coordinate_frame_metadata.parquet",
            "agent_metadata.parquet",
            "trajectory_samples.parquet",
        )
    )
    assert _artifact_paths(artifacts) == expected_paths
    assert tuple(
        artifact.schema_name.value
        for artifact in (
            artifacts.scenario_manifest,
            artifacts.coordinate_frame_metadata,
            artifacts.agent_metadata,
            artifacts.trajectory_samples,
        )
    ) == (
        CanonicalSchemaName.SCENARIO_MANIFEST.value,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA.value,
        CanonicalSchemaName.AGENT_METADATA.value,
        CanonicalSchemaName.TRAJECTORY_SAMPLES.value,
    )
    assert (
        artifacts.scenario_manifest.row_count,
        artifacts.coordinate_frame_metadata.row_count,
        artifacts.agent_metadata.row_count,
        artifacts.trajectory_samples.row_count,
    ) == (16, 16, 27, 293)
    for relative_path in _artifact_paths(artifacts):
        path = tmp_path / relative_path
        assert path.stat().st_size > 0
        assert not path.with_name(f".{path.name}.partial").exists()
    assert json.loads((tmp_path / expected_paths[0]).read_text("utf-8")) == (
        synthetic.synthetic_dataset_index_to_dict(dataset)
    )
    synthetic.verify_synthetic_dataset_artifacts(tmp_path, artifacts)
    assert not any(run.manifests_path.iterdir())
    assert not list(artifact_store.list_partial_artifacts(run))


def test_bounded_reading_and_polars_scanning(tmp_path: Path) -> None:
    _, _, artifacts = _materialize(tmp_path)
    for artifact in (
        artifacts.scenario_manifest,
        artifacts.coordinate_frame_metadata,
        artifacts.agent_metadata,
        artifacts.trajectory_samples,
    ):
        batches = tuple(
            parquet_io.iter_canonical_parquet_batches(
                tmp_path,
                (artifact.written_artifact.relative_path,),
                artifact.schema_name,
                batch_size=5,
            )
        )
        assert sum(batch.num_rows for batch in batches) == artifact.row_count
        assert all(batch.num_rows <= 5 for batch in batches)
        frame = parquet_io.scan_canonical_parquet(
            tmp_path,
            (artifact.written_artifact.relative_path,),
            artifact.schema_name,
        ).collect()
        assert isinstance(frame, pl.DataFrame)
        assert frame.height == artifact.row_count
        assert frame.columns == get_arrow_schema(artifact.schema_name).names


def test_overwrite_and_finalized_write_are_rejected(tmp_path: Path) -> None:
    run, dataset, _ = _materialize(tmp_path)
    with pytest.raises(ArtifactError):
        synthetic.materialize_synthetic_dataset(run, dataset)
    artifact_store.finalize_run_directory(run)
    with pytest.raises(ArtifactError):
        synthetic.materialize_synthetic_dataset(
            run,
            dataset,
            relative_directory="artifacts/another",
        )


def test_equivalent_runs_have_deterministic_checksums(tmp_path: Path) -> None:
    first_run, dataset, first = _materialize(tmp_path, "deterministic-one")
    second_run = _run_directory(tmp_path, "deterministic-two")
    second = synthetic.materialize_synthetic_dataset(second_run, dataset)
    first_checksums = (
        first.scenario_index.content_checksum,
        first.scenario_manifest.written_artifact.content_checksum,
        first.coordinate_frame_metadata.written_artifact.content_checksum,
        first.agent_metadata.written_artifact.content_checksum,
        first.trajectory_samples.written_artifact.content_checksum,
    )
    second_checksums = (
        second.scenario_index.content_checksum,
        second.scenario_manifest.written_artifact.content_checksum,
        second.coordinate_frame_metadata.written_artifact.content_checksum,
        second.agent_metadata.written_artifact.content_checksum,
        second.trajectory_samples.written_artifact.content_checksum,
    )
    assert first_checksums == second_checksums
    artifact_store.finalize_run_directory(first_run)
    artifact_store.finalize_run_directory(second_run)


def test_later_write_failure_preserves_only_completed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run_directory(tmp_path)
    dataset = synthetic.build_synthetic_dataset()
    original = vars(synthetic)["atomic_write_canonical_parquet"]
    calls = 0

    def fail_second(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated later write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        synthetic,
        "atomic_write_canonical_parquet",
        fail_second,
    )
    with pytest.raises(OSError, match="simulated"):
        synthetic.materialize_synthetic_dataset(run, dataset)
    target = run.artifacts_path / "synthetic_dataset"
    assert sorted(path.name for path in target.iterdir()) == [
        "scenario_index.json",
        "scenario_manifest.parquet",
    ]
    assert not list(artifact_store.list_partial_artifacts(run))


def test_verification_rejects_altered_index_and_wrong_counts(tmp_path: Path) -> None:
    _, _, artifacts = _materialize(tmp_path)
    path = tmp_path / artifacts.scenario_index.relative_path
    path.write_bytes(
        path.read_bytes().replace(
            b"synthetic_kinematicweave", b"synthetic_kinematicweavf"
        )
    )
    with pytest.raises(ArtifactError, match="checksum"):
        synthetic.verify_synthetic_dataset_artifacts(tmp_path, artifacts)

    decoded = synthetic.synthetic_dataset_index_to_dict(
        synthetic.build_synthetic_dataset()
    )
    decoded["scenario_count"] = 99
    data = canonical_json_bytes(decoded)
    path.write_bytes(data)
    written = artifact_store.WrittenArtifact(
        artifacts.scenario_index.relative_path,
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    changed = replace(artifacts, scenario_index=written)
    with pytest.raises(SchemaError, match="scenario_count"):
        synthetic.verify_synthetic_dataset_artifacts(tmp_path, changed)


def test_verification_rejects_missing_and_corrupt_parquet(tmp_path: Path) -> None:
    _, _, artifacts = _materialize(tmp_path, "missing")
    missing = (
        tmp_path / artifacts.coordinate_frame_metadata.written_artifact.relative_path
    )
    missing.unlink()
    with pytest.raises(ArtifactError):
        synthetic.verify_synthetic_dataset_artifacts(tmp_path, artifacts)

    _, _, corrupt_artifacts = _materialize(tmp_path, "corrupt")
    corrupt = tmp_path / corrupt_artifacts.agent_metadata.written_artifact.relative_path
    corrupt.write_bytes(b"not parquet")
    replacement = artifact_store.WrittenArtifact(
        corrupt_artifacts.agent_metadata.written_artifact.relative_path,
        corrupt.stat().st_size,
        hashlib.sha256(corrupt.read_bytes()).hexdigest(),
    )
    changed_agent = replace(
        corrupt_artifacts.agent_metadata,
        written_artifact=replacement,
    )
    changed = replace(corrupt_artifacts, agent_metadata=changed_agent)
    with pytest.raises(SchemaError):
        synthetic.verify_synthetic_dataset_artifacts(tmp_path, changed)


def test_artifact_model_rejects_identity_and_schema_mismatches(
    tmp_path: Path,
) -> None:
    _, _, artifacts = _materialize(tmp_path)
    with pytest.raises(ValidationError):
        replace(artifacts, dataset_id="other")
    with pytest.raises(ValidationError):
        replace(artifacts, dataset_version="2.0")
    with pytest.raises(ValidationError):
        replace(artifacts, scenario_index=cast(Any, "invalid"))
    with pytest.raises(ValidationError):
        replace(
            artifacts,
            scenario_manifest=artifacts.agent_metadata,
        )


def test_module_imports_are_side_effect_free_and_dependency_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse(MODULE_PATH.read_text("utf-8"))
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    forbidden = {
        "argoverse",
        "geopandas",
        "networkx",
        "numpy",
        "rerun",
        "shapely",
        "subprocess",
        "torch",
    }
    assert imports.isdisjoint(forbidden)

    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.rglob("*"))
    importlib.reload(synthetic)
    assert tuple(tmp_path.rglob("*")) == before


def test_complete_dataset_integration(tmp_path: Path) -> None:
    dataset = synthetic.build_synthetic_dataset()
    for item in dataset.scenarios:
        validate_scenario_bundle(
            item.scenario,
            item.coordinate_frame,
            item.agents,
            item.trajectories,
        )
    first_run = _run_directory(tmp_path, "integration-one")
    second_run = _run_directory(tmp_path, "integration-two")
    first = synthetic.materialize_synthetic_dataset(first_run, dataset)
    second = synthetic.materialize_synthetic_dataset(second_run, dataset)
    synthetic.verify_synthetic_dataset_artifacts(tmp_path, first)
    synthetic.verify_synthetic_dataset_artifacts(tmp_path, second)

    expected_rows = (16, 16, 27, 293)
    first_artifacts = (
        first.scenario_manifest,
        first.coordinate_frame_metadata,
        first.agent_metadata,
        first.trajectory_samples,
    )
    second_artifacts = (
        second.scenario_manifest,
        second.coordinate_frame_metadata,
        second.agent_metadata,
        second.trajectory_samples,
    )
    for artifact, expected in zip(first_artifacts, expected_rows, strict=True):
        batches = tuple(
            parquet_io.iter_canonical_parquet_batches(
                tmp_path,
                (artifact.written_artifact.relative_path,),
                artifact.schema_name,
                batch_size=4,
            )
        )
        assert sum(batch.num_rows for batch in batches) == expected
        collected = parquet_io.scan_canonical_parquet(
            tmp_path,
            (artifact.written_artifact.relative_path,),
            artifact.schema_name,
        ).collect()
        assert collected.height == expected
        assert collected.columns == get_arrow_schema(artifact.schema_name).names
    assert tuple(
        artifact.written_artifact.content_checksum for artifact in first_artifacts
    ) == tuple(
        artifact.written_artifact.content_checksum for artifact in second_artifacts
    )
    assert (
        first.scenario_index.content_checksum == second.scenario_index.content_checksum
    )
    artifact_store.finalize_run_directory(first_run)
    artifact_store.finalize_run_directory(second_run)
    assert not list(artifact_store.list_partial_artifacts(first_run))
    assert not list(artifact_store.list_partial_artifacts(second_run))
