# Phase 5 Layout-Feasibility Runbook

## Scope

Batch 5.1 determines whether repeated motion contains enough spatial evidence
to justify later layout-induction work. It is a descriptive feasibility gate,
not a layout implementation. It creates no corridors, centerlines, junctions,
transition graphs, semantic zones, road networks, or spatial grammar.

The frozen cohort is exactly the 150 development scenarios in the accepted
Phase 4 cohort manifest. Pilot and test access is prohibited. Exclusions remain
attached to the frozen membership and never trigger replacement.

## Hidden-map boundary

Stage A receives exactly four canonical files per scenario:

- `scenario_manifest.parquet`;
- `coordinate_frame_metadata.parquet`;
- `agent_metadata.parquet`;
- `trajectory_samples.parquet`.

The Stage A API rejects non-development roles and any non-allowlisted file set.
It derives the accepted velocity-bounded procedural replay, semantic events,
route candidates, and scenario-local shared-motion comparison in memory.
Canonical maps, map summaries, map paths, and map-derived geometry are absent.

Stage A writes an immutable generated bundle under
`cache/phase5_layout_feasibility/generation-*/stage_a/`. Its manifest freezes
the cohort, configuration, exact input checksums, bundle checksum, completion
state, and access audit.

Stage B verifies the complete Stage A identity and checksum before constructing
the matching development-map inputs. It refuses incomplete, altered, or
mismatched evidence and cannot write beneath the Stage A root. Repeated Stage B
execution must leave the Stage A snapshot byte-identical.

## Frozen descriptive contract

The feasibility contract is written before map diagnostics. It reuses the
accepted Batch 3.6 shared-motion thresholds and freezes spatial-support,
endpoint, crossing, elevation, map-distance, orientation, and map-sampling
criteria. These are qualified engineering and scientific criteria, not
hypothesis tests.

Every candidate claim is classified as one of:

- feasible now;
- feasible only with aggregation;
- feasible only as partial motion-supported structure;
- unsupported;
- deferred.

Stage B cannot change those rules, thresholds, cohort membership, exclusions,
or scenario selection.

## Execution

Run the optional deterministic resource probe:

```text
uv run --frozen python scripts/run_layout_feasibility.py --probe-scenarios 25 --generated-root cache/phase5_layout_feasibility_probe
```

The probe is never sufficient for PASS.

Run the full gate:

```text
uv run --frozen python scripts/run_layout_feasibility.py
```

The full command executes two isolated 150-scenario generations. Each
generation freezes Stage A, runs Stage B twice, and verifies that Stage A
remains unchanged. The two Stage A snapshots and Stage B diagnostics must be
byte-identical before tracked evidence is written.

Verify tracked evidence without opening provider data or maps:

```text
uv run --frozen python scripts/run_layout_feasibility.py --verify-only
uv run --frozen pytest -q tests/test_layout_feasibility.py tests/test_layout_feasibility_integration.py
```

## Evidence

Tracked evidence is under `results/phase5/layout_feasibility/`. Detailed Stage A
motion paths and repeated Stage B generated diagnostics remain ignored under
`cache/`.

The accepted run analyzed 5,940 layout-eligible tracks and retained 1,295
exclusions. Of the candidate valid runs, 45.35% participated in multi-track
spatial support, while 2.59% of eligible tracks participated in the stricter
accepted shared-template contract. The evidence includes 901 geometric
crossings: 304 with observed transition support, none resolved by source
elevation, and 597 retained as ambiguous.

Post-freeze diagnostics found that 49.57% of sampled motion points were within
the declared support distance of mapped structure, while observed motion
supported 32.52% of sampled local mapped structure. These values characterize
coverage and sparsity; they do not tune Stage A or establish layout recovery.

## Geographic aggregation

All 150 records use the declared AV2 city-map source CRS, but no shared parent
frame or transform-to-parent metadata is present. Cross-scenario comparability
therefore is not proven. Deterministic grouping is limited to scenario-local
construction, numerical proximity is not treated as geographic identity, and
multi-scenario aggregation is deferred.

## Failure and ambiguity policy

Zero counts, sparse scenarios, singleton support, fragmentation, exclusions,
ambiguous planar crossings, weak map coverage, and low shared-template coverage
remain in the evidence. A geometric crossing is never promoted to a junction
without observed transition or elevation evidence. Missing elevation preserves
ambiguity.

## Reproduction boundary

The Batch 5.1 command requires the accepted local development cache. It does not
download, inspect, or execute pilot/test scenarios. Phase 4 results remain
unchanged. Any future threshold, cohort, geographic-grouping, or production
layout change requires a separately approved batch.
