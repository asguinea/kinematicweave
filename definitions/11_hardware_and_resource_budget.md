# Laptop Resource and Execution Budget

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.10 — Laptop Resource and Execution Budget  
**Status:** Draft for approval

## 1. Purpose

This document defines the resource envelope for developing and evaluating the KinematicWeave project on the reference laptop.

The project must remain practical on:

- ASUS ROG Zephyrus G14 GA403UP;
- AMD Ryzen 9 270-class CPU;
- 32 GB system RAM;
- 1 TB SSD;
- NVIDIA RTX 5070 laptop GPU;
- approximately 8 GB of GPU memory.

The resource budget is part of the scientific claim. Core experiments must not require cloud compute, a workstation, or an external cluster.

## 2. General operating principles

The implementation must:

- remain CPU-capable for all core experiments;
- use GPU acceleration only when it materially improves a measured workload;
- process scenarios and geographic tiles incrementally;
- avoid loading the complete dataset into memory;
- write resumable per-unit results;
- preserve at least 15% free SSD space during normal operation;
- prevent unbounded worker, cache, or output growth;
- record resource usage for pilot and frozen campaigns.

## 3. Resource classes

Experiments are assigned one resource class.

| Class | Intended duration | Expected RAM | Execution behavior |
|---|---:|---:|---|
| Tiny | Under 1 minute | Under 2 GB | Used for unit tests and exact synthetic checks |
| Small | Under 15 minutes | Under 8 GB | Interactive development and smoke evaluation |
| Medium | 15 minutes to 3 hours | Under 16 GB | Pilot studies and bounded real-data jobs |
| Large | 3 to 12 hours | Under 24 GB | Resumable campaign component |
| Very large | Multiple resumable runs over one or more days | Under 24 GB per process | Full sweeps split by unit, method, or condition |

No mandatory single process may intentionally consume the complete 32 GB of RAM.

## 4. RAM budget

### 4.1 Normal target

Normal processing should remain below:

```text
24 GB peak resident memory
```

This leaves capacity for:

- the operating system;
- development tools;
- filesystem cache;
- resource-monitoring overhead;
- memory-allocation spikes.

### 4.2 Warning threshold

A process crossing:

```text
22 GB RSS
```

must emit a warning in benchmark or experiment logs when resource monitoring is enabled.

### 4.3 Hard-stop threshold

Long-running experiment processes should stop or reduce concurrency before expected memory use exceeds:

```text
26 GB RSS
```

The implementation must not rely on operating-system swapping to complete a core experiment.

### 4.4 Unit tests and CI

The ordinary test suite should remain below:

```text
4 GB RSS
```

The reduced smoke pipeline should remain below:

```text
8 GB RSS
```

### 4.5 Memory-sensitive operations

The following require bounded implementations:

- pairwise-distance computation;
- trajectory clustering;
- spatial indexing;
- raster construction;
- geometry matching;
- bootstrap analysis;
- figure aggregation.

Global dense matrices whose dimensions scale with all trajectory samples are prohibited unless their maximum size is explicitly bounded.

## 5. GPU and VRAM budget

### 5.1 Core requirement

Core correctness and confirmatory evaluation must be runnable without a GPU.

### 5.2 GPU target

When GPU execution is used, normal peak allocation should remain below:

```text
6.5 GB VRAM
```

This leaves headroom for:

- driver and display use;
- allocator fragmentation;
- library overhead;
- temporary kernels.

### 5.3 Hard ceiling

Mandatory GPU workloads must not require more than:

```text
7.5 GB VRAM
```

A workload exceeding this limit must:

- reduce batch size;
- fall back to CPU;
- or be classified as optional.

### 5.4 Approved GPU uses

Potential approved uses include:

- batched numeric operations;
- optional perception inference;
- optional depth estimation;
- large but bounded tensor calculations;
- profiling comparisons against CPU.

### 5.5 Prohibited core GPU dependencies

The core project must not require:

- custom CUDA kernels;
- large-model training;
- multi-GPU execution;
- mixed-GPU distributed execution;
- GPU-only serialization or replay.

## 6. SSD budget

The 1 TB SSD must retain operating-system and development headroom.

### 6.1 Free-space rule

Normal operation must preserve at least:

```text
15% free disk space
```

A large experiment must check available space before starting.

### 6.2 Recommended project allocation

| Category | Target ceiling |
|---|---:|
| Source motion and map data | 250 GB |
| Canonical processed data | 150 GB |
| Temporary cache | 100 GB |
| Raw experiment results | 100 GB |
| Aggregated results, figures, and reports | 25 GB |
| Optional perception assets | 100 GB |
| Reserved free and system headroom | Remaining capacity |

These are ceilings, not allocation targets.

### 6.3 Cache behavior

Caches must:

- be reproducible;
- be safe to delete;
- have documented ownership;
- support size inspection;
- use deterministic keys;
- avoid duplicate copies of immutable source data.

### 6.4 Output growth

Experiment planning must estimate:

- units;
- conditions;
- methods;
- seeds;
- expected bytes per unit.

A sweep must refuse to begin when estimated output would violate the free-space rule.

