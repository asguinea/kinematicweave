"""Committed-evidence integration checks for Phase 4 Batch 4.4."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from kinematicweave.experiments.motion_sweep import (
    BYTE_RATIO_TARGETS,
    KEYFRAME_RATIO_TARGETS,
)

_COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)


@pytest.fixture(scope="module")
def repository_root() -> Path:
    return Path(__file__).parents[1]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sweep_evidence_has_exact_ranked_subset_and_frozen_grid(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/motion_sweep"
    evidence = _json(evidence_root / "evidence.json")
    contract = _json(evidence_root / "sweep_contract.json")
    metric_contract = _json(
        repository_root / "results/phase4/motion_metrics/metrics_contract.json"
    )
    scenarios = contract["scenarios"]
    assert evidence["batch_decision"] == "achieved"
    assert evidence["cohort_identity"] == _COHORT_IDENTITY
    assert evidence["validation_identity"] == _VALIDATION_IDENTITY
    assert evidence["development_scenario_count"] == len(scenarios) == 25
    assert [row["selection_rank"] for row in scenarios] == list(range(1, 26))
    assert [row["source_scenario_id"] for row in scenarios] == metric_contract[
        "development_scenario_ids"
    ][:25]
    assert evidence["parameter_point_count"] == len(contract["parameter_grid"]) == 39
    family_counts: dict[str, int] = {}
    for point in contract["parameter_grid"]:
        family = point["family"]
        family_counts[family] = family_counts.get(family, 0) + 1
    assert family_counts == {
        "raw_samples": 1,
        "exact_adjacent": 1,
        "uniform_linear": 5,
        "uniform_hermite": 5,
        "fixed_interval_linear": 5,
        "rdp_linear": 5,
        "position_bounded_linear": 4,
        "unconstrained_hermite": 4,
        "position_velocity_hybrid": 9,
    }
    assert contract["byte_ratio_targets"] == list(BYTE_RATIO_TARGETS)
    assert contract["keyframe_ratio_targets"] == list(KEYFRAME_RATIO_TARGETS)
    assert contract["pilot_or_test_metadata_or_outcomes_accessed"] is False
    assert contract["final_method_selected"] is False


def test_sweep_execution_matching_metrics_and_repeat_reuse(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/motion_sweep"
    evidence = _json(evidence_root / "evidence.json")
    exploratory = _json(evidence_root / "exploratory_results.json")
    byte = _json(evidence_root / "matched_byte_results.json")
    keyframe = _json(evidence_root / "matched_keyframe_results.json")
    failure = _json(evidence_root / "failure_report.json")
    performance = _json(evidence_root / "performance_report.json")

    trajectory_count = evidence["included_trajectory_count"]
    assert trajectory_count > 0
    assert evidence["scenario_configuration_count"] == 39 * 25
    assert exploratory["scenario_configuration_count"] == 39 * 25
    assert len(exploratory["configurations"]) == 39
    assert all(
        row["scenario_count"] == 25
        and row["trajectory_count"] == trajectory_count
        and row["byte_ratio"] >= 0.0
        and row["keyframe_ratio"] >= 0.0
        and 0.0 <= row["semantic_micro_f1"] <= 1.0
        for row in exploratory["configurations"]
    )
    assert failure["failure_count"] == evidence["failure_count"] == 0
    assert byte["selection_count"] == keyframe["selection_count"] == 9 * 5
    assert evidence["second_pass_checkpoint_reuse_count"] == 39 * 25
    assert evidence["recomputed_completed_unit_count"] == 0
    assert (
        evidence["checkpoint_output_sha256"]
        == evidence["repeat_checkpoint_output_sha256"]
    )
    assert performance["campaign_total_seconds"] > 0.0
    assert performance["peak_process_rss_bytes"] > 0
    assert performance["generated_disk_bytes"] > 0
    assert performance["worker_count"] == 1
    assert performance["recomputed_completed_unit_count"] == 0

    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in exploratory["configurations"]:
        by_family.setdefault(row["family"], []).append(row)
    for payload, field_name in ((byte, "byte_ratio"), (keyframe, "keyframe_ratio")):
        for selection in payload["selections"]:
            candidates = by_family[selection["family"]]
            target = selection["target"]
            under = [row for row in candidates if row[field_name] <= target]
            expected_budget = (
                max(row[field_name] for row in under)
                if under
                else min(row[field_name] for row in candidates)
            )
            expected_identity = min(
                row["parameter_identity"]
                for row in candidates
                if row[field_name] == expected_budget
            )
            assert selection["actual_budget"] == expected_budget
            assert selection["parameter_identity"] == expected_identity
            assert (
                selection["selected_result"]["parameter_identity"] == expected_identity
            )


def test_sweep_evidence_checksums_and_generated_artifact_hygiene(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/motion_sweep"
    evidence = _json(evidence_root / "evidence.json")
    for name, expected in evidence["evidence_file_sha256"].items():
        assert _sha256(evidence_root / name) == expected
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(path.startswith("cache/phase4_motion_sweep/") for path in tracked)
    assert not any(
        path.startswith("results/phase4/motion_sweep/") and path.endswith(".parquet")
        for path in tracked
    )
    assert not any(path.endswith((".partial", ".tmp")) for path in tracked)
