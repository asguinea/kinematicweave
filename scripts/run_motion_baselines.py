"""Run the frozen Phase 4 comparable motion baselines on development only."""

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
    validate_baseline_replay,
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
from kinematicweave.data.baseline_artifacts import (
    materialize_equivalent_procedural_artifacts,
    materialize_equivalent_raw_artifacts,
)
from kinematicweave.data.procedural_artifacts import verify_procedural_tape_artifacts
from kinematicweave.domain.procedural import ProceduralPrimitiveType, ProceduralTape
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    MotionCohortManifest,
    MotionCohortUnit,
    motion_cohort_manifest_from_json,
)
from kinematicweave.resources import peak_process_rss_bytes

_BATCH = "4.2"
_STARTING_HEAD = "0870b0b6a02c6ab35f8f20ce36e3075b0d23316e"
_COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
_DEVELOPMENT_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)
_PASS_STATEMENT = (
    "Phase 4 comparable motion baselines completed on all 150 genuine AV2 "
    "development scenarios."
)
_LINEAR_CONFIG = PiecewiseLinearCodecConfig(0.10)
_HERMITE_CONFIG = HermiteCodecConfig(0.10)
_HYBRID_CONFIG = VelocityBoundedCodecConfig(0.10, 1.00)
_CHECKPOINT_VERSION = "2.0"

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
            source_validation_report_identity=_DEVELOPMENT_VALIDATION_IDENTITY,
        )

    return encode


