"""Deterministic semantic waypoint and motion-event detection."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
import math
from pathlib import Path

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.codecs.exact import replay_track
from kinematicweave.codecs.piecewise_linear import _parquet_rows, _trajectory_from_rows
from kinematicweave.codecs.velocity_bounded import (
    VelocityBoundedCodecConfig,
    encode_canonical_parquet_velocity_bounded,
    validate_velocity_bounded_replay,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.procedural import ProceduralTape, ProceduralTrack
from kinematicweave.domain.records import OriginType, Trajectory, TrajectorySampleRecord
from kinematicweave.domain.semantic import (
    MotionEvent,
    MotionEventType,
    SemanticMotionTape,
    SemanticMotionTrack,
    SemanticWaypoint,
    SemanticWaypointRole,
)
from kinematicweave.errors import ArtifactError, ValidationError

__all__ = [
    "SEMANTIC_ALGORITHM_VERSION",
    "SemanticMotionConfig",
    "analyze_event_preservation",
    "build_semantic_motion_tape",
    "build_semantic_motion_track",
    "detect_semantic_trajectory",
    "encode_canonical_parquet_semantic_motion",
    "replay_detection_trajectory",
    "semantic_motion_configuration_identity",
    "summarize_semantic_motion_tape",
    "validate_semantic_waypoint_replay",
]

SEMANTIC_ALGORITHM_VERSION = "maximal-run-groups-v1"
_SPEED_SOURCE_POLICY = (
    "stored_speed_then_velocity_norm_then_timestamp_finite_difference"
)
_HEADING_SOURCE_POLICY = (
    "stored_heading_then_moving_velocity_heading_then_displacement_direction"
)
_GAP_POLICY = "invalid_samples_terminate_runs_two_sided_gaps_only"
_GROUPING_POLICY = "maximal_contiguous_qualifying_intervals_per_valid_run"
_ANCHOR_POLICY = (
    "gap_start;stop_minimum_speed;turn_maximum_cumulative_angle;"
    "acceleration_or_braking_maximum_absolute_acceleration;"
    "nonturn_anchors_reuse_the_event_start_role_at_the_anchor_sample"
)
_ORDERING_POLICY = (
    "waypoint_timestamp_source_index_role_id;"
    "event_start_end_type_id;earliest_source_sample_ties"
)
_INT64_MAX = 2**63 - 1


def _threshold(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValidationError(f"{field_name} must be finite")
    if normalized < 0.0:
        raise ValidationError(f"{field_name} must not be negative")
    return normalized


def _duration(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if not 0 <= value <= _INT64_MAX:
        raise ValidationError(f"{field_name} must be a nonnegative int64")
    return value


@dataclass(frozen=True, slots=True)
class SemanticMotionConfig:
    """Fixed development configuration for deterministic semantic detection."""

    stop_speed_threshold_mps: float = 0.50
    minimum_stop_duration_ns: int = 1_000_000_000
    turn_delta_deadband_rad: float = 0.01
    minimum_turn_angle_rad: float = 0.35
    minimum_turn_duration_ns: int = 300_000_000
    acceleration_deadband_mps2: float = 0.20
    minimum_speed_change_mps: float = 1.00
    minimum_acceleration_duration_ns: int = 300_000_000

    def __post_init__(self) -> None:
        for field_name in (
            "stop_speed_threshold_mps",
            "turn_delta_deadband_rad",
            "minimum_turn_angle_rad",
            "acceleration_deadband_mps2",
            "minimum_speed_change_mps",
        ):
            object.__setattr__(
                self,
                field_name,
                _threshold(getattr(self, field_name), field_name),
            )
        for field_name in (
            "minimum_stop_duration_ns",
            "minimum_turn_duration_ns",
            "minimum_acceleration_duration_ns",
        ):
            object.__setattr__(
                self,
                field_name,
                _duration(getattr(self, field_name), field_name),
            )


_DEFAULT_CONFIG = SemanticMotionConfig()
_DEFAULT_CODEC_CONFIG = VelocityBoundedCodecConfig()


def semantic_motion_configuration_identity(config: SemanticMotionConfig) -> str:
    """Return the identity of all behavior-affecting detector settings."""
    if not isinstance(config, SemanticMotionConfig):
        raise ValidationError("config must be a SemanticMotionConfig")
    return canonical_sha256(
        "semantic-motion-detector-configuration",
        {
            "algorithm_version": SEMANTIC_ALGORITHM_VERSION,
            "stop_speed_threshold_mps": config.stop_speed_threshold_mps,
            "minimum_stop_duration_ns": config.minimum_stop_duration_ns,
            "turn_delta_deadband_rad": config.turn_delta_deadband_rad,
            "minimum_turn_angle_rad": config.minimum_turn_angle_rad,
            "minimum_turn_duration_ns": config.minimum_turn_duration_ns,
            "acceleration_deadband_mps2": config.acceleration_deadband_mps2,
            "minimum_speed_change_mps": config.minimum_speed_change_mps,
            "minimum_acceleration_duration_ns": (
                config.minimum_acceleration_duration_ns
            ),
            "speed_source_policy": _SPEED_SOURCE_POLICY,
            "heading_source_policy": _HEADING_SOURCE_POLICY,
            "gap_policy": _GAP_POLICY,
            "interval_grouping_policy": _GROUPING_POLICY,
            "event_anchor_policy": _ANCHOR_POLICY,
            "ordering_and_tie_break_contract": _ORDERING_POLICY,
        },
    )


@dataclass(frozen=True, slots=True)
class _Feature:
    sample: TrajectorySampleRecord
    speed_mps: float
    speed_source: str
    heading_rad: float | None
    heading_source: str


@dataclass(frozen=True, slots=True)
class _DraftEvent:
    event_type: MotionEventType
    start: _Feature
    anchor: _Feature
    end: _Feature
    magnitude: float | None
    attributes: Mapping[str, object]
    ordinal: int


@dataclass(frozen=True, slots=True)
class _DraftWaypoint:
    role: SemanticWaypointRole
    feature: _Feature
    related_event_id: str | None
    ordinal: int


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


def _finite_difference_vector(
    run: Sequence[TrajectorySampleRecord],
    index: int,
) -> tuple[float, float] | None:
    if len(run) < 2:
        return None
    left = max(0, index - 1)
    right = min(len(run) - 1, index + 1)
    elapsed_ns = run[right].timestamp_ns - run[left].timestamp_ns
    if elapsed_ns <= 0:
        return None
    scale = 1_000_000_000.0 / elapsed_ns
    return (
        (run[right].x_m - run[left].x_m) * scale,
        (run[right].y_m - run[left].y_m) * scale,
    )


def _features(
    run: Sequence[TrajectorySampleRecord],
    config: SemanticMotionConfig,
) -> tuple[_Feature, ...]:
    values: list[_Feature] = []
    for index, sample in enumerate(run):
        vector = _finite_difference_vector(run, index)
        if sample.speed_mps is not None:
            speed = sample.speed_mps
            speed_source = "stored_speed"
        elif sample.velocity_x_mps is not None and sample.velocity_y_mps is not None:
            speed = math.hypot(sample.velocity_x_mps, sample.velocity_y_mps)
            speed_source = "velocity_vector"
        elif vector is not None:
            speed = math.hypot(*vector)
            speed_source = "finite_difference_position"
        else:
            speed = 0.0
            speed_source = "singleton_zero_fallback"

        if sample.heading_rad is not None:
            heading = sample.heading_rad
            heading_source = "stored_heading"
        elif (
            sample.velocity_x_mps is not None
            and sample.velocity_y_mps is not None
            and speed > config.stop_speed_threshold_mps
        ):
            heading = math.atan2(sample.velocity_y_mps, sample.velocity_x_mps)
            heading_source = "velocity_vector"
        elif vector is not None and math.hypot(*vector) > 1.0e-12:
            heading = math.atan2(vector[1], vector[0])
            heading_source = "finite_difference_displacement"
        else:
            heading = None
            heading_source = "unavailable"
        values.append(
            _Feature(
                sample=sample,
                speed_mps=speed,
                speed_source=speed_source,
                heading_rad=heading,
                heading_source=heading_source,
            )
        )
    return tuple(values)


def _sources(features: Sequence[_Feature], field_name: str) -> list[str]:
    return sorted({str(getattr(feature, field_name)) for feature in features})


def _stop_events(
    run: Sequence[_Feature],
    config: SemanticMotionConfig,
) -> tuple[_DraftEvent, ...]:
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for index, feature in enumerate(run):
        qualifies = feature.speed_mps <= config.stop_speed_threshold_mps
        if qualifies and start is None:
            start = index
        if start is not None and (not qualifies or index == len(run) - 1):
            end = index if qualifies else index - 1
            groups.append((start, end))
            start = None
    events: list[_DraftEvent] = []
    for ordinal, (start_index, end_index) in enumerate(groups):
        start_feature = run[start_index]
        end_feature = run[end_index]
        duration = end_feature.sample.timestamp_ns - start_feature.sample.timestamp_ns
        if duration < config.minimum_stop_duration_ns:
            continue
        interval = run[start_index : end_index + 1]
        anchor = min(
            interval,
            key=lambda item: (item.speed_mps, item.sample.sample_index),
        )
        speeds = [item.speed_mps for item in interval]
        preceding = start_index > 0 and run[start_index - 1].speed_mps > (
            config.stop_speed_threshold_mps
        )
        following = end_index + 1 < len(run) and run[end_index + 1].speed_mps > (
            config.stop_speed_threshold_mps
        )
        events.append(
            _DraftEvent(
                event_type=MotionEventType.STOP,
                start=start_feature,
                anchor=anchor,
                end=end_feature,
                magnitude=max(speeds),
                attributes={
                    "duration_ns": duration,
                    "following_motion_present": following,
                    "lacks_motion_context": not preceding and not following,
                    "maximum_speed_mps": max(speeds),
                    "mean_speed_mps": math.fsum(speeds) / len(speeds),
                    "minimum_speed_mps": min(speeds),
                    "preceding_motion_present": preceding,
                    "speed_source_policy": _SPEED_SOURCE_POLICY,
                    "speed_sources": _sources(interval, "speed_source"),
                },
                ordinal=ordinal,
            )
        )
    return tuple(events)


def _wrapped_delta(start: float, end: float) -> float:
    return (end - start + math.pi) % (2.0 * math.pi) - math.pi


def _signed_groups(values: Sequence[float]) -> tuple[tuple[int, int, int], ...]:
    groups: list[tuple[int, int, int]] = []
    start: int | None = None
    sign = 0
    for index, value in enumerate(values):
        current_sign = 1 if value > 0.0 else -1 if value < 0.0 else 0
        if current_sign and start is None:
            start = index
            sign = current_sign
        elif start is not None and current_sign != sign:
            groups.append((start, index - 1, sign))
            start = index if current_sign else None
            sign = current_sign
    if start is not None:
        groups.append((start, len(values) - 1, sign))
    return tuple(groups)


def _turn_events(
    run: Sequence[_Feature],
    config: SemanticMotionConfig,
) -> tuple[_DraftEvent, ...]:
    deltas: list[float] = []
    for left, right in pairwise(run):
        if left.heading_rad is None or right.heading_rad is None:
            deltas.append(0.0)
            continue
        delta = _wrapped_delta(left.heading_rad, right.heading_rad)
        deltas.append(0.0 if abs(delta) < config.turn_delta_deadband_rad else delta)
    events: list[_DraftEvent] = []
    ordinals = {MotionEventType.LEFT_TURN: 0, MotionEventType.RIGHT_TURN: 0}
    for first_delta, last_delta, sign in _signed_groups(deltas):
        start = run[first_delta]
        end = run[last_delta + 1]
        duration = end.sample.timestamp_ns - start.sample.timestamp_ns
        interval_deltas = deltas[first_delta : last_delta + 1]
        signed_angle = math.fsum(interval_deltas)
        if (
            abs(signed_angle) < config.minimum_turn_angle_rad
            or duration < config.minimum_turn_duration_ns
        ):
            continue
        event_type = (
            MotionEventType.LEFT_TURN if sign > 0 else MotionEventType.RIGHT_TURN
        )
        cumulative = 0.0
        candidates: list[tuple[float, _Feature]] = [(0.0, start)]
        for offset, delta in enumerate(interval_deltas, start=1):
            cumulative += delta
            candidates.append((abs(cumulative), run[first_delta + offset]))
        anchor = max(
            candidates,
            key=lambda item: (item[0], -item[1].sample.sample_index),
        )[1]
        ordinal = ordinals[event_type]
        ordinals[event_type] += 1
        interval_features = run[first_delta : last_delta + 2]
        events.append(
            _DraftEvent(
                event_type=event_type,
                start=start,
                anchor=anchor,
                end=end,
                magnitude=signed_angle,
                attributes={
                    "absolute_angle_rad": abs(signed_angle),
                    "duration_ns": duration,
                    "heading_source_policy": _HEADING_SOURCE_POLICY,
                    "heading_sources": _sources(
                        interval_features,
                        "heading_source",
                    ),
                    "maximum_step_delta_rad": max(
                        (abs(value) for value in interval_deltas),
                        default=0.0,
                    ),
                    "mean_turn_rate_radps": (
                        signed_angle / (duration / 1_000_000_000.0)
                    ),
                    "signed_angle_rad": signed_angle,
                    "source_sample_range": [
                        start.sample.sample_index,
                        end.sample.sample_index,
                    ],
                },
                ordinal=ordinal,
            )
        )
    return tuple(events)


def _acceleration_events(
    run: Sequence[_Feature],
    config: SemanticMotionConfig,
) -> tuple[_DraftEvent, ...]:
    accelerations: list[float] = []
    for left, right in pairwise(run):
        duration_ns = right.sample.timestamp_ns - left.sample.timestamp_ns
        if duration_ns <= 0:
            accelerations.append(0.0)
            continue
        acceleration = (right.speed_mps - left.speed_mps) / (
            duration_ns / 1_000_000_000.0
        )
        accelerations.append(
            0.0
            if abs(acceleration) < config.acceleration_deadband_mps2
            else acceleration
        )
    events: list[_DraftEvent] = []
    ordinals = {MotionEventType.ACCELERATION: 0, MotionEventType.BRAKING: 0}
    for first_delta, last_delta, sign in _signed_groups(accelerations):
        start = run[first_delta]
        end = run[last_delta + 1]
        duration = end.sample.timestamp_ns - start.sample.timestamp_ns
        net_change = end.speed_mps - start.speed_mps
        if (
            abs(net_change) < config.minimum_speed_change_mps
            or duration < config.minimum_acceleration_duration_ns
        ):
            continue
        interval_accelerations = accelerations[first_delta : last_delta + 1]
        anchor_offset = max(
            range(len(interval_accelerations)),
            key=lambda index: (
                abs(interval_accelerations[index]),
                -run[first_delta + index].sample.sample_index,
            ),
        )
        anchor = run[first_delta + anchor_offset]
        event_type = (
            MotionEventType.ACCELERATION if sign > 0 else MotionEventType.BRAKING
        )
        ordinal = ordinals[event_type]
        ordinals[event_type] += 1
        interval_features = run[first_delta : last_delta + 2]
        events.append(
            _DraftEvent(
                event_type=event_type,
                start=start,
                anchor=anchor,
                end=end,
                magnitude=net_change,
                attributes={
                    "duration_ns": duration,
                    "mean_acceleration_mps2": (
                        net_change / (duration / 1_000_000_000.0)
                    ),
                    "net_speed_change_mps": net_change,
                    "peak_acceleration_mps2": interval_accelerations[anchor_offset],
                    "source_sample_range": [
                        start.sample.sample_index,
                        end.sample.sample_index,
                    ],
                    "speed_source_policy": _SPEED_SOURCE_POLICY,
                    "speed_sources": _sources(interval_features, "speed_source"),
                },
                ordinal=ordinal,
            )
        )
    return tuple(events)


def _event_id(track: ProceduralTrack, event: _DraftEvent) -> str:
    digest = canonical_sha256(
        "semantic-motion-event",
        {
            "tape_id": track.tape_id,
            "procedural_track_id": track.procedural_track_id,
            "event_type": event.event_type.value,
            "start_source_sample_index": event.start.sample.sample_index,
            "anchor_source_sample_index": event.anchor.sample.sample_index,
            "end_source_sample_index": event.end.sample.sample_index,
            "event_ordinal": event.ordinal,
        },
    )
    return f"motion-event:{digest}"


def _waypoint_id(track: ProceduralTrack, waypoint: _DraftWaypoint) -> str:
    digest = canonical_sha256(
        "semantic-motion-waypoint",
        {
            "tape_id": track.tape_id,
            "procedural_track_id": track.procedural_track_id,
            "role": waypoint.role.value,
            "source_sample_index": waypoint.feature.sample.sample_index,
            "related_event_id": waypoint.related_event_id,
            "role_ordinal": waypoint.ordinal,
        },
    )
    return f"semantic-waypoint:{digest}"


def _roles(
    event_type: MotionEventType,
) -> tuple[
    SemanticWaypointRole,
    SemanticWaypointRole,
    SemanticWaypointRole,
]:
    if event_type is MotionEventType.GAP:
        return (
            SemanticWaypointRole.GAP_START,
            SemanticWaypointRole.GAP_START,
            SemanticWaypointRole.GAP_END,
        )
    if event_type is MotionEventType.STOP:
        return (
            SemanticWaypointRole.STOP_START,
            SemanticWaypointRole.STOP_START,
            SemanticWaypointRole.STOP_END,
        )
    if event_type in (MotionEventType.LEFT_TURN, MotionEventType.RIGHT_TURN):
        return (
            SemanticWaypointRole.TURN_ENTRY,
            SemanticWaypointRole.TURN_APEX,
            SemanticWaypointRole.TURN_EXIT,
        )
    if event_type is MotionEventType.ACCELERATION:
        return (
            SemanticWaypointRole.ACCELERATION_START,
            SemanticWaypointRole.ACCELERATION_START,
            SemanticWaypointRole.ACCELERATION_END,
        )
    return (
        SemanticWaypointRole.BRAKING_START,
        SemanticWaypointRole.BRAKING_START,
        SemanticWaypointRole.BRAKING_END,
    )


def _build_records(
    track: ProceduralTrack,
    structural: Sequence[_DraftWaypoint],
    drafts: Sequence[_DraftEvent],
) -> tuple[tuple[SemanticWaypoint, ...], tuple[MotionEvent, ...]]:
    draft_events = tuple(
        sorted(
            drafts,
            key=lambda item: (
                item.start.sample.timestamp_ns,
                item.end.sample.timestamp_ns,
                tuple(MotionEventType).index(item.event_type),
                _event_id(track, item),
            ),
        )
    )
    event_ids = {_event_id(track, item): item for item in draft_events}
    waypoint_drafts = list(structural)
    event_waypoint_ids: dict[str, tuple[str, str, str]] = {}
    for event_id, event in event_ids.items():
        roles = _roles(event.event_type)
        features = (event.start, event.anchor, event.end)
        event_drafts: list[_DraftWaypoint] = []
        for ordinal, (role, feature) in enumerate(zip(roles, features, strict=True)):
            matching = next(
                (
                    existing
                    for existing in event_drafts
                    if existing.role is role
                    and existing.feature.sample.sample_index
                    == feature.sample.sample_index
                ),
                None,
            )
            draft = matching or _DraftWaypoint(role, feature, event_id, ordinal)
            if matching is None:
                event_drafts.append(draft)
                waypoint_drafts.append(draft)
        identifiers = tuple(
            _waypoint_id(
                track,
                next(
                    item
                    for item in event_drafts
                    if item.role is role
                    and item.feature.sample.sample_index == feature.sample.sample_index
                ),
            )
            for role, feature in zip(roles, features, strict=True)
        )
        event_waypoint_ids[event_id] = (
            identifiers[0],
            identifiers[1],
            identifiers[2],
        )

    role_rank = {role: index for index, role in enumerate(SemanticWaypointRole)}
    ordered_drafts = tuple(
        sorted(
            waypoint_drafts,
            key=lambda item: (
                item.feature.sample.timestamp_ns,
                item.feature.sample.sample_index,
                role_rank[item.role],
                _waypoint_id(track, item),
            ),
        )
    )
    waypoints = tuple(
        SemanticWaypoint(
            tape_id=track.tape_id,
            procedural_track_id=track.procedural_track_id,
            waypoint_id=_waypoint_id(track, draft),
            waypoint_index=index,
            waypoint_role=draft.role,
            timestamp_ns=draft.feature.sample.timestamp_ns,
            source_sample_index=draft.feature.sample.sample_index,
            x_m=draft.feature.sample.x_m,
            y_m=draft.feature.sample.y_m,
            z_m=draft.feature.sample.z_m,
            heading_rad=draft.feature.heading_rad,
            speed_mps=draft.feature.speed_mps,
            related_event_ids=(
                () if draft.related_event_id is None else (draft.related_event_id,)
            ),
            origin_type=OriginType.INFERRED,
            quality_flags=(),
        )
        for index, draft in enumerate(ordered_drafts)
    )
    events = tuple(
        MotionEvent(
            tape_id=track.tape_id,
            procedural_track_id=track.procedural_track_id,
            event_id=event_id,
            event_index=index,
            event_type=draft.event_type,
            start_waypoint_id=event_waypoint_ids[event_id][0],
            anchor_waypoint_id=event_waypoint_ids[event_id][1],
            end_waypoint_id=event_waypoint_ids[event_id][2],
            start_time_ns=draft.start.sample.timestamp_ns,
            anchor_time_ns=draft.anchor.sample.timestamp_ns,
            end_time_ns=draft.end.sample.timestamp_ns,
            magnitude_value=draft.magnitude,
            semantic_attributes_json=canonical_json_text(
                draft.attributes,
                trailing_newline=False,
            ),
            origin_type=OriginType.INFERRED,
            quality_flags=(),
        )
        for index, (event_id, draft) in enumerate(event_ids.items())
    )
    return waypoints, events


def detect_semantic_trajectory(
    trajectory: Trajectory,
    procedural_track: ProceduralTrack,
    config: SemanticMotionConfig = _DEFAULT_CONFIG,
) -> SemanticMotionTrack:
    """Detect deterministic source-derived semantics for one trajectory."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(procedural_track, ProceduralTrack):
        raise ValidationError("procedural_track must be a ProceduralTrack")
    if trajectory.trajectory_id != procedural_track.trajectory_id:
        raise ValidationError("trajectory and procedural track identifiers differ")
    if not isinstance(config, SemanticMotionConfig):
        raise ValidationError("config must be a SemanticMotionConfig")
    runs = _valid_runs(trajectory.samples)
    if len(runs) != procedural_track.run_count:
        raise ValidationError("source and procedural valid-run counts differ")
    feature_runs = tuple(_features(run, config) for run in runs)
    structural = [
        _DraftWaypoint(
            SemanticWaypointRole.TRACK_START,
            feature_runs[0][0],
            None,
            0,
        ),
        _DraftWaypoint(
            SemanticWaypointRole.TRACK_END,
            feature_runs[-1][-1],
            None,
            0,
        ),
    ]
    for run_index, run in enumerate(feature_runs):
        structural.extend(
            (
                _DraftWaypoint(
                    SemanticWaypointRole.RUN_START,
                    run[0],
                    None,
                    run_index,
                ),
                _DraftWaypoint(
                    SemanticWaypointRole.RUN_END,
                    run[-1],
                    None,
                    run_index,
                ),
            )
        )

    drafts: list[_DraftEvent] = []
    for run in feature_runs:
        drafts.extend(_stop_events(run, config))
        drafts.extend(_turn_events(run, config))
        drafts.extend(_acceleration_events(run, config))
    for gap_index, (left, right) in enumerate(pairwise(feature_runs)):
        left_sample = left[-1].sample
        right_sample = right[0].sample
        invalid = tuple(
            sample
            for sample in trajectory.samples
            if left_sample.sample_index
            < sample.sample_index
            < right_sample.sample_index
            and not sample.is_valid
        )
        drafts.append(
            _DraftEvent(
                event_type=MotionEventType.GAP,
                start=left[-1],
                anchor=left[-1],
                end=right[0],
                magnitude=None,
                attributes={
                    "first_invalid_timestamp_ns": invalid[0].timestamp_ns,
                    "following_run_index": gap_index + 1,
                    "invalid_sample_count": len(invalid),
                    "last_invalid_timestamp_ns": invalid[-1].timestamp_ns,
                    "preceding_run_index": gap_index,
                },
                ordinal=gap_index,
            )
        )
    waypoints, events = _build_records(procedural_track, structural, drafts)
    first_valid_index = runs[0][0].sample_index
    last_valid_index = runs[-1][-1].sample_index
    return SemanticMotionTrack(
        procedural_track=procedural_track,
        waypoints=waypoints,
        events=events,
        leading_invalid_sample_count=first_valid_index,
        trailing_invalid_sample_count=trajectory.sample_count - last_valid_index - 1,
    )


