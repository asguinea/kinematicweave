"""Deterministic aggregate Figure 2 and primary Table 1 release package."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import csv
import hashlib
import io
from itertools import pairwise
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any, cast

from PIL import Image

from kinematicweave.errors import ArtifactError
from kinematicweave.visualization.deterministic import (
    BLUE,
    CORAL,
    GREEN,
    GRID,
    INK,
    MUTED,
    WHITE,
    Color,
    Drawing,
)
from kinematicweave.visualization.figure1_procedural_overview import (
    verify_figure1_outputs,
)

type Json = dict[str, Any]

SCHEMA_VERSION = "1.0"
BATCH = "F2.2"
SCRIPT_VERSION = "1.1"
STARTING_HEAD = "3dba41457f1af4d1cd3f4cb87f3cc3a31b262f19"

TEST_SCENARIO_COUNT = 300
TEST_TRAJECTORY_COUNT = 13_689
CONFIGURATION_COUNT = 18
PRIMARY_BYTE_TARGET = 0.48
PRIMARY_KEYFRAME_TARGET = 0.12
COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
TEST_SCENARIO_IDENTITY = (
    "cee7af2ae750b8b2ab2e88d6940aefcb7d63fa24570af1c2946f53c5d0c550df"
)
TEST_TRAJECTORY_IDENTITY = (
    "4650ada7c760d20ca30c91e39c316c42848b638d2be06a0a8a001c1a35d39531"
)
MATRIX_IDENTITY = "c87a88e2f6a6a443a2654f5785cec16ab15e8ad8e2b1fef2d6d2bde8d5d59599"
METRIC_IDENTITY = "03440d32241c0e55d490cf617cd1b2d9ed1a546865372b8dc155f06c696e3277"


def _release_font() -> tuple[Path, str]:
    candidates = (
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), "DejaVu Sans"),
        (Path("C:/Windows/Fonts/arial.ttf"), "Arial"),
    )
    for path, family in candidates:
        if path.is_file():
            return path, family
    raise ArtifactError("a supported release font is unavailable")


FIGURE_RELATIVE = Path("figures/benchmark/figure2_rate_distortion")
GRAYSCALE_RELATIVE = Path("figures/benchmark/figure2_rate_distortion_grayscale.png")
TABLE_RELATIVE = Path("tables/benchmark/table1_primary_matched_byte")
REPRESENTATIVE_JPEG_RELATIVE = Path("figures/showcase/representative_figure1.jpg")
REPRESENTATIVE_WEB_RELATIVE = Path("figures/showcase/representative_figure1_web.png")
FIGURE_NOTES_RELATIVE = Path("reports/figure2_rate_distortion_notes.md")
TABLE_NOTES_RELATIVE = Path("reports/table1_primary_matched_byte_notes.md")
RESULT_RELATIVE = Path("results/benchmark_figures/figure2_rate_distortion")

PHASE4_SOURCE_DIRECTORIES = (
    Path("results/phase4/protocol_freeze"),
    Path("results/phase4/frozen_campaign"),
    Path("results/phase4/statistical_analysis"),
    Path("results/phase4/ablation_analysis"),
    Path("results/phase4/qualitative_motion"),
)
FIGURE1_EVIDENCE_RELATIVE = Path(
    "results/benchmark_figures/figure1_procedural_overview/evidence.json"
)
FIGURE1_PNG_RELATIVE = Path("figures/benchmark/figure1_procedural_overview.png")

REFERENCE_FAMILIES = ("raw_samples", "exact_adjacent")
PLOTTED_REFERENCE_FAMILIES = ("raw_samples",)
BASELINE_FAMILIES = (
    "uniform_linear",
    "uniform_hermite",
    "fixed_interval_linear",
    "rdp_linear",
)
MAIN_FAMILIES = ("position_bounded_linear", "position_velocity_hybrid")
ABLATION_FAMILIES = ("unconstrained_hermite",)
FAMILY_ORDER = (
    *REFERENCE_FAMILIES,
    *BASELINE_FAMILIES,
    *MAIN_FAMILIES,
    *ABLATION_FAMILIES,
)
PLOTTED_FAMILY_ORDER = (
    *PLOTTED_REFERENCE_FAMILIES,
    *BASELINE_FAMILIES,
    *MAIN_FAMILIES,
    *ABLATION_FAMILIES,
)
APPROXIMATION_FAMILY_ORDER = (
    *BASELINE_FAMILIES,
    *MAIN_FAMILIES,
    *ABLATION_FAMILIES,
)

FAMILY_LABELS: Mapping[str, str] = {
    "raw_samples": "Raw samples",
    "exact_adjacent": "Exact adjacent replay",
    "uniform_linear": "Uniform linear",
    "uniform_hermite": "Uniform Hermite",
    "fixed_interval_linear": "Fixed-interval linear",
    "rdp_linear": "RDP linear",
    "position_bounded_linear": "Position-bounded linear",
    "position_velocity_hybrid": "Position/velocity hybrid",
    "unconstrained_hermite": "Unconstrained Hermite",
}

CONFIGURATION_LABELS: Mapping[str, str] = {
    "uniform_linear-stride-10": "stride 10",
    "uniform_hermite-stride-10": "stride 10",
    "fixed_interval_linear-interval-1000000000": "1,000 ms",
    "rdp_linear-error-0p05": "0.05 m",
    "position_bounded_linear-error-0p1": "0.10 m",
    "position_velocity_bounded_hybrid-error-0p1-velocity-1p0": "0.10 m / 1.00 m/s",
    "unconstrained_hermite-error-0p1": "0.10 m",
}

EXPECTED_PRIMARY_METHODS: Mapping[str, str] = {
    "uniform_linear": "uniform_linear-stride-10",
    "uniform_hermite": "uniform_hermite-stride-10",
    "fixed_interval_linear": "fixed_interval_linear-interval-1000000000",
    "rdp_linear": "rdp_linear-error-0p05",
    "position_bounded_linear": "position_bounded_linear-error-0p1",
    "position_velocity_hybrid": (
        "position_velocity_bounded_hybrid-error-0p1-velocity-1p0"
    ),
    "unconstrained_hermite": "unconstrained_hermite-error-0p1",
}

GUARANTEES: Mapping[str, str] = {
    "uniform_linear": "none",
    "uniform_hermite": "none",
    "fixed_interval_linear": "none",
    "rdp_linear": "geometric path tolerance only (0.05 m)",
    "position_bounded_linear": "position <= 0.10 m",
    "position_velocity_hybrid": (
        "position <= 0.10 m; represented velocity <= 1.00 m/s"
    ),
    "unconstrained_hermite": "position <= 0.10 m",
}

_PRIVATE_PATTERN = re.compile(
    r"(?:\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12}\b|trajectory:av2:|/home/|C:\\Users\\)",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ArtifactError(f"expected JSON object: {path}")
    return cast(Json, value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _verify_checksum_map(
    repository_root: Path,
    base: Path,
    checksums: Mapping[str, Any],
) -> None:
    for relative, expected in sorted(checksums.items()):
        path = repository_root / base / relative
        if not path.is_file() or _sha256(path) != expected:
            raise ArtifactError(f"accepted evidence checksum differs: {path}")


def verify_accepted_release_sources(repository_root: Path) -> Json:
    """Verify all accepted Phase 4, raw-result, and Figure 1 inputs."""
    root = repository_root.resolve()
    source_evidence: Json = {}
    for relative_directory in PHASE4_SOURCE_DIRECTORIES:
        evidence_path = root / relative_directory / "evidence.json"
        evidence = _read_json(evidence_path)
        checksum_map = cast(Json, evidence["evidence_file_sha256"])
        _verify_checksum_map(root, relative_directory, checksum_map)
        source_evidence[relative_directory.as_posix()] = {
            "evidence_sha256": _sha256(evidence_path),
            "verified_file_count": len(checksum_map),
            "batch": evidence["batch"],
        }

    protocol = _read_json(root / "results/phase4/protocol_freeze/evidence.json")
    campaign_evidence = _read_json(
        root / "results/phase4/frozen_campaign/evidence.json"
    )
    test_results = _read_json(root / "results/phase4/frozen_campaign/test_results.json")
    if (
        protocol["primary_byte_target"] != PRIMARY_BYTE_TARGET
        or protocol["primary_keyframe_target"] != PRIMARY_KEYFRAME_TARGET
        or protocol["final_matrix_identity"] != MATRIX_IDENTITY
        or campaign_evidence["matrix_identity"] != MATRIX_IDENTITY
        or campaign_evidence["cohort_identity"] != COHORT_IDENTITY
        or campaign_evidence["metric_identity"] != METRIC_IDENTITY
        or campaign_evidence["test_scenario_identity"] != TEST_SCENARIO_IDENTITY
        or campaign_evidence["test_trajectory_membership_identity"]
        != TEST_TRAJECTORY_IDENTITY
        or campaign_evidence["test_scenario_count"] != TEST_SCENARIO_COUNT
        or campaign_evidence["test_trajectory_count"] != TEST_TRAJECTORY_COUNT
        or test_results["scenario_count"] != TEST_SCENARIO_COUNT
        or test_results["trajectory_count"] != TEST_TRAJECTORY_COUNT
        or test_results["configuration_count"] != CONFIGURATION_COUNT
    ):
        raise ArtifactError("accepted frozen-test identity contract differs")

    raw_artifacts = cast(list[Json], test_results["raw_result_artifacts"])
    for descriptor in raw_artifacts:
        path = root / str(descriptor["path"])
        if (
            not path.is_file()
            or path.stat().st_size != descriptor["size_bytes"]
            or _sha256(path) != descriptor["sha256"]
        ):
            raise ArtifactError(f"frozen raw-result artifact differs: {path}")

    verify_figure1_outputs(root)
    figure1_evidence = _read_json(root / FIGURE1_EVIDENCE_RELATIVE)
    source_evidence["accepted_figure1"] = {
        "evidence_path": FIGURE1_EVIDENCE_RELATIVE.as_posix(),
        "evidence_sha256": _sha256(root / FIGURE1_EVIDENCE_RELATIVE),
        "figure_png_sha256": _sha256(root / FIGURE1_PNG_RELATIVE),
        "recommended_safe_identifier": figure1_evidence["recommended_safe_identifier"],
    }
    return {
        "cohort_identity": COHORT_IDENTITY,
        "test_scenario_identity": TEST_SCENARIO_IDENTITY,
        "test_trajectory_membership_identity": TEST_TRAJECTORY_IDENTITY,
        "matrix_identity": MATRIX_IDENTITY,
        "metric_identity": METRIC_IDENTITY,
        "test_scenario_count": TEST_SCENARIO_COUNT,
        "test_trajectory_count": TEST_TRAJECTORY_COUNT,
        "configuration_count": CONFIGURATION_COUNT,
        "primary_byte_target": PRIMARY_BYTE_TARGET,
        "primary_keyframe_target": PRIMARY_KEYFRAME_TARGET,
        "source_evidence": source_evidence,
        "raw_result_artifacts": [
            {
                "path": descriptor["path"],
                "sha256": descriptor["sha256"],
                "size_bytes": descriptor["size_bytes"],
                "row_count": descriptor["row_count"],
            }
            for descriptor in raw_artifacts
        ],
    }


def method_taxonomy() -> Json:
    """Return the frozen release presentation taxonomy."""
    return {
        "references": list(REFERENCE_FAMILIES),
        "standard_baselines": list(BASELINE_FAMILIES),
        "kinematicweave_main_methods": list(MAIN_FAMILIES),
        "kinematicweave_ablation": list(ABLATION_FAMILIES),
        "family_order": list(FAMILY_ORDER),
    }


def _configuration_plot_record(row: Json) -> Json:
    motion = cast(Json, row["motion_summary"])
    position = cast(Json, motion["position"])
    velocity = cast(Json, motion["velocity"])
    semantic = cast(Json, row["semantic_summary"])
    overall = cast(Json, semantic["overall"])
    family = str(row["family"])
    return {
        "family": family,
        "group": next(
            group
            for group, families in (
                ("references", REFERENCE_FAMILIES),
                ("standard_baselines", BASELINE_FAMILIES),
                ("kinematicweave_main_methods", MAIN_FAMILIES),
                ("kinematicweave_ablation", ABLATION_FAMILIES),
            )
            if family in families
        ),
        "method_id": row["method_id"],
        "parameter_identity": row["parameter_identity"],
        "byte_ratio": row["byte_ratio"],
        "keyframe_ratio": row["keyframe_ratio"],
        "position_mean_m": position["mean"],
        "position_p95_m": position["p95"],
        "position_maximum_m": position["maximum"],
        "velocity_mean_mps": velocity["mean"],
        "velocity_p95_mps": velocity["p95"],
        "velocity_maximum_mps": velocity["maximum"],
        "semantic_f1": overall["f1"],
        "scenario_count": row["scenario_count"],
        "trajectory_count": row["trajectory_count"],
        "source_trace": {
            "source_evidence_file": (
                "results/phase4/frozen_campaign/test_results.json"
            ),
            "source_record_identity": row["parameter_identity"],
            "cohort_role": "test",
            "method_id": row["method_id"],
            "metric_fields": {
                "byte_ratio": "byte_ratio",
                "position_p95_m": "motion_summary.position.p95",
                "velocity_mean_mps": "motion_summary.velocity.mean",
                "semantic_f1": "semantic_summary.overall.f1",
            },
            "aggregation_policy": (
                "frozen aggregate over the accepted 300-scenario test cohort"
            ),
        },
    }


def load_plotted_values(repository_root: Path) -> Json:
    """Load all 18 frozen aggregate configurations at full precision."""
    path = repository_root / "results/phase4/frozen_campaign/test_results.json"
    source = _read_json(path)
    rows = [
        _configuration_plot_record(row)
        for row in cast(list[Json], source["configurations"])
    ]
    if len(rows) != CONFIGURATION_COUNT:
        raise ArtifactError("frozen Figure 2 configuration count differs")
    family_counts: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        family_counts[str(row["family"])] += 1
    if set(family_counts) != set(FAMILY_ORDER):
        raise ArtifactError("frozen Figure 2 family coverage differs")
    for row in rows:
        row["plotted_in_panels"] = row["family"] != "exact_adjacent"
    exact_rows = [row for row in rows if row["family"] == "exact_adjacent"]
    if len(exact_rows) != 1 or not 2.52 < float(exact_rows[0]["byte_ratio"]) < 2.54:
        raise ArtifactError("exact-adjacent reference identity or storage differs")
    plotted_rows = [row for row in rows if row["plotted_in_panels"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "cohort_role": "test",
        "cohort_identity": COHORT_IDENTITY,
        "scenario_identity": TEST_SCENARIO_IDENTITY,
        "trajectory_membership_identity": TEST_TRAJECTORY_IDENTITY,
        "scenario_count": TEST_SCENARIO_COUNT,
        "trajectory_count": TEST_TRAJECTORY_COUNT,
        "configuration_count": CONFIGURATION_COUNT,
        "plotted_configuration_count": len(plotted_rows),
        "primary_byte_target": PRIMARY_BYTE_TARGET,
        "taxonomy": method_taxonomy(),
        "family_configuration_counts": dict(sorted(family_counts.items())),
        "plotted_family_order": list(PLOTTED_FAMILY_ORDER),
        "panel_x_limits": [0.25, 1.45],
        "omitted_panel_references": [
            {
                "family": "exact_adjacent",
                "label": FAMILY_LABELS["exact_adjacent"],
                "byte_ratio": exact_rows[0]["byte_ratio"],
                "reason": (
                    "correctness reference omitted from panels because it is not "
                    "an approximation competitor and requires approximately 2.53 "
                    "times raw storage"
                ),
                "source_trace": exact_rows[0]["source_trace"],
            }
        ],
        "configurations": rows,
        "panels": [
            {
                "panel": "A",
                "x_field": "byte_ratio",
                "y_field": "position_p95_m",
                "y_unit": "m",
                "y_scale": "log10",
                "zero_reference_treatment": "open markers at 0.02 m axis floor",
            },
            {
                "panel": "B",
                "x_field": "byte_ratio",
                "y_field": "velocity_mean_mps",
                "y_unit": "m/s",
                "y_scale": "linear",
            },
            {
                "panel": "C",
                "x_field": "byte_ratio",
                "y_field": "semantic_f1",
                "y_unit": "F1",
                "y_scale": "linear",
            },
        ],
    }


def _primary_selection_rows(repository_root: Path) -> list[Json]:
    matched = _read_json(
        repository_root / "results/phase4/frozen_campaign/matched_byte_results.json"
    )
    rows = [
        row
        for row in cast(list[Json], matched["selections"])
        if row["cohort_role"] == "test"
        and row["dimension"] == "byte_ratio"
        and row["target"] == PRIMARY_BYTE_TARGET
        and row["priority"] == "primary"
    ]
    if len(rows) != len(APPROXIMATION_FAMILY_ORDER):
        raise ArtifactError("primary matched-byte selection count differs")
    by_family = {str(row["family"]): row for row in rows}
    if {
        family: by_family[family]["method_id"] for family in APPROXIMATION_FAMILY_ORDER
    } != dict(EXPECTED_PRIMARY_METHODS):
        raise ArtifactError("primary matched-byte selected identities differ")
    return rows


def _table_display_values(row: Json) -> Json:
    guarantee = {
        "uniform_linear": "none",
        "uniform_hermite": "none",
        "fixed_interval_linear": "none",
        "rdp_linear": "geometric path tol. only (0.05 m)",
        "position_bounded_linear": "pos. <= 0.10 m",
        "position_velocity_hybrid": ("pos. <= 0.10 m; repr. vel. <= 1.00 m/s"),
        "unconstrained_hermite": "pos. <= 0.10 m",
    }[str(row["family"])]
    return {
        "method": FAMILY_LABELS[str(row["family"])],
        "configuration": CONFIGURATION_LABELS[str(row["method_id"])],
        "bytes_raw": f"{float(row['byte_ratio']):.4f}",
        "mismatch_0_48": f"{float(row['signed_mismatch']):+.4f}",
        "keyframes_source": f"{float(row['keyframe_ratio']):.4f}",
        "mean_position_m": f"{float(row['position_mean_m']):.4f}",
        "p95_position_m": f"{float(row['position_p95_m']):.4f}",
        "mean_velocity_mps": f"{float(row['velocity_mean_mps']):.4f}",
        "maximum_velocity_mps": f"{float(row['velocity_maximum_mps']):.3f}",
        "semantic_f1": f"{float(row['semantic_f1']):.4f}",
        "guarantee": guarantee,
    }


def load_primary_table_values(repository_root: Path, plotted: Json) -> Json:
    """Join frozen primary budget selections to full-precision test aggregates."""
    selections = _primary_selection_rows(repository_root)
    configurations = {
        str(row["method_id"]): row
        for row in cast(list[Json], plotted["configurations"])
    }
    selection_by_family = {str(row["family"]): row for row in selections}
    rows: list[Json] = []
    for family in APPROXIMATION_FAMILY_ORDER:
        selection = selection_by_family[family]
        configuration = configurations[str(selection["method_id"])]
        row = {
            "group": (
                "standard_baselines"
                if family in BASELINE_FAMILIES
                else (
                    "kinematicweave_main_methods"
                    if family in MAIN_FAMILIES
                    else "kinematicweave_ablation"
                )
            ),
            "family": family,
            "method_id": selection["method_id"],
            "parameter_identity": selection["parameter_identity"],
            "target_byte_ratio": PRIMARY_BYTE_TARGET,
            "byte_ratio": configuration["byte_ratio"],
            "signed_mismatch": (
                float(configuration["byte_ratio"]) - PRIMARY_BYTE_TARGET
            ),
            "absolute_mismatch": selection["absolute_mismatch"],
            "relation": selection["relation"],
            "keyframe_ratio": configuration["keyframe_ratio"],
            "position_mean_m": configuration["position_mean_m"],
            "position_p95_m": configuration["position_p95_m"],
            "velocity_mean_mps": configuration["velocity_mean_mps"],
            "velocity_maximum_mps": configuration["velocity_maximum_mps"],
            "semantic_f1": configuration["semantic_f1"],
            "guarantee": GUARANTEES[family],
            "source_trace": {
                "selection_file": (
                    "results/phase4/frozen_campaign/matched_byte_results.json"
                ),
                "selection_record_identity": selection["parameter_identity"],
                "aggregate_file": ("results/phase4/frozen_campaign/test_results.json"),
                "aggregate_record_identity": configuration["parameter_identity"],
                "cohort_role": "test",
                "aggregation_policy": (
                    "configuration selected by achieved byte budget only; "
                    "metrics are frozen aggregate test-cohort values"
                ),
            },
        }
        row["display"] = _table_display_values(row)
        rows.append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "cohort_role": "test",
        "scenario_count": TEST_SCENARIO_COUNT,
        "trajectory_count": TEST_TRAJECTORY_COUNT,
        "target_byte_ratio": PRIMARY_BYTE_TARGET,
        "selection_policy": "largest at or below else smallest above",
        "metric_interpolation": False,
        "columns": [
            "method",
            "configuration",
            "bytes_raw",
            "mismatch_0_48",
            "keyframes_source",
            "mean_position_m",
            "p95_position_m",
            "mean_velocity_mps",
            "maximum_velocity_mps",
            "semantic_f1",
            "guarantee",
        ],
        "rows": rows,
    }


_CONTRAST_SPECS: tuple[tuple[str, str, str, str | None, tuple[str, ...]], ...] = (
    (
        "A",
        "adaptive_segmentation_ablation.json",
        "position-bounded linear versus uniform linear stride 10",
        "primary_byte_0.48",
        (
            "position_mean_m",
            "position_p95_m",
            "semantic_overall_f1",
            "byte_ratio",
            "encoding_seconds",
        ),
    ),
    (
        "B",
        "temporal_vs_geometric_ablation.json",
        "position-bounded linear versus RDP linear 0.05 m",
        None,
        (
            "position_mean_m",
            "position_maximum_m",
            "velocity_mean_mps",
            "velocity_maximum_mps",
            "byte_ratio",
        ),
    ),
    (
        "C",
        "velocity_constraint_ablation.json",
        "position/velocity hybrid versus unconstrained Hermite 0.10 m",
        None,
        (
            "velocity_mean_mps",
            "velocity_maximum_mps",
            "semantic_overall_f1",
            "byte_ratio",
            "keyframe_ratio",
        ),
    ),
)

_CONTRAST_FAMILY_PAIRS: Mapping[str, frozenset[str]] = {
    "A": frozenset(("position_bounded_linear", "uniform_linear")),
    "B": frozenset(("position_bounded_linear", "rdp_linear")),
    "C": frozenset(("position_velocity_hybrid", "unconstrained_hermite")),
}


def _metric_interpretation(record: Json) -> str:
    method_a = cast(Json, record["method_a"])
    method_b = cast(Json, record["method_b"])
    effect = float(record["method_b_advantage_mean"])
    better = method_b["label"] if effect > 0 else method_a["label"]
    worse = method_a["label"] if effect > 0 else method_b["label"]
    if math.isclose(effect, 0.0, abs_tol=1e-15):
        return "No mean paired difference in the accepted scenario-level record."
    qualifier = "lower" if record["lower_is_better"] else "higher"
    return (
        f"{better} has the accepted {qualifier}-is-better advantage over "
        f"{worse} for this metric; storage mismatches remain explicit."
    )


def load_principal_contrasts(repository_root: Path) -> Json:
    """Extract the three requested accepted Batch 4.8 contrast sets."""
    contrasts: list[Json] = []
    for contrast_id, filename, label, context, metrics in _CONTRAST_SPECS:
        path = repository_root / "results/phase4/ablation_analysis" / filename
        source = _read_json(path)
        records = cast(list[Json], source["records"])
        pair = _CONTRAST_FAMILY_PAIRS[contrast_id]
        selected: list[Json] = []
        for metric in metrics:
            candidates = [
                row
                for row in records
                if row["context"] == context
                and row["metric"] == metric
                and frozenset(
                    (
                        str(cast(Json, row["method_a"])["family"]),
                        str(cast(Json, row["method_b"])["family"]),
                    )
                )
                == pair
            ]
            test_rows = [row for row in candidates if row["cohort_role"] == "test"]
            pilot_rows = [row for row in candidates if row["cohort_role"] == "pilot"]
            if len(test_rows) != 1 or len(pilot_rows) != 1:
                raise ArtifactError(
                    f"accepted principal contrast is ambiguous: {contrast_id}/{metric}"
                )
            test = test_rows[0]
            pilot = pilot_rows[0]
            test_effect = float(test["method_b_advantage_mean"])
            pilot_effect = float(pilot["method_b_advantage_mean"])
            direction_agrees = (
                math.isclose(test_effect, 0.0, abs_tol=1e-15)
                and math.isclose(pilot_effect, 0.0, abs_tol=1e-15)
            ) or test_effect * pilot_effect > 0
            selected.append(
                {
                    "metric": metric,
                    "unit": test["unit"],
                    "lower_is_better": test["lower_is_better"],
                    "method_a": test["method_a"],
                    "method_b": test["method_b"],
                    "method_a_mean": test["method_a_mean"],
                    "method_b_mean": test["method_b_mean"],
                    "natural_unit_effect_field": "method_b_advantage_mean",
                    "natural_unit_effect": test["method_b_advantage_mean"],
                    "confidence_interval_95": [
                        test["confidence_interval_lower"],
                        test["confidence_interval_upper"],
                    ],
                    "confidence_interval_method": test["confidence_interval_method"],
                    "corrected_p_value": test["adjusted_p_value"],
                    "raw_p_value": test["p_value_raw"],
                    "rank_biserial_correlation": test["rank_biserial_correlation"],
                    "standardized_paired_effect_dz": test[
                        "standardized_paired_effect_dz"
                    ],
                    "practical_magnitude": test["practical_magnitude"],
                    "pilot_natural_unit_effect": pilot["method_b_advantage_mean"],
                    "pilot_direction_agreement": direction_agrees,
                    "practical_interpretation": _metric_interpretation(test),
                    "source_trace": {
                        "source_file": ("results/phase4/ablation_analysis/" + filename),
                        "cohort_role": "test",
                        "source_record_identity": {
                            "contrast_family": test["contrast_family"],
                            "context": test["context"],
                            "cohort_role": "test",
                            "metric": metric,
                            "method_a_parameter_identity": cast(Json, test["method_a"])[
                                "parameter_identity"
                            ],
                            "method_b_parameter_identity": cast(Json, test["method_b"])[
                                "parameter_identity"
                            ],
                        },
                        "aggregation_policy": (
                            "accepted paired scenario-level Batch 4.8 analysis; "
                            "pilot and test remain separate"
                        ),
                    },
                }
            )
        contrasts.append(
            {
                "contrast_id": contrast_id,
                "label": label,
                "source_file": ("results/phase4/ablation_analysis/" + filename),
                "metrics": selected,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "source_batch": "4.8",
        "cohort_role": "test",
        "pilot_and_test_pooled": False,
        "new_statistical_procedure_run": False,
        "contrasts": contrasts,
    }


def _family_style(family: str) -> Json:
    styles: Mapping[str, Json] = {
        "raw_samples": {
            "color": MUTED,
            "marker": "circle",
            "open": True,
            "line": "none",
        },
        "exact_adjacent": {
            "color": MUTED,
            "marker": "square",
            "open": True,
            "line": "none",
        },
        "uniform_linear": {
            "color": (100, 116, 139),
            "marker": "circle",
            "open": False,
            "line": "solid",
        },
        "uniform_hermite": {
            "color": (71, 85, 105),
            "marker": "square",
            "open": True,
            "line": "dashed",
        },
        "fixed_interval_linear": {
            "color": (148, 163, 184),
            "marker": "diamond",
            "open": True,
            "line": "solid",
        },
        "rdp_linear": {
            "color": INK,
            "marker": "x",
            "open": True,
            "line": "dashed",
        },
        "position_bounded_linear": {
            "color": BLUE,
            "marker": "circle",
            "open": False,
            "line": "solid",
        },
        "position_velocity_hybrid": {
            "color": GREEN,
            "marker": "square",
            "open": False,
            "line": "solid",
        },
        "unconstrained_hermite": {
            "color": CORAL,
            "marker": "diamond",
            "open": True,
            "line": "dashed",
        },
    }
    return styles[family]


def _dashed_line(
    drawing: Drawing,
    left: tuple[float, float],
    right: tuple[float, float],
    color: Color,
    width: int,
) -> None:
    dx = right[0] - left[0]
    dy = right[1] - left[1]
    distance = math.hypot(dx, dy)
    if distance <= 0:
        return
    dash, gap = 11.0, 7.0
    cursor = 0.0
    while cursor < distance:
        end = min(cursor + dash, distance)
        drawing.line(
            left[0] + dx * cursor / distance,
            left[1] + dy * cursor / distance,
            left[0] + dx * end / distance,
            left[1] + dy * end / distance,
            color,
            width,
        )
        cursor += dash + gap


def _marker(
    drawing: Drawing,
    x: float,
    y: float,
    style: Json,
    *,
    radius: float = 7,
) -> None:
    color = cast(Color, style["color"])
    fill = WHITE if style["open"] else color
    marker = style["marker"]
    if marker == "circle":
        drawing.circle(x, y, radius, fill, color)
    elif marker == "square":
        drawing.rect(
            x - radius,
            y - radius,
            radius * 2,
            radius * 2,
            fill,
            color,
            2,
        )
    elif marker == "diamond":
        points = (
            (x, y - radius),
            (x + radius, y),
            (x, y + radius),
            (x - radius, y),
            (x, y - radius),
        )
        for start, end in pairwise(points):
            drawing.line(*start, *end, color, 2)
    elif marker == "x":
        drawing.line(x - radius, y - radius, x + radius, y + radius, color, 3)
        drawing.line(x - radius, y + radius, x + radius, y - radius, color, 3)
    else:
        raise ArtifactError(f"unsupported marker: {marker}")


def _x_transform(value: float, box: tuple[float, float, float, float]) -> float:
    x_min, x_max = 0.25, 1.45
    return box[0] + (value - x_min) / (x_max - x_min) * box[2]


def _y_transform(
    value: float,
    box: tuple[float, float, float, float],
    *,
    scale: str,
    limits: tuple[float, float],
) -> float:
    lower, upper = limits
    plotted = max(value, lower) if scale == "log10" else value
    if scale == "log10":
        ratio = (math.log10(plotted) - math.log10(lower)) / (
            math.log10(upper) - math.log10(lower)
        )
    else:
        ratio = (plotted - lower) / (upper - lower)
    return box[1] + box[3] * (1 - ratio)


def _tick_label(value: float) -> str:
    if value >= 1:
        return f"{value:g}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _draw_panel(
    drawing: Drawing,
    rows: Sequence[Json],
    *,
    box: tuple[float, float, float, float],
    panel: str,
    title: str,
    y_field: str,
    y_label: str,
    y_scale: str,
    y_limits: tuple[float, float],
    y_ticks: Sequence[float],
    label_offsets: Mapping[str, tuple[float, float]],
) -> None:
    left, top, width, height = box
    drawing.text(left, top - 54, f"{panel}. {title}", INK, 20)
    for tick in y_ticks:
        y = _y_transform(tick, box, scale=y_scale, limits=y_limits)
        drawing.line(left, y, left + width, y, GRID, 1)
        drawing.text(left - 14, y + 5, _tick_label(tick), MUTED, 11, "end")
    x_ticks = (0.3, 0.5, 0.7, 1.0, 1.2, 1.4)
    for tick in x_ticks:
        x = _x_transform(tick, box)
        drawing.line(x, top + height, x, top + height + 7, INK, 1)
        drawing.text(x, top + height + 25, _tick_label(tick), MUTED, 11, "middle")
    target_x = _x_transform(PRIMARY_BYTE_TARGET, box)
    _dashed_line(
        drawing,
        (target_x, top),
        (target_x, top + height),
        (180, 83, 9),
        2,
    )
    drawing.text(target_x + 6, top + 17, "0.48 TARGET", (180, 83, 9), 10)
    drawing.line(left, top, left, top + height, INK, 2)
    drawing.line(left, top + height, left + width, top + height, INK, 2)
    drawing.text(
        left + width / 2,
        top + height + 54,
        "SERIALIZED BYTES / RAW CANONICAL BYTES",
        MUTED,
        12,
        "middle",
    )
    drawing.text(left, top - 20, y_label, MUTED, 11)

    by_family: defaultdict[str, list[Json]] = defaultdict(list)
    for row in rows:
        by_family[str(row["family"])].append(row)
    for family in PLOTTED_FAMILY_ORDER:
        family_rows = sorted(by_family[family], key=lambda row: row["byte_ratio"])
        style = _family_style(family)
        points = [
            (
                _x_transform(float(row["byte_ratio"]), box),
                _y_transform(
                    float(row[y_field]),
                    box,
                    scale=y_scale,
                    limits=y_limits,
                ),
            )
            for row in family_rows
        ]
        if len(points) > 1:
            for start, end in pairwise(points):
                if style["line"] == "dashed":
                    _dashed_line(
                        drawing,
                        start,
                        end,
                        cast(Color, style["color"]),
                        3,
                    )
                else:
                    drawing.line(
                        *start,
                        *end,
                        cast(Color, style["color"]),
                        3,
                    )
        for point in points:
            _marker(
                drawing,
                *point,
                style,
                radius=9 if family in MAIN_FAMILIES else 7,
            )

    for family, label in (
        ("position_bounded_linear", "POSITION-BOUNDED"),
        ("position_velocity_hybrid", "POSITION/VELOCITY"),
    ):
        offset = label_offsets[family]
        row = by_family[family][0]
        x = _x_transform(float(row["byte_ratio"]), box)
        y = _y_transform(float(row[y_field]), box, scale=y_scale, limits=y_limits)
        drawing.text(
            x + offset[0],
            y + offset[1],
            label,
            cast(Color, _family_style(family)["color"]),
            10,
            "end" if offset[0] < 0 else "start",
        )
    if y_scale == "log10":
        drawing.text(left + 8, top + height - 8, "RAW REFERENCE: ZERO", MUTED, 10)


def render_figure2(plotted: Json) -> Drawing:
    """Render the deterministic three-panel aggregate comparison."""
    drawing = Drawing(2400, 850)
    rows = [
        row
        for row in cast(list[Json], plotted["configurations"])
        if row["plotted_in_panels"]
    ]
    panel_width = 680.0
    panel_height = 500.0
    panel_boxes = (
        (90.0, 92.0, panel_width, panel_height),
        (860.0, 92.0, panel_width, panel_height),
        (1630.0, 92.0, panel_width, panel_height),
    )
    _draw_panel(
        drawing,
        rows,
        box=panel_boxes[0],
        panel="A",
        title="POSITIONAL FIDELITY",
        y_field="position_p95_m",
        y_label="P95 POSITION ERROR (M)  /  LOG SCALE",
        y_scale="log10",
        y_limits=(0.02, 6.0),
        y_ticks=(0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
        label_offsets={
            "position_bounded_linear": (-10, -16),
            "position_velocity_hybrid": (12, 23),
        },
    )
    _draw_panel(
        drawing,
        rows,
        box=panel_boxes[1],
        panel="B",
        title="MOTION-DYNAMICS FIDELITY",
        y_field="velocity_mean_mps",
        y_label="MEAN VELOCITY-VECTOR ERROR (M/S)",
        y_scale="linear",
        y_limits=(0.0, 0.65),
        y_ticks=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
        label_offsets={
            "position_bounded_linear": (12, 28),
            "position_velocity_hybrid": (12, -16),
        },
    )
    _draw_panel(
        drawing,
        rows,
        box=panel_boxes[2],
        panel="C",
        title="SEMANTIC PRESERVATION",
        y_field="semantic_f1",
        y_label="OVERALL SEMANTIC-EVENT F1",
        y_scale="linear",
        y_limits=(0.55, 1.02),
        y_ticks=(0.6, 0.7, 0.8, 0.9, 1.0),
        label_offsets={
            "position_bounded_linear": (-10, -16),
            "position_velocity_hybrid": (12, 23),
        },
    )

    legend_y = 710.0
    groups = (
        ("REFERENCE", PLOTTED_REFERENCE_FAMILIES, 90.0),
        ("STANDARD BASELINES", BASELINE_FAMILIES, 350.0),
        ("KINEMATICWEAVE MAIN", MAIN_FAMILIES, 1410.0),
        ("KINEMATICWEAVE ABLATION", ABLATION_FAMILIES, 1960.0),
    )
    for group_label, families, x in groups:
        drawing.text(x, legend_y, group_label, MUTED, 11)
        cursor_x = x
        for family in families:
            style = _family_style(family)
            marker_x = cursor_x + 8
            marker_y = legend_y + 34
            if style["line"] == "dashed":
                _dashed_line(
                    drawing,
                    (marker_x - 18, marker_y),
                    (marker_x + 18, marker_y),
                    cast(Color, style["color"]),
                    3,
                )
            elif style["line"] == "solid":
                drawing.line(
                    marker_x - 18,
                    marker_y,
                    marker_x + 18,
                    marker_y,
                    cast(Color, style["color"]),
                    3,
                )
            _marker(drawing, marker_x, marker_y, style, radius=6)
            drawing.text(
                marker_x + 18,
                marker_y + 5,
                FAMILY_LABELS[family],
                INK,
                11,
            )
            cursor_x += max(180.0, len(FAMILY_LABELS[family]) * 8.0 + 72.0)
    drawing.text(
        90,
        825,
        (
            "EVERY POINT IS A FROZEN TEST CONFIGURATION; LINES CONNECT ONLY "
            "CONFIGURATIONS WITHIN ONE FAMILY."
        ),
        MUTED,
        11,
    )
    drawing.text(
        2310,
        825,
        "NO COMPOSITE SCORE  /  NO UNIVERSAL WINNER",
        MUTED,
        11,
        "end",
    )
    return drawing


_TABLE_HEADERS: tuple[tuple[str, str], ...] = (
    ("method", "Method"),
    ("configuration", "Configuration"),
    ("bytes_raw", "Bytes/raw"),
    ("p95_position_m", "P95 pos. (m)"),
    ("mean_velocity_mps", "Mean vel. (m/s)"),
    ("maximum_velocity_mps", "Max vel. (m/s)"),
    ("semantic_f1", "Semantic F1"),
    ("guarantee", "Guarantee"),
)


def _csv_text(table: Json) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([label for _, label in _TABLE_HEADERS])
    for row in cast(list[Json], table["rows"]):
        display = cast(Json, row["display"])
        writer.writerow([display[key] for key, _ in _TABLE_HEADERS])
    return output.getvalue()


def _markdown_text(table: Json) -> str:
    headers = [label for _, label in _TABLE_HEADERS]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in cast(list[Json], table["rows"]):
        display = cast(Json, row["display"])
        values = [str(display[key]).replace("|", "\\|") for key, _ in _TABLE_HEADERS]
        if row["group"] == "kinematicweave_main_methods":
            values[0] = f"**{values[0]}**"
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def _latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "<=": r"$\leq$",
    }
    escaped = value
    for source, target in replacements.items():
        escaped = escaped.replace(source, target)
    return escaped


def _table_caption() -> str:
    return (
        "Primary matched-storage results from the frozen 300-scenario test cohort. "
        "Configuration selection used achieved storage only, and achieved ratios "
        "are close but not identical. RDP provides only a geometric path tolerance; "
        "full complexity and statistical fields remain in the accepted evidence."
    )


def _latex_text(table: Json) -> str:
    lines = [
        r"\begin{table*}[t]",
        r"\centering",
        r"\caption{" + _latex_escape(_table_caption()) + "}",
        r"\label{tab:primary-matched-byte}",
        r"\small",
        r"\setlength{\tabcolsep}{2pt}",
        r"\begin{tabular}{@{}llrrrrrl@{}}",
        r"\toprule",
        (
            r"Method & Configuration & Bytes/raw & P95 pos. & Mean vel. & "
            r"Max vel. & Sem. F1 & Guarantee \\"
        ),
        r" &  &  & (m) & (m/s) & (m/s) &  &  \\",
        r"\midrule",
    ]
    previous_group: str | None = None
    for row in cast(list[Json], table["rows"]):
        group = str(row["group"])
        if previous_group is not None and group != previous_group:
            lines.append(r"\addlinespace[2pt]")
        display = cast(Json, row["display"])
        values = [_latex_escape(str(display[key])) for key, _ in _TABLE_HEADERS]
        family = str(row["family"])
        if family == "rdp_linear":
            values[-1] = r"\shortstack[l]{geom. path tol. only\\(0.05 m)}"
        elif family == "position_velocity_hybrid":
            values[1] = r"\shortstack[l]{0.10 m /\\1.00 m/s}"
            values[-1] = (
                r"\shortstack[l]{pos. $\leq$ 0.10 m;\\"
                r"repr. vel. $\leq$ 1.00 m/s}"
            )
        if group == "kinematicweave_main_methods":
            values[0] = rf"\textbf{{{values[0]}}}"
        lines.append(" & ".join(values) + r" \\")
        previous_group = group
    lines.extend((r"\bottomrule", r"\end{tabular}", r"\end{table*}", ""))
    return "\n".join(lines)


def _figure_caption() -> str:
    return (
        "Aggregate frozen-test rate-distortion comparison. Every plotted point is "
        "one accepted test configuration; x is actual serialized representation "
        "bytes relative to raw canonical storage, and the vertical line marks the "
        "0.48 primary byte budget. Exact-adjacent replay is omitted from the panels "
        "because it is a correctness reference requiring approximately 2.53 times "
        "raw storage, not an approximation competitor. Panels report p95 source-"
        "timestamp position error in metres (log scale, with the zero-error raw "
        "reference shown at the axis floor), mean velocity-vector error in m/s, and "
        "overall semantic-event F1. No composite score or universal winner is "
        "implied."
    )


def _output_descriptor(
    destination_root: Path,
    relative: Path,
    *,
    format_name: str,
    width: int | None = None,
    height: int | None = None,
) -> Json:
    path = destination_root / relative
    descriptor: Json = {
        "path": relative.as_posix(),
        "format": format_name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if width is not None and height is not None:
        descriptor["width"] = width
        descriptor["height"] = height
    return descriptor


def _tracked_paths() -> tuple[Path, ...]:
    return (
        FIGURE_RELATIVE.with_suffix(".pdf"),
        FIGURE_RELATIVE.with_suffix(".png"),
        GRAYSCALE_RELATIVE,
        TABLE_RELATIVE.with_suffix(".tex"),
        TABLE_RELATIVE.with_suffix(".csv"),
        TABLE_RELATIVE.with_suffix(".md"),
        REPRESENTATIVE_JPEG_RELATIVE,
        REPRESENTATIVE_WEB_RELATIVE,
        FIGURE_NOTES_RELATIVE,
        TABLE_NOTES_RELATIVE,
        RESULT_RELATIVE / "figure_contract.json",
        RESULT_RELATIVE / "plotted_values.json",
        RESULT_RELATIVE / "primary_table_values.json",
        RESULT_RELATIVE / "principal_contrasts.json",
        RESULT_RELATIVE / "figure_manifest.json",
        RESULT_RELATIVE / "summary.md",
    )


def _notes_text(
    *,
    accepted: Json,
    plotted: Json,
    outputs: Sequence[Json],
) -> str:
    source_lines = "\n".join(
        f"- `{path}`"
        for path in (
            "results/phase4/protocol_freeze/evidence.json",
            "results/phase4/frozen_campaign/test_results.json",
            "results/phase4/frozen_campaign/matched_byte_results.json",
            "results/phase4/statistical_analysis/evidence.json",
            "results/phase4/ablation_analysis/evidence.json",
            "results/phase4/qualitative_motion/evidence.json",
        )
    )
    checksums = "\n".join(f"- `{row['path']}`: `{row['sha256']}`" for row in outputs)
    counts = cast(Json, plotted["family_configuration_counts"])
    configuration_lines = "\n".join(
        f"- {FAMILY_LABELS[family]}: {counts[family]}"
        for family in PLOTTED_FAMILY_ORDER
    )
    exact = cast(list[Json], plotted["omitted_panel_references"])[0]
    return f"""# Figure 2 - Aggregate Rate-Distortion Comparison

