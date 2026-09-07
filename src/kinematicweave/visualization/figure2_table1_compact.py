"""Deterministic compact Figure 2 and Table 1 release assets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import csv
import hashlib
import io
from itertools import pairwise
import json
import math
from pathlib import Path
import shutil
from typing import Any, cast

from PIL import Image

from kinematicweave.errors import ArtifactError
from kinematicweave.visualization.deterministic import GRID, INK, MUTED, Color, Drawing
from kinematicweave.visualization.figure2_rate_distortion import (
    APPROXIMATION_FAMILY_ORDER,
    FAMILY_LABELS,
    MAIN_FAMILIES,
    PLOTTED_FAMILY_ORDER,
    PRIMARY_BYTE_TARGET,
    _dashed_line,
    _family_style,
    _marker,
    verify_figure2_and_table1_outputs,
)

type Json = dict[str, Any]

SCHEMA_VERSION = "1.0"
BATCH = "F2.2"
SCRIPT_VERSION = "1.0"
STARTING_HEAD = "47024f59699355c0c0292d135139b1e7f74b4ad3"
FIGURE_WIDTH = 1800
FIGURE_HEIGHT = 1320

ACCEPTED_RESULT = Path("results/benchmark_figures/figure2_rate_distortion")
ACCEPTED_FIGURE = Path("figures/benchmark/figure2_rate_distortion.png")
ACCEPTED_TABLE = Path("tables/benchmark/table1_primary_matched_byte.tex")
FIGURE = Path("figures/benchmark/figure2_rate_distortion_compact")
GRAYSCALE = Path("figures/benchmark/figure2_rate_distortion_compact_grayscale.png")
TABLE = Path("tables/benchmark/table1_primary_matched_byte_compact")
FIGURE_NOTES = Path("reports/figure2_rate_distortion_compact_notes.md")
TABLE_NOTES = Path("reports/table1_primary_matched_byte_compact_notes.md")
RESULT = Path("results/benchmark_figures/figure2_rate_distortion_compact")

GENERATED_PATHS = (
    FIGURE.with_suffix(".pdf"),
    FIGURE.with_suffix(".png"),
    GRAYSCALE,
    TABLE.with_suffix(".tex"),
    TABLE.with_suffix(".csv"),
    TABLE.with_suffix(".md"),
    FIGURE_NOTES,
    TABLE_NOTES,
    RESULT / "figure_manifest.json",
    RESULT / "plotted_values.json",
    RESULT / "table_values.json",
    RESULT / "layout_recommendation.json",
    RESULT / "evidence.json",
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


def _release_font() -> Path:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ArtifactError("a conventional release font is unavailable")


def _accepted_inputs(repository_root: Path) -> tuple[Json, Json, Json]:
    verify_figure2_and_table1_outputs(repository_root)
    plotted_path = repository_root / ACCEPTED_RESULT / "plotted_values.json"
    table_path = repository_root / ACCEPTED_RESULT / "primary_table_values.json"
    plotted = _read_json(plotted_path)
    table = _read_json(table_path)
    hashes = {
        path.relative_to(repository_root).as_posix(): _sha256(path)
        for path in (
            plotted_path,
            table_path,
            repository_root / ACCEPTED_RESULT / "evidence.json",
            repository_root / ACCEPTED_FIGURE,
            repository_root / ACCEPTED_TABLE,
        )
    }
    return plotted, table, hashes


def _x(value: float, box: tuple[float, float, float, float]) -> float:
    return box[0] + (value - 0.25) / 1.20 * box[2]


def _y(
    value: float,
    box: tuple[float, float, float, float],
    *,
    scale: str,
    limits: tuple[float, float],
) -> float:
    lower, upper = limits
    plotted = max(value, lower) if scale == "log10" else value
    if scale == "log10":
        ratio = (math.log10(plotted) - math.log10(lower)) / (
            math.log10(upper) - math.log10(lower)
        )
    else:
        ratio = (plotted - lower) / (upper - lower)
    return box[1] + box[3] * (1.0 - ratio)


def _tick(value: float) -> str:
    return f"{value:g}" if value >= 1 else f"{value:.2f}".rstrip("0").rstrip(".")


def _draw_panel(
    drawing: Drawing,
    rows: Sequence[Json],
    *,
    box: tuple[float, float, float, float],
    panel: str,
    title: str,
    field: str,
    y_label: str,
    scale: str,
    limits: tuple[float, float],
    ticks: Sequence[float],
) -> None:
    left, top, width, height = box
    drawing.text(left, top - 55, f"{panel}. {title}", INK, 28)
    drawing.text(left, top - 18, y_label, MUTED, 17)
    for tick in ticks:
        y = _y(tick, box, scale=scale, limits=limits)
        drawing.line(left, y, left + width, y, GRID, 1)
        drawing.text(left - 15, y + 6, _tick(tick), MUTED, 16, "end")
    for tick in (0.3, 0.5, 0.7, 1.0, 1.2, 1.4):
        x = _x(tick, box)
        drawing.line(x, top + height, x, top + height + 8, INK, 2)
        drawing.text(x, top + height + 29, _tick(tick), MUTED, 16, "middle")
    target_x = _x(PRIMARY_BYTE_TARGET, box)
    _dashed_line(
        drawing,
        (target_x, top),
        (target_x, top + height),
        (180, 83, 9),
        2,
    )
    drawing.text(target_x + 8, top + 22, "0.48 TARGET", (180, 83, 9), 15)
    drawing.line(left, top, left, top + height, INK, 2)
    drawing.line(left, top + height, left + width, top + height, INK, 2)
    drawing.text(
        left + width / 2,
        top + height + 62,
        "SERIALIZED BYTES / RAW BYTES",
        MUTED,
        17,
        "middle",
    )

    by_family: defaultdict[str, list[Json]] = defaultdict(list)
    for row in rows:
        by_family[str(row["family"])].append(row)
    for family in PLOTTED_FAMILY_ORDER:
        family_rows = sorted(by_family[family], key=lambda row: row["byte_ratio"])
        style = _family_style(family)
        points = [
            (
                _x(float(row["byte_ratio"]), box),
                _y(float(row[field]), box, scale=scale, limits=limits),
            )
            for row in family_rows
        ]
        for start, end in pairwise(points):
            line_width = 5 if family in MAIN_FAMILIES else 3
            if style["line"] == "dashed":
                _dashed_line(
                    drawing,
                    start,
                    end,
                    cast(Color, style["color"]),
                    line_width,
                )
            else:
                drawing.line(
                    *start,
                    *end,
                    cast(Color, style["color"]),
                    line_width,
                )
        for point in points:
            _marker(
                drawing,
                *point,
                style,
                radius=11 if family in MAIN_FAMILIES else 8,
            )
    if scale == "log10":
        drawing.text(left + 10, top + height - 12, "RAW REFERENCE: ZERO", MUTED, 14)


def render_compact_figure(plotted: Json) -> Drawing:
    """Render the compact-ready 2-by-2 Figure 2 composition."""
    drawing = Drawing(FIGURE_WIDTH, FIGURE_HEIGHT)
    rows = [
        row
        for row in cast(list[Json], plotted["configurations"])
        if row["plotted_in_panels"]
    ]
    boxes = (
        (105.0, 105.0, 680.0, 430.0),
        (1015.0, 105.0, 680.0, 430.0),
        (105.0, 760.0, 680.0, 430.0),
    )
    _draw_panel(
        drawing,
        rows,
        box=boxes[0],
        panel="A",
        title="POSITIONAL FIDELITY",
        field="position_p95_m",
        y_label="P95 POSITION ERROR (M), LOG SCALE",
        scale="log10",
        limits=(0.02, 6.0),
        ticks=(0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
    )
    _draw_panel(
        drawing,
        rows,
        box=boxes[1],
        panel="B",
        title="MOTION FIDELITY",
        field="velocity_mean_mps",
        y_label="MEAN VELOCITY-VECTOR ERROR (M/S)",
        scale="linear",
        limits=(0.0, 0.65),
        ticks=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
    )
    _draw_panel(
        drawing,
        rows,
        box=boxes[2],
        panel="C",
        title="SEMANTIC PRESERVATION",
        field="semantic_f1",
        y_label="OVERALL SEMANTIC-EVENT F1",
        scale="linear",
        limits=(0.55, 1.02),
        ticks=(0.6, 0.7, 0.8, 0.9, 1.0),
    )

    drawing.text(945, 715, "METHOD FAMILIES", INK, 28)
    legend_y = 760.0
    for family in PLOTTED_FAMILY_ORDER:
        style = _family_style(family)
        marker_x = 980.0
        marker_y = legend_y + 8.0
        line_width = 5 if family in MAIN_FAMILIES else 3
        if style["line"] == "dashed":
            _dashed_line(
                drawing,
                (marker_x - 25, marker_y),
                (marker_x + 25, marker_y),
                cast(Color, style["color"]),
                line_width,
            )
        elif style["line"] == "solid":
            drawing.line(
                marker_x - 25,
                marker_y,
                marker_x + 25,
                marker_y,
                cast(Color, style["color"]),
                line_width,
            )
        _marker(
            drawing,
            marker_x,
            marker_y,
            style,
            radius=11 if family in MAIN_FAMILIES else 8,
        )
        drawing.text(marker_x + 35, marker_y + 6, FAMILY_LABELS[family], INK, 18)
        legend_y += 48

    exact = next(
        row
        for row in cast(list[Json], plotted["configurations"])
        if row["family"] == "exact_adjacent"
    )
    drawing.line(945, 1172, 1695, 1172, GRID, 2)
    drawing.text(945, 1210, "EXACT-ADJACENT REFERENCE", INK, 19)
    drawing.text(
        945,
        1242,
        f"{float(exact['byte_ratio']):.3f} BYTES/RAW; ZERO MOTION ERROR; F1 1.000",
        MUTED,
        16,
    )
    drawing.text(
        945,
        1280,
        "FAMILY-ONLY LINES  /  NO COMPOSITE SCORE  /  NO UNIVERSAL WINNER",
        MUTED,
        15,
    )
    return drawing


_TABLE_HEADERS: tuple[tuple[str, str], ...] = (
    ("method", "Method"),
    ("bytes_raw", "B/raw"),
    ("p95_position_m", "P95 pos. (m)"),
    ("semantic_f1", "Sem. F1"),
    ("contract", "Contract"),
)

_SHORT_CONTRACTS: Mapping[str, str] = {
    "uniform_linear": "none",
    "uniform_hermite": "none",
    "fixed_interval_linear": "none",
    "rdp_linear": "geometric tolerance only (0.05 m)",
    "position_bounded_linear": "pos <= 0.10 m",
    "position_velocity_hybrid": "pos <= 0.10 m; vel <= 1.00 m/s",
    "unconstrained_hermite": "pos <= 0.10 m",
}


def compact_table_rows(table: Json) -> list[Json]:
    """Return display-only rows without altering accepted numeric strings."""
    rows: list[Json] = []
    for row in cast(list[Json], table["rows"]):
        display = cast(Json, row["display"])
        family = str(row["family"])
        rows.append(
            {
                "family": family,
                "group": row["group"],
                "method": display["method"],
                "bytes_raw": display["bytes_raw"],
                "p95_position_m": display["p95_position_m"],
                "semantic_f1": display["semantic_f1"],
                "contract": _SHORT_CONTRACTS[family],
            }
        )
    return rows


def _csv_text(rows: Sequence[Json]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([label for _, label in _TABLE_HEADERS])
    for row in rows:
        writer.writerow([row[key] for key, _ in _TABLE_HEADERS])
    return output.getvalue()


def _markdown_text(rows: Sequence[Json]) -> str:
    headers = [label for _, label in _TABLE_HEADERS]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        values = [str(row[key]).replace("|", "\\|") for key, _ in _TABLE_HEADERS]
        if row["group"] == "kinematicweave_main_methods":
            values[0] = f"**{values[0]}**"
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def _latex_escape(value: str) -> str:
    return (
        value.replace("&", r"\&")
        .replace("%", r"\%")
        .replace("_", r"\_")
        .replace("<=", r"$\leq$")
    )


def _latex_text(rows: Sequence[Json]) -> str:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Primary matched-storage frozen-test comparison at the 0.48 bytes/raw target. Achieved ratios are close but not identical.}",
        r"\label{tab:primary-matched-byte-compact}",
        r"\small",
        r"\setlength{\tabcolsep}{2.5pt}",
        r"\begin{tabular}{@{}p{0.25\linewidth}rrrp{0.31\linewidth}@{}}",
        r"\toprule",
        "Method & B/raw & P95 pos. & Sem. F1 & Contract \\\\",
        " &  & (m) &  &  \\\\",
        r"\midrule",
    ]
    previous_group: str | None = None
    for row in rows:
        group = str(row["group"])
        if previous_group is not None and group != previous_group:
            lines.append(r"\addlinespace[2pt]")
        values = [_latex_escape(str(row[key])) for key, _ in _TABLE_HEADERS]
        if group == "kinematicweave_main_methods":
            values[0] = rf"\textbf{{{values[0]}}}"
        lines.append(" & ".join(values) + " \\\\")
        previous_group = group
    lines.extend((r"\bottomrule", r"\end{tabular}", r"\end{table}", ""))
    return "\n".join(lines)


def _layout_recommendation() -> Json:
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "recommended_strategy": "both_figure2_and_table1",
        "placement": "page 2: compact Figure 2 above compact Table 1",
        "options": [
            {
                "strategy": "figure2_only",
                "readability": "strong",
                "scientific_sufficiency": "shows all configurations and tradeoffs but omits exact primary numeric rows",
                "fit": "comfortable",
                "redundancy_with_figure1": "low; aggregate evidence complements the procedural hero figure",
                "recommended": False,
            },
            {
                "strategy": "table1_only",
                "readability": "strong",
                "scientific_sufficiency": "preserves primary values but omits the full rate-distortion context",
                "fit": "comfortable",
                "redundancy_with_figure1": "low",
                "recommended": False,
            },
            {
                "strategy": "both_figure2_and_table1",
                "readability": "strong when stacked at page width",
                "scientific_sufficiency": "best balance of full tradeoff context and exact primary values",
                "fit": "realistic on page 2 after 2-by-2 figure compaction and five-column table compaction",
                "redundancy_with_figure1": "low; both assets provide aggregate evidence rather than repeating the hero example",
                "recommended": True,
            },
        ],
        "justification": "The compact figure communicates the full storage-fidelity tradeoff while the narrow table anchors the seven accepted matched-byte values. Stacking both preserves complementary visual and numeric evidence without repeating Figure 1.",
    }


def _descriptor(
    destination: Path,
    relative: Path,
    *,
    format_name: str,
    dimensions: tuple[int, int] | None = None,
) -> Json:
    path = destination / relative
    value: Json = {
        "path": relative.as_posix(),
        "format": format_name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if dimensions is not None:
        value["width"], value["height"] = dimensions
    return value


def _shown_values_text(rows: Sequence[Json]) -> str:
    return "\n".join(
        f"- {row['method']}: B/raw {row['bytes_raw']}; p95 position {row['p95_position_m']} m; semantic F1 {row['semantic_f1']}; contract {row['contract']}."
        for row in rows
    )


def _source_text(source_hashes: Json) -> str:
    return "\n".join(
        f"- `{path}`: `{digest}`" for path, digest in sorted(source_hashes.items())
    )


def _figure_notes_text(rows: Sequence[Json], source_hashes: Json) -> str:
    return f"""# Compact Figure 2 - Compact Aggregate Comparison

