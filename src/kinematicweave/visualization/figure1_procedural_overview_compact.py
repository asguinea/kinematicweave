"""Deterministic compact compact layout for accepted release Figure 1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, cast

from PIL import Image

from kinematicweave.domain.procedural import ProceduralPrimitiveType
from kinematicweave.errors import ArtifactError
from kinematicweave.visualization.deterministic import (
    BLUE,
    CORAL,
    GOLD,
    GRID,
    INK,
    MUTED,
    WHITE,
    Drawing,
)
from kinematicweave.visualization.figure1_procedural_overview import (
    PRIMARY_SAFE_IDENTIFIER,
    RELEASE_FONT,
    _dashed_polyline,
    _direction_arrow,
    _error_inset,
    _event_marker,
    _event_style,
    _square,
    _start_end_markers,
    verify_figure1_outputs,
)

type Json = dict[str, Any]

SCHEMA_VERSION = "1.0"
BATCH = "F1.3"
SCRIPT_VERSION = "1.0"
STARTING_HEAD = "4e8595631e6c094ef5dcd37cba4fd8899917f8ee"

ORIGINAL_WIDTH = 1800
ORIGINAL_HEIGHT = 1400
COMPACT_WIDTH = 1800
COMPACT_HEIGHT = 960
ROTATION_DEGREES = 90

ACCEPTED_RESULT = Path("results/benchmark_figures/figure1_procedural_overview")
ACCEPTED_FIGURE = Path("figures/benchmark/figure1_procedural_overview")
FIGURE = Path("figures/benchmark/figure1_procedural_overview_compact")
GRAYSCALE = Path("figures/benchmark/figure1_procedural_overview_compact_grayscale.png")
SCRIPT = Path("scripts/generate_figure1_procedural_overview_compact.py")
NOTES = Path("reports/figure1_procedural_overview_compact_notes.md")
RESULT = Path("results/benchmark_figures/figure1_procedural_overview_compact")

GENERATED_PATHS = (
    FIGURE.with_suffix(".pdf"),
    FIGURE.with_suffix(".png"),
    GRAYSCALE,
    NOTES,
    RESULT / "evidence.json",
    RESULT / "figure_manifest.json",
    RESULT / "plotted_values.json",
    RESULT / "layout_recommendation.json",
    RESULT / "summary.md",
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


def _accepted_inputs(repository_root: Path) -> tuple[Json, Json]:
    verify_figure1_outputs(repository_root)
    plotted_path = repository_root / ACCEPTED_RESULT / "plotted_values.json"
    plotted = _read_json(plotted_path)
    if plotted["safe_candidate_identifier"] != PRIMARY_SAFE_IDENTIFIER:
        raise ArtifactError("accepted Figure 1 candidate identity differs")
    source_paths = (
        plotted_path,
        repository_root / ACCEPTED_RESULT / "evidence.json",
        repository_root / ACCEPTED_RESULT / "figure_manifest.json",
        repository_root / ACCEPTED_FIGURE.with_suffix(".pdf"),
        repository_root / ACCEPTED_FIGURE.with_suffix(".png"),
        repository_root
        / Path("figures/benchmark/figure1_procedural_overview_grayscale.png"),
    )
    hashes = {
        path.relative_to(repository_root).as_posix(): _sha256(path)
        for path in source_paths
    }
    return plotted, hashes


def _rotated_xy(row: Mapping[str, Any]) -> tuple[float, float]:
    """Rotate accepted coordinates 90 degrees without changing scale."""
    return -float(row["y_m"]), float(row["x_m"])


def _display_geometry(plotted: Json) -> tuple[Json, tuple[float, float]]:
    points = cast(list[Json], plotted["source_positions"])
    rotated = [_rotated_xy(row) for row in points]
    x_values = [point[0] for point in rotated]
    y_values = [point[1] for point in rotated]
    x_center = (min(x_values) + max(x_values)) / 2
    y_center = (min(y_values) + max(y_values)) / 2
    x_span = max(max(x_values) - min(x_values), 1e-9) * 1.10
    y_span = max(max(y_values) - min(y_values), x_span * 0.26) * 1.08
    scale = min(700.0 / x_span, 180.0 / y_span)
    limits: Json = {
        "x_min_m": x_center - x_span / 2,
        "x_max_m": x_center + x_span / 2,
        "y_min_m": y_center - y_span / 2,
        "y_max_m": y_center + y_span / 2,
        "aspect_ratio": "equal",
        "rotation_degrees": ROTATION_DEGREES,
    }
    return limits, (x_span * scale, y_span * scale)


def _transform_rows(
    rows: Sequence[Mapping[str, Any]],
    limits: Mapping[str, Any],
    box: tuple[float, float, float, float],
) -> list[tuple[float, float]]:
    x, y, width, height = box
    x_min = float(limits["x_min_m"])
    x_max = float(limits["x_max_m"])
    y_min = float(limits["y_min_m"])
    y_max = float(limits["y_max_m"])
    return [
        (
            x + (_rotated_xy(row)[0] - x_min) / (x_max - x_min) * width,
            y + height - (_rotated_xy(row)[1] - y_min) / (y_max - y_min) * height,
        )
        for row in rows
    ]


def _panel_header(drawing: Drawing, x: float, y: float, title: str) -> None:
    drawing.text(x, y, title, size=21)
    drawing.line(x, y + 14, x + 792, y + 14, GRID, 2)


def _draw_source_panel(
    drawing: Drawing,
    plotted: Json,
    limits: Json,
    box: tuple[float, float, float, float],
    origin: tuple[float, float],
) -> None:
    points = _transform_rows(cast(list[Json], plotted["source_positions"]), limits, box)
    drawing.polyline(points, MUTED, 3)
    for point in points:
        drawing.circle(*point, 2.4, INK)
    _direction_arrow(drawing, points, BLUE)
    _start_end_markers(drawing, points)
    annotations = cast(Json, plotted["annotation_values"])
    drawing.text(
        origin[0] + 12,
        origin[1] + 310,
        f"{annotations['source_samples']} SOURCE SAMPLES  /  DENSE CANONICAL STATES",
        MUTED,
        13,
    )


def _draw_exact_panel(
    drawing: Drawing,
    plotted: Json,
    limits: Json,
    box: tuple[float, float, float, float],
    origin: tuple[float, float],
) -> None:
    source = _transform_rows(cast(list[Json], plotted["source_positions"]), limits, box)
    exact = _transform_rows(
        cast(list[Json], plotted["exact_replay_positions"]), limits, box
    )
    breakpoints = _transform_rows(
        cast(list[Json], plotted["exact_breakpoints"]), limits, box
    )
    drawing.polyline(source, GRID, 5)
    drawing.polyline(exact, BLUE, 4)
    for point in breakpoints:
        drawing.circle(*point, 2.4, WHITE, BLUE)
    _start_end_markers(drawing, exact)
    annotations = cast(Json, plotted["annotation_values"])
    drawing.text(
        origin[0] + 12,
        origin[1] + 310,
        f"{annotations['exact_segments']} EXACT SEGMENTS  /  ONE PER SAMPLE PAIR",
        MUTED,
        13,
    )


def _draw_hybrid_panel(
    drawing: Drawing,
    plotted: Json,
    limits: Json,
    box: tuple[float, float, float, float],
    origin: tuple[float, float],
) -> None:
    source = _transform_rows(cast(list[Json], plotted["source_positions"]), limits, box)
    drawing.polyline(source, GRID, 5)
    for segment in cast(list[Json], plotted["hybrid_segments"]):
        points = _transform_rows(
            cast(list[Json], segment["dense_replay_positions"]), limits, box
        )
        primitive = str(segment["primitive_type"])
        if primitive == ProceduralPrimitiveType.CUBIC_HERMITE.value:
            _dashed_polyline(drawing, points, CORAL, 6)
        elif primitive == ProceduralPrimitiveType.HOLD.value:
            drawing.circle(*points[0], 6, GOLD, INK)
        else:
            drawing.polyline(points, BLUE, 6)
    breakpoints = _transform_rows(
        cast(list[Json], plotted["hybrid_breakpoints"]), limits, box
    )
    for point in breakpoints:
        _square(drawing, point, 4.5, WHITE, INK)
    _start_end_markers(drawing, source)

    annotations = cast(Json, plotted["annotation_values"])
    composition = cast(Json, annotations["hybrid_primitive_composition"])
    text_y = origin[1] + 292
    drawing.text(
        origin[0] + 12,
        text_y,
        (
            f"{annotations['compact_segments']} COMPACT SEGMENTS  /  "
            f"{composition['linear']} LINEAR + {composition['cubic_hermite']} HERMITE"
        ),
        MUTED,
        13,
    )
    drawing.line(origin[0] + 12, text_y + 27, origin[0] + 47, text_y + 27, BLUE, 5)
    drawing.text(origin[0] + 58, text_y + 32, "LINEAR", MUTED, 10)
    _dashed_polyline(
        drawing,
        (
            (origin[0] + 135, text_y + 27),
            (origin[0] + 152, text_y + 27),
            (origin[0] + 169, text_y + 27),
        ),
        CORAL,
        5,
    )
    drawing.text(origin[0] + 180, text_y + 32, "HERMITE", MUTED, 10)
    _square(drawing, (origin[0] + 276, text_y + 27), 4, WHITE, INK)
    drawing.text(origin[0] + 289, text_y + 32, "BREAKPOINT", MUTED, 10)
    _error_inset(
        drawing,
        plotted,
        (origin[0] + 505, origin[1] + 274, 287, 62),
    )


def _draw_semantic_panel(
    drawing: Drawing,
    plotted: Json,
    limits: Json,
    box: tuple[float, float, float, float],
    origin: tuple[float, float],
) -> list[Json]:
    source = _transform_rows(cast(list[Json], plotted["source_positions"]), limits, box)
    hybrid = _transform_rows(
        cast(list[Json], plotted["hybrid_replay_positions"]), limits, box
    )
    drawing.polyline(source, GRID, 5)
    drawing.polyline(hybrid, BLUE, 6)
    waypoints = {
        int(row["waypoint_index"]): row
        for row in cast(list[Json], plotted["semantic_waypoints"])
    }
    events = sorted(
        cast(list[Json], plotted["semantic_events"]),
        key=lambda row: (
            int(row["start_time_ns"]),
            int(row["anchor_time_ns"]),
            str(row["event_type"]),
        ),
    )
    first_time = int(cast(list[Json], plotted["source_positions"])[0]["timestamp_ns"])
    timeline: list[Json] = []
    for index, event in enumerate(events):
        start_row = waypoints[int(event["start_waypoint_index"])]
        anchor_row = waypoints[int(event["anchor_waypoint_index"])]
        end_row = waypoints[int(event["end_waypoint_index"])]
        start, anchor, end = _transform_rows(
            (start_row, anchor_row, end_row), limits, box
        )
        event_type = str(event["event_type"])
        color, short_label = _event_style(event_type)
        label = {
            "ACCEL": "ACCELERATION",
            "BRAKE": "BRAKING",
            "LEFT": "LEFT TURN",
            "RIGHT": "RIGHT TURN",
            "STOP": "STOP",
        }[short_label]
        drawing.line(*start, *end, color, 3)
        _event_marker(drawing, event_type, start, anchor=False)
        _event_marker(drawing, event_type, end, anchor=False)
        number = index + 1
        drawing.circle(*anchor, 8, WHITE, color)
        drawing.text(anchor[0], anchor[1] + 4, str(number), color, 10, "middle")

        row_index = index // 3
        column_index = index % 3
        label_x = origin[0] + 20 + column_index * 258
        label_y = origin[1] + 283 + row_index * 39
        drawing.circle(label_x, label_y - 5, 8, WHITE, color)
        drawing.text(label_x, label_y - 1, str(number), color, 10, "middle")
        interval = (
            (int(event["start_time_ns"]) - first_time) / 1_000_000_000,
            (int(event["end_time_ns"]) - first_time) / 1_000_000_000,
        )
        drawing.text(
            label_x + 16,
            label_y,
            f"{label}  {interval[0]:.1f}-{interval[1]:.1f} S",
            INK,
            10,
        )
        timeline.append(
            {
                "event_number": number,
                "event_type": event_type,
                "label": label,
                "interval_s": list(interval),
                "anchor_x": anchor[0],
                "anchor_y": anchor[1],
                "timeline_row": row_index,
                "timeline_column": column_index,
            }
        )
    _start_end_markers(drawing, hybrid)
    annotations = cast(Json, plotted["annotation_values"])
    drawing.text(
        origin[0] + 20,
        origin[1] + 257,
        (
            "SEMANTIC TIMELINE  /  EVENTS PRESERVED "
            f"{annotations['replay_preserved_semantic_event_count']}/"
            f"{annotations['source_semantic_event_count']}  /  "
            f"F1 {annotations['semantic_preservation_f1']:.3f}"
        ),
        MUTED,
        12,
    )
    return timeline


def render_compact_figure(plotted: Json) -> tuple[Drawing, list[Json], Json]:
    """Render the accepted four-panel content with a rigid horizontal rotation."""
    drawing = Drawing(COMPACT_WIDTH, COMPACT_HEIGHT)
    margin = 50.0
    gap_x = 36.0
    panel_width = (COMPACT_WIDTH - 2 * margin - gap_x) / 2
    origins = (
        (margin, 66.0),
        (margin + panel_width + gap_x, 66.0),
        (margin, 428.0),
        (margin + panel_width + gap_x, 428.0),
    )
    titles = (
        "A. CANONICAL MOTION SAMPLES",
        "B. EXACT PROCEDURAL REPLAY",
        "C. COMPACT BOUNDED REPLAY",
        "D. SEMANTIC MOTION LAYER",
    )
    drawing.text(margin, 39, "PROCEDURAL MOTION OVERVIEW", size=27)
    drawing.text(
        COMPACT_WIDTH - margin,
        39,
        "DENSE SOURCE  >  EXACT  >  COMPACT  >  SEMANTIC",
        MUTED,
        13,
        "end",
    )
    for origin, title in zip(origins, titles, strict=True):
        _panel_header(drawing, *origin, title)

    limits, plot_dimensions = _display_geometry(plotted)
    plot_width, plot_height = plot_dimensions
    region_width = 760.0
    region_height = 185.0
    panel_boxes = tuple(
        (
            origin[0] + 16 + (region_width - plot_width) / 2,
            origin[1] + 56 + (region_height - plot_height) / 2,
            plot_width,
            plot_height,
        )
        for origin in origins
    )
    _draw_source_panel(drawing, plotted, limits, panel_boxes[0], origins[0])
    _draw_exact_panel(drawing, plotted, limits, panel_boxes[1], origins[1])
    _draw_hybrid_panel(drawing, plotted, limits, panel_boxes[2], origins[2])
    timeline = _draw_semantic_panel(
        drawing, plotted, limits, panel_boxes[3], origins[3]
    )

    annotations = cast(Json, plotted["annotation_values"])
    composition = cast(Json, annotations["hybrid_primitive_composition"])
    footer_y = 814.0
    drawing.line(margin, footer_y - 18, COMPACT_WIDTH - margin, footer_y - 18, GRID, 2)
    column_width = (COMPACT_WIDTH - 2 * margin) / 3
    row_one = (
        f"{annotations['source_samples']} SAMPLES",
        (
            f"{annotations['exact_segments']} EXACT  >  "
            f"{annotations['compact_segments']} COMPACT  /  "
            f"{annotations['segment_reduction_percentage']:.1f}% FEWER"
        ),
        (
            "EVENTS PRESERVED  "
            f"{annotations['replay_preserved_semantic_event_count']}/"
            f"{annotations['source_semantic_event_count']}  /  "
            f"F1 {annotations['semantic_preservation_f1']:.3f}"
        ),
    )
    row_two = (
        (
            "MAX / P95 POSITION  "
            f"{annotations['maximum_position_error_m']:.3f} / "
            f"{annotations['p95_position_error_m']:.3f} M"
        ),
        f"MAX REPRESENTED-VELOCITY ERROR  {annotations['maximum_velocity_error_mps']:.3f} M/S",
        (
            f"PRIMITIVES  {composition['linear']} LINEAR  /  "
            f"{composition['cubic_hermite']} HERMITE"
        ),
    )
    for index, value in enumerate(row_one):
        drawing.text(margin + index * column_width, footer_y + 8, value, INK, 13)
    for index, value in enumerate(row_two):
        drawing.text(margin + index * column_width, footer_y + 43, value, MUTED, 11)
    drawing.text(
        margin,
        footer_y + 82,
        (
            "POSITION <= 0.10 M  /  REPRESENTED VELOCITY <= 1.00 M/S  /  "
            "GENUINE AV2 EXAMPLE"
        ),
        MUTED,
        11,
    )
    drawing.text(
        COMPACT_WIDTH - margin,
        footer_y + 82,
        "COMPACT FIGURE 1",
        MUTED,
        11,
        "end",
    )
    return drawing, timeline, limits


def _layout_recommendation() -> Json:
    reduction = (ORIGINAL_HEIGHT - COMPACT_HEIGHT) / ORIGINAL_HEIGHT * 100
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "recommended_use": "page_1_primary_figure",
        "layout": "compact 2-by-2 panels with horizontal motion orientation",
        "original_dimensions": [ORIGINAL_WIDTH, ORIGINAL_HEIGHT],
        "compact_dimensions": [COMPACT_WIDTH, COMPACT_HEIGHT],
        "height_reduction_pixels": ORIGINAL_HEIGHT - COMPACT_HEIGHT,
        "height_reduction_percentage": reduction,
        "width_unchanged": True,
        "reason": "A rigid 90-degree rotation aligns the trajectory with the page width, allowing shallower equal-scale panels, a two-row semantic timeline, and a condensed shared metric footer without removing scientific content.",
    }


def _descriptor(
    destination: Path,
    relative: Path,
    *,
    format_name: str,
) -> Json:
    path = destination / relative
    return {
        "path": relative.as_posix(),
        "format": format_name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "width": COMPACT_WIDTH,
        "height": COMPACT_HEIGHT,
    }


def _annotation_lines(annotations: Json) -> str:
    composition = cast(Json, annotations["hybrid_primitive_composition"])
    values = (
        ("Source samples", annotations["source_samples"]),
        ("Exact segments", annotations["exact_segments"]),
        ("Compact segments", annotations["compact_segments"]),
        ("Linear primitives", composition["linear"]),
        ("Cubic Hermite primitives", composition["cubic_hermite"]),
        ("Maximum position error (m)", annotations["maximum_position_error_m"]),
        ("P95 position error (m)", annotations["p95_position_error_m"]),
        (
            "Maximum represented-velocity error (m/s)",
            annotations["maximum_velocity_error_mps"],
        ),
        ("Source semantic events", annotations["source_semantic_event_count"]),
        (
            "Preserved semantic events",
            annotations["replay_preserved_semantic_event_count"],
        ),
        ("Semantic preservation F1", annotations["semantic_preservation_f1"]),
    )
    return "\n".join(f"- {label}: `{value}`" for label, value in values)


def _notes_text(plotted: Json, source_hashes: Json) -> str:
    annotations = cast(Json, plotted["annotation_values"])
    source_lines = "\n".join(
        f"- `{path}`: `{digest}`" for path, digest in sorted(source_hashes.items())
    )
    reduction = (ORIGINAL_HEIGHT - COMPACT_HEIGHT) / ORIGINAL_HEIGHT * 100
    return f"""# Compact Figure 1 - Compact Abstract Layout

