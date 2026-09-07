# Coding and Repository Conventions

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.11 — Reproducibility, Testing, and Repository Policy  
**Status:** Draft for approval

## 1. Purpose

This document defines repository structure, coding conventions, dependency rules, artifact handling, and implementation discipline.

## 2. Repository layout

```text
kinematicweave/
├── definitions/
├── configs/
├── src/
│   └── kinematicweave/
├── tests/
├── scripts/
├── experiments/
├── data/
├── results/
├── figures/
├── qualitative/
├── reports/
├── pyproject.toml
├── uv.lock
├── README.md
└── LICENSE
```

## 3. Directory responsibilities

### `definitions/`

Authoritative design specifications.

Implementation agents must not rewrite these files unless the batch explicitly authorizes it.

### `configs/`

Versioned configuration files for:

- datasets;
- methods;
- experiments;
- metrics;
- visualization;
- resource limits.

### `src/kinematicweave/`

Production Python package.

### `tests/`

Automated tests and small fixtures.

### `scripts/`

Thin convenience wrappers only.

Business logic must live in the package.

### `experiments/`

Experiment declarations and sweep definitions.

### `data/`

Local source, canonical, cache, and split artifacts.

Large contents are normally ignored by Git.

### `results/`

Raw and aggregated machine-readable experiment outputs.

### `figures/`

Generated quantitative figures.

### `qualitative/`

Generated images, animations, and viewer exports.

### `reports/`

Experiment, failure, hardware, and reproducibility reports.

## 4. Python version and packaging

The project uses:

- one declared supported Python version range;
- `pyproject.toml`;
- `src/` package layout;
- `uv` for environment and lockfile management unless an approved decision changes it;
- editable installs only for development.

## 5. Naming conventions

### 5.1 Python

- modules and functions: `snake_case`;
- classes: `PascalCase`;
- constants: `UPPER_SNAKE_CASE`;
- private members: leading underscore;
- type aliases: `PascalCase`.

### 5.2 Identifiers

Persistent entity identifiers follow the stable textual conventions defined in the coordinate and domain documents.

### 5.3 Files

- definitions retain their approved numeric prefixes;
- configs use lowercase descriptive names;
- generated artifacts include experiment or run identifiers where practical;
- no spaces in machine-generated filenames.

## 6. Public API conventions

Public functions and classes require:

- type annotations;
- concise docstrings;
- explicit units in names or documentation;
- declared error behavior;
- deterministic ordering where returning collections;
- no hidden global configuration.

## 7. Data structures

Prefer:

- immutable dataclasses for domain value objects;
- NumPy arrays for dense numeric trajectory data;
- Polars/PyArrow for tabular processing;
- Shapely geometries for planar geometry;
- NetworkX for initial graph algorithms;
- typed configuration objects.

Avoid:

- lists of tiny mutable point objects in hot paths;
- untyped dictionaries for central domain objects;
- implicit tuple field meaning;
- global mutable registries without controlled initialization.

## 8. Error handling

Use explicit exception types for:

- validation errors;
- unsupported schema versions;
- codec failures;
- geometry failures;
- experiment failures;
- resource-limit failures.

Do not:

- swallow broad exceptions;
- silently coerce invalid records;
- continue after scientific validity is compromised;
- return ambiguous sentinel values.

Expected absence may use `None` or a typed result status according to the contract.

## 9. Logging

Use structured logging through one project-owned interface.

Logs should include relevant identifiers such as:

- run;
- experiment;
- scenario;
- tile;
- method;
- stage.

Do not use `print()` for library diagnostics.

Command-line user output may use a presentation layer.

## 10. Configuration

Configuration must be:

- explicit;
- typed where practical;
- serializable;
- versioned;
- saved after resolution;
- free of hidden environment-dependent defaults.

Secrets must not appear in committed configuration.

## 11. Paths

Use `pathlib.Path`.

Persistent manifests store:

- repository-relative paths;
- run-relative paths;
- or logical artifact identifiers.

Avoid machine-specific absolute paths in committed files and hashes.

