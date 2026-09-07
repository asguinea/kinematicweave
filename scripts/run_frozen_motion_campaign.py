"""Execute the frozen Phase 4 pilot and test motion campaign."""

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
from kinematicweave.data.materialization import verify_materialization_cache_entry
from kinematicweave.data.motion_metric_artifacts import (
    MotionMetricArtifact,
    MotionMetricTable,
    verify_metric_parquet,
    write_metric_parquet,
)
from kinematicweave.data.motion_sweep_artifacts import (
    MotionSweepArtifact,
    MotionSweepTable,
    verify_sweep_parquet,
    write_sweep_parquet,
)
from kinematicweave.errors import ArtifactError, ResourceLimitError
from kinematicweave.experiments.frozen_campaign import (
    CONFIGURATION_COUNT,
    FROZEN_COHORT_IDENTITY,
    FROZEN_MATRIX_IDENTITY,
    PILOT_SCENARIO_COUNT,
    TEST_SCENARIO_COUNT,
    PilotCompletion,
    checkpoint_output_identity,
    file_snapshot_identity,
    pilot_scenario_identity,
    read_pilot_units,
    read_test_units,
    test_scenario_identity,
    write_pilot_completion,
)
from kinematicweave.experiments.motion_cohort import MotionCohortUnit
from kinematicweave.experiments.motion_sweep import (
    BudgetDimension,
    ConfigurationBudgetRecord,
    SweepConfiguration,
    SweepParameterPoint,
    frozen_parameter_grid,
    select_matched_budgets,
)
from kinematicweave.logging import configure_logging, get_logger
import run_motion_sweep as sweep  # type: ignore[import-not-found]

_BATCH = "4.6"
_STARTING_HEAD = "f90ebae21c7073460e7a5e2f880c8ef39ef8a791"
_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)
_METRIC_IDENTITY = "03440d32241c0e55d490cf617cd1b2d9ed1a546865372b8dc155f06c696e3277"
_PASS_STATEMENT = (
    "Phase 4 frozen motion campaign completed on all 50 pilot and 300 test "
    "AV2 scenarios."
)
_ESTIMATED_REQUIRED_BYTES = 2_000_000_000
_HARD_RSS_BYTES = 26 * 1024**3
_LOGGER = get_logger("scripts.run_frozen_motion_campaign")


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
        raise ArtifactError(f"JSON root must be a mapping: {path}")
    return cast(Mapping[str, object], value)


