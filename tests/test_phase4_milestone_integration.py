"""Repository-level checks for the frozen Phase 4 milestone package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from kinematicweave.reporting.phase4_milestone import (
    BATCH_LEDGER,
    INTEGRATION_STARTING_HEAD,
    MILESTONE_FILES,
    PHASE4_BASE_HEAD,
    REVIEW_PATH,
    SOURCE_EVIDENCE_PATHS,
)

ROOT = Path(__file__).resolve().parents[1]
MILESTONE = ROOT / "results/phase4/milestone"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tracked_files() -> tuple[str, ...]:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), "ls-files"),
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(completed.stdout.splitlines())


def test_milestone_directory_has_exact_expected_files() -> None:
    assert tuple(sorted(path.name for path in MILESTONE.iterdir())) == tuple(
        sorted(MILESTONE_FILES)
    )


def test_evidence_record_links_every_source_and_output_checksum() -> None:
    evidence = _json(MILESTONE / "evidence.json")
    assert evidence["accepted_batch_commit_ledger"] == BATCH_LEDGER
    assert evidence["accepted_batch_4_9_head"] == PHASE4_BASE_HEAD
    assert evidence["integration_starting_head"] == INTEGRATION_STARTING_HEAD
    assert evidence["source_evidence_sha256"] == {
        path.as_posix(): _sha256(ROOT / path) for path in SOURCE_EVIDENCE_PATHS
    }
    assert evidence["milestone_file_sha256"] == {
        name: _sha256(MILESTONE / name)
        for name in MILESTONE_FILES
        if name != "evidence.json"
    }
    assert evidence["milestone_review_sha256"] == _sha256(ROOT / REVIEW_PATH)
    assert evidence["prior_evidence_modified"] is False
    assert evidence["phase5_started"] is False


def test_review_contains_all_required_sections_and_ledger() -> None:
    review = (ROOT / REVIEW_PATH).read_text(encoding="utf-8")
    assert review.startswith(
        "# Phase 4 Milestone Review \N{EM DASH} Motion Representation Evidence"
    )
    headings = (
        "## 1. Milestone decision",
        "## 2. Research questions",
        "## 3. Cohort and protocol",
        "## 4. Method taxonomy",
        "## 5. Frozen metrics",
        "## 6. Primary matched-budget results",
        "## 7. Confirmatory statistics",
        "## 8. Ablation findings",
        "## 9. Qualitative findings",
        "## 10. Failures and exceptions",
        "## 11. Runtime and laptop feasibility",
        "## 12. Reproducibility",
        "## 13. Claim-to-evidence audit",
        "## 14. Known limitations",
        "## 15. Phase 5 entry criteria",
        "## 16. Reproduction commands",
        "## 17. Batch and commit ledger",
    )
    assert all(heading in review for heading in headings)
    assert PHASE4_BASE_HEAD in review
    assert INTEGRATION_STARTING_HEAD in review
    assert all(commit in review for commit in BATCH_LEDGER.values())
    assert "No universal winner is declared." in review


def test_repository_documents_the_freeze_and_reproduction_paths() -> None:
    decision_log = (ROOT / "definitions/19_decision_log.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/phase4_motion_evaluation_runbook.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## D-027 - Phase 4 motion evidence is frozen for milestone M4" in (
        decision_log
    )
    assert "**Status:** Accepted" in decision_log
    assert (
        "uv run --frozen python scripts/run_phase4_milestone.py --verify-only"
        in runbook
    )
    assert "docs/phase4_milestone_review.md" in readme
    assert "results/phase4/milestone/summary.md" in readme
    assert "implemented layout analysis is a descriptive" in readme


def test_reproduction_manifest_retains_accepted_expensive_sequence() -> None:
    reproduction = _json(MILESTONE / "reproduction_manifest.json")
    expensive = reproduction["complete_expensive_empirical_reproduction"]
    assert expensive["requires_official_provider_data"] is True
    assert expensive["commands"] == [
        "uv run --frozen python scripts/run_motion_evaluation_cohort.py",
        "uv run --frozen python scripts/run_motion_baselines.py",
        "uv run --frozen python scripts/run_motion_metrics.py",
        "uv run --frozen python scripts/run_motion_sweep.py",
        "uv run --frozen python scripts/run_protocol_freeze.py",
        "uv run --frozen python scripts/run_frozen_motion_campaign.py",
        "uv run --frozen python scripts/run_statistical_analysis.py",
        "uv run --frozen python scripts/run_representation_ablations.py",
        "uv run --frozen python scripts/run_qualitative_motion.py",
        "uv run --frozen python scripts/run_phase4_milestone.py",
    ]


def test_generated_provider_and_campaign_data_remain_untracked() -> None:
    tracked = _tracked_files()
    forbidden_suffixes = (
        ".parquet",
        ".zip",
        ".tar",
        ".tar.gz",
        ".exe",
        ".dll",
    )
    forbidden_parts = (
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "results/phase4/frozen_campaign/checkpoints/",
        "results/phase4/qualitative_motion/replay_frames/",
    )
    assert tuple(path for path in tracked if path.startswith("data/")) == (
        "data/.gitkeep",
    )
    assert not any(path.endswith(forbidden_suffixes) for path in tracked)
    assert not any(part in path for path in tracked for part in forbidden_parts)


def test_milestone_outputs_contain_no_private_paths_or_provider_ids() -> None:
    text = "\n".join(
        (MILESTONE / name).read_text(encoding="utf-8") for name in MILESTONE_FILES
    )
    text += (ROOT / REVIEW_PATH).read_text(encoding="utf-8")
    forbidden = (
        "C:\\Users\\",
        "/home/example-user/",
        "s3://",
        '"raw_provider_id"',
        '"scenario_id"',
        '"trajectory_id"',
    )
    assert all(token not in text for token in forbidden)
