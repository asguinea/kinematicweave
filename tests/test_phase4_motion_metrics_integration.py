"""Committed-evidence integration checks for Phase 4 Batch 4.3."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    motion_cohort_manifest_from_json,
)

_COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
_VALIDATION_IDENTITY = (
    "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
)
_TRAJECTORY_IDENTITY = (
    "21df77d7e9bc3eed61c5e805e491675b8ca7d5fcdfbb41c300c024858717408c"
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


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {item for nested in value.values() for item in _strings(nested)}
    if isinstance(value, list):
        return {item for nested in value for item in _strings(nested)}
    return set()


def test_motion_metric_evidence_has_exact_frozen_membership_and_methods(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/motion_metrics"
    evidence = _json(evidence_root / "evidence.json")
    contract = _json(evidence_root / "metrics_contract.json")
    cohort = motion_cohort_manifest_from_json(
        (
            repository_root / "results/phase4/motion_cohort/cohort_manifest.json"
        ).read_text(encoding="utf-8")
    )
    development = [
        unit.source_scenario_id
        for unit in cohort.units
        if unit.cohort_role is CohortRole.DEVELOPMENT
    ]
    withheld = {
        unit.source_scenario_id
        for unit in cohort.units
        if unit.cohort_role in {CohortRole.PILOT, CohortRole.TEST}
    }

    assert evidence["batch_decision"] == "achieved"
    assert evidence["cohort_identity"] == _COHORT_IDENTITY
    assert evidence["development_validation_identity"] == _VALIDATION_IDENTITY
    assert evidence["included_trajectory_identity"] == _TRAJECTORY_IDENTITY
    assert evidence["development_scenario_count"] == len(development) == 150
    assert evidence["included_trajectory_count"] == 7012
    assert evidence["method_configuration_count"] == 18
    assert contract["development_scenario_ids"] == development
    assert len(contract["method_configurations"]) == 18
    assert (
        len({row["configuration_id"] for row in contract["method_configurations"]})
        == 18
    )
    assert not (_strings(contract) & withheld)
    assert evidence["pilot_or_test_outcomes_accessed"] is False
    assert evidence["official_source"] == (
        "s3://argoverse/datasets/av2/motion-forecasting/"
    )


def test_motion_metric_records_invariants_audit_and_resources(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/motion_metrics"
    evidence = _json(evidence_root / "evidence.json")
    motion = _json(evidence_root / "development_metric_summary.json")
    events = _json(evidence_root / "event_preservation_summary.json")
    audit = _json(evidence_root / "audit_report.json")
    performance = _json(evidence_root / "performance_report.json")

    assert evidence["trajectory_motion_record_count"] == 18 * 7012
    assert evidence["trajectory_event_record_count"] == 18 * 7012 * 6
    assert evidence["scenario_method_record_count"] == 18 * 150
    assert evidence["failure_count"] == 0
    assert evidence["raw_zero_error"] is True
    assert evidence["exact_zero_error"] is True
    assert evidence["raw_event_preservation_perfect"] is True
    assert evidence["determinism_repeat_count"] == 18 * 150
    assert evidence["determinism_mismatch_count"] == 0
    assert motion["raw_zero_error"] is True
    assert motion["exact_zero_error"] is True
    assert events["raw_event_preservation_perfect"] is True
    assert len(events["event_types"]) == 6
    assert audit["all_exact_fields_match"] is True
    assert audit["all_floating_fields_match"] is True
    assert len(audit["methods"]) == 18
    assert performance["hardware"]["host_model"] == "ASUS reference laptop"
    for row in performance["methods"]:
        runtime = row["runtime"]
        assert runtime["total_seconds"] >= 0.0
        assert runtime["peak_process_rss_bytes"] > 0
        assert runtime["output_disk_bytes"] > 0


def test_motion_metric_evidence_checksums_and_git_hygiene(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/motion_metrics"
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
    assert not any(path.startswith("cache/phase4_motion_metrics/") for path in tracked)
    assert not any(
        path.startswith("results/phase4/motion_metrics/") and path.endswith(".parquet")
        for path in tracked
    )
    assert not any(path.endswith(".partial") for path in tracked)
