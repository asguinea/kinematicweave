# Canonical Data Contracts

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.5 — Canonical Data Contracts  
**Status:** Draft for approval

## 1. Purpose

This document defines the canonical records exchanged between modules in the KinematicWeave project.

These contracts are independent of any external dataset. Dataset adapters must convert source-specific data into these records before core algorithms consume them.

The contracts are designed for:

- deterministic processing;
- Parquet-based tabular storage;
- JSON-based manifests and small structured objects;
- bounded-memory execution;
- schema validation;
- versioned evolution;
- traceability from source data to final results.

## 2. General contract rules

### 2.1 Canonical formats

Preferred storage:

- **Parquet** for large tabular records;
- **JSON** for manifests, configurations, and small nested records;
- **JSON Lines** only when append-oriented streaming is materially useful;
- binary blobs only when explicitly defined by the serialization specification.

### 2.2 Required metadata

Every persistent table or manifest must include:

- `schema_name`;
- `schema_version`;
- producer identity or producer version;
- creation metadata in the enclosing artifact manifest;
- coordinate and time metadata where spatial or temporal values occur.

For Parquet tables, `schema_name` and `schema_version` may be stored in file metadata rather than repeated per row.

### 2.3 Type notation

This document uses:

- `string` — UTF-8 text;
- `bool` — Boolean;
- `int16`, `int32`, `int64` — signed integers;
- `float32`, `float64` — IEEE floating-point;
- `timestamp_ns` — signed 64-bit integer nanoseconds;
- `list<T>` — ordered variable-length list;
- `struct{...}` — nested record;
- `enum<...>` — closed string enumeration;
- `map<string, scalar>` — small metadata map with scalar values.

### 2.4 Nullability

A field is non-null unless marked **nullable**.

Missing values must not be encoded using magic numbers.

### 2.5 Stable ordering

Unless a contract states otherwise:

- rows are written in canonical ascending identifier order;
- nested lists preserve semantically meaningful order;
- unordered metadata maps are canonicalized before hashing;
- consumers must not rely on incidental file-row order unless the contract defines it.

### 2.6 Identifier references

All references use stable string identifiers.

A referenced identifier must resolve within the artifact set declared by the enclosing manifest.

### 2.7 Coordinate and time conventions

All contracts follow `coordinate_and_time_conventions.md`.

In particular:

- positions are in metres;
- angles are in radians;
- canonical timestamps are integer nanoseconds;
- planar heading is zero along `+x`, positive counterclockwise;
- primary planar coordinates are `(x, y)`;
- optional elevation is `z`.

### 2.8 Provenance categories

Where `origin_type` appears, allowed values are:

- `source_ground_truth`;
- `source_observation`;
- `synthetic`;
- `oracle_converted`;
- `inferred`;
- `decoded`;
- `edited`;
- `derived_metric`.

### 2.9 Quality flags

Quality flags are represented as ordered lists of strings.

Initial common flags include:

- `missing_heading`;
- `missing_elevation`;
- `interpolated_source_gap`;
- `duplicate_timestamp_resolved`;
- `short_track`;
- `low_confidence_source`;
- `invalid_geometry_repaired`;
- `grade_separation_ambiguous`.

New flags may be added without a schema-major change if consumers treat unknown flags as informational.

## 3. Schema versioning policy

Schema versions use:

```text
MAJOR.MINOR
```

- **MAJOR** changes break compatibility.
- **MINOR** changes add optional fields or enumeration values without changing existing field meaning.

Rules:

- producers write one declared version;
- consumers reject unsupported major versions;
- consumers may accept newer minor versions when unknown optional fields can be ignored safely;
- field meaning must never change silently;
- migration tools are required before removing or renaming persistent fields.

Initial versions in this document are `1.0`.

---

# 4. Scenario manifest

**Schema name:** `scenario_manifest`  
**Version:** `1.0`  
**Format:** JSON or one-row-per-scenario Parquet

## 4.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `scenario_id` | string | No | Stable scenario identifier |
| `dataset_id` | string | No | Canonical dataset identifier |
| `dataset_version` | string | No | Source dataset version |
| `split_name` | string | No | `synthetic`, `smoke`, `development`, `pilot`, `test`, or approved held-out split |
| `city_or_region` | string | Yes | Human-readable source region |
| `source_scenario_id` | string | Yes | Original dataset identifier |
| `start_time_ns` | timestamp_ns | No | Inclusive canonical start |
| `end_time_ns` | timestamp_ns | No | Inclusive canonical end; must be `>= start_time_ns` |
| `coordinate_frame_id` | string | No | Reference to canonical frame metadata |
| `origin_x_m` | float64 | No | Local-frame origin expressed in source/global frame when available |
| `origin_y_m` | float64 | No | Local-frame origin expressed in source/global frame when available |
| `origin_z_m` | float64 | Yes | Optional vertical origin |
| `source_crs` | string | Yes | Source coordinate reference description |
| `has_elevation` | bool | No | Whether usable `z` values are available |
| `agent_count` | int32 | No | Number of canonical agents |
| `source_map_available` | bool | No | Whether a source vector map is available |
| `quality_flags` | list<string> | No | Ordered flags; may be empty |
| `adapter_name` | string | No | Dataset adapter identifier |
| `adapter_version` | string | No | Adapter version |
| `source_checksum` | string | Yes | Source-file checksum when practical |

