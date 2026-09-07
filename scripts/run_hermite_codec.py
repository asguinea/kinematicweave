"""Run the Phase 3 Hermite-codec synthetic and genuine AV2 evidence gates."""

import argparse
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import platform
import time
import tracemalloc

from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.exact import replay_track
from kinematicweave.codecs.hermite import (
    HermiteCodecConfig,
    encode_scenario_hermite,
    hermite_encoder_parameters_identity,
    validate_hermite_replay,
    verify_hermite_artifacts,
)
from kinematicweave.codecs.piecewise_linear import POSITION_COMPARISON_GUARD_M
from kinematicweave.data.parquet_io import trajectories_to_table
from kinematicweave.data.procedural_artifacts import (
    materialize_procedural_tape,
)
from kinematicweave.data.synthetic import SyntheticScenario, build_synthetic_dataset
from kinematicweave.domain.procedural import (
    ProceduralPrimitiveType,
    ProceduralTape,
    ProceduralTrack,
)
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

_BATCH = "3.3"
_TITLE = "Phase 3 Velocity-Aware Cubic Hermite Codec"
_PASS_STATEMENT = (
    "M3 velocity-aware Hermite codec evaluated on synthetic and genuine AV2 "
    "provider data."
)
_REQUIRED_STARTING_HEAD = "66a36cace93a848220458b90acf60586e8707a63"
_CONFIG = HermiteCodecConfig(maximum_position_error_m=0.10)
_EXACT_SYNTHETIC_SEGMENTS = 263
_EXACT_SYNTHETIC_BYTES = 374_216
_EXACT_AV2_SEGMENTS = 22_550
_EXACT_AV2_BYTES = 2_819_141
_LINEAR_SYNTHETIC_SEGMENTS = 55
_LINEAR_AV2_SEGMENTS = 2_488
_LINEAR_AV2_BYTES = 570_662
_LINEAR_AV2_POSITION_P95_M = 0.0918930
_LINEAR_AV2_HEADING_P95_RAD = 0.0195878
_LINEAR_AV2_VELOCITY_P95_MPS = 0.429269
_LINEAR_AV2_VELOCITY_MAXIMUM_MPS = 6.28142

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
        f"hermite:{label}:first",
        tape,
    )
    second = materialize_procedural_tape(
        repository_root,
        generated_root,
        f"hermite:{label}:repeat",
        tape,
    )
    for artifacts in (first, second):
        verify_hermite_artifacts(
            repository_root,
            artifacts,
            trajectories,
            _CONFIG,
            expected_tape=tape,
        )
    first_checksums = _artifact_checksums(first)
    if first_checksums != _artifact_checksums(second):
        raise ArtifactError("equivalent Hermite tape Parquet checksums differ")
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
) -> dict[str, object]:
    validation = validate_hermite_replay(track, trajectory, _CONFIG)
    endpoint_indices = {
        index
        for segment in track.segments
        for index in (
            segment.source_start_sample_index,
            segment.source_end_sample_index,
        )
    }
    velocity_rows: list[tuple[float, int]] = []
    heading_errors: list[float] = []
    for sample in trajectory.samples:
        if not sample.is_valid:
            continue
        state = replay_track(track, sample.timestamp_ns)
        if state is None:
            raise ValidationError("valid sample has no Hermite replay state")
        if (
            sample.velocity_x_mps is not None
            and sample.velocity_y_mps is not None
            and state.velocity_x_mps is not None
            and state.velocity_y_mps is not None
        ):
            velocity_rows.append(
                (
                    (
                        (state.velocity_x_mps - sample.velocity_x_mps) ** 2
                        + (state.velocity_y_mps - sample.velocity_y_mps) ** 2
                    )
                    ** 0.5,
                    sample.sample_index,
                )
            )
    maximum_velocity_error, worst_index = max(
        velocity_rows,
        default=(0.0, trajectory.samples[0].sample_index),
    )
    heading_errors.extend(validation.heading_errors_rad)
    return {
        "trajectory_safe_id": canonical_sha256(
            "hermite-error-analysis-trajectory",
            {
                "scenario_id": scenario_id,
                "trajectory_id": trajectory.trajectory_id,
            },
        ),
        "segment_count": track.segment_count,
        "primitive_composition": _primitive_counts(track),
        "maximum_position_error_m": max(validation.position_errors_m, default=0.0),
        "maximum_heading_error_rad": max(heading_errors, default=0.0),
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
    linear_procedural_bytes: int | None,
    collect_diagnostics: bool,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
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
        "trajectories_using_cubic_hermite": 0,
        "segment_count": 0,
    }
    checksum_rows: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    position_errors: list[float] = []
    heading_errors: list[float] = []
    velocity_errors: list[float] = []
    procedural_bytes = 0
    generated_disk_bytes = 0
    replayed_valid_samples = 0
    invalid_gap_samples_with_state = 0
    encoding_seconds = 0.0
    replay_seconds = 0.0
    artifact_seconds = 0.0
    started = time.perf_counter()
    cpu_started = time.process_time()
    tracemalloc.start()
    try:
        for scenario, frame, agents, trajectories in bundles:
            operation_started = time.perf_counter()
            tape = encode_scenario_hermite(
                scenario,
                frame,
                agents,
                trajectories,
                _CONFIG,
                source_validation_report_identity=validation_identity,
            )
            encoding_seconds += time.perf_counter() - operation_started
            source_by_id = {
                trajectory.trajectory_id: trajectory for trajectory in trajectories
            }

            operation_started = time.perf_counter()
            for track in tape.tracks:
                source = source_by_id[track.trajectory_id]
                validation = validate_hermite_replay(track, source, _CONFIG)
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
                uses_hermite = (
                    primitive_counts[ProceduralPrimitiveType.CUBIC_HERMITE.value] > 0
                )
                counts["trajectories_using_cubic_hermite"] += uses_hermite
                if collect_diagnostics:
                    diagnostics.append(
                        _trajectory_diagnostic(
                            scenario.scenario_id,
                            track,
                            source,
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
            output_bytes, disk_bytes, checksums = _materialize_twice(
                repository_root,
                generated_root,
                f"{label}:{scenario.scenario_id}",
                tape,
                trajectories,
            )
            artifact_seconds += time.perf_counter() - operation_started
            procedural_bytes += output_bytes
            generated_disk_bytes += disk_bytes
            checksum_rows.append(
                {
                    "scenario_safe_id": canonical_sha256(
                        "hermite-evidence-scenario",
                        {"scenario_id": scenario.scenario_id},
                    ),
                    "tape_id": tape.tape_id,
                    "parquet_sha256": list(checksums),
                }
            )
    finally:
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    elapsed = time.perf_counter() - started
    cpu_seconds = time.process_time() - cpu_started
    segment_count = counts["segment_count"]
    maximum_error = max(position_errors, default=0.0)
    if maximum_error > (_CONFIG.maximum_position_error_m + POSITION_COMPARISON_GUARD_M):
        raise ValidationError("dataset gate exceeds configured position bound")
    if invalid_gap_samples_with_state:
        raise ValidationError("dataset gate bridged an invalid source gap")
    if segment_count > linear_segment_count:
        raise ValidationError("candidate superset regressed segment count")
    trajectory_count = counts["trajectory_count"]
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
            "candidate_primitives": [
                primitive.value for primitive in _CONFIG.candidate_primitives
            ],
            "floating_comparison_guard_m": POSITION_COMPARISON_GUARD_M,
            "encoder_parameters_identity": hermite_encoder_parameters_identity(_CONFIG),
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
        "compression": {
            "segment_reduction_from_exact": exact_segment_count - segment_count,
            "segment_ratio_to_exact": segment_count / exact_segment_count,
            "segment_change_from_piecewise_linear": (
                segment_count - linear_segment_count
            ),
            "segment_ratio_to_piecewise_linear": (segment_count / linear_segment_count),
            "byte_reduction_from_exact": exact_procedural_bytes - procedural_bytes,
            "byte_ratio_to_exact": procedural_bytes / exact_procedural_bytes,
            "byte_change_from_piecewise_linear": (
                None
                if linear_procedural_bytes is None
                else procedural_bytes - linear_procedural_bytes
            ),
            "byte_ratio_to_piecewise_linear": (
                None
                if linear_procedural_bytes is None
                else procedural_bytes / linear_procedural_bytes
            ),
            "procedural_to_source_byte_ratio": (
                procedural_bytes / source_bytes if source_bytes else None
            ),
        },
        "errors": {
            "position_m": _error_statistics(position_errors),
            "heading_rad": _error_statistics(heading_errors),
            "velocity_mps": _error_statistics(velocity_errors),
        },
        "replayed_valid_sample_count": replayed_valid_samples,
        "invalid_gap_samples_with_state": invalid_gap_samples_with_state,
        "run_endpoints_exact": True,
        "retained_breakpoints_exact": True,
        "source_bytes": source_bytes,
        "procedural_artifact_bytes": procedural_bytes,
        "equivalent_repeat_parquet_checksums_match": True,
        "scenario_artifact_checksums": checksum_rows,
        "resources": {
            "wall_seconds": elapsed,
            "cpu_seconds": cpu_seconds,
            "encoding_seconds": encoding_seconds,
            "replay_verification_seconds": replay_seconds,
            "artifact_write_and_verification_seconds": artifact_seconds,
            "trajectories_per_second": trajectory_count / elapsed,
            "peak_tracemalloc_bytes": peak_memory_bytes,
            "generated_disk_footprint_bytes": generated_disk_bytes,
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
            "execution_policy": "sequential_bounded_per_scenario",
        },
    }
    return result, tuple(diagnostics)


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


def _diagnostic_velocity(item: Mapping[str, object]) -> float:
    return _metric(item, "maximum_velocity_error_mps")


def _is_wsl() -> bool:
    release = platform.release().lower()
    try:
        proc_version = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        proc_version = ""
    return "microsoft" in release or "microsoft" in proc_version


def run(repository_root: Path, generated_root: Path) -> Mapping[str, object]:
    """Execute both gates and write tracked, self-verifying evidence."""
    if _git_head(repository_root) != _REQUIRED_STARTING_HEAD:
        raise ArtifactError("current HEAD differs from the required Batch 3.2 commit")
    evidence_root = repository_root / "results/phase3/hermite_codec"
    provider_root = repository_root / "results/phase2/av2_provider_pilot"
    exact_root = repository_root / "results/phase3/exact_codec_baseline"
    linear_root = repository_root / "results/phase3/piecewise_linear_codec"
    exact_evidence = _read_json(exact_root / "evidence.json")
    linear_evidence = _read_json(linear_root / "evidence.json")
    exact_synthetic = _read_json(exact_root / "synthetic_evidence.json")
    exact_av2 = _read_json(exact_root / "av2_provider_evidence.json")
    linear_synthetic = _read_json(linear_root / "synthetic_evidence.json")
    linear_av2 = _read_json(linear_root / "av2_provider_evidence.json")
    if (
        _mapping(exact_synthetic["counts"])["segment_count"]
        != _EXACT_SYNTHETIC_SEGMENTS
        or exact_synthetic["procedural_artifact_bytes"] != _EXACT_SYNTHETIC_BYTES
        or _mapping(exact_av2["counts"])["segment_count"] != _EXACT_AV2_SEGMENTS
        or exact_av2["procedural_artifact_bytes"] != _EXACT_AV2_BYTES
        or _mapping(linear_synthetic["counts"])["segment_count"]
        != _LINEAR_SYNTHETIC_SEGMENTS
        or _mapping(linear_av2["counts"])["segment_count"] != _LINEAR_AV2_SEGMENTS
        or linear_av2["procedural_artifact_bytes"] != _LINEAR_AV2_BYTES
    ):
        raise ArtifactError("committed exact or linear baseline evidence differs")
    linear_av2_errors = _mapping(linear_av2["errors"])
    linear_position = _mapping(linear_av2_errors["position_m"])
    linear_heading = _mapping(linear_av2_errors["heading_rad"])
    linear_velocity = _mapping(linear_av2_errors["velocity_mps"])
    if not (
        abs(_metric(linear_position, "p95") - _LINEAR_AV2_POSITION_P95_M) < 1e-6
        and abs(_metric(linear_heading, "p95") - _LINEAR_AV2_HEADING_P95_RAD) < 1e-6
        and abs(_metric(linear_velocity, "p95") - _LINEAR_AV2_VELOCITY_P95_MPS) < 1e-6
        and abs(_metric(linear_velocity, "maximum") - _LINEAR_AV2_VELOCITY_MAXIMUM_MPS)
        < 1e-5
    ):
        raise ArtifactError("committed linear AV2 diagnostics differ")

    pilot_report = _read_json(provider_root / "pilot_report_first_run.json")
    provider_evidence = _read_json(provider_root / "evidence.json")
    validation_report = _read_json(provider_root / "validation_report.json")
    cache_directories_value = pilot_report.get("cache_entry_directories")
    if not isinstance(cache_directories_value, list) or any(
        not isinstance(item, str) for item in cache_directories_value
    ):
        raise ArtifactError("pilot report cache directories are invalid")
    cache_directories = tuple(cache_directories_value)
    included_agent_ids_value = validation_report.get("included_agent_ids")
    included_trajectory_ids_value = validation_report.get("included_trajectory_ids")
    if not isinstance(included_agent_ids_value, list) or any(
        not isinstance(item, str) for item in included_agent_ids_value
    ):
        raise ArtifactError("validation report included agent IDs are invalid")
    if not isinstance(included_trajectory_ids_value, list) or any(
        not isinstance(item, str) for item in included_trajectory_ids_value
    ):
        raise ArtifactError("validation report included trajectory IDs are invalid")
    included_agent_ids = frozenset(included_agent_ids_value)
    included_trajectory_ids = frozenset(included_trajectory_ids_value)
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
    source_identity_before = _tree_identity(source_paths, repository_root)
    cache_identity_before = _tree_identity(cache_paths, repository_root)

    synthetic_dataset = build_synthetic_dataset()
    synthetic_trajectories = tuple(
        trajectory
        for scenario in synthetic_dataset.scenarios
        for trajectory in scenario.trajectories
    )
    synthetic, _ = _run_dataset_gate(
        repository_root,
        generated_root / "synthetic",
        "synthetic",
        _synthetic_bundles(synthetic_dataset.scenarios),
        validation_identity="synthetic-validation:v1",
        source_bytes=trajectories_to_table(synthetic_trajectories).nbytes,
        exact_segment_count=_EXACT_SYNTHETIC_SEGMENTS,
        exact_procedural_bytes=_EXACT_SYNTHETIC_BYTES,
        linear_segment_count=_LINEAR_SYNTHETIC_SEGMENTS,
        linear_procedural_bytes=int(
            _metric(linear_synthetic, "procedural_artifact_bytes")
        ),
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
    if len(av2_trajectories) != 418 or len(av2_trajectories) != len(
        included_trajectory_ids
    ):
        raise ArtifactError("loaded AV2 trajectory count differs from inclusion set")
    av2, diagnostics = _run_dataset_gate(
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
        collect_diagnostics=True,
    )
    source_identity_after = _tree_identity(source_paths, repository_root)
    cache_identity_after = _tree_identity(cache_paths, repository_root)
    unchanged = (
        source_identity_before == source_identity_after
        and cache_identity_before == cache_identity_after
    )
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
            "source_tree_sha256_before": source_identity_before,
            "source_tree_sha256_after": source_identity_after,
            "cache_tree_sha256_before": cache_identity_before,
            "cache_tree_sha256_after": cache_identity_after,
            "source_and_cache_unchanged": unchanged,
        },
        "comparison_to_piecewise_linear": {
            "position_p95_m": _LINEAR_AV2_POSITION_P95_M,
            "heading_p95_rad": _LINEAR_AV2_HEADING_P95_RAD,
            "velocity_p95_mps": _LINEAR_AV2_VELOCITY_P95_MPS,
            "velocity_maximum_mps": _LINEAR_AV2_VELOCITY_MAXIMUM_MPS,
        },
    }
    ordered_diagnostics = sorted(
        diagnostics,
        key=lambda item: (
            -_diagnostic_velocity(item),
            str(item["trajectory_safe_id"]),
        ),
    )
    error_analysis = {
        "batch": _BATCH,
        "dataset": "genuine_av2_provider",
        "trajectory_count": len(diagnostics),
        "trajectories_using_cubic_hermite": _mapping(av2["counts"])[
            "trajectories_using_cubic_hermite"
        ],
        "highest_velocity_error_trajectories": ordered_diagnostics[:10],
        "raw_provider_paths_or_rows_committed": False,
    }

    synthetic_path = evidence_root / "synthetic_evidence.json"
    av2_path = evidence_root / "av2_provider_evidence.json"
    analysis_path = evidence_root / "error_analysis.json"
    summary_path = evidence_root / "summary.md"
    evidence_path = evidence_root / "evidence.json"
    _write(synthetic_path, synthetic)
    _write(av2_path, av2)
    _write(analysis_path, error_analysis)
    synthetic_counts = _mapping(synthetic["counts"])
    av2_counts = _mapping(av2["counts"])
    av2_errors = _mapping(av2["errors"])
    summary = (
        f"# {_TITLE}\n\n"
        f"- Batch: {_BATCH}\n"
        "- Configuration: fixed development setting at 0.10 m maximum "
        "source-timestamp position error\n"
        "- Candidate-set optimality: minimum segments over deterministic hold, "
        "linear, and cubic-Hermite candidates, followed by the declared error "
        "and tie-break objectives\n"
        "- Position is the hard fitting constraint; heading and velocity are "
        "measured diagnostics\n"
        "- Synthetic gate: PASS\n"
        "- Genuine AV2 provider gate: PASS\n"
        f"- Synthetic trajectories: {synthetic_counts['trajectory_count']}\n"
        f"- Synthetic segments: {synthetic_counts['segment_count']} "
        f"(linear baseline {_LINEAR_SYNTHETIC_SEGMENTS})\n"
        f"- Synthetic Hermite segments: "
        f"{synthetic_counts['cubic_hermite_segment_count']}\n"
        f"- AV2 scenarios: {av2_counts['scenario_count']}\n"
        f"- AV2 trajectories: {av2_counts['trajectory_count']}\n"
        f"- AV2 segments: {av2_counts['segment_count']} "
        f"(linear baseline {_LINEAR_AV2_SEGMENTS})\n"
        f"- AV2 Hermite segments: {av2_counts['cubic_hermite_segment_count']}\n"
        f"- AV2 procedural bytes: {av2['procedural_artifact_bytes']} "
        f"(linear baseline {_LINEAR_AV2_BYTES})\n"
        f"- AV2 position p95 (m): "
        f"{_mapping(av2_errors['position_m'])['p95']}\n"
        f"- AV2 heading p95 (rad): "
        f"{_mapping(av2_errors['heading_rad'])['p95']}\n"
        f"- AV2 velocity p95 / maximum (m/s): "
        f"{_mapping(av2_errors['velocity_mps'])['p95']} / "
        f"{_mapping(av2_errors['velocity_mps'])['maximum']}\n"
        "- Equivalent repeat Parquet checksums: matched\n"
        "- Provider source/cache mutation: none\n\n"
        "Whether Hermite improved the genuine provider data is determined by "
        "the measured comparisons above; no metric is presumed to improve. "
        "The 0.10 m value is a fixed development setting. Scientific tolerance "
        "and method comparisons remain Phase 4 work.\n\n"
        f"{_PASS_STATEMENT}\n"
    )
    _write_text(summary_path, summary)
    component_paths = (synthetic_path, av2_path, analysis_path, summary_path)
    evidence = {
        "title": _TITLE,
        "batch": _BATCH,
        "batch_decision": "achieved",
        "pass_statement": _PASS_STATEMENT,
        "synthetic_gate": "PASS",
        "genuine_av2_provider_gate": "PASS",
        "configuration": {
            "maximum_position_error_m": _CONFIG.maximum_position_error_m,
            "candidate_primitives": [
                primitive.value for primitive in _CONFIG.candidate_primitives
            ],
            "encoder_parameters_identity": hermite_encoder_parameters_identity(_CONFIG),
            "fixed_development_setting": True,
            "release_threshold_selection": False,
        },
        "exact_baseline_reference": {
            "evidence_sha256": _sha256(exact_root / "evidence.json"),
            "batch_decision": exact_evidence["batch_decision"],
        },
        "piecewise_linear_baseline_reference": {
            "commit": _REQUIRED_STARTING_HEAD,
            "evidence_sha256": _sha256(linear_root / "evidence.json"),
            "batch_decision": linear_evidence["batch_decision"],
        },
        "component_sha256": {path.name: _sha256(path) for path in component_paths},
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.platform(),
            "machine": platform.machine(),
            "wsl_used": _is_wsl(),
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
        default=Path("results/generated/phase3/hermite_codec"),
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
