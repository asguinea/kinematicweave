"""Focused tests for comparable deterministic motion baselines."""

from dataclasses import FrozenInstanceError
import importlib
import math
from pathlib import Path
import subprocess
import sys

import pytest

from kinematicweave.artifact_store import finalize_run_directory, prepare_run_directory
from kinematicweave.baselines.motion import (
    BaselineMethod,
    MotionBaselineConfig,
    baseline_config_from_json,
    baseline_config_identity,
    baseline_config_to_canonical_json,
    baseline_keyframe_count,
    baseline_source_keyframes,
    encode_scenario_baseline,
    encode_trajectory_baseline,
    evaluate_raw_trajectory,
    included_motion_trajectories,
    read_canonical_scenario_bundle,
    required_baseline_grid,
    validate_baseline_replay,
)
from kinematicweave.codecs.exact import replay_track
from kinematicweave.data.baseline_artifacts import (
    materialize_equivalent_procedural_artifacts,
    materialize_equivalent_raw_artifacts,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    atomic_write_canonical_parquet,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    trajectories_to_table,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.data.synthetic import (
    SyntheticScenarioKind,
    build_synthetic_scenario,
)
from kinematicweave.domain.procedural import ProceduralPrimitiveType
from kinematicweave.domain.records import (
    AgentClass,
    OriginType,
    Trajectory,
    TrajectorySampleRecord,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError


def _sample(
    index: int,
    *,
    timestamp_ns: int | None = None,
    valid: bool = True,
    x_m: float | None = None,
    y_m: float = 0.0,
    z_m: float | None = None,
    heading_rad: float | None = 0.0,
    velocity_x_mps: float | None = 1.0,
    velocity_y_mps: float | None = 0.0,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        sample_index=index,
        timestamp_ns=index * 100_000_000 if timestamp_ns is None else timestamp_ns,
        x_m=float(index) if x_m is None else x_m,
        y_m=y_m,
        z_m=z_m,
        heading_rad=heading_rad,
        velocity_x_mps=velocity_x_mps,
        velocity_y_mps=velocity_y_mps,
        speed_mps=None,
        acceleration_x_mps2=None,
        acceleration_y_mps2=None,
        is_observed=True,
        is_valid=valid,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _trajectory(samples: tuple[TrajectorySampleRecord, ...]) -> Trajectory:
    return Trajectory(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        samples=samples,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=("fixture",),
    )


def test_required_grid_and_configurations_are_frozen_strict_and_identified() -> None:
    grid = required_baseline_grid()

    assert len(grid) == 14
    assert tuple(config.key for config in grid) == (
        "raw_samples",
        "uniform_linear-stride-2",
        "uniform_linear-stride-5",
        "uniform_linear-stride-10",
        "uniform_hermite-stride-2",
        "uniform_hermite-stride-5",
        "uniform_hermite-stride-10",
        "rdp_linear-error-0p05",
        "rdp_linear-error-0p1",
        "rdp_linear-error-0p25",
        "rdp_linear-error-0p5",
        "fixed_interval_linear-interval-200000000",
        "fixed_interval_linear-interval-500000000",
        "fixed_interval_linear-interval-1000000000",
    )
    config = MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=2)
    assert (
        baseline_config_from_json(baseline_config_to_canonical_json(config)) == config
    )
    assert baseline_config_identity(config) != baseline_config_identity(
        MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=5)
    )
    with pytest.raises(FrozenInstanceError):
        config.stride = 3  # type: ignore[misc]
    with pytest.raises(SchemaError):
        baseline_config_from_json(
            baseline_config_to_canonical_json(config).replace("}\n", ', "extra":1}\n')
        )


@pytest.mark.parametrize(
    "config",
    (
        MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=2),
        MotionBaselineConfig(BaselineMethod.UNIFORM_HERMITE, stride=2),
        MotionBaselineConfig(
            BaselineMethod.RDP_LINEAR,
            maximum_perpendicular_error_m=0.1,
        ),
        MotionBaselineConfig(
            BaselineMethod.FIXED_INTERVAL_LINEAR,
            interval_ns=100_000_000,
        ),
    ),
)
def test_endpoints_and_invalid_gaps_are_preserved(
    config: MotionBaselineConfig,
) -> None:
    trajectory = _trajectory(
        (
            _sample(0),
            _sample(1),
            _sample(2, valid=False),
            _sample(3),
            _sample(4),
        )
    )
    track = encode_trajectory_baseline(trajectory, AgentClass.VEHICLE, config)
    validation = validate_baseline_replay(
        trajectory,
        track=track,
        config=config,
    )

    assert track.run_count == 2
    assert validation.valid_run_count == 2
    assert validation.exact_endpoint_error_m == 0.0
    assert validation.gap_preservation_failures == 0
    assert replay_track(track, trajectory.samples[2].timestamp_ns) is None


