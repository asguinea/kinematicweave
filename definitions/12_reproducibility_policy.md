# Reproducibility Policy

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.11 — Reproducibility, Testing, and Repository Policy  
**Status:** Draft for approval

## 1. Purpose

This policy defines the minimum information required to reproduce:

- dataset preparation;
- encoded tapes;
- inferred layouts;
- experiment runs;
- statistical analyses;
- figures and tables;
- qualitative artifacts.

The project must make every reported result traceable to code, configuration, input data, and raw records.

## 2. Reproducibility levels

### Level R0 — Unit reproducibility

A unit test or synthetic case produces the same expected result from a clean environment.

### Level R1 — Run reproducibility

A recorded experiment run can be rerun with the same:

- code revision;
- dependency lock;
- configuration;
- dataset version;
- split manifest;
- seeds;
- hardware mode.

### Level R2 — Result reproducibility

Raw result records can be regenerated and compared under the declared exact or numeric-equivalence rules.

### Level R3 — Artifact reproducibility

Figures, tables, reports, and qualitative artifacts can be regenerated from frozen raw or aggregated records.

The final repository must support R3 for all primary reported outputs.

## 3. Environment locking

The project uses:

- `pyproject.toml` for package and tool configuration;
- a committed dependency lockfile;
- explicit Python version support;
- documented system dependencies;
- optional CUDA dependencies isolated from the CPU core.

A fresh clone must not depend on an unrecorded global Python environment.

## 4. Run identity

Every experiment run records:

- run identifier;
- experiment identifier and version;
- Git commit;
- dirty-worktree status;
- resolved configuration;
- dataset and split identifiers;
- root and derived seed information;
- Python version;
- dependency-lock identity;
- machine metadata;
- start and end times;
- output paths;
- completion status.

## 5. Dirty-worktree policy

Confirmatory runs should use a clean worktree.

If a run is executed with uncommitted changes:

- `git_dirty` is recorded as true;
- the run is not considered release-grade;
- the exact diff should be captured where practical;
- the run must be repeated from a committed revision before final result freeze.

## 6. Configuration capture

Every run stores the fully resolved configuration after defaults, includes, and command-line overrides are applied.

The stored configuration must be sufficient to reproduce:

- method choice;
- parameters;
- budgets;
- seeds;
- splits;
- perturbations;
- metrics;
- output options;
- resource limits.

## 7. Seed capture

Randomness follows the deterministic seed-derivation policy.

Every run records:

- root seed;
- derived seed method;
- replicate count;
- per-unit seed when needed;
- separate streams for perturbation and method stochasticity.

## 8. Dataset provenance

Canonical dataset artifacts record:

- dataset identifier and version;
- adapter name and version;
- source release;
- source checksums when practical;
- canonical schema versions;
- preprocessing configuration;
- split manifest;
- exclusions and reasons.

The project must never rely on “latest” dataset content without an explicit version.

## 9. Result immutability

Raw result records are immutable after a run is complete.

Corrections require:

- a new run identifier;
- a new analysis or experiment version;
- preserved previous outputs;
- documented reason.

Aggregations and figures are regenerated from raw records and do not overwrite them.

## 10. Provenance chain

Every final artifact must be traceable through:

```text
Artifact
    ↓
Generating command
    ↓
Resolved configuration
    ↓
Aggregated records
    ↓
Raw metric records
    ↓
Method outputs
    ↓
Canonical dataset and split
    ↓
Source dataset version
    ↓
Git revision and locked environment
```

## 11. Reproduction modes

### 11.1 Smoke reproduction

A short workflow that:

- uses synthetic and tiny real fixtures;
- runs on CPU;
- completes quickly;
- verifies the end-to-end pipeline;
- regenerates representative small artifacts.

### 11.2 Reduced reproduction

A laptop-friendly subset that:

- uses the same code and metrics as the full campaign;
- produces representative quantitative and qualitative outputs;
- completes within a documented short runtime.

### 11.3 Full reproduction

The complete frozen campaign using the approved test splits and experiment matrix.

## 12. Reproduction commands

The final repository must provide documented commands for:

- environment setup;
- dataset preparation;
- split validation;
- smoke tests;
- pilot runs;
- full experiment families;
- statistical aggregation;
- figure and table generation;
- qualitative export;
- result verification.

Commands must avoid machine-specific absolute paths.

## 13. Artifact verification

Artifacts should support verification through:

- schema validation;
- expected row counts;
- content hashes where appropriate;
- run-manifest references;
- file-size checks;
- deterministic regeneration tests for small fixtures.

## 14. Dependency updates

A dependency update requires:

- lockfile update;
- full test suite;
- synthetic determinism checks;
- pilot comparison for metric-sensitive libraries;
- documentation when results may change.

Critical libraries include:

- NumPy;
- SciPy;
- Shapely/GEOS;
- PyArrow;
- Polars;
- scikit-learn;
- NetworkX;
- PyTorch when used;
- visualization libraries.

## 15. External tools

Any external executable used in the pipeline must have:

- version capture;
- installation instructions;
- deterministic invocation where possible;
- a documented fallback or explicit optional status.

## 16. Platform policy

The reference platform is Ubuntu under WSL2 on the ASUS laptop.

The project may support additional platforms, but final reproducibility claims are anchored to the recorded reference environment.

## 17. Release freeze

A release candidate requires:

- clean Git revision;
- committed lockfile;
- frozen definitions;
- frozen split manifests;
- frozen experiment and analysis versions;
- complete raw results;
- regenerated figures and reports;
- successful clean-clone smoke reproduction;
- documented known limitations.

## 18. Acceptance criteria

This policy is acceptable when:

- every reported artifact has a complete provenance chain;
- raw results are immutable;
- full resolved configurations are stored;
- datasets and adapters are versioned;
- seeds are reproducible;
- clean and dirty runs are distinguishable;
- smoke, reduced, and full reproduction modes are defined;
- dependency updates cannot silently change frozen results;
- final artifacts can be regenerated from recorded inputs.
