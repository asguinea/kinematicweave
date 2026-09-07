# Glossary

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.1 — Project Charter and Research Scope  
**Status:** Draft for approval

This glossary defines the terms used by the project. Later Phase 0 batches may add terms or refine mathematical details, but implementation must not assign conflicting meanings.

## 2.5D

A spatial representation in which horizontal position is modeled explicitly and elevation may be stored or visualized, while the main geometric and topological reasoning occurs in a locally planar coordinate system.

For this project, 2.5D normally includes x, y, heading, and time, with optional z.

## Access zone

A persistent spatial region associated with recurring trajectory starts, ends, entries, exits, parking activity, transit access, or similar endpoint behavior.

## Agent

A dynamic entity represented in a scenario, such as a vehicle, cyclist, or pedestrian.

An agent has a stable identifier within its identifier scope and may own one or more trajectories or procedural lines.

## Baseline

A comparison method implemented and evaluated under the same declared input, split, size-accounting, and metric rules as the proposed method.

## Branch

A derived version of a tape created from a parent tape by one or more edits.

A branch preserves provenance linking it to its parent and to the operations that produced it.

## Codec

A representation method with a common interface for encoding, decoding, serialization, deserialization, and serialized-size reporting.

A codec may be lossless or lossy.

## Connectivity graph

A graph representing which corridors, junctions, or access zones are traversably connected and, where applicable, the permitted direction of travel.

It is distinct from the layout hierarchy or grammar tree.

## Control point

A stored point or parameter that helps define a procedural segment.

A control point may include position, time, tangent, heading, or other attributes according to the later serialization specification.

## Core scope

The minimum work required to support the project's primary empirical claims and reproducible evidence package.

Core scope blocks project completion if missing.

## Corridor

A persistent traversable spatial element inferred or provided as ground truth.

Examples include a lane-like path, road corridor, sidewalk path, or repeated movement channel.

## Dataset adapter

A module that converts an external dataset into the project's canonical internal contracts.

Dataset-specific structures must not propagate beyond the adapter boundary.

## Decode

Reconstruct supported trajectory states or samples from a stored representation.

## Determinism

The property that a defined operation produces the same canonical result under the documented input, configuration, ordering, software, and numeric conditions.

## Deterministic replay

Replay that produces the same canonical state sequence or state hashes across repeated executions under the documented determinism conditions.

## Dynamic entity

See **Agent**.

## Edit

A recorded operation that changes a tape or creates a branch.

Examples include delay, segment replacement, corridor closure, and rerouting.

## Edit locality

The amount of the stored representation that must be modified to express an edit.

The project may measure this using modified records, symbols, segments, bytes, or another definition fixed in a later evaluation document.

## Encode

Convert an input trajectory or scene element into a specified stored representation.

## Event

See **Semantic event**.

## Event-aware procedural line

A procedural line whose segmentation or stored metadata explicitly represents selected semantic events.

## Experimental unit

The independent unit over which a metric or statistical comparison is aggregated.

Examples may include a scene, trajectory, agent, or geographic tile. Individual trajectory samples are not automatically independent experimental units.

## Full 3D

A representation or evaluation that reasons generally over three spatial dimensions rather than primarily in a locally planar frame.

The core initial evaluation does not claim general full-3D reconstruction.

## Geographic tile

A bounded spatial region used to aggregate trajectories and evaluate inferred layout.

The exact tile size, overlap, and split rules will be defined later.

## Grammar

A structured symbolic representation containing typed nodes, relations, and possibly transformation or refinement rules.

In this project, the grammar represents persistent scene layout and may encode hierarchy separately from traversable connectivity.

## Grammar node

A typed symbolic element in the layout grammar.

Examples include scene, region, corridor, junction, and access zone.

## Grammar repair

Rule-based or constrained processing that modifies an inferred layout or connectivity graph to improve structural validity.

Examples may include merging nearly continuous segments, resolving small gaps, or regularizing junction connectivity.

Grammar repair must be evaluated separately from raw clustering or geometry extraction.

## Ground truth

Reference annotations used for evaluation.

Ground truth is not automatically perfect; any limitations relevant to interpretation must be documented.

## Heading

An agent's planar orientation under the project's fixed coordinate and angle convention.

The exact axis, direction, range, and wrapping policy will be defined in the coordinate conventions.

## Infrastructure

Persistent spatial structure that constrains or supports motion.

In the core project, this primarily means corridors, paths, junctions, entrances, exits, and access zones.

## Inference

Estimation of an unknown representation from allowed inputs.

Converting a known ground-truth map into the project's grammar is not inference; it is oracle conversion or representation-capacity testing.

## Interpolation

A defined method for reconstructing states between stored samples or control points.

## Junction

A persistent layout element at which two or more traversable corridors connect, merge, split, or cross according to the project's topology definition.

## Layout

The persistent spatial component of a tape.

It may include geometry, semantic categories, hierarchy, and connectivity.

## Layout grammar

The symbolic or grammar-oriented structure used to represent persistent layout elements and their relations.

## Matched budget

An evaluation condition in which comparison methods are constrained to equivalent storage, control-point count, or another explicitly defined resource.

