# Research Claims

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.2 — Research Claims and Evidence Map  
**Status:** Draft for approval

## 1. Purpose

This document defines the research claims that the KinematicWeave project will test.

Each primary claim must be:

- falsifiable;
- linked to at least one quantitative experiment;
- evaluated against appropriate baselines;
- bounded by the implemented scene domain and data;
- reportable even when the result is negative.

The project evaluates the representation, inference, replay, and editing concepts. It does not use qualitative examples alone to establish a primary claim.

## 2. Claim classes

The project separates claims into four classes:

1. **Representation claims** — how well procedural lines encode motion.
2. **Inference claims** — what spatial structure can be recovered from repeated trajectories.
3. **Runtime claims** — whether replay, querying, branching, and editing behave correctly and efficiently.
4. **Feasibility claims** — whether the complete evaluation is practical on the reference laptop.

## 3. Primary hypotheses

### H1 — Semantic preservation at matched storage

**Claim**

At matched serialized size, event-aware procedural lines preserve selected semantic motion events better than geometry-only trajectory codecs.

**Primary comparison**

Event-aware procedural lines versus:

- Ramer–Douglas–Peucker simplification;
- uniform temporal subsampling;
- an event-free procedural-line ablation.

**Primary dependent variables**

- stop-event F1;
- turn-event F1.

**Secondary dependent variables**

- average displacement error;
- maximum displacement error;
- heading error.

**Experimental unit**

Trajectory or agent-track, aggregated by scenario where required by the statistical plan.

**Support condition**

The event-aware representation shows a practically meaningful improvement in semantic-event preservation at comparable serialized size without an unacceptable increase in geometric error.

**Limitation or rejection condition**

The claim is not supported when event-aware segmentation provides no reliable improvement, or when any semantic gain is obtained only through materially larger serialized representations.

**Permitted conclusion**

“Under the evaluated event definitions, datasets, and storage budgets, event-aware procedural lines preserved selected motion events better than the tested geometry-only representations.”

**Prohibited conclusion**

“Procedural lines preserve all meaningful behavior.”

---

### H2 — Useful fidelity–compactness tradeoff

**Claim**

Procedural lines provide a useful tradeoff between trajectory reconstruction fidelity and serialized representation size.

**Primary comparison**

Procedural lines versus:

- raw trajectory samples;
- uniform temporal subsampling;
- RDP simplification;
- matched-control-point spline encoding.

**Primary dependent variables**

- bytes per agent-second;
- average displacement error;
- maximum displacement error.

**Secondary dependent variables**

- heading error;
- decode time;
- control points per agent-second.

**Experimental unit**

Trajectory or agent-track.

**Support condition**

Procedural lines lie on or improve the empirical fidelity–size Pareto frontier for a meaningful portion of the tested range.

**Limitation or rejection condition**

The claim is not supported when simpler baselines dominate procedural lines across the tested storage range.

**Permitted conclusion**

“Procedural lines achieved a competitive fidelity–compactness tradeoff on the evaluated trajectories.”

**Prohibited conclusion**

“Procedural lines are always the most compact trajectory representation.”

---

### H3 — Corridor geometry from repeated motion

**Claim**

Repeated trajectories can recover useful persistent corridor geometry in sufficiently observed regions.

**Primary comparison**

Trajectory-only layout inference versus:

- a trajectory-density raster and skeletonization baseline;
- clustering and centerline fitting without symbolic topology repair.

**Primary dependent variables**

- centerline distance;
- centerline precision and recall within tolerance;
- traversable-region IoU where applicable.

**Experimental unit**

Geographic tile.

**Support condition**

The inferred corridors align with ground-truth layout better than the declared baselines under one or more practically relevant trajectory-coverage conditions.

**Limitation or rejection condition**

The claim is limited or rejected when inferred geometry does not outperform simple density-based methods or requires unrealistic coverage.

**Permitted conclusion**

“Repeated trajectories recovered useful corridor geometry under the evaluated coverage and noise conditions.”

**Prohibited conclusion**

“Motion alone reconstructs arbitrary urban geometry.”

---

### H4 — Connectivity and junction topology from repeated motion

**Claim**

Repeated trajectories can recover useful corridor connectivity and junction topology.

**Primary comparison**

Full trajectory-only inference versus:

- density-skeleton topology;
- clustering-derived centerlines without symbolic repair.

**Primary dependent variables**

- junction precision, recall, and F1;
- graph edge precision and recall;
- origin–destination connectivity agreement.

**Secondary dependent variables**

- connected-component agreement;
- routing agreement;
- invalid-node or invalid-edge counts.

**Experimental unit**

Geographic tile.

**Support condition**

The inferred graph preserves useful route connectivity and junction structure beyond geometric overlap alone.

**Limitation or rejection condition**

The claim is not supported when similar-looking geometry produces unreliable or incorrect connectivity.

**Permitted conclusion**

“The inferred layouts preserved useful junction and connectivity structure under the evaluated conditions.”

**Prohibited conclusion**

“The method reconstructs a complete road graph from any collection of trajectories.”

---

### H5 — Value of symbolic topology repair

