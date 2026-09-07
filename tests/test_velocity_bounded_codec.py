"""Tests for the position-and-velocity-bounded hybrid codec."""

from dataclasses import FrozenInstanceError
import importlib
import inspect
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from kinematicweave.codecs.exact import evaluate_segment, replay_track
from kinematicweave.codecs.velocity_bounded import (
    ENCODER_NAME,
    ENCODER_VERSION,
    EncodingPerformanceStats,
    VelocityBoundedCodecConfig,
    _candidate_cache,
    _minimum_candidates,
    encode_canonical_parquet_velocity_bounded,
    encode_scenario_velocity_bounded,
    encode_scenario_velocity_bounded_with_performance,
    encode_trajectory_velocity_bounded,
    summarize_velocity_bounded_tape,
    validate_velocity_bounded_replay,
    velocity_bounded_encoder_parameters_identity,
    verify_velocity_bounded_artifacts,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    trajectories_to_table,
)
from kinematicweave.data.procedural_artifacts import materialize_procedural_tape
from kinematicweave.data.schemas import CanonicalSchemaName, schema_fingerprint
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
from kinematicweave.errors import ArtifactError, ValidationError


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
        timestamp_ns=index * 1_000_000_000 if timestamp_ns is None else timestamp_ns,
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


@pytest.mark.parametrize(
    "field,value",
    (
        ("maximum_position_error_m", True),
        ("maximum_position_error_m", -0.1),
        ("maximum_position_error_m", math.inf),
        ("maximum_velocity_error_mps", False),
        ("maximum_velocity_error_mps", -0.1),
        ("maximum_velocity_error_mps", math.nan),
    ),
)
def test_configuration_rejects_invalid_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        VelocityBoundedCodecConfig(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "primitives",
    (
        (),
        ("hold", "linear"),
        ("hold", "linear", "linear"),
        ("hold", "linear", "quadratic"),
        "hold",
    ),
)
def test_configuration_requires_unique_complete_vocabulary(
    primitives: object,
) -> None:
    with pytest.raises(ValidationError):
        VelocityBoundedCodecConfig(
            candidate_primitives=primitives  # type: ignore[arg-type]
        )


def test_configuration_is_frozen_normalized_and_fully_identified() -> None:
    integer = VelocityBoundedCodecConfig(1, 2)
    floating = VelocityBoundedCodecConfig(1.0, 2.0)
    changed_velocity = VelocityBoundedCodecConfig(1.0, 2.1)

    assert integer.maximum_position_error_m == 1.0
    assert integer.maximum_velocity_error_mps == 2.0
    assert velocity_bounded_encoder_parameters_identity(integer) == (
        velocity_bounded_encoder_parameters_identity(floating)
    )
    assert velocity_bounded_encoder_parameters_identity(integer) != (
        velocity_bounded_encoder_parameters_identity(changed_velocity)
    )
    with pytest.raises(FrozenInstanceError):
        integer.maximum_velocity_error_mps = 3.0  # type: ignore[misc]


def test_velocity_candidate_accepts_exact_bound_and_rejects_above_it() -> None:
    trajectory = _trajectory(
        (
            _sample(0, velocity_x_mps=1.0),
            _sample(1, velocity_x_mps=2.0),
            _sample(2, velocity_x_mps=1.0),
        )
    )
    at_bound = encode_trajectory_velocity_bounded(
        trajectory,
        AgentClass.VEHICLE,
        VelocityBoundedCodecConfig(0.0, 1.0),
    )
    strict = encode_trajectory_velocity_bounded(
        trajectory,
        AgentClass.VEHICLE,
        VelocityBoundedCodecConfig(0.0, 0.999),
    )

    assert at_bound.segment_count == 1
    assert at_bound.segments[0].primitive_type is ProceduralPrimitiveType.LINEAR
    assert strict.segment_count == 2
    assert max(
        validate_velocity_bounded_replay(
            at_bound,
            trajectory,
            VelocityBoundedCodecConfig(0.0, 1.0),
        ).velocity_errors_mps
    ) == pytest.approx(1.0)


def test_missing_source_velocity_is_not_invented_or_constrained() -> None:
    trajectory = _trajectory(
        (
            _sample(0, velocity_x_mps=1.0),
            _sample(1, velocity_x_mps=None, velocity_y_mps=None),
            _sample(2, velocity_x_mps=1.0),
        )
    )
    track = encode_trajectory_velocity_bounded(
        trajectory,
        AgentClass.VEHICLE,
        VelocityBoundedCodecConfig(0.0, 0.0),
    )
    middle = replay_track(track, trajectory.samples[1].timestamp_ns)

    assert track.segment_count == 1
    assert middle is not None
    assert middle.velocity_x_mps == 1.0
    assert validate_velocity_bounded_replay(
        track,
        trajectory,
        VelocityBoundedCodecConfig(0.0, 0.0),
    ).velocity_errors_mps == pytest.approx((0.0, 0.0))


