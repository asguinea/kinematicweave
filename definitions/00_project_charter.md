# Project Charter

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.1 — Project Charter and Research Scope  
**Status:** Draft for approval

## 1. Mission

Build a reproducible research system that evaluates whether dynamic environments can be represented as a compact, persistent, symbolic combination of:

1. a structured spatial layout; and
2. procedural, time-indexed motion programs for dynamic entities.

The system must produce sound preliminary empirical evidence using only resources available on the reference ASUS ROG Zephyrus G14 laptop.

## 2. Research problem

Common representations of dynamic scenes emphasize one or more of the following:

- visual fidelity;
- dense geometry;
- raw sensor storage;
- frame-by-frame state;
- task-specific simulation state.

These representations can be difficult to:

- edit semantically;
- replay deterministically;
- query at a symbolic level;
- branch into what-if alternatives;
- compactly reuse across scenarios;
- connect repeated motion to persistent spatial structure.

The KinematicWeave project investigates a different abstraction: a scene representation in which motion and layout are explicit, structured, serializable, and manipulable.

## 3. Core proposition

A useful dynamic-scene representation can be formed by combining:

- a persistent layout model containing corridors, paths, junctions, access zones, and related structure; and
- procedural lines that encode the movement and semantic events of individual agents over time.

Repeated motion traces may also provide evidence about the persistent layout itself. The representation should support replay, queries, edits, branches, and controlled variation without requiring a photorealistic reconstruction.

## 4. Definition of the reference KinematicWeave

For this project, a **KinematicWeave** is a versioned, serializable, deterministic representation of a dynamic scene containing:

- scene and coordinate metadata;
- a persistent layout representation;
- a connectivity representation;
- time-indexed procedural lines for dynamic agents;
- semantic events associated with those lines;
- branch and edit provenance;
- sufficient information to reconstruct supported scene states and answer supported queries.

The initial reference implementation is allowed to operate primarily in 2.5D urban scenes. It must not depend on photorealistic rendering.

## 5. Primary project objectives

### O1 — Procedural motion representation

Design and implement a common codec interface for raw trajectories, simple compression baselines, and event-aware procedural lines.

### O2 — Quantitative motion evaluation

Measure fidelity, semantic-event preservation, serialized size, and computational cost under fair matched-budget comparisons.

### O3 — Motion-derived layout inference

Infer corridor geometry, junctions, and connectivity from aggregated trajectories without using the evaluation map as an inference input.

### O4 — Quantitative layout evaluation

Measure both geometric and topological agreement with known layout data, including behavior under reduced trajectory coverage and noise.

### O5 — Symbolic runtime

Implement deterministic replay, semantic queries, branching, local edits, corridor closure, and supported rerouting.

### O6 — Laptop feasibility

Demonstrate that the core evaluation is practical within the RAM, VRAM, storage, and runtime limits of the reference laptop.

### O7 — Reproducible research artifact

Produce a repository in which every reported number and qualitative artifact can be traced to a configuration, seed, code revision, input subset, and raw result record.

## 6. Primary research questions

### RQ1 — Fidelity and compactness

At matched serialized size or matched control-point budget, how well do event-aware procedural lines preserve trajectory geometry and motion events relative to simpler representations?

### RQ2 — Infrastructure from motion

Under what trajectory coverage and noise conditions can repeated motion recover useful corridor geometry, junctions, and connectivity?

### RQ3 — Value of symbolic structure

Does grammar- or rule-based topology repair improve inferred connectivity over geometry-only clustering and centerline extraction?

### RQ4 — Replay and manipulation

Can the representation replay deterministically and support localized semantic edits and branches?

### RQ5 — Practicality

What scene sizes and experiment scales are feasible on the reference laptop, and what are the measured CPU, GPU, memory, storage, and latency costs?

## 7. Required evidence

The project must produce quantitative and qualitative evidence.

### Quantitative evidence

At minimum:

- trajectory error versus serialized size;
- semantic event precision, recall, and F1;
- encoding and decoding costs;
- layout geometry metrics;
- layout topology and routing metrics;
- coverage and robustness curves;
- deterministic replay verification;
- query and edit latency;
- memory, VRAM, disk, and throughput measurements;
- paired statistical analysis with uncertainty estimates.

### Qualitative evidence

At minimum:

