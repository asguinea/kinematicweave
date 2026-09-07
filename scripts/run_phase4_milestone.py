"""Generate or verify the final Phase 4 motion-evidence milestone."""

from __future__ import annotations

import argparse
from pathlib import Path

from kinematicweave.reporting.phase4_milestone import (
    generate_phase4_milestone,
    verify_phase4_milestone,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Run the deterministic Batch 4.10 reporting workflow."""
    arguments = _arguments()
    repository_root = arguments.repository_root.resolve()
    if arguments.verify_only:
        verify_phase4_milestone(repository_root)
        return
    generate_phase4_milestone(
        repository_root,
        destination_root=arguments.output_root,
    )


if __name__ == "__main__":
    main()