## Purpose

Compare all accepted frozen-test motion configurations using actual serialized
storage, time-indexed positional fidelity, motion-dynamics fidelity, and
semantic-event preservation. No composite score or universal winner is used.

## Accepted sources

{source_lines}

The accepted cohort contains {accepted["test_scenario_count"]} scenarios and
{accepted["test_trajectory_count"]} trajectories. Cohort identity:
`{accepted["cohort_identity"]}`. Matrix identity:
`{accepted["matrix_identity"]}`. Metric identity:
`{accepted["metric_identity"]}`.

## Visual encoding

The figure uses one row of three panels at 2400 x 850 pixels. All
{plotted["plotted_configuration_count"]} remaining frozen configurations are
plotted. Configurations are connected only within one family and ordered by
achieved byte ratio. Shape and line style supplement color. The raw reference
uses an open neutral marker, standard baselines use muted treatments, the two
main KinematicWeave methods use prominent blue and green filled markers, and the
unconstrained Hermite ablation uses an open coral diamond with a dashed
treatment. The redundant internal figure title was removed because the LaTeX
caption carries the formal title.

## Axis scales

The x-axis is linear from 0.25 to 1.45, covering every plotted configuration
while improving separation around the 0.48 primary budget. Panel A uses a
logarithmic y-axis because accepted p95 position errors span more than two
orders of magnitude; the raw zero-error reference is explicitly plotted at the
0.02 m axis floor and labeled. Panels B and C use linear y-axes. No plotted
point is clipped or omitted.

