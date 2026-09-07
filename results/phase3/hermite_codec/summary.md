# Phase 3 Velocity-Aware Cubic Hermite Codec

- Batch: 3.3
- Configuration: fixed development setting at 0.10 m maximum source-timestamp position error
- Candidate-set optimality: minimum segments over deterministic hold, linear, and cubic-Hermite candidates, followed by the declared error and tie-break objectives
- Position is the hard fitting constraint; heading and velocity are measured diagnostics
- Synthetic gate: PASS
- Genuine AV2 provider gate: PASS
- Synthetic trajectories: 27
- Synthetic segments: 39 (linear baseline 55)
- Synthetic Hermite segments: 6
- AV2 scenarios: 10
- AV2 trajectories: 418
- AV2 segments: 2096 (linear baseline 2488)
- AV2 Hermite segments: 969
- AV2 procedural bytes: 526573 (linear baseline 570662)
- AV2 position p95 (m): 0.07948083475762291
- AV2 heading p95 (rad): 0.02440765353812089
- AV2 velocity p95 / maximum (m/s): 1.2977909500786864 / 11.171192960701672
- Equivalent repeat Parquet checksums: matched
- Provider source/cache mutation: none

Whether Hermite improved the genuine provider data is determined by the measured comparisons above; no metric is presumed to improve. The 0.10 m value is a fixed development setting. Scientific tolerance and method comparisons remain Phase 4 work.

M3 velocity-aware Hermite codec evaluated on synthetic and genuine AV2 provider data.
