# Compact Figure 2 - Compact Aggregate Comparison

## Accepted inputs

- `figures/benchmark/figure2_rate_distortion.png`: `c434f27415495e7bc9db2860c2232ef96bbfa0ae96dc4a20ea2c2033a521a808`
- `results/benchmark_figures/figure2_rate_distortion/evidence.json`: `7e12aef72abc06decf3c53226532721de3abd7299161f2d08f40b9160fe6af0d`
- `results/benchmark_figures/figure2_rate_distortion/plotted_values.json`: `c28064f6735e2588f8ff8118d8dec1ee39d5d7d6068a66c4f834a7d735344f4a`
- `results/benchmark_figures/figure2_rate_distortion/primary_table_values.json`: `2d05e4700a3901f2015c1f3924f927f6a278693e56ed9577d18d5c09bc963215`
- `tables/benchmark/table1_primary_matched_byte.tex`: `8a903a0af8c0b1c2b60d2c2bb3585e1b80557e09617dda0e213d9c25f1dc92e5`

The accepted plotted-values JSON is copied byte-for-byte into this package. No
experiment, metric, configuration selection, threshold, or scientific value was
recomputed.

## Layout

The accepted three-panel horizontal figure was reorganized as a 2-by-2 compact
composition at 1800 x 1320 pixels. The fourth quadrant holds
the compact legend and the exact-adjacent reference, so every accepted family is
visible without widening the three data panels. Position p95 remains logarithmic
because the accepted values span more than two orders of magnitude. Lines still
connect only configurations within one family. Shape, fill, and line style
preserve grayscale distinction.

## Exact matched-byte values shown in companion table

- Uniform linear: B/raw 0.4802; p95 position 0.4973 m; semantic F1 0.8816; contract none.
- Uniform Hermite: B/raw 0.4806; p95 position 0.4788 m; semantic F1 0.5770; contract none.
- Fixed-interval linear: B/raw 0.4806; p95 position 0.4973 m; semantic F1 0.8816; contract none.
- RDP linear: B/raw 0.4419; p95 position 0.9455 m; semantic F1 0.8783; contract geometric tolerance only (0.05 m).
- Position-bounded linear: B/raw 0.5074; p95 position 0.0918 m; semantic F1 0.9121; contract pos <= 0.10 m.
- Position/velocity hybrid: B/raw 0.4913; p95 position 0.0806 m; semantic F1 0.8860; contract pos <= 0.10 m; vel <= 1.00 m/s.
- Unconstrained Hermite: B/raw 0.4699; p95 position 0.0807 m; semantic F1 0.7274; contract pos <= 0.10 m.

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
