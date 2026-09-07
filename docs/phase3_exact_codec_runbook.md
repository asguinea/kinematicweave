# Phase 3 Procedural-Line Codec Runbook

## Scope

This runbook covers the Batch 3.1 exact hold/linear baseline, the Batch 3.2
error-bounded minimum-segment hold/linear codec, the Batch 3.3 velocity-aware
hold/linear/cubic-Hermite codec, and the Batch 3.4 position-and-velocity-bounded
hybrid codec. Batch 3.5 adds deterministic semantic waypoints and motion
events over that numerical replay layer. All preserve invalid-sample gaps and
deterministic replay. The exact codec is the correctness baseline; the compact
codecs optimize their declared candidate sets at fixed development settings.

Batch 3.6 adds deterministic motion categories and scenario-scoped route
templates without changing numerical replay.

## Environment

Use the locked Python 3.12 environment from the repository root. Prefer a
controlled clone in the Ubuntu WSL2 Linux filesystem, with one authoritative
Git history, repository-relative manifests, and provider/cache inputs restored
from their committed identities. Avoid performance-sensitive runs through
`/mnt/c`.

Batch 3.3 resolved the earlier contradictory WSL reports. The sandboxed process
identity had no registered distribution, while the actual development-user
context had Ubuntu registered as WSL version 2. Ubuntu was started and the
Batch 3.3 major tests and evidence workload ran CPU-only in a controlled clone
inside the Linux filesystem. Provider and canonical-cache inputs were copied
once from the authoritative worktree and their combined hashes were verified
before execution. The final tracked files returned to the authoritative
repository without creating divergent Git history.

Before evidence execution, verify:

```text
uv lock --check
uv sync --frozen
```

## Exact Baseline Execution

The exact runner builds the complete deterministic synthetic dataset, then
reads the committed Batch 2.15 validation report and canonical cache and
processes the same ten genuine provider scenarios sequentially:

```text
uv run --frozen python scripts/run_exact_codec_baseline.py
```

The command requires the ignored Batch 2.15 provider source and canonical
cache to be present. A completed generated-results root is immutable. For an
intentional repeat, pass a new repository-relative root:

```text
uv run --frozen python scripts/run_exact_codec_baseline.py --generated-root results/generated/phase3/exact_codec_baseline_repeat
```

## Compact Codec Execution

The compact runner uses the same synthetic and genuine-provider inputs,
encodes with `maximum_position_error_m = 0.10`, validates every source
timestamp, and compares segments and bytes with the committed exact evidence:

```text
uv run --frozen python scripts/run_piecewise_linear_codec.py
```

The 0.10 m value is a fixed development setting. The codec minimizes segment
count for that setting; it is not a release threshold selection.
Scientific comparisons across methods and tolerances remain Phase 4 work.

## Hermite Codec Execution

The Hermite runner uses the same complete synthetic and accepted genuine
provider inputs as the baseline runners:

```text
uv run --frozen python scripts/run_hermite_codec.py
```

For x and y, replay evaluates the standard cubic-Hermite basis using endpoint
velocity multiplied by elapsed seconds as tangent displacement. Its analytic
first derivative supplies replay velocity, and exact endpoint queries return
the stored endpoint fields. Elevation remains linear, headings retain
shortest-wrapped interpolation, and a Hermite candidate is unavailable unless
both endpoint velocity vectors are complete.

The deterministic objective first minimizes segment count. Equal-count
solutions then minimize total squared source-timestamp position error,
represented velocity error, later breakpoints recursively, and primitive
order. Consequently, fewer segments can coexist with worse velocity
diagnostics; consult the measured evidence rather than inferring that the
velocity-aware primitive improves every metric.

## Position-and-Velocity-Bounded Execution

The dual-bound runner uses the same complete synthetic and accepted genuine
provider inputs:

```text
uv run --frozen python scripts/run_velocity_bounded_codec.py
```

