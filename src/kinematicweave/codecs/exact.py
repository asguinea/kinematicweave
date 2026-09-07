"""Deterministic lossless hold/linear codec for canonical trajectories."""

from collections.abc import Iterable, Sequence
from itertools import pairwise
import math

from kinematicweave.canonical import canonical_sha256
from kinematicweave.domain.procedural import (
    ProceduralPrimitiveType,
    ProceduralSegment,
    ProceduralTape,
    ProceduralTrack,
    ReplayState,
)
from kinematicweave.domain.records import (
    AgentClass,
    AgentRecord,
    CoordinateFrameRecord,
    OriginType,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
    trajectory_sample_record_to_dict,
    validate_scenario_bundle,
)
from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import make_identifier

__all__ = [
    "ENCODER_NAME",
    "ENCODER_PARAMETERS_IDENTITY",
    "ENCODER_VERSION",
    "cubic_hermite_basis",
    "cubic_hermite_basis_derivative",
    "decode_source_endpoint_states",
    "encode_scenario_exact",
    "encode_trajectory_exact",
    "evaluate_segment",
    "replay_timestamps",
    "replay_track",
]

ENCODER_NAME = "kinematicweave.exact_hold_linear"
ENCODER_VERSION = "1.0"
ENCODER_PARAMETERS_IDENTITY = canonical_sha256("exact-codec-parameters", {})


