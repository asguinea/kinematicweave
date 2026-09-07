# System Architecture and Component Boundaries

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.3 — System Architecture and Component Boundaries  
**Status:** Draft for approval

## 1. Purpose

This document defines the reference architecture for the KinematicWeave research system.

Its goals are to:

- keep research concerns separate from dataset-specific code;
- make experiments reproducible and auditable;
- support a Python-first implementation;
- allow selective C++ acceleration later without changing public interfaces;
- keep the qualitative viewer outside the scientific core;
- ensure every later implementation batch has a clear module owner.

## 2. Architectural principles

### 2.1 Python-first

The reference implementation is Python-first.

Python owns:

- orchestration;
- data contracts;
- experiments;
- statistics;
- reporting;
- visualization integration;
- the initial runtime implementation.

C++ or CUDA may be introduced only for profiled bottlenecks and must remain behind stable Python interfaces.

### 2.2 Canonical internal contracts

External dataset structures must be converted into project-owned canonical contracts before entering the rest of the system.

No downstream module may depend directly on Argoverse, nuScenes, or another dataset's native object model.

### 2.3 Separation of scientific core and viewer

The scientific core must run headlessly.

Visualization may consume generated artifacts, but:

- metrics must not depend on viewer state;
- experiments must not require manual interaction;
- qualitative outputs must be reproducible from stored artifacts.

### 2.4 Immutable raw results

Raw experiment outputs are append-only and immutable.

Aggregation, statistics, tables, and figures are derived products and must never overwrite raw results.

### 2.5 Configuration-driven experiments

Experiment behavior must be defined by versioned configuration files rather than hidden constants.

Each run records:

- configuration;
- random seeds;
- input split;
- code revision;
- hardware and software metadata;
- produced artifacts.

### 2.6 Optional components cannot block the core path

The following are optional and must remain isolated:

- perception pipelines;
- game-engine integrations;
- native runtime acceleration;
- user-study tooling;
- photorealistic rendering.

## 3. High-level system diagram

```text
External datasets
    │
    ▼
Dataset adapters
    │
    ▼
Canonical data layer
    ├── trajectories
    ├── agents
    ├── vector maps
    └── scenario metadata
    │
    ├───────────────────────────────┐
    ▼                               ▼
Motion representation          Layout inference
    ├── raw samples                ├── density baseline
    ├── uniform sampling           ├── clustering
    ├── RDP                        ├── centerlines
    ├── splines                    ├── junctions
    └── procedural lines           ├── connectivity
                                   └── grammar repair
    │                               │
    └───────────────┬───────────────┘
                    ▼
               KinematicWeave model
                    │
        ┌───────────┼─────────────┐
        ▼           ▼             ▼
     Runtime      Evaluation   Visualization
     replay       metrics      overlays
     queries      statistics   timelines
     edits        reports      galleries
     branches
```

## 4. Reference package structure

```text
src/
└── kinematicweave/
    ├── data/
    ├── domain/
    ├── trajectory/
    ├── events/
    ├── codecs/
    ├── grammar/
    ├── layout/
    ├── runtime/
    ├── editing/
    ├── metrics/
    ├── experiments/
    ├── statistics/
    ├── visualization/
    ├── reporting/
    └── native/
```

The exact file layout may evolve, but the module responsibilities and dependency direction are authoritative.

## 5. Module responsibilities

### 5.1 `kinematicweave.data`

Owns:

- external dataset adapters;
- canonical data loading and writing;
- split manifests;
- scenario indexing;
- Parquet and JSON I/O;
- checksums and cache metadata;
- synthetic data fixtures.

Must not own:

- metrics;
- codecs;
- layout inference;
- runtime logic.

### 5.2 `kinematicweave.domain`

Owns:

- canonical in-memory domain objects;
- identifiers;
- enums;
- coordinate and time types;
- validation rules;
- shared immutable value objects.

Must not own:

- dataset-specific logic;
- algorithms;
- experiments.

### 5.3 `kinematicweave.trajectory`

Owns:

- trajectory validation;
- resampling;
- kinematic feature computation;
- geometric utilities;
- trajectory partitioning primitives.

Must not own:

- semantic event policy;
- codec storage formats;
- dataset adapters.

### 5.4 `kinematicweave.events`

Owns:

- semantic event definitions;
- event detectors;
- event matching;
- event validation;
- event-related ablations.

Initial event classes are expected to include:

- stop;
- turn.

Additional event classes are secondary scope unless approved later.

### 5.5 `kinematicweave.codecs`

Owns:

- common codec interfaces;
- raw-sample representation;
- uniform-subsampling baseline;
- RDP baseline;
- spline baseline;
- event-free procedural lines;
- event-aware procedural lines;
- encode/decode logic;
- codec serialization hooks;
- codec size accounting hooks.