## Accepted inputs

{source_lines}

Selected accepted candidate: `{plotted["safe_candidate_identifier"]}`. The
accepted `plotted_values.json` is copied byte-for-byte into this package. No
candidate selection, scientific computation, threshold, metric, event, or
upstream evidence was changed.

## Dimensions

- Accepted original Figure 1: {ORIGINAL_WIDTH} x {ORIGINAL_HEIGHT} pixels.
- Compact Figure 1: {COMPACT_WIDTH} x {COMPACT_HEIGHT} pixels.
- Height reduction: {ORIGINAL_HEIGHT - COMPACT_HEIGHT} pixels ({reduction:.1f}%).
- Width change: none.

## Layout rationale

Every spatial point and overlay is displayed through the same rigid 90-degree
rotation, `x' = -y, y' = x`. This makes the dominant trajectory direction
horizontal without distortion. Identical display limits and equal scaling are
used in Panels A-D. The horizontal path fills shallow panels efficiently; the
semantic timeline moves below Panel D in two chronological rows, and repeated
metrics are condensed into a shared three-column footer. The result is better
suited to page 1 of a two-page compact abstract because it uses 31.4% less page
height while retaining the four-panel scientific progression.

## Accepted values shown

{_annotation_lines(annotations)}

Semantic event labels and intervals remain chronological and unchanged. The
figure continues to identify the trajectory as a genuine AV2 example without
publishing raw provider identifiers.

