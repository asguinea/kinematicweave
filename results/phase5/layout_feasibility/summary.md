# Batch 5.1 — Blind Layout-Induction Feasibility

## Decision

`feasibility_gate = "completed"`

Phase 5 layout-induction feasibility gate completed on synthetic and 150
genuine AV2 development scenarios with an enforced motion-only construction
boundary.

This is a descriptive feasibility decision. It does not claim that layout
induction has been achieved and it materializes no corridors, centerlines,
junctions, transition graphs, semantic zones, or spatial grammar.

## Frozen cohort and boundary

- Development scenarios analyzed: 150
- Pilot scenario access: 0
- Test scenario access: 0
- Stage A map files opened: 0
- Stage A completed before map access: true
- Stage A remained byte-identical after repeated Stage B execution: true

## Motion-only evidence

- Layout-eligible tracks: 5940
- Excluded tracks retained: 1295
- Tracks with multi-track spatial support: 2694
- Multi-track supported fraction: 0.453535
- Directional/opposing support pairs: 158 / 6
- Potential merges/splits: 349 / 399
- Geometric crossings: 901
- Transition-supported/elevation-separated/ambiguous crossings:
  304 /
  0 /
  597

## Aggregation

Coordinates comparable across scenarios:
`false`.
Recommendation: scenario-local construction; defer multi-scenario geographic aggregation.

## Post-freeze diagnostics

- Motion points near mapped structure: 0.495650
- Mapped local structure supported by motion: 0.325183
- Orientation agreement: 0.564815
- Map-consistent observed transitions: 0.507135

## Claim feasibility

{'deferred': 1, 'feasible now': 2, 'feasible only as partial motion-supported structure': 4, 'feasible only with aggregation': 3, 'unsupported': 2}

Complete local road-network reconstruction and complete symbolic spatial grammar
are unsupported. Physical corridor width and multi-scenario geographic
aggregation remain deferred. Sparse, zero-count, and ambiguous findings remain
in the evidence.

## Determinism

Two isolated full-cohort generations were byte-identical:
`true`.