## 4.2 Canonical ordering

Ascending `scenario_id`.

## 4.3 Producer and consumers

**Producer:** `kinematicweave.data` dataset adapters
**Consumers:** all downstream modules, split tooling, experiment runner

## 4.4 Example

```json
{
  "scenario_id": "scenario:av2:abc123",
  "dataset_id": "av2_motion",
  "dataset_version": "1.1",
  "split_name": "development",
  "city_or_region": "PIT",
  "source_scenario_id": "abc123",
  "start_time_ns": 0,
  "end_time_ns": 11000000000,
  "coordinate_frame_id": "frame:scenario:av2:abc123",
  "origin_x_m": 0.0,
  "origin_y_m": 0.0,
  "origin_z_m": null,
  "source_crs": null,
  "has_elevation": false,
  "agent_count": 27,
  "source_map_available": true,
  "quality_flags": [],
  "adapter_name": "av2_motion_adapter",
  "adapter_version": "1.0",
  "source_checksum": null
}
```

---

# 5. Agent metadata table

**Schema name:** `agent_metadata`  
**Version:** `1.0`  
**Format:** Parquet

## 5.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `scenario_id` | string | No | Owning scenario |
| `agent_id` | string | No | Unique within scenario |
| `source_agent_id` | string | Yes | Original source identifier |
| `agent_class` | enum | No | `vehicle`, `pedestrian`, `cyclist`, `other_dynamic`, `unknown` |
| `length_m` | float32 | Yes | Physical length; must be `> 0` |
| `width_m` | float32 | Yes | Physical width; must be `> 0` |
| `height_m` | float32 | Yes | Physical height; must be `> 0` |
| `first_time_ns` | timestamp_ns | No | First valid canonical sample |
| `last_time_ns` | timestamp_ns | No | Last valid canonical sample |
| `sample_count` | int32 | No | Canonical sample count; must be `>= 1` |
| `is_focal_agent` | bool | No | Dataset-defined focal agent when applicable |
| `is_ego_agent` | bool | No | Ego/platform agent when applicable |
| `origin_type` | enum | No | Common provenance category |
| `quality_flags` | list<string> | No | Ordered quality flags |

## 5.2 Primary key

```text
(scenario_id, agent_id)
```

## 5.3 Canonical ordering

`scenario_id`, then `agent_id`.

## 5.4 Producer and consumers

**Producer:** `kinematicweave.data`
**Consumers:** codecs, layout inference, runtime, metrics, visualization

## 5.5 Example

```json
{
  "scenario_id": "scenario:av2:abc123",
  "agent_id": "agent:abc123:004",
  "source_agent_id": "004",
  "agent_class": "vehicle",
  "length_m": 4.5,
  "width_m": 1.9,
  "height_m": null,
  "first_time_ns": 0,
  "last_time_ns": 10900000000,
  "sample_count": 110,
  "is_focal_agent": false,
  "is_ego_agent": false,
  "origin_type": "source_observation",
  "quality_flags": []
}
```

---

# 6. Trajectory sample table

**Schema name:** `trajectory_samples`  
**Version:** `1.0`  
**Format:** Parquet

## 6.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `scenario_id` | string | No | Owning scenario |
| `agent_id` | string | No | Owning agent |
| `trajectory_id` | string | No | Canonical trajectory identifier |
| `sample_index` | int32 | No | Zero-based canonical order |
| `timestamp_ns` | timestamp_ns | No | Strictly increasing within trajectory |
| `x_m` | float64 | No | Canonical local-frame x |
| `y_m` | float64 | No | Canonical local-frame y |
| `z_m` | float64 | Yes | Optional elevation |
| `heading_rad` | float64 | Yes | Canonical wrapped heading |
| `velocity_x_mps` | float64 | Yes | Canonical x velocity |
| `velocity_y_mps` | float64 | Yes | Canonical y velocity |
| `speed_mps` | float64 | Yes | Planar speed; nonnegative |
| `acceleration_x_mps2` | float64 | Yes | Canonical x acceleration |
| `acceleration_y_mps2` | float64 | Yes | Canonical y acceleration |
| `is_observed` | bool | No | True for source-observed sample |
| `is_valid` | bool | No | False only when retained for diagnostic reasons |
| `origin_type` | enum | No | Provenance category |
| `quality_flags` | list<string> | No | Ordered flags |

## 6.2 Primary key

```text
(scenario_id, trajectory_id, sample_index)
```

`timestamp_ns` must also be unique within a trajectory after canonicalization.