## 7. Dataset budget

The core dataset plan must avoid full raw-sensor acquisition.

Required source material is limited to:

- trajectory scenarios;
- agent metadata;
- local vector maps;
- dataset manifests.

The project must prefer:

- subset downloads;
- compressed source files;
- partitioned canonical data;
- selective local materialization.

## 8. Concurrency budget

### 8.1 Default workers

The default worker count should be conservative:

```text
min(physical_or_effective_cpu_count - 2, 8)
```

The exact implementation may choose a smaller default after profiling.

### 8.2 Memory-aware scheduling

Worker count must account for estimated per-unit memory.

For a stage with memory estimate `M_worker`:

```text
workers <= floor(memory_budget / M_worker)
```

The scheduler must allow a user override.

### 8.3 Determinism

Changing worker count must not change canonical scientific outputs.

### 8.4 I/O-heavy work

Data conversion and Parquet writing should avoid excessive parallel writers that reduce SSD performance or create fragmentation.

## 9. Batch-size policy

Every batch-oriented operation must expose a configurable batch size.

Initial planning targets:

| Operation | Initial batch target |
|---|---:|
| Trajectory codec evaluation | 100–1,000 trajectories |
| Scenario conversion | 1–10 scenarios |
| Geographic layout inference | 1 tile |
| Metric aggregation | 10,000–100,000 records |
| Bootstrap analysis | Chunked by metric and comparison |
| Optional GPU inference | Dynamically fitted below VRAM target |

Final defaults are selected from pilot measurements.

## 10. Runtime budget

### 10.1 Developer feedback

Targets:

- unit tests: under 2 minutes;
- static checks: under 2 minutes;
- synthetic suite: under 5 minutes;
- real-data smoke pipeline: under 15 minutes.

### 10.2 Pilot runs

A pilot experiment should normally complete within:

```text
3 hours
```

Longer pilot jobs require justification.

### 10.3 Individual campaign jobs

No mandatory uninterrupted job should normally exceed:

```text
12 hours
```

Larger experiments must be split by:

- scenario;
- tile;
- method;
- budget;
- perturbation condition;
- seed;
- or another stable unit.

### 10.4 Full campaign

The full frozen campaign may span multiple days, provided that:

- every job is resumable;
- completed units are never recomputed unnecessarily;
- run status is inspectable;
- the laptop can be used safely between jobs;
- outputs remain traceable.

## 11. Resumability requirements

Any experiment expected to exceed 30 minutes must support resumability.

The runner must:

- write one durable completion record per unit;
- distinguish complete, partial, failed, and skipped units;
- avoid rewriting complete raw results;
- resume from the first incomplete unit;
- preserve deterministic seed assignment;
- validate existing outputs before skipping them.

Interruption may include:

- manual termination;
- process crash;
- system restart;
- low-disk stop;
- resource-limit stop.

## 12. Checkpoint policy

### 12.1 Unit-level checkpoint

The preferred checkpoint is the experimental unit:

- trajectory;
- scenario;
- tile;
- tape;
- edit scenario;
- workload.

### 12.2 Intra-unit checkpoint

Intra-unit checkpointing is required only when one unit can exceed 30 minutes.

### 12.3 Atomic writes

Result files must be written atomically where practical:

1. write temporary artifact;
2. validate;
3. rename into final location;
4. update manifest.

Incomplete temporary files are not treated as completed results.

## 13. Resource monitoring

### 13.1 Required measurements

Pilot and frozen runs record, where available:

- wall-clock time;
- CPU time;
- peak RSS;
- peak GPU memory;
- output bytes;
- units processed;
- throughput;
- failed and retried units.

### 13.2 Measurement frequency

Long-running processes should sample resource use approximately every:

```text
5 to 15 seconds
```

The monitoring interval must not materially distort the workload.

### 13.3 Stage-level reporting

Resource records should identify major stages:

- data read;
- preprocessing;
- encoding;
- layout inference;
- metric computation;
- serialization;
- aggregation;
- visualization.

## 14. Thermal and power considerations

Laptop-scale evaluation must account for sustained execution.

Recommended operating conditions:

- connect AC power;
- use a stable performance mode;
- ensure unobstructed cooling;
- avoid closed-lid operation when it restricts ventilation;
- record the selected system performance profile;
- avoid comparing benchmark runs made under materially different power modes.

The project does not require unsafe firmware, voltage, or power modifications.

## 15. CPU benchmark policy

Runtime comparisons must record:

- worker count;
- process count;
- thread-related environment settings;
- CPU power mode when known;
- background-load caveats;
- warm-up policy;
- repeated-run count.

Performance claims should use repeated measurements and robust percentiles.

## 16. GPU benchmark policy

GPU benchmarks must record:

- device name;
- driver version;
- CUDA runtime version;
- framework version;
- precision;
- batch size;
- warm-up iterations;
- synchronization method;
- peak memory.

Timing must synchronize GPU work before measurement ends.

## 17. Environment policy

The supported reference environment is:

- Windows 11 host;
- Ubuntu under WSL2 as the preferred development and experiment environment;
- Python-first tooling;
- locked dependencies;
- optional CUDA-enabled PyTorch.

