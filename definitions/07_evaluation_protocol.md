# Metrics, Baselines, and Ablations

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.8 — Metrics, Baselines, and Ablations  
**Status:** Draft for approval

## 1. Purpose

This document defines the quantitative evaluation protocol for the KinematicWeave project.

It freezes:

- the primary and secondary metrics;
- the baseline methods;
- the required ablations;
- metric inputs and outputs;
- matching procedures;
- invalid and degenerate cases;
- fairness rules;
- reporting conventions.

Exact experiment identifiers and statistical procedures are defined in later Phase 0 batches.

## 2. General evaluation principles

### 2.1 Experimental units

The primary experimental units are:

- trajectory or agent-track for motion representation;
- geographic tile for layout inference;
- tape or scenario for deterministic replay;
- edit scenario for edit locality;
- workload for performance and resource evaluation.

Individual trajectory points are not treated as independent samples for inferential statistics.

### 2.2 Paired comparisons

Where possible, methods are evaluated on the same units under the same:

- input data;
- split;
- perturbation;
- storage or control-point budget;
- random seed;
- metric configuration.

### 2.3 Primary versus secondary metrics

A **primary metric** directly supports a primary hypothesis.

A **secondary metric** explains behavior, detects failure modes, or measures practical cost.

Method claims must not be based only on secondary metrics when the primary metric is unfavorable.

### 2.4 Undefined values

A metric may be undefined when:

- no eligible reference item exists;
- no eligible prediction exists;
- matching cannot be performed;
- geometry is invalid;
- the unit violates metric preconditions.

Undefined values are recorded explicitly and are not silently converted to zero.

### 2.5 Metric registry

Every metric implementation must register:

- metric identifier;
- version;
- input contract;
- output unit;
- configuration fields;
- invalid conditions;
- aggregation guidance.

## 3. Motion representation methods

### 3.1 M0 — Raw samples

Stores the complete canonical trajectory.

Purpose:

- fidelity ceiling;
- storage reference;
- decode-cost reference.

Required payload:

- all canonical timestamps or exact reconstruction rule;
- all represented positions;
- declared numeric precision;
- required identifiers and frame metadata.

### 3.2 M1 — Uniform temporal subsampling

Retains samples at a fixed temporal stride or target count and reconstructs with piecewise-linear interpolation.

Purpose:

- simplest compact baseline.

Required sweep dimensions:

- retained-sample fraction;
- target serialized budget.

### 3.3 M2 — RDP polyline simplification

Uses Ramer–Douglas–Peucker or an equivalent deterministic polyline simplification rule.

Purpose:

- geometry-aware baseline.

Requirements:

- timestamps retained for selected vertices;
- deterministic tie breaking;
- declared simplification tolerance or budget-selection rule.

### 3.4 M3 — Matched-control-point spline

Fits a cubic spline or another approved smooth curve using a control-point count matched to another representation.

Purpose:

- smooth reconstruction baseline.

Requirements:

- knots, boundary conditions, and timing parameterization are serialized;
- hidden spline parameters count toward size.

### 3.5 M4 — Event-free procedural lines

Uses the same procedural-line structure as the proposed method but does not segment or annotate based on semantic events.

Purpose:

- isolates the contribution of event-aware segmentation and metadata.

### 3.6 M5 — Event-aware procedural lines

The proposed reference representation.

Initial behavior:

- detect stop and turn events;
- segment at selected semantic boundaries;
- encode motion using piecewise-linear segments;
- retain event metadata required by replay and evaluation.

## 4. Motion metrics

### 4.1 Average displacement error

**Identifier:** `motion.ade_m`

For reference positions \(p_t\) and decoded positions \(\hat p_t\) evaluated at the same valid timestamps:

\[
\mathrm{ADE} =
\frac{1}{N}
\sum_{t=1}^{N}
\lVert p_t - \hat p_t \rVert_2
\]

Primary computation is planar unless an experiment explicitly declares 3D evaluation.

**Unit:** metres  
**Undefined when:** no common valid timestamps exist.

### 4.2 Final displacement error

**Identifier:** `motion.fde_m`

\[
\mathrm{FDE} =
\lVert p_T - \hat p_T \rVert_2
\]

where \(T\) is the final common valid timestamp.

**Unit:** metres  
**Role:** secondary, because many tracks are not forecasting tasks.

### 4.3 Maximum displacement error

**Identifier:** `motion.max_deviation_m`

\[
\max_t \lVert p_t - \hat p_t \rVert_2
\]

