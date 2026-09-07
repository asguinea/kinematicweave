"""Deterministic velocity-aware hold/linear/cubic-Hermite codec."""

from collections.abc import Sequence
from dataclasses import dataclass
import math
from pathlib import Path
from typing import cast

from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.exact import (
    cubic_hermite_basis,
    cubic_hermite_basis_derivative,
)
from kinematicweave.codecs.piecewise_linear import (
    POSITION_COMPARISON_GUARD_M,
    PiecewiseLinearCodecConfig,
    ReplayValidationSummary,
    _all_positions_equal,
    _elevation_chunks,
    _error_statistics,
    _identifier,
    _merged_flags,
    _normalized_agent_class,
    _parquet_rows,
    _sample_identity,
    _trajectory_from_rows,
    _valid_runs,
    validate_piecewise_linear_replay,
)
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
    validate_scenario_bundle,
)
from kinematicweave.errors import ArtifactError, ValidationError

__all__ = [
    "ALGORITHM_VERSION",
    "ENCODER_NAME",
    "ENCODER_VERSION",
    "HermiteCodecConfig",
    "encode_canonical_parquet_hermite",
    "encode_scenario_hermite",
    "encode_trajectory_hermite",
    "hermite_encoder_parameters_identity",
    "summarize_hermite_tape",
    "validate_hermite_replay",
    "verify_hermite_artifacts",
]

ENCODER_NAME = "minimum_segment_velocity_aware"
ENCODER_VERSION = "1.0"
ALGORITHM_VERSION = "velocity-aware-minimum-segment-dp-v1"
_DISTANCE_POLICY = "xy_when_all_z_absent_else_xyz_when_all_z_present"
_GAP_POLICY = "invalid_samples_terminate_runs_no_bridging"
_ELEVATION_POLICY = "candidate_intervals_must_have_uniform_z_availability"
_HERMITE_POLICY = (
    "xy_cubic_hermite_from_endpoint_velocity_z_linear_heading_shortest_wrap"
)
_OBJECTIVE = (
    "minimum_segments_then_total_squared_position_error_then_total_squared_"
    "represented_velocity_error_then_later_breakpoint_recursively_then_"
    "primitive_order_then_source_order"
)
_REQUIRED_PRIMITIVES = frozenset(
    (
        ProceduralPrimitiveType.HOLD,
        ProceduralPrimitiveType.LINEAR,
        ProceduralPrimitiveType.CUBIC_HERMITE,
    )
)


