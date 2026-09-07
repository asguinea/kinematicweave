# Phase 0 Design Freeze Report

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.15 — Definition Integration and Design Freeze Review  
**Status:** Ready for user approval  
**Milestone:** M0 — Design Freeze

## 1. Executive decision

The Phase 0 design is sufficiently complete to begin implementation planning.

The definitions establish:

- the research scope;
- falsifiable hypotheses;
- the Python-first architecture;
- canonical domain and data contracts;
- coordinate and time conventions;
- fair serialization and size accounting;
- dataset and split rules;
- metrics, baselines, and ablations;
- the experiment matrix;
- the statistical plan;
- the ASUS laptop resource envelope;
- testing and reproducibility obligations;
- qualitative evidence rules;
- Codex batch discipline;
- risk and decision governance.

No critical research or architecture decision must be invented by Codex before Phase 1.

## 2. Frozen core proposition

The core project evaluates a symbolic dynamic-scene representation that combines:

1. persistent layout geometry, hierarchy, and connectivity;
2. time-indexed procedural lines for moving agents;
3. selected semantic events;
4. deterministic replay and queries;
5. symbolic branches and edits;
6. trajectory-only inference of selected persistent infrastructure.

The primary empirical domain is 2.5D urban motion and layout.

## 3. Frozen primary hypotheses

- **H1:** Event-aware procedural lines preserve selected semantic events better than geometry-only codecs at matched size.
- **H2:** Procedural lines provide a useful fidelity–compactness tradeoff.
- **H3:** Repeated trajectories recover useful corridor geometry.
- **H4:** Repeated trajectories recover useful junction and connectivity topology.
- **H5:** Symbolic topology repair improves selected graph-validity measures.
- **H6:** The reference tape runtime replays deterministically.
- **H7:** Supported symbolic edits are more local than raw-sample rewriting.
- **H8:** The frozen core campaign is feasible on the reference ASUS laptop.

Negative, partial, and inconclusive outcomes remain valid results.

## 4. Frozen scope boundaries

### Core

- trajectory and vector-map data;
- procedural-line codecs;
- motion representation baselines;
- trajectory-only layout inference;
- geometry and topology evaluation;
- deterministic replay;
- scripted edits and branches;
- reproducible qualitative evidence;
- laptop resource measurements.

### Optional

- raw-video perception;
- depth estimation;
- game-engine or XR integration;
- native C++ runtime;
- user study;
- additional datasets.

### Excluded from primary claims

- photorealistic reconstruction;
- arbitrary volumetric full-3D inference;
- large-model training;
- safety-critical validity;
- universal map recovery;
- general behavior prediction;
- human usability superiority.

## 5. Frozen architecture

The implementation is Python-first and organized into:

```text
kinematicweave.data
kinematicweave.domain
kinematicweave.trajectory
kinematicweave.events
kinematicweave.codecs
kinematicweave.grammar
kinematicweave.layout
kinematicweave.runtime
kinematicweave.editing
kinematicweave.metrics
kinematicweave.experiments
kinematicweave.statistics
kinematicweave.visualization
kinematicweave.reporting
kinematicweave.native
```

`kinematicweave.native` remains empty or optional until profiling justifies it.

## 6. Frozen evidence strategy

### Motion

Compare raw samples, uniform subsampling, RDP, splines, event-free procedural lines, and event-aware procedural lines.

Primary evidence:

- error versus serialized size;
- stop and turn preservation;
- encode/decode cost;
- ablations and robustness.

### Layout

Compare density/skeletonization, directional clustering/centerlines, and grammar-assisted repair.

Primary evidence:

- centerline geometry;
- junctions;
- graph edges;
- OD connectivity;
- false connections;
- coverage and robustness.

### Runtime and edits

Measure:

- replay hashes;
- query hashes and latency;
- replay scaling;
- edit locality;
- continuity;
- closure compliance;
- rerouting success;
- branch storage.

### Feasibility

Measure:

- wall time;
- peak RAM;
- peak VRAM;
- disk use;
- throughput;
- resumability.

