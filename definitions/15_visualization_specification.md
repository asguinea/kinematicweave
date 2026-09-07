# Visualization and Qualitative Evidence Specification

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.12 — Visualization and Qualitative Evidence Specification  
**Status:** Draft for approval

## 1. Purpose

This document defines how the KinematicWeave project produces qualitative visual evidence.

The goals are to ensure that visual artifacts are:

- reproducible;
- scientifically traceable;
- visually consistent;
- linked to quantitative results;
- honest about success and failure;
- suitable for benchmark figures, reports, and interactive inspection;
- independent of manual viewer state.

Qualitative artifacts support interpretation. They do not replace the primary quantitative evaluation.

## 2. Scope

This specification covers:

- interactive inspection;
- deterministic screenshots;
- animations and videos;
- raw-versus-reconstructed trajectory overlays;
- procedural control points;
- semantic event markers;
- inferred-versus-ground-truth layout overlays;
- grammar and connectivity views;
- branch and edit comparisons;
- failure-case galleries;
- figure captions and metadata.

The primary viewer is expected to use Rerun, but the scientific artifact format must not depend on a specific viewer implementation.

## 3. Visualization principles

### 3.1 Reproducibility

Every qualitative artifact must be regenerable from:

- recorded source artifacts;
- a versioned visualization configuration;
- a stable command;
- a fixed camera and timeline state;
- a recorded code revision.

### 3.2 Traceability

Every image, animation, or viewer session used in a report must identify:

- artifact identifier;
- scenario, tile, tape, or branch identifier;
- method and variant;
- experiment run;
- storage or coverage condition;
- seed when applicable;
- relevant metric records;
- source commit.

### 3.3 No manual scientific alteration

Manual editing must not change:

- geometry;
- trajectories;
- event positions;
- metric labels;
- method outputs;
- success or failure interpretation.

Permitted manual work is limited to presentation adjustments such as:

- final crop;
- text placement;
- panel labels;
- caption layout;

and must not conceal or alter data.

### 3.4 Success and failure balance

The qualitative package must include:

- representative successes;
- representative failures;
- ambiguous cases;
- edge cases;
- cases where metrics and visual appearance disagree.

A gallery containing only favorable examples is prohibited.

### 3.5 Consistent comparison

Compared methods must use:

- the same scene frame;
- the same camera;
- the same viewport;
- the same timeline interval;
- equivalent line widths and marker scales;
- consistent semantic categories.

## 4. Viewer architecture

The viewer consumes generated artifacts from the scientific pipeline.

It may read:

- canonical trajectories;
- encoded procedural lines;
- semantic events;
- inferred layouts;
- ground-truth maps;
- grammar nodes and relations;
- connectivity graphs;
- runtime states;
- edit operations;
- metric records.

The viewer must not:

- compute authoritative metrics;
- mutate raw results;
- become the only way to apply an edit;
- hide missing or failed outputs;
- require manual scene reconstruction for reproducibility.

## 5. Entity hierarchy

The recommended logical viewer hierarchy is:

```text
/world
  /reference
    /map
    /trajectories
    /events
  /methods
    /<method_id>
      /trajectories
      /control_points
      /events
      /layout
      /graph
      /grammar
  /runtime
    /agents
    /queries
  /branches
    /<branch_id>
      /trajectories
      /edits
      /layout
  /metrics
  /annotations
```

Each entity path must be stable and derived from persistent identifiers.

## 6. Coordinate display

All visualization uses the canonical coordinate and time conventions.

Requirements:

- `+x` and `+y` orientation must be visible or documented;
- optional `z` is displayed only when meaningful;
- planar and 3D views must not be confused;
- source and canonical transforms must not be applied invisibly;
- camera transforms are stored separately from scene transforms.

Primary evaluation views are top-down 2.5D unless the artifact explicitly demonstrates elevation.

## 7. Time and replay display

Visualizations must distinguish:

- source observation time;
- decoded procedural-line time;
- branch time;
- query time;
- event interval.

For animations:

