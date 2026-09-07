# Foundation Troubleshooting

Use these procedures from the repository root. Preserve tracked files,
datasets, partial evidence, and completed results while diagnosing failures.

## uv Is Not Found

Run:

```text
uv --version
```

If the command is unavailable, use current official uv documentation to install
or expose uv on `PATH`, then open a new terminal and retry. Do not replace the
locked workflow with an unrecorded global environment.

## Python Version Is Unsupported

The reference version is Python 3.12. Confirm the active managed interpreter:

```text
uv run --frozen python --version
```

Synchronize with:

```text
uv sync --frozen --python 3.12
```

Do not edit the supported range or lockfile merely to accommodate an unrelated
interpreter.

## uv.lock Is Out of Date

Check the lockfile:

```text
uv lock --check
```

Do not bypass frozen validation. If a reviewed dependency change is intended,
handle the lockfile update in its approved dependency batch and run the full
quality suite. Otherwise restore the committed project and lock files without
rewriting Git history.

## kinematicweave Entry Point Is Not Found

Synchronize the environment and verify both entry points:

```text
uv sync --frozen
uv run --frozen kinematicweave --version
uv run --frozen python -m kinematicweave --version
```

If the module works but the console entry point does not, report the uv and
Python versions plus the synchronization output.

## Doctor Reports a Dirty Git State

Inspect only the concise status:

```text
git status --short
```

Review each path and preserve intentional work. Remove only files you created
and understand, or commit them in their approved batch. Do not reset, rewrite
history, or discard another contributor's changes.

## Doctor Reports a Missing GPU

The foundation is CPU-capable, and GPU availability is optional during Phase
1. A missing GPU warning needs no workaround for quality, configuration, smoke,
or local CI checks. Do not install drivers or CUDA solely to silence this
warning.

## Doctor Reports Non-WSL Execution

Native Windows is validated for Phase 1 foundation work. Non-WSL status is
informational for these checks. WSL2 remains the preferred environment for
approved scientific experiments; use current official platform documentation
when establishing that environment.

## Configuration Validation Fails

Run:

```text
uv run --frozen kinematicweave config validate
uv run --frozen kinematicweave config show
```

Check TOML syntax, schema version, root seed, and repository-relative path
values in `configs/project.toml`. Do not replace a failing configuration with
machine-specific absolute paths.

## Configured Directories Are Missing

Run the doctor to identify the affected path:

```text
uv run --frozen kinematicweave doctor
```

Confirm that the configured path is inside the repository and has the intended
ownership. Restore tracked directory keepers when applicable, or create only
the confirmed missing directory. Do not create fake datasets or redirect
outputs into definitions or source directories.

## Free Disk Reserve Is Insufficient

Normal operation must retain at least 15% free disk space. Stop before starting
new output-producing work. Inspect storage ownership, remove only reproducible
caches you can identify safely, or relocate locally owned data through a
reviewed configuration change. Preserve source datasets, manifests, and
results.

## A Smoke Run Is Partial

Inspect the caller-provided identifier:

```text
uv run --frozen kinematicweave artifact inspect-run run:example
```

A partial run is preserved for diagnosis and is not automatically resumed.
Review its manifests and failure summary. Use a new run identifier for another
smoke execution. Do not blindly remove the partial directory or edit its state
markers.

## A Smoke Run Is Complete

Completed runs are immutable and cannot be overwritten. Inspect the run, keep
its artifacts and manifests intact, and choose a new caller-provided run
identifier for a new smoke execution. Never delete a completed run directory
as a routine troubleshooting step.

## Exit Codes

- Exit code `0`: the command succeeded; doctor may still report warnings.
- Exit code `1`: doctor reported an overall failed readiness check, or an
  underlying quality check returned 1.
- Exit code `2`: argument parsing failed, or an expected project or operating
  system error was reported by the CLI, smoke script, or local CI smoke stage.

Quality and local CI may propagate other nonzero subprocess exit codes. Read
the first failing stage rather than disabling later checks.

## Windows Symbolic-Link Test Skips

On Windows, two artifact-store tests may skip when the account lacks symbolic
link privileges. The skip reason should explicitly cite the operating-system
privilege error. These skips are not test failures and do not justify disabling
the remaining suite or changing system security settings.

## Cleaning Ignored Caches

Typical disposable caches include `.pytest_cache/`, `.ruff_cache/`,
`.mypy_cache/`, and Python `__pycache__/` directories. Confirm each target
against `.gitignore` and remove only those specific cache directories using
normal filesystem tools.

Do not use a broad cleanup command that could remove ignored `data/`,
`results/`, figures, qualitative exports, environments, or other locally owned
content. Never commit local datasets.

## Gathering Diagnostics Safely

Useful diagnostic output includes:

```text
uv --version
uv run --frozen python --version
uv run --frozen kinematicweave doctor
uv run --frozen kinematicweave doctor --json
uv run --frozen kinematicweave config show
git status --short
```

Before sharing output, review it for repository locations, usernames, host
details, or project-sensitive metadata. Share only the relevant check messages
and versions.

Do not publish full environment-variable dumps, secret files, tokens,
credentials, private repository URLs, or unrelated system information.

Return to [Foundation Setup](foundation_setup.md) for the validated command
sequence or consult the [ASUS Runbook](asus_runbook.md) for operating limits.