Candidates must satisfy both the fixed 0.10 m source-timestamp position bound
and the fixed 1.00 m/s represented x/y velocity-vector bound. Missing source
velocity is not invented or constrained. Candidate generation evaluates all
end points for each start and primitive with NumPy, caches accepted candidates,
and then runs the exact deterministic dynamic program over that candidate set.
The runner records candidate rejection counts and separates candidate
generation time from dynamic-programming time.

## Semantic Motion Execution

The semantic runner uses the complete synthetic dataset and the accepted
ten-scenario, 418-trajectory genuine AV2 provider set:

```text
uv run --frozen python scripts/run_semantic_motion.py
```

It detects structural waypoints plus gap, stop, left/right turn, acceleration,
and braking events from canonical source trajectories. Speed features prefer
stored speed, then velocity-vector norm, then within-run finite differences.
Heading features prefer stored heading, then moving velocity direction, then
within-run displacement. Invalid samples terminate runs and no detector
crosses a gap.

The thresholds are fixed development settings. Synthetic scenarios provide
exact oracles. AV2 lacks complete labels for these events, so source/replay
agreement measures preservation by the numerical codec rather than detector
correctness. Phase 4 owns threshold sweeps and broader scientific validation.

## Artifact Verification

Each scenario run contains exactly:

```text
tape_manifest.parquet
procedural_tracks.parquet
procedural_segments.parquet
codec_summary.json
```

Each runner reopens every artifact and checks canonical schemas, fingerprints,
checksums, row counts, references, aggregate counts, and replay. The compact
runners additionally verify parameter identity, primitive requirements, finite
replay, source gaps, exact retained breakpoints and run endpoints, and the
configured source-timestamp position bound. The Batch 3.4 runner also verifies
the represented velocity bound wherever source x/y velocity is present. A
second equivalent run must produce matching checksums for all three Parquet
files.

Each Batch 3.5 semantic run contains exactly:

```text
semantic_waypoints.parquet
motion_events.parquet
semantic_summary.json
```

Semantic verification checks schemas, checksums, references, event intervals,
gap integrity, canonical semantic JSON, source/replay preservation summaries,
and the Batch 3.4 position and represented-velocity bounds at every waypoint.
Numerical primitives remain in the referenced procedural tape.

Generated procedural and semantic artifacts remain ignored beneath:

```text
results/generated/phase3/
```

## Shared Motion Execution

The shared-motion runner classifies every accepted procedural track, resamples
positive-length valid runs at 32 normalized planar arc-length positions, and
applies deterministic greedy complete-link clustering within each scenario,
agent class, category, and event signature:

```text
uv run --frozen python scripts/run_shared_motion_templates.py
```

Construction uses source-frame trajectories, Batch 3.4 procedural records,
Batch 3.5 semantic records, and agent class only. Canonical lane centerlines
remain hidden until categories, templates, representatives, memberships, and
ordering are frozen. Map evaluation then measures ordered lane signatures,
route purity, edit similarity, and match distances without repairing templates.

The three Phase 3 layers have distinct authority:

- numerical procedural tracks provide bounded replay;
- semantic events identify source-derived motion structure;
- shared templates organize compatible runs symbolically and do not replace
  either earlier layer.

Each Batch 3.6 generated run contains exactly:

```text
motion_categories.parquet
route_templates.parquet
route_template_memberships.parquet
shared_motion_summary.json
```

The runner executes all 27 synthetic trajectories and the accepted ten-scenario,
418-trajectory AV2 set, performs independent deterministic construction and map
evaluation repeats, verifies the four-file bundles, and writes compact tracked
evidence. Generated Parquet remains ignored below
`results/generated/phase3/shared_motion_templates*`.

## Evidence Verification

Compact tracked evidence is stored in:

```text
results/phase3/exact_codec_baseline/
results/phase3/piecewise_linear_codec/
results/phase3/hermite_codec/
results/phase3/velocity_bounded_codec/
results/phase3/semantic_motion/
results/phase3/shared_motion_templates/
```

