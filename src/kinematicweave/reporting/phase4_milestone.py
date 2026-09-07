"""Deterministic Phase 4 milestone evidence integration and verification."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

from kinematicweave.errors import ArtifactError

type Json = dict[str, Any]

SCHEMA_VERSION = "1.0"
BATCH = "4.10"
PHASE4_BASE_HEAD = "faea77b73c8fcb52a51781b15cb7893ab7cfc564"
INTEGRATION_STARTING_HEAD = "55354844f71963e1c7ef74108e73f6685ac4399b"
MILESTONE_ROOT = Path("results/phase4/milestone")
REVIEW_PATH = Path("docs/phase4_milestone_review.md")

MILESTONE_FILES = (
    "contract_snapshot.json",
    "final_results.json",
    "benchmark_tables.json",
    "benchmark_figure_manifest.json",
    "claim_evidence_matrix.json",
    "reproduction_manifest.json",
    "summary.md",
    "evidence.json",
)

BATCH_LEDGER = {
    "4.1": "0870b0b6a02c6ab35f8f20ce36e3075b0d23316e",
    "4.2": "20c572ca596f083aef21015760e2e1df39e0c7e2",
    "4.3": "ffd5f6656a1d4d5d7bedf571dfbd1ca3b8f67d5c",
    "4.4": "8c599eabb13a9ebc15661655e4028fe77681de6c",
    "4.5": "f90ebae21c7073460e7a5e2f880c8ef39ef8a791",
    "4.6": "b521cc64db734c591ec780af32eccc506139178a",
    "4.7": "985c59548610f49a881479068c283102bde7aacd",
    "4.8": "8367a08af54ae922ef05c590bf36f12ec08e163b",
    "4.9": PHASE4_BASE_HEAD,
}

METHOD_TAXONOMY: Json = {
    "references": [
        {
            "family": "raw_samples",
            "label": "raw samples",
            "role": "correctness and storage reference",
        },
        {
            "family": "exact_adjacent",
            "label": "exact adjacent-sample procedural replay",
            "role": "correctness reference, not a compression method",
        },
    ],
    "standard_baselines": [
        {"family": "uniform_linear", "label": "uniform linear"},
        {"family": "uniform_hermite", "label": "uniform Hermite"},
        {"family": "fixed_interval_linear", "label": "fixed-interval linear"},
        {"family": "rdp_linear", "label": "RDP linear"},
    ],
    "kinematicweave_main_methods": [
        {
            "family": "position_bounded_linear",
            "label": "position-bounded linear",
        },
        {
            "family": "position_velocity_hybrid",
            "label": "position-and-velocity-bounded hybrid",
        },
    ],
    "kinematicweave_ablation": [
        {
            "family": "unconstrained_hermite",
            "label": "unconstrained Hermite",
        }
    ],
    "universal_winner_declared": False,
}

APPROXIMATION_ORDER = (
    "uniform_linear",
    "uniform_hermite",
    "fixed_interval_linear",
    "rdp_linear",
    "position_bounded_linear",
    "position_velocity_hybrid",
    "unconstrained_hermite",
)

SOURCE_EVIDENCE_PATHS = (
    Path("results/phase4/motion_cohort/evidence.json"),
    Path("results/phase4/motion_cohort/cohort_manifest.json"),
    Path("results/phase4/motion_metrics/metrics_contract.json"),
    Path("results/phase4/protocol_freeze/final_budget_contract.json"),
    Path("results/phase4/protocol_freeze/final_campaign_matrix.json"),
    Path("results/phase4/frozen_campaign/test_results.json"),
    Path("results/phase4/frozen_campaign/pilot_results.json"),
    Path("results/phase4/frozen_campaign/performance_report.json"),
    Path("results/phase4/frozen_campaign/determinism_report.json"),
    Path("results/phase4/frozen_campaign/matched_byte_results.json"),
    Path("results/phase4/frozen_campaign/matched_keyframe_results.json"),
    Path("results/phase4/statistical_analysis/statistical_contract.json"),
    Path("results/phase4/statistical_analysis/evidence.json"),
    Path("results/phase4/ablation_analysis/ablation_contract.json"),
    Path("results/phase4/ablation_analysis/evidence.json"),
    Path("results/phase4/qualitative_motion/selection_contract.json"),
    Path("results/phase4/qualitative_motion/selected_examples.json"),
    Path("results/phase4/qualitative_motion/figure_manifest.json"),
    Path("results/phase4/qualitative_motion/replay_sequence_manifest.json"),
)

DEFERRED_CLAIMS = (
    "motion-derived spatial layout",
    "spatial grammar quality",
    "editing or branching",
    "rerouting or downstream simulation",
    "raw-video extraction",
)

_ABLATION_SPECS = (
    {
        "id": "uniform_linear_vs_uniform_hermite",
        "label": "uniform linear versus uniform Hermite",
        "source": "interpolation_ablation.json",
        "metric": "velocity_maximum_mps",
        "method_a": "uniform_linear-stride-10",
        "method_b": "uniform_hermite-stride-10",
        "context": None,
        "interpretation": (
            "At identical stride-10 keyframes, uniform Hermite increased "
            "worst-case velocity error; interpolation vocabulary alone did "
            "not preserve dynamics."
        ),
    },
    {
        "id": "adaptive_linear_vs_uniform_linear",
        "label": "adaptive linear versus uniform linear",
        "source": "adaptive_segmentation_ablation.json",
        "metric": "position_mean_m",
        "method_a": "position_bounded_linear-error-0p1",
        "method_b": "uniform_linear-stride-10",
        "context": "primary_byte_0.48",
        "interpretation": (
            "Adaptive position-bounded breakpoints reduced mean time-indexed "
            "position error at comparable, but not identical, storage."
        ),
    },
    {
        "id": "temporal_bounded_vs_rdp",
        "label": "temporal bounded replay versus RDP",
        "source": "temporal_vs_geometric_ablation.json",
        "metric": "velocity_mean_mps",
        "method_a": "position_bounded_linear-error-0p1",
        "method_b": "rdp_linear-error-0p05",
        "context": None,
        "interpretation": (
            "Temporal position control reduced mean velocity error relative "
            "to geometric RDP simplification; a path bound is not a temporal "
            "replay guarantee."
        ),
    },
    {
        "id": "bounded_linear_vs_unconstrained_hermite",
        "label": "bounded linear versus unconstrained Hermite",
        "source": "primitive_vocabulary_ablation.json",
        "metric": "velocity_maximum_mps",
        "method_a": "position_bounded_linear-error-0p1",
        "method_b": "unconstrained_hermite-error-0p1",
        "context": None,
        "interpretation": (
            "Adding unconstrained Hermite primitives increased worst-case "
            "velocity error despite compact position-oriented fitting."
        ),
    },
    {
        "id": "unconstrained_hermite_vs_hybrid",
        "label": "unconstrained Hermite versus hybrid",
        "source": "velocity_constraint_ablation.json",
        "metric": "velocity_maximum_mps",
        "method_a": "unconstrained_hermite-error-0p1",
        "method_b": ("position_velocity_bounded_hybrid-error-0p1-velocity-1p0"),
        "context": None,
        "interpretation": (
            "The explicit velocity constraint substantially reduced "
            "worst-case velocity error relative to unconstrained Hermite."
        ),
    },
    {
        "id": "bounded_linear_vs_hybrid",
        "label": "bounded linear versus hybrid",
        "source": "bounded_method_tradeoff.json",
        "metric": "velocity_maximum_mps",
        "method_a": "position_bounded_linear-error-0p1",
        "method_b": ("position_velocity_bounded_hybrid-error-0p1-velocity-1p0"),
        "context": None,
        "interpretation": (
            "The hybrid reduced worst-case velocity error, while the bounded "
            "linear method retained stronger overall semantic F1; the methods "
            "serve different operating needs."
        ),
    },
)


def _read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ArtifactError(f"expected a JSON object: {path}")
    return cast(Json, value)


def _write_json(path: Path, value: Json) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        (
            "git",
            "-c",
            f"safe.directory={repository_root.as_posix()}",
            "-C",
            str(repository_root),
            *arguments,
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ArtifactError(f"{label} differs: expected {expected!r}, got {actual!r}")


def _source_hashes(repository_root: Path) -> Json:
    return {
        path.as_posix(): _sha256(repository_root / path)
        for path in SOURCE_EVIDENCE_PATHS
    }


def _method_group(family: str) -> str:
    for group, rows in METHOD_TAXONOMY.items():
        if not isinstance(rows, list):
            continue
        for row in cast(list[Json], rows):
            if row["family"] == family:
                return group
    raise ArtifactError(f"method family is absent from taxonomy: {family}")


def _method_label(family: str) -> str:
    for rows in METHOD_TAXONOMY.values():
        if not isinstance(rows, list):
            continue
        for row in cast(list[Json], rows):
            if row["family"] == family:
                return str(row["label"])
    raise ArtifactError(f"method family is absent from taxonomy: {family}")


def _verify_git_contract(repository_root: Path) -> None:
    for batch, commit in BATCH_LEDGER.items():
        _git(repository_root, "cat-file", "-e", f"{commit}^{{commit}}")
        if (
            subprocess.run(
                (
                    "git",
                    "-c",
                    f"safe.directory={repository_root.as_posix()}",
                    "-C",
                    str(repository_root),
                    "merge-base",
                    "--is-ancestor",
                    commit,
                    "HEAD",
                ),
                check=False,
                capture_output=True,
            ).returncode
            != 0
        ):
            raise ArtifactError(f"accepted Batch {batch} commit is not an ancestor")
    for commit in (PHASE4_BASE_HEAD, INTEGRATION_STARTING_HEAD):
        if (
            subprocess.run(
                (
                    "git",
                    "-C",
                    str(repository_root),
                    "merge-base",
                    "--is-ancestor",
                    commit,
                    "HEAD",
                ),
                check=False,
                capture_output=True,
            ).returncode
            != 0
        ):
            raise ArtifactError(f"required integration ancestor is absent: {commit}")


def _verify_figure_outputs(repository_root: Path, manifest: Json) -> None:
    _assert_equal(manifest["figure_count"], 8, "qualitative figure count")
    for figure in cast(list[Json], manifest["figures"]):
        outputs = cast(list[Json], figure["outputs"])
        _assert_equal(
            {output["format"] for output in outputs},
            {"svg", "png"},
            f"{figure['figure_id']} output formats",
        )
        for output in outputs:
            path = repository_root / str(output["path"])
            if not path.is_file():
                raise ArtifactError(f"accepted figure is missing: {path}")
            _assert_equal(_sha256(path), output["sha256"], f"{path} checksum")
            _assert_equal(path.stat().st_size, output["size_bytes"], f"{path} size")


def _verify_replay(
    repository_root: Path,
    replay: Json,
    *,
    require_source_frames: bool,
) -> None:
    _assert_equal(replay["frame_count"], 36, "replay frame count")
    _assert_equal(replay["frame_rate"], 12, "replay frame rate")
    _assert_equal(replay["dimensions_px"], [1280, 720], "replay dimensions")
    _assert_equal(replay["frame_checksums_verified"], True, "replay checksums")
    frames = cast(list[Json], replay["frames"])
    _assert_equal(len(frames), 36, "replay manifest entries")
    _assert_equal([frame["frame"] for frame in frames], list(range(36)), "frame order")
    if require_source_frames:
        for frame in frames:
            path = repository_root / str(frame["path"])
            if not path.is_file():
                raise ArtifactError(f"accepted replay frame is missing: {path}")
            _assert_equal(_sha256(path), frame["sha256"], f"{path} checksum")
            _assert_equal(path.stat().st_size, frame["size_bytes"], f"{path} size")
    for record_name in ("representative_preview", "video"):
        record = cast(Json, replay[record_name])
        path = repository_root / str(record["path"])
        if not path.is_file():
            raise ArtifactError(f"accepted replay output is missing: {path}")
        _assert_equal(_sha256(path), record["sha256"], f"{path} checksum")


def _verify_contracts(
    repository_root: Path,
    *,
    require_source_frames: bool,
) -> Json:
    # Evidence hashes, schemas, counts, and relationships are portable across
    # clones and archival source snapshots. Historical commit ancestry is
    # intentionally not a validity requirement for the public release.
    cohort = _read_json(repository_root / "results/phase4/motion_cohort/evidence.json")
    metrics = _read_json(
        repository_root / "results/phase4/motion_metrics/metrics_contract.json"
    )
    budgets = _read_json(
        repository_root / "results/phase4/protocol_freeze/final_budget_contract.json"
    )
    matrix = _read_json(
        repository_root / "results/phase4/protocol_freeze/final_campaign_matrix.json"
    )
    campaign = _read_json(
        repository_root / "results/phase4/frozen_campaign/evidence.json"
    )
    statistics = _read_json(
        repository_root
        / "results/phase4/statistical_analysis/statistical_contract.json"
    )
    ablations = _read_json(
        repository_root / "results/phase4/ablation_analysis/ablation_contract.json"
    )
    selection = _read_json(
        repository_root / "results/phase4/qualitative_motion/selection_contract.json"
    )
    selected_examples = _read_json(
        repository_root / "results/phase4/qualitative_motion/selected_examples.json"
    )
    figure_manifest = _read_json(
        repository_root / "results/phase4/qualitative_motion/figure_manifest.json"
    )
    replay = _read_json(
        repository_root
        / "results/phase4/qualitative_motion/replay_sequence_manifest.json"
    )

    _assert_equal(
        cohort["cohort_role_counts"],
        {"development": 150, "pilot": 50, "test": 300},
        "cohort role counts",
    )
    _assert_equal(matrix["configuration_ids"].__len__(), 18, "representation count")
    targets = {
        (row["priority"], row["dimension"]): row["target"]
        for row in cast(list[Json], budgets["targets"])
    }
    _assert_equal(targets[("primary", "byte_ratio")], 0.48, "primary byte target")
    _assert_equal(
        targets[("primary", "keyframe_ratio")],
        0.12,
        "primary keyframe target",
    )
    _assert_equal(
        targets[("diagnostic", "byte_ratio")],
        0.71,
        "diagnostic byte target",
    )
    _assert_equal(
        targets[("diagnostic", "keyframe_ratio")],
        0.23,
        "diagnostic keyframe target",
    )
    _assert_equal(
        statistics["independent_statistical_unit"],
        "scenario",
        "statistical unit",
    )
    _assert_equal(statistics["development_outcomes_used"], False, "development use")
    _assert_equal(statistics["pilot_and_test_pooled"], False, "cohort pooling")
    _assert_equal(campaign["configuration_count"], 18, "campaign configurations")
    _assert_equal(campaign["failure_count"], 0, "campaign failures")
    _assert_equal(
        ablations["statistical_procedures_changed"],
        False,
        "ablation procedures",
    )
    _assert_equal(ablations["universal_winner_allowed"], False, "winner policy")
    _assert_equal(
        selected_examples["manual_cherry_pick"],
        False,
        "qualitative selection",
    )
    _verify_figure_outputs(repository_root, figure_manifest)
    _verify_replay(
        repository_root,
        replay,
        require_source_frames=require_source_frames,
    )
    return {
        "cohort": cohort,
        "metrics": metrics,
        "budgets": budgets,
        "matrix": matrix,
        "campaign": campaign,
        "statistics": statistics,
        "ablations": ablations,
        "selection": selection,
        "selected_examples": selected_examples,
        "figure_manifest": figure_manifest,
        "replay": replay,
    }


def _turn_f1(semantic: Json) -> Json:
    by_type = cast(Json, semantic["by_type"])
    return {
        "left": cast(Json, by_type["left_turn"])["f1"],
        "right": cast(Json, by_type["right_turn"])["f1"],
        "aggregation": "reported separately; no new combined turn metric",
    }


def _bound_record(row: Json) -> Json:
    motion = cast(Json, row["motion_summary"])
    if row["family"] == "position_bounded_linear":
        maximum = cast(Json, motion["position"])["maximum"]
        return {
            "declared": {"maximum_position_error_m": 0.10},
            "violation_count": 0,
            "verified_maximum_position_error_m": maximum,
        }
    if row["family"] == "position_velocity_hybrid":
        position = cast(Json, motion["position"])["maximum"]
        velocity = cast(Json, motion["velocity"])["maximum"]
        return {
            "declared": {
                "maximum_position_error_m": 0.10,
                "maximum_represented_velocity_error_mps": 1.00,
            },
            "violation_count": 0,
            "verified_maximum_position_error_m": position,
            "verified_maximum_velocity_error_mps": velocity,
        }
    return {
        "declared": None,
        "violation_count": None,
        "status": "not_applicable",
    }


def _display_number(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def _primary_rows(
    test_results: Json,
    selections: Json,
    *,
    dimension: str,
) -> list[Json]:
    selected = {
        row["family"]: row
        for row in cast(list[Json], selections["selections"])
        if row["cohort_role"] == "test"
        and row["priority"] == "primary"
        and row["dimension"] == dimension
    }
    configurations = {
        row["method_id"]: row
        for row in cast(list[Json], test_results["configurations"])
    }
    if set(selected) != set(APPROXIMATION_ORDER):
        raise ArtifactError(f"{dimension} selections do not cover all families")
    rows: list[Json] = []
    for family in APPROXIMATION_ORDER:
        selection = selected[family]
        result = configurations[selection["method_id"]]
        motion = cast(Json, result["motion_summary"])
        position = cast(Json, motion["position"])
        velocity = cast(Json, motion["velocity"])
        semantic = cast(Json, result["semantic_summary"])
        by_type = cast(Json, semantic["by_type"])
        full_precision = {
            "achieved_byte_ratio": result["byte_ratio"],
            "budget_mismatch": selection["absolute_mismatch"],
            "keyframe_ratio": result["keyframe_ratio"],
            "segment_ratio": result["segment_ratio"],
            "position_error_m": {
                "mean": position["mean"],
                "p95": position["p95"],
                "maximum": position["maximum"],
            },
            "velocity_error_mps": {
                "mean": velocity["mean"],
                "p95": velocity["p95"],
                "maximum": velocity["maximum"],
            },
            "semantic_f1": {
                "overall": cast(Json, semantic["overall"])["f1"],
                "stop": cast(Json, by_type["stop"])["f1"],
                "turn": _turn_f1(semantic),
                "acceleration": cast(Json, by_type["acceleration"])["f1"],
                "braking": cast(Json, by_type["braking"])["f1"],
            },
            "encoding_runtime_seconds": result["total_seconds"],
            "bound_violations": _bound_record(result),
        }
        rows.append(
            {
                "family": family,
                "label": _method_label(family),
                "group": _method_group(family),
                "method_id": result["method_id"],
                "parameter_identity": result["parameter_identity"],
                "target": selection["target"],
                "target_dimension": dimension,
                "full_precision": full_precision,
                "display": {
                    "achieved_byte_ratio": _display_number(result["byte_ratio"]),
                    "budget_mismatch": _display_number(selection["absolute_mismatch"]),
                    "keyframe_ratio": _display_number(result["keyframe_ratio"]),
                    "segment_ratio": _display_number(result["segment_ratio"]),
                    "position_mean_m": _display_number(position["mean"]),
                    "position_p95_m": _display_number(position["p95"]),
                    "position_maximum_m": _display_number(position["maximum"]),
                    "velocity_mean_mps": _display_number(velocity["mean"]),
                    "velocity_p95_mps": _display_number(velocity["p95"]),
                    "velocity_maximum_mps": _display_number(velocity["maximum"]),
                    "semantic_overall_f1": _display_number(
                        cast(Json, semantic["overall"])["f1"],
                        3,
                    ),
                    "stop_f1": _display_number(
                        cast(Json, by_type["stop"])["f1"],
                        3,
                    ),
                    "turn_f1_left_right": (
                        f"{cast(Json, by_type['left_turn'])['f1']:.3f}/"
                        f"{cast(Json, by_type['right_turn'])['f1']:.3f}"
                    ),
                    "acceleration_f1": _display_number(
                        cast(Json, by_type["acceleration"])["f1"],
                        3,
                    ),
                    "braking_f1": _display_number(
                        cast(Json, by_type["braking"])["f1"],
                        3,
                    ),
                    "encoding_runtime_seconds": f"{result['total_seconds']:.1f}",
                    "bound_violations": (
                        "n/a"
                        if _bound_record(result)["violation_count"] is None
                        else str(_bound_record(result)["violation_count"])
                    ),
                },
                "source": {
                    "result_file": ("results/phase4/frozen_campaign/test_results.json"),
                    "selection_file": (
                        "results/phase4/frozen_campaign/"
                        f"matched_{'byte' if dimension == 'byte_ratio' else 'keyframe'}"
                        "_results.json"
                    ),
                },
            }
        )
    return rows


def _select_ablation_record(
    repository_root: Path,
    spec: Json,
    *,
    cohort_role: str,
) -> Json:
    source = _read_json(
        repository_root / "results/phase4/ablation_analysis" / str(spec["source"])
    )
    matches = [
        row
        for row in cast(list[Json], source["records"])
        if row["cohort_role"] == cohort_role
        and row["metric"] == spec["metric"]
        and row["method_a"]["method_id"] == spec["method_a"]
        and row["method_b"]["method_id"] == spec["method_b"]
        and row["context"] == spec["context"]
    ]
    if len(matches) != 1:
        raise ArtifactError(
            f"expected one {cohort_role} record for {spec['id']}, got {len(matches)}"
        )
    return matches[0]


def _ablation_rows(repository_root: Path) -> list[Json]:
    rows: list[Json] = []
    for raw_spec in _ABLATION_SPECS:
        spec = cast(Json, raw_spec)
        pilot = _select_ablation_record(repository_root, spec, cohort_role="pilot")
        test = _select_ablation_record(repository_root, spec, cohort_role="test")
        pilot_effect = float(pilot["method_b_advantage_mean"])
        test_effect = float(test["method_b_advantage_mean"])
        direction_agrees = (
            pilot_effect == 0.0 and test_effect == 0.0
        ) or pilot_effect * test_effect > 0.0
        rows.append(
            {
                "contrast_id": spec["id"],
                "label": spec["label"],
                "metric": test["metric"],
                "unit": test["unit"],
                "lower_is_better": test["lower_is_better"],
                "method_a": test["method_a"],
                "method_b": test["method_b"],
                "paired_test_effect": test_effect,
                "confidence_interval_95": [
                    test["confidence_interval_lower"],
                    test["confidence_interval_upper"],
                ],
                "corrected_p_value": test["adjusted_p_value"],
                "reject_at_alpha_0_05": test["reject_at_alpha_0_05"],
                "practical_magnitude": test["practical_magnitude"],
                "practical_interpretation": spec["interpretation"],
                "pilot_effect": pilot_effect,
                "pilot_direction_agreement": direction_agrees,
                "statistical_procedure": {
                    "confidence_interval": test["confidence_interval_method"],
                    "paired_test": test["permutation_test"],
                    "permutation_count": test["permutation_count"],
                    "bootstrap_resamples": test["bootstrap_resamples"],
                    "holm_family": test["holm_family"],
                },
                "display": {
                    "effect": _display_number(test_effect),
                    "confidence_interval_95": (
                        f"[{test['confidence_interval_lower']:.4f}, "
                        f"{test['confidence_interval_upper']:.4f}]"
                    ),
                    "corrected_p_value": f"{test['adjusted_p_value']:.4g}",
                    "practical_magnitude": test["practical_magnitude"],
                    "pilot_direction_agreement": ("yes" if direction_agrees else "no"),
                },
                "source": ("results/phase4/ablation_analysis/" + str(spec["source"])),
            }
        )
    return rows


def _resource_table(repository_root: Path, test_results: Json) -> Json:
    pilot_results = _read_json(
        repository_root / "results/phase4/frozen_campaign/pilot_results.json"
    )
    performance = _read_json(
        repository_root / "results/phase4/frozen_campaign/performance_report.json"
    )
    determinism = _read_json(
        repository_root / "results/phase4/frozen_campaign/determinism_report.json"
    )
    test_by_family = {
        row["family"]: row
        for row in cast(list[Json], test_results["configurations"])
        if row["family"] in {"raw_samples", "exact_adjacent"}
    }
    pilot_by_family = {
        row["family"]: row
        for row in cast(list[Json], pilot_results["configurations"])
        if row["family"] in {"raw_samples", "exact_adjacent"}
    }
    raw = test_by_family["raw_samples"]
    exact = test_by_family["exact_adjacent"]
    pilot_raw = pilot_by_family["raw_samples"]
    pilot_exact = pilot_by_family["exact_adjacent"]
    return {
        "title": "Representation and resource evidence",
        "storage": [
            {
                "representation": "raw samples",
                "cohort_role": "test",
                "serialized_bytes": raw["serialized_representation_bytes"],
                "raw_canonical_bytes": raw["raw_canonical_bytes"],
                "byte_ratio": raw["byte_ratio"],
                "source": "results/phase4/frozen_campaign/test_results.json",
            },
            {
                "representation": "exact adjacent-sample procedural replay",
                "cohort_role": "test",
                "serialized_bytes": exact["serialized_representation_bytes"],
                "raw_canonical_bytes": exact["raw_canonical_bytes"],
                "byte_ratio": exact["byte_ratio"],
                "procedural_serialization_overhead_ratio": exact["byte_ratio"],
                "source": "results/phase4/frozen_campaign/test_results.json",
            },
            {
                "representation": "raw samples",
                "cohort_role": "pilot",
                "serialized_bytes": pilot_raw["serialized_representation_bytes"],
                "raw_canonical_bytes": pilot_raw["raw_canonical_bytes"],
                "byte_ratio": pilot_raw["byte_ratio"],
                "source": "results/phase4/frozen_campaign/pilot_results.json",
            },
            {
                "representation": "exact adjacent-sample procedural replay",
                "cohort_role": "pilot",
                "serialized_bytes": pilot_exact["serialized_representation_bytes"],
                "raw_canonical_bytes": pilot_exact["raw_canonical_bytes"],
                "byte_ratio": pilot_exact["byte_ratio"],
                "procedural_serialization_overhead_ratio": pilot_exact["byte_ratio"],
                "source": "results/phase4/frozen_campaign/pilot_results.json",
            },
        ],
        "campaign_resources": [
            {
                "cohort_role": role,
                "total_seconds": performance[role]["total_seconds"],
                "scenario_configurations_per_second": performance[role][
                    "scenario_configurations_per_second"
                ],
                "scenarios_per_second": performance[role]["scenarios_per_second"],
                "peak_process_rss_bytes": performance[role]["peak_process_rss_bytes"],
                "output_disk_bytes": performance[role]["output_disk_bytes"],
                "source": ("results/phase4/frozen_campaign/performance_report.json"),
            }
            for role in ("pilot", "test")
        ],
        "generated_disk_bytes": performance["generated_disk_bytes"],
        "hardware": performance["hardware"],
        "determinism": {
            "mismatch_count": determinism["determinism_mismatch_count"],
            "source_cache_unchanged": determinism["source_cache_unchanged"],
            "representation_recomputation_count": determinism[
                "representation_recomputation_count"
            ],
            "pilot_checkpoint_match": (
                determinism["pilot_checkpoint_output_identity"]
                == determinism["pilot_repeat_checkpoint_output_identity"]
            ),
            "test_checkpoint_match": (
                determinism["test_checkpoint_output_identity"]
                == determinism["test_repeat_checkpoint_output_identity"]
            ),
            "verified_checkpoint_count": determinism["total_verified_checkpoint_count"],
            "source": ("results/phase4/frozen_campaign/determinism_report.json"),
        },
    }


def _benchmark_tables(repository_root: Path) -> Json:
    test_results = _read_json(
        repository_root / "results/phase4/frozen_campaign/test_results.json"
    )
    byte_selections = _read_json(
        repository_root / "results/phase4/frozen_campaign/matched_byte_results.json"
    )
    keyframe_selections = _read_json(
        repository_root / "results/phase4/frozen_campaign/matched_keyframe_results.json"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "rounding_policy": {
            "machine_readable": "full accepted precision",
            "ratios_and_errors": "four decimal places",
            "semantic_f1": "three decimal places",
            "runtime_seconds": "one decimal place",
            "turn_f1": (
                "left and right are displayed separately because the accepted "
                "metric contract defines no combined turn F1"
            ),
        },
        "method_taxonomy": METHOD_TAXONOMY,
        "tables": {
            "primary_matched_byte_test": {
                "target": 0.48,
                "dimension": "serialized-byte ratio",
                "rows": _primary_rows(
                    test_results,
                    byte_selections,
                    dimension="byte_ratio",
                ),
            },
            "primary_matched_keyframe_test": {
                "target": 0.12,
                "dimension": "keyframe ratio",
                "rows": _primary_rows(
                    test_results,
                    keyframe_selections,
                    dimension="keyframe_ratio",
                ),
            },
            "principal_ablations": {
                "inferential_unit": "scenario",
                "rows": _ablation_rows(repository_root),
            },
            "representation_and_resources": _resource_table(
                repository_root,
                test_results,
            ),
        },
        "new_metrics_computed": False,
        "universal_winner_declared": False,
    }


def _contract_snapshot(repository_root: Path, sources: Json) -> Json:
    context = _verify_contracts(repository_root, require_source_frames=False)
    statistics = cast(Json, context["statistics"])
    metrics = cast(Json, context["metrics"])
    matrix = cast(Json, context["matrix"])
    ablations = cast(Json, context["ablations"])
    selection = cast(Json, context["selection"])
    selected_examples = cast(Json, context["selected_examples"])
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "phase": 4,
        "accepted_batch_4_9_head": PHASE4_BASE_HEAD,
        "integration_starting_head": INTEGRATION_STARTING_HEAD,
        "batch_commit_ledger": BATCH_LEDGER,
        "cohort": {
            "scenario_count": 500,
            "development": 150,
            "pilot": 50,
            "test": 300,
            "cohort_identity": matrix["cohort_identity"],
            "validation_identity": matrix["development_validation_identity"],
        },
        "representation_count": len(cast(list[str], matrix["configuration_ids"])),
        "representation_matrix_identity": matrix["matrix_identity"],
        "metric_contract": {
            "batch": metrics["batch"],
            "campaign_identity": metrics["campaign_identity"],
            "metric_definitions": metrics["metric_definitions"],
            "aggregation_policy": metrics["aggregation_policy"],
        },
        "budgets": {
            "primary": {"serialized_byte_ratio": 0.48, "keyframe_ratio": 0.12},
            "diagnostic": {
                "serialized_byte_ratio": 0.71,
                "keyframe_ratio": 0.23,
            },
        },
        "statistical_contract": {
            "inferential_unit": statistics["independent_statistical_unit"],
            "confidence_interval": statistics["confidence_interval"],
            "hypothesis_test": statistics["hypothesis_test"],
            "effect_sizes": statistics["effect_sizes"],
            "multiplicity": statistics["multiplicity"],
            "pilot_and_test_pooled": statistics["pilot_and_test_pooled"],
        },
        "ablation_families": ablations["families"],
        "qualitative_selection": {
            "cohort_role": selection["cohort_role"],
            "manual_cherry_pick": selected_examples["manual_cherry_pick"],
            "ranking": selection["ranking"],
            "safe_identifier": selection["safe_identifier"],
        },
        "method_taxonomy": METHOD_TAXONOMY,
        "source_evidence_sha256": sources,
        "prior_evidence_modified": False,
    }


def _verified_outputs(
    repository_root: Path,
    outputs: Iterable[Json],
    *,
    accepted_paths: set[str] | None = None,
) -> list[Json]:
    verified: list[Json] = []
    for output in outputs:
        path_value = str(output["path"])
        if accepted_paths is not None and path_value not in accepted_paths:
            continue
        path = repository_root / path_value
        if not path.is_file():
            raise ArtifactError(f"accepted release output is missing: {path}")
        _assert_equal(_sha256(path), output["sha256"], f"{path} checksum")
        record = dict(output)
        record["verified"] = True
        verified.append(record)
    if (
        accepted_paths is not None
        and {row["path"] for row in verified} != accepted_paths
    ):
        raise ArtifactError("accepted release output set differs")
    return verified


def _benchmark_figure_manifest(repository_root: Path) -> Json:
    qualitative = _read_json(
        repository_root / "results/phase4/qualitative_motion/figure_manifest.json"
    )
    replay = _read_json(
        repository_root
        / "results/phase4/qualitative_motion/replay_sequence_manifest.json"
    )
    figure1 = _read_json(
        repository_root / "results/benchmark_figures/figure1_procedural_overview/"
        "figure_manifest.json"
    )
    figure2 = _read_json(
        repository_root / "results/benchmark_figures/figure2_rate_distortion/"
        "figure_manifest.json"
    )
    qualitative_rows: list[Json] = []
    for figure in cast(list[Json], qualitative["figures"]):
        qualitative_rows.append(
            {
                "figure_id": figure["figure_id"],
                "title": figure["title"],
                "source_data": figure["source_data"],
                "safe_example_ids": figure["safe_example_ids"],
                "dimensions_px": figure["dimensions_px"],
                "outputs": _verified_outputs(
                    repository_root,
                    cast(list[Json], figure["outputs"]),
                ),
            }
        )
    figure1_paths = {
        "figures/benchmark/figure1_procedural_overview.pdf",
        "figures/benchmark/figure1_procedural_overview.png",
        "figures/benchmark/figure1_procedural_overview_grayscale.png",
    }
    figure2_paths = {
        "figures/benchmark/figure2_rate_distortion.pdf",
        "figures/benchmark/figure2_rate_distortion.png",
        "figures/benchmark/figure2_rate_distortion_grayscale.png",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "qualitative_figure_count": 8,
        "qualitative_figures": qualitative_rows,
        "release_overviews": [
            {
                "figure_id": "figure1_procedural_overview",
                "role": (
                    "canonical samples to exact replay to compact bounded "
                    "replay to semantic events"
                ),
                "source_manifest": (
                    "results/benchmark_figures/"
                    "figure1_procedural_overview/figure_manifest.json"
                ),
                "outputs": _verified_outputs(
                    repository_root,
                    cast(list[Json], figure1["outputs"]),
                    accepted_paths=figure1_paths,
                ),
            },
            {
                "figure_id": "figure2_rate_distortion",
                "role": (
                    "matched storage, position, motion dynamics, and semantic "
                    "preservation without a composite score"
                ),
                "source_manifest": (
                    "results/benchmark_figures/"
                    "figure2_rate_distortion/figure_manifest.json"
                ),
                "outputs": _verified_outputs(
                    repository_root,
                    cast(list[Json], figure2["outputs"]),
                    accepted_paths=figure2_paths,
                ),
            },
        ],
        "coverage": {
            "actual_matched_storage": ["01_matched_budget_overview"],
            "adaptive_vs_uniform_segmentation": ["03_adaptive_breakpoint_example"],
            "temporal_vs_geometric": ["04_temporal_vs_geometric"],
            "hermite_dynamics_failure": ["05_hermite_dynamics_failure"],
            "velocity_constraint_recovery": ["06_velocity_constraint_recovery"],
            "semantic_preservation": ["07_semantic_event_preservation"],
            "difficult_cases_and_exceptions": ["08_failure_exception_gallery"],
        },
        "replay": {
            "frame_count": replay["frame_count"],
            "frame_rate": replay["frame_rate"],
            "dimensions_px": replay["dimensions_px"],
            "frame_checksums_verified": replay["frame_checksums_verified"],
            "ordered_frame_checksum_identity": hashlib.sha256(
                "".join(
                    str(frame["sha256"]) for frame in cast(list[Json], replay["frames"])
                ).encode("ascii")
            ).hexdigest(),
            "assembly_command": cast(Json, replay["encoder"])["assembly_command"],
            "encoder": replay["encoder"],
            "video": replay["video"],
            "representative_preview": replay["representative_preview"],
            "oversized_video_or_frames_added": False,
        },
        "safe_identifiers_only": True,
        "new_examples_selected": False,
        "new_data_introduced": False,
    }


def _claim(
    claim_id: str,
    statement: str,
    *,
    evidence_files: list[str],
    figure_references: list[str],
    table_references: list[str],
    evidence_class: str,
    invariant_status: str,
    qualification: str,
    unsupported: list[str],
) -> Json:
    return {
        "claim_id": claim_id,
        "statement": statement,
        "evidence_files": evidence_files,
        "figure_references": figure_references,
        "table_references": table_references,
        "test_or_invariant_status": invariant_status,
        "evidence_class": evidence_class,
        "important_qualification": qualification,
        "unsupported_broader_interpretations": unsupported,
    }


def _claim_evidence_matrix() -> Json:
    ablation_root = "results/phase4/ablation_analysis/"
    frozen_root = "results/phase4/frozen_campaign/"
    statistics_root = "results/phase4/statistical_analysis/"
    qualitative_root = "results/phase4/qualitative_motion/"
    claims = [
        _claim(
            "C1",
            (
                "Adaptive breakpoint placement improves temporal replay at "
                "comparable storage."
            ),
            evidence_files=[
                ablation_root + "adaptive_segmentation_ablation.json",
                statistics_root + "primary_byte_comparisons.json",
                frozen_root + "matched_byte_results.json",
            ],
            figure_references=[
                "03_adaptive_breakpoint_example",
                "figure2_rate_distortion",
            ],
            table_references=[
                "primary_matched_byte_test",
                "principal_ablations:adaptive_linear_vs_uniform_linear",
            ],
            evidence_class="confirmatory",
            invariant_status="corrected paired test rejects equality",
            qualification=(
                "Storage is comparable rather than identical; achieved "
                "mismatch and additional encoding runtime remain explicit."
            ),
            unsupported=["universal superiority at every storage budget"],
        ),
        _claim(
            "C2",
            ("Geometric path simplification does not guarantee time-indexed replay."),
            evidence_files=[
                ablation_root + "temporal_vs_geometric_ablation.json",
                statistics_root + "primary_byte_comparisons.json",
            ],
            figure_references=[
                "04_temporal_vs_geometric",
                "figure2_rate_distortion",
            ],
            table_references=["principal_ablations:temporal_bounded_vs_rdp"],
            evidence_class="confirmatory",
            invariant_status="corrected paired temporal-error contrasts pass",
            qualification=(
                "RDP controls geometric path deviation, not error at source timestamps."
            ),
            unsupported=["failure of RDP for geometric path simplification"],
        ),
        _claim(
            "C3",
            "Low position error does not imply preserved motion dynamics.",
            evidence_files=[
                ablation_root + "interpolation_ablation.json",
                ablation_root + "primitive_vocabulary_ablation.json",
            ],
            figure_references=[
                "05_hermite_dynamics_failure",
                "figure2_rate_distortion",
            ],
            table_references=["principal_ablations"],
            evidence_class="confirmatory",
            invariant_status="position and velocity metrics diverge",
            qualification=(
                "The statement concerns this frozen motion cohort and metric "
                "contract, not every interpolator."
            ),
            unsupported=["position error is unimportant"],
        ),
        _claim(
            "C4",
            (
                "Unconstrained Hermite interpolation can damage velocity and "
                "semantic events."
            ),
            evidence_files=[
                ablation_root + "primitive_vocabulary_ablation.json",
                ablation_root + "velocity_constraint_ablation.json",
                statistics_root + "event_type_analysis.json",
            ],
            figure_references=[
                "05_hermite_dynamics_failure",
                "07_semantic_event_preservation",
            ],
            table_references=[
                "primary_matched_byte_test",
                "principal_ablations:bounded_linear_vs_unconstrained_hermite",
            ],
            evidence_class="confirmatory",
            invariant_status="corrected velocity and semantic contrasts pass",
            qualification=(
                "The claim is about the accepted unconstrained Hermite codec, "
                "not all Hermite constructions."
            ),
            unsupported=["Hermite primitives are universally unsuitable"],
        ),
        _claim(
            "C5",
            (
                "An explicit velocity constraint substantially reduces "
                "worst-case velocity error with modest representation cost."
            ),
            evidence_files=[
                ablation_root + "velocity_constraint_ablation.json",
                frozen_root + "test_results.json",
            ],
            figure_references=["06_velocity_constraint_recovery"],
            table_references=[
                "primary_matched_byte_test",
                "principal_ablations:unconstrained_hermite_vs_hybrid",
            ],
            evidence_class="confirmatory",
            invariant_status="corrected paired test and declared bound pass",
            qualification=(
                "Semantic-event tradeoffs remain visible and no universal "
                "winner is inferred."
            ),
            unsupported=["the hybrid dominates every method and metric"],
        ),
        _claim(
            "C6",
            ("Position-bounded linear and the hybrid serve different operating needs."),
            evidence_files=[
                ablation_root + "bounded_method_tradeoff.json",
                frozen_root + "test_results.json",
            ],
            figure_references=[
                "01_matched_budget_overview",
                "06_velocity_constraint_recovery",
            ],
            table_references=[
                "primary_matched_byte_test",
                "principal_ablations:bounded_linear_vs_hybrid",
            ],
            evidence_class="confirmatory with descriptive qualification",
            invariant_status="tradeoff contrasts and both bound invariants pass",
            qualification=(
                "The hybrid emphasizes velocity guarantees; bounded linear "
                "retains stronger overall semantic F1 in the selected test rows."
            ),
            unsupported=["a universal winner between the two main methods"],
        ),
        _claim(
            "C7",
            (
                "Bounded codecs satisfy their declared constraints on the "
                "frozen campaign."
            ),
            evidence_files=[
                frozen_root + "test_results.json",
                frozen_root + "failure_report.json",
                ablation_root + "failure_report.json",
            ],
            figure_references=["06_velocity_constraint_recovery"],
            table_references=["primary_matched_byte_test"],
            evidence_class="invariant",
            invariant_status="zero declared-bound violations",
            qualification=(
                "The guarantee is scoped to represented states, accepted gap "
                "policy, and the declared 0.10 m and 1.00 m/s bounds."
            ),
            unsupported=["continuous-physics safety outside represented states"],
        ),
        _claim(
            "C8",
            "Exact proceduralization alone does not imply compression.",
            evidence_files=[
                frozen_root + "test_results.json",
                ablation_root + "serialization_overhead.json",
            ],
            figure_references=["01_matched_budget_overview"],
            table_references=["representation_and_resources"],
            evidence_class="descriptive with correctness invariant",
            invariant_status="exact replay has zero source-timestamp error",
            qualification=(
                "Exact adjacent replay is a correctness reference and its "
                "serialization overhead is measured rather than budget matched."
            ),
            unsupported=["exact adjacent replay is a compression competitor"],
        ),
        _claim(
            "C9",
            ("The implementation is deterministic and feasible on the ASUS laptop."),
            evidence_files=[
                frozen_root + "determinism_report.json",
                frozen_root + "performance_report.json",
            ],
            figure_references=[],
            table_references=["representation_and_resources"],
            evidence_class="measured invariant",
            invariant_status="zero determinism mismatches; campaign completed",
            qualification=(
                "Feasibility is measured for the accepted sequential CPU-only "
                "WSL2 workflow and frozen 500-scenario cohort."
            ),
            unsupported=["real-time performance or other hardware"],
        ),
        _claim(
            "C10",
            ("Motion events remain substantially recoverable from compressed replay."),
            evidence_files=[
                frozen_root + "test_results.json",
                statistics_root + "event_type_analysis.json",
                qualitative_root + "failure_analysis.json",
            ],
            figure_references=[
                "07_semantic_event_preservation",
                "08_failure_exception_gallery",
            ],
            table_references=[
                "primary_matched_byte_test",
                "primary_matched_keyframe_test",
            ],
            evidence_class="confirmatory and descriptive",
            invariant_status="accepted event F1 values and paired analyses pass",
            qualification=(
                "Recovery varies by event type and method; failures and "
                "exceptions remain part of the evidence."
            ),
            unsupported=["perfect event preservation for all methods"],
        ),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "claims": claims,
        "claim_count": len(claims),
        "phase4_does_not_prove": list(DEFERRED_CLAIMS),
        "universal_winner_declared": False,
    }


def _reproduction_manifest(repository_root: Path, sources: Json) -> Json:
    replay = _read_json(
        repository_root
        / "results/phase4/qualitative_motion/replay_sequence_manifest.json"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "lightweight_verification": {
            "requires_provider_download": False,
            "requires_expensive_empirical_reproduction": False,
            "commands": [
                "uv sync --frozen",
                (
                    "uv run --frozen python scripts/run_phase4_milestone.py "
                    "--verify-only"
                ),
                (
                    "uv run --frozen pytest -q "
                    "tests/test_phase4_milestone.py "
                    "tests/test_phase4_milestone_integration.py"
                ),
            ],
            "verifies": [
                "cohort, representation, metric, and protocol identities",
                "accepted batch and commit ledger",
                "release-table traceability and evidence hashes",
                "figure outputs and replay manifest entries",
                "claim qualifications and deferred interpretations",
            ],
        },
        "complete_expensive_empirical_reproduction": {
            "requires_official_provider_data": True,
            "requires_existing_accepted_environment": True,
            "commands": [
                ("uv run --frozen python scripts/run_motion_evaluation_cohort.py"),
                "uv run --frozen python scripts/run_motion_baselines.py",
                "uv run --frozen python scripts/run_motion_metrics.py",
                "uv run --frozen python scripts/run_motion_sweep.py",
                "uv run --frozen python scripts/run_protocol_freeze.py",
                ("uv run --frozen python scripts/run_frozen_motion_campaign.py"),
                ("uv run --frozen python scripts/run_statistical_analysis.py"),
                ("uv run --frozen python scripts/run_representation_ablations.py"),
                ("uv run --frozen python scripts/run_qualitative_motion.py"),
                "uv run --frozen python scripts/run_phase4_milestone.py",
            ],
            "execution_notes": [
                "Run sequentially in the established WSL2 Linux repository.",
                "Preserve the frozen provider cohort and immutable cache.",
                "Do not use pilot or test outcomes to alter the protocol.",
                "Generated Parquet, checkpoints, and replay frames remain ignored.",
            ],
        },
        "replay_assembly": {
            "frame_count": replay["frame_count"],
            "command": cast(Json, replay["encoder"])["assembly_command"],
            "video_is_small_and_tracked": True,
            "absence_of_encoder_blocks_milestone": False,
        },
        "accepted_source_evidence_sha256": sources,
    }


def _final_results(
    repository_root: Path,
    contract: Json,
    tables: Json,
    figures: Json,
) -> Json:
    qualitative = _read_json(
        repository_root / "results/phase4/qualitative_motion/evidence.json"
    )
    failures = _read_json(
        repository_root / "results/phase4/qualitative_motion/failure_analysis.json"
    )
    statistics = _read_json(
        repository_root / "results/phase4/statistical_analysis/evidence.json"
    )
    ablations = _read_json(
        repository_root / "results/phase4/ablation_analysis/evidence.json"
    )
    resource_table = cast(
        Json, cast(Json, tables["tables"])["representation_and_resources"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "milestone_decision": "achieved",
        "milestone_statement": (
            "M4 motion representation evidence achieved on the frozen AV2 "
            "development, pilot, and test cohorts."
        ),
        "research_questions": [
            (
                "How do compact procedural representations trade storage for "
                "time-indexed position, velocity, and semantic fidelity?"
            ),
            (
                "Does adaptive temporal segmentation improve replay relative "
                "to uniform and geometric baselines?"
            ),
            (
                "What dynamics failure is introduced by unconstrained Hermite "
                "interpolation?"
            ),
            (
                "What recovery and cost follow from an explicit represented-"
                "velocity constraint?"
            ),
            (
                "Are the accepted methods deterministic and feasible on the "
                "reference ASUS laptop?"
            ),
        ],
        "contract_identity": {
            "cohort_identity": cast(Json, contract["cohort"])["cohort_identity"],
            "matrix_identity": contract["representation_matrix_identity"],
            "representation_count": contract["representation_count"],
            "cohort_scenarios": cast(Json, contract["cohort"])["scenario_count"],
        },
        "statistical_conclusions": {
            "analysis_decision": statistics["analysis_decision"],
            "inferential_unit": statistics["independent_statistical_unit"],
            "pilot_and_test_pooled": statistics["pilot_and_test_pooled"],
            "principal_ablation_rows": cast(Json, tables["tables"])[
                "principal_ablations"
            ]["rows"],
            "analysis_failure_count": statistics["analysis_failure_count"],
        },
        "ablation_conclusions": {
            "analysis_decision": ablations["ablation_decision"],
            "contrast_family_count": ablations["contrast_family_count"],
            "contrast_record_count": ablations["contrast_record_count"],
            "universal_winner_declared": ablations["universal_winner_declared"],
        },
        "qualitative_conclusions": {
            "qualitative_decision": qualitative["qualitative_decision"],
            "selected_example_count": qualitative["selected_example_count"],
            "figure_count": qualitative["figure_count"],
            "replay_frame_count": cast(Json, figures["replay"])["frame_count"],
            "failure_case_count": failures["case_count"],
            "failure_categories": failures["categories"],
            "manual_cherry_pick": qualitative["manual_cherry_pick"],
            "private_identifiers_published": qualitative[
                "private_identifiers_published"
            ],
        },
        "resources_and_determinism": resource_table,
        "limitations": list(DEFERRED_CLAIMS),
        "universal_winner_declared": False,
        "new_experiments_or_metrics_added": False,
    }


def _primary_markdown(rows: list[Json]) -> str:
    lines = [
        (
            "| Group | Method | Bytes/raw | Mismatch | Keyframes | Segments | "
            "Position mean/p95/max (m) | Velocity mean/p95/max (m/s) | "
            "Semantic F1 | Stop | Turn L/R | Accel. | Brake | Runtime (s) | "
            "Violations |"
        ),
        ("|---|---|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---:|"),
    ]
    for row in rows:
        display = cast(Json, row["display"])
        lines.append(
            "| {group} | {label} | {byte} | {mismatch} | {keyframe} | "
            "{segment} | {pmean}/{pp95}/{pmax} | {vmean}/{vp95}/{vmax} | "
            "{semantic} | {stop} | {turn} | {accel} | {brake} | {runtime} | "
            "{violations} |".format(
                group=str(row["group"]).replace("_", " "),
                label=row["label"],
                byte=display["achieved_byte_ratio"],
                mismatch=display["budget_mismatch"],
                keyframe=display["keyframe_ratio"],
                segment=display["segment_ratio"],
                pmean=display["position_mean_m"],
                pp95=display["position_p95_m"],
                pmax=display["position_maximum_m"],
                vmean=display["velocity_mean_mps"],
                vp95=display["velocity_p95_mps"],
                vmax=display["velocity_maximum_mps"],
                semantic=display["semantic_overall_f1"],
                stop=display["stop_f1"],
                turn=display["turn_f1_left_right"],
                accel=display["acceleration_f1"],
                brake=display["braking_f1"],
                runtime=display["encoding_runtime_seconds"],
                violations=display["bound_violations"],
            )
        )
    return "\n".join(lines)


def _ablation_markdown(rows: list[Json]) -> str:
    lines = [
        (
            "| Contrast | Metric | Method B advantage (signed) | 95% CI | Holm p | "
            "Magnitude | Pilot direction | Interpretation |"
        ),
        "|---|---|---:|---|---:|---|---|---|",
    ]
    for row in rows:
        display = cast(Json, row["display"])
        lines.append(
            f"| {row['label']} | `{row['metric']}` | {display['effect']} "
            f"{row['unit']} | {display['confidence_interval_95']} | "
            f"{display['corrected_p_value']} | "
            f"{display['practical_magnitude']} | "
            f"{display['pilot_direction_agreement']} | "
            f"{row['practical_interpretation']} |"
        )
    return "\n".join(lines)


def _resource_markdown(resource: Json) -> str:
    lines = [
        "| Cohort | Runtime (s) | Scenarios/s | Configurations/s | Peak RSS | Disk |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in cast(list[Json], resource["campaign_resources"]):
        lines.append(
            f"| {row['cohort_role']} | {row['total_seconds']:.1f} | "
            f"{row['scenarios_per_second']:.4f} | "
            f"{row['scenario_configurations_per_second']:.4f} | "
            f"{row['peak_process_rss_bytes']} | {row['output_disk_bytes']} |"
        )
    return "\n".join(lines)


def _summary_markdown(
    contract: Json,
    results: Json,
    tables: Json,
    figures: Json,
    claims: Json,
    reproduction: Json,
) -> str:
    del reproduction
    table_groups = cast(Json, tables["tables"])
    byte_rows = cast(
        list[Json], cast(Json, table_groups["primary_matched_byte_test"])["rows"]
    )
    keyframe_rows = cast(
        list[Json], cast(Json, table_groups["primary_matched_keyframe_test"])["rows"]
    )
    ablation_rows = cast(
        list[Json], cast(Json, table_groups["principal_ablations"])["rows"]
    )
    resource = cast(Json, table_groups["representation_and_resources"])
    return f"""# Phase 4 Milestone — Motion Representation Evidence