def test_uniform_stride_retains_every_kth_sample_and_run_end() -> None:
    trajectory = _trajectory(tuple(_sample(index) for index in range(8)))

    indices = baseline_source_keyframes(
        trajectory,
        MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=3),
    )

    assert indices == ((0, 3, 6, 7),)


def test_fixed_interval_uses_first_irregular_source_timestamp_after_boundary() -> None:
    trajectory = _trajectory(
        (
            _sample(0, timestamp_ns=0),
            _sample(1, timestamp_ns=100_000_000),
            _sample(2, timestamp_ns=250_000_000),
            _sample(3, timestamp_ns=610_000_000),
            _sample(4, timestamp_ns=900_000_000),
        )
    )

    indices = baseline_source_keyframes(
        trajectory,
        MotionBaselineConfig(
            BaselineMethod.FIXED_INTERVAL_LINEAR,
            interval_ns=200_000_000,
        ),
    )

    assert indices == ((0, 2, 3, 4),)


def test_rdp_uses_earliest_equal_distance_tie_break() -> None:
    trajectory = _trajectory(
        tuple(
            _sample(index, x_m=float(index), y_m=y_m)
            for index, y_m in enumerate((0.0, -2.0, -2.0, -2.0, 0.0))
        )
    )

    indices = baseline_source_keyframes(
        trajectory,
        MotionBaselineConfig(
            BaselineMethod.RDP_LINEAR,
            maximum_perpendicular_error_m=1.25,
        ),
    )

    assert indices == ((0, 1, 4),)


def test_rdp_uses_planar_or_three_dimensional_distance_as_declared() -> None:
    planar = _trajectory(
        (
            _sample(0, x_m=0.0),
            _sample(1, x_m=1.0),
            _sample(2, x_m=2.0),
        )
    )
    spatial = _trajectory(
        (
            _sample(0, x_m=0.0, z_m=0.0),
            _sample(1, x_m=1.0, z_m=1.0),
            _sample(2, x_m=2.0, z_m=0.0),
        )
    )
    config = MotionBaselineConfig(
        BaselineMethod.RDP_LINEAR,
        maximum_perpendicular_error_m=0.5,
    )

    assert baseline_source_keyframes(planar, config) == ((0, 2),)
    assert baseline_source_keyframes(spatial, config) == ((0, 1, 2),)


def test_z_availability_changes_are_explicit_keyframe_boundaries() -> None:
    trajectory = _trajectory(
        (
            _sample(0),
            _sample(1),
            _sample(2, z_m=1.0),
            _sample(3, z_m=2.0),
        )
    )
    config = MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=3)
    track = encode_trajectory_baseline(trajectory, AgentClass.VEHICLE, config)

    assert baseline_source_keyframes(trajectory, config) == ((0, 1, 2, 3),)
    assert tuple(
        (segment.source_start_sample_index, segment.source_end_sample_index)
        for segment in track.segments
    ) == ((0, 1), (2, 3))


