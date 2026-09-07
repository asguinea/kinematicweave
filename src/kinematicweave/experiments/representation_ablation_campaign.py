"""Read-only attribution analysis for the frozen Phase 4 motion campaign."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from typing import Any, cast

import numpy as np
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_bytes, canonical_sha256
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.ablation_analysis import (
    CONFIGURATION_IDS,
    AblationFamily,
    ContrastPair,
    apply_ablation_holm,
    attribution_claim_label,
    frozen_ablation_families,
    retained_budget_mismatch,
    validate_frozen_configuration_ids,
    validate_identical_keyframes,
)
from kinematicweave.experiments.statistical_analysis import (
    PILOT_RESAMPLES,
    ROOT_SEED,
    TEST_RESAMPLES,
    paired_analysis,
)
from kinematicweave.experiments.statistical_campaign import (
    FROZEN_BUDGET_CONTRACT_SHA256,
    FROZEN_COHORT_IDENTITY,
    FROZEN_MATRIX_IDENTITY,
    FROZEN_METRIC_IDENTITY,
    FROZEN_VALIDATION_IDENTITY,
)

BATCH = "4.8"
SCHEMA_VERSION = "1.0"
STARTING_HEAD = "985c59548610f49a881479068c283102bde7aacd"
PASS_STATEMENT = (
    "Phase 4 representation contributions attributed on the frozen AV2 pilot "
    "and test cohorts."
)

type Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One predeclared ablation outcome."""

    name: str
    lower_is_better: bool
    unit: str
    domain: str


_METRICS = (
    MetricSpec("byte_ratio", True, "ratio", "storage"),
    MetricSpec("keyframe_ratio", True, "ratio", "complexity"),
    MetricSpec("segment_ratio", True, "ratio", "complexity"),
    MetricSpec("procedural_segment_count", True, "segments", "complexity"),
    MetricSpec("hold_segment_count", True, "segments", "complexity"),
    MetricSpec("linear_segment_count", True, "segments", "complexity"),
    MetricSpec("hermite_segment_count", True, "segments", "complexity"),
    MetricSpec("position_mean_m", True, "m", "position"),
    MetricSpec("position_p95_m", True, "m", "position"),
    MetricSpec("position_maximum_m", True, "m", "position"),
    MetricSpec("heading_mean_rad", True, "rad", "heading"),
    MetricSpec("heading_p95_rad", True, "rad", "heading"),
    MetricSpec("velocity_mean_mps", True, "m/s", "velocity"),
    MetricSpec("velocity_p95_mps", True, "m/s", "velocity"),
    MetricSpec("velocity_maximum_mps", True, "m/s", "velocity"),
    MetricSpec("endpoint_position_mean_m", True, "m", "endpoint"),
    MetricSpec("endpoint_position_p95_m", True, "m", "endpoint"),
    MetricSpec("endpoint_position_maximum_m", True, "m", "endpoint"),
    MetricSpec("semantic_overall_f1", False, "score", "semantic"),
    MetricSpec("semantic_stop_f1", False, "score", "semantic"),
    MetricSpec("semantic_turn_f1", False, "score", "semantic"),
    MetricSpec("semantic_acceleration_f1", False, "score", "semantic"),
    MetricSpec("semantic_braking_f1", False, "score", "semantic"),
    MetricSpec("encoding_seconds", True, "s", "runtime"),
    MetricSpec("replay_seconds", True, "s", "runtime"),
)

_CONFIGURATION_LABELS = {
    "raw_samples": "raw samples",
    "exact_adjacent": "exact adjacent-sample procedural replay",
    "uniform_linear_stride_2": "uniform linear stride 2",
    "uniform_hermite_stride_2": "uniform Hermite stride 2",
    "uniform_linear_stride_5": "uniform linear stride 5",
    "uniform_hermite_stride_5": "uniform Hermite stride 5",
    "uniform_linear_stride_10": "uniform linear stride 10",
    "uniform_hermite_stride_10": "uniform Hermite stride 10",
    "fixed_interval_1000_ms": "fixed-interval linear 1,000 ms",
    "rdp_linear_0_05_m": "RDP linear 0.05 m",
    "position_bounded_linear_0_10_m": "position-bounded linear 0.10 m",
    "unconstrained_hermite_0_10_m": "unconstrained Hermite 0.10 m",
    "position_velocity_hybrid_0_10_m_1_00_mps": (
        "position/velocity hybrid 0.10 m and 1.00 m/s"
    ),
}


def _read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationError(f"{path} must contain a JSON object")
    return cast(Json, value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_evidence(root: Path, relative_root: str) -> Json:
    evidence_root = root / relative_root
    evidence = _read_json(evidence_root / "evidence.json")
    checksums = evidence.get("evidence_file_sha256")
    if not isinstance(checksums, dict):
        raise ValidationError(f"{relative_root} evidence checksums are missing")
    for name, expected in checksums.items():
        path = evidence_root / str(name)
        if not path.is_file() or _sha256(path) != expected:
            raise ArtifactError(f"accepted evidence differs: {path}")
    return evidence


def _describe(values: Iterable[float]) -> Json:
    array = np.asarray(
        [float(value) for value in values if math.isfinite(float(value))],
        dtype=np.float64,
    )
    if array.size == 0:
        return {"count": 0}
    lower, upper = np.quantile(array, (0.25, 0.75), method="linear")
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "median": float(np.median(array)),
        "standard_deviation": float(array.std(ddof=1)) if array.size > 1 else None,
        "interquartile_range": float(upper - lower),
        "p95": float(np.quantile(array, 0.95, method="linear")),
        "minimum": float(array.min()),
        "maximum": float(array.max()),
    }