def _method_specs() -> tuple[MethodSpec, ...]:
    specs: list[MethodSpec] = []
    for config in required_baseline_grid():
        specs.append(
            MethodSpec(
                key=config.key,
                family=cast(BaselineMethod, config.method).value,
                configuration=json.loads(
                    canonical_json_text(
                        {
                            "method": cast(BaselineMethod, config.method).value,
                            "stride": config.stride,
                            "maximum_perpendicular_error_m": (
                                config.maximum_perpendicular_error_m
                            ),
                            "interval_ns": config.interval_ns,
                        }
                    )
                ),
                configuration_identity=baseline_config_identity(config),
                implementation_name=f"phase4.{cast(BaselineMethod, config.method).value}",
                implementation_version=BASELINE_IMPLEMENTATION_VERSION,
                baseline_config=config,
                encoder=None if not config.is_encoded else _baseline_encoder(config),
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
            source_validation_report_identity=_DEVELOPMENT_VALIDATION_IDENTITY,
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
            source_validation_report_identity=_DEVELOPMENT_VALIDATION_IDENTITY,
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
            source_validation_report_identity=_DEVELOPMENT_VALIDATION_IDENTITY,
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
            source_validation_report_identity=_DEVELOPMENT_VALIDATION_IDENTITY,
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
                {"method": "position_bounded_linear", "maximum_position_error_m": 0.10},
                encoder_parameters_identity(_LINEAR_CONFIG),
                LINEAR_NAME,
                LINEAR_VERSION,
                None,
                linear,
            ),
            MethodSpec(
                "unconstrained_hermite-error-0p1",
                "accepted_procedural",
                {"method": "unconstrained_hermite", "maximum_position_error_m": 0.10},
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
        raise ValidationError(
            "required method grid must contain exactly 18 configurations"
        )
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
        raise ArtifactError(f"cannot read JSON evidence: {path}") from error
    if not isinstance(value, Mapping):
        raise ArtifactError(f"JSON evidence is not an object: {path}")
    return value


def _as_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactError("checkpoint integer field is invalid")
    return value


def _as_float(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ArtifactError("checkpoint numeric field is invalid")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ArtifactError("checkpoint numeric field is not finite")
    return normalized


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(canonical_json_text(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


def _development_units(manifest: MotionCohortManifest) -> tuple[MotionCohortUnit, ...]:
    """Return only frozen development units without consulting other-role outcomes."""
    units = tuple(
        unit for unit in manifest.units if unit.cohort_role is CohortRole.DEVELOPMENT
    )
    if len(units) != 150:
        raise ArtifactError(
            "frozen development role must contain exactly 150 scenarios"
        )
    if any(not unit.validation_included for unit in units):
        raise ArtifactError("a frozen development unit failed canonical validation")
    return units


def _verify_source_evidence(
    repository_root: Path,
    evidence_root: Path,
) -> tuple[MotionCohortManifest, tuple[MotionCohortUnit, ...]]:
    evidence_path = evidence_root / "evidence.json"
    evidence = _read_json(evidence_path)
    if (
        evidence.get("cohort_decision") != "frozen"
        or evidence.get("cohort_identity") != _COHORT_IDENTITY
        or evidence.get("official_source")
        != "s3://argoverse/datasets/av2/motion-forecasting/"
    ):
        raise ArtifactError(
            "Batch 4.1 source evidence differs from the frozen contract"
        )
    checksums = evidence.get("evidence_file_sha256")
    if not isinstance(checksums, Mapping):
        raise ArtifactError("Batch 4.1 evidence checksum index is missing")
    for name in ("cohort_manifest.json", "validation_report.json"):
        expected = checksums.get(name)
        path = evidence_root / name
        if not isinstance(expected, str) or _sha256(path) != expected:
            raise ArtifactError(f"Batch 4.1 {name} checksum differs")
    manifest = motion_cohort_manifest_from_json(
        (evidence_root / "cohort_manifest.json").read_text(encoding="utf-8")
    )
    if manifest.cohort_identity != _COHORT_IDENTITY:
        raise ArtifactError("parsed cohort identity differs")
    return manifest, _development_units(manifest)


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
        raise ArtifactError("cache scenario identity differs from frozen membership")
    trajectories = included_motion_trajectories(source.trajectories)
    agent_ids = {trajectory.agent_id for trajectory in trajectories}
    agents = tuple(agent for agent in source.agents if agent.agent_id in agent_ids)
    scenario = replace(source.scenario, agent_count=len(agents))
    return scenario, source.coordinate_frame, agents, trajectories


def _trajectory_identity(
    rows: Sequence[tuple[str, Sequence[str]]],
) -> str:
    return canonical_sha256(
        "phase4-motion-baseline-included-trajectories",
        [
            {
                "scenario_id": scenario_id,
                "trajectory_ids": list(trajectory_ids),
            }
            for scenario_id, trajectory_ids in rows
        ],
    )


def _primitive_counts(tape: ProceduralTape | None) -> dict[str, int]:
    counts = {"hold": 0, "linear": 0, "cubic_hermite": 0}
    if tape is not None:
        for track in tape.tracks:
            for segment in track.segments:
                primitive = ProceduralPrimitiveType(segment.primitive_type)
                counts[primitive.value] += 1
    return counts


def _checkpoint_path(root: Path, method_key: str, scenario_id: str) -> Path:
    return root / "checkpoints-v2" / method_key / f"{scenario_id}.json"


def _verify_checkpoint_artifacts(
    generated_root: Path,
    row: Mapping[str, object],
) -> None:
    if row.get("status") != "completed":
        return
    checksums_value = row.get("artifact_checksums")
    if not isinstance(checksums_value, list) or not all(
        isinstance(value, str) and len(value) == 64 for value in checksums_value
    ):
        raise ArtifactError("completed checkpoint artifact checksums are invalid")
    checksums = cast(list[str], checksums_value)
    filenames = (
        ("trajectory_samples.parquet",)
        if len(checksums) == 1
        else (
            "tape_manifest.parquet",
            "procedural_tracks.parquet",
            "procedural_segments.parquet",
        )
    )
    if len(filenames) != len(checksums):
        raise ArtifactError("completed checkpoint artifact count differs")
    for field_name in ("first_run_id", "repeat_run_id"):
        run_id = row.get(field_name)
        if not isinstance(run_id, str):
            raise ArtifactError("completed checkpoint run identity is invalid")
        run = generated_root / "runs" / run_directory_name(run_id)
        if not (run / ".run.complete").is_file() or (run / ".run.partial").exists():
            raise ArtifactError("completed checkpoint run marker differs")
        for filename, checksum in zip(filenames, checksums, strict=True):
            path = run / filename
            if not path.is_file() or path.is_symlink() or _sha256(path) != checksum:
                raise ArtifactError("completed checkpoint artifact checksum differs")
        if len(checksums) == 3:
            _read_json(run / "codec_summary.json")


def _scenario_result(
    repository_root: Path,
    generated_root: Path,
    spec: MethodSpec,
    scenario: ScenarioRecord,
    frame: CoordinateFrameRecord,
    agents: tuple[AgentRecord, ...],
    trajectories: tuple[Trajectory, ...],
) -> dict[str, object]:
    started = time.perf_counter()
    tape: ProceduralTape | None = None
    encode_started = time.perf_counter()
    if spec.encoder is not None:
        tape = spec.encoder(scenario, frame, agents, trajectories)
    encode_seconds = time.perf_counter() - encode_started

    replay_started = time.perf_counter()
    source_samples = valid_samples = valid_runs = keyframes = 0
    position: list[float] = []
    heading: list[float] = []
    velocity: list[float] = []
    endpoint_error = 0.0
    gap_failures = 0
    tracks = (
        {} if tape is None else {track.trajectory_id: track for track in tape.tracks}
    )
    validation_config = spec.baseline_config or MotionBaselineConfig(
        BaselineMethod.UNIFORM_LINEAR,
        stride=1,
    )
    for trajectory in trajectories:
        validation = validate_baseline_replay(
            trajectory,
            track=tracks.get(trajectory.trajectory_id),
            config=(
                spec.baseline_config
                if spec.baseline_config is not None
                else validation_config
            ),
        )
        source_samples += validation.source_sample_count
        valid_samples += validation.valid_sample_count
        valid_runs += validation.valid_run_count
        keyframes += validation.retained_keyframe_count
        position.extend(validation.position_errors_m)
        heading.extend(validation.heading_errors_rad)
        velocity.extend(validation.velocity_errors_mps)
        endpoint_error = max(endpoint_error, validation.exact_endpoint_error_m)
        gap_failures += validation.gap_preservation_failures
    replay_seconds = time.perf_counter() - replay_started

    artifact_started = time.perf_counter()
    label = f"phase4-baseline:{spec.key}:{scenario.scenario_id}"
    if tape is None:
        artifacts = materialize_equivalent_raw_artifacts(
            repository_root,
            generated_root,
            label,
            trajectories,
        )
    else:
        artifacts = materialize_equivalent_procedural_artifacts(
            repository_root,
            generated_root,
            label,
            tape,
            verifier=lambda item: verify_procedural_tape_artifacts(
                repository_root,
                item,
                expected_tape=tape,
            ),
        )
    artifact_seconds = time.perf_counter() - artifact_started
    peak_memory = peak_process_rss_bytes()
    primitives = _primitive_counts(tape)
    total_seconds = time.perf_counter() - started
    return {
        "checkpoint_version": _CHECKPOINT_VERSION,
        "status": "completed",
        "method_key": spec.key,
        "configuration_identity": spec.configuration_identity,
        "scenario_id": scenario.scenario_id,
        "trajectory_ids": [trajectory.trajectory_id for trajectory in trajectories],
        "counts": {
            "trajectories": len(trajectories),
            "source_samples": source_samples,
            "valid_samples": valid_samples,
            "valid_runs": valid_runs,
            "retained_keyframes": keyframes,
            "procedural_segments": 0 if tape is None else tape.segment_count,
            "hold_segments": primitives["hold"],
            "linear_segments": primitives["linear"],
            "hermite_segments": primitives["cubic_hermite"],
        },
        "encoded_bytes": artifacts.encoded_bytes,
        "generated_disk_bytes": artifacts.generated_disk_bytes,
        "artifact_checksums": list(artifacts.content_checksums),
        "deterministic_checksum_agreement": artifacts.checksum_agreement,
        "first_run_id": artifacts.first_run_id,
        "repeat_run_id": artifacts.repeat_run_id,
        "position_errors_m": position,
        "heading_errors_rad": heading,
        "velocity_errors_mps": velocity,
        "exact_endpoint_error_m": endpoint_error,
        "gap_preservation_failures": gap_failures,
        "resources": {
            "encode_seconds": encode_seconds,
            "replay_seconds": replay_seconds,
            "artifact_seconds": artifact_seconds,
            "total_seconds": total_seconds,
            "peak_process_memory_bytes": peak_memory,
        },
    }


def _failure_result(
    spec: MethodSpec,
    scenario_id: str,
    error: Exception,
) -> dict[str, object]:
    return {
        "checkpoint_version": _CHECKPOINT_VERSION,
        "status": "failed",
        "method_key": spec.key,
        "configuration_identity": spec.configuration_identity,
        "scenario_id": scenario_id,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _statistics(values: Sequence[float]) -> dict[str, int | float | None]:
    return {
        "count": len(values),
        "mean": sum(values) / len(values) if values else None,
        "median": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "maximum": max(values) if values else None,
    }


def _aggregate(
    spec: MethodSpec,
    checkpoints: Sequence[Mapping[str, object]],
    raw_bytes: int,
) -> dict[str, object]:
    completed = [row for row in checkpoints if row.get("status") == "completed"]
    counts = {
        name: sum(cast(Mapping[str, int], row["counts"])[name] for row in completed)
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
        )
    }
    resources = {
        name: sum(
            _as_float(cast(Mapping[str, object], row["resources"])[name])
            for row in completed
        )
        for name in (
            "encode_seconds",
            "replay_seconds",
            "artifact_seconds",
            "total_seconds",
        )
    }
    peak_memory = max(
        (
            _as_int(
                cast(Mapping[str, object], row["resources"])[
                    "peak_process_memory_bytes"
                ]
            )
            for row in completed
        ),
        default=0,
    )
    encoded_bytes = sum(_as_int(row["encoded_bytes"]) for row in completed)
    position = [
        _as_float(value)
        for row in completed
        for value in cast(list[object], row["position_errors_m"])
    ]
    heading = [
        _as_float(value)
        for row in completed
        for value in cast(list[object], row["heading_errors_rad"])
    ]
    velocity = [
        _as_float(value)
        for row in completed
        for value in cast(list[object], row["velocity_errors_mps"])
    ]
    endpoint_error = max(
        (_as_float(row["exact_endpoint_error_m"]) for row in completed),
        default=0.0,
    )
    gap_failures = sum(_as_int(row["gap_preservation_failures"]) for row in completed)
    elapsed = resources["total_seconds"]
    return {
        "method_key": spec.key,
        "family": spec.family,
        "configuration": dict(spec.configuration),
        "configuration_identity": spec.configuration_identity,
        "implementation_name": spec.implementation_name,
        "implementation_version": spec.implementation_version,
        "scenarios_attempted": len(checkpoints),
        "scenarios_completed": len(completed),
        "trajectories_attempted": counts["trajectories"],
        "trajectories_completed": counts["trajectories"],
        "counts": counts,
        "encoded_artifact_bytes": encoded_bytes,
        "raw_canonical_bytes": raw_bytes,
        "compression_ratio": encoded_bytes / raw_bytes if raw_bytes else None,
        "errors": {
            "position_m": _statistics(position),
            "heading_rad": _statistics(heading),
            "velocity_vector_mps": _statistics(velocity),
            "exact_endpoint_error_m": endpoint_error,
            "gap_preservation_failures": gap_failures,
        },
        "source_timestamp_replay_count": len(position),
        "raw_source_timestamp_reconstruction_exact": (
            spec.key == BaselineMethod.RAW_SAMPLES.value
            and len(position) == counts["valid_samples"]
            and max(position, default=0.0) == 0.0
        ),
        "resources": {
            **resources,
            "trajectories_per_second": (
                counts["trajectories"] / elapsed if elapsed else None
            ),
            "peak_process_memory_bytes": peak_memory,
            "generated_disk_bytes": sum(
                _as_int(row["generated_disk_bytes"]) for row in completed
            ),
            "execution_policy": "sequential_bounded_per_scenario",
        },
        "deterministic_checksum_agreement": all(
            bool(row["deterministic_checksum_agreement"]) for row in completed
        ),
        "scenario_artifact_checksums": [
            {
                "scenario_id": row["scenario_id"],
                "checksums": row["artifact_checksums"],
            }
            for row in completed
        ],
        "scenario_artifact_identity": canonical_sha256(
            "phase4-motion-baseline-scenario-artifacts",
            [
                {
                    "scenario_id": row["scenario_id"],
                    "checksums": row["artifact_checksums"],
                }
                for row in completed
            ],
        ),
    }


def _summary(results: Sequence[Mapping[str, object]]) -> str:
    lines = [
        "# Phase 4 Comparable Motion Baselines",
        "",
        "All values below are observations from the frozen 150-scenario development cohort.",
        "They are development-grid evidence, not a final ranking or release selection.",
        "",
        "| Method/configuration | Keyframes | Segments | Bytes | Raw ratio | Position mean / p95 / max (m) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        counts = cast(Mapping[str, int], row["counts"])
        errors = cast(Mapping[str, object], row["errors"])
        position = cast(Mapping[str, float | int | None], errors["position_m"])
        lines.append(
            "| {key} | {keyframes:,} | {segments:,} | {bytes:,} | {ratio:.6f} | "
            "{mean:.6g} / {p95:.6g} / {maximum:.6g} |".format(
                key=row["method_key"],
                keyframes=counts["retained_keyframes"],
                segments=counts["procedural_segments"],
                bytes=_as_int(row["encoded_artifact_bytes"]),
                ratio=_as_float(row["compression_ratio"]),
                mean=float(position["mean"] or 0.0),
                p95=float(position["p95"] or 0.0),
                maximum=float(position["maximum"] or 0.0),
            )
        )
    lines.extend(
        (
            "",
            "The table includes the raw storage reference, uniform keyframe baselines,",
            "the RDP geometric baseline, fixed-time baselines, and the four accepted",
            "procedural comparison methods. No pilot or test outcome was inspected.",
            "",
            _PASS_STATEMENT,
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
        "--cohort-cache-root",
        type=Path,
        default=Path("cache/phase4_motion_cohort/cohort"),
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("cache/phase4_motion_baselines"),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("results/phase4/motion_baselines"),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    root = args.repository_root.resolve()
    cohort_evidence = (root / args.cohort_evidence_root).resolve()
    cache_root = (root / args.cohort_cache_root).resolve()
    generated_root = args.generated_root
    evidence_root = (root / args.evidence_root).resolve()
    if not cache_root.is_relative_to(root) or not evidence_root.is_relative_to(root):
        raise ArtifactError("campaign roots must remain inside the repository")
    manifest, units = _verify_source_evidence(root, cohort_evidence)
    specs = _method_specs()

    included_rows: list[tuple[str, tuple[str, ...]]] = []
    trajectory_count = 0
    for unit in units:
        bundle = _bundle(root, cache_root, unit)
        trajectory_ids = tuple(trajectory.trajectory_id for trajectory in bundle[3])
        included_rows.append((unit.source_scenario_id, trajectory_ids))
        trajectory_count += len(trajectory_ids)
    trajectory_identity = _trajectory_identity(included_rows)

    generated_absolute = (root / generated_root).resolve()
    generated_absolute.mkdir(parents=True, exist_ok=True)
    failures: list[Mapping[str, object]] = []
    all_checkpoints: dict[str, list[Mapping[str, object]]] = {}
    for spec in specs:
        method_rows: list[Mapping[str, object]] = []
        for index, unit in enumerate(units, start=1):
            checkpoint = _checkpoint_path(
                generated_absolute,
                spec.key,
                unit.source_scenario_id,
            )
            row: Mapping[str, object] | None = None
            if checkpoint.is_file():
                row = _read_json(checkpoint)
                if (
                    row.get("checkpoint_version") != _CHECKPOINT_VERSION
                    or row.get("method_key") != spec.key
                    or row.get("configuration_identity") != spec.configuration_identity
                ):
                    raise ArtifactError("campaign checkpoint contract differs")
            if row is None or row.get("status") != "completed":
                scenario, frame, agents, trajectories = _bundle(root, cache_root, unit)
                try:
                    row = _scenario_result(
                        root,
                        generated_root,
                        spec,
                        scenario,
                        frame,
                        agents,
                        trajectories,
                    )
                except Exception as error:  # noqa: BLE001
                    row = _failure_result(spec, unit.source_scenario_id, error)
                _write_json(checkpoint, row)
            _verify_checkpoint_artifacts(generated_absolute, row)
            method_rows.append(row)
            if row.get("status") != "completed":
                failures.append(row)
            print(
                f"{spec.key}: {index}/150 {unit.source_scenario_id} "
                f"{row.get('status')}",
                flush=True,
            )
        all_checkpoints[spec.key] = method_rows

    raw_rows = all_checkpoints[BaselineMethod.RAW_SAMPLES.value]
    raw_bytes = sum(
        _as_int(row["encoded_bytes"])
        for row in raw_rows
        if row.get("status") == "completed"
    )
    results = [_aggregate(spec, all_checkpoints[spec.key], raw_bytes) for spec in specs]
    achieved = (
        not failures
        and all(row["scenarios_completed"] == 150 for row in results)
        and all(row["trajectories_completed"] == trajectory_count for row in results)
        and all(row["deterministic_checksum_agreement"] for row in results)
    )
    contract = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "cohort_identity": manifest.cohort_identity,
        "source_validation_identity": _DEVELOPMENT_VALIDATION_IDENTITY,
        "official_source": manifest.official_source,
        "cohort_role": "development",
        "scenario_count": len(units),
        "development_scenario_ids": [unit.source_scenario_id for unit in units],
        "included_trajectory_count": trajectory_count,
        "included_trajectory_identity": trajectory_identity,
        "methods": [
            {
                "method_key": spec.key,
                "family": spec.family,
                "configuration": dict(spec.configuration),
                "configuration_identity": spec.configuration_identity,
                "implementation_name": spec.implementation_name,
                "implementation_version": spec.implementation_version,
            }
            for spec in specs
        ],
        "motion_encoding_uses_map_data": False,
        "parameter_grid_frozen_before_results": True,
        "pilot_or_test_outcomes_accessed": False,
    }
    failure_report = {
        "batch": _BATCH,
        "failure_count": len(failures),
        "failures": failures,
        "omitted_failures": False,
    }
    performance = {
        "batch": _BATCH,
        "hardware": {
            "operating_system": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
            "wsl2": "microsoft" in platform.release().lower(),
            "gpu_used": False,
        },
        "methods": [
            {
                "method_key": row["method_key"],
                "resources": row["resources"],
            }
            for row in results
        ],
    }
    evidence_root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "baseline_contract.json": contract,
        "development_results.json": {
            "batch": _BATCH,
            "cohort_identity": manifest.cohort_identity,
            "scenario_count": len(units),
            "included_trajectory_count": trajectory_count,
            "methods": results,
        },
        "failure_report.json": failure_report,
        "performance_report.json": performance,
    }
    for name, value in payloads.items():
        _write_json(evidence_root / name, value)
    summary_path = evidence_root / "summary.md"
    summary_path.write_text(_summary(results), encoding="utf-8", newline="\n")
    checksum_files = (*payloads.keys(), "summary.md")
    evidence = {
        "batch": _BATCH,
        "batch_decision": "achieved" if achieved else "not_achieved",
        "pass_statement": _PASS_STATEMENT if achieved else None,
        "starting_head": _STARTING_HEAD,
        "cohort_identity": manifest.cohort_identity,
        "source_validation_identity": _DEVELOPMENT_VALIDATION_IDENTITY,
        "official_source": manifest.official_source,
        "development_scenario_count": len(units),
        "included_trajectory_count": trajectory_count,
        "method_configuration_count": len(specs),
        "failure_count": len(failures),
        "all_equivalent_artifact_checksums_agree": all(
            row["deterministic_checksum_agreement"] for row in results
        ),
        "provider_or_generated_data_tracked": False,
        "pilot_or_test_outcomes_accessed": False,
        "evidence_file_sha256": {
            name: _sha256(evidence_root / name) for name in checksum_files
        },
    }
    _write_json(evidence_root / "evidence.json", evidence)
    print(canonical_json_text(evidence), end="")
    return 0 if achieved else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArtifactError, ValidationError) as error:
        print(f"motion baseline campaign failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
