# Decision Log

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.14 — Risk Register and Decision Governance  
**Status:** Active template

## 1. Purpose

This file records approved decisions that affect:

- research claims;
- scope;
- architecture;
- data contracts;
- datasets and splits;
- metrics;
- experiments;
- statistics;
- resource limits;
- reproducibility;
- implementation conventions.

Decisions must be recorded here rather than existing only in chat, code, or commit history.

## 2. Decision rules

A decision entry is required when a change:

- adds or removes a primary claim;
- changes the primary dataset or test split;
- changes a primary metric;
- changes the experimental unit;
- changes the architecture or module boundaries;
- changes a persistent schema;
- changes the serialization or size-accounting policy;
- adds a mandatory heavyweight dependency;
- introduces C++, CUDA, or external compute;
- changes the reference resource budget;
- changes the frozen experiment or analysis plan;
- narrows or expands scope.

Minor implementation details that conform to existing definitions do not require a decision entry.

## 3. Status values

- **Proposed** — awaiting decision;
- **Accepted** — approved and authoritative;
- **Rejected** — considered but not adopted;
- **Superseded** — replaced by a later decision;
- **Deferred** — postponed until a stated condition;
- **Reversed** — explicitly undone by a later decision.

## 4. Decision entry template

```text
## D-XXX — Decision title

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Rejected | Superseded | Deferred | Reversed
**Owner:** Research | Data | Evaluation | Engineering | Runtime | Resource | Reproducibility | Reporting

### Context

Describe the problem, constraint, or ambiguity.

### Options considered

1. Option A
2. Option B
3. Option C

### Decision

State the approved choice clearly.

### Rationale

Explain why this option was selected.

### Consequences

List positive and negative consequences.

### Affected definitions

- `definitions/...`

### Affected implementation or experiments

- module, phase, batch, experiment, or artifact

### Risks introduced or mitigated

- `R-...`

### Revisit when

State a measurable condition that would justify reconsideration.

### Supersedes

Write `None` or list earlier decision identifiers.
```

## 5. Initial accepted decisions

## D-001 — Python-first implementation

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Engineering

### Context

The project needs fast research iteration, broad scientific-library support, and compatibility with the ASUS laptop.

### Options considered

1. C++-first implementation;
2. Python-first implementation;
3. mixed Python/C++ from the start.

### Decision

Use a Python-first implementation. Introduce C++ only after profiling identifies a material bottleneck.

### Rationale

Most heavy operations are already provided by compiled libraries, while Python reduces development and experiment-management cost.

### Consequences

- faster iteration;
- easier testing and analysis;
- possible later native optimization;
- production-style runtime performance is not assumed initially.

### Affected definitions

- `definitions/03_system_architecture.md`
- `definitions/14_coding_and_repository_conventions.md`

### Affected implementation or experiments

All implementation phases.

### Risks introduced or mitigated

- mitigates `R-024`;
- may affect runtime-scale targets if Python bottlenecks remain.

### Revisit when

End-to-end profiling shows a Python-owned kernel materially blocks an approved milestone.

### Supersedes

None.

---

## D-002 — Trajectory-first primary evaluation

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Research

### Context

An end-to-end video pipeline would mix representation quality with detection, tracking, depth, and calibration errors.

### Options considered

1. video-first evaluation;
2. trajectory-first evaluation;
3. equal emphasis on both.

### Decision

Use canonical trajectories and vector maps for the primary quantitative evaluation. Keep video-to-tape processing optional.

### Rationale

This isolates the central representation and layout-inference claims and fits the laptop resource envelope.

### Consequences

- stronger causal interpretation of representation results;
- no primary claim of robust raw-video reconstruction;
- optional perception work remains Phase 11.

### Affected definitions

- `definitions/00_project_charter.md`
- `definitions/02_scope_and_non_goals.md`
- `definitions/09_dataset_and_split_policy.md`

### Affected implementation or experiments

Phases 2–10; Phase 11 remains optional.

### Risks introduced or mitigated

- mitigates `R-023`;
- reinforces claim-boundary risk `R-006`.

### Revisit when

The core evidence package is frozen and optional demonstration resources remain.

### Supersedes

None.

---

## D-003 — 2.5D primary evidence boundary

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Research

### Context

The concept is named KinematicWeave, but the planned trajectory and map evaluation is primarily locally planar.

### Options considered

1. require full volumetric 3D evaluation;
2. use 2.5D primary evaluation with explicit claim limits;
3. remove all use of the KinematicWeave name.

### Decision

Use 2.5D urban motion and layout as the primary empirical domain while keeping the representation extensible to 3D.

### Rationale

This is scientifically honest, feasible on the laptop, and aligned with available trajectory/map data.

### Consequences

- primary metrics operate on the x-y plane unless stated otherwise;
- elevation may be retained and used for grade separation;
- reports must not imply arbitrary full-3D reconstruction.

