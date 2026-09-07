# Figure 2 - Aggregate Rate-Distortion Comparison

## Purpose

Compare all accepted frozen-test motion configurations using actual serialized
storage, time-indexed positional fidelity, motion-dynamics fidelity, and
semantic-event preservation. No composite score or universal winner is used.

## Accepted sources

- `results/phase4/protocol_freeze/evidence.json`
- `results/phase4/frozen_campaign/test_results.json`
- `results/phase4/frozen_campaign/matched_byte_results.json`
- `results/phase4/statistical_analysis/evidence.json`
- `results/phase4/ablation_analysis/evidence.json`
- `results/phase4/qualitative_motion/evidence.json`

The accepted cohort contains 300 scenarios and
13689 trajectories. Cohort identity:
`dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166`. Matrix identity:
`c87a88e2f6a6a443a2654f5785cec16ab15e8ad8e2b1fef2d6d2bde8d5d59599`. Metric identity:
`03440d32241c0e55d490cf617cd1b2d9ed1a546865372b8dc155f06c696e3277`.

## Visual encoding

The figure uses one row of three panels at 2400 x 850 pixels. All
17 remaining frozen configurations are
plotted. Configurations are connected only within one family and ordered by
achieved byte ratio. Shape and line style supplement color. The raw reference
uses an open neutral marker, standard baselines use muted treatments, the two
main KinematicWeave methods use prominent blue and green filled markers, and the
unconstrained Hermite ablation uses an open coral diamond with a dashed
treatment. The redundant internal figure title was removed because the LaTeX
caption carries the formal title.

## Axis scales

The x-axis is linear from 0.25 to 1.45, covering every plotted configuration
while improving separation around the 0.48 primary budget. Panel A uses a
logarithmic y-axis because accepted p95 position errors span more than two
orders of magnitude; the raw zero-error reference is explicitly plotted at the
0.02 m axis floor and labeled. Panels B and C use linear y-axes. No plotted
point is clipped or omitted.

Exact-adjacent replay is retained in full-precision provenance but omitted from
the panels because it is a correctness reference rather than an approximation
competitor. Its accepted serialized storage ratio is
2.525529, approximately 2.53 times raw storage.

## Configurations shown

- Raw samples: 1
- Uniform linear: 3
- Uniform Hermite: 3
- Fixed-interval linear: 3
- RDP linear: 4
- Position-bounded linear: 1
- Position/velocity hybrid: 1
- Unconstrained Hermite: 1

## Caption

Aggregate frozen-test rate-distortion comparison. Every plotted point is one accepted test configuration; x is actual serialized representation bytes relative to raw canonical storage, and the vertical line marks the 0.48 primary byte budget. Exact-adjacent replay is omitted from the panels because it is a correctness reference requiring approximately 2.53 times raw storage, not an approximation competitor. Panels report p95 source-timestamp position error in metres (log scale, with the zero-error raw reference shown at the axis floor), mean velocity-vector error in m/s, and overall semantic-event F1. No composite score or universal winner is implied.

## Reproduction

`uv run --frozen python scripts/generate_figure2_and_table1.py`

## Output checksums

- `figures/benchmark/figure2_rate_distortion.pdf`: `2fff48f48a94f4563e33a8ce5c8056fb535cd2ac4f3c8fec2fb2add134f5b181`
- `figures/benchmark/figure2_rate_distortion.png`: `c434f27415495e7bc9db2860c2232ef96bbfa0ae96dc4a20ea2c2033a521a808`
- `figures/benchmark/figure2_rate_distortion_grayscale.png`: `e7890fab13f0a37bb2c624f269be91a3fa91c75703e8834da4e1f70f6271f52b`
- `figures/showcase/representative_figure1.jpg`: `6179609118b0b32d923938eb1e4deba806bacb436e4431270464bf5e68114ab8`
- `figures/showcase/representative_figure1_web.png`: `31f380c17cf19fa9ba88f52be6ae896eefbbcc4b5e450f2190e12899b4fc0e06`