- raw and reconstructed motion overlays;
- procedural control points and semantic events;
- inferred and ground-truth layout overlays;
- graph and grammar visualizations;
- branch and edit comparisons;
- representative successes;
- representative failures.

Qualitative examples may explain results but may not substitute for quantitative tests of primary claims.

## 8. Preliminary-evidence standard

The project aims to provide **sound preliminary empirical evidence**, not final proof of all possible forms of the concept.

Evidence is considered sound when:

- the evaluated claim is precisely stated;
- the experimental unit is appropriate;
- baselines receive equivalent inputs;
- storage is measured from reconstructible serialized forms;
- development and test data are separated;
- stochastic methods use recorded seeds;
- uncertainty and effect sizes are reported;
- failed cases are not silently removed;
- code and configurations are reproducible;
- conclusions are limited to the evaluated domain.

## 9. Reference hardware requirement

The core project must be designed for the user's ASUS ROG Zephyrus G14 configuration with:

- 32 GB system RAM;
- 1 TB SSD;
- an NVIDIA RTX 5070 laptop GPU;
- a modern AMD Ryzen 9-class CPU.

The hardware specification is a research constraint, not merely a deployment preference.

The final resource policy will define exact ceilings. Until then, the design must follow these principles:

- the core pipeline must be CPU-capable;
- GPU use must be optional or justified;
- processing must be chunked or streamed;
- expensive experiments must be resumable;
- no required component may assume datacenter-scale VRAM or RAM;
- no required experiment may depend on training a large neural model;
- dataset acquisition must be bounded to fit comfortably on the SSD.

## 10. Intended users

The research artifact is intended for:

- graphics and visualization researchers;
- simulation and digital-twin researchers;
- trajectory and map-inference researchers;
- developers of interactive scene-authoring tools;
- researchers investigating symbolic scene representations;
- readers assessing the proposed concept.

The initial repository is a research system, not a polished commercial application.

## 11. Intended use cases

The implementation should support evidence-relevant forms of:

- trajectory compression and reconstruction;
- semantic event inspection;
- persistent scene replay;
- selective replay by agent class or query;
- inferred corridor and junction inspection;
- route closure and supported rerouting;
- what-if branches;
- compact scene-state archival;
- reproducible qualitative visualization.

## 12. Deliverables

The completed project should contain:

- approved design definitions;
- a tested Python package;
- deterministic command-line workflows;
- canonical processed-data schemas;
- motion representation baselines;
- procedural-line codecs;
- layout-inference methods and baselines;
- a symbolic replay and edit runtime;
- reproducible experiment configurations;
- per-unit raw results;
- statistical analyses;
- release-quality figures and tables;
- qualitative examples and failure cases;
- hardware and resource reports;
- a full reproduction guide.

## 13. Success conditions

The project is successful when the repository can support appropriately scoped conclusions about:

1. the fidelity–compactness behavior of procedural lines;
2. the preservation of selected semantic motion events;
3. the recoverability of selected layout geometry and topology from repeated trajectories;
4. the contribution of symbolic topology repair;
5. deterministic replay and semantic edit locality;
6. practical execution on the reference laptop.

A hypothesis does not have to be supported for the project to be scientifically successful. A clear negative result, boundary condition, or failure analysis is acceptable when the evaluation is sound and reproducible.

## 14. Claim boundaries

The initial project may claim evidence only for the datasets, scene types, agent types, coordinate assumptions, event definitions, and metrics actually evaluated.

The initial project must not imply that it has demonstrated:

- arbitrary full-3D scene understanding;
- robust raw-video perception;
- photorealistic reconstruction;
- general causal prediction of human behavior;
- safety-critical autonomous-driving validity;
- universal urban map reconstruction;
- superiority over appearance representations on appearance tasks.

## 15. Governance

Phase 0 design work is approved by the user before implementation begins.

After design freeze:

- Codex implements only approved batches;
- deviations are reported rather than silently adopted;
- changes to research design require explicit approval;
- raw results remain immutable after generation;
- figures and tables are regenerated from recorded results;
- optional work cannot block the critical evaluation path.

## 16. Phase 0.1 completion condition

This charter is complete when it is consistent with:

- `02_scope_and_non_goals.md`;
- `glossary.md`;
- the user's laptop and research constraints;
- the planned Phase 0 evidence and architecture definitions.

Later Phase 0 batches may refine terminology and traceability, but they should not expand the mission without an explicit decision.
