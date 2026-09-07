# Phase 3 Error-Bounded Piecewise-Linear Codec

- Batch: 3.2
- Configuration: fixed development setting at 0.10 m maximum source-timestamp position error
- Optimality: minimum segment count for the fixed setting
- Synthetic gate: PASS
- Genuine AV2 provider gate: PASS
- Synthetic trajectories: 27
- Synthetic segments: 55 (reduction 208)
- AV2 scenarios: 10
- AV2 trajectories: 418
- AV2 segments: 2488 (reduction 20062)
- AV2 procedural bytes: 570662 (reduction 2248479)
- Equivalent repeat Parquet checksums: matched
- Provider source/cache mutation: none

The 0.10 m value is a fixed development setting, not a release threshold selection. Scientific comparisons across codecs and tolerances remain Phase 4 work.

M3 compact linear codec achieved on synthetic and genuine AV2 provider data.