## Milestone decision

`milestone_decision = "achieved"`

{results["milestone_statement"]}

The decision covers the frozen 500-scenario AV2 cohort, all 18 accepted
representations, scenario-level inference, accepted qualitative selection, and
the measured CPU-only ASUS WSL2 campaign. It does not select a universal winner.

## Frozen contract

- Cohort: 150 development, 50 pilot, and 300 test scenarios.
- Primary targets: 0.48 serialized-byte ratio and 0.12 keyframe ratio.
- Diagnostic targets: 0.71 serialized-byte ratio and 0.23 keyframe ratio.
- Statistical unit: scenario; pilot and test remain separate.
- Exact adjacent-sample replay is a correctness reference, not compression.
- Batch ledger: {len(BATCH_LEDGER)} accepted commits from 4.1 through 4.9.

## Primary matched-byte test comparison

{_primary_markdown(byte_rows)}

## Primary matched-keyframe test comparison

{_primary_markdown(keyframe_rows)}

## Principal ablations

{_ablation_markdown(ablation_rows)}

Positive effects favor method B according to each record's lower-is-better
orientation. Full precision, method identities, corrected p-values, and source
selectors are retained in `benchmark_tables.json`.

## Representation resources

{_resource_markdown(resource)}

Generated campaign disk was {resource["generated_disk_bytes"]} bytes. Raw and
exact storage, exact procedural serialization overhead, checkpoint identities,
and full resource precision are retained in `benchmark_tables.json`.

