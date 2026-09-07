"""Run the Phase 3 compact-codec synthetic and genuine AV2 evidence gates."""

import argparse
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time
import tracemalloc

import pyarrow as pa  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_bytes, canonical_json_text
from kinematicweave.codecs.piecewise_linear import (
    POSITION_COMPARISON_GUARD_M,
    PiecewiseLinearCodecConfig,
    encode_scenario_piecewise_linear,
    encoder_parameters_identity,
    validate_piecewise_linear_replay,
    verify_piecewise_linear_artifacts,
)
from kinematicweave.data.parquet_io import (
    iter_canonical_parquet_batches,
    trajectories_to_table,
)
from kinematicweave.data.procedural_artifacts import (
    ProceduralTapeArtifacts,
    materialize_procedural_tape,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.data.synthetic import SyntheticScenario, build_synthetic_dataset
from kinematicweave.domain.procedural import ProceduralPrimitiveType, ProceduralTape
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
)
from kinematicweave.errors import ArtifactError, ValidationError

_BATCH = "3.2"
_TITLE = "Phase 3 Error-Bounded Piecewise-Linear Codec"
_PASS_STATEMENT = (
    "M3 compact linear codec achieved on synthetic and genuine AV2 provider data."
)
_REQUIRED_STARTING_HEAD = "790dd32e18628ff8f29f13da41360f789d833341"
_CONFIG = PiecewiseLinearCodecConfig(maximum_position_error_m=0.10)
_EXACT_SYNTHETIC_SEGMENTS = 263
_EXACT_SYNTHETIC_BYTES = 374_216
_EXACT_AV2_SEGMENTS = 22_550
_EXACT_AV2_BYTES = 2_819_141


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError(f"cannot read evidence input: {path.name}") from error
    if not isinstance(value, Mapping):
        raise ArtifactError(f"evidence input is not an object: {path.name}")
    return value


