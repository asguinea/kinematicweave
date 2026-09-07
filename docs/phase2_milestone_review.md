# Phase 2 Milestone Review — Canonical Data Ready

## 1. Milestone decision

Milestone M2 is achieved with genuine AV2 provider-data evidence.

The deterministic ten-scenario official `val` provider gate materialized all
ten immutable cache entries, completed bounded combined canonical validation,
and reused all ten entries on the identical second execution. The accepted
evidence was measured on the reference ASUS laptop.

## 2. Phase 2 scope delivered

Phase 2 delivers:

- five versioned canonical Arrow and Polars contracts;
- immutable scenario, coordinate-frame, agent, trajectory-sample, and vector-map
  records;
- deterministic canonical Parquet I/O with bounded reads;
- the complete synthetic correctness oracle;
- strict AV2 motion-Parquet and vector-map JSON adapters;
- canonical eligibility validation and exclusion reporting;
- deterministic geographic tiling and leakage-aware motion/layout splits;
- resumable, immutable materialization caching;
- data-foundation CLI and doctor integration;
- a sequential laptop-scale AV2 pilot API with resource measurements.

No Phase 3 scientific representation, inference, evaluation, or viewer work is
part of this decision.

## 3. Accepted batch and commit ledger

| Batch | Accepted commit |
|---|---|
| 2.1 | `439da08474da03105572d0beb5e730a58d574b2f` |
| 2.2 | `387454c9ec5a6db8a1e2e240f92840ba53db76ae` |
| 2.3 | `e3e202d427772ab5ed304f4ed9e4c7eb5d91bb1b` |
| 2.4 | `dedff082dbca5b7daed2e0c3b33cbd1b2bd67110` |
| 2.5 | `e0d72d4f49e71c3696af75e2d8988881c975a817` |
| 2.6 | `a644fedd5c16b21f4b1a178d22aa267300a78a43` |
| 2.7 | `de68b2af56864c7fcc90a429da4e72ff47b1518a` |
| 2.8 | `5ae8c3e7cf44aac659076bb6b7fe3b33361d7aef` |
| 2.9 | `2a0713700dedf10f1ca0fc9634f9b2f0372eea5b` |
| 2.10 | `56c4f4d2043449bf41029af8dccefb410a988ba0` |
| 2.11 | `6e4859071819ba45c3e665be3a099b3bf6330663` |
| 2.12 | `ab2ada78c3a3d377194d09a869f3a2f3a6ab40df` |
| 2.13 | `71e149ddba289763c042d72a491e452bb70bca1a` |
| 2.14 | `fbc95297424238d92edaab4b6f88ad92a2357f69` |
| 2.15 | This corrective evidence commit; exact SHA recorded in the batch report and Git history. |

Batch 2.15 supplies the genuine-provider evidence required for final M2
acceptance.

## 4. Canonical contract snapshot

The canonical registry order and SHA-256 fingerprints are:

| Schema | Fingerprint |
|---|---|
| `scenario_manifest` | `e59b221b1bf720832d90a19340f773c31b461292323d6ddb0a12fb8096d7d03b` |
| `coordinate_frame_metadata` | `ab8668ac6775de47623281bbe178e88202c0715cbb964057df7ece53c4b2f2ac` |
| `agent_metadata` | `7527dd3653e46f82ac835c81150c57677cd23c3a4eba2a705bd6a2dde3c0ab2b` |
| `trajectory_samples` | `24433aa9c49be2fc95be4fd6f8a30ad163cf1a2a6e116d42b7960be2a2714cfd` |
| `vector_map_elements` | `5f837f27a693002d9c43b9e9101d999a61f0ab53aa4a402c9bc1ce0d79ed0998` |

The phase-level snapshot verifies exact Arrow schemas, empty Arrow tables,
matching Polars schemas, canonical order, and lowercase 64-character
fingerprints without filesystem output.

## 5. End-to-end workflows verified

The Phase 2 integration suite verifies:

1. the complete synthetic dataset from immutable records through canonical
   Parquet, bounded validation, geographic tiles, motion/layout splits, and
   finalized artifacts;
2. paired project-created AV2 motion/map fixtures through coordinate
   translation, canonical Parquet, required-map validation, geometry decoding,
   tiles, leakage groups, splits, and finalization;
3. a four-candidate, three-scenario laptop pilot through deterministic
   selection, selected-source verification, sequential materialization,
   bounded validation, immutable cache reuse, and pilot artifacts;
4. the installed `kinematicweave` entry point, all seven approved data commands, exact
   synthetic counts, missing-cache inspection, expected error handling, and
   the doctor data-foundation section;
5. committed genuine-provider evidence identities, measurements, file
   checksums, exact deterministic selection, and achieved milestone decision.

All integration writes use temporary repositories and source directories. The
project `data/`, `results/`, and `cache/` paths remain unchanged.