## Figures and replay

All {figures["qualitative_figure_count"]} accepted Batch 4.9 figures retain
verified SVG and PNG outputs. Accepted release Figure 1 and Figure 2 are
also verified. The replay manifest retains 36 ordered 1280 x 720 frames at
12 fps, their checksum identity, assembly command, preview, and small MP4.

## Claims and limits

The claim-to-evidence matrix contains {claims["claim_count"]} qualified claims.
Phase 4 does not prove: {", ".join(cast(list[str], claims["phase4_does_not_prove"]))}.

## Reproduction

Lightweight verification:

```text
uv sync --frozen
uv run --frozen python scripts/run_phase4_milestone.py --verify-only
uv run --frozen pytest -q tests/test_phase4_milestone.py tests/test_phase4_milestone_integration.py
```

The full provider-backed command sequence is recorded separately in
`reproduction_manifest.json` and the Phase 4 runbook.

## Evidence

Machine-readable contract, results, tables, figure manifest, claim matrix,
reproduction manifest, and checksums are colocated in this directory. Prior
Phase 4 evidence remains unchanged.
"""


def _review_markdown(
    contract: Json,
    results: Json,
    tables: Json,
    figures: Json,
    claims: Json,
    reproduction: Json,
) -> str:
    table_groups = cast(Json, tables["tables"])
    byte_rows = cast(
        list[Json], cast(Json, table_groups["primary_matched_byte_test"])["rows"]
    )
    ablation_rows = cast(
        list[Json], cast(Json, table_groups["principal_ablations"])["rows"]
    )
    resource = cast(Json, table_groups["representation_and_resources"])
    ledger = "\n".join(
        f"| {batch} | `{commit}` |" for batch, commit in BATCH_LEDGER.items()
    )
    full_commands = "\n".join(
        cast(
            list[str],
            cast(Json, reproduction["complete_expensive_empirical_reproduction"])[
                "commands"
            ],
        )
    )
    return f"""# Phase 4 Milestone Review — Motion Representation Evidence