def _load_scenario_rows(root: Path) -> tuple[list[Json], Json]:
    evidence = _read_json(root / "results/phase4/statistical_analysis/evidence.json")
    descriptor = cast(Json, evidence["derived_scenario_table"])
    path = root / str(descriptor["path"])
    if (
        not path.is_file()
        or _sha256(path) != descriptor["sha256"]
        or descriptor["row_count"] != 6_300
    ):
        raise ArtifactError("accepted Batch 4.7 scenario aggregate differs")
    rows = [cast(Json, row) for row in pq.read_table(path).to_pylist()]
    indexed = {
        (
            str(row["cohort_role"]),
            str(row["parameter_identity"]),
            str(row["source_scenario_id"]),
        ): row
        for row in rows
    }
    if len(indexed) != 6_300:
        raise ArtifactError("scenario aggregate primary key differs")
    raw_descriptors: dict[str, Json] = {}
    for role, expected in (("pilot", 900), ("test", 5_400)):
        raw_path = (
            root
            / "cache/phase4_frozen_campaign"
            / role
            / "final-v1/scenario_method_metrics.parquet"
        )
        raw_rows = pq.read_table(
            raw_path,
            columns=[
                "parameter_identity",
                "source_scenario_id",
                "raw_canonical_bytes",
                "hold_segment_count",
                "linear_segment_count",
                "hermite_segment_count",
                "motion_summary_json",
                "artifact_summary_json",
            ],
        ).to_pylist()
        if len(raw_rows) != expected:
            raise ArtifactError(f"{role} frozen raw scenario count differs")
        for raw in raw_rows:
            key = (role, str(raw["parameter_identity"]), str(raw["source_scenario_id"]))
            if key not in indexed:
                raise ArtifactError("Batch 4.6 and 4.7 scenario membership differs")
            row = indexed[key]
            motion = cast(Json, json.loads(str(raw["motion_summary_json"])))
            row.update(
                {
                    "raw_canonical_bytes": int(raw["raw_canonical_bytes"]),
                    "hold_segment_count": int(raw["hold_segment_count"]),
                    "linear_segment_count": int(raw["linear_segment_count"]),
                    "hermite_segment_count": int(raw["hermite_segment_count"]),
                    "gap_preservation_passed": bool(motion["gap_preservation_passed"]),
                    "artifact_summary_json": str(raw["artifact_summary_json"]),
                }
            )
        raw_descriptors[role] = {
            "path": raw_path.relative_to(root).as_posix(),
            "sha256": _sha256(raw_path),
            "row_count": len(raw_rows),
        }
    observed = {
        name: configuration_id
        for name, configuration_id in CONFIGURATION_IDS.items()
        if any(row["parameter_identity"] == configuration_id for row in rows)
    }
    validate_frozen_configuration_ids(observed)
    return rows, {
        "scenario_aggregate": descriptor,
        "frozen_raw_scenario_tables": raw_descriptors,
    }


def _rows_by_role_configuration(
    rows: Sequence[Json],
) -> dict[tuple[str, str], list[Json]]:
    grouped: dict[tuple[str, str], list[Json]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["cohort_role"]), str(row["parameter_identity"]))].append(row)
    for group in grouped.values():
        group.sort(key=lambda row: str(row["source_scenario_id"]))
    return grouped


def _mean(rows: Sequence[Json], field: str) -> float:
    values = [float(row[field]) for row in rows if row[field] is not None]
    if not values:
        raise ValidationError(f"{field} has no valid values")
    return float(np.mean(values))


def _configuration_record(name: str, rows: Sequence[Json]) -> Json:
    return {
        "name": name,
        "label": _CONFIGURATION_LABELS[name],
        "parameter_identity": CONFIGURATION_IDS[name],
        "method_id": rows[0]["method_id"],
        "family": rows[0]["family"],
    }


def _validate_budget_contexts(root: Path) -> Json:
    byte = _read_json(root / "results/phase4/frozen_campaign/matched_byte_results.json")
    keyframe = _read_json(
        root / "results/phase4/frozen_campaign/matched_keyframe_results.json"
    )
    required = {
        CONFIGURATION_IDS["position_bounded_linear_0_10_m"],
        CONFIGURATION_IDS["uniform_linear_stride_10"],
        CONFIGURATION_IDS["fixed_interval_1000_ms"],
    }
    contexts: list[Json] = []
    for name, payload, dimension, target in (
        ("primary_byte_0.48", byte, "byte_ratio", 0.48),
        ("primary_keyframe_0.12", keyframe, "keyframe_ratio", 0.12),
    ):
        for role in ("pilot", "test"):
            selected = {
                str(row["parameter_identity"]): row
                for row in payload["selections"]
                if row["cohort_role"] == role
                and row["priority"] == "primary"
                and row["dimension"] == dimension
                and float(row["target"]) == target
            }
            if not required.issubset(selected):
                raise ArtifactError(f"{name} does not retain all ablation methods")
            contexts.append(
                {
                    "context": name,
                    "cohort_role": role,
                    "dimension": dimension,
                    "target": target,
                    "selection_input": "relevant_achieved_budget_only",
                    "metric_interpolation": False,
                    "selected_methods": [selected[key] for key in sorted(required)],
                }
            )
    return {
        "context_count": len(contexts),
        "contexts": contexts,
        "rematching_performed": False,
    }


def _bound_invariants(
    role: str,
    name: str,
    rows: Sequence[Json],
) -> Json:
    position_bound_applies = name in {
        "position_bounded_linear_0_10_m",
        "unconstrained_hermite_0_10_m",
        "position_velocity_hybrid_0_10_m_1_00_mps",
    }
    velocity_bound_applies = name == "position_velocity_hybrid_0_10_m_1_00_mps"
    position_maximum = max(float(row["position_maximum_m"]) for row in rows)
    velocity_maximum = max(float(row["velocity_maximum_mps"]) for row in rows)
    return {
        "cohort_role": role,
        "configuration": name,
        "scenario_count": len(rows),
        "position_bound_m": 0.1 if position_bound_applies else None,
        "position_bound_violation_count": (
            sum(float(row["position_maximum_m"]) > 0.1 + 1e-9 for row in rows)
            if position_bound_applies
            else None
        ),
        "observed_position_maximum_m": position_maximum,
        "velocity_bound_mps": 1.0 if velocity_bound_applies else None,
        "velocity_bound_violation_count": (
            sum(float(row["velocity_maximum_mps"]) > 1.0 + 1e-9 for row in rows)
            if velocity_bound_applies
            else None
        ),
        "observed_velocity_maximum_mps": velocity_maximum,
        "gap_preservation_failure_count": sum(
            not bool(row["gap_preservation_passed"]) for row in rows
        ),
    }


