# Coordinate and Time Conventions

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.4 — Domain Model and Coordinate Conventions  
**Status:** Draft for approval

## 1. Purpose

This document defines the default spatial, angular, temporal, numeric, ordering, and identifier conventions for the KinematicWeave project.

Dataset adapters must convert external conventions into these canonical conventions before data enters core algorithms.

## 2. Canonical spatial frame

### 2.1 Frame type

The primary evaluation uses a right-handed local Cartesian frame.

Axes:

- \(+x\): east-like or local forward horizontal axis;
- \(+y\): north-like or local left horizontal axis;
- \(+z\): upward.

The exact relation to a source dataset is recorded in the frame transform.

### 2.2 Units

Canonical units:

- position and distance: metres;
- time: seconds for floating-point durations;
- stored absolute or relative timestamps: integer nanoseconds;
- speed: metres per second;
- acceleration: metres per second squared;
- angles: radians.

### 2.3 Local origin

Each scenario or geographic tile has a documented local origin.

Preferred behavior:

- convert large global coordinates into a local frame before geometric processing;
- retain sufficient metadata to recover the source or global frame when available;
- avoid mixing coordinates from different local frames.

### 2.4 Primary evaluation plane

Primary geometry and topology metrics operate on the \(x\)-\(y\) plane unless a metric explicitly states otherwise.

Elevation \(z\):

- may be retained;
- may be visualized;
- may be used for filtering grade-separated crossings in later methods;
- is not automatically included in planar metrics.

## 3. Transform conventions

A frame transform maps coordinates from a source frame to the canonical frame.

Required transform semantics:

- transforms are explicit;
- transform direction is documented;
- homogeneous transforms use a consistent matrix convention;
- inverse transforms are available when mathematically valid;
- transformations do not silently change units.

The implementation should use one matrix-vector convention consistently. The later coding policy will freeze the exact array layout.

## 4. Heading convention

Canonical heading is the planar yaw angle:

- measured in radians;
- zero points along \(+x\);
- positive rotation is counterclockwise toward \(+y\);
- normalized to the half-open interval \([-\pi, \pi)\).

Examples:

- \(0\): \(+x\);
- \(\pi/2\): \(+y\);
- \(-\pi/2\): \(-y\);
- values equivalent to \(\pi\) normalize to \(-\pi\).

## 5. Angle operations

All heading differences must use wrapped angular difference.

The canonical signed difference from angle \(a\) to angle \(b\) is normalized to:

\[
[-\pi, \pi)
\]

Algorithms must not subtract headings without wrapping.

Circular means and circular variance must use circular statistics rather than linear averages.

## 6. Time representation

### 6.1 Canonical stored timestamp

The canonical stored timestamp is signed integer nanoseconds.

Within a scenario, timestamps may be represented relative to scenario start for compactness, but metadata must define the origin.

### 6.2 Derived time values

Floating-point seconds may be used for:

- interpolation;
- durations;
- rates;
- plotting;
- metric reporting.

Stored ordering and equality should rely on integer timestamps whenever possible.

### 6.3 Scenario interval

A scenario has:

- inclusive start timestamp;
- inclusive final observed timestamp;
- derived duration.

Later replay APIs may define query intervals independently.

## 7. Temporal ordering

Canonical ordering for samples:

1. timestamp ascending;
2. stable source-order tie breaker only before duplicate resolution.

After canonicalization, a trajectory must not contain duplicate timestamps.

Canonical ordering for events:

1. start time ascending;
2. end time ascending;
3. event type;
4. event identifier.

Canonical ordering for agent replay state:

1. agent identifier;
2. procedural-line identifier where required.

## 8. Duplicate timestamp policy

Dataset adapters must resolve duplicate timestamps before producing canonical trajectories.

Allowed policies include:

- reject invalid track;
- select one source record by documented priority;
- merge identical duplicates;
- aggregate only when the aggregation is explicitly defined.

Silent averaging is prohibited.

The applied policy and count of affected records must be reported.

## 9. Missing data

Missing observations must be represented explicitly.

The core system distinguishes:

- not observed;
- invalid measurement;
- unavailable attribute;
- intentionally omitted derived value.

A gap in observation is not automatically a stop.

Interpolation across gaps must follow a documented maximum-gap policy.

## 10. Trajectory support interval

A trajectory's support interval is the interval from its first valid canonical sample to its last valid canonical sample.

Decoding outside support must use an explicit policy:

- error;
- return no state;
- clamp to boundary;
- extrapolate.

The default core policy is **return no state** unless an experiment explicitly requests another behavior.

## 11. Interpolation boundaries

For piecewise-linear interpolation:

- exact control-point timestamps return the exact stored point;
- times strictly between adjacent control points are linearly interpolated;
- zero-duration segments are invalid;
- extrapolation is disabled by default.

Heading interpolation must use wrapped angular interpolation when heading is stored directly.

If heading is derived from position, stationary intervals require a documented fallback.

## 12. Derived kinematics

### 12.1 Velocity

Velocity is derived in metres per second using timestamp-aware finite differences or a later approved method.

### 12.2 Speed

Speed is the Euclidean norm of planar or 3D velocity according to the metric definition.

Primary motion-event evaluation is expected to use planar speed.

### 12.3 Acceleration

Acceleration is derived in metres per second squared using timestamp-aware differences.

### 12.4 Curvature and turn rate

Curvature and angular rate must:

- use wrapped headings;
- account for nonuniform timestamps;
- avoid division by near-zero displacement;
- mark invalid values explicitly.

## 13. Spatial geometry conventions

### 13.1 Points

A canonical planar point is ordered:

```text
[x, y]
```