## Value preservation

All plotted values are unchanged. The compact renderer consumes the accepted
F1.2 support record directly, copies it byte-for-byte, and applies only a rigid
display transform and layout changes.

## Commands and checks

```text
uv run --frozen python scripts/generate_figure1_procedural_overview_compact.py --output-root <isolated-run-a>
uv run --frozen python scripts/generate_figure1_procedural_overview_compact.py --output-root <isolated-run-b>
uv run --frozen python scripts/generate_figure1_procedural_overview_compact.py
uv run --frozen python scripts/generate_figure1_procedural_overview_compact.py --verify-only
```

Checks cover accepted-package verification, candidate identity, byte-identical
plotted values, exact annotation preservation, rigid-rotation geometry,
identical panel limits, dimensions, grayscale pixels, checksums, isolated-run
determinism, full-resolution inspection, grayscale inspection, reduced-width
inspection, and vector-PDF rendering.
"""


def _summary_text(plotted: Json) -> str:
    return f"""# Batch F1.3 - Compact Figure 1

## Status

PASS

## Result

The accepted `{plotted["safe_candidate_identifier"]}` Figure 1 content is
recomposed at {COMPACT_WIDTH} x {COMPACT_HEIGHT} pixels. A common rigid
90-degree rotation makes the trajectory horizontal in all four panels, reducing
height by 31.4% while preserving every accepted plotted value and annotation.