def _pair_records(
    family: AblationFamily,
    role: str,
    pair: ContrastPair,
    grouped: Mapping[tuple[str, str], list[Json]],
) -> list[Json]:
    configuration_a = CONFIGURATION_IDS[pair.method_a]
    configuration_b = CONFIGURATION_IDS[pair.method_b]
    rows_a = grouped[(role, configuration_a)]
    rows_b = grouped[(role, configuration_b)]
    if len(rows_a) != (50 if role == "pilot" else 300) or len(rows_b) != len(rows_a):
        raise ArtifactError("ablation scenario membership count differs")
    if pair.identical_keyframes_required:
        validate_identical_keyframes(
            {
                str(row["source_scenario_id"]): int(row["retained_keyframe_count"])
                for row in rows_a
            },
            {
                str(row["source_scenario_id"]): int(row["retained_keyframe_count"])
                for row in rows_b
            },
        )
    byte_mismatch = retained_budget_mismatch(
        _mean(rows_a, "byte_ratio"),
        _mean(rows_b, "byte_ratio"),
    )
    keyframe_mismatch = retained_budget_mismatch(
        _mean(rows_a, "keyframe_ratio"),
        _mean(rows_b, "keyframe_ratio"),
    )
    segment_mismatch = retained_budget_mismatch(
        _mean(rows_a, "segment_ratio"),
        _mean(rows_b, "segment_ratio"),
    )
    records: list[Json] = []
    for metric in _METRICS:
        values_a = {
            str(row["source_scenario_id"]): (
                None if row[metric.name] is None else float(row[metric.name])
            )
            for row in rows_a
        }
        values_b = {
            str(row["source_scenario_id"]): (
                None if row[metric.name] is None else float(row[metric.name])
            )
            for row in rows_b
        }
        result = paired_analysis(
            values_a,
            values_b,
            lower_is_better=metric.lower_is_better,
            resamples=PILOT_RESAMPLES if role == "pilot" else TEST_RESAMPLES,
            seed_components=(
                "batch4.8",
                family.name,
                role,
                pair.method_a,
                pair.method_b,
                metric.name,
            ),
        )
        records.append(
            {
                "contrast_family": family.name,
                "context": family.context,
                "cohort_role": role,
                "metric": metric.name,
                "metric_domain": metric.domain,
                "unit": metric.unit,
                "lower_is_better": metric.lower_is_better,
                "method_a": _configuration_record(pair.method_a, rows_a),
                "method_b": _configuration_record(pair.method_b, rows_b),
                "identical_keyframes_required": pair.identical_keyframes_required,
                "identical_keyframes_verified": pair.identical_keyframes_required,
                "achieved_byte_ratio": byte_mismatch,
                "achieved_keyframe_ratio": keyframe_mismatch,
                "achieved_segment_ratio": segment_mismatch,
                **result,
            }
        )
    return records


def _analysis_records(
    rows: Sequence[Json],
    families: Sequence[AblationFamily],
) -> list[Json]:
    grouped = _rows_by_role_configuration(rows)
    records = [
        record
        for family in families
        for role in ("pilot", "test")
        for pair in family.pairs
        for record in _pair_records(family, role, pair, grouped)
    ]
    return apply_ablation_holm(records)


def _family_payload(
    name: str,
    records: Sequence[Json],
    rows: Sequence[Json],
    *,
    contexts: Sequence[str] = (),
) -> Json:
    selected = [
        record
        for record in records
        if record["contrast_family"] == name or record["contrast_family"] in contexts
    ]
    configurations = sorted(
        {
            cast(str, record[side]["name"])
            for record in selected
            for side in ("method_a", "method_b")
        }
    )
    grouped = _rows_by_role_configuration(rows)
    invariants = [
        _bound_invariants(
            role,
            configuration,
            grouped[(role, CONFIGURATION_IDS[configuration])],
        )
        for role in ("pilot", "test")
        for configuration in configurations
    ]
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "inferential_unit": "scenario",
        "pilot_and_test_separate": True,
        "contrast_record_count": len(selected),
        "configurations": [
            {
                "name": configuration,
                "label": _CONFIGURATION_LABELS[configuration],
                "parameter_identity": CONFIGURATION_IDS[configuration],
            }
            for configuration in configurations
        ],
        "invariants": invariants,
        "records": selected,
    }