## 7. Frozen dataset strategy

Primary planned real dataset:

- Argoverse 2 Motion Forecasting;
- associated vector-map information;
- no required raw sensor download.

Split hierarchy:

```text
Synthetic
Smoke
Development
Pilot
Frozen test
Optional held-out city
```

Layout leakage is controlled geographically, not merely by scenario identifier.

## 8. Frozen fairness rules

- All compared methods receive the same canonical inputs.
- Ground-truth maps are withheld from trajectory-only inference.
- Compactness uses reconstructible canonical payload bytes.
- Required timing and event metadata count toward size.
- Perturbations are generated once and shared across methods.
- Evaluation timestamps are identical.
- Test parameters are frozen before confirmatory runs.
- Failed units remain visible.

## 9. Frozen statistical rules

- Motion results account for scenario-level dependence.
- Layout results use the geographic tile as the primary unit.
- Coverage seeds are nested within tiles.
- Primary comparisons are paired.
- Final confidence intervals use nonparametric bootstrap.
- Natural-unit differences and effect sizes are mandatory.
- Holm correction is used within confirmatory hypothesis families.
- Determinism requires zero unexplained hash mismatches.
- Feasibility is judged against explicit resource limits.

## 10. Frozen laptop envelope

Reference machine:

- ASUS ROG Zephyrus G14 GA403UP;
- 32 GB RAM;
- 1 TB SSD;
- RTX 5070 laptop GPU;
- Ryzen 9-class CPU.

Core rules:

- CPU-capable;
- normal target below 24 GB RAM;
- bounded GPU use;
- at least 15% SSD free;
- long runs resumable;
- no required cloud compute;
- no mandatory multi-day uninterrupted process.

## 11. Deferred configuration values

The design freeze does not freeze development-tuned numeric values prematurely.

The following are frozen later using development and pilot data:

- stop and turn thresholds;
- tile size;
- clustering parameters;
- matched-size budget grid;
- metric tolerances;
- repair-rule parameters;
- worker counts;
- batch sizes;
- final qualitative colors.

These are implementation configurations, not missing research definitions.

## 12. Required Phase 1 outcome

Phase 1 must produce a reproducible repository foundation capable of:

1. installing from a committed lockfile;
2. running formatting, linting, typing, and tests;
3. executing a synthetic smoke pipeline;
4. loading versioned configuration;
5. recording run, environment, and hardware metadata;
6. producing a small experiment manifest and artifact manifest;
7. enforcing the repository structure and conventions.

Phase 1 must not implement the full scientific methods prematurely.

## 13. Phase 1 planning constraints

When Phase 1 is divided into batches:

- each batch receives a number and name;
- batches remain small and independently reviewable;
- every batch has tests;
- Codex reads the relevant definitions;
- Codex reports using `17_codex_report_template.md`;
- fixes retain the original batch number;
- no batch begins until the previous batch is accepted.

## 14. Known high-priority risks entering implementation

The first implementation phases must watch:

- `R-008` coordinate-frame misalignment;
- `R-009` geographic leakage;
- `R-013` unfair size accounting;
- `R-019` clustering memory growth;
- `R-020` nondeterminism under concurrency;
- `R-021` unstable serialization;
- `R-022` viewer work delaying evaluation;
- `R-023` optional perception scope growth;
- `R-024` premature C++;
- `R-030` campaign runtime;
- `R-031` SSD growth.

## 15. Design-freeze condition

M0 — Design Freeze is achieved when the user approves Batch 0.15.

After approval:

- Phase 0 definitions become the implementation source of truth;
- Phase 1 may be split into implementation batches;
- material research-design changes require a decision-log entry;
- Codex may begin only approved implementation batches.

## 16. Final Phase 0 result

**Recommendation:** Approve M0 — Design Freeze and proceed to Phase 1 batch planning.

The definitions are complete enough to support disciplined implementation without requiring Codex to invent the scientific method, architecture, schemas, evaluation rules, or resource constraints.