- frame rate is recorded;
- simulation time per frame is recorded;
- playback speed is stated;
- time interpolation is deterministic;
- skipped or duplicated frames are prohibited unless documented.

## 8. Visual encoding

### 8.1 Required semantic categories

The visualization system must consistently distinguish:

- ground truth;
- raw observed trajectory;
- reconstructed trajectory;
- inferred layout;
- oracle-converted layout;
- edited branch;
- unavailable or invalid output.

### 8.2 Color policy

Exact colors are selected during implementation, but the following rules are fixed:

- ground truth and prediction must use clearly distinct colors;
- method colors remain stable across all figures;
- failure and warning states use a dedicated visual treatment;
- color is not the only distinction;
- grayscale readability is considered;
- color-blind accessibility is tested.

### 8.3 Line style policy

Recommended distinctions:

- raw/reference trajectory: solid line;
- decoded/predicted trajectory: dashed or differently styled line;
- inferred centerline: solid method-colored line;
- ground-truth centerline: neutral reference line;
- closed corridor: crossed, faded, or blocked style;
- inactive branch: reduced opacity.

### 8.4 Marker policy

Markers must distinguish:

- control points;
- trajectory starts;
- trajectory ends;
- stop events;
- turn events;
- junctions;
- access zones;
- edit locations.

Marker size must be expressed in a stable screen-space or world-space rule.

## 9. Motion reconstruction views

### 9.1 Required layers

A motion reconstruction view includes:

- source trajectory;
- decoded trajectory;
- control points;
- semantic events;
- start and end markers;
- method and budget label;
- trajectory identifier.

### 9.2 Error overlays

Optional error visualization may include:

- pointwise displacement vectors;
- maximum-error location;
- heading mismatch;
- event timing mismatch.

Error exaggeration must be disclosed.

### 9.3 Multi-budget comparison

For selected trajectories, show at least:

- severe compression;
- moderate compression;
- near-lossless compression.

Camera and scene scale must remain constant across panels.

### 9.4 Required motion examples

The final gallery includes:

- straight motion;
- curved motion;
- stop-and-go motion;
- turn preservation;
- event miss;
- high geometric error;
- irregular sampling;
- missing-data case.

## 10. Semantic event views

Event visualizations must show:

- event type;
- reference interval or point;
- predicted interval or point;
- matched or unmatched status;
- representative time;
- optional confidence.

Event matching lines or temporal bars may be used.

False positives and false negatives must be visually distinguishable.

## 11. Layout inference views

### 11.1 Required layers

A layout comparison view includes:

- source trajectories or trajectory density;
- inferred centerlines;
- inferred corridor regions where applicable;
- ground-truth centerlines or regions;
- inferred junctions;
- ground-truth junctions;
- tile boundary;
- context buffer boundary when used.

### 11.2 Coverage comparisons

Coverage figures use identical:

- tile;
- camera;
- map extent;
- method configuration;
- visual scales.

Required conditions:

- 10%;
- 25%;
- 50%;
- 100%.

### 11.3 Geometry and topology separation

At least one artifact must show:

- geometry overlay;
- connectivity graph overlay;

as separate panels.

A visually close centerline result with incorrect connectivity must be included when available.

### 11.4 Required layout examples

The final gallery includes:

- corridor recovery success;
- junction recovery success;
- sparse-coverage degradation;
- false connection;
- missed connection;
- topology-repair success;
- topology-repair harm;
- grade-separation ambiguity;
- disconnected-component case.

## 12. Grammar visualization

Grammar views must support:

- node type;
- parent-child hierarchy;
- geometry reference;
- provenance;
- relation type.

Recommended views:

1. hierarchy tree;
2. spatial geometry linked to selected grammar node;
3. relation overlay;
4. oracle versus inferred grammar summary.

Hierarchy and connectivity must not be shown as though they were the same graph.

## 13. Connectivity graph visualization

Graph views must show:

- graph nodes;
- directed edges;
- corridor references;
- junction nodes;
- access zones;
- closed edges;
- selected routes;
- unreachable targets.

