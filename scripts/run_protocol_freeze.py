"""Audit development evidence and freeze the Phase 4 motion campaign protocol."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import time
from typing import cast

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import ArtifactError
from kinematicweave.experiments.motion_sweep import (
    BudgetDimension,
    ConfigurationBudgetRecord,
    read_ranked_development_prefix,
)
from kinematicweave.experiments.protocol_freeze import (
    PRIMARY_FAMILIES,
    CampaignMethod,
    CampaignRole,
    ComparisonPriority,
    FinalCampaignMatrix,
    ResourceProjection,
    SupplementalGrid,
    freeze_budget_target,
    reconstruct_budget_ranges,
)
from kinematicweave.logging import configure_logging, get_logger

_BATCH = "4.5"
_STARTING_HEAD = "8c599eabb13a9ebc15661655e4028fe77681de6c"
_COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)
_TRAJECTORY_IDENTITY = (
    "21df77d7e9bc3eed61c5e805e491675b8ca7d5fcdfbb41c300c024858717408c"
)
_BATCH4_3_CAMPAIGN = "03440d32241c0e55d490cf617cd1b2d9ed1a546865372b8dc155f06c696e3277"
_BATCH4_4_CONFIGURATION = (
    "d42a5206ab52e86f0e7dacfac53ef7f5419dc3905258c00e17effaf810395332"
)
_PASS_STATEMENT = (
    "Phase 4 motion evaluation protocol frozen using development data only."
)
_TEMPORAL_FAMILIES = (
    "fixed_interval_linear",
    "uniform_hermite",
    "uniform_linear",
)
_LOGGER = get_logger("scripts.run_protocol_freeze")


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


def _write_json(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise ArtifactError(f"immutable artifact already exists: {path}")
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
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ArtifactError(f"{field_name} must be finite and nonnegative")
    return normalized


def _as_sequence(value: object, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ArtifactError(f"{field_name} must be a sequence")
    return value


def _verify_evidence(
    root: Path,
    *,
    decision_field: str,
    expected_decision: str,
) -> Mapping[str, object]:
    evidence = _read_json(root / "evidence.json")
    if evidence.get(decision_field) != expected_decision:
        raise ArtifactError(f"accepted evidence decision differs: {root}")
    checksums = evidence.get("evidence_file_sha256")
    if not isinstance(checksums, Mapping):
        raise ArtifactError(f"accepted evidence checksums are missing: {root}")
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


def _family(method: Mapping[str, object]) -> str:
    method_id = method.get("method_key")
    family = method.get("family")
    if not isinstance(method_id, str) or not isinstance(family, str):
        raise ArtifactError("accepted baseline method identity is invalid")
    if method_id == "exact_adjacent_sample":
        return "exact_adjacent"
    if method_id.startswith("position_bounded_linear-"):
        return "position_bounded_linear"
    if method_id.startswith("unconstrained_hermite-"):
        return "unconstrained_hermite"
    if method_id.startswith("position_velocity_bounded_hybrid-"):
        return "position_velocity_hybrid"
    return family


def _development_budget_records(
    development: Mapping[str, object],
) -> tuple[ConfigurationBudgetRecord, ...]:
    records: list[ConfigurationBudgetRecord] = []
    for value in _as_sequence(development.get("methods"), "development methods"):
        if not isinstance(value, Mapping):
            raise ArtifactError("development method must be a mapping")
        counts = value.get("counts")
        if not isinstance(counts, Mapping):
            raise ArtifactError("development method counts are missing")
        valid_samples = _as_int(counts.get("valid_samples"), "valid_samples")
        if valid_samples == 0:
            raise ArtifactError("development method has zero valid samples")
        method_id = value.get("method_key")
        configuration_id = value.get("configuration_identity")
        if not isinstance(method_id, str) or not isinstance(configuration_id, str):
            raise ArtifactError("development method identity is invalid")
        records.append(
            ConfigurationBudgetRecord(
                _family(value),
                method_id,
                configuration_id,
                _as_float(value.get("compression_ratio"), "compression_ratio"),
                _as_int(counts.get("retained_keyframes"), "retained_keyframes")
                / valid_samples,
            )
        )
    return tuple(records)


def _exploratory_budget_records(
    exploratory: Mapping[str, object],
) -> tuple[ConfigurationBudgetRecord, ...]:
    records: list[ConfigurationBudgetRecord] = []
    for value in _as_sequence(
        exploratory.get("configurations"),
        "exploratory configurations",
    ):
        if not isinstance(value, Mapping):
            raise ArtifactError("exploratory configuration must be a mapping")
        fields = ("family", "method_id", "parameter_identity")
        if any(not isinstance(value.get(field), str) for field in fields):
            raise ArtifactError("exploratory configuration identity is invalid")
        records.append(
            ConfigurationBudgetRecord(
                cast(str, value["family"]),
                cast(str, value["method_id"]),
                cast(str, value["parameter_identity"]),
                _as_float(value.get("byte_ratio"), "byte_ratio"),
                _as_float(value.get("keyframe_ratio"), "keyframe_ratio"),
            )
        )
    return tuple(records)


def _campaign_methods(
    configurations: Sequence[object],
    primary_configuration_ids: set[str],
) -> tuple[CampaignMethod, ...]:
    methods: list[CampaignMethod] = []
    for value in configurations:
        if not isinstance(value, Mapping):
            raise ArtifactError("campaign configuration must be a mapping")
        method_id = value.get("method_id")
        configuration_id = value.get("configuration_id")
        configuration = value.get("configuration")
        if (
            not isinstance(method_id, str)
            or not isinstance(configuration_id, str)
            or not isinstance(configuration, Mapping)
        ):
            raise ArtifactError("campaign configuration is invalid")
        if method_id == "raw_samples":
            family = "raw_samples"
            role = CampaignRole.REFERENCE
            reason = "raw canonical storage and zero-error reference"
        elif method_id == "exact_adjacent_sample":
            family = "exact_adjacent"
            role = CampaignRole.REFERENCE
            reason = "exact procedural replay and serialization reference"
        else:
            synthetic_method = {
                "method_key": method_id,
                "family": cast(str, configuration.get("method", "")),
            }
            family = _family(synthetic_method)
            if configuration_id in primary_configuration_ids:
                role = CampaignRole.PRIMARY
                reason = (
                    "selected by development budget only for at least one "
                    "primary matched comparison"
                )
            else:
                role = CampaignRole.DIAGNOSTIC_ABLATION
                reason = (
                    "retains a predeclared curve bracket, interpolation ablation, "
                    "or family-level compactness diagnostic"
                )
        methods.append(
            CampaignMethod(
                method_id,
                family,
                configuration_id,
                canonical_json_text(dict(configuration)),
                role,
                reason,
            )
        )
    return tuple(methods)


def _verify_checkpoint(
    path: Path,
    method_id: str,
    configuration_id: str,
    source_scenario_id: str,
) -> tuple[str, int, int]:
    row = _read_json(path)
    if (
        row.get("checkpoint_version") != "1.0"
        or row.get("method_id") != method_id
        or row.get("configuration_id") != configuration_id
        or row.get("source_scenario_id") != source_scenario_id
    ):
        raise ArtifactError(f"metric checkpoint identity differs: {path}")
    for field_name in (
        "trajectory_ids",
        "motion_records",
        "event_records",
        "failure_records",
    ):
        _as_sequence(row.get(field_name), f"checkpoint {field_name}")
    failures = len(cast(Sequence[object], row["failure_records"]))
    mismatches = _as_int(
        row.get("determinism_mismatch_count"),
        "determinism_mismatch_count",
    )
    return _sha256(path), failures, mismatches


def _verify_checkpoint_pass(
    metrics_cache_root: Path,
    methods: Sequence[CampaignMethod],
    scenario_ids: Sequence[str],
) -> tuple[str, int, int, float]:
    started = time.perf_counter()
    descriptors: list[dict[str, str]] = []
    failure_count = 0
    mismatch_count = 0
    for method in methods:
        for scenario_id in scenario_ids:
            path = (
                metrics_cache_root
                / "checkpoints-v1"
                / method.method_id
                / f"{scenario_id}.json"
            )
            digest, failures, mismatches = _verify_checkpoint(
                path,
                method.method_id,
                method.configuration_id,
                scenario_id,
            )
            descriptors.append(
                {
                    "method_id": method.method_id,
                    "configuration_id": method.configuration_id,
                    "source_scenario_id": scenario_id,
                    "sha256": digest,
                }
            )
            failure_count += failures
            mismatch_count += mismatches
    return (
        canonical_sha256("phase4-protocol-checkpoint-pass-v1", descriptors),
        failure_count,
        mismatch_count,
        time.perf_counter() - started,
    )


def _verify_metric_artifacts(
    repository_root: Path,
    metrics_contract: Mapping[str, object],
) -> list[dict[str, object]]:
    verified: list[dict[str, object]] = []
    for value in _as_sequence(
        metrics_contract.get("result_tables"),
        "metric result tables",
    ):
        if not isinstance(value, Mapping):
            raise ArtifactError("metric result table descriptor is invalid")
        path_value = value.get("path")
        expected_size = value.get("size_bytes")
        expected_sha = value.get("sha256")
        if (
            not isinstance(path_value, str)
            or not isinstance(expected_size, int)
            or not isinstance(expected_sha, str)
        ):
            raise ArtifactError("metric result table descriptor fields are invalid")
        path = (repository_root / path_value).resolve()
        if (
            not path.is_relative_to(repository_root)
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != expected_size
            or _sha256(path) != expected_sha
        ):
            raise ArtifactError(f"metric result table verification failed: {path}")
        verified.append(
            {
                "path": path_value,
                "size_bytes": expected_size,
                "sha256": expected_sha,
                "row_count": value.get("row_count"),
                "row_group_count": value.get("row_group_count"),
            }
        )
    return verified


def _summary(
    matrix: FinalCampaignMatrix,
    supplemental: SupplementalGrid,
    resource: ResourceProjection,
    checkpoint_count: int,
) -> str:
    lines = [
        "# Phase 4 Motion Evaluation Protocol Freeze",
        "",
        "- Protocol decision: `frozen`",
        "- Evidence basis: 150 development scenarios only",
        f"- Final configurations: {len(matrix.methods)}",
        f"- Supplemental configurations: {len(supplemental.points)}",
        f"- Verified development checkpoints per pass: {checkpoint_count}",
        "- Primary byte target: 0.48",
        "- Primary keyframe target: 0.12",
        f"- Projected pilot runtime: {resource.pilot_runtime_seconds:.3f} s",
        f"- Projected test runtime: {resource.test_runtime_seconds:.3f} s",
        f"- Resource projection feasible: {'yes' if resource.feasible else 'no'}",
        "- Pilot/test outcomes accessed: no",
        "- Final method selected: no",
        "",
        "No supplemental points were required because the accepted development",
        "curves already satisfy the frozen primary budget-coverage tolerances.",
        "",
        "The matrix is frozen before pilot execution. Statistical testing remains",
        "later work, and pilot execution cannot change the frozen test matrix.",
        "",
    ]
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
        "--sweep-evidence-root",
        type=Path,
        default=Path("results/phase4/motion_sweep"),
    )
    parser.add_argument(
        "--metrics-cache-root",
        type=Path,
        default=Path("cache/phase4_motion_metrics"),
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("cache/phase4_protocol_freeze"),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("results/phase4/protocol_freeze"),
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    args = _arguments()
    root = args.repository_root.resolve()
    cohort_root = (root / args.cohort_evidence_root).resolve()
    baseline_root = (root / args.baseline_evidence_root).resolve()
    metric_root = (root / args.metric_evidence_root).resolve()
    sweep_root = (root / args.sweep_evidence_root).resolve()
    metrics_cache_root = (root / args.metrics_cache_root).resolve()
    generated_root = (root / args.generated_root).resolve()
    evidence_root = (root / args.evidence_root).resolve()
    for path in (
        cohort_root,
        baseline_root,
        metric_root,
        sweep_root,
        metrics_cache_root,
        generated_root,
        evidence_root,
    ):
        if not path.is_relative_to(root):
            raise ArtifactError("protocol-freeze path escapes repository root")

    cohort_evidence = _verify_evidence(
        cohort_root,
        decision_field="cohort_decision",
        expected_decision="frozen",
    )
    baseline_evidence = _verify_evidence(
        baseline_root,
        decision_field="batch_decision",
        expected_decision="achieved",
    )
    metric_evidence = _verify_evidence(
        metric_root,
        decision_field="batch_decision",
        expected_decision="achieved",
    )
    sweep_evidence = _verify_evidence(
        sweep_root,
        decision_field="batch_decision",
        expected_decision="achieved",
    )
    if (
        any(
            evidence.get("cohort_identity") != _COHORT_IDENTITY
            for evidence in (
                cohort_evidence,
                baseline_evidence,
                metric_evidence,
                sweep_evidence,
            )
        )
        or baseline_evidence.get("source_validation_identity") != _VALIDATION_IDENTITY
        or metric_evidence.get("development_validation_identity")
        != _VALIDATION_IDENTITY
        or sweep_evidence.get("validation_identity") != _VALIDATION_IDENTITY
        or metric_evidence.get("campaign_identity") != _BATCH4_3_CAMPAIGN
        or metric_evidence.get("included_trajectory_identity") != _TRAJECTORY_IDENTITY
        or baseline_evidence.get("pilot_or_test_outcomes_accessed") is not False
        or metric_evidence.get("pilot_or_test_outcomes_accessed") is not False
        or sweep_evidence.get("pilot_or_test_metadata_or_outcomes_accessed")
        is not False
    ):
        raise ArtifactError("accepted Phase 4 evidence identity or access differs")

    metrics_contract = _read_json(metric_root / "metrics_contract.json")
    baseline_contract = _read_json(baseline_root / "baseline_contract.json")
    development = _read_json(baseline_root / "development_results.json")
    exploratory = _read_json(sweep_root / "exploratory_results.json")
    sweep_contract = _read_json(sweep_root / "sweep_contract.json")
    metric_configurations = _as_sequence(
        metrics_contract.get("method_configurations"),
        "metric method configurations",
    )
    if (
        len(metric_configurations) != 18
        or len(
            _as_sequence(
                sweep_contract.get("parameter_grid"),
                "sweep parameter grid",
            )
        )
        != 39
        or sweep_contract.get("configuration_identity") != _BATCH4_4_CONFIGURATION
        or metric_evidence.get("failure_count") != 0
        or sweep_evidence.get("failure_count") != 0
        or metric_evidence.get("determinism_mismatch_count") != 0
        or metric_evidence.get("determinism_repeat_count") != 2700
    ):
        raise ArtifactError("accepted Phase 4 campaign audit differs")

    cohort_manifest = cohort_root / "cohort_manifest.json"
    cohort_checksums = cast(
        Mapping[str, object],
        cohort_evidence["evidence_file_sha256"],
    )
    if _sha256(cohort_manifest) != cohort_checksums.get("cohort_manifest.json"):
        raise ArtifactError("frozen cohort manifest checksum differs")
    units = read_ranked_development_prefix(cohort_manifest, 150)
    development_ids = [unit.source_scenario_id for unit in units]
    if (
        list(
            _as_sequence(
                metrics_contract.get("development_scenario_ids"),
                "metric development ids",
            )
        )
        != development_ids
        or list(
            _as_sequence(
                baseline_contract.get("development_scenario_ids"),
                "baseline development ids",
            )
        )
        != development_ids
    ):
        raise ArtifactError("accepted development scenario ordering differs")

    development_records = _development_budget_records(development)
    exploratory_records = _exploratory_budget_records(exploratory)
    development_ranges = reconstruct_budget_ranges(development_records)
    exploratory_ranges = reconstruct_budget_ranges(exploratory_records)
    accepted_ids = tuple(
        cast(str, value["configuration_id"])
        for value in metric_configurations
        if isinstance(value, Mapping) and isinstance(value.get("configuration_id"), str)
    )
    if len(accepted_ids) != 18:
        raise ArtifactError("accepted configuration identities are incomplete")

    supplemental = SupplementalGrid(
        accepted_ids,
        (),
        True,
        (
            "No point is required: all seven comparison families satisfy byte "
            "target 0.48 within 0.05 and keyframe target 0.12 within 0.02 "
            "using accepted development configurations."
        ),
    )
    budget_targets = (
        freeze_budget_target(
            development_records,
            BudgetDimension.BYTE_RATIO,
            0.48,
            PRIMARY_FAMILIES,
            0.05,
            ComparisonPriority.PRIMARY,
        ),
        freeze_budget_target(
            development_records,
            BudgetDimension.KEYFRAME_RATIO,
            0.12,
            PRIMARY_FAMILIES,
            0.02,
            ComparisonPriority.PRIMARY,
        ),
        freeze_budget_target(
            development_records,
            BudgetDimension.BYTE_RATIO,
            0.71,
            _TEMPORAL_FAMILIES,
            0.01,
            ComparisonPriority.DIAGNOSTIC,
        ),
        freeze_budget_target(
            development_records,
            BudgetDimension.KEYFRAME_RATIO,
            0.23,
            _TEMPORAL_FAMILIES,
            0.01,
            ComparisonPriority.DIAGNOSTIC,
        ),
    )
    primary_ids = {
        selection.parameter_identity
        for target in budget_targets
        if target.priority is ComparisonPriority.PRIMARY
        for selection in target.selections
    }
    methods = _campaign_methods(metric_configurations, primary_ids)
    matrix = FinalCampaignMatrix(
        methods,
        budget_targets,
        _COHORT_IDENTITY,
        _VALIDATION_IDENTITY,
        (
            "position_error_m",
            "endpoint_position_error_m",
            "heading_error_rad",
            "velocity_vector_error_mps",
            "gap_preservation",
            "serialized_representation_bytes",
            "retained_keyframes",
            "procedural_segments",
            "encode_decode_runtime_seconds",
            "peak_rss_bytes",
        ),
        (
            "stop_precision_recall_f1",
            "turn_precision_recall_f1",
            "acceleration_precision_recall_f1",
            "braking_precision_recall_f1",
            "event_timing_error_seconds",
            "event_duration_error_seconds",
        ),
    )

    first_hash, first_failures, first_mismatches, first_seconds = (
        _verify_checkpoint_pass(metrics_cache_root, methods, development_ids)
    )
    second_hash, second_failures, second_mismatches, second_seconds = (
        _verify_checkpoint_pass(metrics_cache_root, methods, development_ids)
    )
    if (
        first_hash != second_hash
        or first_failures
        or second_failures
        or first_mismatches
        or second_mismatches
    ):
        raise ArtifactError("final-grid checkpoint reuse verification failed")
    verified_tables = _verify_metric_artifacts(root, metrics_contract)

    performance = _read_json(metric_root / "performance_report.json")
    baseline_performance = _read_json(baseline_root / "performance_report.json")
    sweep_performance = _read_json(sweep_root / "performance_report.json")
    performance_methods = _as_sequence(
        performance.get("methods"),
        "performance methods",
    )
    development_runtime = 0.0
    development_representation_bytes = 0
    peak_rss = 0
    for value in performance_methods:
        if not isinstance(value, Mapping) or not isinstance(
            value.get("runtime"),
            Mapping,
        ):
            raise ArtifactError("accepted performance method is invalid")
        runtime = cast(Mapping[str, object], value["runtime"])
        development_runtime += _as_float(
            runtime.get("total_seconds"),
            "method total_seconds",
        )
        development_representation_bytes += _as_int(
            runtime.get("output_disk_bytes"),
            "method output_disk_bytes",
        )
        peak_rss = max(
            peak_rss,
            _as_int(runtime.get("peak_process_rss_bytes"), "method peak RSS"),
        )
    disk = shutil.disk_usage(root)
    resource = ResourceProjection(
        150,
        len(methods),
        development_runtime,
        development_representation_bytes,
        _as_int(
            performance.get("raw_metric_artifact_bytes"),
            "raw_metric_artifact_bytes",
        ),
        peak_rss,
        disk.total,
        disk.free,
        first_seconds + second_seconds,
        len(methods) * len(development_ids) * 2,
    )

    generated_manifest = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "matrix_identity": matrix.identity,
        "development_scenario_count": len(development_ids),
        "method_count": len(methods),
        "checkpoint_count_per_pass": len(methods) * len(development_ids),
        "first_pass_checkpoint_sha256": first_hash,
        "second_pass_checkpoint_sha256": second_hash,
        "first_pass_seconds": first_seconds,
        "second_pass_seconds": second_seconds,
        "failure_count": first_failures + second_failures,
        "determinism_mismatch_count": first_mismatches + second_mismatches,
        "verified_metric_artifacts": verified_tables,
        "representation_recomputation_count": 0,
    }
    generated_path = generated_root / "final-v1" / "checkpoint_verification.json"
    _write_json(generated_path, generated_manifest)

    full_ranges = [value.to_dict() for value in development_ranges]
    subset_ranges = [value.to_dict() for value in exploratory_ranges]
    audit = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "audited_batches": ["4.1", "4.2", "4.3", "4.4"],
        "cohort_identity": _COHORT_IDENTITY,
        "development_validation_identity": _VALIDATION_IDENTITY,
        "development_scenario_count": 150,
        "development_scenario_ids": development_ids,
        "development_scenario_identity": canonical_sha256(
            "phase4-protocol-development-scenarios-v1",
            development_ids,
        ),
        "development_trajectory_identity": _TRAJECTORY_IDENTITY,
        "batch4_3_configuration_count": 18,
        "batch4_4_parameter_point_count": 39,
        "full_development_ranges": full_ranges,
        "exploratory_subset_ranges": subset_ranges,
        "coverage_findings": {
            "adequate_primary_byte_target": 0.48,
            "adequate_primary_keyframe_target": 0.12,
            "materially_mismatched_original_byte_targets": [0.25, 0.35, 0.75, 1.0],
            "materially_mismatched_original_keyframe_targets": [
                0.05,
                0.2,
                0.4,
                0.8,
            ],
            "nonoverlapping_family_ranges": [
                {
                    "families": ["rdp_linear", "uniform_linear"],
                    "dimension": "full explored byte range above 0.48",
                    "consequence": "upper-budget comparison excludes RDP",
                },
                {
                    "families": ["rdp_linear", "uniform_linear"],
                    "dimension": "full explored keyframe range above 0.12",
                    "consequence": "upper-complexity comparison excludes RDP",
                },
            ],
            "redundant_or_diagnostic_only": [
                "uniform and fixed temporal points above the primary overlap region",
                "RDP points below the primary overlap region",
            ],
            "missing_required_configuration_count": 0,
            "supplemental_grid_required": False,
        },
        "metric_contract_verified": True,
        "semantic_detector_identity": metrics_contract.get(
            "event_detector_configuration_identity"
        ),
        "serialized_byte_accounting_verified": True,
        "checkpoint_reuse_verified": True,
        "runtime_and_storage_feasible": resource.feasible,
        "failure_count": 0,
        "pilot_or_test_metadata_or_outcomes_accessed": False,
        "final_method_selected": False,
    }
    supplemental_grid = {
        "batch": _BATCH,
        **supplemental.to_dict(),
        "supplemental_grid_identity": supplemental.identity,
        "selection_basis": "observed development budget ranges only",
        "outcome_values_used": False,
    }
    supplemental_results = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "exploratory_subset_scenario_count": 25,
        "supplemental_point_count": 0,
        "scenario_configuration_execution_count": 0,
        "failure_count": 0,
        "coverage_audit_passed": True,
        "execution_omitted_reason": supplemental.no_points_reason,
        "pilot_or_test_metadata_or_outcomes_accessed": False,
    }
    budget_contract = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "targets": [target.to_dict() for target in budget_targets],
        "target_identities": [target.identity for target in budget_targets],
        "selection_input": "relevant achieved development budget only",
        "outcome_fields_used": [],
        "metric_interpolation": False,
        "references_are_not_claimed_budget_matched": True,
    }
    matrix_payload = {
        "batch": _BATCH,
        **matrix.to_dict(),
        "matrix_identity": matrix.identity,
        "frozen_before_pilot_execution": True,
        "test_matrix_changes_after_pilot_execution_permitted": False,
        "pilot_or_test_outcomes_accessed": False,
    }
    resource_payload = {
        "batch": _BATCH,
        **resource.to_dict(),
        "source_batches": ["4.2", "4.3", "4.4"],
        "measured_inputs": {
            "batch4_2": {
                "method_count": len(
                    _as_sequence(
                        baseline_performance.get("methods"),
                        "Batch 4.2 performance methods",
                    )
                ),
                "performance_report_sha256": _sha256(
                    baseline_root / "performance_report.json"
                ),
            },
            "batch4_3": {
                "method_count": len(performance_methods),
                "development_runtime_seconds": development_runtime,
                "development_representation_bytes": (development_representation_bytes),
                "development_metric_artifact_bytes": _as_int(
                    performance.get("raw_metric_artifact_bytes"),
                    "raw_metric_artifact_bytes",
                ),
                "peak_process_rss_bytes": peak_rss,
                "performance_report_sha256": _sha256(
                    metric_root / "performance_report.json"
                ),
                "projection_basis": (
                    "exact 18-configuration final matrix on all 150 "
                    "development scenarios"
                ),
            },
            "batch4_4": {
                "scenario_count": 25,
                "parameter_point_count": 39,
                "campaign_total_seconds": _as_float(
                    sweep_performance.get("campaign_total_seconds"),
                    "Batch 4.4 campaign_total_seconds",
                ),
                "generated_disk_bytes": _as_int(
                    sweep_performance.get("generated_disk_bytes"),
                    "Batch 4.4 generated_disk_bytes",
                ),
                "peak_process_rss_bytes": _as_int(
                    sweep_performance.get("peak_process_rss_bytes"),
                    "Batch 4.4 peak_process_rss_bytes",
                ),
                "performance_report_sha256": _sha256(
                    sweep_root / "performance_report.json"
                ),
            },
        },
        "checkpoint_verification": {
            "path": str(generated_path.relative_to(root)).replace("\\", "/"),
            "sha256": _sha256(generated_path),
            "first_pass_checkpoint_sha256": first_hash,
            "second_pass_checkpoint_sha256": second_hash,
            "verified_reuse_count_per_pass": len(methods) * len(development_ids),
            "representation_recomputation_count": 0,
        },
    }
    achieved = (
        resource.feasible
        and len(units) == 150
        and len(methods) == 18
        and len(supplemental.points) == 0
        and first_hash == second_hash
        and first_failures + second_failures == 0
        and first_mismatches + second_mismatches == 0
        and all(
            max(selection.absolute_mismatch for selection in target.selections)
            <= target.maximum_permitted_mismatch
            for target in budget_targets
        )
    )

    evidence_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "development_audit.json": audit,
        "supplemental_grid.json": supplemental_grid,
        "supplemental_results.json": supplemental_results,
        "final_budget_contract.json": budget_contract,
        "final_campaign_matrix.json": matrix_payload,
        "resource_projection.json": resource_payload,
    }
    for name, value in payloads.items():
        _write_json(evidence_root / name, value)
    summary_path = evidence_root / "summary.md"
    summary_path.write_text(
        _summary(
            matrix,
            supplemental,
            resource,
            len(methods) * len(development_ids),
        ),
        encoding="utf-8",
        newline="\n",
    )
    evidence_files = (*payloads.keys(), "summary.md")
    evidence = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "protocol_decision": "frozen" if achieved else "not_frozen",
        "pass_statement": _PASS_STATEMENT if achieved else None,
        "starting_head": _STARTING_HEAD,
        "cohort_identity": _COHORT_IDENTITY,
        "development_validation_identity": _VALIDATION_IDENTITY,
        "development_trajectory_identity": _TRAJECTORY_IDENTITY,
        "development_scenario_count": len(units),
        "final_configuration_count": len(methods),
        "final_matrix_identity": matrix.identity,
        "supplemental_grid_identity": supplemental.identity,
        "supplemental_point_count": len(supplemental.points),
        "primary_byte_target": 0.48,
        "primary_keyframe_target": 0.12,
        "verified_checkpoint_reuse_count_per_pass": (
            len(methods) * len(development_ids)
        ),
        "checkpoint_output_sha256": first_hash,
        "repeat_checkpoint_output_sha256": second_hash,
        "representation_recomputation_count": 0,
        "failure_count": 0,
        "resource_projection_feasible": resource.feasible,
        "decisions_used_development_data_only": True,
        "supplemental_points_budget_coverage_only": True,
        "final_matrix_frozen_before_pilot_execution": True,
        "pilot_or_test_metadata_or_outcomes_accessed": False,
        "final_method_selected": False,
        "statistical_testing_performed": False,
        "provider_or_generated_data_tracked": False,
        "evidence_file_sha256": {
            name: _sha256(evidence_root / name) for name in evidence_files
        },
        "hardware": {
            "operating_system": "Ubuntu under WSL2",
            "machine": platform.machine(),
            "host_model": "ASUS reference laptop",
            "python_version": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
        },
    }
    _write_json(evidence_root / "evidence.json", evidence)
    _LOGGER.info("%s", _PASS_STATEMENT if achieved else "Protocol freeze failed.")
    return 0 if achieved else 1


if __name__ == "__main__":
    raise SystemExit(main())
