# Statistical Analysis Plan

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.9 — Experiment Matrix and Statistical Analysis Plan  
**Status:** Draft for approval

## 1. Purpose

This document defines how experiment results are aggregated, compared, and interpreted.

The plan ensures that:

- the correct experimental unit is preserved;
- comparisons are paired where possible;
- uncertainty and effect sizes are reported;
- trajectory samples are not treated as independent units;
- confirmatory multiple comparisons are controlled;
- negative, mixed, failed, and undefined outcomes remain visible.

## 2. Experimental units

Primary units are:

- trajectory for motion representation;
- geographic tile for layout inference;
- tape or scenario for determinism;
- edit scenario for editing;
- workload or run for performance and resources.

Trajectories within the same scenario may be correlated. Coverage seeds within the same tile are nested replicates, not independent tiles.

## 3. Raw-result requirement

All analyses begin from per-unit raw metric records.

Every result must retain:

- unit identifier;
- method and variant;
- condition and budget;
- seed;
- metric value and unit;
- validity status;
- failure reason where applicable.

## 4. Paired comparisons

Methods evaluated on the same unit are compared using paired analysis.

A unit enters a paired numeric comparison only when both methods have valid values. Differences in method failure rates are analyzed separately and must not be hidden by pairwise deletion.

## 5. Scenario-aware motion analysis

Primary motion analysis must account for within-scenario dependence using one frozen approach:

1. aggregate trajectory metrics to a scenario-level summary before inferential comparison; or
2. use a hierarchical bootstrap with scenario as the top-level resampling unit.

The chosen approach must be used consistently across methods.

## 6. Tile-aware layout analysis

The geographic tile is the top-level unit.

For coverage or perturbation experiments:

1. compute results for each seed within a tile;
2. aggregate seeds within the tile according to the frozen rule;
3. aggregate across tiles;
4. construct uncertainty intervals over tiles.

Seeds must not be treated as additional independent geographic observations.

## 7. Descriptive summaries

For continuous metrics, report where relevant:

- mean;
- median;
- standard deviation;
- interquartile range;
- minimum and maximum;
- valid-unit count;
- undefined count;
- failure count.

For proportions, report numerator, denominator, proportion, and confidence interval.

For latency, report p50, p95, p99, mean, and maximum where useful.

## 8. Confidence intervals

### 8.1 Default method

Use nonparametric bootstrap 95% confidence intervals.

Default settings:

- 10,000 resamples for final confirmatory analysis;
- 2,000 resamples for pilot analysis;
- fixed recorded bootstrap seed;
- percentile or BCa interval selected during implementation and frozen before the primary test.

### 8.2 Paired differences

Primary method comparisons report confidence intervals for paired differences.

For higher-is-better metrics:

\[
\Delta = \text{proposed} - \text{baseline}
\]

For lower-is-better metrics:

\[
\Delta = \text{baseline} - \text{proposed}
\]

Positive values therefore favor the proposed method.

## 9. Hypothesis tests

### 9.1 Continuous paired metrics

Use a two-sided paired permutation test when practical.

A Wilcoxon signed-rank test may be used when the permutation implementation is impractical or the frozen analysis specifies it.

The selected test is fixed per metric family before the full campaign.

### 9.2 Binary paired outcomes

Use McNemar's test or an exact paired alternative.

Examples include route existence agreement and query success.

### 9.3 Determinism

Replay determinism is an exact property, not a significance test.

The criterion is:

```text
unexplained_mismatch_count == 0
```

### 9.4 Feasibility

Laptop feasibility is evaluated against explicit resource ceilings and successful campaign completion, not a p-value.

## 10. Effect sizes

Every primary comparison reports its difference in the metric's natural unit.

Additional effect sizes may include:

- standardized paired effect size;
- rank-biserial correlation;
- paired risk difference;
- odds ratio for paired binary outcomes.

Statistical significance alone is insufficient. Practical magnitude must be discussed.

## 11. Multiple-comparison control

Within each primary hypothesis family, apply Holm correction to confirmatory p-values.

Examples:

- stop and turn F1 within H1;
- several primary topology metrics within H4;
- multiple edit types within H7.

Exploratory analyses are labelled separately and do not become confirmatory after results are observed.

## 12. Missing, undefined, and failed results

Every absent value is classified as one of:

- undefined by metric definition;
- invalid input;
- method failure;
- resource failure;
- experiment interruption;
- predeclared exclusion.

Reports include planned, valid, undefined, failed, and excluded counts.

No failed unit is silently deleted. If one method fails more often, failure rate is a scientific outcome.

## 13. Outlier policy

No valid unit is removed only because its metric value is extreme.

Removal is allowed only for a predeclared reason such as:

- corrupted source data;
- duplicate unit;
- coordinate-frame failure;
- metric precondition violation;
- confirmed implementation bug.

When extreme values affect the mean, report the median and distribution while preserving the original untrimmed confirmatory result.

## 14. Macro and micro aggregation

### 14.1 Event metrics

Report both:

- micro precision, recall, and F1 from aggregated TP/FP/FN;
- macro per-unit event metrics.

Preferred primary event result:

- scenario-aware micro F1 with uncertainty over scenarios.

### 14.2 Motion geometry

