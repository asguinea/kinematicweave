"""Focused tests for release Figure 1 selection and rendering."""

from __future__ import annotations

from dataclasses import replace
import importlib
import importlib.util
from itertools import pairwise
from pathlib import Path

import pytest

from kinematicweave.errors import ArtifactError
from kinematicweave.visualization import figure1_procedural_overview as figure1
from kinematicweave.visualization.deterministic import BLUE, Drawing


def _source_rows(count: int = 30) -> list[dict[str, object]]:
    return [
        {
            "x_m": float(index),
            "y_m": float(index % 3) * 0.2,
            "timestamp_ns": index * 100_000_000,
            "speed_mps": 2.0 + float(index % 4),
        }
        for index in range(count)
    ]


def _features(
    safe_id: str = "trajectory-0000000000000001",
) -> figure1.CandidateFeatures:
    return figure1.candidate_features(
        safe_id=safe_id,
        source_rows=_source_rows(),
        exact={"segment_count": 29},
        hybrid={
            "segment_count": 4,
            "hold_count": 0,
            "linear_count": 3,
            "hermite_count": 1,
            "position_statistics": {
                "mean": 0.03,
                "p95": 0.08,
                "maximum": 0.09,
            },
            "velocity_statistics": {
                "mean": 0.20,
                "p95": 0.70,
                "maximum": 0.90,
            },
        },
        events=[
            {
                "event_type": "left_turn",
                "source_event_count": 1,
                "replay_event_count": 1,
                "matched_event_count": 1,
            },
            {
                "event_type": "acceleration",
                "source_event_count": 1,
                "replay_event_count": 1,
                "matched_event_count": 1,
            },
        ],
    )


def _rankable(
    features: figure1.CandidateFeatures,
) -> figure1._RankedCandidate:
    return figure1._RankedCandidate(
        "private-scenario",
        "private-trajectory",
        features,
        tuple(_source_rows()),
    )


def test_candidate_features_cover_counts_bounds_and_semantics() -> None:
    features = _features()
    assert features.valid_source_sample_count == 30
    assert features.exact_segment_count == 29
    assert features.hybrid_segment_count == 4
    assert features.segment_reduction_count == 25
    assert features.hybrid_linear_count == 3
    assert features.hybrid_hermite_count == 1
    assert features.semantic_event_types == ("acceleration", "left_turn")
    assert features.semantic_preservation_f1 == 1.0
    assert features.hybrid_position_maximum_m == 0.09
    assert features.hybrid_velocity_maximum_mps == 0.90


def test_candidate_eligibility_rejects_material_contract_failures() -> None:
    features = _features()
    assert figure1.eligibility_failures(features, speed_range_mps=3.0) == ()
    failures = figure1.eligibility_failures(
        replace(
            features,
            valid_source_sample_count=24,
            hybrid_position_maximum_m=0.11,
            hybrid_velocity_maximum_mps=1.01,
        ),
        speed_range_mps=3.0,
    )
    assert failures == (
        "fewer_than_25_valid_samples",
        "position_bound_exceeded",
        "velocity_bound_exceeded",
    )


def test_ranking_order_and_safe_identifier_tie_break_are_stable() -> None:
    base = _features()
    higher_diversity = replace(
        base,
        safe_identifier="trajectory-ffffffffffffffff",
        semantic_event_type_diversity=3,
    )
    tied_b = replace(base, safe_identifier="trajectory-bbbbbbbbbbbbbbbb")
    tied_a = replace(base, safe_identifier="trajectory-aaaaaaaaaaaaaaaa")
    ranked = figure1.rank_candidates(
        [_rankable(tied_b), _rankable(higher_diversity), _rankable(tied_a)]
    )
    assert [item.features.safe_identifier for item in ranked] == [
        "trajectory-ffffffffffffffff",
        "trajectory-aaaaaaaaaaaaaaaa",
        "trajectory-bbbbbbbbbbbbbbbb",
    ]
    assert [item.rank for item in ranked] == [1, 2, 3]


