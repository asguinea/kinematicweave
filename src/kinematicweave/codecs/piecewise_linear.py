"""Deterministic minimum-segment error-bounded hold/linear codec."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
import math
from pathlib import Path
import statistics

import pyarrow as pa  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.exact import replay_track
from kinematicweave.data.parquet_io import iter_canonical_parquet_batches
from kinematicweave.data.procedural_artifacts import (
    ProceduralTapeArtifacts,
    verify_procedural_tape_artifacts,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.procedural import (
    ProceduralPrimitiveType,
    ProceduralSegment,
    ProceduralTape,
    ProceduralTrack,
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
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.identifiers import make_identifier

__all__ = [
    "ALGORITHM_VERSION",
    "ENCODER_NAME",
    "ENCODER_VERSION",
    "POSITION_COMPARISON_GUARD_M",
    "PiecewiseLinearCodecConfig",
    "ReplayValidationSummary",
    "encode_canonical_parquet_piecewise_linear",
    "encode_scenario_piecewise_linear",
    "encode_trajectory_piecewise_linear",
    "encoder_parameters_identity",
    "summarize_piecewise_linear_tape",
    "validate_piecewise_linear_replay",
    "verify_piecewise_linear_artifacts",
]

ENCODER_NAME = "minimum_segment_piecewise_linear"
ENCODER_VERSION = "1.0"
ALGORITHM_VERSION = "minimum-segment-dp-v1"
POSITION_COMPARISON_GUARD_M = 1.0e-12
_DISTANCE_POLICY = "xy_when_all_z_absent_else_xyz_when_all_z_present"
_GAP_POLICY = "invalid_samples_terminate_runs_no_bridging"
_ELEVATION_POLICY = "candidate_intervals_must_have_uniform_z_availability"
_TIE_BREAK_POLICY = "minimum_segments_then_later_next_breakpoint_recursively"


@dataclass(frozen=True, slots=True)
class PiecewiseLinearCodecConfig:
    """Configuration for source-timestamp position-bounded segmentation."""

    maximum_position_error_m: float = 0.10

    def __post_init__(self) -> None:
        value = self.maximum_position_error_m
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError("maximum_position_error_m must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValidationError("maximum_position_error_m must be finite")
        if normalized < 0.0:
            raise ValidationError("maximum_position_error_m must not be negative")
        object.__setattr__(self, "maximum_position_error_m", normalized)


_DEFAULT_CONFIG = PiecewiseLinearCodecConfig()


@dataclass(frozen=True, slots=True)
class ReplayValidationSummary:
    """Deterministic source-timestamp replay measurements for one trajectory."""

    source_sample_count: int
    valid_sample_count: int
    invalid_sample_count: int
    valid_run_count: int
    replayed_valid_sample_count: int
    invalid_gap_samples_with_state: int
    run_endpoints_exact: bool
    retained_breakpoints_exact: bool
    position_errors_m: tuple[float, ...]
    heading_errors_rad: tuple[float, ...]
    velocity_errors_mps: tuple[float, ...]


def encoder_parameters_identity(config: PiecewiseLinearCodecConfig) -> str:
    """Return the canonical identity of all behavior-affecting parameters."""
    if not isinstance(config, PiecewiseLinearCodecConfig):
        raise ValidationError("config must be a PiecewiseLinearCodecConfig")
    return canonical_sha256(
        "minimum-segment-piecewise-linear-parameters",
        {
            "algorithm_version": ALGORITHM_VERSION,
            "distance_policy": _DISTANCE_POLICY,
            "elevation_policy": _ELEVATION_POLICY,
            "gap_policy": _GAP_POLICY,
            "maximum_position_error_m": config.maximum_position_error_m,
            "tie_break_policy": _TIE_BREAK_POLICY,
        },
    )


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


def _elevation_chunks(
    run: Sequence[TrajectorySampleRecord],
) -> tuple[tuple[TrajectorySampleRecord, ...], ...]:
    chunks: list[tuple[TrajectorySampleRecord, ...]] = []
    current: list[TrajectorySampleRecord] = []
    availability: bool | None = None
    for sample in run:
        sample_availability = sample.z_m is not None
        if current and sample_availability != availability:
            chunks.append(tuple(current))
            current = []
        current.append(sample)
        availability = sample_availability
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _position(sample: TrajectorySampleRecord) -> tuple[float, ...]:
    if sample.z_m is None:
        return (sample.x_m, sample.y_m)
    return (sample.x_m, sample.y_m, sample.z_m)


def _all_positions_equal(samples: Sequence[TrajectorySampleRecord]) -> bool:
    first = _position(samples[0])
    return all(_position(sample) == first for sample in samples[1:])


def _candidate_maximum_error(
    samples: Sequence[TrajectorySampleRecord],
    start_index: int,
    end_index: int,
) -> float | None:
    interval = samples[start_index : end_index + 1]
    has_elevation = interval[0].z_m is not None
    if any((sample.z_m is not None) != has_elevation for sample in interval):
        return None
    start = interval[0]
    end = interval[-1]
    duration_ns = end.timestamp_ns - start.timestamp_ns
    if duration_ns <= 0:
        raise ValidationError("candidate segment timestamps must increase")
    maximum = 0.0
    for sample in interval[1:-1]:
        alpha = (sample.timestamp_ns - start.timestamp_ns) / duration_ns
        replay_x = start.x_m + (end.x_m - start.x_m) * alpha
        replay_y = start.y_m + (end.y_m - start.y_m) * alpha
        squared = (replay_x - sample.x_m) ** 2 + (replay_y - sample.y_m) ** 2
        if has_elevation:
            if start.z_m is None or end.z_m is None or sample.z_m is None:
                raise ValidationError("elevation availability changed in candidate")
            replay_z = start.z_m + (end.z_m - start.z_m) * alpha
            squared += (replay_z - sample.z_m) ** 2
        maximum = max(maximum, math.sqrt(squared))
    return maximum


def _minimum_segment_breakpoints(
    samples: Sequence[TrajectorySampleRecord],
    config: PiecewiseLinearCodecConfig,
) -> tuple[int, ...]:
    count = len(samples)
    if count == 1:
        return (0,)
    best_counts = [count + 1] * count
    next_breakpoint = [-1] * count
    best_counts[-1] = 0
    for start_index in range(count - 2, -1, -1):
        for end_index in range(start_index + 1, count):
            maximum_error = _candidate_maximum_error(
                samples,
                start_index,
                end_index,
            )
            if maximum_error is None or (
                maximum_error
                > config.maximum_position_error_m + POSITION_COMPARISON_GUARD_M
            ):
                continue
            candidate_count = 1 + best_counts[end_index]
            if candidate_count < best_counts[start_index] or (
                candidate_count == best_counts[start_index]
                and end_index > next_breakpoint[start_index]
            ):
                best_counts[start_index] = candidate_count
                next_breakpoint[start_index] = end_index
    if next_breakpoint[0] < 0:
        raise ValidationError("no error-bounded segmentation exists")
    breakpoints = [0]
    while breakpoints[-1] != count - 1:
        following = next_breakpoint[breakpoints[-1]]
        if following <= breakpoints[-1]:
            raise ValidationError("dynamic-programming segmentation is incomplete")
        breakpoints.append(following)
    return tuple(breakpoints)


def _segment(
    *,
    tape_id: str,
    procedural_track_id: str,
    parameter_identity: str,
    run_index: int,
    segment_index: int,
    interval: Sequence[TrajectorySampleRecord],
    quality_flags: Sequence[str],
) -> ProceduralSegment:
    start = interval[0]
    end = interval[-1]
    primitive_type = (
        ProceduralPrimitiveType.HOLD
        if _all_positions_equal(interval)
        else ProceduralPrimitiveType.LINEAR
    )
    segment_id = _identifier(
        "procedural-segment",
        "minimum-segment-piecewise-linear-segment",
        {
            "parameter_identity": parameter_identity,
            "procedural_track_id": procedural_track_id,
            "run_index": run_index,
            "segment_index": segment_index,
            "source_start_sample_index": start.sample_index,
            "source_end_sample_index": end.sample_index,
            "start_time_ns": start.timestamp_ns,
            "end_time_ns": end.timestamp_ns,
            "primitive_type": primitive_type.value,
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


def _normalized_agent_class(agent_class: AgentClass | str) -> AgentClass:
    try:
        return (
            agent_class
            if isinstance(agent_class, AgentClass)
            else AgentClass(agent_class)
        )
    except (TypeError, ValueError):
        raise ValidationError(
            f"agent_class has invalid value {agent_class!r}"
        ) from None


def encode_trajectory_piecewise_linear(
    trajectory: Trajectory,
    agent_class: AgentClass | str,
    config: PiecewiseLinearCodecConfig = _DEFAULT_CONFIG,
    *,
    tape_id: str | None = None,
) -> ProceduralTrack:
    """Encode one trajectory with exact minimum-segment dynamic programming."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(config, PiecewiseLinearCodecConfig):
        raise ValidationError("config must be a PiecewiseLinearCodecConfig")
    normalized_class = _normalized_agent_class(agent_class)
    runs = _valid_runs(trajectory.samples)
    if not runs:
        raise ValidationError("trajectory has no valid samples to encode")
    parameter_identity = encoder_parameters_identity(config)
    track_identity_payload = {
        "scenario_id": trajectory.scenario_id,
        "agent_id": trajectory.agent_id,
        "trajectory_id": trajectory.trajectory_id,
        "parameters": parameter_identity,
        "samples": [_sample_identity(sample) for sample in trajectory.samples],
    }
    procedural_track_id = _identifier(
        "procedural-track",
        "minimum-segment-piecewise-linear-track",
        track_identity_payload,
    )
    normalized_tape_id = tape_id or _identifier(
        "tape",
        "minimum-segment-piecewise-linear-standalone-tape",
        track_identity_payload,
    )
    segments: list[ProceduralSegment] = []
    for run_index, run in enumerate(runs):
        segment_index = 0
        for chunk in _elevation_chunks(run):
            breakpoints = _minimum_segment_breakpoints(chunk, config)
            intervals = (
                ((chunk[0],),)
                if len(breakpoints) == 1
                else tuple(
                    chunk[start : end + 1] for start, end in pairwise(breakpoints)
                )
            )
            for interval in intervals:
                segments.append(
                    _segment(
                        tape_id=normalized_tape_id,
                        procedural_track_id=procedural_track_id,
                        parameter_identity=parameter_identity,
                        run_index=run_index,
                        segment_index=segment_index,
                        interval=interval,
                        quality_flags=_merged_flags(
                            trajectory.quality_flags,
                            *(sample.quality_flags for sample in interval),
                        ),
                    )
                )
                segment_index += 1
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


