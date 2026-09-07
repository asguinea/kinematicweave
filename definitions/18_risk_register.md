# Risk Register

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.14 — Risk Register and Decision Governance  
**Status:** Draft for approval

## 1. Purpose

This register tracks research, engineering, reproducibility, and resource risks that could weaken the KinematicWeave project or delay the evidence package.

Each risk records:

- identifier;
- category;
- description;
- likelihood;
- impact;
- detection signal;
- mitigation;
- fallback;
- owner;
- status.

Likelihood and impact use:

- Low;
- Medium;
- High.

## 2. Risk table

| ID | Category | Risk | Likelihood | Impact | Detection signal | Mitigation | Fallback | Owner | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-001 | Research | Insufficient repeated trajectories per tile | High | High | Many tiles fail minimum coverage or topology is unstable | Measure coverage before freezing tiles; select tiles by predeclared eligibility rules; run coverage curves | Narrow claim to sufficiently observed regions and report coverage threshold | Research | Open |
| R-002 | Research | Motion-derived geometry looks plausible but topology is wrong | High | High | Good centerline metrics with poor OD connectivity or false links | Keep geometry and topology metrics separate; include false-connection metrics | Limit conclusions to geometry when topology is unsupported | Research | Open |
| R-003 | Research | Event definitions are too subjective | Medium | High | High detector sensitivity to thresholds or poor reproducibility | Use operational definitions, synthetic oracle cases, and development-only threshold selection | Report event results as exploratory or narrow to the most stable event class | Research | Open |
| R-004 | Research | Simple baselines outperform procedural lines | Medium | Medium | Pareto frontier dominated by RDP or uniform sampling | Implement fair matched-budget comparisons and preserve negative results | Reframe contribution around editability, semantics, or identified boundary conditions | Research | Open |
| R-005 | Research | Grammar repair increases false connectivity | Medium | High | OD agreement rises while false connections also rise materially | Measure harm metrics; ablate each repair rule | Disable harmful rules and report mixed outcome | Research | Open |
| R-006 | Research | 2.5D results are overstated as full 3D evidence | Medium | High | Draft language implies volumetric reconstruction | Enforce claim restrictions and dimensionality labels in reports | Narrow wording to locally planar 2.5D urban scenes | Research | Open |
| R-007 | Research | Oracle grammar is confused with inference | Medium | High | Oracle results appear alongside trajectory-only inference without distinction | Separate experiment IDs, provenance, tables, and captions | Remove oracle results from inference claims | Research | Open |
| R-008 | Data | Map and trajectory frames are misaligned | Medium | High | Systematic spatial offset or rotation in overlays and metrics | Add adapter validation, known landmarks, and synthetic transform tests | Exclude affected units and document adapter limitation | Data | Open |
| R-009 | Data | Geographic leakage across splits | Medium | High | Same physical region appears in development and test | Group by geographic leakage unit and validate buffered overlap | Regenerate split manifests and invalidate contaminated runs | Data | Open |
| R-010 | Data | Duplicate scenarios or tracks bias results | Medium | Medium | Matching identifiers, checksums, or spatial-temporal signatures | Deduplicate before splitting and keep duplicates in one split | Report duplicate count and rerun affected analysis | Data | Open |
| R-011 | Data | Primary dataset lacks enough pedestrian or cyclist evidence | High | Medium | Low eligible counts after filtering | Report class distribution early; treat vehicles as primary when necessary | Narrow class claim and move other classes to secondary analysis | Data | Open |
| R-012 | Data | Dataset size exceeds SSD budget | Low | High | Free space falls below threshold or duplicate caches grow | Download only motion/map subsets; partition canonical data; estimate outputs | Reduce local subset and delete reproducible caches | Data | Open |
| R-013 | Evaluation | Serialized-size accounting is unfair | Medium | High | One method omits timestamps, events, or decoder metadata | Centralize size accounting and require reconstructible payloads | Invalidate and rerun compactness experiments | Evaluation | Open |
| R-014 | Evaluation | Matching tolerances favor one method | Medium | High | Rankings change sharply across small tolerance changes | Freeze tolerances before test; publish sensitivity on development data | Report sensitivity and narrow claims | Evaluation | Open |
| R-015 | Evaluation | Experimental unit is treated incorrectly | Medium | High | Very small p-values from point-level pseudo-replication | Enforce scenario-aware and tile-level aggregation | Recompute analysis with correct hierarchy | Evaluation | Open |
| R-016 | Evaluation | Test-set tuning occurs accidentally | Medium | High | Parameters change after test results are seen | Freeze configs, metric versions, and test manifests | Create a new experiment version and label prior run exploratory | Evaluation | Open |
| R-017 | Evaluation | Failed units are silently dropped | Medium | High | Method counts differ without failure explanation | Persist status and failure records for every planned unit | Add failure-rate analysis and rerun missing units | Evaluation | Open |
| R-018 | Evaluation | Robustness sweeps become too large | High | Medium | Planned jobs exceed time or disk estimate | Estimate sweep size; prioritize primary levels; use resumable jobs | Reduce secondary levels while preserving core conditions | Evaluation | Open |
| R-019 | Engineering | Clustering creates an out-of-memory condition | Medium | High | RSS approaches hard ceiling or pairwise arrays scale quadratically | Tile processing, spatial indexing, bounded batches, sample caps | Reduce per-tile sample density and document approximation | Engineering | Open |
| R-020 | Engineering | Parallel execution changes outputs | Medium | High | Hashes or metric results differ by worker count | Stable seeds, canonical ordering, deterministic reductions | Run affected stage serially | Engineering | Open |
| R-021 | Engineering | Serialization is unstable across runs | Medium | High | Repeated byte payloads or hashes differ | Canonical ordering, normalized floats, stable metadata | Use logical-content hashing and freeze environment | Engineering | Open |
| R-022 | Engineering | Viewer work delays the core evaluation | High | Medium | Significant effort spent before metrics and experiments work | Keep viewer downstream and headless; prioritize fixed artifact generation | Reduce viewer to static overlays and minimal replay inspection | Engineering | Open |
| R-023 | Engineering | Optional perception work expands the project scope | High | High | Detector, tracker, or depth work begins before core evidence freezes | Keep Phase 11 outside the critical path | Drop the optional demo entirely | Engineering | Open |
| R-024 | Engineering | Native C++ is introduced prematurely | Medium | Medium | Build complexity appears before profiling | Require working Python path and measured bottleneck | Remove native dependency and retain Python implementation | Engineering | Open |
| R-025 | Engineering | Dependency updates change scientific outputs | Medium | High | Metric or geometry regression after lockfile update | Lock dependencies and run pilot regression checks | Pin prior versions and regenerate affected results | Engineering | Open |
| R-026 | Runtime | Rerouting produces physically invalid motion | Medium | High | Discontinuities, excessive speed, or corridor violations | Post-edit validity checks and synthetic rerouting tests | Mark reroute unsupported for that case | Runtime | Open |
| R-027 | Runtime | Symbolic edit is compact but not equivalent to raw baseline | Medium | High | Final trajectories differ in intended outcome | Define edit equivalence and compare validation outputs | Exclude invalid comparison and revise baseline procedure | Runtime | Open |
| R-028 | Runtime | Determinism fails across environments | Medium | Medium | Hash mismatch across hardware or library versions | Anchor exact claims to frozen environment and use numeric equivalence elsewhere | Narrow determinism claim to documented reference environment | Runtime | Open |
| R-029 | Resource | Long jobs are interrupted or laptop overheats | Medium | High | Thermal throttling, crash, or unfinished run | Resumability, AC power, cooling guidance, job segmentation | Resume from unit checkpoints and reduce worker count | Resource | Open |
| R-030 | Resource | Full campaign takes longer than practical | Medium | High | Pilot extrapolation exceeds available schedule | Profile early; prioritize confirmatory matrix; parallelize conservatively | Reduce secondary analyses and narrow claim set | Resource | Open |
| R-031 | Resource | Experiment outputs fill the SSD | Medium | High | Estimated or actual output exceeds free-space rule | Preflight output estimation, cache limits, compact raw records | Archive or delete reproducible intermediates; reduce sweep | Resource | Open |
| R-032 | Reproducibility | Figures cannot be regenerated from raw records | Medium | High | Manual edits or undocumented scripts are required | Generate all figures through versioned commands and manifests | Rebuild figure pipeline before result freeze | Reproducibility | Open |
| R-033 | Reproducibility | External dataset version changes | Low | High | Downloaded files or identifiers differ | Record source version and checksums; preserve manifests | Require user to obtain the documented release | Reproducibility | Open |
| R-034 | Reproducibility | Dirty-worktree runs enter final results | Medium | Medium | Manifest records uncommitted changes | Release gate rejects dirty confirmatory runs | Repeat run from committed revision | Reproducibility | Open |
| R-035 | Reporting | Qualitative examples are cherry-picked | Medium | High | Only best-looking scenes appear | Predefine selection criteria and include failures | Regenerate gallery from metric-based selections | Reporting | Open |
| R-036 | Reporting | Metrics are reported without failure counts | Medium | High | Different method denominators are hidden | Require valid, undefined, failed, and excluded counts | Correct tables and reports before release | Reporting | Open |

