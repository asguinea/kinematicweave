# Experiment Matrix

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.9 — Experiment Matrix and Statistical Analysis Plan  
**Status:** Draft for approval

## 1. Purpose

This document defines the experiment families required to evaluate the primary KinematicWeave hypotheses. Each experiment specifies its purpose, experimental unit, methods, conditions, outputs, and role in the evidence package.

## 2. Experiment classes

Experiments are grouped into:

1. synthetic correctness;
2. motion representation;
3. layout inference;
4. replay and query runtime;
5. editing and branching;
6. resource feasibility;
7. qualitative artifact generation.

Each experiment is labelled as primary confirmatory, required diagnostic, secondary confirmatory, or exploratory.

## 3. Common rules

All experiments must:

- use versioned configuration files;
- record input split, method, parameters, seed, code revision, and hardware;
- emit unit-level raw results;
- preserve failures and undefined metrics;
- remain resumable at the experimental-unit level when expensive;
- follow the contracts and fairness rules in the preceding definition files.

# 4. Synthetic correctness experiments

## SYN-01 — Codec round-trip

**Class:** Required diagnostic  
**Purpose:** Verify encode, serialize, deserialize, decode, size accounting, and deterministic output.

**Inputs:** Straight motion, turns, stops, irregular sampling, missing gaps, and duplicate-resolution fixtures.  
**Methods:** Raw, uniform sampling, RDP, spline, event-free procedural lines, event-aware procedural lines.  
**Unit:** Synthetic trajectory.  
**Outputs:** Round-trip status, expected error checks, byte counts, stable serialization checks.

## SYN-02 — Event known-answer tests

**Class:** Required diagnostic  
**Purpose:** Validate stop and turn detection and matching.

**Inputs:** No-event tracks, full stops, multiple stops, left/right turns, noisy turns, and combined stop-turn cases.  
**Unit:** Synthetic trajectory.  
**Outputs:** Precision, recall, F1, timing error, and duration error.

## SYN-03 — Layout known-answer tests

**Class:** Required diagnostic  
**Purpose:** Validate geometry, junction, graph, and route metrics.

**Inputs:** Straight corridor, parallel corridors, T-junction, four-way intersection, merge, split, disconnected paths, planar crossing without connection, and grade-separated crossing.  
**Methods:** Density skeleton, directional clustering, grammar-assisted inference, oracle grammar.  
**Unit:** Synthetic tile.

## SYN-04 — Replay determinism

**Class:** Required diagnostic  
**Purpose:** Verify replay and query hashes across repetitions, processes, worker counts, and serialization round trips.  
**Unit:** Synthetic tape.  
**Pass condition:** No unexplained hash mismatch.

## SYN-05 — Edit and rerouting correctness

**Class:** Required diagnostic  
**Purpose:** Validate delay, segment replacement, branch creation, corridor closure, successful rerouting, and impossible rerouting.  
**Unit:** Synthetic edit scenario.

# 5. Motion representation experiments

## MOT-01 — Serialized-size sweep

**Class:** Primary confirmatory  
**Hypotheses:** H1, H2  
**Split:** Frozen motion test  
**Unit:** Trajectory, with scenario-aware aggregation.

**Methods:** Uniform sampling, RDP, spline, event-free procedural lines, event-aware procedural lines. Raw samples provide the fidelity and size reference.

**Conditions:** Common bytes-per-agent-second budgets spanning severe compression through near-lossless reconstruction.

**Primary metrics:** ADE, maximum deviation, bytes per agent-second, stop F1, turn F1.  
**Secondary metrics:** Heading error, speed error, encode time, decode time.

**Required outputs:** Per-trajectory results, error-size curves, event-F1-size curves, and empirical Pareto frontier.

## MOT-02 — Matched-control-point comparison

**Class:** Secondary confirmatory  
**Hypothesis:** H2  
**Methods:** RDP, spline, event-free procedural lines, event-aware procedural lines.  
**Metrics:** ADE, maximum deviation, heading error, and actual serialized size.  
**Unit:** Trajectory.

