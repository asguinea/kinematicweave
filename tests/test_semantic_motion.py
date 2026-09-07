"""Tests for deterministic semantic waypoints and motion events."""

from dataclasses import FrozenInstanceError, fields, replace
import importlib
import inspect
from itertools import pairwise
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from kinematicweave.codecs.velocity_bounded import (
    encode_scenario_velocity_bounded,
    encode_trajectory_velocity_bounded,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    trajectories_to_table,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.data.semantic_artifacts import (
    materialize_semantic_motion_tape,
    verify_semantic_motion_artifacts,
)
from kinematicweave.data.synthetic import (
    SyntheticScenarioKind,
    build_synthetic_scenario,
)
from kinematicweave.domain.records import (
    AgentClass,
    OriginType,
    Trajectory,
    TrajectorySampleRecord,
)
from kinematicweave.domain.semantic import (
    MotionEvent,
    MotionEventType,
    SemanticMotionTape,
    SemanticMotionTrack,
    SemanticWaypoint,
    SemanticWaypointRole,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    analyze_event_preservation,
    build_semantic_motion_tape,
    detect_semantic_trajectory,
    encode_canonical_parquet_semantic_motion,
    replay_detection_trajectory,
    semantic_motion_configuration_identity,
    validate_semantic_waypoint_replay,
)


def _sample(
    index: int,
    *,
    valid: bool = True,
    timestamp_ns: int | None = None,
    x_m: float | None = None,
    y_m: float = 0.0,
    z_m: float | None = None,
    heading_rad: float | None = 0.0,
    speed_mps: float | None = None,
    velocity_x_mps: float | None = 1.0,
    velocity_y_mps: float | None = 0.0,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id="scenario:semantic-test",
        agent_id="agent:semantic-test",
        trajectory_id="trajectory:semantic-test",
        sample_index=index,
        timestamp_ns=index * 1_000_000_000 if timestamp_ns is None else timestamp_ns,
        x_m=float(index) if x_m is None else x_m,
        y_m=y_m,
        z_m=z_m,
        heading_rad=heading_rad,
        velocity_x_mps=velocity_x_mps,
        velocity_y_mps=velocity_y_mps,
        speed_mps=speed_mps,
        acceleration_x_mps2=None,
        acceleration_y_mps2=None,
        is_observed=valid,
        is_valid=valid,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _trajectory(samples: tuple[TrajectorySampleRecord, ...]) -> Trajectory:
    return Trajectory(
        scenario_id="scenario:semantic-test",
        agent_id="agent:semantic-test",
        trajectory_id="trajectory:semantic-test",
        samples=samples,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _semantic_track(trajectory: Trajectory) -> SemanticMotionTrack:
    procedural = encode_trajectory_velocity_bounded(
        trajectory,
        AgentClass.VEHICLE,
    )
    return detect_semantic_trajectory(trajectory, procedural)


def _event_types(track: SemanticMotionTrack) -> tuple[MotionEventType, ...]:
    return tuple(MotionEventType(event.event_type) for event in track.events)


def _attributes(event: MotionEvent) -> dict[str, object]:
    value = json.loads(event.semantic_attributes_json)
    assert isinstance(value, dict)
    return value


def _synthetic_semantics(kind: SyntheticScenarioKind) -> tuple[Any, SemanticMotionTape]:
    scenario = build_synthetic_scenario(kind)
    procedural = encode_scenario_velocity_bounded(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        source_validation_report_identity="synthetic-validation:v1",
    )
    return scenario, build_semantic_motion_tape(procedural, scenario.trajectories)


def test_enum_values_and_persistent_fields_are_exact() -> None:
    assert tuple(role.value for role in SemanticWaypointRole) == (
        "track_start",
        "track_end",
        "run_start",
        "run_end",
        "gap_start",
        "gap_end",
        "stop_start",
        "stop_end",
        "turn_entry",
        "turn_apex",
        "turn_exit",
        "acceleration_start",
        "acceleration_end",
        "braking_start",
        "braking_end",
    )
    assert tuple(event.value for event in MotionEventType) == (
        "gap",
        "stop",
        "left_turn",
        "right_turn",
        "acceleration",
        "braking",
    )
    assert tuple(field.name for field in fields(SemanticWaypoint)) == (
        "tape_id",
        "procedural_track_id",
        "waypoint_id",
        "waypoint_index",
        "waypoint_role",
        "timestamp_ns",
        "source_sample_index",
        "x_m",
        "y_m",
        "z_m",
        "heading_rad",
        "speed_mps",
        "related_event_ids",
        "origin_type",
        "quality_flags",
    )
    assert tuple(field.name for field in fields(MotionEvent)) == (
        "tape_id",
        "procedural_track_id",
        "event_id",
        "event_index",
        "event_type",
        "start_waypoint_id",
        "anchor_waypoint_id",
        "end_waypoint_id",
        "start_time_ns",
        "anchor_time_ns",
        "end_time_ns",
        "magnitude_value",
        "semantic_attributes_json",
        "origin_type",
        "quality_flags",
    )


@pytest.mark.parametrize(
    "field,value",
    (
        ("stop_speed_threshold_mps", True),
        ("turn_delta_deadband_rad", -0.1),
        ("minimum_speed_change_mps", math.inf),
        ("minimum_stop_duration_ns", 1.0),
        ("minimum_turn_duration_ns", -1),
        ("minimum_acceleration_duration_ns", 2**63),
    ),
)
def test_configuration_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        SemanticMotionConfig(**{field: value})  # type: ignore[arg-type]


def test_configuration_is_frozen_normalized_and_fully_identified() -> None:
    integer = SemanticMotionConfig(stop_speed_threshold_mps=1)
    floating = SemanticMotionConfig(stop_speed_threshold_mps=1.0)
    changed = SemanticMotionConfig(stop_speed_threshold_mps=1.1)

    assert integer.stop_speed_threshold_mps == 1.0
    assert semantic_motion_configuration_identity(integer) == (
        semantic_motion_configuration_identity(floating)
    )
    assert semantic_motion_configuration_identity(integer) != (
        semantic_motion_configuration_identity(changed)
    )
    with pytest.raises(FrozenInstanceError):
        integer.stop_speed_threshold_mps = 2.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kind", "required", "forbidden"),
    (
        (
            SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED,
            (),
            tuple(MotionEventType),
        ),
        (
            SyntheticScenarioKind.ACCELERATION_DECELERATION,
            (MotionEventType.ACCELERATION, MotionEventType.BRAKING),
            (),
        ),
        (SyntheticScenarioKind.STOP, (MotionEventType.STOP,), ()),
        (
            SyntheticScenarioKind.LEFT_TURN,
            (MotionEventType.LEFT_TURN,),
            (MotionEventType.RIGHT_TURN,),
        ),
        (
            SyntheticScenarioKind.RIGHT_TURN,
            (MotionEventType.RIGHT_TURN,),
            (MotionEventType.LEFT_TURN,),
        ),
    ),
)
def test_synthetic_motion_oracles(
    kind: SyntheticScenarioKind,
    required: tuple[MotionEventType, ...],
    forbidden: tuple[MotionEventType, ...],
) -> None:
    scenario, tape = _synthetic_semantics(kind)
    event_types = tuple(
        event.event_type for track in tape.tracks for event in track.events
    )
    repeated = build_semantic_motion_tape(
        tape.procedural_tape,
        scenario.trajectories,
    )

    assert all(event_type in event_types for event_type in required)
    assert all(event_type not in event_types for event_type in forbidden)
    assert repeated == tape


def test_gap_is_exact_and_leading_trailing_invalid_regions_are_metadata() -> None:
    trajectory = _trajectory(
        tuple(_sample(index, valid=index not in {0, 3, 4, 7}) for index in range(8))
    )
    track = _semantic_track(trajectory)
    gap = next(
        event for event in track.events if event.event_type is MotionEventType.GAP
    )
    attributes = _attributes(gap)

    assert sum(event.event_type is MotionEventType.GAP for event in track.events) == 1
    assert (gap.start_time_ns, gap.end_time_ns) == (
        2_000_000_000,
        5_000_000_000,
    )
    assert attributes["invalid_sample_count"] == 2
    assert track.leading_invalid_sample_count == 1
    assert track.trailing_invalid_sample_count == 1
    assert all(
        event.event_type is MotionEventType.GAP
        or event.end_time_ns <= gap.start_time_ns
        or event.start_time_ns >= gap.end_time_ns
        for event in track.events
    )


def test_stop_boundaries_anchor_tie_and_stationary_context_are_exact() -> None:
    trajectory = _trajectory(
        tuple(
            _sample(
                index,
                speed_mps=speed,
                velocity_x_mps=speed,
            )
            for index, speed in enumerate((2.0, 0.4, 0.1, 0.1, 0.4, 2.0))
        )
    )
    track = _semantic_track(trajectory)
    stop = next(
        event for event in track.events if event.event_type is MotionEventType.STOP
    )
    attributes = _attributes(stop)

    assert (stop.start_time_ns, stop.anchor_time_ns, stop.end_time_ns) == (
        1_000_000_000,
        2_000_000_000,
        4_000_000_000,
    )
    assert stop.magnitude_value == pytest.approx(0.4)
    assert attributes["preceding_motion_present"] is True
    assert attributes["following_motion_present"] is True
    assert attributes["speed_sources"] == ["stored_speed"]

    stationary = _semantic_track(
        _trajectory(
            tuple(
                _sample(index, speed_mps=0.0, velocity_x_mps=0.0) for index in range(3)
            )
        )
    )
    stationary_stop = next(
        event for event in stationary.events if event.event_type is MotionEventType.STOP
    )
    assert _attributes(stationary_stop)["lacks_motion_context"] is True


def test_singleton_multiple_stops_and_event_overlap_contract() -> None:
    singleton = _semantic_track(
        _trajectory(
            (
                _sample(
                    0,
                    speed_mps=0.0,
                    velocity_x_mps=0.0,
                    heading_rad=None,
                ),
            )
        )
    )
    assert singleton.events == ()
    assert {waypoint.waypoint_role for waypoint in singleton.waypoints} == {
        SemanticWaypointRole.TRACK_START,
        SemanticWaypointRole.TRACK_END,
        SemanticWaypointRole.RUN_START,
        SemanticWaypointRole.RUN_END,
    }

    multiple = _semantic_track(
        _trajectory(
            tuple(
                _sample(
                    index,
                    speed_mps=speed,
                    velocity_x_mps=speed,
                )
                for index, speed in enumerate((0.0, 0.0, 2.0, 0.0, 0.0))
            )
        )
    )
    assert (
        sum(event.event_type is MotionEventType.STOP for event in multiple.events) == 2
    )

    overlapping = _semantic_track(
        _trajectory(
            tuple(
                _sample(
                    index,
                    heading_rad=index * 0.2,
                    speed_mps=float(index),
                    velocity_x_mps=float(index),
                )
                for index in range(4)
            )
        )
    )
    acceleration = next(
        event
        for event in overlapping.events
        if event.event_type is MotionEventType.ACCELERATION
    )
    turn = next(
        event
        for event in overlapping.events
        if event.event_type is MotionEventType.LEFT_TURN
    )
    assert max(acceleration.start_time_ns, turn.start_time_ns) <= min(
        acceleration.end_time_ns,
        turn.end_time_ns,
    )
    for event_type in MotionEventType:
        same_type = [
            event for event in overlapping.events if event.event_type is event_type
        ]
        assert all(
            left.end_time_ns <= right.start_time_ns
            for left, right in pairwise(same_type)
        )


def test_feature_source_priority_is_recorded_for_speed_and_heading() -> None:
    stored = _semantic_track(
        _trajectory(
            tuple(
                _sample(index, speed_mps=float(index), velocity_x_mps=10.0)
                for index in range(4)
            )
        )
    )
    stored_acceleration = next(
        event
        for event in stored.events
        if event.event_type is MotionEventType.ACCELERATION
    )
    assert _attributes(stored_acceleration)["speed_sources"] == ["stored_speed"]

    vector = _semantic_track(
        _trajectory(
            tuple(
                _sample(index, velocity_x_mps=float(index), speed_mps=None)
                for index in range(4)
            )
        )
    )
    vector_acceleration = next(
        event
        for event in vector.events
        if event.event_type is MotionEventType.ACCELERATION
    )
    assert _attributes(vector_acceleration)["speed_sources"] == ["velocity_vector"]

    finite = _semantic_track(
        _trajectory(
            tuple(
                _sample(
                    index,
                    x_m=float(index * index),
                    speed_mps=None,
                    velocity_x_mps=None,
                    velocity_y_mps=None,
                    heading_rad=None,
                )
                for index in range(5)
            )
        )
    )
    finite_events = [
        event
        for event in finite.events
        if event.event_type is MotionEventType.ACCELERATION
    ]
    assert finite_events
    assert _attributes(finite_events[0])["speed_sources"] == [
        "finite_difference_position"
    ]

    angles = (0.0, 0.2, 0.4, 0.6)
    heading_trajectory = _trajectory(
        tuple(
            _sample(
                index,
                x_m=math.cos(angle),
                y_m=math.sin(angle),
                heading_rad=None,
                velocity_x_mps=math.cos(angle),
                velocity_y_mps=math.sin(angle),
            )
            for index, angle in enumerate(angles)
        )
    )
    turn = next(
        event
        for event in _semantic_track(heading_trajectory).events
        if event.event_type is MotionEventType.LEFT_TURN
    )
    assert _attributes(turn)["heading_sources"] == ["velocity_vector"]


def test_displacement_heading_and_unavailable_features_are_deterministic() -> None:
    angles = (0.0, 0.25, 0.5, 0.75, 1.0)
    displacement = _semantic_track(
        _trajectory(
            tuple(
                _sample(
                    index,
                    x_m=5.0 * math.cos(angle),
                    y_m=5.0 * math.sin(angle),
                    heading_rad=None,
                    speed_mps=None,
                    velocity_x_mps=None,
                    velocity_y_mps=None,
                )
                for index, angle in enumerate(angles)
            )
        )
    )
    turn = next(
        event
        for event in displacement.events
        if event.event_type is MotionEventType.LEFT_TURN
    )
    assert _attributes(turn)["heading_sources"] == ["finite_difference_displacement"]

    unavailable = _semantic_track(
        _trajectory(
            tuple(
                _sample(
                    index,
                    x_m=0.0,
                    heading_rad=None,
                    speed_mps=None,
                    velocity_x_mps=None,
                    velocity_y_mps=None,
                )
                for index in range(3)
            )
        )
    )
    assert not any(
        event.event_type in (MotionEventType.LEFT_TURN, MotionEventType.RIGHT_TURN)
        for event in unavailable.events
    )


def test_semantic_records_reject_noncanonical_json_and_are_immutable() -> None:
    track = _semantic_track(
        _trajectory(
            tuple(
                _sample(index, speed_mps=0.0, velocity_x_mps=0.0) for index in range(3)
            )
        )
    )
    event = track.events[0]

    with pytest.raises(ValidationError, match="canonical JSON"):
        replace(event, semantic_attributes_json='{"z":1, "a":2}')
    with pytest.raises(ValidationError, match="object"):
        replace(event, semantic_attributes_json="[]")
    with pytest.raises(FrozenInstanceError):
        event.event_index = 2  # type: ignore[misc]


def test_turn_wraparound_deadband_and_elevation() -> None:
    headings = (3.0, 3.12, -3.0, -2.8)
    trajectory = _trajectory(
        tuple(
            _sample(index, heading_rad=heading, z_m=5.0)
            for index, heading in enumerate(headings)
        )
    )
    track = _semantic_track(trajectory)
    turn = next(
        event for event in track.events if event.event_type is MotionEventType.LEFT_TURN
    )

    assert turn.magnitude_value == pytest.approx(
        sum(
            (right - left + math.pi) % (2.0 * math.pi) - math.pi
            for left, right in pairwise(headings)
        )
    )
    assert all(waypoint.z_m == 5.0 for waypoint in track.waypoints)

    deadband = _semantic_track(
        _trajectory(
            tuple(_sample(index, heading_rad=index * 0.009) for index in range(5))
        )
    )
    assert MotionEventType.LEFT_TURN not in _event_types(deadband)
    assert MotionEventType.RIGHT_TURN not in _event_types(deadband)


def test_irregular_timestamps_replay_and_preservation_metrics() -> None:
    scenario, source_tape = _synthetic_semantics(
        SyntheticScenarioKind.IRREGULAR_SAMPLING
    )
    source = source_tape.tracks[0]
    replay_trajectory = replay_detection_trajectory(
        scenario.trajectories[0],
        source.procedural_track,
    )
    replay = detect_semantic_trajectory(replay_trajectory, source.procedural_track)
    preservation = analyze_event_preservation(source, replay)
    validation = validate_semantic_waypoint_replay(
        source,
        scenario.trajectories[0],
    )
    differences = tuple(
        right.timestamp_ns - left.timestamp_ns
        for left, right in pairwise(scenario.trajectories[0].samples)
    )

    assert len(set(differences)) > 1
    assert 0.0 <= preservation["f1"] <= 1.0  # type: ignore[operator]
    assert validation["maximum_position_error_m"] <= 0.10  # type: ignore[operator]
    assert validation["maximum_velocity_error_mps"] <= 1.00  # type: ignore[operator]


def test_semantic_artifacts_round_trip_verify_and_detect_corruption(
    tmp_path: Path,
) -> None:
    scenario, tape = _synthetic_semantics(SyntheticScenarioKind.MISSING_GAP)
    preservation = {"verified": True}
    artifacts = materialize_semantic_motion_tape(
        tmp_path,
        "generated",
        "semantic:test",
        tape,
        scenario.trajectories,
        SemanticMotionConfig(),
        preservation,
    )
    verified, verified_preservation = verify_semantic_motion_artifacts(
        tmp_path,
        artifacts,
        tape.procedural_tape,
        scenario.trajectories,
        SemanticMotionConfig(),
        expected_tape=tape,
        expected_preservation_summary=preservation,
    )

    assert verified == tape
    assert verified_preservation == preservation
    assert {
        path.name
        for path in artifacts.run_directory.path.iterdir()
        if path.is_file() and not path.name.startswith(".run.")
    } == {
        "semantic_waypoints.parquet",
        "motion_events.parquet",
        "semantic_summary.json",
    }

    summary_path = tmp_path / artifacts.semantic_summary.relative_path
    summary_path.write_bytes(summary_path.read_bytes() + b"x")
    with pytest.raises(ArtifactError, match=r"size|checksum"):
        verify_semantic_motion_artifacts(
            tmp_path,
            artifacts,
            tape.procedural_tape,
            scenario.trajectories,
            SemanticMotionConfig(),
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

    piecewise = importlib.import_module("kinematicweave.codecs.piecewise_linear")
    monkeypatch.setattr(piecewise, "iter_canonical_parquet_batches", batches)
    tape = encode_canonical_parquet_semantic_motion(
        Path.cwd(),
        scenario_paths=("scenario.parquet",),
        coordinate_frame_paths=("frame.parquet",),
        agent_paths=("agents.parquet",),
        trajectory_sample_paths=("samples.parquet",),
        source_validation_report_identity="synthetic-validation:v1",
        batch_size=2,
    )
    source = inspect.getsource(encode_canonical_parquet_semantic_motion)

    assert len(tape.tracks) == len(scenario.trajectories)
    assert [item[0] for item in calls].count(
        CanonicalSchemaName.TRAJECTORY_SAMPLES
    ) == 2
    assert all(batch_size == 2 for _, batch_size in calls)
    assert "collect" not in source


def test_import_is_quiet_and_has_no_data_side_effects(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import kinematicweave.events.semantic_motion"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == ""
    assert completed.stderr == ""
    assert tuple(tmp_path.iterdir()) == ()