**Unit:** metres  
**Role:** primary for detecting local failures hidden by ADE.

### 4.4 Heading error

**Identifier:** `motion.heading_mae_rad`

For valid reference and reconstructed headings:

\[
\frac{1}{N}
\sum_t
\left|
\mathrm{wrap}(\theta_t - \hat \theta_t)
\right|
\]

**Unit:** radians  
**Undefined when:** no valid comparable headings exist.

### 4.5 Speed error

**Identifier:** `motion.speed_mae_mps`

Mean absolute difference in planar speed over common valid timestamps.

**Unit:** metres per second  
**Role:** secondary.

### 4.6 Control points per agent-second

**Identifier:** `motion.control_points_per_agent_second`

\[
\frac{\text{stored control-point count}}
{\text{represented duration in seconds}}
\]

**Unit:** count per second.

### 4.7 Bytes per agent-second

**Identifier:** `motion.bytes_per_agent_second`

\[
\frac{\text{canonical serialized payload bytes}}
{\text{represented duration in seconds}}
\]

**Unit:** bytes per second  
**Role:** primary compactness metric.

### 4.8 Compression ratio

**Identifier:** `motion.compression_ratio`

\[
\frac{\text{raw-sample canonical size}}
{\text{method canonical size}}
\]

**Unit:** ratio  
**Undefined when:** method size is zero or raw representation is invalid.

### 4.9 Encode time

**Identifier:** `motion.encode_time_ms`

Wall-clock encoding time measured under the benchmark protocol.

**Unit:** milliseconds.

### 4.10 Decode time

**Identifier:** `motion.decode_time_ms`

Wall-clock time to reconstruct the declared query schedule.

**Unit:** milliseconds.

## 5. Semantic event definitions

Initial event classes:

- stop;
- turn.

Exact detector thresholds are selected on the development set and frozen before the pilot.

### 5.1 Stop event

A stop is a maximal interval satisfying the approved speed and duration conditions.

Required configurable fields:

- speed threshold;
- minimum duration;
- gap-merging tolerance;
- minimum displacement condition.

A trajectory that is stationary for its full duration may be categorized separately from an in-motion stop.

### 5.2 Turn event

A turn is a time-localized interval satisfying approved heading-change and displacement conditions.

Required configurable fields:

- minimum absolute heading change;
- minimum path length;
- smoothing window;
- maximum event duration;
- left/right classification rule.

## 6. Event matching

### 6.1 Candidate matching

A predicted and reference event are candidates when:

- event types match;
- temporal overlap exceeds the configured minimum, or representative times fall within the temporal tolerance;
- optional spatial distance is within the configured threshold.

### 6.2 One-to-one assignment

Matching uses deterministic maximum-weight bipartite assignment.

Preferred match score combines:

- temporal overlap;
- representative-time proximity;
- spatial proximity.

Exact weights are configuration values.

### 6.3 Tie breaking

Equal-score ties are broken by:

1. smaller temporal difference;
2. smaller spatial difference;
3. lexicographically smaller event identifier.

### 6.4 Event precision, recall, and F1

For each event type:

\[
\mathrm{Precision} =
\frac{TP}{TP + FP}
\]

\[
\mathrm{Recall} =
\frac{TP}{TP + FN}
\]

\[
\mathrm{F1} =
\frac{2PR}{P + R}
\]

Undefined-denominator behavior:

- precision is undefined when no predictions exist;
- recall is undefined when no references exist;
- F1 is undefined when precision or recall is undefined.

Aggregated reports must show prevalence and undefined counts.

### 6.5 Event timing error

**Identifier:** `event.representative_time_mae_s`

Mean absolute difference between matched event representative times.

### 6.6 Event duration error

**Identifier:** `event.duration_mae_s`

Mean absolute duration error for matched interval events.

## 7. Motion evaluation conditions

### 7.1 Matched serialized size

Primary comparison condition.

Methods are compared at common byte budgets according to the serialization specification.

### 7.2 Matched control-point count

Secondary diagnostic comparison.

All hidden knots, event boundaries, and required control values are counted.

### 7.3 Error-budget comparison

Optional secondary comparison.

For a target geometric error, report the minimum achieved serialized size.

### 7.4 Pareto frontier

The main motion figure plots:

- x-axis: bytes per agent-second;
- y-axis: reconstruction error or event F1;
- separate curves per method.

A method is Pareto-dominated when another method is no worse on both axes and strictly better on at least one.

## 8. Required motion ablations

### A1 — No event-aware segmentation

Compare M5 to M4.