## MOT-03 — Event-awareness ablation

**Class:** Primary confirmatory  
**Hypothesis:** H1

**Variants:**

- event-free procedural lines;
- full event-aware procedural lines;
- events stored but not used for segmentation;
- event segmentation without event metadata;
- stop-only;
- turn-only;
- stop plus turn.

**Condition:** Matched serialized size.  
**Metrics:** Stop F1, turn F1, ADE, maximum deviation.  
**Unit:** Trajectory.

## MOT-04 — Interpolation ablation

**Class:** Required diagnostic  
**Variants:** Piecewise-linear and spline interpolation using matched boundaries where feasible.  
**Metrics:** ADE, maximum deviation, heading error, size, decode time.  
**Unit:** Trajectory.

## MOT-05 — Numeric-precision ablation

**Class:** Required diagnostic  
**Variants:** Float64, float32, and an optional approved quantized form.  
**Metrics:** Serialized size, ADE, maximum deviation, and round-trip stability.  
**Unit:** Trajectory.

## MOT-06 — Trajectory-complexity analysis

**Class:** Secondary confirmatory  
**Strata:** Duration, curvature, stop count, speed variance, and sampling regularity.  
**Metrics:** Primary motion metrics by stratum.  
**Unit:** Trajectory.

## MOT-07 — Localization-noise robustness

**Class:** Primary confirmatory  
**Hypotheses:** H1, H2  
**Conditions:** Zero and development-selected low, moderate, and high planar-noise levels.  
**Seeds:** Five per nonzero level unless revised before freeze.  
**Metrics:** Motion and event metrics.  
**Unit:** Trajectory.

## MOT-08 — Missing-sample robustness

**Class:** Primary confirmatory  
**Hypotheses:** H1, H2  
**Perturbations:** Random removal, contiguous gaps, and trajectory truncation.  
**Seeds:** Five per stochastic condition.  
**Metrics:** Motion metrics, event metrics, and failure rate.  
**Unit:** Trajectory.

## MOT-09 — Per-class and per-city breakdown

**Class:** Required diagnostic  
**Dimensions:** Agent class, city or region, and ego/focal/other role.  
**Metrics:** Primary motion metrics.  
**Unit:** Trajectory.

# 6. Layout inference experiments

## LAY-01 — Oracle grammar capacity

**Class:** Required diagnostic  
**Purpose:** Test whether known map structure can be represented compactly and replayed correctly.  
**Method:** Oracle grammar.  
**Metrics:** Geometry reconstruction, topology agreement, grammar counts, and serialized size.  
**Unit:** Geographic tile.

## LAY-02 — Full trajectory-only comparison

**Class:** Primary confirmatory  
**Hypotheses:** H3, H4, H5  
**Split:** Frozen layout test  
**Methods:** Density skeleton, directional clustering, and grammar-assisted inference.  
**Condition:** 100% eligible trajectory coverage.

**Primary metrics:** Centerline F1, symmetric centerline distance, junction F1, graph-edge F1, OD connectivity agreement.  
**Secondary metrics:** Region IoU, component agreement, route-length error, false connection count, serialized size.  
**Unit:** Geographic tile.

## LAY-03 — Coverage sweep

**Class:** Primary confirmatory  
**Hypotheses:** H3, H4  
**Coverage:** 10%, 25%, 50%, and 100%.  
**Methods:** Density skeleton, directional clustering, grammar-assisted inference.  
**Seeds:** Five at stochastic fractions; nested subsets preferred.  
**Metrics:** Primary layout geometry and topology metrics.  
**Unit:** Geographic tile.

## LAY-04 — Topology-repair ablation

**Class:** Primary confirmatory  
**Hypothesis:** H5

**Variants:**

- unrepaired centerline graph;
- full repair;
- each major repair rule removed individually.

**Metrics:** OD connectivity agreement, graph-edge F1, junction F1, false connection count.  
**Unit:** Geographic tile.

