# Phase 4 Motion Evaluation Runbook

## Execution boundary

Run the provider workflow from the repository clone stored on the WSL2 Linux
filesystem. Keep provider objects and canonical materialization entries under
the ignored `data/external/av2_motion_phase4/` and
`cache/phase4_motion_cohort/` roots. Do not copy those generated artifacts into
Git-tracked paths.

The frozen cohort uses the official Argoverse 2 Motion Forecasting source. Its
500 scenarios are selected before codec or validation outcomes are inspected:
150 development and 50 pilot scenarios from `train`, plus 300 test scenarios
from `val`.

## Official acquisition and materialization

From the WSL2 repository root, run:

```text
uv sync --frozen
uv run --frozen python scripts/run_motion_evaluation_cohort.py --repository-root . --data-root data/external/av2_motion_phase4 --cache-root cache/phase4_motion_cohort --evidence-root results/phase4/motion_cohort --backend auto
```

The runner lists the official provider catalog, verifies every selected motion
and map object, runs the deterministic 25-scenario resource probe, and then
materializes the complete cohort through the accepted AV2 adapters. Downloads
use bounded streaming and resumable partial handling. Existing objects and
complete immutable cache entries are reused only after checksum verification.

The first complete materialization must contain 500 cache entries and 3,500
declared outputs. The runner then executes the identical plan again; all 500
entries must be reused with no conversion workers and no output checksum
changes.

## Cohort and cache verification

Canonical validation uses bounded readers and the accepted structural and
eligibility rules. Validation records inclusion or exclusion after selection
and cannot mutate the frozen membership.

Verify the tracked evidence and cohort identity with:

```text
uv run --frozen pytest tests/test_phase4_motion_cohort_integration.py -q
```

The evidence gate checks exact role counts, source checksums, cache completion,
second-run reuse, canonical counts, measured resources, file checksums, and Git
hygiene. `results/phase4/motion_cohort/evidence.json` is the checksum index for
the other evidence files.

## Comparable motion baselines

Batch 4.2 consumes only the frozen 150-scenario development membership and the
canonical cache produced by Batch 4.1. From the WSL2 repository clone, run:

```text
uv run --frozen python scripts/run_motion_baselines.py
```

The runner processes one canonical scenario at a time with bounded Parquet
readers. It executes the raw-sample reference, the frozen uniform, RDP, and
fixed-time grids, and the four accepted Phase 3 procedural methods. Every
serialized representation is written twice under the ignored
`cache/phase4_motion_baselines/` root; equivalent canonical Parquet checksums
must agree. Per-scenario checkpoints make an interrupted campaign resumable.

Tracked lightweight evidence is written to
`results/phase4/motion_baselines/`. Verify it with:

```text
uv run --frozen pytest tests/test_phase4_motion_baselines_integration.py -q
```

Generated procedural and raw Parquet files remain ignored and must never be
committed. The baseline campaign does not read map geometry during encoding.
It must not inspect pilot or test outcomes.

These development-grid results establish comparable implementations and exact
serialized-size accounting. Fair-budget matching, rankings, significance,
confidence intervals, event metrics, Pareto analysis, and final method
selection belong to later Phase 4 batches.

## Motion and semantic metrics

Batch 4.3 evaluates every accepted Batch 4.2 method on the same 150 development
scenarios and 7,012 included trajectories:

```text
uv run --frozen python scripts/run_motion_metrics.py
```

Position error is Euclidean x-y when source elevation is absent and x-y-z when
source elevation exists. Heading uses absolute shortest wrapped angular
distance; velocity uses x-y vector distance. First and last samples of every
valid run are evaluated separately. Every invalid source timestamp and one
integer midpoint between adjacent valid runs must replay to no state.

For each finite error vector, the runner sorts ascending and evaluates quantile
`q` at `(n - 1) * q` with linear interpolation; an empty vector has null
quantiles. Sample-micro summaries pool observations. Trajectory-macro summaries
give each trajectory equal weight through its mean. Event micro rates aggregate
matched, replay, and source counts before computing precision, recall, and F1;
event macro rates equally average per-trajectory rates.