### A2 — Events stored but not used for segmentation

Tests whether event metadata alone explains gains.

### A3 — Segmentation without event metadata

Tests whether boundary placement alone explains gains.

### A4 — Piecewise-linear versus spline interpolation

Uses the same segment boundaries where feasible.

### A5 — Float32 versus float64 storage

Measures precision–size tradeoff.

### A6 — Stop-only versus turn-only versus both

Tests event-class contribution.

### A7 — Event thresholds

Development-only sensitivity study followed by frozen settings.

### A8 — Irregular-sampling handling

Compare canonical timestamp-aware behavior to any fixed-rate simplification assumption.

## 9. Layout inference methods

### 9.1 L0 — Density raster and skeletonization

Pipeline:

1. rasterize trajectory density;
2. threshold;
3. perform morphological cleanup;
4. skeletonize;
5. convert skeleton to graph.

Purpose:

- simple map-from-motion baseline.

All raster parameters are frozen after development.

### 9.2 L1 — Directional clustering and centerline fitting

Pipeline:

1. resample trajectories spatially;
2. construct position-direction features;
3. cluster samples;
4. fit centerlines;
5. derive preliminary graph.

No grammar-based topology repair.

### 9.3 L2 — Full grammar-assisted inference

Starts from L1 geometry and applies approved symbolic topology-repair rules.

### 9.4 L3 — Oracle grammar

Converts the ground-truth map into the project grammar.

Purpose:

- representation-capacity evaluation;
- compactness upper bound;
- runtime and query testing.

It is not an inference baseline.

## 10. Layout geometry metrics

### 10.1 Traversable-region IoU

**Identifier:** `layout.region_iou`

For predicted region \(P\) and reference region \(R\):

\[
\mathrm{IoU} =
\frac{|P \cap R|}
{|P \cup R|}
\]

**Unit:** ratio  
**Undefined when:** the union is empty.

Primary computation uses the scored tile interior.

### 10.2 Centerline precision within tolerance

**Identifier:** `layout.centerline_precision`

Sample the predicted centerlines at the declared spatial interval.

Precision is the fraction of predicted samples whose nearest reference centerline distance is within tolerance \(\tau\).

### 10.3 Centerline recall within tolerance

**Identifier:** `layout.centerline_recall`

Sample reference centerlines at the same interval.

Recall is the fraction of reference samples whose nearest predicted centerline distance is within \(\tau\).

### 10.4 Centerline F1

**Identifier:** `layout.centerline_f1`

Harmonic mean of centerline precision and recall.

### 10.5 Symmetric centerline distance

**Identifier:** `layout.symmetric_centerline_distance_m`

\[
\frac{1}{2}
\left(
\frac{1}{|P_s|}
\sum_{p \in P_s} d(p, R)
+
\frac{1}{|R_s|}
\sum_{r \in R_s} d(r, P)
\right)
\]

where \(P_s\) and \(R_s\) are uniformly sampled centerline point sets.

**Unit:** metres.

### 10.6 Width error

**Identifier:** `layout.corridor_width_mae_m`

Optional when comparable reference widths exist.

## 11. Junction matching and metrics

### 11.1 Candidate junction match

Predicted and reference junctions are candidates when:

- planar distance is within a configured radius;
- their incident-corridor direction signatures are compatible;
- grade-separation status does not prohibit the match.

### 11.2 Assignment

Use deterministic one-to-one minimum-cost matching.

Cost may include:

- junction distance;
- degree difference;
- incident-angle difference.

### 11.3 Junction precision, recall, and F1

Calculated from matched and unmatched junctions.

Identifiers:

- `layout.junction_precision`;
- `layout.junction_recall`;
- `layout.junction_f1`.

### 11.4 Junction degree error

**Identifier:** `layout.junction_degree_mae`

Mean absolute degree difference across matched junctions.

## 12. Graph matching

### 12.1 Node correspondence

Graph-node correspondence is established through:

- matched junctions;
- matched access zones;
- matched corridor endpoints;
- deterministic geometric matching.

### 12.2 Edge precision and recall

A predicted graph edge is correct when:

- its endpoint correspondences match a reference connection;
- directionality is compatible;
- the traversed geometry is compatible within the configured tolerance.

Identifiers:

- `layout.graph_edge_precision`;
- `layout.graph_edge_recall`;
- `layout.graph_edge_f1`.

### 12.3 Connected-component agreement

**Identifier:** `layout.component_agreement`

Reports agreement in pairwise reachability induced by connected components.

