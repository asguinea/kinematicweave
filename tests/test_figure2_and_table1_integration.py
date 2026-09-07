"""Committed-evidence integration tests for release Figure 2 and Table 1."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from PIL import Image
import pytest

from kinematicweave.visualization import figure2_rate_distortion as figure2

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/benchmark_figures/figure2_rate_distortion"
PRIVATE_PATTERN = re.compile(
    r"(?:\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12}\b|trajectory:av2:|/home/|C:\\Users\\)",
    re.IGNORECASE,
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_genuine_av2_frozen_test_provenance_and_identities() -> None:
    evidence = _json("evidence.json")
    source = evidence["source_verification"]
    assert evidence["status"] == "PASS"
    assert evidence["batch"] == "F2.2"
    assert source["test_scenario_count"] == 300
    assert source["test_trajectory_count"] == 13_689
    assert source["configuration_count"] == 18
    assert source["primary_byte_target"] == 0.48
    assert source["primary_keyframe_target"] == 0.12
    assert source["cohort_identity"] == figure2.COHORT_IDENTITY
    assert source["test_scenario_identity"] == figure2.TEST_SCENARIO_IDENTITY
    assert (
        source["test_trajectory_membership_identity"]
        == figure2.TEST_TRAJECTORY_IDENTITY
    )
    assert source["matrix_identity"] == figure2.MATRIX_IDENTITY
    assert source["metric_identity"] == figure2.METRIC_IDENTITY
    assert evidence["codec_execution_performed"] is False
    assert evidence["new_experiment_performed"] is False
    assert evidence["new_statistical_procedure_performed"] is False


def test_figure2_preserves_every_configuration_and_omits_only_exact_reference() -> None:
    plotted = _json("plotted_values.json")
    assert plotted["configuration_count"] == 18
    assert plotted["plotted_configuration_count"] == 17
    assert plotted["primary_byte_target"] == 0.48
    assert {row["family"] for row in plotted["configurations"]} == set(
        figure2.FAMILY_ORDER
    )
    assert plotted["plotted_family_order"] == list(figure2.PLOTTED_FAMILY_ORDER)
    assert plotted["panel_x_limits"] == [0.25, 1.45]
    exact = [
        row for row in plotted["configurations"] if row["family"] == "exact_adjacent"
    ]
    assert len(exact) == 1
    assert exact[0]["plotted_in_panels"] is False
    assert 2.52 < exact[0]["byte_ratio"] < 2.54
    assert all(
        row["plotted_in_panels"]
        for row in plotted["configurations"]
        if row["family"] in figure2.APPROXIMATION_FAMILY_ORDER
    )
    assert all(
        row["source_trace"]["source_evidence_file"]
        == "results/phase4/frozen_campaign/test_results.json"
        for row in plotted["configurations"]
    )
    assert all(
        row["source_trace"]["source_record_identity"] == row["parameter_identity"]
        for row in plotted["configurations"]
    )
    assert plotted["panels"][0]["y_scale"] == "log10"
    assert "axis floor" in plotted["panels"][0]["zero_reference_treatment"]


def test_primary_table_values_match_expected_frozen_results() -> None:
    table = _json("primary_table_values.json")
    rows = {row["family"]: row for row in table["rows"]}
    expected = {
        "uniform_linear": ("0.4802", "0.1230", "0.1044", "0.4973", "0.8816"),
        "uniform_hermite": ("0.4806", "0.1230", "0.0984", "0.4788", "0.5770"),
        "fixed_interval_linear": (
            "0.4806",
            "0.1230",
            "0.1044",
            "0.4973",
            "0.8816",
        ),
        "rdp_linear": ("0.4419", "0.1041", "0.2246", "0.9455", "0.8783"),
        "position_bounded_linear": (
            "0.5074",
            "0.1310",
            "0.0427",
            "0.0918",
            "0.9121",
        ),
        "unconstrained_hermite": (
            "0.4699",
            "0.1148",
            "0.0319",
            "0.0807",
            "0.7274",
        ),
        "position_velocity_hybrid": (
            "0.4913",
            "0.1236",
            "0.0319",
            "0.0806",
            "0.8860",
        ),
    }
    assert set(rows) == set(expected)
    for family, values in expected.items():
        display = rows[family]["display"]
        assert (
            display["bytes_raw"],
            display["keyframes_source"],
            display["mean_position_m"],
            display["p95_position_m"],
            display["semantic_f1"],
        ) == values
        assert rows[family]["source_trace"]["cohort_role"] == "test"
    assert "geometric path tolerance only" in rows["rdp_linear"]["guarantee"]
    assert "represented velocity" in rows["position_velocity_hybrid"]["guarantee"]


def test_principal_contrasts_preserve_accepted_statistical_fields() -> None:
    support = _json("principal_contrasts.json")
    assert support["source_batch"] == "4.8"
    assert support["pilot_and_test_pooled"] is False
    for contrast in support["contrasts"]:
        for metric in contrast["metrics"]:
            assert len(metric["confidence_interval_95"]) == 2
            assert metric["corrected_p_value"] is not None
            assert metric["rank_biserial_correlation"] is not None
            assert metric["standardized_paired_effect_dz"] is not None
            assert metric["practical_magnitude"] in {
                "negligible",
                "small",
                "moderate",
                "large",
            }
            assert isinstance(metric["pilot_direction_agreement"], bool)
            assert metric["source_trace"]["source_file"].startswith(
                "results/phase4/ablation_analysis/"
            )


def test_figure_outputs_manifest_and_grayscale_are_valid() -> None:
    evidence = _json("evidence.json")
    manifest = _json("figure_manifest.json")
    for relative, expected in evidence["tracked_output_sha256"].items():
        path = ROOT / relative
        assert path.is_file()
        assert _sha256(path) == expected
    descriptors = {row["path"]: row for row in manifest["outputs"]}
    color = ROOT / "figures/benchmark/figure2_rate_distortion.png"
    grayscale = ROOT / "figures/benchmark/figure2_rate_distortion_grayscale.png"
    pdf = ROOT / "figures/benchmark/figure2_rate_distortion.pdf"
    assert color.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert grayscale.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf.read_bytes().startswith(b"%PDF-1.4")
    assert descriptors["figures/benchmark/figure2_rate_distortion.png"]["width"] == 2400
    assert descriptors["figures/benchmark/figure2_rate_distortion.png"]["height"] == 850
    assert figure2._png_is_grayscale(grayscale)
    assert manifest["visual_inspection"]["status"] == "PASS"
    assert manifest["visual_inspection"]["hidden_high_error_points"] is False
    assert manifest["visual_inspection"]["table_uses_resize_scaling"] is False
    assert (
        manifest["visual_inspection"]["primary_region_horizontal_separation_improved"]
        is True
    )


def test_table_formats_are_consistent_with_machine_values() -> None:
    table = _json("primary_table_values.json")
    csv_text = (ROOT / "tables/benchmark/table1_primary_matched_byte.csv").read_text(
        encoding="utf-8"
    )
    markdown = (ROOT / "tables/benchmark/table1_primary_matched_byte.md").read_text(
        encoding="utf-8"
    )
    latex = (ROOT / "tables/benchmark/table1_primary_matched_byte.tex").read_text(
        encoding="utf-8"
    )
    csv_rows = list(csv.reader(io.StringIO(csv_text)))
    assert len(csv_rows) == 8
    assert csv_rows[0] == [
        "Method",
        "Configuration",
        "Bytes/raw",
        "P95 pos. (m)",
        "Mean vel. (m/s)",
        "Max vel. (m/s)",
        "Semantic F1",
        "Guarantee",
    ]
    for row in table["rows"]:
        display = row["display"]
        assert display["bytes_raw"] in markdown
        assert display["bytes_raw"] in latex
        assert display["method"] in csv_text
    assert "\\begin{table*}" in latex
    assert "\\small" in latex
    assert "\\resizebox" not in latex
    assert "Delta 0.48" not in latex
    assert "KF/source" not in latex
    assert "Mean pos." not in latex
    assert "**Position-bounded linear**" in markdown
    assert "\\textbf{Position-bounded linear}" in latex


def test_representative_exports_meet_release_contract() -> None:
    manifest = _json("figure_manifest.json")
    source = ROOT / "figures/benchmark/figure1_procedural_overview.png"
    jpeg = ROOT / "figures/showcase/representative_figure1.jpg"
    web = ROOT / "figures/showcase/representative_figure1_web.png"
    with Image.open(jpeg) as image:
        assert image.mode == "RGB"
        assert image.width >= 1500
        assert image.height >= 1000
    with Image.open(web) as image:
        assert image.size == (1800, 1400)
    assert web.stat().st_size <= 200_000
    assert web.read_bytes() == source.read_bytes()
    assert manifest["representative_exports"]["source_sha256"] == _sha256(source)


def test_repeated_generation_is_byte_identical(tmp_path: Path) -> None:
    if not (
        ROOT
        / "cache/phase4_frozen_campaign/test/final-v1/trajectory_motion_metrics.parquet"
    ).is_file():
        pytest.skip("full regeneration requires ignored frozen-campaign tables")
    first = tmp_path / "first"
    second = tmp_path / "second"
    figure2.generate_figure2_and_table1(ROOT, destination_root=first)
    figure2.generate_figure2_and_table1(ROOT, destination_root=second)
    relative_paths = (
        *figure2._tracked_paths(),
        figure2.RESULT_RELATIVE / "evidence.json",
    )
    assert all(
        (first / relative).read_bytes() == (second / relative).read_bytes()
        for relative in relative_paths
    )


def test_release_outputs_contain_no_raw_ids_or_private_paths() -> None:
    evidence = _json("evidence.json")
    text = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in evidence["tracked_output_sha256"]
        if Path(relative).suffix in {".json", ".md", ".tex", ".csv"}
    )
    assert PRIVATE_PATTERN.search(text) is None
    assert evidence["raw_provider_identifiers_published"] is False


def test_provider_and_raw_result_artifacts_remain_untracked() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    forbidden = [
        path
        for path in tracked
        if path.startswith("cache/") or path.endswith(".parquet")
    ]
    assert forbidden == []
