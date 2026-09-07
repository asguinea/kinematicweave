# Batch 5.2 Motion Support Fields and Route Bundles

## Boundary

The accepted immutable Batch 5.1 motion-only Stage A bundle was the only
scientific input. All 150 frozen
development scenarios completed with zero pilot, test, or map access.
Construction was scenario-local and no production layout object was created.

## Track reconciliation

- Canonical tracks retained from Batch 5.1: 7235
- Phase 2 eligible tracks: 7012
- Batch 5.1 layout-eligible tracks: 5940
- Batch 5.2 represented tracks: 5940
- Accepted exclusions retained: 1295
- Count discrepancy: 0

Every Batch 5.1 layout-eligible valid motion run participates. The larger Phase
2 eligible count includes stationary or near-stationary tracks retained in the
accepted exclusion accounting.

## Support fields

- Support elements: 48899
- Repeated/singleton support elements:
  13248 / 35651
- Multi-track/singleton-only supported tracks:
  2661 / 3279
- Endpoint/stop/turn support elements:
  9653 / 3015 / 4022

Each square 2 m support cell is derived by linear 1 m sampling of the accepted
Batch 5.1 path and records contributing safe track identities, orientation
bins, and an explicit 1.414214 m
center-to-corner support radius.

## Route bundles and transitions

- Route bundles: 5786
- Multi-track/singleton bundles:
  143 / 5643
- Bundle member-count median/p95:
  1.0 / 1.0
- Unbundled tracks: 0
- Directional/opposing compatible pairs:
  158 / 5
- Observed directed support transitions: 48362
- Merge/split evidence: 349 / 399
- Scenario fragmentation median/p95:
  0.9852941176470589 / 1.0

Opposing paths remain in separate bundles. Geometric crossings never create a
bundle connection by themselves.

## Crossing evidence

- Transition-connected crossings: 45
- Geometric-only ambiguous crossings: 791
- Elevation-separated disconnected crossings:
  0

The accepted Batch 5.1 stored path evidence contains XY but no per-point
elevation. Genuine elevation-separated support therefore remains zero and is
not inferred; the invariant is exercised by the deterministic synthetic oracle.

## Resources and determinism

- First/second generation runtime:
  124.012833 s /
  115.257803 s
- Peak Python traced memory:
  310714938 bytes
- Peak process RSS: 1136259072 bytes
- Serialized support bundle: 39514399 bytes
- Two isolated artifact generations byte-identical: true
- Two isolated tracked-evidence generations byte-identical: true

## Limitations

Support cells and bundles are derived evidence primitives, not corridors,
centerlines, junctions, graphs, zones, or spatial grammar. Singleton, sparse,
ambiguous, zero-count, and unfavorable findings are retained. No cross-scenario
geographic join was performed.
