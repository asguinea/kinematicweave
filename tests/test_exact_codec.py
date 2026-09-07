"""Tests for the exact procedural hold/linear codec baseline."""

from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path

import pytest

from kinematicweave.codecs.exact import (
    decode_source_endpoint_states,
    encode_scenario_exact,
    encode_trajectory_exact,
    evaluate_segment,
    replay_timestamps,
    replay_track,
)
from kinematicweave.data.procedural_artifacts import (
    materialize_procedural_tape,
    read_procedural_tape,
    verify_procedural_tape_artifacts,
)
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
    valid: bool = True,
    x_m: float | None = None,
    y_m: float = 0.0,
    z_m: float | None = None,
    heading_rad: float | None = 0.0,
    velocity_x_mps: float | None = 1.0,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        sample_index=index,
        timestamp_ns=index * 1_000_000_000,
        x_m=float(index) if x_m is None else x_m,
        y_m=y_m,
        z_m=z_m,
        heading_rad=heading_rad,
        velocity_x_mps=velocity_x_mps,
        velocity_y_mps=0.0 if velocity_x_mps is not None else None,
        speed_mps=None,
        acceleration_x_mps2=None,
        acceleration_y_mps2=None,
        is_observed=True,
        is_valid=valid,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _trajectory(
    samples: tuple[TrajectorySampleRecord, ...],
) -> Trajectory:
    return Trajectory(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        samples=samples,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=("fixture",),
    )


def test_exact_encoding_preserves_runs_stationary_intervals_and_singletons() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=1.0),
            _sample(1, x_m=1.0, heading_rad=0.5, velocity_x_mps=0.0),
            _sample(2, valid=False),
            _sample(3, x_m=9.0, z_m=2.0),
        )
    )
    first = encode_trajectory_exact(trajectory, AgentClass.VEHICLE)
    second = encode_trajectory_exact(trajectory, "vehicle")

    assert first == second
    assert not hasattr(first, "__dict__")
    with pytest.raises(FrozenInstanceError):
        first.run_count = 3  # type: ignore[misc]
    assert first.source_sample_count == 4
    assert first.valid_sample_count == 3
    assert first.run_count == 2
    assert first.segment_count == 2
    assert tuple(segment.primitive_type for segment in first.segments) == (
        ProceduralPrimitiveType.HOLD,
        ProceduralPrimitiveType.HOLD,
    )
    assert first.segments[0].start_time_ns < first.segments[0].end_time_ns
    assert first.segments[1].start_time_ns == first.segments[1].end_time_ns
    assert replay_track(first, 2_000_000_000) is None
    assert replay_track(first, -1) is None
    assert replay_track(first, 4_000_000_000) is None


def test_linear_replay_is_exact_at_endpoints_and_wraps_heading_shortest_path() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0, z_m=None, heading_rad=math.pi - 0.1),
            _sample(1, x_m=2.0, z_m=4.0, heading_rad=-math.pi + 0.1),
        )
    )
    track = encode_trajectory_exact(trajectory, AgentClass.VEHICLE)
    segment = track.segments[0]

    start = evaluate_segment(segment, 0)
    middle = evaluate_segment(segment, 500_000_000)
    end = evaluate_segment(segment, 1_000_000_000)
    assert start is not None and end is not None and middle is not None
    assert (start.x_m, start.z_m, start.heading_rad) == (
        0.0,
        None,
        math.pi - 0.1,
    )
    assert (end.x_m, end.z_m, end.heading_rad) == (
        2.0,
        4.0,
        -math.pi + 0.1,
    )
    assert middle.x_m == 1.0
    assert middle.z_m is None
    assert math.isclose(abs(middle.heading_rad or 0.0), math.pi)
    assert start.origin_type is OriginType.DECODED


def test_source_endpoint_decode_reproduces_every_valid_source_field_exactly() -> None:
    samples = (
        _sample(0, x_m=0.0, velocity_x_mps=None),
        _sample(1, x_m=1.25, heading_rad=None, velocity_x_mps=2.0),
        _sample(2, valid=False),
        _sample(3, x_m=3.5, z_m=1.0),
    )
    track = encode_trajectory_exact(_trajectory(samples), AgentClass.VEHICLE)
    decoded = dict(decode_source_endpoint_states(track))

    assert tuple(decoded) == (0, 1, 3)
    for index in decoded:
        source = samples[index]
        state = decoded[index]
        assert (
            state.timestamp_ns,
            state.x_m,
            state.y_m,
            state.z_m,
            state.heading_rad,
            state.velocity_x_mps,
            state.velocity_y_mps,
        ) == (
            source.timestamp_ns,
            source.x_m,
            source.y_m,
            source.z_m,
            source.heading_rad,
            source.velocity_x_mps,
            source.velocity_y_mps,
        )
    assert replay_timestamps(track, (0, 1_000_000_000)) == (
        decoded[0],
        decoded[1],
    )
    with pytest.raises(ValidationError, match="ordered"):
        replay_timestamps(track, (1, 0))


def test_invalid_only_trajectory_and_malformed_segment_are_rejected() -> None:
    with pytest.raises(ValidationError, match="no valid samples"):
        encode_trajectory_exact(
            _trajectory((_sample(0, valid=False),)),
            AgentClass.VEHICLE,
        )

    track = encode_trajectory_exact(
        _trajectory((_sample(0), _sample(1))),
        AgentClass.VEHICLE,
    )
    with pytest.raises(ValidationError, match="parameter_values"):
        replace(track.segments[0], parameter_values=(1.0,))
    with pytest.raises(ValidationError, match="quality_flags"):
        replace(track.segments[0], quality_flags=("duplicate", "duplicate"))
    with pytest.raises(ValidationError, match="heading"):
        replace(track.segments[0], start_heading_rad=math.pi)


def test_scenario_bundle_parquet_round_trip_is_immutable_and_detects_corruption(
    tmp_path: Path,
) -> None:
    scenario = build_synthetic_scenario(SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED)
    tape = encode_scenario_exact(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        source_validation_report_identity="synthetic-validation:v1",
    )
    artifacts = materialize_procedural_tape(
        tmp_path,
        "generated",
        "exact-codec:test",
        tape,
    )

    assert read_procedural_tape(tmp_path, artifacts) == tape
    assert verify_procedural_tape_artifacts(tmp_path, artifacts) == tape
    assert {
        path.name
        for path in artifacts.run_directory.path.iterdir()
        if path.is_file() and not path.name.startswith(".run.")
    } == {
        "tape_manifest.parquet",
        "procedural_tracks.parquet",
        "procedural_segments.parquet",
        "codec_summary.json",
    }
    with pytest.raises(ArtifactError, match="immutable"):
        materialize_procedural_tape(
            tmp_path,
            "generated",
            "exact-codec:test",
            tape,
        )

    summary_path = tmp_path / artifacts.codec_summary.relative_path
    summary_path.write_bytes(summary_path.read_bytes() + b"x")
    with pytest.raises(ArtifactError, match=r"size|checksum"):
        verify_procedural_tape_artifacts(tmp_path, artifacts)


def test_positive_hold_requires_equal_positions() -> None:
    track = encode_trajectory_exact(
        _trajectory((_sample(0, x_m=0.0), _sample(1, x_m=0.0))),
        AgentClass.VEHICLE,
    )
    segment = track.segments[0]
    with pytest.raises(ValidationError, match="positions"):
        replace(segment, end_x_m=1.0)
