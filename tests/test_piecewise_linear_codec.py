"""Tests for the minimum-segment error-bounded piecewise-linear codec."""

from dataclasses import FrozenInstanceError, replace
import importlib
import inspect
import itertools
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from kinematicweave.codecs.exact import evaluate_segment, replay_track
from kinematicweave.codecs.piecewise_linear import (
    ALGORITHM_VERSION,
    ENCODER_NAME,
    ENCODER_VERSION,
    PiecewiseLinearCodecConfig,
    encode_canonical_parquet_piecewise_linear,
    encode_scenario_piecewise_linear,
    encode_trajectory_piecewise_linear,
    encoder_parameters_identity,
    summarize_piecewise_linear_tape,
    validate_piecewise_linear_replay,
    verify_piecewise_linear_artifacts,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    trajectories_to_table,
)
from kinematicweave.data.procedural_artifacts import materialize_procedural_tape
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
        timestamp_ns=(index * 1_000_000_000 if timestamp_ns is None else timestamp_ns),
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
    "value",
    (True, False, -0.1, math.inf, -math.inf, math.nan, "0.1", None),
)
def test_configuration_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError):
        PiecewiseLinearCodecConfig(value)  # type: ignore[arg-type]


def test_configuration_is_frozen_normalized_and_fully_identified() -> None:
    integer = PiecewiseLinearCodecConfig(1)
    floating = PiecewiseLinearCodecConfig(1.0)

    assert integer.maximum_position_error_m == 1.0
    assert isinstance(integer.maximum_position_error_m, float)
    assert encoder_parameters_identity(integer) == encoder_parameters_identity(floating)
    assert encoder_parameters_identity(PiecewiseLinearCodecConfig(0.1)) != (
        encoder_parameters_identity(PiecewiseLinearCodecConfig(0.2))
    )
    assert ALGORITHM_VERSION
    with pytest.raises(FrozenInstanceError):
        integer.maximum_position_error_m = 2.0  # type: ignore[misc]


def test_straight_collinear_and_exact_hold_runs_use_one_segment() -> None:
    straight = _trajectory(tuple(_sample(index) for index in range(5)))
    held = _trajectory(tuple(_sample(index, x_m=2.0, y_m=3.0) for index in range(5)))

    straight_track = encode_trajectory_piecewise_linear(
        straight,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.0),
    )
    held_track = encode_trajectory_piecewise_linear(
        held,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.0),
    )

    assert straight_track.segment_count == 1
    assert straight_track.segments[0].primitive_type is ProceduralPrimitiveType.LINEAR
    assert held_track.segment_count == 1
    assert held_track.segments[0].primitive_type is ProceduralPrimitiveType.HOLD
    assert held_track.segments[0].source_end_sample_index == 4


def test_singleton_gap_and_elevation_transitions_preserve_source_support() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0),
            _sample(1, x_m=1.0),
            _sample(2, valid=False),
            _sample(3, x_m=3.0),
            _sample(4, x_m=4.0, z_m=1.0),
            _sample(5, x_m=5.0, z_m=2.0),
        )
    )
    track = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.0),
    )
    validation = validate_piecewise_linear_replay(
        track,
        trajectory,
        PiecewiseLinearCodecConfig(0.0),
    )

    assert track.run_count == 2
    assert tuple(segment.run_index for segment in track.segments) == (0, 1, 1)
    assert track.segments[1].start_time_ns == track.segments[1].end_time_ns
    assert replay_track(track, trajectory.samples[2].timestamp_ns) is None
    assert validation.invalid_gap_samples_with_state == 0
    assert validation.replayed_valid_sample_count == 5
    assert validation.run_endpoints_exact
    assert validation.retained_breakpoints_exact


def test_irregular_timestamps_control_candidate_interpolation() -> None:
    trajectory = _trajectory(
        (
            _sample(0, timestamp_ns=0, x_m=0.0),
            _sample(1, timestamp_ns=2_000_000_000, x_m=2.0),
            _sample(2, timestamp_ns=5_000_000_000, x_m=5.0),
        )
    )
    track = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.0),
    )

    assert track.segment_count == 1
    assert validate_piecewise_linear_replay(
        track,
        trajectory,
        PiecewiseLinearCodecConfig(0.0),
    ).position_errors_m == (0.0, 0.0, 0.0)


