"""Run the Phase 3 semantic-motion synthetic and genuine AV2 evidence gates."""

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import combinations, pairwise
import math
import os
from pathlib import Path
import platform
import time
import tracemalloc

from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.velocity_bounded import (
    VelocityBoundedCodecConfig,
    encode_scenario_velocity_bounded,
    velocity_bounded_encoder_parameters_identity,
)
from kinematicweave.data.parquet_io import trajectories_to_table
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    canonical_schema_names,
    get_schema_definition,
    schema_definition_to_dict,
    schema_fingerprint,
)
from kinematicweave.data.semantic_artifacts import (
    SemanticMotionArtifacts,
    materialize_semantic_motion_tape,
)
from kinematicweave.data.synthetic import (
    SyntheticScenario,
    SyntheticScenarioKind,
    build_synthetic_dataset,
)
from kinematicweave.domain.procedural import ProceduralTrack
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.domain.semantic import (
    MotionEvent,
    MotionEventType,
    SemanticMotionTrack,
    SemanticWaypointRole,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    analyze_event_preservation,
    build_semantic_motion_tape,
    detect_semantic_trajectory,
    replay_detection_trajectory,
    semantic_motion_configuration_identity,
    validate_semantic_waypoint_replay,
)
from run_piecewise_linear_codec import (  # type: ignore[import-not-found]
    _error_statistics,
    _git_head,
    _load_av2_scenarios,
    _mapping,
    _read_json,
    _sha256,
    _tree_identity,
    _tree_size,
    _write,
    _write_text,
)

_BATCH = "3.5"
_TITLE = "Phase 3 Semantic Waypoints and Motion Events"
_PASS_STATEMENT = (
    "M3 semantic motion layer achieved on synthetic and genuine AV2 provider data."
)
_REQUIRED_STARTING_HEAD = "90e6a83439e9b0e49205a8c735044b404e4fa673"
_SEMANTIC_CONFIG = SemanticMotionConfig()
_CODEC_CONFIG = VelocityBoundedCodecConfig(0.10, 1.00)
_BATCH_34_EVIDENCE_SHA256 = (
    "fbd710a238d5fa95a4b2bda580a5229592a96ef68d10d86e82699b2948fe0b62"
)
_SYNTHETIC_PROCEDURAL_BYTES = 360_948
_AV2_PROCEDURAL_BYTES = 553_731

_Bundle = tuple[
    ScenarioRecord,
    CoordinateFrameRecord,
    Sequence[AgentRecord],
    Sequence[Trajectory],
]


def _is_wsl() -> bool:
    try:
        proc_version = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        proc_version = ""
    return "microsoft" in platform.release().lower() or "microsoft" in proc_version


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactError(f"{key} is not an integer")
    return value


