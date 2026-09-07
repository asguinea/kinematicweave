"""Immutable procedural-line codec domain records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
import math

from kinematicweave.domain.records import OriginType
from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import validate_identifier

__all__ = [
    "ProceduralPrimitiveType",
    "ProceduralSegment",
    "ProceduralTape",
    "ProceduralTrack",
    "ReplayState",
    "procedural_segment_from_dict",
    "procedural_segment_to_dict",
    "procedural_tape_manifest_to_dict",
    "procedural_track_from_dict",
    "procedural_track_to_dict",
    "validate_procedural_tape",
]

_INT32_MAX = 2**31 - 1
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


class ProceduralPrimitiveType(StrEnum):
    """Primitive types supported by procedural trajectory codecs."""

    HOLD = "hold"
    LINEAR = "linear"
    CUBIC_HERMITE = "cubic_hermite"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{field_name} must be nonempty")
    return normalized


def _optional_text(value: object, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name)


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


def _heading(value: object, field_name: str) -> float | None:
    normalized = _optional_finite(value, field_name)
    if normalized is not None and not -math.pi <= normalized < math.pi:
        raise ValidationError(f"{field_name} must be in the interval [-pi, pi)")
    return normalized


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a bool")
    return value


def _flags(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("quality_flags must be a non-string sequence")
    normalized = tuple(_text(item, "quality_flags item") for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValidationError("quality_flags must not contain duplicates")
    return normalized


def _float_values(value: object) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("parameter_values must be a non-string sequence")
    return tuple(_finite(item, "parameter_values item") for item in value)


def _origin(value: object, expected: OriginType, field_name: str) -> OriginType:
    try:
        normalized = (
            value
            if isinstance(value, OriginType)
            else OriginType(_text(value, field_name))
        )
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} has invalid value {value!r}") from None
    if normalized is not expected:
        raise ValidationError(f"{field_name} must be {expected.value!r}")
    return normalized


def _primitive(value: object) -> ProceduralPrimitiveType:
    try:
        return (
            value
            if isinstance(value, ProceduralPrimitiveType)
            else ProceduralPrimitiveType(_text(value, "primitive_type"))
        )
    except (TypeError, ValueError):
        raise ValidationError(f"primitive_type has invalid value {value!r}") from None


@dataclass(frozen=True, slots=True)
class ReplayState:
    """One deterministic decoded state at an integer-nanosecond timestamp."""

    procedural_track_id: str
    timestamp_ns: int
    x_m: float
    y_m: float
    z_m: float | None
    heading_rad: float | None
    velocity_x_mps: float | None
    velocity_y_mps: float | None
    origin_type: OriginType | str = OriginType.DECODED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "procedural_track_id",
            validate_identifier(self.procedural_track_id),
        )
        object.__setattr__(
            self, "timestamp_ns", _int64(self.timestamp_ns, "timestamp_ns")
        )
        for field_name in ("x_m", "y_m"):
            object.__setattr__(
                self, field_name, _finite(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "z_m", _optional_finite(self.z_m, "z_m"))
        object.__setattr__(
            self, "heading_rad", _heading(self.heading_rad, "heading_rad")
        )
        for field_name in ("velocity_x_mps", "velocity_y_mps"):
            object.__setattr__(
                self,
                field_name,
                _optional_finite(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "origin_type",
            _origin(self.origin_type, OriginType.DECODED, "origin_type"),
        )


@dataclass(frozen=True, slots=True)
class ProceduralSegment:
    """One procedural motion primitive over a contiguous source interval."""

    tape_id: str
    procedural_track_id: str
    segment_id: str
    run_index: int
    segment_index: int
    primitive_type: ProceduralPrimitiveType | str
    source_start_sample_index: int
    source_end_sample_index: int
    start_time_ns: int
    end_time_ns: int
    start_x_m: float
    start_y_m: float
    start_z_m: float | None
    end_x_m: float
    end_y_m: float
    end_z_m: float | None
    start_heading_rad: float | None
    end_heading_rad: float | None
    start_velocity_x_mps: float | None
    start_velocity_y_mps: float | None
    end_velocity_x_mps: float | None
    end_velocity_y_mps: float | None
    parameter_values: Sequence[float]
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        for field_name in ("tape_id", "procedural_track_id", "segment_id"):
            object.__setattr__(
                self, field_name, validate_identifier(getattr(self, field_name))
            )
        for field_name in (
            "run_index",
            "segment_index",
            "source_start_sample_index",
            "source_end_sample_index",
        ):
            object.__setattr__(
                self, field_name, _int32(getattr(self, field_name), field_name)
            )
        if self.source_end_sample_index < self.source_start_sample_index:
            raise ValidationError("source sample interval is reversed")
        for field_name in ("start_time_ns", "end_time_ns"):
            object.__setattr__(
                self, field_name, _int64(getattr(self, field_name), field_name)
            )
        if self.end_time_ns < self.start_time_ns:
            raise ValidationError("segment time interval is reversed")
        for field_name in ("start_x_m", "start_y_m", "end_x_m", "end_y_m"):
            object.__setattr__(
                self, field_name, _finite(getattr(self, field_name), field_name)
            )
        for field_name in ("start_z_m", "end_z_m"):
            object.__setattr__(
                self,
                field_name,
                _optional_finite(getattr(self, field_name), field_name),
            )
        for field_name in ("start_heading_rad", "end_heading_rad"):
            object.__setattr__(
                self, field_name, _heading(getattr(self, field_name), field_name)
            )
        for field_name in (
            "start_velocity_x_mps",
            "start_velocity_y_mps",
            "end_velocity_x_mps",
            "end_velocity_y_mps",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_finite(getattr(self, field_name), field_name),
            )
        primitive = _primitive(self.primitive_type)
        object.__setattr__(self, "primitive_type", primitive)
        object.__setattr__(
            self, "parameter_values", _float_values(self.parameter_values)
        )
        if self.parameter_values:
            raise ValidationError("procedural segment parameter_values must be empty")
        object.__setattr__(
            self,
            "origin_type",
            _origin(self.origin_type, OriginType.INFERRED, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))

        if primitive is ProceduralPrimitiveType.HOLD:
            if (
                self.start_x_m,
                self.start_y_m,
                self.start_z_m,
            ) != (
                self.end_x_m,
                self.end_y_m,
                self.end_z_m,
            ):
                raise ValidationError("hold endpoint positions must be identical")
            if self.start_time_ns == self.end_time_ns:
                if self.source_start_sample_index != self.source_end_sample_index:
                    raise ValidationError(
                        "zero-duration hold must represent one source sample"
                    )
                if (
                    self.start_heading_rad,
                    self.start_velocity_x_mps,
                    self.start_velocity_y_mps,
                ) != (
                    self.end_heading_rad,
                    self.end_velocity_x_mps,
                    self.end_velocity_y_mps,
                ):
                    raise ValidationError(
                        "zero-duration hold endpoints must be identical"
                    )
            elif self.source_end_sample_index <= self.source_start_sample_index:
                raise ValidationError("positive-duration hold must span source samples")
        elif primitive is ProceduralPrimitiveType.LINEAR:
            if self.end_time_ns <= self.start_time_ns:
                raise ValidationError("linear segment duration must be positive")
            if self.source_end_sample_index <= self.source_start_sample_index:
                raise ValidationError("linear segment must span source samples")
        else:
            if self.end_time_ns <= self.start_time_ns:
                raise ValidationError("cubic Hermite duration must be positive")
            if self.source_end_sample_index <= self.source_start_sample_index:
                raise ValidationError("cubic Hermite segment must span source samples")
            if any(
                value is None
                for value in (
                    self.start_velocity_x_mps,
                    self.start_velocity_y_mps,
                    self.end_velocity_x_mps,
                    self.end_velocity_y_mps,
                )
            ):
                raise ValidationError(
                    "cubic Hermite endpoints require complete x/y velocity"
                )
            if (self.start_z_m is None) != (self.end_z_m is None):
                raise ValidationError(
                    "cubic Hermite segment cannot span mixed elevation availability"
                )


@dataclass(frozen=True, slots=True)
class ProceduralTrack:
    """One encoded trajectory and its contiguous valid source runs."""

    tape_id: str
    scenario_id: str
    procedural_track_id: str
    agent_id: str
    trajectory_id: str
    agent_class: str
    start_time_ns: int
    end_time_ns: int
    source_sample_count: int
    valid_sample_count: int
    run_count: int
    segment_count: int
    has_elevation: bool
    origin_type: OriginType | str
    quality_flags: Sequence[str]
    segments: Sequence[ProceduralSegment]

    def __post_init__(self) -> None:
        for field_name in (
            "tape_id",
            "scenario_id",
            "procedural_track_id",
            "agent_id",
            "trajectory_id",
        ):
            object.__setattr__(
                self, field_name, validate_identifier(getattr(self, field_name))
            )
        object.__setattr__(self, "agent_class", _text(self.agent_class, "agent_class"))
        for field_name in ("start_time_ns", "end_time_ns"):
            object.__setattr__(
                self, field_name, _int64(getattr(self, field_name), field_name)
            )
        if self.end_time_ns < self.start_time_ns:
            raise ValidationError("track time interval is reversed")
        for field_name in (
            "source_sample_count",
            "valid_sample_count",
            "run_count",
            "segment_count",
        ):
            object.__setattr__(
                self, field_name, _int32(getattr(self, field_name), field_name)
            )
        if self.source_sample_count == 0 or self.valid_sample_count == 0:
            raise ValidationError("procedural track requires source and valid samples")
        object.__setattr__(
            self, "has_elevation", _bool(self.has_elevation, "has_elevation")
        )
        object.__setattr__(
            self,
            "origin_type",
            _origin(self.origin_type, OriginType.INFERRED, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))
        segments_value: object = self.segments
        if isinstance(segments_value, (str, bytes)) or not isinstance(
            segments_value, Sequence
        ):
            raise ValidationError("segments must be a non-string sequence")
        segments = tuple(segments_value)
        if any(not isinstance(item, ProceduralSegment) for item in segments):
            raise ValidationError("segments must contain ProceduralSegment values")
        object.__setattr__(self, "segments", segments)
        _validate_track_segments(self)


def _validate_track_segments(track: ProceduralTrack) -> None:
    segments = track.segments
    if len(segments) != track.segment_count or not segments:
        raise ValidationError("segment_count does not match track segments")
    if any(
        segment.tape_id != track.tape_id
        or segment.procedural_track_id != track.procedural_track_id
        for segment in segments
    ):
        raise ValidationError("segment references do not match track")
    expected_order = tuple(
        sorted(segments, key=lambda item: (item.run_index, item.segment_index))
    )
    if segments != expected_order:
        raise ValidationError("segments must be in canonical run and segment order")
    run_indices = tuple(dict.fromkeys(segment.run_index for segment in segments))
    if run_indices != tuple(range(track.run_count)):
        raise ValidationError("run indices must be contiguous and start at zero")

    represented_indices: set[int] = set()
    previous_run_end: ProceduralSegment | None = None
    for run_index in run_indices:
        run = tuple(segment for segment in segments if segment.run_index == run_index)
        if tuple(segment.segment_index for segment in run) != tuple(range(len(run))):
            raise ValidationError(
                "segment indices must be contiguous and start at zero per run"
            )
        for previous, current in pairwise(run):
            shared_boundary = (
                previous.source_end_sample_index != current.source_start_sample_index
                or previous.end_time_ns != current.start_time_ns
            )
            elevation_boundary = (
                previous.source_end_sample_index + 1
                == current.source_start_sample_index
                and previous.end_time_ns < current.start_time_ns
                and (previous.end_z_m is None) != (current.start_z_m is None)
            )
            if shared_boundary and not elevation_boundary:
                raise ValidationError("segments within a run must be contiguous")
        if previous_run_end is not None:
            if (
                run[0].source_start_sample_index
                <= previous_run_end.source_end_sample_index + 1
            ):
                raise ValidationError(
                    "separate runs must preserve an invalid-sample gap"
                )
            if run[0].start_time_ns <= previous_run_end.end_time_ns:
                raise ValidationError("separate runs must be strictly time ordered")
        previous_run_end = run[-1]
        for segment in run:
            represented_indices.update(
                range(
                    segment.source_start_sample_index,
                    segment.source_end_sample_index + 1,
                )
            )

    if len(represented_indices) != track.valid_sample_count:
        raise ValidationError("valid_sample_count does not match segment coverage")
    if max(represented_indices) >= track.source_sample_count:
        raise ValidationError("segment source index exceeds source_sample_count")
    if track.start_time_ns != segments[0].start_time_ns:
        raise ValidationError("track start_time_ns does not match segments")
    if track.end_time_ns != segments[-1].end_time_ns:
        raise ValidationError("track end_time_ns does not match segments")
    segment_has_elevation = any(
        segment.start_z_m is not None or segment.end_z_m is not None
        for segment in segments
    )
    if track.has_elevation != segment_has_elevation:
        raise ValidationError("track has_elevation does not match segments")


@dataclass(frozen=True, slots=True)
class ProceduralTape:
    """Scenario-level exact procedural tape and persistent manifest fields."""

    tape_id: str
    scenario_id: str
    coordinate_frame_id: str
    source_dataset_id: str
    source_dataset_version: str
    source_validation_report_identity: str | None
    encoder_name: str
    encoder_version: str
    encoder_parameters_identity: str
    start_time_ns: int
    end_time_ns: int
    track_count: int
    segment_count: int
    source_sample_count: int
    encoded_valid_sample_count: int
    has_elevation: bool
    origin_type: OriginType | str
    quality_flags: Sequence[str]
    tracks: Sequence[ProceduralTrack]

    def __post_init__(self) -> None:
        for field_name in ("tape_id", "scenario_id", "coordinate_frame_id"):
            object.__setattr__(
                self, field_name, validate_identifier(getattr(self, field_name))
            )
        for field_name in (
            "source_dataset_id",
            "source_dataset_version",
            "encoder_name",
            "encoder_version",
            "encoder_parameters_identity",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "source_validation_report_identity",
            _optional_text(
                self.source_validation_report_identity,
                "source_validation_report_identity",
            ),
        )
        for field_name in ("start_time_ns", "end_time_ns"):
            object.__setattr__(
                self, field_name, _int64(getattr(self, field_name), field_name)
            )
        if self.end_time_ns < self.start_time_ns:
            raise ValidationError("tape time interval is reversed")
        for field_name in ("track_count", "segment_count"):
            object.__setattr__(
                self, field_name, _int32(getattr(self, field_name), field_name)
            )
        for field_name in ("source_sample_count", "encoded_valid_sample_count"):
            object.__setattr__(
                self,
                field_name,
                _integer(
                    getattr(self, field_name),
                    field_name,
                    minimum=0,
                    maximum=_INT64_MAX,
                ),
            )
        object.__setattr__(
            self, "has_elevation", _bool(self.has_elevation, "has_elevation")
        )
        object.__setattr__(
            self,
            "origin_type",
            _origin(self.origin_type, OriginType.INFERRED, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))
        tracks_value: object = self.tracks
        if isinstance(tracks_value, (str, bytes)) or not isinstance(
            tracks_value, Sequence
        ):
            raise ValidationError("tracks must be a non-string sequence")
        tracks = tuple(tracks_value)
        if any(not isinstance(item, ProceduralTrack) for item in tracks):
            raise ValidationError("tracks must contain ProceduralTrack values")
        object.__setattr__(self, "tracks", tracks)
        validate_procedural_tape(self)


def validate_procedural_tape(tape: ProceduralTape) -> None:
    """Validate scenario-level track references, support, and aggregate counts."""
    if not isinstance(tape, ProceduralTape):
        raise ValidationError("tape must be a ProceduralTape")
    if len(tape.tracks) != tape.track_count or not tape.tracks:
        raise ValidationError("track_count does not match tape tracks")
    if tape.tracks != tuple(
        sorted(tape.tracks, key=lambda item: (item.scenario_id, item.tape_id))
    ):
        raise ValidationError("tracks must be in canonical schema order")
    if len({track.procedural_track_id for track in tape.tracks}) != tape.track_count:
        raise ValidationError("procedural track identifiers must be unique")
    if any(
        track.tape_id != tape.tape_id or track.scenario_id != tape.scenario_id
        for track in tape.tracks
    ):
        raise ValidationError("track references do not match tape")
    if tape.segment_count != sum(track.segment_count for track in tape.tracks):
        raise ValidationError("tape segment_count does not match tracks")
    if tape.source_sample_count != sum(
        track.source_sample_count for track in tape.tracks
    ):
        raise ValidationError("tape source_sample_count does not match tracks")
    if tape.encoded_valid_sample_count != sum(
        track.valid_sample_count for track in tape.tracks
    ):
        raise ValidationError("encoded_valid_sample_count does not match tracks")
    if tape.start_time_ns != min(track.start_time_ns for track in tape.tracks):
        raise ValidationError("tape start_time_ns does not match tracks")
    if tape.end_time_ns != max(track.end_time_ns for track in tape.tracks):
        raise ValidationError("tape end_time_ns does not match tracks")
    if tape.has_elevation != any(track.has_elevation for track in tape.tracks):
        raise ValidationError("tape has_elevation does not match tracks")


def procedural_tape_manifest_to_dict(tape: ProceduralTape) -> dict[str, object]:
    """Return the schema-ordered persistent manifest fields."""
    return {
        "tape_id": tape.tape_id,
        "scenario_id": tape.scenario_id,
        "coordinate_frame_id": tape.coordinate_frame_id,
        "source_dataset_id": tape.source_dataset_id,
        "source_dataset_version": tape.source_dataset_version,
        "source_validation_report_identity": tape.source_validation_report_identity,
        "encoder_name": tape.encoder_name,
        "encoder_version": tape.encoder_version,
        "encoder_parameters_identity": tape.encoder_parameters_identity,
        "start_time_ns": tape.start_time_ns,
        "end_time_ns": tape.end_time_ns,
        "track_count": tape.track_count,
        "segment_count": tape.segment_count,
        "source_sample_count": tape.source_sample_count,
        "encoded_valid_sample_count": tape.encoded_valid_sample_count,
        "has_elevation": tape.has_elevation,
        "origin_type": _origin(
            tape.origin_type, OriginType.INFERRED, "origin_type"
        ).value,
        "quality_flags": list(tape.quality_flags),
    }


def procedural_track_to_dict(track: ProceduralTrack) -> dict[str, object]:
    """Return the schema-ordered persistent track fields."""
    return {
        "tape_id": track.tape_id,
        "scenario_id": track.scenario_id,
        "procedural_track_id": track.procedural_track_id,
        "agent_id": track.agent_id,
        "trajectory_id": track.trajectory_id,
        "agent_class": track.agent_class,
        "start_time_ns": track.start_time_ns,
        "end_time_ns": track.end_time_ns,
        "source_sample_count": track.source_sample_count,
        "valid_sample_count": track.valid_sample_count,
        "run_count": track.run_count,
        "segment_count": track.segment_count,
        "has_elevation": track.has_elevation,
        "origin_type": _origin(
            track.origin_type, OriginType.INFERRED, "origin_type"
        ).value,
        "quality_flags": list(track.quality_flags),
    }


def procedural_segment_to_dict(segment: ProceduralSegment) -> dict[str, object]:
    """Return the schema-ordered persistent segment fields."""
    return {
        "tape_id": segment.tape_id,
        "procedural_track_id": segment.procedural_track_id,
        "segment_id": segment.segment_id,
        "run_index": segment.run_index,
        "segment_index": segment.segment_index,
        "primitive_type": _primitive(segment.primitive_type).value,
        "source_start_sample_index": segment.source_start_sample_index,
        "source_end_sample_index": segment.source_end_sample_index,
        "start_time_ns": segment.start_time_ns,
        "end_time_ns": segment.end_time_ns,
        "start_x_m": segment.start_x_m,
        "start_y_m": segment.start_y_m,
        "start_z_m": segment.start_z_m,
        "end_x_m": segment.end_x_m,
        "end_y_m": segment.end_y_m,
        "end_z_m": segment.end_z_m,
        "start_heading_rad": segment.start_heading_rad,
        "end_heading_rad": segment.end_heading_rad,
        "start_velocity_x_mps": segment.start_velocity_x_mps,
        "start_velocity_y_mps": segment.start_velocity_y_mps,
        "end_velocity_x_mps": segment.end_velocity_x_mps,
        "end_velocity_y_mps": segment.end_velocity_y_mps,
        "parameter_values": list(segment.parameter_values),
        "origin_type": _origin(
            segment.origin_type, OriginType.INFERRED, "origin_type"
        ).value,
        "quality_flags": list(segment.quality_flags),
    }


def procedural_track_from_dict(
    value: Mapping[str, object],
    segments: Sequence[ProceduralSegment],
) -> ProceduralTrack:
    """Reconstruct a validated track from persistent plain values."""
    return ProceduralTrack(**dict(value), segments=segments)  # type: ignore[arg-type]


def procedural_segment_from_dict(
    value: Mapping[str, object],
) -> ProceduralSegment:
    """Reconstruct a validated segment from persistent plain values."""
    return ProceduralSegment(**dict(value))  # type: ignore[arg-type]
