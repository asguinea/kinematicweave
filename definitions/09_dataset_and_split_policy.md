# Dataset, Sampling, and Split Policy

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.7 — Dataset, Sampling, and Split Policy  
**Status:** Draft for approval

## 1. Purpose

This document defines how datasets are selected, acquired, filtered, canonicalized, sampled, partitioned, and frozen for the KinematicWeave evaluation.

Its goals are to:

- prevent development/test leakage;
- keep the project practical on the reference ASUS laptop;
- separate synthetic correctness tests from real-data evidence;
- ensure every exclusion is documented;
- make trajectory-coverage experiments reproducible;
- isolate dataset-specific behavior inside adapters;
- preserve a frozen primary test set once evaluation begins.

## 2. Dataset roles

The project uses datasets in distinct roles.

### 2.1 Synthetic correctness data

Synthetic scenes are used for:

- exact geometry and topology tests;
- event-detector sanity checks;
- serialization and replay tests;
- known-answer metric tests;
- edit and rerouting tests;
- small CI fixtures.

Synthetic data are not sufficient evidence for real-world claims.

### 2.2 Primary real-world dataset

The primary planned dataset is the **Argoverse 2 Motion Forecasting dataset** with its associated vector-map information.

The primary dataset is used for:

- procedural-line evaluation;
- semantic event evaluation when operational labels can be derived consistently;
- motion-derived layout inference;
- geometry and topology evaluation;
- coverage and robustness studies;
- qualitative examples.

The project must use only the motion and map portions needed for the evaluation. Full raw sensor acquisition is outside the core scope.

### 2.3 Optional secondary datasets

A secondary dataset may be introduced only when it provides one of the following:

- an independent validation domain;
- stronger pedestrian coverage;
- annotated trajectory events unavailable in the primary dataset;
- a small end-to-end perception demonstration.

A secondary dataset must not change the frozen primary protocol without explicit approval.

## 3. Dataset acquisition policy

### 3.1 Bounded acquisition

Acquisition scripts must:

- download only required subsets;
- support resume;
- verify expected files;
- record source version;
- avoid duplicate copies;
- report estimated and actual disk use.

### 3.2 Repository policy

The repository must not contain:

- full external datasets;
- restricted raw data;
- dataset credentials;
- large disposable caches.

The repository may contain:

- acquisition instructions;
- adapter code;
- small synthetic fixtures;
- tiny real-data fixtures only when licensing permits;
- checksums and manifests;
- split identifiers.

### 3.3 Local directory structure

Recommended local structure:

```text
data/
├── external/
│   └── av2/
├── canonical/
│   ├── scenarios/
│   ├── trajectories/
│   ├── agents/
│   └── maps/
├── splits/
├── synthetic/
├── cache/
└── manifests/
```

Large directories are ignored by Git unless a later policy explicitly allows selected artifacts.

## 4. Dataset versioning

Every dataset artifact must record:

- dataset identifier;
- dataset version;
- source release;
- adapter version;
- acquisition date;
- source-file checksums when practical;
- canonical schema versions;
- preprocessing configuration.

A dataset update creates a new canonical artifact set rather than silently replacing the previous one.

## 5. Split hierarchy

The project uses the following hierarchy:

```text
Synthetic correctness set
        ↓
Real-data smoke set
        ↓
Development set
        ↓
Pilot evaluation set
        ↓
Frozen primary test set
        ↓
Optional held-out-city evaluations
```

Each level has a distinct purpose.

## 6. Synthetic correctness set

The synthetic set must include, at minimum:

- straight constant-speed motion;
- acceleration and deceleration;
- full stop;
- left and right turns;
- irregular sampling;
- missing-sample gaps;
- intersecting but disconnected paths;
- T-junction;
- four-way junction;
- merge;
- split;
- disconnected components;
- grade-separated crossing;
- corridor closure;
- successful reroute;
- impossible reroute.

Synthetic cases must have stable identifiers and known expected outputs.

## 7. Real-data smoke set

The smoke set is a very small fixed collection used for:

- adapter validation;
- command-line smoke tests;
- pipeline integration;
- viewer checks;
- resource sanity checks.