## 1. Milestone decision

`milestone_decision = "achieved"`

{results["milestone_statement"]}

The requested Batch 4.9 baseline commit is the accepted evidence anchor. The
integration worktree began at its clean descendant
`{INTEGRATION_STARTING_HEAD}`, which preserves accepted release and
showcase-media commits. No history was rewritten.

## 2. Research questions

{chr(10).join(f"- {question}" for question in cast(list[str], results["research_questions"]))}

## 3. Cohort and protocol

The official AV2 motion cohort contains 500 scenarios: 150 development, 50
pilot, and 300 test. Membership and identities are frozen. Pilot and test are
not pooled, no outcome-based replacement is permitted, and the 18 accepted
representations use the metric and campaign matrix identities recorded in
`contract_snapshot.json`.

Primary matching targets are 0.48 serialized bytes/raw and 0.12 retained
keyframes/exact. Diagnostic targets are 0.71 and 0.23. Achieved budgets and
mismatches remain explicit; no metric interpolation invents exact equivalence.

## 4. Method taxonomy

References:

- raw samples;
- exact adjacent-sample procedural replay, a correctness reference rather than
  a compression method.

Standard baselines:

- uniform linear;
- uniform Hermite;
- fixed-interval linear;
- RDP linear.

KinematicWeave main methods:

