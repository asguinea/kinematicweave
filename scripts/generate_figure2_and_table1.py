"""Generate deterministic aggregate Figure 2 and primary Table 1 outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from kinematicweave.visualization.figure2_rate_distortion import (
    generate_figure2_and_table1,
    verify_figure2_and_table1_outputs,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Generate or verify the complete F2.2 release package."""
    arguments = _arguments()
    repository_root = arguments.repository_root.resolve()
    output_root = (
        repository_root
        if arguments.output_root is None
        else arguments.output_root.resolve()
    )
    if arguments.verify_only:
        verify_figure2_and_table1_outputs(
            repository_root,
            destination_root=output_root,
        )
        return
    generate_figure2_and_table1(
        repository_root,
        destination_root=output_root,
    )


if __name__ == "__main__":
    main()
