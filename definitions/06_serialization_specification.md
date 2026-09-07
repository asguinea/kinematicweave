# Serialization, Size Accounting, and Determinism

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.6 — Serialization, Size Accounting, and Determinism  
**Status:** Draft for approval

## 1. Purpose

This document defines how KinematicWeave representations are serialized, measured, hashed, replayed, and compared fairly.

The goals are to ensure that:

- storage comparisons are based on reconstructible serialized artifacts;
- all codecs are evaluated under the same accounting rules;
- repeated serialization is stable;
- replay determinism is testable;
- parallel execution does not alter scientific results;
- numeric tolerances are explicit rather than hidden.

## 2. Scope

This specification applies to:

- raw-sample trajectory representations;
- uniform-subsampling baselines;
- RDP baselines;
- spline baselines;
- event-free procedural lines;
- event-aware procedural lines;
- tape manifests;
- layout and connectivity artifacts;
- edit branches;
- replay-state hashes;
- experiment manifests and raw result records where deterministic serialization is required.

This document does not define compression algorithms themselves. It defines how their persisted forms are compared.

## 3. Serialization principles

### 3.1 Canonical representation

A canonical serialization is a deterministic byte representation of a logical object.

Canonical serialization requires:

- fixed field names;
- fixed field ordering;
- fixed row ordering;
- fixed numeric representation;
- fixed encoding;
- fixed null handling;
- fixed list ordering;
- fixed metadata ordering;
- explicit schema version.

### 3.2 Complete reconstruction requirement

A representation may be counted as compact only when its serialized form contains all information required to reconstruct the evaluated output.

Required information includes, where applicable:

- timestamps or a complete timestamp reconstruction rule;
- positions or control points;
- interpolation metadata;
- agent and trajectory identifiers;
- segment boundaries;
- semantic events;
- numeric precision metadata;
- dimensionality metadata;
- coordinate-frame reference;
- codec identity and version;
- any parameters required by the decoder.

Information may not be omitted from one method merely because it is available elsewhere in memory during evaluation.

### 3.3 Separation of logical and container overhead

The project reports:

1. **logical serialized size** — the canonical representation of the method-specific payload plus required reconstruction metadata;
2. **container size** — the actual file size of the chosen storage container;
3. **compressed container size** — optional, when a common external compression setting is applied.

The primary compactness comparison uses logical serialized size unless the experiment definition explicitly declares another measure.

## 4. Canonical encoding choices

### 4.1 Text encoding

JSON artifacts use:

- UTF-8;
- no byte-order mark;
- Unix newline `\n`;
- no insignificant whitespace in canonical form;
- sorted object keys;
- deterministic float formatting;
- explicit `null`.

Human-readable copies may be pretty-printed, but only canonical form is hashed or size-counted.

### 4.2 Tabular encoding

Parquet artifacts use:

- one declared schema version;
- stable column order;
- stable row order;
- explicit logical types;
- fixed compression configuration within an experiment;
- deterministic partition naming;
- no writer-generated timestamps in hashed payload metadata.

Parquet file bytes are not assumed to be identical across unrelated library versions unless the environment is frozen. Logical table equality remains the portable requirement.

### 4.3 Binary geometry

Geometry uses the canonical binary geometry form declared by the data contract.

Canonicalization must include:

- fixed endianness;
- normalized coordinate precision;
- normalized ring and component ordering where required;
- removal of nonsemantic writer metadata.

### 4.4 Canonical JSON values

For canonical JSON:

- object keys are sorted lexicographically;
- arrays preserve semantic order;
- unordered sets are sorted before encoding;
- booleans are lowercase JSON booleans;
- non-finite floats are forbidden;
- negative zero is normalized to positive zero unless the sign is semantically required;
- strings use standard JSON escaping.

## 5. Numeric representation

### 5.1 Declared numeric precision

Every codec must declare the stored type of each numeric field.

Examples:

- `float32`;
- `float64`;
- signed integer quantization with scale and offset;
- integer nanoseconds.

### 5.2 Quantization

A quantized representation must serialize:

- quantized integer values;
- scale;
- offset;
- dimensionality;
- rounding rule;
- overflow behavior.

Quantization metadata counts toward representation size.

### 5.3 Float normalization for canonical hashing

Before canonical hashing, floating-point values are normalized according to the artifact schema:

- NaN and infinity are rejected;
- negative zero becomes positive zero;
- values are encoded at their declared stored precision;
- no hidden rounding is performed beyond the declared representation.

### 5.4 Computation versus storage precision

Metrics may be computed in `float64` even when a codec stores `float32` or quantized values.

The metric must operate on the decoded representation, not the original high-precision source values.

## 6. Timestamp representation

Canonical persisted timestamps use signed 64-bit integer nanoseconds.

A codec may use relative time offsets if it serializes:

- the time origin;
- the offset unit;
- the offset values;
- any sampling-period rule;
- treatment of irregular intervals.

A fixed-rate method may omit per-sample timestamps only when the serialized representation contains enough information to recover every evaluation timestamp exactly.

## 7. Canonical ordering

### 7.1 Tape ordering

Within a tape, entities are ordered by:

1. agent identifier;
2. procedural-line start time;
3. procedural-line identifier;
4. segment index;
5. control-point index;
6. event ordering defined by the coordinate and time conventions.

### 7.2 Layout ordering

Layout entities are ordered by:

1. entity type;
2. stable entity identifier.

Graph edges are ordered by:

1. source-node identifier;
2. target-node identifier;
3. edge identifier.

### 7.3 Result ordering

Metric records are ordered by:

1. run identifier;
2. experimental-unit type;
3. experimental-unit identifier;
4. method identifier;
5. metric name;
6. canonicalized metric attributes.

## 8. Codec serialization contract

Every codec must implement behavior equivalent to:

```text
encode(source, config) -> representation
decode(representation, query_times) -> reconstructed states
serialize(representation) -> bytes
deserialize(bytes) -> representation
serialized_size(representation) -> integer bytes
metadata(representation) -> canonical metadata
```

Required guarantees:

- serialization followed by deserialization preserves the declared representation;
- decoding before and after round-trip is behaviorally equivalent;
- repeated serialization of the same canonical representation yields identical bytes within the frozen environment;
- `serialized_size()` equals the length of the canonical byte payload;
- decoding does not access the source trajectory.

## 9. Representation-specific payloads

### 9.1 Raw-sample baseline

Must include:

- sample timestamps or exact sampling rule;
- position values;
- dimensionality;
- numeric precision;
- trajectory identity;
- required frame metadata reference.

### 9.2 Uniform-subsampling baseline

Must include:

- retained sample values;
- retained timestamps or exact index reconstruction rule;
- original support interval;
- interpolation policy;
- numeric precision;
- required identifiers and metadata.

### 9.3 RDP baseline

Must include:

- retained vertices;
- associated timestamps;
- interpolation policy;
- tolerance only when required for interpretation or provenance;
- numeric precision;
- required identifiers and metadata.

### 9.4 Spline baseline

Must include:

- spline type;
- knots;
- control points;
- timing parameterization;
- boundary conditions;
- numeric precision;
- required identifiers and metadata.

### 9.5 Event-free procedural lines

Must include:

- line and segment metadata;
- control points;
- segment timing;
- reconstruction policy;
- required identifiers and metadata.

### 9.6 Event-aware procedural lines

Must include everything required by the event-free form plus:

- event records;
- event-to-segment references;
- event-specific attributes required for replay or evaluation.

Event storage counts toward serialized size.

## 10. Size-accounting protocol

### 10.1 Primary size measure

The primary size measure is:

```text
canonical payload bytes
```

For each encoded trajectory:

```text
serialized_size_bytes = len(canonical_codec_payload)
```

Derived compactness measures include:

```text
bytes_per_agent_second =
    serialized_size_bytes / represented_duration_seconds
```

and:

```text
bytes_per_sample_equivalent =
    serialized_size_bytes / source_sample_count
```

### 10.2 Included data

The primary size count includes:

- method payload;
- reconstruction metadata;
- per-trajectory identifiers required by the artifact;
- timestamps or timing rules;
- event records;
- segment types;
- precision metadata;
- decoder-required parameters.

### 10.3 Excluded data

The primary per-trajectory size count may exclude shared artifact-level metadata only when:

- it is genuinely shared;
- every method receives the same exclusion;
- its total size is reported separately;
- the exclusion is documented in the experiment output.

Examples may include:

- one shared schema identifier;
- one shared coordinate-frame manifest;
- one shared codec-version string for a large artifact.

### 10.4 Shared overhead allocation

When shared metadata differs between methods, the experiment must either:

- include it directly; or
- amortize it across represented trajectories using one documented rule.

The same rule applies to all methods.

### 10.5 Container compression

External compression may be reported as a secondary result.

Rules:

- use the same compression algorithm and level for all methods;
- report uncompressed canonical size as well;
- do not tune compression separately per method;
- preserve enough metadata to decompress independently.

### 10.6 Python object memory

Python object size is not a representation-size metric.

It may be reported separately as runtime memory overhead.

## 11. Matched-budget evaluation

### 11.1 Matched-byte condition

For a target byte budget, each method must produce the best result available under a serialized size not exceeding the budget.

The budget-selection procedure must be fixed before the frozen campaign.

### 11.2 Matched-control-point condition

Control-point matching is secondary to byte matching.

A control point is counted according to the method's stored control-point contract. Hidden spline knots or event boundaries cannot be excluded if they are required for reconstruction.

### 11.3 Budget tolerance

When exact byte matching is impossible, the experiment must define:

- allowed budget deviation;
- tie-breaking rule;
- interpolation or nearest-budget policy;
- whether a method exceeding the budget is excluded.

### 11.4 Pareto reporting

The primary fidelity–compactness analysis reports the empirical Pareto frontier.

A single hand-selected tolerance is insufficient as the only compactness result.

## 12. Canonical hashing

### 12.1 Hash purpose

Hashes are used for:

- replay-state determinism;
- artifact identity;
- duplicate detection;
- result provenance;
- round-trip verification.

### 12.2 Hash algorithm

The project uses SHA-256 for canonical artifact and state hashes unless a later approved decision changes it.

### 12.3 Hash input

The hash input is the canonical byte representation of the logical object.

The input must exclude:

- file path;
- process identifier;
- wall-clock creation time;
- machine hostname;
- unordered writer metadata;
- nonsemantic logging fields.

### 12.4 Domain separation

Hash payloads begin with a stable domain tag, for example:

```text
kinematicweave:replay-state:v1
kinematicweave:tape:v1
kinematicweave:query-result:v1
kinematicweave:metric-record:v1
```

This prevents accidental equality across different artifact types.

## 13. Replay-state canonicalization

A replay state at time `t` contains only supported canonical state fields.

Initial expected fields:

- tape or branch identifier;
- query timestamp;
- ordered active-agent records;
- agent identifier;
- position;
- optional elevation;
- heading;
- active segment identifier;
- active semantic event identifiers;
- validity status.

Canonical replay-state ordering is by agent identifier.

The replay hash is computed from the canonical state representation, not from rendered output.

## 14. Replay determinism protocol

For each selected tape:

1. load the same serialized tape independently;
2. replay the same canonical query schedule;
3. canonicalize every state;
4. compute per-state hashes;
5. compute a sequence hash over the ordered state hashes;
6. repeat in separate processes;
7. repeat after serialization and deserialization;
8. compare all hashes.

A determinism test passes only when all expected hashes match.

## 15. Query determinism

Deterministic queries require:

- canonical input parameters;
- canonical result ordering;
- no dependence on hash-map iteration;
- deterministic spatial tie breaking;
- deterministic graph traversal tie breaking;
- stable float normalization.

When multiple graph paths have equal cost, the chosen path is determined by lexicographic stable identifier order unless another rule is explicitly defined.

## 16. Random seed derivation

### 16.1 Root seed

Every stochastic experiment has one recorded root seed.

### 16.2 Derived seeds

Per-unit seeds are deterministically derived from:

```text
root seed
experiment identifier
method identifier
experimental-unit identifier
replicate index
```

The derivation uses a stable hash and does not depend on processing order.

### 16.3 Prohibited seed behavior

The system must not:

- use wall-clock time as an implicit seed;
- reuse process-global random state without resetting;
- derive seeds from Python's unstable built-in hash;
- allow worker scheduling to change unit seeds.

## 17. Parallel execution

Parallel execution is allowed only when it preserves canonical results.

Requirements:

- each experimental unit is independent;
- output records use stable identifiers;
- final output is sorted canonically;
- reductions use deterministic order where floating-point differences matter;
- worker count is recorded;
- resumability does not alter results.