def test_exact_cubic_curve_uses_analytic_hermite_velocity() -> None:
    samples = tuple(
        _sample(
            index,
            x_m=(index / 4.0) ** 3,
            velocity_x_mps=3.0 * (index / 4.0) ** 2 / 4.0,
        )
        for index in range(5)
    )
    trajectory = _trajectory(samples)
    track = encode_trajectory_velocity_bounded(
        trajectory,
        AgentClass.VEHICLE,
        VelocityBoundedCodecConfig(0.0, 0.0),
    )
    validation = validate_velocity_bounded_replay(
        track,
        trajectory,
        VelocityBoundedCodecConfig(0.0, 0.0),
    )

    assert track.segment_count == 1
    assert track.segments[0].primitive_type is ProceduralPrimitiveType.CUBIC_HERMITE
    assert max(validation.position_errors_m) < 1.0e-15
    assert max(validation.velocity_errors_mps) < 1.0e-15


def test_endpoint_exactness_heading_wrap_and_linear_z_are_preserved() -> None:
    trajectory = _trajectory(
        (
            _sample(
                0,
                x_m=0.0,
                z_m=0.0,
                heading_rad=math.pi - 0.1,
                velocity_x_mps=0.0,
            ),
            _sample(
                1,
                x_m=0.5,
                z_m=1.0,
                heading_rad=-math.pi,
                velocity_x_mps=1.0,
            ),
            _sample(
                2,
                x_m=2.0,
                z_m=2.0,
                heading_rad=-math.pi + 0.1,
                velocity_x_mps=2.0,
            ),
        )
    )
    track = encode_trajectory_velocity_bounded(
        trajectory,
        AgentClass.VEHICLE,
        VelocityBoundedCodecConfig(0.5, 1.0),
    )
    validation = validate_velocity_bounded_replay(track, trajectory)
    first = evaluate_segment(track.segments[0], 0)
    last = evaluate_segment(track.segments[-1], 2_000_000_000)

    assert first is not None and last is not None
    assert (first.x_m, first.z_m, first.velocity_x_mps) == (0.0, 0.0, 0.0)
    assert (last.x_m, last.z_m, last.velocity_x_mps) == (2.0, 2.0, 2.0)
    assert validation.run_endpoints_exact
    assert validation.retained_breakpoints_exact


def test_gaps_singletons_and_elevation_changes_are_not_crossed() -> None:
    trajectory = _trajectory(
        (
            _sample(0),
            _sample(1),
            _sample(2, valid=False),
            _sample(3),
            _sample(4, z_m=1.0),
            _sample(5, z_m=2.0),
        )
    )
    track = encode_trajectory_velocity_bounded(trajectory, AgentClass.VEHICLE)
    validation = validate_velocity_bounded_replay(track, trajectory)

    assert track.run_count == 2
    assert replay_track(track, trajectory.samples[2].timestamp_ns) is None
    assert any(
        segment.start_time_ns == segment.end_time_ns for segment in track.segments
    )
    assert validation.invalid_gap_samples_with_state == 0


def _enumerated_best(
    samples: tuple[TrajectorySampleRecord, ...],
    config: VelocityBoundedCodecConfig,
) -> tuple[tuple[int, str], ...]:
    cache, _ = _candidate_cache(samples, config)
    paths: list[tuple[Any, ...]] = []

    def visit(start: int, path: tuple[Any, ...]) -> None:
        if start == len(samples) - 1:
            paths.append(path)
            return
        for candidate in cache[start]:
            visit(candidate.end_index, (*path, candidate))

    visit(0, ())
    rank = {
        primitive: index for index, primitive in enumerate(config.candidate_primitives)
    }

    def key(path: tuple[Any, ...]) -> tuple[object, ...]:
        return (
            len(path),
            math.fsum(item.position_squared_error for item in path),
            math.fsum(item.velocity_squared_error for item in path),
            tuple(-item.end_index for item in path[:-1]),
            tuple(rank[item.primitive_type] for item in path),
        )

    selected = min(paths, key=key)
    return tuple((item.end_index, item.primitive_type.value) for item in selected)


@pytest.mark.parametrize(
    "ordinates,velocities,position_bound,velocity_bound",
    (
        ((0.0, 0.2, -0.1, 0.4, 0.0), (1.0, 1.2, 0.8, 1.1, 1.0), 0.2, 0.5),
        ((0.0, 1.0, 1.0, 0.0), (1.0, 2.0, 2.0, 1.0), 0.5, 0.75),
        ((0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0), 0.0, 0.0),
    ),
)
def test_dynamic_programming_matches_exhaustive_enumeration(
    ordinates: tuple[float, ...],
    velocities: tuple[float, ...],
    position_bound: float,
    velocity_bound: float,
) -> None:
    samples = tuple(
        _sample(index, y_m=ordinate, velocity_x_mps=velocity)
        for index, (ordinate, velocity) in enumerate(
            zip(ordinates, velocities, strict=True)
        )
    )
    config = VelocityBoundedCodecConfig(position_bound, velocity_bound)
    selected, _ = _minimum_candidates(samples, config)

    assert tuple(
        (item.end_index, item.primitive_type.value) for item in selected
    ) == _enumerated_best(samples, config)