## LAY-05 — Direction-feature ablation

**Class:** Required diagnostic  
**Variants:** Position-only clustering versus position-plus-direction clustering.  
**Metrics:** Primary layout metrics.  
**Unit:** Geographic tile.

## LAY-06 — Ego-agent ablation

**Class:** Primary confirmatory  
**Variants:** Ego included and ego excluded.  
**Metrics:** Primary layout metrics and coverage statistics.  
**Unit:** Geographic tile.

## LAY-07 — Agent-class ablation

**Class:** Secondary confirmatory  
**Variants:** Vehicles only, pedestrians only when feasible, cyclists only when feasible, and all eligible classes.  
**Metrics:** Primary layout metrics.  
**Unit:** Geographic tile.

## LAY-08 — Localization-noise robustness

**Class:** Primary confirmatory  
**Hypotheses:** H3, H4, H5  
**Conditions:** Development-selected planar-noise levels.  
**Seeds:** Five per nonzero level.  
**Methods:** All primary layout methods.  
**Unit:** Geographic tile.

## LAY-09 — Missing-trajectory robustness

**Class:** Primary confirmatory  
**Hypotheses:** H3, H4  
**Perturbations:** Random trajectory removal, contiguous gaps, and directional imbalance.  
**Seeds:** Five per stochastic condition.  
**Unit:** Geographic tile.

## LAY-10 — Grade-separation study

**Class:** Secondary confirmatory  
**Conditions:** Without and with available elevation or separation cues.  
**Metrics:** False connection count, junction precision, OD connectivity agreement.  
**Unit:** Eligible crossing or tile.

## LAY-11 — Held-out-city evaluation

**Class:** Secondary confirmatory  
**Purpose:** Test geographic generalization.  
**Activation:** Only when approved before the frozen campaign and feasible within the resource budget.  
**Unit:** Geographic tile.

# 7. Runtime experiments

## RUN-01 — Replay determinism campaign

**Class:** Primary confirmatory  
**Hypothesis:** H6  
**Inputs:** Synthetic tapes, real tapes, and edited branches.  
**Conditions:** Repeated execution, separate processes, serialization round trip, and worker-count variation.  
**Metric:** Replay and query hash equality.  
**Pass condition:** No unexplained mismatch.  
**Unit:** Tape or scenario.

## RUN-02 — Replay scalability

**Class:** Primary confirmatory  
**Hypothesis:** H8  
**Initial workload targets:** 100, 1,000, and 10,000 active agents; optional 100,000 stored lines for offline query tests.  
**Metrics:** Agent updates per second, frame-time percentiles, peak RAM, peak VRAM.  
**Unit:** Workload.

## RUN-03 — Query benchmark

**Class:** Primary confirmatory  
**Hypotheses:** H6, H8

**Queries:** State at time, agent lookup, semantic filter, spatial filter, temporal event filter, corridor users, and branch comparison.

**Metrics:** p50/p95/p99 latency, result-hash equality, peak memory.  
**Unit:** Query workload.

## RUN-04 — Serialization and load benchmark

**Class:** Required diagnostic  
**Metrics:** Serialize time, deserialize time, artifact size, peak memory, and hash-verification time.  
**Unit:** Tape.

# 8. Editing experiments

## EDT-01 — Delay edit locality

**Class:** Primary confirmatory  
**Hypothesis:** H7  
**Comparison:** Symbolic delay versus equivalent raw-sample rewrite.  
**Metrics:** Modified records, symbols, bytes, latency, and continuity violations.  
**Unit:** Edit scenario.

## EDT-02 — Segment replacement locality

**Class:** Primary confirmatory  
**Hypothesis:** H7  
**Comparison:** Symbolic segment replacement versus raw-sample rewrite.  
**Metrics:** Edit locality and validity metrics.  
**Unit:** Edit scenario.

## EDT-03 — Corridor closure and rerouting

**Class:** Primary confirmatory  
**Hypotheses:** H7, H8