## 6.3 Canonical ordering

`scenario_id`, `trajectory_id`, `timestamp_ns`.

## 6.4 Producer and consumers

**Producer:** dataset adapters, synthetic generators, codec decoders  
**Consumers:** events, codecs, layout inference, metrics, visualization

## 6.5 Example

```json
{
  "scenario_id": "scenario:av2:abc123",
  "agent_id": "agent:abc123:004",
  "trajectory_id": "trajectory:abc123:004",
  "sample_index": 37,
  "timestamp_ns": 3700000000,
  "x_m": 12.84,
  "y_m": -4.16,
  "z_m": null,
  "heading_rad": 1.432,
  "velocity_x_mps": 0.45,
  "velocity_y_mps": 3.12,
  "speed_mps": 3.152,
  "acceleration_x_mps2": null,
  "acceleration_y_mps2": null,
  "is_observed": true,
  "is_valid": true,
  "origin_type": "source_observation",
  "quality_flags": []
}
```

---

# 7. Semantic event table

**Schema name:** `semantic_events`  
**Version:** `1.0`  
**Format:** Parquet

## 7.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `scenario_id` | string | No | Owning scenario |
| `event_id` | string | No | Stable event identifier |
| `agent_id` | string | No | Owning agent |
| `trajectory_id` | string | Yes | Source or reference trajectory |
| `procedural_line_id` | string | Yes | Associated encoded line |
| `event_type` | enum | No | Initially `stop` or `turn` |
| `start_time_ns` | timestamp_ns | No | Inclusive event start |
| `end_time_ns` | timestamp_ns | No | Inclusive event end; `>= start` |
| `representative_time_ns` | timestamp_ns | Yes | Canonical event instant when needed for matching |
| `x_m` | float64 | Yes | Representative planar location |
| `y_m` | float64 | Yes | Representative planar location |
| `confidence` | float32 | Yes | `[0, 1]` when probabilistic |
| `source_type` | enum | No | `source_annotation`, `detected`, `decoded`, `synthetic`, `edited` |
| `detector_name` | string | Yes | Detector or generator identifier |
| `detector_version` | string | Yes | Detector or generator version |
| `attributes_json` | string | Yes | Canonical JSON with event-specific attributes |
| `quality_flags` | list<string> | No | Ordered flags |

## 7.2 Primary key

```text
(scenario_id, event_id)
```

## 7.3 Validation

At least one of `trajectory_id` or `procedural_line_id` must be present.

Event-specific attributes may include:

- stop duration;
- minimum speed;
- total heading change;
- turn direction;
- confidence components.

## 7.4 Canonical ordering

`scenario_id`, `agent_id`, `start_time_ns`, `end_time_ns`, `event_type`, `event_id`.

## 7.5 Producer and consumers

**Producer:** event detectors, synthetic generators, edit runtime  
**Consumers:** codecs, metrics, runtime, visualization

---

# 8. Procedural-line record

**Schema name:** `procedural_lines`  
**Version:** `1.0`  
**Format:** Parquet or JSON collection

## 8.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `tape_id` | string | No | Owning tape |
| `scenario_id` | string | No | Owning scenario |
| `procedural_line_id` | string | No | Unique within tape |
| `agent_id` | string | No | Owning agent |
| `source_trajectory_id` | string | Yes | Source trajectory |
| `codec_id` | string | No | Codec identifier |
| `codec_version` | string | No | Codec version |
| `start_time_ns` | timestamp_ns | No | Supported start time |
| `end_time_ns` | timestamp_ns | No | Supported end time |
| `segment_count` | int32 | No | Number of child segments |
| `event_count` | int32 | No | Number of linked events |
| `reconstruction_policy` | enum | No | Initially `piecewise_linear`, `cubic_spline`, or codec-defined value |
| `origin_type` | enum | No | Usually `inferred`, `decoded`, `synthetic`, or `edited` |
| `parent_line_id` | string | Yes | Parent line for edited derivations |
| `attributes_json` | string | Yes | Canonical codec metadata |
| `quality_flags` | list<string> | No | Ordered flags |

## 8.2 Primary key

```text
(tape_id, procedural_line_id)
```

## 8.3 Canonical ordering

`tape_id`, `agent_id`, `start_time_ns`, `procedural_line_id`.

## 8.4 Producer and consumers

**Producer:** codecs, editing  
**Consumers:** runtime, metrics, serialization, visualization

---

# 9. Procedural-segment record

**Schema name:** `procedural_segments`  
**Version:** `1.0`  
**Format:** Parquet

## 9.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `tape_id` | string | No | Owning tape |
| `procedural_line_id` | string | No | Parent line |
| `segment_id` | string | No | Unique within line |
| `segment_index` | int32 | No | Zero-based order |
| `start_time_ns` | timestamp_ns | No | Inclusive start |
| `end_time_ns` | timestamp_ns | No | Inclusive end; must be `> start` |
| `segment_type` | enum | No | `linear`, `cubic_spline`, or approved codec-specific type |
| `control_point_count` | int32 | No | Must match child records |
| `start_event_id` | string | Yes | Boundary event |
| `end_event_id` | string | Yes | Boundary event |
| `attributes_json` | string | Yes | Canonical segment-specific metadata |
| `quality_flags` | list<string> | No | Ordered flags |