def test_candidate_generation_is_cached_by_start_and_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("kinematicweave.codecs.velocity_bounded")
    original = module._primitive_replay
    calls = 0

    def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_primitive_replay", counted)
    samples = tuple(_sample(index) for index in range(8))
    _, performance = _minimum_candidates(samples, VelocityBoundedCodecConfig())

    assert calls == (len(samples) - 1) * 3
    assert performance.primitive_interval_count == (
        len(samples) * (len(samples) - 1) // 2 * 3
    )
    assert performance.accepted_candidate_count > 0
    assert performance.dynamic_programming_transition_count > 0


def test_scenario_summary_performance_and_artifacts_are_deterministic(
    tmp_path: Path,
) -> None:
    scenario = build_synthetic_scenario(SyntheticScenarioKind.LEFT_TURN)
    first, performance = encode_scenario_velocity_bounded_with_performance(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        source_validation_report_identity="synthetic-validation:v1",
    )
    second = encode_scenario_velocity_bounded(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        source_validation_report_identity="synthetic-validation:v1",
    )
    artifacts = materialize_procedural_tape(
        tmp_path,
        "generated",
        "velocity-bounded:test",
        first,
    )
    verified, summary = verify_velocity_bounded_artifacts(
        tmp_path,
        artifacts,
        scenario.trajectories,
        expected_tape=first,
    )

    assert first == second == verified
    assert first.encoder_name == ENCODER_NAME
    assert first.encoder_version == ENCODER_VERSION
    assert isinstance(performance, EncodingPerformanceStats)
    assert (
        summarize_velocity_bounded_tape(
            first,
            scenario.trajectories,
            performance=performance,
        )["performance"]
        == performance.to_dict()
    )
    assert summary["velocity_error_mps"]["maximum"] <= 1.0  # type: ignore[index]
    summary_path = tmp_path / artifacts.codec_summary.relative_path
    summary_path.write_bytes(summary_path.read_bytes() + b"x")
    with pytest.raises(ArtifactError, match=r"size|checksum"):
        verify_velocity_bounded_artifacts(
            tmp_path,
            artifacts,
            scenario.trajectories,
        )


def test_validation_detects_stricter_velocity_bound() -> None:
    trajectory = _trajectory(
        (
            _sample(0, velocity_x_mps=1.0),
            _sample(1, velocity_x_mps=2.0),
            _sample(2, velocity_x_mps=1.0),
        )
    )
    track = encode_trajectory_velocity_bounded(
        trajectory,
        AgentClass.VEHICLE,
        VelocityBoundedCodecConfig(0.0, 1.0),
    )

    with pytest.raises(ValidationError, match="maximum_velocity"):
        validate_velocity_bounded_replay(
            track,
            trajectory,
            VelocityBoundedCodecConfig(0.0, 0.5),
        )


def test_bounded_parquet_api_uses_record_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = build_synthetic_scenario(SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED)
    tables = {
        CanonicalSchemaName.SCENARIO_MANIFEST: scenario_records_to_table(
            (scenario.scenario,)
        ),
        CanonicalSchemaName.COORDINATE_FRAME_METADATA: (
            coordinate_frame_records_to_table((scenario.coordinate_frame,))
        ),
        CanonicalSchemaName.AGENT_METADATA: agent_records_to_table(scenario.agents),
        CanonicalSchemaName.TRAJECTORY_SAMPLES: trajectories_to_table(
            scenario.trajectories
        ),
    }
    calls: list[tuple[CanonicalSchemaName, int]] = []

    def batches(
        repository_root: Path,
        relative_paths: Any,
        expected: CanonicalSchemaName,
        *,
        batch_size: int,
    ) -> Any:
        del repository_root, relative_paths
        calls.append((expected, batch_size))
        return iter(tables[expected].to_batches(max_chunksize=2))

    piecewise_module = importlib.import_module("kinematicweave.codecs.piecewise_linear")
    monkeypatch.setattr(piecewise_module, "iter_canonical_parquet_batches", batches)
    tape = encode_canonical_parquet_velocity_bounded(
        Path.cwd(),
        scenario_paths=("scenario.parquet",),
        coordinate_frame_paths=("frame.parquet",),
        agent_paths=("agents.parquet",),
        trajectory_sample_paths=("samples.parquet",),
        source_validation_report_identity="synthetic-validation:v1",
        batch_size=2,
    )
    source = inspect.getsource(encode_canonical_parquet_velocity_bounded)

    assert tape.track_count == len(scenario.trajectories)
    assert [item[0] for item in calls] == [
        CanonicalSchemaName.SCENARIO_MANIFEST,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        CanonicalSchemaName.AGENT_METADATA,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    ]
    assert "to_pylist" not in source
    assert "collect" not in source
    assert schema_fingerprint(CanonicalSchemaName.PROCEDURAL_SEGMENTS) == (
        "e994f5d7c2f2cfc21ddbb06b44c076d04e94491fe2f201bc4b2a76bfa089cd86"
    )


def test_import_is_quiet_and_has_no_data_side_effects(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import kinematicweave.codecs.velocity_bounded"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == ""
    assert completed.stderr == ""
    assert tuple(tmp_path.iterdir()) == ()
