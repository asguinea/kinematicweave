# Phase 3 Shared Motion Categories and Route Templates

- Batch: 3.6
- Grouping: deterministic, motion-only, scenario-scoped source frame
- Representative policy: observed medoid source path
- Numerical replay authority: Batch 3.4 procedural tracks remain authoritative
- Synthetic gate: PASS; 27 trajectories and all explicit oracles passed
- Genuine AV2 gate: PASS; 10 scenarios and 418 trajectories categorized
- AV2 templates/shared/singleton: 414/4/410
- AV2 shared-template coverage: 0.019138755980861243
- Templates with usable lane signatures: 324
- Shared-motion/procedural/semantic/combined bytes: 596780/553731/678216/1828727
- AV2 total runtime/throughput/peak memory/disk: 108.675179104 s / 3.8463244638408396 trajectories/s / 18342953 bytes / 1193581 bytes
- AV2 maps were used only after templates and memberships were frozen
- Thresholds are fixed development settings; Phase 4 owns sweeps
- Phase 5 owns layout induction from motion bundles

M3 shared motion categories and route templates achieved on synthetic and genuine AV2 provider data.