## 9.2 Primary key

```text
(tape_id, procedural_line_id, segment_id)
```

## 9.3 Canonical ordering

`tape_id`, `procedural_line_id`, `segment_index`.

## 9.4 Producer and consumers

**Producer:** codecs, editing  
**Consumers:** runtime, serialization, metrics, visualization

---

# 10. Control-point record

**Schema name:** `control_points`  
**Version:** `1.0`  
**Format:** Parquet

## 10.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `tape_id` | string | No | Owning tape |
| `procedural_line_id` | string | No | Owning line |
| `segment_id` | string | No | Owning segment |
| `control_point_id` | string | No | Stable control-point identifier |
| `control_point_index` | int32 | No | Zero-based order within segment |
| `timestamp_ns` | timestamp_ns | Yes | Required when the codec stores explicit control-point time |
| `x_m` | float64 | No | Canonical x |
| `y_m` | float64 | No | Canonical y |
| `z_m` | float64 | Yes | Optional z |
| `heading_rad` | float64 | Yes | Optional heading |
| `tangent_x` | float64 | Yes | Optional spline tangent |
| `tangent_y` | float64 | Yes | Optional spline tangent |
| `semantic_role` | enum | No | `start`, `intermediate`, `end`, `event_boundary`, `codec_internal` |
| `attributes_json` | string | Yes | Canonical additional metadata |

## 10.2 Primary key

```text
(tape_id, procedural_line_id, segment_id, control_point_id)
```

## 10.3 Canonical ordering

`tape_id`, `procedural_line_id`, `segment_id`, `control_point_index`.

## 10.4 Producer and consumers

**Producer:** codecs, editing  
**Consumers:** runtime, serialization, visualization, representation metrics

---

# 11. Canonical vector-map record

**Schema name:** `vector_map_elements`  
**Version:** `1.0`  
**Format:** Parquet

## 11.1 Geometry representation

Geometry is stored using one of:

- canonical WKB bytes in a `geometry_wkb` binary column; or
- a later approved GeoParquet-compatible geometry column.

The repository must use one consistent representation per artifact version.

## 11.2 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `scenario_id` | string | No | Associated scenario |
| `map_element_id` | string | No | Stable identifier |
| `element_type` | enum | No | `lane_centerline`, `lane_boundary`, `road_area`, `crosswalk`, `walkway`, `junction_area`, `access_zone`, `other` |
| `geometry_type` | enum | No | `point`, `linestring`, `polygon`, `multilinestring`, `multipolygon` |
| `geometry_wkb` | binary | No | Valid canonical-frame geometry |
| `directionality` | enum | No | `directed`, `bidirectional`, `unknown`, `not_applicable` |
| `parent_element_id` | string | Yes | Optional source hierarchy |
| `successor_ids` | list<string> | No | Ordered successor references |
| `predecessor_ids` | list<string> | No | Ordered predecessor references |
| `left_neighbor_id` | string | Yes | Optional source relation |
| `right_neighbor_id` | string | Yes | Optional source relation |
| `semantic_attributes_json` | string | Yes | Canonical source semantics |
| `origin_type` | enum | No | Usually `source_ground_truth` or `synthetic` |
| `quality_flags` | list<string> | No | Ordered flags |

## 11.3 Primary key

```text
(scenario_id, map_element_id)
```

## 11.4 Canonical ordering

`scenario_id`, `element_type`, `map_element_id`.

## 11.5 Producer and consumers

**Producer:** map adapters, synthetic generators  
**Consumers:** oracle grammar conversion, evaluation metrics, visualization

---

# 12. Inferred corridor record

**Schema name:** `inferred_corridors`  
**Version:** `1.0`  
**Format:** Parquet

## 12.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `layout_id` | string | No | Owning inferred layout |
| `scenario_or_tile_id` | string | No | Evaluation unit |
| `corridor_id` | string | No | Stable inferred identifier |
| `centerline_wkb` | binary | No | Valid nonempty linestring |
| `region_wkb` | binary | Yes | Optional corridor polygon |
| `directionality` | enum | No | `directed`, `bidirectional`, `unknown` |
| `agent_class_scope` | list<string> | No | Classes supporting inference |
| `support_trajectory_count` | int32 | No | Must be `>= 1` |
| `support_sample_count` | int32 | No | Must be `>= 1` |
| `mean_direction_rad` | float64 | Yes | Wrapped canonical direction |
| `estimated_width_m` | float64 | Yes | Must be `> 0` |
| `confidence` | float32 | Yes | `[0, 1]` |
| `method_id` | string | No | Inference method |
| `method_version` | string | No | Method version |
| `origin_type` | enum | No | `inferred` or `synthetic` |
| `quality_flags` | list<string> | No | Ordered flags |

