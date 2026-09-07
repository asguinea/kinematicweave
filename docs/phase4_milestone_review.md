# Phase 4 Milestone Review — Motion Representation Evidence

## 1. Milestone decision

`milestone_decision = "achieved"`

M4 motion representation evidence achieved on the frozen AV2 development, pilot, and test cohorts.

The requested Batch 4.9 baseline commit is the accepted evidence anchor. The
integration worktree began at its clean descendant
`55354844f71963e1c7ef74108e73f6685ac4399b`, which preserves accepted release and
showcase-media commits. No history was rewritten.

## 2. Research questions

- How do compact procedural representations trade storage for time-indexed position, velocity, and semantic fidelity?
- Does adaptive temporal segmentation improve replay relative to uniform and geometric baselines?
- What dynamics failure is introduced by unconstrained Hermite interpolation?
- What recovery and cost follow from an explicit represented-velocity constraint?
- Are the accepted methods deterministic and feasible on the reference ASUS laptop?

## 3. Cohort and protocol

The official AV2 motion cohort contains 500 scenarios: 150 development, 50
pilot, and 300 test. Membership and identities are frozen. Pilot and test are
not pooled, no outcome-based replacement is permitted, and the 18 accepted
representations use the metric and campaign matrix identities recorded in
`contract_snapshot.json`.

Primary matching targets are 0.48 serialized bytes/raw and 0.12 retained
keyframes/exact. Diagnostic targets are 0.71 and 0.23. Achieved budgets and
mismatches remain explicit; no metric interpolation invents exact equivalence.

## 4. Method taxonomy

References:

- raw samples;
- exact adjacent-sample procedural replay, a correctness reference rather than
  a compression method.

Standard baselines:

- uniform linear;
- uniform Hermite;
- fixed-interval linear;
- RDP linear.

KinematicWeave main methods:

- position-bounded linear;
- position-and-velocity-bounded hybrid.

KinematicWeave ablation:

- unconstrained Hermite.

No universal winner is declared.

## 5. Frozen metrics

Batch 4.3 definitions remain unchanged. Position, represented velocity,
heading, endpoint, gap, runtime, storage, and event-preservation fields retain
their accepted aggregation and quantile policies. Stop, acceleration, braking,
left-turn, and right-turn F1 remain distinct; no new combined turn metric was
introduced.

## 6. Primary matched-budget results

| Group | Method | Bytes/raw | Mismatch | Keyframes | Segments | Position mean/p95/max (m) | Velocity mean/p95/max (m/s) | Semantic F1 | Stop | Turn L/R | Accel. | Brake | Runtime (s) | Violations |
|---|---|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---:|
| standard baselines | uniform linear | 0.4802 | 0.0002 | 0.1230 | 0.1070 | 0.1044/0.4973/4.1641 | 0.1352/0.6030/18.4608 | 0.882 | 0.976 | 0.942/0.959 | 0.821 | 0.758 | 189.2 | n/a |
| standard baselines | uniform Hermite | 0.4806 | 0.0006 | 0.1230 | 0.1070 | 0.0984/0.4788/4.2813 | 0.4722/2.1076/34.1599 | 0.577 | 0.843 | 0.942/0.959 | 0.507 | 0.378 | 174.9 | n/a |
| standard baselines | fixed-interval linear | 0.4806 | 0.0006 | 0.1230 | 0.1070 | 0.1044/0.4973/4.1641 | 0.1352/0.6030/18.4608 | 0.882 | 0.976 | 0.942/0.959 | 0.821 | 0.758 | 169.3 | n/a |
| standard baselines | RDP linear | 0.4419 | 0.0381 | 0.1041 | 0.0878 | 0.2246/0.9455/19.9132 | 0.1940/0.9112/17.1483 | 0.878 | 0.962 | 0.950/0.961 | 0.820 | 0.771 | 169.1 | n/a |
| kinematicweave main methods | position-bounded linear | 0.5074 | 0.0274 | 0.1310 | 0.1152 | 0.0427/0.0918/0.1000 | 0.0814/0.3774/17.1483 | 0.912 | 0.974 | 0.937/0.951 | 0.869 | 0.849 | 431.5 | 0 |
| kinematicweave main methods | position-and-velocity-bounded hybrid | 0.4913 | 0.0113 | 0.1236 | 0.1077 | 0.0319/0.0806/0.1000 | 0.1166/0.5544/0.9999 | 0.886 | 0.927 | 0.930/0.946 | 0.858 | 0.837 | 558.4 | 0 |
| kinematicweave ablation | unconstrained Hermite | 0.4699 | 0.0101 | 0.1148 | 0.0987 | 0.0319/0.0807/0.1000 | 0.2745/1.2986/26.0622 | 0.727 | 0.896 | 0.933/0.943 | 0.658 | 0.553 | 1803.8 | n/a |