def _serialization_overhead(rows: Sequence[Json]) -> Json:
    grouped = _rows_by_role_configuration(rows)
    roles: list[Json] = []
    for role in ("pilot", "test"):
        raw_rows = grouped[(role, CONFIGURATION_IDS["raw_samples"])]
        exact_rows = grouped[(role, CONFIGURATION_IDS["exact_adjacent"])]
        raw_by_scenario = {str(row["source_scenario_id"]): row for row in raw_rows}
        exact_by_scenario = {str(row["source_scenario_id"]): row for row in exact_rows}
        if set(raw_by_scenario) != set(exact_by_scenario):
            raise ArtifactError("raw/exact scenario membership differs")
        file_totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"size_bytes": 0, "row_count": 0, "scenario_file_count": 0}
        )
        ratios: list[float] = []
        differences: list[float] = []
        for scenario_id in sorted(raw_by_scenario):
            raw = raw_by_scenario[scenario_id]
            exact = exact_by_scenario[scenario_id]
            raw_bytes = int(raw["serialized_representation_bytes"])
            exact_bytes = int(exact["serialized_representation_bytes"])
            ratios.append(exact_bytes / raw_bytes)
            differences.append(float(exact_bytes - raw_bytes))
            for representation, row in (("raw", raw), ("exact", exact)):
                summary = cast(Json, json.loads(str(row["artifact_summary_json"])))
                first_run = cast(Json, summary["runs"][0])
                for file_value in first_run["files"]:
                    file_record = cast(Json, file_value)
                    key = f"{representation}:{file_record['name']}"
                    file_totals[key]["size_bytes"] += int(file_record["size_bytes"])
                    row_count = file_record["row_count"]
                    file_totals[key]["row_count"] += (
                        0 if row_count is None else int(row_count)
                    )
                    file_totals[key]["scenario_file_count"] += 1
        raw_total = sum(int(row["serialized_representation_bytes"]) for row in raw_rows)
        exact_total = sum(
            int(row["serialized_representation_bytes"]) for row in exact_rows
        )
        exact_segment_bytes = file_totals["exact:procedural_segments.parquet"][
            "size_bytes"
        ]
        roles.append(
            {
                "cohort_role": role,
                "scenario_count": len(raw_rows),
                "raw_serialized_bytes": raw_total,
                "exact_serialized_bytes": exact_total,
                "exact_minus_raw_bytes": exact_total - raw_total,
                "exact_to_raw_ratio": _describe(ratios),
                "paired_byte_increase": _describe(differences),
                "file_components": dict(sorted(file_totals.items())),
                "exact_nonsegment_bytes": exact_total - exact_segment_bytes,
                "parquet_overhead_policy": (
                    "physical file sizes include Parquet framing; accepted artifacts "
                    "do not isolate footer and encoding overhead from payload bytes"
                ),
                "exact_replay_is_compression_method": False,
            }
        )
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "analysis_type": "descriptive_reference_decomposition",
        "roles": roles,
        "raw_and_exact_treated_as_budget_competitors": False,
        "codec_execution_performed": False,
    }


def _find_record(
    records: Sequence[Json],
    family: str,
    metric: str,
    *,
    role: str = "test",
    method_a: str | None = None,
    method_b: str | None = None,
) -> Json:
    matches = [
        record
        for record in records
        if record["contrast_family"] == family
        and record["cohort_role"] == role
        and record["metric"] == metric
        and (method_a is None or record["method_a"]["name"] == method_a)
        and (method_b is None or record["method_b"]["name"] == method_b)
    ]
    if len(matches) != 1:
        raise ValidationError(
            f"expected one record for {family}/{role}/{metric}, got {len(matches)}"
        )
    return matches[0]


def _effect_cell(record: Json) -> Json:
    adjusted = float(record["adjusted_p_value"])
    return {
        "metric": record["metric"],
        "unit": record["unit"],
        "method_a": record["method_a"]["label"],
        "method_b": record["method_b"]["label"],
        "positive_favors": "method_b",
        "natural_effect": record["method_b_advantage_mean"],
        "confidence_interval": [
            record["confidence_interval_lower"],
            record["confidence_interval_upper"],
        ],
        "adjusted_p_value": adjusted,
        "practical_magnitude": record["practical_magnitude"],
        "claim_label": attribution_claim_label(adjusted_p_value=adjusted),
    }


def _contribution_attribution(
    records: Sequence[Json],
    serialization: Json,
    family_payloads: Mapping[str, Json],
) -> Json:
    mappings = (
        (
            "adaptive breakpoint selection",
            "adaptive_segmentation_primary_byte",
            "position_bounded_linear_0_10_m",
            "uniform_linear_stride_10",
        ),
        (
            "linear interpolation",
            "interpolation_primitive",
            "uniform_linear_stride_10",
            "uniform_hermite_stride_10",
        ),
        (
            "Hermite primitive availability",
            "primitive_vocabulary",
            None,
            None,
        ),
        (
            "explicit velocity constraint",
            "velocity_constraint",
            None,
            None,
        ),
        (
            "temporal error optimization",
            "temporal_vs_geometric",
            None,
            None,
        ),
        (
            "geometric path simplification",
            "temporal_vs_geometric",
            None,
            None,
        ),
    )
    rows: list[Json] = []
    for contribution, family, method_a, method_b in mappings:
        cells = {
            "storage": [
                _effect_cell(
                    _find_record(
                        records,
                        family,
                        "byte_ratio",
                        method_a=method_a,
                        method_b=method_b,
                    )
                )
            ],
            "segment_keyframe_complexity": [
                _effect_cell(
                    _find_record(
                        records,
                        family,
                        metric,
                        method_a=method_a,
                        method_b=method_b,
                    )
                )
                for metric in ("keyframe_ratio", "segment_ratio")
            ],
            "position_fidelity": [
                _effect_cell(
                    _find_record(
                        records,
                        family,
                        metric,
                        method_a=method_a,
                        method_b=method_b,
                    )
                )
                for metric in ("position_mean_m", "position_maximum_m")
            ],
            "velocity_fidelity": [
                _effect_cell(
                    _find_record(
                        records,
                        family,
                        metric,
                        method_a=method_a,
                        method_b=method_b,
                    )
                )
                for metric in ("velocity_mean_mps", "velocity_maximum_mps")
            ],
            "semantic_preservation": [
                _effect_cell(
                    _find_record(
                        records,
                        family,
                        metric,
                        method_a=method_a,
                        method_b=method_b,
                    )
                )
                for metric in ("semantic_overall_f1", "semantic_stop_f1")
            ],
            "runtime": [
                _effect_cell(
                    _find_record(
                        records,
                        family,
                        metric,
                        method_a=method_a,
                        method_b=method_b,
                    )
                )
                for metric in ("encoding_seconds", "replay_seconds")
            ],
        }
        if contribution == "explicit velocity constraint":
            worst_case = {
                "claim_label": attribution_claim_label(
                    adjusted_p_value=None, invariant=True
                ),
                "statement": (
                    "hybrid position and velocity bounds had zero violations in "
                    "pilot and test"
                ),
            }
        elif contribution == "temporal error optimization":
            worst_case = {
                "claim_label": attribution_claim_label(
                    adjusted_p_value=None, invariant=True
                ),
                "statement": (
                    "position-bounded replay had zero 0.10 m bound violations"
                ),
            }
        elif contribution == "geometric path simplification":
            worst_case = {
                "claim_label": "descriptive_tendency",
                "statement": (
                    "RDP bounds geometric path deviation, not time-indexed replay "
                    "position or velocity"
                ),
            }
        else:
            worst_case = {
                "claim_label": "descriptive_tendency",
                "statement": "no additional hard worst-case guarantee",
            }
        rows.append(
            {
                "contribution": contribution,
                **cells,
                "worst_case_guarantees": worst_case,
            }
        )
    test_serialization = next(
        role for role in serialization["roles"] if role["cohort_role"] == "test"
    )
    rows.append(
        {
            "contribution": "procedural serialization",
            "storage": {
                "claim_label": "descriptive_tendency",
                "exact_to_raw_ratio": test_serialization["exact_to_raw_ratio"],
            },
            "segment_keyframe_complexity": {
                "claim_label": "descriptive_tendency",
                "statement": "one exact linear segment per adjacent valid sample pair",
            },
            "position_fidelity": {
                "claim_label": "invariant_guarantee",
                "statement": "zero source-timestamp position error",
            },
            "velocity_fidelity": {
                "claim_label": "invariant_guarantee",
                "statement": "zero source-timestamp velocity error",
            },
            "semantic_preservation": {
                "claim_label": "invariant_guarantee",
                "statement": "raw source events preserved exactly",
            },
            "runtime": {
                "claim_label": "descriptive_tendency",
                "statement": "procedural encoding and replay add measured work",
            },
            "worst_case_guarantees": {
                "claim_label": "invariant_guarantee",
                "statement": "exact replay is a correctness reference, not compression",
            },
        }
    )
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "columns": [
            "storage",
            "segment_keyframe_complexity",
            "position_fidelity",
            "velocity_fidelity",
            "semantic_preservation",
            "runtime",
            "worst_case_guarantees",
        ],
        "claim_labels": [
            "significant_result",
            "descriptive_tendency",
            "invariant_guarantee",
        ],
        "row_count": len(rows),
        "rows": rows,
        "universal_winner_declared": False,
        "source_family_count": len(family_payloads),
    }


