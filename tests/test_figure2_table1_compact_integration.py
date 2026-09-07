"""Integration tests for deterministic compact release asset generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from PIL import Image

from kinematicweave.visualization.figure2_table1_compact import (
    ACCEPTED_FIGURE,
    ACCEPTED_RESULT,
    ACCEPTED_TABLE,
    FIGURE,
    FIGURE_HEIGHT,
    FIGURE_WIDTH,
    GENERATED_PATHS,
    GRAYSCALE,
    RESULT,
    TABLE,
    generate_compact_assets,
    verify_compact_assets,
)

type Json = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Json:
    return cast(Json, json.loads(path.read_text(encoding="utf-8")))


def test_isolated_generation_is_byte_deterministic_and_preserves_sources(
    tmp_path: Path,
) -> None:
    accepted_paths = (
        ROOT / ACCEPTED_RESULT / "plotted_values.json",
        ROOT / ACCEPTED_RESULT / "primary_table_values.json",
        ROOT / ACCEPTED_RESULT / "evidence.json",
        ROOT / ACCEPTED_FIGURE,
        ROOT / ACCEPTED_TABLE,
    )
    before = {path: _sha256(path) for path in accepted_paths}
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"

    generate_compact_assets(ROOT, destination_root=run_a)
    generate_compact_assets(ROOT, destination_root=run_b)
    verify_compact_assets(ROOT, destination_root=run_a)
    verify_compact_assets(ROOT, destination_root=run_b)

    assert {path: _sha256(path) for path in accepted_paths} == before
    assert all(
        (run_a / relative).read_bytes() == (run_b / relative).read_bytes()
        for relative in GENERATED_PATHS
    )
    assert (run_a / RESULT / "plotted_values.json").read_bytes() == (
        ROOT / ACCEPTED_RESULT / "plotted_values.json"
    ).read_bytes()
    assert (run_a / RESULT / "table_values.json").read_bytes() == (
        ROOT / ACCEPTED_RESULT / "primary_table_values.json"
    ).read_bytes()


def test_compact_exports_and_manifest_are_consistent(tmp_path: Path) -> None:
    destination = tmp_path / "compact"
    evidence = generate_compact_assets(ROOT, destination_root=destination)
    manifest = _json(destination / RESULT / "figure_manifest.json")

    with Image.open(destination / FIGURE.with_suffix(".png")) as image:
        assert image.size == (FIGURE_WIDTH, FIGURE_HEIGHT)
        assert image.mode == "RGB"
    with Image.open(destination / GRAYSCALE) as image:
        assert image.size == (FIGURE_WIDTH, FIGURE_HEIGHT)
        assert image.mode == "RGB"
        pixels = image.tobytes()
        assert all(
            pixels[index] == pixels[index + 1] == pixels[index + 2]
            for index in range(0, len(pixels), 3)
        )

    assert manifest["figure"]["method_family_count"] == 9
    assert manifest["figure"]["plotted_configuration_count"] == 17
    assert manifest["figure"]["exact_adjacent_reference"]["shown_in_reference_block"]
    assert manifest["table"]["approximation_family_count"] == 7
    assert manifest["table"]["column_count"] == 5
    assert set(evidence["generated_output_sha256"]) == {
        path.as_posix() for path in GENERATED_PATHS if path != RESULT / "evidence.json"
    }

    csv_text = (destination / TABLE.with_suffix(".csv")).read_text("utf-8")
    markdown = (destination / TABLE.with_suffix(".md")).read_text("utf-8")
    latex = (destination / TABLE.with_suffix(".tex")).read_text("utf-8")
    assert csv_text.count("\n") == 8
    assert markdown.count("\n") == 9
    assert latex.count(" \\\\\n") == 9
    assert "Position/velocity hybrid" in csv_text
    assert "pos <= 0.10 m; vel <= 1.00 m/s" in markdown
    assert r"pos $\leq$ 0.10 m; vel $\leq$ 1.00 m/s" in latex
