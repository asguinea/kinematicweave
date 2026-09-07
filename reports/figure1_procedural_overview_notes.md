# Figure 1 — Procedural Motion Overview

## 1. Figure purpose

Explain how one genuine canonical AV2 trajectory becomes an exact procedural
replay, then a compact position-and-velocity-bounded replay, and finally a
semantic motion layer. Batch F1.1 selected and verified the candidates; Batch
F1.2 preserves their scientific content and records the human-reviewed final
release choice.

## 2. Accepted input artifacts

Committed Phase 3 exact, linear, Hermite, hybrid, semantic, and milestone
evidence; committed Phase 4 frozen-campaign and qualitative-motion evidence;
verified local frozen metric and representation artifacts. The accepted test
cohort identity is `cee7af2ae750b8b2ab2e88d6940aefcb7d63fa24570af1c2946f53c5d0c550df`.

## 3. Deterministic candidate-selection rule

Eligibility and ranking are fixed in
`results/benchmark_figures/figure1_procedural_overview/selection_contract.json`.
Ranking is lexicographic by event-type diversity, segment reduction, curvature,
semantic F1, sample count, clutter, and safe identifier. No raw-ID allowlist is
used. The later human visual comparison does not change this ranking record.

## 4. Eligible candidate count

5087 candidates.

## 5. Top three candidates

- Rank 1: `trajectory-c3b9d7c1f89e1c25`; events acceleration, braking, left_turn, right_turn, stop; 110 samples; 109 exact to 5 compact segments; readability FAIL.
- Rank 2: `trajectory-369819dca08e6fee`; events acceleration, braking, left_turn, right_turn, stop; 58 samples; 57 exact to 4 compact segments; readability FAIL.
- Rank 3: `trajectory-f83da3381e4f5eda`; events acceleration, braking, left_turn, right_turn, stop; 110 samples; 109 exact to 12 compact segments; readability PASS.

## 6. F1.1 density-gate recommendation

`trajectory-f83da3381e4f5eda` (rank 3).
rank 3 is the first top-three candidate passing every gate; higher-ranked gate failures: [{'rank': 1, 'failures': ['event_labels_too_dense_for_path_length']}, {'rank': 2, 'failures': ['event_labels_too_dense_for_path_length']}]

## 7. Readability-gate results

- Rank 1: FAIL: event_labels_too_dense_for_path_length.
- Rank 2: FAIL: event_labels_too_dense_for_path_length.
- Rank 3: PASS.

The gate evaluates spatial annotation density only. It does not evaluate hero
figure explanatory quality.

## 8. Numeric annotations

- Source samples: 110
- Exact segments: 109
- Compact segments: 5
- Segment reduction: 95.4%
- Hybrid primitives: hold 0,
  linear 1, Hermite
  4
- Maximum position error: 0.078 m
- P95 position error: 0.074 m
- Maximum velocity error: 0.45 m/s
- Events preserved: 5/
  6
- Semantic F1: 0.909

## 9. Traceability for every annotation

- Source sample count: verified canonical source rows.
- Exact and compact segment counts: verified procedural-track and segment records.
- Primitive composition: verified hybrid segment primitive types.
- Position and velocity errors: direct accepted replay API recomputation at canonical source timestamps, checked against frozen trajectory metrics.
- Semantic counts and F1: accepted detector rerun on verified source and hybrid replay, checked against frozen event metrics.

## 10. F1.1 draft layout

A 2 x 2 spatial layout uses identical axis limits, equal aspect ratio, and
shared start/end conventions. Panel C includes one compact source-timestamp
position-error inset against the 0.10 m bound.

## 11. Observed readability or clutter issues

Ranks 1 and 2 exceed the predeclared 0.35-event-per-metre label-density gate.
Human review finds rank 1 substantially stronger as the primary explanatory
figure because its curved maneuver makes the 109-to-5 exact-to-compact
transformation and Hermite vocabulary visible. Rank 3 is spatially clear but
nearly straight, leaving the four panels visually too similar.

## 12. F1.1 refinements carried into F1.2

F1.2 uses DejaVu Sans, adaptive equal-scale viewports, numbered semantic anchors,
an external event timeline, refined line weights, and a stronger footer
hierarchy without changing candidate data, codecs, or accepted evidence.

## 13. F1.1 draft caption

Genuine AV2 example illustrating the progression from canonical source samples to exact procedural replay, compact position-and-velocity-bounded replay, and semantic motion annotations. The compact representation reduces the trajectory from 109 exact segments to 5 segments while maintaining a maximum source-timestamp position error of 0.078 m, a p95 position error of 0.074 m, and a maximum represented-velocity error of 0.45 m/s. 5 of 6 source semantic events are preserved on replay.

## 14. Reproduction command

`uv run --frozen python scripts/generate_figure1_procedural_overview.py`

Exact provider identifiers are available only in the ignored local candidate
mapping artifact.

## 15. Output checksums