The matched-keyframe table uses the same frozen selected configurations and is
available with full precision in `benchmark_tables.json`.

## 7. Confirmatory statistics

The independent unit is the scenario. Accepted procedures use paired
percentile-bootstrap 95% intervals, two-sided paired sign-flip permutation
tests, natural-unit effects, paired Cohen dz, rank-biserial effects, and Holm
correction within the declared family, role, and metric domain. Pilot and test
remain separate.

## 8. Ablation findings

| Contrast | Metric | Method B advantage (signed) | 95% CI | Holm p | Magnitude | Pilot direction | Interpretation |
|---|---|---:|---|---:|---|---|---|
| uniform linear versus uniform Hermite | `velocity_maximum_mps` | -2.2190 m/s | [-2.6457, -1.7885] | 0.0008999 | moderate | yes | At identical stride-10 keyframes, uniform Hermite increased worst-case velocity error; interpolation vocabulary alone did not preserve dynamics. |
| adaptive linear versus uniform linear | `position_mean_m` | -0.0671 m | [-0.0733, -0.0612] | 0.0008999 | large | yes | Adaptive position-bounded breakpoints reduced mean time-indexed position error at comparable, but not identical, storage. |
| temporal bounded replay versus RDP | `velocity_mean_mps` | -0.1328 m/s | [-0.1466, -0.1201] | 0.0003 | large | yes | Temporal position control reduced mean velocity error relative to geometric RDP simplification; a path bound is not a temporal replay guarantee. |
| bounded linear versus unconstrained Hermite | `velocity_maximum_mps` | -5.7747 m/s | [-6.1991, -5.3458] | 0.0003 | large | yes | Adding unconstrained Hermite primitives increased worst-case velocity error despite compact position-oriented fitting. |
| unconstrained Hermite versus hybrid | `velocity_maximum_mps` | 9.2313 m/s | [8.8069, 9.6670] | 0.0003 | large | yes | The explicit velocity constraint substantially reduced worst-case velocity error relative to unconstrained Hermite. |
| bounded linear versus hybrid | `velocity_maximum_mps` | 3.4565 m/s | [3.1218, 3.8071] | 0.0003 | large | yes | The hybrid reduced worst-case velocity error, while the bounded linear method retained stronger overall semantic F1; the methods serve different operating needs. |

The six rows preserve exact test effects, corrected p-values, and pilot
direction checks. Storage, complexity, position, velocity, semantics, runtime,
and hard guarantees remain separate.

## 9. Qualitative findings

Eight deterministic Batch 4.9 figures cover matched storage, adaptive
breakpoints, temporal versus geometric simplification, unconstrained Hermite
dynamics failure, velocity-constraint recovery, semantic preservation, and
difficult or opposite-direction cases. Selection uses frozen-test metric ranks
and safe hashed identifiers, not manual visual cherry-picking.

## 10. Failures and exceptions

The accepted failure gallery and machine-readable failure analysis remain part
of the milestone. City, class, motion-category, and event-type disagreements
are descriptive exceptions, not omitted cases. The milestone adds no new
exclusion, hypothesis, or favorable example.