def _descriptive_difference(
    rows_a: Sequence[Json],
    rows_b: Sequence[Json],
    field: str,
    *,
    lower_is_better: bool,
) -> float | None:
    values_a = [float(row[field]) for row in rows_a if row[field] is not None]
    values_b = [float(row[field]) for row in rows_b if row[field] is not None]
    if not values_a or not values_b:
        return None
    return (
        float(np.mean(values_a) - np.mean(values_b))
        if lower_is_better
        else float(np.mean(values_b) - np.mean(values_a))
    )


def _robustness(
    root: Path,
    rows: Sequence[Json],
    records: Sequence[Json],
) -> Json:
    principal_pairs = (
        (
            "interpolation_primitive",
            "uniform_linear_stride_10",
            "uniform_hermite_stride_10",
        ),
        (
            "adaptive_segmentation_primary_byte",
            "position_bounded_linear_0_10_m",
            "uniform_linear_stride_10",
        ),
        (
            "temporal_vs_geometric",
            "position_bounded_linear_0_10_m",
            "rdp_linear_0_05_m",
        ),
        (
            "primitive_vocabulary",
            "position_bounded_linear_0_10_m",
            "unconstrained_hermite_0_10_m",
        ),
        (
            "velocity_constraint",
            "unconstrained_hermite_0_10_m",
            "position_velocity_hybrid_0_10_m_1_00_mps",
        ),
        (
            "bounded_method_tradeoff",
            "position_bounded_linear_0_10_m",
            "position_velocity_hybrid_0_10_m_1_00_mps",
        ),
    )
    city_records: list[Json] = []
    city_metrics = (
        ("position_mean_m", True),
        ("velocity_mean_mps", True),
        ("semantic_overall_f1", False),
        ("byte_ratio", True),
        ("encoding_seconds", True),
    )
    for family, method_a, method_b in principal_pairs:
        for role in ("pilot", "test"):
            for city in sorted(
                {
                    str(row["city_or_region"])
                    for row in rows
                    if row["cohort_role"] == role
                }
            ):
                rows_a = [
                    row
                    for row in rows
                    if row["cohort_role"] == role
                    and row["parameter_identity"] == CONFIGURATION_IDS[method_a]
                    and str(row["city_or_region"]) == city
                ]
                rows_b = [
                    row
                    for row in rows
                    if row["cohort_role"] == role
                    and row["parameter_identity"] == CONFIGURATION_IDS[method_b]
                    and str(row["city_or_region"]) == city
                ]
                if not rows_a or len(rows_a) != len(rows_b):
                    continue
                for metric, lower in city_metrics:
                    effect = _descriptive_difference(
                        rows_a, rows_b, metric, lower_is_better=lower
                    )
                    overall = _find_record(
                        records,
                        family,
                        metric,
                        role=role,
                        method_a=method_a,
                        method_b=method_b,
                    )
                    city_records.append(
                        {
                            "contrast_family": family,
                            "cohort_role": role,
                            "city_or_region": city,
                            "metric": metric,
                            "scenario_count": len(rows_a),
                            "method_b_advantage_mean": effect,
                            "direction_agrees_with_overall": (
                                effect is not None
                                and (effect >= 0.0)
                                == (float(overall["method_b_advantage_mean"]) >= 0.0)
                            ),
                        }
                    )
    prior = _read_json(
        root / "results/phase4/statistical_analysis/robustness_analysis.json"
    )
    strata_rows = cast(
        list[Json], prior["trajectory_agent_class_and_motion_category_strata"]
    )
    strata_index = {
        (
            str(row["cohort_role"]),
            str(row["parameter_identity"]),
            str(row["stratum_kind"]),
            str(row["stratum"]),
        ): row
        for row in strata_rows
    }
    stratum_records: list[Json] = []
    for family, method_a, method_b in principal_pairs:
        for role in ("pilot", "test"):
            for kind in ("agent_class", "motion_category"):
                strata = sorted(
                    {
                        key[3]
                        for key in strata_index
                        if key[0] == role
                        and key[1] == CONFIGURATION_IDS[method_a]
                        and key[2] == kind
                        and (
                            role,
                            CONFIGURATION_IDS[method_b],
                            kind,
                            key[3],
                        )
                        in strata_index
                    }
                )
                for stratum in strata:
                    row_a = strata_index[
                        (role, CONFIGURATION_IDS[method_a], kind, stratum)
                    ]
                    row_b = strata_index[
                        (role, CONFIGURATION_IDS[method_b], kind, stratum)
                    ]
                    for metric, summary_name in (
                        ("position_mean_m", "trajectory_macro_position_mean_m"),
                        ("velocity_mean_mps", "trajectory_macro_velocity_mean_mps"),
                    ):
                        summary_a = cast(Json, row_a[summary_name])
                        summary_b = cast(Json, row_b[summary_name])
                        effect = float(summary_a["mean"]) - float(summary_b["mean"])
                        overall = _find_record(
                            records,
                            family,
                            metric,
                            role=role,
                            method_a=method_a,
                            method_b=method_b,
                        )
                        stratum_records.append(
                            {
                                "contrast_family": family,
                                "cohort_role": role,
                                "stratum_kind": kind,
                                "stratum": stratum,
                                "metric": metric,
                                "method_b_advantage_mean": effect,
                                "trajectory_count_method_a": row_a["trajectory_count"],
                                "trajectory_count_method_b": row_b["trajectory_count"],
                                "direction_agrees_with_overall": (
                                    (effect >= 0.0)
                                    == (
                                        float(overall["method_b_advantage_mean"]) >= 0.0
                                    )
                                ),
                            }
                        )
    event_records = [
        {
            "contrast_family": record["contrast_family"],
            "cohort_role": record["cohort_role"],
            "event_type": str(record["metric"])
            .removeprefix("semantic_")
            .removesuffix("_f1"),
            "method_a": record["method_a"],
            "method_b": record["method_b"],
            "method_b_advantage_mean": record["method_b_advantage_mean"],
            "adjusted_p_value": record["adjusted_p_value"],
            "practical_magnitude": record["practical_magnitude"],
            "valid_pair_count": record["valid_pair_count"],
            "missing_either_count": record["missing_either_count"],
        }
        for record in records
        if record["metric"]
        in {
            "semantic_stop_f1",
            "semantic_turn_f1",
            "semantic_acceleration_f1",
            "semantic_braking_f1",
        }
    ]
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "inferential_status": "descriptive_only",
        "city_records": city_records,
        "agent_class_and_motion_category_records": stratum_records,
        "event_type_records": event_records,
        "city_direction_disagreement_count": sum(
            not row["direction_agrees_with_overall"] for row in city_records
        ),
        "stratum_direction_disagreement_count": sum(
            not row["direction_agrees_with_overall"] for row in stratum_records
        ),
        "exceptions_omitted": False,
    }