def build_semantic_motion_track(
    trajectory: Trajectory,
    procedural_track: ProceduralTrack,
    config: SemanticMotionConfig = _DEFAULT_CONFIG,
) -> SemanticMotionTrack:
    """Construct and validate one semantic motion track."""
    return detect_semantic_trajectory(trajectory, procedural_track, config)


def build_semantic_motion_tape(
    procedural_tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
    config: SemanticMotionConfig = _DEFAULT_CONFIG,
) -> SemanticMotionTape:
    """Construct one semantic tape referencing an existing procedural tape."""
    if not isinstance(procedural_tape, ProceduralTape):
        raise ValidationError("procedural_tape must be a ProceduralTape")
    values = tuple(trajectories)
    source_by_id = {item.trajectory_id: item for item in values}
    if len(source_by_id) != len(values):
        raise ValidationError("trajectory identifiers must be unique")
    try:
        tracks = tuple(
            build_semantic_motion_track(
                source_by_id[track.trajectory_id],
                track,
                config,
            )
            for track in procedural_tape.tracks
        )
    except KeyError as error:
        raise ValidationError(
            "procedural tape references unknown trajectory"
        ) from error
    return SemanticMotionTape(
        procedural_tape=procedural_tape,
        tracks=tracks,
        detector_configuration_identity=semantic_motion_configuration_identity(config),
        source_validation_identity=(procedural_tape.source_validation_report_identity),
    )


