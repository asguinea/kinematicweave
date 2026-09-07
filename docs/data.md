# Data guide

## What is included

The repository contains generated fixtures for unit and integration tests,
selected aggregate measurements, manifests, and benchmark visualizations. It
does not contain the Argoverse 2 source dataset or raw canonicalized scenario
tables.

The adapter fixtures in `tests/fixtures/` are project-created. Their identifiers
and coordinates are synthetic and are deliberately small enough for fast,
offline validation.

## External dataset

The full evaluation uses the Argoverse 2 Motion Forecasting Dataset. Before
acquiring it, review the [provider's terms](https://www.argoverse.org/av2.html)
and [DATA_LICENSE.md](../DATA_LICENSE.md). The data is licensed separately from
the KinematicWeave source code.

Downloaded files belong under `data/external/` and are ignored by Git. The
acquisition workflow records the source release, object sizes, checksums, and a
deterministic cohort manifest. It supports the public provider endpoint without
requiring cloud credentials.

Inspect the available data commands with:

```text
uv run --frozen kinematicweave data --help
```

For the exact provider acquisition, discovery, validation, and materialization
sequence, follow the [data runbook](phase2_data_runbook.md). For the frozen
500-scenario cohort, follow the
[motion evaluation runbook](phase4_motion_evaluation_runbook.md).

## Repository safety

- Never commit `data/external/`, cache entries, raw scenario exports, or private
  selection maps.
- Do not assume that a small provider-derived file is redistributable.
- Keep all source paths out of committed manifests; store logical or
  repository-relative paths.
- Record exclusions with one primary reason and preserve counts before and
  after filtering.
- Verify available disk space before acquisition or materialization.
