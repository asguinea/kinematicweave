# Codex Batch Protocol

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.13 — Codex Batch and Reporting Protocol  
**Status:** Draft for approval

## 1. Purpose

This document defines how Codex must execute every implementation batch after Phase 0.

The protocol exists to keep implementation:

- scoped;
- auditable;
- testable;
- aligned with `definitions/`;
- easy to review;
- easy to correct without starting unrelated work.

## 2. Authority

The approved files in `definitions/` are the source of truth.

When code and definitions disagree, Codex must:

1. stop expanding the conflicting implementation;
2. report the conflict;
3. avoid silently changing the design;
4. wait for an approved correction or definition update.

## 3. Batch identity

Every implementation request must include:

- phase number;
- batch number;
- batch name;
- objective;
- allowed scope;
- required deliverables;
- required tests;
- acceptance criteria.

Codex must repeat the batch number and name at the beginning of its work and in the final report.

## 4. Before implementation

Codex must:

1. inspect the current repository;
2. read all definition files relevant to the batch;
3. identify existing code and tests affected by the batch;
4. note any ambiguity or contradiction;
5. avoid implementation when a critical design decision is missing.

Codex should not ask unnecessary questions when the repository and definitions already provide the answer.

## 5. Scope discipline

Codex must implement only the approved batch.

It may make small supporting changes when they are necessary for the batch, but must report them.

Codex must not:

- begin the next batch;
- add unrelated features;
- refactor unrelated modules;
- change research claims;
- change primary metrics;
- change dataset or split rules;
- add heavyweight dependencies without justification;
- introduce C++ or GPU-only code without approval;
- alter files in `definitions/` unless explicitly instructed.

## 6. Implementation rules

Codex must:

- preserve public behavior outside the batch scope;
- follow repository conventions;
- use typed public interfaces;
- add validation where required by the contracts;
- keep dataset-specific logic inside adapters;
- preserve deterministic ordering;
- keep long-running operations resumable where required;
- write machine-readable outputs where specified.

## 7. Testing rules

Every implementation batch must include appropriate tests.

At minimum, Codex must:

- add or update focused tests for new behavior;
- run the focused tests;
- run the standard repository quality checks available at that time;
- report skipped, failed, or unavailable checks;
- avoid claiming PASS when required tests fail.

Where relevant, tests should cover:

- success cases;
- boundary cases;
- invalid inputs;
- deterministic behavior;
- serialization round trips;
- known-answer synthetic cases;
- regression behavior.

## 8. Dependency rules

Before adding a dependency, Codex must verify that it:

- is necessary for the batch;
- does not duplicate an existing dependency without reason;
- supports the reference environment;
- has compatible licensing;
- fits the laptop resource constraints.

Every new dependency must be listed and justified in the final report.

## 9. File-change rules

Codex must clearly distinguish:

- files added;
- files modified;
- files deleted;
- generated files.

Codex must not delete or overwrite user work without explicit instruction.

Generated data, caches, and large artifacts must not be committed unless the batch requires them.

## 10. Definition conformance

Before finishing, Codex must check the implementation against the relevant definitions.

The report must state:

- which definition files were consulted;
- whether the implementation conforms;
- any deviations;
- any unresolved ambiguity.

## 11. Batch status values

The final status must be one of:

- **PASS** — all required work and tests completed successfully;
- **PARTIAL** — useful work completed, but some required items remain;
- **BLOCKED** — work cannot continue because of a missing dependency, decision, environment issue, or contradiction;
- **FAIL** — implementation or tests failed materially.

Codex must not use PASS when:

- required tests fail;
- required deliverables are missing;
- the implementation violates definitions;
- unresolved blockers remain.

## 12. Failure handling

When a problem appears, Codex should:

1. identify the smallest reproducible cause;
2. inspect relevant logs and tests;
3. fix issues within the current batch scope when possible;
4. avoid broad speculative changes;
5. report unresolved failures clearly.

If the batch cannot be completed safely, Codex must stop with PARTIAL or BLOCKED rather than improvising a new design.

## 13. Commands and evidence

Codex must report the commands it executed for:

- installation;
- formatting;
- linting;
- type checking;
- tests;
- smoke runs;
- benchmarks;
- artifact generation.

Command output does not need to be copied in full, but the result must be summarized accurately.

## 14. Resource reporting

When the batch includes performance-sensitive or long-running work, Codex must report relevant observations such as:

- runtime;
- peak RAM;
- peak VRAM;
- disk use;
- worker count;
- batch size;
- whether resumability was exercised.

For ordinary small batches, “Not measured; not required for this batch” is acceptable.

## 15. Design deviations

A design deviation is any implementation choice that differs from an approved definition.

Codex must not hide deviations.

Each deviation report must include:

- affected definition;
- implemented behavior;
- reason;
- impact;
- recommended resolution.

## 16. Outstanding issues

Every known issue must be reported.

Examples:

- unimplemented edge case;
- skipped platform test;
- temporary fallback;
- known performance limitation;
- incomplete documentation;
- unresolved dependency warning.

“None” is acceptable only when there are genuinely no known outstanding issues within the batch scope.

## 17. Review workflow

After Codex finishes:

1. the user and assistant review the report and repository state;
2. the batch is accepted or issues are identified;
3. fixes are performed under the same batch number;
4. only after acceptance does planning move to the next batch.

## 18. Fix iterations

A fix prompt must retain:

- the same batch number;
- the same batch name;
- the original acceptance criteria;
- a list of observed issues;
- a requirement to update tests;
- the same final report format.

Codex must not rename a failed batch into a new batch merely to avoid fixing it.

## 19. Commit policy

Unless explicitly asked, Codex should not create commits.

When commits are requested:

- use a clear batch-specific commit message;
- avoid mixing unrelated changes;
- do not rewrite existing history;
- report the resulting commit identifier.

## 20. Stop condition

Codex must stop after:

- completing the approved scope;
- running required checks;
- producing the final report.

It must not continue into the next batch.

## 21. Acceptance criteria

This protocol is acceptable when:

- every Codex task has a clear batch identity;
- Codex reads relevant definitions before implementation;
- scope growth is prohibited;
- required tests determine PASS status;
- deviations and blockers remain visible;
- the same batch number is used for fixes;
- new dependencies are justified;
- Codex stops before beginning the next batch;
- final reports are detailed enough to review the work efficiently.
