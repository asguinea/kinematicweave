# Foundation Setup

This guide describes the validated Phase 1 foundation environment and command
surface. It does not describe scientific codecs, datasets, layout inference, or
runtime features because those capabilities are not implemented.

## Prerequisites

The foundation requires:

- Git;
- uv;
- Python 3.12, installed directly or managed by uv.

Use official Git and uv documentation for platform installation. Phase 1
foundation checks do not require a GPU, WSL, network access after the locked
environment is available, or external datasets.

## Repository Location

Clone the approved repository and run commands from its root, the directory
containing `pyproject.toml`, `uv.lock`, `configs/`, `src/`, and `scripts/`.
Examples in this guide use repository-relative paths and assume that working
directory.

Inspect local changes before validation:

```text
git status --short
```

Do not include local datasets, generated results, or secrets in commits.

## Environment Synchronization

Create or synchronize the environment strictly from the committed lockfile:

```text
uv sync --frozen
```

The reference interpreter is Python 3.12. Do not bypass `--frozen` when
reproducing the foundation. Dependency changes require their own reviewed
lockfile update.

## Package Entry Points

The installed console entry point and module entry point expose the same
foundation CLI:

```text
uv run --frozen kinematicweave --version
uv run --frozen kinematicweave --help
uv run --frozen python -m kinematicweave --version
```

The currently implemented CLI commands are:

```text
uv run --frozen kinematicweave doctor
uv run --frozen kinematicweave doctor --json
uv run --frozen kinematicweave config validate
uv run --frozen kinematicweave config show
uv run --frozen kinematicweave artifact inspect-run run:example
```

`artifact inspect-run` is read-only. It reports whether the deterministic run
directory for the caller-provided run identifier is missing, partial, or
complete.

## Configuration

The default configuration is `configs/project.toml`. Validate it with:

```text
uv run --frozen kinematicweave config validate
```

Display its resolved canonical form with:

```text
uv run --frozen kinematicweave config show
```

Configuration paths are repository-relative. The checked-in configuration
selects `data/`, `results/`, `reports/`, `figures/`, and `qualitative/`.

## Environment Doctor

Run the human-readable readiness report with:

```text
uv run --frozen kinematicweave doctor
```

For machine-readable diagnostics:

```text
uv run --frozen kinematicweave doctor --json
```

The doctor checks repository structure, definitions, configuration, lockfile
identity, writable configured directories, disk reserve, Git state, GPU
availability, and WSL status. A missing GPU or native Windows execution can be
reported as a warning because the foundation remains CPU-capable.

## Quality and Local CI

Run the accepted quality gate with:

```text
uv run --frozen python scripts/quality.py
```

It checks the lockfile, formatting, linting, strict typing, and the complete
test suite.

Run the cross-platform local CI command with:

```text
uv run --frozen python scripts/ci.py
```

Local CI runs the quality gate first. It then creates a temporary Git
repository, copies only `configs/project.toml` and `uv.lock`, executes and
verifies the foundation smoke pipeline, checks duplicate-run rejection, and
removes the temporary repository. It does not write to the real `results/`
directory.

## Foundation Smoke Script

Display script options without creating a run:

```text
uv run --frozen python scripts/run_foundation_smoke.py --help
```

An actual smoke execution requires `--run-id`:

```text
uv run --frozen python scripts/run_foundation_smoke.py --run-id run:foundation-smoke:local-001
```

The run identifier is mandatory and caller-provided; the script never invents
one. Successful output is stored below the configured results root, normally
`results/runs/`, in a deterministic directory derived from that identifier.
Choose a new run identifier for each new smoke execution.

The smoke output is synthetic foundation evidence. It verifies configuration,
metadata, manifests, atomic artifacts, reporting, and finalization; it is not
scientific evidence.

## Run Directory States

A prepared run begins with `.run.partial`. Successful finalization replaces
that marker with `.run.complete`.

- A completed run is immutable and cannot be overwritten.
- A partial run is preserved for diagnosis and is not automatically resumed.
- A new smoke execution must use a new run identifier.
- Inspect state without mutation using `kinematicweave artifact inspect-run`.

Do not manually edit manifests, checksums, state markers, or completed
artifacts.

## Output Directory Behavior

Downloaded data, processed data, results, figures, qualitative exports, logs,
and caches are ignored where appropriate. Lightweight `.gitkeep`, README, and
manifest files may remain tracked. The foundation smoke script writes only
under the selected results root; local CI uses a temporary repository instead.

## Exit Codes

- `0`: the requested command completed successfully; doctor warnings may still
  be present.
- `1`: `kinematicweave doctor` completed with an overall failed readiness check, or a
  quality subprocess returned status 1.
- `2`: command-line usage was invalid, or an expected project or operating
  system error was reported by the CLI or smoke automation.

Quality and local CI propagate originating subprocess exit codes when
applicable. Read stderr and the preceding stage output before acting.

## Clean-Clone Verification

From a clean repository root, run:

```text
git status --short
uv sync --frozen
uv run --frozen kinematicweave --version
uv run --frozen kinematicweave config validate
uv run --frozen kinematicweave doctor
uv run --frozen python scripts/quality.py
uv run --frozen python scripts/ci.py
git status --short
```

The two Git status commands should show no tracked changes. Doctor warnings
about optional GPU or WSL state do not by themselves invalidate the CPU-capable
foundation.

## Platform Status

Native Windows has been validated for Phase 1 foundation commands and tests.
Ubuntu under WSL2 is the preferred long-term environment for approved
scientific experiments. Repository and manifest paths remain relative on both
platforms.

See the [ASUS operating runbook](asus_runbook.md) for approved laptop limits and
[troubleshooting guidance](troubleshooting.md) for conservative recovery
procedures.
