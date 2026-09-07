"""Confirmatory statistical analysis of the frozen Phase 4 motion campaign."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from typing import Any, cast

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_bytes, canonical_sha256
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.statistical_analysis import (
    PILOT_RESAMPLES,
    ROOT_SEED,
    TEST_RESAMPLES,
    EventCounts,
    all_method_pairs,
    combine_event_counts,
    holm_adjust,
    paired_analysis,
)

SCHEMA_VERSION = "1.0"
BATCH = "4.7"
STARTING_HEAD = "b521cc64db734c591ec780af32eccc506139178a"
FROZEN_COHORT_IDENTITY = (
    "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
)
FROZEN_MATRIX_IDENTITY = (
    "c87a88e2f6a6a443a2654f5785cec16ab15e8ad8e2b1fef2d6d2bde8d5d59599"
)
FROZEN_METRIC_IDENTITY = (
    "03440d32241c0e55d490cf617cd1b2d9ed1a546865372b8dc155f06c696e3277"
)
FROZEN_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)
FROZEN_BUDGET_CONTRACT_SHA256 = (
    "771bcd84b419d3f86a4615f3fc2499e9c45010a953133933edbb9a323ea5c7e3"
)
PASS_STATEMENT = (
    "Phase 4 confirmatory statistical analysis completed on the frozen AV2 "
    "pilot and test cohorts."
)

type Json = dict[str, Any]

_PRIMARY_FAMILIES = (
    "fixed_interval_linear",
    "position_bounded_linear",
    "position_velocity_hybrid",
    "rdp_linear",
    "unconstrained_hermite",
    "uniform_hermite",
    "uniform_linear",
)
_EVENT_TYPES = ("stop", "turn", "acceleration", "braking")
_METRICS: tuple[tuple[str, bool, str, str], ...] = (
    ("position_mean_m", True, "m", "geometry"),
    ("position_p95_m", True, "m", "geometry"),
    ("velocity_mean_mps", True, "m/s", "geometry"),
    ("velocity_p95_mps", True, "m/s", "geometry"),
    ("semantic_overall_f1", False, "score", "semantic"),
    ("semantic_stop_f1", False, "score", "semantic"),
    ("semantic_turn_f1", False, "score", "semantic"),
    ("semantic_acceleration_f1", False, "score", "semantic"),
    ("semantic_braking_f1", False, "score", "semantic"),
    ("encoding_seconds", True, "s", "geometry"),
)

_SCENARIO_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("cohort_role", pa.string(), nullable=False),
        pa.field("source_scenario_id", pa.string(), nullable=False),
        pa.field("scenario_id", pa.string(), nullable=False),
        pa.field("selection_rank", pa.int64(), nullable=False),
        pa.field("city_or_region", pa.string(), nullable=True),
        pa.field("parameter_identity", pa.string(), nullable=False),
        pa.field("method_id", pa.string(), nullable=False),
        pa.field("family", pa.string(), nullable=False),
        pa.field("trajectory_count", pa.int64(), nullable=False),
        pa.field("source_sample_count", pa.int64(), nullable=False),
        pa.field("position_mean_m", pa.float64(), nullable=True),
        pa.field("position_median_m", pa.float64(), nullable=True),
        pa.field("position_p95_m", pa.float64(), nullable=True),
        pa.field("position_maximum_m", pa.float64(), nullable=True),
        pa.field("heading_mean_rad", pa.float64(), nullable=True),
        pa.field("heading_p95_rad", pa.float64(), nullable=True),
        pa.field("velocity_mean_mps", pa.float64(), nullable=True),
        pa.field("velocity_p95_mps", pa.float64(), nullable=True),
        pa.field("velocity_maximum_mps", pa.float64(), nullable=True),
        pa.field("endpoint_position_mean_m", pa.float64(), nullable=True),
        pa.field("endpoint_position_p95_m", pa.float64(), nullable=True),
        pa.field("endpoint_position_maximum_m", pa.float64(), nullable=True),
        pa.field("semantic_overall_precision", pa.float64(), nullable=True),
        pa.field("semantic_overall_recall", pa.float64(), nullable=True),
        pa.field("semantic_overall_f1", pa.float64(), nullable=True),
        pa.field("semantic_overall_source_count", pa.int64(), nullable=False),
        pa.field("semantic_overall_replay_count", pa.int64(), nullable=False),
        pa.field("semantic_overall_matched_count", pa.int64(), nullable=False),
        *[
            field
            for event in _EVENT_TYPES
            for field in (
                pa.field(f"semantic_{event}_precision", pa.float64(), nullable=True),
                pa.field(f"semantic_{event}_recall", pa.float64(), nullable=True),
                pa.field(f"semantic_{event}_f1", pa.float64(), nullable=True),
                pa.field(f"semantic_{event}_source_count", pa.int64(), nullable=False),
                pa.field(f"semantic_{event}_replay_count", pa.int64(), nullable=False),
                pa.field(f"semantic_{event}_matched_count", pa.int64(), nullable=False),
            )
        ],
        pa.field("serialized_representation_bytes", pa.int64(), nullable=False),
        pa.field("retained_keyframe_count", pa.int64(), nullable=False),
        pa.field("procedural_segment_count", pa.int64(), nullable=False),
        pa.field("byte_ratio", pa.float64(), nullable=False),
        pa.field("keyframe_ratio", pa.float64(), nullable=False),
        pa.field("segment_ratio", pa.float64(), nullable=False),
        pa.field("encoding_seconds", pa.float64(), nullable=False),
        pa.field("replay_seconds", pa.float64(), nullable=False),
        pa.field("total_seconds", pa.float64(), nullable=False),
        pa.field("failure_count", pa.int64(), nullable=False),
        pa.field("constraint_violation_count", pa.int64(), nullable=False),
    ],
    metadata={
        b"kinematicweave.schema_name": b"phase4_statistical_scenario_aggregates",
        b"kinematicweave.schema_version": b"1.0",
        b"kinematicweave.primary_key": (
            b"cohort_role,parameter_identity,source_scenario_id"
        ),
    },
)


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


def _cohort_metadata(
    root: Path,
) -> tuple[dict[str, str | None], dict[tuple[str, str], str]]:
    manifest = _read_json(root / "results/phase4/motion_cohort/cohort_manifest.json")
    city_by_source: dict[str, str | None] = {}
    class_by_source_agent: dict[tuple[str, str], str] = {}
    cache_root = root / "cache/phase4_motion_cohort/cohort/entries"
    for raw_unit in manifest["units"]:
        unit = cast(Json, raw_unit)
        if unit["cohort_role"] not in {"pilot", "test"}:
            continue
        source_id = str(unit["source_scenario_id"])
        key = str(unit["materialization_cache_key"])
        entry = cache_root / key[:2] / key
        scenario = pq.read_table(
            entry / "scenario_manifest.parquet",
            columns=["city_or_region"],
        ).to_pylist()[0]
        city_by_source[source_id] = cast(str | None, scenario["city_or_region"])
        agents = pq.read_table(
            entry / "agent_metadata.parquet",
            columns=["source_agent_id", "agent_class"],
        ).to_pylist()
        for agent in agents:
            source_agent = agent["source_agent_id"]
            if source_agent is not None:
                class_by_source_agent[(source_id, str(source_agent))] = str(
                    agent["agent_class"]
                )
    return city_by_source, class_by_source_agent


def _event_counts(value: Mapping[str, object]) -> EventCounts:
    return EventCounts(
        source=int(cast(int, value["source_event_count"])),
        replay=int(cast(int, value["replay_event_count"])),
        matched=int(cast(int, value["matched_event_count"])),
    )


def _motion_value(summary: Json, family: str, metric: str) -> float | None:
    family_value = cast(Json, summary[family])
    value = family_value[metric]
    return None if value is None else float(value)


def _scenario_row(
    raw: Json,
    role: str,
    city: str | None,
) -> Json:
    motion = cast(Json, json.loads(str(raw["motion_summary_json"])))
    semantic = cast(Json, json.loads(str(raw["semantic_summary_json"])))
    by_type = cast(Json, semantic["by_type"])
    overall = _event_counts(cast(Json, semantic["overall"]))
    turn = combine_event_counts(
        [
            _event_counts(cast(Json, by_type["left_turn"])),
            _event_counts(cast(Json, by_type["right_turn"])),
        ]
    )
    event_counts = {
        "stop": _event_counts(cast(Json, by_type["stop"])),
        "turn": turn,
        "acceleration": _event_counts(cast(Json, by_type["acceleration"])),
        "braking": _event_counts(cast(Json, by_type["braking"])),
    }
    family = str(raw["family"])
    constraint_violations = 0
    if family in {"position_bounded_linear", "position_velocity_hybrid"}:
        maximum = _motion_value(motion, "position", "maximum")
        constraint_violations += int(maximum is not None and maximum > 0.1 + 1e-9)
    if family == "position_velocity_hybrid":
        maximum = _motion_value(motion, "velocity", "maximum")
        constraint_violations += int(maximum is not None and maximum > 1.0 + 1e-9)
    return {
        "schema_version": SCHEMA_VERSION,
        "cohort_role": role,
        "source_scenario_id": str(raw["source_scenario_id"]),
        "scenario_id": str(raw["scenario_id"]),
        "selection_rank": int(raw["selection_rank"]),
        "city_or_region": city,
        "parameter_identity": str(raw["parameter_identity"]),
        "method_id": str(raw["method_id"]),
        "family": family,
        "trajectory_count": int(raw["trajectory_count"]),
        "source_sample_count": int(raw["source_sample_count"]),
        "position_mean_m": _motion_value(motion, "position", "mean"),
        "position_median_m": _motion_value(motion, "position", "median"),
        "position_p95_m": _motion_value(motion, "position", "p95"),
        "position_maximum_m": _motion_value(motion, "position", "maximum"),
        "heading_mean_rad": _motion_value(motion, "heading", "mean"),
        "heading_p95_rad": _motion_value(motion, "heading", "p95"),
        "velocity_mean_mps": _motion_value(motion, "velocity", "mean"),
        "velocity_p95_mps": _motion_value(motion, "velocity", "p95"),
        "velocity_maximum_mps": _motion_value(motion, "velocity", "maximum"),
        "endpoint_position_mean_m": _motion_value(motion, "endpoint_position", "mean"),
        "endpoint_position_p95_m": _motion_value(motion, "endpoint_position", "p95"),
        "endpoint_position_maximum_m": _motion_value(
            motion, "endpoint_position", "maximum"
        ),
        "semantic_overall_precision": overall.precision,
        "semantic_overall_recall": overall.recall,
        "semantic_overall_f1": overall.f1,
        "semantic_overall_source_count": overall.source,
        "semantic_overall_replay_count": overall.replay,
        "semantic_overall_matched_count": overall.matched,
        **{
            field: value
            for name in _EVENT_TYPES
            for field, value in (
                (f"semantic_{name}_precision", event_counts[name].precision),
                (f"semantic_{name}_recall", event_counts[name].recall),
                (f"semantic_{name}_f1", event_counts[name].f1),
                (f"semantic_{name}_source_count", event_counts[name].source),
                (f"semantic_{name}_replay_count", event_counts[name].replay),
                (f"semantic_{name}_matched_count", event_counts[name].matched),
            )
        },
        "serialized_representation_bytes": int(raw["serialized_representation_bytes"]),
        "retained_keyframe_count": int(raw["retained_keyframe_count"]),
        "procedural_segment_count": int(raw["procedural_segment_count"]),
        "byte_ratio": float(raw["byte_ratio"]),
        "keyframe_ratio": float(raw["keyframe_ratio"]),
        "segment_ratio": float(raw["segment_ratio"]),
        "encoding_seconds": float(raw["encoding_seconds"]),
        "replay_seconds": float(raw["replay_evaluation_seconds"]),
        "total_seconds": float(raw["total_seconds"]),
        "failure_count": 0,
        "constraint_violation_count": constraint_violations,
    }


def _scenario_aggregates(
    root: Path,
    city_by_source: Mapping[str, str | None],
) -> tuple[list[Json], Json]:
    rows: list[Json] = []
    raw_descriptors: dict[str, Json] = {}
    for role, expected in (("pilot", 900), ("test", 5_400)):
        path = (
            root
            / "cache/phase4_frozen_campaign"
            / role
            / "final-v1/scenario_method_metrics.parquet"
        )
        raw_rows = pq.read_table(path).to_pylist()
        if len(raw_rows) != expected:
            raise ArtifactError(f"{role} scenario aggregate count differs")
        rows.extend(
            _scenario_row(
                cast(Json, row), role, city_by_source[str(row["source_scenario_id"])]
            )
            for row in raw_rows
        )
        raw_descriptors[role] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "row_count": len(raw_rows),
        }
    rows.sort(
        key=lambda row: (
            str(row["cohort_role"]),
            int(row["selection_rank"]),
            str(row["parameter_identity"]),
        )
    )
    return rows, raw_descriptors


def _write_derived_table(root: Path, rows: Sequence[Json]) -> Json:
    cache_root = root / "cache/phase4_statistical_analysis"
    cache_root.mkdir(parents=True, exist_ok=True)
    path = cache_root / "scenario_aggregates.parquet"
    temporary = cache_root / "scenario_aggregates.repeat.parquet"
    table = pa.Table.from_pylist(list(rows), schema=_SCENARIO_SCHEMA)
    for destination in (path, temporary):
        pq.write_table(
            table,
            destination,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
            row_group_size=512,
        )
    first_hash = _sha256(path)
    repeat_hash = _sha256(temporary)
    temporary.unlink()
    if first_hash != repeat_hash:
        raise ArtifactError("derived scenario table is not byte deterministic")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": first_hash,
        "repeat_sha256": repeat_hash,
        "row_count": table.num_rows,
        "column_count": table.num_columns,
        "schema_identity": canonical_sha256(
            "phase4-statistical-schema", str(table.schema)
        ),
    }


def _describe(values: Iterable[float | None]) -> Json:
    clean = np.asarray(
        [
            float(value)
            for value in values
            if value is not None and math.isfinite(value)
        ],
        dtype=np.float64,
    )
    if clean.size == 0:
        return {"count": 0, "undefined_count": 0}
    lower, upper = np.quantile(clean, (0.25, 0.75), method="linear")
    return {
        "count": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "standard_deviation": float(clean.std(ddof=1)) if clean.size > 1 else None,
        "interquartile_range": float(upper - lower),
        "minimum": float(clean.min()),
        "maximum": float(clean.max()),
    }


def _scenario_summary(rows: Sequence[Json], descriptor: Json, raw: Json) -> Json:
    summaries: list[Json] = []
    metric_names = tuple(
        field.name for field in _SCENARIO_SCHEMA if pa.types.is_floating(field.type)
    )
    groups: dict[tuple[str, str], list[Json]] = defaultdict(list)
    for row in rows:
        groups[(str(row["cohort_role"]), str(row["parameter_identity"]))].append(row)
    for (role, configuration_id), group in sorted(groups.items()):
        summaries.append(
            {
                "cohort_role": role,
                "parameter_identity": configuration_id,
                "method_id": group[0]["method_id"],
                "family": group[0]["family"],
                "scenario_count": len(group),
                "failure_count": sum(int(row["failure_count"]) for row in group),
                "constraint_violation_count": sum(
                    int(row["constraint_violation_count"]) for row in group
                ),
                "metrics": {
                    metric: {
                        **_describe(cast(float | None, row[metric]) for row in group),
                        "undefined_count": sum(row[metric] is None for row in group),
                    }
                    for metric in metric_names
                },
            }
        )
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "independent_statistical_unit": "scenario",
        "nested_units": ["trajectory", "sample", "event"],
        "derived_table": descriptor,
        "raw_input_tables": raw,
        "group_count": len(summaries),
        "groups": summaries,
    }


def _selection_contract(root: Path) -> tuple[list[Json], list[Json]]:
    byte = _read_json(root / "results/phase4/frozen_campaign/matched_byte_results.json")
    keyframe = _read_json(
        root / "results/phase4/frozen_campaign/matched_keyframe_results.json"
    )
    return cast(list[Json], byte["selections"]), cast(
        list[Json], keyframe["selections"]
    )


def _conditions(byte: Sequence[Json], keyframe: Sequence[Json]) -> tuple[Json, ...]:
    result: list[Json] = []
    for name, dimension, target, priority, selections in (
        ("primary_byte", "byte_ratio", 0.48, "primary", byte),
        ("primary_keyframe", "keyframe_ratio", 0.12, "primary", keyframe),
        ("diagnostic_byte", "byte_ratio", 0.71, "diagnostic", byte),
        ("diagnostic_keyframe", "keyframe_ratio", 0.23, "diagnostic", keyframe),
    ):
        for role in ("pilot", "test"):
            selected = [
                row
                for row in selections
                if row["cohort_role"] == role
                and row["priority"] == priority
                and row["dimension"] == dimension
                and float(row["target"]) == target
            ]
            expected_families = (
                _PRIMARY_FAMILIES
                if priority == "primary"
                else ("fixed_interval_linear", "uniform_hermite", "uniform_linear")
            )
            if tuple(sorted(row["family"] for row in selected)) != tuple(
                sorted(expected_families)
            ):
                raise ArtifactError(f"{name} {role} selection family differs")
            result.append(
                {
                    "name": name,
                    "cohort_role": role,
                    "dimension": dimension,
                    "target": target,
                    "priority": priority,
                    "selections": selected,
                }
            )
    return tuple(result)


def _comparison_rows(rows: Sequence[Json], conditions: Sequence[Json]) -> list[Json]:
    by_role_configuration: dict[tuple[str, str], list[Json]] = defaultdict(list)
    for row in rows:
        by_role_configuration[
            (str(row["cohort_role"]), str(row["parameter_identity"]))
        ].append(row)
    cache: dict[tuple[str, str, str, str], Json] = {}
    comparisons: list[Json] = []
    for condition in conditions:
        role = str(condition["cohort_role"])
        selected = cast(list[Json], condition["selections"])
        selections = {str(row["parameter_identity"]): row for row in selected}
        method_ids = tuple(sorted(selections))
        for method_a, method_b in all_method_pairs(method_ids):
            metadata_a = selections[method_a]
            metadata_b = selections[method_b]
            rows_a = by_role_configuration[(role, method_a)]
            rows_b = by_role_configuration[(role, method_b)]
            for metric, lower_is_better, unit, domain in _METRICS:
                cache_key = (role, method_a, method_b, metric)
                if cache_key not in cache:
                    values_a = {
                        str(row["source_scenario_id"]): cast(float | None, row[metric])
                        for row in rows_a
                    }
                    values_b = {
                        str(row["source_scenario_id"]): cast(float | None, row[metric])
                        for row in rows_b
                    }
                    cache[cache_key] = paired_analysis(
                        values_a,
                        values_b,
                        lower_is_better=lower_is_better,
                        resamples=(
                            PILOT_RESAMPLES if role == "pilot" else TEST_RESAMPLES
                        ),
                        seed_components=(role, method_a, method_b, metric),
                    )
                comparisons.append(
                    {
                        "condition": condition["name"],
                        "cohort_role": role,
                        "priority": condition["priority"],
                        "dimension": condition["dimension"],
                        "target": condition["target"],
                        "metric": metric,
                        "metric_domain": domain,
                        "unit": unit,
                        "lower_is_better": lower_is_better,
                        "method_a_parameter_identity": method_a,
                        "method_a_method_id": metadata_a["method_id"],
                        "method_a_family": metadata_a["family"],
                        "method_a_actual_budget": metadata_a["actual_budget"],
                        "method_b_parameter_identity": method_b,
                        "method_b_method_id": metadata_b["method_id"],
                        "method_b_family": metadata_b["family"],
                        "method_b_actual_budget": metadata_b["actual_budget"],
                        **cache[cache_key],
                    }
                )
    for family in {
        (
            str(row["condition"]),
            str(row["cohort_role"]),
            str(row["metric_domain"]),
        )
        for row in comparisons
    }:
        indices = [
            index
            for index, row in enumerate(comparisons)
            if (
                row["condition"],
                row["cohort_role"],
                row["metric_domain"],
            )
            == family
        ]
        adjusted = holm_adjust(
            [float(comparisons[index]["p_value_raw"]) for index in indices]
        )
        for index, value in zip(indices, adjusted, strict=True):
            comparisons[index]["holm_family"] = ":".join(family)
            comparisons[index]["holm_family_size"] = len(indices)
            comparisons[index]["adjusted_p_value"] = value
            comparisons[index]["reject_at_alpha_0_05"] = value <= 0.05
    comparisons.sort(
        key=lambda row: (
            str(row["condition"]),
            str(row["cohort_role"]),
            str(row["metric"]),
            str(row["method_a_parameter_identity"]),
            str(row["method_b_parameter_identity"]),
        )
    )
    return comparisons


def _comparison_payload(
    comparisons: Sequence[Json],
    condition_prefix: str,
) -> Json:
    selected = [
        row for row in comparisons if str(row["condition"]).startswith(condition_prefix)
    ]
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "comparison_count": len(selected),
        "comparisons": selected,
    }


def _event_analysis(rows: Sequence[Json], conditions: Sequence[Json]) -> Json:
    selected_ids = {
        (str(condition["cohort_role"]), str(selection["parameter_identity"]))
        for condition in conditions
        for selection in cast(list[Json], condition["selections"])
    }
    analyses: list[Json] = []
    for role, configuration_id in sorted(selected_ids):
        group = [
            row
            for row in rows
            if row["cohort_role"] == role
            and row["parameter_identity"] == configuration_id
        ]
        for event in _EVENT_TYPES:
            field = f"semantic_{event}_f1"
            values = [cast(float | None, row[field]) for row in group]
            counts = [
                EventCounts(
                    source=int(row[f"semantic_{event}_source_count"]),
                    replay=int(row[f"semantic_{event}_replay_count"]),
                    matched=int(row[f"semantic_{event}_matched_count"]),
                )
                for row in group
            ]
            analyses.append(
                {
                    "cohort_role": role,
                    "parameter_identity": configuration_id,
                    "method_id": group[0]["method_id"],
                    "event_type": event,
                    "main_scenario_macro_present_only": _describe(values),
                    "undefined_scenario_count": sum(value is None for value in values),
                    "source_absent_scenario_count": sum(
                        count.source == 0 for count in counts
                    ),
                    "source_present_undefined_scenario_count": sum(
                        count.source > 0 and value is None
                        for count, value in zip(counts, values, strict=True)
                    ),
                    "source_absent_false_positive_scenario_count": sum(
                        count.source == 0 and count.replay > 0 for count in counts
                    ),
                    "all_scenario_presence_aware_sensitivity": _describe(
                        count.presence_aware_score for count in counts
                    ),
                    "source_absent_sensitivity_policy": (
                        "one when source and replay are absent; zero for false positive"
                    ),
                    "source_absent_sensitivity_is_secondary": True,
                }
            )
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "analysis_count": len(analyses),
        "analyses": analyses,
    }


def _pilot_test_consistency(comparisons: Sequence[Json]) -> Json:
    grouped: dict[tuple[str, str, str, str], dict[str, Json]] = defaultdict(dict)
    for row in comparisons:
        key = (
            str(row["condition"]).replace("primary_", "").replace("diagnostic_", ""),
            str(row["metric"]),
            str(row["method_a_parameter_identity"]),
            str(row["method_b_parameter_identity"]),
        )
        grouped[key][str(row["cohort_role"])] = row
    checks: list[Json] = []
    for key, roles in sorted(grouped.items()):
        if set(roles) != {"pilot", "test"}:
            continue
        pilot = roles["pilot"]
        test = roles["test"]
        pilot_effect = float(pilot["method_b_advantage_mean"])
        test_effect = float(test["method_b_advantage_mean"])
        checks.append(
            {
                "comparison_key": list(key),
                "direction_agrees": (pilot_effect >= 0.0) == (test_effect >= 0.0),
                "pilot_effect": pilot_effect,
                "test_effect": test_effect,
                "absolute_effect_difference": abs(pilot_effect - test_effect),
                "confidence_intervals_overlap": (
                    float(pilot["confidence_interval_lower"])
                    <= float(test["confidence_interval_upper"])
                    and float(test["confidence_interval_lower"])
                    <= float(pilot["confidence_interval_upper"])
                ),
            }
        )
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "pilot_and_test_not_pooled": True,
        "check_count": len(checks),
        "direction_agreement_count": sum(row["direction_agrees"] for row in checks),
        "checks": checks,
    }


def _robustness(
    root: Path,
    rows: Sequence[Json],
    conditions: Sequence[Json],
    class_by_source_agent: Mapping[tuple[str, str], str],
) -> Json:
    selected_ids = {
        (str(condition["cohort_role"]), str(selection["parameter_identity"]))
        for condition in conditions
        for selection in cast(list[Json], condition["selections"])
    }
    city_groups: dict[tuple[str, str, str], list[Json]] = defaultdict(list)
    for row in rows:
        key = (str(row["cohort_role"]), str(row["parameter_identity"]))
        if key in selected_ids:
            city_groups[(key[0], key[1], str(row["city_or_region"]))].append(row)
    city = [
        {
            "cohort_role": key[0],
            "parameter_identity": key[1],
            "city_or_region": key[2],
            "scenario_count": len(group),
            "position_mean_m": _describe(
                cast(float | None, row["position_mean_m"]) for row in group
            ),
            "semantic_overall_f1": _describe(
                cast(float | None, row["semantic_overall_f1"]) for row in group
            ),
        }
        for key, group in sorted(city_groups.items())
    ]
    trajectory: list[Json] = []
    for role in ("pilot", "test"):
        motion_path = (
            root
            / "cache/phase4_frozen_campaign"
            / role
            / "final-v1/trajectory_motion_metrics.parquet"
        )
        event_path = (
            root
            / "cache/phase4_frozen_campaign"
            / role
            / "final-v1/trajectory_event_metrics.parquet"
        )
        source_by_scenario = {
            str(row["scenario_id"]): str(row["source_scenario_id"])
            for row in rows
            if row["cohort_role"] == role
        }
        raw_event_rows = pq.read_table(
            event_path,
            columns=[
                "configuration_id",
                "scenario_id",
                "trajectory_id",
                "event_type",
                "source_event_count",
            ],
        ).to_pylist()
        categories: dict[tuple[str, str], set[str]] = defaultdict(set)
        for event in raw_event_rows:
            if (
                event["configuration_id"]
                != ("8949a585c070e67f704c6bbb305125fe8199e371c05745de82fd60e3a86f406e")
                or int(event["source_event_count"]) == 0
            ):
                continue
            event_type = str(event["event_type"])
            if event_type == "stop":
                category = "stop"
            elif event_type in {"left_turn", "right_turn"}:
                category = "turn"
            elif event_type in {"acceleration", "braking"}:
                category = "longitudinal"
            else:
                continue
            categories[(str(event["scenario_id"]), str(event["trajectory_id"]))].add(
                category
            )
        motion_rows = pq.read_table(
            motion_path,
            columns=[
                "configuration_id",
                "scenario_id",
                "trajectory_id",
                "position_statistics",
                "velocity_statistics",
            ],
        ).to_pylist()
        grouped: dict[tuple[str, str, str], list[tuple[float | None, float | None]]] = (
            defaultdict(list)
        )
        allowed = {
            configuration_id
            for item_role, configuration_id in selected_ids
            if item_role == role
        }
        for motion in motion_rows:
            configuration_id = str(motion["configuration_id"])
            if configuration_id not in allowed:
                continue
            scenario_id = str(motion["scenario_id"])
            trajectory_id = str(motion["trajectory_id"])
            source_id = source_by_scenario[scenario_id]
            source_agent = trajectory_id.rsplit(":", 1)[-1]
            agent_class = class_by_source_agent.get(
                (source_id, source_agent), "unknown"
            )
            present = categories.get((scenario_id, trajectory_id), set())
            motion_category = (
                "no_event"
                if not present
                else next(iter(present))
                if len(present) == 1
                else "mixed"
            )
            position = cast(Json, motion["position_statistics"])
            velocity = cast(Json, motion["velocity_statistics"])
            for kind, stratum in (
                ("agent_class", agent_class),
                ("motion_category", motion_category),
            ):
                grouped[(configuration_id, kind, stratum)].append(
                    (
                        cast(float | None, position["mean"]),
                        cast(float | None, velocity["mean"]),
                    )
                )
        for (configuration_id, kind, stratum), values in sorted(grouped.items()):
            trajectory.append(
                {
                    "cohort_role": role,
                    "parameter_identity": configuration_id,
                    "stratum_kind": kind,
                    "stratum": stratum,
                    "trajectory_count": len(values),
                    "trajectory_macro_position_mean_m": _describe(
                        value[0] for value in values
                    ),
                    "trajectory_macro_velocity_mean_mps": _describe(
                        value[1] for value in values
                    ),
                }
            )
    scenario_macro = [
        {
            "cohort_role": role,
            "parameter_identity": configuration_id,
            "position_mean_m": _describe(
                cast(float | None, row["position_mean_m"])
                for row in rows
                if row["cohort_role"] == role
                and row["parameter_identity"] == configuration_id
            ),
            "velocity_mean_mps": _describe(
                cast(float | None, row["velocity_mean_mps"])
                for row in rows
                if row["cohort_role"] == role
                and row["parameter_identity"] == configuration_id
            ),
        }
        for role, configuration_id in sorted(selected_ids)
    ]
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "inferential_status": "descriptive_only",
        "scenario_macro": scenario_macro,
        "city_strata": city,
        "trajectory_agent_class_and_motion_category_strata": trajectory,
        "missingness_policy": "undefined values retained and counted; no imputation",
        "extreme_value_policy": "all valid extreme observations retained",
    }


def _multiplicity(comparisons: Sequence[Json]) -> Json:
    families: list[Json] = []
    for family in sorted({str(row["holm_family"]) for row in comparisons}):
        group = [row for row in comparisons if row["holm_family"] == family]
        families.append(
            {
                "holm_family": family,
                "hypothesis_count": len(group),
                "raw_significant_count": sum(
                    float(row["p_value_raw"]) <= 0.05 for row in group
                ),
                "adjusted_significant_count": sum(
                    bool(row["reject_at_alpha_0_05"]) for row in group
                ),
            }
        )
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "procedure": "Holm step-down family-wise error correction",
        "alpha": 0.05,
        "family_definition": "condition x cohort role x metric domain",
        "family_count": len(families),
        "families": families,
    }


def _contract() -> Json:
    return {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "starting_head": STARTING_HEAD,
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "metric_identity": FROZEN_METRIC_IDENTITY,
        "validation_identity": FROZEN_VALIDATION_IDENTITY,
        "budget_contract_sha256": FROZEN_BUDGET_CONTRACT_SHA256,
        "independent_statistical_unit": "scenario",
        "nested_observations": ["trajectory", "sample", "event"],
        "pilot_role": "secondary replication and feasibility evidence",
        "test_role": "confirmatory inference",
        "development_outcomes_used": False,
        "pilot_and_test_pooled": False,
        "paired_difference_orientation": "positive favors method B",
        "confidence_interval": {
            "method": "paired percentile bootstrap of scenario differences",
            "confidence_level": 0.95,
            "pilot_resamples": PILOT_RESAMPLES,
            "test_resamples": TEST_RESAMPLES,
            "root_seed": ROOT_SEED,
        },
        "hypothesis_test": {
            "method": "two-sided paired sign-flip permutation",
            "exact_maximum_pair_count": 16,
            "monte_carlo_resamples_by_role": {
                "pilot": PILOT_RESAMPLES,
                "test": TEST_RESAMPLES,
            },
        },
        "effect_sizes": [
            "natural-unit paired mean difference",
            "paired Cohen dz",
            "signed-rank biserial correlation",
        ],
        "practical_magnitude_thresholds_absolute_dz": {
            "negligible": [0.0, 0.2],
            "small": [0.2, 0.5],
            "moderate": [0.5, 0.8],
            "large": [0.8, None],
        },
        "multiplicity": "Holm within condition x cohort role x metric domain",
        "primary_byte_target": 0.48,
        "primary_keyframe_target": 0.12,
        "diagnostic_byte_target": 0.71,
        "diagnostic_keyframe_target": 0.23,
        "required_metrics": [metric[0] for metric in _METRICS],
        "undefined_policy": "retain, count, and omit only from the affected pair",
        "extreme_value_policy": "retain all valid values",
        "analysis_decision": "completed",
    }


def _summary(comparisons: Sequence[Json], failures: int) -> str:
    test_rows = [
        row
        for row in comparisons
        if row["cohort_role"] == "test" and str(row["condition"]).startswith("primary")
    ]
    significant = sum(bool(row["reject_at_alpha_0_05"]) for row in test_rows)
    return (
        "# Phase 4 Confirmatory Statistical Analysis\n\n"
        f"{PASS_STATEMENT}\n\n"
        "The frozen 300-scenario test cohort is the confirmatory evidence base. "
        "The 50-scenario pilot is reported separately as secondary replication "
        "evidence; neither cohort is pooled with development data or with each other.\n\n"
        f"The complete primary test families contain {len(test_rows)} paired metric "
        f"comparisons; {significant} remain significant after their predeclared Holm "
        "correction. Effects are oriented so positive values favor method B. "
        "Statistical significance is not treated as practical importance, and the "
        "full natural-unit effects, confidence intervals, standardized effects, "
        "win/tie/loss counts, and adjusted p-values remain in the JSON evidence.\n\n"
        "Results vary by metric and matched-budget condition, so this batch does not "
        "claim a universal winner or select a final method. Raw and exact references "
        "remain descriptive rather than budget-matched methods. Robustness views by "
        "city, agent class, motion category, and aggregation level are descriptive "
        "and do not create additional confirmatory hypotheses.\n\n"
        f"Recorded analysis failures: {failures}. Undefined event metrics and missing "
        "pairs are retained explicitly rather than imputed.\n"
    )


def _write_json(path: Path, value: Json) -> None:
    path.write_bytes(canonical_json_bytes(value))


def run_statistical_campaign(root: Path) -> Json:
    """Run and persist the complete deterministic Batch 4.7 analysis."""
    for relative in (
        "results/phase4/motion_cohort",
        "results/phase4/motion_metrics",
        "results/phase4/motion_sweep",
        "results/phase4/protocol_freeze",
        "results/phase4/frozen_campaign",
    ):
        _verify_evidence(root, relative)
    frozen = _read_json(root / "results/phase4/frozen_campaign/evidence.json")
    if (
        frozen["campaign_decision"] != "completed"
        or frozen["cohort_identity"] != FROZEN_COHORT_IDENTITY
        or frozen["matrix_identity"] != FROZEN_MATRIX_IDENTITY
        or frozen["failure_count"] != 0
    ):
        raise ArtifactError("Batch 4.6 frozen campaign gate did not pass")
    city, classes = _cohort_metadata(root)
    rows, raw_descriptors = _scenario_aggregates(root, city)
    derived_descriptor = _write_derived_table(root, rows)
    byte, keyframe = _selection_contract(root)
    budget_contract_path = (
        root / "results/phase4/protocol_freeze/final_budget_contract.json"
    )
    if _sha256(budget_contract_path) != FROZEN_BUDGET_CONTRACT_SHA256:
        raise ArtifactError("frozen budget contract differs")
    budget_selection_identity = canonical_sha256(
        "phase4-frozen-budget-selections",
        {"matched_byte": byte, "matched_keyframe": keyframe},
    )
    conditions = _conditions(byte, keyframe)
    comparisons = _comparison_rows(rows, conditions)
    repeat_comparisons = _comparison_rows(rows, conditions)
    first_identity = canonical_sha256("phase4-statistical-comparisons", comparisons)
    repeat_identity = canonical_sha256(
        "phase4-statistical-comparisons", repeat_comparisons
    )
    if first_identity != repeat_identity:
        raise ArtifactError("repeated statistical analysis differs")
    scenario_payload = _scenario_summary(rows, derived_descriptor, raw_descriptors)
    primary_byte = {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "comparison_count": sum(
            row["condition"] == "primary_byte" for row in comparisons
        ),
        "comparisons": [
            row for row in comparisons if row["condition"] == "primary_byte"
        ],
    }
    primary_keyframe = {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "comparison_count": sum(
            row["condition"] == "primary_keyframe" for row in comparisons
        ),
        "comparisons": [
            row for row in comparisons if row["condition"] == "primary_keyframe"
        ],
    }
    diagnostic = _comparison_payload(comparisons, "diagnostic_")
    event_type = _event_analysis(rows, conditions)
    consistency = _pilot_test_consistency(comparisons)
    robustness = _robustness(root, rows, conditions, classes)
    multiplicity = _multiplicity(comparisons)
    failure_report = {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "analysis_failure_count": 0,
        "campaign_failure_count": 0,
        "missing_pair_count": sum(
            int(row["missing_either_count"]) for row in comparisons
        ),
        "undefined_effect_count": sum(
            row["standardized_paired_effect_dz"] is None for row in comparisons
        ),
        "constraint_violation_count": sum(
            int(row["constraint_violation_count"]) for row in rows
        ),
        "failures": [],
        "omitted_failures": False,
    }
    output_root = root / "results/phase4/statistical_analysis"
    if output_root.exists():
        raise ArtifactError("statistical evidence directory already exists")
    output_root.mkdir(parents=True)
    payloads = {
        "statistical_contract.json": _contract(),
        "scenario_aggregates.json": scenario_payload,
        "primary_byte_comparisons.json": primary_byte,
        "primary_keyframe_comparisons.json": primary_keyframe,
        "diagnostic_comparisons.json": diagnostic,
        "event_type_analysis.json": event_type,
        "pilot_test_consistency.json": consistency,
        "robustness_analysis.json": robustness,
        "multiplicity_report.json": multiplicity,
        "failure_report.json": failure_report,
    }
    for name, payload in payloads.items():
        _write_json(output_root / name, payload)
    (output_root / "summary.md").write_text(
        _summary(comparisons, 0),
        encoding="utf-8",
        newline="\n",
    )
    evidence_files = (*payloads, "summary.md")
    evidence = {
        "batch": BATCH,
        "schema_version": SCHEMA_VERSION,
        "analysis_decision": "completed",
        "pass_statement": PASS_STATEMENT,
        "starting_head": STARTING_HEAD,
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "metric_identity": FROZEN_METRIC_IDENTITY,
        "validation_identity": FROZEN_VALIDATION_IDENTITY,
        "budget_contract_sha256": FROZEN_BUDGET_CONTRACT_SHA256,
        "budget_selection_identity": budget_selection_identity,
        "independent_statistical_unit": "scenario",
        "root_seed": ROOT_SEED,
        "pilot_resamples": PILOT_RESAMPLES,
        "test_resamples": TEST_RESAMPLES,
        "environment": {
            "operating_system": "Ubuntu under WSL2",
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
            "gpu_used": False,
        },
        "pilot_scenario_count": 50,
        "test_scenario_count": 300,
        "scenario_aggregate_count": len(rows),
        "primary_byte_comparison_count": primary_byte["comparison_count"],
        "primary_keyframe_comparison_count": primary_keyframe["comparison_count"],
        "diagnostic_comparison_count": diagnostic["comparison_count"],
        "analysis_output_identity": first_identity,
        "repeat_analysis_output_identity": repeat_identity,
        "determinism_verified": first_identity == repeat_identity,
        "derived_scenario_table": derived_descriptor,
        "analysis_failure_count": 0,
        "campaign_failure_count": 0,
        "pilot_and_test_pooled": False,
        "development_outcomes_used": False,
        "raw_and_exact_inferentially_compared": False,
        "final_method_selected": False,
        "evidence_file_sha256": {
            name: _sha256(output_root / name) for name in evidence_files
        },
    }
    _write_json(output_root / "evidence.json", evidence)
    return evidence


__all__ = [
    "BATCH",
    "FROZEN_BUDGET_CONTRACT_SHA256",
    "FROZEN_COHORT_IDENTITY",
    "FROZEN_MATRIX_IDENTITY",
    "FROZEN_METRIC_IDENTITY",
    "FROZEN_VALIDATION_IDENTITY",
    "PASS_STATEMENT",
    "SCHEMA_VERSION",
    "STARTING_HEAD",
    "run_statistical_campaign",
]
