# Phase 4 Milestone — Motion Representation Evidence

## Milestone decision

`milestone_decision = "achieved"`

M4 motion representation evidence achieved on the frozen AV2 development, pilot, and test cohorts.

The decision covers the frozen 500-scenario AV2 cohort, all 18 accepted
representations, scenario-level inference, accepted qualitative selection, and
the measured CPU-only ASUS WSL2 campaign. It does not select a universal winner.

## Frozen contract

- Cohort: 150 development, 50 pilot, and 300 test scenarios.
- Primary targets: 0.48 serialized-byte ratio and 0.12 keyframe ratio.
- Diagnostic targets: 0.71 serialized-byte ratio and 0.23 keyframe ratio.
- Statistical unit: scenario; pilot and test remain separate.
- Exact adjacent-sample replay is a correctness reference, not compression.
- Batch ledger: 9 accepted commits from 4.1 through 4.9.

## Primary matched-byte test comparison

| Group | Method | Bytes/raw | Mismatch | Keyframes | Segments | Position mean/p95/max (m) | Velocity mean/p95/max (m/s) | Semantic F1 | Stop | Turn L/R | Accel. | Brake | Runtime (s) | Violations |
|---|---|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---:|
| standard baselines | uniform linear | 0.4802 | 0.0002 | 0.1230 | 0.1070 | 0.1044/0.4973/4.1641 | 0.1352/0.6030/18.4608 | 0.882 | 0.976 | 0.942/0.959 | 0.821 | 0.758 | 189.2 | n/a |
| standard baselines | uniform Hermite | 0.4806 | 0.0006 | 0.1230 | 0.1070 | 0.0984/0.4788/4.2813 | 0.4722/2.1076/34.1599 | 0.577 | 0.843 | 0.942/0.959 | 0.507 | 0.378 | 174.9 | n/a |
| standard baselines | fixed-interval linear | 0.4806 | 0.0006 | 0.1230 | 0.1070 | 0.1044/0.4973/4.1641 | 0.1352/0.6030/18.4608 | 0.882 | 0.976 | 0.942/0.959 | 0.821 | 0.758 | 169.3 | n/a |
| standard baselines | RDP linear | 0.4419 | 0.0381 | 0.1041 | 0.0878 | 0.2246/0.9455/19.9132 | 0.1940/0.9112/17.1483 | 0.878 | 0.962 | 0.950/0.961 | 0.820 | 0.771 | 169.1 | n/a |
| kinematicweave main methods | position-bounded linear | 0.5074 | 0.0274 | 0.1310 | 0.1152 | 0.0427/0.0918/0.1000 | 0.0814/0.3774/17.1483 | 0.912 | 0.974 | 0.937/0.951 | 0.869 | 0.849 | 431.5 | 0 |
| kinematicweave main methods | position-and-velocity-bounded hybrid | 0.4913 | 0.0113 | 0.1236 | 0.1077 | 0.0319/0.0806/0.1000 | 0.1166/0.5544/0.9999 | 0.886 | 0.927 | 0.930/0.946 | 0.858 | 0.837 | 558.4 | 0 |
| kinematicweave ablation | unconstrained Hermite | 0.4699 | 0.0101 | 0.1148 | 0.0987 | 0.0319/0.0807/0.1000 | 0.2745/1.2986/26.0622 | 0.727 | 0.896 | 0.933/0.943 | 0.658 | 0.553 | 1803.8 | n/a |

## Primary matched-keyframe test comparison

| Group | Method | Bytes/raw | Mismatch | Keyframes | Segments | Position mean/p95/max (m) | Velocity mean/p95/max (m/s) | Semantic F1 | Stop | Turn L/R | Accel. | Brake | Runtime (s) | Violations |
|---|---|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---:|
| standard baselines | uniform linear | 0.4802 | 0.0030 | 0.1230 | 0.1070 | 0.1044/0.4973/4.1641 | 0.1352/0.6030/18.4608 | 0.882 | 0.976 | 0.942/0.959 | 0.821 | 0.758 | 189.2 | n/a |
| standard baselines | uniform Hermite | 0.4806 | 0.0030 | 0.1230 | 0.1070 | 0.0984/0.4788/4.2813 | 0.4722/2.1076/34.1599 | 0.577 | 0.843 | 0.942/0.959 | 0.507 | 0.378 | 174.9 | n/a |
| standard baselines | fixed-interval linear | 0.4806 | 0.0030 | 0.1230 | 0.1070 | 0.1044/0.4973/4.1641 | 0.1352/0.6030/18.4608 | 0.882 | 0.976 | 0.942/0.959 | 0.821 | 0.758 | 169.3 | n/a |
| standard baselines | RDP linear | 0.4419 | 0.0159 | 0.1041 | 0.0878 | 0.2246/0.9455/19.9132 | 0.1940/0.9112/17.1483 | 0.878 | 0.962 | 0.950/0.961 | 0.820 | 0.771 | 169.1 | n/a |
| kinematicweave main methods | position-bounded linear | 0.5074 | 0.0110 | 0.1310 | 0.1152 | 0.0427/0.0918/0.1000 | 0.0814/0.3774/17.1483 | 0.912 | 0.974 | 0.937/0.951 | 0.869 | 0.849 | 431.5 | 0 |
| kinematicweave main methods | position-and-velocity-bounded hybrid | 0.4913 | 0.0036 | 0.1236 | 0.1077 | 0.0319/0.0806/0.1000 | 0.1166/0.5544/0.9999 | 0.886 | 0.927 | 0.930/0.946 | 0.858 | 0.837 | 558.4 | 0 |
| kinematicweave ablation | unconstrained Hermite | 0.4699 | 0.0052 | 0.1148 | 0.0987 | 0.0319/0.0807/0.1000 | 0.2745/1.2986/26.0622 | 0.727 | 0.896 | 0.933/0.943 | 0.658 | 0.553 | 1803.8 | n/a |