## 12. Numeric code

Numeric algorithms must:

- document input shape;
- document units;
- validate finite values;
- avoid hidden dtype conversion;
- use wrapped angle operations;
- use timestamp-aware differences;
- avoid unbounded dense pairwise matrices.

## 13. Deterministic collections

Canonical paths must not depend on:

- set iteration order;
- dictionary insertion accidents;
- filesystem listing order;
- process scheduling.

Sort explicitly using stable identifiers.

## 14. Dependency policy

A new dependency is acceptable when it:

- materially reduces implementation risk or complexity;
- is actively maintained;
- supports the reference platform;
- has compatible licensing;
- fits the resource budget;
- does not duplicate an existing dependency without reason.

Every dependency addition must be justified in the implementing batch report.

## 15. Preferred core dependencies

Expected initial dependencies include:

- NumPy;
- SciPy;
- Polars;
- PyArrow;
- Shapely;
- scikit-learn;
- NetworkX;
- Matplotlib;
- pytest;
- static typing and linting tools;
- Rerun for qualitative visualization.

PyTorch is optional unless a specific workload justifies it.

## 16. C++ and native code policy

Native code is not part of the initial foundation.

It may be introduced only when:

- a complete Python implementation exists;
- end-to-end profiling identifies a bottleneck;
- the optimized kernel has a stable Python interface;
- Python/native equivalence tests are added;
- the build works in the reference environment;
- the resource benefit is measured.

Preferred tools:

- C++20;
- CMake;
- pybind11.

## 17. GPU code policy

Core APIs must not require GPU tensors.

GPU use must be:

- behind explicit configuration;
- optional;
- reproducible where used;
- memory-bounded;
- accompanied by CPU fallback for the core path.

## 18. Generated artifacts

Generated artifacts must not be edited manually to change scientific content.

Figures and tables are generated from recorded data.

If manual annotation is required for release, both the source artifact and annotation process must be documented.

## 19. Git policy

Commit:

- source;
- tests;
- definitions;
- configs;
- small fixtures;
- documentation;
- lightweight manifests;
- selected aggregated results when appropriate.

Do not commit:

- full datasets;
- credentials;
- caches;
- virtual environments;
- transient logs;
- large model weights without explicit approval;
- disposable experiment intermediates.

## 20. Large-file policy

Before adding a large artifact, determine:

- whether it is reproducible;
- whether it is required for review;
- whether licensing permits redistribution;
- whether a manifest or download script is better;
- whether Git LFS or release storage is appropriate.

## 21. Documentation policy

Required documentation includes:

- public API docstrings;
- README usage;
- dataset setup;
- experiment commands;
- result interpretation;
- failure modes;
- resource expectations.

Comments should explain why, not restate obvious code.

## 22. Script policy

Scripts should:

- parse arguments;
- call package APIs;
- return meaningful exit codes;
- avoid duplicate algorithm implementations;
- record invoked configuration.

## 23. CLI policy

CLI commands must:

- support `--help`;
- validate configuration before expensive work;
- show planned workload for large runs;
- fail clearly;
- avoid destructive defaults;
- produce machine-readable manifests.

## 24. Backward compatibility

During pre-release development, public APIs may evolve through approved batches.

Persistent schemas require explicit versioning and migration.

Breaking changes after result freeze require:

- a new schema or method version;
- migration or preserved old reader;
- regenerated affected results.

## 25. Batch discipline

Codex must:

- implement only the approved batch;
- avoid opportunistic refactors outside scope;
- read relevant definitions first;
- preserve existing passing behavior;
- add tests;
- report deviations;
- stop before beginning the next batch.

## 26. Acceptance criteria

This document is acceptable when:

- repository directories have clear ownership;
- business logic cannot accumulate in scripts;
- public interfaces are typed and documented;
- persistent paths and identifiers are stable;
- dependency additions require justification;
- native and GPU code remain optional until justified;
- generated scientific artifacts are reproducible;
- large data and caches remain outside normal Git history;
- batch implementation remains disciplined and auditable.
