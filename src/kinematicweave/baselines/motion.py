"""Fair deterministic motion baselines over canonical trajectories."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.codecs.exact import replay_track
from kinematicweave.data.parquet_io import iter_canonical_parquet_batches
from kinematicweave.data.schemas import CanonicalSchemaName
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
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import make_identifier

__all__ = [
    "BASELINE_IMPLEMENTATION_VERSION",
    "BaselineMethod",
    "BaselineReplayValidation",
    "CanonicalScenarioBundle",
    "MotionBaselineConfig",
    "baseline_config_from_json",
    "baseline_config_identity",
    "baseline_config_to_canonical_json",
    "baseline_keyframe_count",
    "baseline_source_keyframes",
    "encode_scenario_baseline",
    "encode_trajectory_baseline",
    "evaluate_raw_trajectory",
    "included_motion_trajectories",
    "read_canonical_scenario_bundle",
    "required_baseline_grid",
    "validate_baseline_replay",
]

BASELINE_IMPLEMENTATION_VERSION = "1.0"
_SCHEMA_VERSION = "1.0"
_INT64_MAX = 2**63 - 1
_MINIMUM_VALID_SAMPLES = 10
_MINIMUM_VALID_DURATION_NS = 1_000_000_000
_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "method",
        "stride",
        "maximum_perpendicular_error_m",
        "interval_ns",
    }
)


class BaselineMethod(StrEnum):
    """Approved Batch 4.2 baseline method families."""

    RAW_SAMPLES = "raw_samples"
    UNIFORM_LINEAR = "uniform_linear"
    UNIFORM_HERMITE = "uniform_hermite"
    RDP_LINEAR = "rdp_linear"
    FIXED_INTERVAL_LINEAR = "fixed_interval_linear"


def _method(value: object) -> BaselineMethod:
    try:
        return (
            value
            if isinstance(value, BaselineMethod)
            else BaselineMethod(cast(str, value))
        )
    except (TypeError, ValueError):
        raise ValidationError(f"invalid baseline method {value!r}") from None


def _positive_int(value: object, field_name: str, *, maximum: int = _INT64_MAX) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if not 0 < value <= maximum:
        raise ValidationError(f"{field_name} must be in the range [1, {maximum}]")
    return value


def _nonnegative_float(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValidationError(f"{field_name} must be finite and nonnegative")
    return normalized


@dataclass(frozen=True, slots=True)
class MotionBaselineConfig:
    """Strict immutable configuration for one comparable baseline."""

    method: BaselineMethod | str
    stride: int | None = None
    maximum_perpendicular_error_m: float | None = None
    interval_ns: int | None = None
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        method = _method(self.method)
        object.__setattr__(self, "method", method)
        expected = {
            BaselineMethod.RAW_SAMPLES: (False, False, False),
            BaselineMethod.UNIFORM_LINEAR: (True, False, False),
            BaselineMethod.UNIFORM_HERMITE: (True, False, False),
            BaselineMethod.RDP_LINEAR: (False, True, False),
            BaselineMethod.FIXED_INTERVAL_LINEAR: (False, False, True),
        }[method]
        present = (
            self.stride is not None,
            self.maximum_perpendicular_error_m is not None,
            self.interval_ns is not None,
        )
        if present != expected:
            raise ValidationError(
                f"{method.value} received fields outside its exact configuration"
            )
        if self.stride is not None:
            object.__setattr__(
                self, "stride", _positive_int(self.stride, "stride", maximum=2**31 - 1)
            )
        if self.maximum_perpendicular_error_m is not None:
            object.__setattr__(
                self,
                "maximum_perpendicular_error_m",
                _nonnegative_float(
                    self.maximum_perpendicular_error_m,
                    "maximum_perpendicular_error_m",
                ),
            )
        if self.interval_ns is not None:
            object.__setattr__(
                self,
                "interval_ns",
                _positive_int(self.interval_ns, "interval_ns"),
            )

    @property
    def key(self) -> str:
        """Return a stable filesystem-safe method/configuration key."""
        method = _method(self.method)
        if self.stride is not None:
            return f"{method.value}-stride-{self.stride}"
        if self.maximum_perpendicular_error_m is not None:
            value = format(self.maximum_perpendicular_error_m, ".12g").replace(".", "p")
            return f"{method.value}-error-{value}"
        if self.interval_ns is not None:
            return f"{method.value}-interval-{self.interval_ns}"
        return method.value

    @property
    def is_encoded(self) -> bool:
        """Return whether this configuration produces procedural artifacts."""
        return self.method is not BaselineMethod.RAW_SAMPLES


def _config_value(config: MotionBaselineConfig) -> dict[str, object]:
    return {
        "schema_version": config.schema_version,
        "method": _method(config.method).value,
        "stride": config.stride,
        "maximum_perpendicular_error_m": config.maximum_perpendicular_error_m,
        "interval_ns": config.interval_ns,
    }


def baseline_config_identity(config: MotionBaselineConfig) -> str:
    """Return the canonical identity of all baseline behavior."""
    if not isinstance(config, MotionBaselineConfig):
        raise ValidationError("config must be a MotionBaselineConfig")
    return canonical_sha256(
        "phase4-motion-baseline-configuration",
        {
            "implementation_version": BASELINE_IMPLEMENTATION_VERSION,
            "configuration": _config_value(config),
            "gap_policy": "invalid_samples_terminate_runs_no_bridging",
            "elevation_policy": "segments_never_cross_z_availability_changes",
            "heading_policy": "shortest_wrapped_linear_interpolation",
            "optional_policy": "interpolate_only_when_both_endpoints_are_present",
            "rdp_tie_break": "earliest_source_sample_index",
            "fixed_interval_policy": (
                "first_source_timestamp_at_or_after_each_elapsed_boundary"
            ),
        },
    )


def baseline_config_to_canonical_json(config: MotionBaselineConfig) -> str:
    """Serialize one baseline configuration as strict canonical JSON."""
    return canonical_json_text(_config_value(config))


def baseline_config_from_json(text: str) -> MotionBaselineConfig:
    """Strictly deserialize one canonical baseline configuration."""
    if not isinstance(text, str):
        raise SchemaError("baseline configuration JSON must be text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError("baseline configuration JSON is malformed") from None
    if not isinstance(value, Mapping) or set(value) != _CONFIG_FIELDS:
        raise SchemaError("baseline configuration fields differ from the contract")
    try:
        config = MotionBaselineConfig(
            schema_version=cast(Any, value["schema_version"]),
            method=cast(Any, value["method"]),
            stride=cast(Any, value["stride"]),
            maximum_perpendicular_error_m=cast(
                Any, value["maximum_perpendicular_error_m"]
            ),
            interval_ns=cast(Any, value["interval_ns"]),
        )
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None
    if text != baseline_config_to_canonical_json(config):
        raise SchemaError("baseline configuration JSON is not canonical")
    return config


def required_baseline_grid() -> tuple[MotionBaselineConfig, ...]:
    """Return the exact immutable Batch 4.2 development grid."""
    return (
        MotionBaselineConfig(BaselineMethod.RAW_SAMPLES),
        *(
            MotionBaselineConfig(BaselineMethod.UNIFORM_LINEAR, stride=stride)
            for stride in (2, 5, 10)
        ),
        *(
            MotionBaselineConfig(BaselineMethod.UNIFORM_HERMITE, stride=stride)
            for stride in (2, 5, 10)
        ),
        *(
            MotionBaselineConfig(
                BaselineMethod.RDP_LINEAR,
                maximum_perpendicular_error_m=error,
            )
            for error in (0.05, 0.10, 0.25, 0.50)
        ),
        *(
            MotionBaselineConfig(
                BaselineMethod.FIXED_INTERVAL_LINEAR,
                interval_ns=interval,
            )
            for interval in (200_000_000, 500_000_000, 1_000_000_000)
        ),
    )


def _merged_flags(*values: Sequence[str]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for flags in values:
        for value in flags:
            normalized = value.strip()
            if normalized and normalized not in seen:
                merged.append(normalized)
                seen.add(normalized)
    return tuple(merged)


def _identifier(entity_type: str, domain: str, value: object) -> str:
    return make_identifier(entity_type, canonical_sha256(domain, value))


def _sample_identity(sample: TrajectorySampleRecord) -> dict[str, object]:
    return trajectory_sample_record_to_dict(sample)


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


def _z_chunks(
    run: Sequence[TrajectorySampleRecord],
) -> tuple[tuple[TrajectorySampleRecord, ...], ...]:
    chunks: list[tuple[TrajectorySampleRecord, ...]] = []
    current: list[TrajectorySampleRecord] = []
    availability: bool | None = None
    for sample in run:
        present = sample.z_m is not None
        if current and present != availability:
            chunks.append(tuple(current))
            current = []
        current.append(sample)
        availability = present
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _uniform_keyframes(
    run: Sequence[TrajectorySampleRecord],
    stride: int,
) -> tuple[TrajectorySampleRecord, ...]:
    indices = list(range(0, len(run), stride))
    if indices[-1] != len(run) - 1:
        indices.append(len(run) - 1)
    return tuple(run[index] for index in indices)


def _fixed_interval_keyframes(
    run: Sequence[TrajectorySampleRecord],
    interval_ns: int,
) -> tuple[TrajectorySampleRecord, ...]:
    selected = [run[0]]
    boundary = run[0].timestamp_ns + interval_ns
    cursor = 1
    while boundary < run[-1].timestamp_ns:
        while cursor < len(run) and run[cursor].timestamp_ns < boundary:
            cursor += 1
        if cursor >= len(run) - 1:
            break
        if selected[-1] is not run[cursor]:
            selected.append(run[cursor])
        boundary += interval_ns
    if selected[-1] is not run[-1]:
        selected.append(run[-1])
    return tuple(selected)


def _point(sample: TrajectorySampleRecord) -> tuple[float, ...]:
    return (
        (sample.x_m, sample.y_m)
        if sample.z_m is None
        else (sample.x_m, sample.y_m, sample.z_m)
    )


def _perpendicular_distance(
    point: Sequence[float],
    start: Sequence[float],
    end: Sequence[float],
) -> float:
    delta = tuple(b - a for a, b in zip(start, end, strict=True))
    relative = tuple(value - a for value, a in zip(point, start, strict=True))
    squared_length = sum(value * value for value in delta)
    if squared_length == 0.0:
        return math.sqrt(sum(value * value for value in relative))
    alpha = max(
        0.0,
        min(
            1.0,
            sum(a * b for a, b in zip(relative, delta, strict=True)) / squared_length,
        ),
    )
    return math.sqrt(
        sum(
            (value - (a + alpha * direction)) ** 2
            for value, a, direction in zip(point, start, delta, strict=True)
        )
    )


def _rdp_keyframes(
    chunk: Sequence[TrajectorySampleRecord],
    tolerance_m: float,
) -> tuple[TrajectorySampleRecord, ...]:
    if len(chunk) <= 2:
        return tuple(chunk)
    retained = {0, len(chunk) - 1}
    pending = [(0, len(chunk) - 1)]
    while pending:
        start_index, end_index = pending.pop()
        start = _point(chunk[start_index])
        end = _point(chunk[end_index])
        maximum = -1.0
        maximum_index: int | None = None
        for index in range(start_index + 1, end_index):
            distance = _perpendicular_distance(_point(chunk[index]), start, end)
            if distance > maximum:
                maximum = distance
                maximum_index = index
        if maximum_index is not None and maximum > tolerance_m:
            retained.add(maximum_index)
            pending.append((maximum_index, end_index))
            pending.append((start_index, maximum_index))
    return tuple(chunk[index] for index in sorted(retained))


def _split_selected_by_z(
    run: Sequence[TrajectorySampleRecord],
    selected: Sequence[TrajectorySampleRecord],
) -> tuple[tuple[TrajectorySampleRecord, ...], ...]:
    selected_indices = {sample.sample_index for sample in selected}
    for previous, current in pairwise(run):
        if (previous.z_m is None) != (current.z_m is None):
            selected_indices.add(previous.sample_index)
            selected_indices.add(current.sample_index)
    ordered = tuple(sample for sample in run if sample.sample_index in selected_indices)
    return _z_chunks(ordered)


def _selected_chunks(
    run: Sequence[TrajectorySampleRecord],
    config: MotionBaselineConfig,
) -> tuple[tuple[TrajectorySampleRecord, ...], ...]:
    if config.method in (
        BaselineMethod.UNIFORM_LINEAR,
        BaselineMethod.UNIFORM_HERMITE,
    ):
        if config.stride is None:
            raise ValidationError("uniform baseline stride is unavailable")
        return _split_selected_by_z(run, _uniform_keyframes(run, config.stride))
    if config.method is BaselineMethod.FIXED_INTERVAL_LINEAR:
        if config.interval_ns is None:
            raise ValidationError("fixed-interval baseline interval is unavailable")
        return _split_selected_by_z(
            run,
            _fixed_interval_keyframes(run, config.interval_ns),
        )
    if config.method is BaselineMethod.RDP_LINEAR:
        if config.maximum_perpendicular_error_m is None:
            raise ValidationError("RDP tolerance is unavailable")
        return tuple(
            _rdp_keyframes(chunk, config.maximum_perpendicular_error_m)
            for chunk in _z_chunks(run)
        )
    raise ValidationError("raw_samples does not produce procedural keyframes")


def _primitive(
    config: MotionBaselineConfig,
    start: TrajectorySampleRecord,
    end: TrajectorySampleRecord,
) -> ProceduralPrimitiveType:
    if start is end:
        return ProceduralPrimitiveType.HOLD
    if config.method is BaselineMethod.UNIFORM_HERMITE and all(
        value is not None
        for value in (
            start.velocity_x_mps,
            start.velocity_y_mps,
            end.velocity_x_mps,
            end.velocity_y_mps,
        )
    ):
        return ProceduralPrimitiveType.CUBIC_HERMITE
    return ProceduralPrimitiveType.LINEAR


def _segment(
    *,
    tape_id: str,
    track_id: str,
    config: MotionBaselineConfig,
    run_index: int,
    segment_index: int,
    start: TrajectorySampleRecord,
    end: TrajectorySampleRecord,
    quality_flags: Sequence[str],
) -> ProceduralSegment:
    primitive = _primitive(config, start, end)
    return ProceduralSegment(
        tape_id=tape_id,
        procedural_track_id=track_id,
        segment_id=_identifier(
            "procedural-segment",
            "phase4-motion-baseline-segment",
            {
                "configuration_identity": baseline_config_identity(config),
                "tape_id": tape_id,
                "track_id": track_id,
                "run_index": run_index,
                "segment_index": segment_index,
                "primitive": primitive.value,
                "source_start_sample_index": start.sample_index,
                "source_end_sample_index": end.sample_index,
            },
        ),
        run_index=run_index,
        segment_index=segment_index,
        primitive_type=primitive,
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


def _agent_class(value: AgentClass | str) -> AgentClass:
    try:
        return value if isinstance(value, AgentClass) else AgentClass(value)
    except (TypeError, ValueError):
        raise ValidationError(f"invalid agent class {value!r}") from None


def encode_trajectory_baseline(
    trajectory: Trajectory,
    agent_class: AgentClass | str,
    config: MotionBaselineConfig,
    *,
    tape_id: str | None = None,
) -> ProceduralTrack:
    """Encode one trajectory into the established procedural domain."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(config, MotionBaselineConfig) or not config.is_encoded:
        raise ValidationError("config must select an encoded baseline")
    normalized_class = _agent_class(agent_class)
    runs = _valid_runs(trajectory.samples)
    if not runs:
        raise ValidationError("trajectory has no valid samples to encode")
    source_value = {
        "scenario_id": trajectory.scenario_id,
        "agent_id": trajectory.agent_id,
        "trajectory_id": trajectory.trajectory_id,
        "configuration_identity": baseline_config_identity(config),
        "samples": [_sample_identity(sample) for sample in trajectory.samples],
    }
    track_id = _identifier(
        "procedural-track",
        "phase4-motion-baseline-track",
        source_value,
    )
    normalized_tape_id = tape_id or _identifier(
        "tape",
        "phase4-motion-baseline-standalone-tape",
        source_value,
    )
    segments: list[ProceduralSegment] = []
    for run_index, run in enumerate(runs):
        segment_index = 0
        for selected in _selected_chunks(run, config):
            pairs = (
                ((selected[0], selected[0]),)
                if len(selected) == 1
                else tuple(pairwise(selected))
            )
            for start, end in pairs:
                segments.append(
                    _segment(
                        tape_id=normalized_tape_id,
                        track_id=track_id,
                        config=config,
                        run_index=run_index,
                        segment_index=segment_index,
                        start=start,
                        end=end,
                        quality_flags=_merged_flags(
                            trajectory.quality_flags,
                            start.quality_flags,
                            end.quality_flags,
                        ),
                    )
                )
                segment_index += 1
    valid = tuple(sample for run in runs for sample in run)
    return ProceduralTrack(
        tape_id=normalized_tape_id,
        scenario_id=trajectory.scenario_id,
        procedural_track_id=track_id,
        agent_id=trajectory.agent_id,
        trajectory_id=trajectory.trajectory_id,
        agent_class=normalized_class.value,
        start_time_ns=valid[0].timestamp_ns,
        end_time_ns=valid[-1].timestamp_ns,
        source_sample_count=trajectory.sample_count,
        valid_sample_count=len(valid),
        run_count=len(runs),
        segment_count=len(segments),
        has_elevation=any(sample.z_m is not None for sample in valid),
        origin_type=OriginType.INFERRED,
        quality_flags=_merged_flags(
            trajectory.quality_flags,
            *(sample.quality_flags for sample in valid),
        ),
        segments=segments,
    )