@dataclass(frozen=True, slots=True)
class HermiteCodecConfig:
    """Configuration for velocity-aware error-bounded segmentation."""

    maximum_position_error_m: float = 0.10
    candidate_primitives: tuple[ProceduralPrimitiveType, ...] = (
        ProceduralPrimitiveType.HOLD,
        ProceduralPrimitiveType.LINEAR,
        ProceduralPrimitiveType.CUBIC_HERMITE,
    )

    def __post_init__(self) -> None:
        value = self.maximum_position_error_m
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError("maximum_position_error_m must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValidationError("maximum_position_error_m must be finite")
        if normalized < 0.0:
            raise ValidationError("maximum_position_error_m must not be negative")
        raw_values = cast(object, self.candidate_primitives)
        if isinstance(raw_values, (str, bytes)) or not isinstance(raw_values, Sequence):
            raise ValidationError("candidate_primitives must be a sequence")
        try:
            primitives = tuple(
                value
                if isinstance(value, ProceduralPrimitiveType)
                else ProceduralPrimitiveType(value)
                for value in raw_values
            )
        except (TypeError, ValueError):
            raise ValidationError(
                "candidate_primitives contains an invalid value"
            ) from None
        if len(set(primitives)) != len(primitives):
            raise ValidationError("candidate_primitives must be unique")
        if set(primitives) != _REQUIRED_PRIMITIVES:
            raise ValidationError(
                "candidate_primitives must contain hold, linear, and cubic_hermite"
            )
        object.__setattr__(self, "maximum_position_error_m", normalized)
        object.__setattr__(self, "candidate_primitives", primitives)


_DEFAULT_CONFIG = HermiteCodecConfig()


@dataclass(frozen=True, slots=True)
class _Candidate:
    start_index: int
    end_index: int
    primitive_type: ProceduralPrimitiveType
    position_squared_error: float
    velocity_squared_error: float


def hermite_encoder_parameters_identity(config: HermiteCodecConfig) -> str:
    """Return the canonical identity of every behavior-affecting parameter."""
    if not isinstance(config, HermiteCodecConfig):
        raise ValidationError("config must be a HermiteCodecConfig")
    return canonical_sha256(
        "minimum-segment-velocity-aware-parameters",
        {
            "algorithm_version": ALGORITHM_VERSION,
            "candidate_primitives": [
                primitive.value for primitive in config.candidate_primitives
            ],
            "distance_policy": _DISTANCE_POLICY,
            "elevation_policy": _ELEVATION_POLICY,
            "gap_policy": _GAP_POLICY,
            "hermite_policy": _HERMITE_POLICY,
            "maximum_position_error_m": config.maximum_position_error_m,
            "objective": _OBJECTIVE,
        },
    )


def _interpolated_axis(
    primitive: ProceduralPrimitiveType,
    start_position: float,
    end_position: float,
    start_velocity: float | None,
    end_velocity: float | None,
    duration_seconds: float,
    u: float,
) -> tuple[float, float | None]:
    if primitive is ProceduralPrimitiveType.HOLD:
        velocity = (
            None
            if start_velocity is None or end_velocity is None
            else start_velocity + (end_velocity - start_velocity) * u
        )
        return start_position, velocity
    if primitive is ProceduralPrimitiveType.LINEAR:
        velocity = (
            None
            if start_velocity is None or end_velocity is None
            else start_velocity + (end_velocity - start_velocity) * u
        )
        return start_position + (end_position - start_position) * u, velocity
    if start_velocity is None or end_velocity is None:
        raise ValidationError("cubic Hermite candidate requires endpoint velocity")
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


def _candidate(
    samples: Sequence[TrajectorySampleRecord],
    start_index: int,
    end_index: int,
    primitive: ProceduralPrimitiveType,
    config: HermiteCodecConfig,
) -> _Candidate | None:
    interval = samples[start_index : end_index + 1]
    if len(interval) < 2:
        return None
    has_elevation = interval[0].z_m is not None
    if any((sample.z_m is not None) != has_elevation for sample in interval):
        return None
    if primitive is ProceduralPrimitiveType.HOLD and not _all_positions_equal(interval):
        return None
    start = interval[0]
    end = interval[-1]
    if primitive is ProceduralPrimitiveType.CUBIC_HERMITE and any(
        value is None
        for value in (
            start.velocity_x_mps,
            start.velocity_y_mps,
            end.velocity_x_mps,
            end.velocity_y_mps,
        )
    ):
        return None
    duration_ns = end.timestamp_ns - start.timestamp_ns
    if duration_ns <= 0:
        raise ValidationError("candidate segment timestamps must increase")
    duration_seconds = duration_ns / 1_000_000_000.0
    if primitive is ProceduralPrimitiveType.CUBIC_HERMITE:
        endpoint_velocities = (
            start.velocity_x_mps,
            start.velocity_y_mps,
            end.velocity_x_mps,
            end.velocity_y_mps,
        )
        if any(
            velocity is None or not math.isfinite(duration_seconds * velocity)
            for velocity in endpoint_velocities
        ):
            return None
    position_squared_error = 0.0
    velocity_squared_error = 0.0
    for sample in interval:
        u = (sample.timestamp_ns - start.timestamp_ns) / duration_ns
        if sample is start:
            replay_x, replay_y = start.x_m, start.y_m
            replay_vx, replay_vy = start.velocity_x_mps, start.velocity_y_mps
        elif sample is end:
            replay_x, replay_y = end.x_m, end.y_m
            replay_vx, replay_vy = end.velocity_x_mps, end.velocity_y_mps
        else:
            replay_x, replay_vx = _interpolated_axis(
                primitive,
                start.x_m,
                end.x_m,
                start.velocity_x_mps,
                end.velocity_x_mps,
                duration_seconds,
                u,
            )
            replay_y, replay_vy = _interpolated_axis(
                primitive,
                start.y_m,
                end.y_m,
                start.velocity_y_mps,
                end.velocity_y_mps,
                duration_seconds,
                u,
            )
        represented = tuple(
            value
            for value in (replay_x, replay_y, replay_vx, replay_vy)
            if value is not None
        )
        if any(not math.isfinite(value) for value in represented):
            return None
        squared = (replay_x - sample.x_m) ** 2 + (replay_y - sample.y_m) ** 2
        if has_elevation:
            if start.z_m is None or end.z_m is None or sample.z_m is None:
                raise ValidationError("elevation availability changed in candidate")
            replay_z = start.z_m + (end.z_m - start.z_m) * u
            if not math.isfinite(replay_z):
                return None
            squared += (replay_z - sample.z_m) ** 2
        if not math.isfinite(squared):
            return None
        if math.sqrt(squared) > (
            config.maximum_position_error_m + POSITION_COMPARISON_GUARD_M
        ):
            return None
        position_squared_error += squared
        if (
            sample.velocity_x_mps is not None
            and sample.velocity_y_mps is not None
            and replay_vx is not None
            and replay_vy is not None
        ):
            velocity_error = math.hypot(
                replay_vx - sample.velocity_x_mps,
                replay_vy - sample.velocity_y_mps,
            )
            maximum_squarable = math.sqrt(float.fromhex("0x1.fffffffffffffp+1023"))
            if not math.isfinite(velocity_error) or velocity_error > (
                maximum_squarable
            ):
                return None
            velocity_squared_error += velocity_error * velocity_error
            if not math.isfinite(velocity_squared_error):
                return None
    return _Candidate(
        start_index=start_index,
        end_index=end_index,
        primitive_type=primitive,
        position_squared_error=position_squared_error,
        velocity_squared_error=velocity_squared_error,
    )


def _minimum_velocity_aware_candidates(
    samples: Sequence[TrajectorySampleRecord],
    config: HermiteCodecConfig,
) -> tuple[_Candidate, ...]:
    count = len(samples)
    if count == 1:
        return (
            _Candidate(
                start_index=0,
                end_index=0,
                primitive_type=ProceduralPrimitiveType.HOLD,
                position_squared_error=0.0,
                velocity_squared_error=0.0,
            ),
        )
    primitive_rank = {
        primitive: rank for rank, primitive in enumerate(config.candidate_primitives)
    }
    best: list[tuple[_Candidate, ...] | None] = [None] * count
    best[-1] = ()
    for start_index in range(count - 2, -1, -1):
        best_key: tuple[object, ...] | None = None
        best_path: tuple[_Candidate, ...] | None = None
        for end_index in range(start_index + 1, count):
            suffix = best[end_index]
            if suffix is None:
                continue
            for primitive in config.candidate_primitives:
                candidate = _candidate(
                    samples,
                    start_index,
                    end_index,
                    primitive,
                    config,
                )
                if candidate is None:
                    continue
                path = (candidate, *suffix)
                breakpoints = tuple(item.end_index for item in path[:-1])
                key = (
                    len(path),
                    math.fsum(item.position_squared_error for item in path),
                    math.fsum(item.velocity_squared_error for item in path),
                    tuple(-index for index in breakpoints),
                    tuple(primitive_rank[item.primitive_type] for item in path),
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best_path = path
        best[start_index] = best_path
    if best[0] is None:
        raise ValidationError("no error-bounded velocity-aware segmentation exists")
    return best[0]


def _segment(
    *,
    tape_id: str,
    procedural_track_id: str,
    parameter_identity: str,
    run_index: int,
    segment_index: int,
    candidate: _Candidate,
    chunk: Sequence[TrajectorySampleRecord],
    quality_flags: Sequence[str],
) -> ProceduralSegment:
    start = chunk[candidate.start_index]
    end = chunk[candidate.end_index]
    primitive = candidate.primitive_type
    segment_id = _identifier(
        "procedural-segment",
        "minimum-segment-velocity-aware-segment",
        {
            "parameter_identity": parameter_identity,
            "procedural_track_id": procedural_track_id,
            "run_index": run_index,
            "segment_index": segment_index,
            "source_start_sample_index": start.sample_index,
            "source_end_sample_index": end.sample_index,
            "start_time_ns": start.timestamp_ns,
            "end_time_ns": end.timestamp_ns,
            "primitive_type": primitive.value,
        },
    )
    return ProceduralSegment(
        tape_id=tape_id,
        procedural_track_id=procedural_track_id,
        segment_id=segment_id,
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


def encode_trajectory_hermite(
    trajectory: Trajectory,
    agent_class: AgentClass | str,
    config: HermiteCodecConfig = _DEFAULT_CONFIG,
    *,
    tape_id: str | None = None,
) -> ProceduralTrack:
    """Encode one trajectory with deterministic velocity-aware dynamic programming."""
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(config, HermiteCodecConfig):
        raise ValidationError("config must be a HermiteCodecConfig")
    normalized_class = _normalized_agent_class(agent_class)
    runs = _valid_runs(trajectory.samples)
    if not runs:
        raise ValidationError("trajectory has no valid samples to encode")
    parameter_identity = hermite_encoder_parameters_identity(config)
    track_identity_payload = {
        "scenario_id": trajectory.scenario_id,
        "agent_id": trajectory.agent_id,
        "trajectory_id": trajectory.trajectory_id,
        "parameters": parameter_identity,
        "samples": [_sample_identity(sample) for sample in trajectory.samples],
    }
    procedural_track_id = _identifier(
        "procedural-track",
        "minimum-segment-velocity-aware-track",
        track_identity_payload,
    )
    normalized_tape_id = tape_id or _identifier(
        "tape",
        "minimum-segment-velocity-aware-standalone-tape",
        track_identity_payload,
    )
    segments: list[ProceduralSegment] = []
    for run_index, run in enumerate(runs):
        segment_index = 0
        for chunk in _elevation_chunks(run):
            for candidate in _minimum_velocity_aware_candidates(chunk, config):
                interval = chunk[candidate.start_index : candidate.end_index + 1]
                segments.append(
                    _segment(
                        tape_id=normalized_tape_id,
                        procedural_track_id=procedural_track_id,
                        parameter_identity=parameter_identity,
                        run_index=run_index,
                        segment_index=segment_index,
                        candidate=candidate,
                        chunk=chunk,
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


def encode_scenario_hermite(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
    config: HermiteCodecConfig = _DEFAULT_CONFIG,
    *,
    source_validation_report_identity: str | None,
) -> ProceduralTape:
    """Encode one validated canonical scenario bundle."""
    if not isinstance(config, HermiteCodecConfig):
        raise ValidationError("config must be a HermiteCodecConfig")
    validate_scenario_bundle(scenario, coordinate_frame, agents, trajectories)
    ordered = tuple(
        sorted(trajectories, key=lambda item: (item.scenario_id, item.trajectory_id))
    )
    agents_by_id = {agent.agent_id: agent for agent in agents}
    parameter_identity = hermite_encoder_parameters_identity(config)
    tape_id = _identifier(
        "tape",
        "minimum-segment-velocity-aware-scenario-tape",
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
                for trajectory in ordered
            ],
        },
    )
    tracks = tuple(
        encode_trajectory_hermite(
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


def encode_canonical_parquet_hermite(
    repository_root: Path,
    *,
    scenario_paths: Sequence[str | Path],
    coordinate_frame_paths: Sequence[str | Path],
    agent_paths: Sequence[str | Path],
    trajectory_sample_paths: Sequence[str | Path],
    source_validation_report_identity: str | None,
    config: HermiteCodecConfig = _DEFAULT_CONFIG,
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
    return encode_scenario_hermite(
        ScenarioRecord(**scenario_rows[0]),  # type: ignore[arg-type]
        CoordinateFrameRecord(**frame_rows[0]),  # type: ignore[arg-type]
        agents,
        trajectories,
        config,
        source_validation_report_identity=source_validation_report_identity,
    )


def validate_hermite_replay(
    track: ProceduralTrack,
    trajectory: Trajectory,
    config: HermiteCodecConfig = _DEFAULT_CONFIG,
) -> ReplayValidationSummary:
    """Validate source-timestamp support, fidelity, gaps, and position bound."""
    if not isinstance(config, HermiteCodecConfig):
        raise ValidationError("config must be a HermiteCodecConfig")
    return validate_piecewise_linear_replay(
        track,
        trajectory,
        PiecewiseLinearCodecConfig(config.maximum_position_error_m),
    )


def summarize_hermite_tape(
    tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
    config: HermiteCodecConfig = _DEFAULT_CONFIG,
) -> dict[str, object]:
    """Return deterministic source-timestamp and primitive diagnostics."""
    if tape.encoder_name != ENCODER_NAME or tape.encoder_version != ENCODER_VERSION:
        raise ValidationError("tape encoder identity differs")
    if tape.encoder_parameters_identity != hermite_encoder_parameters_identity(config):
        raise ValidationError("tape encoder parameter identity differs")
    source_by_id = {trajectory.trajectory_id: trajectory for trajectory in trajectories}
    if len(source_by_id) != len(trajectories):
        raise ValidationError("trajectory identifiers must be unique")
    validations = tuple(
        validate_hermite_replay(track, source_by_id[track.trajectory_id], config)
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
    primitive_counts = {
        primitive.value: sum(
            segment.primitive_type is primitive
            for track in tape.tracks
            for segment in track.segments
        )
        for primitive in config.candidate_primitives
    }
    return {
        "encoder_name": tape.encoder_name,
        "encoder_version": tape.encoder_version,
        "encoder_parameters_identity": tape.encoder_parameters_identity,
        "configuration": {
            "maximum_position_error_m": config.maximum_position_error_m,
            "candidate_primitives": [
                primitive.value for primitive in config.candidate_primitives
            ],
            "floating_comparison_guard_m": POSITION_COMPARISON_GUARD_M,
        },
        "trajectory_count": len(validations),
        "source_sample_count": sum(item.source_sample_count for item in validations),
        "valid_sample_count": sum(item.valid_sample_count for item in validations),
        "invalid_sample_count": sum(item.invalid_sample_count for item in validations),
        "valid_run_count": sum(item.valid_run_count for item in validations),
        "replayed_valid_sample_count": sum(
            item.replayed_valid_sample_count for item in validations
        ),
        "invalid_gap_samples_with_state": sum(
            item.invalid_gap_samples_with_state for item in validations
        ),
        "run_endpoints_exact": all(item.run_endpoints_exact for item in validations),
        "retained_breakpoints_exact": all(
            item.retained_breakpoints_exact for item in validations
        ),
        "primitive_segment_counts": primitive_counts,
        "primitive_segment_shares": {
            primitive: count / tape.segment_count
            for primitive, count in primitive_counts.items()
        },
        "trajectories_using_cubic_hermite": sum(
            any(
                segment.primitive_type is ProceduralPrimitiveType.CUBIC_HERMITE
                for segment in track.segments
            )
            for track in tape.tracks
        ),
        "segment_count": tape.segment_count,
        "position_error_m": _error_statistics(position_errors),
        "heading_error_rad": _error_statistics(heading_errors),
        "velocity_error_mps": _error_statistics(velocity_errors),
    }


def verify_hermite_artifacts(
    repository_root: Path,
    artifacts: ProceduralTapeArtifacts,
    trajectories: Sequence[Trajectory],
    config: HermiteCodecConfig = _DEFAULT_CONFIG,
    *,
    expected_tape: ProceduralTape | None = None,
) -> tuple[ProceduralTape, dict[str, object]]:
    """Verify artifact integrity and the Hermite source-timestamp contract."""
    tape = verify_procedural_tape_artifacts(
        repository_root,
        artifacts,
        expected_tape=expected_tape,
    )
    try:
        summary = summarize_hermite_tape(tape, trajectories, config)
    except KeyError as error:
        raise ArtifactError("artifact tape references an unknown trajectory") from error
    return tape, summary
