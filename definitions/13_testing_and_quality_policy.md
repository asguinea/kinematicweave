# Testing and Quality Policy

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.11 — Reproducibility, Testing, and Repository Policy  
**Status:** Draft for approval

## 1. Purpose

This policy defines the testing and quality obligations for all implementation batches.

A batch is not complete merely because code runs. It must include appropriate automated checks, documented limitations, and conformance with the approved definitions.

## 2. Test categories

### 2.1 Unit tests

Test isolated functions and classes.

Required for:

- coordinate transforms;
- interpolation;
- resampling;
- event detection;
- serialization helpers;
- metric formulas;
- graph operations;
- edit primitives.

### 2.2 Schema tests

Validate:

- required fields;
- types;
- nullability;
- enumerations;
- ordering;
- referential integrity;
- schema versions.

### 2.3 Property-based tests

Use generated inputs where valuable.

Expected targets:

- encode/decode invariants;
- angle wrapping;
- monotonically increasing timestamps;
- serialization stability;
- graph invariants;
- edit provenance;
- metric bounds.

### 2.4 Synthetic oracle tests

Use exact known-answer scenes and trajectories.

Required for:

- stops and turns;
- straight and curved motion;
- missing-data handling;
- corridor geometry;
- junction topology;
- grade separation;
- rerouting;
- deterministic replay.

### 2.5 Integration tests

Verify complete module paths such as:

- adapter → canonical records;
- trajectory → codec → decode → metric;
- trajectories → layout → graph → metric;
- tape → replay → query → hash;
- edit → branch → replay;
- raw results → statistics → figure-ready data.

### 2.6 Smoke experiments

Execute a small end-to-end real-data workflow using the frozen smoke set.

### 2.7 Regression tests

Protect:

- metric values;
- serialized schemas;
- deterministic hashes;
- CLI behavior;
- resource-sensitive paths;
- figure-ready aggregate data.

### 2.8 Performance tests

Measure selected stable workloads.

Performance tests are not ordinary unit tests and may be marked separately.

## 3. Test pyramid

The repository should contain:

- many fast unit and schema tests;
- a moderate number of synthetic and integration tests;
- a small number of smoke and performance tests;
- full campaign validation outside ordinary CI.

## 4. Minimum batch obligation

Every implementation batch must:

- add tests for new behavior;
- update affected tests;
- run relevant focused tests;
- run the standard quality suite;
- report any skipped or failing tests;
- document untested behavior.

A documentation-only batch may omit code tests but must validate internal links and file consistency where tooling exists.

## 5. Correctness standards

### 5.1 Exact checks

Use exact equality for:

- identifiers;
- timestamps;
- symbolic ordering;
- canonical bytes;
- hashes;
- graph references;
- manifest fields.

### 5.2 Numeric checks

Use named tolerances for:

- decoded positions;
- headings;
- geometry distances;
- metric regressions;
- statistical outputs.

Tests must not introduce arbitrary inline tolerances without a documented reason.

### 5.3 No snapshot-only validation

Snapshot or golden tests may supplement but not replace semantic assertions.

## 6. Determinism tests

Required determinism coverage includes:

- repeated serialization;
- repeated replay;
- separate process execution;
- worker-count variation where supported;
- stable seed derivation;
- stable query ordering;
- serialization round trips.

## 7. Metric tests

Every metric implementation requires:

- hand-computed known examples;
- perfect-match case;
- complete-miss case;
- empty or undefined case;
- invalid-input case;
- ordering or matching tie case;
- bounds check;
- unit check.

## 8. Codec tests

Every codec requires:

- interface conformance;
- encode/decode test;
- serialization round trip;
- byte-count agreement;
- decoding without source access;
- irregular timestamp handling;
- support-boundary behavior;
- invalid payload rejection.

## 9. Layout tests

Layout inference and graph construction require tests for:

- parallel paths;
- merging and splitting;
- crossing without connectivity;
- grade separation;
- disconnected components;
- endpoint clustering;
- false-link prevention;
- repair-rule behavior;
- stable graph identifiers.

## 10. Runtime and edit tests

Required cases:

- state at exact and interpolated times;
- outside-support query;
- empty query result;
- canonical query ordering;
- delay;
- segment replacement;
- corridor closure;
- successful reroute;
- impossible reroute;
- branch provenance;
- failed edit rollback or rejection.

## 11. Statistical tests

The analysis layer requires tests for:

- bootstrap reproducibility;
- paired-difference orientation;
- confidence-interval shape and bounds;
- Holm correction;
- missing-pair handling;
- nested tile/seed aggregation;
- event micro and macro averaging;
- exact determinism summaries.

## 12. Resource tests

Resource-sensitive tests should verify:

- bounded batch sizes;
- worker limits;
- resumability;
- atomic result writing;
- no recomputation of completed units;
- low-disk refusal;
- CPU fallback;
- GPU optionality.

## 13. Test data

Test data must be:

- small;
- deterministic;
- license-compatible;
- clearly synthetic unless redistribution is approved;
- versioned;
- easy to regenerate.

Large external datasets are never required for the ordinary unit suite.

## 14. Golden artifacts

Golden artifacts may be used for:

- canonical JSON;
- tiny Parquet schemas;
- replay hashes;
- synthetic figure-ready data;
- small serialized tapes.

Golden files must include:

- format version;
- generator command;
- update procedure;
- reviewable changes.

## 15. Continuous integration

Hosted CI should run, where practical:

- installation from lockfile;
- formatting check;
- linting;
- static typing;
- unit tests;
- schema tests;
- synthetic oracle tests;
- a minimal integration test.

GPU and large-data tests are not required in hosted CI.

## 16. Local quality command

The repository should provide one standard command that runs:

- formatting check;
- lint;
- type check;
- unit tests;
- selected integration tests.

The exact command is defined during Phase 1.

## 17. Failure policy

A batch cannot be marked PASS when:

- required tests fail;
- new behavior has no relevant tests;
- definitions are violated;
- known data corruption is ignored;
- deterministic outputs change without explanation;
- resource limits are knowingly exceeded.

Allowed statuses:

- PASS;
- PARTIAL;
- BLOCKED;
- FAIL.

## 18. Coverage policy

Line coverage is a diagnostic measure, not the goal.

Priority is given to:

- important branches;
- invalid inputs;
- boundary conditions;
- scientific metric correctness;
- deterministic behavior.

A minimum coverage threshold may be introduced after the repository stabilizes.

## 19. Static typing

Public interfaces and persistent domain objects require type annotations.

Type checking should be strict enough to catch:

- nullable-field mistakes;
- identifier confusion;
- array shape misuse where expressible;
- incompatible configuration types;
- missing return handling.

## 20. Linting and formatting

Formatting is automated.

Lint rules should catch:

- unused code;
- shadowed names;
- unsafe mutable defaults;
- broad exception swallowing;
- nondeterministic set/dict iteration in canonical paths;
- suspicious numeric comparisons;
- accidental debug output.

## 21. Scientific quality review

Before a full campaign:

- metric formulas are reviewed;
- test splits are validated;
- baselines are runnable;
- failure handling is exercised;
- raw result schemas are validated;
- statistical scripts are tested on synthetic records;
- plots are checked against source data.

## 22. Acceptance criteria

This policy is acceptable when:

- each module has a defined test responsibility;
- synthetic oracle tests cover the central claims;
- deterministic behavior is tested explicitly;
- metrics have known-answer cases;
- codecs cannot hide source access or incorrect byte counts;
- layout topology tests include false crossings and grade separation;
- resumability and atomic writes are tested;
- CI remains lightweight;
- failed required tests prevent a PASS status.
