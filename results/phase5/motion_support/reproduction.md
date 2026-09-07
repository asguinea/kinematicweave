# Batch 5.2 Reproduction

The accepted Batch 5.1 Stage A artifact must exist and pass its verifier.

```bash
uv run --frozen python scripts/run_layout_feasibility.py --verify-only
uv run --frozen python scripts/run_motion_support.py
uv run --frozen python scripts/run_motion_support.py --verify-only
uv run --frozen pytest -q tests/test_motion_support.py tests/test_motion_support_integration.py
```

The full command performs two isolated 150-scenario motion-only generations,
compares their immutable artifacts, writes the tracked evidence twice in
isolated generated roots, verifies byte identity, and installs the verified
tracked copy. It does not open canonical map files or withheld-split scenarios.
