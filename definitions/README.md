# KinematicWeave Definitions

## Purpose

This directory is the authoritative design and research specification for the KinematicWeave project.

The implementation, tests, experiments, reports, and visualizations must conform to the approved definitions recorded here. When source code and a definition disagree, the definition takes precedence until the discrepancy is reviewed and an explicit decision is recorded.

The definitions are intended to make the project:

- scientifically disciplined;
- reproducible;
- implementable in small, auditable batches;
- suitable for the ASUS ROG Zephyrus G14 reference laptop;
- resistant to accidental scope growth;
- traceable from research claim to reported result.

## Source concept

The project operationalizes the concept proposed in the draft:

> KinematicWeave: Symbolic Layout and Procedural Replay for Spatio-Temporal Scene Modeling

The implementation focuses on the draft's central ideas:

1. separating persistent layout from dynamic motion;
2. representing moving entities as symbolic procedural lines;
3. using repeated motion to infer persistent spatial infrastructure;
4. enabling deterministic replay, querying, editing, branching, and what-if variation.

This directory does not reproduce the draft. It translates the concept into precise engineering and evaluation definitions.

## Authority and change control

The files in `definitions/` are design inputs, not generated documentation.

Changes to an approved definition must be:

1. intentional;
2. reviewed;
3. reflected in the decision log once that file exists;
4. propagated to affected tests, experiments, and reports.

Codex must not silently alter research claims, evaluation rules, data contracts, metric definitions, or scope boundaries.

## Reading order

During Phase 0, read the files in numeric order. The initial Batch 0.1 files are:

1. `00_project_charter.md`
2. `02_scope_and_non_goals.md`
3. `glossary.md`

Additional Phase 0 batches will add the remaining definitions and integration documents.

The permanent empirical acceptance rule is:

- `20_empirical_evidence_gate.md` — required external, provider-data, and
  measured-hardware evidence for claims, phases, and milestones.

## Current project state

- **Phase:** 2 — Canonical Data Foundation
- **Current batch:** 2.15 — AV2 Acquisition and Real-Provider Evidence Gate
- **Implementation status:** Phase 2 complete
- **Design-freeze status:** Phase 0 definitions frozen; accepted decisions apply
- **Milestone status:** M2 achieved with genuine AV2 provider-data evidence

## Rules for implementation agents

Until Phase 0 is complete:

- do not create the production implementation;
- do not introduce dependencies;
- do not decide schemas, metrics, or experiment protocols in code;
- do not infer missing research decisions;
- do not treat examples as final contracts.

After Phase 0 is complete, every implementation batch must begin by reading the relevant files in this directory.

## Definition hierarchy

The intended hierarchy is:

```text
Project charter and scope
    ↓
Research claims
    ↓
Architecture and domain model
    ↓
Data and serialization contracts
    ↓
Dataset and evaluation protocol
    ↓
Experiment matrix and statistics
    ↓
Resource, testing, and reproducibility policies
    ↓
Visualization and reporting rules
    ↓
Implementation batches
```

## Batch 0.1 acceptance checklist

Batch 0.1 is acceptable when:

- the project mission is explicit;
- the initial scientific contribution is bounded;
- the target evidence is defined;
- the laptop constraint is treated as a design requirement;
- primary and optional work are separated;
- 2.5D and full-3D claims are distinguished;
- non-goals are explicit;
- central terms used in these documents are defined in `glossary.md`.
