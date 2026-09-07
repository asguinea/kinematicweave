# Architecture overview

KinematicWeave separates provider ingestion, canonical scene records,
procedural representations, evaluation, and artifact generation so that no
single layer silently changes the meaning of another.

```text
External dataset or generated fixture
                |
                v
        Provider adapter layer
                |
                v
      Canonical scene + map records
                |
        +-------+--------+
        |                |
        v                v
 Numerical codecs   Semantic events
        |                |
        +-------+--------+
                |
                v
       Replay and evaluation
                |
                v
  Immutable manifests and artifacts
```

## Core boundaries

- `domain/` defines immutable records and their invariants.
- `data/` owns provider adapters, schemas, validation, and artifact I/O.
- `codecs/` converts canonical trajectories into replayable procedural tracks.
- `events/` and `layout/` add semantic motion and shared-motion structure
  without changing the numerical replay source of truth.
- `metrics/` and `experiments/` implement comparisons and frozen workflows.
- `visualization/` and `reporting/` derive human-readable artifacts from
  recorded values.

All persistent identifiers, configuration, tables, and manifests have explicit
validation. Generated paths are repository-relative, completed runs are
immutable, and content hashes bind artifacts to their recorded provenance.

The detailed normative contracts live in [definitions](../definitions/README.md).