def _mapping(value: object, label: str = "value") -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"{label} is not an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_identity(paths: Iterable[Path], repository_root: Path) -> str:
    rows = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        rows.append(
            {
                "path": path.relative_to(repository_root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


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
    schema: CanonicalSchemaName,
) -> Iterator[dict[str, object]]:
    for batch in iter_canonical_parquet_batches(
        repository_root,
        (path,),
        schema,
        batch_size=4_096,
    ):
        yield from _batch_rows(batch)


def _sample_index(row: Mapping[str, object]) -> int:
    value = row.get("sample_index")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactError("trajectory sample_index is not an integer")
    return value


def _trajectory_from_rows(rows: Sequence[Mapping[str, object]]) -> Trajectory:
    samples = tuple(
        TrajectorySampleRecord(**dict(row))  # type: ignore[arg-type]
        for row in sorted(rows, key=_sample_index)
    )
    flags: list[str] = []
    for sample in samples:
        for flag in sample.quality_flags:
            if flag not in flags:
                flags.append(flag)
    return Trajectory(
        scenario_id=samples[0].scenario_id,
        agent_id=samples[0].agent_id,
        trajectory_id=samples[0].trajectory_id,
        samples=samples,
        origin_type=samples[0].origin_type,
        quality_flags=tuple(flags),
    )


def _load_av2_scenarios(
    repository_root: Path,
    cache_directories: Sequence[str],
    included_agent_ids: frozenset[str],
    included_trajectory_ids: frozenset[str],
) -> tuple[
    tuple[
        ScenarioRecord,
        CoordinateFrameRecord,
        tuple[AgentRecord, ...],
        tuple[Trajectory, ...],
    ],
    ...,
]:
    bundles = []
    for cache_directory in cache_directories:
        root = Path(cache_directory)
        scenario_rows = tuple(
            _rows(
                repository_root,
                root / "scenario_manifest.parquet",
                CanonicalSchemaName.SCENARIO_MANIFEST,
            )
        )
        frame_rows = tuple(
            _rows(
                repository_root,
                root / "coordinate_frame_metadata.parquet",
                CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            )
        )
        if len(scenario_rows) != 1 or len(frame_rows) != 1:
            raise ArtifactError("AV2 cache entry must contain one scenario and frame")
        agents = tuple(
            AgentRecord(**row)  # type: ignore[arg-type]
            for row in _rows(
                repository_root,
                root / "agent_metadata.parquet",
                CanonicalSchemaName.AGENT_METADATA,
            )
            if row["agent_id"] in included_agent_ids
        )
        grouped: dict[str, list[Mapping[str, object]]] = {}
        for row in _rows(
            repository_root,
            root / "trajectory_samples.parquet",
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        ):
            trajectory_id = str(row["trajectory_id"])
            if trajectory_id in included_trajectory_ids:
                grouped.setdefault(trajectory_id, []).append(row)
        trajectories = tuple(
            _trajectory_from_rows(grouped[trajectory_id])
            for trajectory_id in sorted(grouped)
        )
        scenario = replace(
            ScenarioRecord(**scenario_rows[0]),  # type: ignore[arg-type]
            agent_count=len(agents),
        )
        bundles.append(
            (
                scenario,
                CoordinateFrameRecord(**frame_rows[0]),  # type: ignore[arg-type]
                agents,
                trajectories,
            )
        )
    return tuple(bundles)


def _artifact_checksums(
    artifacts: ProceduralTapeArtifacts,
) -> tuple[str, str, str]:
    return (
        artifacts.tape_manifest.written_artifact.content_checksum,
        artifacts.procedural_tracks.written_artifact.content_checksum,
        artifacts.procedural_segments.written_artifact.content_checksum,
    )


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
        f"piecewise-linear:{label}:first",
        tape,
    )
    second = materialize_procedural_tape(
        repository_root,
        generated_root,
        f"piecewise-linear:{label}:repeat",
        tape,
    )
    verify_piecewise_linear_artifacts(
        repository_root,
        first,
        trajectories,
        _CONFIG,
        expected_tape=tape,
    )
    verify_piecewise_linear_artifacts(
        repository_root,
        second,
        trajectories,
        _CONFIG,
        expected_tape=tape,
    )
    first_checksums = _artifact_checksums(first)
    if first_checksums != _artifact_checksums(second):
        raise ArtifactError("equivalent tape Parquet checksums differ")
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
        "mean": sum(values) / len(values) if values else None,
        "median": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
    }