Target scale:

- approximately 5 to 10 scenarios;
- at least two scene types;
- at least two agent classes when available;
- at least one map-rich intersection scene.

The smoke set is not used for final statistical conclusions.

## 8. Development set

The development set is used for:

- implementation debugging;
- parameter selection;
- threshold tuning;
- algorithm design;
- metric validation;
- visualization development;
- performance profiling.

Rules:

- all method tuning occurs here;
- failed development attempts may be retained in notes but need not become final experiments;
- development outputs are clearly labelled;
- development-set results are not presented as frozen test evidence.

## 9. Pilot evaluation set

The pilot set is used for:

- verifying runtime and storage estimates;
- validating experiment manifests;
- checking that frozen metrics behave sensibly;
- identifying operational failures before the full campaign;
- confirming that result aggregation works.

The pilot set must not be repeatedly mined for algorithm tuning.

Small corrective changes discovered through the pilot must be documented before the primary test is opened.

## 10. Frozen primary test set

The frozen primary test set is used for confirmatory evaluation.

Once frozen:

- scenario or tile identifiers are versioned and committed;
- parameters are not tuned using test outcomes;
- test failures are recorded rather than silently excluded;
- changes require a new experiment version;
- raw test results are immutable;
- exploratory analyses on test results are labelled as exploratory.

## 11. Optional held-out-city evaluation

When feasible, the project may perform city-held-out evaluation.

A preferred design is:

- select one city as held out;
- develop or fit nonlearned parameters on the remaining cities;
- evaluate on the held-out city;
- repeat for each city or for a manageable subset.

This evaluation is secondary unless the full protocol and compute budget are frozen before the campaign.

## 12. Geographic leakage control

### 12.1 Leakage unit

For layout inference, leakage must be controlled at a geographic unit larger than an individual trajectory.

The preferred unit is a nonoverlapping geographic tile or source map region.

### 12.2 Rule

No primary test tile may overlap spatially with a development tile after accounting for any tile buffer used by the method.

### 12.3 Scenario overlap

When multiple scenarios cover the same physical region:

- they must be assigned to the same split for layout evaluation; or
- the repeated region must be excluded from confirmatory layout evaluation.

### 12.4 Motion-codec evaluation

For trajectory-codec evaluation, scenario-level separation is required.

If repeated tracks or duplicate source records exist, they must remain within one split.

## 13. Geographic tiling policy

Exact tile size will be tuned on the development set and then frozen.

Initial candidate sizes:

- 50 m × 50 m;
- 75 m × 75 m;
- 100 m × 100 m.

Potential context buffer:

- 5 m to 20 m outside the scored tile.

Rules:

- each tile has a stable identifier;
- the scored interior and context buffer are distinct;
- only the interior contributes to primary geometry metrics unless defined otherwise;
- trajectories are clipped or assigned according to one deterministic policy;
- tile overlap is prohibited in the primary split unless the overlap grouping is treated as one leakage unit.

## 14. Scenario eligibility

A scenario is eligible for trajectory-codec evaluation when:

- the canonical adapter succeeds;
- timestamps are valid;
- at least one eligible dynamic agent exists;
- the trajectory meets the minimum duration and sample rules;
- required coordinate metadata are available;
- corruption or missing-data issues do not make the unit uninterpretable.

A scenario is eligible for layout evaluation when:

- a usable source vector map exists;
- enough trajectories intersect the candidate tile;
- map and trajectory frames align successfully;
- the scored region contains at least one target layout element;
- ambiguity from missing map coverage is below the approved threshold.

## 15. Agent eligibility

An agent trajectory is eligible when:

- the agent class is in the approved scope;
- sample count meets the minimum;
- duration meets the minimum;
- timestamps are strictly increasing after canonicalization;
- positions are finite;
- the trajectory contains sufficient motion for the experiment.

Initial candidate minimums:

- at least 10 canonical samples;
- at least 1 second duration.

Final values are selected on the development set and frozen before the pilot.

## 16. Stationary and near-stationary tracks

Stationary tracks are handled explicitly.

For motion-codec evaluation:

- they may be included in a dedicated stratum;
- they must not dominate aggregate geometric metrics;
- event definitions must distinguish a stationary trajectory from a detected stop during motion.

For layout inference:

- fully stationary tracks are excluded from corridor estimation;
- endpoint or access-zone inference may use them only under a later approved rule.

## 17. Missing-data filtering

The canonical adapter must record:

- original sample count;
- retained sample count;
- missing intervals;
- duplicate timestamps;
- interpolation performed;
- exclusion reason when applicable.

Rules:

- long gaps are not silently interpolated;
- a track crossing a gap may be split into valid subtracks;
- gap thresholds are configuration values;
- exclusion counts are reported by dataset, split, city, and agent class.

## 18. Duplicate handling

Potential duplicates include:

- identical scenarios;
- repeated tracks;
- copied source files;
- overlapping map extracts;
- the same physical region observed multiple times.

Duplicate detection may use:

- source identifiers;
- checksums;
- spatial-temporal signatures;
- map-region identifiers.

Confirmed duplicates must remain in one split or be deduplicated before splitting.

## 19. Agent-class policy

Initial primary classes:

- vehicle;
- pedestrian;
- cyclist.

The primary experiment may focus on vehicles if pedestrian and cyclist support is insufficient.

Rules:

- class mappings are explicit in the adapter;
- unknown classes are not forced into a known category;
- class-specific results are reported when sample sizes permit;
- all-class aggregation must state its weighting method.

## 20. Ego and focal-agent policy

The project distinguishes:

- ego agent;
- focal agent;
- other observed agents.

For motion representation:

- ego and focal agents may be included if they meet eligibility criteria;
- class and role are recorded.

For layout inference:

- experiments must include an ablation comparing:
  - ego included;
  - ego excluded.

This tests whether layout recovery depends excessively on a privileged trajectory.

## 21. Source-map policy

The source vector map has two roles:

1. evaluation target;
2. oracle grammar input.

For trajectory-only inference:

- source-map geometry is withheld from the inference method;
- source-map topology is withheld;
- source semantic labels are withheld except where required only to define the evaluation region.

For oracle grammar:

- source-map geometry and topology may be converted directly;
- outputs are labelled `oracle_converted`;
- oracle results test representation capacity, not inference.

## 22. Coverage subsampling

Coverage experiments estimate performance as trajectory evidence decreases.

Primary target fractions:

- 10%;
- 25%;
- 50%;
- 100%.

Optional additional fractions:

- 5%;
- 75%.

### 22.1 Sampling unit

The default sampling unit is the trajectory, not the trajectory point.

All samples of a selected trajectory remain together.

### 22.2 Stratification

Coverage subsampling should preserve, where practical:

- agent-class proportions;
- movement-direction diversity;
- scenario distribution.

The exact stratification rule is fixed before the full campaign.

### 22.3 Seeds

Each fraction uses multiple deterministic seeds.

Initial target:

- five seeds per nontrivial fraction.

The 100% condition needs no stochastic seed unless another stochastic method component exists.

### 22.4 Nested sampling

Preferred behavior is nested subsampling within each seed:

```text
10% ⊆ 25% ⊆ 50% ⊆ 100%
```

This improves interpretability of coverage curves.

## 23. Noise and corruption protocols

Robustness studies may modify canonical trajectories after split assignment.

Planned perturbations include:

- additive localization noise;
- random sample removal;
- contiguous observation gaps;
- heading noise;
- trajectory truncation.

Rules:

- perturbations never alter the unmodified canonical source artifact;
- perturbation configuration and seed are recorded;
- the same perturbed input is shared across methods;
- perturbations are generated independently of ground-truth map labels.

## 24. Random seed policy

Every stochastic split or subsampling operation uses:

- one declared root seed;
- deterministic derived seeds;
- stable identifiers;
- no dependence on worker order.

Split manifests contain the exact selected identifiers, so rerunning split generation is not required to reproduce the campaign.

## 25. Split-manifest contents

Every split manifest records:

- manifest identifier;
- dataset identifier and version;
- adapter version;
- split-policy version;
- root seed;
- scenario identifiers;
- tile identifiers where applicable;
- excluded identifiers;
- exclusion reasons;
- city or region distribution;
- agent-class distribution;
- creation command;
- source code revision.

