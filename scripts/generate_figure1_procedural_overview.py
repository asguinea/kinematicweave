"""Generate the deterministic final release Figure 1 package."""

from __future__ import annotations

import argparse
from pathlib import Path

from kinematicweave.visualization.figure1_procedural_overview import (
    generate_figure1_procedural_overview,
    verify_figure1_outputs,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
    )
    return parser.parse_args()


def main() -> None:
    """Generate or verify Figure 1 from repository-contained paths."""
    arguments = _arguments()
    repository_root = arguments.repository_root.resolve()
    output_root = (
        repository_root
        if arguments.output_root is None
        else arguments.output_root.resolve()
    )
    if arguments.verify_only:
        verify_figure1_outputs(output_root)
        return
    generate_figure1_procedural_overview(
        repository_root,
        destination_root=output_root,
        generated_root=arguments.generated_root,
    )


if __name__ == "__main__":
    main()
