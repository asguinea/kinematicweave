# Phase 3 Milestone Review — Procedural Motion Tape

## 1. Milestone decision

**Achieved.** M3 procedural motion tape achieved on synthetic and genuine AV2
provider data.

The frozen package joins canonical trajectories, bounded numerical procedural
tracks, semantic waypoints and events, and shared motion categories and route
templates. It persists only references to canonical Parquet artifacts and
provides deterministic read-only loading, querying, gap inspection, and replay.

## 2. Benchmark claims addressed

This milestone supplies implementation and development-set evidence for
procedural motion persistence, bounded replay, semantic preservation,
queryability, compactness accounting, and shared symbolic motion structure.
It does not establish release-scale comparative claims. Editing locality
remains outside this milestone.

## 3. Accepted batch and commit ledger

| Batch | Accepted commit |
| --- | --- |
| 3.1 | `790dd32e18628ff8f29f13da41360f789d833341` |
| 3.2 | `66a36cace93a848220458b90acf60586e8707a63` |
| 3.3 | `6eb9495e6e3aa205b69bc19805a28ea4f2a84e07` |
| 3.4 | `90e6a83439e9b0e49205a8c735044b404e4fa673` |
| 3.5 | `01c2e7f1af96a99327ef437c821780af1f8db8ec` |
| 3.6 | `38ad8ba28879206505929536e9eeea5bd2a51852` |
| 3.7 | Phase 3 integration commit; exact SHA is recorded in Git history and the batch report |

## 4. Frozen canonical contract

The registry remains exactly thirteen schemas in this order:
`scenario_manifest`, `coordinate_frame_metadata`, `agent_metadata`,
`trajectory_samples`, `vector_map_elements`, `procedural_tape_manifest`,
`procedural_tracks`, `procedural_segments`, `semantic_waypoints`,
`motion_events`, `motion_categories`, `route_templates`, and
`route_template_memberships`.

The exact Arrow schemas, metadata, Polars projections, empty-table behavior,
ordering, bounded reads, artifact verification, and fingerprints are frozen in
`results/phase3/milestone/contract_snapshot.json`. The dataset package
manifest is derived and is not a canonical schema.

## 5. Numerical procedural representation

The authoritative representation is the accepted Batch 3.4
position-and-velocity-bounded hybrid codec. On the ten AV2 scenarios it uses
2,327 segments for 418 tracks, with maximum measured source-timestamp position
error `0.09989488370623803 m` and represented velocity-vector error
`0.9995655552801491 m/s`. Source gaps are not bridged.

## 6. Semantic waypoints and motion events

The package contains 3,583 AV2 waypoints and 668 deterministic source-derived
events. Stop, turn, acceleration, and braking query cases were exercised.
The accepted aggregate source/replay F1 is `0.8892216`; this measures
preservation under the fixed detector, not event ground-truth accuracy.

## 7. Shared motion categories and templates

The genuine-provider package contains 86 categories, 414 route templates, 418
memberships, and 4 shared templates. Shared-template track coverage is
`1.9139%`. Where lane signatures were usable, mean exact lane-sequence purity
was `0.99846`. Templates are scenario-scoped observed representatives and AV2
maps were hidden during construction.

## 8. Unified package and query interface

`ProceduralMotionPackage` schema version `1.0` records dataset and method
identities, ordered scenario and tape IDs, aggregate counts, exact schema
fingerprints, named source identities, and thirteen logical table references.
Every physical part records repository-relative path, size, SHA-256, schema,
fingerprint, and row count.

The loader verifies bytes, schemas, multipart ordering, aggregate counts, and
the full source-to-membership reference chain. Its read-only API provides
scenario, tape, track, segment, waypoint, event, category, template,
membership, gap, and integer-timestamp replay queries.

## 9. Synthetic oracle evidence

The complete oracle gate processed 16 scenarios, 27 trajectories, 293 source
samples, 291 valid samples, and 28 valid runs. It produced 39 numerical
segments, 156 waypoints, 17 events, 6 categories, 28 templates, and 28
memberships. All accepted numerical, semantic, grouping, gap, and
determinism oracles passed.

## 10. Genuine AV2 evidence

The genuine gate used the unchanged Batch 2.15 official-provider selection:
10 scenarios and all 418 included trajectories. It produced 2,327 segments,
3,583 waypoints, 668 events, 86 categories, 414 templates, 418 memberships,
and 4 shared templates. Provider and canonical-cache tree identities remained
unchanged. No provider or generated canonical table is tracked.

## 11. Quantitative result snapshot

| Representation | AV2 segments | Bytes | Key result |
| --- | ---: | ---: | --- |
| Exact | 22,550 | 2,819,141 | zero endpoint error |
| Position-bounded linear | 2,488 | 570,662 | maximum position below 0.10 m |
| Unconstrained Hermite | 2,096 | 526,573 | measured velocity degradation |
| Position-and-velocity bounded | 2,327 | 553,731 | position 0.099895 m; velocity 0.999566 m/s |

Semantic artifacts use 678,216 bytes and shared-motion artifacts use 596,780
bytes. The accepted combined representation is 1,828,727 bytes, ratio
`0.26325` to 6,946,791 canonical source bytes.

The result is strong numerical compression with explicit bounded replay and
useful semantic preservation. Shared templates are coherent but deliberately
conservative; coverage is modest because grouping is scenario-scoped and
requires exact event signatures.

## 12. Determinism and reproducibility

Equivalent synthetic and AV2 integrations ran in isolated Linux roots with
identical repository-relative layouts. Package identities, canonical package
JSON, logical models, referenced Parquet checksums, and all three package
bundle checksums matched exactly. The AV2 package identity is
`bd1a11cc57f76e403f74575b14b118a282344dd009897c0e574d80109dfffa48`.

## 13. WSL2 and ASUS resource posture

The empirical gate ran under WSL2 on the Linux filesystem with Python 3.12.3,
CPU-sequential bounded per-scenario processing, and no GPU. The measured AV2
integration used 66,487,799 peak traced bytes and 133.51 seconds for the first
complete stack. Package construction took 0.318 seconds, cross-layer
validation 2.488 seconds, and verified package loading 5.126 seconds.

Median package-query latency was 0.104 ms; p95 was 0.191 ms. Median replay
query latency was 0.070 ms; p95 was 0.106 ms. These are development-set
measurements, not real-time claims.

## 14. Known limitations

- The ten-scenario provider set is a development evidence gate, not a
  release campaign.
- Semantic events are deterministic rules, not complete AV2 labels.
- Shared-template coverage is low by design under conservative grouping.
- The package is read-only and does not provide editing, branches, or rerouting.
- Package validation indexes the bounded development set in memory after
  streaming and verifying canonical batches.

## 15. Phase 4 and Phase 5 entry criteria

Phase 4 may begin only from this frozen schema, identity, bounded-replay,
semantic, grouping, and evidence contract. It owns large-scale method,
tolerance, and threshold evaluation. Phase 5 may consume the frozen motion
package only after its own approved batch and owns motion-derived spatial
grammar and layout induction. Neither phase may reinterpret map-evaluation
fields as construction inputs.

## 16. Reproduction commands

Run from the WSL2 Linux-filesystem repository:

```text
uv lock --check
uv sync --frozen
uv run --frozen pytest tests/test_motion_tape_package.py tests/test_phase3_milestone_integration.py -q
uv run --frozen python scripts/run_phase3_milestone.py
uv run --frozen python scripts/quality.py
uv run --frozen python scripts/ci.py
```

Generated Parquet and package bundles remain below
`results/generated/phase3/milestone*`. Tracked review evidence is under
`results/phase3/milestone/`.