### Affected definitions

- `definitions/02_scope_and_non_goals.md`
- `definitions/coordinate_and_time_conventions.md`
- `definitions/07_evaluation_protocol.md`

### Affected implementation or experiments

Motion, layout, visualization, and reporting.

### Risks introduced or mitigated

Mitigates `R-006`.

### Revisit when

A later dataset and experiment explicitly support full-3D motion and topology.

### Supersedes

None.

---

## D-004 — Argoverse 2 Motion Forecasting as primary planned dataset

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Data

### Context

The project requires trajectories and associated vector maps without full sensor-scale downloads.

### Options considered

1. Argoverse 2 Motion Forecasting;
2. full autonomous-driving sensor datasets;
3. synthetic-only evaluation;
4. multiple datasets from the beginning.

### Decision

Use Argoverse 2 Motion Forecasting and associated vector maps as the primary planned dataset, subject to acquisition and pilot validation.

### Rationale

It supports trajectory and map evaluation at manageable storage and compute scale.

### Consequences

- adapter development targets AV2 first;
- final class coverage depends on eligible data;
- a secondary dataset remains optional.

### Affected definitions

- `definitions/09_dataset_and_split_policy.md`
- `definitions/08_experiment_matrix.md`

### Affected implementation or experiments

Phase 2 and all real-data experiments.

### Risks introduced or mitigated

- relates to `R-001`, `R-011`, and `R-012`.

### Revisit when

Pilot validation shows the dataset cannot support a primary claim adequately.

### Supersedes

None.

---

## D-005 — Ground-truth maps withheld from trajectory-only inference

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Evaluation

### Context

Ground-truth maps are needed for evaluation and oracle conversion but would invalidate trajectory-only inference if used as input.

### Options considered

1. allow map priors;
2. use maps only for scoring and oracle conversion;
3. omit maps entirely.

### Decision

Withhold ground-truth map geometry and topology from trajectory-only inference. Use them only for evaluation-region definition, scoring, and oracle grammar conversion.

### Rationale

This preserves the validity of the motion-derived layout claim.

### Consequences

- strict data-flow separation is required;
- oracle results must be labeled separately;
- accidental map leakage invalidates a run.

### Affected definitions

- `definitions/09_dataset_and_split_policy.md`
- `definitions/07_evaluation_protocol.md`

### Affected implementation or experiments

Phases 5 and 6.

### Risks introduced or mitigated

Mitigates `R-007` and protects against invalid inference claims.

### Revisit when

A separately defined map-assisted experiment is added as secondary work.

### Supersedes

None.

---

## D-006 — Canonical serialized size as primary compactness measure

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Evaluation

### Context

In-memory object size and selectively omitted metadata would make codec comparisons unfair.

### Options considered

1. Python object memory;
2. raw array payload only;
3. canonical reconstructible serialized payload;
4. compressed file size only.

### Decision

Use canonical reconstructible serialized payload bytes as the primary compactness measure.

### Rationale

It includes the information a decoder actually needs and supports fair matched-budget evaluation.

### Consequences

- timestamps, events, interpolation metadata, and required identifiers count;
- container compression is secondary;
- shared overhead must be handled consistently.

### Affected definitions

- `definitions/06_serialization_specification.md`
- `definitions/07_evaluation_protocol.md`

### Affected implementation or experiments

Phases 3 and 4.

### Risks introduced or mitigated

Mitigates `R-013`.

### Revisit when

A release artifact requires a different secondary storage comparison.

### Supersedes

None.

---

## D-007 — Geometry and topology evaluated separately

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Evaluation

### Context

A layout may look geometrically close while having incorrect connectivity.

### Options considered

1. one combined layout score;
2. geometry-only evaluation;
3. separate geometry and topology evaluation.

### Decision

Evaluate geometry and topology separately, with OD connectivity and false-link metrics required.

### Rationale

This directly tests the draft's infrastructure and replay claims and prevents raster overlap from hiding graph errors.

### Consequences

- more metrics and matching logic;
- claims may be supported for geometry but not topology;
- final reporting must preserve this distinction.

### Affected definitions

- `definitions/07_evaluation_protocol.md`
- `definitions/10_statistical_analysis_plan.md`

### Affected implementation or experiments

Phases 5 and 6.

### Risks introduced or mitigated

Mitigates `R-002`.

### Revisit when

A validated composite score is introduced only as a secondary summary.

### Supersedes

None.

---

## D-008 — Core experiments must be CPU-capable

**Date:** 2026-07-22  
**Status:** Accepted  
**Owner:** Resource

### Context

The reference laptop has a capable GPU, but reproducibility and determinism should not depend on it.

### Options considered

1. GPU-first core;
2. CPU-only project;
3. CPU-capable core with optional GPU acceleration.

### Decision

Require CPU-capable core experiments and allow optional GPU acceleration within the defined VRAM budget.

### Rationale