- `figures/benchmark/figure1_candidates/candidate_01_preview.png`: `2205dfdf271c0373427aa4e42001bdc0169b5ef6fda395951d9ea556ea240b8f`
- `figures/benchmark/figure1_candidates/candidate_02_preview.png`: `4079a6f324596600570e6c33e0cdb3751e8ede65bbf7ce8ba99bdd024eeadb4d`
- `figures/benchmark/figure1_candidates/candidate_03_preview.png`: `dafdbccb074301892712fc03348d76de973e5f4d882478cf08d371f7279e0bdd`
- `figures/benchmark/figure1_final_candidates/candidate_01_composition.png`: `31f380c17cf19fa9ba88f52be6ae896eefbbcc4b5e450f2190e12899b4fc0e06`
- `figures/benchmark/figure1_final_candidates/candidate_01_composition_grayscale.png`: `34442ae8e15874fdd5a7612168096f430620c6124d2ccec532816619cd89f520`
- `figures/benchmark/figure1_final_candidates/candidate_03_composition.png`: `94ba3816be30d5318bfd40cc8e9f236431921a30d4609edc59658a2b3fda40ff`
- `figures/benchmark/figure1_final_candidates/candidate_03_composition_grayscale.png`: `9ae66ff20ecadd34928cd5164283cef21ee8d2b74d152d2d830ad8cfa648b082`
- `figures/benchmark/figure1_final_candidates/one_column_comparison.png`: `2876eb670f44fea949f755d7b680e7b4cf694fce0a9a358c885d3dc8563c429b`
- `figures/benchmark/figure1_final_candidates/two_column_comparison.png`: `d041051c57023cc25bea07283e96e565da884a5c5ef322b7198077bc1b291b9a`
- `figures/benchmark/figure1_procedural_overview.pdf`: `4368269b679bdd06ce51767cf79c60d257d3bb4dea6bbd7d8f04c38d843d5009`
- `figures/benchmark/figure1_procedural_overview.png`: `31f380c17cf19fa9ba88f52be6ae896eefbbcc4b5e450f2190e12899b4fc0e06`
- `figures/benchmark/figure1_procedural_overview_draft.pdf`: `6c2af385c54dcd2ff04719511bc1b6a73876cfc5385d9da598ed66ad58912994`
- `figures/benchmark/figure1_procedural_overview_draft.png`: `34d1b75a6e62f0745a78d6269ca1afa747f973df80a4c3210660dfa5c5a2764e`
- `figures/benchmark/figure1_procedural_overview_grayscale.png`: `34442ae8e15874fdd5a7612168096f430620c6124d2ccec532816619cd89f520`
- `reports/figure1_procedural_overview_caption.md`: `6df250d5335ad1aa9403d70ae40e04d02642ae59c012614a082484cbfc23b9e7`
- `results/benchmark_figures/figure1_procedural_overview/candidate_03_plotted_values.json`: `5f83f38c409702575694915d8aeff88ba93e9744affd363fe3f433dca8988892`
- `results/benchmark_figures/figure1_procedural_overview/figure_manifest.json`: `edba2649a20d71bfbb1e6760d75882b9f752f8447d895a3d4afca21629007150`
- `results/benchmark_figures/figure1_procedural_overview/final_candidate_comparison.json`: `769b8cede6c0f014ea734e0007ea02552a7639cc5424441ed3ef32a18b694e5b`
- `results/benchmark_figures/figure1_procedural_overview/plotted_values.json`: `ac3636da7ad0ab4d74e4f54621778d8c545d1407cb5193c421a0f7626ad3e73e`
- `results/benchmark_figures/figure1_procedural_overview/selected_candidates.json`: `ddcc037091629c8b7bfb25e7cad182339953d6cbac9f2387ba0c781b00c27f88`
- `results/benchmark_figures/figure1_procedural_overview/selection_contract.json`: `345065c9612ecd4212b22b71b86bdb9add0d0d9b81560d89248472db765544b9`

## 16. F1.2 final-production treatment

The final 2 x 2 layout retains identical within-candidate spatial extents and
equal scale across panels. Panel titles and trajectory marks lead the hierarchy;
concise panel
annotations and a six-item metric footer remain secondary. Linear primitives
use a solid stroke, Hermite primitives use a dashed stroke, breakpoints use open
squares, and source samples remain smaller neutral marks.

## 17. Semantic-label treatment

All five event types remain visible. Compact numbered anchors on the path map to
collision-checked, chronological rows in an external Panel D timeline. No
semantic label is placed directly on the spatial path and no leader-line field
obscures the maneuver.

## 18. Final visual inspection

PASS: full-resolution PNG, vector PDF, grayscale PNG, one-column reduction,
two-column reduction, 100% rendering, and print-like preview were inspected.
No clipping, label overlap, panel-title loss, ambiguous endpoint, oversized
legend, or primitive/event-style ambiguity was observed. Candidate 1 is
recommended over candidate 3 because it better exposes reduction, maneuver
shape, and panel-to-panel transformation while retaining readable semantics.

## 19. Final caption

Genuine AV2 example showing canonical motion samples, exact procedural replay, compact position-and-velocity-bounded replay, and semantic motion annotations. The compact representation reduces 109 exact segments to 5 procedural segments (95.4% fewer) while maintaining maximum and p95 source-timestamp position errors of 0.078 m and 0.074 m, respectively, and a maximum represented-velocity error of 0.447 m/s. 5 of 6 source semantic events are preserved on replay; stop, acceleration, braking, left turn, and right turn remain represented.