Native Windows execution may be supported later, but it must not be required for the initial frozen campaign.

## 18. WSL2 considerations

When using WSL2:

- project source should preferably live in the Linux filesystem for heavy I/O;
- large datasets should avoid slow cross-filesystem access patterns;
- available memory and swap configuration must be documented;
- GPU access must be validated before optional GPU runs;
- path handling must remain repository-relative in manifests.

## 19. Core fallback behavior

Every core stage must define behavior when:

- GPU is unavailable;
- requested worker count is too high;
- memory estimate exceeds budget;
- disk space is insufficient;
- an experiment is interrupted;
- one unit fails.

Preferred behavior:

- reduce batch size;
- reduce workers;
- switch to CPU;
- stop cleanly before resource exhaustion;
- record the reason;
- preserve completed work.

## 20. Resource-aware experiment planning

Before a large sweep, the runner should estimate:

```text
planned_units =
    units
    × methods
    × budgets
    × perturbation_levels
    × seeds
```

The plan should display:

- total jobs;
- estimated runtime;
- estimated output volume;
- selected concurrency;
- available disk space;
- whether the plan fits the approved budget.

## 21. Stage-specific resource targets

### 21.1 Data preparation

Targets:

- under 12 GB RAM;
- CPU-only;
- partitioned writes;
- resumable by scenario.

### 21.2 Motion-codec evaluation

Targets:

- under 12 GB RAM;
- CPU-only reference;
- resumable by trajectory or scenario;
- no full-dataset in-memory table.

### 21.3 Layout inference

Targets:

- under 24 GB RAM;
- process one tile at a time;
- bounded sample count per tile;
- spatial-index or approximate-neighbor methods when needed;
- no unbounded dense pairwise matrices.

### 21.4 Statistical analysis

Targets:

- under 16 GB RAM;
- read only required columns;
- process metric families separately;
- chunk bootstrap inputs where needed.

### 21.5 Visualization

Targets:

- under 12 GB RAM for standard scenarios;
- load selected qualitative artifacts only;
- headless export supported;
- viewer performance does not define scientific correctness.

### 21.6 Optional perception

Targets:

- under 7.5 GB VRAM;
- short sequences;
- pretrained models only;
- no impact on the core campaign.

## 22. Scale targets

Initial target scales:

### Replay

- 100 active agents;
- 1,000 active agents;
- 10,000 active agents;
- optional 100,000 stored lines for offline query tests.

### Motion evaluation

- 500–1,000 frozen real scenarios, subject to eligibility and pilot timing.

### Layout evaluation

- 30–60 frozen geographic tiles;
- coverage sweep at 10%, 25%, 50%, and 100%;
- five seeds for stochastic subsampling conditions.

These targets may be reduced only through an explicit scope decision and corresponding claim limitation.

## 23. Timeout policy

### 23.1 Unit timeout

Each experimental-unit type has a configurable timeout.

A timeout must:

- mark the unit failed;
- preserve logs;
- avoid partial result acceptance;
- allow later rerun.

### 23.2 Stage timeout

A stage that exceeds the pilot-derived maximum expected duration should warn before termination.

### 23.3 No silent retry loops

Retries are bounded and recorded.

## 24. Resource regression policy

Performance tests should detect major regressions.

Initial warning thresholds relative to a frozen benchmark:

- more than 25% increase in median runtime;
- more than 25% increase in peak RAM;
- more than 25% increase in output size;
- unexpected GPU dependency;
- loss of resumability.

Regression thresholds are diagnostic and may be refined after the first stable implementation.

## 25. Minimum hardware report

The final repository must produce a report containing:

- CPU model;
- logical CPU count;
- RAM;
- GPU model;
- GPU memory;
- operating system;
- WSL version when applicable;
- Python version;
- major library versions;
- available disk before and after campaign;
- selected power/performance mode when known.

## 26. Feasibility success criteria

Hypothesis H8 is supported when:

- the frozen core campaign completes on the reference laptop;
- no core stage exceeds the hard RAM or VRAM ceiling;
- disk headroom remains above the free-space rule;
- long jobs resume correctly;
- measured runtimes and scale are fully reported;
- no external compute is required.

H8 is partially supported when:

- a reduced but predeclared campaign completes;
- one optional scale target is not reached;
- some GPU acceleration is unavailable but CPU execution succeeds.

H8 is not supported when:

- mandatory experiments repeatedly exhaust memory;
- required data exceed the practical SSD envelope;
- the campaign requires cloud or external compute;
- results cannot be resumed after interruption;
- mandatory jobs require impractically long uninterrupted execution.

## 27. Acceptance criteria

This document is acceptable when:

- RAM, VRAM, SSD, runtime, and worker limits are explicit;
- core experiments remain CPU-capable;
- long runs are resumable;
- no required single job needs multi-day uninterrupted execution;
- data acquisition excludes unnecessary raw sensors;
- disk growth is estimated before large sweeps;
- resource monitoring is part of pilot and frozen runs;
- thermal and power conditions are documented;
- stage-specific targets fit the reference laptop;
- H8 has measurable support and failure conditions.
