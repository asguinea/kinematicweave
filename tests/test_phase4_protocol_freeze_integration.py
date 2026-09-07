from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from kinematicweave.canonical import canonical_sha256


@pytest.fixture(scope="module")
def repository_root() -> Path:
    return Path(__file__).parents[1]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_protocol_freeze_has_exact_development_identity_and_no_withheld_access(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results" / "phase4" / "protocol_freeze"
    evidence = _json(evidence_root / "evidence.json")
    audit = _json(evidence_root / "development_audit.json")

    assert evidence["protocol_decision"] == "frozen"
    assert evidence["cohort_identity"] == (
        "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
    )
    assert evidence["development_validation_identity"] == (
        "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
    )
    assert evidence["development_scenario_count"] == 150
    assert audit["development_scenario_count"] == 150
    assert len(audit["development_scenario_ids"]) == 150
    assert len(set(audit["development_scenario_ids"])) == 150
    assert audit["development_scenario_identity"] == canonical_sha256(
        "phase4-protocol-development-scenarios-v1",
        audit["development_scenario_ids"],
    )
    assert audit["batch4_3_configuration_count"] == 18
    assert audit["batch4_4_parameter_point_count"] == 39
    assert evidence["decisions_used_development_data_only"] is True
    assert evidence["pilot_or_test_metadata_or_outcomes_accessed"] is False
    assert evidence["final_method_selected"] is False
    assert evidence["statistical_testing_performed"] is False


def test_protocol_freeze_budgets_matrix_and_checkpoint_reuse(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results" / "phase4" / "protocol_freeze"
    evidence = _json(evidence_root / "evidence.json")
    supplemental = _json(evidence_root / "supplemental_grid.json")
    budgets = _json(evidence_root / "final_budget_contract.json")
    matrix = _json(evidence_root / "final_campaign_matrix.json")
    resource = _json(evidence_root / "resource_projection.json")

    assert supplemental["points"] == []
    assert supplemental["outcome_values_used"] is False
    assert evidence["supplemental_point_count"] == 0
    assert [
        (row["dimension"], row["target"], row["priority"]) for row in budgets["targets"]
    ] == [
        ("byte_ratio", 0.48, "primary"),
        ("keyframe_ratio", 0.12, "primary"),
        ("byte_ratio", 0.71, "diagnostic"),
        ("keyframe_ratio", 0.23, "diagnostic"),
    ]
    assert budgets["outcome_fields_used"] == []
    assert budgets["metric_interpolation"] is False
    assert matrix["matrix_identity"] == evidence["final_matrix_identity"]
    assert len(matrix["methods"]) == 18
    assert len(set(matrix["configuration_ids"])) == 18
    families = {row["family"] for row in matrix["methods"]}
    assert {
        "raw_samples",
        "exact_adjacent",
        "uniform_linear",
        "uniform_hermite",
        "fixed_interval_linear",
        "rdp_linear",
        "position_bounded_linear",
        "unconstrained_hermite",
        "position_velocity_hybrid",
    } <= families
    assert matrix["frozen_before_pilot_execution"] is True
    assert matrix["test_matrix_changes_after_pilot_execution_permitted"] is False
    assert evidence["verified_checkpoint_reuse_count_per_pass"] == 2700
    assert evidence["representation_recomputation_count"] == 0
    assert (
        evidence["checkpoint_output_sha256"]
        == evidence["repeat_checkpoint_output_sha256"]
    )
    assert evidence["failure_count"] == 0
    assert resource["feasibility"]["overall"] is True
    assert set(resource["measured_inputs"]) == {"batch4_2", "batch4_3", "batch4_4"}
    assert resource["pilot"]["checkpoint_count"] == 900
    assert resource["test"]["checkpoint_count"] == 5400


def test_protocol_freeze_evidence_checksums_and_git_hygiene(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results" / "phase4" / "protocol_freeze"
    evidence = _json(evidence_root / "evidence.json")
    for name, expected in evidence["evidence_file_sha256"].items():
        assert _sha256(evidence_root / name) == expected
    assert evidence["provider_or_generated_data_tracked"] is False
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(path.startswith("cache/phase4_protocol_freeze/") for path in tracked)
    assert not any(path.endswith(".parquet") for path in tracked)
    assert not any(path.endswith(".partial") for path in tracked)