def encode_scenario_baseline(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
    config: MotionBaselineConfig,
    *,
    source_validation_report_identity: str | None,
) -> ProceduralTape:
    """Encode one validated scenario using one approved baseline."""
    if not isinstance(config, MotionBaselineConfig) or not config.is_encoded:
        raise ValidationError("config must select an encoded baseline")
    validate_scenario_bundle(scenario, coordinate_frame, agents, trajectories)
    ordered = tuple(
        sorted(trajectories, key=lambda item: (item.scenario_id, item.trajectory_id))
    )
    agents_by_id = {agent.agent_id: agent for agent in agents}
    parameter_identity = baseline_config_identity(config)
    tape_id = _identifier(
        "tape",
        "phase4-motion-baseline-scenario-tape",
        {
            "scenario_id": scenario.scenario_id,
            "coordinate_frame_id": coordinate_frame.coordinate_frame_id,
            "source_dataset_id": scenario.dataset_id,
            "source_dataset_version": scenario.dataset_version,
            "source_validation_report_identity": source_validation_report_identity,
            "configuration_identity": parameter_identity,
            "trajectories": [
                {
                    "trajectory_id": trajectory.trajectory_id,
                    "samples": [
                        _sample_identity(sample) for sample in trajectory.samples
                    ],
                }
                for trajectory in ordered
            ],
        },
    )
    tracks = tuple(
        encode_trajectory_baseline(
            trajectory,
            agents_by_id[trajectory.agent_id].agent_class,
            config,
            tape_id=tape_id,
        )
        for trajectory in ordered
    )
    return ProceduralTape(
        tape_id=tape_id,
        scenario_id=scenario.scenario_id,
        coordinate_frame_id=coordinate_frame.coordinate_frame_id,
        source_dataset_id=scenario.dataset_id,
        source_dataset_version=scenario.dataset_version,
        source_validation_report_identity=source_validation_report_identity,
        encoder_name=f"phase4.{_method(config.method).value}",
        encoder_version=BASELINE_IMPLEMENTATION_VERSION,
        encoder_parameters_identity=parameter_identity,
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


def baseline_keyframe_count(track: ProceduralTrack) -> int:
    """Count unique retained source keyframes across independent valid runs."""
    if not isinstance(track, ProceduralTrack):
        raise ValidationError("track must be a ProceduralTrack")
    return sum(
        len(
            {
                index
                for segment in track.segments
                if segment.run_index == run_index
                for index in (
                    segment.source_start_sample_index,
                    segment.source_end_sample_index,
                )
            }
        )
        for run_index in range(track.run_count)
    )


def baseline_source_keyframes(
    trajectory: Trajectory,
    config: MotionBaselineConfig,
) -> tuple[tuple[int, ...], ...]:
    """Return retained source-sample indices in valid-run order."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(config, MotionBaselineConfig):
        raise ValidationError("config must be a MotionBaselineConfig")
    runs = _valid_runs(trajectory.samples)
    if config.method is BaselineMethod.RAW_SAMPLES:
        return tuple(tuple(sample.sample_index for sample in run) for run in runs)
    return tuple(
        tuple(
            sorted(
                {
                    sample.sample_index
                    for chunk in _selected_chunks(run, config)
                    for sample in chunk
                }
            )
        )
        for run in runs
    )


def _raw_track_id(trajectory: Trajectory) -> str:
    return _identifier(
        "procedural-track",
        "phase4-raw-sample-track",
        {
            "trajectory_id": trajectory.trajectory_id,
            "samples": [_sample_identity(sample) for sample in trajectory.samples],
        },
    )


def evaluate_raw_trajectory(
    trajectory: Trajectory,
    timestamp_ns: int,
) -> ReplayState | None:
    """Replay raw samples exactly and only at valid stored timestamps."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(timestamp_ns, int) or isinstance(timestamp_ns, bool):
        raise ValidationError("timestamp_ns must be a non-Boolean integer")
    for sample in trajectory.samples:
        if sample.timestamp_ns == timestamp_ns:
            if not sample.is_valid:
                return None
            return ReplayState(
                procedural_track_id=_raw_track_id(trajectory),
                timestamp_ns=sample.timestamp_ns,
                x_m=sample.x_m,
                y_m=sample.y_m,
                z_m=sample.z_m,
                heading_rad=sample.heading_rad,
                velocity_x_mps=sample.velocity_x_mps,
                velocity_y_mps=sample.velocity_y_mps,
                origin_type=OriginType.DECODED,
            )
        if sample.timestamp_ns > timestamp_ns:
            break
    return None


@dataclass(frozen=True, slots=True)
class BaselineReplayValidation:
    """Per-trajectory source-timestamp replay diagnostics."""

    source_sample_count: int
    valid_sample_count: int
    valid_run_count: int
    retained_keyframe_count: int
    position_errors_m: tuple[float, ...]
    heading_errors_rad: tuple[float, ...]
    velocity_errors_mps: tuple[float, ...]
    exact_endpoint_error_m: float
    gap_preservation_failures: int

    def __post_init__(self) -> None:
        for field_name in (
            "source_sample_count",
            "valid_sample_count",
            "valid_run_count",
            "retained_keyframe_count",
            "gap_preservation_failures",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError(f"{field_name} must be a nonnegative integer")
        object.__setattr__(
            self,
            "exact_endpoint_error_m",
            _nonnegative_float(
                self.exact_endpoint_error_m,
                "exact_endpoint_error_m",
            ),
        )
        for field_name in (
            "position_errors_m",
            "heading_errors_rad",
            "velocity_errors_mps",
        ):
            values = tuple(
                _nonnegative_float(value, f"{field_name} item")
                for value in getattr(self, field_name)
            )
            object.__setattr__(self, field_name, values)


def _wrap_heading(value: float) -> float:
    wrapped = (value + math.pi) % (2.0 * math.pi) - math.pi
    return -math.pi if wrapped == math.pi else wrapped


def _position_error(sample: TrajectorySampleRecord, state: ReplayState) -> float:
    if (sample.z_m is None) != (state.z_m is None):
        raise ValidationError("replay elevation availability differs")
    squared = (sample.x_m - state.x_m) ** 2 + (sample.y_m - state.y_m) ** 2
    if sample.z_m is not None:
        if state.z_m is None:
            raise ValidationError("replay omitted source elevation")
        squared += (sample.z_m - state.z_m) ** 2
    return math.sqrt(squared)


def validate_baseline_replay(
    trajectory: Trajectory,
    *,
    track: ProceduralTrack | None = None,
    config: MotionBaselineConfig,
) -> BaselineReplayValidation:
    """Measure replay at every original timestamp and enforce gap preservation."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(config, MotionBaselineConfig):
        raise ValidationError("config must be a MotionBaselineConfig")
    if config.is_encoded:
        if not isinstance(track, ProceduralTrack):
            raise ValidationError("encoded baseline validation requires a track")
        if (
            track.scenario_id,
            track.agent_id,
            track.trajectory_id,
        ) != (
            trajectory.scenario_id,
            trajectory.agent_id,
            trajectory.trajectory_id,
        ):
            raise ValidationError("track and trajectory identities differ")
    elif track is not None:
        raise ValidationError("raw baseline validation does not accept a track")

    runs = _valid_runs(trajectory.samples)
    endpoint_indices = {
        sample.sample_index for run in runs for sample in (run[0], run[-1])
    }
    position: list[float] = []
    heading: list[float] = []
    velocity: list[float] = []
    endpoint_error = 0.0
    gap_failures = 0
    for sample in trajectory.samples:
        state = (
            replay_track(track, sample.timestamp_ns)
            if track is not None
            else evaluate_raw_trajectory(trajectory, sample.timestamp_ns)
        )
        if not sample.is_valid:
            gap_failures += state is not None
            continue
        if state is None:
            raise ValidationError("valid source timestamp has no replay state")
        error = _position_error(sample, state)
        position.append(error)
        if sample.sample_index in endpoint_indices:
            endpoint_error = max(endpoint_error, error)
        if sample.heading_rad is not None and state.heading_rad is not None:
            heading.append(abs(_wrap_heading(state.heading_rad - sample.heading_rad)))
        if (
            sample.velocity_x_mps is not None
            and sample.velocity_y_mps is not None
            and state.velocity_x_mps is not None
            and state.velocity_y_mps is not None
        ):
            velocity.append(
                math.hypot(
                    state.velocity_x_mps - sample.velocity_x_mps,
                    state.velocity_y_mps - sample.velocity_y_mps,
                )
            )
    if gap_failures:
        raise ValidationError("baseline replay crosses an invalid source gap")
    if endpoint_error != 0.0:
        raise ValidationError("valid run endpoint replay is not exact")
    retained = (
        sum(len(run) for run in runs)
        if track is None
        else baseline_keyframe_count(track)
    )
    return BaselineReplayValidation(
        source_sample_count=trajectory.sample_count,
        valid_sample_count=sum(sample.is_valid for sample in trajectory.samples),
        valid_run_count=len(runs),
        retained_keyframe_count=retained,
        position_errors_m=tuple(position),
        heading_errors_rad=tuple(heading),
        velocity_errors_mps=tuple(velocity),
        exact_endpoint_error_m=endpoint_error,
        gap_preservation_failures=gap_failures,
    )


@dataclass(frozen=True, slots=True)
class CanonicalScenarioBundle:
    """One bounded canonical scenario bundle without source-map geometry."""

    scenario: ScenarioRecord
    coordinate_frame: CoordinateFrameRecord
    agents: tuple[AgentRecord, ...]
    trajectories: tuple[Trajectory, ...]
    trajectory_sample_parquet_bytes: int

    def __post_init__(self) -> None:
        validate_scenario_bundle(
            self.scenario,
            self.coordinate_frame,
            self.agents,
            self.trajectories,
        )
        object.__setattr__(
            self,
            "trajectory_sample_parquet_bytes",
            _positive_int(
                self.trajectory_sample_parquet_bytes,
                "trajectory_sample_parquet_bytes",
            ),
        )


def _batch_rows(batch: pa.RecordBatch) -> Iterator[dict[str, object]]:
    names = tuple(batch.schema.names)
    columns = tuple(batch.column(index) for index in range(batch.num_columns))
    for row_index in range(batch.num_rows):
        yield {
            name: column[row_index].as_py()
            for name, column in zip(names, columns, strict=True)
        }


def _rows(
    repository_root: Path,
    path: Path,
    schema_name: CanonicalSchemaName,
    batch_size: int,
) -> Iterator[dict[str, object]]:
    try:
        relative_path = path.relative_to(repository_root)
    except ValueError:
        raise ArtifactError("canonical cache path is outside repository_root") from None
    for batch in iter_canonical_parquet_batches(
        repository_root,
        (relative_path,),
        schema_name,
        batch_size=batch_size,
    ):
        yield from _batch_rows(batch)


def _trajectory(rows: Sequence[Mapping[str, object]]) -> Trajectory:
    samples = tuple(
        TrajectorySampleRecord(**dict(row))  # type: ignore[arg-type]
        for row in sorted(rows, key=lambda row: cast(int, row["sample_index"]))
    )
    return Trajectory(
        scenario_id=samples[0].scenario_id,
        agent_id=samples[0].agent_id,
        trajectory_id=samples[0].trajectory_id,
        samples=samples,
        origin_type=samples[0].origin_type,
        quality_flags=_merged_flags(*(sample.quality_flags for sample in samples)),
    )


def read_canonical_scenario_bundle(
    repository_root: Path,
    cache_entry: Path,
    *,
    batch_size: int = 65_536,
) -> CanonicalScenarioBundle:
    """Read one canonical cache entry with bounded Parquet batches and no map input."""
    if not isinstance(repository_root, Path) or not repository_root.is_absolute():
        raise ValidationError("repository_root must be an absolute Path")
    if not isinstance(cache_entry, Path) or not cache_entry.is_absolute():
        raise ValidationError("cache_entry must be an absolute Path")
    normalized_batch_size = _positive_int(batch_size, "batch_size", maximum=2**31 - 1)
    paths = {
        "scenario": cache_entry / "scenario_manifest.parquet",
        "frame": cache_entry / "coordinate_frame_metadata.parquet",
        "agents": cache_entry / "agent_metadata.parquet",
        "samples": cache_entry / "trajectory_samples.parquet",
    }
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise ArtifactError("canonical cache entry is incomplete or unsafe")
    scenario_rows = tuple(
        _rows(
            repository_root,
            paths["scenario"],
            CanonicalSchemaName.SCENARIO_MANIFEST,
            normalized_batch_size,
        )
    )
    frame_rows = tuple(
        _rows(
            repository_root,
            paths["frame"],
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            normalized_batch_size,
        )
    )
    if len(scenario_rows) != 1 or len(frame_rows) != 1:
        raise ArtifactError("canonical cache requires one scenario and frame")
    agents = tuple(
        AgentRecord(**row)  # type: ignore[arg-type]
        for row in _rows(
            repository_root,
            paths["agents"],
            CanonicalSchemaName.AGENT_METADATA,
            normalized_batch_size,
        )
    )
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in _rows(
        repository_root,
        paths["samples"],
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        normalized_batch_size,
    ):
        trajectory_id = row.get("trajectory_id")
        if not isinstance(trajectory_id, str):
            raise ArtifactError("trajectory sample has invalid trajectory_id")
        grouped.setdefault(trajectory_id, []).append(row)
    trajectories = tuple(_trajectory(grouped[key]) for key in sorted(grouped))
    return CanonicalScenarioBundle(
        scenario=ScenarioRecord(**scenario_rows[0]),  # type: ignore[arg-type]
        coordinate_frame=CoordinateFrameRecord(**frame_rows[0]),  # type: ignore[arg-type]
        agents=agents,
        trajectories=trajectories,
        trajectory_sample_parquet_bytes=paths["samples"].stat().st_size,
    )


def included_motion_trajectories(
    trajectories: Sequence[Trajectory],
) -> tuple[Trajectory, ...]:
    """Apply the frozen Phase 2 trajectory eligibility contract."""
    included: list[Trajectory] = []
    for trajectory in trajectories:
        if not isinstance(trajectory, Trajectory):
            raise ValidationError("trajectories must contain Trajectory values")
        valid = tuple(sample for sample in trajectory.samples if sample.is_valid)
        if len(valid) < _MINIMUM_VALID_SAMPLES:
            continue
        if valid[-1].timestamp_ns - valid[0].timestamp_ns < _MINIMUM_VALID_DURATION_NS:
            continue
        included.append(trajectory)
    return tuple(sorted(included, key=lambda item: item.trajectory_id))
