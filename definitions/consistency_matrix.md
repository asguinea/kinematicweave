# Phase 0 Consistency Matrix

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.15 — Definition Integration and Design Freeze Review  
**Status:** Ready for approval

## 1. Purpose

This matrix verifies that the Phase 0 definitions form one coherent implementation and evaluation specification.

The required traceability chain is:

```text
Research claim
    ↓
Hypothesis
    ↓
Experiment
    ↓
Dataset and split
    ↓
Method, baseline, and ablation
    ↓
Metric
    ↓
Raw result contract
    ↓
Statistical analysis
    ↓
Figure, table, or qualitative artifact
    ↓
Permitted conclusion
```

## 2. Definition inventory

| Area | Authoritative files |
|---|---|
| Charter and scope | `00_project_charter.md`, `02_scope_and_non_goals.md`, `glossary.md` |
| Claims and evidence | `01_research_claims.md`, `claim_evidence_matrix.md` |
| Architecture | `03_system_architecture.md` |
| Domain and conventions | `04_domain_model.md`, `coordinate_and_time_conventions.md` |
| Data contracts | `05_data_contracts.md` |
| Serialization and determinism | `06_serialization_specification.md` |
| Evaluation | `07_evaluation_protocol.md` |
| Experiments | `08_experiment_matrix.md` |
| Dataset and splits | `09_dataset_and_split_policy.md` |
| Statistics | `10_statistical_analysis_plan.md` |
| Hardware budget | `11_hardware_and_resource_budget.md` |
| Reproducibility and quality | `12_reproducibility_policy.md`, `13_testing_and_quality_policy.md`, `14_coding_and_repository_conventions.md` |
| Visualization | `15_visualization_specification.md` |
| Codex workflow | `16_codex_batch_protocol.md`, `17_codex_report_template.md` |
| Governance | `18_risk_register.md`, `19_decision_log.md` |

## 3. Primary claim traceability

### H1 — Semantic preservation at matched storage

| Traceability item | Definition |
|---|---|
| Claim | Event-aware procedural lines preserve selected semantic events better than geometry-only codecs at matched serialized size |
| Primary experiment | `MOT-01`, `MOT-03` |
| Dataset unit | Eligible trajectory, with scenario-aware aggregation |
| Dataset split | Frozen motion test |
| Proposed method | `M5` event-aware procedural lines |
| Baselines | `M1` uniform sampling, `M2` RDP, `M4` event-free procedural lines |
| Primary metrics | Stop F1, turn F1 |
| Safeguard metrics | ADE, maximum deviation |
| Raw record | `metric_records` |
| Statistical analysis | Scenario-aware bootstrap, paired differences, Holm correction |
| Main outputs | Event-F1-versus-size curves and semantic-event table |
| Permitted conclusion | Limited to the evaluated event definitions, data, and budgets |

**Consistency result:** Complete.

---

### H2 — Useful fidelity–compactness tradeoff

| Traceability item | Definition |
|---|---|
| Claim | Procedural lines provide a useful trajectory fidelity–size tradeoff |
| Primary experiment | `MOT-01` |
| Supporting experiments | `MOT-02`, `MOT-04`, `MOT-05`, `MOT-06` |
| Dataset unit | Trajectory |
| Proposed methods | `M4`, `M5` |
| Baselines | `M0`, `M1`, `M2`, `M3` |
| Primary metrics | Bytes per agent-second, ADE, maximum deviation |
| Fairness rule | Canonical reconstructible serialized payload |
| Statistical analysis | Paired budget-level differences and Pareto analysis |
| Main outputs | Error–size curves and Pareto frontier |
| Permitted conclusion | Competitive or favorable only over the tested budget range |

**Consistency result:** Complete.

---

### H3 — Corridor geometry from repeated motion

| Traceability item | Definition |
|---|---|
| Claim | Repeated trajectories recover useful persistent corridor geometry in sufficiently observed regions |
| Primary experiments | `LAY-02`, `LAY-03`, `LAY-08`, `LAY-09` |
| Dataset unit | Nonoverlapping geographic tile |
| Input restriction | Ground-truth map withheld from trajectory-only inference |
| Proposed method | `L2` grammar-assisted inference |
| Baselines | `L0` density/skeleton, `L1` directional clustering/centerlines |
| Primary metrics | Centerline F1, symmetric centerline distance |
| Secondary metrics | Region IoU, width error where available |
| Statistical analysis | Tile-level paired analysis and nested coverage-seed aggregation |
| Main outputs | Geometry table, coverage curve, robustness curves |
| Permitted conclusion | Restricted to documented coverage, noise, and scene conditions |

**Consistency result:** Complete.

---

### H4 — Connectivity and junction topology from repeated motion

| Traceability item | Definition |
|---|---|
| Claim | Repeated trajectories recover useful junction and connectivity structure |
| Primary experiments | `LAY-02`, `LAY-03`, `LAY-08`, `LAY-09` |
| Dataset unit | Geographic tile |
| Proposed method | `L2` |
| Baselines | `L0`, `L1` |
| Primary metrics | Junction F1, graph-edge F1, OD connectivity agreement |
| Harm metric | False connection count |
| Raw contracts | `graph_nodes`, `graph_edges`, `metric_records` |
| Statistical analysis | Tile-level paired comparisons |
| Main outputs | Topology table and connectivity examples |
| Permitted conclusion | Geometry and topology conclusions remain separate |