## Motion-derived layout

Persistent layout estimated using repeated agent trajectories as the principal inference input.

## Motion event

See **Semantic event**.

## Motion trace

An observed or reconstructed sequence describing an agent's movement through space and time.

A motion trace may be raw, resampled, simplified, or decoded.

## Non-goal

A capability or claim intentionally excluded from the current project scope.

A non-goal is not a missing feature unless the scope is explicitly changed.

## Optional scope

Work that may improve demonstration value or future extensibility but is not required for the primary evidence package.

## Oracle grammar

A grammar produced using ground-truth layout information.

It is used to test representation capacity or establish an upper bound, not trajectory-only inference quality.

## Path

A general traversable route or repeated movement channel.

When used as a typed persistent layout element, the project should prefer the more specific term **Corridor** unless a later domain model defines a separate path type.

## Perception pipeline

The upstream process that may estimate detections, identities, depth, pose, or trajectories from sensor data.

The perception pipeline is optional in the core project.

## Persistent

Stored across replay operations and available for serialization, querying, versioning, or editing rather than existing only as a transient rendered frame.

## Photorealism

Visual similarity to real imagery.

Photorealism is not a primary objective or metric for this project.

## Preliminary empirical evidence

Reproducible quantitative and qualitative results that test precisely scoped claims under documented conditions, while acknowledging that the study does not establish universal validity.

## Procedural line

A serializable, time-indexed symbolic motion representation for one agent or one motion component.

A procedural line contains enough information to reconstruct supported motion states and may contain segments, control points, interpolation metadata, and semantic events.

## Procedural segment

A bounded component of a procedural line with a defined time interval and reconstruction rule.

Segments may be separated by semantic events or representation decisions.

## Provenance

Metadata recording the origin and transformation history of data, representations, edits, results, or artifacts.

## Query

A defined request for information from a tape.

Examples include selecting agents by class, retrieving states at a time, or identifying motion within a spatial region.

## Raw result

A machine-readable metric or observation produced directly by an experiment before statistical aggregation or manual interpretation.

Raw results should be immutable after generation.

## Raw trajectory

The canonical input sequence before lossy representation by a codec.

"Raw" in this project normally means canonical processed trajectory data, not necessarily unprocessed sensor measurements.

## Reconstruction

The output produced by decoding a stored representation at selected times.

## Replay

Reconstruction of scene or agent states over a time interval according to the tape's stored representation.

## Representation capacity

The ability of the representation format to encode a target structure when that structure is already known.

Representation capacity is distinct from inference quality.

## Representation size

The measured serialized size of all information required to reconstruct the evaluated output under the declared protocol.

In-memory Python object size is not the primary representation-size metric.

## Rerouting

Replacing an affected route with a path computed over the connectivity graph after an edit such as a corridor closure.

Rerouting does not by itself imply behaviorally realistic prediction.

## Scenario

A bounded dynamic-scene example with a defined spatial frame, temporal interval, agents, and associated metadata.

## Scene

The represented environment and its contents.

A scenario is the evaluation or data unit that bounds a particular scene interval.

## Secondary scope

Useful work that can strengthen the evidence but may be simplified or deferred without invalidating the primary project.

## Semantic

Associated with an explicit documented category, event, relation, or query meaning.

Human readability alone does not make a representation semantic.

## Semantic event

A typed, time-localized or interval-based motion occurrence with a documented operational definition.

Initial examples include stops and turns. Exact detection and matching rules will be defined later.

## Serialization

Conversion of an in-memory representation into a canonical stored byte sequence or declared file representation.

## Spatial grammar

See **Layout grammar**.

## State

The set of represented agent or scene attributes at a particular time.

The exact canonical state fields will be defined later.

## Symbol

A typed element of the stored representation, such as a grammar node, event, segment, or edit operation.

## Symbolic edit

An edit expressed in terms of typed representation elements or semantic operations rather than direct manual rewriting of every affected trajectory sample.

## Tape

See **KinematicWeave**.

## Tape runtime

The system responsible for loading a tape, replaying it, answering supported queries, applying edits, creating branches, and producing deterministic states.

## Topology

The connectivity and relational structure of the inferred or ground-truth layout, independent of exact geometric coordinates.

## Topology repair

See **Grammar repair**.

## Trajectory

An ordered representation of an agent's position and related state over time.

## Trajectory coverage

The amount or diversity of motion evidence available for layout inference within a defined region.

Coverage may be manipulated through controlled subsampling, but its exact operational definitions will be fixed later.

## Trajectory sample

A time-indexed observation or canonical record containing an agent's position and any other defined attributes.

## Versioned

Carrying sufficient identity and provenance to distinguish revisions of a tape, schema, configuration, or result artifact.

## What-if variation

A branch or edit that changes represented conditions so an alternative replay can be inspected.

A what-if variation is not automatically a validated prediction of real-world consequences.

## KinematicWeave

A versioned, serializable, deterministic representation of a dynamic scene that combines persistent layout with procedural, time-indexed motion and supports the project's defined replay, query, and edit operations.