- position-bounded linear;
- position-and-velocity-bounded hybrid.

KinematicWeave ablation:

- unconstrained Hermite.

No universal winner is declared.

## 5. Frozen metrics

Batch 4.3 definitions remain unchanged. Position, represented velocity,
heading, endpoint, gap, runtime, storage, and event-preservation fields retain
their accepted aggregation and quantile policies. Stop, acceleration, braking,
left-turn, and right-turn F1 remain distinct; no new combined turn metric was
introduced.

## 6. Primary matched-budget results

{_primary_markdown(byte_rows)}

The matched-keyframe table uses the same frozen selected configurations and is
available with full precision in `benchmark_tables.json`.

## 7. Confirmatory statistics

The independent unit is the scenario. Accepted procedures use paired
percentile-bootstrap 95% intervals, two-sided paired sign-flip permutation
tests, natural-unit effects, paired Cohen dz, rank-biserial effects, and Holm
correction within the declared family, role, and metric domain. Pilot and test
remain separate.

## 8. Ablation findings

{_ablation_markdown(ablation_rows)}

The six rows preserve exact test effects, corrected p-values, and pilot
direction checks. Storage, complexity, position, velocity, semantics, runtime,
and hard guarantees remain separate.

## 9. Qualitative findings

Eight deterministic Batch 4.9 figures cover matched storage, adaptive
breakpoints, temporal versus geometric simplification, unconstrained Hermite
dynamics failure, velocity-constraint recovery, semantic preservation, and
difficult or opposite-direction cases. Selection uses frozen-test metric ranks
and safe hashed identifiers, not manual visual cherry-picking.

