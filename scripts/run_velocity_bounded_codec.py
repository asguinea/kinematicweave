"""Run the Phase 3 dual-bound codec synthetic and genuine AV2 evidence gates."""

import argparse
from collections.abc import Mapping, Sequence
import math
import os
from pathlib import Path
import platform
import time
import tracemalloc

from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.exact import replay_track
from kinematicweave.codecs.velocity_bounded import (
    EncodingPerformanceStats,
    VelocityBoundedCodecConfig,
    encode_scenario_velocity_bounded_with_performance,
    validate_velocity_bounded_replay,
    velocity_bounded_encoder_parameters_identity,
    verify_velocity_bounded_artifacts,
)
from kinematicweave.data.parquet_io import trajectories_to_table
from kinematicweave.data.procedural_artifacts import materialize_procedural_tape
from kinematicweave.data.synthetic import SyntheticScenario, build_synthetic_dataset
from kinematicweave.domain.procedural import ProceduralTape, ProceduralTrack
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.errors import ArtifactError, ValidationError
from run_piecewise_linear_codec import (  # type: ignore[import-not-found]
    _artifact_checksums,
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

_BATCH = "3.4"
_TITLE = "Phase 3 Position-and-Velocity-Bounded Codec"
_PASS_STATEMENT = (
    "M3 position-and-velocity-bounded codec evaluated on synthetic and genuine "
    "AV2 provider data."
)
_REQUIRED_STARTING_HEAD = "6eb9495e6e3aa205b69bc19805a28ea4f2a84e07"
_CONFIG = VelocityBoundedCodecConfig(0.10, 1.00)
_NONBINDING_VELOCITY_CONFIG = VelocityBoundedCodecConfig(0.10, 1.0e100)

_EXACT_SYNTHETIC_SEGMENTS = 263
_EXACT_SYNTHETIC_BYTES = 374_216
_LINEAR_SYNTHETIC_SEGMENTS = 55
_LINEAR_SYNTHETIC_BYTES = 361_809
_HERMITE_SYNTHETIC_SEGMENTS = 39
_HERMITE_SYNTHETIC_BYTES = 359_435

_EXACT_AV2_SEGMENTS = 22_550
_EXACT_AV2_BYTES = 2_819_141
_LINEAR_AV2_SEGMENTS = 2_488
_LINEAR_AV2_BYTES = 570_662
_LINEAR_AV2_VELOCITY_P95_MPS = 0.429269
_LINEAR_AV2_VELOCITY_MAXIMUM_MPS = 6.28142
_LINEAR_AV2_TOTAL_SECONDS = 105.887
_HERMITE_AV2_SEGMENTS = 2_096
_HERMITE_AV2_BYTES = 526_573
_HERMITE_AV2_VELOCITY_P95_MPS = 1.2977909500786864
_HERMITE_AV2_VELOCITY_MAXIMUM_MPS = 11.171192960701672
_HERMITE_AV2_ENCODING_SECONDS = 690.967903858
_HERMITE_AV2_TOTAL_SECONDS = 705.767876143

_Bundle = tuple[
    ScenarioRecord,
    CoordinateFrameRecord,
    Sequence[AgentRecord],
    Sequence[Trajectory],
]


def _materialize_twice(
    repository_root: Path,
    generated_root: Path,
    label: str,
    tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
) -> tuple[int, int, tuple[str, str, str]]:
    first = materialize_procedural_tape(
        repository_root,
        generated_root,
        f"velocity-bounded:{label}:first",
        tape,
    )
    second = materialize_procedural_tape(
        repository_root,
        generated_root,
        f"velocity-bounded:{label}:repeat",
        tape,
    )
    for artifacts in (first, second):
        verify_velocity_bounded_artifacts(
            repository_root,
            artifacts,
            trajectories,
            _CONFIG,
            expected_tape=tape,
        )
    first_checksums = _artifact_checksums(first)
    if first_checksums != _artifact_checksums(second):
        raise ArtifactError("equivalent dual-bound Parquet checksums differ")
    first_bytes = (
        sum(
            artifact.written_artifact.size_bytes
            for artifact in (
                first.tape_manifest,
                first.procedural_tracks,
                first.procedural_segments,
            )
        )
        + first.codec_summary.size_bytes
    )
    disk_bytes = _tree_size(first.run_directory.path) + _tree_size(
        second.run_directory.path
    )
    return first_bytes, disk_bytes, first_checksums


def _primitive_counts(track: ProceduralTrack) -> dict[str, int]:
    return {
        primitive.value: sum(
            segment.primitive_type is primitive for segment in track.segments
        )
        for primitive in _CONFIG.candidate_primitives
    }


def _trajectory_diagnostic(
    scenario_id: str,
    track: ProceduralTrack,
    trajectory: Trajectory,
    hermite_segment_count: int,
) -> dict[str, object]:
    validation = validate_velocity_bounded_replay(track, trajectory, _CONFIG)
    endpoint_indices = {
        index
        for segment in track.segments
        for index in (
            segment.source_start_sample_index,
            segment.source_end_sample_index,
        )
    }
    velocity_rows: list[tuple[float, int]] = []
    for sample in trajectory.samples:
        if (
            not sample.is_valid
            or sample.velocity_x_mps is None
            or sample.velocity_y_mps is None
        ):
            continue
        state = replay_track(track, sample.timestamp_ns)
        if (
            state is None
            or state.velocity_x_mps is None
            or state.velocity_y_mps is None
        ):
            raise ValidationError("source velocity has no represented replay velocity")
        velocity_rows.append(
            (
                math.hypot(
                    state.velocity_x_mps - sample.velocity_x_mps,
                    state.velocity_y_mps - sample.velocity_y_mps,
                ),
                sample.sample_index,
            )
        )
    maximum_velocity_error, worst_index = max(
        velocity_rows,
        default=(0.0, trajectory.samples[0].sample_index),
    )
    return {
        "trajectory_safe_id": canonical_sha256(
            "velocity-bounded-error-analysis-trajectory",
            {
                "scenario_id": scenario_id,
                "trajectory_id": trajectory.trajectory_id,
            },
        ),
        "segment_count": track.segment_count,
        "segments_added_from_batch_3_3": track.segment_count - hermite_segment_count,
        "primitive_composition": _primitive_counts(track),
        "maximum_position_error_m": max(validation.position_errors_m, default=0.0),
        "maximum_heading_error_rad": max(validation.heading_errors_rad, default=0.0),
        "maximum_velocity_error_mps": maximum_velocity_error,
        "worst_velocity_error_location": (
            "retained_breakpoint" if worst_index in endpoint_indices else "interior"
        ),
    }


def _run_dataset_gate(
    repository_root: Path,
    generated_root: Path,
    label: str,
    bundles: Sequence[_Bundle],
    *,
    validation_identity: str,
    source_bytes: int,
    exact_segment_count: int,
    exact_procedural_bytes: int,
    linear_segment_count: int,
    linear_procedural_bytes: int,
    hermite_segment_count: int,
    hermite_procedural_bytes: int,
    collect_diagnostics: bool,
) -> tuple[
    dict[str, object],
    tuple[dict[str, object], ...],
    EncodingPerformanceStats,
]:
    counts: dict[str, int] = {
        "scenario_count": len(bundles),
        "trajectory_count": 0,
        "source_sample_count": 0,
        "valid_sample_count": 0,
        "invalid_sample_count": 0,
        "valid_run_count": 0,
        "hold_segment_count": 0,
        "linear_segment_count": 0,
        "cubic_hermite_segment_count": 0,
        "trajectories_using_hold": 0,
        "trajectories_using_linear": 0,
        "trajectories_using_cubic_hermite": 0,
        "segment_count": 0,
    }
    checksums: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    position_errors: list[float] = []
    heading_errors: list[float] = []
    velocity_errors: list[float] = []
    performance = EncodingPerformanceStats()
    procedural_bytes = 0
    generated_disk_bytes = 0
    replayed_valid_samples = 0
    invalid_gap_samples_with_state = 0
    encoding_seconds = 0.0
    comparison_seconds = 0.0
    replay_seconds = 0.0
    artifact_seconds = 0.0
    started = time.perf_counter()
    cpu_started = time.process_time()
    tracemalloc.start()
    try:
        for scenario, frame, agents, trajectories in bundles:
            operation_started = time.perf_counter()
            tape, scenario_performance = (
                encode_scenario_velocity_bounded_with_performance(
                    scenario,
                    frame,
                    agents,
                    trajectories,
                    _CONFIG,
                    source_validation_report_identity=validation_identity,
                )
            )
            encoding_seconds += time.perf_counter() - operation_started
            performance += scenario_performance
            source_by_id = {
                trajectory.trajectory_id: trajectory for trajectory in trajectories
            }

            hermite_counts_by_id: dict[str, int] = {}
            if collect_diagnostics:
                operation_started = time.perf_counter()
                comparison_tape, _ = encode_scenario_velocity_bounded_with_performance(
                    scenario,
                    frame,
                    agents,
                    trajectories,
                    _NONBINDING_VELOCITY_CONFIG,
                    source_validation_report_identity=validation_identity,
                )
                comparison_seconds += time.perf_counter() - operation_started
                hermite_counts_by_id = {
                    track.trajectory_id: track.segment_count
                    for track in comparison_tape.tracks
                }

            operation_started = time.perf_counter()
            for track in tape.tracks:
                source = source_by_id[track.trajectory_id]
                validation = validate_velocity_bounded_replay(track, source, _CONFIG)
                position_errors.extend(validation.position_errors_m)
                heading_errors.extend(validation.heading_errors_rad)
                velocity_errors.extend(validation.velocity_errors_mps)
                replayed_valid_samples += validation.replayed_valid_sample_count
                invalid_gap_samples_with_state += (
                    validation.invalid_gap_samples_with_state
                )
                if (
                    not validation.run_endpoints_exact
                    or not validation.retained_breakpoints_exact
                ):
                    raise ValidationError("source endpoint exactness check failed")
                primitive_counts = _primitive_counts(track)
                for primitive, count in primitive_counts.items():
                    counts[f"{primitive}_segment_count"] += count
                    counts[f"trajectories_using_{primitive}"] += count > 0
                if collect_diagnostics:
                    diagnostics.append(
                        _trajectory_diagnostic(
                            scenario.scenario_id,
                            track,
                            source,
                            hermite_counts_by_id[track.trajectory_id],
                        )
                    )
            replay_seconds += time.perf_counter() - operation_started

            samples = tuple(
                sample for trajectory in trajectories for sample in trajectory.samples
            )
            counts["trajectory_count"] += len(trajectories)
            counts["source_sample_count"] += len(samples)
            counts["valid_sample_count"] += sum(sample.is_valid for sample in samples)
            counts["invalid_sample_count"] += sum(
                not sample.is_valid for sample in samples
            )
            counts["valid_run_count"] += sum(track.run_count for track in tape.tracks)
            counts["segment_count"] += tape.segment_count

            operation_started = time.perf_counter()
            output_bytes, disk_bytes, artifact_checksums = _materialize_twice(
                repository_root,
                generated_root,
                f"{label}:{scenario.scenario_id}",
                tape,
                trajectories,
            )
            artifact_seconds += time.perf_counter() - operation_started
            procedural_bytes += output_bytes
            generated_disk_bytes += disk_bytes
            checksums.append(
                {
                    "scenario_safe_id": canonical_sha256(
                        "velocity-bounded-evidence-scenario",
                        {"scenario_id": scenario.scenario_id},
                    ),
                    "tape_id": tape.tape_id,
                    "parquet_sha256": list(artifact_checksums),
                }
            )
    finally:
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    wall_seconds = time.perf_counter() - started
    segment_count = counts["segment_count"]
    position_maximum = max(position_errors, default=0.0)
    velocity_maximum = max(velocity_errors, default=0.0)
    if position_maximum > _CONFIG.maximum_position_error_m:
        raise ValidationError("dataset gate exceeds configured position bound")
    if velocity_maximum > _CONFIG.maximum_velocity_error_mps:
        raise ValidationError("dataset gate exceeds configured velocity bound")
    if invalid_gap_samples_with_state:
        raise ValidationError("dataset gate bridged an invalid source gap")
    if segment_count > exact_segment_count:
        raise ValidationError("dataset gate exceeds exact baseline segment count")
    if (
        collect_diagnostics
        and sum(
            _integer(item, "segment_count")
            - _integer(item, "segments_added_from_batch_3_3")
            for item in diagnostics
        )
        != hermite_segment_count
    ):
        raise ValidationError("vectorized Batch 3.3 comparison count differs")
    primitive_counts = {
        primitive.value: counts[f"{primitive.value}_segment_count"]
        for primitive in _CONFIG.candidate_primitives
    }
    result: dict[str, object] = {
        "batch": _BATCH,
        "gate": label,
        "status": "PASS",
        "configuration": {
            "maximum_position_error_m": _CONFIG.maximum_position_error_m,
            "maximum_velocity_error_mps": _CONFIG.maximum_velocity_error_mps,
            "candidate_primitives": [
                primitive.value for primitive in _CONFIG.candidate_primitives
            ],
            "encoder_parameters_identity": (
                velocity_bounded_encoder_parameters_identity(_CONFIG)
            ),
        },
        "counts": counts,
        "primitive_segment_counts": primitive_counts,
        "primitive_segment_shares": {
            primitive: count / segment_count
            for primitive, count in primitive_counts.items()
        },
        "exact_baseline": {
            "segment_count": exact_segment_count,
            "procedural_artifact_bytes": exact_procedural_bytes,
        },
        "piecewise_linear_baseline": {
            "segment_count": linear_segment_count,
            "procedural_artifact_bytes": linear_procedural_bytes,
        },
        "hermite_baseline": {
            "segment_count": hermite_segment_count,
            "procedural_artifact_bytes": hermite_procedural_bytes,
        },
        "compression": {
            "segment_reduction_from_exact": exact_segment_count - segment_count,
            "segment_ratio_to_exact": segment_count / exact_segment_count,
            "segment_change_from_piecewise_linear": (
                segment_count - linear_segment_count
            ),
            "segment_ratio_to_piecewise_linear": segment_count / linear_segment_count,
            "segment_change_from_hermite": segment_count - hermite_segment_count,
            "segment_ratio_to_hermite": segment_count / hermite_segment_count,
            "byte_reduction_from_exact": exact_procedural_bytes - procedural_bytes,
            "byte_ratio_to_exact": procedural_bytes / exact_procedural_bytes,
            "byte_change_from_piecewise_linear": (
                procedural_bytes - linear_procedural_bytes
            ),
            "byte_ratio_to_piecewise_linear": (
                procedural_bytes / linear_procedural_bytes
            ),
            "byte_change_from_hermite": procedural_bytes - hermite_procedural_bytes,
            "byte_ratio_to_hermite": procedural_bytes / hermite_procedural_bytes,
            "procedural_to_source_byte_ratio": procedural_bytes / source_bytes,
        },
        "errors": {
            "position_m": _error_statistics(position_errors),
            "heading_rad": _error_statistics(heading_errors),
            "velocity_mps": _error_statistics(velocity_errors),
        },
        "candidate_performance": performance.to_dict(),
        "replayed_valid_sample_count": replayed_valid_samples,
        "invalid_gap_samples_with_state": invalid_gap_samples_with_state,
        "run_endpoints_exact": True,
        "retained_breakpoints_exact": True,
        "source_bytes": source_bytes,
        "procedural_artifact_bytes": procedural_bytes,
        "equivalent_repeat_parquet_checksums_match": True,
        "scenario_artifact_checksums": checksums,
        "resources": {
            "wall_seconds": wall_seconds,
            "cpu_seconds": time.process_time() - cpu_started,
            "encoding_seconds": encoding_seconds,
            "batch_3_3_vectorized_comparison_seconds": comparison_seconds,
            "replay_verification_seconds": replay_seconds,
            "artifact_write_and_verification_seconds": artifact_seconds,
            "trajectories_per_second": counts["trajectory_count"] / wall_seconds,
            "peak_tracemalloc_bytes": peak_memory_bytes,
            "generated_disk_footprint_bytes": generated_disk_bytes,
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
            "execution_policy": "sequential_bounded_per_scenario",
        },
    }
    return result, tuple(diagnostics), performance


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


def _metric(mapping: Mapping[str, object], key: str) -> float:
    value = mapping[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ArtifactError(f"{key} is not numeric")
    return float(value)


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactError(f"{key} is not an integer")
    return value


def _is_wsl() -> bool:
    release = platform.release().lower()
    try:
        proc_version = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        proc_version = ""
    return "microsoft" in release or "microsoft" in proc_version


def _distribution(values: Sequence[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "minimum": 0, "maximum": 0, "mean": 0.0, "median": 0}
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    p95_index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "maximum": ordered[-1],
        "mean": math.fsum(ordered) / len(ordered),
        "median": median,
        "p95": ordered[p95_index],
        "negative_count": sum(value < 0 for value in ordered),
        "zero_count": sum(value == 0 for value in ordered),
        "positive_count": sum(value > 0 for value in ordered),
        "sum": sum(ordered),
    }


def run(repository_root: Path, generated_root: Path) -> Mapping[str, object]:
    """Execute both gates and write tracked, self-verifying evidence."""
    if _git_head(repository_root) != _REQUIRED_STARTING_HEAD:
        raise ArtifactError("current HEAD differs from the required Batch 3.3 commit")
    if not _is_wsl() or not str(repository_root).startswith("/home/"):
        raise ArtifactError("evidence must run from the WSL2 Linux filesystem")

    evidence_root = repository_root / "results/phase3/velocity_bounded_codec"
    provider_root = repository_root / "results/phase2/av2_provider_pilot"
    exact_root = repository_root / "results/phase3/exact_codec_baseline"
    linear_root = repository_root / "results/phase3/piecewise_linear_codec"
    hermite_root = repository_root / "results/phase3/hermite_codec"
    exact_evidence = _read_json(exact_root / "evidence.json")
    linear_evidence = _read_json(linear_root / "evidence.json")
    hermite_evidence = _read_json(hermite_root / "evidence.json")
    exact_synthetic = _read_json(exact_root / "synthetic_evidence.json")
    exact_av2 = _read_json(exact_root / "av2_provider_evidence.json")
    linear_synthetic = _read_json(linear_root / "synthetic_evidence.json")
    linear_av2 = _read_json(linear_root / "av2_provider_evidence.json")
    hermite_synthetic = _read_json(hermite_root / "synthetic_evidence.json")
    hermite_av2 = _read_json(hermite_root / "av2_provider_evidence.json")
    expected_baselines = (
        (_mapping(exact_synthetic["counts"])["segment_count"], 263),
        (exact_synthetic["procedural_artifact_bytes"], 374_216),
        (_mapping(linear_synthetic["counts"])["segment_count"], 55),
        (linear_synthetic["procedural_artifact_bytes"], 361_809),
        (_mapping(hermite_synthetic["counts"])["segment_count"], 39),
        (hermite_synthetic["procedural_artifact_bytes"], 359_435),
        (_mapping(exact_av2["counts"])["segment_count"], 22_550),
        (exact_av2["procedural_artifact_bytes"], 2_819_141),
        (_mapping(linear_av2["counts"])["segment_count"], 2_488),
        (linear_av2["procedural_artifact_bytes"], 570_662),
        (_mapping(hermite_av2["counts"])["segment_count"], 2_096),
        (hermite_av2["procedural_artifact_bytes"], 526_573),
    )
    if any(actual != expected for actual, expected in expected_baselines):
        raise ArtifactError("committed Phase 3 baseline evidence differs")

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
    synthetic, _, synthetic_performance = _run_dataset_gate(
        repository_root,
        generated_root / "synthetic",
        "synthetic",
        _synthetic_bundles(synthetic_dataset.scenarios),
        validation_identity="synthetic-validation:v1",
        source_bytes=trajectories_to_table(synthetic_trajectories).nbytes,
        exact_segment_count=_EXACT_SYNTHETIC_SEGMENTS,
        exact_procedural_bytes=_EXACT_SYNTHETIC_BYTES,
        linear_segment_count=_LINEAR_SYNTHETIC_SEGMENTS,
        linear_procedural_bytes=_LINEAR_SYNTHETIC_BYTES,
        hermite_segment_count=_HERMITE_SYNTHETIC_SEGMENTS,
        hermite_procedural_bytes=_HERMITE_SYNTHETIC_BYTES,
        collect_diagnostics=False,
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
    av2, diagnostics, av2_performance = _run_dataset_gate(
        repository_root,
        generated_root / "av2",
        "genuine_av2_provider",
        av2_bundles,
        validation_identity=validation_identity,
        source_bytes=trajectories_to_table(av2_trajectories).nbytes,
        exact_segment_count=_EXACT_AV2_SEGMENTS,
        exact_procedural_bytes=_EXACT_AV2_BYTES,
        linear_segment_count=_LINEAR_AV2_SEGMENTS,
        linear_procedural_bytes=_LINEAR_AV2_BYTES,
        hermite_segment_count=_HERMITE_AV2_SEGMENTS,
        hermite_procedural_bytes=_HERMITE_AV2_BYTES,
        collect_diagnostics=True,
    )
    source_after = _tree_identity(source_paths, repository_root)
    cache_after = _tree_identity(cache_paths, repository_root)
    unchanged = source_before == source_after and cache_before == cache_after
    if not unchanged:
        raise ArtifactError("AV2 provider source or canonical cache changed")
    av2 = {
        **av2,
        "provider_contract": {
            "dataset_id": provider_evidence["dataset_id"],
            "dataset_version": provider_evidence["dataset_version"],
            "validation_report_identity": validation_identity,
            "selected_scenario_ids": provider_evidence["selected_scenario_ids"],
            "official_remote_root": provider_evidence["official_remote_root"],
            "genuine_provider_evidence": True,
        },
        "input_integrity": {
            "source_tree_sha256_before": source_before,
            "source_tree_sha256_after": source_after,
            "cache_tree_sha256_before": cache_before,
            "cache_tree_sha256_after": cache_after,
            "source_and_cache_unchanged": unchanged,
        },
    }

    ordered_diagnostics = sorted(
        diagnostics,
        key=lambda item: (
            -_metric(item, "maximum_velocity_error_mps"),
            str(item["trajectory_safe_id"]),
        ),
    )
    additions = [
        _integer(item, "segments_added_from_batch_3_3") for item in diagnostics
    ]
    av2_counts = _mapping(av2["counts"])
    error_analysis = {
        "batch": _BATCH,
        "dataset": "genuine_av2_provider",
        "trajectory_count": len(diagnostics),
        "trajectories_using_primitives": {
            primitive.value: av2_counts[f"trajectories_using_{primitive.value}"]
            for primitive in _CONFIG.candidate_primitives
        },
        "segments_added_relative_to_batch_3_3": _distribution(additions),
        "candidate_rejections": {
            "position": av2_performance.position_rejection_count,
            "velocity": av2_performance.velocity_rejection_count,
        },
        "highest_velocity_error_trajectories": ordered_diagnostics[:10],
        "raw_provider_paths_or_rows_committed": False,
    }
    av2_resources = _mapping(av2["resources"])
    av2_encoding_seconds = _metric(av2_resources, "encoding_seconds")
    performance_analysis = {
        "batch": _BATCH,
        "algorithm_version": "vectorized-candidate-cache-dp-v1",
        "optimization_scope": (
            "exact dynamic programming over the generated deterministic candidate set"
        ),
        "techniques": [
            "NumPy evaluation of all end points for each start and primitive",
            "one reusable candidate cache per valid elevation run",
            "one dynamic-programming pass over cached accepted candidates",
            "no repeated procedural object construction during candidate evaluation",
        ],
        "synthetic": synthetic_performance.to_dict(),
        "genuine_av2": av2_performance.to_dict(),
        "batch_3_3_reference": {
            "encoding_seconds": _HERMITE_AV2_ENCODING_SECONDS,
            "total_seconds": _HERMITE_AV2_TOTAL_SECONDS,
        },
        "encoding_speedup_over_batch_3_3": (
            _HERMITE_AV2_ENCODING_SECONDS / av2_encoding_seconds
        ),
        "material_runtime_improvement": (
            av2_encoding_seconds < _HERMITE_AV2_ENCODING_SECONDS
        ),
        "dominant_measured_phase": (
            "candidate_generation"
            if av2_performance.candidate_generation_seconds
            >= av2_performance.dynamic_programming_seconds
            else "dynamic_programming"
        ),
    }

    synthetic_path = evidence_root / "synthetic_evidence.json"
    av2_path = evidence_root / "av2_provider_evidence.json"
    analysis_path = evidence_root / "error_analysis.json"
    performance_path = evidence_root / "performance_analysis.json"
    summary_path = evidence_root / "summary.md"
    evidence_path = evidence_root / "evidence.json"
    _write(synthetic_path, synthetic)
    _write(av2_path, av2)
    _write(analysis_path, error_analysis)
    _write(performance_path, performance_analysis)

    synthetic_counts = _mapping(synthetic["counts"])
    synthetic_errors = _mapping(synthetic["errors"])
    av2_errors = _mapping(av2["errors"])
    summary = (
        f"# {_TITLE}\n\n"
        f"- Batch: {_BATCH}\n"
        "- Hard development bounds: 0.10 m source-timestamp position and "
        "1.00 m/s represented x/y velocity-vector error\n"
        "- Candidate-set optimality: exact trajectory-local dynamic programming "
        "over deterministic hold, linear, and cubic-Hermite candidates\n"
        "- Synthetic gate: PASS; "
        f"{synthetic_counts['trajectory_count']} trajectories, "
        f"{synthetic_counts['segment_count']} segments, "
        f"{synthetic['procedural_artifact_bytes']} bytes\n"
        "- Genuine AV2 gate: PASS; 10 scenarios, "
        f"{av2_counts['trajectory_count']} trajectories, "
        f"{av2_counts['segment_count']} segments, "
        f"{av2['procedural_artifact_bytes']} bytes\n"
        "- AV2 primitive segments (hold/linear/Hermite): "
        f"{av2_counts['hold_segment_count']}/"
        f"{av2_counts['linear_segment_count']}/"
        f"{av2_counts['cubic_hermite_segment_count']}\n"
        "- AV2 maximum position/velocity error: "
        f"{_mapping(av2_errors['position_m'])['maximum']} m / "
        f"{_mapping(av2_errors['velocity_mps'])['maximum']} m/s\n"
        "- Synthetic maximum position/velocity error: "
        f"{_mapping(synthetic_errors['position_m'])['maximum']} m / "
        f"{_mapping(synthetic_errors['velocity_mps'])['maximum']} m/s\n"
        f"- Segment comparison exact/linear/unconstrained-Hermite/dual-bound: "
        f"{_EXACT_AV2_SEGMENTS}/{_LINEAR_AV2_SEGMENTS}/"
        f"{_HERMITE_AV2_SEGMENTS}/{av2_counts['segment_count']}\n"
        f"- Byte comparison exact/linear/unconstrained-Hermite/dual-bound: "
        f"{_EXACT_AV2_BYTES}/{_LINEAR_AV2_BYTES}/"
        f"{_HERMITE_AV2_BYTES}/{av2['procedural_artifact_bytes']}\n"
        "- Batch 3.2 velocity p95/max: "
        f"{_LINEAR_AV2_VELOCITY_P95_MPS}/{_LINEAR_AV2_VELOCITY_MAXIMUM_MPS} "
        f"m/s; total runtime {_LINEAR_AV2_TOTAL_SECONDS} s\n"
        "- Batch 3.3 velocity p95/max: "
        f"{_HERMITE_AV2_VELOCITY_P95_MPS}/"
        f"{_HERMITE_AV2_VELOCITY_MAXIMUM_MPS} m/s; total runtime "
        f"{_HERMITE_AV2_TOTAL_SECONDS} s\n"
        f"- Dual-bound encoding runtime: {av2_encoding_seconds} s; speedup "
        f"{performance_analysis['encoding_speedup_over_batch_3_3']}x\n"
        "- Equivalent repeat procedural Parquet checksums: matched\n"
        "- Provider source and canonical cache mutation: none\n\n"
        "The velocity guarantee may require more segments and bytes than the "
        "linear-only or unconstrained-Hermite codecs; the measured comparison "
        "above reports that tradeoff directly. Both bounds are development "
        "settings. Phase 4 owns threshold sweeps and scientific method "
        "selection.\n\n"
        f"{_PASS_STATEMENT}\n"
    )
    _write_text(summary_path, summary)
    components = (
        synthetic_path,
        av2_path,
        analysis_path,
        performance_path,
        summary_path,
    )
    evidence = {
        "title": _TITLE,
        "batch": _BATCH,
        "batch_decision": "achieved",
        "pass_statement": _PASS_STATEMENT,
        "synthetic_gate": "PASS",
        "genuine_av2_provider_gate": "PASS",
        "configuration": {
            "maximum_position_error_m": _CONFIG.maximum_position_error_m,
            "maximum_velocity_error_mps": _CONFIG.maximum_velocity_error_mps,
            "candidate_primitives": [
                primitive.value for primitive in _CONFIG.candidate_primitives
            ],
            "encoder_parameters_identity": (
                velocity_bounded_encoder_parameters_identity(_CONFIG)
            ),
            "fixed_development_settings": True,
            "release_threshold_selection": False,
        },
        "baseline_references": {
            "exact_evidence_sha256": _sha256(exact_root / "evidence.json"),
            "linear_evidence_sha256": _sha256(linear_root / "evidence.json"),
            "hermite_evidence_sha256": _sha256(hermite_root / "evidence.json"),
            "all_baselines_achieved": all(
                value["batch_decision"] == "achieved"
                for value in (exact_evidence, linear_evidence, hermite_evidence)
            ),
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
        default=Path("results/generated/phase3/velocity_bounded_codec"),
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
