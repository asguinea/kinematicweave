"""Known-answer tests for qualitative example selection and rendering."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from kinematicweave.errors import ValidationError
from kinematicweave.experiments.qualitative_selection import (
    SelectionRule,
    safe_identifier,
    select_by_rule,
    selection_contract,
    stable_rank,
)
from kinematicweave.visualization.deterministic import BLUE, Drawing


def test_stable_rank_uses_safe_identifier_for_ties() -> None:
    candidates = [
        {"safe_record_id": "scenario-b", "score": 2.0},
        {"safe_record_id": "scenario-a", "score": 2.0},
        {"safe_record_id": "scenario-c", "score": 1.0},
    ]
    ranked = stable_rank(candidates, metric="score", direction="descending")
    assert [row["safe_record_id"] for row in ranked] == [
        "scenario-a",
        "scenario-b",
        "scenario-c",
    ]
    assert [row["rank"] for row in ranked] == [1, 2, 3]


def test_rank_rule_selects_largest_median_and_unfavorable_smallest() -> None:
    candidates = [
        {"safe_record_id": f"scenario-{index}", "benefit": float(index)}
        for index in range(5)
    ]
    assert (
        select_by_rule(
            candidates,
            SelectionRule("largest", "benefit", "descending"),
        )["benefit"]
        == 4.0
    )
    assert (
        select_by_rule(
            candidates,
            SelectionRule("median", "benefit", "descending", "median"),
        )["benefit"]
        == 2.0
    )
    assert (
        select_by_rule(
            candidates,
            SelectionRule("unfavorable", "benefit", "descending", "smallest"),
        )["benefit"]
        == 0.0
    )


def test_invalid_rank_inputs_fail_closed() -> None:
    with pytest.raises(ValidationError):
        stable_rank([], metric="score", direction="descending")
    with pytest.raises(ValidationError):
        stable_rank(
            [{"safe_record_id": "scenario-a", "score": float("nan")}],
            metric="score",
            direction="descending",
        )
    with pytest.raises(ValidationError):
        SelectionRule("test", "score", "sideways")


def test_contract_has_required_coverage_and_no_allowlist() -> None:
    contract = selection_contract()
    categories = {row["category"] for row in contract["rules"]}
    assert {
        "adaptive_largest_position_improvement",
        "adaptive_median_position_improvement",
        "adaptive_smallest_or_adverse_improvement",
        "rdp_largest_temporal_failure",
        "hermite_largest_velocity_inflation",
        "hermite_largest_semantic_loss",
        "hybrid_largest_velocity_recovery",
        "hybrid_largest_semantic_tradeoff",
        "hybrid_little_advantage",
    } <= categories
    assert set(contract["semantic_rules"]) == {
        "stop",
        "turn",
        "acceleration",
        "braking",
    }
    assert set(contract["agent_rules"]) == {"vehicle", "pedestrian", "cyclist"}
    assert contract["ranking"]["manual_identifier_allowlist"] is False
    assert "source_scenario_id" not in str(contract)


def test_safe_identifiers_are_stable_and_nonrevealing() -> None:
    source = "provider-sensitive-source"
    assert safe_identifier("scenario", source) == safe_identifier("scenario", source)
    assert source not in safe_identifier("scenario", source)
    assert safe_identifier("scenario", source).startswith("scenario-")


def test_renderer_is_deterministic_in_both_formats(tmp_path: Path) -> None:
    hashes: list[tuple[bytes, bytes]] = []
    for index in range(2):
        drawing = Drawing(320, 180)
        drawing.text(20, 35, "DETERMINISTIC FIGURE", size=16)
        drawing.line(20, 150, 290, 40, BLUE, 4)
        drawing.circle(150, 90, 8, BLUE)
        svg = tmp_path / f"figure-{index}.svg"
        png = tmp_path / f"figure-{index}.png"
        drawing.save_svg(svg, title="Deterministic figure")
        drawing.save_png(png)
        hashes.append((svg.read_bytes(), png.read_bytes()))
    assert hashes[0] == hashes[1]
    assert hashes[0][0].startswith(b"<svg")
    assert hashes[0][1].startswith(b"\x89PNG")


def test_campaign_module_import_is_safe() -> None:
    module = importlib.import_module(
        "kinematicweave.experiments.qualitative_motion_campaign"
    )
    assert callable(module.run_qualitative_motion_campaign)