def _write_json(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise ArtifactError(f"immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(canonical_json_text(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


def _verify_evidence(
    root: Path,
    decision_field: str,
    decision: str,
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
    repository_root: Path,
    cohort_root: Path,
    matrix_root: Path,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    roots = (
        (cohort_root, "cohort_decision", "frozen"),
        (
            repository_root / "results/phase4/motion_baselines",
            "batch_decision",
            "achieved",
        ),
        (
            repository_root / "results/phase4/motion_metrics",
            "batch_decision",
            "achieved",
        ),
        (
            repository_root / "results/phase4/motion_sweep",
            "batch_decision",
            "achieved",
        ),
        (matrix_root, "protocol_decision", "frozen"),
    )
    evidence = [
        _verify_evidence(root, field, decision) for root, field, decision in roots
    ]
    if any(item.get("cohort_identity") != FROZEN_COHORT_IDENTITY for item in evidence):
        raise ArtifactError("accepted Phase 4 cohort identity differs")
    if evidence[2].get("campaign_identity") != _METRIC_IDENTITY:
        raise ArtifactError("accepted Batch 4.3 metric identity differs")
    if evidence[4].get("final_matrix_identity") != FROZEN_MATRIX_IDENTITY:
        raise ArtifactError("accepted Batch 4.5 matrix identity differs")
    if evidence[4].get("final_matrix_frozen_before_pilot_execution") is not True:
        raise ArtifactError("campaign matrix was not frozen before pilot")
    manifest = cohort_root / "cohort_manifest.json"
    cohort_hashes = evidence[0].get("evidence_file_sha256")
    if not isinstance(cohort_hashes, Mapping) or _sha256(manifest) != cohort_hashes.get(
        "cohort_manifest.json"
    ):
        raise ArtifactError("frozen cohort manifest checksum differs")
    matrix = _read_json(matrix_root / "final_campaign_matrix.json")
    if (
        matrix.get("matrix_identity") != FROZEN_MATRIX_IDENTITY
        or matrix.get("cohort_identity") != FROZEN_COHORT_IDENTITY
        or matrix.get("development_validation_identity") != _VALIDATION_IDENTITY
    ):
        raise ArtifactError("frozen campaign matrix content differs")
    return evidence[0], matrix


def _normalized_method(value: str) -> str:
    return value.replace(
        "position_velocity_bounded_hybrid",
        "position_velocity_hybrid",
    ).replace("-velocity-1p0", "-velocity-1")


def _normalized_configuration(value: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(value)
    method = normalized.get("method")
    if method == "exact_adjacent_sample":
        normalized["method"] = "exact_adjacent"
    elif method == "position_velocity_bounded_hybrid":
        normalized["method"] = "position_velocity_hybrid"
    return normalized


def _configuration(
    matrix: Mapping[str, object],
    count: int,
) -> tuple[SweepConfiguration, dict[str, tuple[str, str]]]:
    methods = matrix.get("methods")
    if isinstance(methods, (str, bytes)) or not isinstance(methods, Sequence):
        raise ArtifactError("frozen matrix methods are missing")
    by_identity = {
        point.encoder_configuration_identity: point for point in frozen_parameter_grid()
    }
    points: list[SweepParameterPoint] = []
    reporting: dict[str, tuple[str, str]] = {}
    for method in methods:
        if not isinstance(method, Mapping):
            raise ArtifactError("frozen matrix method is invalid")
        identity = method.get("configuration_id")
        if not isinstance(identity, str) or identity not in by_identity:
            raise ArtifactError("frozen matrix configuration is not implemented")
        point = by_identity[identity]
        method_id = method.get("method_id")
        configuration_value = method.get("configuration")
        if (
            not isinstance(method_id, str)
            or not isinstance(configuration_value, Mapping)
            or _normalized_method(point.method_id) != _normalized_method(method_id)
            or point.family != method.get("family")
            or _normalized_configuration(point.configuration)
            != _normalized_configuration(configuration_value)
        ):
            raise ArtifactError(
                f"frozen matrix implementation mapping differs: {method_id}"
            )
        points.append(point)
        reporting[point.parameter_identity] = (method_id, identity)
    if (
        len(points) != CONFIGURATION_COUNT
        or len({point.parameter_identity for point in points}) != CONFIGURATION_COUNT
    ):
        raise ArtifactError("frozen matrix must contain exactly 18 configurations")
    return (
        SweepConfiguration(
            tuple(points),
            subset_size=count,
            byte_ratio_targets=(0.48, 0.71),
            keyframe_ratio_targets=(0.12, 0.23),
        ),
        reporting,
    )


def _cache_entry(
    repository_root: Path, cache_root: Path, unit: MotionCohortUnit
) -> Path:
    path = cache_root / "entries" / unit.materialization_cache_key[:2]
    path /= unit.materialization_cache_key
    verify_materialization_cache_entry(
        repository_root,
        path.relative_to(repository_root),
    )
    return path


def _input_snapshot(
    repository_root: Path,
    cache_root: Path,
    units: Sequence[MotionCohortUnit],
    role: str,
) -> tuple[str, int]:
    paths = [
        path
        for unit in units
        for path in _cache_entry(repository_root, cache_root, unit).rglob("*")
        if path.is_file()
    ]
    return file_snapshot_identity(paths, repository_root, role), sum(
        path.stat().st_size for path in paths
    )


def _membership(
    repository_root: Path,
    cache_root: Path,
    units: Sequence[MotionCohortUnit],
    role: str,
) -> tuple[str, int, int, list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    trajectory_count = 0
    source_sample_count = 0
    for unit in units:
        trajectories = sweep._bundle(repository_root, cache_root, unit)[3]
        trajectory_ids = [trajectory.trajectory_id for trajectory in trajectories]
        sample_count = sum(len(trajectory.samples) for trajectory in trajectories)
        trajectory_count += len(trajectory_ids)
        source_sample_count += sample_count
        records.append(
            {
                "source_scenario_id": unit.source_scenario_id,
                "selection_rank": unit.selection_rank,
                "trajectory_ids": trajectory_ids,
                "source_sample_count": sample_count,
            }
        )
    identity = canonical_sha256(
        f"phase4-frozen-{role}-trajectory-membership-v1",
        records,
    )
    return identity, trajectory_count, source_sample_count, records


def _finite(value: object) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(_finite(item) for item in value.values())
    if isinstance(value, Sequence):
        return all(_finite(item) for item in value)
    return False


def _statistics_maximum(row: Mapping[str, object], field: str) -> float | None:
    statistics = row.get(field)
    if not isinstance(statistics, Mapping):
        raise ArtifactError(f"motion metric {field} is unavailable")
    value = statistics.get("maximum")
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ArtifactError(f"motion metric {field}.maximum is unavailable")
    return float(value)


def _scientific_invariants(
    motion: Sequence[Mapping[str, object]],
    events: Sequence[Mapping[str, object]],
    points: Sequence[SweepParameterPoint],
    reporting_identities: Mapping[str, tuple[str, str]],
) -> dict[str, object]:
    method_by_identity = {
        reporting_identities[point.parameter_identity][1]: point for point in points
    }
    raw_identity = next(
        reporting_identities[point.parameter_identity][1]
        for point in points
        if point.method_id == "raw_samples"
    )
    exact_identity = next(
        reporting_identities[point.parameter_identity][1]
        for point in points
        if point.method_id == "exact_adjacent_sample"
    )
    raw_motion = [row for row in motion if row["configuration_id"] == raw_identity]
    exact_motion = [row for row in motion if row["configuration_id"] == exact_identity]
    raw_events = [row for row in events if row["configuration_id"] == raw_identity]
    statistics_fields = (
        "position_statistics",
        "heading_statistics",
        "velocity_statistics",
    )
    endpoint_fields = (
        "endpoint_position_maximum_m",
        "endpoint_heading_maximum_rad",
        "endpoint_velocity_maximum_mps",
    )
    raw_zero = all(
        _statistics_maximum(row, field) in (0.0, None)
        for row in raw_motion
        for field in statistics_fields
    ) and all(
        row.get(field) in (0, 0.0, None)
        for row in raw_motion
        for field in endpoint_fields
    )
    exact_zero = all(
        _statistics_maximum(row, field) in (0.0, None)
        for row in exact_motion
        for field in statistics_fields
    ) and all(
        row.get(field) in (0, 0.0, None)
        for row in exact_motion
        for field in endpoint_fields
    )
    raw_semantic = all(
        row.get("matched_event_count") == row.get("source_event_count")
        and row.get("unmatched_source_count") == 0
        and row.get("unmatched_replay_count") == 0
        for row in raw_events
    )
    gap_preservation = all(row.get("gap_preservation_passed") is True for row in motion)
    bounded_violations: list[dict[str, object]] = []
    for row in motion:
        point = method_by_identity[cast(str, row["configuration_id"])]
        position_limit = point.configuration.get("maximum_position_error_m")
        position_maximum = _statistics_maximum(row, "position_statistics")
        if (
            isinstance(position_limit, (int, float))
            and position_maximum is not None
            and position_maximum > float(position_limit) + 1e-9
        ):
            bounded_violations.append(
                {
                    "method_id": point.method_id,
                    "trajectory_id": row["trajectory_id"],
                    "constraint": "position",
                }
            )
        velocity_limit = point.configuration.get("maximum_velocity_error_mps")
        velocity_maximum = _statistics_maximum(row, "velocity_statistics")
        if (
            isinstance(velocity_limit, (int, float))
            and velocity_maximum is not None
            and velocity_maximum > float(velocity_limit) + 1e-9
        ):
            bounded_violations.append(
                {
                    "method_id": point.method_id,
                    "trajectory_id": row["trajectory_id"],
                    "constraint": "velocity",
                }
            )
    return {
        "raw_zero_source_timestamp_error": raw_zero,
        "raw_semantic_preservation_perfect": raw_semantic,
        "exact_adjacent_zero_source_timestamp_error": exact_zero,
        "all_gap_contracts_preserved": gap_preservation,
        "all_replay_values_and_metrics_finite": _finite(motion) and _finite(events),
        "bounded_constraint_violation_count": len(bounded_violations),
        "bounded_constraint_violations": bounded_violations,
    }


def _budget_selections(
    matrix: Mapping[str, object],
    configuration_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    targets = matrix.get("budget_targets")
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
        raise ArtifactError("frozen budget targets are missing")
    records = [
        ConfigurationBudgetRecord(
            cast(str, row["family"]),
            cast(str, row["method_id"]),
            cast(str, row["parameter_identity"]),
            cast(float, row["byte_ratio"]),
            cast(float, row["keyframe_ratio"]),
        )
        for row in configuration_rows
    ]
    results: list[dict[str, object]] = []
    for target in targets:
        if not isinstance(target, Mapping):
            raise ArtifactError("frozen budget target is invalid")
        families = target.get("included_families")
        if isinstance(families, (str, bytes)) or not isinstance(families, Sequence):
            raise ArtifactError("frozen budget families are missing")
        included_families = {cast(str, family) for family in families}
        candidates = tuple(
            record for record in records if record.family in included_families
        )
        dimension = BudgetDimension(cast(str, target["dimension"]))
        selection = select_matched_budgets(
            candidates,
            (cast(float, target["target"]),),
            dimension,
        )
        maximum = cast(float, target["maximum_permitted_mismatch"])
        if any(item.absolute_mismatch > maximum for item in selection):
            raise ArtifactError("frozen matched-budget tolerance was not achieved")
        for item in selection:
            results.append(
                {
                    **item.to_dict(),
                    "priority": target["priority"],
                    "maximum_permitted_mismatch": maximum,
                    "metric_interpolation": False,
                    "selection_input": "relevant_achieved_budget_only",
                }
            )
    return results


def _write_raw_results(
    role_root: Path,
    scenario_rows: Sequence[Mapping[str, object]],
    configuration_rows: Sequence[Mapping[str, object]],
    motion: Sequence[Mapping[str, object]],
    events: Sequence[Mapping[str, object]],
    failures: Sequence[Mapping[str, object]],
    selections: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    paths = (
        (
            role_root / "trajectory_motion_metrics.parquet",
            MotionMetricTable.TRAJECTORY_MOTION,
            verify_metric_parquet,
        ),
        (
            role_root / "trajectory_event_metrics.parquet",
            MotionMetricTable.TRAJECTORY_EVENT,
            verify_metric_parquet,
        ),
        (
            role_root / "scenario_method_metrics.parquet",
            MotionSweepTable.SCENARIO_CONFIGURATION,
            verify_sweep_parquet,
        ),
        (
            role_root / "configuration_results.parquet",
            MotionSweepTable.CONFIGURATION_AGGREGATE,
            verify_sweep_parquet,
        ),
        (
            role_root / "evaluation_failures.parquet",
            MotionSweepTable.FAILURE,
            verify_sweep_parquet,
        ),
        (
            role_root / "matched_budget_selections.parquet",
            MotionSweepTable.MATCHED_BUDGET,
            verify_sweep_parquet,
        ),
    )
    if role_root.exists() or role_root.is_symlink():
        if role_root.is_symlink() or not role_root.is_dir():
            raise ArtifactError("immutable role result root is invalid")
        existing_manifest = _read_json(role_root / "campaign_manifest.json")
        artifacts_value = existing_manifest.get("artifacts")
        if isinstance(artifacts_value, (str, bytes)) or not isinstance(
            artifacts_value, Sequence
        ):
            raise ArtifactError("immutable role result descriptors are missing")
        artifacts = [
            dict(cast(Mapping[str, object], value))
            for value in artifacts_value
            if isinstance(value, Mapping)
        ]
        if len(artifacts) != len(paths):
            raise ArtifactError("immutable role result descriptor count differs")
        verified: list[dict[str, object]] = []
        for descriptor, (path, table_name, _verifier) in zip(
            artifacts,
            paths,
            strict=True,
        ):
            if descriptor.get("path") != path.as_posix():
                raise ArtifactError("immutable role result path differs")
            if isinstance(table_name, MotionMetricTable):
                metric_artifact = MotionMetricArtifact(
                    table_name,
                    path,
                    cast(int, descriptor["size_bytes"]),
                    cast(str, descriptor["sha256"]),
                    cast(int, descriptor["row_count"]),
                    cast(int, descriptor["row_group_count"]),
                )
                verify_metric_parquet(metric_artifact)
                verified.append(metric_artifact.to_dict())
            else:
                sweep_artifact = MotionSweepArtifact(
                    cast(MotionSweepTable, table_name),
                    path,
                    cast(int, descriptor["size_bytes"]),
                    cast(str, descriptor["sha256"]),
                    cast(int, descriptor["row_count"]),
                    cast(int, descriptor["row_group_count"]),
                )
                verify_sweep_parquet(sweep_artifact)
                verified.append(sweep_artifact.to_dict())
        expected_manifest = {**manifest, "artifacts": artifacts}
        expected_manifest["first_pass_reuse_count"] = existing_manifest.get(
            "first_pass_reuse_count"
        )
        if artifacts != verified or dict(existing_manifest) != expected_manifest:
            raise ArtifactError("immutable role result manifest differs")
        effective = dict(existing_manifest)
        del effective["artifacts"]
        return artifacts, effective
    role_root.mkdir(parents=True, exist_ok=False)
    artifacts = [
        write_metric_parquet(
            role_root / "trajectory_motion_metrics.parquet",
            motion,
            MotionMetricTable.TRAJECTORY_MOTION,
        ).to_dict(),
        write_metric_parquet(
            role_root / "trajectory_event_metrics.parquet",
            events,
            MotionMetricTable.TRAJECTORY_EVENT,
        ).to_dict(),
        write_sweep_parquet(
            role_root / "scenario_method_metrics.parquet",
            scenario_rows,
            MotionSweepTable.SCENARIO_CONFIGURATION,
        ).to_dict(),
        write_sweep_parquet(
            role_root / "configuration_results.parquet",
            [sweep._aggregate_table_row(row) for row in configuration_rows],
            MotionSweepTable.CONFIGURATION_AGGREGATE,
        ).to_dict(),
        write_sweep_parquet(
            role_root / "evaluation_failures.parquet",
            failures,
            MotionSweepTable.FAILURE,
        ).to_dict(),
        write_sweep_parquet(
            role_root / "matched_budget_selections.parquet",
            [
                {
                    key: row[key]
                    for key in (
                        "schema_version",
                        "dimension",
                        "family",
                        "target",
                        "method_id",
                        "parameter_identity",
                        "actual_budget",
                        "relation",
                        "absolute_mismatch",
                    )
                }
                for row in selections
            ],
            MotionSweepTable.MATCHED_BUDGET,
        ).to_dict(),
    ]
    _write_json(
        role_root / "campaign_manifest.json", {**manifest, "artifacts": artifacts}
    )
    return artifacts, dict(manifest)


def _run_role(
    repository_root: Path,
    cohort_cache_root: Path,
    generated_root: Path,
    role: str,
    units: Sequence[MotionCohortUnit],
    configuration: SweepConfiguration,
    reporting_identities: Mapping[str, tuple[str, str]],
    matrix: Mapping[str, object],
    cache_before: str,
    cache_bytes: int,
    membership_identity: str,
    trajectory_count: int,
    source_sample_count: int,
    membership: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    role_root = generated_root / role
    checkpoint_root = role_root / "checkpoint_store"
    representation_root = role_root / "representations"
    started = time.perf_counter()
    first, first_reused = sweep._run_first_pass(
        repository_root,
        cohort_cache_root,
        representation_root,
        checkpoint_root,
        configuration,
        units,
        reporting_identities,
    )
    first_identity = checkpoint_output_identity(
        [checkpoint.payload_sha256 for checkpoint in first],
        role,
    )
    second, second_reused, _legacy_hash = sweep._run_reuse_pass(
        representation_root,
        checkpoint_root,
        configuration,
        units,
    )
    second_identity = checkpoint_output_identity(
        [checkpoint.payload_sha256 for checkpoint in second],
        role,
    )
    if first_identity != second_identity:
        raise ArtifactError("repeat checkpoint output identity differs")
    executions, motion, events, failures = sweep._collect(second)
    expected_units = len(units) * CONFIGURATION_COUNT
    if len(executions) != expected_units or failures:
        raise ArtifactError(f"{role} scenario/configuration accounting differs")
    scenario_rows = [sweep._scenario_table_row(execution) for execution in executions]
    configuration_rows = sweep._configuration_rows(
        configuration,
        scenario_rows,
        motion,
        events,
        reporting_identities,
    )
    selections = _budget_selections(matrix, configuration_rows)
    invariants = _scientific_invariants(
        motion,
        events,
        configuration.parameter_points,
        reporting_identities,
    )
    if (
        not all(
            value is True
            for key, value in invariants.items()
            if key
            in {
                "raw_zero_source_timestamp_error",
                "raw_semantic_preservation_perfect",
                "exact_adjacent_zero_source_timestamp_error",
                "all_gap_contracts_preserved",
                "all_replay_values_and_metrics_finite",
            }
        )
        or invariants["bounded_constraint_violation_count"] != 0
    ):
        raise ArtifactError(f"{role} scientific invariant failed")
    if len(motion) != trajectory_count * CONFIGURATION_COUNT:
        raise ArtifactError(f"{role} trajectory/configuration accounting differs")
    if len(events) != len(motion) * 6:
        raise ArtifactError(f"{role} event-type accounting differs")
    cache_after, cache_after_bytes = _input_snapshot(
        repository_root,
        cohort_cache_root,
        units,
        role,
    )
    if cache_after != cache_before or cache_after_bytes != cache_bytes:
        raise ArtifactError(f"{role} source/cache snapshot changed")
    verification_seconds = time.perf_counter() - started
    execution_seconds = sum(
        cast(float, row["total_seconds"]) for row in configuration_rows
    )
    replay_hashes = [cast(str, row["replay_hash"]) for row in motion]
    scenario_identity = (
        pilot_scenario_identity(units)
        if role == "pilot"
        else test_scenario_identity(units)
    )
    manifest = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "cohort_role": role,
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "validation_identity": _VALIDATION_IDENTITY,
        "metric_identity": _METRIC_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "scenario_identity": scenario_identity,
        "trajectory_membership_identity": membership_identity,
        "scenario_ids": [unit.source_scenario_id for unit in units],
        "membership": list(membership),
        "configuration_ids": [
            point.parameter_identity for point in configuration.parameter_points
        ],
        "scenario_count": len(units),
        "configuration_count": CONFIGURATION_COUNT,
        "scenario_configuration_count": len(executions),
        "trajectory_count": trajectory_count,
        "trajectory_configuration_count": len(motion),
        "source_sample_count": source_sample_count,
        "trajectory_event_record_count": len(events),
        "failure_count": len(failures),
        "checkpoint_output_identity": first_identity,
        "repeat_checkpoint_output_identity": second_identity,
        "first_pass_reuse_count": first_reused,
        "verification_pass_reuse_count": second_reused,
        "representation_recomputation_count_during_verification": 0,
        "replay_identity_count": len(replay_hashes),
        "replay_identity_set_size": len(set(replay_hashes)),
        "replay_identity_digest": canonical_sha256(
            f"phase4-frozen-{role}-replay-identities-v1",
            replay_hashes,
        ),
        "source_cache_snapshot_before": cache_before,
        "source_cache_snapshot_after": cache_after,
        "source_cache_bytes": cache_bytes,
        "source_cache_unchanged": True,
        "protocol_changed": False,
        "test_outcomes_used_to_alter_campaign": False,
        "invariants": invariants,
    }
    raw_artifacts, manifest = _write_raw_results(
        role_root / "final-v1",
        scenario_rows,
        configuration_rows,
        motion,
        events,
        failures,
        selections,
        manifest,
    )
    published_artifacts = [
        {
            **artifact,
            "path": Path(cast(str, artifact["path"]))
            .relative_to(repository_root)
            .as_posix(),
        }
        for artifact in raw_artifacts
    ]
    result = {
        **manifest,
        "configurations": configuration_rows,
        "matched_budget_selections": selections,
        "raw_result_artifacts": published_artifacts,
        "performance": {
            "total_seconds": execution_seconds,
            "verification_pass_seconds": verification_seconds,
            "scenarios_per_second": len(units) / execution_seconds,
            "scenario_configurations_per_second": len(executions) / execution_seconds,
            "peak_process_rss_bytes": max(
                cast(int, row["peak_process_rss_bytes"]) for row in configuration_rows
            ),
            "output_disk_bytes": sweep._tree_size(role_root),
            "worker_count": 1,
            "cpu_only": True,
        },
    }
    peak_rss = cast(
        int,
        cast(Mapping[str, object], result["performance"])["peak_process_rss_bytes"],
    )
    if peak_rss > _HARD_RSS_BYTES:
        raise ResourceLimitError(f"{role} exceeded the hard RSS limit")
    return result, selections


def _summary(
    pilot: Mapping[str, object],
    test: Mapping[str, object],
) -> str:
    pilot_performance = cast(Mapping[str, object], pilot["performance"])
    test_performance = cast(Mapping[str, object], test["performance"])
    return "\n".join(
        (
            "# Phase 4 Frozen AV2 Motion Campaign",
            "",
            "The exact Batch 4.5 matrix was executed without scientific protocol "
            "changes. The 50-scenario pilot gate completed before the 300-scenario "
            "test cohort was opened.",
            "",
            "## Completion",
            "",
            f"- Pilot scenario/configuration units: {pilot['scenario_configuration_count']}",
            f"- Test scenario/configuration units: {test['scenario_configuration_count']}",
            f"- Pilot trajectory/configuration records: {pilot['trajectory_configuration_count']}",
            f"- Test trajectory/configuration records: {test['trajectory_configuration_count']}",
            "- Undocumented failures: 0",
            "- Checkpoint verification: complete with zero representation recomputation",
            "- Source/cache mutation: none",
            "",
            "## Resources",
            "",
            f"- Pilot runtime seconds: {pilot_performance['total_seconds']}",
            f"- Test runtime seconds: {test_performance['total_seconds']}",
            f"- Peak process RSS bytes: {max(cast(int, pilot_performance['peak_process_rss_bytes']), cast(int, test_performance['peak_process_rss_bytes']))}",
            "",
            "Results remain descriptive until Batch 4.7 statistical analysis. "
            "No final method is selected here.",
            "",
            _PASS_STATEMENT,
            "",
        )
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--cohort-evidence-root",
        type=Path,
        default=Path("results/phase4/motion_cohort"),
    )
    parser.add_argument(
        "--protocol-root",
        type=Path,
        default=Path("results/phase4/protocol_freeze"),
    )
    parser.add_argument(
        "--cohort-cache-root",
        type=Path,
        default=Path("cache/phase4_motion_cohort/cohort"),
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("cache/phase4_frozen_campaign"),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("results/phase4/frozen_campaign"),
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    args = _arguments()
    root = args.repository_root.resolve()
    cohort_root = (root / args.cohort_evidence_root).resolve()
    protocol_root = (root / args.protocol_root).resolve()
    cache_root = (root / args.cohort_cache_root).resolve()
    generated_root = (root / args.generated_root).resolve()
    evidence_root = (root / args.evidence_root).resolve()
    if any(
        not path.is_relative_to(root)
        for path in (
            cohort_root,
            protocol_root,
            cache_root,
            generated_root,
            evidence_root,
        )
    ):
        raise ArtifactError("campaign paths must remain inside the repository")
    disk = shutil.disk_usage(root)
    reserve = int(disk.total * 0.15)
    if disk.free - reserve < _ESTIMATED_REQUIRED_BYTES:
        raise ResourceLimitError("insufficient disk headroom for frozen campaign")
    cohort_evidence, matrix = _verify_inputs(root, cohort_root, protocol_root)
    manifest_path = cohort_root / "cohort_manifest.json"
    generated_root.mkdir(parents=True, exist_ok=True)

    pilot_units = read_pilot_units(manifest_path)
    pilot_configuration, pilot_reporting = _configuration(
        matrix,
        PILOT_SCENARIO_COUNT,
    )
    pilot_cache, pilot_cache_bytes = _input_snapshot(
        root, cache_root, pilot_units, "pilot"
    )
    (
        pilot_trajectory_identity,
        pilot_trajectory_count,
        pilot_sample_count,
        pilot_membership,
    ) = _membership(root, cache_root, pilot_units, "pilot")
    pilot_result, pilot_selections = _run_role(
        root,
        cache_root,
        generated_root,
        "pilot",
        pilot_units,
        pilot_configuration,
        pilot_reporting,
        matrix,
        pilot_cache,
        pilot_cache_bytes,
        pilot_trajectory_identity,
        pilot_trajectory_count,
        pilot_sample_count,
        pilot_membership,
    )
    pilot_completion = PilotCompletion(
        cohort_identity=FROZEN_COHORT_IDENTITY,
        matrix_identity=FROZEN_MATRIX_IDENTITY,
        pilot_scenario_identity=cast(str, pilot_result["scenario_identity"]),
        trajectory_membership_identity=pilot_trajectory_identity,
        cache_snapshot_identity=pilot_cache,
        checkpoint_output_identity=cast(
            str, pilot_result["checkpoint_output_identity"]
        ),
        scenario_count=PILOT_SCENARIO_COUNT,
        configuration_count=CONFIGURATION_COUNT,
        scenario_configuration_count=PILOT_SCENARIO_COUNT * CONFIGURATION_COUNT,
        failure_count=0,
        protocol_changed=False,
        resource_feasible=True,
    )
    pilot_gate_path = generated_root / "pilot_completion.json"
    write_pilot_completion(pilot_gate_path, pilot_completion)
    _LOGGER.info("pilot gate completed; test access is now permitted")

    test_units = read_test_units(manifest_path, pilot_gate_path)
    test_configuration, test_reporting = _configuration(
        matrix,
        TEST_SCENARIO_COUNT,
    )
    test_cache, test_cache_bytes = _input_snapshot(root, cache_root, test_units, "test")
    (
        test_trajectory_identity,
        test_trajectory_count,
        test_sample_count,
        test_membership,
    ) = _membership(root, cache_root, test_units, "test")
    test_result, test_selections = _run_role(
        root,
        cache_root,
        generated_root,
        "test",
        test_units,
        test_configuration,
        test_reporting,
        matrix,
        test_cache,
        test_cache_bytes,
        test_trajectory_identity,
        test_trajectory_count,
        test_sample_count,
        test_membership,
    )

    matched = [
        {**selection, "cohort_role": role}
        for role, selections in (
            ("pilot", pilot_selections),
            ("test", test_selections),
        )
        for selection in selections
    ]
    matched_byte = [row for row in matched if row["dimension"] == "byte_ratio"]
    matched_keyframe = [row for row in matched if row["dimension"] == "keyframe_ratio"]
    performance = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "hardware": {
            "operating_system": "Ubuntu under WSL2",
            "machine": platform.machine(),
            "host_model": "ASUS reference laptop",
            "python_version": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
        },
        "sequential_processing": True,
        "worker_count": 1,
        "pilot": pilot_result["performance"],
        "test": test_result["performance"],
        "disk_free_before_bytes": disk.free,
        "disk_free_after_bytes": shutil.disk_usage(root).free,
        "generated_disk_bytes": sweep._tree_size(generated_root),
    }
    contract = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "starting_head": _STARTING_HEAD,
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "validation_identity": _VALIDATION_IDENTITY,
        "metric_identity": _METRIC_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "protocol_frozen_before_pilot_and_test": True,
        "pilot_caused_scientific_protocol_change": False,
        "test_outcomes_used_to_alter_campaign": False,
        "configuration_count": CONFIGURATION_COUNT,
        "configurations": matrix["methods"],
        "budget_targets": matrix["budget_targets"],
        "execution_order": matrix["execution_order"],
        "checkpoint_policy": matrix["checkpoint_policy"],
        "aggregation_policy": matrix["aggregation_policy"],
        "exclusion_policy": matrix["exclusion_policy"],
        "final_method_selected": False,
        "statistical_analysis_performed": False,
        "official_source": cohort_evidence["official_source"],
    }
    failure_report = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "failure_count": 0,
        "undocumented_failure_count": 0,
        "failures": [],
        "omitted_failures": False,
    }
    determinism = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "pilot_checkpoint_output_identity": pilot_result["checkpoint_output_identity"],
        "pilot_repeat_checkpoint_output_identity": pilot_result[
            "repeat_checkpoint_output_identity"
        ],
        "test_checkpoint_output_identity": test_result["checkpoint_output_identity"],
        "test_repeat_checkpoint_output_identity": test_result[
            "repeat_checkpoint_output_identity"
        ],
        "pilot_verification_reuse_count": pilot_result["verification_pass_reuse_count"],
        "test_verification_reuse_count": test_result["verification_pass_reuse_count"],
        "total_verified_checkpoint_count": 6300,
        "representation_recomputation_count": 0,
        "determinism_mismatch_count": 0,
        "source_cache_unchanged": True,
    }
    evidence_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "campaign_contract.json": contract,
        "pilot_results.json": pilot_result,
        "test_results.json": test_result,
        "matched_byte_results.json": {
            "batch": _BATCH,
            "schema_version": "1.0",
            "selection_count": len(matched_byte),
            "selections": matched_byte,
        },
        "matched_keyframe_results.json": {
            "batch": _BATCH,
            "schema_version": "1.0",
            "selection_count": len(matched_keyframe),
            "selections": matched_keyframe,
        },
        "failure_report.json": failure_report,
        "performance_report.json": performance,
        "determinism_report.json": determinism,
    }
    for name, payload in payloads.items():
        _write_json(evidence_root / name, payload)
    shutil.copyfile(pilot_gate_path, evidence_root / "pilot_completion.json")
    summary_path = evidence_root / "summary.md"
    summary_path.write_text(
        _summary(pilot_result, test_result),
        encoding="utf-8",
        newline="\n",
    )
    evidence_files = (*payloads.keys(), "pilot_completion.json", "summary.md")
    evidence = {
        "batch": _BATCH,
        "schema_version": "1.0",
        "campaign_decision": "completed",
        "pass_statement": _PASS_STATEMENT,
        "starting_head": _STARTING_HEAD,
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "validation_identity": _VALIDATION_IDENTITY,
        "metric_identity": _METRIC_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "pilot_scenario_identity": pilot_result["scenario_identity"],
        "test_scenario_identity": test_result["scenario_identity"],
        "pilot_trajectory_membership_identity": pilot_trajectory_identity,
        "test_trajectory_membership_identity": test_trajectory_identity,
        "pilot_scenario_count": PILOT_SCENARIO_COUNT,
        "test_scenario_count": TEST_SCENARIO_COUNT,
        "configuration_count": CONFIGURATION_COUNT,
        "pilot_scenario_configuration_count": 900,
        "test_scenario_configuration_count": 5400,
        "total_scenario_configuration_count": 6300,
        "pilot_trajectory_count": pilot_trajectory_count,
        "test_trajectory_count": test_trajectory_count,
        "pilot_trajectory_configuration_count": pilot_result[
            "trajectory_configuration_count"
        ],
        "test_trajectory_configuration_count": test_result[
            "trajectory_configuration_count"
        ],
        "pilot_source_sample_count": pilot_sample_count,
        "test_source_sample_count": test_sample_count,
        "failure_count": 0,
        "verified_checkpoint_count": 6300,
        "representation_recomputation_count": 0,
        "source_cache_unchanged": True,
        "protocol_frozen_before_pilot_and_test": True,
        "pilot_caused_scientific_protocol_change": False,
        "test_outcomes_used_to_alter_campaign": False,
        "results_descriptive_until_batch4_7": True,
        "final_method_selected": False,
        "provider_or_generated_data_tracked": False,
        "evidence_file_sha256": {
            name: _sha256(evidence_root / name) for name in evidence_files
        },
    }
    _write_json(evidence_root / "evidence.json", evidence)
    _LOGGER.info("%s", _PASS_STATEMENT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