def encode_canonical_parquet_semantic_motion(
    repository_root: Path,
    *,
    scenario_paths: Sequence[str | Path],
    coordinate_frame_paths: Sequence[str | Path],
    agent_paths: Sequence[str | Path],
    trajectory_sample_paths: Sequence[str | Path],
    source_validation_report_identity: str | None,
    semantic_config: SemanticMotionConfig = _DEFAULT_CONFIG,
    codec_config: VelocityBoundedCodecConfig = _DEFAULT_CODEC_CONFIG,
    batch_size: int = 65_536,
) -> SemanticMotionTape:
    """Build procedural and semantic layers using bounded canonical readers."""
    procedural_tape = encode_canonical_parquet_velocity_bounded(
        repository_root,
        scenario_paths=scenario_paths,
        coordinate_frame_paths=coordinate_frame_paths,
        agent_paths=agent_paths,
        trajectory_sample_paths=trajectory_sample_paths,
        source_validation_report_identity=source_validation_report_identity,
        config=codec_config,
        batch_size=batch_size,
    )
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in _parquet_rows(
        repository_root,
        trajectory_sample_paths,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        batch_size,
    ):
        trajectory_id = row.get("trajectory_id")
        if not isinstance(trajectory_id, str):
            raise ArtifactError("trajectory sample has invalid trajectory_id")
        grouped.setdefault(trajectory_id, []).append(row)
    trajectories = tuple(
        _trajectory_from_rows(grouped[trajectory_id])
        for trajectory_id in sorted(grouped)
    )
    return build_semantic_motion_tape(
        procedural_tape,
        trajectories,
        semantic_config,
    )


