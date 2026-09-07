# Domain Model

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.4 — Domain Model and Coordinate Conventions  
**Status:** Draft for approval

## 1. Purpose

This document defines the conceptual objects used throughout the KinematicWeave project.

The domain model is independent of:

- any specific external dataset;
- a particular storage format;
- the viewer;
- optional perception components;
- any future native implementation.

Later data-contract and serialization documents will translate these concepts into concrete schemas.

## 2. General rules

### 2.1 Stable identity

Every persistent entity must have a stable identifier within a documented scope.

Identifiers must:

- be deterministic when derived from source data;
- remain stable across repeated processing;
- avoid dependence on array position;
- be serializable as text;
- be unique within their declared namespace.

### 2.2 Explicit provenance

Every inferred, converted, edited, or generated object must record provenance sufficient to determine:

- its source scenario or tape;
- the method that created it;
- the configuration used;
- whether it is ground truth, oracle-derived, inferred, synthetic, or edited.

### 2.3 No hidden semantic state

Important information must not exist only as implicit Python state.

If an attribute affects:

- reconstruction;
- evaluation;
- editing;
- replay;
- filtering;
- serialization size;
- or interpretation,

it must be represented explicitly.

### 2.4 Immutable source objects

Canonical source trajectories and source maps are treated as immutable.

Edits create new derived objects or tape branches rather than mutating the source records in place.

## 3. Entity overview

```text
Dataset
  └── Scenario
       ├── CoordinateFrame
       ├── Agent
       │    └── Trajectory
       │         ├── TrajectorySample
       │         └── SemanticEvent
       ├── SourceMap
       └── Tape
            ├── Layout
            │    ├── GrammarNode
            │    ├── GrammarRelation
            │    ├── Corridor
            │    ├── Junction
            │    ├── AccessZone
            │    └── ConnectivityGraph
            ├── ProceduralLine
            │    └── ProceduralSegment
            │         └── ControlPoint
            ├── TapeBranch
            ├── EditOperation
            └── QueryResult
```

Experiment entities are modeled separately from scene entities:

```text
ExperimentDefinition
  └── ExperimentRun
       ├── RunManifest
       ├── ExperimentalUnitResult
       ├── ResourceObservation
       └── GeneratedArtifact
```

## 4. Dataset

A **Dataset** is a named, versioned collection of scenarios and associated source metadata.

Required conceptual properties:

- dataset identifier;
- dataset version;
- source license reference;
- source coordinate conventions;
- adapter version;
- local acquisition metadata.

A dataset is not itself committed to the repository unless licensing and size permit.

## 5. Scenario

A **Scenario** is the principal bounded dynamic-scene unit.

A scenario contains:

- a stable scenario identifier;
- a temporal interval;
- one authoritative local coordinate frame;
- zero or more agents;
- zero or more source-map elements;
- source metadata;
- split membership;
- data-quality indicators.

A scenario may be synthetic or derived from an external dataset.

### 5.1 Scenario invariants

- start time must not exceed end time;
- all scenario-local timestamps use the same time basis;
- all scenario-local geometry is expressible in the scenario frame;
- agent identifiers are unique within the scenario;
- source data and derived tapes remain distinguishable.

## 6. Coordinate frame

A **CoordinateFrame** defines how positions, orientations, and optional elevations are interpreted.

Conceptual properties:

- frame identifier;
- parent frame identifier when applicable;
- origin;
- axis definitions;
- units;
- transform to parent or source frame;
- frame provenance.

The primary evaluation uses one canonical local frame per scenario or geographic tile.

## 7. Agent

An **Agent** is a dynamic entity represented in the scenario.

Conceptual properties:

- agent identifier;
- scenario identifier;
- semantic class;
- source identifier;
- physical dimensions when available;
- observation interval;
- metadata and quality flags.

Initial expected classes include:

- vehicle;
- pedestrian;
- cyclist;
- other dynamic agent;
- unknown.

Exact enumerations will be frozen in the data contracts.

### 7.1 Agent invariants

- an agent belongs to exactly one scenario;
- an agent may own one canonical trajectory;
- a derived tape may contain one or more procedural lines associated with the agent;
- an unknown class is preferable to inventing a class.

## 8. Trajectory

A **Trajectory** is an ordered sequence of time-indexed states for one agent.

Conceptual properties:

- trajectory identifier;
- owning agent identifier;
- scenario identifier;
- ordered samples;
- temporal coverage;
- source or derivation type;
- quality flags.

### 8.1 Trajectory invariants

- sample timestamps are strictly increasing after canonicalization;
- duplicate timestamps are resolved by the adapter or validation policy;
- positions use the declared coordinate frame;
- missing samples are represented explicitly, not silently interpolated;
- the source trajectory remains distinguishable from reconstructed trajectories.

## 9. Trajectory sample

A **TrajectorySample** is one canonical state observation at one timestamp.

Core conceptual fields:

- timestamp;
- planar position;
- optional elevation;
- heading when available or derived;
- optional velocity;
- optional acceleration;
- observation-validity flags.

