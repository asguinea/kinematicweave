"""Generate deterministic Phase 4 qualitative motion evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
from typing import Any, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import run_directory_name
from kinematicweave.canonical import canonical_json_bytes
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.ablation_analysis import CONFIGURATION_IDS
from kinematicweave.experiments.qualitative_selection import (
    SelectionRule,
    safe_identifier,
    select_by_rule,
    selection_contract,
)
from kinematicweave.visualization.deterministic import (
    BLUE,
    CORAL,
    GOLD,
    GREEN,
    GRID,
    INK,
    MUTED,
    Drawing,
)

BATCH = "4.9"
SCHEMA_VERSION = "1.0"
STARTING_HEAD = "8367a08af54ae922ef05c590bf36f12ec08e163b"
PASS_STATEMENT = (
    "Phase 4 qualitative motion evidence completed on deterministic frozen-test "
    "examples."
)

type Json = dict[str, Any]

_ROOT = Path("results/phase4/qualitative_motion")
_CACHE = Path("cache/phase4_qualitative_motion")
_FROZEN = Path("cache/phase4_frozen_campaign/test/final-v1")
_REPRESENTATIONS = Path("cache/phase4_frozen_campaign/test/representations/runs")
_CHECKPOINTS = Path("cache/phase4_frozen_campaign/test/checkpoint_store/checkpoints-v1")

_CONFIG_LABELS = {
    "raw_samples": "RAW",
    "exact_adjacent": "EXACT",
    "uniform_linear_stride_2": "UNIFORM LINEAR S2",
    "uniform_hermite_stride_2": "UNIFORM HERMITE S2",
    "uniform_linear_stride_5": "UNIFORM LINEAR S5",
    "uniform_hermite_stride_5": "UNIFORM HERMITE S5",
    "uniform_linear_stride_10": "UNIFORM LINEAR S10",
    "uniform_hermite_stride_10": "UNIFORM HERMITE S10",
    "fixed_interval_1000_ms": "FIXED 1000 MS",
    "rdp_linear_0_05_m": "RDP 0.05 M",
    "position_bounded_linear_0_10_m": "POSITION BOUNDED",
    "unconstrained_hermite_0_10_m": "UNCONSTRAINED HERMITE",
    "position_velocity_hybrid_0_10_m_1_00_mps": "POSITION/VELOCITY HYBRID",
}

_METHOD_COLORS = {
    "source": INK,
    "uniform_linear_stride_10": MUTED,
    "rdp_linear_0_05_m": GOLD,
    "position_bounded_linear_0_10_m": BLUE,
    "unconstrained_hermite_0_10_m": CORAL,
    "position_velocity_hybrid_0_10_m_1_00_mps": GREEN,
}


def _read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationError(f"{path} must contain a JSON object")
    return cast(Json, value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(dict(value)) + b"\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_evidence(root: Path, relative: str) -> Json:
    evidence_root = root / relative
    evidence = _read_json(evidence_root / "evidence.json")
    checksums = evidence.get("evidence_file_sha256")
    if not isinstance(checksums, dict):
        raise ArtifactError(f"{relative} evidence checksum map is missing")
    for name, expected in checksums.items():
        path = evidence_root / str(name)
        if not path.is_file() or _sha256(path) != expected:
            raise ArtifactError(f"accepted evidence differs: {path}")
    return evidence


def _input_evidence(root: Path) -> dict[str, str]:
    paths = (
        "results/phase4/motion_cohort",
        "results/phase4/motion_baselines",
        "results/phase4/motion_metrics",
        "results/phase4/motion_sweep",
        "results/phase4/protocol_freeze",
        "results/phase4/frozen_campaign",
        "results/phase4/statistical_analysis",
        "results/phase4/ablation_analysis",
    )
    checksums: dict[str, str] = {}
    for relative in paths:
        _verify_evidence(root, relative)
        evidence_path = root / relative / "evidence.json"
        checksums[evidence_path.relative_to(root).as_posix()] = _sha256(evidence_path)
    return checksums


def _scenario_rows(root: Path) -> list[Json]:
    evidence = _read_json(root / "results/phase4/statistical_analysis/evidence.json")
    descriptor = cast(Json, evidence["derived_scenario_table"])
    path = root / str(descriptor["path"])
    if not path.is_file() or _sha256(path) != descriptor["sha256"]:
        raise ArtifactError("accepted Batch 4.7 scenario aggregate differs")
    rows = [
        cast(Json, row)
        for row in pq.read_table(path).to_pylist()
        if row["cohort_role"] == "test"
    ]
    if len(rows) != 5_400:
        raise ArtifactError("test scenario aggregate must contain 5,400 rows")
    return rows


def _paired_candidates(
    indexed: Mapping[tuple[str, str], Json],
    left: str,
    right: str,
    metric: str,
    output_metric: str,
    *,
    transform: str = "left_minus_right",
) -> list[Json]:
    left_id = CONFIGURATION_IDS[left]
    right_id = CONFIGURATION_IDS[right]
    scenario_ids = sorted(
        scenario
        for configuration, scenario in indexed
        if configuration == left_id and (right_id, scenario) in indexed
    )
    candidates: list[Json] = []
    for source_id in scenario_ids:
        left_value = float(indexed[(left_id, source_id)][metric])
        right_value = float(indexed[(right_id, source_id)][metric])
        if transform == "absolute_sum":
            value = abs(left_value - right_value)
        else:
            value = left_value - right_value
        candidates.append(
            {
                "safe_record_id": safe_identifier("scenario", source_id),
                output_metric: value,
                "_source_scenario_id": source_id,
                "_left": left,
                "_right": right,
                "metrics": {
                    f"{left}.{metric}": left_value,
                    f"{right}.{metric}": right_value,
                    f"{left}.procedural_segment_count": int(
                        indexed[(left_id, source_id)]["procedural_segment_count"]
                    ),
                    f"{right}.procedural_segment_count": int(
                        indexed[(right_id, source_id)]["procedural_segment_count"]
                    ),
                },
            }
        )
    return candidates


def _select_scenarios(rows: Sequence[Json]) -> tuple[list[Json], Json]:
    indexed = {
        (str(row["parameter_identity"]), str(row["source_scenario_id"])): row
        for row in rows
    }
    rules_and_candidates: list[tuple[SelectionRule, list[Json]]] = [
        (
            SelectionRule(
                "adaptive_largest_position_improvement",
                "uniform_minus_adaptive_position_mean_m",
                "descending",
            ),
            _paired_candidates(
                indexed,
                "uniform_linear_stride_10",
                "position_bounded_linear_0_10_m",
                "position_mean_m",
                "uniform_minus_adaptive_position_mean_m",
            ),
        ),
        (
            SelectionRule(
                "adaptive_median_position_improvement",
                "uniform_minus_adaptive_position_mean_m",
                "descending",
                "median",
            ),
            _paired_candidates(
                indexed,
                "uniform_linear_stride_10",
                "position_bounded_linear_0_10_m",
                "position_mean_m",
                "uniform_minus_adaptive_position_mean_m",
            ),
        ),
        (
            SelectionRule(
                "adaptive_smallest_or_adverse_improvement",
                "uniform_minus_adaptive_position_mean_m",
                "descending",
                "smallest",
            ),
            _paired_candidates(
                indexed,
                "uniform_linear_stride_10",
                "position_bounded_linear_0_10_m",
                "position_mean_m",
                "uniform_minus_adaptive_position_mean_m",
            ),
        ),
        (
            SelectionRule(
                "rdp_largest_temporal_failure",
                "rdp_minus_bounded_velocity_maximum_mps",
                "descending",
            ),
            _paired_candidates(
                indexed,
                "rdp_linear_0_05_m",
                "position_bounded_linear_0_10_m",
                "velocity_maximum_mps",
                "rdp_minus_bounded_velocity_maximum_mps",
            ),
        ),
        (
            SelectionRule(
                "rdp_median_temporal_case",
                "rdp_minus_bounded_velocity_maximum_mps",
                "descending",
                "median",
            ),
            _paired_candidates(
                indexed,
                "rdp_linear_0_05_m",
                "position_bounded_linear_0_10_m",
                "velocity_maximum_mps",
                "rdp_minus_bounded_velocity_maximum_mps",
            ),
        ),
        (
            SelectionRule(
                "hermite_largest_velocity_inflation",
                "hermite_minus_bounded_velocity_maximum_mps",
                "descending",
            ),
            _paired_candidates(
                indexed,
                "unconstrained_hermite_0_10_m",
                "position_bounded_linear_0_10_m",
                "velocity_maximum_mps",
                "hermite_minus_bounded_velocity_maximum_mps",
            ),
        ),
        (
            SelectionRule(
                "hermite_largest_semantic_loss",
                "bounded_minus_hermite_semantic_f1",
                "descending",
            ),
            _paired_candidates(
                indexed,
                "position_bounded_linear_0_10_m",
                "unconstrained_hermite_0_10_m",
                "semantic_overall_f1",
                "bounded_minus_hermite_semantic_f1",
            ),
        ),
        (
            SelectionRule(
                "hybrid_largest_velocity_recovery",
                "hermite_minus_hybrid_velocity_maximum_mps",
                "descending",
            ),
            _paired_candidates(
                indexed,
                "unconstrained_hermite_0_10_m",
                "position_velocity_hybrid_0_10_m_1_00_mps",
                "velocity_maximum_mps",
                "hermite_minus_hybrid_velocity_maximum_mps",
            ),
        ),
    ]
    event_candidates: list[Json] = []
    hermite_id = CONFIGURATION_IDS["unconstrained_hermite_0_10_m"]
    hybrid_id = CONFIGURATION_IDS["position_velocity_hybrid_0_10_m_1_00_mps"]
    for source_id in sorted(
        scenario for configuration, scenario in indexed if configuration == hermite_id
    ):
        hermite = indexed[(hermite_id, source_id)]
        hybrid = indexed[(hybrid_id, source_id)]
        accel = float(hybrid["semantic_acceleration_f1"] or 0.0) - float(
            hermite["semantic_acceleration_f1"] or 0.0
        )
        braking = float(hybrid["semantic_braking_f1"] or 0.0) - float(
            hermite["semantic_braking_f1"] or 0.0
        )
        event_candidates.append(
            {
                "safe_record_id": safe_identifier("scenario", source_id),
                "hybrid_minus_hermite_event_f1": max(accel, braking),
                "_source_scenario_id": source_id,
                "_left": "unconstrained_hermite_0_10_m",
                "_right": "position_velocity_hybrid_0_10_m_1_00_mps",
                "metrics": {
                    "acceleration_f1_recovery": accel,
                    "braking_f1_recovery": braking,
                    "unconstrained_hermite.procedural_segment_count": int(
                        hermite["procedural_segment_count"]
                    ),
                    "position_velocity_hybrid.procedural_segment_count": int(
                        hybrid["procedural_segment_count"]
                    ),
                },
            }
        )
    rules_and_candidates.append(
        (
            SelectionRule(
                "hybrid_largest_acceleration_or_braking_recovery",
                "hybrid_minus_hermite_event_f1",
                "descending",
            ),
            event_candidates,
        )
    )
    semantic_tradeoff_candidates = []
    for source_id in sorted(
        scenario for configuration, scenario in indexed if configuration == hermite_id
    ):
        hermite = indexed[(hermite_id, source_id)]
        hybrid = indexed[(hybrid_id, source_id)]
        semantic_tradeoff_candidates.append(
            {
                "safe_record_id": safe_identifier("scenario", source_id),
                "hermite_minus_hybrid_semantic_f1": float(
                    hermite["semantic_overall_f1"]
                )
                - float(hybrid["semantic_overall_f1"]),
                "_source_scenario_id": source_id,
                "_left": "unconstrained_hermite_0_10_m",
                "_right": "position_velocity_hybrid_0_10_m_1_00_mps",
                "metrics": {
                    "unconstrained_hermite.semantic_overall_f1": hermite[
                        "semantic_overall_f1"
                    ],
                    "position_velocity_hybrid.semantic_overall_f1": hybrid[
                        "semantic_overall_f1"
                    ],
                    "unconstrained_hermite.procedural_segment_count": int(
                        hermite["procedural_segment_count"]
                    ),
                    "position_velocity_hybrid.procedural_segment_count": int(
                        hybrid["procedural_segment_count"]
                    ),
                },
            }
        )
    rules_and_candidates.append(
        (
            SelectionRule(
                "hybrid_largest_semantic_tradeoff",
                "hermite_minus_hybrid_semantic_f1",
                "descending",
            ),
            semantic_tradeoff_candidates,
        )
    )
    little_candidates: list[Json] = []
    for source_id in sorted(
        scenario for configuration, scenario in indexed if configuration == hermite_id
    ):
        hermite = indexed[(hermite_id, source_id)]
        hybrid = indexed[(hybrid_id, source_id)]
        advantages = (
            float(hermite["position_mean_m"]) - float(hybrid["position_mean_m"]),
            float(hermite["velocity_mean_mps"]) - float(hybrid["velocity_mean_mps"]),
            float(hybrid["semantic_overall_f1"])
            - float(hermite["semantic_overall_f1"]),
        )
        little_candidates.append(
            {
                "safe_record_id": safe_identifier("scenario", source_id),
                "hybrid_total_advantage": sum(abs(value) for value in advantages),
                "_source_scenario_id": source_id,
                "_left": "unconstrained_hermite_0_10_m",
                "_right": "position_velocity_hybrid_0_10_m_1_00_mps",
                "metrics": {
                    "position_advantage_m": advantages[0],
                    "velocity_advantage_mps": advantages[1],
                    "semantic_advantage_f1": advantages[2],
                    "unconstrained_hermite.procedural_segment_count": int(
                        hermite["procedural_segment_count"]
                    ),
                    "position_velocity_hybrid.procedural_segment_count": int(
                        hybrid["procedural_segment_count"]
                    ),
                },
            }
        )
    rules_and_candidates.append(
        (
            SelectionRule(
                "hybrid_little_advantage",
                "hybrid_total_advantage",
                "ascending",
            ),
            little_candidates,
        )
    )
    selected = [
        select_by_rule(candidates, rule) for rule, candidates in rules_and_candidates
    ]

    bounded_id = CONFIGURATION_IDS["position_bounded_linear_0_10_m"]
    event_fields = {
        "stop": "semantic_stop",
        "turn": "semantic_turn",
        "acceleration": "semantic_acceleration",
        "braking": "semantic_braking",
    }
    bounded_rows = [row for row in rows if str(row["parameter_identity"]) == bounded_id]
    for event, prefix in event_fields.items():
        candidates = [
            {
                "safe_record_id": safe_identifier(
                    "scenario", str(row["source_scenario_id"])
                ),
                f"{prefix}_f1": float(row[f"{prefix}_f1"] or 0.0),
                "_source_scenario_id": str(row["source_scenario_id"]),
                "_left": "source",
                "_right": "position_bounded_linear_0_10_m",
                "metrics": {
                    "source_event_count": int(row[f"{prefix}_source_count"]),
                    "replay_event_count": int(row[f"{prefix}_replay_count"]),
                    "f1": row[f"{prefix}_f1"],
                    "position_bounded.procedural_segment_count": int(
                        row["procedural_segment_count"]
                    ),
                },
            }
            for row in bounded_rows
            if int(row[f"{prefix}_source_count"]) > 0
        ]
        selected.append(
            select_by_rule(
                candidates,
                SelectionRule(
                    f"semantic_{event}",
                    f"{prefix}_f1",
                    "ascending",
                ),
            )
        )
    overlap_candidates = []
    for row in bounded_rows:
        present = sum(
            int(row[f"{prefix}_source_count"]) > 0 for prefix in event_fields.values()
        )
        if present >= 2:
            overlap_candidates.append(
                {
                    "safe_record_id": safe_identifier(
                        "scenario", str(row["source_scenario_id"])
                    ),
                    "semantic_overall_f1": float(row["semantic_overall_f1"]),
                    "_source_scenario_id": str(row["source_scenario_id"]),
                    "_left": "source",
                    "_right": "position_bounded_linear_0_10_m",
                    "metrics": {
                        "present_event_type_count": present,
                        "semantic_overall_f1": row["semantic_overall_f1"],
                        "position_bounded.procedural_segment_count": int(
                            row["procedural_segment_count"]
                        ),
                    },
                }
            )
    selected.append(
        select_by_rule(
            overlap_candidates,
            SelectionRule(
                "semantic_overlapping_events",
                "semantic_overall_f1",
                "ascending",
            ),
        )
    )
    for record in selected:
        record["configuration_comparison"] = [
            record["_left"],
            record["_right"],
        ]
    private = {
        record["category"]: {
            "source_scenario_id": record.pop("_source_scenario_id"),
            "left_configuration": record.pop("_left"),
            "right_configuration": record.pop("_right"),
        }
        for record in selected
    }
    return selected, private


def _checkpoint_roots(root: Path) -> dict[str, Path]:
    mapped: dict[str, Path] = {}
    for directory in sorted((root / _CHECKPOINTS).iterdir()):
        sample = next(directory.glob("*.json"), None)
        if sample is None:
            continue
        data = _read_json(sample)
        execution = cast(Json, cast(Json, data["payload"])["execution"])
        mapped[str(execution["parameter_identity"])] = directory
    if len(mapped) != 18:
        raise ArtifactError("frozen checkpoint configuration map is incomplete")
    return mapped


def _run_path(
    root: Path,
    checkpoint_roots: Mapping[str, Path],
    configuration_id: str,
    source_scenario_id: str,
) -> Path:
    checkpoint = checkpoint_roots[configuration_id] / f"{source_scenario_id}.json"
    data = _read_json(checkpoint)
    execution = cast(Json, cast(Json, data["payload"])["execution"])
    summary = cast(Json, execution["artifact_summary"])
    runs = cast(list[Json], summary["runs"])
    run_id = str(runs[0]["run_id"])
    path = root / _REPRESENTATIONS / run_directory_name(run_id)
    if not path.is_dir():
        raise ArtifactError("frozen representation run is missing")
    return path


def _trajectory_classes(
    root: Path,
    checkpoint_roots: Mapping[str, Path],
    scenario_ids: Iterable[str],
) -> dict[str, str]:
    configuration = CONFIGURATION_IDS["position_bounded_linear_0_10_m"]
    classes: dict[str, str] = {}
    for source_id in sorted(scenario_ids):
        run = _run_path(root, checkpoint_roots, configuration, source_id)
        rows = pq.read_table(
            run / "procedural_tracks.parquet",
            columns=["trajectory_id", "agent_class"],
        ).to_pylist()
        classes.update(
            {str(row["trajectory_id"]): str(row["agent_class"]) for row in rows}
        )
    return classes


def _trajectory_metrics(root: Path, configuration: str) -> list[Json]:
    path = root / _FROZEN / "trajectory_motion_metrics.parquet"
    table = pq.read_table(
        path,
        filters=[("configuration_id", "=", CONFIGURATION_IDS[configuration])],
    )
    return [cast(Json, row) for row in table.to_pylist()]


def _add_agent_examples(
    root: Path,
    selected: list[Json],
    private: Json,
    checkpoint_roots: Mapping[str, Path],
) -> None:
    rows = _trajectory_metrics(root, "position_bounded_linear_0_10_m")
    scenarios = {str(row["scenario_id"]).rsplit(":", 1)[-1] for row in rows}
    classes = _trajectory_classes(root, checkpoint_roots, scenarios)
    for agent_class in ("vehicle", "pedestrian", "cyclist"):
        candidates = [
            row for row in rows if classes.get(str(row["trajectory_id"])) == agent_class
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda row: (
                -float(cast(Json, row["position_statistics"])["maximum"]),
                safe_identifier("trajectory", str(row["trajectory_id"])),
            )
        )
        row = candidates[0]
        safe_trajectory = safe_identifier("trajectory", str(row["trajectory_id"]))
        safe_scenario = safe_identifier("scenario", str(row["scenario_id"]))
        category = f"agent_{agent_class}"
        selected.append(
            {
                "category": category,
                "safe_record_id": safe_trajectory,
                "safe_scenario_id": safe_scenario,
                "selection_metric": "position maximum error",
                "selection_direction": "descending",
                "selection_quantile": "largest",
                "tie_break_rule": "safe_record_id ascending",
                "rank": 1,
                "candidate_count": len(candidates),
                "metrics": {
                    "agent_class": agent_class,
                    "position_maximum_m": cast(Json, row["position_statistics"])[
                        "maximum"
                    ],
                    "segment_count": int(row["segment_count"]),
                    "hold_count": int(row["hold_count"]),
                    "linear_count": int(row["linear_count"]),
                    "hermite_count": int(row["hermite_count"]),
                },
                "configuration_comparison": [
                    "source",
                    "position_bounded_linear_0_10_m",
                ],
            }
        )
        source_scenario = str(row["scenario_id"]).rsplit(":", 1)[-1]
        private[category] = {
            "source_scenario_id": source_scenario,
            "source_trajectory_id": str(row["trajectory_id"]),
            "left_configuration": "source",
            "right_configuration": "position_bounded_linear_0_10_m",
        }


def _add_robustness_examples(
    root: Path,
    rows: Sequence[Json],
    selected: list[Json],
    private: Json,
) -> None:
    robustness = _read_json(
        root / "results/phase4/ablation_analysis/robustness_analysis.json"
    )
    disagreements = [
        row
        for row in cast(list[Json], robustness["city_records"])
        if row["cohort_role"] == "test" and not row["direction_agrees_with_overall"]
    ]
    disagreements.sort(
        key=lambda row: (
            str(row["contrast_family"]),
            str(row["metric"]),
            -abs(float(row["method_b_advantage_mean"])),
            str(row["city_or_region"]),
        )
    )
    hermite_id = CONFIGURATION_IDS["unconstrained_hermite_0_10_m"]
    hybrid_id = CONFIGURATION_IDS["position_velocity_hybrid_0_10_m_1_00_mps"]
    indexed = {
        (str(row["parameter_identity"]), str(row["source_scenario_id"])): row
        for row in rows
    }
    for index, disagreement in enumerate(disagreements[:2], start=1):
        city = str(disagreement["city_or_region"])
        metric = str(disagreement["metric"])
        candidates: list[tuple[float, str]] = []
        for (configuration, source_id), row in indexed.items():
            if configuration != hermite_id or str(row["city_or_region"]) != city:
                continue
            effect = float(row[metric]) - float(indexed[(hybrid_id, source_id)][metric])
            candidates.append((effect, source_id))
        candidates.sort(
            key=lambda item: (item[0], safe_identifier("scenario", item[1]))
        )
        effect, source_id = candidates[0]
        category = f"robustness_city_exception_{index}"
        selected.append(
            {
                "category": category,
                "safe_record_id": safe_identifier("scenario", source_id),
                "selection_metric": metric,
                "selection_direction": "ascending",
                "selection_quantile": "smallest",
                "tie_break_rule": "safe_record_id ascending",
                "rank": 1,
                "candidate_count": len(candidates),
                "metrics": {
                    "stratum_kind": "city",
                    "stratum": city,
                    "contrast_family": "velocity_constraint",
                    "scenario_method_b_advantage": effect,
                    "stratum_method_b_advantage_mean": disagreement[
                        "method_b_advantage_mean"
                    ],
                    "direction_agrees_with_aggregate": False,
                    "unconstrained_hermite.procedural_segment_count": int(
                        indexed[(hermite_id, source_id)]["procedural_segment_count"]
                    ),
                    "position_velocity_hybrid.procedural_segment_count": int(
                        indexed[(hybrid_id, source_id)]["procedural_segment_count"]
                    ),
                },
                "configuration_comparison": [
                    "unconstrained_hermite_0_10_m",
                    "position_velocity_hybrid_0_10_m_1_00_mps",
                ],
            }
        )
        private[category] = {
            "source_scenario_id": source_id,
            "left_configuration": "unconstrained_hermite_0_10_m",
            "right_configuration": "position_velocity_hybrid_0_10_m_1_00_mps",
        }


def _choose_trajectory(
    root: Path,
    private: Json,
    category: str,
    configuration: str,
    metric: str,
) -> str:
    existing = cast(Json, private[category]).get("source_trajectory_id")
    if existing is not None:
        return str(existing)
    source_id = str(cast(Json, private[category])["source_scenario_id"])
    scenario_id = f"scenario:av2:{source_id}"
    rows = [
        row
        for row in _trajectory_metrics(root, configuration)
        if row["scenario_id"] == scenario_id
    ]
    ranked = sorted(
        rows,
        key=lambda row: (
            -float(cast(Json, row[metric])["maximum"]),
            safe_identifier("trajectory", str(row["trajectory_id"])),
        ),
    )
    trajectory_id = str(ranked[0]["trajectory_id"])
    cast(Json, private[category])["source_trajectory_id"] = trajectory_id
    return trajectory_id


def _raw_trace(
    root: Path,
    checkpoint_roots: Mapping[str, Path],
    source_scenario_id: str,
    trajectory_id: str,
) -> list[Json]:
    run = _run_path(
        root,
        checkpoint_roots,
        CONFIGURATION_IDS["raw_samples"],
        source_scenario_id,
    )
    rows = pq.read_table(
        run / "trajectory_samples.parquet",
        filters=[("trajectory_id", "=", trajectory_id)],
    ).to_pylist()
    return [
        cast(Json, row)
        for row in rows
        if row["is_valid"] and row["x_m"] is not None and row["y_m"] is not None
    ]


def _procedural_trace(
    root: Path,
    checkpoint_roots: Mapping[str, Path],
    source_scenario_id: str,
    trajectory_id: str,
    configuration: str,
    timestamps: Sequence[int],
) -> tuple[list[tuple[float, float, float]], list[Json]]:
    run = _run_path(
        root,
        checkpoint_roots,
        CONFIGURATION_IDS[configuration],
        source_scenario_id,
    )
    tracks = pq.read_table(
        run / "procedural_tracks.parquet",
        filters=[("trajectory_id", "=", trajectory_id)],
    ).to_pylist()
    if len(tracks) != 1:
        raise ArtifactError("selected procedural trajectory is not unique")
    procedural_track_id = tracks[0]["procedural_track_id"]
    segments = [
        cast(Json, row)
        for row in pq.read_table(
            run / "procedural_segments.parquet",
            filters=[("procedural_track_id", "=", procedural_track_id)],
        ).to_pylist()
    ]
    segments.sort(key=lambda row: (int(row["run_index"]), int(row["segment_index"])))
    values: list[tuple[float, float, float]] = []
    segment_index = 0
    for timestamp in timestamps:
        while segment_index + 1 < len(segments) and timestamp > int(
            segments[segment_index]["end_time_ns"]
        ):
            segment_index += 1
        segment = segments[segment_index]
        start_time = int(segment["start_time_ns"])
        end_time = int(segment["end_time_ns"])
        duration_ns = max(end_time - start_time, 1)
        u = min(max((timestamp - start_time) / duration_ns, 0.0), 1.0)
        x0, y0 = float(segment["start_x_m"]), float(segment["start_y_m"])
        x1, y1 = float(segment["end_x_m"]), float(segment["end_y_m"])
        vx0 = float(segment["start_velocity_x_mps"] or 0.0)
        vy0 = float(segment["start_velocity_y_mps"] or 0.0)
        vx1 = float(segment["end_velocity_x_mps"] or 0.0)
        vy1 = float(segment["end_velocity_y_mps"] or 0.0)
        duration_s = duration_ns / 1_000_000_000.0
        if segment["primitive_type"] == "cubic_hermite":
            h00 = 2 * u**3 - 3 * u**2 + 1
            h10 = u**3 - 2 * u**2 + u
            h01 = -2 * u**3 + 3 * u**2
            h11 = u**3 - u**2
            x = h00 * x0 + h10 * duration_s * vx0 + h01 * x1 + h11 * duration_s * vx1
            y = h00 * y0 + h10 * duration_s * vy0 + h01 * y1 + h11 * duration_s * vy1
            dh00 = 6 * u**2 - 6 * u
            dh10 = 3 * u**2 - 4 * u + 1
            dh01 = -6 * u**2 + 6 * u
            dh11 = 3 * u**2 - 2 * u
            vx = (dh00 * x0 + dh01 * x1) / duration_s + dh10 * vx0 + dh11 * vx1
            vy = (dh00 * y0 + dh01 * y1) / duration_s + dh10 * vy0 + dh11 * vy1
        else:
            x = x0 + u * (x1 - x0)
            y = y0 + u * (y1 - y0)
            vx = (x1 - x0) / duration_s
            vy = (y1 - y0) / duration_s
        values.append((x, y, math.hypot(vx, vy)))
    return values, segments


def _scale_points(
    points: Sequence[tuple[float, float]],
    box: tuple[float, float, float, float],
) -> list[tuple[float, float]]:
    x, y, width, height = box
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_span = max(x_max - x_min, 1e-9)
    y_span = max(y_max - y_min, 1e-9)
    return [
        (
            x + (point[0] - x_min) / x_span * width,
            y + height - (point[1] - y_min) / y_span * height,
        )
        for point in points
    ]


def _axes(drawing: Drawing, box: tuple[int, int, int, int], title: str) -> None:
    x, y, width, height = box
    drawing.text(x, y - 18, title, size=20)
    for step in range(5):
        yy = y + step * height / 4
        drawing.line(x, yy, x + width, yy, GRID, 1)
    drawing.line(x, y, x, y + height, INK, 2)
    drawing.line(x, y + height, x + width, y + height, INK, 2)


def _series_points(
    values: Sequence[float],
    box: tuple[int, int, int, int],
    *,
    maximum: float | None = None,
) -> list[tuple[float, float]]:
    x, y, width, height = box
    limit = maximum if maximum is not None else max(max(values), 1e-9)
    return [
        (
            x + index / max(len(values) - 1, 1) * width,
            y + height - value / max(limit, 1e-9) * height,
        )
        for index, value in enumerate(values)
    ]


def _save_figure(
    drawing: Drawing,
    figures: Path,
    stem: str,
    title: str,
    manifest: list[Json],
    *,
    source: str,
    safe_examples: Sequence[str] = (),
) -> None:
    svg = figures / f"{stem}.svg"
    png = figures / f"{stem}.png"
    drawing.save_svg(svg, title=title)
    drawing.save_png(png)
    manifest.append(
        {
            "figure_id": stem,
            "title": title,
            "source_data": source,
            "safe_example_ids": list(safe_examples),
            "dimensions_px": [drawing.width, drawing.height],
            "outputs": [
                {
                    "path": (_ROOT / "figures" / svg.name).as_posix(),
                    "format": "svg",
                    "sha256": _sha256(svg),
                    "size_bytes": svg.stat().st_size,
                },
                {
                    "path": (_ROOT / "figures" / png.name).as_posix(),
                    "format": "png",
                    "sha256": _sha256(png),
                    "size_bytes": png.stat().st_size,
                },
            ],
        }
    )


def _aggregate_figures(
    root: Path,
    figures: Path,
    figure_records: list[Json],
) -> None:
    rows = [
        cast(Json, row)
        for row in pq.read_table(
            root / _FROZEN / "configuration_results.parquet"
        ).to_pylist()
    ]
    named = {
        name: next(row for row in rows if row["parameter_identity"] == identity)
        for name, identity in CONFIGURATION_IDS.items()
    }
    principal = [
        "fixed_interval_1000_ms",
        "uniform_linear_stride_10",
        "rdp_linear_0_05_m",
        "position_bounded_linear_0_10_m",
        "unconstrained_hermite_0_10_m",
        "position_velocity_hybrid_0_10_m_1_00_mps",
    ]
    drawing = Drawing()
    drawing.text(70, 70, "MATCHED-BUDGET OVERVIEW", size=34)
    metrics = (
        ("byte_ratio", "STORAGE RATIO", False),
        ("position_mean_m", "MEAN POSITION ERROR (M)", False),
        ("velocity_mean_mps", "MEAN VELOCITY ERROR (M/S)", False),
        ("semantic_micro_f1", "SEMANTIC F1", True),
    )
    for panel, (metric, title, _) in enumerate(metrics):
        x = 70 + (panel % 2) * 770
        y = 150 + (panel // 2) * 400
        _axes(drawing, (x, y, 680, 280), title)
        values = [float(named[name][metric]) for name in principal]
        maximum = max(values) * 1.12 or 1.0
        for index, (name, value) in enumerate(zip(principal, values, strict=True)):
            xx = x + 55 + index * 105
            yy = y + 280 - value / maximum * 245
            color = _METHOD_COLORS.get(name, MUTED)
            drawing.circle(xx, yy, 10, color, INK)
            drawing.text(xx, y + 315, str(index + 1), color, 16, "middle")
    for index, name in enumerate(principal, start=1):
        legend_index = index - 1
        drawing.text(
            70 + (legend_index // 3) * 770,
            900 + (legend_index % 3) * 28,
            (
                f"{index} "
                + (
                    "BASELINE - "
                    if index <= 3
                    else "TAPE ABLATION - "
                    if index == 5
                    else "TAPE MAIN - "
                )
                + _CONFIG_LABELS[name]
            ),
            size=13,
        )
    _save_figure(
        drawing,
        figures,
        "01_matched_budget_overview",
        "Matched-budget overview",
        figure_records,
        source="frozen Batch 4.6 test configuration results",
    )

    drawing = Drawing()
    drawing.text(70, 70, "ERROR-STORAGE TRADEOFF", size=34)
    drawing.text(70, 110, "X = ACTUAL SERIALIZED-BYTE RATIO", MUTED, 18)
    trade_metrics = (
        ("position_mean_m", "MEAN POSITION ERROR (M)"),
        ("position_p95_m", "P95 POSITION ERROR (M)"),
        ("velocity_mean_mps", "MEAN VELOCITY ERROR (M/S)"),
        ("semantic_micro_f1", "SEMANTIC F1"),
    )
    highlighted = {
        CONFIGURATION_IDS["position_bounded_linear_0_10_m"]: BLUE,
        CONFIGURATION_IDS["position_velocity_hybrid_0_10_m_1_00_mps"]: GREEN,
        CONFIGURATION_IDS["unconstrained_hermite_0_10_m"]: CORAL,
        CONFIGURATION_IDS["rdp_linear_0_05_m"]: GOLD,
    }
    for panel, (metric, title) in enumerate(trade_metrics):
        x = 70 + (panel % 2) * 770
        y = 150 + (panel // 2) * 400
        _axes(drawing, (x, y, 680, 280), title)
        max_x = max(float(row["byte_ratio"]) for row in rows)
        values = [float(row[metric]) for row in rows]
        min_y, max_y = min(values), max(values)
        for row in rows:
            point_x = x + float(row["byte_ratio"]) / max_x * 680
            point_y = (
                y + 280 - (float(row[metric]) - min_y) / max(max_y - min_y, 1e-9) * 260
            )
            color = highlighted.get(str(row["parameter_identity"]), MUTED)
            drawing.circle(point_x, point_y, 9 if color != MUTED else 5, color)
    trade_legend = (
        ("POSITION BOUNDED", BLUE),
        ("POSITION/VELOCITY HYBRID", GREEN),
        ("UNCONSTRAINED HERMITE", CORAL),
        ("RDP 0.05 M", GOLD),
    )
    for index, (label, color) in enumerate(trade_legend):
        legend_x = 70 + (index % 2) * 770
        legend_y = 900 + (index // 2) * 35
        drawing.circle(legend_x, legend_y - 6, 7, color)
        drawing.text(legend_x + 22, legend_y, label, size=14)
    _save_figure(
        drawing,
        figures,
        "02_error_storage_tradeoff",
        "Error-storage tradeoff",
        figure_records,
        source="all 18 frozen Batch 4.6 test configurations",
    )


def _trace_bundle(
    root: Path,
    checkpoint_roots: Mapping[str, Path],
    private: Json,
    category: str,
    trajectory_configuration: str,
) -> Json:
    source_id = str(cast(Json, private[category])["source_scenario_id"])
    trajectory_id = _choose_trajectory(
        root,
        private,
        category,
        trajectory_configuration,
        "position_statistics",
    )
    raw = _raw_trace(root, checkpoint_roots, source_id, trajectory_id)
    timestamps = [int(row["timestamp_ns"]) for row in raw]
    methods: Json = {}
    event_metrics_path = root / _FROZEN / "trajectory_event_metrics.parquet"
    events: Json = {}
    for configuration in (
        "uniform_linear_stride_10",
        "rdp_linear_0_05_m",
        "position_bounded_linear_0_10_m",
        "unconstrained_hermite_0_10_m",
        "position_velocity_hybrid_0_10_m_1_00_mps",
    ):
        values, segments = _procedural_trace(
            root,
            checkpoint_roots,
            source_id,
            trajectory_id,
            configuration,
            timestamps,
        )
        methods[configuration] = {"values": values, "segments": segments}
        event_rows = pq.read_table(
            event_metrics_path,
            filters=[
                ("configuration_id", "=", CONFIGURATION_IDS[configuration]),
                ("trajectory_id", "=", trajectory_id),
            ],
        ).to_pylist()
        events[configuration] = {
            str(row["event_type"]): {
                "source_count": int(row["source_event_count"]),
                "replay_count": int(row["replay_event_count"]),
                "matched_count": int(row["matched_event_count"]),
                "f1": row["f1"],
            }
            for row in event_rows
            if row["event_type"]
            in {"stop", "left_turn", "right_turn", "acceleration", "braking"}
        }
    return {
        "raw": raw,
        "timestamps": timestamps,
        "methods": methods,
        "events": events,
        "safe_trajectory_id": safe_identifier("trajectory", trajectory_id),
    }


def _trace_figure(
    bundle: Json,
    figures: Path,
    figure_records: list[Json],
    *,
    stem: str,
    title: str,
    methods: Sequence[str],
    secondary: str,
    source: str,
) -> None:
    drawing = Drawing()
    drawing.text(70, 70, title, size=32)
    raw = cast(list[Json], bundle["raw"])
    all_points = [(float(row["x_m"]), float(row["y_m"])) for row in raw]
    for method in methods:
        all_points.extend(
            (float(value[0]), float(value[1]))
            for value in cast(Json, cast(Json, bundle["methods"])[method])["values"]
        )
    scaled_all = _scale_points(all_points, (90, 150, 650, 650))
    offset = 0
    source_points = scaled_all[: len(raw)]
    offset += len(raw)
    drawing.polyline(source_points, INK, 5)
    for method in methods:
        count = len(cast(Json, cast(Json, bundle["methods"])[method])["values"])
        points = scaled_all[offset : offset + count]
        offset += count
        drawing.polyline(points, _METHOD_COLORS[method], 4)
        segments = cast(
            list[Json], cast(Json, cast(Json, bundle["methods"])[method])["segments"]
        )
        retained = {int(segment["source_start_sample_index"]) for segment in segments}
        retained.add(int(segments[-1]["source_end_sample_index"]))
        for index in sorted(retained):
            if 0 <= index < len(points):
                drawing.circle(*points[index], 4, _METHOD_COLORS[method])
    drawing.text(90, 845, f"SOURCE  SAFE ID {bundle['safe_trajectory_id']}", size=18)
    for index, method in enumerate(methods):
        drawing.line(
            850, 145 + index * 42, 910, 145 + index * 42, _METHOD_COLORS[method], 5
        )
        drawing.text(930, 153 + index * 42, _CONFIG_LABELS[method], size=18)
    box = (850, 390, 650, 350)
    _axes(drawing, box, secondary)
    source_speeds = [float(row["speed_mps"] or 0.0) for row in raw]
    maximum = float(max(source_speeds, default=1e-9))
    if secondary.startswith("SPEED"):
        drawing.polyline(_series_points(source_speeds, box, maximum=maximum), INK, 4)
        for method in methods:
            speeds = [
                float(value[2])
                for value in cast(Json, cast(Json, bundle["methods"])[method])["values"]
            ]
            maximum = max(maximum, max(speeds))
        drawing.polyline(_series_points(source_speeds, box, maximum=maximum), INK, 4)
        for method in methods:
            speeds = [
                float(value[2])
                for value in cast(Json, cast(Json, bundle["methods"])[method])["values"]
            ]
            drawing.polyline(
                _series_points(speeds, box, maximum=maximum),
                _METHOD_COLORS[method],
                3,
            )
    elif secondary.startswith("VELOCITY"):
        source_speeds = [float(row["speed_mps"] or 0.0) for row in raw]
        velocity_errors: dict[str, list[float]] = {}
        maximum = 1e-9
        for method in methods:
            speeds = [
                float(value[2])
                for value in cast(Json, cast(Json, bundle["methods"])[method])["values"]
            ]
            series = [
                abs(replay - source)
                for replay, source in zip(speeds, source_speeds, strict=True)
            ]
            velocity_errors[method] = series
            maximum = max(maximum, max(series))
        for method in methods:
            drawing.polyline(
                _series_points(velocity_errors[method], box, maximum=maximum),
                _METHOD_COLORS[method],
                3,
            )
        start_time = int(raw[0]["timestamp_ns"])
        duration = max(int(raw[-1]["timestamp_ns"]) - start_time, 1)
        primitive_colors = {
            "linear": BLUE,
            "cubic_hermite": CORAL,
            "hold": GOLD,
        }
        for method_index, method in enumerate(methods):
            strip_y = 785 + method_index * 34
            drawing.text(850, strip_y, _CONFIG_LABELS[method][:18], size=11)
            segments = cast(
                list[Json],
                cast(Json, cast(Json, bundle["methods"])[method])["segments"],
            )
            for segment in segments:
                start = (int(segment["start_time_ns"]) - start_time) / duration
                end = (int(segment["end_time_ns"]) - start_time) / duration
                drawing.rect(
                    1070 + max(start, 0.0) * 410,
                    strip_y - 12,
                    max((end - start) * 410, 2),
                    12,
                    primitive_colors[str(segment["primitive_type"])],
                )
            event_rows = cast(Json, cast(Json, bundle["events"])[method])
            source_count = sum(
                int(cast(Json, event)["source_count"]) for event in event_rows.values()
            )
            matched_count = sum(
                int(cast(Json, event)["matched_count"]) for event in event_rows.values()
            )
            drawing.text(
                850,
                875 + method_index * 25,
                f"EVENT MATCH {matched_count}/{source_count}",
                size=11,
            )
        drawing.text(
            1070,
            875,
            "PRIMITIVES: LINEAR BLUE  HERMITE CORAL  HOLD GOLD",
            size=10,
        )
    else:
        source_xy = [(float(row["x_m"]), float(row["y_m"])) for row in raw]
        errors: dict[str, list[float]] = {}
        maximum = 1e-9
        for method in methods:
            values = cast(Json, cast(Json, bundle["methods"])[method])["values"]
            series = [
                math.hypot(float(value[0]) - point[0], float(value[1]) - point[1])
                for value, point in zip(values, source_xy, strict=True)
            ]
            errors[method] = series
            maximum = max(maximum, max(series))
        for method in methods:
            drawing.polyline(
                _series_points(errors[method], box, maximum=maximum),
                _METHOD_COLORS[method],
                3,
            )
    _save_figure(
        drawing,
        figures,
        stem,
        title,
        figure_records,
        source=source,
        safe_examples=[str(bundle["safe_trajectory_id"])],
    )


def _semantic_figure(
    root: Path,
    figures: Path,
    figure_records: list[Json],
    selected: Sequence[Json],
) -> None:
    categories = (
        "semantic_stop",
        "semantic_turn",
        "semantic_acceleration",
        "semantic_braking",
    )
    records = {str(row["category"]): row for row in selected}
    drawing = Drawing()
    drawing.text(70, 70, "SEMANTIC-EVENT PRESERVATION", size=34)
    drawing.text(
        70,
        110,
        "POSITION-BOUNDED REPLAY - NORMALIZED EVENT ORDER AND CORRESPONDENCE",
        MUTED,
        18,
    )
    colors = (BLUE, GOLD, GREEN, CORAL)
    for event_index, category in enumerate(categories):
        record = records[category]
        metrics = cast(Json, record["metrics"])
        y = 210 + event_index * 180
        drawing.text(70, y, category.removeprefix("semantic_"), size=22)
        drawing.line(300, y, 1450, y, GRID, 8)
        source_count = max(int(metrics["source_event_count"]), 1)
        replay_count = int(metrics["replay_event_count"])
        for index in range(source_count):
            x = 320 + index / max(source_count - 1, 1) * 1090
            drawing.circle(x, y - 22, 7, INK)
        for index in range(replay_count):
            x = 320 + index / max(replay_count - 1, 1) * 1090
            drawing.circle(x, y + 24, 7, colors[event_index])
        drawing.text(
            300,
            y + 75,
            f"SOURCE {source_count}  REPLAY {replay_count}  F1 {float(metrics['f1'] or 0.0):.3f}",
            size=18,
        )
    _save_figure(
        drawing,
        figures,
        "07_semantic_event_preservation",
        "Semantic-event preservation",
        figure_records,
        source="frozen Batch 4.6 source/replay event correspondence",
        safe_examples=[
            str(records[category]["safe_record_id"]) for category in categories
        ],
    )


def _gallery_figure(
    figures: Path,
    figure_records: list[Json],
    selected: Sequence[Json],
) -> None:
    categories = (
        "adaptive_smallest_or_adverse_improvement",
        "hermite_largest_semantic_loss",
        "hybrid_little_advantage",
        "robustness_city_exception_1",
        "robustness_city_exception_2",
        "agent_cyclist",
    )
    indexed = {str(row["category"]): row for row in selected}
    drawing = Drawing()
    drawing.text(70, 70, "FAILURE AND EXCEPTION GALLERY", size=34)
    safe_ids: list[str] = []
    for index, category in enumerate(categories):
        if category not in indexed:
            continue
        row = indexed[category]
        safe_ids.append(str(row["safe_record_id"]))
        x = 70 + (index % 3) * 510
        y = 150 + (index // 3) * 390
        drawing.rect(x, y, 450, 320, (248, 250, 252), GRID, 2)
        words = category.replace("_", " ").upper()
        drawing.text(x + 25, y + 45, words[:31], size=17)
        drawing.text(x + 25, y + 82, words[31:62], size=17)
        metrics = cast(Json, row["metrics"])
        numeric = [
            (key, float(value))
            for key, value in metrics.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ][:4]
        maximum = max((abs(value) for _, value in numeric), default=1.0)
        for metric_index, (name, value) in enumerate(numeric):
            yy = y + 130 + metric_index * 45
            width = abs(value) / max(maximum, 1e-9) * 105
            drawing.rect(x + 240, yy - 17, width, 22, CORAL if value < 0 else BLUE)
            drawing.text(x + 20, yy, name[:18], size=13)
            drawing.text(x + 420, yy, f"{value:.3f}", size=13, anchor="end")
        drawing.text(x + 25, y + 300, str(row["safe_record_id"]), MUTED, 14)
    _save_figure(
        drawing,
        figures,
        "08_failure_exception_gallery",
        "Failure and exception gallery",
        figure_records,
        source="deterministically selected frozen test failures and exceptions",
        safe_examples=safe_ids,
    )


def _replay_frames(
    root: Path,
    output_root: Path,
    bundle: Json,
) -> Json:
    frame_root = root / _CACHE / "replay_frames"
    frame_root.mkdir(parents=True, exist_ok=True)
    raw = cast(list[Json], bundle["raw"])
    methods = (
        "uniform_linear_stride_10",
        "rdp_linear_0_05_m",
        "position_bounded_linear_0_10_m",
        "unconstrained_hermite_0_10_m",
        "position_velocity_hybrid_0_10_m_1_00_mps",
    )
    all_xy = [(float(row["x_m"]), float(row["y_m"])) for row in raw]
    for method in methods:
        all_xy.extend(
            (float(value[0]), float(value[1]))
            for value in cast(Json, cast(Json, bundle["methods"])[method])["values"]
        )
    scaled = _scale_points(all_xy, (60, 100, 850, 560))
    source_scaled = scaled[: len(raw)]
    offset = len(raw)
    method_scaled: dict[str, list[tuple[float, float]]] = {}
    for method in methods:
        count = len(raw)
        method_scaled[method] = scaled[offset : offset + count]
        offset += count
    frame_count = min(36, len(raw))
    indices = [
        round(index * (len(raw) - 1) / max(frame_count - 1, 1))
        for index in range(frame_count)
    ]
    frame_records = []
    for frame_number, sample_index in enumerate(indices):
        drawing = Drawing(1280, 720)
        drawing.text(45, 55, "SYNCHRONIZED MOTION REPLAY", size=26)
        drawing.polyline(source_scaled, INK, 4)
        drawing.circle(*source_scaled[sample_index], 10, INK)
        for method_index, method in enumerate(methods):
            drawing.polyline(method_scaled[method], _METHOD_COLORS[method], 2)
            drawing.circle(
                *method_scaled[method][sample_index], 8, _METHOD_COLORS[method]
            )
            y = 150 + method_index * 78
            drawing.text(950, y, _CONFIG_LABELS[method][:24], size=15)
            source_value = raw[sample_index]
            method_value = cast(Json, cast(Json, bundle["methods"])[method])["values"][
                sample_index
            ]
            physical_error = math.hypot(
                float(source_value["x_m"]) - float(method_value[0]),
                float(source_value["y_m"]) - float(method_value[1]),
            )
            drawing.text(950, y + 30, f"ERROR {physical_error:.3f} M", size=13)
            drawing.rect(
                950,
                y + 42,
                min(physical_error * 120, 240),
                12,
                _METHOD_COLORS[method],
            )
        progress = sample_index / max(len(raw) - 1, 1)
        drawing.line(60, 680, 910, 680, GRID, 10)
        drawing.circle(60 + progress * 850, 680, 12, BLUE)
        drawing.text(950, 570, "EVENT OVERLAY: FROZEN CORRESPONDENCE", size=11)
        drawing.text(950, 610, f"FRAME {frame_number + 1}/{frame_count}", size=18)
        drawing.text(950, 650, f"NORMALIZED TIME {progress:.3f}", size=18)
        path = frame_root / f"frame_{frame_number:04d}.png"
        drawing.save_png(path)
        frame_records.append(
            {
                "frame": frame_number,
                "sample_index": sample_index,
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    preview_source = frame_root / f"frame_{frame_count // 2:04d}.png"
    preview = output_root / "figures/replay_preview.png"
    shutil.copyfile(preview_source, preview)
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "sequence_id": "phase4-qualitative-replay-v1",
        "safe_trajectory_id": bundle["safe_trajectory_id"],
        "comparison_order": ["source", *methods],
        "frame_rate": 12,
        "frame_count": frame_count,
        "dimensions_px": [1280, 720],
        "frame_pattern": "cache/phase4_qualitative_motion/replay_frames/frame_%04d.png",
        "frames": frame_records,
        "encoder": {
            "available": False,
            "version": None,
            "reason": "No deterministic system encoder was available.",
            "assembly_command": (
                "ffmpeg -framerate 12 -i frame_%04d.png -c:v libx264 "
                "-pix_fmt yuv420p qualitative_motion.mp4"
            ),
            "video_sha256": None,
        },
        "ordering_verified": [row["frame"] for row in frame_records]
        == list(range(frame_count)),
        "frame_checksums_verified": all(
            _sha256(root / str(row["path"])) == row["sha256"] for row in frame_records
        ),
        "representative_preview": {
            "path": preview.relative_to(root).as_posix(),
            "sha256": _sha256(preview),
            "size_bytes": preview.stat().st_size,
        },
    }


def _failure_analysis(selected: Sequence[Json]) -> Json:
    mechanism = {
        "adaptive": (
            "Retained temporal breakpoints alter where interpolation error is "
            "spent; the recorded paired position errors support the direction."
        ),
        "rdp": (
            "Geometric path simplification does not constrain time-indexed "
            "progress, consistent with the recorded velocity-error increase."
        ),
        "hermite": (
            "Endpoint derivatives produce curved temporal dynamics between "
            "retained points, consistent with velocity or semantic error."
        ),
        "hybrid": (
            "The velocity-bound refinement inserts segments until the recorded "
            "motion guarantee is met; semantic effects remain separately reported."
        ),
        "semantic": (
            "Frozen event correspondence records show unmatched or shifted replay "
            "events; no unrecorded causal mechanism is asserted."
        ),
        "robustness": (
            "The accepted subgroup direction differs from the aggregate direction; "
            "this qualifies rather than overturns the overall result."
        ),
        "agent": (
            "The selected class-specific trajectory has the largest recorded "
            "position maximum within its qualifying class."
        ),
    }
    cases = []
    for row in selected:
        category = str(row["category"])
        key = next(
            (prefix for prefix in mechanism if category.startswith(prefix)), "semantic"
        )
        cases.append(
            {
                "category": category,
                "safe_record_id": row["safe_record_id"],
                "rank": row["rank"],
                "candidate_count": row["candidate_count"],
                "selection_rule": {
                    "metric": row["selection_metric"],
                    "direction": row["selection_direction"],
                    "quantile": row["selection_quantile"],
                    "tie_break": row["tie_break_rule"],
                },
                "metrics_and_event_counts": row["metrics"],
                "primitive_composition": {
                    "configuration_comparison": row["configuration_comparison"],
                    "recorded_segment_counts": {
                        key: value
                        for key, value in cast(Json, row["metrics"]).items()
                        if "segment_count" in key or key.endswith("_count")
                    },
                    "vocabulary": {
                        "uniform_linear": ["linear", "hold"],
                        "rdp_linear": ["linear", "hold"],
                        "position_bounded_linear": ["linear", "hold"],
                        "unconstrained_hermite": ["cubic_hermite", "hold"],
                        "position_velocity_hybrid": [
                            "linear",
                            "cubic_hermite",
                            "hold",
                        ],
                    },
                },
                "likely_mechanism": mechanism[key],
                "aggregate_interpretation": (
                    "qualifies"
                    if "smallest" in category
                    or "little_advantage" in category
                    or "exception" in category
                    else "confirms"
                ),
                "evidence_qualification": (
                    "descriptive trajectory/scenario evidence; no new inference"
                ),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "analysis_scope": "frozen 300-scenario test cohort",
        "categories": [
            "worst position errors",
            "worst velocity errors",
            "lowest semantic F1",
            "Hermite-specific failures",
            "RDP temporal failures",
            "adaptive little-benefit cases",
            "hybrid semantic/guarantee tradeoffs",
            "city and agent-class exceptions",
        ],
        "case_count": len(cases),
        "cases": cases,
    }


def _summary(selected: Sequence[Json], figure_records: Sequence[Json]) -> str:
    return f"""# Phase 4 Qualitative Motion Evidence

