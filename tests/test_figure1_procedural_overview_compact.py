"""Unit tests for the compact accepted Figure 1 layout."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, cast

from kinematicweave.visualization.figure1_procedural_overview_compact import (
    COMPACT_HEIGHT,
    COMPACT_WIDTH,
    ORIGINAL_HEIGHT,
    ORIGINAL_WIDTH,
    _display_geometry,
    _layout_recommendation,
    _rotated_xy,
    render_compact_figure,
)

type Json = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
PLOTTED = (
    ROOT / "results/benchmark_figures/figure1_procedural_overview/plotted_values.json"
)


def _plotted() -> Json:
    return cast(Json, json.loads(PLOTTED.read_text(encoding="utf-8")))


def test_rotation_is_rigid_and_makes_dominant_direction_horizontal() -> None:
    plotted = _plotted()
    rows = cast(list[Json], plotted["source_positions"])
    original_start = (float(rows[0]["x_m"]), float(rows[0]["y_m"]))
    original_end = (float(rows[-1]["x_m"]), float(rows[-1]["y_m"]))
    rotated_start = _rotated_xy(rows[0])
    rotated_end = _rotated_xy(rows[-1])

    assert math.dist(original_start, original_end) == math.dist(
        rotated_start, rotated_end
    )
    assert abs(rotated_end[0] - rotated_start[0]) > abs(
        rotated_end[1] - rotated_start[1]
    )
    assert rotated_end[0] > rotated_start[0]


def test_compact_geometry_and_recommendation_reduce_height() -> None:
    plotted = _plotted()
    limits, dimensions = _display_geometry(plotted)
    recommendation = _layout_recommendation()

    assert limits["aspect_ratio"] == "equal"
    assert limits["rotation_degrees"] == 90
    assert dimensions[0] > dimensions[1]
    assert recommendation["original_dimensions"] == [ORIGINAL_WIDTH, ORIGINAL_HEIGHT]
    assert recommendation["compact_dimensions"] == [COMPACT_WIDTH, COMPACT_HEIGHT]
    assert recommendation["height_reduction_pixels"] == 440
    assert recommendation["height_reduction_percentage"] > 30


def test_render_preserves_four_panels_and_required_annotations() -> None:
    plotted = _plotted()
    drawing, timeline, limits = render_compact_figure(plotted)
    text = [
        str(command.values[2]) for command in drawing.commands if command.kind == "text"
    ]

    assert (drawing.width, drawing.height) == (COMPACT_WIDTH, COMPACT_HEIGHT)
    assert limits["aspect_ratio"] == "equal"
    assert len(timeline) == 6
    assert [row["event_number"] for row in timeline] == [1, 2, 3, 4, 5, 6]
    for value in (
        "A. CANONICAL MOTION SAMPLES",
        "B. EXACT PROCEDURAL REPLAY",
        "C. COMPACT BOUNDED REPLAY",
        "D. SEMANTIC MOTION LAYER",
        "110 SAMPLES",
        "109 EXACT  >  5 COMPACT  /  95.4% FEWER",
        "EVENTS PRESERVED  5/6  /  F1 0.909",
        "MAX / P95 POSITION  0.078 / 0.074 M",
        "MAX REPRESENTED-VELOCITY ERROR  0.447 M/S",
        "PRIMITIVES  1 LINEAR  /  4 HERMITE",
    ):
        assert value in text
