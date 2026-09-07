"""Generate deterministic Phase 4 qualitative motion evidence."""

from __future__ import annotations

from pathlib import Path

from kinematicweave.experiments.qualitative_motion_campaign import (
    run_qualitative_motion_campaign,
)


def main() -> None:
    """Run Batch 4.9 from the repository root."""
    run_qualitative_motion_campaign(Path.cwd().resolve())


if __name__ == "__main__":
    main()
