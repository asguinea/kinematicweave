"""Run the frozen Phase 4 confirmatory statistical analysis."""

from __future__ import annotations

from pathlib import Path

from kinematicweave.experiments.statistical_campaign import run_statistical_campaign


def main() -> None:
    """Run Batch 4.7 from the repository root."""
    run_statistical_campaign(Path.cwd().resolve())


if __name__ == "__main__":
    main()
