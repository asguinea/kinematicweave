"""Run the frozen Phase 4 representation attribution analysis."""

from __future__ import annotations

from pathlib import Path

from kinematicweave.experiments.representation_ablation_campaign import (
    run_representation_ablation_campaign,
)


def main() -> None:
    """Run Batch 4.8 from the repository root."""
    run_representation_ablation_campaign(Path.cwd().resolve())


if __name__ == "__main__":
    main()