**Consistency result:** Complete.

---

### H5 — Value of symbolic topology repair

| Traceability item | Definition |
|---|---|
| Claim | Symbolic repair improves graph validity over unrepaired geometry |
| Primary experiment | `LAY-04` |
| Dataset unit | Geographic tile |
| Comparison | Identical inferred centerlines before and after repair |
| Primary metrics | OD agreement, graph-edge F1, junction F1 |
| Harm metric | False connection count |
| Required ablation | Remove each major repair rule |
| Statistical analysis | Paired tile differences with harm-aware interpretation |
| Main outputs | Repair ablation table and before/after examples |
| Permitted conclusion | Only for rules that improve topology without unacceptable false links |

**Consistency result:** Complete.

---

### H6 — Deterministic replay

| Traceability item | Definition |
|---|---|
| Claim | A serialized tape replays deterministically under documented conditions |
| Primary experiment | `RUN-01` |
| Synthetic precursor | `SYN-04` |
| Dataset unit | Tape or scenario |
| Primary metric | Replay sequence hash equality |
| Supporting metric | Query-result hash equality |
| Serialization rule | Canonical state and SHA-256 domain-separated hashing |
| Pass rule | Zero unexplained mismatches |
| Main output | Determinism report |
| Permitted conclusion | Anchored to the frozen reference environment and protocol |

**Consistency result:** Complete.

---

### H7 — Symbolic edit locality

| Traceability item | Definition |
|---|---|
| Claim | Supported semantic edits modify fewer representation elements than raw-sample rewriting |
| Primary experiments | `EDT-01`, `EDT-02`, `EDT-03`, `EDT-04` |
| Dataset unit | Edit scenario |
| Symbolic operations | Delay, segment replacement, closure, rerouting, branch creation |
| Baselines | Equivalent raw-sample edit procedures |
| Primary metrics | Modified records, symbols, and serialized bytes |
| Safeguards | Continuity, closure, and rerouting validity checks |
| Main outputs | Edit-locality table and branch comparisons |
| Permitted conclusion | Limited to implemented operations; not a usability claim |

**Consistency result:** Complete.

---

### H8 — Laptop-scale feasibility

| Traceability item | Definition |
|---|---|
| Claim | The frozen core campaign is feasible on the reference ASUS laptop |
| Primary experiments | `RUN-02`, `RUN-03`, `RES-02`, `RES-03` |
| Diagnostic experiment | `RES-01` |
| Dataset unit | Workload and experiment run |
| Primary metrics | Peak RAM, VRAM, disk, wall time, throughput, resumability |
| Resource limits | Under 24 GB normal RAM target, CPU-capable core, bounded VRAM and SSD use |
| Pass rule | Frozen campaign completes within approved ceilings without external compute |
| Main output | Hardware and campaign resource report |
| Permitted conclusion | Restricted to the measured workload and reference machine |

**Consistency result:** Complete.

## 4. Architecture-to-contract mapping

| Module | Primary contracts |
|---|---|
| `kinematicweave.data` | Scenario manifest, agent metadata, trajectory samples, vector-map elements |
| `kinematicweave.domain` | Stable identifiers, enums, coordinate and time types |
| `kinematicweave.trajectory` | Trajectory samples and derived kinematic arrays |
| `kinematicweave.events` | Semantic event records |
| `kinematicweave.codecs` | Procedural lines, segments, control points, tape manifest |
| `kinematicweave.layout` | Inferred corridors, junctions, graph nodes, graph edges |
| `kinematicweave.grammar` | Grammar nodes and relations |
| `kinematicweave.runtime` | Tape manifest, query results, canonical replay state |
| `kinematicweave.editing` | Edit-operation records and branch provenance |
| `kinematicweave.metrics` | Per-unit metric records |
| `kinematicweave.experiments` | Experiment manifest and unit status |
| `kinematicweave.statistics` | Aggregated result artifacts derived from raw records |
| `kinematicweave.visualization` | Artifact manifests and recorded camera/timeline configuration |
| `kinematicweave.reporting` | Figures, tables, and reports linked to source artifacts |

**Consistency result:** Every persistent module boundary has a documented contract or a documented derivation from one.

## 5. Data-flow consistency

```text
External motion and map data
    ↓ dataset adapter
Canonical scenario, agent, trajectory, and map records
    ↓
Motion codecs and trajectory-only layout inference
    ↓
Procedural-line records and inferred layout records
    ↓
Tape, grammar, and connectivity graph
    ↓
Replay, queries, branches, and edits
    ↓
Per-unit metrics and resource observations
    ↓
Statistical aggregation
    ↓
Figures, tables, qualitative artifacts, and final reports
```

### Validation

- Dataset-native objects do not cross the adapter boundary.
- Ground-truth maps are unavailable to trajectory-only inference.
- Metrics are independent of visualization.
- Raw results are immutable.
- Optional perception is outside the core dependency path.
- C++ may replace internal kernels without changing public Python APIs.