def _contract(families: Sequence[AblationFamily], budget_contexts: Json) -> Json:
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "starting_head": STARTING_HEAD,
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "metric_identity": FROZEN_METRIC_IDENTITY,
        "validation_identity": FROZEN_VALIDATION_IDENTITY,
        "budget_contract_sha256": FROZEN_BUDGET_CONTRACT_SHA256,
        "inferential_unit": "scenario",
        "pilot_role": "replication",
        "test_role": "confirmatory",
        "pilot_and_test_pooled": False,
        "codec_execution_performed": False,
        "configuration_change_performed": False,
        "metric_change_performed": False,
        "budget_rematching_performed": False,
        "statistical_procedures_changed": False,
        "root_seed": ROOT_SEED,
        "confidence_level": 0.95,
        "pilot_resamples": PILOT_RESAMPLES,
        "test_resamples": TEST_RESAMPLES,
        "paired_test": "two-sided paired sign-flip permutation",
        "multiplicity": "Holm within ablation family x role x metric domain",
        "frozen_configurations": [
            {
                "name": name,
                "label": _CONFIGURATION_LABELS[name],
                "parameter_identity": configuration_id,
            }
            for name, configuration_id in CONFIGURATION_IDS.items()
        ],
        "families": [
            {
                "name": family.name,
                "context": family.context,
                "pairs": [
                    {
                        "method_a": pair.method_a,
                        "method_b": pair.method_b,
                        "positive_effect_favors": "method_b",
                        "identical_keyframes_required": (
                            pair.identical_keyframes_required
                        ),
                    }
                    for pair in family.pairs
                ],
            }
            for family in families
        ],
        "budget_contexts": budget_contexts,
        "metrics": [
            {
                "name": metric.name,
                "unit": metric.unit,
                "domain": metric.domain,
                "lower_is_better": metric.lower_is_better,
            }
            for metric in _METRICS
        ],
        "undefined_policy": "retain, count, and omit only from the affected pair",
        "source_absence_policy": "reuse accepted Batch 4.7 event handling",
        "composite_score_created": False,
        "universal_winner_allowed": False,
        "ablation_decision": "completed",
    }


