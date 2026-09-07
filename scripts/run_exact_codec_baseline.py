"""Run the Phase 3 exact-codec synthetic and genuine AV2 evidence gates."""

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time
import tracemalloc

from kinematicweave.canonical import canonical_json_bytes, canonical_json_text
from kinematicweave.codecs.exact import (
    decode_source_endpoint_states,
    encode_scenario_exact,
    replay_track,
)
from kinematicweave.data.parquet_io import (
    read_canonical_parquet_table,
    trajectories_to_table,
)
from kinematicweave.data.procedural_artifacts import (
    ProceduralTapeArtifacts,
    materialize_procedural_tape,
    verify_procedural_tape_artifacts,
)
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    canonical_schema_names,
    get_schema_definition,
    schema_definition_to_dict,
    schema_fingerprint,
)
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

_BATCH = "3.1"
_TITLE = "Phase 3 Exact Procedural-Line Codec Baseline"
_PASS_STATEMENT = (
    "M3 exact-codec baseline achieved on synthetic and genuine AV2 provider data."
)


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError(f"cannot read evidence input: {path.name}") from error
    if not isinstance(value, Mapping):
        raise ArtifactError(f"evidence input is not an object: {path.name}")
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


def _flags(rows: Sequence[TrajectorySampleRecord]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for sample in rows:
        for flag in sample.quality_flags:
            if flag not in seen:
                seen.add(flag)
                output.append(flag)
    return tuple(output)


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
    return Trajectory(
        scenario_id=samples[0].scenario_id,
        agent_id=samples[0].agent_id,
        trajectory_id=samples[0].trajectory_id,
        samples=samples,
        origin_type=samples[0].origin_type,
        quality_flags=_flags(samples),
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
        scenario_rows = read_canonical_parquet_table(
            repository_root,
            (root / "scenario_manifest.parquet",),
            CanonicalSchemaName.SCENARIO_MANIFEST,
        ).to_pylist()
        frame_rows = read_canonical_parquet_table(
            repository_root,
            (root / "coordinate_frame_metadata.parquet",),
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        ).to_pylist()
        agent_rows = read_canonical_parquet_table(
            repository_root,
            (root / "agent_metadata.parquet",),
            CanonicalSchemaName.AGENT_METADATA,
        ).to_pylist()
        sample_rows = read_canonical_parquet_table(
            repository_root,
            (root / "trajectory_samples.parquet",),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        ).to_pylist()
        if len(scenario_rows) != 1 or len(frame_rows) != 1:
            raise ArtifactError("AV2 cache entry must contain one scenario and frame")
        scenario = ScenarioRecord(**scenario_rows[0])
        frame = CoordinateFrameRecord(**frame_rows[0])
        agents = tuple(
            AgentRecord(**row)
            for row in agent_rows
            if row["agent_id"] in included_agent_ids
        )
        grouped: dict[str, list[Mapping[str, object]]] = {}
        for row in sample_rows:
            trajectory_id = str(row["trajectory_id"])
            if trajectory_id in included_trajectory_ids:
                grouped.setdefault(trajectory_id, []).append(row)
        trajectories = tuple(
            _trajectory_from_rows(grouped[trajectory_id])
            for trajectory_id in sorted(grouped)
        )
        scenario = replace(scenario, agent_count=len(agents))
        bundles.append((scenario, frame, agents, trajectories))
    return tuple(bundles)


def _artifact_checksums(
    artifacts: ProceduralTapeArtifacts,
) -> tuple[str, str, str]:
    return (
        artifacts.tape_manifest.written_artifact.content_checksum,
        artifacts.procedural_tracks.written_artifact.content_checksum,
        artifacts.procedural_segments.written_artifact.content_checksum,
    )


def _assert_exact_replay(
    tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
) -> tuple[float, int]:
    source_by_id = {trajectory.trajectory_id: trajectory for trajectory in trajectories}
    maximum_position_error = 0.0
    replay_count = 0
    for track in tape.tracks:
        source = source_by_id[track.trajectory_id]
        decoded = dict(decode_source_endpoint_states(track))
        expected_indices = tuple(
            sample.sample_index for sample in source.samples if sample.is_valid
        )
        if tuple(decoded) != expected_indices:
            raise ValidationError("decoded source endpoint indices differ")
        for sample_index in expected_indices:
            sample = source.samples[sample_index]
            state = decoded[sample_index]
            replayed = replay_track(track, sample.timestamp_ns)
            if replayed != state:
                raise ValidationError("timestamp replay differs from endpoint decode")
            expected = (
                sample.timestamp_ns,
                sample.x_m,
                sample.y_m,
                sample.z_m,
                sample.heading_rad,
                sample.velocity_x_mps,
                sample.velocity_y_mps,
            )
            actual = (
                state.timestamp_ns,
                state.x_m,
                state.y_m,
                state.z_m,
                state.heading_rad,
                state.velocity_x_mps,
                state.velocity_y_mps,
            )
            if actual != expected:
                raise ValidationError("decoded source fields are not exact")
            error = math.hypot(state.x_m - sample.x_m, state.y_m - sample.y_m)
            maximum_position_error = max(maximum_position_error, error)
            replay_count += 1
        joined = {
            (
                segment.source_start_sample_index,
                segment.source_end_sample_index,
            )
            for segment in track.segments
        }
        for left, right in zip(source.samples, source.samples[1:], strict=False):
            if left.is_valid and right.is_valid:
                continue
            if (left.sample_index, right.sample_index) in joined:
                raise ValidationError("codec bridged an invalid-sample gap")
    return maximum_position_error, replay_count


def _scenario_metrics(
    tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
) -> dict[str, int]:
    samples = tuple(
        sample for trajectory in trajectories for sample in trajectory.samples
    )
    return {
        "trajectory_count": len(trajectories),
        "source_sample_count": len(samples),
        "valid_sample_count": sum(sample.is_valid for sample in samples),
        "invalid_sample_count": sum(not sample.is_valid for sample in samples),
        "run_count": sum(track.run_count for track in tape.tracks),
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
        "source_duration_ns": sum(
            trajectory.end_time_ns - trajectory.start_time_ns
            for trajectory in trajectories
        ),
        "encoded_duration_ns": sum(
            segment.end_time_ns - segment.start_time_ns
            for track in tape.tracks
            for segment in track.segments
        ),
    }


def _add_counts(target: dict[str, int], values: Mapping[str, int]) -> None:
    for key, value in values.items():
        target[key] = target.get(key, 0) + value


def _materialize_twice(
    repository_root: Path,
    generated_root: Path,
    label: str,
    tape: ProceduralTape,
) -> tuple[int, tuple[str, str, str]]:
    first = materialize_procedural_tape(
        repository_root,
        generated_root,
        f"exact-codec:{label}:first",
        tape,
    )
    second = materialize_procedural_tape(
        repository_root,
        generated_root,
        f"exact-codec:{label}:repeat",
        tape,
    )
    verify_procedural_tape_artifacts(repository_root, first, expected_tape=tape)
    verify_procedural_tape_artifacts(repository_root, second, expected_tape=tape)
    first_checksums = _artifact_checksums(first)
    if first_checksums != _artifact_checksums(second):
        raise ArtifactError("equivalent tape Parquet checksums differ")
    output_bytes = (
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
    return output_bytes, first_checksums


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
) -> dict[str, object]:
    counts: dict[str, int] = {"scenario_count": len(bundles)}
    checksum_rows = []
    procedural_bytes = 0
    maximum_position_error = 0.0
    replay_count = 0
    errors: list[str] = []
    encoding_seconds = 0.0
    replay_seconds = 0.0
    artifact_seconds = 0.0
    started = time.perf_counter()
    cpu_started = time.process_time()
    tracemalloc.start()
    try:
        for scenario, frame, agents, trajectories in bundles:
            operation_started = time.perf_counter()
            tape = encode_scenario_exact(
                scenario,
                frame,
                agents,
                trajectories,
                source_validation_report_identity=validation_identity,
            )
            encoding_seconds += time.perf_counter() - operation_started
            operation_started = time.perf_counter()
            position_error, scenario_replay_count = _assert_exact_replay(
                tape, trajectories
            )
            replay_seconds += time.perf_counter() - operation_started
            maximum_position_error = max(maximum_position_error, position_error)
            replay_count += scenario_replay_count
            _add_counts(counts, _scenario_metrics(tape, trajectories))
            operation_started = time.perf_counter()
            output_bytes, checksums = _materialize_twice(
                repository_root,
                generated_root,
                f"{label}:{scenario.scenario_id}",
                tape,
            )
            artifact_seconds += time.perf_counter() - operation_started
            procedural_bytes += output_bytes
            checksum_rows.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "tape_id": tape.tape_id,
                    "parquet_sha256": list(checksums),
                }
            )
    except (ArtifactError, OSError, ValidationError) as error:
        errors.append(f"{type(error).__name__}: {error}")
        raise
    finally:
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    elapsed = time.perf_counter() - started
    cpu_seconds = time.process_time() - cpu_started
    trajectory_count = counts.get("trajectory_count", 0)
    return {
        "batch": _BATCH,
        "gate": label,
        "status": "PASS",
        "counts": counts,
        "maximum_source_endpoint_position_error_m": maximum_position_error,
        "replayed_valid_sample_count": replay_count,
        "source_bytes": source_bytes,
        "procedural_artifact_bytes": procedural_bytes,
        "procedural_to_source_byte_ratio": (
            procedural_bytes / source_bytes if source_bytes else None
        ),
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
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
            "execution_policy": "sequential_bounded_per_scenario",
        },
        "errors": errors,
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