Run the integration check with:

```text
uv run --frozen pytest tests/test_phase3_integration.py tests/test_phase3_piecewise_integration.py tests/test_phase3_hermite_integration.py tests/test_phase3_velocity_bounded_integration.py -q
```

The integration checks verify component evidence checksums, genuine-provider
identity, processed scenario and trajectory counts, exact and bounded replay,
compact segment and byte comparisons, honest diagnostic regressions,
deterministic checksums, safe error-analysis identifiers, and the absence of
tracked provider or generated Parquet files.

For Batch 3.5, run:

```text
uv run --frozen pytest tests/test_semantic_motion.py tests/test_phase3_semantic_integration.py -q
```

The semantic integration check verifies exact synthetic oracles, genuine
provider identity and counts, event preservation, waypoint replay bounds,
evidence hashes, deterministic semantic Parquet checksums, and the absence of
tracked provider or generated semantic Parquet files.

For Batch 3.6, run:

```text
uv run --frozen pytest tests/test_shared_motion.py tests/test_phase3_shared_motion_integration.py -q
```

The shared-motion integration check verifies evidence hashes, exact schema
additions, all synthetic oracles, genuine AV2 identity and counts, nonzero
shared-template coverage, independent deterministic repeats, map-only
evaluation, safe diagnostics, and the absence of tracked provider or generated
Parquet files.

## Unified Package Execution

Batch 3.7 assembles the accepted numerical, semantic, and shared-motion layers
into an immutable dataset-level package. Run it from the WSL2 Linux-filesystem
repository with the Batch 2.15 provider source and canonical cache present:

```text
uv run --frozen python scripts/run_phase3_milestone.py
```

A completed generated root is immutable. Use a new repository-relative root
for an intentional repeat:

```text
uv run --frozen python scripts/run_phase3_milestone.py --generated-root results/generated/phase3/milestone_repeat
```

The runner builds the full stack twice in isolated Linux roots with identical
relative layouts. It verifies all referenced canonical artifacts before
constructing the package, then compares package identity, canonical JSON,
logical objects, and bundle checksums.

The generated package bundle contains exactly:

```text
procedural_motion_package.json
phase3_contract_snapshot.json
phase3_summary.md
```

Referenced Parquet remains in the generated source, procedural, semantic, and
shared subdirectories and is not copied into the bundle.

## Package Loading and Queries

The package API is read-only:

```python
from pathlib import Path

from kinematicweave.data.motion_tape_package import load_procedural_motion_package

root = Path.cwd()
package, reader = load_procedural_motion_package(
    root,
    "results/generated/phase3/milestone/genuine_av2_provider/package/"
    "runs/run-<identity>/procedural_motion_package.json",
)
scenario = reader.get_scenario(package.scenario_ids[0])
tape = reader.get_tape(scenario_id=scenario.scenario_id)
track = reader.get_track(tape.tracks[0].procedural_track_id)
events = reader.get_events(track.procedural_track_id)
memberships = reader.get_memberships(track.procedural_track_id)
state = reader.replay(track.procedural_track_id, track.start_time_ns)
inside_gap = reader.is_source_gap(track.procedural_track_id, track.start_time_ns)
```

Template queries return a template with all members; category queries return a
category with its ordered templates. Missing identifiers raise project-owned
errors. Loading verifies artifact size, SHA-256, schema, row count, global
multipart ordering, method identities, aggregate counts, construction policy,
and the complete cross-layer reference chain.

Verify the tracked milestone evidence with:

```text
uv run --frozen pytest tests/test_motion_tape_package.py tests/test_phase3_milestone_integration.py -q
```

Provider input remains under `data/external/`, canonical cache entries remain
under `cache/av2_provider_pilot/`, generated package artifacts remain under
`results/generated/phase3/milestone*`, and tracked compact evidence remains
under `results/phase3/milestone/`.
