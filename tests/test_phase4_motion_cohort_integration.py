"""Committed-evidence checks for the frozen Phase 4 AV2 motion cohort."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

from kinematicweave.data.av2_acquisition import AV2_OFFICIAL_MOTION_ROOT
from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    motion_cohort_manifest_from_json,
)

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE = _ROOT / "results/phase4/motion_cohort"
_PASS = (
    "Phase 4 AV2 motion evaluation cohort frozen with 500 genuine provider scenarios."
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((_EVIDENCE / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_cohort_manifest_is_complete_and_provider_backed() -> None:
    manifest = motion_cohort_manifest_from_json(
        (_EVIDENCE / "cohort_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.official_source == AV2_OFFICIAL_MOTION_ROOT
    assert len(manifest.units) == 500
    assert (
        sum(
            CohortRole(item.cohort_role).value == "development"
            for item in manifest.units
        )
        == 150
    )
    assert (
        sum(CohortRole(item.cohort_role).value == "pilot" for item in manifest.units)
        == 50
    )
    assert (
        sum(CohortRole(item.cohort_role).value == "test" for item in manifest.units)
        == 300
    )
    assert len({item.source_scenario_id for item in manifest.units}) == 500
    assert all(item.motion_sha256 and item.map_sha256 for item in manifest.units)
    assert all(item.materialization_cache_key for item in manifest.units)
    assert all(
        item.validation_included or item.exclusion_reason is not None
        for item in manifest.units
    )


def test_acquisition_materialization_reuse_and_validation_are_complete() -> None:
    acquisition = _json("acquisition_report.json")
    materialization = _json("materialization_report.json")
    reuse = _json("reuse_report.json")
    validation = _json("validation_report.json")
    assert acquisition["official_source"] == AV2_OFFICIAL_MOTION_ROOT
    assert acquisition["selected_scenario_count"] == 500
    assert acquisition["selected_file_count"] == 1_000
    assert acquisition["source_pairs_verified"] == 500
    assert acquisition["outcome_based_replacements"] == 0
    assert materialization["declared_output_count"] == 3_500
    assert materialization["complete_cache_entry_count"] == 500
    assert materialization["incomplete_cache_entry_count"] == 0
    assert reuse["reused_entry_count"] == 500
    assert reuse["conversion_worker_run_count"] == 0
    assert reuse["canonical_output_checksum_changes"] == 0
    assert validation["complete_validation"] is True
    assert validation["membership_changed_by_validation"] is False
    overall = validation["summaries"][-1]
    assert overall["source_scenarios"] == 500
    assert overall["source_trajectories"] > 0
    assert overall["source_samples"] > 0
    assert overall["vector_map_elements"] > 0


def test_resource_measurements_and_cohort_decision_are_real() -> None:
    resource = _json("resource_pilot.json")
    evidence = _json("evidence.json")
    measurements = resource["measurements"]
    assert len(resource["selected_source_scenario_ids"]) == 25
    assert (
        resource["development_count"],
        resource["pilot_count"],
        resource["test_count"],
    ) == (10, 5, 10)
    for name in (
        "acquisition_verification_seconds",
        "conversion_seconds",
        "validation_seconds",
        "total_seconds",
        "peak_process_memory_bytes",
        "source_bytes",
        "cache_bytes",
        "disk_free_before_bytes",
        "disk_free_after_bytes",
    ):
        assert measurements[name] > 0
    assert evidence["cohort_decision"] == "frozen"
    assert evidence["pass_statement"] == _PASS
    assert evidence["hardware"]["wsl2"] is True
    assert "ASUS" in evidence["hardware"]["manufacturer"].upper()
    assert evidence["provider_or_cache_data_tracked"] is False


def test_evidence_checksums_and_git_hygiene_are_complete() -> None:
    evidence = _json("evidence.json")
    checksums = evidence["evidence_file_sha256"]
    assert len(checksums) == 10
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