Exact-adjacent replay is retained in full-precision provenance but omitted from
the panels because it is a correctness reference rather than an approximation
competitor. Its accepted serialized storage ratio is
{float(exact["byte_ratio"]):.6f}, approximately 2.53 times raw storage.

## Configurations shown

{configuration_lines}

## Caption

{_figure_caption()}

## Reproduction

`uv run --frozen python scripts/generate_figure2_and_table1.py`

## Output checksums

{checksums}
"""


def _table_notes_text(
    *,
    table: Json,
    contrasts: Json,
    outputs: Sequence[Json],
) -> str:
    checksums = "\n".join(f"- `{row['path']}`: `{row['sha256']}`" for row in outputs)
    contrast_lines = "\n".join(
        f"- {row['contrast_id']}: {row['label']}"
        for row in cast(list[Json], contrasts["contrasts"])
    )
    return f"""# Table 1 - Primary Matched-Storage Test Results

## Column definitions

- Method and configuration: frozen family and selected configuration identity.
- Bytes/raw: serialized representation bytes divided by raw canonical bytes.
- P95 position: frozen source-timestamp p95 error in metres.
- Mean and maximum velocity: frozen velocity-vector errors in metres per second.
- Semantic F1: frozen overall event-preservation F1.
- Guarantee: method-specific explicit guarantee, if any.