Every codec must expose equivalent public operations.

### 5.6 `kinematicweave.grammar`

Owns:

- grammar node and relation types;
- layout hierarchy;
- grammar validation;
- topology-repair rules;
- oracle conversion from known maps;
- grammar serialization.

It does not own trajectory clustering or geometric centerline extraction.

### 5.7 `kinematicweave.layout`

Owns:

- trajectory aggregation by geographic tile;
- density rasterization;
- clustering;
- directional feature construction;
- centerline fitting;
- corridor geometry;
- junction inference;
- connectivity-graph construction;
- spatial indexing for inferred layout.

### 5.8 `kinematicweave.runtime`

Owns:

- tape loading;
- timeline advancement;
- deterministic replay;
- state reconstruction;
- time, space, agent, and semantic queries;
- canonical state ordering;
- replay hashing;
- branch loading and comparison.

### 5.9 `kinematicweave.editing`

Owns:

- edit-operation definitions;
- delay edits;
- segment replacement;
- corridor closure;
- supported rerouting;
- branch creation;
- edit provenance;
- post-edit validity checks.

### 5.10 `kinematicweave.metrics`

Owns:

- motion metrics;
- event metrics;
- layout geometry metrics;
- graph and topology metrics;
- replay and edit metrics;
- resource metrics;
- matching algorithms used by metrics.

Metrics must be pure or explicitly controlled and must not depend on the viewer.

### 5.11 `kinematicweave.experiments`

Owns:

- experiment configuration loading;
- run orchestration;
- resumability;
- per-unit execution;
- run manifests;
- raw result writing;
- failure recording;
- workload partitioning.

It must call methods from other modules rather than reimplement them.

### 5.12 `kinematicweave.statistics`

Owns:

- aggregation;
- bootstrap confidence intervals;
- paired tests;
- effect sizes;
- multiple-comparison correction;
- result summaries.

It consumes raw results and never mutates them.

### 5.13 `kinematicweave.visualization`

Owns:

- Rerun integration;
- deterministic camera and timeline definitions;
- motion overlays;
- inferred/ground-truth layout overlays;
- grammar and graph views;
- branch comparisons;
- qualitative exports.

### 5.14 `kinematicweave.reporting`

Owns:

- tables;
- figures;
- experiment summaries;
- hardware reports;
- failure reports;
- traceability links from outputs to raw records.

### 5.15 `kinematicweave.native`

Owns optional bindings to profiled native kernels.

Rules:

- it begins empty or minimal;
- no public experiment depends directly on native implementation details;
- a pure-Python fallback must exist for core functionality unless explicitly approved otherwise;
- correctness tests must compare native and Python implementations.

## 6. Dependency direction

Allowed dependency direction:

```text
domain
  ↑
data, trajectory, events
  ↑
codecs, layout, grammar
  ↑
runtime, editing, metrics
  ↑
experiments
  ↑
statistics, visualization, reporting
```

Cross-cutting utilities may exist only when they are narrow and do not become an uncontrolled dependency hub.

## 7. Forbidden dependencies

The following dependencies are forbidden:

- `data` depending on `experiments`;
- `metrics` depending on `visualization`;
- `codecs` depending on a specific dataset adapter;
- `layout` depending on the runtime;
- `runtime` depending on Rerun or another viewer;
- `statistics` modifying raw experiment records;
- `reporting` recomputing hidden metrics;
- optional perception code importing into the core package path.

## 8. Public interfaces

### 8.1 Dataset adapter interface

A dataset adapter must support operations equivalent to:

```text
list_scenarios()
load_scenario()
load_map()
materialize_canonical_records()
validate_source()
```

### 8.2 Codec interface

Every codec must support operations equivalent to:

```text
encode()
decode()
serialize()
deserialize()
serialized_size()
metadata()
```

### 8.3 Layout inference interface

Every layout method must support operations equivalent to:

```text
fit()
infer_geometry()
infer_topology()
serialize_result()
metadata()
```

A baseline may implement geometry and topology differently, but experiment code must invoke a common contract.

### 8.4 Runtime interface

The runtime must support operations equivalent to:

```text
load_tape()
state_at()
replay()
query()
apply_edit()
create_branch()
serialize_branch()
canonical_hash()
```

### 8.5 Metric interface

Metrics must support operations equivalent to:

```text
compute(prediction, reference, config)
validate_inputs()
metadata()
```

### 8.6 Experiment runner interface

The experiment system must support operations equivalent to:

```text
plan()
run()
resume()
validate_outputs()
summarize_status()
```

## 9. Command-line architecture

The intended command-line entry point is:

```text
kinematicweave
```

Expected command groups:

```text
kinematicweave data ...
kinematicweave codec ...
kinematicweave layout ...
kinematicweave runtime ...
kinematicweave experiment ...
kinematicweave report ...
kinematicweave visualize ...
kinematicweave doctor ...
```