Use scenario-aware aggregation of per-trajectory metrics.

Class-balanced summaries may be reported secondarily when class imbalance is substantial.

### 14.3 Layout

Use macro averaging over tiles. Every tile receives equal weight unless a metric explicitly defines area weighting.

### 14.4 Runtime

Report each workload scale separately. Do not pool incompatible scales.

## 15. Fidelity–compactness analysis

For each method:

- evaluate the shared serialized-size budget grid;
- use the frozen nearest-under-budget rule when exact matching is impossible;
- report error and event quality at selected common budgets;
- identify empirical Pareto-dominated points;
- report method availability and failure count at each budget.

Area-under-curve summaries are secondary unless all methods share the same full budget range.

## 16. H1 — Semantic preservation

Primary outcomes:

- stop F1;
- turn F1.

Required safeguards:

- ADE;
- maximum deviation;
- event prevalence;
- false-positive rate on event-absent trajectories.

A semantic improvement is limited when accompanied by unacceptable geometric degradation.

## 17. H2 — Fidelity and compactness

Primary evidence:

- paired error differences at common byte budgets;
- Pareto-frontier participation;
- bytes per agent-second;
- method failure and availability counts.

The conclusion may be limited to specific budgets or trajectory strata.

## 18. H3 — Corridor geometry

Primary outcomes:

- centerline F1;
- symmetric centerline distance.

Secondary outcomes:

- region IoU;
- optional corridor-width error.

The complete coverage curve must be reported. Support may be conditional on a minimum coverage level.

## 19. H4 — Topology recovery

Primary outcomes:

- junction F1;
- graph-edge F1;
- OD connectivity agreement.

False connection count is a required harm metric.

Geometry and topology must be reported separately rather than collapsed into one opaque score.

## 20. H5 — Topology repair

Compare repaired and unrepaired outputs on the same tiles and underlying inferred centerlines.

Primary paired outcomes:

- OD connectivity agreement difference;
- graph-edge F1 difference;
- junction F1 difference;
- false-connection difference.

Repair is supported only when structural gains do not create unacceptable false links.

## 21. H6 — Deterministic replay

Report:

- tapes tested;
- replay schedules;
- process repetitions;
- serialization round trips;
- worker-count conditions;
- hash mismatches.

Any unexplained mismatch prevents an unconditional determinism claim.

## 22. H7 — Edit locality

Primary outcomes:

- modified record count;
- modified symbol count;
- modified serialized bytes.

Interpret locality only when symbolic and raw edits implement equivalent outcomes and pass continuity, closure, and rerouting validity checks.

Each edit type is analyzed separately before any overall summary.

## 23. H8 — Laptop feasibility

Primary outcomes:

- peak RAM;
- peak VRAM;
- disk use;
- wall-clock duration;
- completion status;
- resumability.

H8 is supported only when the frozen core campaign completes on the reference laptop within the Batch 0.10 limits.

## 24. Seed policy

Default confirmatory minimum:

- five seeds for stochastic coverage and perturbation experiments.

Report:

- root seed;
- derived seed count;
- separate streams for perturbation randomness and method randomness where applicable.

More seeds may be added after the pilot only when approved before the frozen campaign.

## 25. Pilot analysis

Pilot analysis verifies:

- metric distributions;
- failure rates;
- runtime;
- confidence-interval implementation;
- storage estimates;
- practical-effect thresholds;
- figure and table generation.

Pilot results must not be mined to choose the most favorable primary outcome.

## 26. Test-set freeze

Before opening the frozen test set:

- metric versions are frozen;
- primary comparisons are declared;
- confidence-interval method is fixed;
- test families and Holm correction are fixed;
- exclusion rules are fixed;
- practical harm thresholds are fixed where used;
- figure templates are prepared.

## 27. Reanalysis policy

A new analysis version is required when:

- a metric bug is fixed;
- an exclusion rule changes;
- an aggregation rule changes;
- a primary test changes;
- a correction family changes.

Previous results remain archived.

## 28. Interpretation labels

Each primary hypothesis is reported as:

- supported;
- partially supported;
- not supported;
- inconclusive.

The report must explain the evidence and boundary conditions behind the label.

## 29. Figures and tables

Every primary figure or table must show or link to:

- method and variant;
- condition or budget;
- valid-unit count;
- uncertainty;
- metric direction;
- undefined and failure counts;
- analysis version.

Avoid truncated axes, omitted baselines, selective seeds, and smoothing that hides measured points.

## 30. Required statistical artifacts

The analysis pipeline must generate:

- aggregated result tables;
- confidence-interval tables;
- paired-difference tables;
- effect-size tables;
- p-value and correction tables;
- missingness and failure summaries;
- figure-ready data;
- analysis manifest.

## 31. Acceptance criteria

This document is acceptable when:

- the correct experimental unit is defined for every claim;
- paired comparisons are the default;
- scenario dependence and tile nesting are addressed;
- bootstrap uncertainty and effect sizes are mandatory;
- Holm correction is defined for confirmatory families;
- determinism and feasibility use exact criteria;
- missing, failed, and undefined outcomes remain visible;
- negative and mixed results are explicitly reportable;
- the full analysis can be frozen before the primary test is opened.
