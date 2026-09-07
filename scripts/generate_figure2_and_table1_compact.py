"""Generate or verify compact compact-ready Figure 2 and Table 1 assets."""

from __future__ import annotations

import argparse
from pathlib import Path

from kinematicweave.visualization.figure2_table1_compact import (
    generate_compact_assets,
    verify_compact_assets,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository containing the accepted release package.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional isolated destination; defaults to the repository root.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify existing compact outputs without regenerating them.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    repository_root = args.repository_root.resolve()
    destination_root = (
        repository_root if args.output_root is None else args.output_root.resolve()
    )
    if args.verify_only:
        evidence = verify_compact_assets(
            repository_root, destination_root=destination_root
        )
    else:
        evidence = generate_compact_assets(
            repository_root, destination_root=destination_root
        )
    if evidence["batch"] != "F2.2":
        raise RuntimeError("unexpected compact-asset evidence batch")


if __name__ == "__main__":
    main()