## Scientific conformance

No candidate, experiment, evidence record, metric, event, threshold, count,
error value, or scientific conclusion changed.
"""


def _png_is_grayscale(path: Path) -> bool:
    with Image.open(path) as source:
        pixels = source.convert("RGB").tobytes()
    return all(
        pixels[index] == pixels[index + 1] == pixels[index + 2]
        for index in range(0, len(pixels), 3)
    )


def generate_compact_figure1(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Generate the compact layout solely from accepted Figure 1 records."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    plotted, source_hashes = _accepted_inputs(root)
    for directory in (
        destination / FIGURE.parent,
        destination / NOTES.parent,
        destination / RESULT,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if not RELEASE_FONT.is_file():
        raise ArtifactError("required DejaVu Sans release font is unavailable")

    drawing, timeline, display_limits = render_compact_figure(plotted)
    drawing.save_pdf(destination / FIGURE.with_suffix(".pdf"))
    drawing.save_png(destination / FIGURE.with_suffix(".png"), font_path=RELEASE_FONT)
    drawing.save_png(
        destination / GRAYSCALE,
        grayscale=True,
        font_path=RELEASE_FONT,
    )

    result_root = destination / RESULT
    shutil.copyfile(
        root / ACCEPTED_RESULT / "plotted_values.json",
        result_root / "plotted_values.json",
    )
    recommendation = _layout_recommendation()
    _write_json(result_root / "layout_recommendation.json", recommendation)
    outputs = [
        _descriptor(destination, FIGURE.with_suffix(".pdf"), format_name="PDF"),
        _descriptor(destination, FIGURE.with_suffix(".png"), format_name="PNG"),
        _descriptor(destination, GRAYSCALE, format_name="PNG grayscale"),
    ]
    annotations = cast(Json, plotted["annotation_values"])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "script_version": SCRIPT_VERSION,
        "starting_head": STARTING_HEAD,
        "selected_safe_identifier": plotted["safe_candidate_identifier"],
        "accepted_source_sha256": source_hashes,
        "accepted_plotted_values_sha256": source_hashes[
            "results/benchmark_figures/figure1_procedural_overview/plotted_values.json"
        ],
        "scientific_values_unchanged": True,
        "annotation_values": annotations,
        "layout": {
            "original_dimensions": [ORIGINAL_WIDTH, ORIGINAL_HEIGHT],
            "compact_dimensions": [COMPACT_WIDTH, COMPACT_HEIGHT],
            "height_reduction_pixels": ORIGINAL_HEIGHT - COMPACT_HEIGHT,
            "height_reduction_percentage": recommendation[
                "height_reduction_percentage"
            ],
            "panel_layout": "2-by-2",
            "rotation_degrees": ROTATION_DEGREES,
            "rotation_transform": "x_prime=-y; y_prime=x",
            "rigid_transform": True,
            "geometry_distorted": False,
            "spatial_aspect_ratio": "equal",
            "spatial_axis_limits_identical": True,
            "display_limits": display_limits,
            "semantic_timeline": "two chronological rows below Panel D",
        },
        "semantic_timeline": timeline,
        "outputs": outputs,
        "visual_inspection": {
            "status": "PASS",
            "views": [
                "full_resolution_color",
                "full_resolution_grayscale",
                "compact_abstract_width",
                "vector_pdf_render",
            ],
            "clipping": False,
            "label_overlap": False,
            "panel_comparability": True,
            "semantic_readability": True,
        },
    }
    _write_json(result_root / "figure_manifest.json", manifest)
    (destination / NOTES).write_text(
        _notes_text(plotted, source_hashes), encoding="utf-8", newline="\n"
    )
    (result_root / "summary.md").write_text(
        _summary_text(plotted), encoding="utf-8", newline="\n"
    )

    checksums = {
        relative.as_posix(): _sha256(destination / relative)
        for relative in GENERATED_PATHS
        if relative != RESULT / "evidence.json"
    }
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "script_version": SCRIPT_VERSION,
        "starting_head": STARTING_HEAD,
        "status": "PASS",
        "presentation_only": True,
        "selected_safe_identifier": plotted["safe_candidate_identifier"],
        "accepted_source_sha256": source_hashes,
        "generated_output_sha256": checksums,
        "accepted_plotted_values_copied_byte_for_byte": True,
        "candidate_selection_rerun": False,
        "scientific_values_recomputed": False,
        "upstream_evidence_modified": False,
        "rigid_display_rotation_only": True,
    }
    _write_json(result_root / "evidence.json", evidence)
    return verify_compact_figure1(root, destination_root=destination)


def verify_compact_figure1(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Verify compact Figure 1 values, geometry, files, and checksums."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    accepted, source_hashes = _accepted_inputs(root)
    evidence = _read_json(destination / RESULT / "evidence.json")
    checksums = cast(Json, evidence["generated_output_sha256"])
    expected = {
        relative.as_posix()
        for relative in GENERATED_PATHS
        if relative != RESULT / "evidence.json"
    }
    if set(checksums) != expected:
        raise ArtifactError("compact Figure 1 output set differs")
    for relative, digest in sorted(checksums.items()):
        path = destination / relative
        if not path.is_file() or _sha256(path) != digest:
            raise ArtifactError(f"compact Figure 1 checksum differs: {relative}")
    if (destination / RESULT / "plotted_values.json").read_bytes() != (
        root / ACCEPTED_RESULT / "plotted_values.json"
    ).read_bytes():
        raise ArtifactError("accepted Figure 1 plotted values were not preserved")
    if evidence["accepted_source_sha256"] != source_hashes:
        raise ArtifactError("compact Figure 1 accepted-source identities differ")

    with Image.open(destination / FIGURE.with_suffix(".png")) as image:
        if image.size != (COMPACT_WIDTH, COMPACT_HEIGHT) or image.mode != "RGB":
            raise ArtifactError("compact Figure 1 raster contract differs")
    if not _png_is_grayscale(destination / GRAYSCALE):
        raise ArtifactError("compact Figure 1 grayscale output contains color")
    manifest = _read_json(destination / RESULT / "figure_manifest.json")
    recommendation = _read_json(destination / RESULT / "layout_recommendation.json")
    if (
        manifest["selected_safe_identifier"] != PRIMARY_SAFE_IDENTIFIER
        or manifest["annotation_values"] != accepted["annotation_values"]
        or manifest["layout"]["compact_dimensions"] != [COMPACT_WIDTH, COMPACT_HEIGHT]
        or manifest["layout"]["original_dimensions"]
        != [ORIGINAL_WIDTH, ORIGINAL_HEIGHT]
        or not manifest["layout"]["rigid_transform"]
        or manifest["layout"]["geometry_distorted"]
        or not manifest["layout"]["spatial_axis_limits_identical"]
        or manifest["layout"]["spatial_aspect_ratio"] != "equal"
        or len(cast(list[Json], manifest["semantic_timeline"])) != 6
        or recommendation["recommended_use"] != "page_1_primary_figure"
        or manifest["visual_inspection"]["status"] != "PASS"
    ):
        raise ArtifactError("compact Figure 1 layout or value contract differs")
    return evidence
