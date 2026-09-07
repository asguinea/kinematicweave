# Claim–Evidence Matrix

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.2 — Research Claims and Evidence Map  
**Status:** Draft for approval

## 1. Purpose

This matrix connects each primary hypothesis to the evidence required to support, limit, or reject it.

Experiment identifiers, exact metric definitions, statistical tests, and acceptance thresholds will be finalized in later Phase 0 batches.

## 2. Matrix

| Hypothesis | Core question | Required methods | Required baselines or ablations | Primary evidence | Experimental unit | Required qualitative evidence |
|---|---|---|---|---|---|---|
| H1 — Semantic preservation | Do event-aware procedural lines preserve selected motion events at matched size? | Event-aware procedural-line codec | RDP, uniform subsampling, event-free procedural lines | Stop F1, turn F1, serialized size, geometric error | Trajectory, with scenario-aware aggregation where required | Examples of preserved, shifted, missed, and false events |
| H2 — Fidelity–compactness | Are procedural lines competitive across storage budgets? | Full procedural-line codec | Raw samples, RDP, uniform subsampling, matched-control-point spline | Error–size Pareto curves, bytes per agent-second, decode cost | Trajectory | Raw/reconstructed overlays at several budgets |
| H3 — Corridor geometry | Can repeated motion recover persistent corridor geometry? | Trajectory clustering, centerline fitting, corridor construction | Density raster plus skeletonization; centerlines without symbolic repair | Centerline distance, precision/recall, traversable-region IoU | Geographic tile | Inferred/ground-truth overlays at multiple coverage levels |
| H4 — Topology recovery | Can repeated motion recover useful junctions and connectivity? | Connectivity-graph and junction inference | Density-skeleton graph; unrepaired centerline graph | Junction F1, edge precision/recall, OD connectivity agreement | Geographic tile | Correct and incorrect junction/connectivity examples |
| H5 — Topology repair | Does symbolic repair improve graph validity? | Grammar or rule-based topology repair | Same inferred geometry before repair | OD connectivity agreement, junction F1, edge metrics, false-link count | Geographic tile | Before/after repair examples, including harmful repairs |
| H6 — Deterministic replay | Does the same serialized tape replay identically? | Reference tape runtime | Repeated runs; serialization round trip | Canonical state-hash equality, query-result equality | Tape or scenario | Optional diagnostic visualization only; quantitative equality is decisive |
| H7 — Edit locality | Are supported semantic edits more local than raw-sample rewriting? | Delay, segment replacement, closure, reroute, branch creation | Equivalent raw-sample edit procedure | Modified elements, modified bytes, edit latency, validity checks | Edit scenario | Before/after branch comparison |
| H8 — Laptop feasibility | Can the frozen core campaign run on the reference laptop? | Complete pipeline | Workload-scale sweep and documented resource budget | Peak RAM/VRAM, disk, runtime, throughput, resumability | Experiment run | Screenshots or logs of representative runs are additional evidence |

## 3. Required evidence by claim

### H1

Must include:

- at least one matched-serialized-size comparison;
- a documented event-matching procedure;
- event-class prevalence;
- confidence intervals;
- a geometric-fidelity check to prevent semantic gains from hiding severe trajectory distortion.

Must not rely on:

- visual inspection alone;
- unmatched representation sizes;
- manually selected event examples.

### H2

Must include:

- a storage-budget sweep;
- actual serialized size;
- at least one raw or near-lossless reference;
- a Pareto analysis rather than a single arbitrary tolerance;
- per-class or per-complexity breakdowns when sample counts permit.

Must not use:

- Python object memory as the primary size metric;
- omitted timestamps or metadata for one method but not another.

### H3

Must include:

- trajectory-only inference;
- ground-truth map withheld from inference;
- multiple trajectory-coverage conditions;
- geometric baselines;
- geographic tiles with adequate and inadequate motion coverage.

Must not conflate:

- oracle grammar conversion;
- trajectory-only inference.

### H4

Must include:

- graph-level metrics;
- explicit node and edge matching;
- connectivity or route tests;
- cases where geometry appears close but topology is wrong.

Must not rely only on:

- raster IoU;
- centerline distance.

### H5

Must include:

- identical pre-repair geometry as the starting point;
- metrics for both repaired true connections and introduced false connections;
- an ablation that disables each major repair rule when practical.

Must not report only:

- the number of connected components;
- qualitative improvements.

### H6

Must include:

- repeated runs;
- serialization/deserialization round trips;
- canonical ordering;
- canonical state hashes;
- failure on any unexplained mismatch.

Approximate visual equivalence is insufficient.

### H7

Must include:

- a defined equivalent raw-sample editing procedure;
- at least three edit types;
- representation-change counts;
- post-edit validity checks;
- branch provenance.

Must not claim:

- human usability;
- authoring speed;
- cognitive simplicity;

unless those are evaluated separately.

### H8

Must include:

- hardware and software capture;
- peak memory;
- disk usage;
- elapsed time;
- workload size;
- interrupted-run recovery for long experiments;
- a complete frozen campaign or a clearly defined reduced claim.

Must not infer feasibility from:

- unit tests alone;
- tiny synthetic examples;
- estimates without measured runs.

## 4. Evidence priority

### Confirmatory evidence

The following are intended to be frozen before the full campaign:

- primary hypotheses;
- primary dataset and split;
- primary methods and baselines;
- primary metrics;
- statistical comparison plan;
- resource budget.

### Exploratory evidence

The following may be explored without becoming primary claims automatically:

- additional event categories;
- alternative cluster features;
- additional repair rules;
- city-specific observations;
- novel qualitative scenarios;
- optional perception demonstrations.

Exploratory findings must be labelled as such unless promoted through an approved design change before the frozen campaign.

## 5. Minimum evidence package

The complete project should produce at least:

1. one primary motion fidelity–size figure;
2. one semantic-event table;
3. one layout geometry table;
4. one layout topology table;
5. one trajectory-coverage figure;
6. one topology-repair ablation;
7. one replay determinism report;
8. one edit-locality table;
9. one laptop resource report;
10. one qualitative success-and-failure gallery.

## 6. Interpretation rules

A primary hypothesis may be reported as:

- **supported**;
- **partially supported**;
- **not supported**;
- **inconclusive**.

The final report must explain the reason, not just apply a label.

Examples:

- H1 may be partially supported if stop preservation improves but turn preservation does not.
- H3 may be supported only above a documented coverage level.
- H5 may be inconclusive if repair improves connectivity but introduces too many false edges.
- H8 may be partially supported if the reduced campaign fits but the full planned sweep does not.

## 7. Batch 0.2 acceptance checklist

- Every primary hypothesis is falsifiable.
- Every primary hypothesis has quantitative evidence.
- Every primary hypothesis identifies a baseline or repeated-run comparison.
- Experimental units are stated.
- Permitted and prohibited conclusions are explicit.
- Negative and mixed outcomes remain reportable.
- The matrix is consistent with the project charter and scope.
