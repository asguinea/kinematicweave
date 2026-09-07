"""Frozen Phase 4 motion metrics and deterministic result models."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import cast

from kinematicweave.baselines.motion import (
    baseline_keyframe_count,
    evaluate_raw_trajectory,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.codecs.exact import replay_track
from kinematicweave.domain.procedural import (
    ProceduralPrimitiveType,
    ProceduralTrack,
    ReplayState,
)
from kinematicweave.domain.records import OriginType, Trajectory, TrajectorySampleRecord
from kinematicweave.errors import ValidationError

METRICS_SCHEMA_VERSION = "1.0"
REPLAY_HASH_DOMAIN = "phase4-motion-metrics-replay-v1"
QUANTILE_POLICY = (
    "sort finite values ascending; position=(n-1)*q; linearly interpolate "
    "adjacent values; null when n=0"
)
StateEvaluator = Callable[[int], ReplayState | None]


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value


def _count(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{field_name} must be a nonnegative integer")
    return value


def _number(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a finite nonnegative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValidationError(f"{field_name} must be a finite nonnegative number")
    return normalized


def _numbers(value: object, field_name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a finite sequence")
    return tuple(_number(item, f"{field_name} item") for item in value)


def deterministic_quantile(values: Sequence[float], q: float) -> float | None:
    """Return the frozen linearly interpolated quantile of finite values."""
    quantile = _number(q, "q")
    if quantile > 1.0:
        raise ValidationError("q must not exceed one")
    ordered = sorted(_numbers(values, "values"))
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


@dataclass(frozen=True, slots=True)
class ErrorStatistics:
    """Deterministic descriptive statistics for one finite error vector."""

    count: int
    mean: float | None
    median: float | None
    p95: float | None
    maximum: float | None
    sum: float
    sum_of_squares: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "count", _count(self.count, "count"))
        object.__setattr__(self, "sum", _number(self.sum, "sum"))
        object.__setattr__(
            self, "sum_of_squares", _number(self.sum_of_squares, "sum_of_squares")
        )
        for name in ("mean", "median", "p95", "maximum"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _number(value, name))
        summaries = (self.mean, self.median, self.p95, self.maximum)
        if self.count == 0:
            if any(value is not None for value in summaries):
                raise ValidationError("empty statistics require null summaries")
            if self.sum or self.sum_of_squares:
                raise ValidationError("empty statistics require zero sums")
        elif any(value is None for value in summaries):
            raise ValidationError("nonempty statistics require all summaries")

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "mean": self.mean,
            "median": self.median,
            "p95": self.p95,
            "maximum": self.maximum,
            "sum": self.sum,
            "sum_of_squares": self.sum_of_squares,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ErrorStatistics:
        expected = {
            "count",
            "mean",
            "median",
            "p95",
            "maximum",
            "sum",
            "sum_of_squares",
        }
        if set(value) != expected:
            raise ValidationError("statistics fields differ")
        return cls(**dict(value))  # type: ignore[arg-type]


def descriptive_statistics(values: Sequence[float]) -> ErrorStatistics:
    """Compute the frozen statistics contract using stable finite summation."""
    normalized = _numbers(values, "values")
    if not normalized:
        return ErrorStatistics(0, None, None, None, None, 0.0, 0.0)
    total = math.fsum(normalized)
    return ErrorStatistics(
        len(normalized),
        total / len(normalized),
        deterministic_quantile(normalized, 0.5),
        deterministic_quantile(normalized, 0.95),
        max(normalized),
        total,
        math.fsum(value * value for value in normalized),
    )


def trajectory_macro_statistics(
    error_vectors: Sequence[Sequence[float]],
) -> ErrorStatistics:
    """Summarize equally weighted per-trajectory error-vector means."""
    means = tuple(
        math.fsum(values) / len(values)
        for vector in error_vectors
        if (values := _numbers(vector, "trajectory error vector"))
    )
    return descriptive_statistics(means)


@dataclass(frozen=True, slots=True)
class TrajectoryMotionMetrics:
    """Complete per-trajectory motion, gap, endpoint, and complexity result."""

    method_id: str
    configuration_id: str
    scenario_id: str
    trajectory_id: str
    replay_hash: str
    source_sample_count: int
    valid_sample_count: int
    valid_run_count: int
    retained_keyframe_count: int
    segment_count: int
    hold_count: int
    linear_count: int
    hermite_count: int
    position_errors_m: Sequence[float]
    heading_errors_rad: Sequence[float]
    heading_missing_count: int
    velocity_errors_mps: Sequence[float]
    velocity_missing_count: int
    endpoint_position_errors_m: Sequence[float]
    endpoint_heading_errors_rad: Sequence[float]
    endpoint_velocity_errors_mps: Sequence[float]
    invalid_source_timestamp_count: int
    midpoint_probe_count: int
    unexpected_replay_state_count: int
    gap_preservation_passed: bool

    def __post_init__(self) -> None:
        for name in (
            "method_id",
            "configuration_id",
            "scenario_id",
            "trajectory_id",
            "replay_hash",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in (
            "source_sample_count",
            "valid_sample_count",
            "valid_run_count",
            "retained_keyframe_count",
            "segment_count",
            "hold_count",
            "linear_count",
            "hermite_count",
            "heading_missing_count",
            "velocity_missing_count",
            "invalid_source_timestamp_count",
            "midpoint_probe_count",
            "unexpected_replay_state_count",
        ):
            object.__setattr__(self, name, _count(getattr(self, name), name))
        for name in (
            "position_errors_m",
            "heading_errors_rad",
            "velocity_errors_mps",
            "endpoint_position_errors_m",
            "endpoint_heading_errors_rad",
            "endpoint_velocity_errors_mps",
        ):
            object.__setattr__(self, name, _numbers(getattr(self, name), name))
        if not isinstance(self.gap_preservation_passed, bool):
            raise ValidationError("gap_preservation_passed must be a Boolean")
        if len(self.position_errors_m) != self.valid_sample_count:
            raise ValidationError("position error count differs from valid samples")
        if len(self.heading_errors_rad) + self.heading_missing_count != (
            self.valid_sample_count
        ):
            raise ValidationError("heading compared and missing counts differ")
        if len(self.velocity_errors_mps) + self.velocity_missing_count != (
            self.valid_sample_count
        ):
            raise ValidationError("velocity compared and missing counts differ")
        if (
            self.segment_count
            != self.hold_count + self.linear_count + self.hermite_count
        ):
            raise ValidationError("primitive counts do not sum to segments")
        if self.gap_preservation_passed != (self.unexpected_replay_state_count == 0):
            raise ValidationError("gap pass differs from probe outcomes")

    @property
    def identity(self) -> str:
        return canonical_sha256("phase4-motion-metric-record-v1", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": METRICS_SCHEMA_VERSION,
            "method_id": self.method_id,
            "configuration_id": self.configuration_id,
            "scenario_id": self.scenario_id,
            "trajectory_id": self.trajectory_id,
            "replay_hash": self.replay_hash,
            "source_sample_count": self.source_sample_count,
            "valid_sample_count": self.valid_sample_count,
            "valid_run_count": self.valid_run_count,
            "retained_keyframe_count": self.retained_keyframe_count,
            "segment_count": self.segment_count,
            "hold_count": self.hold_count,
            "linear_count": self.linear_count,
            "hermite_count": self.hermite_count,
            "position_errors_m": list(self.position_errors_m),
            "position_statistics": descriptive_statistics(
                self.position_errors_m
            ).to_dict(),
            "heading_errors_rad": list(self.heading_errors_rad),
            "heading_statistics": descriptive_statistics(
                self.heading_errors_rad
            ).to_dict(),
            "heading_compared_count": len(self.heading_errors_rad),
            "heading_missing_count": self.heading_missing_count,
            "velocity_errors_mps": list(self.velocity_errors_mps),
            "velocity_statistics": descriptive_statistics(
                self.velocity_errors_mps
            ).to_dict(),
            "velocity_compared_count": len(self.velocity_errors_mps),
            "velocity_missing_count": self.velocity_missing_count,
            "endpoint_position_errors_m": list(self.endpoint_position_errors_m),
            "endpoint_position_maximum_m": max(
                self.endpoint_position_errors_m, default=None
            ),
            "endpoint_heading_errors_rad": list(self.endpoint_heading_errors_rad),
            "endpoint_heading_maximum_rad": max(
                self.endpoint_heading_errors_rad, default=None
            ),
            "endpoint_velocity_errors_mps": list(self.endpoint_velocity_errors_mps),
            "endpoint_velocity_maximum_mps": max(
                self.endpoint_velocity_errors_mps, default=None
            ),
            "invalid_source_timestamp_count": self.invalid_source_timestamp_count,
            "midpoint_probe_count": self.midpoint_probe_count,
            "unexpected_replay_state_count": self.unexpected_replay_state_count,
            "gap_preservation_passed": self.gap_preservation_passed,
        }

    def to_canonical_json(self) -> str:
        return canonical_json_text(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> TrajectoryMotionMetrics:
        derived = {
            "schema_version",
            "position_statistics",
            "heading_statistics",
            "heading_compared_count",
            "velocity_statistics",
            "velocity_compared_count",
            "endpoint_position_maximum_m",
            "endpoint_heading_maximum_rad",
            "endpoint_velocity_maximum_mps",
        }
        fields = set(cls.__dataclass_fields__)
        if set(value) != fields | derived:
            raise ValidationError("trajectory metric fields differ")
        if value["schema_version"] != METRICS_SCHEMA_VERSION:
            raise ValidationError("unsupported trajectory metric schema")
        parsed = cls(**{name: value[name] for name in fields})  # type: ignore[arg-type]
        if parsed.to_dict() != dict(value):
            raise ValidationError("derived trajectory metrics differ")
        return parsed


@dataclass(frozen=True, slots=True)
class TrajectoryMotionEvaluation:
    """One metric record plus the replay trajectory used for semantics."""

    metrics: TrajectoryMotionMetrics
    replay_trajectory: Trajectory


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


def _position_error(sample: TrajectorySampleRecord, state: ReplayState) -> float:
    squared = (sample.x_m - state.x_m) ** 2 + (sample.y_m - state.y_m) ** 2
    if sample.z_m is not None:
        if state.z_m is None:
            raise ValidationError("replay omitted source elevation")
        squared += (sample.z_m - state.z_m) ** 2
    return _number(math.sqrt(squared), "position error")


def position_error_m(sample: TrajectorySampleRecord, state: ReplayState) -> float:
    """Return the frozen source-aware 2D or 3D Euclidean position error."""
    return _position_error(sample, state)


def _heading_error(sample: TrajectorySampleRecord, state: ReplayState) -> float | None:
    if sample.heading_rad is None or state.heading_rad is None:
        return None
    value = (state.heading_rad - sample.heading_rad + math.pi) % (
        2.0 * math.pi
    ) - math.pi
    return _number(abs(value), "heading error")


def heading_error_rad(
    sample: TrajectorySampleRecord, state: ReplayState
) -> float | None:
    """Return absolute shortest wrapped heading error when both values exist."""
    return _heading_error(sample, state)


def _velocity_error(sample: TrajectorySampleRecord, state: ReplayState) -> float | None:
    values = (
        sample.velocity_x_mps,
        sample.velocity_y_mps,
        state.velocity_x_mps,
        state.velocity_y_mps,
    )
    if any(value is None for value in values):
        return None
    source_x, source_y, replay_x, replay_y = values
    assert source_x is not None and source_y is not None
    assert replay_x is not None and replay_y is not None
    return _number(
        math.hypot(replay_x - source_x, replay_y - source_y), "velocity error"
    )


def velocity_error_mps(
    sample: TrajectorySampleRecord, state: ReplayState
) -> float | None:
    """Return planar velocity-vector error when both vectors are complete."""
    return _velocity_error(sample, state)


def _state_dict(timestamp_ns: int, state: ReplayState | None) -> dict[str, object]:
    origin = None
    if state is not None:
        origin = (
            state.origin_type.value
            if isinstance(state.origin_type, OriginType)
            else state.origin_type
        )
    return {
        "timestamp_ns": timestamp_ns,
        "state_present": state is not None,
        "x_m": None if state is None else state.x_m,
        "y_m": None if state is None else state.y_m,
        "z_m": None if state is None else state.z_m,
        "heading_rad": None if state is None else state.heading_rad,
        "velocity_x_mps": None if state is None else state.velocity_x_mps,
        "velocity_y_mps": None if state is None else state.velocity_y_mps,
        "decoded_origin": origin,
    }


def _replay_sample(
    source: TrajectorySampleRecord,
    state: ReplayState | None,
    *,
    raw: bool,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id=source.scenario_id,
        agent_id=source.agent_id,
        trajectory_id=source.trajectory_id,
        sample_index=source.sample_index,
        timestamp_ns=source.timestamp_ns,
        x_m=source.x_m if state is None else state.x_m,
        y_m=source.y_m if state is None else state.y_m,
        z_m=source.z_m if state is None else state.z_m,
        heading_rad=source.heading_rad if state is None else state.heading_rad,
        velocity_x_mps=(
            source.velocity_x_mps if state is None else state.velocity_x_mps
        ),
        velocity_y_mps=(
            source.velocity_y_mps if state is None else state.velocity_y_mps
        ),
        speed_mps=source.speed_mps if raw else None,
        acceleration_x_mps2=source.acceleration_x_mps2 if raw else None,
        acceleration_y_mps2=source.acceleration_y_mps2 if raw else None,
        is_observed=source.is_observed,
        is_valid=source.is_valid,
        origin_type=OriginType.DECODED,
        quality_flags=source.quality_flags,
    )


def _primitive_counts(track: ProceduralTrack | None) -> tuple[int, int, int]:
    hold = linear = hermite = 0
    for segment in () if track is None else track.segments:
        primitive = ProceduralPrimitiveType(segment.primitive_type)
        if primitive is ProceduralPrimitiveType.HOLD:
            hold += 1
        elif primitive is ProceduralPrimitiveType.LINEAR:
            linear += 1
        else:
            hermite += 1
    return hold, linear, hermite


def evaluate_trajectory_motion(
    method_id: str,
    configuration_id: str,
    trajectory: Trajectory,
    track: ProceduralTrack | None,
) -> TrajectoryMotionEvaluation:
    """Evaluate one method at source timestamps and deterministic gap probes."""
    _text(method_id, "method_id")
    _text(configuration_id, "configuration_id")
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if track is not None and (
        track.scenario_id,
        track.agent_id,
        track.trajectory_id,
    ) != (
        trajectory.scenario_id,
        trajectory.agent_id,
        trajectory.trajectory_id,
    ):
        raise ValidationError("track and trajectory identities differ")
    evaluator: StateEvaluator = (
        (lambda timestamp: evaluate_raw_trajectory(trajectory, timestamp))
        if track is None
        else (lambda timestamp: replay_track(track, timestamp))
    )
    runs = _valid_runs(trajectory.samples)
    endpoint_indices = [
        sample.sample_index for run in runs for sample in (run[0], run[-1])
    ]
    position: list[float] = []
    heading: list[float] = []
    velocity: list[float] = []
    endpoint_position: list[float] = []
    endpoint_heading: list[float] = []
    endpoint_velocity: list[float] = []
    replay_samples: list[TrajectorySampleRecord] = []
    hash_rows: list[dict[str, object]] = []
    heading_missing = velocity_missing = unexpected = 0
    for source in trajectory.samples:
        state = evaluator(source.timestamp_ns)
        hash_rows.append(
            {
                "probe_type": "source_timestamp",
                "sample_index": source.sample_index,
                **_state_dict(source.timestamp_ns, state),
            }
        )
        if not source.is_valid:
            unexpected += state is not None
            replay_samples.append(_replay_sample(source, None, raw=track is None))
            continue
        if state is None:
            raise ValidationError("valid source timestamp has no replay state")
        position_error = _position_error(source, state)
        heading_error = _heading_error(source, state)
        velocity_error = _velocity_error(source, state)
        position.append(position_error)
        if heading_error is None:
            heading_missing += 1
        else:
            heading.append(heading_error)
        if velocity_error is None:
            velocity_missing += 1
        else:
            velocity.append(velocity_error)
        for _ in range(endpoint_indices.count(source.sample_index)):
            endpoint_position.append(position_error)
            if heading_error is not None:
                endpoint_heading.append(heading_error)
            if velocity_error is not None:
                endpoint_velocity.append(velocity_error)
        replay_samples.append(_replay_sample(source, state, raw=track is None))

    midpoint_count = 0
    for gap_index in range(len(runs) - 1):
        left = runs[gap_index][-1].timestamp_ns
        right = runs[gap_index + 1][0].timestamp_ns
        midpoint = (left + right) // 2
        if left < midpoint < right:
            midpoint_count += 1
            state = evaluator(midpoint)
            unexpected += state is not None
            hash_rows.append(
                {
                    "probe_type": "gap_midpoint",
                    "gap_index": gap_index,
                    **_state_dict(midpoint, state),
                }
            )
    hash_rows.sort(
        key=lambda row: (
            cast(int, row["timestamp_ns"]),
            str(row["probe_type"]),
            cast(int, row.get("sample_index", row.get("gap_index", -1))),
        )
    )
    hold, linear, hermite = _primitive_counts(track)
    valid_count = sum(sample.is_valid for sample in trajectory.samples)
    metrics = TrajectoryMotionMetrics(
        method_id=method_id,
        configuration_id=configuration_id,
        scenario_id=trajectory.scenario_id,
        trajectory_id=trajectory.trajectory_id,
        replay_hash=canonical_sha256(
            REPLAY_HASH_DOMAIN,
            {
                "method_id": method_id,
                "configuration_id": configuration_id,
                "scenario_id": trajectory.scenario_id,
                "trajectory_id": trajectory.trajectory_id,
                "probes": hash_rows,
            },
        ),
        source_sample_count=trajectory.sample_count,
        valid_sample_count=valid_count,
        valid_run_count=len(runs),
        retained_keyframe_count=(
            valid_count if track is None else baseline_keyframe_count(track)
        ),
        segment_count=0 if track is None else track.segment_count,
        hold_count=hold,
        linear_count=linear,
        hermite_count=hermite,
        position_errors_m=position,
        heading_errors_rad=heading,
        heading_missing_count=heading_missing,
        velocity_errors_mps=velocity,
        velocity_missing_count=velocity_missing,
        endpoint_position_errors_m=endpoint_position,
        endpoint_heading_errors_rad=endpoint_heading,
        endpoint_velocity_errors_mps=endpoint_velocity,
        invalid_source_timestamp_count=sum(
            not sample.is_valid for sample in trajectory.samples
        ),
        midpoint_probe_count=midpoint_count,
        unexpected_replay_state_count=unexpected,
        gap_preservation_passed=unexpected == 0,
    )
    return TrajectoryMotionEvaluation(
        metrics,
        Trajectory(
            scenario_id=trajectory.scenario_id,
            agent_id=trajectory.agent_id,
            trajectory_id=trajectory.trajectory_id,
            samples=tuple(replay_samples),
            origin_type=OriginType.DECODED,
            quality_flags=trajectory.quality_flags,
        ),
    )


@dataclass(frozen=True, slots=True)
class ArtifactFileMetric:
    """Physical metadata for one accepted representation artifact."""

    name: str
    size_bytes: int
    sha256: str
    row_count: int | None
    row_group_count: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "size_bytes", _count(self.size_bytes, "size_bytes"))
        object.__setattr__(self, "sha256", _text(self.sha256, "sha256"))
        for name in ("row_count", "row_group_count"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _count(value, name))

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "row_group_count": self.row_group_count,
        }


@dataclass(frozen=True, slots=True)
class ScenarioArtifactRuntimeMetrics:
    """Scenario-level physical artifact and measured stage metrics."""

    method_id: str
    configuration_id: str
    scenario_id: str
    status: str
    artifact_files: Sequence[ArtifactFileMetric]
    serialized_representation_bytes: int
    summary_manifest_bytes: int
    artifact_bundle_bytes: int
    output_disk_bytes: int
    encoding_seconds: float
    artifact_writing_seconds: float
    artifact_verification_seconds: float
    replay_evaluation_seconds: float
    semantic_redetection_seconds: float
    total_seconds: float
    peak_process_rss_bytes: int

    def __post_init__(self) -> None:
        for name in ("method_id", "configuration_id", "scenario_id", "status"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        files = tuple(self.artifact_files)
        if any(not isinstance(item, ArtifactFileMetric) for item in files):
            raise ValidationError("artifact_files contain invalid values")
        object.__setattr__(self, "artifact_files", files)
        for name in (
            "serialized_representation_bytes",
            "summary_manifest_bytes",
            "artifact_bundle_bytes",
            "output_disk_bytes",
            "peak_process_rss_bytes",
        ):
            object.__setattr__(self, name, _count(getattr(self, name), name))
        for name in (
            "encoding_seconds",
            "artifact_writing_seconds",
            "artifact_verification_seconds",
            "replay_evaluation_seconds",
            "semantic_redetection_seconds",
            "total_seconds",
        ):
            object.__setattr__(self, name, _number(getattr(self, name), name))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": METRICS_SCHEMA_VERSION,
            "method_id": self.method_id,
            "configuration_id": self.configuration_id,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "artifact_files": [item.to_dict() for item in self.artifact_files],
            "serialized_representation_bytes": self.serialized_representation_bytes,
            "summary_manifest_bytes": self.summary_manifest_bytes,
            "artifact_bundle_bytes": self.artifact_bundle_bytes,
            "output_disk_bytes": self.output_disk_bytes,
            "encoding_seconds": self.encoding_seconds,
            "artifact_writing_seconds": self.artifact_writing_seconds,
            "artifact_verification_seconds": self.artifact_verification_seconds,
            "replay_evaluation_seconds": self.replay_evaluation_seconds,
            "semantic_redetection_seconds": self.semantic_redetection_seconds,
            "total_seconds": self.total_seconds,
            "peak_process_rss_bytes": self.peak_process_rss_bytes,
        }


@dataclass(frozen=True, slots=True)
class EvaluationFailure:
    """Explicit per-stage method failure without silent trajectory removal."""

    method_id: str
    configuration_id: str
    scenario_id: str
    trajectory_id: str | None
    failure_stage: str
    error_type: str
    error_message: str

    def __post_init__(self) -> None:
        for name in (
            "method_id",
            "configuration_id",
            "scenario_id",
            "failure_stage",
            "error_type",
            "error_message",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.trajectory_id is not None:
            object.__setattr__(
                self, "trajectory_id", _text(self.trajectory_id, "trajectory_id")
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": METRICS_SCHEMA_VERSION,
            "method_id": self.method_id,
            "configuration_id": self.configuration_id,
            "scenario_id": self.scenario_id,
            "trajectory_id": self.trajectory_id,
            "failure_stage": self.failure_stage,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class MethodConfigurationResult:
    """Deterministic aggregate identity for one method configuration."""

    method_id: str
    configuration_id: str
    trajectory_count: int
    failure_count: int
    pooled_position_statistics: ErrorStatistics
    pooled_heading_statistics: ErrorStatistics
    pooled_velocity_statistics: ErrorStatistics

    def __post_init__(self) -> None:
        object.__setattr__(self, "method_id", _text(self.method_id, "method_id"))
        object.__setattr__(
            self, "configuration_id", _text(self.configuration_id, "configuration_id")
        )
        object.__setattr__(
            self, "trajectory_count", _count(self.trajectory_count, "trajectory_count")
        )
        object.__setattr__(
            self, "failure_count", _count(self.failure_count, "failure_count")
        )
        for name in (
            "pooled_position_statistics",
            "pooled_heading_statistics",
            "pooled_velocity_statistics",
        ):
            if not isinstance(getattr(self, name), ErrorStatistics):
                raise ValidationError(f"{name} must be ErrorStatistics")

    def to_dict(self) -> dict[str, object]:
        return {
            "method_id": self.method_id,
            "configuration_id": self.configuration_id,
            "trajectory_count": self.trajectory_count,
            "failure_count": self.failure_count,
            "pooled_position_statistics": self.pooled_position_statistics.to_dict(),
            "pooled_heading_statistics": self.pooled_heading_statistics.to_dict(),
            "pooled_velocity_statistics": self.pooled_velocity_statistics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MetricsCampaignManifest:
    """Frozen deterministic campaign identity and membership."""

    batch: str
    cohort_identity: str
    development_validation_identity: str
    included_trajectory_identity: str
    scenario_ids: Sequence[str]
    method_configuration_ids: Sequence[str]
    detector_configuration_identity: str
    pilot_or_test_outcomes_accessed: bool

    def __post_init__(self) -> None:
        for name in (
            "batch",
            "cohort_identity",
            "development_validation_identity",
            "included_trajectory_identity",
            "detector_configuration_identity",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("scenario_ids", "method_configuration_ids"):
            values = tuple(_text(item, f"{name} item") for item in getattr(self, name))
            if len(values) != len(set(values)):
                raise ValidationError(f"{name} must be unique")
            object.__setattr__(self, name, values)
        if not isinstance(self.pilot_or_test_outcomes_accessed, bool):
            raise ValidationError("pilot_or_test_outcomes_accessed must be Boolean")

    @property
    def identity(self) -> str:
        return canonical_sha256("phase4-motion-metrics-campaign", self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": METRICS_SCHEMA_VERSION,
            "batch": self.batch,
            "cohort_identity": self.cohort_identity,
            "development_validation_identity": self.development_validation_identity,
            "included_trajectory_identity": self.included_trajectory_identity,
            "scenario_ids": list(self.scenario_ids),
            "method_configuration_ids": list(self.method_configuration_ids),
            "detector_configuration_identity": self.detector_configuration_identity,
            "pilot_or_test_outcomes_accessed": self.pilot_or_test_outcomes_accessed,
        }
