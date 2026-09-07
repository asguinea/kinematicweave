"""Integration checks for committed Batch 4.6 frozen campaign evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

import pytest

from kinematicweave.experiments.frozen_campaign import (
    FROZEN_COHORT_IDENTITY,
    FROZEN_MATRIX_IDENTITY,
    load_pilot_completion,
)
from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    motion_cohort_manifest_from_json,
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


def test_frozen_campaign_exact_counts_membership_and_protocol(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/frozen_campaign"
    evidence = _json(evidence_root / "evidence.json")
    pilot = _json(evidence_root / "pilot_results.json")
    test = _json(evidence_root / "test_results.json")
    contract = _json(evidence_root / "campaign_contract.json")
    matrix = _json(
        repository_root / "results/phase4/protocol_freeze/final_campaign_matrix.json"
    )
    cohort = motion_cohort_manifest_from_json(
        (
            repository_root / "results/phase4/motion_cohort/cohort_manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["campaign_decision"] == "completed"
    assert evidence["cohort_identity"] == FROZEN_COHORT_IDENTITY
    assert evidence["matrix_identity"] == FROZEN_MATRIX_IDENTITY
    assert evidence["pilot_scenario_count"] == 50
    assert evidence["test_scenario_count"] == 300
    assert evidence["pilot_scenario_configuration_count"] == 900
    assert evidence["test_scenario_configuration_count"] == 5400
    assert evidence["total_scenario_configuration_count"] == 6300
    assert evidence["configuration_count"] == 18
    assert evidence["failure_count"] == 0
    assert evidence["protocol_frozen_before_pilot_and_test"] is True
    assert evidence["pilot_caused_scientific_protocol_change"] is False
    assert evidence["test_outcomes_used_to_alter_campaign"] is False
    assert evidence["results_descriptive_until_batch4_7"] is True
    assert evidence["final_method_selected"] is False
    assert contract["configurations"] == matrix["methods"]

    development_ids = {
        unit.source_scenario_id
        for unit in cohort.units
        if cast(CohortRole, unit.cohort_role) is CohortRole.DEVELOPMENT
    }
    pilot_ids = [
        unit.source_scenario_id
        for unit in cohort.units
        if cast(CohortRole, unit.cohort_role) is CohortRole.PILOT
    ]
    test_ids = [
        unit.source_scenario_id
        for unit in cohort.units
        if cast(CohortRole, unit.cohort_role) is CohortRole.TEST
    ]
    assert pilot["scenario_ids"] == pilot_ids
    assert test["scenario_ids"] == test_ids
    assert len(pilot_ids) == len(set(pilot_ids)) == 50
    assert len(test_ids) == len(set(test_ids)) == 300
    assert not development_ids.intersection(pilot_ids)
    assert not development_ids.intersection(test_ids)
    assert not set(pilot_ids).intersection(test_ids)
    assert all(
        len(source_id) == 36 and source_id.count("-") == 4 for source_id in pilot_ids
    )
    assert all(
        len(source_id) == 36 and source_id.count("-") == 4 for source_id in test_ids
    )


def test_frozen_campaign_record_accounting_invariants_and_determinism(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/frozen_campaign"
    pilot = _json(evidence_root / "pilot_results.json")
    test = _json(evidence_root / "test_results.json")
    determinism = _json(evidence_root / "determinism_report.json")
    failures = _json(evidence_root / "failure_report.json")
    contract = _json(evidence_root / "campaign_contract.json")

    assert pilot["trajectory_count"] == 2278
    assert pilot["source_sample_count"] == 126392
    assert pilot["trajectory_configuration_count"] == 41004
    assert pilot["trajectory_event_record_count"] == 246024
    assert test["trajectory_count"] == 13689
    assert test["source_sample_count"] == 765237
    assert test["trajectory_configuration_count"] == 246402
    assert test["trajectory_event_record_count"] == 1478412
    frozen_configuration_ids = {
        row["configuration_id"] for row in contract["configurations"]
    }
    assert pilot["configuration_ids"] == test["configuration_ids"]
    assert len(set(pilot["configuration_ids"])) == 18
    assert {
        row["parameter_identity"] for row in pilot["configurations"]
    } == frozen_configuration_ids
    assert {
        row["parameter_identity"] for row in test["configurations"]
    } == frozen_configuration_ids
    for result in (pilot, test):
        invariants = result["invariants"]
        assert invariants["raw_zero_source_timestamp_error"] is True
        assert invariants["raw_semantic_preservation_perfect"] is True
        assert invariants["exact_adjacent_zero_source_timestamp_error"] is True
        assert invariants["all_gap_contracts_preserved"] is True
        assert invariants["all_replay_values_and_metrics_finite"] is True
        assert invariants["bounded_constraint_violation_count"] == 0
        assert (
            result["checkpoint_output_identity"]
            == result["repeat_checkpoint_output_identity"]
        )
        assert (
            result["source_cache_snapshot_before"]
            == result["source_cache_snapshot_after"]
        )
        assert result["source_cache_unchanged"] is True
        assert (
            result["verification_pass_reuse_count"]
            == result["scenario_configuration_count"]
        )
    assert determinism["total_verified_checkpoint_count"] == 6300
    assert determinism["representation_recomputation_count"] == 0
    assert determinism["determinism_mismatch_count"] == 0
    assert failures == {
        "batch": "4.6",
        "schema_version": "1.0",
        "failure_count": 0,
        "undocumented_failure_count": 0,
        "failures": [],
        "omitted_failures": False,
    }


def test_frozen_campaign_budget_outputs_and_artifacts_verify(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/frozen_campaign"
    byte_results = _json(evidence_root / "matched_byte_results.json")
    keyframe_results = _json(evidence_root / "matched_keyframe_results.json")
    pilot = _json(evidence_root / "pilot_results.json")
    test = _json(evidence_root / "test_results.json")

    assert byte_results["selection_count"] == 20
    assert keyframe_results["selection_count"] == 20
    for row in (*byte_results["selections"], *keyframe_results["selections"]):
        assert row["cohort_role"] in {"pilot", "test"}
        assert row["selection_input"] == "relevant_achieved_budget_only"
        assert row["metric_interpolation"] is False
        assert row["relation"] in {"below", "equal", "above"}
        assert row["absolute_mismatch"] <= row["maximum_permitted_mismatch"]

    expected_rows = {
        "pilot": [41004, 246024, 900, 18, 0, 20],
        "test": [246402, 1478412, 5400, 18, 0, 20],
    }
    for role, result in (("pilot", pilot), ("test", test)):
        descriptors = result["raw_result_artifacts"]
        assert [row["row_count"] for row in descriptors] == expected_rows[role]
        if descriptors and not (repository_root / descriptors[0]["path"]).is_file():
            pytest.skip("raw campaign tables are intentionally excluded from Git")
        for descriptor in descriptors:
            relative_path = Path(descriptor["path"])
            assert not relative_path.is_absolute()
            path = repository_root / relative_path
            assert path.is_file() and not path.is_symlink()
            assert path.stat().st_size == descriptor["size_bytes"]
            assert _sha256(path) == descriptor["sha256"]


def test_frozen_campaign_evidence_checksums_gate_and_git_hygiene(
    repository_root: Path,
) -> None:
    evidence_root = repository_root / "results/phase4/frozen_campaign"
    evidence = _json(evidence_root / "evidence.json")
    for name, expected in evidence["evidence_file_sha256"].items():
        assert _sha256(evidence_root / name) == expected
    completion = load_pilot_completion(evidence_root / "pilot_completion.json")
    assert completion.passed is True
    assert completion.matrix_identity == FROZEN_MATRIX_IDENTITY
    assert evidence["provider_or_generated_data_tracked"] is False
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(path.startswith("cache/phase4_frozen_campaign/") for path in tracked)
    assert not any(path.endswith(".parquet") for path in tracked)
    assert not any(path.endswith(".partial") for path in tracked)
    assert not any(
        path.startswith("data/") and not path.endswith(".gitkeep") for path in tracked
    )