**Claim**

Symbolic or grammar-based topology repair improves graph validity and route connectivity over geometry extraction alone.

**Primary comparison**

The same inferred centerlines:

- before topology repair;
- after topology repair.

**Primary dependent variables**

- origin–destination connectivity agreement;
- graph edge precision and recall;
- junction F1.

**Secondary dependent variables**

- connected-component agreement;
- number of small gaps repaired;
- number of false connections introduced.

**Experimental unit**

Geographic tile.

**Support condition**

Repair improves one or more primary topology metrics without creating a larger harmful degradation in geometric or edge precision.

**Limitation or rejection condition**

The claim is not supported when repair only increases connectivity by introducing false links.

**Permitted conclusion**

“The tested topology-repair rules improved selected graph-validity measures relative to unrepaired centerlines.”

**Prohibited conclusion**

“Grammar rules always recover the correct topology.”

---

### H6 — Deterministic replay

**Claim**

A serialized tape replays deterministically under the documented software, ordering, and numeric conditions.

**Primary comparison**

Repeated execution of the same tape, configuration, and query schedule.

**Primary dependent variable**

- equality of canonical replay-state hashes.

**Secondary dependent variables**

- equality of query results;
- equality of event order;
- equality after serialization and deserialization.

**Experimental unit**

Tape or scenario.

**Support condition**

All repeated executions produce identical canonical results under the declared determinism protocol.

**Limitation or rejection condition**

Any unexplained mismatch invalidates the claim until resolved or explicitly bounded.

**Permitted conclusion**

“The reference runtime produced deterministic replay under the documented conditions.”

**Prohibited conclusion**

“Replay is bitwise deterministic across all hardware and software environments.”

---

### H7 — Symbolic edit locality

**Claim**

Selected semantic edits require changes to fewer representation elements than equivalent edits applied directly to raw trajectory samples.

**Primary edits**

- delay at a stop;
- replace one trajectory segment;
- close a corridor and reroute affected agents;
- create an alternate branch.

**Primary dependent variables**

- number of modified representation records;
- number of modified procedural segments or symbols;
- modified serialized bytes.

**Secondary dependent variables**

- edit latency;
- number of affected agents;
- post-edit continuity violations.

**Experimental unit**

Edit scenario.

**Support condition**

The symbolic representation expresses the edit through materially fewer modified elements while maintaining the declared validity checks.

**Limitation or rejection condition**

The claim is limited when edits require broad reconstruction or when compact edits create invalid motion.

**Permitted conclusion**

“The implemented symbolic edits were more local than direct raw-sample rewriting for the evaluated operations.”

**Prohibited conclusion**

“The representation is generally easier for humans to edit.”

---

### H8 — Laptop-scale feasibility

**Claim**

The core data preparation, motion evaluation, layout evaluation, replay, and qualitative generation workflows are feasible on the reference ASUS laptop.

**Primary dependent variables**

- peak RAM;
- peak VRAM;
- disk usage;
- wall-clock time;
- throughput;
- successful resumability after interruption.

**Experimental unit**

Experiment run and workload scale.

**Support condition**

The frozen core campaign completes within the approved resource budget and without requiring external compute.

**Limitation or rejection condition**

The claim must be narrowed when mandatory workloads exceed the approved resource envelope or require impractical uninterrupted execution.

**Permitted conclusion**

“The frozen evaluation campaign was completed on the reference laptop within the reported resource envelope.”

**Prohibited conclusion**

“The system is real-time or lightweight on all consumer laptops.”

## 4. Secondary claims

Secondary claims may be reported only when supported by the final experiment matrix.

Possible secondary claims include:

- performance varies systematically with trajectory complexity;
- layout quality improves predictably with trajectory coverage;
- certain agent classes contribute more strongly to layout recovery;
- the oracle grammar compactly represents known layout;
- query latency scales acceptably over a documented range of agents;
- selected qualitative failures correspond to identifiable metric degradation.

Secondary claims must not replace or weaken the primary tests.

## 5. Negative and mixed outcomes

The project treats the following as valid scientific outcomes:

- a simple baseline outperforms the proposed representation;
- semantic gains occur only at some storage budgets;
- layout inference succeeds for corridors but not topology;
- topology repair helps sparse cases but harms dense cases;
- laptop feasibility holds only for bounded scenario sizes;
- some edit operations are local while others require broad recomputation.

Negative or mixed findings must be preserved in the final evidence report.

## 6. General claim restrictions

All final claims must specify, where relevant:

- dataset;
- split;
- scene type;
- agent type;
- trajectory-coverage condition;
- noise condition;
- metric;
- storage or compute budget;
- reference hardware;
- implementation version.

The project must avoid universal language unless the evidence genuinely supports it.

## 7. Traceability requirement

Every primary hypothesis must map to:

```text
Hypothesis
    ↓
Experiment identifiers
    ↓
Input split
    ↓
Methods and baselines
    ↓
Metrics
    ↓
Raw result records
    ↓
Statistical analysis
    ↓
Figure or table
    ↓
Permitted conclusion
```

The full identifiers will be frozen in later Phase 0 batches.