def test_readability_gate_has_declared_density_failure() -> None:
    features = _features()
    passed = replace(
        features,
        annotation_density_score=0.20,
        spatial_extent_diagonal_m=10.0,
    )
    failed = replace(passed, annotation_density_score=0.36)
    assert figure1.readability_gate(passed).passed
    result = figure1.readability_gate(failed)
    assert not result.passed
    assert result.failures == ("event_labels_too_dense_for_path_length",)


def test_selection_contract_prohibits_raw_identifier_allowlists() -> None:
    contract = figure1.selection_contract()
    assert contract["manual_identifier_allowlist"] is False
    assert contract["ranking_fixed_before_rendering"] is True
    assert (
        contract["safe_identifier_policy"]["raw_identifiers_in_tracked_outputs"]
        is False
    )
    assert "source_scenario_id" not in str(contract)


def test_output_path_containment_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError):
        figure1._contained(tmp_path, tmp_path / ".." / "outside")


def test_pdf_renderer_is_deterministic_and_valid(tmp_path: Path) -> None:
    outputs: list[bytes] = []
    for index in range(2):
        drawing = Drawing(320, 180)
        drawing.text(20, 35, "FIGURE 1", size=16)
        drawing.line(20, 150, 290, 40, BLUE, 4)
        drawing.circle(150, 90, 8, BLUE)
        path = tmp_path / f"figure-{index}.pdf"
        drawing.save_pdf(path)
        outputs.append(path.read_bytes())
    assert outputs[0] == outputs[1]
    assert outputs[0].startswith(b"%PDF-1.4")
    assert outputs[0].endswith(b"%%EOF\n")


def test_grayscale_renderer_is_deterministic_and_contains_no_color(
    tmp_path: Path,
) -> None:
    outputs: list[bytes] = []
    for index in range(2):
        drawing = Drawing(160, 90)
        drawing.line(10, 70, 150, 20, BLUE, 4)
        drawing.text(12, 25, "FINAL", BLUE, 14)
        path = tmp_path / f"grayscale-{index}.png"
        drawing.save_png(path, grayscale=True)
        outputs.append(path.read_bytes())
        assert figure1._png_is_grayscale(path)
    assert outputs[0] == outputs[1]


def test_semantic_label_layout_is_stable_complete_and_non_overlapping() -> None:
    event_types = ("acceleration", "braking", "left_turn", "right_turn", "stop")
    plotted = {
        "axis_limits": {
            "x_min_m": 0.0,
            "x_max_m": 10.0,
            "y_min_m": 0.0,
            "y_max_m": 10.0,
        },
        "semantic_waypoints": [
            {
                "waypoint_index": index,
                "x_m": float(index),
                "y_m": 5.0,
            }
            for index in range(5)
        ],
        "semantic_events": [
            {
                "event_type": event_type,
                "anchor_waypoint_index": index,
                "start_time_ns": index * 1_000_000_000,
                "anchor_time_ns": index * 1_000_000_000 + 100_000_000,
                "end_time_ns": index * 1_000_000_000 + 500_000_000,
            }
            for index, event_type in enumerate(event_types)
        ],
        "source_positions": [{"timestamp_ns": 0}],
    }
    first = figure1.semantic_label_layout(
        plotted,
        (0.0, 0.0, 300.0, 300.0),
        (330.0, 60.0),
    )
    second = figure1.semantic_label_layout(
        plotted,
        (0.0, 0.0, 300.0, 300.0),
        (330.0, 60.0),
    )
    assert first == second
    assert [row["event_type"] for row in first] == [
        "acceleration",
        "braking",
        "left_turn",
        "right_turn",
        "stop",
    ]
    assert [row["event_number"] for row in first] == [1, 2, 3, 4, 5]
    assert all(row["leader_line"] is False for row in first)
    for left, right in pairwise(first):
        left_box = left["label_box"]
        right_box = right["label_box"]
        assert left_box[1] + left_box[3] <= right_box[1]


def test_generator_modules_have_no_import_time_io() -> None:
    module = importlib.import_module(
        "kinematicweave.visualization.figure1_procedural_overview"
    )
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts/generate_figure1_procedural_overview.py"
    )
    specification = importlib.util.spec_from_file_location(
        "figure1_generator_script",
        script_path,
    )
    assert specification is not None
    assert specification.loader is not None
    script = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(script)
    assert callable(module.generate_figure1_procedural_overview)
    assert callable(script.main)