The frozen Batch 3.5 detector is run on canonical source trajectories and on
method replay at original source timestamps. Gap, stop, left-turn, right-turn,
acceleration, and braking events use the accepted deterministic matching
policy. This measures source/replay preservation, not detector accuracy.

Per-scenario checkpoints and the four derived Parquet result tables are under
the ignored `cache/phase4_motion_metrics/` root. They are immutable, canonically
sorted, schema-versioned, bounded on read, and verified by exact size and
SHA-256. Lightweight evidence is tracked under
`results/phase4/motion_metrics/`.

The runner audits counts, representation complexity, serialized bytes, and
pooled errors against unchanged Batch 4.2 evidence. Verify the complete
development result with:

```text
uv run --frozen pytest tests/test_phase4_motion_metrics_integration.py -q
```

Batch 4.3 must not access pilot or frozen-test outcomes and does not rank,
select, significance-test, Pareto-filter, or match budgets across methods.

## Matched-budget exploratory sweep

Batch 4.4 validates the resumable sweep engine on exactly development ranks
1-25. The runner verifies the complete cohort manifest by checksum but uses a
bounded JSON-prefix reader that stops after the 25th development unit, so pilot
and test metadata and outcomes are not decoded.

Run the frozen exploratory grid from the WSL2 Linux-filesystem clone:

```text
uv run --frozen python scripts/run_motion_sweep.py
```

The 39 points are fixed before execution: raw and exact references; uniform
linear and Hermite strides 2, 3, 5, 8, and 10; fixed intervals 200, 300, 500,
750, and 1,000 ms; RDP tolerances 0.05, 0.10, 0.25, 0.50, and 1.00 m;
position-bounded and unconstrained-Hermite tolerances 0.025, 0.05, 0.10, and
0.20 m; and all nine combinations of position bounds 0.05, 0.10, and 0.20 m
with velocity bounds 0.50, 1.00, and 2.00 m/s.

Each scenario/configuration checkpoint is canonical, atomic, and immutable
after completion. A failed checkpoint remains explicit and may be retried only
with an increased attempt count. On resume, representation artifacts are
verified by exact size and SHA-256 before a completed checkpoint is reused.
The runner performs a second complete pass and requires all 975 checkpoints to
be reused without representation recomputation and with the same deterministic
checkpoint-output hash.

Actual serialized bytes are divided by raw canonical bytes. Retained keyframes
are divided by valid source samples, and procedural segments are divided by
exact adjacent-sample segments. Byte targets are 0.25, 0.35, 0.50, 0.75, and
1.00; keyframe targets are 0.05, 0.10, 0.20, 0.40, and 0.80. For each method
family and target, selection uses only that budget value: choose the largest
value at or below the target, otherwise the smallest value above it, then
tie-break by canonical parameter identity. Metrics are never interpolated.

Generated representations, checkpoints, and raw Parquet results are stored
under ignored `cache/phase4_motion_sweep/`. Tracked exploratory evidence is
under `results/phase4/motion_sweep/` and is verified with:

```text
uv run --frozen pytest tests/test_phase4_motion_sweep_integration.py -q
```

This sweep supports parameter-range exploration only. It does not establish
exact budget equivalence, rank methods, or select a release configuration.
Batch 4.5 owns the protocol audit and final campaign-grid freeze.

## Frozen pilot and test protocol

Batch 4.5 audits Batches 4.1-4.4 using development data only. Run the audit
from the WSL2 Linux-filesystem clone after the accepted Batch 4.3 metric
checkpoints and Batch 4.4 evidence have been verified:

```text
uv run --frozen python scripts/run_protocol_freeze.py
```