def test_three_dimensional_error_is_enforced() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0, z_m=0.0),
            _sample(1, x_m=1.0, z_m=0.2),
            _sample(2, x_m=2.0, z_m=0.0),
        )
    )

    strict = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.1),
    )
    relaxed = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.2),
    )

    assert strict.segment_count == 2
    assert relaxed.segment_count == 1
    assert max(
        validate_piecewise_linear_replay(
            relaxed,
            trajectory,
            PiecewiseLinearCodecConfig(0.2),
        ).position_errors_m
    ) == pytest.approx(0.2)


def test_tie_break_prefers_later_next_breakpoint() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0, y_m=0.0),
            _sample(1, x_m=1.0, y_m=1.0),
            _sample(2, x_m=2.0, y_m=1.0),
            _sample(3, x_m=3.0, y_m=0.0),
        )
    )
    track = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.5),
    )

    assert track.segment_count == 2
    assert track.segments[0].source_end_sample_index == 2


def _candidate_error(
    samples: tuple[TrajectorySampleRecord, ...],
    start: int,
    end: int,
) -> float:
    first = samples[start]
    last = samples[end]
    duration = last.timestamp_ns - first.timestamp_ns
    maximum = 0.0
    for sample in samples[start + 1 : end]:
        alpha = (sample.timestamp_ns - first.timestamp_ns) / duration
        x_m = first.x_m + (last.x_m - first.x_m) * alpha
        y_m = first.y_m + (last.y_m - first.y_m) * alpha
        maximum = max(maximum, math.hypot(x_m - sample.x_m, y_m - sample.y_m))
    return maximum


def _exhaustive_best_breakpoints(
    samples: tuple[TrajectorySampleRecord, ...],
    bound: float,
) -> tuple[int, ...]:
    interior = tuple(range(1, len(samples) - 1))
    candidates = []
    for included_count in range(len(interior) + 1):
        for included in itertools.combinations(interior, included_count):
            breakpoints = (0, *included, len(samples) - 1)
            if all(
                _candidate_error(samples, start, end) <= bound + 1.0e-12
                for start, end in itertools.pairwise(breakpoints)
            ):
                candidates.append(breakpoints)
    return min(candidates, key=lambda value: (len(value), tuple(-x for x in value[1:])))


@pytest.mark.parametrize(
    "ordinates,bound",
    (
        ((0.0, 0.2, -0.1, 0.4, 0.0), 0.15),
        ((0.0, 1.0, 1.0, 0.0), 0.5),
        ((0.0, 0.0, 0.0, 0.0), 0.0),
        ((0.0, 0.4, 0.8, 1.2, 1.6), 0.0),
    ),
)
def test_dynamic_programming_matches_exhaustive_optimum(
    ordinates: tuple[float, ...],
    bound: float,
) -> None:
    samples = tuple(
        _sample(index, x_m=float(index), y_m=ordinate)
        for index, ordinate in enumerate(ordinates)
    )
    trajectory = _trajectory(samples)
    track = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(bound),
    )
    actual = (
        track.segments[0].source_start_sample_index,
        *(segment.source_end_sample_index for segment in track.segments),
    )

    assert actual == _exhaustive_best_breakpoints(samples, bound)


def test_heading_optional_values_and_diagnostic_errors_use_established_replay() -> None:
    trajectory = _trajectory(
        (
            _sample(
                0,
                x_m=0.0,
                heading_rad=math.pi - 0.1,
                velocity_x_mps=0.0,
            ),
            _sample(
                1,
                x_m=1.0,
                heading_rad=-math.pi,
                velocity_x_mps=3.0,
            ),
            _sample(
                2,
                x_m=2.0,
                heading_rad=-math.pi + 0.1,
                velocity_x_mps=2.0,
            ),
        )
    )
    track = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.0),
    )
    middle = evaluate_segment(track.segments[0], 1_000_000_000)
    validation = validate_piecewise_linear_replay(
        track,
        trajectory,
        PiecewiseLinearCodecConfig(0.0),
    )

    assert middle is not None
    assert middle.heading_rad == -math.pi
    assert validation.heading_errors_rad == pytest.approx((0.0, 0.0, 0.0))
    assert validation.velocity_errors_mps == pytest.approx((0.0, 2.0, 0.0))