This improves reproducibility and reduces CUDA-specific risk while preserving acceleration opportunities.

### Consequences

- CPU runtime is the deterministic reference;
- GPU code requires fallback;
- optional perception may remain GPU-dependent.

### Affected definitions

- `definitions/11_hardware_and_resource_budget.md`
- `definitions/14_coding_and_repository_conventions.md`

### Affected implementation or experiments

All core phases.

### Risks introduced or mitigated

Mitigates `R-028` and resource portability risk.

### Revisit when

A core workload cannot meet the approved campaign budget without GPU acceleration.

### Supersedes

None.

## D-009 — Provider evidence is required for Phase 2 acceptance

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Reproducibility

### Context

The earlier Phase 2 milestone review accepted an implementation and
project-created fixture foundation while genuine Argoverse 2 provider data had
not been acquired or executed. That acceptance did not establish provider-data
compatibility or measured laptop evidence.

### Options considered

1. Retain fixture-only Phase 2 acceptance and describe provider execution as optional.
2. Withdraw fixture-only acceptance and require genuine provider-data execution.
3. Waive provider evidence permanently.

### Decision

Fixture-only Phase 2 acceptance is withdrawn. Phase 2 milestone M2 requires the
empirical evidence gate in `definitions/20_empirical_evidence_gate.md`, including
genuine Argoverse 2 provider-data execution and measured hardware evidence.

### Rationale

Implementation and fixtures demonstrate internal correctness but cannot prove
compatibility with current provider files or real execution on the reference
laptop.

### Consequences

- M2 remains unachieved until the required provider evidence passes.
- Synthetic and project-created fixture evidence remains supporting evidence.
- Missing required external evidence is a blocker rather than optional follow-up.
- Later phases cannot begin while the M2 provider-data gate is unmet.

### Affected definitions

- `definitions/00_project_charter.md`
- `definitions/09_dataset_and_split_policy.md`
- `definitions/11_hardware_and_resource_budget.md`
- `definitions/12_reproducibility_policy.md`
- `definitions/20_empirical_evidence_gate.md`

### Affected implementation or experiments

- Phase 2 milestone review
- Phase 2 Batch 2.15 provider-data pilot
- future external-data and measured-hardware evidence gates

### Risks introduced or mitigated

- Mitigates acceptance based on fixture-only or implementation-only evidence.
- Introduces bounded provider acquisition and execution cost as required phase work.

### Revisit when

Revisit only through an explicit human waiver recorded in this decision log.

### Supersedes

The fixture-only Phase 2 milestone acceptance recorded before Batch 2.15.

## D-010 — Active remediation and genuine AV2 evidence establish M2

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Reproducibility

### Context

Genuine AV2 execution exposed two ordinary provider incompatibilities: official
motion timestamp endpoints use Arrow `double`, and scenario-local maps can
reference lanes outside the local archive. Both were correctable without a
human waiver.

### Options considered

1. Treat each intermediate provider failure as a human checkpoint.
2. Replace provider execution with fixture evidence.
3. Require active remediation within the batch and accept M2 only after the
   complete materialization and reuse gate passes.

### Decision

Adopt active remediation as the permanent empirical-evidence workflow. Batch
2.15 corrected both provider incompatibilities and completed the deterministic
ten-scenario genuine AV2 materialization and cache-reuse gate on the reference
ASUS laptop. M2 is achieved.

### Rationale

Intermediate failures reveal integration facts. They do not justify abandoning
an achievable evidence gate. Fixtures remain useful regression evidence but
cannot replace provider execution.

### Consequences

- Ordinary acquisition, provider, integration, and platform defects are
  investigated and corrected within the active batch.
- Blockers require failed reasonable remediation attempts or a genuinely
  unavailable external resource or human decision.
- Final reports enumerate intermediate failures and their corrections.
- Phase 3 may begin only under a separate approved batch.

### Affected definitions

- `definitions/19_decision_log.md`
- `definitions/20_empirical_evidence_gate.md`

### Affected implementation or experiments

- Phase 2 Batch 2.15 provider-data evidence
- future empirical evidence gates

### Risks introduced or mitigated

Mitigates fixture substitution, premature blocker declarations, and untested
provider assumptions.

### Revisit when

An empirical gate requires a resource or decision that remains unavailable
after documented reasonable remediation attempts.

### Supersedes

The unachieved-M2 state recorded by D-009; its provider-evidence requirement
remains in force.

## D-011 — Exact hold/linear codec is the Phase 3 correctness baseline

### Status

Accepted.

### Context

Phase 3 requires a lossless reference codec before compact or inferred motion
representations can be evaluated.

### Decision

Use deterministic adjacent-valid-sample `hold` and `linear` primitives as the
exact baseline. Invalid samples terminate runs and are never bridged.
Singleton valid runs use a zero-duration hold. Replay preserves stored source
endpoints exactly and uses decoded provenance.