## 12.2 Primary key

```text
(layout_id, corridor_id)
```

## 12.3 Canonical ordering

`layout_id`, `corridor_id`.

## 12.4 Producer and consumers

**Producer:** `kinematicweave.layout`
**Consumers:** grammar, connectivity inference, metrics, runtime, visualization

---

# 13. Inferred junction record

**Schema name:** `inferred_junctions`  
**Version:** `1.0`  
**Format:** Parquet

## 13.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `layout_id` | string | No | Owning inferred layout |
| `scenario_or_tile_id` | string | No | Evaluation unit |
| `junction_id` | string | No | Stable junction identifier |
| `geometry_wkb` | binary | No | Point or polygon |
| `junction_type` | enum | No | `merge`, `split`, `crossing`, `multiway`, `unknown` |
| `incident_corridor_ids` | list<string> | No | At least two unique references |
| `support_trajectory_count` | int32 | No | Supporting trajectory count |
| `confidence` | float32 | Yes | `[0, 1]` |
| `grade_separation_status` | enum | No | `connected`, `separated`, `ambiguous`, `not_applicable` |
| `method_id` | string | No | Inference method |
| `method_version` | string | No | Method version |
| `origin_type` | enum | No | `inferred` or `synthetic` |
| `quality_flags` | list<string> | No | Ordered flags |

## 13.2 Primary key

```text
(layout_id, junction_id)
```

## 13.3 Producer and consumers

**Producer:** `kinematicweave.layout`
**Consumers:** grammar, connectivity graph, metrics, runtime, visualization

---

# 14. Grammar-node record

**Schema name:** `grammar_nodes`  
**Version:** `1.0`  
**Format:** Parquet or JSON collection

## 14.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `layout_id` | string | No | Owning layout |
| `grammar_node_id` | string | No | Unique within layout |
| `node_type` | enum | No | `scene`, `region`, `corridor`, `junction`, `access_zone` |
| `parent_node_id` | string | Yes | Null only for root or approved detached diagnostic node |
| `geometry_ref_type` | enum | Yes | `map_element`, `corridor`, `junction`, `access_zone`, `inline` |
| `geometry_ref_id` | string | Yes | Referenced geometry |
| `inline_geometry_wkb` | binary | Yes | Used only when `geometry_ref_type=inline` |
| `semantic_attributes_json` | string | Yes | Canonical node attributes |
| `origin_type` | enum | No | Provenance category |
| `source_entity_ids` | list<string> | No | Ordered provenance references |
| `quality_flags` | list<string> | No | Ordered flags |

## 14.2 Validation

Exactly one root node of type `scene` is required for a complete layout grammar.

Hierarchy must be acyclic.

## 14.3 Primary key

```text
(layout_id, grammar_node_id)
```

## 14.4 Producer and consumers

**Producer:** oracle conversion, grammar construction, editing  
**Consumers:** runtime, reporting, visualization, grammar metrics

---

# 15. Grammar-relation record

**Schema name:** `grammar_relations`  
**Version:** `1.0`  
**Format:** Parquet

## 15.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `layout_id` | string | No | Owning layout |
| `relation_id` | string | No | Stable relation identifier |
| `source_node_id` | string | No | Existing grammar node |
| `target_node_id` | string | No | Existing grammar node |
| `relation_type` | enum | No | `contains`, `adjacent_to`, `derived_from`, `enters`, `exits`, `overlaps`, `corresponds_to` |
| `directed` | bool | No | Relation directionality |
| `attributes_json` | string | Yes | Canonical relation metadata |
| `origin_type` | enum | No | Provenance category |

## 15.2 Primary key

```text
(layout_id, relation_id)
```

---

# 16. Connectivity graph node record

**Schema name:** `graph_nodes`  
**Version:** `1.0`  
**Format:** Parquet

## 16.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `graph_id` | string | No | Owning graph |
| `graph_node_id` | string | No | Unique within graph |
| `node_type` | enum | No | `corridor_endpoint`, `junction`, `access_zone`, `routing_anchor` |
| `x_m` | float64 | No | Canonical x |
| `y_m` | float64 | No | Canonical y |
| `z_m` | float64 | Yes | Optional elevation |
| `entity_ref_id` | string | Yes | Corridor, junction, or access-zone reference |
| `origin_type` | enum | No | Provenance category |
| `quality_flags` | list<string> | No | Ordered flags |

## 16.2 Primary key

```text
(graph_id, graph_node_id)
```

---

# 17. Connectivity graph edge record

**Schema name:** `graph_edges`  
**Version:** `1.0`  
**Format:** Parquet