For sampled node pairs:

- true positive: connected in both graphs;
- true negative: disconnected in both;
- disagreements are recorded.

The primary summary is balanced accuracy or an equivalent frozen statistic.

### 12.4 Origin–destination connectivity agreement

**Identifier:** `layout.od_connectivity_agreement`

For a fixed set of matched origin–destination pairs, compare whether a route exists in both predicted and reference graphs.

\[
\frac{\text{pairs with matching connected/disconnected status}}
{\text{valid tested pairs}}
\]

### 12.5 Route-length error

**Identifier:** `layout.route_length_relative_error`

For OD pairs connected in both graphs:

\[
\frac{|\hat L - L|}{L}
\]

where \(L\) is reference shortest-path length.

### 12.6 Invalid connection count

**Identifier:** `layout.false_connection_count`

Counts predicted connections that join reference-disconnected structures.

This is especially important for topology-repair evaluation.

## 13. Layout compactness metrics

### 13.1 Grammar node count

**Identifier:** `layout.grammar_node_count`

### 13.2 Grammar relation count

**Identifier:** `layout.grammar_relation_count`

### 13.3 Graph node and edge counts

Identifiers:

- `layout.graph_node_count`;
- `layout.graph_edge_count`.

### 13.4 Serialized layout size

**Identifier:** `layout.serialized_size_bytes`

Includes:

- geometry;
- hierarchy;
- graph;
- semantic attributes;
- required metadata.

### 13.5 Bytes per square metre

**Identifier:** `layout.bytes_per_square_metre`

Optional compactness normalization by scored tile area.

## 14. Required layout ablations

### B1 — No direction features

Cluster using only planar position.

### B2 — No grammar repair

Compare L2 to L1.

### B3 — Individual repair-rule removal

Disable each major repair rule one at a time.

### B4 — Ego included versus excluded

Tests dependence on ego motion.

### B5 — Vehicles only versus all eligible classes

Tests agent-class contribution.

### B6 — Coverage sweep

Evaluate 10%, 25%, 50%, and 100% trajectory coverage.

### B7 — Localization noise

Apply approved planar noise levels.

### B8 — Random missing trajectories

Use deterministic coverage subsampling.

### B9 — Contiguous trajectory gaps

Tests sensitivity to occlusion-like missing intervals.

### B10 — Tile size sensitivity

Development-only study used to freeze tile scale.

### B11 — Grade-separation handling

Compare with and without elevation or separation cues where available.

## 15. Replay metrics

### 15.1 State-hash equality

**Identifier:** `runtime.replay_hash_match`

Boolean equality of canonical replay sequence hashes.

Primary determinism result.

### 15.2 Query-result hash equality

**Identifier:** `runtime.query_hash_match`

Boolean equality across repeated query executions.

### 15.3 Replay throughput

**Identifier:** `runtime.agent_updates_per_second`

\[
\frac{\text{total agent state updates}}
{\text{elapsed seconds}}
\]

### 15.4 Frame-time percentiles

Identifiers:

- `runtime.frame_time_p50_ms`;
- `runtime.frame_time_p95_ms`;
- `runtime.frame_time_p99_ms`.

### 15.5 Query latency

Identifiers are query-specific, for example:

- `runtime.state_query_p95_ms`;
- `runtime.spatial_query_p95_ms`;
- `runtime.semantic_query_p95_ms`.

## 16. Edit metrics

### 16.1 Modified record count

**Identifier:** `edit.modified_record_count`

Number of persistent representation records changed or added.

### 16.2 Modified symbol count

**Identifier:** `edit.modified_symbol_count`

Counts changed procedural segments, events, grammar nodes, graph edges, and edit records according to the frozen counting rule.

### 16.3 Modified serialized bytes

**Identifier:** `edit.modified_serialized_bytes`

Byte-level difference attributable to the edit under the canonical branch representation.

### 16.4 Edit latency

**Identifier:** `edit.apply_time_ms`

### 16.5 Affected agent count

**Identifier:** `edit.affected_agent_count`

### 16.6 Continuity violations

**Identifier:** `edit.continuity_violation_count`

Counts discontinuities exceeding approved position, speed, heading, or acceleration limits.

### 16.7 Closed-corridor violations

**Identifier:** `edit.closed_corridor_violation_count`

Counts edited trajectories that still use a closed corridor.

### 16.8 Rerouting success

**Identifier:** `edit.rerouting_success_rate`

Fraction of eligible affected agents receiving a valid route.

## 17. Required edit baselines