def replay_detection_trajectory(
    trajectory: Trajectory,
    procedural_track: ProceduralTrack,
) -> Trajectory:
    """Build detector-only samples from replay at original source timestamps."""
    samples: list[TrajectorySampleRecord] = []
    for source in trajectory.samples:
        state = replay_track(procedural_track, source.timestamp_ns)
        if not source.is_valid:
            state = None
        if source.is_valid and state is None:
            raise ValidationError("valid source sample has no procedural replay state")
        samples.append(
            TrajectorySampleRecord(
                scenario_id=source.scenario_id,
                agent_id=source.agent_id,
                trajectory_id=source.trajectory_id,
                sample_index=source.sample_index,
                timestamp_ns=source.timestamp_ns,
                x_m=source.x_m if state is None else state.x_m,
                y_m=source.y_m if state is None else state.y_m,
                z_m=source.z_m if state is None else state.z_m,
                heading_rad=None if state is None else state.heading_rad,
                velocity_x_mps=None if state is None else state.velocity_x_mps,
                velocity_y_mps=None if state is None else state.velocity_y_mps,
                speed_mps=None,
                acceleration_x_mps2=None,
                acceleration_y_mps2=None,
                is_observed=source.is_observed,
                is_valid=source.is_valid,
                origin_type=OriginType.DECODED,
                quality_flags=source.quality_flags,
            )
        )
    return Trajectory(
        scenario_id=trajectory.scenario_id,
        agent_id=trajectory.agent_id,
        trajectory_id=trajectory.trajectory_id,
        samples=tuple(samples),
        origin_type=OriginType.DECODED,
        quality_flags=trajectory.quality_flags,
    )


