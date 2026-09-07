"""Committed-evidence integration tests for release Figure 1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import pytest

from kinematicweave.visualization.figure1_procedural_overview import (
    generate_figure1_procedural_overview,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/benchmark_figures/figure1_procedural_overview"
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_genuine_av2_and_accepted_algorithm_identities() -> None:
    evidence = _json("evidence.json")
    assert evidence["status"] == "PASS"
    assert evidence["batch"] == "F1.2"
    assert evidence["genuine_av2"] is True
    assert evidence["candidate_selection_rerun"] is False
    assert evidence["human_visual_review_override"] is True
    assert evidence["final_candidate_comparison_recorded"] is True
    assert evidence["scientific_values_recomputed_and_verified"] is True
    assert evidence["accepted_input_identity"]["dataset_id"] == "av2_motion"
    assert evidence["accepted_input_identity"]["genuine_provider_evidence"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", evidence["exact_configuration_identity"])
    assert re.fullmatch(r"[0-9a-f]{64}", evidence["hybrid_configuration_identity"])
    assert re.fullmatch(r"[0-9a-f]{64}", evidence["semantic_detector_identity"])


def test_top_three_ranking_and_selected_safe_identifier() -> None:
    evidence = _json("evidence.json")
    selected = _json("selected_candidates.json")
    expected = [
        "trajectory-c3b9d7c1f89e1c25",
        "trajectory-369819dca08e6fee",
        "trajectory-f83da3381e4f5eda",
    ]
    assert evidence["top_three_safe_identifiers"] == expected
    assert [row["safe_identifier"] for row in selected["top_three"]] == expected
    assert selected["recommended_candidate"] == expected[2]
    assert selected["recommended_rank"] == 3
    assert not selected["top_three"][0]["readability_gate"]["passed"]
    assert not selected["top_three"][1]["readability_gate"]["passed"]
    assert selected["top_three"][2]["readability_gate"]["passed"]


def test_numeric_annotations_are_complete_and_traceable() -> None:
    plotted = _json("plotted_values.json")
    annotations = plotted["annotation_values"]
    assert plotted["safe_candidate_identifier"] == "trajectory-c3b9d7c1f89e1c25"
    assert annotations["source_samples"] == len(plotted["source_positions"]) == 110
    assert annotations["exact_segments"] == len(plotted["exact_segments"]) == 109
    assert annotations["compact_segments"] == len(plotted["hybrid_segments"]) == 5
    assert annotations["hybrid_primitive_composition"] == {
        "hold": 0,
        "linear": 1,
        "cubic_hermite": 4,
    }
    assert annotations["maximum_position_error_m"] <= 0.10
    assert annotations["p95_position_error_m"] <= 0.10
    assert annotations["maximum_velocity_error_mps"] <= 1.00
    assert annotations["source_semantic_event_count"] == 6
    assert annotations["replay_preserved_semantic_event_count"] == 5
    assert annotations["semantic_preservation_f1"] == pytest.approx(10 / 11)
    assert set(annotations["semantic_event_types"]) == {
        "stop",
        "left_turn",
        "right_turn",
        "acceleration",
        "braking",
    }
    assert plotted["axis_limits"]["aspect_ratio"] == "equal"
    assert plotted["final_production"]["batch"] == "F1.2"
    assert plotted["final_production"]["scientific_values_frozen"] is True
    assert len(plotted["final_production"]["semantic_label_layout"]) == 6
    assert all(
        row["leader_line"] is False
        for row in plotted["final_production"]["semantic_label_layout"]
    )
    assert all(
        set(row) >= {"event_type", "start_time_ns", "anchor_time_ns", "end_time_ns"}
        for row in plotted["semantic_events"]
    )


def test_all_required_outputs_and_evidence_checksums() -> None:
    evidence = _json("evidence.json")
    manifest = _json("figure_manifest.json")
    required = {
        "figures/benchmark/figure1_procedural_overview_draft.pdf",
        "figures/benchmark/figure1_procedural_overview_draft.png",
        "figures/benchmark/figure1_procedural_overview.pdf",
        "figures/benchmark/figure1_procedural_overview.png",
        "figures/benchmark/figure1_procedural_overview_grayscale.png",
        "figures/benchmark/figure1_candidates/candidate_01_preview.png",
        "figures/benchmark/figure1_candidates/candidate_02_preview.png",
        "figures/benchmark/figure1_candidates/candidate_03_preview.png",
        "figures/benchmark/figure1_final_candidates/candidate_01_composition.png",
        "figures/benchmark/figure1_final_candidates/candidate_03_composition.png",
        "figures/benchmark/figure1_final_candidates/candidate_01_composition_grayscale.png",
        "figures/benchmark/figure1_final_candidates/candidate_03_composition_grayscale.png",
        "figures/benchmark/figure1_final_candidates/one_column_comparison.png",
        "figures/benchmark/figure1_final_candidates/two_column_comparison.png",
        "reports/figure1_procedural_overview_caption.md",
        "reports/figure1_procedural_overview_notes.md",
        "results/benchmark_figures/figure1_procedural_overview/selection_contract.json",
        "results/benchmark_figures/figure1_procedural_overview/"
        "selected_candidates.json",
        "results/benchmark_figures/figure1_procedural_overview/plotted_values.json",
        "results/benchmark_figures/figure1_procedural_overview/"
        "candidate_03_plotted_values.json",
        "results/benchmark_figures/figure1_procedural_overview/"
        "final_candidate_comparison.json",
        "results/benchmark_figures/figure1_procedural_overview/figure_manifest.json",
        "results/benchmark_figures/figure1_procedural_overview/summary.md",
    }
    assert set(evidence["tracked_output_sha256"]) == required
    for relative, expected in evidence["tracked_output_sha256"].items():
        path = ROOT / relative
        assert path.is_file()
        assert _sha256(path) == expected
    assert len(manifest["outputs"]) == 14
    assert manifest["rendering_configuration"]["spatial_axis_limits_identical"]
    assert manifest["rendering_configuration"]["spatial_aspect_ratio"] == "equal"
    assert manifest["selected_safe_identifier"] == "trajectory-c3b9d7c1f89e1c25"
    assert manifest["human_visual_review_override"] is True
    assert manifest["rendering_configuration"]["font_family"]["raster"] == "DejaVu Sans"
    assert manifest["visual_inspection"]["status"] == "PASS"
    assert manifest["rendering_configuration"]["primitive_styles"] == {
        "linear": "solid",
        "cubic_hermite": "dashed",
        "breakpoint": "open square",
        "source_sample": "small neutral circle",
    }
    for descriptor in manifest["outputs"]:
        path = ROOT / descriptor["path"]
        assert path.stat().st_size == descriptor["size_bytes"]
        assert _sha256(path) == descriptor["sha256"]


def test_final_outputs_caption_and_semantics_are_complete() -> None:
    manifest = _json("figure_manifest.json")
    descriptors = {row["path"]: row for row in manifest["outputs"]}
    for relative in (
        "figures/benchmark/figure1_procedural_overview.png",
        "figures/benchmark/figure1_procedural_overview_grayscale.png",
    ):
        assert descriptors[relative]["width"] == 1800
        assert descriptors[relative]["height"] == 1400
        assert (ROOT / relative).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    pdf = ROOT / "figures/benchmark/figure1_procedural_overview.pdf"
    assert pdf.read_bytes().startswith(b"%PDF-1.4")
    caption_path = ROOT / "reports/figure1_procedural_overview_caption.md"
    caption = caption_path.read_text(encoding="utf-8")
    assert _sha256(caption_path) == manifest["caption_sha256"]
    for value in ("109", "5", "95.4%", "0.078 m", "0.074 m", "0.447 m/s"):
        assert value in caption
    for event in ("stop", "acceleration", "braking", "left turn", "right turn"):
        assert event in caption
    plotted = _json("plotted_values.json")
    labels = plotted["final_production"]["semantic_label_layout"]
    assert {row["event_type"] for row in labels} == {
        "stop",
        "acceleration",
        "braking",
        "left_turn",
        "right_turn",
    }


def test_repeated_final_generation_is_byte_identical(tmp_path: Path) -> None:
    if not (
        ROOT / "cache/phase4_qualitative_motion/replay_frames/frame_0000.png"
    ).is_file():
        pytest.skip("full regeneration requires ignored qualitative replay frames")
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_figure1_procedural_overview(ROOT, destination_root=first)
    generate_figure1_procedural_overview(ROOT, destination_root=second)
    relative_paths = (
        "figures/benchmark/figure1_procedural_overview.pdf",
        "figures/benchmark/figure1_procedural_overview.png",
        "figures/benchmark/figure1_procedural_overview_grayscale.png",
        "figures/benchmark/figure1_final_candidates/candidate_01_composition.png",
        "figures/benchmark/figure1_final_candidates/candidate_03_composition.png",
        "figures/benchmark/figure1_final_candidates/one_column_comparison.png",
        "figures/benchmark/figure1_final_candidates/two_column_comparison.png",
        "reports/figure1_procedural_overview_caption.md",
        "reports/figure1_procedural_overview_notes.md",
        "results/benchmark_figures/figure1_procedural_overview/plotted_values.json",
        "results/benchmark_figures/figure1_procedural_overview/"
        "candidate_03_plotted_values.json",
        "results/benchmark_figures/figure1_procedural_overview/"
        "final_candidate_comparison.json",
        "results/benchmark_figures/figure1_procedural_overview/figure_manifest.json",
        "results/benchmark_figures/figure1_procedural_overview/evidence.json",
        "results/benchmark_figures/figure1_procedural_overview/summary.md",
    )
    assert all(
        (first / relative).read_bytes() == (second / relative).read_bytes()
        for relative in relative_paths
    )


def test_tracked_outputs_are_release_safe() -> None:
    evidence = _json("evidence.json")
    tracked_text = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in evidence["tracked_output_sha256"]
        if Path(relative).suffix in {".json", ".md"}
    )
    assert UUID_PATTERN.search(tracked_text) is None
    assert "/home/" not in tracked_text
    assert "C:\\Users\\" not in tracked_text
    assert "source_scenario_id" not in tracked_text
    assert "trajectory:av2:" not in tracked_text
    assert evidence["raw_provider_identifiers_published"] is False


def test_provider_and_generated_source_artifacts_are_not_tracked() -> None:
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
        if (
            path.startswith("cache/")
            or path.startswith("results/generated/")
            or path.endswith(".parquet")
        )
    ]
    assert forbidden == []


def test_private_candidate_mapping_is_ignored() -> None:
    path = (
        ROOT / "cache/benchmark_figures/figure1_procedural_overview/"
        "private_candidate_mapping.json"
    )
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", str(path)],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0
