# Compact Table 1 - Compact Matched-Byte Summary

## Accepted inputs

- `figures/benchmark/figure2_rate_distortion.png`: `c434f27415495e7bc9db2860c2232ef96bbfa0ae96dc4a20ea2c2033a521a808`
- `results/benchmark_figures/figure2_rate_distortion/evidence.json`: `7e12aef72abc06decf3c53226532721de3abd7299161f2d08f40b9160fe6af0d`
- `results/benchmark_figures/figure2_rate_distortion/plotted_values.json`: `c28064f6735e2588f8ff8118d8dec1ee39d5d7d6068a66c4f834a7d735344f4a`
- `results/benchmark_figures/figure2_rate_distortion/primary_table_values.json`: `2d05e4700a3901f2015c1f3924f927f6a278693e56ed9577d18d5c09bc963215`
- `tables/benchmark/table1_primary_matched_byte.tex`: `8a903a0af8c0b1c2b60d2c2bb3585e1b80557e09617dda0e213d9c25f1dc92e5`

The accepted primary-table JSON is copied byte-for-byte into this package. The
compact exports select display fields from those accepted records only.

## Exact values shown

- Uniform linear: B/raw 0.4802; p95 position 0.4973 m; semantic F1 0.8816; contract none.
- Uniform Hermite: B/raw 0.4806; p95 position 0.4788 m; semantic F1 0.5770; contract none.
- Fixed-interval linear: B/raw 0.4806; p95 position 0.4973 m; semantic F1 0.8816; contract none.
- RDP linear: B/raw 0.4419; p95 position 0.9455 m; semantic F1 0.8783; contract geometric tolerance only (0.05 m).
- Position-bounded linear: B/raw 0.5074; p95 position 0.0918 m; semantic F1 0.9121; contract pos <= 0.10 m.
- Position/velocity hybrid: B/raw 0.4913; p95 position 0.0806 m; semantic F1 0.8860; contract pos <= 0.10 m; vel <= 1.00 m/s.
- Unconstrained Hermite: B/raw 0.4699; p95 position 0.0807 m; semantic F1 0.7274; contract pos <= 0.10 m.

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
