"""Run frozen Phase 4 motion and semantic metrics on development only."""

# ruff: noqa: T201

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
from typing import cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import run_directory_name
from kinematicweave.baselines.motion import (
    BASELINE_IMPLEMENTATION_VERSION,
    BaselineMethod,
    MotionBaselineConfig,
    baseline_config_identity,
    encode_scenario_baseline,
    included_motion_trajectories,
    read_canonical_scenario_bundle,
    required_baseline_grid,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.codecs.exact import (
    ENCODER_NAME as EXACT_NAME,
)
from kinematicweave.codecs.exact import (
    ENCODER_PARAMETERS_IDENTITY,
    encode_scenario_exact,
)
from kinematicweave.codecs.exact import (
    ENCODER_VERSION as EXACT_VERSION,
)
from kinematicweave.codecs.hermite import (
    ENCODER_NAME as HERMITE_NAME,
)
from kinematicweave.codecs.hermite import (
    ENCODER_VERSION as HERMITE_VERSION,
)
from kinematicweave.codecs.hermite import (
    HermiteCodecConfig,
    encode_scenario_hermite,
    hermite_encoder_parameters_identity,
)
from kinematicweave.codecs.piecewise_linear import (
    ENCODER_NAME as LINEAR_NAME,
)
from kinematicweave.codecs.piecewise_linear import (
    ENCODER_VERSION as LINEAR_VERSION,
)
from kinematicweave.codecs.piecewise_linear import (
    PiecewiseLinearCodecConfig,
    encode_scenario_piecewise_linear,
    encoder_parameters_identity,
)
from kinematicweave.codecs.velocity_bounded import (
    ENCODER_NAME as HYBRID_NAME,
)
from kinematicweave.codecs.velocity_bounded import (
    ENCODER_VERSION as HYBRID_VERSION,
)
from kinematicweave.codecs.velocity_bounded import (
    VelocityBoundedCodecConfig,
    encode_scenario_velocity_bounded,
    velocity_bounded_encoder_parameters_identity,
)
from kinematicweave.data.motion_metric_artifacts import (
    MotionMetricArtifact,
    MotionMetricTable,
    read_metric_parquet,
    verify_metric_parquet,
    write_metric_parquet,
)
from kinematicweave.domain.procedural import ProceduralTape
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    semantic_motion_configuration_identity,
)
from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    MotionCohortManifest,
    MotionCohortUnit,
    motion_cohort_manifest_from_json,
)
from kinematicweave.metrics.motion import (
    QUANTILE_POLICY,
    ArtifactFileMetric,
    EvaluationFailure,
    MetricsCampaignManifest,
    ScenarioArtifactRuntimeMetrics,
    descriptive_statistics,
    evaluate_trajectory_motion,
)
from kinematicweave.metrics.semantic import evaluate_trajectory_events
from kinematicweave.resources import peak_process_rss_bytes

_BATCH = "4.3"
_STARTING_HEAD = "20c572ca596f083aef21015760e2e1df39e0c7e2"
_COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)
_TRAJECTORY_IDENTITY = (
    "21df77d7e9bc3eed61c5e805e491675b8ca7d5fcdfbb41c300c024858717408c"
)
_PASS_STATEMENT = (
    "Phase 4 motion and semantic metrics frozen on all 150 genuine AV2 "
    "development scenarios."
)
_CHECKPOINT_VERSION = "1.0"
_NUMERIC_AUDIT_TOLERANCE = 1.0e-12
_LINEAR_CONFIG = PiecewiseLinearCodecConfig(0.10)
_HERMITE_CONFIG = HermiteCodecConfig(0.10)
_HYBRID_CONFIG = VelocityBoundedCodecConfig(0.10, 1.00)
_SEMANTIC_CONFIG = SemanticMotionConfig()

Encoder = Callable[
    [
        ScenarioRecord,
        CoordinateFrameRecord,
        Sequence[AgentRecord],
        Sequence[Trajectory],
    ],
    ProceduralTape,
]


@dataclass(frozen=True, slots=True)
class MethodSpec:
    key: str
    family: str
    configuration: Mapping[str, object]
    configuration_identity: str
    implementation_name: str
    implementation_version: str
    baseline_config: MotionBaselineConfig | None
    encoder: Encoder | None


def _baseline_encoder(config: MotionBaselineConfig) -> Encoder:
    def encode(
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
            config,
            source_validation_report_identity=_VALIDATION_IDENTITY,
        )

    return encode