def test_singleton_and_stationary_runs_have_valid_deterministic_segments() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=2.0),
            _sample(1, valid=False),
            _sample(2, x_m=5.0),
            _sample(3, x_m=5.0),
            _sample(4, x_m=5.0),
        )
    )
    config = MotionBaselineConfig(
        BaselineMethod.RDP_LINEAR,
        maximum_perpendicular_error_m=0.0,
    )
    track = encode_trajectory_baseline(trajectory, AgentClass.VEHICLE, config)

    assert tuple(segment.primitive_type for segment in track.segments) == (
        ProceduralPrimitiveType.HOLD,
        ProceduralPrimitiveType.LINEAR,
    )


def test_uniform_hermite_uses_complete_velocity_and_falls_back_to_linear() -> None:
    complete = _trajectory((_sample(0), _sample(1), _sample(2)))
    missing = _trajectory(
        (
            _sample(0),
            _sample(1),
            _sample(2, velocity_x_mps=None, velocity_y_mps=None),
        )
    )
    config = MotionBaselineConfig(BaselineMethod.UNIFORM_HERMITE, stride=2)

    complete_track = encode_trajectory_baseline(
        complete,
        AgentClass.VEHICLE,
        config,
    )
    missing_track = encode_trajectory_baseline(
        missing,
        AgentClass.VEHICLE,
        config,
    )

    assert complete_track.segments[0].primitive_type is (
        ProceduralPrimitiveType.CUBIC_HERMITE
    )
    assert missing_track.segments[0].primitive_type is ProceduralPrimitiveType.LINEAR


def test_replay_optional_values_and_raw_stored_timestamp_contract() -> None:
    trajectory = _trajectory(
        (
            _sample(0, heading_rad=None, velocity_x_mps=None, velocity_y_mps=None),
            _sample(1, heading_rad=None, velocity_x_mps=None, velocity_y_mps=None),
            _sample(2, heading_rad=None, velocity_x_mps=None, velocity_y_mps=None),
        )
    )
    raw = MotionBaselineConfig(BaselineMethod.RAW_SAMPLES)
    linear = MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=2)
    track = encode_trajectory_baseline(trajectory, AgentClass.VEHICLE, linear)

    raw_state = evaluate_raw_trajectory(trajectory, trajectory.samples[1].timestamp_ns)
    assert raw_state is not None
    assert raw_state.heading_rad is None
    assert evaluate_raw_trajectory(trajectory, 50_000_000) is None
    assert validate_baseline_replay(trajectory, config=raw).position_errors_m == (
        0.0,
        0.0,
        0.0,
    )
    assert (
        validate_baseline_replay(
            trajectory,
            track=track,
            config=linear,
        ).heading_errors_rad
        == ()
    )


def test_keyframe_count_and_development_eligibility_are_stable() -> None:
    long = _trajectory(tuple(_sample(index) for index in range(11)))
    short = _trajectory(tuple(_sample(index) for index in range(10)))
    config = MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=5)
    track = encode_trajectory_baseline(long, AgentClass.VEHICLE, config)

    assert baseline_keyframe_count(track) == 3
    assert included_motion_trajectories((short, long)) == (long,)


def test_invalid_configurations_and_raw_encoding_are_rejected() -> None:
    trajectory = _trajectory(tuple(_sample(index) for index in range(3)))
    for kwargs in (
        {"method": BaselineMethod.UNIFORM_LINEAR},
        {"method": BaselineMethod.RAW_SAMPLES, "stride": 1},
        {"method": BaselineMethod.UNIFORM_HERMITE, "stride": 0},
        {
            "method": BaselineMethod.RDP_LINEAR,
            "maximum_perpendicular_error_m": math.nan,
        },
        {"method": BaselineMethod.FIXED_INTERVAL_LINEAR, "interval_ns": True},
    ):
        with pytest.raises(ValidationError):
            MotionBaselineConfig(**kwargs)
    with pytest.raises(ValidationError):
        encode_trajectory_baseline(
            trajectory,
            AgentClass.VEHICLE,
            MotionBaselineConfig(BaselineMethod.RAW_SAMPLES),
        )