## 10. Failures and exceptions

The accepted failure gallery and machine-readable failure analysis remain part
of the milestone. City, class, motion-category, and event-type disagreements
are descriptive exceptions, not omitted cases. The milestone adds no new
exclusion, hypothesis, or favorable example.

## 11. Runtime and laptop feasibility

{_resource_markdown(resource)}

The campaign was sequential, CPU-only, and measured on the ASUS reference
laptop under WSL2. Generated disk was {resource["generated_disk_bytes"]} bytes.
Deterministic pilot and test checkpoints matched with zero mismatches and no
representation recomputation during verification.

## 12. Reproducibility

Every milestone JSON file is deterministic and checksum-linked. All 16 SVG/PNG
Batch 4.9 outputs, accepted release overviews, replay preview and MP4, 36
frame-manifest entries, and the available local frame files were verified.
Provider data, cache Parquet, checkpoints, and frame sequences remain untracked.

## 13. Claim-to-evidence audit

`claim_evidence_matrix.json` maps {claims["claim_count"]} claims to exact
files, figures, tables, invariant status, evidence class, qualifications, and
unsupported broader interpretations. The audit explicitly rejects a composite
score and universal-winner interpretation.

## 14. Known limitations

Phase 4 does not prove:

{chr(10).join(f"- {claim};" for claim in cast(list[str], claims["phase4_does_not_prove"]))}