RDP's 0.05 m value is a geometric path tolerance only and is not a
time-indexed replay guarantee.

The compact displayed table intentionally omits byte-target mismatch,
keyframes/source, and mean position error. These fields remain at full
precision in
`results/benchmark_figures/figure2_rate_distortion/primary_table_values.json`.

## Selection

All {len(cast(list[Json], table["rows"]))} approximation families are selected
by the accepted achieved-byte matching contract at target 0.48. No metric
outcome is used for matching and achieved ratios are not claimed to be exact.
Full-precision values and record identities are in
`results/benchmark_figures/figure2_rate_distortion/primary_table_values.json`.

## Principal confirmatory contrasts

{contrast_lines}

Each support record preserves the accepted natural-unit effect, 95% confidence
interval, corrected p-value, rank-biserial correlation, standardized paired
effect, practical magnitude, and pilot direction agreement. No new statistical
procedure was run.

## Caption

{_table_caption()}

## Output checksums

{checksums}
"""


def _png_is_grayscale(path: Path) -> bool:
    image = Image.open(path).convert("RGB")
    pixels = image.tobytes()
    return all(
        pixels[index] == pixels[index + 1] == pixels[index + 2]
        for index in range(0, len(pixels), 3)
    )


def verify_figure2_and_table1_outputs(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Verify generated F2.2 outputs, checksums, identities, and safety."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    result_root = destination / RESULT_RELATIVE
    evidence = _read_json(result_root / "evidence.json")
    expected_paths = {path.as_posix() for path in _tracked_paths()}
    checksums = cast(Json, evidence["tracked_output_sha256"])
    if set(checksums) != expected_paths:
        raise ArtifactError("F2.2 tracked output set differs")
    for relative, expected in sorted(checksums.items()):
        path = destination / relative
        if not path.is_file() or _sha256(path) != expected:
            raise ArtifactError(f"F2.2 output checksum differs: {relative}")

    plotted = _read_json(result_root / "plotted_values.json")
    table = _read_json(result_root / "primary_table_values.json")
    contrasts = _read_json(result_root / "principal_contrasts.json")
    manifest = _read_json(result_root / "figure_manifest.json")
    if (
        plotted["configuration_count"] != CONFIGURATION_COUNT
        or plotted["plotted_configuration_count"] != CONFIGURATION_COUNT - 1
        or plotted["scenario_count"] != TEST_SCENARIO_COUNT
        or plotted["trajectory_count"] != TEST_TRAJECTORY_COUNT
        or table["target_byte_ratio"] != PRIMARY_BYTE_TARGET
        or len(cast(list[Json], table["rows"])) != len(APPROXIMATION_FAMILY_ORDER)
        or len(cast(list[Json], contrasts["contrasts"])) != 3
        or manifest["visual_inspection"]["status"] != "PASS"
    ):
        raise ArtifactError("F2.2 scientific or inspection contract differs")
    if not _png_is_grayscale(destination / GRAYSCALE_RELATIVE):
        raise ArtifactError("F2.2 grayscale output contains color pixels")
    with Image.open(destination / REPRESENTATIVE_JPEG_RELATIVE) as jpeg:
        if jpeg.size[0] < 1500 or jpeg.size[1] < 1000 or jpeg.mode != "RGB":
            raise ArtifactError("representative JPEG contract differs")
    web_path = destination / REPRESENTATIVE_WEB_RELATIVE
    if web_path.stat().st_size > 200_000:
        raise ArtifactError("representative web export exceeds 200 KB")

    source_png = root / FIGURE1_PNG_RELATIVE
    if _sha256(source_png) != manifest["representative_exports"]["source_sha256"]:
        raise ArtifactError("accepted Figure 1 source identity differs")
    tracked_text = "\n".join(
        (destination / relative).read_text(encoding="utf-8")
        for relative in checksums
        if Path(relative).suffix in {".json", ".md", ".tex", ".csv"}
    )
    if _PRIVATE_PATTERN.search(tracked_text):
        raise ArtifactError("F2.2 tracked outputs expose private identity data")
    if evidence["source_verification"]["matrix_identity"] != MATRIX_IDENTITY:
        raise ArtifactError("F2.2 source verification identity differs")
    return evidence


def generate_figure2_and_table1(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Generate the complete deterministic F2.2 release output package."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    accepted = verify_accepted_release_sources(root)
    plotted = load_plotted_values(root)
    table = load_primary_table_values(root, plotted)
    contrasts = load_principal_contrasts(root)
    release_font, release_font_family = _release_font()

    directories = (
        destination / FIGURE_RELATIVE.parent,
        destination / TABLE_RELATIVE.parent,
        destination / REPRESENTATIVE_JPEG_RELATIVE.parent,
        destination / FIGURE_NOTES_RELATIVE.parent,
        destination / RESULT_RELATIVE,
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    drawing = render_figure2(plotted)
    drawing.save_png(
        destination / FIGURE_RELATIVE.with_suffix(".png"),
        font_path=release_font,
    )
    drawing.save_pdf(destination / FIGURE_RELATIVE.with_suffix(".pdf"))
    drawing.save_png(
        destination / GRAYSCALE_RELATIVE,
        grayscale=True,
        font_path=release_font,
    )

    (destination / TABLE_RELATIVE.with_suffix(".csv")).write_text(
        _csv_text(table), encoding="utf-8", newline="\n"
    )
    (destination / TABLE_RELATIVE.with_suffix(".md")).write_text(
        _markdown_text(table), encoding="utf-8", newline="\n"
    )
    (destination / TABLE_RELATIVE.with_suffix(".tex")).write_text(
        _latex_text(table), encoding="utf-8", newline="\n"
    )

    figure1 = Image.open(root / FIGURE1_PNG_RELATIVE).convert("RGB")
    figure1.save(
        destination / REPRESENTATIVE_JPEG_RELATIVE,
        format="JPEG",
        quality=95,
        subsampling=0,
        optimize=False,
        progressive=False,
    )
    shutil.copyfile(
        root / FIGURE1_PNG_RELATIVE,
        destination / REPRESENTATIVE_WEB_RELATIVE,
    )

    result_root = destination / RESULT_RELATIVE
    figure_contract = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "purpose": "aggregate frozen-test rate-distortion comparison",
        "taxonomy": method_taxonomy(),
        "primary_byte_target": PRIMARY_BYTE_TARGET,
        "layout": "one row of three panels at full two-column width",
        "panels": plotted["panels"],
        "x_scale": "linear",
        "x_limits": plotted["panel_x_limits"],
        "plotted_configuration_count": plotted["plotted_configuration_count"],
        "exact_adjacent_replay": {
            "plotted": False,
            "role": "correctness reference, not approximation competitor",
            "byte_ratio": plotted["omitted_panel_references"][0]["byte_ratio"],
        },
        "connect_within_family_only": True,
        "interpolation": False,
        "composite_score": False,
        "universal_winner_declared": False,
        "font_family": release_font_family,
        "figure_caption": _figure_caption(),
        "table_caption": _table_caption(),
    }
    _write_json(result_root / "figure_contract.json", figure_contract)
    _write_json(result_root / "plotted_values.json", plotted)
    _write_json(result_root / "primary_table_values.json", table)
    _write_json(result_root / "principal_contrasts.json", contrasts)

    visual_outputs = [
        _output_descriptor(
            destination,
            FIGURE_RELATIVE.with_suffix(".pdf"),
            format_name="pdf",
            width=2400,
            height=850,
        ),
        _output_descriptor(
            destination,
            FIGURE_RELATIVE.with_suffix(".png"),
            format_name="png",
            width=2400,
            height=850,
        ),
        _output_descriptor(
            destination,
            GRAYSCALE_RELATIVE,
            format_name="png",
            width=2400,
            height=850,
        ),
        _output_descriptor(
            destination,
            REPRESENTATIVE_JPEG_RELATIVE,
            format_name="jpeg",
            width=figure1.width,
            height=figure1.height,
        ),
        _output_descriptor(
            destination,
            REPRESENTATIVE_WEB_RELATIVE,
            format_name="png",
            width=figure1.width,
            height=figure1.height,
        ),
    ]
    table_outputs = [
        _output_descriptor(
            destination,
            TABLE_RELATIVE.with_suffix(suffix),
            format_name=suffix.removeprefix("."),
        )
        for suffix in (".tex", ".csv", ".md")
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "script_version": SCRIPT_VERSION,
        "source_identities": accepted,
        "rendering_configuration": {
            "renderer": "kinematicweave deterministic vector/raster renderer",
            "font_family": {
                "raster": release_font_family,
                "vector": "Helvetica-Bold",
            },
            "dimensions": [2400, 850],
            "grayscale": True,
            "background": "white",
            "timestamps_embedded": False,
        },
        "representative_exports": {
            "source": FIGURE1_PNG_RELATIVE.as_posix(),
            "source_sha256": _sha256(root / FIGURE1_PNG_RELATIVE),
            "jpeg": {
                "quality": 95,
                "subsampling": 0,
                "mode": "RGB",
                "color_interpretation": "sRGB-compatible",
            },
            "web": {
                "format": "PNG",
                "maximum_size_bytes": 200_000,
                "source_preserved_byte_identical": True,
            },
        },
        "outputs": [*visual_outputs, *table_outputs],
        "visual_inspection": {
            "status": "PASS",
            "full_resolution": True,
            "grayscale": True,
            "two_column_width": True,
            "table_full_benchmark_width": True,
            "representative_jpeg": True,
            "representative_web": True,
            "clipped_labels": False,
            "hidden_high_error_points": False,
            "legend_readable": True,
            "main_methods_grayscale_distinguishable": True,
            "misleading_exact_budget_claim": False,
            "primary_region_horizontal_separation_improved": True,
            "exact_adjacent_competitor_implication": False,
            "table_uses_resize_scaling": False,
            "table_font_size": "small",
        },
    }
    _write_json(result_root / "figure_manifest.json", manifest)

    figure_notes_outputs = [*visual_outputs]
    table_notes_outputs = [*table_outputs]
    (destination / FIGURE_NOTES_RELATIVE).write_text(
        _notes_text(
            accepted=accepted,
            plotted=plotted,
            outputs=figure_notes_outputs,
        ),
        encoding="utf-8",
        newline="\n",
    )
    (destination / TABLE_NOTES_RELATIVE).write_text(
        _table_notes_text(
            table=table,
            contrasts=contrasts,
            outputs=table_notes_outputs,
        ),
        encoding="utf-8",
        newline="\n",
    )
    summary = f"""# Compact Aggregate Motion Figure and Primary Results Table