For each supported semantic edit, compare against a raw-sample edit procedure.

### Delay baseline

Directly shift or duplicate affected raw samples according to the approved rule.

### Segment-replacement baseline

Rewrite all raw samples in the replaced time interval.

### Corridor-closure baseline

Identify and rewrite all raw trajectories using the closed corridor.

### Branch baseline

Store a complete copied raw trajectory set for the alternate scenario.

Baseline procedures must preserve the same intended outcome and validity checks.

## 18. Resource metrics

### 18.1 Peak RAM

**Identifier:** `resource.peak_rss_bytes`

### 18.2 Peak GPU memory

**Identifier:** `resource.peak_gpu_memory_bytes`

### 18.3 Disk read and write

Identifiers:

- `resource.disk_read_bytes`;
- `resource.disk_write_bytes`.

### 18.4 Output size

**Identifier:** `resource.output_bytes`

### 18.5 Wall-clock time

**Identifier:** `resource.wall_time_s`

### 18.6 Resumability

**Identifier:** `resource.resume_recomputed_unit_count`

Counts completed units recomputed after an intentional interruption.

Target for a correct resumable run is zero, excluding units that were incomplete at interruption.

## 19. Fairness rules

### 19.1 Equivalent inputs

All methods receive the same canonical source records for a comparison.

### 19.2 No hidden map input

Trajectory-only layout methods cannot access ground-truth map geometry or topology.

### 19.3 Equivalent metadata accounting

Required reconstruction metadata is included for every codec.

### 19.4 Equivalent perturbations

Noise and missing-data variants are generated once per unit and shared across methods.

### 19.5 Equivalent evaluation timestamps

Motion methods are decoded at the same canonical timestamps.

### 19.6 Parameter tuning

All thresholds and hyperparameters are selected using development data only.

### 19.7 Compute limits

A method may use more computation, but runtime and resources must be reported.

No baseline is intentionally underoptimized in a way that changes its scientific validity.

## 20. Degenerate-case policy

### 20.1 Empty prediction

Geometry metrics:

- precision may be undefined or zero according to the frozen metric definition;
- recall is zero when reference geometry exists;
- all cases are counted explicitly.

### 20.2 Empty reference

The tile is normally ineligible for primary layout scoring.

If retained diagnostically, metrics are marked undefined rather than favorable.

### 20.3 No events

Per-unit event recall is undefined when no reference events exist.

Aggregate event analysis must report:

- event-present units;
- event-absent units;
- false positives on event-absent units.

### 20.4 Zero-duration trajectory

Ineligible.

### 20.5 Invalid geometry

Attempted repair must be flagged.

If repair fails, geometry-based metrics are invalid for that unit and failure is recorded.

### 20.6 Disconnected graph

Disconnected graphs are valid.

Connectivity metrics must evaluate the disconnection rather than treating it as an error condition.

## 21. Aggregation guidance

Exact statistical aggregation is defined in Batch 0.9.

Metric implementations must still expose per-unit records.

Default descriptive summaries include:

- median;
- mean;
- standard deviation;
- interquartile range;
- 95% confidence interval;
- valid unit count;
- undefined unit count;
- failure count.

Micro-averaging and macro-averaging must be clearly distinguished.

## 22. Required primary outputs

### Motion

- ADE versus bytes per agent-second;
- maximum deviation versus bytes per agent-second;
- stop and turn F1 at matched budgets;
- encode/decode cost table;
- event-aware ablation table.

### Layout

- geometry metric table;
- topology metric table;
- coverage curves;
- topology-repair ablation;
- robustness curves;
- oracle grammar compactness table.

### Runtime and edits

- replay determinism report;
- scalability curves;
- query-latency table;
- edit-locality table;
- resource-usage report.

## 23. Qualitative linkage

Every qualitative example must link to:

- scenario or tile identifier;
- method;
- configuration;
- seed;
- relevant metric records;
- artifact manifest.

Qualitative selection must include both representative successes and failures.

## 24. Acceptance criteria

This document is acceptable when:

- every primary hypothesis has at least one primary metric;
- every metric has a mathematical or operational definition;
- matching algorithms and tie breaking are explicit;
- invalid and degenerate cases are documented;
- every baseline receives equivalent inputs;
- storage comparisons follow the canonical serialization rules;
- geometry and topology are evaluated separately;
- topology repair is compared against the identical unrepaired geometry;
- raw-sample edit baselines are defined;
- per-unit raw metrics can support later statistical analysis;
- no primary claim depends solely on qualitative evidence.