## 6. Quantitative acceptance results

Synthetic oracle:

- 16 scenarios;
- 16 coordinate frames;
- 27 agents;
- 27 trajectories;
- 293 trajectory samples;
- 2 intentionally invalid diagnostic samples retained in canonical source data
  and handled by eligibility validation without silent deletion.

AV2 motion fixture:

- 4 source tracks;
- 15 source states;
- 3 included dynamic tracks under `dynamic_only`;
- 1 excluded static track.

AV2 map fixture:

- 3 lane segments;
- 6 lane boundaries;
- 3 generated centerlines;
- 1 road area;
- 1 crosswalk;
- 11 canonical map elements.

Batch 2.14 validation:

- 5 Phase 2 integration tests added and passed;
- complete suite: 1,315 passed and 10 expected Windows symbolic-link tests
  skipped;
- Ruff formatting: passed;
- Ruff lint: passed;
- mypy strict checking: passed;
- lockfile check and frozen synchronization: passed;
- local quality command: passed;
- local CI and isolated foundation smoke: passed.

Genuine AV2 provider pilot:

- 24,988 candidate `val` scenarios;
- 10 deterministically selected scenarios;
- 20 verified provider files totaling 2,086,085 bytes;
- 10 first-run materializations and 10 second-run immutable cache reuses;
- 70 declared per-scenario outputs totaling 2,366,878 bytes;
- 10 source scenarios, 428 source agents and trajectories, 23,068 samples,
  and 1,498 vector-map elements;
- 418 included agents and trajectories;
- 10 eligibility exclusions, all for insufficient duration;
- 189,689,856 measured peak process-memory bytes in the final evidence run;
- CPU-only execution with zero GPU use.

## 7. Determinism and reproducibility evidence

Equivalent isolated synthetic runs produced identical canonical Parquet
checksums, canonical validation-report JSON, tile index and JSONL, and motion
and layout split manifests.

Equivalent isolated AV2 fixture runs produced identical canonical motion and
map Parquet checksums. The genuine provider pilot reproduced its plan identity,
selected scenario order, materialization cache keys, canonical cache bytes, and
cache-entry checksums. Its second execution reused all ten immutable entries
without invoking a conversion worker.

Measured durations, disk-free snapshots, and pilot report JSON containing
resource measurements are intentionally excluded from byte-identical
cross-execution requirements.

## 8. ASUS laptop resource posture

The accepted data path is CPU-capable, uses bounded Parquet batches, processes
pilot scenarios sequentially, checks disk reserve before materialization, and
uses one immutable cache checkpoint per scenario. The pilot reports source
bytes, cache output bytes, stage durations, total duration, and disk-free
snapshots.

The accepted provider evidence measured 189,689,856 peak process-memory bytes and
retained approximately 600 GB free disk space. It was CPU-only with zero GPU
use. These measurements support the approved bounded laptop posture without
claiming full-dataset campaign performance.

## 9. Real-provider data status

Official AV2 Motion Forecasting data was acquired from
`s3://argoverse/datasets/av2/motion-forecasting/`, partition `val`, with
`s5cmd v2.3.0-991c9fb`. The exact selected IDs, source hashes, measurements,
cache-reuse evidence, and evidence-file checksums are committed under
`results/phase2/av2_provider_pilot/`.

Provider execution exposed and corrected two incompatibilities:

- timestamp endpoints are physical Arrow `double`; finite exactly integral
  values are losslessly normalized to canonical signed int64 nanoseconds
  without rounding;
- scenario-local maps can reference lanes outside their local crop; canonical
  topology retains only locally resolvable references, while exact omitted IDs
  are preserved in semantic provenance and relation-specific quality flags.

## 10. Known limitations and deferred work

The following remain explicitly deferred:

- full-dataset conversion;
- coverage subsampling experiments;
- scientific procedural-line codecs;
- motion and layout evaluation;
- inferred layout generation;
- interactive viewer work;
- the full experimental campaign.

The ten-scenario provider pass establishes compatibility and bounded laptop
execution, not evidence for final scientific conclusions or full-dataset
campaign performance.

## 11. Phase 3 entry criteria

The Phase 3 entry condition is satisfied for the accepted Phase 2
implementation:

The canonical data contracts, immutable records, deterministic Parquet layer,
synthetic oracle, AV2 motion/map adapters, validation, tiling, splits, caching,
CLI/doctor integration, and genuine-provider materialization/reuse gate all pass
their accepted tests.

Phase 3 work must still respect the deferred scientific and provider-data
boundaries above.

## 12. Reproduction commands

Run from the repository root in the locked environment:

```text
uv lock --check
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy src tests scripts
uv run --frozen pytest tests/test_phase2_integration.py -q
uv run --frozen pytest
uv run python scripts/quality.py
uv run --frozen python scripts/ci.py
```
