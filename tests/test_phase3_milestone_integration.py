"""Committed integration checks for the Batch 3.7 milestone evidence."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

from kinematicweave.data.schemas import canonical_schema_names, schema_fingerprint

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "results/phase3/milestone"
_PASS_STATEMENT = (
    "M3 procedural motion tape achieved on synthetic and genuine AV2 provider data."
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((_EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_milestone_evidence_hashes_contract_and_decision_are_complete() -> None:
    evidence = _json("evidence.json")
    names = (
        "contract_snapshot.json",
        "synthetic_integration.json",
        "av2_provider_integration.json",
        "phase3_results.json",
        "summary.md",
    )
    assert evidence["component_sha256"] == {
        name: _sha256(_EVIDENCE_ROOT / name) for name in names
    }
    assert evidence["milestone_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS_STATEMENT
    assert evidence["synthetic_gate"] == "PASS"
    assert evidence["genuine_av2_provider_gate"] == "PASS"
    assert evidence["environment"]["wsl_used"] is True
    assert len(evidence["prior_phase3_evidence"]) == 6
    assert all(
        item["decision"] == "achieved"
        for item in evidence["prior_phase3_evidence"].values()
    )
    summary = (_EVIDENCE_ROOT / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith("# Phase 3 Milestone — Procedural Motion Tape\n")
    assert _PASS_STATEMENT in summary


def test_exact_thirteen_schema_registry_is_frozen() -> None:
    snapshot = _json("contract_snapshot.json")
    expected_names = [name.value for name in canonical_schema_names()]
    expected_fingerprints = {
        name.value: schema_fingerprint(name) for name in canonical_schema_names()
    }
    assert snapshot["schema_count"] == 13
    assert snapshot["schema_names"] == expected_names
    assert snapshot["schema_fingerprints"] == expected_fingerprints
    assert snapshot["package_manifest_is_derived_not_canonical"] is True
    assert all(item["empty_table_verified"] for item in snapshot["schemas"])
    assert all(item["bounded_reader_verified"] for item in snapshot["schemas"])
    assert all(item["artifact_verification_supported"] for item in snapshot["schemas"])
    assert all(not item["lookup_filesystem_output"] for item in snapshot["schemas"])


def test_complete_synthetic_package_gate_passes() -> None:
    evidence = _json("synthetic_integration.json")
    assert evidence["status"] == "PASS"
    assert evidence["source_sample_count"] == 293
    assert evidence["valid_sample_count"] == 291
    assert evidence["valid_run_count"] == 28
    assert evidence["counts"] == {
        "scenarios": 16,
        "procedural_tracks": 27,
        "procedural_segments": 39,
        "semantic_waypoints": 156,
        "motion_events": 17,
        "motion_categories": 6,
        "route_templates": 28,
        "route_template_memberships": 28,
    }
    assert all(evidence["synthetic_oracles"].values())
    assert evidence["query_verification"]["recorded_source_gap_count"] == 1
    assert evidence["equivalent_isolated_package_identity_match"] is True
    assert evidence["equivalent_isolated_package_json_match"] is True
    assert evidence["equivalent_isolated_bundle_checksums_match"] is True


def test_genuine_av2_package_gate_queries_and_storage_pass() -> None:
    evidence = _json("av2_provider_integration.json")
    assert evidence["status"] == "PASS"
    assert evidence["provider_contract"]["genuine_provider_evidence"] is True
    assert evidence["provider_contract"]["trajectory_count"] == 418
    assert len(evidence["provider_contract"]["selected_scenario_ids"]) == 10
    assert evidence["counts"] == {
        "scenarios": 10,
        "procedural_tracks": 418,
        "procedural_segments": 2_327,
        "semantic_waypoints": 3_583,
        "motion_events": 668,
        "motion_categories": 86,
        "route_templates": 414,
        "route_template_memberships": 418,
    }
    assert evidence["shared_template_count"] == 4
    assert evidence["input_integrity"]["source_and_cache_unchanged"] is True
    assert evidence["map_data_used_for_construction"] is False
    assert evidence["map_evaluation_fields_are_evaluation_only"] is True
    assert evidence["query_verification"]["recorded_source_gap_count"] == 0
    assert evidence["query_verification"]["outside_support_verified"] is True
    assert evidence["query_verification"]["shared_template_query_verified"] is True
    assert evidence["query_verification"]["singleton_template_query_verified"] is True
    assert set(evidence["query_verification"]["event_types_exercised"]) >= {
        "stop",
        "left_turn",
        "right_turn",
        "acceleration",
        "braking",
    }
    assert (
        "cubic_hermite" in evidence["query_verification"]["primitive_types_exercised"]
    )
    assert evidence["accepted_storage"]["combined_representation_bytes"] == 1_828_727
    assert evidence["accepted_storage"]["canonical_source_bytes"] == 6_946_791
    assert evidence["equivalent_isolated_package_identity_match"] is True
    assert evidence["equivalent_isolated_package_json_match"] is True
    assert evidence["equivalent_isolated_bundle_checksums_match"] is True


def test_consolidated_phase3_snapshot_matches_accepted_results() -> None:
    result = _json("phase3_results.json")
    assert result["exact"] == {
        "segments": 22_550,
        "bytes": 2_819_141,
        "maximum_endpoint_error_m": 0.0,
    }
    assert result["position_bounded_linear"]["segments"] == 2_488
    assert result["position_bounded_linear"]["bytes"] == 570_662
    assert result["position_bounded_linear"]["maximum_position_error_m"] < 0.1
    assert result["unconstrained_hermite"]["segments"] == 2_096
    assert result["unconstrained_hermite"]["bytes"] == 526_573
    assert result["unconstrained_hermite"]["velocity_degradation_measured"] is True
    assert result["position_velocity_bounded"]["segments"] == 2_327
    assert result["position_velocity_bounded"]["bytes"] == 553_731
    assert result["position_velocity_bounded"]["maximum_position_error_m"] == (
        0.09989488370623803
    )
    assert result["position_velocity_bounded"]["maximum_velocity_error_mps"] == (
        0.9995655552801491
    )
    assert result["semantic"]["waypoints"] == 3_583
    assert result["semantic"]["source_events"] == 668
    assert result["semantic"]["aggregate_f1"] == 0.8892215568862275
    assert result["semantic"]["bytes"] == 678_216
    assert result["shared"]["categories"] == 86
    assert result["shared"]["templates"] == 414
    assert result["shared"]["shared_templates"] == 4
    assert result["shared"]["bytes"] == 596_780
    assert result["combined"]["bytes"] == 1_828_727


def test_review_runbook_readme_and_git_hygiene_are_complete() -> None:
    review = (_ROOT / "docs/phase3_milestone_review.md").read_text(encoding="utf-8")
    runbook = (_ROOT / "docs/phase3_exact_codec_runbook.md").read_text(encoding="utf-8")
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert review.startswith("# Phase 3 Milestone Review — Procedural Motion Tape\n")
    for section in range(1, 17):
        assert f"## {section}." in review
    for required in (
        "scripts/run_phase3_milestone.py",
        "procedural_motion_package.json",
        "phase3_contract_snapshot.json",
        "phase3_summary.md",
        "load_procedural_motion_package",
        "reader.replay",
        "reader.is_source_gap",
        "results/generated/phase3/milestone",
        "results/phase3/milestone/",
    ):
        assert required in runbook
    assert "docs/phase3_milestone_review.md" in readme
    assert "results/phase3/milestone/summary.md" in readme

    completed = subprocess.run(
        ["git", "-c", f"safe.directory={_ROOT.as_posix()}", "ls-files"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = tuple(line for line in completed.stdout.splitlines() if line)
    assert not any(
        path.startswith(("cache/", "data/external/", "results/generated/"))
        for path in tracked
    )
    assert not any(path.endswith((".partial", ".parquet")) for path in tracked)
