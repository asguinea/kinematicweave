# Phase 3 Semantic Waypoints and Motion Events

- Batch: 3.5
- Semantic layer: deterministic rule-based source detection over the Batch 3.4 numerical replay layer
- Thresholds: fixed development settings; Phase 4 owns sweeps
- Synthetic gate: PASS; 27 trajectories and all explicit oracles passed
- Genuine AV2 gate: PASS; 10 scenarios and 418 trajectories
- AV2 semantic waypoints/events: 3583/668
- AV2 aggregate source/replay precision/recall/F1: 0.8892215568862275/0.8892215568862275/0.8892215568862275
- AV2 semantic/procedural/combined bytes: 678216/553731/1231947
- AV2 runtime/throughput/peak memory: 125.16864439699998 s / 3.339494503705103 trajectories/s / 12386381 bytes
- AV2 maximum waypoint position/velocity errors: 0.09952153405464238 m / 0.9964772499462721 m/s
- Equivalent repeated semantic Parquet checksums: matched
- Provider source and canonical cache mutation: none

AV2 does not provide complete ground-truth labels for all detected events. Synthetic scenarios provide exact oracle evidence. Source/replay agreement measures preservation, not detector correctness. Phase 4 owns threshold sweeps and broader scientific validation.

M3 semantic motion layer achieved on synthetic and genuine AV2 provider data.
