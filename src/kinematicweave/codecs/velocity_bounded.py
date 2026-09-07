"""Deterministic position-and-velocity-bounded hybrid trajectory codec."""

from collections.abc import Sequence
from dataclasses import dataclass
import math
from pathlib import Path
import time
from typing import cast

import numpy as np

from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.piecewise_linear import (
    POSITION_COMPARISON_GUARD_M,
    PiecewiseLinearCodecConfig,
    ReplayValidationSummary,
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
    "VELOCITY_COMPARISON_GUARD_MPS",
    "EncodingPerformanceStats",
    "VelocityBoundedCodecConfig",
    "encode_canonical_parquet_velocity_bounded",
    "encode_scenario_velocity_bounded",
    "encode_scenario_velocity_bounded_with_performance",
    "encode_trajectory_velocity_bounded",
    "encode_trajectory_velocity_bounded_with_performance",
    "summarize_velocity_bounded_tape",
    "validate_velocity_bounded_replay",
    "velocity_bounded_encoder_parameters_identity",
    "verify_velocity_bounded_artifacts",
]

ENCODER_NAME = "minimum_segment_position_velocity_bounded"
ENCODER_VERSION = "1.0"
ALGORITHM_VERSION = "vectorized-candidate-cache-dp-v1"
VELOCITY_COMPARISON_GUARD_MPS = 1.0e-12
_DISTANCE_POLICY = "xy_when_all_z_absent_else_xyz_when_all_z_present"
_GAP_POLICY = "invalid_samples_terminate_runs_no_bridging"
_ELEVATION_POLICY = "candidate_intervals_must_have_uniform_z_availability"
_REPLAY_POLICY = (
    "hold_and_linear_established_optional_velocity_cubic_hermite_analytic_"
    "velocity_exact_stored_endpoints"
)
_OBJECTIVE = (
    "minimum_segments_then_total_squared_position_error_then_total_squared_"
    "velocity_error_then_later_breakpoint_recursively_then_primitive_order_"
    "then_source_order"
)
_REQUIRED_PRIMITIVES = frozenset(
    (
        ProceduralPrimitiveType.HOLD,
        ProceduralPrimitiveType.LINEAR,
        ProceduralPrimitiveType.CUBIC_HERMITE,
    )
)