If a library cannot provide deterministic behavior under parallel execution, the relevant step must run serially or be reported as nondeterministic.

## 18. GPU determinism

The core project must not depend on GPU determinism.

When GPU operations are used:

- deterministic modes are enabled where supported;
- nondeterministic kernels are avoided for confirmatory experiments;
- CPU and GPU results are compared on a validation subset;
- the device and library versions are recorded;
- any remaining nondeterminism is explicitly reported.

A CPU implementation remains the reference for core deterministic replay.

## 19. Floating-point equality

The project distinguishes:

### 19.1 Exact equality

Used for:

- integer timestamps;
- identifiers;
- canonical byte payloads;
- hashes;
- ordered symbolic events;
- schema metadata.

### 19.2 Stored-value equality

Used after serialization round-trip at the declared stored precision.

### 19.3 Numeric equivalence

Used for decoded floating-point states and metrics when exact equality is not required.

Numeric equivalence requires named absolute and/or relative tolerances stored in configuration.

### 19.4 Behavioral equivalence

Used when two representations produce outputs equivalent under a declared metric threshold.

Behavioral equivalence does not imply byte equality.

## 20. Tolerance registry

All tolerances must be registered by name.

Expected categories:

- `serialization_float_roundtrip`;
- `decoded_position_equality`;
- `decoded_heading_equality`;
- `event_time_match`;
- `event_space_match`;
- `centerline_match`;
- `junction_match`;
- `graph_node_match`;
- `metric_regression`.

Exact values are defined in the evaluation protocol and configuration files.

## 21. Serialization round-trip tests

Every persistent representation requires tests for:

- serialize → deserialize;
- deserialize → serialize;
- byte stability;
- schema validation;
- invalid payload rejection;
- missing required field rejection;
- unknown supported-minor field handling;
- numeric precision preservation;
- deterministic ordering;
- behavioral equivalence after round-trip.

## 22. Corruption and failure behavior

Readers must fail explicitly on:

- unsupported major schema version;
- truncated payload;
- invalid numeric values;
- unresolved references;
- inconsistent declared counts;
- invalid timestamp ordering;
- unknown mandatory codec type;
- hash mismatch when verification is requested.

Partial recovery may exist only as a separate diagnostic operation.

## 23. Artifact identity

An artifact identity contains:

- artifact type;
- schema version;
- producer version;
- canonical content hash.

Two artifacts with different provenance may share canonical content identity, but provenance records remain separate.

## 24. Result immutability

Raw result files are immutable after a run is marked complete.

Corrections require:

- a new run identifier;
- a new artifact;
- preserved old results;
- an explanation in the decision or experiment record.

Aggregation and figure generation never rewrite raw metric values.

## 25. Cross-platform expectations

Required cross-platform guarantees:

- logical schema compatibility;
- stable identifiers;
- stable timestamps;
- stable canonical JSON;
- stable replay behavior within declared numeric tolerances.

Bit-identical Parquet files across different library versions are not required.

The frozen reference environment is the authority for byte-identical file tests.

## 26. Security and safety constraints

Serialization readers must:

- avoid arbitrary code execution;
- avoid unsafe pickle as a persistent research format;
- validate lengths and counts;
- reject path traversal in archive-style artifacts;
- avoid loading unbounded nested data into memory;
- treat external artifacts as untrusted input.

## 27. Required metadata in experiment outputs

Every compactness or determinism result must record:

- codec identifier and version;
- schema version;
- stored numeric precision;
- serialized size;
- shared overhead treatment;
- container compression setting;
- timestamp representation;
- decoder version;
- hash domain and algorithm;
- determinism mode;
- worker count;
- device type;
- tolerance identifiers.

## 28. Acceptance criteria

This document is acceptable when:

- every codec is measured from a reconstructible serialized form;
- timestamps, identifiers, events, and decoder-required metadata are accounted for;
- Python object size is excluded from representation compactness claims;
- matched-budget comparisons have one common rule;
- canonical ordering and hashing are explicit;
- random seeds do not depend on processing order;
- replay-state hashing is independent of rendering;
- CPU replay is the deterministic reference;
- round-trip and corruption behavior are defined;
- raw result immutability is explicit;
- later metric definitions can use this protocol without inventing storage rules.