### Rationale

This contract provides a simple auditable oracle for later compact codecs and
separates exact replay correctness from compression or inference quality.

### Consequences

- Exact endpoint position error must be `0.0`.
- Equivalent inputs must produce stable IDs and matching Parquet checksums.
- The exact baseline is not the final compact representation.
- Synthetic and genuine-provider evidence are both required for M3.

### Affected implementation or experiments

- Phase 3 Batch 3.1 exact codec, replay, serialization, and evidence

### Revisit when

A later approved codec has demonstrated correctness against this baseline.

## D-012 — Compact linear codec minimizes segments at a fixed error bound

### Status

Accepted.

### Context

Phase 3 requires a deterministic compact motion representation that remains
auditable against the exact Batch 3.1 baseline.

### Decision

For each valid source run, use exact dynamic programming to minimize the number
of source-endpoint hold/linear segments whose replay error at every source
timestamp is at most the configured position bound. Invalid samples terminate
runs, elevation-availability changes terminate candidate intervals, and equal
optima prefer the later next breakpoint recursively.

### Rationale

This contract gives the fixed development setting a reproducible global
optimum while preserving source gaps, endpoint identity, and deterministic
artifact output.

### Consequences

- The 0.10 m Batch 3.2 setting is developmental, not a release threshold.
- Position is the hard fitting constraint; heading and velocity are diagnostics.
- Phase 4 owns scientific comparisons across codecs and tolerances.

### Affected implementation or experiments

- Phase 3 Batch 3.2 compact codec, replay validation, artifacts, and evidence

### Revisit when

Phase 4 freezes the comparative codec and tolerance experiment matrix.

## D-013 - Velocity-aware codec uses endpoint-derivative cubic Hermite candidates

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Engineering

### Context

Batch 3.3 requires a curved primitive that uses the velocity fields already
present in the procedural schema while retaining deterministic, auditable
source-timestamp fitting.

### Decision

Add `cubic_hermite` to the primitive vocabulary without changing the canonical
schema. Cubic Hermite candidates use stored endpoint x/y velocity as analytic
endpoint derivatives, linear z interpolation, and the established wrapped
heading interpolation. The trajectory-local dynamic program minimizes segment
count first, then squared position error, represented velocity error, later
breakpoints, and declared primitive order.

### Rationale

This is a strict candidate-set extension of the compact linear codec and allows
curved replay without fitting hidden parameters or introducing an optimizer.
It also makes the limited optimality claim and all metric tradeoffs explicit.

### Consequences

- Position remains the only hard fitting constraint at the fixed 0.10 m
  development setting.
- Heading and velocity remain measured diagnostics.
- Hermite endpoint velocity must be complete and replay velocity is analytic.
- Phase 4, not Batch 3.3, owns tolerance and method comparisons.

### Affected implementation or experiments

- Phase 3 Batch 3.3 Hermite codec, replay, artifacts, and evidence

### Revisit when

Phase 4 freezes the comparative codec and tolerance experiment matrix.

## D-014 - Hybrid codec enforces source position and represented velocity bounds

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Engineering

### Context

Batch 3.3 improved compression with cubic Hermite candidates, but genuine AV2
evaluation exposed represented velocity errors above the intended development
envelope.

### Decision

Retain the deterministic hold, linear, and cubic-Hermite candidate vocabulary
and add a hard 1.00 m/s represented x/y velocity-vector bound alongside the
existing hard 0.10 m source-timestamp position bound. Apply the velocity bound
only where both source velocity components are present. Use cached, vectorized
candidate generation followed by the exact trajectory-local dynamic program.

### Rationale

The dual bound directly addresses the observed Batch 3.3 weakness while
preserving its useful curved replay and its limited candidate-set optimality
claim. Vectorized evaluation changes execution cost, not candidate semantics.

### Consequences

- Compression may regress relative to the unconstrained Hermite codec.
- Missing source velocity remains missing and creates no inferred constraint.
- Candidate and dynamic-programming work are measured separately.
- The two bounds remain fixed development settings; Phase 4 owns sweeps and
  scientific method selection.

### Affected implementation or experiments

- Phase 3 Batch 3.4 codec, replay validation, artifacts, diagnostics, and evidence

### Revisit when

Phase 4 freezes the comparative codec and tolerance experiment matrix.

## D-015 - Semantic motion uses deterministic source-derived rules

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Engineering

### Context

The Batch 3.4 procedural representation provides bounded numerical replay but
does not explicitly identify behaviorally meaningful temporal structure.

### Decision

Derive immutable semantic waypoints and gap, stop, turn, acceleration, and
braking events from canonical source trajectories using fixed Batch 3.5 rules.
Reference the existing procedural tape for numerical replay, preserve invalid
gaps, and measure source/replay event agreement without treating it as event
ground-truth accuracy.

### Rationale

