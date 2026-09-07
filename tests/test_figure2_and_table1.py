"""Focused tests for aggregate Figure 2 and primary Table 1 logic."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from kinematicweave.visualization import figure2_rate_distortion as figure2

ROOT = Path(__file__).resolve().parents[1]


def test_method_taxonomy_matches_release_groups() -> None:
    taxonomy = figure2.method_taxonomy()
    assert taxonomy == {
        "references": ["raw_samples", "exact_adjacent"],
        "standard_baselines": [
            "uniform_linear",
            "uniform_hermite",
            "fixed_interval_linear",
            "rdp_linear",
        ],
        "kinematicweave_main_methods": [
            "position_bounded_linear",
            "position_velocity_hybrid",
        ],
        "kinematicweave_ablation": ["unconstrained_hermite"],
        "family_order": list(figure2.FAMILY_ORDER),
    }


def test_guarantees_distinguish_geometric_and_time_indexed_bounds() -> None:
    assert figure2.GUARANTEES["uniform_linear"] == "none"
    rdp = figure2.GUARANTEES["rdp_linear"]
    assert rdp == "geometric path tolerance only (0.05 m)"
    assert "time-indexed" not in rdp
    assert figure2.GUARANTEES["position_bounded_linear"] == "position <= 0.10 m"
    hybrid = figure2.GUARANTEES["position_velocity_hybrid"]
    assert "position <= 0.10 m" in hybrid
    assert "represented velocity <= 1.00 m/s" in hybrid


def test_frozen_plotted_values_have_complete_configuration_coverage() -> None:
    plotted = figure2.load_plotted_values(ROOT)
    assert plotted["configuration_count"] == 18
    assert plotted["plotted_configuration_count"] == 17
    rows = plotted["configurations"]
    assert len(rows) == 18
    assert {row["family"] for row in rows} == set(figure2.FAMILY_ORDER)
    assert all(row["source_trace"]["cohort_role"] == "test" for row in rows)
    exact = [row for row in rows if row["family"] == "exact_adjacent"]
    assert len(exact) == 1
    assert exact[0]["plotted_in_panels"] is False
    assert 2.52 < exact[0]["byte_ratio"] < 2.54
    assert all(
        row["plotted_in_panels"]
        for row in rows
        if row["family"] in figure2.APPROXIMATION_FAMILY_ORDER
    )
    assert plotted["panel_x_limits"] == [0.25, 1.45]
    assert plotted["omitted_panel_references"][0]["family"] == "exact_adjacent"


def test_primary_table_uses_frozen_byte_selection() -> None:
    plotted = figure2.load_plotted_values(ROOT)
    table = figure2.load_primary_table_values(ROOT, plotted)
    assert table["target_byte_ratio"] == 0.48
    assert len(table["rows"]) == 7
    assert [row["family"] for row in table["rows"]] == list(
        figure2.APPROXIMATION_FAMILY_ORDER
    )
    expected = {
        "uniform_linear": ("uniform_linear-stride-10", "0.4802"),
        "uniform_hermite": ("uniform_hermite-stride-10", "0.4806"),
        "fixed_interval_linear": (
            "fixed_interval_linear-interval-1000000000",
            "0.4806",
        ),
        "rdp_linear": ("rdp_linear-error-0p05", "0.4419"),
        "position_bounded_linear": (
            "position_bounded_linear-error-0p1",
            "0.5074",
        ),
        "position_velocity_hybrid": (
            "position_velocity_bounded_hybrid-error-0p1-velocity-1p0",
            "0.4913",
        ),
        "unconstrained_hermite": (
            "unconstrained_hermite-error-0p1",
            "0.4699",
        ),
    }
    for row in table["rows"]:
        method_id, rounded = expected[row["family"]]
        assert row["method_id"] == method_id
        assert row["display"]["bytes_raw"] == rounded


def test_principal_contrasts_are_accepted_records_with_pilot_direction() -> None:
    support = figure2.load_principal_contrasts(ROOT)
    assert support["new_statistical_procedure_run"] is False
    contrasts = support["contrasts"]
    assert [row["contrast_id"] for row in contrasts] == ["A", "B", "C"]
    expected_metrics = {
        "A": {
            "position_mean_m",
            "position_p95_m",
            "semantic_overall_f1",
            "byte_ratio",
            "encoding_seconds",
        },
        "B": {
            "position_mean_m",
            "position_maximum_m",
            "velocity_mean_mps",
            "velocity_maximum_mps",
            "byte_ratio",
        },
        "C": {
            "velocity_mean_mps",
            "velocity_maximum_mps",
            "semantic_overall_f1",
            "byte_ratio",
            "keyframe_ratio",
        },
    }
    for contrast in contrasts:
        metrics = contrast["metrics"]
        assert {row["metric"] for row in metrics} == expected_metrics[
            contrast["contrast_id"]
        ]
        assert all(row["corrected_p_value"] is not None for row in metrics)
        assert all(
            isinstance(row["pilot_direction_agreement"], bool) for row in metrics
        )
        assert all(row["source_trace"]["cohort_role"] == "test" for row in metrics)


def test_generator_modules_have_no_import_time_io() -> None:
    module = importlib.import_module(
        "kinematicweave.visualization.figure2_rate_distortion"
    )
    script_path = ROOT / "scripts/generate_figure2_and_table1.py"
    specification = importlib.util.spec_from_file_location(
        "figure2_generator_script",
        script_path,
    )
    assert specification is not None
    assert specification.loader is not None
    script = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(script)
    assert callable(module.generate_figure2_and_table1)
    assert callable(script.main)