Later contracts will decide which fields are mandatory versus derived.

## 10. Semantic event

A **SemanticEvent** is a typed occurrence or interval associated with an agent trajectory.

Initial event types:

- stop;
- turn.

Potential later event types:

- lane change;
- merge;
- split;
- entry;
- exit;
- interaction;
- yielding.

Conceptual properties:

- event identifier;
- owning trajectory or procedural line;
- event type;
- start time;
- end time or event timestamp;
- confidence when applicable;
- detector or source;
- event-specific attributes.

### 10.1 Event invariants

- event time lies within the owning trajectory or line interval;
- event type has an operational definition;
- detected and ground-truth events remain distinguishable;
- overlapping events are allowed only when their types and matching rules permit it.

## 11. Procedural line

A **ProceduralLine** is the symbolic, time-indexed motion representation for one agent or one continuous motion component.

Conceptual properties:

- procedural-line identifier;
- owning agent identifier;
- source trajectory identifier;
- ordered procedural segments;
- associated semantic events;
- representation version;
- codec identity;
- provenance.

### 11.1 Procedural-line invariants

- segment intervals are ordered;
- segments do not overlap unless a later specification explicitly allows layered motion;
- supported query times are defined;
- all reconstruction metadata required by the codec is present;
- serialization and deserialization preserve behavior under the declared tolerances.

## 12. Procedural segment

A **ProceduralSegment** is a bounded component of a procedural line with one reconstruction rule.

Conceptual properties:

- segment identifier;
- parent procedural-line identifier;
- start and end time;
- interpolation or motion model;
- ordered control points;
- segment attributes;
- boundary event references.

Initial implementations are expected to use piecewise-linear motion. Splines are a baseline or secondary representation.

## 13. Control point

A **ControlPoint** is a stored parameter that constrains a procedural segment.

Conceptual properties may include:

- control-point identifier;
- segment identifier;
- order index;
- time;
- position;
- optional heading;
- optional tangent or derivative information;
- semantic role.

A control point is not necessarily identical to a source trajectory sample.

## 14. Tape

A **Tape** is the complete serializable scene representation.

A tape contains:

- tape identifier;
- tape version;
- scenario reference;
- coordinate-frame reference;
- persistent layout;
- procedural lines;
- semantic events;
- provenance;
- optional parent or branch references;
- deterministic replay metadata.

### 14.1 Tape invariants

- all contained entities refer to compatible coordinate and time conventions;
- identifiers are unique within their scopes;
- all required references resolve;
- replay order is canonical;
- unsupported operations fail explicitly;
- a tape can be serialized without relying on hidden runtime state.

## 15. Layout

A **Layout** is the persistent spatial component of a tape.

It contains:

- geometric infrastructure;
- a symbolic hierarchy or grammar;
- a traversability/connectivity graph;
- semantic categories;
- provenance.

The layout may be:

- ground truth;
- oracle-converted;
- inferred from trajectories;
- synthetic;
- edited.

These origins must not be conflated.

## 16. Grammar node

A **GrammarNode** is a typed symbolic element in the persistent layout hierarchy.

Initial expected node types:

- scene;
- region;
- corridor;
- junction;
- access zone.

Conceptual properties:

- grammar-node identifier;
- node type;
- optional geometry reference;
- semantic attributes;
- parent relation;
- provenance.

A node may participate in both hierarchy and semantic relations, but hierarchy and connectivity remain distinct.

## 17. Grammar relation

A **GrammarRelation** is a typed relation between grammar nodes.

Initial relation categories may include:

- contains;
- adjacent to;
- derived from;
- enters;
- exits;
- overlaps;
- corresponds to.

Connectivity for routing is represented in the connectivity graph rather than inferred solely from hierarchy.

## 18. Corridor

A **Corridor** is a persistent traversable path-like layout element.

Conceptual properties:

- corridor identifier;
- centerline geometry;
- optional corridor polygon or width;
- directionality;
- semantic class;
- endpoint references;
- source or inference confidence;
- provenance.

Examples include:

- road-lane-like movement channels;
- pedestrian paths;
- cycle paths;
- generalized repeated-motion corridors.

## 19. Junction

A **Junction** is a persistent connection, merge, split, or crossing among corridors.

Conceptual properties:

- junction identifier;
- location or region geometry;
- incident corridor references;
- junction type;
- directional constraints;
- provenance.

A geometric crossing is not automatically a traversable junction.

## 20. Access zone

An **AccessZone** is a persistent region associated with starts, ends, entries, exits, stops, parking, transit access, or similar repeated endpoint behavior.

Conceptual properties:

- access-zone identifier;
- region geometry;
- semantic category;
- associated corridor or junction references;
- supporting trajectory evidence;
- provenance.

## 21. Connectivity graph

A **ConnectivityGraph** represents traversable relations among layout elements.

Conceptual structure:

- graph nodes;
- directed or undirected graph edges;
- edge attributes;
- route constraints;
- provenance.

Graph nodes may correspond to:

- corridor endpoints;
- junctions;
- access zones;
- other routing-relevant entities.

### 21.1 Connectivity invariants

- edge endpoints must exist;
- directionality is explicit;
- graph topology is not inferred from drawing order;
- false geometric crossings must not silently become graph edges;
- disconnected components are valid and measurable.

## 22. Tape branch

A **TapeBranch** is a derived tape version created from a parent tape through one or more edits.

Conceptual properties:

- branch identifier;
- parent tape or branch identifier;
- edit sequence;
- creation metadata;
- branch label;
- resulting tape reference.

Branches form an acyclic provenance structure.

## 23. Edit operation

An **EditOperation** is a typed, serializable transformation applied to a tape or branch.

Initial edit types:

- delay agent;
- replace procedural segment;
- close corridor;
- reroute affected agents;
- create branch.

Conceptual properties:

- edit identifier;
- edit type;
- target references;
- parameters;
- application time;
- author or tool identity where applicable;
- preconditions;
- result status;
- provenance.

### 23.1 Edit invariants

- source objects remain recoverable;
- failed edits do not silently produce partial state;
- edits record affected entities;
- branch provenance preserves edit order;
- post-edit validation results are recorded.

## 24. Query

A **Query** is a typed request against a tape or runtime.

Initial query categories:

- state at time;
- agent by identifier;
- agents by semantic class;
- agents within spatial region;
- events within temporal interval;
- agents using a corridor;
- branch comparison.

Queries must have deterministic ordering.

## 25. Query result

A **QueryResult** is a serializable response to a query.

Conceptual properties:

- query identifier;
- tape or branch identifier;
- query type;
- resolved parameters;
- ordered result references or values;
- execution metadata;
- result hash where appropriate.

## 26. Experiment definition

An **ExperimentDefinition** is a versioned declaration of a scientific experiment.

It identifies:

- experiment family and identifier;
- input split;
- methods;
- baselines;
- parameters or sweeps;
- metrics;
- seeds;
- expected outputs;
- resource expectations.

## 27. Experiment run

An **ExperimentRun** is one execution of an experiment definition under a resolved configuration.

Conceptual properties:

- run identifier;
- experiment identifier;
- resolved configuration;
- code revision;
- hardware and software metadata;
- start and end time;
- run status;
- output locations.

## 28. Experimental unit result

An **ExperimentalUnitResult** is the raw result for one independent unit, such as:

- one trajectory;
- one scenario;
- one geographic tile;
- one edit scenario;
- one runtime workload.

It contains:

- unit identifier;
- method identifier;
- metric values;
- validity flags;
- failure information;
- provenance.

## 29. Resource observation

A **ResourceObservation** records measured execution cost.

Conceptual properties:

- run identifier;
- workload identifier;
- wall-clock time;
- CPU time when available;
- peak resident memory;
- peak GPU memory when applicable;
- disk read and write estimates when available;
- output size;
- measurement method.

## 30. Generated artifact

A **GeneratedArtifact** is a derived file such as:

- figure;
- table;
- report;
- screenshot;
- animation;
- serialized tape;
- aggregated result.

It must reference:

- producing run or command;
- input records;
- configuration;
- code revision;
- artifact type;
- location;
- checksum when later policy requires it.

## 31. Cardinality summary

```text
Dataset 1 ── * Scenario
Scenario 1 ── * Agent
Agent 1 ── 1 canonical Trajectory
Trajectory 1 ── * TrajectorySample
Trajectory 1 ── * SemanticEvent
Agent 1 ── * ProceduralLine
ProceduralLine 1 ── * ProceduralSegment
ProceduralSegment 1 ── * ControlPoint
Scenario 1 ── * Tape
Tape 1 ── 1 Layout
Layout 1 ── * GrammarNode
Layout 1 ── * GrammarRelation
Layout 1 ── * Corridor
Layout 1 ── * Junction
Layout 1 ── * AccessZone
Layout 1 ── 1 ConnectivityGraph
Tape 1 ── * TapeBranch
TapeBranch 1 ── * EditOperation
ExperimentDefinition 1 ── * ExperimentRun
ExperimentRun 1 ── * ExperimentalUnitResult
ExperimentRun 1 ── * ResourceObservation
ExperimentRun 1 ── * GeneratedArtifact
```

## 32. Equality concepts

The project distinguishes:

- **identifier equality** — same stable identifier;
- **structural equality** — same fields and references;
- **canonical equality** — same normalized serialized representation;
- **numeric equivalence** — values equal within documented tolerances;
- **behavioral equivalence** — decoded or replayed outputs equal under declared metrics.

Later documents will define which equality applies to each test.

## 33. Domain-model acceptance criteria

This document is acceptable when:

- all persistent entities have clear ownership;
- source, inferred, oracle, synthetic, and edited objects remain distinguishable;
- hierarchy and connectivity are separate;
- trajectory and procedural-line concepts are not conflated;
- replay and editing require no hidden state;
- experiment records are first-class domain objects;
- the model supports synthetic and real data;
- later schemas can implement the model without inventing new central concepts.