Separate source-authoritative symbolic records from compressed numerical
replay while retaining deterministic identities, ordering, provenance, and
artifact verification.

### Consequences

- AV2 event counts are detector outputs, not complete labeled ground truth.
- Synthetic scenarios provide the exact semantic oracle gate.
- Detector thresholds remain fixed development settings.
- Phase 4 owns threshold sweeps and broader scientific validation.

### Affected implementation or experiments

- Phase 3 Batch 3.5 semantic models, detectors, artifacts, and evidence

### Revisit when

Phase 4 freezes the semantic detector evaluation matrix.

## D-016 - Shared motion templates are motion-only observed medoids

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Engineering

### Context

Batch 3.5 exposes deterministic motion events but does not organize compatible
procedural runs into reusable symbolic route structures.

### Decision

Classify tracks from fixed semantic-event and valid-path rules, then cluster
positive-length valid runs within one scenario by deterministic complete-link
compatibility in the source x-y frame. Represent each cluster with its observed
medoid path. Keep map data hidden during construction and use canonical AV2 lane
centerlines only for post-construction evaluation.

### Rationale

This creates an interpretable shared-motion layer while preserving provenance,
direction, gaps, bounded numerical replay authority, and a clean empirical
separation between motion grouping and map agreement.

### Consequences

- Categories may span scenarios, but route templates are scenario-scoped.
- Zero-length stationary tracks remain category metadata and never fabricate
  route geometry.
- Singleton templates remain explicit and shared coverage is measured honestly.
- Fixed thresholds remain development settings; Phase 4 owns sweeps.
- Phase 5 owns layout induction from motion bundles.

### Affected implementation or experiments

- Phase 3 Batch 3.6 shared-motion models, grouping, artifacts, map evaluation,
  diagnostics, and evidence

### Revisit when

Phase 4 freezes shared-motion threshold sweeps and comparative evaluation.

## D-017 - Phase 3 packages reference thirteen immutable canonical tables

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Engineering

### Context

The accepted numerical, semantic, and shared-motion layers require one
dataset-level persistence and query contract without duplicating canonical
table rows or weakening their individual provenance.

### Decision

Freeze `ProceduralMotionPackage` schema version `1.0` as a canonical-JSON
manifest over the exact ordered thirteen-schema registry. Each logical table
references ordered immutable repository-relative Parquet parts by schema
fingerprint, byte size, SHA-256, and row count. The package identity uses the
`procedural-motion-package` domain and includes dataset, source, method,
schema, artifact, identifier, and aggregate-count identities. Loading verifies
the complete reference chain before exposing read-only deterministic queries
and Batch 3.4 numerical replay.

### Rationale

This proves persistence and queryability while keeping canonical Parquet rows
authoritative, bounded readers enforceable, and generated package bundles
small and reproducible.

### Consequences

- The package manifest is derived and is not a fourteenth canonical schema.
- Numerical replay remains authoritative.
- Semantic events and shared templates remain interpretation layers.
- Package loading fails on structural, schema, checksum, count, or reference
  corruption.
- Mutation, editing, branching, rerouting, and layout induction remain outside
  this contract.

### Affected implementation or experiments

- Phase 3 Batch 3.7 package model, loader, validator, query interface, bundle,
  integration evidence, and milestone review

### Revisit when

Phase 7 defines the editing and branch runtime contract.

## D-018 - Phase 4 uses a frozen 500-scenario AV2 motion cohort

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Evaluation

### Context

Phase 4 requires a genuine-provider evaluation cohort whose membership cannot
be influenced by codec behavior, validation outcomes, or later scientific
results.

### Decision

Freeze exactly 150 development and 50 pilot scenarios from the official AV2
Motion Forecasting train partition and 300 test scenarios from its val
partition. Select by deterministic root-seed ranking over role, partition, and
provider scenario ID before inspecting scientific outcomes. Preserve source
checksums, selection ranks, validation outcomes, and the exact role membership
in one canonical cohort identity.

### Rationale

The fixed, disjoint roles support implementation debugging, final protocol
confirmation, and an untouched frozen campaign while preventing
outcome-dependent replacement.

### Consequences

- Development may be used for implementation and parameter-grid design.
- Pilot is reserved for final protocol confirmation.
- Test remains untouched until the frozen campaign.
- Validation exclusions are recorded but never change cohort membership.
- Provider files and canonical cache entries remain local ignored artifacts.

### Affected implementation or experiments

- Phase 4 Batch 4.1 cohort acquisition, materialization, validation, and evidence
- All later Phase 4 motion-representation campaigns

### Revisit when

Only a separately approved dataset-policy decision may replace or extend the
cohort; Phase 4 outcomes are not sufficient cause.

## D-019 - Comparable motion baselines use one frozen development grid

**Date:** 2026-07-26
**Status:** Accepted
**Owner:** Evaluation

### Context

