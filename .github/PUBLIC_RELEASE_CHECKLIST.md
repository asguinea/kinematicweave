# Public release checklist

- [x] Confirm that every contributor has authorized the repository's license.
- [x] Replace the organization-style author in `CITATION.cff` with the intended
      software authors and persistent identifiers.
- [x] Initialize the public repository without importing the existing local Git
      history; keep the pre-release Git metadata in a local backup.
- [x] Confirm that local archives, source datasets, caches, environment files,
      and unrelated PDFs are absent from the snapshot.
- [x] Run `uv run --frozen python scripts/ci.py` from the exact snapshot.
- [x] Authenticate GitHub CLI as the intended repository owner and push the
      reviewed initial commit.
- [x] Enable private vulnerability reporting, Dependabot alerts, secret
      scanning, push protection, and code scanning.
- [x] Protect the default branch and require the Quality and CodeQL checks.
- [x] Add a concise repository description and topics on GitHub.
- [ ] Create a signed `v0.1.0-alpha` tag only after the default branch is green.