A canonical 3D point is ordered:

```text
[x, y, z]
```

### 13.2 Polylines

Polyline vertices are ordered according to travel direction when direction is known.

For undirected geometry, provenance must indicate that no travel direction is implied.

### 13.3 Polygons

Polygon rings follow the geometry library's canonical convention after normalization.

The project must not infer semantic direction from polygon winding.

### 13.4 Corridor directionality

Corridor directionality is explicit:

- directed;
- bidirectional;
- unknown.

Reversing a polyline does not automatically change a bidirectional corridor's identity.

## 14. Grade separation

A planar geometric crossing is not automatically a junction.

When elevation or source semantics indicate grade separation:

- the crossing must remain disconnected unless evidence supports connectivity;
- the relevant provenance must be preserved.

When elevation is unavailable, ambiguity must be recorded rather than silently resolved as a junction.

## 15. Numeric precision

### 15.1 Internal computation

Default internal floating-point precision is 64-bit for:

- coordinate transforms;
- metric computation;
- topology matching;
- statistical aggregation.

Methods may use 32-bit values for performance when:

- the representation specifies it;
- serialization size requires it;
- numerical impact is tested and reported.

### 15.2 Serialization

Serialization precision is method-specific and must be explicit.

A method must not receive an unfair size advantage by omitting required precision metadata.

### 15.3 Integer types

Identifiers are serialized as text.

Timestamps use signed 64-bit integers unless a later contract gives a stronger reason otherwise.

## 16. Tolerance policy

No global hidden epsilon is permitted.

Tolerances must be:

- named;
- configured;
- stored with the run;
- expressed in canonical units;
- associated with a specific operation.

Expected tolerance categories include:

- geometric equality;
- timestamp equality;
- event matching;
- junction matching;
- centerline matching;
- serialization round-trip;
- replay-state comparison.

Exact values will be frozen in later evaluation definitions.

## 17. Canonical identifiers

Identifiers use UTF-8 strings.

Recommended form:

```text
<entity-type>:<source-or-scope>:<stable-token>
```

Examples:

```text
scenario:av2:abc123
agent:abc123:vehicle_004
trajectory:abc123:vehicle_004
tape:abc123:baseline_rdp
```

These examples are illustrative; exact formatting will be frozen in data contracts.

## 18. Identifier scope

Required scopes:

- dataset identifier: global within the project;
- scenario identifier: global within the dataset;
- agent identifier: unique within the scenario, preferably globally composable;
- trajectory identifier: unique within the scenario;
- procedural-line identifier: unique within the tape;
- grammar-node identifier: unique within the layout;
- experiment-run identifier: global within the result registry.

## 19. Stable hashing inputs

Canonical hashes must not include:

- memory addresses;
- unordered map iteration order;
- process identifiers;
- wall-clock timestamps unless explicitly part of the object;
- machine-specific absolute paths;
- noncanonical floating-point text formatting.

Stable hashing rules will be finalized in the serialization specification.

## 20. Coordinate normalization for algorithms

Algorithms may normalize coordinates for numerical conditioning, but they must record enough information to map outputs back to the canonical frame.

Examples:

- subtract tile origin;
- scale features for clustering;
- normalize direction features.

Feature scaling used for inference is not a coordinate-frame change unless geometry itself is transformed.

## 21. Directional feature convention

When direction is used as a clustering feature, the preferred representation is:

\[
[\cos(\theta), \sin(\theta)]
\]

rather than raw wrapped angle.

Any weighting relative to spatial coordinates must be explicit in configuration.

## 22. Geographic tiling

Later dataset policy will define tile size and overlap.

Coordinate rules already fixed here:

- each tile has a stable identifier;
- tile geometry is expressed in a declared frame;
- a trajectory may intersect multiple tiles only according to the approved tiling policy;
- split leakage must be controlled at the geographic unit selected later.

## 23. Invalid values

NaN and infinity are not valid persistent coordinates or timestamps.

They may appear transiently during computation only when:

- immediately detected;
- converted to an explicit invalid result;
- never silently serialized as valid geometry.

## 24. Coordinate and time metadata

Every canonical scenario or tape must retain:

- frame identifier;
- units;
- axis convention;
- time origin;
- timestamp unit;
- transform provenance;
- dimensionality flags;
- whether elevation is available and trustworthy.

## 25. Cross-dataset compatibility

Adapters must convert source data to the canonical conventions.

The core package must not contain logic such as:

- dataset-specific heading correction;
- dataset-specific axis swapping;
- hard-coded source timestamp rates;
- source-specific missing-value sentinels.

Those belong in adapters.

## 26. Determinism implications

Deterministic replay and hashing require:

- canonical entity ordering;
- canonical event ordering;
- stable timestamp representation;
- explicit float normalization rules;
- deterministic tie breaking;
- controlled parallel reduction.

The detailed policy will be defined in Batch 0.6.

## 27. Visualization implications

Visualizations must:

- label axes or provide orientation cues where relevant;
- use the same canonical frame as the underlying artifact;
- not mirror or rotate scenes silently for presentation;
- record camera transforms separately from scene transforms;
- distinguish planar evaluation from optional elevation display.

## 28. Acceptance criteria

This document is acceptable when:

- axis orientation and units are explicit;
- heading and angle wrapping are unambiguous;
- stored and derived time forms are separated;
- duplicate and missing data policies are explicit;
- interpolation outside support is defined;
- planar metrics and optional elevation are distinguished;
- grade-separated crossings are not automatically connected;
- precision and tolerance policies are explicit;
- identifier scopes and canonical ordering are defined;
- dataset adapters can convert external conventions without leaking them downstream.