Phase 4 needs deterministic storage and procedural comparison methods before
fair-budget analysis can begin. Allowing per-trajectory tuning or changing the
grid after inspecting outcomes would make those comparisons irreproducible.

### Decision

Freeze the Batch 4.2 raw-sample reference, uniform linear and Hermite stride
grids, deterministic RDP tolerance grid, fixed elapsed-time grid, and the four
accepted Phase 3 configurations. Execute every configuration on all 150 frozen
development scenarios with identical included trajectories, source-timestamp
replay, invalid-gap handling, canonical serialized-byte accounting, and two
equivalent artifact writes. Do not use map data or inspect pilot or test
outcomes.

### Rationale

One immutable development grid and common artifact contract isolate
representation behavior from cohort selection, provider access, and
outcome-dependent tuning.

### Consequences

- Raw samples are the canonical storage reference, not a procedural codec.
- Encoded baselines reuse the accepted procedural records and schemas.
- Baseline failures remain explicit and prevent the batch decision from being
  achieved.
- Batch 4.2 evidence cannot declare a final winner.
- Matched-budget, significance, event, and Pareto analyses remain deferred.

### Affected implementation or experiments

- Phase 4 Batch 4.2 baseline API, campaign runner, artifacts, and evidence
- Later Phase 4 fair-budget and frozen-campaign comparisons

### Revisit when

Only a separately approved protocol decision may revise this development grid;
observed Batch 4.2 outcomes are not sufficient cause.

## D-020 - Phase 4 motion and semantic metrics use one frozen contract

**Date:** 2026-07-27
**Status:** Accepted
**Owner:** Evaluation

### Context

Every Phase 4 representation requires comparable source-timestamp geometry,
gap, endpoint, deterministic replay, semantic preservation, complexity,
storage, and resource measurements before later budget matching or statistical
analysis.

### Decision

Freeze Batch 4.3 metrics as follows: source-aware 2D/3D Euclidean position
error; shortest wrapped heading error; planar velocity-vector error; separate
valid-run endpoints; invalid-timestamp and integer-midpoint gap probes; the
explicit interpolated quantile rule; distinct sample-micro and
trajectory-macro aggregation; frozen Batch 3.5 event detection and matching;
canonical replay hashes; scenario-level physical bytes; and explicit failures.
Apply the contract only to all 150 frozen development scenarios and all 7,012
included trajectories for every Batch 4.2 method.

### Rationale

One versioned common layer prevents method-specific metric choices, hidden
missingness, sample/trajectory weighting confusion, and storage estimates from
being substituted for physical artifacts.

### Consequences

- Raw and exact representations must have zero source-timestamp motion error.
- Raw samples must preserve source events exactly.
- Derived Parquet records remain untracked while lightweight evidence is
  committed.
- Batch 4.2 evidence remains unchanged and discrepancies are audited openly.
- No ranking, matched-budget, Pareto, significance, or final-method claim is
  authorized by this decision.

### Affected implementation or experiments

- Phase 4 Batch 4.3 metrics API, result schemas, campaign, audit, and evidence
- All later Phase 4 motion comparisons

### Revisit when

Only a separately approved metric-version decision may revise this contract;
pilot or frozen-test outcomes are not sufficient cause.

## D-021 - Matched-budget exploration uses a frozen deterministic sweep

**Date:** 2026-07-27
**Status:** Accepted
**Owner:** Evaluation

### Context

Phase 4 requires comparable representation-size and complexity observations
before the final campaign protocol can be frozen.

### Decision

Freeze Batch 4.4 to the declared 39-point grid on development ranks 1-25.
Process parameter points and scenarios in stable order with immutable
scenario/configuration checkpoints. Match byte and keyframe targets using only
the relevant achieved budget: largest value at or below the target, otherwise
smallest above, with canonical parameter identity as the sole tie-break.

### Consequences

- Completed artifacts must verify before checkpoint reuse.
- Failed units remain explicit and retry with an advanced attempt count.
- Metrics are not interpolated and do not influence budget selection.
- Pilot and test metadata and outcomes remain unread.
- Results are exploratory and cannot select a final method or release
  configuration.

### Affected implementation or experiments

- Phase 4 Batch 4.4 sweep engine, artifacts, campaign, and evidence
- Batch 4.5 protocol audit and final campaign-grid freeze

### Revisit when

Only a separately approved protocol decision may change the final frozen grid;
exploratory outcomes alone do not authorize a change.

## D-022 - Motion evaluation protocol freezes at supported development budgets

**Date:** 2026-07-27
**Status:** Accepted
**Owner:** Evaluation

### Context

The complete Phase 4 development evidence must support fair matched-budget
comparisons before either withheld cohort is executed.

### Decision