def _interval_iou(left: MotionEvent, right: MotionEvent) -> float:
    intersection = max(
        0,
        min(left.end_time_ns, right.end_time_ns)
        - max(left.start_time_ns, right.start_time_ns),
    )
    union = max(left.end_time_ns, right.end_time_ns) - min(
        left.start_time_ns,
        right.start_time_ns,
    )
    if union == 0:
        return 1.0 if left.start_time_ns == right.start_time_ns else 0.0
    return intersection / union


def _error_statistics(values: Sequence[int]) -> dict[str, int | float]:
    if not values:
        return {
            "count": 0,
            "minimum": 0,
            "maximum": 0,
            "mean": 0.0,
            "median": 0.0,
            "p95": 0,
        }
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (
        float(ordered[middle])
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "maximum": ordered[-1],
        "mean": math.fsum(ordered) / len(ordered),
        "median": median,
        "p95": ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)],
    }


def analyze_event_preservation(
    source: SemanticMotionTrack,
    replay: SemanticMotionTrack,
) -> dict[str, object]:
    """Match source and replay events deterministically and report preservation."""
    if (
        source.procedural_track.procedural_track_id
        != replay.procedural_track.procedural_track_id
    ):
        raise ValidationError("preservation tracks must share one procedural track")
    by_type: dict[str, object] = {}
    aggregate_source = 0
    aggregate_replay = 0
    aggregate_matched = 0
    all_start_errors: list[int] = []
    all_end_errors: list[int] = []
    all_anchor_errors: list[int] = []
    for event_type in MotionEventType:
        source_events = tuple(
            item for item in source.events if item.event_type is event_type
        )
        replay_events = tuple(
            item for item in replay.events if item.event_type is event_type
        )
        candidates = sorted(
            (
                (
                    -_interval_iou(source_event, replay_event),
                    abs(source_event.start_time_ns - replay_event.start_time_ns),
                    abs(source_event.end_time_ns - replay_event.end_time_ns),
                    source_index,
                    replay_index,
                )
                for source_index, source_event in enumerate(source_events)
                for replay_index, replay_event in enumerate(replay_events)
            )
        )
        used_source: set[int] = set()
        used_replay: set[int] = set()
        matches: list[tuple[int, int]] = []
        for negative_iou, _, _, source_index, replay_index in candidates:
            if (
                negative_iou == 0.0
                or source_index in used_source
                or replay_index in used_replay
            ):
                continue
            used_source.add(source_index)
            used_replay.add(replay_index)
            matches.append((source_index, replay_index))
        start_errors = [
            abs(source_events[left].start_time_ns - replay_events[right].start_time_ns)
            for left, right in matches
        ]
        end_errors = [
            abs(source_events[left].end_time_ns - replay_events[right].end_time_ns)
            for left, right in matches
        ]
        anchor_errors = [
            abs(
                source_events[left].anchor_time_ns - replay_events[right].anchor_time_ns
            )
            for left, right in matches
        ]
        all_start_errors.extend(start_errors)
        all_end_errors.extend(end_errors)
        all_anchor_errors.extend(anchor_errors)
        source_count = len(source_events)
        replay_count = len(replay_events)
        matched_count = len(matches)
        precision = (
            matched_count / replay_count
            if replay_count
            else (1.0 if source_count == 0 else 0.0)
        )
        recall = (
            matched_count / source_count
            if source_count
            else (1.0 if replay_count == 0 else 0.0)
        )
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        by_type[event_type.value] = {
            "source_event_count": source_count,
            "replay_event_count": replay_count,
            "matched_count": matched_count,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "start_boundary_absolute_error_ns": _error_statistics(start_errors),
            "end_boundary_absolute_error_ns": _error_statistics(end_errors),
            "anchor_time_absolute_error_ns": _error_statistics(anchor_errors),
            "unmatched_source_count": source_count - matched_count,
            "unmatched_replay_count": replay_count - matched_count,
            "matches": [
                {
                    "source_event_index": source_events[left].event_index,
                    "replay_event_index": replay_events[right].event_index,
                    "temporal_iou": _interval_iou(
                        source_events[left],
                        replay_events[right],
                    ),
                    "start_boundary_absolute_error_ns": start_error,
                    "end_boundary_absolute_error_ns": end_error,
                    "anchor_time_absolute_error_ns": anchor_error,
                }
                for (left, right), start_error, end_error, anchor_error in zip(
                    matches,
                    start_errors,
                    end_errors,
                    anchor_errors,
                    strict=True,
                )
            ],
        }
        aggregate_source += source_count
        aggregate_replay += replay_count
        aggregate_matched += matched_count
    precision = (
        aggregate_matched / aggregate_replay
        if aggregate_replay
        else (1.0 if aggregate_source == 0 else 0.0)
    )
    recall = (
        aggregate_matched / aggregate_source
        if aggregate_source
        else (1.0 if aggregate_replay == 0 else 0.0)
    )
    return {
        "source_event_count": aggregate_source,
        "replay_event_count": aggregate_replay,
        "matched_count": aggregate_matched,
        "precision": precision,
        "recall": recall,
        "f1": (
            2.0 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        ),
        "by_type": by_type,
        "start_boundary_absolute_error_ns": _error_statistics(all_start_errors),
        "end_boundary_absolute_error_ns": _error_statistics(all_end_errors),
        "anchor_time_absolute_error_ns": _error_statistics(all_anchor_errors),
    }


