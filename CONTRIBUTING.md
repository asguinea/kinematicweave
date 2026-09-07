# Contributing

Thank you for considering a contribution to KinematicWeave.

## Before you start

For bug fixes and small documentation improvements, open a focused pull
request. For changes to data contracts, metrics, evaluation cohorts, or
persistent schemas, open an issue first so that compatibility and scientific
validity can be discussed before implementation.

Do not include private data, credentials, source-dataset files, or artifacts
whose redistribution terms are unclear.

## Development setup

KinematicWeave supports Python 3.12 and 3.13 and uses uv for dependency and
environment management:

```text
git clone <repository-url>
cd kinematicweave
uv sync --frozen
uv run --frozen python scripts/quality.py
```

The quality command checks the lockfile, formatting, linting, strict typing,
and the complete test suite. Add or update tests for every behavior change.

## Change guidelines

- Keep scientific logic in `src/kinematicweave/`; scripts should remain thin
  workflow entry points.
- Preserve deterministic ordering and canonical serialization.
- Include units in names or documentation for numeric quantities.
- Do not overwrite completed run directories or mutate frozen raw results.
- Record configuration, seeds, data identities, and failure counts for new
  experiment workflows.
- Keep provider-specific objects inside dataset adapters.
- Update the relevant definition and reproduction guide when a contract
  changes.

## Pull requests

Use a descriptive title and explain the problem, the approach, validation
performed, and any effect on reproducibility or stored schemas. Keep changes
focused. By contributing, you agree that your contribution is licensed under
Apache-2.0.

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md) in all project spaces.