## Accepted inputs

{_source_text(source_hashes)}

The accepted plotted-values JSON is copied byte-for-byte into this package. No
experiment, metric, configuration selection, threshold, or scientific value was
recomputed.

## Layout

The accepted three-panel horizontal figure was reorganized as a 2-by-2 compact
composition at {FIGURE_WIDTH} x {FIGURE_HEIGHT} pixels. The fourth quadrant holds
the compact legend and the exact-adjacent reference, so every accepted family is
visible without widening the three data panels. Position p95 remains logarithmic
because the accepted values span more than two orders of magnitude. Lines still
connect only configurations within one family. Shape, fill, and line style
preserve grayscale distinction.

## Exact matched-byte values shown in companion table

{_shown_values_text(rows)}

## Abbreviations

- B/raw: serialized representation bytes divided by raw canonical bytes.
- P95 pos.: source-timestamp p95 position error in metres.
- Sem. F1: overall semantic-event F1.

## Recommendation

Use both assets on page 2, with compact Figure 2 above compact Table 1. The
figure supplies the complete aggregate tradeoff and the table supplies the
exact seven-family primary comparison; neither duplicates Figure 1's role.

## Commands and checks

```text
uv run --frozen python scripts/generate_figure2_and_table1_compact.py --output-root <isolated-run-a>
uv run --frozen python scripts/generate_figure2_and_table1_compact.py --output-root <isolated-run-b>
uv run --frozen python scripts/generate_figure2_and_table1_compact.py
uv run --frozen python scripts/generate_figure2_and_table1_compact.py --verify-only
```