def validate_semantic_waypoint_replay(
    semantic_track: SemanticMotionTrack,
    trajectory: Trajectory,
    codec_config: VelocityBoundedCodecConfig = _DEFAULT_CODEC_CONFIG,
) -> dict[str, object]:
    """Verify every semantic waypoint against source and Batch 3.4 replay bounds."""
    validate_velocity_bounded_replay(
        semantic_track.procedural_track,
        trajectory,
        codec_config,
    )
    source_by_index = {sample.sample_index: sample for sample in trajectory.samples}
    position_errors: list[float] = []
    velocity_errors: list[float] = []
    for waypoint in semantic_track.waypoints:
        if waypoint.source_sample_index is None:
            raise ValidationError("semantic waypoint lacks source sample index")
        source = source_by_index[waypoint.source_sample_index]
        state = replay_track(
            semantic_track.procedural_track,
            waypoint.timestamp_ns,
        )
        if state is None:
            raise ValidationError("semantic waypoint does not resolve in replay")
        z_error = (
            0.0
            if source.z_m is None and state.z_m is None
            else (state.z_m or 0.0) - (source.z_m or 0.0)
        )
        position_errors.append(
            math.sqrt(
                (state.x_m - source.x_m) ** 2
                + (state.y_m - source.y_m) ** 2
                + z_error**2
            )
        )
        if (
            source.velocity_x_mps is not None
            and source.velocity_y_mps is not None
            and state.velocity_x_mps is not None
            and state.velocity_y_mps is not None
        ):
            velocity_errors.append(
                math.hypot(
                    state.velocity_x_mps - source.velocity_x_mps,
                    state.velocity_y_mps - source.velocity_y_mps,
                )
            )
    return {
        "waypoint_count": len(semantic_track.waypoints),
        "position_errors_m": tuple(position_errors),
        "velocity_errors_mps": tuple(velocity_errors),
        "maximum_position_error_m": max(position_errors, default=0.0),
        "maximum_velocity_error_mps": max(velocity_errors, default=0.0),
    }