def _run_dataset_gate(
    repository_root: Path,
    generated_root: Path,
    label: str,
    bundles: Sequence[
        tuple[
            ScenarioRecord,
            CoordinateFrameRecord,
            Sequence[AgentRecord],
            Sequence[Trajectory],
        ]
    ],
    *,
    validation_identity: str,
    source_bytes: int,
    exact_segment_count: int,
    exact_procedural_bytes: int,
    strict_improvement: bool,
) -> dict[str, object]:
    counts: dict[str, int] = {
        "scenario_count": len(bundles),
        "trajectory_count": 0,
        "source_sample_count": 0,
        "valid_sample_count": 0,
        "invalid_sample_count": 0,
        "valid_run_count": 0,
        "hold_segment_count": 0,
        "linear_segment_count": 0,
        "segment_count": 0,
    }
    checksum_rows = []
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
            tape = encode_scenario_piecewise_linear(
                scenario,
                frame,
                agents,
                trajectories,
                _CONFIG,
                source_validation_report_identity=validation_identity,
            )
            encoding_seconds += time.perf_counter() - operation_started

            operation_started = time.perf_counter()
            source_by_id = {
                trajectory.trajectory_id: trajectory for trajectory in trajectories
            }
            for track in tape.tracks:
                validation = validate_piecewise_linear_replay(
                    track,
                    source_by_id[track.trajectory_id],
                    _CONFIG,
                )
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
            counts["hold_segment_count"] += sum(
                segment.primitive_type is ProceduralPrimitiveType.HOLD
                for track in tape.tracks
                for segment in track.segments
            )
            counts["linear_segment_count"] += sum(
                segment.primitive_type is ProceduralPrimitiveType.LINEAR
                for track in tape.tracks
                for segment in track.segments
            )
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
                    "scenario_id": scenario.scenario_id,
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
    segment_reduction = exact_segment_count - segment_count
    byte_reduction = exact_procedural_bytes - procedural_bytes
    maximum_error = max(position_errors, default=0.0)
    if maximum_error > (_CONFIG.maximum_position_error_m + POSITION_COMPARISON_GUARD_M):
        raise ValidationError("dataset gate exceeds configured position bound")
    if invalid_gap_samples_with_state:
        raise ValidationError("dataset gate bridged an invalid source gap")
    if strict_improvement:
        if segment_reduction <= 0:
            raise ValidationError("segment count did not improve over exact baseline")
        if byte_reduction <= 0:
            raise ValidationError("procedural bytes did not improve exact baseline")
    elif segment_reduction < 0:
        raise ValidationError("segment count exceeds exact baseline")
    trajectory_count = counts["trajectory_count"]
    return {
        "batch": _BATCH,
        "gate": label,
        "status": "PASS",
        "configuration": {
            "maximum_position_error_m": _CONFIG.maximum_position_error_m,
            "floating_comparison_guard_m": POSITION_COMPARISON_GUARD_M,
            "encoder_parameters_identity": encoder_parameters_identity(_CONFIG),
        },
        "counts": counts,
        "exact_baseline": {
            "commit": _REQUIRED_STARTING_HEAD,
            "segment_count": exact_segment_count,
            "procedural_artifact_bytes": exact_procedural_bytes,
        },
        "compression": {
            "segment_reduction": segment_reduction,
            "segment_ratio_to_exact": segment_count / exact_segment_count,
            "byte_reduction": byte_reduction,
            "byte_ratio_to_exact": procedural_bytes / exact_procedural_bytes,
            "procedural_to_source_byte_ratio": (
                procedural_bytes / source_bytes if source_bytes else None
            ),
            "exact_to_source_byte_ratio": (
                exact_procedural_bytes / source_bytes if source_bytes else None
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


def _synthetic_bundles(
    scenarios: Sequence[SyntheticScenario],
) -> tuple[
    tuple[
        ScenarioRecord,
        CoordinateFrameRecord,
        Sequence[AgentRecord],
        Sequence[Trajectory],
    ],
    ...,
]:
    return tuple(
        (
            scenario.scenario,
            scenario.coordinate_frame,
            scenario.agents,
            scenario.trajectories,
        )
        for scenario in scenarios
    )


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(canonical_json_text(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _git_head(repository_root: Path) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository_root.as_posix()}",
            "rev-parse",
            "HEAD",
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def run(repository_root: Path, generated_root: Path) -> Mapping[str, object]:
    """Execute both gates and write compact tracked evidence."""
    if _git_head(repository_root) != _REQUIRED_STARTING_HEAD:
        raise ArtifactError("current HEAD differs from the required Batch 3.1 commit")
    evidence_root = repository_root / "results/phase3/piecewise_linear_codec"
    provider_root = repository_root / "results/phase2/av2_provider_pilot"
    exact_root = repository_root / "results/phase3/exact_codec_baseline"
    exact_evidence = _read_json(exact_root / "evidence.json")
    exact_synthetic = _read_json(exact_root / "synthetic_evidence.json")
    exact_av2 = _read_json(exact_root / "av2_provider_evidence.json")
    if (
        _mapping(exact_synthetic["counts"])["segment_count"]
        != _EXACT_SYNTHETIC_SEGMENTS
        or exact_synthetic["procedural_artifact_bytes"] != _EXACT_SYNTHETIC_BYTES
        or _mapping(exact_av2["counts"])["segment_count"] != _EXACT_AV2_SEGMENTS
        or exact_av2["procedural_artifact_bytes"] != _EXACT_AV2_BYTES
    ):
        raise ArtifactError("committed exact-baseline evidence differs")

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
    synthetic = _run_dataset_gate(
        repository_root,
        generated_root / "synthetic",
        "synthetic",
        _synthetic_bundles(synthetic_dataset.scenarios),
        validation_identity="synthetic-validation:v1",
        source_bytes=trajectories_to_table(synthetic_trajectories).nbytes,
        exact_segment_count=_EXACT_SYNTHETIC_SEGMENTS,
        exact_procedural_bytes=_EXACT_SYNTHETIC_BYTES,
        strict_improvement=False,
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
    if len(av2_trajectories) != len(included_trajectory_ids):
        raise ArtifactError("loaded AV2 trajectory count differs from inclusion set")
    av2 = _run_dataset_gate(
        repository_root,
        generated_root / "av2",
        "genuine_av2_provider",
        av2_bundles,
        validation_identity=validation_identity,
        source_bytes=trajectories_to_table(av2_trajectories).nbytes,
        exact_segment_count=_EXACT_AV2_SEGMENTS,
        exact_procedural_bytes=_EXACT_AV2_BYTES,
        strict_improvement=True,
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
    }

    synthetic_path = evidence_root / "synthetic_evidence.json"
    av2_path = evidence_root / "av2_provider_evidence.json"
    summary_path = evidence_root / "summary.md"
    evidence_path = evidence_root / "evidence.json"
    _write(synthetic_path, synthetic)
    _write(av2_path, av2)
    synthetic_counts = _mapping(synthetic["counts"])
    synthetic_compression = _mapping(synthetic["compression"])
    av2_counts = _mapping(av2["counts"])
    av2_compression = _mapping(av2["compression"])
    summary = (
        f"# {_TITLE}\n\n"
        f"- Batch: {_BATCH}\n"
        "- Configuration: fixed development setting at 0.10 m maximum "
        "source-timestamp position error\n"
        "- Optimality: minimum segment count for the fixed setting\n"
        "- Synthetic gate: PASS\n"
        "- Genuine AV2 provider gate: PASS\n"
        f"- Synthetic trajectories: {synthetic_counts['trajectory_count']}\n"
        f"- Synthetic segments: {synthetic_counts['segment_count']} "
        f"(reduction {synthetic_compression['segment_reduction']})\n"
        f"- AV2 scenarios: {av2_counts['scenario_count']}\n"
        f"- AV2 trajectories: {av2_counts['trajectory_count']}\n"
        f"- AV2 segments: {av2_counts['segment_count']} "
        f"(reduction {av2_compression['segment_reduction']})\n"
        f"- AV2 procedural bytes: {av2['procedural_artifact_bytes']} "
        f"(reduction {av2_compression['byte_reduction']})\n"
        "- Equivalent repeat Parquet checksums: matched\n"
        "- Provider source/cache mutation: none\n\n"
        "The 0.10 m value is a fixed development setting, not a release "
        "threshold selection. Scientific comparisons across codecs and "
        "tolerances remain Phase 4 work.\n\n"
        f"{_PASS_STATEMENT}\n"
    )
    _write_text(summary_path, summary)
    component_paths = (synthetic_path, av2_path, summary_path)
    evidence = {
        "title": _TITLE,
        "batch": _BATCH,
        "batch_decision": "achieved",
        "pass_statement": _PASS_STATEMENT,
        "synthetic_gate": "PASS",
        "genuine_av2_provider_gate": "PASS",
        "configuration": {
            "maximum_position_error_m": _CONFIG.maximum_position_error_m,
            "encoder_parameters_identity": encoder_parameters_identity(_CONFIG),
            "fixed_development_setting": True,
            "release_threshold_selection": False,
        },
        "exact_baseline_reference": {
            "commit": _REQUIRED_STARTING_HEAD,
            "evidence_sha256": _sha256(exact_root / "evidence.json"),
            "batch_decision": exact_evidence["batch_decision"],
        },
        "component_sha256": {path.name: _sha256(path) for path in component_paths},
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.platform(),
            "machine": platform.machine(),
            "wsl_used": False,
            "wsl_limitation": "WSL has no installed Linux distribution",
            "execution_path": "<repository-root>",
            "execution_policy": "Windows CPU sequential bounded per scenario",
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
        default=Path("results/generated/phase3/piecewise_linear_codec"),
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