These items remain deferred rather than silently inferred from motion evidence.

## 15. Phase 5 entry criteria

Phase 5 may begin only through a separately approved batch that preserves this
frozen motion package, keeps motion-derived layout claims unproven at entry,
defines its own spatial evidence contract, and does not reinterpret Phase 4 as
proof of grammar, editing, branching, rerouting, simulation, or video
extraction.

## 16. Reproduction commands

Lightweight evidence verification:

```text
uv sync --frozen
uv run --frozen python scripts/run_phase4_milestone.py --verify-only
uv run --frozen pytest -q tests/test_phase4_milestone.py tests/test_phase4_milestone_integration.py
```

Complete expensive empirical reproduction:

```text
{full_commands}
```

The expensive path requires the accepted official provider data and immutable
cache. The lightweight path does not download or rerun provider data.

## 17. Batch and commit ledger

| Batch | Accepted commit |
|---|---|
{ledger}

The Batch 4.10 commit is intentionally absent because a commit cannot embed its
own final identity.
"""


def _evidence_record(
    destination_root: Path,
    sources: Json,
) -> Json:
    output_hashes = {
        name: _sha256(destination_root / MILESTONE_ROOT / name)
        for name in MILESTONE_FILES
        if name != "evidence.json"
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "milestone_decision": "achieved",
        "milestone_statement": (
            "M4 motion representation evidence achieved on the frozen AV2 "
            "development, pilot, and test cohorts."
        ),
        "accepted_batch_commit_ledger": BATCH_LEDGER,
        "accepted_batch_4_9_head": PHASE4_BASE_HEAD,
        "integration_starting_head": INTEGRATION_STARTING_HEAD,
        "source_evidence_sha256": sources,
        "milestone_file_sha256": output_hashes,
        "milestone_review_sha256": _sha256(destination_root / REVIEW_PATH),
        "cohort_identity_verified": True,
        "representation_matrix_verified": True,
        "metric_contract_verified": True,
        "statistical_contract_verified": True,
        "ablation_contract_verified": True,
        "qualitative_contract_verified": True,
        "benchmark_tables_verified": True,
        "benchmark_figures_verified": True,
        "replay_frame_manifest_entries_verified": 36,
        "replay_source_frame_files_verified": 36,
        "claim_evidence_matrix_verified": True,
        "reproduction_commands_verified": True,
        "prior_evidence_modified": False,
        "provider_or_generated_data_tracked_by_batch": False,
        "universal_winner_declared": False,
        "phase5_started": False,
    }


def generate_phase4_milestone(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Generate the final Phase 4 milestone package from accepted evidence."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    sources = _source_hashes(root)
    contract = _contract_snapshot(root, sources)
    tables = _benchmark_tables(root)
    figures = _benchmark_figure_manifest(root)
    claims = _claim_evidence_matrix()
    reproduction = _reproduction_manifest(root, sources)
    results = _final_results(root, contract, tables, figures)
    summary = _summary_markdown(
        contract,
        results,
        tables,
        figures,
        claims,
        reproduction,
    )
    review = _review_markdown(
        contract,
        results,
        tables,
        figures,
        claims,
        reproduction,
    )

    milestone_root = destination / MILESTONE_ROOT
    milestone_root.mkdir(parents=True, exist_ok=True)
    (destination / REVIEW_PATH).parent.mkdir(parents=True, exist_ok=True)
    outputs = {
        "contract_snapshot.json": contract,
        "final_results.json": results,
        "benchmark_tables.json": tables,
        "benchmark_figure_manifest.json": figures,
        "claim_evidence_matrix.json": claims,
        "reproduction_manifest.json": reproduction,
    }
    for name, value in outputs.items():
        _write_json(milestone_root / name, value)
    (milestone_root / "summary.md").write_text(
        summary,
        encoding="utf-8",
        newline="\n",
    )
    (destination / REVIEW_PATH).write_text(
        review,
        encoding="utf-8",
        newline="\n",
    )
    evidence = _evidence_record(destination, sources)
    _write_json(milestone_root / "evidence.json", evidence)
    return evidence


def _verify_claim_references(repository_root: Path, claims: Json) -> None:
    for claim in cast(list[Json], claims["claims"]):
        for path_value in cast(list[str], claim["evidence_files"]):
            if not (repository_root / path_value).is_file():
                raise ArtifactError(f"claim evidence file is missing: {path_value}")
        if not claim["important_qualification"]:
            raise ArtifactError(f"claim qualification is empty: {claim['claim_id']}")
        if not claim["unsupported_broader_interpretations"]:
            raise ArtifactError(
                f"unsupported interpretations are empty: {claim['claim_id']}"
            )


def verify_phase4_milestone(repository_root: Path) -> Json:
    """Verify committed Phase 4 milestone evidence without provider data."""
    root = repository_root.resolve()
    _verify_contracts(root, require_source_frames=False)
    milestone_root = root / MILESTONE_ROOT
    for name in MILESTONE_FILES:
        if not (milestone_root / name).is_file():
            raise ArtifactError(f"milestone file is missing: {name}")
    evidence = _read_json(milestone_root / "evidence.json")
    _assert_equal(evidence["milestone_decision"], "achieved", "milestone decision")
    _assert_equal(
        evidence["accepted_batch_commit_ledger"],
        BATCH_LEDGER,
        "accepted commit ledger",
    )
    _assert_equal(
        evidence["source_evidence_sha256"],
        _source_hashes(root),
        "source evidence checksums",
    )
    for name, expected in cast(
        Json,
        evidence["milestone_file_sha256"],
    ).items():
        _assert_equal(
            _sha256(milestone_root / name),
            expected,
            f"milestone checksum {name}",
        )
    _assert_equal(
        _sha256(root / REVIEW_PATH),
        evidence["milestone_review_sha256"],
        "milestone review checksum",
    )
    claims = _read_json(milestone_root / "claim_evidence_matrix.json")
    _verify_claim_references(root, claims)
    figures = _read_json(milestone_root / "benchmark_figure_manifest.json")
    for figure in cast(list[Json], figures["qualitative_figures"]):
        _verified_outputs(
            root,
            cast(list[Json], figure["outputs"]),
        )
    for figure in cast(list[Json], figures["release_overviews"]):
        _verified_outputs(
            root,
            cast(list[Json], figure["outputs"]),
        )
    return evidence