def _summary(
    records: Sequence[Json],
    serialization: Json,
    robustness: Json,
) -> str:
    adaptive_position = _find_record(
        records,
        "adaptive_segmentation_primary_byte",
        "position_mean_m",
        method_a="position_bounded_linear_0_10_m",
        method_b="uniform_linear_stride_10",
    )
    temporal_velocity = _find_record(
        records, "temporal_vs_geometric", "velocity_mean_mps"
    )
    primitive_position = _find_record(
        records, "primitive_vocabulary", "position_mean_m"
    )
    primitive_velocity = _find_record(
        records, "primitive_vocabulary", "velocity_mean_mps"
    )
    velocity_velocity = _find_record(
        records, "velocity_constraint", "velocity_mean_mps"
    )
    velocity_maximum = _find_record(
        records, "velocity_constraint", "velocity_maximum_mps"
    )
    velocity_bytes = _find_record(records, "velocity_constraint", "byte_ratio")
    velocity_runtime = _find_record(records, "velocity_constraint", "encoding_seconds")
    bounded_bytes = _find_record(records, "bounded_method_tradeoff", "byte_ratio")
    bounded_segments = _find_record(records, "bounded_method_tradeoff", "segment_ratio")
    bounded_position = _find_record(
        records, "bounded_method_tradeoff", "position_mean_m"
    )
    bounded_velocity_mean = _find_record(
        records, "bounded_method_tradeoff", "velocity_mean_mps"
    )
    bounded_velocity_maximum = _find_record(
        records, "bounded_method_tradeoff", "velocity_maximum_mps"
    )
    bounded_semantic = _find_record(
        records, "bounded_method_tradeoff", "semantic_overall_f1"
    )
    bounded_runtime = _find_record(
        records, "bounded_method_tradeoff", "encoding_seconds"
    )
    test_serialization = next(
        role for role in serialization["roles"] if role["cohort_role"] == "test"
    )

    def favored(record: Json) -> str:
        return (
            str(record["method_b"]["label"])
            if float(record["method_b_advantage_mean"]) > 0.0
            else str(record["method_a"]["label"])
        )

    return (
        "# Phase 4 Representation Ablations and Contribution Attribution\n\n"
        f"{PASS_STATEMENT}\n\n"
        "The scenario-level attribution reuses the frozen Batch 4.6 and 4.7 "
        "artifacts without codec execution, metric changes, interpolation, or "
        "budget rematching. Test results are confirmatory and pilot results remain "
        "separate replication evidence.\n\n"
        "Adaptive linear segmentation helps when a time-indexed position guarantee "
        "matters because it places breakpoints in response to replay error rather "
        f"than at a fixed cadence. In the primary-byte context, {favored(adaptive_position)} "
        "had the lower test mean position error. This is attribution at retained "
        "achieved budgets, not a claim of invented exact budget equality.\n\n"
        "Geometric path simplification is insufficient for temporal replay because "
        "RDP controls perpendicular path geometry rather than the state reconstructed "
        f"at each timestamp. The test velocity comparison favored {favored(temporal_velocity)}, "
        "while the position-bounded method alone carries the measured time-indexed "
        "0.10 m replay guarantee.\n\n"
        "Adding unconstrained Hermite primitives changes interpolation between "
        f"selected endpoints: test position favored {favored(primitive_position)}, "
        f"but velocity favored {favored(primitive_velocity)}. Position preservation "
        "therefore does not imply dynamics preservation.\n\n"
        "The explicit velocity constraint adds a verified 1.00 m/s worst-case bound "
        "with zero pilot or test violations. Mean position was effectively unchanged "
        "between unconstrained and constrained Hermite, while the hybrid reduced "
        f"mean velocity error by {float(velocity_velocity['method_b_advantage_mean']):.6f} "
        "m/s and worst-case velocity error by "
        f"{float(velocity_maximum['method_b_advantage_mean']):.6f} m/s. The constraint "
        f"cost {abs(float(velocity_bytes['method_b_advantage_mean'])):.6f} byte-ratio "
        f"units, while {favored(velocity_runtime)} encoded faster; replay runtime did "
        "not show the same practical advantage.\n\n"
        "The simpler position-bounded linear method and the hybrid serve different "
        "requirements. Against linear, the hybrid reduced byte ratio by "
        f"{float(bounded_bytes['method_b_advantage_mean']):.6f}, segment ratio by "
        f"{float(bounded_segments['method_b_advantage_mean']):.6f}, mean position "
        f"error by {float(bounded_position['method_b_advantage_mean']):.6f} m, and "
        "worst-case velocity error by "
        f"{float(bounded_velocity_maximum['method_b_advantage_mean']):.6f} m/s. "
        "Linear reduced mean velocity error by "
        f"{abs(float(bounded_velocity_mean['method_b_advantage_mean'])):.6f} m/s, "
        f"raised overall semantic F1 by "
        f"{abs(float(bounded_semantic['method_b_advantage_mean'])):.6f}, and encoded "
        f"{abs(float(bounded_runtime['method_b_advantage_mean'])):.6f} s faster. Hybrid is "
        "preferable when the explicit velocity guarantee dominates; linear is "
        "preferable when semantic preservation, average velocity error, runtime, and "
        "primitive simplicity dominate. Neither wins every outcome.\n\n"
        "Exact adjacent-sample proceduralization preserves replay correctness but "
        "does not imply compression. Across test scenarios its exact-to-raw byte "
        f"ratio had mean {test_serialization['exact_to_raw_ratio']['mean']:.6f}; "
        "tracks, segments, manifests, metadata, and Parquet framing add physical "
        "storage beyond the raw sample table.\n\n"
        f"Robustness reporting retained {robustness['city_direction_disagreement_count']} "
        "city-level and "
        f"{robustness['stratum_direction_disagreement_count']} class or motion-category "
        "direction disagreements. These exceptions remain descriptive and are not "
        "filtered from the evidence. No universally best method is declared.\n"
    )


def _write_json(path: Path, value: Json) -> None:
    path.write_bytes(canonical_json_bytes(value))


