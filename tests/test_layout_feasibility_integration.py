"""Integration checks for the tracked Batch 5.1 feasibility evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "results/phase5/layout_feasibility"
EXPECTED_FILES = (
    "aggregation_analysis.json",
    "av2_motion_results.json",
    "claim_feasibility_matrix.json",
    "evidence.json",
    "failure_report.json",
    "feasibility_contract.json",
    "post_freeze_map_diagnostics.json",
    "summary.md",
    "synthetic_results.json",
)
EXPECTED_MOTION_SUPPORT_FILES = (
    "artifact_manifest.json",
    "av2_results.json",
    "construction_contract.json",
    "evidence.json",
    "failure_report.json",
    "reproduction.md",
    "resource_measurements.json",
    "summary.md",
    "synthetic_results.json",
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_tracked_evidence_has_exact_required_file_set_and_hashes() -> None:
    assert tuple(sorted(path.name for path in EVIDENCE_ROOT.iterdir())) == (
        EXPECTED_FILES
    )
    evidence = _json("evidence.json")
    assert evidence["evidence_file_sha256"] == {
        name: _sha256(EVIDENCE_ROOT / name)
        for name in EXPECTED_FILES
        if name != "evidence.json"
    }
    assert evidence["starting_head"] == ("db8fdda6db603e063c161b5daa7d6b500e5909fd")


def test_full_development_cohort_and_hidden_map_boundary_are_frozen() -> None:
    contract = _json("feasibility_contract.json")
    motion = _json("av2_motion_results.json")
    evidence = _json("evidence.json")
    assert contract["cohort"] == {
        "role": "development",
        "scenario_count": 150,
        "pilot_access": "prohibited",
        "test_access": "prohibited",
        "replacement_after_execution": "prohibited",
        "exclusions_change_membership": False,
    }
    assert motion["development_scenarios_analyzed"] == 150
    assert motion["pilot_scenario_access_count"] == 0
    assert motion["test_scenario_access_count"] == 0
    assert motion["map_file_open_count"] == 0
    assert evidence["stage_a_completed_before_map_access"] is True
    assert (
        evidence["stage_a_snapshot_before_stage_b"]
        == evidence["stage_a_snapshot_after_stage_b"]
    )
    assert evidence["stage_a_checksums_unchanged_after_stage_b"] is True
    assert evidence["isolated_full_generation_count"] == 2
    assert evidence["deterministic_isolated_regeneration_verified"] is True


def test_synthetic_and_genuine_motion_results_retain_negative_findings() -> None:
    synthetic = _json("synthetic_results.json")
    motion = _json("av2_motion_results.json")
    failures = _json("failure_report.json")
    assert synthetic["case_count"] == 9
    assert synthetic["all_checks_passed"] is True
    aggregate = motion["aggregate"]
    assert aggregate["scenario_count"] == 150
    assert aggregate["layout_eligible_track_count"] == 5940
    assert aggregate["excluded_track_count"] == 1295
    assert aggregate["multi_track_supported_track_count"] == 2694
    assert aggregate["existing_shared_template_track_count"] == 154
    assert aggregate["geometric_crossing_count"] == 901
    assert aggregate["observed_transition_crossing_count"] == 304
    assert aggregate["elevation_separated_crossing_count"] == 0
    assert aggregate["ambiguous_geometric_crossing_count"] == 597
    assert failures["all_zero_count_and_unfavorable_findings_retained"] is True
    assert failures["scenario_replacement_count"] == 0


def test_aggregation_and_post_freeze_diagnostics_are_qualified() -> None:
    aggregation = _json("aggregation_analysis.json")
    diagnostics = _json("post_freeze_map_diagnostics.json")
    assert aggregation["coordinate_frame_identity"] == (
        "ad7defd0f2c0b8697d89cce152c83449923a9d65921334635e42094f2254964d"
    )
    assert aggregation["source_crs_values"] == ["av2_city_map"]
    assert aggregation["coordinates_comparable_across_scenarios"] is False
    assert aggregation["deterministic_grouping_without_maps"] == ("scenario-local only")
    assert aggregation["grouping_crosses_development_pilot_test_boundaries"] is False
    assert diagnostics["stage_a_verified_before_map_access"] is True
    assert diagnostics["diagnostics_used_for_tuning"] is False
    assert diagnostics["feedback_to_stage_a"] is False
    assert diagnostics["development_scenarios_analyzed"] == 150
    assert diagnostics["pilot_scenario_access_count"] == 0
    assert diagnostics["test_scenario_access_count"] == 0


def test_claim_matrix_classifies_complete_layout_claims_as_unsupported() -> None:
    matrix = _json("claim_feasibility_matrix.json")
    assert matrix["claim_count"] == 12
    assert matrix["classification_counts"] == {
        "deferred": 1,
        "feasible now": 2,
        "feasible only as partial motion-supported structure": 4,
        "feasible only with aggregation": 3,
        "unsupported": 2,
    }
    by_category = {row["candidate_category"]: row for row in matrix["claims"]}
    assert (
        by_category["complete local road-network reconstruction"]["classification"]
        == "unsupported"
    )
    assert (
        by_category["complete symbolic spatial grammar"]["classification"]
        == "unsupported"
    )
    assert by_category["corridor width or uncertainty"]["classification"] == (
        "deferred"
    )
    assert matrix["production_layout_induction_implemented"] is False


def test_stage_a_module_has_no_map_adapter_or_map_record_dependency() -> None:
    source = (ROOT / "src/kinematicweave/experiments/layout_feasibility.py").read_text(
        encoding="utf-8"
    )
    assert "kinematicweave.data.av2_map" not in source
    assert "kinematicweave.domain.map_records" not in source
    assert 'raise ValidationError("Stage A cannot receive map inputs")' in source
    script = (ROOT / "scripts/run_layout_feasibility.py").read_text(encoding="utf-8")
    assert script.index("verify_stage_a_bundle(") < script.index(
        "_map_inputs(repository_root, units)"
    )


def test_tracked_evidence_is_anonymous_and_contains_no_layout_objects() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(EVIDENCE_ROOT.iterdir())
    )
    forbidden = (
        "C:\\Users\\",
        "/home/",
        '"source_scenario_id"',
        '"map_object_path"',
        '"motion_object_path"',
    )
    assert all(token not in text for token in forbidden)
    assert (
        re.search(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}\b",
            text,
        )
        is None
    )
    assert '"production_layout_induction_implemented":false' in text
    tracked_phase5 = tuple(
        path for path in _git("ls-files", "results/phase5").splitlines() if path
    )
    expected_tracked = {
        f"results/phase5/layout_feasibility/{name}" for name in EXPECTED_FILES
    }
    expected_tracked.update(
        f"results/phase5/motion_support/{name}"
        for name in EXPECTED_MOTION_SUPPORT_FILES
    )
    assert set(tracked_phase5) <= expected_tracked


def test_phase4_tree_is_unchanged_and_modules_import_safely() -> None:
    phase4_root = ROOT / "results/phase4"
    before = {
        path.relative_to(phase4_root): _sha256(path)
        for path in phase4_root.rglob("*")
        if path.is_file()
    }
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            (
                "import kinematicweave.experiments.layout_feasibility;"
                "import kinematicweave.experiments.layout_map_diagnostics"
            ),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    after = {
        path.relative_to(phase4_root): _sha256(path)
        for path in phase4_root.rglob("*")
        if path.is_file()
    }
    assert after == before