## Scope

This evidence uses the frozen 300-scenario Batch 4.6 test cohort and the accepted
Batch 4.7-4.8 statistical and ablation artifacts. It does not rerun a codec,
change a threshold, rematch a budget, redetect an event, or alter an inferential
procedure. The {len(selected)} published examples were selected by fixed
machine-readable ranks before rendering.

## Visual interpretation

Adaptive segmentation changes where retained timestamps fall. The selected
overlays show position-bounded linear replay spending keyframes around temporal
motion changes, while uniform spacing can spend the same broad resource class
on low-change intervals. The adverse and median cases remain visible, so this
is not a favorable-only gallery.

RDP can trace a spatially plausible route while placing an agent incorrectly in
time because its geometric simplification objective does not bound
time-indexed interpolation error. The spatial path and synchronized error trace
therefore answer different questions.

Unconstrained Hermite can keep positions visually close while endpoint
derivatives create distorted speed, acceleration, braking, or event timing
between keyframes. The velocity-constrained hybrid repairs the largest recorded
velocity inflation by adding refinement where the frozen bound requires it.
That stronger guarantee can cost storage and can preserve semantics differently;
the evidence reports those outcomes separately.

The two principal KinematicWeave codecs have different operating roles.
Position-bounded linear is the direct temporal position-fidelity method.
The position/velocity hybrid is the stronger dynamics-control method when
velocity fidelity or a worst-case guarantee matters. Neither is declared a
universal winner.

