# Reproducibility guide

## Environment

The supported interpreter range is Python 3.12 through 3.13. Dependencies are
resolved by the committed `uv.lock` file.

```text
uv sync --frozen
uv run --frozen kinematicweave doctor
```

The doctor checks the interpreter, installed distribution, dependency versions,
configuration, repository layout, write access, and the configured free-space
reserve. A failed resource check is actionable and does not indicate a numeric
or schema failure.

## Level 1: offline smoke run

This workflow uses only generated data and exercises configuration, canonical
serialization, manifests, atomic artifact writes, and immutable completion:

```text
uv run --frozen python scripts/run_foundation_smoke.py --run-id example:smoke
```

Use a new run identifier for each invocation. The command will not overwrite a
completed run.

## Level 2: committed-evidence verification

These commands validate schemas, cross-file identities, checksums, selection
contracts, and stated gates without decoding external scenarios:

```text
uv run --frozen python scripts/run_phase4_milestone.py --verify-only
uv run --frozen python scripts/run_layout_feasibility.py --verify-only
uv run --frozen python scripts/generate_figure1_procedural_overview.py --verify-only
uv run --frozen python scripts/generate_figure2_and_table1.py --verify-only
```

## Level 3: full regeneration

Full regeneration requires the provider dataset and substantially more time and
disk space. Follow these documents in order:

1. [Data guide](data.md)
2. [Data foundation runbook](phase2_data_runbook.md)
3. [Motion evaluation runbook](phase4_motion_evaluation_runbook.md)
4. [Layout feasibility runbook](phase5_layout_feasibility_runbook.md)

The cohort, seeds, metrics, configurations, and analysis rules are frozen in
versioned manifests. Do not tune against pilot or test outcomes. A correction
requires a new experiment identity and preserved earlier outputs.

## Quality and determinism

Run the complete local gate with:

```text
uv run --frozen python scripts/quality.py
```

The gate verifies the lockfile, formatting, linting, strict typing, and tests.
The tests cover exact serialization, deterministic generation, corruption
detection, invalid input, path containment, schema round trips, and scientific
invariants.

Small artifacts must regenerate byte-for-byte. Numeric workloads record their
comparison tolerance where byte identity is inappropriate. Hardware timing and
peak-memory values are descriptive and can vary by platform.

## Provenance

Each release-grade run records its configuration, dataset and split identities,
seed derivation, environment, start and completion state, output paths, and
artifact checksums. Committed manifests use repository-relative or logical
paths rather than user-specific absolute paths.