## 17.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `graph_id` | string | No | Owning graph |
| `graph_edge_id` | string | No | Unique within graph |
| `source_node_id` | string | No | Existing graph node |
| `target_node_id` | string | No | Existing graph node |
| `directed` | bool | No | Whether traversal is directional |
| `corridor_id` | string | Yes | Associated corridor |
| `geometry_wkb` | binary | Yes | Optional traversed geometry |
| `length_m` | float64 | No | Must be nonnegative |
| `cost` | float64 | No | Routing cost; must be nonnegative |
| `is_open` | bool | No | Current traversability |
| `closure_edit_id` | string | Yes | Edit responsible for closure |
| `origin_type` | enum | No | Provenance category |
| `quality_flags` | list<string> | No | Ordered flags |

## 17.2 Primary key

```text
(graph_id, graph_edge_id)
```

## 17.3 Canonical ordering

`graph_id`, `source_node_id`, `target_node_id`, `graph_edge_id`.

## 17.4 Producer and consumers

**Producer:** layout inference, oracle conversion, editing  
**Consumers:** runtime, rerouting, graph metrics, visualization

---

# 18. Edit-operation record

**Schema name:** `edit_operations`  
**Version:** `1.0`  
**Format:** JSON or Parquet

## 18.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `edit_id` | string | No | Stable edit identifier |
| `branch_id` | string | No | Branch receiving the edit |
| `parent_tape_or_branch_id` | string | No | Source version |
| `edit_sequence_index` | int32 | No | Zero-based order in branch |
| `edit_type` | enum | No | `delay_agent`, `replace_segment`, `close_corridor`, `reroute_agents`, `create_branch` |
| `target_ids` | list<string> | No | Ordered target references |
| `effective_start_time_ns` | timestamp_ns | Yes | Optional activation time |
| `effective_end_time_ns` | timestamp_ns | Yes | Optional end time |
| `parameters_json` | string | No | Canonical edit parameters |
| `preconditions_json` | string | Yes | Canonical preconditions |
| `status` | enum | No | `planned`, `applied`, `rejected`, `failed` |
| `affected_entity_ids` | list<string> | No | Ordered result references |
| `validation_json` | string | Yes | Post-edit checks |
| `created_by` | string | No | Tool or user identity |
| `created_time_ns` | timestamp_ns | Yes | Provenance time; excluded from deterministic state when appropriate |
| `error_message` | string | Yes | Required when failed |

## 18.2 Primary key

```text
(branch_id, edit_id)
```

## 18.3 Producer and consumers

**Producer:** `kinematicweave.editing`
**Consumers:** runtime, metrics, reporting, visualization

---

# 19. Query-result record

**Schema name:** `query_results`  
**Version:** `1.0`  
**Format:** JSON or Parquet

## 19.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `query_id` | string | No | Stable query identifier |
| `tape_or_branch_id` | string | No | Queried version |
| `query_type` | enum | No | `state_at_time`, `agent_lookup`, `semantic_filter`, `spatial_filter`, `temporal_event_filter`, `corridor_users`, `branch_compare` |
| `parameters_json` | string | No | Canonical resolved parameters |
| `result_entity_ids` | list<string> | No | Canonically ordered identifiers |
| `result_values_json` | string | Yes | Canonical scalar or structured values |
| `result_count` | int32 | No | Must match identifiers or declared value count |
| `result_hash` | string | Yes | Canonical deterministic hash |
| `elapsed_time_ms` | float64 | Yes | Benchmarking only |
| `status` | enum | No | `success`, `empty`, `invalid_query`, `failed` |
| `error_message` | string | Yes | Required when failed |

## 19.2 Producer and consumers

**Producer:** runtime  
**Consumers:** determinism tests, query benchmarks, reporting

---

# 20. Per-unit metric record

**Schema name:** `metric_records`  
**Version:** `1.0`  
**Format:** Parquet

## 20.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `run_id` | string | No | Experiment run |
| `experiment_id` | string | No | Experiment definition |
| `unit_type` | enum | No | `trajectory`, `scenario`, `tile`, `edit_scenario`, `tape`, `workload` |
| `unit_id` | string | No | Stable experimental-unit identifier |
| `method_id` | string | No | Evaluated method |
| `baseline_or_variant` | string | Yes | Baseline or ablation label |
| `seed` | int64 | Yes | Required for stochastic units |
| `metric_name` | string | No | Registered metric identifier |
| `metric_version` | string | No | Metric implementation version |
| `metric_value` | float64 | Yes | Null only for invalid/undefined metric |
| `metric_unit` | string | No | Examples: `m`, `rad`, `bytes`, `ms`, `ratio`, `count` |
| `validity_status` | enum | No | `valid`, `undefined`, `invalid_input`, `failed` |
| `invalid_reason` | string | Yes | Required unless valid |
| `sample_count` | int64 | Yes | Number of observations contributing |
| `weight` | float64 | Yes | Optional predeclared aggregation weight |
| `attributes_json` | string | Yes | Budget, tolerance, class, coverage, or other dimensions |
| `source_artifact_ids` | list<string> | No | Ordered provenance references |