Directionality must be visually explicit.

Equal-cost route tie breaking need not be visualized unless relevant to a case.

## 14. Runtime replay views

Replay views show:

- current canonical time;
- active agents;
- active procedural segment;
- active semantic event;
- selected branch;
- optional query results.

Replay visualization is diagnostic. Determinism is established by canonical hashes, not visual similarity.

## 15. Edit and branch comparisons

### 15.1 Required edit views

The final package includes:

- delay edit;
- segment replacement;
- corridor closure;
- successful reroute;
- impossible reroute;
- branch comparison.

### 15.2 Before-and-after structure

Each edit artifact must show:

- parent tape or branch;
- edit operation;
- resulting branch;
- affected entities;
- unaffected context;
- validation outcome.

### 15.3 Locality depiction

Where practical, modified representation elements are highlighted.

The visualization must not imply edit locality solely from visual appearance. The corresponding quantitative record must be linked.

## 16. Query visualization

Supported query displays may include:

- agents selected by class;
- agents inside a spatial region;
- events inside a time interval;
- agents using a corridor;
- differences between branches.

Query results must use canonical ordering and display the query configuration.

## 17. Camera specification

Every exported artifact stores a camera specification.

### 17.1 Top-down camera

Required fields:

- camera center;
- world extent;
- rotation;
- projection type;
- image dimensions;
- padding.

### 17.2 Perspective camera

Required fields:

- position;
- orientation;
- field of view;
- near and far planes;
- target or look direction;
- image dimensions.

### 17.3 Camera reuse

A comparison group references one shared camera identifier.

Manual camera movement after artifact selection must be saved as a new versioned camera configuration.

## 18. Export specifications

### 18.1 Static images

Primary static export:

- PNG;
- lossless;
- deterministic dimensions;
- embedded or adjacent metadata.

Recommended benchmark resolution:

- minimum 1600 pixels on the long side;
- higher when text or graph detail requires it.

### 18.2 Vector figures

Use SVG or PDF for:

- charts;
- grammar diagrams;
- graph diagrams;
- schematic comparisons;

when the generating library supports reproducible vector output.

### 18.3 Video

Primary video export:

- MP4 using a widely supported codec;
- recorded frame rate;
- recorded time range;
- recorded playback speed;
- fixed resolution.

An image-sequence source may be retained for deterministic regeneration.

### 18.4 Interactive recordings

Rerun recordings or equivalent viewer session artifacts may be stored when practical, but they do not replace static report assets.

## 19. Artifact metadata

Each qualitative artifact must have adjacent metadata containing:

- artifact identifier;
- artifact type;
- scenario, tile, tape, or branch identifier;
- experiment run identifier;
- method and variant;
- configuration identifier;
- seed;
- camera identifier;
- timeline range;
- source artifact identifiers;
- relevant metric identifiers;
- generation command;
- Git commit;
- file size;
- generation status.

The metadata format will follow the artifact-manifest contract.

## 20. Naming convention

Recommended file naming:

```text
<artifact_type>__<unit_id>__<method_id>__<condition>__<artifact_id>.<ext>
```

Example:

```text
layout_overlay__tile_0042__grammar_full__coverage_25__qual_001.png
```

Names must avoid spaces and machine-specific paths.

## 21. Qualitative selection protocol

### 21.1 Selection sources

Examples are selected from frozen result records using explicit criteria.

Allowed criteria include:

- near-median performance;
- best decile;
- worst decile;
- representative failure category;
- predefined scene type;
- metric disagreement.

### 21.2 Selection transparency

Every selected case records its selection reason.

### 21.3 Prohibited selection

Do not select examples solely because they look attractive.

Do not exclude failures that contradict the preferred narrative.

### 21.4 Development examples

Development-only examples must be labeled and excluded from final test evidence unless separately justified.

## 22. Failure taxonomy for galleries

Qualitative failures should be categorized.

Initial categories:

- oversimplified trajectory;
- event timing shift;
- false stop;
- missed stop;
- false turn;
- missed turn;
- sparse trajectory coverage;
- direction-cluster merge;
- direction-cluster fragmentation;
- missed junction;
- false junction;
- false graph connection;
- broken connectivity;
- harmful topology repair;
- grade-separation ambiguity;
- rerouting failure;
- post-edit discontinuity;
- resource-limited visualization.

## 23. Quantitative linkage

Each qualitative artifact links to at least one raw or aggregated quantitative record.

Examples:

- trajectory overlay → ADE, maximum deviation, event results, size;
- layout overlay → centerline F1, junction F1, OD connectivity;
- edit comparison → modified bytes, continuity checks, rerouting status;
- runtime capture → workload size and measured latency.

## 24. Captions

Every release or report caption must state:

- what is shown;
- reference versus predicted encoding;
- method and condition;
- key interpretation;
- relevant limitation;
- whether the example is synthetic, development, pilot, or frozen test.

Captions must not claim more than the linked metrics support.

## 25. Accessibility

Qualitative artifacts should:

- avoid relying on red–green distinction alone;
- use line style or marker shape in addition to color;
- maintain readable text size;
- use sufficient contrast;
- provide concise alt text in reports where supported.

## 26. Prohibited misleading practices

The project must not:

- use different camera extents to make one method appear better;
- hide unmatched predictions outside a crop;
- omit reference geometry without disclosure;
- use variable line widths that imply confidence without definition;
- smooth trajectories only for display unless disclosed;
- remove failed agents from a scene without annotation;
- alter opacity to conceal clutter selectively;
- compare different coverage subsets as though they were identical;
- manually repair method outputs for screenshots.

## 27. Headless generation

Primary static qualitative artifacts must support headless generation.

A command should be able to:

1. load a recorded artifact set;
2. apply a versioned visualization configuration;
3. set camera and timeline;
4. export;
5. write metadata;
6. exit with a meaningful status.

Interactive inspection may be used to choose configurations, but final generation must be scripted.

## 28. Viewer performance

Viewer performance is secondary.

The viewer should remain practical for:

- smoke scenarios;
- selected real scenarios;
- selected layout tiles;
- branch comparisons.

Large campaign results may be downselected before visualization.

Downselection must not alter underlying geometry or metrics.

## 29. Required qualitative package

The final repository must contain:

### Motion

- at least four representative successes;
- at least four representative failures;
- at least three storage budgets;
- at least one stop and one turn example.

### Layout

- at least four successes;
- at least four failures;
- all primary coverage levels;
- one topology-repair success;
- one topology-repair harm or no-op.

### Runtime and editing

- deterministic replay example;
- delay;
- segment replacement;
- closure and reroute;
- impossible reroute;
- branch comparison.

### Overview

- one full KinematicWeave pipeline figure;
- one domain or architecture schematic;
- one combined scene view showing layout and procedural motion.

## 30. Directory structure

Recommended output structure:

```text
qualitative/
├── motion/
│   ├── success/
│   ├── failure/
│   └── budgets/
├── layout/
│   ├── coverage/
│   ├── topology/
│   ├── repair/
│   └── failure/
├── runtime/
├── editing/
├── overview/
├── cameras/
├── configs/
└── manifests/
```

## 31. Reproducibility checks

Required checks include:

- same source artifacts produce the same rendered geometry;
- camera configuration is stable;
- timeline is stable;
- metadata references resolve;
- expected panels exist;
- output dimensions match configuration;
- no manual-only step is required for primary artifacts.

Pixel-identical output across unrelated GPU drivers is not required unless the rendering stack supports it. Geometric and metadata equivalence are required.

## 32. Acceptance criteria

This document is acceptable when:

- every qualitative artifact is reproducible by command;
- ground truth, prediction, oracle, and edits remain distinguishable;
- camera and timeline state are versioned;
- success and failure examples are required;
- qualitative selection is traceable to frozen metrics;
- layout geometry and topology are shown separately;
- branch and edit provenance are visible;
- misleading presentation practices are prohibited;
- headless generation is required for final static artifacts;
- accessibility and caption rules are explicit.