def test_validation_detects_a_stricter_bound_than_the_encoded_configuration() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0, y_m=0.0),
            _sample(1, x_m=1.0, y_m=0.2),
            _sample(2, x_m=2.0, y_m=0.0),
        )
    )
    track = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
        PiecewiseLinearCodecConfig(0.2),
    )

    with pytest.raises(ValidationError, match="exceeds"):
        validate_piecewise_linear_replay(
            track,
            trajectory,
            PiecewiseLinearCodecConfig(0.1),
        )


def test_scenario_summary_and_artifact_verification_are_deterministic(
    tmp_path: Path,
) -> None:
    scenario = build_synthetic_scenario(SyntheticScenarioKind.LEFT_TURN)
    config = PiecewiseLinearCodecConfig(0.1)
    first = encode_scenario_piecewise_linear(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        config,
        source_validation_report_identity="synthetic-validation:v1",
    )
    second = encode_scenario_piecewise_linear(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        config,
        source_validation_report_identity="synthetic-validation:v1",
    )
    artifacts = materialize_procedural_tape(
        tmp_path,
        "generated",
        "piecewise-linear:test",
        first,
    )
    verified, summary = verify_piecewise_linear_artifacts(
        tmp_path,
        artifacts,
        scenario.trajectories,
        config,
        expected_tape=first,
    )

    assert first == second == verified
    assert first.encoder_name == ENCODER_NAME
    assert first.encoder_version == ENCODER_VERSION
    assert first.encoder_parameters_identity == encoder_parameters_identity(config)
    assert summary == summarize_piecewise_linear_tape(
        first,
        scenario.trajectories,
        config,
    )
    summary_path = tmp_path / artifacts.codec_summary.relative_path
    summary_path.write_bytes(summary_path.read_bytes() + b"x")
    with pytest.raises(ArtifactError, match=r"size|checksum"):
        verify_piecewise_linear_artifacts(
            tmp_path,
            artifacts,
            scenario.trajectories,
            config,
        )


def test_bounded_parquet_api_uses_record_batches_without_polars_collection(
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

    module = importlib.import_module("kinematicweave.codecs.piecewise_linear")
    monkeypatch.setattr(module, "iter_canonical_parquet_batches", batches)
    tape = encode_canonical_parquet_piecewise_linear(
        Path.cwd(),
        scenario_paths=("scenario.parquet",),
        coordinate_frame_paths=("frame.parquet",),
        agent_paths=("agents.parquet",),
        trajectory_sample_paths=("samples.parquet",),
        source_validation_report_identity="synthetic-validation:v1",
        batch_size=2,
    )
    source = inspect.getsource(encode_canonical_parquet_piecewise_linear)

    assert tape.track_count == len(scenario.trajectories)
    assert [item[0] for item in calls] == [
        CanonicalSchemaName.SCENARIO_MANIFEST,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        CanonicalSchemaName.AGENT_METADATA,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    ]
    assert "to_pylist" not in source
    assert "collect" not in source


def test_import_is_quiet_and_has_no_data_side_effects(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import kinematicweave.codecs.piecewise_linear",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == ""
    assert completed.stderr == ""
    assert tuple(tmp_path.iterdir()) == ()


def test_malformed_track_identity_is_rejected() -> None:
    trajectory = _trajectory((_sample(0), _sample(1)))
    track = encode_trajectory_piecewise_linear(
        trajectory,
        AgentClass.VEHICLE,
    )

    with pytest.raises(ValidationError, match="identities differ"):
        validate_piecewise_linear_replay(
            replace(track, trajectory_id="trajectory:other"),
            trajectory,
        )