## Failures and exceptions

The evidence includes smallest/adverse adaptive improvement, little hybrid
advantage, low semantic preservation, class coverage, and two accepted
opposite-direction city strata. These cases qualify the aggregate conclusions
without changing the frozen confirmatory analysis. Mechanism statements are
limited to recorded paths, primitives, errors, and event correspondence.

## Outputs

Eight release figures are committed in SVG and PNG form. A deterministic
{len(figure_records)}-figure manifest records labels, source artifacts, safe
example identifiers, sizes, and checksums. The additional replay compares
source, uniform linear, RDP, position-bounded linear, unconstrained Hermite, and
the hybrid over synchronized time. Because no system encoder was available, the
verified numbered PNG frames remain ignored while their assembly manifest and a
representative preview are committed.

## Decision

`qualitative_decision = "completed"`

{PASS_STATEMENT}
"""


def run_qualitative_motion_campaign(repository_root: Path) -> Json:
    """Generate and verify all Batch 4.9 qualitative artifacts."""
    root = repository_root.resolve(strict=True)
    upstream = _input_evidence(root)
    output_root = root / _ROOT
    figures = output_root / "figures"
    output_root.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    (root / _CACHE).mkdir(parents=True, exist_ok=True)

    rows = _scenario_rows(root)
    selected, private = _select_scenarios(rows)
    checkpoint_roots = _checkpoint_roots(root)
    _add_agent_examples(root, selected, private, checkpoint_roots)
    _add_robustness_examples(root, rows, selected, private)
    selected.sort(key=lambda row: str(row["category"]))

    for row in selected:
        row.setdefault("safe_scenario_id", row["safe_record_id"])
    _write_json(root / _CACHE / "private_selection.json", private)
    _write_json(output_root / "selection_contract.json", selection_contract())
    _write_json(
        output_root / "selected_examples.json",
        {
            "schema_version": SCHEMA_VERSION,
            "batch": BATCH,
            "cohort_role": "test",
            "scenario_count": 300,
            "manual_cherry_pick": False,
            "exact_identifiers_published": False,
            "example_count": len(selected),
            "examples": selected,
        },
    )

    figure_records: list[Json] = []
    _aggregate_figures(root, figures, figure_records)
    adaptive = _trace_bundle(
        root,
        checkpoint_roots,
        private,
        "adaptive_largest_position_improvement",
        "position_bounded_linear_0_10_m",
    )
    _trace_figure(
        adaptive,
        figures,
        figure_records,
        stem="03_adaptive_breakpoint_example",
        title="ADAPTIVE BREAKPOINT EXAMPLE",
        methods=(
            "uniform_linear_stride_10",
            "position_bounded_linear_0_10_m",
        ),
        secondary="POSITION ERROR THROUGH TIME",
        source="frozen source samples, procedural segments, and replay metrics",
    )
    rdp = _trace_bundle(
        root,
        checkpoint_roots,
        private,
        "rdp_largest_temporal_failure",
        "rdp_linear_0_05_m",
    )
    _trace_figure(
        rdp,
        figures,
        figure_records,
        stem="04_temporal_vs_geometric",
        title="TEMPORAL VERSUS GEOMETRIC SIMPLIFICATION",
        methods=(
            "rdp_linear_0_05_m",
            "position_bounded_linear_0_10_m",
        ),
        secondary="TIME-INDEXED POSITION ERROR",
        source="frozen source samples, RDP segments, and bounded segments",
    )
    hermite = _trace_bundle(
        root,
        checkpoint_roots,
        private,
        "hermite_largest_velocity_inflation",
        "unconstrained_hermite_0_10_m",
    )
    _trace_figure(
        hermite,
        figures,
        figure_records,
        stem="05_hermite_dynamics_failure",
        title="HERMITE DYNAMICS FAILURE",
        methods=(
            "uniform_linear_stride_10",
            "unconstrained_hermite_0_10_m",
            "position_velocity_hybrid_0_10_m_1_00_mps",
        ),
        secondary="SPEED THROUGH TIME",
        source="frozen source samples and stored linear/Hermite primitives",
    )
    _trace_figure(
        hermite,
        figures,
        figure_records,
        stem="06_velocity_constraint_recovery",
        title="VELOCITY-CONSTRAINT RECOVERY",
        methods=(
            "unconstrained_hermite_0_10_m",
            "position_velocity_hybrid_0_10_m_1_00_mps",
        ),
        secondary="VELOCITY ERROR AND SEGMENT RESPONSE",
        source="frozen Hermite and velocity-constrained hybrid primitives",
    )
    _semantic_figure(root, figures, figure_records, selected)
    _gallery_figure(figures, figure_records, selected)

    replay = _replay_frames(root, output_root, hermite)
    _write_json(output_root / "replay_sequence_manifest.json", replay)
    _write_json(output_root / "failure_analysis.json", _failure_analysis(selected))
    _write_json(
        output_root / "figure_manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "batch": BATCH,
            "visual_language": {
                "source": "charcoal",
                "uniform": "gray",
                "RDP": "gold",
                "position_bounded": "blue",
                "unconstrained_Hermite": "coral",
                "position_velocity_hybrid": "green",
            },
            "figure_count": len(figure_records),
            "figures": figure_records,
        },
    )
    summary = _summary(selected, figure_records)
    (output_root / "summary.md").write_text(summary, encoding="utf-8", newline="\n")

    evidence_files = (
        "selection_contract.json",
        "selected_examples.json",
        "failure_analysis.json",
        "figure_manifest.json",
        "replay_sequence_manifest.json",
        "summary.md",
    )
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "starting_head": STARTING_HEAD,
        "qualitative_decision": "completed",
        "pass_statement": PASS_STATEMENT,
        "cohort_role": "test",
        "scenario_count": 300,
        "selected_example_count": len(selected),
        "figure_count": len(figure_records),
        "vector_figure_count": len(figure_records),
        "raster_figure_count": len(figure_records),
        "codec_execution_performed": False,
        "metric_or_event_recomputation_performed": False,
        "manual_cherry_pick": False,
        "private_identifiers_published": False,
        "gpu_used": False,
        "environment": {
            "operating_system": "Ubuntu under WSL2",
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "renderer": "kinematicweave deterministic SVG/PNG renderer v1",
        },
        "upstream_evidence_sha256": upstream,
        "evidence_file_sha256": {
            name: _sha256(output_root / name) for name in evidence_files
        },
        "figure_manifest_sha256": _sha256(output_root / "figure_manifest.json"),
        "replay_manifest_sha256": _sha256(
            output_root / "replay_sequence_manifest.json"
        ),
    }
    _write_json(output_root / "evidence.json", evidence)
    return evidence


__all__ = [
    "BATCH",
    "PASS_STATEMENT",
    "SCHEMA_VERSION",
    "STARTING_HEAD",
    "run_qualitative_motion_campaign",
]