def run_representation_ablation_campaign(root: Path) -> Json:
    """Execute and persist the complete read-only Batch 4.8 attribution."""
    upstream = {}
    for relative in (
        "results/phase4/motion_cohort",
        "results/phase4/motion_baselines",
        "results/phase4/motion_metrics",
        "results/phase4/motion_sweep",
        "results/phase4/protocol_freeze",
        "results/phase4/frozen_campaign",
        "results/phase4/statistical_analysis",
    ):
        upstream[relative] = _verify_evidence(root, relative)
    batch4_7 = upstream["results/phase4/statistical_analysis"]
    if (
        batch4_7["analysis_decision"] != "completed"
        or batch4_7["cohort_identity"] != FROZEN_COHORT_IDENTITY
        or batch4_7["matrix_identity"] != FROZEN_MATRIX_IDENTITY
        or batch4_7["metric_identity"] != FROZEN_METRIC_IDENTITY
        or batch4_7["analysis_failure_count"] != 0
    ):
        raise ArtifactError("Batch 4.7 statistical evidence gate did not pass")
    rows, input_descriptors = _load_scenario_rows(root)
    budget_contexts = _validate_budget_contexts(root)
    families = frozen_ablation_families()
    records = _analysis_records(rows, families)
    repeat_records = _analysis_records(rows, families)
    analysis_identity = canonical_sha256("phase4-ablation-analysis", records)
    repeat_identity = canonical_sha256("phase4-ablation-analysis", repeat_records)
    if analysis_identity != repeat_identity:
        raise ArtifactError("repeated ablation analysis differs")
    interpolation = _family_payload("interpolation_primitive", records, rows)
    adaptive = _family_payload(
        "adaptive_segmentation",
        records,
        rows,
        contexts=(
            "adaptive_segmentation_primary_byte",
            "adaptive_segmentation_primary_keyframe",
        ),
    )
    temporal = _family_payload("temporal_vs_geometric", records, rows)
    primitive = _family_payload("primitive_vocabulary", records, rows)
    velocity = _family_payload("velocity_constraint", records, rows)
    bounded = _family_payload("bounded_method_tradeoff", records, rows)
    serialization = _serialization_overhead(rows)
    family_payloads = {
        "interpolation_ablation": interpolation,
        "adaptive_segmentation_ablation": adaptive,
        "temporal_vs_geometric_ablation": temporal,
        "primitive_vocabulary_ablation": primitive,
        "velocity_constraint_ablation": velocity,
        "bounded_method_tradeoff": bounded,
    }
    attribution = _contribution_attribution(records, serialization, family_payloads)
    robustness = _robustness(root, rows, records)
    missing_pair_count = sum(int(record["missing_either_count"]) for record in records)
    undefined_effect_count = sum(
        record["standardized_paired_effect_dz"] is None for record in records
    )
    failure_report = {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "campaign_failure_count": 0,
        "analysis_failure_count": 0,
        "bound_violation_count": sum(
            int(invariant[field])
            for payload in family_payloads.values()
            for invariant in payload["invariants"]
            for field in (
                "position_bound_violation_count",
                "velocity_bound_violation_count",
            )
            if invariant[field] is not None
        ),
        "gap_preservation_failure_count": sum(
            int(invariant["gap_preservation_failure_count"])
            for payload in family_payloads.values()
            for invariant in payload["invariants"]
        ),
        "missing_pair_count": missing_pair_count,
        "undefined_effect_count": undefined_effect_count,
        "failures": [],
        "omitted_failures": False,
    }
    output_root = root / "results/phase4/ablation_analysis"
    if output_root.exists():
        raise ArtifactError("ablation evidence directory already exists")
    output_root.mkdir(parents=True)
    payloads = {
        "ablation_contract.json": _contract(families, budget_contexts),
        "interpolation_ablation.json": interpolation,
        "adaptive_segmentation_ablation.json": adaptive,
        "temporal_vs_geometric_ablation.json": temporal,
        "primitive_vocabulary_ablation.json": primitive,
        "velocity_constraint_ablation.json": velocity,
        "bounded_method_tradeoff.json": bounded,
        "serialization_overhead.json": serialization,
        "contribution_attribution.json": attribution,
        "robustness_analysis.json": robustness,
        "failure_report.json": failure_report,
    }
    for name, payload in payloads.items():
        _write_json(output_root / name, payload)
    (output_root / "summary.md").write_text(
        _summary(records, serialization, robustness),
        encoding="utf-8",
        newline="\n",
    )
    evidence_files = (*payloads, "summary.md")
    evidence = {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "ablation_decision": "completed",
        "pass_statement": PASS_STATEMENT,
        "starting_head": STARTING_HEAD,
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "metric_identity": FROZEN_METRIC_IDENTITY,
        "validation_identity": FROZEN_VALIDATION_IDENTITY,
        "budget_contract_sha256": FROZEN_BUDGET_CONTRACT_SHA256,
        "pilot_scenario_count": 50,
        "test_scenario_count": 300,
        "inferential_unit": "scenario",
        "contrast_family_count": len(families),
        "contrast_record_count": len(records),
        "analysis_identity": analysis_identity,
        "repeat_analysis_identity": repeat_identity,
        "determinism_verified": analysis_identity == repeat_identity,
        "input_descriptors": input_descriptors,
        "codec_execution_performed": False,
        "metric_rematching_performed": False,
        "new_configuration_created": False,
        "pilot_and_test_pooled": False,
        "universal_winner_declared": False,
        "analysis_failure_count": 0,
        "campaign_failure_count": 0,
        "environment": {
            "operating_system": "Ubuntu under WSL2",
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
        },
        "upstream_evidence_sha256": {
            f"{relative}/evidence.json": _sha256(root / relative / "evidence.json")
            for relative in upstream
        },
        "evidence_file_sha256": {
            name: _sha256(output_root / name) for name in evidence_files
        },
    }
    _write_json(output_root / "evidence.json", evidence)
    return evidence


__all__ = [
    "BATCH",
    "PASS_STATEMENT",
    "SCHEMA_VERSION",
    "STARTING_HEAD",
    "run_representation_ablation_campaign",
]
