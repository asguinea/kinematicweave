# Phase 2 Data Foundation Runbook

This runbook covers the supported Phase 2 data-foundation workflow on the ASUS
laptop. Run commands from the repository root. Python and `uv` commands are
portable; the variable examples use PowerShell syntax.

## Preferred future environment

WSL2 with Ubuntu is the preferred environment for subsequent development. Check
the installed state from PowerShell:

```text
wsl.exe --status
wsl.exe --list --verbose
```

For sustained Linux-native work, clone or move the repository into the WSL
Linux filesystem, such as `~/src/kinematicweave`, instead of operating primarily
through `/mnt/c`. This avoids cross-filesystem metadata and I/O behavior.

Batch 2.15 was successfully completed on Windows on the reference ASUS laptop.
WSL2 is preferred for future work, but it is not required for AV2 provider
compatibility.

## Environment synchronization

Synchronize exactly from the committed lockfile:

```text
uv sync --frozen
```

## Environment doctor

Run the human-readable doctor:

```text
uv run --frozen kinematicweave doctor
```

For machine-readable diagnostics:

```text
uv run --frozen kinematicweave doctor --json
```

The `data_foundation` section reports the scientific-library versions, five
canonical schema fingerprints, registry identifiers, geometry check, and
synthetic probe.

## Registry and synthetic inspection

Inspect the approved dataset registry:

```text
uv run --frozen kinematicweave data registry
```

Build and validate the complete in-memory synthetic correctness set:

```text
uv run --frozen kinematicweave data synthetic-check
```

Both commands are read-only.

## External source discovery

Set an explicit local source root and write the manifest output to the ignored
data area:

```text
$sourceRoot = "<external-av2-source-root>"
New-Item -ItemType Directory -Force data/manifests | Out-Null
uv run --frozen kinematicweave data source-discover --dataset-id av2_motion --dataset-version "<dataset-version>" --adapter-version "1.0" --source-root $sourceRoot --source-root-label "local-av2" --checksum-mode sha256 > data/manifests/av2_source_manifest.json
```

Discovery is bounded by default. Use `--max-files` or `--max-total-bytes` to
set tighter limits for a particular local installation.

Verify the saved manifest against the same explicit source root:

```text
uv run --frozen kinematicweave data source-verify --manifest data/manifests/av2_source_manifest.json --source-root $sourceRoot
```

The manifest stores repository-relative source-file paths and a logical root
label; it does not serialize the absolute source root.

## Bounded canonical validation

Validate repository-relative canonical Parquet files without writing reports:

```text
uv run --frozen kinematicweave data validate --repository-root . --scenario-manifest data/canonical/scenario_manifest.parquet --coordinate-frame-metadata data/canonical/coordinate_frame_metadata.parquet --agent-metadata data/canonical/agent_metadata.parquet --trajectory-samples data/canonical/trajectory_samples.parquet --vector-map-elements data/canonical/vector_map_elements.parquet --minimum-valid-sample-count 10 --minimum-valid-duration-ns 1000000000 --require-source-map --batch-size 65536
```

Adjust thresholds only under the approved protocol. Repeat a table option when
the canonical table is partitioned across multiple Parquet files.

## Cache inspection and pruning

Inspect the default materialization cache:

```text
uv run --frozen kinematicweave data cache-scan --repository-root . --cache-root data/cache/materialization
```

Review incomplete entries with a dry run:

```text
uv run --frozen kinematicweave data cache-prune --repository-root . --cache-root data/cache/materialization
```

Only after reviewing that output, explicitly remove validated incomplete
entries:

```text
uv run --frozen kinematicweave data cache-prune --repository-root . --cache-root data/cache/materialization --apply
```

Do not use `cache-prune --apply` without first reviewing its dry-run output.
Completed immutable entries are verified and preserved.

## Laptop pilot API

Batch 2.13 intentionally provides no pilot CLI command. Use the typed Python API
with explicit placeholders:

```python
from pathlib import Path

from kinematicweave.artifact_store import finalize_run_directory, prepare_run_directory
from kinematicweave.data.pilot import (
    Av2PilotConfig,
    build_av2_pilot_plan,
    execute_av2_pilot,
    materialize_av2_pilot_artifacts,
    verify_av2_pilot_artifacts,
)
from kinematicweave.data.registry import dataset_source_manifest_from_json

repository_root = Path(".").resolve()
source_root = Path("<external-av2-source-root>").resolve()
manifest_path = Path("data/manifests/av2_source_manifest.json")
manifest = dataset_source_manifest_from_json(
    manifest_path.read_text(encoding="utf-8")
)

config = Av2PilotConfig(
    source_partition=Path("<source-partition>"),
    dataset_version="<dataset-version>",
    scenario_count=5,
    root_seed=0,
    assignment_namespace="av2-real-data-pilot-v1",
)
plan = build_av2_pilot_plan(manifest, config=config)
execution = execute_av2_pilot(
    repository_root,
    source_root,
    Path("data/cache/av2-pilot"),
    plan,
)

run = prepare_run_directory(
    repository_root,
    "results",
    "run:av2:local-pilot",
)
artifacts = materialize_av2_pilot_artifacts(run, execution)
verify_av2_pilot_artifacts(repository_root, artifacts)
finalize_run_directory(run)
```

The pilot verifies selected source files, processes scenarios sequentially,
validates canonical outputs through bounded reads with required maps, and
reuses complete immutable cache entries on a repeated execution.

## Genuine AV2 provider evidence

The accepted ten-scenario provider gate uses the official AV2 Motion Forecasting
S3 source and does not require the earlier four local pilot environment
variables:

```text
uv run --frozen python scripts/run_av2_provider_pilot.py --repository-root . --scenario-count 10 --data-root data/external/av2_motion --cache-root cache/av2_provider_pilot --evidence-root results/phase2/av2_provider_pilot --backend auto
```

The command uses deterministic selection, verifies or reuses official source
files, validates ten immutable seven-output cache entries, and performs an
identical reuse execution. Raw provider files and cache entries remain ignored
by Git. The small committed evidence is under
`results/phase2/av2_provider_pilot/`.

The earlier manual local-pilot API may still be configured with
`KINEMATICWEAVE_AV2_PILOT_SOURCE_ROOT`, `KINEMATICWEAVE_AV2_PILOT_SOURCE_MANIFEST`,
`KINEMATICWEAVE_AV2_PILOT_SOURCE_PARTITION`, and
`KINEMATICWEAVE_AV2_PILOT_DATASET_VERSION`. Those variables are optional compatibility
inputs and are not required by the accepted provider-evidence command.

## Phase 2 tests and quality checks

Run the phase-level integration suite:

```text
uv run --frozen pytest tests/test_phase2_integration.py -q
```

Run the complete quality and local CI checks:

```text
uv run python scripts/quality.py
uv run --frozen python scripts/ci.py
```

## Path and Git safety

- Use repository-relative paths for canonical data and caches.
- Keep external source roots as explicit local paths.
- Do not place provider data in Git.
- Do not commit cache entries or generated Parquet files.
- Keep source manifests free of absolute source roots.
- Keep credentials out of manifests, commands, and reports.
- Preserve source files as immutable inputs.
- Review cache-prune dry-run output before using `--apply`.
