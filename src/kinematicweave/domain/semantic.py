"""Immutable semantic waypoint and motion-event domain records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
import json
import math

from kinematicweave.canonical import canonical_json_text
from kinematicweave.domain.procedural import ProceduralTape, ProceduralTrack
from kinematicweave.domain.records import OriginType
from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import validate_identifier

__all__ = [
    "MotionEvent",
    "MotionEventType",
    "SemanticMotionTape",
    "SemanticMotionTrack",
    "SemanticWaypoint",
    "SemanticWaypointRole",
    "motion_event_from_dict",
    "motion_event_to_dict",
    "semantic_waypoint_from_dict",
    "semantic_waypoint_to_dict",
    "validate_semantic_motion_tape",
    "validate_semantic_motion_track",
]

_INT32_MAX = 2**31 - 1
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


class SemanticWaypointRole(StrEnum):
    """Fixed semantic waypoint role vocabulary in tie-break order."""

    TRACK_START = "track_start"
    TRACK_END = "track_end"
    RUN_START = "run_start"
    RUN_END = "run_end"
    GAP_START = "gap_start"
    GAP_END = "gap_end"
    STOP_START = "stop_start"
    STOP_END = "stop_end"
    TURN_ENTRY = "turn_entry"
    TURN_APEX = "turn_apex"
    TURN_EXIT = "turn_exit"
    ACCELERATION_START = "acceleration_start"
    ACCELERATION_END = "acceleration_end"
    BRAKING_START = "braking_start"
    BRAKING_END = "braking_end"


class MotionEventType(StrEnum):
    """Fixed motion event vocabulary in canonical tie-break order."""

    GAP = "gap"
    STOP = "stop"
    LEFT_TURN = "left_turn"
    RIGHT_TURN = "right_turn"
    ACCELERATION = "acceleration"
    BRAKING = "braking"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{field_name} must be nonempty")
    return normalized


def _integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if not minimum <= value <= maximum:
        raise ValidationError(
            f"{field_name} must be in the range [{minimum}, {maximum}]"
        )
    return value


def _int32(value: object, field_name: str) -> int:
    return _integer(value, field_name, minimum=0, maximum=_INT32_MAX)


def _int64(value: object, field_name: str) -> int:
    return _integer(value, field_name, minimum=_INT64_MIN, maximum=_INT64_MAX)


def _finite(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValidationError(f"{field_name} must be finite")
    return normalized


def _optional_finite(value: object, field_name: str) -> float | None:
    return None if value is None else _finite(value, field_name)


def _identifier_sequence(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    normalized = tuple(validate_identifier(item) for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return normalized


def _flags(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("quality_flags must be a non-string sequence")
    normalized = tuple(_text(item, "quality_flags item") for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValidationError("quality_flags must not contain duplicates")
    return normalized


def _enum[EnumT: StrEnum](
    enum_type: type[EnumT],
    value: object,
    field_name: str,
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValidationError(f"{field_name} has invalid value {value!r}")


def _inferred(value: object) -> OriginType:
    normalized = _enum(OriginType, value, "origin_type")
    if normalized is not OriginType.INFERRED:
        raise ValidationError("semantic records must use OriginType.INFERRED")
    return normalized


def _role(value: object) -> SemanticWaypointRole:
    return _enum(SemanticWaypointRole, value, "waypoint_role")


def _event_type(value: object) -> MotionEventType:
    return _enum(MotionEventType, value, "event_type")


def _canonical_object_json(value: object) -> str:
    normalized = _text(value, "semantic_attributes_json")
    try:
        decoded = json.loads(normalized)
    except json.JSONDecodeError as error:
        raise ValidationError("semantic_attributes_json must be valid JSON") from error
    if not isinstance(decoded, dict):
        raise ValidationError("semantic_attributes_json must contain an object")
    try:
        canonical = canonical_json_text(decoded, trailing_newline=False)
    except ValidationError as error:
        raise ValidationError(
            "semantic_attributes_json contains unsupported values"
        ) from error
    if normalized != canonical:
        raise ValidationError("semantic_attributes_json must be canonical JSON")
    return canonical


@dataclass(frozen=True, slots=True)
class SemanticWaypoint:
    """One persistent semantic waypoint tied to a procedural track."""

    tape_id: str
    procedural_track_id: str
    waypoint_id: str
    waypoint_index: int
    waypoint_role: SemanticWaypointRole | str
    timestamp_ns: int
    source_sample_index: int | None
    x_m: float
    y_m: float
    z_m: float | None
    heading_rad: float | None
    speed_mps: float | None
    related_event_ids: Sequence[str]
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        for field_name in ("tape_id", "procedural_track_id", "waypoint_id"):
            object.__setattr__(
                self, field_name, validate_identifier(getattr(self, field_name))
            )
        object.__setattr__(
            self,
            "waypoint_index",
            _int32(self.waypoint_index, "waypoint_index"),
        )
        object.__setattr__(
            self,
            "waypoint_role",
            _enum(SemanticWaypointRole, self.waypoint_role, "waypoint_role"),
        )
        object.__setattr__(
            self, "timestamp_ns", _int64(self.timestamp_ns, "timestamp_ns")
        )
        if self.source_sample_index is not None:
            object.__setattr__(
                self,
                "source_sample_index",
                _int32(self.source_sample_index, "source_sample_index"),
            )
        for field_name in ("x_m", "y_m"):
            object.__setattr__(
                self, field_name, _finite(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "z_m", _optional_finite(self.z_m, "z_m"))
        heading = _optional_finite(self.heading_rad, "heading_rad")
        if heading is not None and not -math.pi <= heading < math.pi:
            raise ValidationError("heading_rad must be in the interval [-pi, pi)")
        object.__setattr__(self, "heading_rad", heading)
        speed = _optional_finite(self.speed_mps, "speed_mps")
        if speed is not None and speed < 0.0:
            raise ValidationError("speed_mps must not be negative")
        object.__setattr__(self, "speed_mps", speed)
        object.__setattr__(
            self,
            "related_event_ids",
            _identifier_sequence(self.related_event_ids, "related_event_ids"),
        )
        object.__setattr__(self, "origin_type", _inferred(self.origin_type))
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class MotionEvent:
    """One persistent typed motion interval tied to semantic waypoints."""

    tape_id: str
    procedural_track_id: str
    event_id: str
    event_index: int
    event_type: MotionEventType | str
    start_waypoint_id: str
    anchor_waypoint_id: str
    end_waypoint_id: str
    start_time_ns: int
    anchor_time_ns: int
    end_time_ns: int
    magnitude_value: float | None
    semantic_attributes_json: str
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        for field_name in (
            "tape_id",
            "procedural_track_id",
            "event_id",
            "start_waypoint_id",
            "anchor_waypoint_id",
            "end_waypoint_id",
        ):
            object.__setattr__(
                self, field_name, validate_identifier(getattr(self, field_name))
            )
        object.__setattr__(self, "event_index", _int32(self.event_index, "event_index"))
        object.__setattr__(
            self,
            "event_type",
            _enum(MotionEventType, self.event_type, "event_type"),
        )
        for field_name in ("start_time_ns", "anchor_time_ns", "end_time_ns"):
            object.__setattr__(
                self, field_name, _int64(getattr(self, field_name), field_name)
            )
        if not self.start_time_ns <= self.anchor_time_ns <= self.end_time_ns:
            raise ValidationError("event times must satisfy start <= anchor <= end")
        object.__setattr__(
            self,
            "magnitude_value",
            _optional_finite(self.magnitude_value, "magnitude_value"),
        )
        object.__setattr__(
            self,
            "semantic_attributes_json",
            _canonical_object_json(self.semantic_attributes_json),
        )
        object.__setattr__(self, "origin_type", _inferred(self.origin_type))
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class SemanticMotionTrack:
    """A procedural track with ordered source-derived semantic records."""

    procedural_track: ProceduralTrack
    waypoints: Sequence[SemanticWaypoint]
    events: Sequence[MotionEvent]
    leading_invalid_sample_count: int = 0
    trailing_invalid_sample_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.procedural_track, ProceduralTrack):
            raise ValidationError("procedural_track must be a ProceduralTrack")
        waypoints_value: object = self.waypoints
        events_value: object = self.events
        if isinstance(waypoints_value, (str, bytes)) or not isinstance(
            waypoints_value, Sequence
        ):
            raise ValidationError("waypoints must be a non-string sequence")
        if isinstance(events_value, (str, bytes)) or not isinstance(
            events_value, Sequence
        ):
            raise ValidationError("events must be a non-string sequence")
        waypoints = tuple(waypoints_value)
        events = tuple(events_value)
        if any(not isinstance(item, SemanticWaypoint) for item in waypoints):
            raise ValidationError("waypoints must contain SemanticWaypoint values")
        if any(not isinstance(item, MotionEvent) for item in events):
            raise ValidationError("events must contain MotionEvent values")
        object.__setattr__(self, "waypoints", waypoints)
        object.__setattr__(self, "events", events)
        for field_name in (
            "leading_invalid_sample_count",
            "trailing_invalid_sample_count",
        ):
            object.__setattr__(
                self, field_name, _int32(getattr(self, field_name), field_name)
            )
        validate_semantic_motion_track(self)


@dataclass(frozen=True, slots=True)
class SemanticMotionTape:
    """A procedural tape plus ordered semantic motion tracks and provenance."""

    procedural_tape: ProceduralTape
    tracks: Sequence[SemanticMotionTrack]
    detector_configuration_identity: str
    source_validation_identity: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.procedural_tape, ProceduralTape):
            raise ValidationError("procedural_tape must be a ProceduralTape")
        tracks_value: object = self.tracks
        if isinstance(tracks_value, (str, bytes)) or not isinstance(
            tracks_value, Sequence
        ):
            raise ValidationError("tracks must be a non-string sequence")
        tracks = tuple(tracks_value)
        if any(not isinstance(item, SemanticMotionTrack) for item in tracks):
            raise ValidationError("tracks must contain SemanticMotionTrack values")
        object.__setattr__(self, "tracks", tracks)
        object.__setattr__(
            self,
            "detector_configuration_identity",
            _text(
                self.detector_configuration_identity,
                "detector_configuration_identity",
            ),
        )
        if self.source_validation_identity is not None:
            object.__setattr__(
                self,
                "source_validation_identity",
                _text(
                    self.source_validation_identity,
                    "source_validation_identity",
                ),
            )
        validate_semantic_motion_tape(self)


def _waypoint_order(
    waypoint: SemanticWaypoint,
) -> tuple[int, int, int, str]:
    role_rank = {role: index for index, role in enumerate(SemanticWaypointRole)}
    return (
        waypoint.timestamp_ns,
        (
            waypoint.source_sample_index
            if waypoint.source_sample_index is not None
            else _INT32_MAX + 1
        ),
        role_rank[_role(waypoint.waypoint_role)],
        waypoint.waypoint_id,
    )


def _event_order(event: MotionEvent) -> tuple[int, int, int, str]:
    type_rank = {event_type: index for index, event_type in enumerate(MotionEventType)}
    return (
        event.start_time_ns,
        event.end_time_ns,
        type_rank[_event_type(event.event_type)],
        event.event_id,
    )


def validate_semantic_motion_track(track: SemanticMotionTrack) -> None:
    """Validate semantic ordering, references, intervals, and gap integrity."""
    procedural = track.procedural_track
    if not track.waypoints:
        raise ValidationError("semantic motion track requires waypoints")
    if tuple(item.waypoint_index for item in track.waypoints) != tuple(
        range(len(track.waypoints))
    ):
        raise ValidationError("waypoint indices must be contiguous and zero-based")
    if tuple(sorted(track.waypoints, key=_waypoint_order)) != track.waypoints:
        raise ValidationError("waypoints are not in canonical semantic order")
    if tuple(item.event_index for item in track.events) != tuple(
        range(len(track.events))
    ):
        raise ValidationError("event indices must be contiguous and zero-based")
    if tuple(sorted(track.events, key=_event_order)) != track.events:
        raise ValidationError("events are not in canonical semantic order")
    if len({item.waypoint_id for item in track.waypoints}) != len(track.waypoints):
        raise ValidationError("waypoint identifiers must be unique")
    if len({item.event_id for item in track.events}) != len(track.events):
        raise ValidationError("event identifiers must be unique")
    if any(
        item.tape_id != procedural.tape_id
        or item.procedural_track_id != procedural.procedural_track_id
        for item in (*track.waypoints, *track.events)
    ):
        raise ValidationError("semantic references do not match procedural track")
    if any(
        not procedural.start_time_ns <= item.timestamp_ns <= procedural.end_time_ns
        for item in track.waypoints
    ):
        raise ValidationError("waypoint timestamp lies outside track support")

    waypoints = {item.waypoint_id: item for item in track.waypoints}
    events = {item.event_id: item for item in track.events}
    for waypoint in track.waypoints:
        if any(event_id not in events for event_id in waypoint.related_event_ids):
            raise ValidationError("waypoint related_event_ids contains unknown event")
    for event in track.events:
        referenced = tuple(
            waypoints.get(identifier)
            for identifier in (
                event.start_waypoint_id,
                event.anchor_waypoint_id,
                event.end_waypoint_id,
            )
        )
        if any(item is None for item in referenced):
            raise ValidationError("event references an unknown waypoint")
        start, anchor, end = referenced
        if (
            start is None
            or anchor is None
            or end is None
            or start.timestamp_ns != event.start_time_ns
            or anchor.timestamp_ns != event.anchor_time_ns
            or end.timestamp_ns != event.end_time_ns
        ):
            raise ValidationError("event times do not match referenced waypoints")
        resolved = tuple(item for item in referenced if item is not None)
        if any(event.event_id not in item.related_event_ids for item in resolved):
            raise ValidationError("event and waypoint links must be symmetric")

    same_type: dict[MotionEventType, list[MotionEvent]] = {}
    for event in track.events:
        same_type.setdefault(event.event_type, []).append(event)
    for event_type, values in same_type.items():
        if event_type is MotionEventType.GAP:
            continue
        for previous, current in pairwise(values):
            if current.start_time_ns < previous.end_time_ns:
                raise ValidationError("same-type semantic events must not overlap")

    runs = tuple(
        tuple(segment for segment in procedural.segments if segment.run_index == index)
        for index in range(procedural.run_count)
    )
    run_intervals = tuple(
        (
            run[0].source_start_sample_index,
            run[-1].source_end_sample_index,
        )
        for run in runs
    )
    for event in track.events:
        start = waypoints[event.start_waypoint_id]
        end = waypoints[event.end_waypoint_id]
        if start.source_sample_index is None or end.source_sample_index is None:
            raise ValidationError("event waypoints require source sample indices")
        if event.event_type is MotionEventType.GAP:
            if (start.waypoint_role, end.waypoint_role) != (
                SemanticWaypointRole.GAP_START,
                SemanticWaypointRole.GAP_END,
            ):
                raise ValidationError("gap event requires gap_start and gap_end")
            if not any(
                start.source_sample_index == left[1]
                and end.source_sample_index == right[0]
                for left, right in pairwise(run_intervals)
            ):
                raise ValidationError("gap event does not join adjacent valid runs")
        elif not any(
            run_start <= start.source_sample_index <= end.source_sample_index <= run_end
            for run_start, run_end in run_intervals
        ):
            raise ValidationError("motion event crosses an invalid source gap")


def validate_semantic_motion_tape(tape: SemanticMotionTape) -> None:
    """Validate tape-level semantic track ownership and deterministic order."""
    if len(tape.tracks) != tape.procedural_tape.track_count:
        raise ValidationError("semantic track count differs from procedural tape")
    procedural_ids = tuple(
        track.procedural_track_id for track in tape.procedural_tape.tracks
    )
    semantic_ids = tuple(
        track.procedural_track.procedural_track_id for track in tape.tracks
    )
    if semantic_ids != procedural_ids:
        raise ValidationError("semantic tracks do not follow procedural tape order")
    if any(
        track.procedural_track != procedural
        for track, procedural in zip(
            tape.tracks,
            tape.procedural_tape.tracks,
            strict=True,
        )
    ):
        raise ValidationError("semantic tracks reference different procedural tracks")
    if (
        tape.source_validation_identity
        != tape.procedural_tape.source_validation_report_identity
    ):
        raise ValidationError("semantic source validation identity differs")


def semantic_waypoint_to_dict(waypoint: SemanticWaypoint) -> dict[str, object]:
    """Return schema-ordered persistent waypoint fields."""
    return {
        "tape_id": waypoint.tape_id,
        "procedural_track_id": waypoint.procedural_track_id,
        "waypoint_id": waypoint.waypoint_id,
        "waypoint_index": waypoint.waypoint_index,
        "waypoint_role": _role(waypoint.waypoint_role).value,
        "timestamp_ns": waypoint.timestamp_ns,
        "source_sample_index": waypoint.source_sample_index,
        "x_m": waypoint.x_m,
        "y_m": waypoint.y_m,
        "z_m": waypoint.z_m,
        "heading_rad": waypoint.heading_rad,
        "speed_mps": waypoint.speed_mps,
        "related_event_ids": list(waypoint.related_event_ids),
        "origin_type": _inferred(waypoint.origin_type).value,
        "quality_flags": list(waypoint.quality_flags),
    }


def motion_event_to_dict(event: MotionEvent) -> dict[str, object]:
    """Return schema-ordered persistent event fields."""
    return {
        "tape_id": event.tape_id,
        "procedural_track_id": event.procedural_track_id,
        "event_id": event.event_id,
        "event_index": event.event_index,
        "event_type": _event_type(event.event_type).value,
        "start_waypoint_id": event.start_waypoint_id,
        "anchor_waypoint_id": event.anchor_waypoint_id,
        "end_waypoint_id": event.end_waypoint_id,
        "start_time_ns": event.start_time_ns,
        "anchor_time_ns": event.anchor_time_ns,
        "end_time_ns": event.end_time_ns,
        "magnitude_value": event.magnitude_value,
        "semantic_attributes_json": event.semantic_attributes_json,
        "origin_type": _inferred(event.origin_type).value,
        "quality_flags": list(event.quality_flags),
    }


def semantic_waypoint_from_dict(value: Mapping[str, object]) -> SemanticWaypoint:
    """Reconstruct one validated waypoint from persistent values."""
    return SemanticWaypoint(**dict(value))  # type: ignore[arg-type]


def motion_event_from_dict(value: Mapping[str, object]) -> MotionEvent:
    """Reconstruct one validated event from persistent values."""
    return MotionEvent(**dict(value))  # type: ignore[arg-type]
