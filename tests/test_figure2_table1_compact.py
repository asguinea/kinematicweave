"""Focused unit tests for compact Figure 2 and Table 1 presentation logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from kinematicweave.visualization.figure2_rate_distortion import (
    FAMILY_LABELS,
    PLOTTED_FAMILY_ORDER,
)
from kinematicweave.visualization.figure2_table1_compact import (
    FIGURE_HEIGHT,
    FIGURE_WIDTH,
    _layout_recommendation,
    compact_table_rows,
    render_compact_figure,
)

type Json = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = ROOT / "results/benchmark_figures/figure2_rate_distortion"


def _json(path: Path) -> Json:
    return cast(Json, json.loads(path.read_text(encoding="utf-8")))


def test_compact_rows_preserve_accepted_display_values() -> None:
    table = _json(ACCEPTED / "primary_table_values.json")
    rows = compact_table_rows(table)
    accepted_rows = cast(list[Json], table["rows"])

    assert len(rows) == 7
    assert [row["family"] for row in rows] == [row["family"] for row in accepted_rows]
    for compact, accepted in zip(rows, accepted_rows, strict=True):
        display = cast(Json, accepted["display"])
        assert compact["method"] == display["method"]
        assert compact["bytes_raw"] == display["bytes_raw"]
        assert compact["p95_position_m"] == display["p95_position_m"]
        assert compact["semantic_f1"] == display["semantic_f1"]


def test_compact_render_contract_keeps_all_panels_and_families() -> None:
    plotted = _json(ACCEPTED / "plotted_values.json")
    drawing = render_compact_figure(plotted)
    text = [
        str(command.values[2]) for command in drawing.commands if command.kind == "text"
    ]

    assert (drawing.width, drawing.height) == (FIGURE_WIDTH, FIGURE_HEIGHT)
    assert all(
        title in text
        for title in (
            "A. POSITIONAL FIDELITY",
            "B. MOTION FIDELITY",
            "C. SEMANTIC PRESERVATION",
        )
    )
    assert all(FAMILY_LABELS[family].upper() in text for family in PLOTTED_FAMILY_ORDER)
    assert "EXACT-ADJACENT REFERENCE" in text
    assert "FAMILY-ONLY LINES  /  NO COMPOSITE SCORE  /  NO UNIVERSAL WINNER" in text


def test_layout_recommendation_evaluates_all_requested_options() -> None:
    recommendation = _layout_recommendation()
    options = cast(list[Json], recommendation["options"])

    assert [row["strategy"] for row in options] == [
        "figure2_only",
        "table1_only",
        "both_figure2_and_table1",
    ]
    assert recommendation["recommended_strategy"] == "both_figure2_and_table1"
    assert [row["recommended"] for row in options] == [False, False, True]
