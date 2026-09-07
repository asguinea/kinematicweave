"""Run the frozen Batch 4.4 matched-budget exploratory motion sweep."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import time
from typing import cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import run_directory_name
from kinematicweave.baselines.motion import (
    BaselineMethod,
    MotionBaselineConfig,
    encode_scenario_baseline,
    included_motion_trajectories,
    read_canonical_scenario_bundle,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.codecs.exact import encode_scenario_exact
from kinematicweave.codecs.hermite import HermiteCodecConfig, encode_scenario_hermite
from kinematicweave.codecs.piecewise_linear import (
    PiecewiseLinearCodecConfig,
    encode_scenario_piecewise_linear,
)
from kinematicweave.codecs.velocity_bounded import (
    VelocityBoundedCodecConfig,
    encode_scenario_velocity_bounded,
)
from kinematicweave.data.baseline_artifacts import (
    EquivalentArtifactResult,
    materialize_equivalent_procedural_artifacts,
    materialize_equivalent_raw_artifacts,
)
from kinematicweave.data.motion_metric_artifacts import (
    MotionMetricTable,
    write_metric_parquet,
)
from kinematicweave.data.motion_sweep_artifacts import (
    MotionSweepTable,
    write_sweep_parquet,
)
from kinematicweave.data.procedural_artifacts import verify_procedural_tape_artifacts
from kinematicweave.domain.procedural import ProceduralPrimitiveType, ProceduralTape
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.errors import ArtifactError, ResourceLimitError, ValidationError
from kinematicweave.events.semantic_motion import SemanticMotionConfig
from kinematicweave.experiments.motion_cohort import MotionCohortUnit
from kinematicweave.experiments.motion_sweep import (
    BudgetDimension,
    CheckpointStatus,
    ConfigurationBudgetRecord,
    ScenarioConfigurationExecution,
    SweepCheckpointState,
    SweepConfiguration,
    SweepFailureRecord,
    SweepManifest,
    SweepParameterPoint,
    SweepResultSummary,
    checkpoint_path,
    execution_order,
    frozen_parameter_grid,
    load_checkpoint,
    read_ranked_development_prefix,
    select_matched_budgets,
    write_checkpoint,
)
from kinematicweave.logging import configure_logging, get_logger
from kinematicweave.metrics.motion import (
    descriptive_statistics,
    evaluate_trajectory_motion,
)
from kinematicweave.metrics.semantic import evaluate_trajectory_events
from kinematicweave.resources import peak_process_rss_bytes

_BATCH = "4.4"
_STARTING_HEAD = "ffd5f6656a1d4d5d7bedf571dfbd1ca3b8f67d5c"
_COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)
_BATCH4_2_EVIDENCE = "100c0be635b230e7ce61022d8f40019b5b7ea73a6860a819401b4a2aea187f65"
_BATCH4_3_CAMPAIGN = "03440d32241c0e55d490cf617cd1b2d9ed1a546865372b8dc155f06c696e3277"
_PASS_STATEMENT = (
    "Phase 4 matched-budget sweep engine verified on 25 genuine AV2 "
    "development scenarios."
)
_SEMANTIC_CONFIG = SemanticMotionConfig()
_ESTIMATED_REQUIRED_BYTES = 3_000_000_000
_LOGGER = get_logger("scripts.run_motion_sweep")

Encoder = Callable[
    [
        ScenarioRecord,
        CoordinateFrameRecord,
        Sequence[AgentRecord],
        Sequence[Trajectory],
    ],
    ProceduralTape,
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Mapping[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ArtifactError(f"required JSON artifact is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ArtifactError(f"required JSON artifact is unreadable: {path}") from error
    if not isinstance(value, Mapping):
        raise ArtifactError(f"JSON artifact root must be a mapping: {path}")
    return cast(Mapping[str, object], value)


def _write_json(path: Path, value: object, *, immutable: bool = True) -> None:
    if immutable and (path.exists() or path.is_symlink()):
        raise ArtifactError(f"artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(canonical_json_text(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


def _as_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ArtifactError(f"{field_name} must be a nonnegative integer")
    return value


def _as_float(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ArtifactError(f"{field_name} must be numeric")
    normalized = float(value)
    if not 0.0 <= normalized < float("inf"):
        raise ArtifactError(f"{field_name} must be finite and nonnegative")
    return normalized


def _verify_evidence_root(
    root: Path, *, decision_field: str, decision: str
) -> Mapping[str, object]:
    evidence = _read_json(root / "evidence.json")
    if evidence.get(decision_field) != decision:
        raise ArtifactError(f"accepted evidence decision differs: {root}")
    checksums = evidence.get("evidence_file_sha256")
    if not isinstance(checksums, Mapping):
        raise ArtifactError(f"evidence checksums are missing: {root}")
    for name, expected in checksums.items():
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or _sha256(root / name) != expected
        ):
            raise ArtifactError(
                f"accepted evidence checksum differs: {root / str(name)}"
            )
    return evidence


def _verify_inputs(
    cohort_root: Path,
    baseline_root: Path,
    metric_root: Path,
) -> tuple[tuple[MotionCohortUnit, ...], Mapping[str, object]]:
    cohort = _verify_evidence_root(
        cohort_root,
        decision_field="cohort_decision",
        decision="frozen",
    )
    baseline = _verify_evidence_root(
        baseline_root,
        decision_field="batch_decision",
        decision="achieved",
    )
    metric = _verify_evidence_root(
        metric_root,
        decision_field="batch_decision",
        decision="achieved",
    )
    if (
        cohort.get("cohort_identity") != _COHORT_IDENTITY
        or baseline.get("cohort_identity") != _COHORT_IDENTITY
        or metric.get("cohort_identity") != _COHORT_IDENTITY
        or metric.get("campaign_identity") != _BATCH4_3_CAMPAIGN
        or baseline.get("source_validation_identity") != _VALIDATION_IDENTITY
        or metric.get("development_validation_identity") != _VALIDATION_IDENTITY
        or cohort.get("official_source")
        != "s3://argoverse/datasets/av2/motion-forecasting/"
        or baseline.get("pilot_or_test_outcomes_accessed") is not False
        or metric.get("pilot_or_test_outcomes_accessed") is not False
    ):
        raise ArtifactError("accepted Phase 4 identity or access evidence differs")
    baseline_checksum = _sha256(baseline_root / "evidence.json")
    if baseline_checksum != _BATCH4_2_EVIDENCE:
        raise ArtifactError("Batch 4.2 evidence identity differs")

    manifest_path = cohort_root / "cohort_manifest.json"
    cohort_checksums = cast(Mapping[str, object], cohort["evidence_file_sha256"])
    if _sha256(manifest_path) != cohort_checksums.get("cohort_manifest.json"):
        raise ArtifactError("frozen cohort manifest checksum differs")
    units = read_ranked_development_prefix(manifest_path, 25)

    metric_contract = _read_json(metric_root / "metrics_contract.json")
    development_ids = metric_contract.get("development_scenario_ids")
    if (
        not isinstance(development_ids, Sequence)
        or isinstance(development_ids, (str, bytes))
        or list(development_ids[:25]) != [unit.source_scenario_id for unit in units]
    ):
        raise ArtifactError("ranked development prefix differs from accepted metrics")
    baseline_contract = _read_json(baseline_root / "baseline_contract.json")
    baseline_ids = baseline_contract.get("development_scenario_ids")
    if (
        not isinstance(baseline_ids, Sequence)
        or isinstance(baseline_ids, (str, bytes))
        or list(baseline_ids[:25]) != [unit.source_scenario_id for unit in units]
    ):
        raise ArtifactError("ranked development prefix differs from accepted baselines")
    return units, cohort


def _cache_entry(cache_root: Path, unit: MotionCohortUnit) -> Path:
    key = unit.materialization_cache_key
    return cache_root / "entries" / key[:2] / key


def _bundle(
    repository_root: Path,
    cache_root: Path,
    unit: MotionCohortUnit,
) -> tuple[
    ScenarioRecord,
    CoordinateFrameRecord,
    tuple[AgentRecord, ...],
    tuple[Trajectory, ...],
]:
    source = read_canonical_scenario_bundle(
        repository_root,
        _cache_entry(cache_root, unit),
    )
    if source.scenario.source_scenario_id != unit.source_scenario_id:
        raise ArtifactError("canonical scenario differs from frozen subset")
    trajectories = included_motion_trajectories(source.trajectories)
    agent_ids = {trajectory.agent_id for trajectory in trajectories}
    agents = tuple(agent for agent in source.agents if agent.agent_id in agent_ids)
    return (
        replace(source.scenario, agent_count=len(agents)),
        source.coordinate_frame,
        agents,
        trajectories,
    )


def _baseline_config(point: SweepParameterPoint) -> MotionBaselineConfig | None:
    configuration = point.configuration
    if point.family == BaselineMethod.RAW_SAMPLES.value:
        return MotionBaselineConfig(BaselineMethod.RAW_SAMPLES)
    if point.family in {
        BaselineMethod.UNIFORM_LINEAR.value,
        BaselineMethod.UNIFORM_HERMITE.value,
    }:
        return MotionBaselineConfig(
            BaselineMethod(point.family),
            stride=cast(int, configuration["stride"]),
        )
    if point.family == BaselineMethod.FIXED_INTERVAL_LINEAR.value:
        return MotionBaselineConfig(
            BaselineMethod.FIXED_INTERVAL_LINEAR,
            interval_ns=cast(int, configuration["interval_ns"]),
        )
    if point.family == BaselineMethod.RDP_LINEAR.value:
        return MotionBaselineConfig(
            BaselineMethod.RDP_LINEAR,
            maximum_perpendicular_error_m=cast(
                float, configuration["maximum_perpendicular_error_m"]
            ),
        )
    return None


def _encoder(point: SweepParameterPoint) -> Encoder | None:
    baseline = _baseline_config(point)
    if baseline is not None:
        if not baseline.is_encoded:
            return None

        def encode_baseline(
            scenario: ScenarioRecord,
            frame: CoordinateFrameRecord,
            agents: Sequence[AgentRecord],
            trajectories: Sequence[Trajectory],
        ) -> ProceduralTape:
            return encode_scenario_baseline(
                scenario,
                frame,
                agents,
                trajectories,
                baseline,
                source_validation_report_identity=_VALIDATION_IDENTITY,
            )

        return encode_baseline
    configuration = point.configuration
    if point.family == "exact_adjacent":

        def encode_exact(
            scenario: ScenarioRecord,
            frame: CoordinateFrameRecord,
            agents: Sequence[AgentRecord],
            trajectories: Sequence[Trajectory],
        ) -> ProceduralTape:
            return encode_scenario_exact(
                scenario,
                frame,
                agents,
                trajectories,
                source_validation_report_identity=_VALIDATION_IDENTITY,
            )

        return encode_exact
    if point.family == "position_bounded_linear":
        linear_config = PiecewiseLinearCodecConfig(
            cast(float, configuration["maximum_position_error_m"])
        )

        def encode_linear(
            scenario: ScenarioRecord,
            frame: CoordinateFrameRecord,
            agents: Sequence[AgentRecord],
            trajectories: Sequence[Trajectory],
        ) -> ProceduralTape:
            return encode_scenario_piecewise_linear(
                scenario,
                frame,
                agents,
                trajectories,
                linear_config,
                source_validation_report_identity=_VALIDATION_IDENTITY,
            )

        return encode_linear
    if point.family == "unconstrained_hermite":
        hermite_config = HermiteCodecConfig(
            cast(float, configuration["maximum_position_error_m"])
        )

        def encode_hermite(
            scenario: ScenarioRecord,
            frame: CoordinateFrameRecord,
            agents: Sequence[AgentRecord],
            trajectories: Sequence[Trajectory],
        ) -> ProceduralTape:
            return encode_scenario_hermite(
                scenario,
                frame,
                agents,
                trajectories,
                hermite_config,
                source_validation_report_identity=_VALIDATION_IDENTITY,
            )

        return encode_hermite
    if point.family == "position_velocity_hybrid":
        hybrid_config = VelocityBoundedCodecConfig(
            cast(float, configuration["maximum_position_error_m"]),
            cast(float, configuration["maximum_velocity_error_mps"]),
        )

        def encode_hybrid(
            scenario: ScenarioRecord,
            frame: CoordinateFrameRecord,
            agents: Sequence[AgentRecord],
            trajectories: Sequence[Trajectory],
        ) -> ProceduralTape:
            return encode_scenario_velocity_bounded(
                scenario,
                frame,
                agents,
                trajectories,
                hybrid_config,
                source_validation_report_identity=_VALIDATION_IDENTITY,
            )

        return encode_hybrid
    raise ValidationError(f"unsupported sweep family: {point.family}")


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _file_descriptor(path: Path) -> dict[str, object]:
    row_count: int | None = None
    row_group_count: int | None = None
    if path.suffix == ".parquet":
        metadata = pq.ParquetFile(path).metadata
        row_count = metadata.num_rows
        row_group_count = metadata.num_row_groups
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "row_count": row_count,
        "row_group_count": row_group_count,
    }


def _artifact_summary(
    generated_root: Path,
    result: EquivalentArtifactResult,
) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    for run_id in (result.first_run_id, result.repeat_run_id):
        run = generated_root / "runs" / run_directory_name(run_id)
        if not (run / ".run.complete").is_file() or (run / ".run.partial").exists():
            raise ArtifactError("representation run completion marker differs")
        names = (
            ("trajectory_samples.parquet",)
            if (run / "trajectory_samples.parquet").is_file()
            else (
                "tape_manifest.parquet",
                "procedural_tracks.parquet",
                "procedural_segments.parquet",
                "codec_summary.json",
            )
        )
        files = [_file_descriptor(run / name) for name in names]
        runs.append(
            {
                "run_id": run_id,
                "tree_size_bytes": _tree_size(run),
                "files": files,
            }
        )
    first_hashes = {
        cast(str, item["name"]): item["sha256"]
        for item in cast(list[dict[str, object]], runs[0]["files"])
        if cast(str, item["name"]).endswith(".parquet")
    }
    repeat_hashes = {
        cast(str, item["name"]): item["sha256"]
        for item in cast(list[dict[str, object]], runs[1]["files"])
        if cast(str, item["name"]).endswith(".parquet")
    }
    if first_hashes != repeat_hashes or not result.checksum_agreement:
        raise ArtifactError(
            "equivalent representation Parquet artifacts differ: "
            f"first={first_hashes!r}, repeat={repeat_hashes!r}, "
            f"accepted_agreement={result.checksum_agreement!r}"
        )
    return {
        "encoded_bytes": result.encoded_bytes,
        "generated_disk_bytes": result.generated_disk_bytes,
        "checksum_agreement": True,
        "runs": runs,
    }


def _verify_artifact_summary(
    generated_root: Path,
    summary: Mapping[str, object],
) -> None:
    runs = summary.get("runs")
    if (
        not isinstance(runs, Sequence)
        or isinstance(runs, (str, bytes))
        or len(runs) != 2
    ):
        raise ArtifactError("checkpoint artifact runs are invalid")
    checksum_rows: list[dict[str, str]] = []
    for run_value in runs:
        if not isinstance(run_value, Mapping):
            raise ArtifactError("checkpoint artifact run is invalid")
        run_id = run_value.get("run_id")
        files = run_value.get("files")
        if (
            not isinstance(run_id, str)
            or not isinstance(files, Sequence)
            or isinstance(files, (str, bytes))
        ):
            raise ArtifactError("checkpoint artifact descriptor is invalid")
        run = generated_root / "runs" / run_directory_name(run_id)
        if not (run / ".run.complete").is_file() or (run / ".run.partial").exists():
            raise ArtifactError("checkpoint representation run is incomplete")
        hashes: dict[str, str] = {}
        for item in files:
            if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                raise ArtifactError("checkpoint representation file is invalid")
            path = run / cast(str, item["name"])
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != item.get("size_bytes")
                or _sha256(path) != item.get("sha256")
            ):
                raise ArtifactError("checkpoint representation artifact differs")
            if path.suffix == ".parquet":
                hashes[path.name] = cast(str, item["sha256"])
        checksum_rows.append(hashes)
    if checksum_rows[0] != checksum_rows[1]:
        raise ArtifactError("checkpoint repeat representation differs")


def _primitive_counts(tape: ProceduralTape | None) -> dict[str, int]:
    counts = {"hold": 0, "linear": 0, "cubic_hermite": 0}
    if tape is not None:
        for track in tape.tracks:
            for segment in track.segments:
                counts[ProceduralPrimitiveType(segment.primitive_type).value] += 1
    return counts


def _event_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    by_type: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    for record in records:
        by_type[cast(str, record["event_type"])].append(record)

    def aggregate(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
        source = sum(
            _as_int(row["source_event_count"], "source_event_count") for row in rows
        )
        replay = sum(
            _as_int(row["replay_event_count"], "replay_event_count") for row in rows
        )
        matched = sum(
            _as_int(row["matched_event_count"], "matched_event_count") for row in rows
        )
        precision = matched / replay if replay else (1.0 if source == 0 else 0.0)
        recall = matched / source if source else (1.0 if replay == 0 else 0.0)
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        return {
            "source_event_count": source,
            "replay_event_count": replay,
            "matched_event_count": matched,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    return {
        "overall": aggregate(records),
        "by_type": {
            event_type: aggregate(by_type[event_type]) for event_type in sorted(by_type)
        },
    }


def _motion_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    position = [
        _as_float(value, "position_error")
        for row in records
        for value in cast(Sequence[object], row["position_errors_m"])
    ]
    heading = [
        _as_float(value, "heading_error")
        for row in records
        for value in cast(Sequence[object], row["heading_errors_rad"])
    ]
    velocity = [
        _as_float(value, "velocity_error")
        for row in records
        for value in cast(Sequence[object], row["velocity_errors_mps"])
    ]
    endpoint_position = [
        _as_float(value, "endpoint_position_error")
        for row in records
        for value in cast(Sequence[object], row["endpoint_position_errors_m"])
    ]
    return {
        "position": descriptive_statistics(position).to_dict(),
        "heading": descriptive_statistics(heading).to_dict(),
        "velocity": descriptive_statistics(velocity).to_dict(),
        "endpoint_position": descriptive_statistics(endpoint_position).to_dict(),
        "gap_preservation_passed": all(
            row.get("gap_preservation_passed") is True for row in records
        ),
        "unexpected_replay_state_count": sum(
            _as_int(
                row["unexpected_replay_state_count"],
                "unexpected_replay_state_count",
            )
            for row in records
        ),
    }


def _scenario_execution(
    repository_root: Path,
    cache_root: Path,
    generated_root: Path,
    point: SweepParameterPoint,
    unit: MotionCohortUnit,
    raw_bytes: int | None,
    reporting_identity: tuple[str, str] | None = None,
) -> Mapping[str, object]:
    started = time.perf_counter()
    scenario, frame, agents, trajectories = _bundle(repository_root, cache_root, unit)
    encoder = _encoder(point)
    encoding_started = time.perf_counter()
    tape = None if encoder is None else encoder(scenario, frame, agents, trajectories)
    encoding_seconds = time.perf_counter() - encoding_started
    tracks = (
        {} if tape is None else {track.trajectory_id: track for track in tape.tracks}
    )

    artifact_started = time.perf_counter()
    run_label = f"phase4-sweep:{point.parameter_identity}:{unit.source_scenario_id}"
    generated_results_root = generated_root.relative_to(repository_root)
    if tape is None:
        artifact_result = materialize_equivalent_raw_artifacts(
            repository_root,
            generated_results_root,
            run_label,
            trajectories,
        )
    else:
        artifact_result = materialize_equivalent_procedural_artifacts(
            repository_root,
            generated_results_root,
            run_label,
            tape,
            verifier=lambda artifacts: verify_procedural_tape_artifacts(
                repository_root,
                artifacts,
                expected_tape=tape,
            ),
        )
    artifact_writing_seconds = time.perf_counter() - artifact_started
    verification_started = time.perf_counter()
    artifact_summary = _artifact_summary(generated_root, artifact_result)
    _verify_artifact_summary(generated_root, artifact_summary)
    artifact_verification_seconds = time.perf_counter() - verification_started

    replay_started = time.perf_counter()
    reported_method_id, reported_configuration_id = reporting_identity or (
        point.method_id,
        point.parameter_identity,
    )
    evaluations = [
        evaluate_trajectory_motion(
            reported_method_id,
            reported_configuration_id,
            trajectory,
            tracks.get(trajectory.trajectory_id),
        )
        for trajectory in trajectories
    ]
    replay_seconds = time.perf_counter() - replay_started

    semantic_started = time.perf_counter()
    semantic_tape = tape
    if semantic_tape is None:
        semantic_tape = encode_scenario_exact(
            scenario,
            frame,
            agents,
            trajectories,
            source_validation_report_identity=_VALIDATION_IDENTITY,
        )
    semantic_tracks = {track.trajectory_id: track for track in semantic_tape.tracks}
    event_records = [
        event.to_dict()
        for trajectory, evaluation in zip(trajectories, evaluations, strict=True)
        for event in evaluate_trajectory_events(
            reported_method_id,
            reported_configuration_id,
            trajectory,
            evaluation.replay_trajectory,
            semantic_tracks[trajectory.trajectory_id],
            _SEMANTIC_CONFIG,
        )
    ]
    semantic_seconds = time.perf_counter() - semantic_started
    motion_records = [evaluation.metrics.to_dict() for evaluation in evaluations]
    counts = {
        "source_samples": sum(
            _as_int(row["source_sample_count"], "source_sample_count")
            for row in motion_records
        ),
        "valid_samples": sum(
            _as_int(row["valid_sample_count"], "valid_sample_count")
            for row in motion_records
        ),
        "valid_runs": sum(
            _as_int(row["valid_run_count"], "valid_run_count") for row in motion_records
        ),
        "retained_keyframes": sum(
            _as_int(row["retained_keyframe_count"], "retained_keyframe_count")
            for row in motion_records
        ),
        "segments": sum(
            _as_int(row["segment_count"], "segment_count") for row in motion_records
        ),
    }
    exact_segments = counts["valid_samples"] - counts["valid_runs"]
    actual_raw_bytes = artifact_result.encoded_bytes if raw_bytes is None else raw_bytes
    if point.family != BaselineMethod.RAW_SAMPLES.value and raw_bytes is None:
        raise ArtifactError("raw canonical byte reference is unavailable")
    motion_summary = _motion_summary(motion_records)
    semantic_summary = _event_summary(event_records)
    primitives = _primitive_counts(tape)
    total_seconds = time.perf_counter() - started
    resources = {
        "encoding_seconds": encoding_seconds,
        "artifact_writing_seconds": artifact_writing_seconds,
        "artifact_verification_seconds": artifact_verification_seconds,
        "replay_evaluation_seconds": replay_seconds,
        "semantic_redetection_seconds": semantic_seconds,
        "total_seconds": total_seconds,
        "peak_process_rss_bytes": peak_process_rss_bytes(),
        "output_disk_bytes": artifact_result.generated_disk_bytes,
    }
    execution = ScenarioConfigurationExecution(
        parameter_identity=reported_configuration_id,
        method_id=reported_method_id,
        family=point.family,
        source_scenario_id=unit.source_scenario_id,
        scenario_id=scenario.scenario_id,
        selection_rank=unit.selection_rank,
        trajectory_ids=tuple(trajectory.trajectory_id for trajectory in trajectories),
        source_sample_count=counts["source_samples"],
        valid_sample_count=counts["valid_samples"],
        retained_keyframe_count=counts["retained_keyframes"],
        procedural_segment_count=counts["segments"],
        serialized_representation_bytes=artifact_result.encoded_bytes,
        raw_canonical_bytes=actual_raw_bytes,
        exact_adjacent_segments=exact_segments,
        motion_summary_json=canonical_json_text(motion_summary),
        semantic_summary_json=canonical_json_text(semantic_summary),
        artifact_summary_json=canonical_json_text(artifact_summary),
        resource_summary_json=canonical_json_text(resources),
    )
    return {
        "execution": execution.to_dict(),
        "motion_records": motion_records,
        "event_records": event_records,
        "primitive_counts": primitives,
    }


def _checkpoint_payload(checkpoint: SweepCheckpointState) -> Mapping[str, object]:
    value = json.loads(checkpoint.payload_json)
    if not isinstance(value, Mapping):
        raise ArtifactError("checkpoint payload root differs")
    return cast(Mapping[str, object], value)


def _verify_completed_checkpoint(
    checkpoint: SweepCheckpointState,
    point: SweepParameterPoint,
    unit: MotionCohortUnit,
    generated_root: Path,
) -> Mapping[str, object]:
    if (
        checkpoint.status is not CheckpointStatus.COMPLETED
        or checkpoint.parameter_identity != point.parameter_identity
        or checkpoint.method_id != point.method_id
        or checkpoint.source_scenario_id != unit.source_scenario_id
        or checkpoint.selection_rank != unit.selection_rank
    ):
        raise ArtifactError("completed checkpoint identity differs")
    payload = _checkpoint_payload(checkpoint)
    execution = payload.get("execution")
    if not isinstance(execution, Mapping):
        raise ArtifactError("completed checkpoint execution is missing")
    artifact = execution.get("artifact_summary")
    if not isinstance(artifact, Mapping):
        raise ArtifactError("completed checkpoint artifact summary is missing")
    _verify_artifact_summary(generated_root, cast(Mapping[str, object], artifact))
    return payload


def _raw_bytes_from_checkpoint(
    checkpoint_root: Path,
    raw_point: SweepParameterPoint,
    unit: MotionCohortUnit,
    generated_root: Path,
) -> int:
    checkpoint = load_checkpoint(
        checkpoint_path(checkpoint_root, raw_point, unit.source_scenario_id)
    )
    payload = _verify_completed_checkpoint(
        checkpoint,
        raw_point,
        unit,
        generated_root,
    )
    execution = cast(Mapping[str, object], payload["execution"])
    return _as_int(
        execution["serialized_representation_bytes"],
        "serialized_representation_bytes",
    )


def _run_first_pass(
    repository_root: Path,
    cache_root: Path,
    generated_root: Path,
    checkpoint_root: Path,
    configuration: SweepConfiguration,
    units: Sequence[MotionCohortUnit],
    reporting_identities: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[list[SweepCheckpointState], int]:
    checkpoints: list[SweepCheckpointState] = []
    reused_count = 0
    raw_point = configuration.parameter_points[0]
    order = execution_order(configuration, units)
    total = len(order)
    for index, (point, unit) in enumerate(order, start=1):
        path = checkpoint_path(checkpoint_root, point, unit.source_scenario_id)
        existing: SweepCheckpointState | None = None
        if path.is_file():
            existing = load_checkpoint(path)
            if existing.status is CheckpointStatus.COMPLETED:
                _verify_completed_checkpoint(existing, point, unit, generated_root)
                checkpoints.append(existing)
                reused_count += 1
                _LOGGER.info(
                    "pass1 %d/%d %s rank=%d reused",
                    index,
                    total,
                    point.method_id,
                    unit.selection_rank,
                )
                continue
        attempt = 1 if existing is None else existing.attempt_count + 1
        raw_bytes = (
            None
            if point.parameter_identity == raw_point.parameter_identity
            else _raw_bytes_from_checkpoint(
                checkpoint_root,
                raw_point,
                unit,
                generated_root,
            )
        )
        try:
            payload = _scenario_execution(
                repository_root,
                cache_root,
                generated_root,
                point,
                unit,
                raw_bytes,
                None
                if reporting_identities is None
                else reporting_identities[point.parameter_identity],
            )
            checkpoint = SweepCheckpointState.create(
                point,
                unit,
                CheckpointStatus.COMPLETED,
                attempt,
                payload,
            )
        except Exception as error:  # noqa: BLE001
            failure = SweepFailureRecord(
                point.parameter_identity,
                point.method_id,
                unit.source_scenario_id,
                unit.selection_rank,
                attempt,
                type(error).__name__,
                str(error),
            )
            checkpoint = SweepCheckpointState.create(
                point,
                unit,
                CheckpointStatus.FAILED,
                attempt,
                {"failure": failure.to_dict()},
            )
        write_checkpoint(path, checkpoint)
        checkpoints.append(checkpoint)
        _LOGGER.info(
            "pass1 %d/%d %s rank=%d %s",
            index,
            total,
            point.method_id,
            unit.selection_rank,
            cast(CheckpointStatus, checkpoint.status).value,
        )
    return checkpoints, reused_count


def _run_reuse_pass(
    generated_root: Path,
    checkpoint_root: Path,
    configuration: SweepConfiguration,
    units: Sequence[MotionCohortUnit],
) -> tuple[list[SweepCheckpointState], int, str]:
    checkpoints: list[SweepCheckpointState] = []
    hashes: list[str] = []
    order = execution_order(configuration, units)
    total = len(order)
    for index, (point, unit) in enumerate(order, start=1):
        checkpoint = load_checkpoint(
            checkpoint_path(checkpoint_root, point, unit.source_scenario_id)
        )
        if checkpoint.status is CheckpointStatus.COMPLETED:
            _verify_completed_checkpoint(checkpoint, point, unit, generated_root)
        checkpoints.append(checkpoint)
        hashes.append(checkpoint.payload_sha256)
        if index % 25 == 0:
            _LOGGER.info(
                "pass2 %d/%d verified checkpoint reuse",
                index,
                total,
            )
    return (
        checkpoints,
        sum(
            checkpoint.status is CheckpointStatus.COMPLETED
            for checkpoint in checkpoints
        ),
        canonical_sha256("phase4-motion-sweep-checkpoint-output-v1", hashes),
    )


def _collect(
    checkpoints: Sequence[SweepCheckpointState],
) -> tuple[
    list[Mapping[str, object]],
    list[Mapping[str, object]],
    list[Mapping[str, object]],
    list[Mapping[str, object]],
]:
    executions: list[Mapping[str, object]] = []
    motion: list[Mapping[str, object]] = []
    events: list[Mapping[str, object]] = []
    failures: list[Mapping[str, object]] = []
    for checkpoint in checkpoints:
        payload = _checkpoint_payload(checkpoint)
        if checkpoint.status is CheckpointStatus.FAILED:
            failure = payload.get("failure")
            if isinstance(failure, Mapping):
                failures.append(cast(Mapping[str, object], failure))
            continue
        execution = payload.get("execution")
        motion_records = payload.get("motion_records")
        event_records = payload.get("event_records")
        primitive_counts = payload.get("primitive_counts")
        if (
            not isinstance(execution, Mapping)
            or not isinstance(motion_records, Sequence)
            or isinstance(motion_records, (str, bytes))
            or not isinstance(event_records, Sequence)
            or isinstance(event_records, (str, bytes))
            or not isinstance(primitive_counts, Mapping)
        ):
            raise ArtifactError("completed checkpoint payload differs")
        row = dict(execution)
        row["primitive_counts"] = dict(primitive_counts)
        executions.append(row)
        motion.extend(cast(Sequence[Mapping[str, object]], motion_records))
        events.extend(cast(Sequence[Mapping[str, object]], event_records))
    return executions, motion, events, failures


def _scenario_table_row(execution: Mapping[str, object]) -> dict[str, object]:
    motion = cast(Mapping[str, object], execution["motion_summary"])
    semantic = cast(Mapping[str, object], execution["semantic_summary"])
    resources = cast(Mapping[str, object], execution["resource_summary"])
    primitives = cast(Mapping[str, object], execution["primitive_counts"])
    position = cast(Mapping[str, object], motion["position"])
    heading = cast(Mapping[str, object], motion["heading"])
    velocity = cast(Mapping[str, object], motion["velocity"])
    overall = cast(Mapping[str, object], semantic["overall"])
    return {
        "schema_version": "1.0",
        "parameter_identity": execution["parameter_identity"],
        "method_id": execution["method_id"],
        "family": execution["family"],
        "source_scenario_id": execution["source_scenario_id"],
        "scenario_id": execution["scenario_id"],
        "selection_rank": execution["selection_rank"],
        "trajectory_count": execution["trajectory_count"],
        "source_sample_count": execution["source_sample_count"],
        "valid_sample_count": execution["valid_sample_count"],
        "retained_keyframe_count": execution["retained_keyframe_count"],
        "procedural_segment_count": execution["procedural_segment_count"],
        "hold_segment_count": primitives["hold"],
        "linear_segment_count": primitives["linear"],
        "hermite_segment_count": primitives["cubic_hermite"],
        "serialized_representation_bytes": execution["serialized_representation_bytes"],
        "raw_canonical_bytes": execution["raw_canonical_bytes"],
        "exact_adjacent_segments": execution["exact_adjacent_segments"],
        "byte_ratio": execution["byte_ratio"],
        "keyframe_ratio": execution["keyframe_ratio"],
        "segment_ratio": execution["segment_ratio"],
        "position_mean_m": position["mean"],
        "position_p95_m": position["p95"],
        "heading_mean_rad": heading["mean"],
        "velocity_mean_mps": velocity["mean"],
        "semantic_micro_f1": overall["f1"],
        "encoding_seconds": resources["encoding_seconds"],
        "artifact_writing_seconds": resources["artifact_writing_seconds"],
        "artifact_verification_seconds": resources["artifact_verification_seconds"],
        "replay_evaluation_seconds": resources["replay_evaluation_seconds"],
        "semantic_redetection_seconds": resources["semantic_redetection_seconds"],
        "total_seconds": resources["total_seconds"],
        "peak_process_rss_bytes": resources["peak_process_rss_bytes"],
        "output_disk_bytes": resources["output_disk_bytes"],
        "artifact_summary_json": canonical_json_text(execution["artifact_summary"]),
        "motion_summary_json": canonical_json_text(motion),
        "semantic_summary_json": canonical_json_text(semantic),
    }


def _configuration_rows(
    configuration: SweepConfiguration,
    scenario_rows: Sequence[Mapping[str, object]],
    motion_records: Sequence[Mapping[str, object]],
    event_records: Sequence[Mapping[str, object]],
    reporting_identities: Mapping[str, tuple[str, str]] | None = None,
) -> list[dict[str, object]]:
    scenarios_by_parameter: defaultdict[str, list[Mapping[str, object]]] = defaultdict(
        list
    )
    motion_by_parameter: defaultdict[str, list[Mapping[str, object]]] = defaultdict(
        list
    )
    events_by_parameter: defaultdict[str, list[Mapping[str, object]]] = defaultdict(
        list
    )
    for row in scenario_rows:
        scenarios_by_parameter[cast(str, row["parameter_identity"])].append(row)
    for row in motion_records:
        motion_by_parameter[cast(str, row["configuration_id"])].append(row)
    for row in event_records:
        events_by_parameter[cast(str, row["configuration_id"])].append(row)
    rows: list[dict[str, object]] = []
    for point in configuration.parameter_points:
        reported_method_id, reported_configuration_id = (
            (point.method_id, point.parameter_identity)
            if reporting_identities is None
            else reporting_identities[point.parameter_identity]
        )
        scenarios = scenarios_by_parameter[reported_configuration_id]
        motion_summary = _motion_summary(motion_by_parameter[reported_configuration_id])
        event_summary = _event_summary(events_by_parameter[reported_configuration_id])
        serialized = sum(
            _as_int(row["serialized_representation_bytes"], "serialized bytes")
            for row in scenarios
        )
        raw = sum(_as_int(row["raw_canonical_bytes"], "raw bytes") for row in scenarios)
        keyframes = sum(
            _as_int(row["retained_keyframe_count"], "keyframes") for row in scenarios
        )
        valid = sum(
            _as_int(row["valid_sample_count"], "valid samples") for row in scenarios
        )
        segments = sum(
            _as_int(row["procedural_segment_count"], "segments") for row in scenarios
        )
        exact_segments = sum(
            _as_int(row["exact_adjacent_segments"], "exact segments")
            for row in scenarios
        )
        position = cast(Mapping[str, object], motion_summary["position"])
        heading = cast(Mapping[str, object], motion_summary["heading"])
        velocity = cast(Mapping[str, object], motion_summary["velocity"])
        semantic = cast(Mapping[str, object], event_summary["overall"])
        rows.append(
            {
                "schema_version": "1.0",
                "parameter_identity": reported_configuration_id,
                "method_id": reported_method_id,
                "family": point.family,
                "scenario_count": len(scenarios),
                "trajectory_count": sum(
                    _as_int(row["trajectory_count"], "trajectory count")
                    for row in scenarios
                ),
                "serialized_representation_bytes": serialized,
                "raw_canonical_bytes": raw,
                "retained_keyframe_count": keyframes,
                "valid_sample_count": valid,
                "procedural_segment_count": segments,
                "exact_adjacent_segments": exact_segments,
                "byte_ratio": serialized / raw,
                "keyframe_ratio": keyframes / valid,
                "segment_ratio": segments / exact_segments,
                "position_mean_m": position["mean"],
                "position_p95_m": position["p95"],
                "heading_mean_rad": heading["mean"],
                "velocity_mean_mps": velocity["mean"],
                "semantic_micro_f1": semantic["f1"],
                "total_seconds": sum(
                    _as_float(row["total_seconds"], "total seconds")
                    for row in scenarios
                ),
                "peak_process_rss_bytes": max(
                    _as_int(row["peak_process_rss_bytes"], "peak RSS")
                    for row in scenarios
                ),
                "output_disk_bytes": sum(
                    _as_int(row["output_disk_bytes"], "output bytes")
                    for row in scenarios
                ),
                "failure_count": 0,
                "motion_summary": motion_summary,
                "semantic_summary": event_summary,
                "configuration": dict(point.configuration),
            }
        )
    return rows


def _aggregate_table_row(row: Mapping[str, object]) -> dict[str, object]:
    names = (
        "schema_version",
        "parameter_identity",
        "method_id",
        "family",
        "scenario_count",
        "trajectory_count",
        "serialized_representation_bytes",
        "raw_canonical_bytes",
        "retained_keyframe_count",
        "valid_sample_count",
        "procedural_segment_count",
        "exact_adjacent_segments",
        "byte_ratio",
        "keyframe_ratio",
        "segment_ratio",
        "position_mean_m",
        "position_p95_m",
        "heading_mean_rad",
        "velocity_mean_mps",
        "semantic_micro_f1",
        "total_seconds",
        "peak_process_rss_bytes",
        "output_disk_bytes",
        "failure_count",
    )
    return {name: row[name] for name in names}


def _artifact_descriptor(path: Path, table_name: str) -> dict[str, object]:
    metadata = pq.ParquetFile(path).metadata
    return {
        "table_name": table_name,
        "path": path.as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "row_count": metadata.num_rows,
        "row_group_count": metadata.num_row_groups,
    }


def _summary_markdown(
    configuration_rows: Sequence[Mapping[str, object]],
    failures: int,
    reuse_count: int,
) -> str:
    lines = [
        "# Phase 4 Matched-Budget Sweep Engine",
        "",
        "- Evidence class: exploratory development evidence",
        "- Frozen subset: 25 genuine AV2 development scenarios",
        f"- Parameter points: {len(configuration_rows)}",
        f"- Scenario/configuration executions: {len(configuration_rows) * 25}",
        f"- Failures: {failures}",
        f"- Verified second-pass checkpoint reuses: {reuse_count}",
        "- Pilot/test cohorts accessed: no",
        "- Final method selected: no",
        "",
        "## Explored Ranges",
        "",
        "| Family | Byte ratio range | Keyframe ratio range | Position mean range (m) |",
        "|---|---:|---:|---:|",
    ]
    by_family: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in configuration_rows:
        by_family[cast(str, row["family"])].append(row)
    for family in sorted(by_family):
        rows = by_family[family]
        byte_values = [cast(float, row["byte_ratio"]) for row in rows]
        keyframe_values = [cast(float, row["keyframe_ratio"]) for row in rows]
        position_values = [
            cast(float, row["position_mean_m"])
            for row in rows
            if row["position_mean_m"] is not None
        ]
        lines.append(
            f"| `{family}` | {min(byte_values):.6f}-{max(byte_values):.6f} | "
            f"{min(keyframe_values):.6f}-{max(keyframe_values):.6f} | "
            f"{min(position_values):.6f}-{max(position_values):.6f} |"
        )
    lines.extend(
        (
            "",
            "Budget matching uses only actual representation size or complexity.",
            "No metric interpolation, final selection, or confirmatory claim is made.",
            "",
        )
    )
    return "\n".join(lines)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--cohort-evidence-root",
        type=Path,
        default=Path("results/phase4/motion_cohort"),
    )
    parser.add_argument(
        "--baseline-evidence-root",
        type=Path,
        default=Path("results/phase4/motion_baselines"),
    )
    parser.add_argument(
        "--metric-evidence-root",
        type=Path,
        default=Path("results/phase4/motion_metrics"),
    )
    parser.add_argument(
        "--cohort-cache-root",
        type=Path,
        default=Path("cache/phase4_motion_cohort/cohort"),
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("cache/phase4_motion_sweep"),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("results/phase4/motion_sweep"),
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    args = _arguments()
    root = args.repository_root.resolve()
    cohort_root = (root / args.cohort_evidence_root).resolve()
    baseline_root = (root / args.baseline_evidence_root).resolve()
    metric_root = (root / args.metric_evidence_root).resolve()
    cache_root = (root / args.cohort_cache_root).resolve()
    generated_root = (root / args.generated_root).resolve()
    evidence_root = (root / args.evidence_root).resolve()
    for path in (
        cohort_root,
        baseline_root,
        metric_root,
        cache_root,
        generated_root,
        evidence_root,
    ):
        if not path.is_relative_to(root):
            raise ArtifactError("campaign roots must remain inside repository")

    free_before = shutil.disk_usage(root).free
    reserve = int(shutil.disk_usage(root).total * 0.15)
    if free_before - reserve < _ESTIMATED_REQUIRED_BYTES:
        raise ResourceLimitError("insufficient disk headroom for motion sweep")
    units, cohort_evidence = _verify_inputs(
        cohort_root,
        baseline_root,
        metric_root,
    )
    configuration = SweepConfiguration(frozen_parameter_grid())
    generated_root.mkdir(parents=True, exist_ok=True)
    checkpoint_root = generated_root

    membership: list[dict[str, object]] = []
    included_trajectory_count = 0
    for unit in units:
        trajectories = _bundle(root, cache_root, unit)[3]
        trajectory_ids = [trajectory.trajectory_id for trajectory in trajectories]
        included_trajectory_count += len(trajectory_ids)
        membership.append(
            {
                "source_scenario_id": unit.source_scenario_id,
                "selection_rank": unit.selection_rank,
                "trajectory_ids": trajectory_ids,
            }
        )
    subset_identity = canonical_sha256(
        "phase4-motion-sweep-subset-v1",
        {
            "cohort_identity": _COHORT_IDENTITY,
            "units": [
                {
                    "source_scenario_id": unit.source_scenario_id,
                    "selection_rank": unit.selection_rank,
                }
                for unit in units
            ],
        },
    )
    trajectory_identity = canonical_sha256(
        "phase4-motion-sweep-trajectories-v1",
        membership,
    )

    campaign_started = time.perf_counter()
    first_checkpoints, first_reuse_count = _run_first_pass(
        root,
        cache_root,
        generated_root / "representations",
        checkpoint_root,
        configuration,
        units,
    )
    first_hash = canonical_sha256(
        "phase4-motion-sweep-checkpoint-output-v1",
        [checkpoint.payload_sha256 for checkpoint in first_checkpoints],
    )
    second_checkpoints, second_reuse_count, second_hash = _run_reuse_pass(
        generated_root / "representations",
        checkpoint_root,
        configuration,
        units,
    )
    if first_hash != second_hash:
        raise ArtifactError("repeat-run deterministic checkpoint output differs")

    executions, motion_records, event_records, failures = _collect(second_checkpoints)
    scenario_rows = [_scenario_table_row(execution) for execution in executions]
    configuration_rows = _configuration_rows(
        configuration,
        scenario_rows,
        motion_records,
        event_records,
    )
    budget_records = tuple(
        ConfigurationBudgetRecord(
            cast(str, row["family"]),
            cast(str, row["method_id"]),
            cast(str, row["parameter_identity"]),
            cast(float, row["byte_ratio"]),
            cast(float, row["keyframe_ratio"]),
        )
        for row in configuration_rows
    )
    byte_selections = select_matched_budgets(
        budget_records,
        configuration.byte_ratio_targets,
        BudgetDimension.BYTE_RATIO,
    )
    keyframe_selections = select_matched_budgets(
        budget_records,
        configuration.keyframe_ratio_targets,
        BudgetDimension.KEYFRAME_RATIO,
    )
    selection_rows = [
        selection.to_dict() for selection in (*byte_selections, *keyframe_selections)
    ]

    final_root = generated_root / "final-v1"
    final_root.mkdir(parents=True, exist_ok=False)
    written = [
        write_sweep_parquet(
            final_root / "scenario_configuration_results.parquet",
            scenario_rows,
            MotionSweepTable.SCENARIO_CONFIGURATION,
        ),
        write_sweep_parquet(
            final_root / "configuration_results.parquet",
            [_aggregate_table_row(row) for row in configuration_rows],
            MotionSweepTable.CONFIGURATION_AGGREGATE,
        ),
        write_sweep_parquet(
            final_root / "matched_budget_selections.parquet",
            selection_rows,
            MotionSweepTable.MATCHED_BUDGET,
        ),
        write_sweep_parquet(
            final_root / "sweep_failures.parquet",
            failures,
            MotionSweepTable.FAILURE,
        ),
    ]
    metric_artifacts = [
        write_metric_parquet(
            final_root / "trajectory_motion_metrics.parquet",
            motion_records,
            MotionMetricTable.TRAJECTORY_MOTION,
        ),
        write_metric_parquet(
            final_root / "trajectory_event_metrics.parquet",
            event_records,
            MotionMetricTable.TRAJECTORY_EVENT,
        ),
    ]
    manifest = SweepManifest(
        configuration.identity,
        _COHORT_IDENTITY,
        _VALIDATION_IDENTITY,
        subset_identity,
        trajectory_identity,
        tuple(unit.source_scenario_id for unit in units),
        tuple(point.parameter_identity for point in configuration.parameter_points),
        len(configuration.parameter_points) * len(units),
        len(executions),
        len(failures),
        second_reuse_count,
    )
    summary = SweepResultSummary(
        manifest.identity,
        len(configuration_rows),
        len(scenario_rows),
        len(byte_selections),
        len(keyframe_selections),
        len(failures),
        canonical_json_text(
            {
                "checkpoint_output_sha256": first_hash,
                "first_pass_reuse_count": first_reuse_count,
                "second_pass_reuse_count": second_reuse_count,
            }
        ),
    )
    raw_artifacts = [artifact.to_dict() for artifact in written]
    raw_artifacts.extend(artifact.to_dict() for artifact in metric_artifacts)
    _write_json(
        final_root / "sweep_manifest.json",
        {
            **manifest.to_dict(),
            "manifest_identity": manifest.identity,
            "configuration": configuration.to_dict(),
            "subset_membership": membership,
            "artifacts": raw_artifacts,
        },
    )
    _write_json(final_root / "sweep_result_summary.json", summary.to_dict())

    total_seconds = time.perf_counter() - campaign_started
    by_identity = {
        cast(str, row["parameter_identity"]): row for row in configuration_rows
    }
    matched_byte = [
        {
            **selection.to_dict(),
            "selected_result": by_identity[selection.parameter_identity],
        }
        for selection in byte_selections
    ]
    matched_keyframe = [
        {
            **selection.to_dict(),
            "selected_result": by_identity[selection.parameter_identity],
        }
        for selection in keyframe_selections
    ]
    hardware = {
        "operating_system": "Ubuntu under WSL2",
        "machine": platform.machine(),
        "host_model": "ASUS reference laptop",
        "python_version": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "gpu_used": False,
    }
    contract = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "starting_head": _STARTING_HEAD,
        "cohort_identity": _COHORT_IDENTITY,
        "validation_identity": _VALIDATION_IDENTITY,
        "subset_identity": subset_identity,
        "trajectory_membership_identity": trajectory_identity,
        "official_source": cohort_evidence["official_source"],
        "scenario_count": len(units),
        "scenarios": [
            {
                "source_scenario_id": unit.source_scenario_id,
                "selection_rank": unit.selection_rank,
            }
            for unit in units
        ],
        "included_trajectory_count": included_trajectory_count,
        "configuration_identity": configuration.identity,
        "parameter_grid": [point.to_dict() for point in configuration.parameter_points],
        "byte_ratio_targets": list(configuration.byte_ratio_targets),
        "keyframe_ratio_targets": list(configuration.keyframe_ratio_targets),
        "matching_policy": {
            "selection_input": "relevant_budget_value_only",
            "rule": "largest_at_or_below_else_smallest_above",
            "tie_break": "canonical_parameter_identity",
            "metric_interpolation": False,
        },
        "execution_policy": "sequential_bounded_one_scenario",
        "pilot_or_test_metadata_or_outcomes_accessed": False,
        "final_method_selected": False,
    }
    exploratory = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "evidence_class": "exploratory_development",
        "configuration_count": len(configuration_rows),
        "scenario_configuration_count": len(scenario_rows),
        "included_trajectory_count": included_trajectory_count,
        "configurations": configuration_rows,
        "raw_result_artifacts": raw_artifacts,
    }
    failure_report = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "failure_count": len(failures),
        "failures": failures,
        "omitted_failures": False,
    }
    performance = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "hardware": hardware,
        "sequential_processing": True,
        "worker_count": 1,
        "campaign_total_seconds": total_seconds,
        "peak_process_rss_bytes": peak_process_rss_bytes(),
        "generated_disk_bytes": _tree_size(generated_root),
        "disk_free_before_bytes": free_before,
        "disk_free_after_bytes": shutil.disk_usage(root).free,
        "first_pass_reuse_count": first_reuse_count,
        "second_pass_reuse_count": second_reuse_count,
        "recomputed_completed_unit_count": 0,
        "checkpoint_output_sha256": first_hash,
        "repeat_checkpoint_output_sha256": second_hash,
        "configurations": [
            {
                "parameter_identity": row["parameter_identity"],
                "method_id": row["method_id"],
                "total_seconds": row["total_seconds"],
                "peak_process_rss_bytes": row["peak_process_rss_bytes"],
                "output_disk_bytes": row["output_disk_bytes"],
            }
            for row in configuration_rows
        ],
    }
    achieved = (
        not failures
        and len(executions) == 975
        and len(configuration_rows) == 39
        and second_reuse_count == 975
        and first_hash == second_hash
        and all(
            _as_int(row["trajectory_count"], "trajectory count")
            == included_trajectory_count
            for row in configuration_rows
        )
    )
    evidence_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "sweep_contract.json": contract,
        "exploratory_results.json": exploratory,
        "matched_byte_results.json": {
            "batch": _BATCH,
            "schema_version": "1.0",
            "dimension": "byte_ratio",
            "selection_count": len(matched_byte),
            "selections": matched_byte,
        },
        "matched_keyframe_results.json": {
            "batch": _BATCH,
            "schema_version": "1.0",
            "dimension": "keyframe_ratio",
            "selection_count": len(matched_keyframe),
            "selections": matched_keyframe,
        },
        "failure_report.json": failure_report,
        "performance_report.json": performance,
    }
    for name, value in payloads.items():
        _write_json(evidence_root / name, value)
    summary_path = evidence_root / "summary.md"
    summary_path.write_text(
        _summary_markdown(configuration_rows, len(failures), second_reuse_count),
        encoding="utf-8",
        newline="\n",
    )
    evidence_files = (*payloads.keys(), "summary.md")
    evidence = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "batch_decision": "achieved" if achieved else "not_achieved",
        "pass_statement": _PASS_STATEMENT if achieved else None,
        "starting_head": _STARTING_HEAD,
        "cohort_identity": _COHORT_IDENTITY,
        "validation_identity": _VALIDATION_IDENTITY,
        "subset_identity": subset_identity,
        "trajectory_membership_identity": trajectory_identity,
        "development_scenario_count": len(units),
        "included_trajectory_count": included_trajectory_count,
        "parameter_point_count": len(configuration.parameter_points),
        "scenario_configuration_count": len(executions),
        "failure_count": len(failures),
        "matched_byte_selection_count": len(byte_selections),
        "matched_keyframe_selection_count": len(keyframe_selections),
        "second_pass_checkpoint_reuse_count": second_reuse_count,
        "recomputed_completed_unit_count": 0,
        "checkpoint_output_sha256": first_hash,
        "repeat_checkpoint_output_sha256": second_hash,
        "pilot_or_test_metadata_or_outcomes_accessed": False,
        "provider_or_generated_data_tracked": False,
        "final_method_selected": False,
        "batch4_5_owns_protocol_audit_and_final_grid_freeze": True,
        "evidence_file_sha256": {
            name: _sha256(evidence_root / name) for name in evidence_files
        },
    }
    _write_json(evidence_root / "evidence.json", evidence)
    _LOGGER.info(
        "%s",
        _PASS_STATEMENT if achieved else "Batch 4.4 evidence not achieved.",
    )
    return 0 if achieved else 1


if __name__ == "__main__":
    raise SystemExit(main())