The accepted all-development curves already provide useful overlap. Therefore
the supplemental grid contains zero points: no extra configuration is needed
to cover the primary byte target 0.48 within mismatch 0.05 or the primary
keyframe target 0.12 within mismatch 0.02 across all seven comparison families.
This empty grid is frozen and machine-readable; adding a point without a new
versioned coverage defect would violate the protocol.

The complete 18-configuration Batch 4.3 matrix is retained for pilot and test.
Raw samples and exact adjacent replay are reference configurations. The
stride-10, one-second interval, 0.05 m RDP, and accepted procedural
configurations supply the primary matched comparisons. Higher-fidelity temporal
points and lower-budget RDP points remain diagnostic curve brackets and
interpolation or compactness ablations. No configuration is removed because of
its development accuracy or semantic result.

Diagnostic temporal-only comparisons use byte ratio 0.71 and keyframe ratio
0.23, each with mismatch at most 0.01. All matching uses the relevant achieved
budget only, applies the frozen nearest-under-else-smallest-above rule, and
tie-breaks by canonical configuration identity. Metrics are not interpolated,
and raw or exact references are never described as budget matched.

The final matrix processes methods in declared order and scenarios in frozen
cohort-rank order with one worker. Each scenario/configuration checkpoint is
atomic, immutable after completion, verified before reuse, and retained when
failed. A repeated pass must verify all 900 pilot or 5,400 test checkpoints
without representation recomputation and reproduce the checkpoint identity.

Motion analysis retains trajectory-level raw records and uses scenario-aware
aggregation for paired inference. Semantic results report micro event counts
and scenario-aware macro summaries. Failures, exclusions, undefined values, and
extreme valid observations remain explicit. Runtime is reported by workload
scale rather than pooled.

The measured development matrix projects below three hours for pilot and below
twelve hours for test under sequential execution. Before either campaign, the
runner must preserve 15 percent free disk, use one worker, warn above 22 GiB
RSS, target below 24 GiB, and stop before expected use exceeds 26 GiB.

Pilot execution may confirm runtime, memory, storage, failures, confidence
interval implementation, and figure feasibility. It may stop for an external
resource or implementation defect, but it cannot select a method or change any
configuration, budget, metric, threshold, aggregation, exclusion, or test
matrix. Pilot and test execution require separately approved later batches.

Verify the committed freeze evidence with:

```text
uv run --frozen pytest tests/test_phase4_protocol_freeze_integration.py -q
```

## Frozen pilot and test campaign

Batch 4.6 executes the frozen 18-configuration matrix first on all 50 pilot
scenarios and then, only after an immutable pilot completion gate passes, on
all 300 test scenarios:

```text
uv run --frozen python scripts/run_frozen_motion_campaign.py
```

The command verifies the accepted Batch 4.1 through 4.5 evidence, frozen
matrix identity, official source objects, canonical cache entries, and
source/cache snapshot before processing. It uses one worker and bounded
sequential scenario processing. Pilot execution must account for all 900
scenario/configuration units without a scientific protocol change. The
immutable `pilot_completion.json` gate is written before test outcomes are
read. Test execution must account for all 5,400 units without using outcomes
to alter the campaign.

Scenario/configuration checkpoints, generated representations, and raw
Parquet tables are stored under the ignored
`cache/phase4_frozen_campaign/` root. Completed checkpoints are immutable.
Rerunning the command resumes from verified checkpoints; corrupt or conflicting
completed artifacts stop the campaign. The final verification pass checks all
6,300 checkpoints without recomputing representations, reproduces deterministic
checkpoint identities, and confirms unchanged source and cache checksums.

The runner applies the frozen matching rule independently to pilot and test
aggregates. Primary comparisons target byte ratio 0.48 with mismatch at most
0.05 and keyframe ratio 0.12 with mismatch at most 0.02. Diagnostic temporal
comparisons target byte ratio 0.71 and keyframe ratio 0.23 with mismatch at
most 0.01. Selection uses only the achieved budget for the relevant dimension;
it does not inspect quality, interpolate metrics, or replace a family.