Examples are illustrative until later batches freeze exact commands.

## 10. Configuration architecture

Configuration is layered:

```text
project defaults
    ↓
dataset configuration
    ↓
method configuration
    ↓
experiment configuration
    ↓
explicit command-line overrides
```

Every resolved configuration must be saved with the run.

Hidden environment-dependent behavior is prohibited.

## 11. Artifact flow

```text
External data
    ↓
Canonical processed data
    ↓
Method outputs
    ↓
Per-unit raw metrics
    ↓
Aggregated statistics
    ↓
Tables, figures, and qualitative artifacts
```

Each artifact must record enough provenance to identify its producer and inputs.

## 12. Storage layout

The intended repository-level layout is:

```text
definitions/
configs/
src/
tests/
scripts/
data/
experiments/
results/
figures/
qualitative/
reports/
```

Responsibilities:

- `definitions/` — authoritative research and engineering specifications;
- `configs/` — versioned run configurations;
- `src/` — implementation;
- `tests/` — automated tests and fixtures;
- `scripts/` — thin convenience wrappers only;
- `data/` — local external and processed data, mostly ignored by Git;
- `experiments/` — experiment declarations and sweep definitions;
- `results/` — raw and aggregated machine-readable results;
- `figures/` — generated quantitative figures;
- `qualitative/` — generated visual evidence;
- `reports/` — generated and authored reports.

## 13. Error handling

Core requirements:

- invalid input records fail with explicit validation errors;
- failed experiment units are recorded, not silently skipped;
- long experiments are resumable;
- partial outputs are distinguishable from complete outputs;
- metrics return documented invalid states for degenerate cases;
- warnings that affect scientific validity are stored in run metadata.

## 14. Logging

Logging must support:

- human-readable console output;
- machine-readable run logs where appropriate;
- scenario or tile identifiers;
- experiment identifiers;
- timing;
- resource observations;
- explicit failure reasons.

Logs are diagnostic artifacts, not substitutes for structured results.

## 15. Concurrency and memory

The architecture must permit:

- bounded worker counts;
- chunked Parquet reads;
- per-scenario or per-tile processing;
- resumability at the experimental-unit level;
- avoidance of global all-pairs operations;
- CPU execution of the core path.

Parallelism must not change canonical results.

## 16. Native acceleration boundary

Native optimization may be considered for:

- trajectory simplification;
- interpolation and replay;
- nearest-neighbor or spatial-index operations;
- large geometry kernels.

Native code must not be introduced merely because C++ is available.

The optimization workflow is:

```text
working Python implementation
    ↓
end-to-end profiling
    ↓
identified bottleneck
    ↓
small native kernel
    ↓
Python/native equivalence tests
```

## 17. Optional perception boundary

Optional perception code must live outside the core dependency path, for example:

```text
src/kinematicweave_optional/perception/
```

or in a separate package.

Its only supported output into the core is canonical trajectory and scene data.

## 18. Viewer boundary

The viewer reads:

- canonical scene data;
- encoded motion;
- inferred layout;
- runtime states;
- result records.

The viewer must not:

- alter raw results;
- define metric values;
- become the only way to apply an edit;
- require manual action for reproducibility.

## 19. Testing boundaries

Each module requires tests at its own boundary.

Examples:

- adapters: source-to-canonical conversion tests;
- codecs: round-trip and size-accounting tests;
- layout: synthetic geometry and topology tests;
- runtime: determinism and query tests;
- editing: provenance and validity tests;
- metrics: known-value tests;
- experiments: resumability and manifest tests;
- reporting: traceability and regeneration tests.

## 20. Ownership map by project phase

| Phase | Primary owning modules |
|---|---|
| 1 — Foundation | domain, experiments, reporting |
| 2 — Data | data, domain |
| 3 — Procedural lines | trajectory, events, codecs |
| 4 — Motion evaluation | metrics, experiments, statistics, reporting |
| 5 — Layout inference | layout, grammar |
| 6 — Layout evaluation | metrics, experiments, statistics, reporting |
| 7 — Runtime and editing | runtime, editing, grammar |
| 8 — Qualitative viewer | visualization, reporting |
| 9 — Full campaign | experiments, statistics, reporting |
| 10 — Release | reporting and repository-wide integration |
| 11 — Optional perception | isolated optional package |

## 21. Architecture acceptance criteria

This document is acceptable when:

- every planned deliverable has a clear module owner;
- dataset-specific structures cannot leak into core algorithms;
- all codecs can be compared through one interface;
- metrics and experiments are independent of visualization;
- raw results remain immutable;
- optional perception and native acceleration cannot block the core;
- dependency direction prevents circular architecture;
- the design is implementable on the reference laptop;
- later batches can define schemas and protocols without changing the overall component structure.