**Consistency result:** Complete.

## 6. Coordinate, geometry, and time consistency

| Topic | Frozen convention |
|---|---|
| Spatial frame | Right-handed local Cartesian |
| Planar axes | `+x`, `+y` |
| Vertical axis | `+z` upward |
| Distance | Metres |
| Time storage | Signed integer nanoseconds |
| Derived duration | Seconds |
| Heading | Zero at `+x`, positive counterclockwise |
| Heading range | `[-π, π)` |
| Primary evaluation | Planar x-y |
| Elevation | Optional; may support grade-separation handling |
| Outside trajectory support | Return no state by default |
| Persistent float computation | Float64 by default |
| Stored precision | Explicit per codec |
| Missing observations | Explicit, never silently treated as stops |

**Consistency result:** Complete.

## 7. Serialization and size-accounting consistency

- All codecs expose equivalent operations.
- The decoder may not access source trajectories.
- Required timestamps or reconstruction rules count toward size.
- Semantic events count toward event-aware representation size.
- Shared overhead must be treated consistently.
- Python object size is not a compactness metric.
- Raw, logical, and optional compressed container sizes remain distinguishable.
- Replay hashing is based on canonical logical state, not rendered output.

**Consistency result:** Complete.

## 8. Dataset and evaluation consistency

- Synthetic data support correctness, not real-world claims.
- Smoke, development, pilot, and frozen test roles are distinct.
- Motion evaluation uses scenario-separated real data.
- Layout evaluation controls leakage geographically.
- Repeated observations of the same region remain within one split.
- Coverage subsampling is trajectory-level, nested, and seeded.
- Source maps are used only for scoring and oracle conversion in trajectory-only experiments.
- Failed and excluded units remain counted.

**Consistency result:** Complete.

## 9. Statistical consistency

- Motion analysis accounts for trajectories nested within scenarios.
- Layout analysis treats the tile as the primary unit.
- Coverage seeds are nested replicates within tiles.
- Paired comparisons are preferred.
- Bootstrap confidence intervals and natural-unit effect sizes are required.
- Holm correction applies within primary hypothesis families.
- Determinism and feasibility use exact pass criteria rather than unsuitable p-values.
- Missingness and method failure remain visible.

**Consistency result:** Complete.

## 10. Resource consistency

- All core experiments are CPU-capable.
- Normal process target is below 24 GB RAM.
- Large jobs are partitioned and resumable.
- Mandatory GPU workloads remain below the approved VRAM ceiling.
- Full raw sensor datasets are excluded from the critical path.
- The experiment runner must estimate job and disk growth before large sweeps.
- No mandatory uninterrupted job should normally exceed 12 hours.

**Consistency result:** Complete.

## 11. Qualitative evidence consistency

- Final visual artifacts are generated from recorded artifacts.
- Cameras and timeline states are versioned.
- Ground truth, prediction, oracle, and edited branches are distinct.
- Geometry and topology are visualized separately.
- Successes and failures are both required.
- Every qualitative artifact links to quantitative records.
- Manual scientific alteration is prohibited.

**Consistency result:** Complete.

## 12. Codex workflow consistency

- Phase 0 definitions are authoritative.
- Codex implements one approved batch at a time.
- Required tests determine PASS status.
- Design deviations are reported.
- Fixes remain under the same batch number.
- Codex stops before the next batch.
- Native code and heavyweight dependencies require justification.

**Consistency result:** Complete.

## 13. Nonblocking details deferred to implementation batches

The following details remain intentionally configurable and are not design blockers:

- exact event thresholds;
- final tile size among approved candidates;
- exact matched-byte budget grid;
- exact clustering hyperparameters;
- exact repair rules discovered during development;
- exact numeric tolerances;
- final worker and batch-size defaults;
- exact colors and typography;
- final Python minor version and library versions.

These values must be selected using development and pilot data, recorded in configuration, and frozen before the primary test campaign.

## 14. Contradiction review

No unresolved critical contradiction is identified among the Phase 0 definitions.

Important distinctions preserved consistently:

- 2.5D primary evidence versus general 3D ambition;
- representation capacity versus inference quality;
- geometry versus topology;
- symbolic edit locality versus human usability;
- deterministic replay versus cross-platform bit identity;
- compactness versus Python runtime memory;
- core trajectory evaluation versus optional perception;
- Python-first implementation versus possible later native acceleration.

## 15. Integration acceptance checklist

- [x] Every primary hypothesis maps to experiments.
- [x] Every experiment maps to methods, baselines, metrics, and units.
- [x] Every primary metric can be written to the raw metric contract.
- [x] Every persistent object has an owner and schema.
- [x] Dataset leakage controls are explicit.
- [x] Statistical units and nesting are explicit.
- [x] Resource limits match the reference laptop.
- [x] Qualitative artifacts are reproducible.
- [x] Codex implementation discipline is defined.
- [x] Risks and accepted decisions are recorded.
- [x] Optional components cannot block the critical path.

## 16. Result

The Phase 0 definition set is internally coherent and ready for design-freeze approval.