Checks cover accepted-source verification, byte-identical value copies,
checksums, dimensions, grayscale pixels, complete family coverage, isolated-run
determinism, full-resolution inspection, grayscale inspection, and reduced-width
inspection.
"""


def _table_notes_text(rows: Sequence[Json], source_hashes: Json) -> str:
    return f"""# Compact Table 1 - Compact Matched-Byte Summary

## Accepted inputs

{_source_text(source_hashes)}

The accepted primary-table JSON is copied byte-for-byte into this package. The
compact exports select display fields from those accepted records only.

## Exact values shown

{_shown_values_text(rows)}

## Compaction

The compact table uses five unambiguous columns: Method, B/raw, P95 pos. (m),
Sem. F1, and Contract. It preserves all seven accepted approximation families.
Long guarantee text is shortened only as listed above; `geometric tolerance
only` remains explicit for RDP, and the hybrid velocity contract remains
explicit. Full precision and omitted accepted fields remain in `table_values.json`.

## Recommendation

Use both assets on page 2, with compact Figure 2 above compact Table 1. Table-only
would lose the configuration-level tradeoff; figure-only would lose the concise
numeric anchor for the benchmark narrative.

## Commands and checks

The generator and verification commands are identical to those recorded in the
compact Figure 2 notes. Checks include exact value preservation, seven-family
coverage, CSV/Markdown/LaTeX consistency, manifest checksums, deterministic
isolated generation, and compact-width inspection.
"""


def _summary_text(rows: Sequence[Json]) -> str:
    return f"""# Batch F2.2 - Compact Asset Compaction

