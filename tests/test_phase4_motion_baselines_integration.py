"""Committed-evidence checks for Phase 4 comparable motion baselines."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

from kinematicweave.baselines.motion import required_baseline_grid
from kinematicweave.data.av2_acquisition import AV2_OFFICIAL_MOTION_ROOT
from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    motion_cohort_manifest_from_json,
)

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE = _ROOT / "results/phase4/motion_baselines"
_COHORT = _ROOT / "results/phase4/motion_cohort"
_COHORT_IDENTITY = "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
_PASS = (
    "Phase 4 comparable motion baselines completed on all 150 genuine AV2 "
    "development scenarios."
)
_ACCEPTED_KEYS = {
    "exact_adjacent_sample",
    "position_bounded_linear-error-0p1",
    "unconstrained_hermite-error-0p1",
    "position_velocity_bounded_hybrid-error-0p1-velocity-1p0",
}


def _json(name: str) -> dict[str, Any]:
    value = json.loads((_EVIDENCE / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_baseline_contract_is_exactly_development_only() -> None:
    contract = _json("baseline_contract.json")
    manifest = motion_cohort_manifest_from_json(
        (_COHORT / "cohort_manifest.json").read_text(encoding="utf-8")
    )
    development = {
        unit.source_scenario_id
        for unit in manifest.units
        if unit.cohort_role is CohortRole.DEVELOPMENT
    }
    withheld = {
        unit.source_scenario_id
        for unit in manifest.units
        if unit.cohort_role in {CohortRole.PILOT, CohortRole.TEST}
    }
    scenario_ids = contract["development_scenario_ids"]

    assert contract["cohort_identity"] == _COHORT_IDENTITY
    assert contract["official_source"] == AV2_OFFICIAL_MOTION_ROOT
    assert contract["cohort_role"] == "development"
    assert contract["scenario_count"] == 150
    assert len(scenario_ids) == len(set(scenario_ids)) == 150
    assert set(scenario_ids) == development
    assert set(scenario_ids).isdisjoint(withheld)
    assert contract["included_trajectory_count"] > 0
    assert len(contract["included_trajectory_identity"]) == 64
    assert contract["pilot_or_test_outcomes_accessed"] is False
    assert contract["motion_encoding_uses_map_data"] is False


def test_every_required_method_completed_every_included_trajectory() -> None:
    contract = _json("baseline_contract.json")
    results = _json("development_results.json")
    expected_keys = {config.key for config in required_baseline_grid()} | _ACCEPTED_KEYS
    rows = results["methods"]

    assert len(rows) == len(expected_keys) == 18
    assert {row["method_key"] for row in rows} == expected_keys
    assert {row["method_key"] for row in contract["methods"]} == expected_keys
    assert results["included_trajectory_count"] == contract["included_trajectory_count"]
    for row in rows:
        assert row["scenarios_attempted"] == row["scenarios_completed"] == 150
        assert (
            row["trajectories_attempted"]
            == row["trajectories_completed"]
            == contract["included_trajectory_count"]
        )
        assert row["counts"]["source_samples"] >= row["counts"]["valid_samples"] > 0
        assert row["raw_canonical_bytes"] > 0
        assert row["encoded_artifact_bytes"] > 0
        assert row["compression_ratio"] > 0.0
        assert row["errors"]["exact_endpoint_error_m"] == 0.0
        assert row["errors"]["gap_preservation_failures"] == 0
        assert row["source_timestamp_replay_count"] == row["counts"]["valid_samples"]
        assert row["deterministic_checksum_agreement"] is True
        assert len(row["scenario_artifact_checksums"]) == 150
        assert all(checks["checksums"] for checks in row["scenario_artifact_checksums"])
        assert len(row["scenario_artifact_identity"]) == 64
    raw = next(row for row in rows if row["method_key"] == "raw_samples")
    assert raw["raw_source_timestamp_reconstruction_exact"] is True


def test_errors_resources_and_failure_report_are_complete() -> None:
    results = _json("development_results.json")
    failures = _json("failure_report.json")
    performance = _json("performance_report.json")

    assert failures["failure_count"] == 0
    assert failures["failures"] == []
    assert failures["omitted_failures"] is False
    assert performance["hardware"]["wsl2"] is True
    assert performance["hardware"]["gpu_used"] is False
    for row in results["methods"]:
        for metric in ("position_m", "heading_rad", "velocity_vector_mps"):
            values = row["errors"][metric]
            assert values["count"] > 0
            assert 0.0 <= values["mean"] <= values["maximum"]
            assert 0.0 <= values["median"] <= values["maximum"]
            assert 0.0 <= values["p95"] <= values["maximum"]
        resources = row["resources"]
        for name in (
            "encode_seconds",
            "replay_seconds",
            "artifact_seconds",
            "total_seconds",
            "trajectories_per_second",
        ):
            assert resources[name] >= 0.0
        assert resources["total_seconds"] > 0.0
        assert resources["peak_process_memory_bytes"] > 0
        assert resources["generated_disk_bytes"] > 0


def test_evidence_checksums_decision_and_git_hygiene_are_complete() -> None:
    evidence = _json("evidence.json")
    checksums = evidence["evidence_file_sha256"]

    assert evidence["batch_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS
    assert evidence["cohort_identity"] == _COHORT_IDENTITY
    assert evidence["development_scenario_count"] == 150
    assert evidence["method_configuration_count"] == 18
    assert evidence["failure_count"] == 0
    assert evidence["all_equivalent_artifact_checksums_agree"] is True
    assert evidence["provider_or_generated_data_tracked"] is False
    assert evidence["pilot_or_test_outcomes_accessed"] is False
    assert checksums == {name: _sha256(_EVIDENCE / name) for name in sorted(checksums)}

    tracked = subprocess.run(
        ["git", "-c", f"safe.directory={_ROOT.as_posix()}", "ls-files"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(
        item.startswith(("data/external/", "cache/", "results/generated/"))
        for item in tracked
    )
    assert not any(item.endswith((".parquet", ".partial")) for item in tracked)
