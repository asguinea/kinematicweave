"""Frozen semantic-event preservation metrics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math

from kinematicweave.domain.procedural import ProceduralTrack
from kinematicweave.domain.records import Trajectory
from kinematicweave.domain.semantic import MotionEventType
from kinematicweave.errors import ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    analyze_event_preservation,
    detect_semantic_trajectory,
)
from kinematicweave.metrics.motion import (
    METRICS_SCHEMA_VERSION,
    ErrorStatistics,
    descriptive_statistics,
)

_DEFAULT_CONFIG = SemanticMotionConfig()


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value


def _count(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{field_name} must be a nonnegative integer")
    return value


def _rate(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a finite rate")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValidationError(f"{field_name} must be in [0, 1]")
    return normalized


def _errors(value: object, field_name: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a sequence")
    return tuple(_count(item, f"{field_name} item") for item in value)


@dataclass(frozen=True, slots=True)
class TrajectoryEventMetrics:
    """One per-trajectory event-type preservation record."""

    method_id: str
    configuration_id: str
    scenario_id: str
    trajectory_id: str
    event_type: str
    source_event_count: int
    replay_event_count: int
    matched_event_count: int
    precision: float
    recall: float
    f1: float
    start_boundary_errors_ns: Sequence[int]
    end_boundary_errors_ns: Sequence[int]
    anchor_time_errors_ns: Sequence[int]
    unmatched_source_count: int
    unmatched_replay_count: int

    def __post_init__(self) -> None:
        for name in (
            "method_id",
            "configuration_id",
            "scenario_id",
            "trajectory_id",
            "event_type",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.event_type not in {item.value for item in MotionEventType}:
            raise ValidationError("event_type is not in the frozen vocabulary")
        for name in (
            "source_event_count",
            "replay_event_count",
            "matched_event_count",
            "unmatched_source_count",
            "unmatched_replay_count",
        ):
            object.__setattr__(self, name, _count(getattr(self, name), name))
        for name in ("precision", "recall", "f1"):
            object.__setattr__(self, name, _rate(getattr(self, name), name))
        for name in (
            "start_boundary_errors_ns",
            "end_boundary_errors_ns",
            "anchor_time_errors_ns",
        ):
            object.__setattr__(self, name, _errors(getattr(self, name), name))
        if self.matched_event_count > min(
            self.source_event_count, self.replay_event_count
        ):
            raise ValidationError("matched event count exceeds available events")
        if self.unmatched_source_count != (
            self.source_event_count - self.matched_event_count
        ):
            raise ValidationError("unmatched source count differs")
        if self.unmatched_replay_count != (
            self.replay_event_count - self.matched_event_count
        ):
            raise ValidationError("unmatched replay count differs")
        if any(
            len(values) != self.matched_event_count
            for values in (
                self.start_boundary_errors_ns,
                self.end_boundary_errors_ns,
                self.anchor_time_errors_ns,
            )
        ):
            raise ValidationError("event timing errors must match matched count")

    @staticmethod
    def _statistics(values: Sequence[int]) -> ErrorStatistics:
        return descriptive_statistics(tuple(float(value) for value in values))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": METRICS_SCHEMA_VERSION,
            "method_id": self.method_id,
            "configuration_id": self.configuration_id,
            "scenario_id": self.scenario_id,
            "trajectory_id": self.trajectory_id,
            "event_type": self.event_type,
            "source_event_count": self.source_event_count,
            "replay_event_count": self.replay_event_count,
            "matched_event_count": self.matched_event_count,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "start_boundary_errors_ns": list(self.start_boundary_errors_ns),
            "start_boundary_statistics_ns": self._statistics(
                self.start_boundary_errors_ns
            ).to_dict(),
            "end_boundary_errors_ns": list(self.end_boundary_errors_ns),
            "end_boundary_statistics_ns": self._statistics(
                self.end_boundary_errors_ns
            ).to_dict(),
            "anchor_time_errors_ns": list(self.anchor_time_errors_ns),
            "anchor_time_statistics_ns": self._statistics(
                self.anchor_time_errors_ns
            ).to_dict(),
            "unmatched_source_count": self.unmatched_source_count,
            "unmatched_replay_count": self.unmatched_replay_count,
        }


def _matched_errors(
    value: Mapping[str, object],
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    matches = value.get("matches")
    if isinstance(matches, (str, bytes)) or not isinstance(matches, Sequence):
        raise ValidationError("event matches are invalid")
    start: list[int] = []
    end: list[int] = []
    anchor: list[int] = []
    for item in matches:
        if not isinstance(item, Mapping):
            raise ValidationError("event match is invalid")
        start.append(_count(item.get("start_boundary_absolute_error_ns"), "start"))
        end.append(_count(item.get("end_boundary_absolute_error_ns"), "end"))
        anchor.append(_count(item.get("anchor_time_absolute_error_ns"), "anchor"))
    return tuple(start), tuple(end), tuple(anchor)


def evaluate_trajectory_events(
    method_id: str,
    configuration_id: str,
    source_trajectory: Trajectory,
    replay_trajectory: Trajectory,
    procedural_track: ProceduralTrack,
    config: SemanticMotionConfig = _DEFAULT_CONFIG,
) -> tuple[TrajectoryEventMetrics, ...]:
    """Run the frozen detector on source and replay and compare every event type."""
    source = detect_semantic_trajectory(source_trajectory, procedural_track, config)
    replay = detect_semantic_trajectory(replay_trajectory, procedural_track, config)
    result = analyze_event_preservation(source, replay)
    by_type = result.get("by_type")
    if not isinstance(by_type, Mapping):
        raise ValidationError("event preservation result lacks by_type records")
    rows: list[TrajectoryEventMetrics] = []
    for event_type in MotionEventType:
        value = by_type.get(event_type.value)
        if not isinstance(value, Mapping):
            raise ValidationError("event preservation result lacks an event type")
        start, end, anchor = _matched_errors(value)
        rows.append(
            TrajectoryEventMetrics(
                method_id=method_id,
                configuration_id=configuration_id,
                scenario_id=source_trajectory.scenario_id,
                trajectory_id=source_trajectory.trajectory_id,
                event_type=event_type.value,
                source_event_count=_count(
                    value.get("source_event_count"), "source_event_count"
                ),
                replay_event_count=_count(
                    value.get("replay_event_count"), "replay_event_count"
                ),
                matched_event_count=_count(value.get("matched_count"), "matched_count"),
                precision=_rate(value.get("precision"), "precision"),
                recall=_rate(value.get("recall"), "recall"),
                f1=_rate(value.get("f1"), "f1"),
                start_boundary_errors_ns=start,
                end_boundary_errors_ns=end,
                anchor_time_errors_ns=anchor,
                unmatched_source_count=_count(
                    value.get("unmatched_source_count"), "unmatched_source_count"
                ),
                unmatched_replay_count=_count(
                    value.get("unmatched_replay_count"), "unmatched_replay_count"
                ),
            )
        )
    return tuple(rows)