@dataclass(frozen=True, slots=True)
class VelocityBoundedCodecConfig:
    """Fixed dual-bound candidate and optimization configuration."""

    maximum_position_error_m: float = 0.10
    maximum_velocity_error_mps: float = 1.00
    candidate_primitives: tuple[ProceduralPrimitiveType, ...] = (
        ProceduralPrimitiveType.HOLD,
        ProceduralPrimitiveType.LINEAR,
        ProceduralPrimitiveType.CUBIC_HERMITE,
    )

    def __post_init__(self) -> None:
        for field_name in (
            "maximum_position_error_m",
            "maximum_velocity_error_mps",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValidationError(f"{field_name} must be numeric")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValidationError(f"{field_name} must be finite")
            if normalized < 0.0:
                raise ValidationError(f"{field_name} must not be negative")
            object.__setattr__(self, field_name, normalized)
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
        object.__setattr__(self, "candidate_primitives", primitives)


_DEFAULT_CONFIG = VelocityBoundedCodecConfig()


@dataclass(frozen=True, slots=True)
class EncodingPerformanceStats:
    """Measured candidate-cache and dynamic-programming work."""

    primitive_interval_count: int = 0
    primitive_contract_rejection_count: int = 0
    position_rejection_count: int = 0
    velocity_rejection_count: int = 0
    numeric_rejection_count: int = 0
    accepted_candidate_count: int = 0
    dynamic_programming_transition_count: int = 0
    candidate_generation_seconds: float = 0.0
    dynamic_programming_seconds: float = 0.0

    def __add__(self, other: object) -> "EncodingPerformanceStats":
        if not isinstance(other, EncodingPerformanceStats):
            return NotImplemented
        return EncodingPerformanceStats(
            primitive_interval_count=(
                self.primitive_interval_count + other.primitive_interval_count
            ),
            primitive_contract_rejection_count=(
                self.primitive_contract_rejection_count
                + other.primitive_contract_rejection_count
            ),
            position_rejection_count=(
                self.position_rejection_count + other.position_rejection_count
            ),
            velocity_rejection_count=(
                self.velocity_rejection_count + other.velocity_rejection_count
            ),
            numeric_rejection_count=(
                self.numeric_rejection_count + other.numeric_rejection_count
            ),
            accepted_candidate_count=(
                self.accepted_candidate_count + other.accepted_candidate_count
            ),
            dynamic_programming_transition_count=(
                self.dynamic_programming_transition_count
                + other.dynamic_programming_transition_count
            ),
            candidate_generation_seconds=(
                self.candidate_generation_seconds + other.candidate_generation_seconds
            ),
            dynamic_programming_seconds=(
                self.dynamic_programming_seconds + other.dynamic_programming_seconds
            ),
        )

    def to_dict(self) -> dict[str, int | float]:
        """Return stable JSON-ready performance counters."""
        return {
            "primitive_interval_count": self.primitive_interval_count,
            "primitive_contract_rejection_count": (
                self.primitive_contract_rejection_count
            ),
            "position_rejection_count": self.position_rejection_count,
            "velocity_rejection_count": self.velocity_rejection_count,
            "numeric_rejection_count": self.numeric_rejection_count,
            "accepted_candidate_count": self.accepted_candidate_count,
            "dynamic_programming_transition_count": (
                self.dynamic_programming_transition_count
            ),
            "candidate_generation_seconds": self.candidate_generation_seconds,
            "dynamic_programming_seconds": self.dynamic_programming_seconds,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    start_index: int
    end_index: int
    primitive_type: ProceduralPrimitiveType
    position_squared_error: float
    velocity_squared_error: float


def velocity_bounded_encoder_parameters_identity(
    config: VelocityBoundedCodecConfig,
) -> str:
    """Return the identity of every behavior-affecting codec parameter."""
    if not isinstance(config, VelocityBoundedCodecConfig):
        raise ValidationError("config must be a VelocityBoundedCodecConfig")
    return canonical_sha256(
        "minimum-segment-position-velocity-bounded-parameters",
        {
            "algorithm_version": ALGORITHM_VERSION,
            "candidate_primitives": [
                primitive.value for primitive in config.candidate_primitives
            ],
            "distance_policy": _DISTANCE_POLICY,
            "elevation_policy": _ELEVATION_POLICY,
            "gap_policy": _GAP_POLICY,
            "maximum_position_error_m": config.maximum_position_error_m,
            "maximum_velocity_error_mps": config.maximum_velocity_error_mps,
            "objective": _OBJECTIVE,
            "position_comparison_guard_m": POSITION_COMPARISON_GUARD_M,
            "replay_policy": _REPLAY_POLICY,
            "velocity_comparison_guard_mps": VELOCITY_COMPARISON_GUARD_MPS,
        },
    )


def _optional_array(
    samples: Sequence[TrajectorySampleRecord],
    field_name: str,
) -> np.ndarray:
    return np.asarray(
        [
            math.nan
            if getattr(sample, field_name) is None
            else getattr(sample, field_name)
            for sample in samples
        ],
        dtype=np.float64,
    )


def _primitive_replay(
    primitive: ProceduralPrimitiveType,
    u: np.ndarray,
    duration_seconds: np.ndarray,
    start_x: float,
    start_y: float,
    end_x: np.ndarray,
    end_y: np.ndarray,
    start_vx: float,
    start_vy: float,
    end_vx: np.ndarray,
    end_vy: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if primitive is ProceduralPrimitiveType.HOLD:
        replay_x = np.full_like(u, start_x)
        replay_y = np.full_like(u, start_y)
        replay_vx = start_vx + (end_vx[:, None] - start_vx) * u
        replay_vy = start_vy + (end_vy[:, None] - start_vy) * u
        return replay_x, replay_y, replay_vx, replay_vy
    if primitive is ProceduralPrimitiveType.LINEAR:
        replay_x = start_x + (end_x[:, None] - start_x) * u
        replay_y = start_y + (end_y[:, None] - start_y) * u
        replay_vx = start_vx + (end_vx[:, None] - start_vx) * u
        replay_vy = start_vy + (end_vy[:, None] - start_vy) * u
        return replay_x, replay_y, replay_vx, replay_vy
    squared = u * u
    cubed = squared * u
    h00 = 2.0 * cubed - 3.0 * squared + 1.0
    h10 = cubed - 2.0 * squared + u
    h01 = -2.0 * cubed + 3.0 * squared
    h11 = cubed - squared
    dh00 = 6.0 * squared - 6.0 * u
    dh10 = 3.0 * squared - 4.0 * u + 1.0
    dh01 = -6.0 * squared + 6.0 * u
    dh11 = 3.0 * squared - 2.0 * u
    start_tx = duration_seconds * start_vx
    start_ty = duration_seconds * start_vy
    end_tx = duration_seconds * end_vx[:, None]
    end_ty = duration_seconds * end_vy[:, None]
    replay_x = h00 * start_x + h10 * start_tx + h01 * end_x[:, None] + h11 * end_tx
    replay_y = h00 * start_y + h10 * start_ty + h01 * end_y[:, None] + h11 * end_ty
    replay_vx = (
        dh00 * start_x + dh10 * start_tx + dh01 * end_x[:, None] + dh11 * end_tx
    ) / duration_seconds
    replay_vy = (
        dh00 * start_y + dh10 * start_ty + dh01 * end_y[:, None] + dh11 * end_ty
    ) / duration_seconds
    return replay_x, replay_y, replay_vx, replay_vy


def _candidate_cache(
    samples: Sequence[TrajectorySampleRecord],
    config: VelocityBoundedCodecConfig,
) -> tuple[list[list[_Candidate]], EncodingPerformanceStats]:
    count = len(samples)
    candidates_by_start: list[list[_Candidate]] = [[] for _ in range(count)]
    if count == 1:
        candidates_by_start[0].append(
            _Candidate(
                start_index=0,
                end_index=0,
                primitive_type=ProceduralPrimitiveType.HOLD,
                position_squared_error=0.0,
                velocity_squared_error=0.0,
            )
        )
        return candidates_by_start, EncodingPerformanceStats(
            primitive_interval_count=1,
            accepted_candidate_count=1,
        )

    timestamps = np.asarray(
        [sample.timestamp_ns for sample in samples],
        dtype=np.int64,
    )
    x_values = np.asarray([sample.x_m for sample in samples], dtype=np.float64)
    y_values = np.asarray([sample.y_m for sample in samples], dtype=np.float64)
    z_values = _optional_array(samples, "z_m")
    vx_values = _optional_array(samples, "velocity_x_mps")
    vy_values = _optional_array(samples, "velocity_y_mps")
    source_velocity_available = np.isfinite(vx_values) & np.isfinite(vy_values)
    has_elevation = samples[0].z_m is not None

    primitive_intervals = 0
    contract_rejections = 0
    position_rejections = 0
    velocity_rejections = 0
    numeric_rejections = 0
    accepted_candidates = 0
    started = time.perf_counter()
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        for start_index in range(count - 1):
            end_indices = np.arange(start_index + 1, count)
            column_indices = np.arange(start_index, count)
            interval_mask = column_indices[None, :] <= end_indices[:, None]
            interior_mask = (
                interval_mask
                & (column_indices[None, :] > start_index)
                & (column_indices[None, :] < end_indices[:, None])
            )
            duration_ns = timestamps[end_indices] - timestamps[start_index]
            if np.any(duration_ns <= 0):
                raise ValidationError("candidate segment timestamps must increase")
            duration_seconds = duration_ns[:, None] / 1_000_000_000.0
            u = np.where(
                interval_mask,
                (timestamps[column_indices][None, :] - timestamps[start_index])
                / duration_ns[:, None],
                0.0,
            )
            endpoint_velocity_complete = (
                source_velocity_available[start_index]
                & source_velocity_available[end_indices]
            )
            source_velocity_mask = source_velocity_available[column_indices][None, :]
            same_position = (
                (x_values[column_indices] == x_values[start_index])
                & (y_values[column_indices] == y_values[start_index])
                & (
                    True
                    if not has_elevation
                    else z_values[column_indices] == z_values[start_index]
                )
            )
            hold_contract = np.logical_and.accumulate(same_position)[
                end_indices - start_index
            ]

            for primitive in config.candidate_primitives:
                row_count = len(end_indices)
                primitive_intervals += row_count
                if primitive is ProceduralPrimitiveType.HOLD:
                    contract_valid = hold_contract.copy()
                elif primitive is ProceduralPrimitiveType.CUBIC_HERMITE:
                    contract_valid = endpoint_velocity_complete.copy()
                    tangent_values = np.column_stack(
                        (
                            duration_seconds[:, 0] * vx_values[start_index],
                            duration_seconds[:, 0] * vy_values[start_index],
                            duration_seconds[:, 0] * vx_values[end_indices],
                            duration_seconds[:, 0] * vy_values[end_indices],
                        )
                    )
                    contract_valid &= np.all(np.isfinite(tangent_values), axis=1)
                else:
                    contract_valid = np.ones(row_count, dtype=np.bool_)
                contract_rejections += int(np.count_nonzero(~contract_valid))

                replay_x, replay_y, replay_vx, replay_vy = _primitive_replay(
                    primitive,
                    u,
                    duration_seconds,
                    x_values[start_index],
                    y_values[start_index],
                    x_values[end_indices],
                    y_values[end_indices],
                    vx_values[start_index],
                    vy_values[start_index],
                    vx_values[end_indices],
                    vy_values[end_indices],
                )
                position_squared = (
                    replay_x - x_values[column_indices][None, :]
                ) ** 2 + (replay_y - y_values[column_indices][None, :]) ** 2
                if has_elevation:
                    replay_z = (
                        z_values[start_index]
                        + (z_values[end_indices, None] - z_values[start_index]) * u
                    )
                    position_squared += (
                        replay_z - z_values[column_indices][None, :]
                    ) ** 2
                finite_position = np.all(
                    np.where(interior_mask, np.isfinite(position_squared), True),
                    axis=1,
                )
                maximum_position_error = np.sqrt(
                    np.max(np.where(interior_mask, position_squared, 0.0), axis=1)
                )
                within_position = maximum_position_error <= (
                    config.maximum_position_error_m + POSITION_COMPARISON_GUARD_M
                )
                numeric_bad = contract_valid & ~finite_position
                numeric_rejections += int(np.count_nonzero(numeric_bad))
                position_bad = contract_valid & finite_position & ~within_position
                position_rejections += int(np.count_nonzero(position_bad))
                position_valid = contract_valid & finite_position & within_position

                represented_velocity_available = endpoint_velocity_complete[:, None]
                missing_represented_velocity = (
                    interior_mask
                    & source_velocity_mask
                    & ~represented_velocity_available
                )
                velocity_evaluation_mask = (
                    interior_mask
                    & source_velocity_mask
                    & represented_velocity_available
                )
                velocity_squared = (
                    replay_vx - vx_values[column_indices][None, :]
                ) ** 2 + (replay_vy - vy_values[column_indices][None, :]) ** 2
                finite_velocity = np.all(
                    np.where(
                        velocity_evaluation_mask,
                        np.isfinite(velocity_squared),
                        True,
                    ),
                    axis=1,
                )
                maximum_velocity_error = np.sqrt(
                    np.max(
                        np.where(velocity_evaluation_mask, velocity_squared, 0.0),
                        axis=1,
                    )
                )
                velocity_valid = (
                    ~np.any(missing_represented_velocity, axis=1)
                    & finite_velocity
                    & (
                        maximum_velocity_error
                        <= (
                            config.maximum_velocity_error_mps
                            + VELOCITY_COMPARISON_GUARD_MPS
                        )
                    )
                )
                numeric_velocity_bad = position_valid & ~finite_velocity
                numeric_rejections += int(np.count_nonzero(numeric_velocity_bad))
                velocity_bad = position_valid & finite_velocity & ~velocity_valid
                velocity_rejections += int(np.count_nonzero(velocity_bad))
                accepted = position_valid & velocity_valid
                accepted_candidates += int(np.count_nonzero(accepted))
                position_sums = np.sum(
                    np.where(interior_mask, position_squared, 0.0),
                    axis=1,
                )
                velocity_sums = np.sum(
                    np.where(velocity_evaluation_mask, velocity_squared, 0.0),
                    axis=1,
                )
                for row_index in np.flatnonzero(accepted):
                    candidates_by_start[start_index].append(
                        _Candidate(
                            start_index=start_index,
                            end_index=int(end_indices[row_index]),
                            primitive_type=primitive,
                            position_squared_error=float(position_sums[row_index]),
                            velocity_squared_error=float(velocity_sums[row_index]),
                        )
                    )
    elapsed = time.perf_counter() - started
    return candidates_by_start, EncodingPerformanceStats(
        primitive_interval_count=primitive_intervals,
        primitive_contract_rejection_count=contract_rejections,
        position_rejection_count=position_rejections,
        velocity_rejection_count=velocity_rejections,
        numeric_rejection_count=numeric_rejections,
        accepted_candidate_count=accepted_candidates,
        candidate_generation_seconds=elapsed,
    )


def _minimum_candidates(
    samples: Sequence[TrajectorySampleRecord],
    config: VelocityBoundedCodecConfig,
) -> tuple[tuple[_Candidate, ...], EncodingPerformanceStats]:
    candidates_by_start, generation_stats = _candidate_cache(samples, config)
    if len(samples) == 1:
        return (candidates_by_start[0][0],), generation_stats
    primitive_rank = {
        primitive: rank for rank, primitive in enumerate(config.candidate_primitives)
    }
    best: list[tuple[_Candidate, ...] | None] = [None] * len(samples)
    best[-1] = ()
    transitions = 0
    started = time.perf_counter()
    for start_index in range(len(samples) - 2, -1, -1):
        best_key: tuple[object, ...] | None = None
        best_path: tuple[_Candidate, ...] | None = None
        for candidate in candidates_by_start[start_index]:
            suffix = best[candidate.end_index]
            if suffix is None:
                continue
            transitions += 1
            path = (candidate, *suffix)
            key = (
                len(path),
                math.fsum(item.position_squared_error for item in path),
                math.fsum(item.velocity_squared_error for item in path),
                tuple(-item.end_index for item in path[:-1]),
                tuple(primitive_rank[item.primitive_type] for item in path),
            )
            if best_key is None or key < best_key:
                best_key = key
                best_path = path
        best[start_index] = best_path
    elapsed = time.perf_counter() - started
    if best[0] is None:
        raise ValidationError("no dual-bound segmentation exists")
    return best[0], generation_stats + EncodingPerformanceStats(
        dynamic_programming_transition_count=transitions,
        dynamic_programming_seconds=elapsed,
    )


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
    segment_id = _identifier(
        "procedural-segment",
        "position-velocity-bounded-segment",
        {
            "parameter_identity": parameter_identity,
            "procedural_track_id": procedural_track_id,
            "run_index": run_index,
            "segment_index": segment_index,
            "source_start_sample_index": start.sample_index,
            "source_end_sample_index": end.sample_index,
            "start_time_ns": start.timestamp_ns,
            "end_time_ns": end.timestamp_ns,
            "primitive_type": candidate.primitive_type.value,
        },
    )
    return ProceduralSegment(
        tape_id=tape_id,
        procedural_track_id=procedural_track_id,
        segment_id=segment_id,
        run_index=run_index,
        segment_index=segment_index,
        primitive_type=candidate.primitive_type,
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


def _encode_trajectory(
    trajectory: Trajectory,
    agent_class: AgentClass | str,
    config: VelocityBoundedCodecConfig,
    *,
    tape_id: str | None,
) -> tuple[ProceduralTrack, EncodingPerformanceStats]:
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be a Trajectory")
    if not isinstance(config, VelocityBoundedCodecConfig):
        raise ValidationError("config must be a VelocityBoundedCodecConfig")
    normalized_class = _normalized_agent_class(agent_class)
    runs = _valid_runs(trajectory.samples)
    if not runs:
        raise ValidationError("trajectory has no valid samples to encode")
    parameter_identity = velocity_bounded_encoder_parameters_identity(config)
    track_identity_payload = {
        "scenario_id": trajectory.scenario_id,
        "agent_id": trajectory.agent_id,
        "trajectory_id": trajectory.trajectory_id,
        "parameters": parameter_identity,
        "samples": [_sample_identity(sample) for sample in trajectory.samples],
    }
    procedural_track_id = _identifier(
        "procedural-track",
        "position-velocity-bounded-track",
        track_identity_payload,
    )
    normalized_tape_id = tape_id or _identifier(
        "tape",
        "position-velocity-bounded-standalone-tape",
        track_identity_payload,
    )
    segments: list[ProceduralSegment] = []
    performance = EncodingPerformanceStats()
    for run_index, run in enumerate(runs):
        segment_index = 0
        for chunk in _elevation_chunks(run):
            candidates, chunk_performance = _minimum_candidates(chunk, config)
            performance += chunk_performance
            for candidate in candidates:
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
    track = ProceduralTrack(
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
    return track, performance


def encode_trajectory_velocity_bounded(
    trajectory: Trajectory,
    agent_class: AgentClass | str,
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
    *,
    tape_id: str | None = None,
) -> ProceduralTrack:
    """Encode one trajectory with deterministic dual-bound dynamic programming."""
    return _encode_trajectory(
        trajectory,
        agent_class,
        config,
        tape_id=tape_id,
    )[0]


def encode_trajectory_velocity_bounded_with_performance(
    trajectory: Trajectory,
    agent_class: AgentClass | str,
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
    *,
    tape_id: str | None = None,
) -> tuple[ProceduralTrack, EncodingPerformanceStats]:
    """Encode one trajectory and return measured candidate/DP diagnostics."""
    return _encode_trajectory(trajectory, agent_class, config, tape_id=tape_id)


def _encode_scenario(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
    config: VelocityBoundedCodecConfig,
    *,
    source_validation_report_identity: str | None,
) -> tuple[ProceduralTape, EncodingPerformanceStats]:
    if not isinstance(config, VelocityBoundedCodecConfig):
        raise ValidationError("config must be a VelocityBoundedCodecConfig")
    validate_scenario_bundle(scenario, coordinate_frame, agents, trajectories)
    ordered = tuple(
        sorted(trajectories, key=lambda item: (item.scenario_id, item.trajectory_id))
    )
    agents_by_id = {agent.agent_id: agent for agent in agents}
    parameter_identity = velocity_bounded_encoder_parameters_identity(config)
    tape_id = _identifier(
        "tape",
        "position-velocity-bounded-scenario-tape",
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
    tracks: list[ProceduralTrack] = []
    performance = EncodingPerformanceStats()
    for trajectory in ordered:
        track, track_performance = _encode_trajectory(
            trajectory,
            agents_by_id[trajectory.agent_id].agent_class,
            config,
            tape_id=tape_id,
        )
        tracks.append(track)
        performance += track_performance
    tape = ProceduralTape(
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
        tracks=tuple(tracks),
    )
    return tape, performance


def encode_scenario_velocity_bounded(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
    *,
    source_validation_report_identity: str | None,
) -> ProceduralTape:
    """Encode one validated scenario with both hard source-timestamp bounds."""
    return _encode_scenario(
        scenario,
        coordinate_frame,
        agents,
        trajectories,
        config,
        source_validation_report_identity=source_validation_report_identity,
    )[0]


def encode_scenario_velocity_bounded_with_performance(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
    *,
    source_validation_report_identity: str | None,
) -> tuple[ProceduralTape, EncodingPerformanceStats]:
    """Encode one scenario and return candidate-generation and DP diagnostics."""
    return _encode_scenario(
        scenario,
        coordinate_frame,
        agents,
        trajectories,
        config,
        source_validation_report_identity=source_validation_report_identity,
    )


def encode_canonical_parquet_velocity_bounded(
    repository_root: Path,
    *,
    scenario_paths: Sequence[str | Path],
    coordinate_frame_paths: Sequence[str | Path],
    agent_paths: Sequence[str | Path],
    trajectory_sample_paths: Sequence[str | Path],
    source_validation_report_identity: str | None,
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
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
    return encode_scenario_velocity_bounded(
        ScenarioRecord(**scenario_rows[0]),  # type: ignore[arg-type]
        CoordinateFrameRecord(**frame_rows[0]),  # type: ignore[arg-type]
        agents,
        trajectories,
        config,
        source_validation_report_identity=source_validation_report_identity,
    )


def validate_velocity_bounded_replay(
    track: ProceduralTrack,
    trajectory: Trajectory,
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
) -> ReplayValidationSummary:
    """Verify support, endpoint fidelity, and both source-timestamp bounds."""
    if not isinstance(config, VelocityBoundedCodecConfig):
        raise ValidationError("config must be a VelocityBoundedCodecConfig")
    summary = validate_piecewise_linear_replay(
        track,
        trajectory,
        PiecewiseLinearCodecConfig(config.maximum_position_error_m),
    )
    from kinematicweave.codecs.exact import replay_track

    for sample in trajectory.samples:
        if (
            not sample.is_valid
            or sample.velocity_x_mps is None
            or sample.velocity_y_mps is None
        ):
            continue
        state = replay_track(track, sample.timestamp_ns)
        if state is None:
            raise ValidationError("valid source timestamp has no replay state")
        if state.velocity_x_mps is None or state.velocity_y_mps is None:
            raise ValidationError("replay omits represented source velocity")
        error = math.hypot(
            state.velocity_x_mps - sample.velocity_x_mps,
            state.velocity_y_mps - sample.velocity_y_mps,
        )
        if error > (config.maximum_velocity_error_mps + VELOCITY_COMPARISON_GUARD_MPS):
            raise ValidationError("replay exceeds maximum_velocity_error_mps")
    return summary


def summarize_velocity_bounded_tape(
    tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
    *,
    performance: EncodingPerformanceStats | None = None,
) -> dict[str, object]:
    """Return deterministic codec, primitive, error, and optional work metrics."""
    if tape.encoder_name != ENCODER_NAME or tape.encoder_version != ENCODER_VERSION:
        raise ValidationError("tape encoder identity differs")
    if tape.encoder_parameters_identity != (
        velocity_bounded_encoder_parameters_identity(config)
    ):
        raise ValidationError("tape encoder parameter identity differs")
    source_by_id = {trajectory.trajectory_id: trajectory for trajectory in trajectories}
    if len(source_by_id) != len(trajectories):
        raise ValidationError("trajectory identifiers must be unique")
    validations = tuple(
        validate_velocity_bounded_replay(
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
    primitive_counts = {
        primitive.value: sum(
            segment.primitive_type is primitive
            for track in tape.tracks
            for segment in track.segments
        )
        for primitive in config.candidate_primitives
    }
    value: dict[str, object] = {
        "encoder_name": tape.encoder_name,
        "encoder_version": tape.encoder_version,
        "encoder_parameters_identity": tape.encoder_parameters_identity,
        "configuration": {
            "maximum_position_error_m": config.maximum_position_error_m,
            "maximum_velocity_error_mps": config.maximum_velocity_error_mps,
            "candidate_primitives": [
                primitive.value for primitive in config.candidate_primitives
            ],
            "position_comparison_guard_m": POSITION_COMPARISON_GUARD_M,
            "velocity_comparison_guard_mps": VELOCITY_COMPARISON_GUARD_MPS,
        },
        "trajectory_count": len(validations),
        "source_sample_count": sum(item.source_sample_count for item in validations),
        "valid_sample_count": sum(item.valid_sample_count for item in validations),
        "invalid_sample_count": sum(item.invalid_sample_count for item in validations),
        "valid_run_count": sum(item.valid_run_count for item in validations),
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
        "trajectories_using_primitives": {
            primitive.value: sum(
                any(segment.primitive_type is primitive for segment in track.segments)
                for track in tape.tracks
            )
            for primitive in config.candidate_primitives
        },
        "segment_count": tape.segment_count,
        "position_error_m": _error_statistics(position_errors),
        "heading_error_rad": _error_statistics(heading_errors),
        "velocity_error_mps": _error_statistics(velocity_errors),
    }
    if performance is not None:
        value["performance"] = performance.to_dict()
    return value


def verify_velocity_bounded_artifacts(
    repository_root: Path,
    artifacts: ProceduralTapeArtifacts,
    trajectories: Sequence[Trajectory],
    config: VelocityBoundedCodecConfig = _DEFAULT_CONFIG,
    *,
    expected_tape: ProceduralTape | None = None,
) -> tuple[ProceduralTape, dict[str, object]]:
    """Verify artifact integrity, identity, references, and both hard bounds."""
    tape = verify_procedural_tape_artifacts(
        repository_root,
        artifacts,
        expected_tape=expected_tape,
    )
    try:
        summary = summarize_velocity_bounded_tape(tape, trajectories, config)
    except KeyError as error:
        raise ArtifactError("artifact tape references an unknown trajectory") from error
    return tape, summary