def _merged_flags(*values: Sequence[str]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for flags in values:
        for flag in flags:
            normalized = flag.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
    return tuple(merged)


def _identifier(entity_type: str, domain: str, value: object) -> str:
    return make_identifier(entity_type, canonical_sha256(domain, value))


def _sample_identity(sample: TrajectorySampleRecord) -> dict[str, object]:
    return trajectory_sample_record_to_dict(sample)


def _track_identity(trajectory: Trajectory) -> str:
    return _identifier(
        "procedural-track",
        "exact-procedural-track",
        {
            "scenario_id": trajectory.scenario_id,
            "agent_id": trajectory.agent_id,
            "trajectory_id": trajectory.trajectory_id,
            "samples": [_sample_identity(sample) for sample in trajectory.samples],
        },
    )


def _same_position(
    start: TrajectorySampleRecord,
    end: TrajectorySampleRecord,
) -> bool:
    return (
        start.x_m == end.x_m
        and start.y_m == end.y_m
        and (
            (start.z_m is None and end.z_m is None)
            or (start.z_m is not None and end.z_m is not None and start.z_m == end.z_m)
        )
    )


def _segment(
    *,
    tape_id: str,
    procedural_track_id: str,
    run_index: int,
    segment_index: int,
    start: TrajectorySampleRecord,
    end: TrajectorySampleRecord,
    primitive_type: ProceduralPrimitiveType,
    quality_flags: Sequence[str],
) -> ProceduralSegment:
    segment_id = _identifier(
        "procedural-segment",
        "exact-procedural-segment",
        {
            "tape_id": tape_id,
            "procedural_track_id": procedural_track_id,
            "run_index": run_index,
            "segment_index": segment_index,
            "source_start_sample_index": start.sample_index,
            "source_end_sample_index": end.sample_index,
            "start_time_ns": start.timestamp_ns,
            "end_time_ns": end.timestamp_ns,
        },
    )
    return ProceduralSegment(
        tape_id=tape_id,
        procedural_track_id=procedural_track_id,
        segment_id=segment_id,
        run_index=run_index,
        segment_index=segment_index,
        primitive_type=primitive_type,
        source_start_sample_index=start.sample_index,
        source_end_sample_index=end.sample_index,
        start_time_ns=start.timestamp_ns,
        end_time_ns=end.timestamp_ns,
        start_x_m=start.x_m,
        start_y_m=start.y_m,
        start_z_m=start.z_m,
        end_x_m=end.x_m,
        end_y_m=end.y_m,
        end_z_m=end.z_m,
        start_heading_rad=start.heading_rad,
        end_heading_rad=end.heading_rad,
        start_velocity_x_mps=start.velocity_x_mps,
        start_velocity_y_mps=start.velocity_y_mps,
        end_velocity_x_mps=end.velocity_x_mps,
        end_velocity_y_mps=end.velocity_y_mps,
        parameter_values=(),
        origin_type=OriginType.INFERRED,
        quality_flags=quality_flags,
    )


def _valid_runs(
    samples: Sequence[TrajectorySampleRecord],
) -> tuple[tuple[TrajectorySampleRecord, ...], ...]:
    runs: list[tuple[TrajectorySampleRecord, ...]] = []
    current: list[TrajectorySampleRecord] = []
    for sample in samples:
        if sample.is_valid:
            current.append(sample)
        elif current:
            runs.append(tuple(current))
            current = []
    if current:
        runs.append(tuple(current))
    return tuple(runs)


def encode_trajectory_exact(
    trajectory: Trajectory,
    agent_class: AgentClass | str,
    *,
    tape_id: str | None = None,
) -> ProceduralTrack:
    """Encode valid source runs without bridging invalid samples."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    try:
        normalized_class = (
            agent_class
            if isinstance(agent_class, AgentClass)
            else AgentClass(agent_class)
        )
    except (TypeError, ValueError):
        raise ValidationError(
            f"agent_class has invalid value {agent_class!r}"
        ) from None
    runs = _valid_runs(trajectory.samples)
    if not runs:
        raise ValidationError("trajectory has no valid samples to encode")
    normalized_tape_id = tape_id or _identifier(
        "tape",
        "exact-standalone-tape",
        {
            "track": _track_identity(trajectory),
            "encoder": ENCODER_NAME,
            "version": ENCODER_VERSION,
            "parameters": ENCODER_PARAMETERS_IDENTITY,
        },
    )
    procedural_track_id = _track_identity(trajectory)
    segments: list[ProceduralSegment] = []
    for run_index, run in enumerate(runs):
        if len(run) == 1:
            sample = run[0]
            segments.append(
                _segment(
                    tape_id=normalized_tape_id,
                    procedural_track_id=procedural_track_id,
                    run_index=run_index,
                    segment_index=0,
                    start=sample,
                    end=sample,
                    primitive_type=ProceduralPrimitiveType.HOLD,
                    quality_flags=_merged_flags(
                        trajectory.quality_flags, sample.quality_flags
                    ),
                )
            )
            continue
        for segment_index, (start, end) in enumerate(pairwise(run)):
            segments.append(
                _segment(
                    tape_id=normalized_tape_id,
                    procedural_track_id=procedural_track_id,
                    run_index=run_index,
                    segment_index=segment_index,
                    start=start,
                    end=end,
                    primitive_type=(
                        ProceduralPrimitiveType.HOLD
                        if _same_position(start, end)
                        else ProceduralPrimitiveType.LINEAR
                    ),
                    quality_flags=_merged_flags(
                        trajectory.quality_flags,
                        start.quality_flags,
                        end.quality_flags,
                    ),
                )
            )
    valid_samples = tuple(sample for run in runs for sample in run)
    return ProceduralTrack(
        tape_id=normalized_tape_id,
        scenario_id=trajectory.scenario_id,
        procedural_track_id=procedural_track_id,
        agent_id=trajectory.agent_id,
        trajectory_id=trajectory.trajectory_id,
        agent_class=normalized_class.value,
        start_time_ns=valid_samples[0].timestamp_ns,
        end_time_ns=valid_samples[-1].timestamp_ns,
        source_sample_count=trajectory.sample_count,
        valid_sample_count=len(valid_samples),
        run_count=len(runs),
        segment_count=len(segments),
        has_elevation=any(sample.z_m is not None for sample in valid_samples),
        origin_type=OriginType.INFERRED,
        quality_flags=_merged_flags(
            trajectory.quality_flags,
            *(sample.quality_flags for sample in valid_samples),
        ),
        segments=segments,
    )


def encode_scenario_exact(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
    *,
    source_validation_report_identity: str | None,
) -> ProceduralTape:
    """Encode one validated canonical scenario bundle into an exact tape."""
    validate_scenario_bundle(scenario, coordinate_frame, agents, trajectories)
    ordered_trajectories = tuple(
        sorted(trajectories, key=lambda item: (item.scenario_id, item.trajectory_id))
    )
    agents_by_id = {agent.agent_id: agent for agent in agents}
    tape_id = _identifier(
        "tape",
        "exact-scenario-tape",
        {
            "scenario_id": scenario.scenario_id,
            "coordinate_frame_id": coordinate_frame.coordinate_frame_id,
            "source_dataset_id": scenario.dataset_id,
            "source_dataset_version": scenario.dataset_version,
            "source_validation_report_identity": source_validation_report_identity,
            "encoder": ENCODER_NAME,
            "version": ENCODER_VERSION,
            "parameters": ENCODER_PARAMETERS_IDENTITY,
            "trajectories": [
                {
                    "trajectory_id": trajectory.trajectory_id,
                    "samples": [
                        _sample_identity(sample) for sample in trajectory.samples
                    ],
                }
                for trajectory in ordered_trajectories
            ],
        },
    )
    tracks = tuple(
        encode_trajectory_exact(
            trajectory,
            agents_by_id[trajectory.agent_id].agent_class,
            tape_id=tape_id,
        )
        for trajectory in ordered_trajectories
    )
    return ProceduralTape(
        tape_id=tape_id,
        scenario_id=scenario.scenario_id,
        coordinate_frame_id=coordinate_frame.coordinate_frame_id,
        source_dataset_id=scenario.dataset_id,
        source_dataset_version=scenario.dataset_version,
        source_validation_report_identity=source_validation_report_identity,
        encoder_name=ENCODER_NAME,
        encoder_version=ENCODER_VERSION,
        encoder_parameters_identity=ENCODER_PARAMETERS_IDENTITY,
        start_time_ns=min(track.start_time_ns for track in tracks),
        end_time_ns=max(track.end_time_ns for track in tracks),
        track_count=len(tracks),
        segment_count=sum(track.segment_count for track in tracks),
        source_sample_count=sum(track.source_sample_count for track in tracks),
        encoded_valid_sample_count=sum(track.valid_sample_count for track in tracks),
        has_elevation=any(track.has_elevation for track in tracks),
        origin_type=OriginType.INFERRED,
        quality_flags=_merged_flags(
            scenario.quality_flags,
            coordinate_frame.quality_flags,
            *(track.quality_flags for track in tracks),
        ),
        tracks=tracks,
    )


def _interpolate_optional(
    start: float | None,
    end: float | None,
    alpha: float,
) -> float | None:
    if start is None or end is None:
        return None
    return start + (end - start) * alpha


def _wrap_heading(value: float) -> float:
    wrapped = (value + math.pi) % (2.0 * math.pi) - math.pi
    return -math.pi if wrapped == math.pi else wrapped


def _interpolate_heading(
    start: float | None,
    end: float | None,
    alpha: float,
) -> float | None:
    if start is None or end is None:
        return None
    delta = _wrap_heading(end - start)
    return _wrap_heading(start + delta * alpha)


def cubic_hermite_basis(u: float) -> tuple[float, float, float, float]:
    """Return the standard cubic Hermite basis at normalized time ``u``."""
    if not isinstance(u, (int, float)) or isinstance(u, bool):
        raise ValidationError("u must be numeric")
    normalized = float(u)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValidationError("u must be finite and in the interval [0, 1]")
    squared = normalized * normalized
    cubed = squared * normalized
    return (
        2.0 * cubed - 3.0 * squared + 1.0,
        cubed - 2.0 * squared + normalized,
        -2.0 * cubed + 3.0 * squared,
        cubed - squared,
    )


def cubic_hermite_basis_derivative(
    u: float,
) -> tuple[float, float, float, float]:
    """Return derivatives of the Hermite basis with respect to ``u``."""
    if not isinstance(u, (int, float)) or isinstance(u, bool):
        raise ValidationError("u must be numeric")
    normalized = float(u)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValidationError("u must be finite and in the interval [0, 1]")
    squared = normalized * normalized
    return (
        6.0 * squared - 6.0 * normalized,
        3.0 * squared - 4.0 * normalized + 1.0,
        -6.0 * squared + 6.0 * normalized,
        3.0 * squared - 2.0 * normalized,
    )


def _cubic_hermite_axis(
    start_position: float,
    end_position: float,
    start_velocity: float,
    end_velocity: float,
    duration_seconds: float,
    u: float,
) -> tuple[float, float]:
    h00, h10, h01, h11 = cubic_hermite_basis(u)
    dh00, dh10, dh01, dh11 = cubic_hermite_basis_derivative(u)
    start_tangent = duration_seconds * start_velocity
    end_tangent = duration_seconds * end_velocity
    position = (
        h00 * start_position
        + h10 * start_tangent
        + h01 * end_position
        + h11 * end_tangent
    )
    velocity = (
        dh00 * start_position
        + dh10 * start_tangent
        + dh01 * end_position
        + dh11 * end_tangent
    ) / duration_seconds
    return position, velocity


def _endpoint_state(
    segment: ProceduralSegment,
    *,
    end: bool,
) -> ReplayState:
    prefix = "end" if end else "start"
    return ReplayState(
        procedural_track_id=segment.procedural_track_id,
        timestamp_ns=getattr(segment, f"{prefix}_time_ns"),
        x_m=getattr(segment, f"{prefix}_x_m"),
        y_m=getattr(segment, f"{prefix}_y_m"),
        z_m=getattr(segment, f"{prefix}_z_m"),
        heading_rad=getattr(segment, f"{prefix}_heading_rad"),
        velocity_x_mps=getattr(segment, f"{prefix}_velocity_x_mps"),
        velocity_y_mps=getattr(segment, f"{prefix}_velocity_y_mps"),
        origin_type=OriginType.DECODED,
    )


def evaluate_segment(
    segment: ProceduralSegment,
    timestamp_ns: int,
) -> ReplayState | None:
    """Evaluate a segment at an integer-nanosecond timestamp."""
    if not isinstance(segment, ProceduralSegment):
        raise ValidationError("segment must be a ProceduralSegment")
    if not isinstance(timestamp_ns, int) or isinstance(timestamp_ns, bool):
        raise ValidationError("timestamp_ns must be a non-Boolean integer")
    if timestamp_ns < segment.start_time_ns or timestamp_ns > segment.end_time_ns:
        return None
    if timestamp_ns == segment.start_time_ns:
        return _endpoint_state(segment, end=False)
    if timestamp_ns == segment.end_time_ns:
        return _endpoint_state(segment, end=True)
    alpha = (timestamp_ns - segment.start_time_ns) / (
        segment.end_time_ns - segment.start_time_ns
    )
    if segment.primitive_type is ProceduralPrimitiveType.HOLD:
        return ReplayState(
            procedural_track_id=segment.procedural_track_id,
            timestamp_ns=timestamp_ns,
            x_m=segment.start_x_m,
            y_m=segment.start_y_m,
            z_m=segment.start_z_m,
            heading_rad=_interpolate_heading(
                segment.start_heading_rad,
                segment.end_heading_rad,
                alpha,
            ),
            velocity_x_mps=_interpolate_optional(
                segment.start_velocity_x_mps,
                segment.end_velocity_x_mps,
                alpha,
            ),
            velocity_y_mps=_interpolate_optional(
                segment.start_velocity_y_mps,
                segment.end_velocity_y_mps,
                alpha,
            ),
            origin_type=OriginType.DECODED,
        )
    if segment.primitive_type is ProceduralPrimitiveType.CUBIC_HERMITE:
        if any(
            value is None
            for value in (
                segment.start_velocity_x_mps,
                segment.start_velocity_y_mps,
                segment.end_velocity_x_mps,
                segment.end_velocity_y_mps,
            )
        ):
            raise ValidationError(
                "cubic Hermite endpoints require complete x/y velocity"
            )
        duration_seconds = (
            segment.end_time_ns - segment.start_time_ns
        ) / 1_000_000_000.0
        start_velocity_x = segment.start_velocity_x_mps
        start_velocity_y = segment.start_velocity_y_mps
        end_velocity_x = segment.end_velocity_x_mps
        end_velocity_y = segment.end_velocity_y_mps
        if (
            start_velocity_x is None
            or start_velocity_y is None
            or end_velocity_x is None
            or end_velocity_y is None
        ):
            raise ValidationError("cubic Hermite velocity validation failed")
        x_m, velocity_x_mps = _cubic_hermite_axis(
            segment.start_x_m,
            segment.end_x_m,
            start_velocity_x,
            end_velocity_x,
            duration_seconds,
            alpha,
        )
        y_m, velocity_y_mps = _cubic_hermite_axis(
            segment.start_y_m,
            segment.end_y_m,
            start_velocity_y,
            end_velocity_y,
            duration_seconds,
            alpha,
        )
        return ReplayState(
            procedural_track_id=segment.procedural_track_id,
            timestamp_ns=timestamp_ns,
            x_m=x_m,
            y_m=y_m,
            z_m=_interpolate_optional(segment.start_z_m, segment.end_z_m, alpha),
            heading_rad=_interpolate_heading(
                segment.start_heading_rad,
                segment.end_heading_rad,
                alpha,
            ),
            velocity_x_mps=velocity_x_mps,
            velocity_y_mps=velocity_y_mps,
            origin_type=OriginType.DECODED,
        )
    return ReplayState(
        procedural_track_id=segment.procedural_track_id,
        timestamp_ns=timestamp_ns,
        x_m=segment.start_x_m + (segment.end_x_m - segment.start_x_m) * alpha,
        y_m=segment.start_y_m + (segment.end_y_m - segment.start_y_m) * alpha,
        z_m=_interpolate_optional(segment.start_z_m, segment.end_z_m, alpha),
        heading_rad=_interpolate_heading(
            segment.start_heading_rad,
            segment.end_heading_rad,
            alpha,
        ),
        velocity_x_mps=_interpolate_optional(
            segment.start_velocity_x_mps,
            segment.end_velocity_x_mps,
            alpha,
        ),
        velocity_y_mps=_interpolate_optional(
            segment.start_velocity_y_mps,
            segment.end_velocity_y_mps,
            alpha,
        ),
        origin_type=OriginType.DECODED,
    )


def replay_track(
    track: ProceduralTrack,
    timestamp_ns: int,
) -> ReplayState | None:
    """Replay a track, returning ``None`` outside support and across gaps."""
    if not isinstance(track, ProceduralTrack):
        raise ValidationError("track must be a ProceduralTrack")
    for segment in track.segments:
        state = evaluate_segment(segment, timestamp_ns)
        if state is not None:
            return state
        if segment.start_time_ns > timestamp_ns:
            break
    return None


def replay_timestamps(
    track: ProceduralTrack,
    timestamps_ns: Iterable[int],
) -> tuple[ReplayState | None, ...]:
    """Replay a finite ordered timestamp sequence without filesystem access."""
    if isinstance(timestamps_ns, (str, bytes)):
        raise ValidationError("timestamps_ns must be a non-string iterable")
    timestamps = tuple(timestamps_ns)
    if any(
        not isinstance(timestamp, int) or isinstance(timestamp, bool)
        for timestamp in timestamps
    ):
        raise ValidationError("timestamps_ns must contain non-Boolean integers")
    if any(current < previous for previous, current in pairwise(timestamps)):
        raise ValidationError("timestamps_ns must be ordered")
    return tuple(replay_track(track, timestamp) for timestamp in timestamps)


def decode_source_endpoint_states(
    track: ProceduralTrack,
) -> tuple[tuple[int, ReplayState], ...]:
    """Decode each represented source sample index exactly once."""
    decoded: dict[int, ReplayState] = {}
    for segment in track.segments:
        for sample_index, state in (
            (
                segment.source_start_sample_index,
                _endpoint_state(segment, end=False),
            ),
            (segment.source_end_sample_index, _endpoint_state(segment, end=True)),
        ):
            existing = decoded.get(sample_index)
            if existing is not None and existing != state:
                raise ValidationError("shared segment endpoint states differ")
            decoded[sample_index] = state
    return tuple(sorted(decoded.items()))