**Cases:** Alternate route exists, no route exists, multiple affected agents, and closure near a junction.

**Metrics:** Modified records and bytes, affected agents, rerouting success, closure violations, continuity violations, edit latency.  
**Unit:** Edit scenario.

## EDT-04 — Branch storage comparison

**Class:** Primary confirmatory  
**Hypothesis:** H7  
**Comparison:** Parent-reference-plus-edits branch versus complete copied raw representation.  
**Metrics:** Branch size, modified bytes, creation time, replay-hash stability.  
**Unit:** Branch scenario.

# 9. Resource experiments

## RES-01 — Pilot resource profile

**Class:** Required diagnostic  
**Hypothesis:** H8  
**Scope:** Data preparation through report generation on the pilot set.  
**Metrics:** Peak RAM, peak VRAM, disk use, stage wall time, output size, throughput.  
**Unit:** Pipeline stage and run.

## RES-02 — Interrupted-run resumability

**Class:** Primary confirmatory  
**Hypothesis:** H8

**Procedure:** Interrupt a multi-unit experiment, resume it, verify completed units are not recomputed, and verify final result consistency.

**Metrics:** Recomputed completed units, lost units, result-hash equality, recovery time.  
**Unit:** Experiment run.

## RES-03 — Frozen-campaign resource report

**Class:** Primary confirmatory  
**Hypothesis:** H8  
**Scope:** Complete frozen campaign.  
**Metrics:** Total wall time, peak RAM, peak VRAM, disk use, artifact volume, failed or retried runs, and uninterrupted job durations.  
**Unit:** Run and campaign.

# 10. Qualitative artifact experiments

## QUAL-01 — Motion reconstruction gallery

Includes low-error and high-error cases, multiple budgets, preserved events, missed events, and false events.

## QUAL-02 — Layout inference gallery

Includes multiple coverage levels, geometry success, topology success, sparse failure, false connection, repair success, and harmful repair.

## QUAL-03 — Replay and edit gallery

Includes deterministic replay, delay, segment replacement, closure, successful reroute, impossible reroute, and branch comparison.

Every qualitative artifact must link to the relevant unit-level result records.

# 11. Minimum frozen confirmatory campaign

The minimum campaign contains:

- MOT-01, MOT-03, MOT-07, MOT-08;
- LAY-02, LAY-03, LAY-04, LAY-06, LAY-08, LAY-09;
- RUN-01, RUN-02, RUN-03;
- EDT-01, EDT-02, EDT-03, EDT-04;
- RES-02, RES-03.

Removing an experiment requires explicit approval and a corresponding narrowing of the claim set.

# 12. Experiment dependencies

```text
Synthetic correctness
    ↓
Data adapters and canonical data
    ↓
Method validation
    ↓
Pilot experiments
    ↓
Parameter and protocol freeze
    ↓
Confirmatory campaign
    ↓
Statistical aggregation
    ↓
Qualitative selection from frozen records
    ↓
Final reports
```

# 13. Resource classes

- **Tiny:** seconds and negligible memory.
- **Small:** minutes and low memory.
- **Medium:** tens of minutes to a few hours.
- **Large:** several hours and resumable.
- **Very large:** a multi-session campaign that remains resumable and laptop-feasible.

Exact ceilings are defined in Batch 0.10.

# 14. Required outputs

Every experiment run must produce:

- experiment manifest;
- resolved configuration;
- unit-level status;
- raw metric records;
- resource observations;
- failure records;
- artifact references.

An experiment is not complete when only a plot or summary table exists.

# 15. Acceptance criteria

This document is acceptable when:

- every primary hypothesis maps to one or more confirmatory experiments;
- synthetic correctness precedes real-data evidence;
- methods, baselines, ablations, units, and outputs are explicit;
- stochastic experiments declare seeds;
- expensive campaigns are resumable;
- optional held-out-city work cannot silently become mandatory;
- qualitative outputs derive from recorded experiment results.