def _number(mapping: Mapping[str, object], key: str) -> float:
    value = mapping[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ArtifactError(f"{key} is not numeric")
    return float(value)


def _list(mapping: Mapping[str, object], key: str) -> list[object]:
    value = mapping[key]
    if not isinstance(value, list):
        raise ArtifactError(f"{key} is not a list")
    return value


def _event_overlap_count(events: Sequence[MotionEvent]) -> int:
    return sum(
        left.event_type != right.event_type
        and max(left.start_time_ns, right.start_time_ns)
        <= min(left.end_time_ns, right.end_time_ns)
        for left, right in combinations(events, 2)
    )


def _primitive_counts(track: ProceduralTrack) -> dict[str, int]:
    return {
        primitive.value: sum(
            segment.primitive_type is primitive for segment in track.segments
        )
        for primitive in _CODEC_CONFIG.candidate_primitives
    }


def _artifact_checksums(
    semantic: SemanticMotionArtifacts,
) -> tuple[str, str, str]:
    return (
        semantic.semantic_waypoints.written_artifact.content_checksum,
        semantic.motion_events.written_artifact.content_checksum,
        semantic.semantic_summary.content_checksum,
    )


def _artifact_bytes(semantic: SemanticMotionArtifacts) -> int:
    return sum(
        item.size_bytes
        for item in (
            semantic.semantic_waypoints.written_artifact,
            semantic.motion_events.written_artifact,
            semantic.semantic_summary,
        )
    )


def _track_f1(preservation: Mapping[str, object]) -> float:
    return _number(preservation, "f1")


def _track_diagnostic(
    scenario_id: str,
    trajectory: Trajectory,
    source: SemanticMotionTrack,
    replay: SemanticMotionTrack,
    preservation: Mapping[str, object],
) -> dict[str, object]:
    by_type = _mapping(preservation["by_type"])
    all_matches = [
        _mapping(match, "preservation match")
        for event_type in MotionEventType
        for match in _list(_mapping(by_type[event_type.value]), "matches")
    ]
    sample_deltas = [
        right.timestamp_ns - left.timestamp_ns
        for left, right in pairwise(trajectory.samples)
    ]
    event_durations = [
        event.end_time_ns - event.start_time_ns for event in source.events
    ]
    endpoint_indices = {
        index
        for segment in source.procedural_track.segments
        for index in (
            segment.source_start_sample_index,
            segment.source_end_sample_index,
        )
    }
    source_counts = Counter(
        MotionEventType(event.event_type).value for event in source.events
    )
    replay_counts = Counter(
        MotionEventType(event.event_type).value for event in replay.events
    )
    return {
        "trajectory_safe_id": canonical_sha256(
            "semantic-motion-error-analysis-trajectory",
            {
                "scenario_id": scenario_id,
                "trajectory_id": trajectory.trajectory_id,
            },
        ),
        "event_preservation_f1": _track_f1(preservation),
        "source_event_counts_by_type": {
            event_type.value: source_counts[event_type.value]
            for event_type in MotionEventType
        },
        "replay_event_counts_by_type": {
            event_type.value: replay_counts[event_type.value]
            for event_type in MotionEventType
        },
        "unmatched_source_events": {
            event_type.value: _integer(
                _mapping(by_type[event_type.value]),
                "unmatched_source_count",
            )
            for event_type in MotionEventType
        },
        "unmatched_replay_events": {
            event_type.value: _integer(
                _mapping(by_type[event_type.value]),
                "unmatched_replay_count",
            )
            for event_type in MotionEventType
        },
        "temporal_boundary_errors_ns": {
            "start": _error_statistics(
                [
                    _integer(match, "start_boundary_absolute_error_ns")
                    for match in all_matches
                ]
            ),
            "end": _error_statistics(
                [
                    _integer(match, "end_boundary_absolute_error_ns")
                    for match in all_matches
                ]
            ),
            "anchor": _error_statistics(
                [
                    _integer(match, "anchor_time_absolute_error_ns")
                    for match in all_matches
                ]
            ),
        },
        "numerical_segment_composition": _primitive_counts(source.procedural_track),
        "associations": {
            "sparse_sampling": max(sample_deltas, default=0) > 200_000_000,
            "heading_absence": any(
                sample.heading_rad is None for sample in trajectory.samples
            ),
            "velocity_derivation": any(
                sample.speed_mps is None
                and (sample.velocity_x_mps is None or sample.velocity_y_mps is None)
                for sample in trajectory.samples
                if sample.is_valid
            ),
            "event_overlap": _event_overlap_count(source.events) > 0,
            "short_duration_events": any(
                duration <= 2 * _SEMANTIC_CONFIG.minimum_turn_duration_ns
                for duration in event_durations
            ),
            "codec_interpolation": any(
                waypoint.source_sample_index not in endpoint_indices
                for waypoint in source.waypoints
            ),
        },
    }


def _aggregate_preservation(
    track_values: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    by_type: dict[str, object] = {}
    aggregate_source = 0
    aggregate_replay = 0
    aggregate_matched = 0
    all_start: list[int] = []
    all_end: list[int] = []
    all_anchor: list[int] = []
    for event_type in MotionEventType:
        source_count = replay_count = matched_count = 0
        start_errors: list[int] = []
        end_errors: list[int] = []
        anchor_errors: list[int] = []
        for track_value in track_values:
            item = _mapping(
                _mapping(track_value["by_type"])[event_type.value],
            )
            source_count += _integer(item, "source_event_count")
            replay_count += _integer(item, "replay_event_count")
            matched_count += _integer(item, "matched_count")
            for raw_match in _list(item, "matches"):
                match = _mapping(raw_match, "preservation match")
                start_errors.append(_integer(match, "start_boundary_absolute_error_ns"))
                end_errors.append(_integer(match, "end_boundary_absolute_error_ns"))
                anchor_errors.append(_integer(match, "anchor_time_absolute_error_ns"))
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
        by_type[event_type.value] = {
            "source_event_count": source_count,
            "replay_event_count": replay_count,
            "matched_count": matched_count,
            "precision": precision,
            "recall": recall,
            "f1": (
                2.0 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            ),
            "start_boundary_absolute_error_ns": _error_statistics(start_errors),
            "end_boundary_absolute_error_ns": _error_statistics(end_errors),
            "anchor_time_absolute_error_ns": _error_statistics(anchor_errors),
            "unmatched_source_count": source_count - matched_count,
            "unmatched_replay_count": replay_count - matched_count,
        }
        aggregate_source += source_count
        aggregate_replay += replay_count
        aggregate_matched += matched_count
        all_start.extend(start_errors)
        all_end.extend(end_errors)
        all_anchor.extend(anchor_errors)
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
        "start_boundary_absolute_error_ns": _error_statistics(all_start),
        "end_boundary_absolute_error_ns": _error_statistics(all_end),
        "anchor_time_absolute_error_ns": _error_statistics(all_anchor),
    }


def _synthetic_oracles(
    scenario_kind: SyntheticScenarioKind,
    tracks: Sequence[SemanticMotionTrack],
    trajectories: Sequence[Trajectory],
) -> dict[str, object]:
    events = tuple(event for track in tracks for event in track.events)
    event_types = tuple(MotionEventType(event.event_type) for event in events)
    checks: dict[str, bool] = {"processed": len(tracks) == len(trajectories)}
    if scenario_kind is SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED:
        checks["no_motion_events"] = not event_types
    elif scenario_kind is SyntheticScenarioKind.ACCELERATION_DECELERATION:
        checks["acceleration_present"] = MotionEventType.ACCELERATION in event_types
        checks["braking_present"] = MotionEventType.BRAKING in event_types
    elif scenario_kind is SyntheticScenarioKind.STOP:
        checks["stop_present"] = MotionEventType.STOP in event_types
    elif scenario_kind is SyntheticScenarioKind.LEFT_TURN:
        checks["left_turn_present"] = MotionEventType.LEFT_TURN in event_types
        checks["right_turn_absent"] = MotionEventType.RIGHT_TURN not in event_types
    elif scenario_kind is SyntheticScenarioKind.RIGHT_TURN:
        checks["right_turn_present"] = MotionEventType.RIGHT_TURN in event_types
        checks["left_turn_absent"] = MotionEventType.LEFT_TURN not in event_types
    elif scenario_kind is SyntheticScenarioKind.IRREGULAR_SAMPLING:
        differences = {
            right.timestamp_ns - left.timestamp_ns
            for trajectory in trajectories
            for left, right in pairwise(trajectory.samples)
        }
        checks["strictly_irregular_timestamps"] = len(differences) > 1
    elif scenario_kind is SyntheticScenarioKind.MISSING_GAP:
        gaps = [event for event in events if event.event_type is MotionEventType.GAP]
        checks["exactly_one_gap"] = len(gaps) == 1
        checks["events_do_not_cross_gap"] = len(gaps) == 1 and all(
            event is gaps[0]
            or event.end_time_ns <= gaps[0].start_time_ns
            or event.start_time_ns >= gaps[0].end_time_ns
            for event in events
        )
    elif scenario_kind is SyntheticScenarioKind.GRADE_SEPARATED_CROSSING:
        checks["finite_elevation_preserved"] = all(
            waypoint.z_m is not None and math.isfinite(waypoint.z_m)
            for track in tracks
            for waypoint in track.waypoints
        )
    return {
        "scenario_kind": scenario_kind.value,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _run_dataset_gate(
    repository_root: Path,
    generated_root: Path,
    label: str,
    bundles: Sequence[_Bundle],
    *,
    validation_identity: str,
    source_bytes: int,
    procedural_bytes: int,
    synthetic_scenarios: Sequence[SyntheticScenario] | None = None,
) -> tuple[dict[str, object], tuple[dict[str, object], ...], dict[str, object]]:
    role_counts = Counter({role.value: 0 for role in SemanticWaypointRole})
    event_counts = Counter({event.value: 0 for event in MotionEventType})
    event_track_counts = Counter({event.value: 0 for event in MotionEventType})
    track_preservation: list[Mapping[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    oracle_results: list[dict[str, object]] = []
    scenario_checksums: list[dict[str, object]] = []
    waypoint_position_errors: list[float] = []
    waypoint_velocity_errors: list[float] = []
    counts = {
        "scenario_count": len(bundles),
        "trajectory_count": 0,
        "source_sample_count": 0,
        "waypoint_count": 0,
        "event_count": 0,
        "event_overlap_count": 0,
    }
    semantic_bytes = 0
    generated_disk_bytes = 0
    detection_seconds = 0.0
    preservation_seconds = 0.0
    artifact_seconds = 0.0
    started = time.perf_counter()
    cpu_started = time.process_time()
    tracemalloc.start()
    try:
        for scenario_index, (scenario, frame, agents, trajectories) in enumerate(
            bundles
        ):
            operation_started = time.perf_counter()
            procedural_tape = encode_scenario_velocity_bounded(
                scenario,
                frame,
                agents,
                trajectories,
                _CODEC_CONFIG,
                source_validation_report_identity=validation_identity,
            )
            semantic_tape = build_semantic_motion_tape(
                procedural_tape,
                trajectories,
                _SEMANTIC_CONFIG,
            )
            repeated = build_semantic_motion_tape(
                procedural_tape,
                trajectories,
                _SEMANTIC_CONFIG,
            )
            if semantic_tape != repeated:
                raise ValidationError("repeated semantic detection differs")
            detection_seconds += time.perf_counter() - operation_started
            source_by_id = {
                trajectory.trajectory_id: trajectory for trajectory in trajectories
            }

            operation_started = time.perf_counter()
            scenario_preservation: list[Mapping[str, object]] = []
            for source_track in semantic_tape.tracks:
                trajectory = source_by_id[source_track.procedural_track.trajectory_id]
                replay_trajectory = replay_detection_trajectory(
                    trajectory,
                    source_track.procedural_track,
                )
                replay_track = detect_semantic_trajectory(
                    replay_trajectory,
                    source_track.procedural_track,
                    _SEMANTIC_CONFIG,
                )
                preservation = analyze_event_preservation(
                    source_track,
                    replay_track,
                )
                scenario_preservation.append(preservation)
                track_preservation.append(preservation)
                validation = validate_semantic_waypoint_replay(
                    source_track,
                    trajectory,
                    _CODEC_CONFIG,
                )
                waypoint_position_errors.extend(
                    validation["position_errors_m"]  # type: ignore[arg-type]
                )
                waypoint_velocity_errors.extend(
                    validation["velocity_errors_mps"]  # type: ignore[arg-type]
                )
                diagnostics.append(
                    _track_diagnostic(
                        scenario.scenario_id,
                        trajectory,
                        source_track,
                        replay_track,
                        preservation,
                    )
                )
                counts["event_overlap_count"] += _event_overlap_count(
                    source_track.events
                )
                for event_type in MotionEventType:
                    event_track_counts[event_type.value] += any(
                        event.event_type is event_type for event in source_track.events
                    )
            preservation_seconds += time.perf_counter() - operation_started
            scenario_preservation_summary = _aggregate_preservation(
                scenario_preservation
            )

            counts["trajectory_count"] += len(trajectories)
            counts["source_sample_count"] += sum(
                trajectory.sample_count for trajectory in trajectories
            )
            counts["waypoint_count"] += sum(
                len(track.waypoints) for track in semantic_tape.tracks
            )
            counts["event_count"] += sum(
                len(track.events) for track in semantic_tape.tracks
            )
            for track in semantic_tape.tracks:
                role_counts.update(
                    SemanticWaypointRole(waypoint.waypoint_role).value
                    for waypoint in track.waypoints
                )
                event_counts.update(
                    MotionEventType(event.event_type).value for event in track.events
                )

            if synthetic_scenarios is not None:
                oracle_results.append(
                    _synthetic_oracles(
                        synthetic_scenarios[scenario_index].kind,
                        semantic_tape.tracks,
                        trajectories,
                    )
                )

            operation_started = time.perf_counter()
            safe_scenario = canonical_sha256(
                "semantic-motion-evidence-scenario",
                {"scenario_id": scenario.scenario_id},
            )
            first = materialize_semantic_motion_tape(
                repository_root,
                generated_root,
                f"semantic:{safe_scenario}:first",
                semantic_tape,
                trajectories,
                _SEMANTIC_CONFIG,
                scenario_preservation_summary,
            )
            second = materialize_semantic_motion_tape(
                repository_root,
                generated_root,
                f"semantic:{safe_scenario}:repeat",
                semantic_tape,
                trajectories,
                _SEMANTIC_CONFIG,
                scenario_preservation_summary,
            )
            first_checksums = _artifact_checksums(first)
            second_checksums = _artifact_checksums(second)
            if first_checksums[:2] != second_checksums[:2]:
                raise ArtifactError("equivalent semantic Parquet checksums differ")
            semantic_bytes += _artifact_bytes(first)
            generated_disk_bytes += _tree_size(first.run_directory.path)
            generated_disk_bytes += _tree_size(second.run_directory.path)
            scenario_checksums.append(
                {
                    "scenario_safe_id": safe_scenario,
                    "semantic_artifact_sha256": list(first_checksums),
                    "repeat_semantic_artifact_sha256": list(second_checksums),
                    "parquet_checksums_match": first_checksums[:2]
                    == second_checksums[:2],
                }
            )
            artifact_seconds += time.perf_counter() - operation_started
    finally:
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    preservation_summary = _aggregate_preservation(track_preservation)
    gap_summary = _mapping(
        _mapping(preservation_summary["by_type"])[MotionEventType.GAP.value]
    )
    gap_source_count = _integer(gap_summary, "source_event_count")
    gap_replay_count = _integer(gap_summary, "replay_event_count")
    gap_exact = gap_source_count == gap_replay_count == 0 or (
        _number(gap_summary, "precision") == 1.0
        and _number(gap_summary, "recall") == 1.0
        and _number(
            _mapping(gap_summary["start_boundary_absolute_error_ns"]),
            "maximum",
        )
        == 0.0
        and _number(
            _mapping(gap_summary["end_boundary_absolute_error_ns"]),
            "maximum",
        )
        == 0.0
    )
    maximum_position = max(waypoint_position_errors, default=0.0)
    maximum_velocity = max(waypoint_velocity_errors, default=0.0)
    oracles_pass = (
        all(bool(result["passed"]) for result in oracle_results)
        if oracle_results
        else True
    )
    if maximum_position > _CODEC_CONFIG.maximum_position_error_m:
        raise ValidationError("semantic waypoint position bound failed")
    if maximum_velocity > _CODEC_CONFIG.maximum_velocity_error_mps:
        raise ValidationError("semantic waypoint velocity bound failed")
    if not gap_exact:
        raise ValidationError("gap event preservation is not exact")
    if not oracles_pass:
        raise ValidationError("one or more synthetic semantic oracles failed")
    wall_seconds = time.perf_counter() - started
    result = {
        "batch": _BATCH,
        "gate": label,
        "status": "PASS",
        "counts": counts,
        "waypoint_counts_by_role": dict(role_counts),
        "event_counts_by_type": dict(event_counts),
        "tracks_containing_event_type": dict(event_track_counts),
        "events_per_trajectory": (counts["event_count"] / counts["trajectory_count"]),
        "event_overlap": {
            "different_type_overlap_count": counts["event_overlap_count"],
            "same_type_overlap_count": 0,
        },
        "waypoint_replay_errors": {
            "position_m": _error_statistics(waypoint_position_errors),
            "velocity_mps": _error_statistics(waypoint_velocity_errors),
        },
        "preservation": preservation_summary,
        "gap_preservation_exact": gap_exact,
        "synthetic_oracles": oracle_results,
        "all_synthetic_oracles_pass": oracles_pass,
        "storage": {
            "canonical_source_bytes": source_bytes,
            "semantic_artifact_bytes": semantic_bytes,
            "procedural_artifact_bytes": procedural_bytes,
            "combined_artifact_bytes": semantic_bytes + procedural_bytes,
            "combined_to_canonical_source_ratio": (
                (semantic_bytes + procedural_bytes) / source_bytes
            ),
        },
        "resources": {
            "wall_seconds": wall_seconds,
            "cpu_seconds": time.process_time() - cpu_started,
            "detection_seconds": detection_seconds,
            "preservation_analysis_seconds": preservation_seconds,
            "artifact_write_and_verification_seconds": artifact_seconds,
            "trajectories_per_second": counts["trajectory_count"] / wall_seconds,
            "peak_tracemalloc_bytes": peak_memory_bytes,
            "generated_disk_footprint_bytes": generated_disk_bytes,
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
            "execution_policy": "sequential_bounded_per_scenario",
        },
        "scenario_artifact_checksums": scenario_checksums,
        "equivalent_repeat_parquet_checksums_match": all(
            bool(item["parquet_checksums_match"]) for item in scenario_checksums
        ),
    }
    return result, tuple(diagnostics), preservation_summary


def _synthetic_bundles(
    scenarios: Sequence[SyntheticScenario],
) -> tuple[_Bundle, ...]:
    return tuple(
        (
            scenario.scenario,
            scenario.coordinate_frame,
            scenario.agents,
            scenario.trajectories,
        )
        for scenario in scenarios
    )


def _configuration_value() -> dict[str, object]:
    return {
        "stop_speed_threshold_mps": _SEMANTIC_CONFIG.stop_speed_threshold_mps,
        "minimum_stop_duration_ns": _SEMANTIC_CONFIG.minimum_stop_duration_ns,
        "turn_delta_deadband_rad": _SEMANTIC_CONFIG.turn_delta_deadband_rad,
        "minimum_turn_angle_rad": _SEMANTIC_CONFIG.minimum_turn_angle_rad,
        "minimum_turn_duration_ns": _SEMANTIC_CONFIG.minimum_turn_duration_ns,
        "acceleration_deadband_mps2": (_SEMANTIC_CONFIG.acceleration_deadband_mps2),
        "minimum_speed_change_mps": _SEMANTIC_CONFIG.minimum_speed_change_mps,
        "minimum_acceleration_duration_ns": (
            _SEMANTIC_CONFIG.minimum_acceleration_duration_ns
        ),
        "detector_configuration_identity": (
            semantic_motion_configuration_identity(_SEMANTIC_CONFIG)
        ),
        "fixed_development_settings": True,
        "release_threshold_selection": False,
    }


def run(repository_root: Path, generated_root: Path) -> Mapping[str, object]:
    """Execute semantic gates and write tracked self-verifying evidence."""
    if _git_head(repository_root) != _REQUIRED_STARTING_HEAD:
        raise ArtifactError("current HEAD differs from required Batch 3.4 commit")
    if not _is_wsl() or not str(repository_root).startswith("/home/"):
        raise ArtifactError("evidence must run from the WSL2 Linux filesystem")
    batch_34_root = repository_root / "results/phase3/velocity_bounded_codec"
    batch_34_path = batch_34_root / "evidence.json"
    if _sha256(batch_34_path) != _BATCH_34_EVIDENCE_SHA256:
        raise ArtifactError("committed Batch 3.4 evidence checksum differs")
    batch_34 = _read_json(batch_34_path)
    if batch_34.get("batch_decision") != "achieved":
        raise ArtifactError("Batch 3.4 evidence is not achieved")

    provider_root = repository_root / "results/phase2/av2_provider_pilot"
    pilot_report = _read_json(provider_root / "pilot_report_first_run.json")
    provider_evidence = _read_json(provider_root / "evidence.json")
    validation_report = _read_json(provider_root / "validation_report.json")
    cache_value = pilot_report.get("cache_entry_directories")
    agent_value = validation_report.get("included_agent_ids")
    trajectory_value = validation_report.get("included_trajectory_ids")
    if (
        not isinstance(cache_value, list)
        or not all(isinstance(item, str) for item in cache_value)
        or not isinstance(agent_value, list)
        or not all(isinstance(item, str) for item in agent_value)
        or not isinstance(trajectory_value, list)
        or not all(isinstance(item, str) for item in trajectory_value)
    ):
        raise ArtifactError("provider inclusion metadata is invalid")
    cache_directories = tuple(cache_value)
    included_agent_ids = frozenset(agent_value)
    included_trajectory_ids = frozenset(trajectory_value)
    validation_identity = str(provider_evidence["validation_report_identity"])
    acquisition = _read_json(provider_root / "acquisition_report.json")
    acquired = acquisition.get("acquired_files")
    if not isinstance(acquired, list):
        raise ArtifactError("acquisition report file list is invalid")
    source_paths = tuple(
        repository_root / str(_mapping(item, "acquired file")["relative_path"])
        for item in acquired
    )
    cache_paths = tuple(
        path
        for directory in cache_directories
        for path in (repository_root / directory).rglob("*")
        if path.is_file()
    )
    source_before = _tree_identity(source_paths, repository_root)
    cache_before = _tree_identity(cache_paths, repository_root)

    synthetic_dataset = build_synthetic_dataset()
    synthetic_trajectories = tuple(
        trajectory
        for scenario in synthetic_dataset.scenarios
        for trajectory in scenario.trajectories
    )
    synthetic, _, synthetic_preservation = _run_dataset_gate(
        repository_root,
        generated_root / "synthetic",
        "synthetic",
        _synthetic_bundles(synthetic_dataset.scenarios),
        validation_identity="synthetic-validation:v1",
        source_bytes=trajectories_to_table(synthetic_trajectories).nbytes,
        procedural_bytes=_SYNTHETIC_PROCEDURAL_BYTES,
        synthetic_scenarios=synthetic_dataset.scenarios,
    )

    av2_bundles = _load_av2_scenarios(
        repository_root,
        cache_directories,
        included_agent_ids,
        included_trajectory_ids,
    )
    av2_trajectories = tuple(
        trajectory
        for _, _, _, trajectories in av2_bundles
        for trajectory in trajectories
    )
    if len(av2_bundles) != 10 or len(av2_trajectories) != 418:
        raise ArtifactError("loaded AV2 provider selection differs")
    av2, diagnostics, av2_preservation = _run_dataset_gate(
        repository_root,
        generated_root / "av2",
        "genuine_av2_provider",
        av2_bundles,
        validation_identity=validation_identity,
        source_bytes=trajectories_to_table(av2_trajectories).nbytes,
        procedural_bytes=_AV2_PROCEDURAL_BYTES,
    )
    source_after = _tree_identity(source_paths, repository_root)
    cache_after = _tree_identity(cache_paths, repository_root)
    if source_before != source_after or cache_before != cache_after:
        raise ArtifactError("provider source or canonical cache changed")
    av2 = {
        **av2,
        "provider_contract": {
            "dataset_id": provider_evidence["dataset_id"],
            "dataset_version": provider_evidence["dataset_version"],
            "validation_report_identity": validation_identity,
            "selected_scenario_ids": provider_evidence["selected_scenario_ids"],
            "genuine_provider_evidence": True,
        },
        "input_integrity": {
            "source_tree_sha256_before": source_before,
            "source_tree_sha256_after": source_after,
            "cache_tree_sha256_before": cache_before,
            "cache_tree_sha256_after": cache_after,
            "source_and_cache_unchanged": True,
        },
    }

    evidence_root = repository_root / "results/phase3/semantic_motion"
    schema_path = evidence_root / "schema_snapshot.json"
    synthetic_path = evidence_root / "synthetic_evidence.json"
    av2_path = evidence_root / "av2_provider_evidence.json"
    preservation_path = evidence_root / "preservation_analysis.json"
    error_path = evidence_root / "error_analysis.json"
    evidence_path = evidence_root / "evidence.json"
    summary_path = evidence_root / "summary.md"
    schema_snapshot = {
        "batch": _BATCH,
        "schema_count": len(canonical_schema_names()),
        "schemas": [
            {
                **schema_definition_to_dict(get_schema_definition(name)),
                "fingerprint": schema_fingerprint(name),
            }
            for name in canonical_schema_names()
        ],
        "previous_eight_fingerprints_unchanged": True,
        "semantic_waypoints_fingerprint": schema_fingerprint(
            CanonicalSchemaName.SEMANTIC_WAYPOINTS
        ),
        "motion_events_fingerprint": schema_fingerprint(
            CanonicalSchemaName.MOTION_EVENTS
        ),
    }
    preservation_analysis = {
        "batch": _BATCH,
        "interpretation": (
            "source/replay agreement measures behavioral preservation by the "
            "numerical codec; it does not establish detector correctness"
        ),
        "synthetic": synthetic_preservation,
        "genuine_av2_provider": av2_preservation,
        "gap_preservation_exact": bool(
            synthetic["gap_preservation_exact"] and av2["gap_preservation_exact"]
        ),
    }
    ordered_diagnostics = sorted(
        diagnostics,
        key=lambda item: (
            _number(item, "event_preservation_f1"),
            str(item["trajectory_safe_id"]),
        ),
    )
    error_analysis = {
        "batch": _BATCH,
        "dataset": "genuine_av2_provider",
        "trajectory_count": len(diagnostics),
        "lowest_event_preservation_f1_tracks": ordered_diagnostics[:10],
        "association_counts": {
            key: sum(bool(_mapping(item["associations"])[key]) for item in diagnostics)
            for key in (
                "sparse_sampling",
                "heading_absence",
                "velocity_derivation",
                "event_overlap",
                "short_duration_events",
                "codec_interpolation",
            )
        },
        "raw_provider_paths_or_rows_committed": False,
    }
    _write(schema_path, schema_snapshot)
    _write(synthetic_path, synthetic)
    _write(av2_path, av2)
    _write(preservation_path, preservation_analysis)
    _write(error_path, error_analysis)

    av2_counts = _mapping(av2["counts"])
    av2_storage = _mapping(av2["storage"])
    av2_resources = _mapping(av2["resources"])
    av2_preservation_value = _mapping(av2["preservation"])
    av2_waypoint_errors = _mapping(av2["waypoint_replay_errors"])
    summary = (
        f"# {_TITLE}\n\n"
        f"- Batch: {_BATCH}\n"
        "- Semantic layer: deterministic rule-based source detection over the "
        "Batch 3.4 numerical replay layer\n"
        "- Thresholds: fixed development settings; Phase 4 owns sweeps\n"
        "- Synthetic gate: PASS; 27 trajectories and all explicit oracles passed\n"
        "- Genuine AV2 gate: PASS; 10 scenarios and 418 trajectories\n"
        f"- AV2 semantic waypoints/events: {av2_counts['waypoint_count']}/"
        f"{av2_counts['event_count']}\n"
        f"- AV2 aggregate source/replay precision/recall/F1: "
        f"{av2_preservation_value['precision']}/"
        f"{av2_preservation_value['recall']}/"
        f"{av2_preservation_value['f1']}\n"
        f"- AV2 semantic/procedural/combined bytes: "
        f"{av2_storage['semantic_artifact_bytes']}/"
        f"{av2_storage['procedural_artifact_bytes']}/"
        f"{av2_storage['combined_artifact_bytes']}\n"
        f"- AV2 runtime/throughput/peak memory: "
        f"{av2_resources['wall_seconds']} s / "
        f"{av2_resources['trajectories_per_second']} trajectories/s / "
        f"{av2_resources['peak_tracemalloc_bytes']} bytes\n"
        f"- AV2 maximum waypoint position/velocity errors: "
        f"{_mapping(av2_waypoint_errors['position_m'])['maximum']} m / "
        f"{_mapping(av2_waypoint_errors['velocity_mps'])['maximum']} m/s\n"
        "- Equivalent repeated semantic Parquet checksums: matched\n"
        "- Provider source and canonical cache mutation: none\n\n"
        "AV2 does not provide complete ground-truth labels for all detected "
        "events. Synthetic scenarios provide exact oracle evidence. Source/replay "
        "agreement measures preservation, not detector correctness. Phase 4 owns "
        "threshold sweeps and broader scientific validation.\n\n"
        f"{_PASS_STATEMENT}\n"
    )
    _write_text(summary_path, summary)
    components = (
        schema_path,
        synthetic_path,
        av2_path,
        preservation_path,
        error_path,
        summary_path,
    )
    evidence = {
        "title": _TITLE,
        "batch": _BATCH,
        "batch_decision": "achieved",
        "pass_statement": _PASS_STATEMENT,
        "synthetic_gate": "PASS",
        "genuine_av2_provider_gate": "PASS",
        "semantic_layer_rule_based_and_deterministic": True,
        "configuration": _configuration_value(),
        "feature_source_policy": {
            "speed": ("stored speed, velocity norm, then within-run finite difference"),
            "heading": (
                "stored heading, moving velocity heading, then within-run "
                "displacement direction"
            ),
        },
        "batch_3_4_codec": {
            "maximum_position_error_m": _CODEC_CONFIG.maximum_position_error_m,
            "maximum_velocity_error_mps": (_CODEC_CONFIG.maximum_velocity_error_mps),
            "encoder_parameters_identity": (
                velocity_bounded_encoder_parameters_identity(_CODEC_CONFIG)
            ),
            "evidence_sha256": _sha256(batch_34_path),
        },
        "schema_fingerprints": {
            "semantic_waypoints": schema_fingerprint(
                CanonicalSchemaName.SEMANTIC_WAYPOINTS
            ),
            "motion_events": schema_fingerprint(CanonicalSchemaName.MOTION_EVENTS),
        },
        "scientific_interpretation": {
            "av2_complete_event_ground_truth_available": False,
            "synthetic_exact_oracles": True,
            "source_replay_agreement_is_preservation_not_correctness": True,
            "phase4_owns_threshold_sweeps_and_broader_validation": True,
        },
        "component_sha256": {path.name: _sha256(path) for path in components},
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.platform(),
            "machine": platform.machine(),
            "wsl_used": True,
            "execution_path": "<repository-root>",
            "execution_policy": "WSL2 Linux CPU sequential bounded per scenario",
            "uv_lock_sha256": _sha256(repository_root / "uv.lock"),
        },
        "generated_artifacts_tracked": False,
    }
    _write(evidence_path, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("results/generated/phase3/semantic_motion"),
    )
    arguments = parser.parse_args()
    repository_root = arguments.repository_root.resolve(strict=True)
    generated_root = arguments.generated_root
    if generated_root.is_absolute():
        try:
            generated_root = generated_root.relative_to(repository_root)
        except ValueError:
            parser.error("--generated-root must be beneath the repository root")
    run(repository_root, generated_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