## Principal ablations

| Contrast | Metric | Method B advantage (signed) | 95% CI | Holm p | Magnitude | Pilot direction | Interpretation |
|---|---|---:|---|---:|---|---|---|
| uniform linear versus uniform Hermite | `velocity_maximum_mps` | -2.2190 m/s | [-2.6457, -1.7885] | 0.0008999 | moderate | yes | At identical stride-10 keyframes, uniform Hermite increased worst-case velocity error; interpolation vocabulary alone did not preserve dynamics. |
| adaptive linear versus uniform linear | `position_mean_m` | -0.0671 m | [-0.0733, -0.0612] | 0.0008999 | large | yes | Adaptive position-bounded breakpoints reduced mean time-indexed position error at comparable, but not identical, storage. |
| temporal bounded replay versus RDP | `velocity_mean_mps` | -0.1328 m/s | [-0.1466, -0.1201] | 0.0003 | large | yes | Temporal position control reduced mean velocity error relative to geometric RDP simplification; a path bound is not a temporal replay guarantee. |
| bounded linear versus unconstrained Hermite | `velocity_maximum_mps` | -5.7747 m/s | [-6.1991, -5.3458] | 0.0003 | large | yes | Adding unconstrained Hermite primitives increased worst-case velocity error despite compact position-oriented fitting. |
| unconstrained Hermite versus hybrid | `velocity_maximum_mps` | 9.2313 m/s | [8.8069, 9.6670] | 0.0003 | large | yes | The explicit velocity constraint substantially reduced worst-case velocity error relative to unconstrained Hermite. |
| bounded linear versus hybrid | `velocity_maximum_mps` | 3.4565 m/s | [3.1218, 3.8071] | 0.0003 | large | yes | The hybrid reduced worst-case velocity error, while the bounded linear method retained stronger overall semantic F1; the methods serve different operating needs. |

Positive effects favor method B according to each record's lower-is-better
orientation. Full precision, method identities, corrected p-values, and source
selectors are retained in `benchmark_tables.json`.

## Representation resources

| Cohort | Runtime (s) | Scenarios/s | Configurations/s | Peak RSS | Disk |
|---|---:|---:|---:|---:|---:|
| pilot | 1060.5 | 0.0471 | 0.8487 | 1079054336 | 635115730 |
| test | 6660.0 | 0.0450 | 0.8108 | 3301355520 | 3833222663 |

Generated campaign disk was 4468339143 bytes. Raw and
exact storage, exact procedural serialization overhead, checkpoint identities,
and full resource precision are retained in `benchmark_tables.json`.

## Figures and replay

All 8 accepted Batch 4.9 figures retain
verified SVG and PNG outputs. Accepted release Figure 1 and Figure 2 are
also verified. The replay manifest retains 36 ordered 1280 x 720 frames at
12 fps, their checksum identity, assembly command, preview, and small MP4.

## Claims and limits

The claim-to-evidence matrix contains 10 qualified claims.
Phase 4 does not prove: motion-derived spatial layout, spatial grammar quality, editing or branching, rerouting or downstream simulation, raw-video extraction.

## Reproduction

Lightweight verification:

```text
uv sync --frozen
uv run --frozen python scripts/run_phase4_milestone.py --verify-only
uv run --frozen pytest -q tests/test_phase4_milestone.py tests/test_phase4_milestone_integration.py
```

The full provider-backed command sequence is recorded separately in
`reproduction_manifest.json` and the Phase 4 runbook.

## Evidence

Machine-readable contract, results, tables, figure manifest, claim matrix,
reproduction manifest, and checksums are colocated in this directory. Prior
Phase 4 evidence remains unchanged.