Tracked lightweight evidence is written to
`results/phase4/frozen_campaign/`. Verify evidence checksums, exact membership
and accounting, scientific invariants, raw artifact descriptors, the pilot
gate, and generated-data Git hygiene with:

```text
uv run --frozen pytest tests/test_phase4_frozen_campaign_integration.py -q
```

The evidence is descriptive. Do not draw inferential conclusions, rank a final
method, or select a method before the separately approved Batch 4.7 statistical
analysis.

## Confirmatory statistical analysis

Batch 4.7 consumes the immutable Batch 4.6 pilot and test result tables without
re-encoding, rematching budgets, or changing the frozen protocol:

```text
uv run --frozen python scripts/run_statistical_analysis.py
```

The scenario is the independent statistical unit. Trajectory, sample, and event
records remain nested observations. The 300-scenario test cohort supplies the
confirmatory analysis, while the 50-scenario pilot is reported separately as
secondary replication evidence. Development outcomes are not read by the
statistical runner, and pilot and test observations are never pooled.

Primary families compare every pair among the seven frozen methods at byte
ratio 0.48 and keyframe ratio 0.12. Diagnostic temporal families compare every
pair among the three frozen temporal methods at byte ratio 0.71 and keyframe
ratio 0.23. Effects are oriented so positive values favor method B. The runner
reports natural-unit paired effects, 95 percent paired percentile-bootstrap
intervals, two-sided paired sign-flip tests, paired standardized effects,
signed-rank biserial correlations, win/tie/loss counts, practical-magnitude
labels, and raw and Holm-adjusted p-values.

Undefined event metrics remain explicit. Main event-type F1 summaries include
only scenarios where the source event exists; a separate presence-aware
sensitivity view assigns one when source and replay are both absent and zero
for source-absent false positives. Robustness summaries by city, agent class,
motion category, and trajectory-versus-scenario aggregation are descriptive
only.

The generated scenario aggregate Parquet table is stored under ignored
`cache/phase4_statistical_analysis/`. The required lightweight evidence is
tracked under `results/phase4/statistical_analysis/`. Verify the statistical
contract, complete paired families, confidence intervals, multiplicity,
missingness, determinism, checksums, and Git hygiene with:

```text
uv run --frozen pytest tests/test_phase4_statistical_analysis_integration.py -q
```

Batch 4.7 does not claim a universal winner and does not select a final method.

## Representation ablations and contribution attribution

Batch 4.8 reuses the accepted Batch 4.6 scenario results and Batch 4.7
scenario-level aggregates without executing codecs, changing metrics, rematching
budgets, or creating configurations:

```text
uv run --frozen python scripts/run_representation_ablations.py
```

The scenario remains the inferential unit. Test results are confirmatory and
pilot results remain separate replication evidence. Every contrast reports
paired natural-unit effects, 95 percent paired percentile-bootstrap intervals,
two-sided sign-flip tests, Cohen dz, rank-biserial effects, win/tie/loss counts,
and Holm-adjusted p-values within the declared ablation family, cohort role, and
metric domain.

The frozen contrasts isolate linear versus Hermite interpolation at identical
stride-2, stride-5, and stride-10 keyframes; adaptive versus uniform or fixed
segmentation in both primary budget contexts; temporal position control versus
RDP path simplification; Hermite primitive availability; explicit velocity
control; and the complete bounded-linear versus hybrid tradeoff. Actual achieved
byte, keyframe, and segment mismatches remain attached to every record. They are
not replaced by invented exact budget equivalence.

Raw samples and exact adjacent-sample procedural replay are decomposed only as
descriptive correctness and storage references. Exact proceduralization is not
called compression. Physical file evidence separates source samples, tracks,
segments, manifests, and codec metadata where possible; Parquet framing remains
part of each measured file because the accepted artifacts do not expose it as a
separate payload.

Interpret contribution labels precisely:

- `significant_result` is supported by the corrected paired test;
- `descriptive_tendency` reports direction without a corrected significance claim;
- `invariant_guarantee` is reserved for exact bound, gap, or replay properties.

City, agent-class, motion-category, and event-type breakdowns are descriptive
and retain disagreements with the overall direction. Storage, complexity,
position, velocity, semantics, runtime, and worst-case guarantees remain
separate. No composite score or universal winner is permitted.

Tracked evidence is under `results/phase4/ablation_analysis/`. Verify exact
configuration identities, identical-keyframe contrasts, budget mismatches,
paired statistics, attribution labels, robustness exceptions, checksums, and
generated-data Git hygiene with:

```text
uv run --frozen pytest tests/test_phase4_ablation_analysis_integration.py -q
```

## Deterministic qualitative motion evidence

Batch 4.9 consumes only the accepted Batch 4.6 test outputs and Batch 4.7-4.8
analysis artifacts:

```text
uv run --frozen python scripts/run_qualitative_motion.py
```

The selection contract is fixed before rendering. It ranks paired scenario or
trajectory metrics with safe hashed identities as the final tie break. It
includes largest, median, and unfavorable effects; stop, turn, acceleration,
braking, and overlapping events; qualifying vehicle, pedestrian, and cyclist
tracks; and two accepted opposite-direction robustness strata. Exact provider
identifiers are written only to the ignored
`cache/phase4_qualitative_motion/private_selection.json` reproduction map.

Release SVG and PNG figures, manifests, failure analysis, and lightweight
evidence are generated under `results/phase4/qualitative_motion/`. Numbered
additional replay frames remain ignored under
`cache/phase4_qualitative_motion/replay_frames/`; their ordered checksums,
assembly command, rendering configuration, and a representative preview are
tracked.

Reproduce the figures and replay sequence by rerunning the command above.
Verify deterministic ranking, method/event/class coverage, unfavorable cases,
figure labels and checksums, safe identifiers, replay ordering, and evidence
checksums with:

```text
uv run --frozen pytest tests/test_phase4_qualitative_motion_integration.py -q
```

The qualitative layer explains frozen outcomes. It does not rerun codecs,
redetect events, rematch budgets, change metrics, or create new inferential
claims.

## Later campaign roles

Later Phase 4 campaign runners must consume
`results/phase4/motion_cohort/cohort_manifest.json` without reranking or
replacement. They must require one explicit role:

- `development` for implementation debugging and parameter-grid design;
- `pilot` for final protocol confirmation;
- `test` only for the approved frozen campaign.

Batch 4.1 does not implement those scientific campaign commands. A later batch
must expose and validate each command before it is documented as executable.
Until then, the integration-test command above is the supported way to verify
all three frozen role assignments.

## Phase 4 milestone package

Milestone M4 integrates the accepted Batch 4.1 through 4.9 evidence without
rerunning or modifying experiments. The tracked package is under
`results/phase4/milestone/`, with the review at
`phase4_milestone_review.md`.

Use the lightweight path to verify tracked evidence, tables, figures, replay
metadata, claims, reproduction commands, and checksums without provider data:

```text
uv sync --frozen
uv run --frozen python scripts/run_phase4_milestone.py --verify-only
uv run --frozen pytest -q tests/test_phase4_milestone.py tests/test_phase4_milestone_integration.py
```

The complete expensive provider-backed reproduction sequence is:

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

Run the expensive path sequentially in the established WSL2 Linux repository
with the accepted official-provider data and immutable cache. It must not
rerank the cohort, change the protocol, pool pilot and test, or track generated
Parquet, checkpoints, provider data, or replay frames.

## Membership prohibition

Never replace a cohort scenario because it is difficult, excluded, slow,
inconvenient, or produces an unfavorable Phase 4 result. Source or validation
failures remain attached to the original frozen unit. Any separately approved
dataset-policy change must create a new versioned cohort identity and preserve
this evidence.
