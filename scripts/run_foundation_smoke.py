"""Run the deterministic foundation smoke pipeline."""

import argparse
from pathlib import Path
import sys

from kinematicweave.errors import KinematicWeaveError
from kinematicweave.experiments.smoke import run_foundation_smoke


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--config", type=Path, default=Path("configs/project.toml"))
    parser.add_argument("--results-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the pipeline, and present its result."""
    arguments = _build_parser().parse_args(argv)
    try:
        result = run_foundation_smoke(
            arguments.repo_root,
            run_id=arguments.run_id,
            config_path=arguments.config,
            results_root=arguments.results_root,
        )
    except (KinematicWeaveError, OSError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 2

    sys.stdout.write(
        f"run_id: {result.run_id}\n"
        f"diagnostic_value: {result.diagnostic_value}\n"
        f"complete_manifest: {result.complete_manifest_path.as_posix()}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