## 26. Split-generation workflow

```text
Acquire source data
    ↓
Canonicalize
    ↓
Validate and deduplicate
    ↓
Group by leakage-control unit
    ↓
Create smoke/development/pilot/test assignments
    ↓
Validate geographic separation
    ↓
Write immutable split manifests
    ↓
Freeze primary test identifiers
```

## 27. Recommended initial scale

The exact scale depends on measured disk and runtime cost.

Initial planning targets:

### Smoke

- 5–10 scenarios.

### Development

- approximately 50 scenarios;
- approximately 10–20 layout tiles.

### Pilot

- approximately 50–100 distinct scenarios;
- approximately 10–20 distinct layout tiles.

### Frozen motion test

- approximately 500–1,000 scenarios, subject to eligibility and laptop runtime.

### Frozen layout test

- approximately 30–60 nonoverlapping geographic tiles with sufficient repeated trajectory coverage.

These are planning targets, not mandatory sample sizes. The final frozen counts must be reported exactly.

## 28. Storage budget policy

Dataset preparation must keep:

- source motion/map data;
- canonical Parquet data;
- temporary cache;
- experiment outputs;

within the hardware budget defined in Batch 0.10.

Requirements:

- no unnecessary sensor data;
- partitioned canonical tables;
- cache eviction policy;
- output-size checks before large sweeps;
- at least 15% SSD free space maintained during normal operation.

## 29. Exclusion reporting

Every excluded unit must receive one primary exclusion reason.

Initial reason categories:

- adapter failure;
- invalid timestamps;
- insufficient samples;
- insufficient duration;
- unsupported agent class;
- no usable map;
- insufficient trajectory coverage;
- coordinate-frame mismatch;
- duplicate;
- geographic leakage conflict;
- invalid geometry;
- resource-limit exclusion;
- other documented reason.

Reports must include counts before and after filtering.

## 30. Prohibited tuning practices

The following are prohibited after the primary test is frozen:

- changing method parameters in response to test performance;
- changing event thresholds based on test labels;
- selecting only favorable test cities or tiles;
- removing hard test cases without reporting them;
- changing metric tolerances after seeing method rankings;
- choosing coverage seeds based on results;
- redefining the experimental unit to increase significance.

Any unavoidable correction requires a new experiment version and an explicit record.

## 31. Pilot-to-test transition

Before opening the frozen test set, verify:

- adapters are stable;
- schemas are frozen for the campaign;
- all primary metrics run successfully;
- all baselines run successfully;
- output storage estimates are acceptable;
- result manifests are complete;
- failed-unit handling is tested;
- statistical scripts are ready;
- qualitative generation does not alter raw outputs.

## 32. Data-quality reporting

The final dataset report must include:

- source dataset version;
- acquired data volume;
- canonical data volume;
- scenario counts;
- agent counts by class;
- trajectory duration distribution;
- sample-count distribution;
- city or region distribution;
- exclusion counts and reasons;
- tile coverage distribution;
- map-element distribution;
- known annotation limitations.

## 33. Licensing and attribution

The repository must document:

- dataset license;
- required citations;
- redistribution restrictions;
- whether derived identifiers may be committed;
- whether small fixtures may be redistributed;
- how users obtain the source data themselves.

No dataset artifact is published merely because it is technically small.

## 34. Failure policy

If the primary dataset cannot support one claim adequately:

- do not silently replace the claim;
- document the limitation;
- narrow the claim;
- add a secondary dataset only through an approved decision;
- preserve the original negative or inconclusive result.

## 35. Acceptance criteria

This document is acceptable when:

- synthetic, smoke, development, pilot, and test roles are distinct;
- geographic leakage control is explicit;
- motion and layout experiments use appropriate split units;
- dataset adapters remain isolated;
- eligibility and exclusion rules are documented;
- coverage subsampling is deterministic;
- source maps are withheld from trajectory-only inference;
- ego-agent dependence can be tested;
- test-set tuning is prohibited;
- acquisition and storage fit the reference laptop;
- final split manifests can reproduce every evaluated identifier.