def _method_specs() -> tuple[MethodSpec, ...]:
    specs: list[MethodSpec] = []
    for config in required_baseline_grid():
        family = cast(BaselineMethod, config.method).value
        specs.append(
            MethodSpec(
                config.key,
                family,
                {
                    "method": family,
                    "stride": config.stride,
                    "maximum_perpendicular_error_m": (
                        config.maximum_perpendicular_error_m
                    ),
                    "interval_ns": config.interval_ns,
                },
                baseline_config_identity(config),
                f"phase4.{family}",
                BASELINE_IMPLEMENTATION_VERSION,
                config,
                None if not config.is_encoded else _baseline_encoder(config),
            )
        )

    def exact(
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

    def linear(
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
            _LINEAR_CONFIG,
            source_validation_report_identity=_VALIDATION_IDENTITY,
        )

    def hermite(
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
            _HERMITE_CONFIG,
            source_validation_report_identity=_VALIDATION_IDENTITY,
        )

    def hybrid(
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
            _HYBRID_CONFIG,
            source_validation_report_identity=_VALIDATION_IDENTITY,
        )

    specs.extend(
        (
            MethodSpec(
                "exact_adjacent_sample",
                "accepted_procedural",
                {"method": "exact_adjacent_sample"},
                ENCODER_PARAMETERS_IDENTITY,
                EXACT_NAME,
                EXACT_VERSION,
                None,
                exact,
            ),
            MethodSpec(
                "position_bounded_linear-error-0p1",
                "accepted_procedural",
                {
                    "method": "position_bounded_linear",
                    "maximum_position_error_m": 0.10,
                },
                encoder_parameters_identity(_LINEAR_CONFIG),
                LINEAR_NAME,
                LINEAR_VERSION,
                None,
                linear,
            ),
            MethodSpec(
                "unconstrained_hermite-error-0p1",
                "accepted_procedural",
                {
                    "method": "unconstrained_hermite",
                    "maximum_position_error_m": 0.10,
                },
                hermite_encoder_parameters_identity(_HERMITE_CONFIG),
                HERMITE_NAME,
                HERMITE_VERSION,
                None,
                hermite,
            ),
            MethodSpec(
                "position_velocity_bounded_hybrid-error-0p1-velocity-1p0",
                "accepted_procedural",
                {
                    "method": "position_velocity_bounded_hybrid",
                    "maximum_position_error_m": 0.10,
                    "maximum_velocity_error_mps": 1.00,
                },
                velocity_bounded_encoder_parameters_identity(_HYBRID_CONFIG),
                HYBRID_NAME,
                HYBRID_VERSION,
                None,
                hybrid,
            ),
        )
    )
    if len(specs) != 18 or len({spec.key for spec in specs}) != 18:
        raise ValidationError("method grid must contain exactly 18 configurations")
    return tuple(specs)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError(f"cannot read JSON object: {path}") from error
    if not isinstance(value, Mapping):
        raise ArtifactError(f"JSON value is not an object: {path}")
    return value


def _write_json(path: Path, value: object, *, immutable: bool = False) -> None:
    if immutable and (path.exists() or path.is_symlink()):
        raise ArtifactError(f"immutable JSON artifact already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(canonical_json_text(value), encoding="utf-8", newline="\n")
    if immutable:
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ArtifactError(
                f"immutable JSON artifact exists: {path.name}"
            ) from None
        temporary.unlink()
    else:
        temporary.replace(path)


def _as_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactError("expected integer field")
    return value


def _as_float(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ArtifactError("expected numeric field")
    result = float(value)
    if not math.isfinite(result):
        raise ArtifactError("numeric field is nonfinite")
    return result


def _development_units(
    manifest: MotionCohortManifest,
) -> tuple[MotionCohortUnit, ...]:
    units = tuple(
        unit for unit in manifest.units if unit.cohort_role is CohortRole.DEVELOPMENT
    )
    if len(units) != 150 or any(not unit.validation_included for unit in units):
        raise ArtifactError("frozen development membership differs")
    return units


def _verify_inputs(
    root: Path,
    cohort_evidence_root: Path,
    baseline_evidence_root: Path,
    specs: Sequence[MethodSpec],
) -> tuple[
    MotionCohortManifest,
    tuple[MotionCohortUnit, ...],
    Mapping[str, object],
]:
    cohort_evidence = _read_json(cohort_evidence_root / "evidence.json")
    if (
        cohort_evidence.get("cohort_decision") != "frozen"
        or cohort_evidence.get("cohort_identity") != _COHORT_IDENTITY
        or cohort_evidence.get("pilot_or_test_outcomes_accessed") is True
    ):
        raise ArtifactError("Batch 4.1 cohort evidence differs")
    cohort_checksums = cohort_evidence.get("evidence_file_sha256")
    if not isinstance(cohort_checksums, Mapping):
        raise ArtifactError("Batch 4.1 evidence checksum index is missing")
    for name, expected in cohort_checksums.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise ArtifactError("Batch 4.1 evidence checksum entry is invalid")
        if _sha256(cohort_evidence_root / name) != expected:
            raise ArtifactError(f"Batch 4.1 evidence checksum differs: {name}")
    manifest = motion_cohort_manifest_from_json(
        (cohort_evidence_root / "cohort_manifest.json").read_text(encoding="utf-8")
    )
    units = _development_units(manifest)

    baseline_evidence = _read_json(baseline_evidence_root / "evidence.json")
    if (
        baseline_evidence.get("batch_decision") != "achieved"
        or baseline_evidence.get("cohort_identity") != _COHORT_IDENTITY
        or baseline_evidence.get("included_trajectory_count") != 7012
        or baseline_evidence.get("pilot_or_test_outcomes_accessed") is not False
    ):
        raise ArtifactError("Batch 4.2 evidence differs")
    baseline_checksums = baseline_evidence.get("evidence_file_sha256")
    if not isinstance(baseline_checksums, Mapping):
        raise ArtifactError("Batch 4.2 checksum index is missing")
    for name, expected in baseline_checksums.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise ArtifactError("Batch 4.2 checksum entry is invalid")
        if _sha256(baseline_evidence_root / name) != expected:
            raise ArtifactError(f"Batch 4.2 evidence checksum differs: {name}")
    contract = _read_json(baseline_evidence_root / "baseline_contract.json")
    methods = contract.get("methods")
    if not isinstance(methods, Sequence):
        raise ArtifactError("Batch 4.2 method contract is missing")
    expected_methods = [(spec.key, spec.configuration_identity) for spec in specs]
    actual_methods = []
    for value in methods:
        if not isinstance(value, Mapping):
            raise ArtifactError("Batch 4.2 method entry is invalid")
        actual_methods.append(
            (value.get("method_key"), value.get("configuration_identity"))
        )
    if actual_methods != expected_methods:
        raise ArtifactError("Batch 4.2 method/configuration grid differs")
    if contract.get(
        "included_trajectory_identity"
    ) != _TRAJECTORY_IDENTITY or contract.get("development_scenario_ids") != [
        unit.source_scenario_id for unit in units
    ]:
        raise ArtifactError("Batch 4.2 frozen membership differs")
    return (
        manifest,
        units,
        _read_json(baseline_evidence_root / "development_results.json"),
    )


def _cache_entry(cache_root: Path, unit: MotionCohortUnit) -> Path:
    key = unit.materialization_cache_key
    return cache_root / "entries" / key[:2] / key


def _bundle(
    root: Path,
    cache_root: Path,
    unit: MotionCohortUnit,
) -> tuple[
    ScenarioRecord,
    CoordinateFrameRecord,
    tuple[AgentRecord, ...],
    tuple[Trajectory, ...],
]:
    source = read_canonical_scenario_bundle(root, _cache_entry(cache_root, unit))
    if source.scenario.source_scenario_id != unit.source_scenario_id:
        raise ArtifactError("canonical scenario differs from frozen membership")
    trajectories = included_motion_trajectories(source.trajectories)
    agent_ids = {trajectory.agent_id for trajectory in trajectories}
    agents = tuple(agent for agent in source.agents if agent.agent_id in agent_ids)
    return (
        replace(source.scenario, agent_count=len(agents)),
        source.coordinate_frame,
        agents,
        trajectories,
    )


def _baseline_checkpoint(
    baseline_root: Path,
    spec: MethodSpec,
    unit: MotionCohortUnit,
) -> Mapping[str, object]:
    path = (
        baseline_root / "checkpoints-v2" / spec.key / f"{unit.source_scenario_id}.json"
    )
    row = _read_json(path)
    if (
        row.get("status") != "completed"
        or row.get("checkpoint_version") != "2.0"
        or row.get("method_key") != spec.key
        or row.get("configuration_identity") != spec.configuration_identity
    ):
        raise ArtifactError("Batch 4.2 scenario checkpoint differs")
    return row


def _artifact_metrics(
    baseline_root: Path,
    checkpoint: Mapping[str, object],
) -> tuple[tuple[ArtifactFileMetric, ...], int, int, int]:
    checksums = checkpoint.get("artifact_checksums")
    if not isinstance(checksums, Sequence) or isinstance(checksums, (str, bytes)):
        raise ArtifactError("baseline artifact checksums are invalid")
    expected = tuple(str(value) for value in checksums)
    names = (
        ("trajectory_samples.parquet",)
        if len(expected) == 1
        else (
            "tape_manifest.parquet",
            "procedural_tracks.parquet",
            "procedural_segments.parquet",
        )
    )
    if len(names) != len(expected):
        raise ArtifactError("baseline artifact count differs")
    first_files: list[ArtifactFileMetric] = []
    first_bundle_bytes = 0
    summary_manifest_bytes = 0
    for run_field in ("first_run_id", "repeat_run_id"):
        run_id = checkpoint.get(run_field)
        if not isinstance(run_id, str):
            raise ArtifactError("baseline run identity is invalid")
        run = baseline_root / "runs" / run_directory_name(run_id)
        if not (run / ".run.complete").is_file() or (run / ".run.partial").exists():
            raise ArtifactError("baseline run completion markers differ")
        for name, checksum in zip(names, expected, strict=True):
            path = run / name
            if path.is_symlink() or not path.is_file() or _sha256(path) != checksum:
                raise ArtifactError("baseline representation artifact differs")
            if run_field == "first_run_id":
                parquet = pq.ParquetFile(path)
                metric = ArtifactFileMetric(
                    name,
                    path.stat().st_size,
                    checksum,
                    parquet.metadata.num_rows,
                    parquet.metadata.num_row_groups,
                )
                first_files.append(metric)
                first_bundle_bytes += metric.size_bytes
                if name == "tape_manifest.parquet":
                    summary_manifest_bytes += metric.size_bytes
        summary = run / "codec_summary.json"
        if summary.is_file() and not summary.is_symlink():
            _read_json(summary)
            if run_field == "first_run_id":
                size = summary.stat().st_size
                first_bundle_bytes += size
                summary_manifest_bytes += size
    return (
        tuple(first_files),
        summary_manifest_bytes,
        first_bundle_bytes,
        _as_int(checkpoint["generated_disk_bytes"]),
    )


def _scenario_checkpoint_path(
    metrics_root: Path,
    spec: MethodSpec,
    unit: MotionCohortUnit,
) -> Path:
    return (
        metrics_root / "checkpoints-v1" / spec.key / f"{unit.source_scenario_id}.json"
    )


def _scenario_result(
    root: Path,
    cache_root: Path,
    baseline_root: Path,
    spec: MethodSpec,
    unit: MotionCohortUnit,
) -> dict[str, object]:
    started = time.perf_counter()
    scenario, frame, agents, trajectories = _bundle(root, cache_root, unit)
    encoding_started = time.perf_counter()
    tape = (
        None
        if spec.encoder is None
        else spec.encoder(scenario, frame, agents, trajectories)
    )
    encoding_seconds = time.perf_counter() - encoding_started
    tracks = {} if tape is None else {item.trajectory_id: item for item in tape.tracks}

    verification_started = time.perf_counter()
    baseline = _baseline_checkpoint(baseline_root, spec, unit)
    files, summary_bytes, bundle_bytes, output_disk_bytes = _artifact_metrics(
        baseline_root, baseline
    )
    verification_seconds = time.perf_counter() - verification_started

    replay_started = time.perf_counter()
    evaluations = [
        evaluate_trajectory_motion(
            spec.key,
            spec.configuration_identity,
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
    semantic_tracks = {item.trajectory_id: item for item in semantic_tape.tracks}
    event_rows = [
        row.to_dict()
        for trajectory, evaluation in zip(trajectories, evaluations, strict=True)
        for row in evaluate_trajectory_events(
            spec.key,
            spec.configuration_identity,
            trajectory,
            evaluation.replay_trajectory,
            semantic_tracks[trajectory.trajectory_id],
            _SEMANTIC_CONFIG,
        )
    ]
    semantic_seconds = time.perf_counter() - semantic_started

    repeated = evaluations[0]
    repeated_hash = evaluate_trajectory_motion(
        spec.key,
        spec.configuration_identity,
        trajectories[0],
        tracks.get(trajectories[0].trajectory_id),
    ).metrics.replay_hash
    determinism_mismatches = int(repeated.metrics.replay_hash != repeated_hash)
    total_seconds = time.perf_counter() - started
    scenario_metrics = ScenarioArtifactRuntimeMetrics(
        method_id=spec.key,
        configuration_id=spec.configuration_identity,
        scenario_id=scenario.scenario_id,
        status="completed",
        artifact_files=files,
        serialized_representation_bytes=_as_int(baseline["encoded_bytes"]),
        summary_manifest_bytes=summary_bytes,
        artifact_bundle_bytes=bundle_bytes,
        output_disk_bytes=output_disk_bytes,
        encoding_seconds=encoding_seconds,
        artifact_writing_seconds=0.0,
        artifact_verification_seconds=verification_seconds,
        replay_evaluation_seconds=replay_seconds,
        semantic_redetection_seconds=semantic_seconds,
        total_seconds=total_seconds,
        peak_process_rss_bytes=peak_process_rss_bytes(),
    )
    return {
        "checkpoint_version": _CHECKPOINT_VERSION,
        "method_id": spec.key,
        "configuration_id": spec.configuration_identity,
        "source_scenario_id": unit.source_scenario_id,
        "scenario_id": scenario.scenario_id,
        "trajectory_ids": [trajectory.trajectory_id for trajectory in trajectories],
        "motion_records": [item.metrics.to_dict() for item in evaluations],
        "event_records": event_rows,
        "scenario_record": scenario_metrics.to_dict(),
        "failure_records": [],
        "determinism_repeat_count": 1,
        "determinism_mismatch_count": determinism_mismatches,
    }


def _failure_checkpoint(
    spec: MethodSpec,
    unit: MotionCohortUnit,
    error: Exception,
) -> dict[str, object]:
    failure = EvaluationFailure(
        spec.key,
        spec.configuration_identity,
        unit.source_scenario_id,
        None,
        "scenario",
        type(error).__name__,
        str(error),
    )
    return {
        "checkpoint_version": _CHECKPOINT_VERSION,
        "method_id": spec.key,
        "configuration_id": spec.configuration_identity,
        "source_scenario_id": unit.source_scenario_id,
        "scenario_id": unit.source_scenario_id,
        "trajectory_ids": [],
        "motion_records": [],
        "event_records": [],
        "scenario_record": None,
        "failure_records": [failure.to_dict()],
        "determinism_repeat_count": 0,
        "determinism_mismatch_count": 0,
    }


def _validate_checkpoint(
    row: Mapping[str, object],
    spec: MethodSpec,
    unit: MotionCohortUnit,
) -> None:
    if (
        row.get("checkpoint_version") != _CHECKPOINT_VERSION
        or row.get("method_id") != spec.key
        or row.get("configuration_id") != spec.configuration_identity
        or row.get("source_scenario_id") != unit.source_scenario_id
    ):
        raise ArtifactError("metrics checkpoint contract differs")
    for name in (
        "trajectory_ids",
        "motion_records",
        "event_records",
        "failure_records",
    ):
        value = row.get(name)
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ArtifactError(f"metrics checkpoint {name} is invalid")


def _collect(
    checkpoints: Sequence[Mapping[str, object]],
) -> tuple[
    list[Mapping[str, object]],
    list[Mapping[str, object]],
    list[Mapping[str, object]],
    list[Mapping[str, object]],
]:
    motion: list[Mapping[str, object]] = []
    events: list[Mapping[str, object]] = []
    scenarios: list[Mapping[str, object]] = []
    failures: list[Mapping[str, object]] = []
    for row in checkpoints:
        for target, name in (
            (motion, "motion_records"),
            (events, "event_records"),
            (failures, "failure_records"),
        ):
            target.extend(cast(Sequence[Mapping[str, object]], row[name]))
        scenario = row.get("scenario_record")
        if isinstance(scenario, Mapping):
            scenarios.append(scenario)
    return motion, events, scenarios, failures


def _statistics_dict(values: Sequence[float]) -> dict[str, object]:
    return descriptive_statistics(values).to_dict()


def _macro_statistics(
    rows: Sequence[Mapping[str, object]],
    field_name: str,
) -> dict[str, object]:
    means: list[float] = []
    for row in rows:
        values = row.get(field_name)
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            normalized = [_as_float(value) for value in values]
            if normalized:
                means.append(math.fsum(normalized) / len(normalized))
    return _statistics_dict(means)


def _aggregate_motion(
    specs: Sequence[MethodSpec],
    motion_rows: Sequence[Mapping[str, object]],
    scenario_rows: Sequence[Mapping[str, object]],
    failures: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for spec in specs:
        rows = [row for row in motion_rows if row["method_id"] == spec.key]
        scenario = [row for row in scenario_rows if row["method_id"] == spec.key]
        method_failures = [row for row in failures if row["method_id"] == spec.key]
        pooled = {
            name: [
                _as_float(value)
                for row in rows
                for value in cast(Sequence[object], row[field])
            ]
            for name, field in (
                ("position_m", "position_errors_m"),
                ("heading_rad", "heading_errors_rad"),
                ("velocity_vector_mps", "velocity_errors_mps"),
            )
        }
        counts = {
            name: sum(_as_int(row[field]) for row in rows)
            for name, field in (
                ("trajectories", "_one"),
                ("source_samples", "source_sample_count"),
                ("valid_samples", "valid_sample_count"),
                ("valid_runs", "valid_run_count"),
                ("retained_keyframes", "retained_keyframe_count"),
                ("procedural_segments", "segment_count"),
                ("hold_segments", "hold_count"),
                ("linear_segments", "linear_count"),
                ("hermite_segments", "hermite_count"),
            )
            if field != "_one"
        }
        counts["trajectories"] = len(rows)
        runtimes = {
            name: math.fsum(_as_float(row[name]) for row in scenario)
            for name in (
                "encoding_seconds",
                "artifact_writing_seconds",
                "artifact_verification_seconds",
                "replay_evaluation_seconds",
                "semantic_redetection_seconds",
                "total_seconds",
            )
        }
        results.append(
            {
                "method_id": spec.key,
                "family": spec.family,
                "configuration": dict(spec.configuration),
                "configuration_id": spec.configuration_identity,
                "trajectory_count": len(rows),
                "scenario_count": len(scenario),
                "failure_count": len(method_failures),
                "counts": counts,
                "serialized_representation_bytes": sum(
                    _as_int(row["serialized_representation_bytes"]) for row in scenario
                ),
                "position": {
                    "micro": _statistics_dict(pooled["position_m"]),
                    "trajectory_macro": _macro_statistics(rows, "position_errors_m"),
                },
                "heading": {
                    "micro": _statistics_dict(pooled["heading_rad"]),
                    "trajectory_macro": _macro_statistics(rows, "heading_errors_rad"),
                    "missing_count": sum(
                        _as_int(row["heading_missing_count"]) for row in rows
                    ),
                },
                "velocity": {
                    "micro": _statistics_dict(pooled["velocity_vector_mps"]),
                    "trajectory_macro": _macro_statistics(rows, "velocity_errors_mps"),
                    "missing_count": sum(
                        _as_int(row["velocity_missing_count"]) for row in rows
                    ),
                },
                "endpoint_maxima": {
                    name: max(
                        (
                            _as_float(row[field])
                            for row in rows
                            if row.get(field) is not None
                        ),
                        default=None,
                    )
                    for name, field in (
                        ("position_m", "endpoint_position_maximum_m"),
                        ("heading_rad", "endpoint_heading_maximum_rad"),
                        ("velocity_mps", "endpoint_velocity_maximum_mps"),
                    )
                },
                "gap": {
                    "invalid_source_timestamp_count": sum(
                        _as_int(row["invalid_source_timestamp_count"]) for row in rows
                    ),
                    "midpoint_probe_count": sum(
                        _as_int(row["midpoint_probe_count"]) for row in rows
                    ),
                    "unexpected_replay_state_count": sum(
                        _as_int(row["unexpected_replay_state_count"]) for row in rows
                    ),
                    "all_passed": all(
                        row.get("gap_preservation_passed") is True for row in rows
                    ),
                },
                "runtime": {
                    **runtimes,
                    "peak_process_rss_bytes": max(
                        (_as_int(row["peak_process_rss_bytes"]) for row in scenario),
                        default=0,
                    ),
                    "output_disk_bytes": sum(
                        _as_int(row["output_disk_bytes"]) for row in scenario
                    ),
                    "trajectories_per_second": (
                        len(rows) / runtimes["total_seconds"]
                        if runtimes["total_seconds"]
                        else None
                    ),
                },
            }
        )
    return results


def _aggregate_events(
    specs: Sequence[MethodSpec],
    event_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for spec in specs:
        method_rows = [row for row in event_rows if row["method_id"] == spec.key]
        by_type: list[dict[str, object]] = []
        for event_type in (
            "gap",
            "stop",
            "left_turn",
            "right_turn",
            "acceleration",
            "braking",
        ):
            rows = [row for row in method_rows if row["event_type"] == event_type]
            source = sum(_as_int(row["source_event_count"]) for row in rows)
            replay = sum(_as_int(row["replay_event_count"]) for row in rows)
            matched = sum(_as_int(row["matched_event_count"]) for row in rows)
            precision = matched / replay if replay else (1.0 if source == 0 else 0.0)
            recall = matched / source if source else (1.0 if replay == 0 else 0.0)
            f1 = (
                2.0 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            )
            by_type.append(
                {
                    "event_type": event_type,
                    "trajectory_count": len(rows),
                    "source_event_count": source,
                    "replay_event_count": replay,
                    "matched_event_count": matched,
                    "micro_precision": precision,
                    "micro_recall": recall,
                    "micro_f1": f1,
                    "macro_precision": math.fsum(
                        _as_float(row["precision"]) for row in rows
                    )
                    / len(rows),
                    "macro_recall": math.fsum(_as_float(row["recall"]) for row in rows)
                    / len(rows),
                    "macro_f1": math.fsum(_as_float(row["f1"]) for row in rows)
                    / len(rows),
                    "unmatched_source_count": sum(
                        _as_int(row["unmatched_source_count"]) for row in rows
                    ),
                    "unmatched_replay_count": sum(
                        _as_int(row["unmatched_replay_count"]) for row in rows
                    ),
                    "start_boundary_error_ns": _statistics_dict(
                        [
                            float(value)
                            for row in rows
                            for value in cast(
                                Sequence[int], row["start_boundary_errors_ns"]
                            )
                        ]
                    ),
                    "end_boundary_error_ns": _statistics_dict(
                        [
                            float(value)
                            for row in rows
                            for value in cast(
                                Sequence[int], row["end_boundary_errors_ns"]
                            )
                        ]
                    ),
                    "anchor_time_error_ns": _statistics_dict(
                        [
                            float(value)
                            for row in rows
                            for value in cast(
                                Sequence[int], row["anchor_time_errors_ns"]
                            )
                        ]
                    ),
                }
            )
        results.append(
            {
                "method_id": spec.key,
                "configuration_id": spec.configuration_identity,
                "event_record_count": len(method_rows),
                "by_type": by_type,
            }
        )
    return results


def _close(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is right
    return abs(_as_float(left) - _as_float(right)) <= _NUMERIC_AUDIT_TOLERANCE


def _audit(
    methods: Sequence[Mapping[str, object]],
    baseline_results: Mapping[str, object],
) -> dict[str, object]:
    baseline_methods = baseline_results.get("methods")
    if not isinstance(baseline_methods, Sequence):
        raise ArtifactError("Batch 4.2 development methods are missing")
    by_key = {
        str(row["method_key"]): row
        for row in baseline_methods
        if isinstance(row, Mapping)
    }
    rows: list[dict[str, object]] = []
    for method in methods:
        key = str(method["method_id"])
        baseline = cast(Mapping[str, object], by_key[key])
        exact_checks: dict[str, bool] = {}
        counts = cast(Mapping[str, object], method["counts"])
        baseline_counts = cast(Mapping[str, object], baseline["counts"])
        for name in (
            "trajectories",
            "source_samples",
            "valid_samples",
            "valid_runs",
            "retained_keyframes",
            "procedural_segments",
            "hold_segments",
            "linear_segments",
            "hermite_segments",
        ):
            exact_checks[name] = counts[name] == baseline_counts[name]
        exact_checks["serialized_representation_bytes"] = (
            method["serialized_representation_bytes"]
            == baseline["encoded_artifact_bytes"]
        )
        float_checks: dict[str, bool] = {}
        baseline_errors = cast(Mapping[str, object], baseline["errors"])
        for family, baseline_name in (
            ("position", "position_m"),
            ("heading", "heading_rad"),
            ("velocity", "velocity_vector_mps"),
        ):
            current = cast(
                Mapping[str, object],
                cast(Mapping[str, object], method[family])["micro"],
            )
            previous = cast(Mapping[str, object], baseline_errors[baseline_name])
            for statistic in ("count", "mean", "median", "p95", "maximum"):
                float_checks[f"{family}.{statistic}"] = _close(
                    current[statistic], previous[statistic]
                )
        rows.append(
            {
                "method_id": key,
                "exact_checks": exact_checks,
                "floating_checks": float_checks,
                "all_exact_fields_match": all(exact_checks.values()),
                "all_floating_fields_match": all(float_checks.values()),
                "numerical_tolerance": _NUMERIC_AUDIT_TOLERANCE,
                "runtime_audit": {
                    "policy": (
                        "reported side by side only; measured wall time is "
                        "nondeterministic and Batch 4.2 combined artifact write "
                        "and verification"
                    ),
                    "batch4_2": baseline["resources"],
                    "batch4_3": method["runtime"],
                },
            }
        )
    return {
        "schema_version": "1.0",
        "batch4_2_evidence_preserved": True,
        "scientifically_frozen_policy": (
            "sample-micro vectors use finite errors at original valid source "
            "timestamps; trajectory-macro summaries equally weight trajectories"
        ),
        "known_policy_discrepancies": [
            {
                "field": "artifact runtime",
                "batch4_2_policy": "combined artifact write and verification",
                "batch4_3_policy": (
                    "reuses immutable artifacts, records zero new representation "
                    "write time, and times verification separately"
                ),
            }
        ],
        "methods": rows,
        "all_exact_fields_match": all(
            bool(row["all_exact_fields_match"]) for row in rows
        ),
        "all_floating_fields_match": all(
            bool(row["all_floating_fields_match"]) for row in rows
        ),
    }


def _artifact_descriptor(
    root: Path, artifact: MotionMetricArtifact
) -> dict[str, object]:
    value = artifact.to_dict()
    value["path"] = artifact.path.relative_to(root).as_posix()
    return value


def _summary_markdown(
    motion: Sequence[Mapping[str, object]],
    failure_count: int,
    achieved: bool,
) -> str:
    lines = [
        "# Phase 4 Motion and Semantic Evaluation Metrics",
        "",
        f"- Batch decision: `{'achieved' if achieved else 'not_achieved'}`",
        "- Cohort: 150 genuine AV2 development scenarios",
        "- Included trajectories per method: 7,012",
        "- Method/configurations: 18",
        f"- Evaluation failures: {failure_count}",
        "- Pilot/test outcomes accessed: no",
        "",
        "## Metric Summary",
        "",
        "| Method | Position mean (m) | Position p95 (m) | Event records |",
        "|---|---:|---:|---:|",
    ]
    for row in motion:
        position = cast(
            Mapping[str, object],
            cast(Mapping[str, object], row["position"])["micro"],
        )
        lines.append(
            f"| `{row['method_id']}` | {position['mean']} | "
            f"{position['p95']} | {7012 * 6} |"
        )
    lines.extend(
        [
            "",
            "These are development results. No ranking, significance, Pareto, "
            "matched-budget, or final-method claim is made.",
            "",
        ]
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
        "--cohort-cache-root",
        type=Path,
        default=Path("cache/phase4_motion_cohort/cohort"),
    )
    parser.add_argument(
        "--baseline-root",
        type=Path,
        default=Path("cache/phase4_motion_baselines"),
    )
    parser.add_argument(
        "--baseline-evidence-root",
        type=Path,
        default=Path("results/phase4/motion_baselines"),
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("cache/phase4_motion_metrics"),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("results/phase4/motion_metrics"),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    root = args.repository_root.resolve()
    cohort_evidence_root = (root / args.cohort_evidence_root).resolve()
    cache_root = (root / args.cohort_cache_root).resolve()
    baseline_root = (root / args.baseline_root).resolve()
    baseline_evidence_root = (root / args.baseline_evidence_root).resolve()
    generated_root = (root / args.generated_root).resolve()
    evidence_root = (root / args.evidence_root).resolve()
    for path in (
        cohort_evidence_root,
        cache_root,
        baseline_root,
        baseline_evidence_root,
        generated_root,
        evidence_root,
    ):
        if not path.is_relative_to(root):
            raise ArtifactError("campaign path escapes repository root")

    specs = _method_specs()
    manifest, units, baseline_results = _verify_inputs(
        root, cohort_evidence_root, baseline_evidence_root, specs
    )
    trajectory_rows: list[tuple[str, list[str]]] = []
    for unit in units:
        trajectories = _bundle(root, cache_root, unit)[3]
        trajectory_rows.append(
            (
                unit.source_scenario_id,
                [trajectory.trajectory_id for trajectory in trajectories],
            )
        )
    trajectory_identity = canonical_sha256(
        "phase4-motion-baseline-included-trajectories",
        [
            {"scenario_id": scenario_id, "trajectory_ids": ids}
            for scenario_id, ids in trajectory_rows
        ],
    )
    if trajectory_identity != _TRAJECTORY_IDENTITY:
        raise ArtifactError("included development trajectory identity differs")
    campaign = MetricsCampaignManifest(
        _BATCH,
        manifest.cohort_identity,
        _VALIDATION_IDENTITY,
        trajectory_identity,
        [unit.source_scenario_id for unit in units],
        [spec.configuration_identity for spec in specs],
        semantic_motion_configuration_identity(_SEMANTIC_CONFIG),
        False,
    )

    checkpoints: list[Mapping[str, object]] = []
    for spec in specs:
        for index, unit in enumerate(units, start=1):
            path = _scenario_checkpoint_path(generated_root, spec, unit)
            if path.is_file():
                row = _read_json(path)
                _validate_checkpoint(row, spec, unit)
            else:
                try:
                    row = _scenario_result(root, cache_root, baseline_root, spec, unit)
                except Exception as error:  # noqa: BLE001
                    row = _failure_checkpoint(spec, unit, error)
                _write_json(path, row)
            checkpoints.append(row)
            print(
                f"{spec.key}: {index}/150 {unit.source_scenario_id} "
                f"{'failed' if row['failure_records'] else 'completed'}",
                flush=True,
            )

    motion_rows, event_rows, scenario_rows, failure_rows = _collect(checkpoints)
    final_root = generated_root / "final-v1"
    final_root.mkdir(parents=True, exist_ok=True)
    table_specs = (
        (
            "trajectory_motion_metrics.parquet",
            motion_rows,
            MotionMetricTable.TRAJECTORY_MOTION,
        ),
        (
            "trajectory_event_metrics.parquet",
            event_rows,
            MotionMetricTable.TRAJECTORY_EVENT,
        ),
        (
            "scenario_method_metrics.parquet",
            scenario_rows,
            MotionMetricTable.SCENARIO_METHOD,
        ),
        (
            "evaluation_failures.parquet",
            failure_rows,
            MotionMetricTable.EVALUATION_FAILURE,
        ),
    )
    artifacts: list[MotionMetricArtifact] = []
    for name, rows, table_name in table_specs:
        path = final_root / name
        if path.is_file():
            table = read_metric_parquet(
                path, table_name, maximum_rows=max(len(rows), 1_000_000)
            )
            if table.to_pylist() != sorted(
                [dict(row) for row in rows],
                key=lambda row: tuple(
                    "" if row.get(key) is None else row[key]
                    for key in (
                        (
                            "method_id",
                            "configuration_id",
                            "scenario_id",
                            "trajectory_id",
                        )
                        if table_name is MotionMetricTable.TRAJECTORY_MOTION
                        else (
                            (
                                "method_id",
                                "configuration_id",
                                "scenario_id",
                                "trajectory_id",
                                "event_type",
                            )
                            if table_name is MotionMetricTable.TRAJECTORY_EVENT
                            else (
                                ("method_id", "configuration_id", "scenario_id")
                                if table_name is MotionMetricTable.SCENARIO_METHOD
                                else (
                                    "method_id",
                                    "configuration_id",
                                    "scenario_id",
                                    "trajectory_id",
                                    "failure_stage",
                                )
                            )
                        )
                    )
                ),
            ):
                raise ArtifactError("existing final metric table content differs")
            parquet = pq.ParquetFile(path)
            artifact = MotionMetricArtifact(
                table_name,
                path,
                path.stat().st_size,
                _sha256(path),
                parquet.metadata.num_rows,
                parquet.metadata.num_row_groups,
            )
        else:
            artifact = write_metric_parquet(path, rows, table_name)
        verify_metric_parquet(artifact)
        artifacts.append(artifact)

    method_results = _aggregate_motion(specs, motion_rows, scenario_rows, failure_rows)
    event_results = _aggregate_events(specs, event_rows)
    audit = _audit(method_results, baseline_results)
    repeat_count = sum(_as_int(row["determinism_repeat_count"]) for row in checkpoints)
    mismatch_count = sum(
        _as_int(row["determinism_mismatch_count"]) for row in checkpoints
    )
    raw = next(row for row in method_results if row["method_id"] == "raw_samples")
    exact = next(
        row for row in method_results if row["method_id"] == "exact_adjacent_sample"
    )
    raw_events = next(row for row in event_results if row["method_id"] == "raw_samples")
    raw_zero = (
        cast(
            Mapping[str, object], cast(Mapping[str, object], raw["position"])["micro"]
        )["maximum"]
        == 0.0
    )
    exact_zero = (
        cast(
            Mapping[str, object],
            cast(Mapping[str, object], exact["position"])["micro"],
        )["maximum"]
        == 0.0
    )
    raw_event_perfect = all(
        item["micro_precision"] == 1.0
        and item["micro_recall"] == 1.0
        and item["micro_f1"] == 1.0
        for item in cast(Sequence[Mapping[str, object]], raw_events["by_type"])
    )
    achieved = (
        len(motion_rows) == 18 * 7012
        and len(event_rows) == 18 * 7012 * 6
        and len(scenario_rows) == 18 * 150
        and not failure_rows
        and all(row["trajectory_count"] == 7012 for row in method_results)
        and all(row["scenario_count"] == 150 for row in method_results)
        and all(
            cast(Mapping[str, object], row["gap"])["all_passed"] is True
            for row in method_results
        )
        and raw_zero
        and exact_zero
        and raw_event_perfect
        and mismatch_count == 0
        and audit["all_exact_fields_match"] is True
        and audit["all_floating_fields_match"] is True
    )
    raw_artifacts = {
        "metrics_campaign.json": {
            **campaign.to_dict(),
            "campaign_identity": campaign.identity,
            "command": "uv run --frozen python scripts/run_motion_metrics.py",
            "result_artifacts": [
                _artifact_descriptor(root, item) for item in artifacts
            ],
        },
        "metrics_summary.json": {
            "schema_version": "1.0",
            "campaign_identity": campaign.identity,
            "motion_methods": method_results,
            "event_methods": event_results,
            "failure_count": len(failure_rows),
        },
    }
    for name, value in raw_artifacts.items():
        path = final_root / name
        if path.is_file():
            if _read_json(path) != value:
                raise ArtifactError(f"existing raw metric JSON differs: {name}")
        else:
            _write_json(path, value, immutable=True)

    metric_contract = {
        "schema_version": "1.0",
        "batch": _BATCH,
        "campaign_identity": campaign.identity,
        "cohort_identity": _COHORT_IDENTITY,
        "development_validation_identity": _VALIDATION_IDENTITY,
        "included_trajectory_identity": _TRAJECTORY_IDENTITY,
        "development_scenario_ids": [unit.source_scenario_id for unit in units],
        "metric_definitions": {
            "position": (
                "Euclidean x-y when source z is absent; Euclidean x-y-z when "
                "source z exists; missing replay z then fails"
            ),
            "heading": "absolute shortest wrapped angular distance in radians",
            "velocity": "Euclidean x-y velocity-vector distance",
            "endpoint": "first and last valid sample of every valid run separately",
            "gap": (
                "all invalid source timestamps plus one integer midpoint between "
                "adjacent valid runs must replay to no state"
            ),
            "replay_hash_domain": "phase4-motion-metrics-replay-v1",
        },
        "quantile_policy": QUANTILE_POLICY,
        "aggregation_policy": {
            "sample_micro": "pool finite per-sample errors within method/configuration",
            "trajectory_macro": "equal average over per-trajectory means",
            "events_micro": "aggregate matched/replay/source counts before rates",
            "events_macro": "equal average over per-trajectory event-type rates",
        },
        "event_detector_configuration_identity": (
            semantic_motion_configuration_identity(_SEMANTIC_CONFIG)
        ),
        "event_matching": (
            "accepted Batch 3.5 deterministic event-type-specific greedy "
            "maximum-overlap policy with stable timing and index tie breaks"
        ),
        "result_tables": [_artifact_descriptor(root, item) for item in artifacts],
        "method_configurations": [
            {
                "method_id": spec.key,
                "configuration": dict(spec.configuration),
                "configuration_id": spec.configuration_identity,
            }
            for spec in specs
        ],
        "pilot_or_test_outcomes_accessed": False,
    }
    development_summary = {
        "schema_version": "1.0",
        "campaign_identity": campaign.identity,
        "scenario_count": 150,
        "included_trajectory_count": 7012,
        "motion_record_count": len(motion_rows),
        "methods": method_results,
        "raw_zero_error": raw_zero,
        "exact_zero_error": exact_zero,
    }
    event_summary = {
        "schema_version": "1.0",
        "campaign_identity": campaign.identity,
        "event_record_count": len(event_rows),
        "event_types": [
            "gap",
            "stop",
            "left_turn",
            "right_turn",
            "acceleration",
            "braking",
        ],
        "methods": event_results,
        "raw_event_preservation_perfect": raw_event_perfect,
    }
    failure_report = {
        "schema_version": "1.0",
        "campaign_identity": campaign.identity,
        "failure_count": len(failure_rows),
        "failures": failure_rows,
        "omitted_failures": False,
    }
    performance = {
        "schema_version": "1.0",
        "campaign_identity": campaign.identity,
        "hardware": {
            "operating_system": "Ubuntu under WSL2",
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
            "host_model": "ASUS reference laptop",
        },
        "execution_policy": "sequential_bounded_per_scenario",
        "artifact_write_policy": (
            "reuse immutable Batch 4.2 representations; zero new representation "
            "write time; metric tables written once after checkpoints"
        ),
        "methods": [
            {"method_id": row["method_id"], "runtime": row["runtime"]}
            for row in method_results
        ],
        "raw_metric_artifact_bytes": sum(item.size_bytes for item in artifacts),
    }
    evidence_root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "metrics_contract.json": metric_contract,
        "development_metric_summary.json": development_summary,
        "event_preservation_summary.json": event_summary,
        "audit_report.json": audit,
        "failure_report.json": failure_report,
        "performance_report.json": performance,
    }
    for name, value in payloads.items():
        _write_json(evidence_root / name, value)
    summary_path = evidence_root / "summary.md"
    summary_path.write_text(
        _summary_markdown(method_results, len(failure_rows), achieved),
        encoding="utf-8",
        newline="\n",
    )
    checksum_names = (*payloads.keys(), "summary.md")
    evidence = {
        "schema_version": "1.0",
        "batch": _BATCH,
        "batch_decision": "achieved" if achieved else "not_achieved",
        "pass_statement": _PASS_STATEMENT if achieved else None,
        "starting_head": _STARTING_HEAD,
        "campaign_identity": campaign.identity,
        "cohort_identity": _COHORT_IDENTITY,
        "development_validation_identity": _VALIDATION_IDENTITY,
        "included_trajectory_identity": _TRAJECTORY_IDENTITY,
        "official_source": manifest.official_source,
        "development_scenario_count": 150,
        "included_trajectory_count": 7012,
        "method_configuration_count": 18,
        "trajectory_motion_record_count": len(motion_rows),
        "trajectory_event_record_count": len(event_rows),
        "scenario_method_record_count": len(scenario_rows),
        "failure_count": len(failure_rows),
        "raw_zero_error": raw_zero,
        "exact_zero_error": exact_zero,
        "raw_event_preservation_perfect": raw_event_perfect,
        "determinism_repeat_count": repeat_count,
        "determinism_mismatch_count": mismatch_count,
        "batch4_2_audit_passed": (
            audit["all_exact_fields_match"] is True
            and audit["all_floating_fields_match"] is True
        ),
        "pilot_or_test_outcomes_accessed": False,
        "provider_or_generated_data_tracked": False,
        "evidence_file_sha256": {
            name: _sha256(evidence_root / name) for name in checksum_names
        },
    }
    _write_json(evidence_root / "evidence.json", evidence)
    print(canonical_json_text(evidence), end="")
    return 0 if achieved else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArtifactError, ValidationError) as error:
        print(f"motion metrics campaign failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