## 20.2 Primary key

A metric record is uniquely identified by:

```text
(run_id, unit_type, unit_id, method_id, metric_name, attributes_json)
```

`attributes_json` must be canonicalized.

## 20.3 Canonical ordering

`run_id`, `unit_type`, `unit_id`, `method_id`, `metric_name`.

## 20.4 Producer and consumers

**Producer:** metrics and experiment runner  
**Consumers:** statistics, reporting, audit tools

---

# 21. Experiment manifest

**Schema name:** `experiment_manifest`  
**Version:** `1.0`  
**Format:** JSON

## 21.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `run_id` | string | No | Globally unique run identifier |
| `experiment_id` | string | No | Frozen experiment identifier |
| `experiment_version` | string | No | Experiment-definition version |
| `status` | enum | No | `planned`, `running`, `partial`, `complete`, `failed`, `cancelled` |
| `config_path` | string | No | Repository-relative source configuration |
| `resolved_config_json` | string | No | Canonical resolved configuration |
| `dataset_id` | string | No | Input dataset |
| `split_name` | string | No | Input split |
| `unit_type` | enum | No | Experimental-unit type |
| `planned_unit_count` | int64 | No | Planned count |
| `completed_unit_count` | int64 | No | Completed count |
| `failed_unit_count` | int64 | No | Failed count |
| `method_ids` | list<string> | No | Ordered evaluated methods |
| `metric_names` | list<string> | No | Ordered requested metrics |
| `seeds` | list<int64> | No | Ordered seeds |
| `git_commit` | string | No | Source revision |
| `git_dirty` | bool | No | Whether uncommitted changes existed |
| `python_version` | string | No | Runtime version |
| `environment_lock_id` | string | No | Lockfile or environment identity |
| `host_id` | string | No | Reference to captured system metadata |
| `start_time_utc` | string | Yes | ISO 8601 provenance time |
| `end_time_utc` | string | Yes | ISO 8601 provenance time |
| `raw_result_paths` | list<string> | No | Repository-relative or run-relative paths |
| `log_paths` | list<string> | No | Diagnostic logs |
| `failure_summary` | string | Yes | Required for failed or partial runs |

## 21.2 Producer and consumers

**Producer:** experiment runner  
**Consumers:** resume tooling, statistics, reporting, reproducibility checks

---

# 22. System-resource record

**Schema name:** `resource_observations`  
**Version:** `1.0`  
**Format:** Parquet

## 22.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `run_id` | string | No | Experiment run |
| `observation_id` | string | No | Stable observation identifier |
| `scope_type` | enum | No | `run`, `unit`, `stage`, `process` |
| `scope_id` | string | No | Referenced scope |
| `stage_name` | string | Yes | Pipeline stage |
| `start_time_utc` | string | Yes | ISO 8601 provenance time |
| `duration_s` | float64 | Yes | Nonnegative elapsed time |
| `cpu_time_s` | float64 | Yes | Nonnegative CPU time |
| `peak_rss_bytes` | int64 | Yes | Peak resident memory |
| `peak_gpu_memory_bytes` | int64 | Yes | Peak allocated or observed GPU memory |
| `disk_read_bytes` | int64 | Yes | Measured or estimated |
| `disk_write_bytes` | int64 | Yes | Measured or estimated |
| `output_bytes` | int64 | Yes | Generated output size |
| `processed_unit_count` | int64 | Yes | Units completed in scope |
| `throughput_units_per_s` | float64 | Yes | Derived throughput |
| `measurement_method` | string | No | Tool or implementation |
| `quality_flags` | list<string> | No | Ordered limitations |

## 22.2 Producer and consumers

**Producer:** resource monitor, experiment runner  
**Consumers:** feasibility evaluation, reporting, regression tests

---

# 23. Tape manifest

**Schema name:** `tape_manifest`  
**Version:** `1.0`  
**Format:** JSON

Although not listed separately in the initial batch plan, a tape-level manifest is required to connect the records above.

## 23.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `tape_id` | string | No | Stable tape identifier |
| `tape_version` | string | No | Tape schema/representation version |
| `scenario_id` | string | No | Source scenario |
| `layout_id` | string | No | Owning layout |
| `graph_id` | string | No | Connectivity graph |
| `coordinate_frame_id` | string | No | Canonical frame |
| `codec_ids` | list<string> | No | Codecs represented |
| `procedural_line_count` | int32 | No | Child count |
| `event_count` | int32 | No | Linked event count |
| `branch_parent_id` | string | Yes | Parent version |
| `origin_type` | enum | No | Provenance category |
| `source_artifact_ids` | list<string> | No | Ordered provenance |
| `artifact_paths` | map<string, string> | No | Paths to component files |
| `canonical_hash` | string | Yes | Defined in Batch 0.6 |
| `quality_flags` | list<string> | No | Ordered flags |

---

# 24. Artifact manifest

**Schema name:** `artifact_manifest`  
**Version:** `1.0`  
**Format:** JSON

