"""Generate or verify compact compact-ready Figure 1 assets."""

from __future__ import annotations

import argparse
from pathlib import Path

from kinematicweave.visualization.figure1_procedural_overview_compact import (
    generate_compact_figure1,
    verify_compact_figure1,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    repository_root = arguments.repository_root.resolve()
    destination = (
        repository_root
        if arguments.output_root is None
        else arguments.output_root.resolve()
    )
    if arguments.verify_only:
        evidence = verify_compact_figure1(repository_root, destination_root=destination)
    else:
        evidence = generate_compact_figure1(
            repository_root, destination_root=destination
        )
    if evidence["batch"] != "F1.3":
        raise RuntimeError("unexpected compact Figure 1 evidence batch")


if __name__ == "__main__":
    main()
