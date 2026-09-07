"""Generate or verify the final showcase-media package."""

from __future__ import annotations

import argparse
from pathlib import Path

from kinematicweave.visualization.showcase_media import (
    generate_showcase_media,
    verify_showcase_media,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Generate or verify deterministic final showcase media."""
    arguments = _arguments()
    repository_root = arguments.repository_root.resolve()
    output_root = (
        repository_root
        if arguments.output_root is None
        else arguments.output_root.resolve()
    )
    if arguments.verify_only:
        verify_showcase_media(
            repository_root,
            destination_root=output_root,
        )
        return
    generate_showcase_media(
        repository_root,
        destination_root=output_root,
    )


if __name__ == "__main__":
    main()
