# Compact Figure 1 - Compact Abstract Layout

## Accepted inputs

- `figures/benchmark/figure1_procedural_overview.pdf`: `4368269b679bdd06ce51767cf79c60d257d3bb4dea6bbd7d8f04c38d843d5009`
- `figures/benchmark/figure1_procedural_overview.png`: `31f380c17cf19fa9ba88f52be6ae896eefbbcc4b5e450f2190e12899b4fc0e06`
- `figures/benchmark/figure1_procedural_overview_grayscale.png`: `34442ae8e15874fdd5a7612168096f430620c6124d2ccec532816619cd89f520`
- `results/benchmark_figures/figure1_procedural_overview/evidence.json`: `a9c0e0849a3db3399b84c677daeb5332ae712778bf60d233bb6d14a18a351b68`
- `results/benchmark_figures/figure1_procedural_overview/figure_manifest.json`: `edba2649a20d71bfbb1e6760d75882b9f752f8447d895a3d4afca21629007150`
- `results/benchmark_figures/figure1_procedural_overview/plotted_values.json`: `ac3636da7ad0ab4d74e4f54621778d8c545d1407cb5193c421a0f7626ad3e73e`

Selected accepted candidate: `trajectory-c3b9d7c1f89e1c25`. The
accepted `plotted_values.json` is copied byte-for-byte into this package. No
candidate selection, scientific computation, threshold, metric, event, or
upstream evidence was changed.

## Dimensions

- Accepted original Figure 1: 1800 x 1400 pixels.
- Compact Figure 1: 1800 x 960 pixels.
- Height reduction: 440 pixels (31.4%).
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

- Source samples: `110`
- Exact segments: `109`
- Compact segments: `5`
- Linear primitives: `1`
- Cubic Hermite primitives: `4`
- Maximum position error (m): `0.07827359002814926`
- P95 position error (m): `0.0741763222339639`
- Maximum represented-velocity error (m/s): `0.4473862775795578`
- Source semantic events: `6`
- Preserved semantic events: `5`
- Semantic preservation F1: `0.9090909090909091`

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
