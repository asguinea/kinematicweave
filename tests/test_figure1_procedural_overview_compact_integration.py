"""Integration tests for compact accepted Figure 1 generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from PIL import Image

from kinematicweave.visualization.figure1_procedural_overview import (
    PRIMARY_SAFE_IDENTIFIER,
)
from kinematicweave.visualization.figure1_procedural_overview_compact import (
    ACCEPTED_FIGURE,
    ACCEPTED_RESULT,
    COMPACT_HEIGHT,
    COMPACT_WIDTH,
    FIGURE,
    GENERATED_PATHS,
    GRAYSCALE,
    ORIGINAL_HEIGHT,
    RESULT,
    generate_compact_figure1,
    verify_compact_figure1,
)

type Json = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Json:
    return cast(Json, json.loads(path.read_text(encoding="utf-8")))


def test_generation_is_byte_deterministic_and_accepted_sources_are_unchanged(
    tmp_path: Path,
) -> None:
    accepted_paths = (
        ROOT / ACCEPTED_RESULT / "plotted_values.json",
        ROOT / ACCEPTED_RESULT / "evidence.json",
        ROOT / ACCEPTED_RESULT / "figure_manifest.json",
        ROOT / ACCEPTED_FIGURE.with_suffix(".pdf"),
        ROOT / ACCEPTED_FIGURE.with_suffix(".png"),
        ROOT / "figures/benchmark/figure1_procedural_overview_grayscale.png",
    )
    before = {path: _sha256(path) for path in accepted_paths}
    first = tmp_path / "first"
    second = tmp_path / "second"

    generate_compact_figure1(ROOT, destination_root=first)
    generate_compact_figure1(ROOT, destination_root=second)
    verify_compact_figure1(ROOT, destination_root=first)
    verify_compact_figure1(ROOT, destination_root=second)

    assert {path: _sha256(path) for path in accepted_paths} == before
    assert all(
        (first / relative).read_bytes() == (second / relative).read_bytes()
        for relative in GENERATED_PATHS
    )
    assert (first / RESULT / "plotted_values.json").read_bytes() == (
        ROOT / ACCEPTED_RESULT / "plotted_values.json"
    ).read_bytes()


def test_manifest_dimensions_values_and_grayscale_are_valid(tmp_path: Path) -> None:
    destination = tmp_path / "compact"
    evidence = generate_compact_figure1(ROOT, destination_root=destination)
    accepted = _json(ROOT / ACCEPTED_RESULT / "plotted_values.json")
    manifest = _json(destination / RESULT / "figure_manifest.json")

    with Image.open(destination / FIGURE.with_suffix(".png")) as image:
        assert image.size == (COMPACT_WIDTH, COMPACT_HEIGHT)
        assert image.mode == "RGB"
    with Image.open(destination / GRAYSCALE) as image:
        assert image.size == (COMPACT_WIDTH, COMPACT_HEIGHT)
        assert image.mode == "RGB"
        pixels = image.tobytes()
        assert all(
            pixels[index] == pixels[index + 1] == pixels[index + 2]
            for index in range(0, len(pixels), 3)
        )

    assert COMPACT_HEIGHT < ORIGINAL_HEIGHT
    assert manifest["selected_safe_identifier"] == PRIMARY_SAFE_IDENTIFIER
    assert manifest["annotation_values"] == accepted["annotation_values"]
    assert manifest["layout"]["rotation_transform"] == "x_prime=-y; y_prime=x"
    assert manifest["layout"]["rigid_transform"] is True
    assert manifest["layout"]["geometry_distorted"] is False
    assert manifest["layout"]["spatial_axis_limits_identical"] is True
    assert manifest["layout"]["spatial_aspect_ratio"] == "equal"
    assert len(manifest["semantic_timeline"]) == 6
    assert set(evidence["generated_output_sha256"]) == {
        relative.as_posix()
        for relative in GENERATED_PATHS
        if relative != RESULT / "evidence.json"
    }