def test_raw_serialized_size_and_equivalent_artifacts_are_exact(
    tmp_path: Path,
) -> None:
    trajectory = _trajectory(tuple(_sample(index) for index in range(11)))

    result = materialize_equivalent_raw_artifacts(
        tmp_path,
        Path("generated"),
        "raw:test",
        (trajectory,),
    )
    reopened = materialize_equivalent_raw_artifacts(
        tmp_path,
        Path("generated"),
        "raw:test",
        (trajectory,),
    )

    first_path = (
        tmp_path
        / "generated"
        / "runs"
        / next((tmp_path / "generated" / "runs").iterdir()).name
        / "trajectory_samples.parquet"
    )
    assert result.encoded_bytes == first_path.stat().st_size
    assert result.content_checksums == reopened.content_checksums
    assert result.checksum_agreement

    first_path.write_bytes(b"corrupt")
    with pytest.raises((ArtifactError, SchemaError, OSError)):
        materialize_equivalent_raw_artifacts(
            tmp_path,
            Path("generated"),
            "raw:test",
            (trajectory,),
        )


def test_equivalent_procedural_baseline_artifacts_are_deterministic(
    tmp_path: Path,
) -> None:
    synthetic = build_synthetic_scenario(SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED)
    config = MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=2)
    tape = encode_scenario_baseline(
        synthetic.scenario,
        synthetic.coordinate_frame,
        synthetic.agents,
        synthetic.trajectories,
        config,
        source_validation_report_identity="synthetic-validation:v1",
    )

    result = materialize_equivalent_procedural_artifacts(
        tmp_path,
        Path("generated"),
        "uniform:test",
        tape,
    )
    reopened = materialize_equivalent_procedural_artifacts(
        tmp_path,
        Path("generated"),
        "uniform:test",
        tape,
    )

    assert len(result.content_checksums) == 3
    assert result.content_checksums == reopened.content_checksums
    assert result.encoded_bytes > 0
    assert result.checksum_agreement


def test_bounded_canonical_bundle_read_never_requires_map_data(tmp_path: Path) -> None:
    synthetic = build_synthetic_scenario(SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED)
    run = prepare_run_directory(tmp_path, "cache", "canonical:scenario")
    for name, table, schema in (
        (
            "scenario_manifest.parquet",
            scenario_records_to_table((synthetic.scenario,)),
            CanonicalSchemaName.SCENARIO_MANIFEST,
        ),
        (
            "coordinate_frame_metadata.parquet",
            coordinate_frame_records_to_table((synthetic.coordinate_frame,)),
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        ),
        (
            "agent_metadata.parquet",
            agent_records_to_table(synthetic.agents),
            CanonicalSchemaName.AGENT_METADATA,
        ),
        (
            "trajectory_samples.parquet",
            trajectories_to_table(synthetic.trajectories),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        ),
    ):
        atomic_write_canonical_parquet(run, name, table, schema, row_group_size=2)
    finalize_run_directory(run)

    bundle = read_canonical_scenario_bundle(tmp_path, run.path, batch_size=2)

    assert bundle.scenario == synthetic.scenario
    assert bundle.coordinate_frame == synthetic.coordinate_frame
    assert tuple(agent.agent_id for agent in bundle.agents) == tuple(
        agent.agent_id for agent in synthetic.agents
    )
    assert tuple(
        trajectory.trajectory_id for trajectory in bundle.trajectories
    ) == tuple(trajectory.trajectory_id for trajectory in synthetic.trajectories)
    assert sum(item.sample_count for item in bundle.trajectories) == sum(
        item.sample_count for item in synthetic.trajectories
    )
    assert not (run.path / "vector_map_elements.parquet").exists()


def test_module_import_has_no_filesystem_or_process_side_effects(
    tmp_path: Path,
) -> None:
    code = (
        "from pathlib import Path\n"
        "import kinematicweave.baselines.motion\n"
        "assert list(Path('.').iterdir()) == []\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert importlib.import_module("kinematicweave.baselines.motion")