## 11. Runtime and laptop feasibility

| Cohort | Runtime (s) | Scenarios/s | Configurations/s | Peak RSS | Disk |
|---|---:|---:|---:|---:|---:|
| pilot | 1060.5 | 0.0471 | 0.8487 | 1079054336 | 635115730 |
| test | 6660.0 | 0.0450 | 0.8108 | 3301355520 | 3833222663 |

The campaign was sequential, CPU-only, and measured on the ASUS reference
laptop under WSL2. Generated disk was 4468339143 bytes.
Deterministic pilot and test checkpoints matched with zero mismatches and no
representation recomputation during verification.

## 12. Reproducibility

Every milestone JSON file is deterministic and checksum-linked. All 16 SVG/PNG
Batch 4.9 outputs, accepted release overviews, replay preview and MP4, 36
frame-manifest entries, and the available local frame files were verified.
Provider data, cache Parquet, checkpoints, and frame sequences remain untracked.

## 13. Claim-to-evidence audit

`claim_evidence_matrix.json` maps 10 claims to exact
files, figures, tables, invariant status, evidence class, qualifications, and
unsupported broader interpretations. The audit explicitly rejects a composite
score and universal-winner interpretation.

## 14. Known limitations

Phase 4 does not prove:

- motion-derived spatial layout;
- spatial grammar quality;
- editing or branching;
- rerouting or downstream simulation;
- raw-video extraction;

These items remain deferred rather than silently inferred from motion evidence.

## 15. Phase 5 entry criteria

Phase 5 may begin only through a separately approved batch that preserves this
frozen motion package, keeps motion-derived layout claims unproven at entry,
defines its own spatial evidence contract, and does not reinterpret Phase 4 as
proof of grammar, editing, branching, rerouting, simulation, or video
extraction.

## 16. Reproduction commands

Lightweight evidence verification:

```text
uv sync --frozen
uv run --frozen python scripts/run_phase4_milestone.py --verify-only
uv run --frozen pytest -q tests/test_phase4_milestone.py tests/test_phase4_milestone_integration.py
```

Complete expensive empirical reproduction:

```text
uv run --frozen python scripts/run_motion_evaluation_cohort.py
uv run --frozen python scripts/run_motion_baselines.py
uv run --frozen python scripts/run_motion_metrics.py
uv run --frozen python scripts/run_motion_sweep.py
uv run --frozen python scripts/run_protocol_freeze.py
uv run --frozen python scripts/run_frozen_motion_campaign.py
uv run --frozen python scripts/run_statistical_analysis.py
uv run --frozen python scripts/run_representation_ablations.py
uv run --frozen python scripts/run_qualitative_motion.py
uv run --frozen python scripts/run_phase4_milestone.py
```

The expensive path requires the accepted official provider data and immutable
cache. The lightweight path does not download or rerun provider data.

## 17. Batch and commit ledger

| Batch | Accepted commit |
|---|---|
| 4.1 | `0870b0b6a02c6ab35f8f20ce36e3075b0d23316e` |
| 4.2 | `20c572ca596f083aef21015760e2e1df39e0c7e2` |
| 4.3 | `ffd5f6656a1d4d5d7bedf571dfbd1ca3b8f67d5c` |
| 4.4 | `8c599eabb13a9ebc15661655e4028fe77681de6c` |
| 4.5 | `f90ebae21c7073460e7a5e2f880c8ef39ef8a791` |
| 4.6 | `b521cc64db734c591ec780af32eccc506139178a` |
| 4.7 | `985c59548610f49a881479068c283102bde7aacd` |
| 4.8 | `8367a08af54ae922ef05c590bf36f12ec08e163b` |
| 4.9 | `faea77b73c8fcb52a51781b15cb7893ab7cfc564` |

The Batch 4.10 commit is intentionally absent because a commit cannot embed its
own final identity.