def _schema_snapshot() -> dict[str, object]:
    return {
        "batch": _BATCH,
        "schema_version": "1.0",
        "schemas": [
            {
                **schema_definition_to_dict(get_schema_definition(name)),
                "fingerprint": schema_fingerprint(name),
            }
            for name in canonical_schema_names()
        ],
    }


def run(repository_root: Path, generated_root: Path) -> Mapping[str, object]:
    """Execute both gates and write compact tracked evidence."""
    evidence_root = repository_root / "results/phase3/exact_codec_baseline"
    provider_root = repository_root / "results/phase2/av2_provider_pilot"
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
        repository_root / str(_mapping(item)["relative_path"]) for item in acquired
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
    synthetic_source_bytes = trajectories_to_table(synthetic_trajectories).nbytes
    synthetic = _run_dataset_gate(
        repository_root,
        generated_root / "synthetic",
        "synthetic",
        _synthetic_bundles(synthetic_dataset.scenarios),
        validation_identity="synthetic-validation:v1",
        source_bytes=synthetic_source_bytes,
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
    av2_source_bytes = trajectories_to_table(av2_trajectories).nbytes
    av2 = _run_dataset_gate(
        repository_root,
        generated_root / "av2",
        "genuine_av2_provider",
        av2_bundles,
        validation_identity=validation_identity,
        source_bytes=av2_source_bytes,
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

    schema_path = evidence_root / "schema_snapshot.json"
    synthetic_path = evidence_root / "synthetic_evidence.json"
    av2_path = evidence_root / "av2_provider_evidence.json"
    summary_path = evidence_root / "summary.md"
    evidence_path = evidence_root / "evidence.json"
    _write(schema_path, _schema_snapshot())
    _write(synthetic_path, synthetic)
    _write(av2_path, av2)
    synthetic_counts = _mapping(synthetic["counts"])
    av2_counts = _mapping(av2["counts"])
    summary = (
        f"# {_TITLE}\n\n"
        f"- Batch: {_BATCH}\n"
        "- Synthetic gate: PASS\n"
        "- Genuine AV2 provider gate: PASS\n"
        f"- Synthetic scenarios: {synthetic_counts['scenario_count']}\n"
        f"- AV2 scenarios: {av2_counts['scenario_count']}\n"
        f"- AV2 trajectories: {av2_counts['trajectory_count']}\n"
        "- Maximum source-endpoint position error: 0.0 m\n"
        "- Invalid-sample gaps bridged: 0\n"
        "- Equivalent repeat Parquet checksums: matched\n"
        "- Provider source/cache mutation: none\n\n"
        f"{_PASS_STATEMENT}\n"
    )
    _write_text(summary_path, summary)
    component_paths = (schema_path, synthetic_path, av2_path, summary_path)
    evidence = {
        "title": _TITLE,
        "batch": _BATCH,
        "batch_decision": "achieved",
        "pass_statement": _PASS_STATEMENT,
        "synthetic_gate": "PASS",
        "genuine_av2_provider_gate": "PASS",
        "component_sha256": {path.name: _sha256(path) for path in component_paths},
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.platform(),
            "machine": platform.machine(),
            "wsl_used": False,
            "execution_path": "<repository-root>",
        },
        "generated_artifacts_tracked": False,
    }
    _write(evidence_path, evidence)
    return evidence


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ArtifactError("evidence list item is not an object")
    return value


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
        default=Path("results/generated/phase3/exact_codec_baseline"),
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