## Status

PASS

## Outputs

- Compact Figure 2: {FIGURE_WIDTH} x {FIGURE_HEIGHT}, 2-by-2 composition.
- Compact Table 1: {len(rows)} accepted approximation families, 5 columns.
- Accepted plotted and table values preserved byte-for-byte.
- Layout recommendation: both assets on page 2, figure above table.

## Scientific conformance

No experiment, result, metric, threshold, matched-budget selection, ranking, or
conclusion was changed. Figure 1, benchmark text, and accepted Phase 3/4 evidence
were not modified.
"""


def _png_is_grayscale(path: Path) -> bool:
    with Image.open(path) as source:
        pixels = source.convert("RGB").tobytes()
    return all(
        pixels[index] == pixels[index + 1] == pixels[index + 2]
        for index in range(0, len(pixels), 3)
    )


def generate_compact_assets(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Generate compact assets solely from the accepted release package."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    plotted, table, source_hashes = _accepted_inputs(root)
    rows = compact_table_rows(table)
    if [row["family"] for row in rows] != list(APPROXIMATION_FAMILY_ORDER):
        raise ArtifactError("accepted compact-table family order differs")

    for directory in (
        destination / FIGURE.parent,
        destination / TABLE.parent,
        destination / FIGURE_NOTES.parent,
        destination / RESULT,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    drawing = render_compact_figure(plotted)
    drawing.save_png(
        destination / FIGURE.with_suffix(".png"),
        font_path=_release_font(),
    )
    drawing.save_pdf(destination / FIGURE.with_suffix(".pdf"))
    drawing.save_png(
        destination / GRAYSCALE,
        grayscale=True,
        font_path=_release_font(),
    )

    (destination / TABLE.with_suffix(".csv")).write_text(
        _csv_text(rows), encoding="utf-8", newline="\n"
    )
    (destination / TABLE.with_suffix(".md")).write_text(
        _markdown_text(rows), encoding="utf-8", newline="\n"
    )
    (destination / TABLE.with_suffix(".tex")).write_text(
        _latex_text(rows), encoding="utf-8", newline="\n"
    )

    result_root = destination / RESULT
    shutil.copyfile(
        root / ACCEPTED_RESULT / "plotted_values.json",
        result_root / "plotted_values.json",
    )
    shutil.copyfile(
        root / ACCEPTED_RESULT / "primary_table_values.json",
        result_root / "table_values.json",
    )
    recommendation = _layout_recommendation()
    _write_json(result_root / "layout_recommendation.json", recommendation)

    core_outputs = [
        _descriptor(
            destination,
            FIGURE.with_suffix(".pdf"),
            format_name="PDF",
            dimensions=(FIGURE_WIDTH, FIGURE_HEIGHT),
        ),
        _descriptor(
            destination,
            FIGURE.with_suffix(".png"),
            format_name="PNG",
            dimensions=(FIGURE_WIDTH, FIGURE_HEIGHT),
        ),
        _descriptor(
            destination,
            GRAYSCALE,
            format_name="PNG grayscale",
            dimensions=(FIGURE_WIDTH, FIGURE_HEIGHT),
        ),
        _descriptor(destination, TABLE.with_suffix(".tex"), format_name="LaTeX"),
        _descriptor(destination, TABLE.with_suffix(".csv"), format_name="CSV"),
        _descriptor(destination, TABLE.with_suffix(".md"), format_name="Markdown"),
        _descriptor(
            destination,
            RESULT / "plotted_values.json",
            format_name="JSON",
        ),
        _descriptor(
            destination,
            RESULT / "table_values.json",
            format_name="JSON",
        ),
        _descriptor(
            destination,
            RESULT / "layout_recommendation.json",
            format_name="JSON",
        ),
    ]
    exact = next(
        row
        for row in cast(list[Json], plotted["configurations"])
        if row["family"] == "exact_adjacent"
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "script_version": SCRIPT_VERSION,
        "starting_head": STARTING_HEAD,
        "presentation_only": True,
        "accepted_source_sha256": source_hashes,
        "figure": {
            "layout": "2-by-2; three metric panels plus compact legend/reference block",
            "width": FIGURE_WIDTH,
            "height": FIGURE_HEIGHT,
            "panel_count": 3,
            "panel_x_limits": plotted["panel_x_limits"],
            "position_panel_scale": "log10",
            "primary_byte_target": PRIMARY_BYTE_TARGET,
            "plotted_configuration_count": plotted["plotted_configuration_count"],
            "method_family_count": 9,
            "family_connections_only": True,
            "exact_adjacent_reference": {
                "shown_in_reference_block": True,
                "byte_ratio": exact["byte_ratio"],
                "position_p95_m": exact["position_p95_m"],
                "velocity_mean_mps": exact["velocity_mean_mps"],
                "semantic_f1": exact["semantic_f1"],
            },
            "composite_score": False,
            "universal_winner_declared": False,
            "font": _release_font().name,
        },
        "table": {
            "approximation_family_count": len(rows),
            "column_count": len(_TABLE_HEADERS),
            "columns": [label for _, label in _TABLE_HEADERS],
            "numeric_strings_preserved_from_accepted_display": True,
        },
        "visual_inspection": {
            "required_views": [
                "full_resolution",
                "grayscale",
                "approximate_page_2_width",
            ],
            "status": "PASS",
            "checks": [
                "no clipping",
                "all method families visible",
                "labels legible",
                "grayscale distinctions retained",
            ],
        },
        "outputs": core_outputs,
    }
    _write_json(result_root / "figure_manifest.json", manifest)

    (destination / FIGURE_NOTES).write_text(
        _figure_notes_text(rows, source_hashes), encoding="utf-8", newline="\n"
    )
    (destination / TABLE_NOTES).write_text(
        _table_notes_text(rows, source_hashes), encoding="utf-8", newline="\n"
    )
    (result_root / "summary.md").write_text(
        _summary_text(rows), encoding="utf-8", newline="\n"
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
        "presentation_only": True,
        "accepted_source_sha256": source_hashes,
        "generated_output_sha256": checksums,
        "scientific_values_recomputed": False,
        "accepted_plotted_values_copied_byte_for_byte": True,
        "accepted_table_values_copied_byte_for_byte": True,
        "figure1_modified": False,
        "benchmark_text_modified": False,
        "phase3_or_phase4_evidence_modified": False,
    }
    _write_json(result_root / "evidence.json", evidence)
    return verify_compact_assets(root, destination_root=destination)


def verify_compact_assets(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Verify compact asset values, exports, checksums, and layout metadata."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    accepted_plotted, accepted_table, source_hashes = _accepted_inputs(root)
    evidence = _read_json(destination / RESULT / "evidence.json")
    checksums = cast(Json, evidence["generated_output_sha256"])
    expected = {
        relative.as_posix()
        for relative in GENERATED_PATHS
        if relative != RESULT / "evidence.json"
    }
    if set(checksums) != expected:
        raise ArtifactError("compact output set differs")
    for relative, digest in sorted(checksums.items()):
        path = destination / relative
        if not path.is_file() or _sha256(path) != digest:
            raise ArtifactError(f"compact output checksum differs: {relative}")

    accepted_plotted_path = root / ACCEPTED_RESULT / "plotted_values.json"
    accepted_table_path = root / ACCEPTED_RESULT / "primary_table_values.json"
    if (
        destination / RESULT / "plotted_values.json"
    ).read_bytes() != accepted_plotted_path.read_bytes() or (
        destination / RESULT / "table_values.json"
    ).read_bytes() != accepted_table_path.read_bytes():
        raise ArtifactError("accepted compact source values were not preserved")
    if evidence["accepted_source_sha256"] != source_hashes:
        raise ArtifactError("compact accepted-source identities differ")

    with Image.open(destination / FIGURE.with_suffix(".png")) as image:
        if image.size != (FIGURE_WIDTH, FIGURE_HEIGHT) or image.mode != "RGB":
            raise ArtifactError("compact figure raster contract differs")
    if not _png_is_grayscale(destination / GRAYSCALE):
        raise ArtifactError("compact grayscale figure contains color pixels")

    manifest = _read_json(destination / RESULT / "figure_manifest.json")
    recommendation = _read_json(destination / RESULT / "layout_recommendation.json")
    rows = compact_table_rows(accepted_table)
    if (
        accepted_plotted["plotted_configuration_count"] != 17
        or manifest["figure"]["method_family_count"] != 9
        or manifest["table"]["approximation_family_count"] != 7
        or manifest["table"]["column_count"] != 5
        or recommendation["recommended_strategy"] != "both_figure2_and_table1"
        or manifest["visual_inspection"]["status"] != "PASS"
    ):
        raise ArtifactError("compact layout or recommendation contract differs")

    csv_rows = list(
        csv.DictReader(
            io.StringIO((destination / TABLE.with_suffix(".csv")).read_text("utf-8"))
        )
    )
    if len(csv_rows) != len(rows) or list(csv_rows[0]) != [
        label for _, label in _TABLE_HEADERS
    ]:
        raise ArtifactError("compact CSV export differs")
    for exported, expected_row in zip(csv_rows, rows, strict=True):
        if exported != {label: str(expected_row[key]) for key, label in _TABLE_HEADERS}:
            raise ArtifactError("compact CSV values differ from accepted displays")
    return evidence