def encode_scenario_piecewise_linear(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
    config: PiecewiseLinearCodecConfig = _DEFAULT_CONFIG,
    *,
    source_validation_report_identity: str | None,
) -> ProceduralTape:
    """Encode one validated canonical scenario bundle."""
    if not isinstance(config, PiecewiseLinearCodecConfig):
        raise ValidationError("config must be a PiecewiseLinearCodecConfig")
    validate_scenario_bundle(scenario, coordinate_frame, agents, trajectories)
    ordered_trajectories = tuple(
        sorted(trajectories, key=lambda item: (item.scenario_id, item.trajectory_id))
    )
    agents_by_id = {agent.agent_id: agent for agent in agents}
    parameter_identity = encoder_parameters_identity(config)
    tape_id = _identifier(
        "tape",
        "minimum-segment-piecewise-linear-scenario-tape",
        {
            "scenario_id": scenario.scenario_id,
            "coordinate_frame_id": coordinate_frame.coordinate_frame_id,
            "source_dataset_id": scenario.dataset_id,
            "source_dataset_version": scenario.dataset_version,
            "source_validation_report_identity": source_validation_report_identity,
            "encoder": ENCODER_NAME,
            "version": ENCODER_VERSION,
            "parameters": parameter_identity,
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
        encode_trajectory_piecewise_linear(
            trajectory,
            agents_by_id[trajectory.agent_id].agent_class,
            config,
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


def _batch_rows(batch: pa.RecordBatch) -> Iterator[dict[str, object]]:
    names = tuple(batch.schema.names)
    columns = tuple(batch.column(index) for index in range(batch.num_columns))
    for row_index in range(batch.num_rows):
        yield {
            name: column[row_index].as_py()
            for name, column in zip(names, columns, strict=True)
        }


def _parquet_rows(
    repository_root: Path,
    paths: Sequence[str | Path],
    schema: CanonicalSchemaName,
    batch_size: int,
) -> Iterator[dict[str, object]]:
    for batch in iter_canonical_parquet_batches(
        repository_root,
        paths,
        schema,
        batch_size=batch_size,
    ):
        yield from _batch_rows(batch)


def encode_canonical_parquet_piecewise_linear(
    repository_root: Path,
    *,
    scenario_paths: Sequence[str | Path],
    coordinate_frame_paths: Sequence[str | Path],
    agent_paths: Sequence[str | Path],
    trajectory_sample_paths: Sequence[str | Path],
    source_validation_report_identity: str | None,
    config: PiecewiseLinearCodecConfig = _DEFAULT_CONFIG,
    batch_size: int = 65_536,
) -> ProceduralTape:
    """Encode one canonical scenario from bounded Parquet record batches."""
    scenario_rows = tuple(
        _parquet_rows(
            repository_root,
            scenario_paths,
            CanonicalSchemaName.SCENARIO_MANIFEST,
            batch_size,
        )
    )
    frame_rows = tuple(
        _parquet_rows(
            repository_root,
            coordinate_frame_paths,
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            batch_size,
        )
    )
    if len(scenario_rows) != 1 or len(frame_rows) != 1:
        raise ArtifactError("canonical scenario input requires one scenario and frame")
    agents = tuple(
        AgentRecord(**row)  # type: ignore[arg-type]
        for row in _parquet_rows(
            repository_root,
            agent_paths,
            CanonicalSchemaName.AGENT_METADATA,
            batch_size,
        )
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
    return encode_scenario_piecewise_linear(
        ScenarioRecord(**scenario_rows[0]),  # type: ignore[arg-type]
        CoordinateFrameRecord(**frame_rows[0]),  # type: ignore[arg-type]
        agents,
        trajectories,
        config,
        source_validation_report_identity=source_validation_report_identity,
    )


def _trajectory_from_rows(rows: Sequence[Mapping[str, object]]) -> Trajectory:
    samples = tuple(
        TrajectorySampleRecord(**dict(row))  # type: ignore[arg-type]
        for row in sorted(rows, key=_sample_index)
    )
    flags = _merged_flags(*(sample.quality_flags for sample in samples))
    return Trajectory(
        scenario_id=samples[0].scenario_id,
        agent_id=samples[0].agent_id,
        trajectory_id=samples[0].trajectory_id,
        samples=samples,
        origin_type=samples[0].origin_type,
        quality_flags=flags,
    )


def _sample_index(row: Mapping[str, object]) -> int:
    value = row.get("sample_index")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactError("trajectory sample_index is not an integer")
    return value


def _wrap_heading(value: float) -> float:
    wrapped = (value + math.pi) % (2.0 * math.pi) - math.pi
    return -math.pi if wrapped == math.pi else wrapped


def validate_piecewise_linear_replay(
    track: ProceduralTrack,
    trajectory: Trajectory,
    config: PiecewiseLinearCodecConfig = _DEFAULT_CONFIG,
) -> ReplayValidationSummary:
    """Validate source-timestamp fidelity, support, gaps, and the error bound."""
    if not isinstance(track, ProceduralTrack):
        raise ValidationError("track must be a ProceduralTrack")
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(config, PiecewiseLinearCodecConfig):
        raise ValidationError("config must be a PiecewiseLinearCodecConfig")
    if (
        track.scenario_id != trajectory.scenario_id
        or track.agent_id != trajectory.agent_id
        or track.trajectory_id != trajectory.trajectory_id
    ):
        raise ValidationError("track and trajectory identities differ")

    position_errors: list[float] = []
    heading_errors: list[float] = []
    velocity_errors: list[float] = []
    invalid_with_state = 0
    endpoint_indices: set[int] = set()
    for segment in track.segments:
        endpoint_indices.add(segment.source_start_sample_index)
        endpoint_indices.add(segment.source_end_sample_index)
        if any(
            not trajectory.samples[index].is_valid
            for index in range(
                segment.source_start_sample_index,
                segment.source_end_sample_index + 1,
            )
        ):
            raise ValidationError("segment bridges an invalid source sample")

    valid_runs = _valid_runs(trajectory.samples)
    run_endpoint_indices = {
        index
        for run in valid_runs
        for index in (run[0].sample_index, run[-1].sample_index)
    }
    endpoints_exact = True
    breakpoints_exact = True
    replayed = 0
    for sample in trajectory.samples:
        state = replay_track(track, sample.timestamp_ns)
        if not sample.is_valid:
            invalid_with_state += state is not None
            continue
        if state is None:
            raise ValidationError("valid source timestamp has no replay state")
        if (state.z_m is None) != (sample.z_m is None):
            raise ValidationError("replay elevation availability differs")
        squared = (state.x_m - sample.x_m) ** 2 + (state.y_m - sample.y_m) ** 2
        if sample.z_m is not None:
            if state.z_m is None:
                raise ValidationError("replay omitted source elevation")
            squared += (state.z_m - sample.z_m) ** 2
        position_error = math.sqrt(squared)
        if position_error > (
            config.maximum_position_error_m + POSITION_COMPARISON_GUARD_M
        ):
            raise ValidationError("replay exceeds maximum_position_error_m")
        position_errors.append(position_error)
        if sample.heading_rad is not None and state.heading_rad is not None:
            heading_errors.append(
                abs(_wrap_heading(state.heading_rad - sample.heading_rad))
            )
        if (
            sample.velocity_x_mps is not None
            and sample.velocity_y_mps is not None
            and state.velocity_x_mps is not None
            and state.velocity_y_mps is not None
        ):
            velocity_errors.append(
                math.hypot(
                    state.velocity_x_mps - sample.velocity_x_mps,
                    state.velocity_y_mps - sample.velocity_y_mps,
                )
            )
        exact_state = (
            state.x_m,
            state.y_m,
            state.z_m,
            state.heading_rad,
            state.velocity_x_mps,
            state.velocity_y_mps,
        ) == (
            sample.x_m,
            sample.y_m,
            sample.z_m,
            sample.heading_rad,
            sample.velocity_x_mps,
            sample.velocity_y_mps,
        )
        if sample.sample_index in run_endpoint_indices:
            endpoints_exact = endpoints_exact and exact_state
        if sample.sample_index in endpoint_indices:
            breakpoints_exact = breakpoints_exact and exact_state
        replayed += 1
    if invalid_with_state:
        raise ValidationError("invalid source timestamp has a replay state")
    if not endpoints_exact:
        raise ValidationError("valid run endpoint replay is not exact")
    if not breakpoints_exact:
        raise ValidationError("retained breakpoint replay is not exact")
    return ReplayValidationSummary(
        source_sample_count=trajectory.sample_count,
        valid_sample_count=len(position_errors),
        invalid_sample_count=trajectory.sample_count - len(position_errors),
        valid_run_count=len(valid_runs),
        replayed_valid_sample_count=replayed,
        invalid_gap_samples_with_state=invalid_with_state,
        run_endpoints_exact=endpoints_exact,
        retained_breakpoints_exact=breakpoints_exact,
        position_errors_m=tuple(position_errors),
        heading_errors_rad=tuple(heading_errors),
        velocity_errors_mps=tuple(velocity_errors),
    )


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    alpha = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * alpha


def _error_statistics(values: Sequence[float]) -> dict[str, int | float | None]:
    return {
        "count": len(values),
        "maximum": max(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "p95": _percentile(values, 0.95),
    }


def summarize_piecewise_linear_tape(
    tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
    config: PiecewiseLinearCodecConfig = _DEFAULT_CONFIG,
) -> dict[str, object]:
    """Return a deterministic summary with source-timestamp diagnostics."""
    if tape.encoder_name != ENCODER_NAME or tape.encoder_version != ENCODER_VERSION:
        raise ValidationError("tape encoder identity differs")
    if tape.encoder_parameters_identity != encoder_parameters_identity(config):
        raise ValidationError("tape encoder parameter identity differs")
    source_by_id = {trajectory.trajectory_id: trajectory for trajectory in trajectories}
    if len(source_by_id) != len(trajectories):
        raise ValidationError("trajectory identifiers must be unique")
    validations = tuple(
        validate_piecewise_linear_replay(
            track,
            source_by_id[track.trajectory_id],
            config,
        )
        for track in tape.tracks
    )
    position_errors = tuple(
        value for summary in validations for value in summary.position_errors_m
    )
    heading_errors = tuple(
        value for summary in validations for value in summary.heading_errors_rad
    )
    velocity_errors = tuple(
        value for summary in validations for value in summary.velocity_errors_mps
    )
    return {
        "encoder_name": tape.encoder_name,
        "encoder_version": tape.encoder_version,
        "encoder_parameters_identity": tape.encoder_parameters_identity,
        "configuration": {
            "maximum_position_error_m": config.maximum_position_error_m,
            "floating_comparison_guard_m": POSITION_COMPARISON_GUARD_M,
        },
        "trajectory_count": len(validations),
        "source_sample_count": sum(
            summary.source_sample_count for summary in validations
        ),
        "valid_sample_count": sum(
            summary.valid_sample_count for summary in validations
        ),
        "invalid_sample_count": sum(
            summary.invalid_sample_count for summary in validations
        ),
        "valid_run_count": sum(summary.valid_run_count for summary in validations),
        "replayed_valid_sample_count": sum(
            summary.replayed_valid_sample_count for summary in validations
        ),
        "invalid_gap_samples_with_state": sum(
            summary.invalid_gap_samples_with_state for summary in validations
        ),
        "run_endpoints_exact": all(
            summary.run_endpoints_exact for summary in validations
        ),
        "retained_breakpoints_exact": all(
            summary.retained_breakpoints_exact for summary in validations
        ),
        "hold_segment_count": sum(
            segment.primitive_type is ProceduralPrimitiveType.HOLD
            for track in tape.tracks
            for segment in track.segments
        ),
        "linear_segment_count": sum(
            segment.primitive_type is ProceduralPrimitiveType.LINEAR
            for track in tape.tracks
            for segment in track.segments
        ),
        "segment_count": tape.segment_count,
        "position_error_m": _error_statistics(position_errors),
        "heading_error_rad": _error_statistics(heading_errors),
        "velocity_error_mps": _error_statistics(velocity_errors),
    }


def verify_piecewise_linear_artifacts(
    repository_root: Path,
    artifacts: ProceduralTapeArtifacts,
    trajectories: Sequence[Trajectory],
    config: PiecewiseLinearCodecConfig = _DEFAULT_CONFIG,
    *,
    expected_tape: ProceduralTape | None = None,
) -> tuple[ProceduralTape, dict[str, object]]:
    """Verify the artifact bundle and its source-timestamp position contract."""
    tape = verify_procedural_tape_artifacts(
        repository_root,
        artifacts,
        expected_tape=expected_tape,
    )
    try:
        summary = summarize_piecewise_linear_tape(tape, trajectories, config)
    except KeyError as error:
        raise ArtifactError("artifact tape references an unknown trajectory") from error
    return tape, summary
