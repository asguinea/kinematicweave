# Phase 3 Position-and-Velocity-Bounded Codec

- Batch: 3.4
- Hard development bounds: 0.10 m source-timestamp position and 1.00 m/s represented x/y velocity-vector error
- Candidate-set optimality: exact trajectory-local dynamic programming over deterministic hold, linear, and cubic-Hermite candidates
- Synthetic gate: PASS; 27 trajectories, 39 segments, 360948 bytes
- Genuine AV2 gate: PASS; 10 scenarios, 418 trajectories, 2327 segments, 553731 bytes
- AV2 primitive segments (hold/linear/Hermite): 0/1738/589
- AV2 maximum position/velocity error: 0.09989488370623803 m / 0.9995655552801491 m/s
- Synthetic maximum position/velocity error: 0.07606517589277238 m / 0.8 m/s
- Segment comparison exact/linear/unconstrained-Hermite/dual-bound: 22550/2488/2096/2327
- Byte comparison exact/linear/unconstrained-Hermite/dual-bound: 2819141/570662/526573/553731
- Batch 3.2 velocity p95/max: 0.429269/6.28142 m/s; total runtime 105.887 s
- Batch 3.3 velocity p95/max: 1.2977909500786864/11.171192960701672 m/s; total runtime 705.767876143 s
- Dual-bound encoding runtime: 88.08307814000003 s; speedup 7.8445022409386x
- Equivalent repeat procedural Parquet checksums: matched
- Provider source and canonical cache mutation: none

The velocity guarantee may require more segments and bytes than the linear-only or unconstrained-Hermite codecs; the measured comparison above reports that tradeoff directly. Both bounds are development settings. Phase 4 owns threshold sweeps and scientific method selection.

M3 position-and-velocity-bounded codec evaluated on synthetic and genuine AV2 provider data.