## 3. Risk review cadence

Review the register:

- at the end of every implementation phase;
- before opening the pilot set;
- before freezing the primary test;
- before the full campaign;
- before release.

A risk becomes **Active** when its detection signal is observed.

A risk becomes **Mitigated** only when evidence shows the mitigation is working.

A risk becomes **Closed** when it is no longer applicable or the affected work has been completed safely.

## 4. Escalation rules

Immediate review is required when:

- a High-impact risk becomes Active;
- a primary claim must be narrowed;
- a frozen metric or split must change;
- the resource envelope is exceeded;
- a confirmatory run is invalidated;
- a new mandatory dependency or external compute requirement appears.

## 5. Risk ownership

Default owners:

- Research — hypotheses, claim wording, experiment validity;
- Data — acquisition, adapters, splits, leakage;
- Evaluation — metrics, matching, statistics;
- Engineering — architecture, implementation, determinism;
- Runtime — replay, editing, rerouting;
- Resource — RAM, VRAM, SSD, runtime;
- Reproducibility — provenance and regeneration;
- Reporting — figures, tables, qualitative selection.

Ownership may be refined in later project management files.

## 6. Acceptance criteria

This register is acceptable when:

- all known high-impact risks have a detection signal;
- every high-impact risk has a mitigation and fallback;
- research, data, evaluation, engineering, runtime, resource, reproducibility, and reporting risks are covered;
- scope expansion through viewer, perception, or native code is explicitly controlled;
- risks can be updated without deleting historical entries.