## 24.1 Fields

| Field | Type | Nullable | Meaning / validation |
|---|---|---:|---|
| `artifact_id` | string | No | Stable artifact identifier |
| `artifact_type` | enum | No | `data`, `tape`, `raw_result`, `aggregate`, `figure`, `table`, `report`, `qualitative_image`, `qualitative_video`, `log` |
| `path` | string | No | Repository-relative or run-relative path |
| `producer` | string | No | Command or component |
| `producer_version` | string | No | Version |
| `run_id` | string | Yes | Producing run |
| `source_artifact_ids` | list<string> | No | Ordered direct inputs |
| `config_id` | string | Yes | Configuration reference |
| `git_commit` | string | No | Source revision |
| `content_checksum` | string | Yes | Later reproducibility policy may require it |
| `size_bytes` | int64 | No | File size |
| `created_time_utc` | string | Yes | Provenance time |
| `metadata_json` | string | Yes | Canonical additional metadata |

---

# 25. Cross-contract validation rules

## 25.1 Referential integrity

Required checks include:

- every agent references an existing scenario;
- every trajectory sample references an existing agent and trajectory;
- every event references an existing trajectory or procedural line;
- every segment references an existing procedural line;
- every control point references an existing segment;
- every grammar relation references existing grammar nodes;
- every graph edge references existing graph nodes;
- every edit target resolves in the parent tape or branch;
- every metric record references an existing run and unit.

## 25.2 Count integrity

Declared counts must match child records:

- scenario `agent_count`;
- agent `sample_count`;
- procedural line `segment_count`;
- segment `control_point_count`;
- tape `procedural_line_count`;
- manifest completed and failed unit counts.

## 25.3 Temporal integrity

- sample times strictly increase within a trajectory;
- segment intervals are ordered and nonzero;
- event intervals lie within supported trajectory or line intervals;
- branch edit order is explicit;
- query time parameters use canonical time units.

## 25.4 Geometry integrity

- persistent geometries are nonempty unless diagnostic status allows otherwise;
- geometry type matches declared type;
- all geometry uses the declared frame;
- invalid geometry repairs are flagged;
- planar crossings do not imply graph connectivity.

## 25.5 Deterministic ordering

All list fields used in hashing or comparison must have documented canonical order.

Where order has no semantic meaning, values are sorted lexicographically before serialization.

---

# 26. Producer–consumer summary

| Contract | Primary producer | Primary consumers |
|---|---|---|
| Scenario manifest | Data adapters | All modules |
| Agent metadata | Data adapters | Codecs, layout, runtime |
| Trajectory samples | Data adapters / decoders | Events, codecs, metrics |
| Semantic events | Event detectors | Codecs, runtime, metrics |
| Procedural lines | Codecs | Runtime, metrics |
| Procedural segments | Codecs | Runtime, serialization |
| Control points | Codecs | Runtime, visualization |
| Vector-map elements | Map adapters | Oracle grammar, evaluation |
| Inferred corridors | Layout inference | Grammar, graph, metrics |
| Inferred junctions | Layout inference | Grammar, graph, metrics |
| Grammar nodes/relations | Grammar module | Runtime, visualization |
| Graph nodes/edges | Layout/grammar/editing | Runtime, rerouting, metrics |
| Edit operations | Editing | Runtime, metrics, reporting |
| Query results | Runtime | Determinism and benchmarks |
| Metric records | Metrics/experiments | Statistics, reporting |
| Experiment manifest | Experiment runner | Resume, audit, reports |
| Resource observations | Resource monitor | Feasibility reports |
| Tape manifest | Codecs/runtime | Replay, audit, visualization |
| Artifact manifest | All artifact producers | Reproducibility and reporting |

---

# 27. Compatibility and migration

## 27.1 Reader behavior

Readers must:

- validate `schema_name`;
- validate supported major version;
- reject missing required fields;
- ignore unknown optional fields only when safe;
- preserve unknown metadata when round-tripping where practical.

## 27.2 Writer behavior

Writers must:

- emit canonical field names;
- emit one declared schema version;
- avoid deprecated fields in new artifacts;
- write deterministic ordering;
- record validation failures.

## 27.3 Migration

A schema migration must:

- identify source and target versions;
- preserve provenance;
- report dropped or transformed fields;
- validate the target artifact;
- never overwrite the only copy of a source artifact.

---

# 28. Batch 0.5 acceptance criteria

This document is acceptable when:

- every module boundary exchanges documented records;
- all core scene, layout, runtime, and experiment entities have persistent contracts;
- field types, units, nullability, and validation rules are explicit;
- canonical ordering is defined;
- producer and consumer ownership is clear;
- schemas support Parquet or JSON without hidden state;
- source, inferred, oracle, synthetic, decoded, and edited origins remain distinguishable;
- versioning and migration rules are explicit;
- later serialization and evaluation batches can refine byte layout and metrics without changing the conceptual contracts.