def summarize_semantic_motion_tape(
    tape: SemanticMotionTape,
    trajectories: Sequence[Trajectory],
    config: SemanticMotionConfig = _DEFAULT_CONFIG,
) -> dict[str, object]:
    """Return deterministic semantic counts and waypoint replay diagnostics."""
    source_by_id = {item.trajectory_id: item for item in trajectories}
    replay_validations = tuple(
        validate_semantic_waypoint_replay(
            track,
            source_by_id[track.procedural_track.trajectory_id],
        )
        for track in tape.tracks
    )
    return {
        "detector_configuration_identity": (
            semantic_motion_configuration_identity(config)
        ),
        "track_count": len(tape.tracks),
        "waypoint_count": sum(len(track.waypoints) for track in tape.tracks),
        "event_count": sum(len(track.events) for track in tape.tracks),
        "waypoint_counts_by_role": {
            role.value: sum(
                waypoint.waypoint_role is role
                for track in tape.tracks
                for waypoint in track.waypoints
            )
            for role in SemanticWaypointRole
        },
        "event_counts_by_type": {
            event_type.value: sum(
                event.event_type is event_type
                for track in tape.tracks
                for event in track.events
            )
            for event_type in MotionEventType
        },
        "tracks_containing_event_type": {
            event_type.value: sum(
                any(event.event_type is event_type for event in track.events)
                for track in tape.tracks
            )
            for event_type in MotionEventType
        },
        "leading_invalid_sample_count": sum(
            track.leading_invalid_sample_count for track in tape.tracks
        ),
        "trailing_invalid_sample_count": sum(
            track.trailing_invalid_sample_count for track in tape.tracks
        ),
        "maximum_waypoint_position_error_m": max(
            (
                _summary_number(item, "maximum_position_error_m")
                for item in replay_validations
            ),
            default=0.0,
        ),
        "maximum_waypoint_velocity_error_mps": max(
            (
                _summary_number(item, "maximum_velocity_error_mps")
                for item in replay_validations
            ),
            default=0.0,
        ),
    }


def _summary_number(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if not isinstance(item, (int, float)) or isinstance(item, bool):
        raise ValidationError(f"{key} must be numeric")
    return float(item)