Freeze the accepted 18-configuration Batch 4.3 matrix for pilot and test.
Primary comparisons use serialized-byte ratio 0.48 with maximum mismatch 0.05
and keyframe ratio 0.12 with maximum mismatch 0.02 across all seven comparison
families. Diagnostic temporal comparisons use byte ratio 0.71 and keyframe
ratio 0.23 with maximum mismatch 0.01. The supplemental grid is empty because
accepted development points already satisfy primary coverage.

### Consequences

- Raw samples and exact adjacent replay remain references, not matched claims.
- Every accepted interpolation, compactness, and procedural ablation remains.
- Matching uses only the relevant achieved budget and never interpolates.
- Pilot work may validate feasibility but cannot change the frozen test matrix.
- No method is selected by this decision.

### Affected implementation or experiments

- Phase 4 Batch 4.5 protocol audit and freeze evidence
- Later Phase 4 pilot and test motion campaigns

### Revisit when

Only a separately versioned protocol correction for a verified implementation
or contract defect may supersede this decision; pilot outcomes are not grounds
for changing the test matrix.

## D-023 - Frozen motion campaign executed without protocol change

**Date:** 2026-07-27
**Status:** Accepted
**Owner:** Evaluation

### Context

The withheld pilot and test cohorts required complete execution of the accepted
Batch 4.5 matrix before statistical analysis.

### Decision

Record completion of the frozen 18-configuration campaign on all 50 pilot and
300 test scenarios. The pilot gate passed without a scientific protocol change,
and test outcomes did not alter the campaign.

### Consequences

- All 6,300 scenario/configuration units and their verification passes are
  accounted for.
- Results remain descriptive until Batch 4.7.
- No final method is selected by this decision.

### Affected implementation or experiments

- Phase 4 Batch 4.6 frozen pilot and test motion campaign

### Revisit when

Batch 4.7 performs the separately approved statistical analysis.

## D-024 - Confirmatory inference uses paired scenario aggregates

**Date:** 2026-07-27
**Status:** Accepted
**Owner:** Evaluation

### Context

The frozen Batch 4.6 pilot and test campaign completed without protocol change
and required a predeclared confirmatory analysis that did not inflate the
sample size with nested trajectories, samples, or events.

### Decision

Use the scenario as the independent unit for all paired inference. Treat the
300-scenario test cohort as confirmatory and the 50-scenario pilot as separate
secondary replication evidence. For every frozen matched-budget method pair,
report paired percentile-bootstrap confidence intervals, two-sided paired
sign-flip tests, natural and standardized effects, signed-rank biserial
correlations, win/tie/loss counts, and Holm-adjusted p-values within each
condition, cohort role, and metric domain.

### Consequences

- Development, pilot, and test outcomes are not pooled.
- Undefined event metrics and missing pairs remain explicit and are not imputed.
- Source-absent event sensitivity is secondary to present-event F1 analysis.
- City, class, motion-category, and aggregation-level analyses are descriptive.
- Statistical significance does not imply practical importance.
- No universal winner or final method is selected by this decision.

### Affected implementation or experiments

- Phase 4 Batch 4.7 statistical procedures, campaign, evidence, and tests
- Later Phase 4 reporting and final-method selection

### Revisit when

Only a separately approved analysis correction may revise these inferential
choices. Outcome preference is not grounds for revision.

## D-025 - Representation contributions use frozen scenario-level contrasts

**Date:** 2026-07-27
**Status:** Accepted
**Owner:** Evaluation

### Context

The completed frozen campaign and confirmatory analysis permit attribution of
measured behavior to predeclared representation choices without reopening codec,
metric, cohort, or budget decisions.

### Decision

Attribute representation contributions using ordered, paired scenario-level
contrasts over the accepted Batch 4.6 results and Batch 4.7 aggregates. Preserve
actual achieved budget mismatches, keep pilot and test separate, apply the
accepted deterministic paired procedures, and correct within each ablation
family, cohort role, and metric domain. Label statistical results, descriptive
tendencies, and invariant guarantees distinctly.

### Consequences

- No codec is re-executed and no configuration or metric is changed.
- Identical-keyframe interpolation contrasts fail if keyframe counts differ.
- Raw and exact procedural references are descriptive, not matched competitors.
- City, class, motion-category, and event-type exceptions remain visible.
- Storage, fidelity, semantics, runtime, and guarantees are not collapsed.
- No universal winner or final release configuration is selected.

### Affected implementation or experiments

- Phase 4 Batch 4.8 ablation procedures, evidence, tests, and runbook
- Later Phase 4 reporting and final-method selection

### Revisit when

Only a separately approved correction to an input artifact or attribution
procedure may revise this analysis. Outcome preference is not grounds for
revision.

## D-026 - Qualitative evidence uses deterministic frozen-test ranks

**Date:** 2026-07-27
**Status:** Accepted
**Owner:** Reporting

### Context

Phase 4 requires release figures and failure examples that explain the
accepted quantitative results without visually cherry-picking favorable test
scenarios or exposing provider identifiers.

### Decision