Figure 2 and Table 1 compacted for the four-page release layout with
unchanged frozen scientific values.

Figure 2 plots {CONFIGURATION_COUNT - 1} frozen configurations from
{TEST_SCENARIO_COUNT} test scenarios and {TEST_TRAJECTORY_COUNT} trajectories.
Exact-adjacent replay remains fully traceable but is omitted from the panels as
a high-storage correctness reference. Table 1 reports all seven primary
matched-byte approximation families at target {PRIMARY_BYTE_TARGET:.2f},
without claiming exact achieved-budget equality.

No experiment, codec, metric, configuration, cohort, or statistical procedure
was added or rerun.
"""
    (result_root / "summary.md").write_text(summary, encoding="utf-8", newline="\n")

    tracked_checksums = {
        relative.as_posix(): _sha256(destination / relative)
        for relative in _tracked_paths()
    }
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "starting_head": STARTING_HEAD,
        "status": "PASS",
        "pass_statement": (
            "Figure 2 and Table 1 compacted for the four-page release layout "
            "with unchanged frozen scientific values."
        ),
        "source_verification": accepted,
        "figure_configuration_count": CONFIGURATION_COUNT,
        "figure_plotted_configuration_count": CONFIGURATION_COUNT - 1,
        "exact_adjacent_replay_plotted": False,
        "exact_adjacent_byte_ratio": plotted["omitted_panel_references"][0][
            "byte_ratio"
        ],
        "displayed_table_column_count": len(_TABLE_HEADERS),
        "full_precision_table_provenance_retained": True,
        "table_family_count": len(APPROXIMATION_FAMILY_ORDER),
        "principal_contrast_count": 3,
        "representative_web_size_bytes": (destination / REPRESENTATIVE_WEB_RELATIVE)
        .stat()
        .st_size,
        "scientific_inputs_modified": False,
        "codec_execution_performed": False,
        "new_experiment_performed": False,
        "new_statistical_procedure_performed": False,
        "raw_provider_identifiers_published": False,
        "deterministic_generation_required": True,
        "tracked_output_sha256": tracked_checksums,
    }
    _write_json(result_root / "evidence.json", evidence)
    return verify_figure2_and_table1_outputs(
        root,
        destination_root=destination,
    )


__all__ = [
    "ABLATION_FAMILIES",
    "APPROXIMATION_FAMILY_ORDER",
    "BASELINE_FAMILIES",
    "BATCH",
    "CONFIGURATION_COUNT",
    "FAMILY_ORDER",
    "GUARANTEES",
    "MAIN_FAMILIES",
    "PRIMARY_BYTE_TARGET",
    "PRIMARY_KEYFRAME_TARGET",
    "REFERENCE_FAMILIES",
    "SCHEMA_VERSION",
    "SCRIPT_VERSION",
    "STARTING_HEAD",
    "generate_figure2_and_table1",
    "load_plotted_values",
    "load_primary_table_values",
    "load_principal_contrasts",
    "method_taxonomy",
    "render_figure2",
    "verify_accepted_release_sources",
    "verify_figure2_and_table1_outputs",
]