Select qualitative examples from the frozen 300-scenario test cohort with
machine-readable metric ranks fixed before rendering and safe hashed
identifiers as the final tie break. Publish favorable, median, adverse,
semantic, agent-class, and robustness-exception cases. Keep exact source
identifiers only in an ignored local reproduction map.

### Consequences

- Figure and replay inputs are reproducible from accepted frozen artifacts.
- Failures and opposite-direction subgroups remain visible.
- Qualitative evidence does not change codecs, metrics, budgets, or inference.
- SVG/PNG figures and replay manifests carry deterministic checksums.

### Affected implementation or experiments

- Phase 4 Batch 4.9 selection, figures, replay sequence, and failure analysis

### Revisit when

Only a separately approved correction to an accepted input artifact or
release-evidence contract justifies revision.

## D-027 - Phase 4 motion evidence is frozen for milestone M4

**Date:** 2026-07-28
**Status:** Accepted
**Owner:** Evaluation

### Context

Batches 4.1 through 4.9 completed the frozen AV2 cohort, comparable
representations, metric contract, protocol, pilot and test campaign,
confirmatory statistics, representation ablations, and deterministic
qualitative evidence needed for the Phase 4 milestone review.

### Decision

Freeze the accepted Phase 4 motion-evidence package as milestone M4. Preserve
the 500-scenario development, pilot, and test cohort; 18 representations;
matched primary and diagnostic budgets; scenario-level inferential procedures;
ablation families; qualitative ranking contract; failures; and qualifications.
Treat raw samples and exact adjacent-sample replay as references, keep
position-bounded linear and the velocity-bounded hybrid as distinct operating
choices, and do not declare a universal winner.

### Consequences

- Phase 4 evidence may be integrated, rendered, checksum-linked, and reviewed
  without changing experiments, configurations, metrics, or hypotheses.
- Exact adjacent replay remains a correctness reference, not compression.
- Phase 4 does not prove spatial layout, grammar quality, editing, branching,
  rerouting, simulation, or raw-video extraction.
- Phase 5 requires a separately approved entry batch and evidence contract.

### Affected implementation or experiments

- Phase 4 Batch 4.10 milestone evidence, release tables, figure manifest,
  claim audit, reproduction package, and review

### Revisit when

Only a separately approved correction to accepted Phase 4 evidence or an
explicitly versioned new campaign may revise this freeze.

## D-028 - Layout feasibility uses a frozen motion-only Stage A

**Date:** 2026-07-28
**Status:** Accepted
**Owner:** Evaluation

### Context

Phase 4 established motion-representation evidence but did not establish that
motion observations are sufficient to induce persistent spatial layout. A
feasibility gate is required before production corridor, junction, graph, zone,
or grammar work.

### Decision

Evaluate layout-induction feasibility on the frozen 150-scenario development
cohort through two auditable stages. Stage A receives only canonical motion,
derived procedural and semantic motion, accepted scenario-local shared-motion
logic, split metadata, and coordinate-frame metadata. Freeze and checksum Stage
A before Stage B opens the matching development maps. Stage B is descriptive,
writes separately, and cannot tune, replace, or modify Stage A.

Use the accepted Batch 3.6 shared-motion thresholds and predeclared descriptive
criteria. Retain zero-count, sparse, ambiguous, and unfavorable findings. Do not
use pilot or test data and do not materialize production layout objects.

### Consequences

- Stage A and Stage B have separate artifact roots and checksum identities.
- Cross-scenario grouping requires affirmative coordinate-frame metadata;
  numerical coordinate proximity is insufficient.
- Geometric crossings remain ambiguous unless observed transitions or elevation
  support a classification.
- Batch 5.1 may classify candidate claims only as feasible, aggregation-bound,
  partial, unsupported, or deferred.
- Complete road-network reconstruction and complete symbolic spatial grammar
  remain unsupported by this gate.

### Affected implementation or experiments

- Phase 5 Batch 5.1 motion feasibility, map diagnostics, claim matrix, evidence,
  tests, and runbook

### Revisit when

Only a separately approved batch may authorize production layout induction,
new thresholds, broader cohorts, or multi-scenario geographic grouping.

## 6. Governance workflow

When a new decision is needed:

1. create a Proposed entry;
2. identify affected definitions and risks;
3. review alternatives;
4. approve, reject, or defer;
5. update affected definitions if accepted;
6. mark implementation or experiment versions affected;
7. preserve the decision history.

## 7. Contradiction rule

When two accepted decisions conflict:

- the newer decision does not silently replace the older one;
- the newer entry must explicitly list the older decision under `Supersedes`;
- affected definitions must be updated;
- existing results affected by the change must be versioned or invalidated.

## 8. Acceptance criteria

This log is acceptable when:

- it provides a reusable decision template;
- central project decisions are recorded;
- decisions link to definitions and risks;
- superseded decisions remain traceable;
- code is not allowed to become the only record of a research-design change.
