"""Integration checks for committed Batch 4.8 attribution evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from kinematicweave.canonical import canonical_json_text
from kinematicweave.experiments.ablation_analysis import CONFIGURATION_IDS
from kinematicweave.experiments.representation_ablation_campaign import (
    PASS_STATEMENT,
    STARTING_HEAD,
)
from kinematicweave.experiments.statistical_campaign import (
    FROZEN_BUDGET_CONTRACT_SHA256,
    FROZEN_COHORT_IDENTITY,
    FROZEN_MATRIX_IDENTITY,
    FROZEN_METRIC_IDENTITY,
    FROZEN_VALIDATION_IDENTITY,
)

_REQUIRED_FILES = {
    "ablation_contract.json",
    "interpolation_ablation.json",
    "adaptive_segmentation_ablation.json",
    "temporal_vs_geometric_ablation.json",
    "primitive_vocabulary_ablation.json",
    "velocity_constraint_ablation.json",
    "bounded_method_tradeoff.json",
    "serialization_overhead.json",
    "contribution_attribution.json",
    "robustness_analysis.json",
    "failure_report.json",
    "evidence.json",
    "summary.md",
}
_INFERENTIAL_FILES = {
    "interpolation_ablation.json": 150,
    "adaptive_segmentation_ablation.json": 300,
    "temporal_vs_geometric_ablation.json": 50,
    "primitive_vocabulary_ablation.json": 50,
    "velocity_constraint_ablation.json": 50,
    "bounded_method_tradeoff.json": 50,
}


@pytest.fixture(scope="module")
def evidence_root() -> Path:
    return Path(__file__).parents[1] / "results/phase4/ablation_analysis"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ablation_evidence_gate_and_frozen_contract(evidence_root: Path) -> None:
    evidence = _json(evidence_root / "evidence.json")
    contract = _json(evidence_root / "ablation_contract.json")
    assert {path.name for path in evidence_root.iterdir()} == _REQUIRED_FILES
    assert evidence["ablation_decision"] == "completed"
    assert evidence["pass_statement"] == PASS_STATEMENT
    assert evidence["starting_head"] == STARTING_HEAD
    assert evidence["cohort_identity"] == FROZEN_COHORT_IDENTITY
    assert evidence["matrix_identity"] == FROZEN_MATRIX_IDENTITY
    assert evidence["metric_identity"] == FROZEN_METRIC_IDENTITY
    assert evidence["validation_identity"] == FROZEN_VALIDATION_IDENTITY
    assert evidence["budget_contract_sha256"] == FROZEN_BUDGET_CONTRACT_SHA256
    assert evidence["pilot_scenario_count"] == 50
    assert evidence["test_scenario_count"] == 300
    assert evidence["inferential_unit"] == "scenario"
    assert evidence["contrast_family_count"] == 7
    assert evidence["contrast_record_count"] == 650
    assert evidence["analysis_failure_count"] == 0
    assert evidence["campaign_failure_count"] == 0
    assert evidence["codec_execution_performed"] is False
    assert evidence["metric_rematching_performed"] is False
    assert evidence["new_configuration_created"] is False
    assert evidence["pilot_and_test_pooled"] is False
    assert evidence["universal_winner_declared"] is False
    assert evidence["determinism_verified"] is True
    assert evidence["analysis_identity"] == evidence["repeat_analysis_identity"]
    assert {
        row["name"]: row["parameter_identity"]
        for row in contract["frozen_configurations"]
    } == CONFIGURATION_IDS
    assert contract["codec_execution_performed"] is False
    assert contract["budget_rematching_performed"] is False
    assert contract["statistical_procedures_changed"] is False
    assert contract["composite_score_created"] is False


@pytest.mark.parametrize(
    ("name", "expected_count"),
    list(_INFERENTIAL_FILES.items()),
)
def test_every_ablation_record_has_paired_statistics(
    evidence_root: Path,
    name: str,
    expected_count: int,
) -> None:
    payload = _json(evidence_root / name)
    records = payload["records"]
    assert payload["contrast_record_count"] == len(records) == expected_count
    assert {row["cohort_role"] for row in records} == {"pilot", "test"}
    for record in records:
        assert record["planned_scenario_count"] in {50, 300}
        assert 0 < record["valid_pair_count"] <= record["planned_scenario_count"]
        assert 0.0 <= record["p_value_raw"] <= 1.0
        assert 0.0 <= record["adjusted_p_value"] <= 1.0
        assert record["adjusted_p_value"] + 1e-15 >= record["p_value_raw"]
        assert (
            record["confidence_interval_lower"] <= record["confidence_interval_upper"]
        )
        assert record["standardized_paired_effect_dz"] is None or isinstance(
            record["standardized_paired_effect_dz"], float
        )
        assert record["rank_biserial_correlation"] is None or isinstance(
            record["rank_biserial_correlation"], float
        )
        assert (
            record["method_b_wins"] + record["ties"] + record["method_b_losses"]
            == record["valid_pair_count"]
        )
        for dimension in (
            "achieved_byte_ratio",
            "achieved_keyframe_ratio",
            "achieved_segment_ratio",
        ):
            assert record[dimension]["absolute_mismatch"] >= 0.0


def test_identical_keyframes_and_budget_mismatches_are_preserved(
    evidence_root: Path,
) -> None:
    interpolation = _json(evidence_root / "interpolation_ablation.json")
    assert all(
        record["identical_keyframes_required"]
        and record["identical_keyframes_verified"]
        for record in interpolation["records"]
    )
    keyframe_records = [
        record
        for record in interpolation["records"]
        if record["metric"] == "keyframe_ratio"
    ]
    assert len(keyframe_records) == 6
    assert all(record["method_b_advantage_mean"] == 0.0 for record in keyframe_records)
    adaptive = _json(evidence_root / "adaptive_segmentation_ablation.json")
    assert {record["context"] for record in adaptive["records"]} == {
        "primary_byte_0.48",
        "primary_keyframe_0.12",
    }
    temporal = _json(evidence_root / "temporal_vs_geometric_ablation.json")
    assert any(
        record["achieved_byte_ratio"]["absolute_mismatch"] > 0.0
        for record in temporal["records"]
    )
    assert any(
        record["achieved_keyframe_ratio"]["absolute_mismatch"] > 0.0
        for record in temporal["records"]
    )


def test_velocity_invariants_serialization_and_attribution(
    evidence_root: Path,
) -> None:
    velocity = _json(evidence_root / "velocity_constraint_ablation.json")
    hybrid = [
        row
        for row in velocity["invariants"]
        if row["configuration"] == "position_velocity_hybrid_0_10_m_1_00_mps"
    ]
    assert len(hybrid) == 2
    assert all(row["position_bound_violation_count"] == 0 for row in hybrid)
    assert all(row["velocity_bound_violation_count"] == 0 for row in hybrid)
    assert all(row["gap_preservation_failure_count"] == 0 for row in hybrid)

    serialization = _json(evidence_root / "serialization_overhead.json")
    assert serialization["raw_and_exact_treated_as_budget_competitors"] is False
    assert serialization["codec_execution_performed"] is False
    assert {row["cohort_role"] for row in serialization["roles"]} == {
        "pilot",
        "test",
    }
    assert all(
        row["exact_serialized_bytes"] > row["raw_serialized_bytes"]
        and row["exact_to_raw_ratio"]["mean"] > 1.0
        for row in serialization["roles"]
    )
    test_role = next(
        row for row in serialization["roles"] if row["cohort_role"] == "test"
    )
    assert {
        "raw:trajectory_samples.parquet",
        "exact:tape_manifest.parquet",
        "exact:procedural_tracks.parquet",
        "exact:procedural_segments.parquet",
        "exact:codec_summary.json",
    }.issubset(test_role["file_components"])

    attribution = _json(evidence_root / "contribution_attribution.json")
    assert attribution["row_count"] == 7
    assert {row["contribution"] for row in attribution["rows"]} == {
        "adaptive breakpoint selection",
        "linear interpolation",
        "Hermite primitive availability",
        "explicit velocity constraint",
        "temporal error optimization",
        "geometric path simplification",
        "procedural serialization",
    }
    labels = {"significant_result", "descriptive_tendency", "invariant_guarantee"}
    assert set(attribution["claim_labels"]) == labels
    assert attribution["universal_winner_declared"] is False


def test_robustness_missingness_checksums_and_git_hygiene(
    evidence_root: Path,
) -> None:
    robustness = _json(evidence_root / "robustness_analysis.json")
    failures = _json(evidence_root / "failure_report.json")
    evidence = _json(evidence_root / "evidence.json")
    assert robustness["inferential_status"] == "descriptive_only"
    assert robustness["city_records"]
    assert robustness["agent_class_and_motion_category_records"]
    assert robustness["event_type_records"]
    assert robustness["exceptions_omitted"] is False
    assert robustness["city_direction_disagreement_count"] >= 0
    assert robustness["stratum_direction_disagreement_count"] >= 0
    assert failures["analysis_failure_count"] == 0
    assert failures["campaign_failure_count"] == 0
    assert failures["bound_violation_count"] == 0
    assert failures["gap_preservation_failure_count"] == 0
    assert failures["missing_pair_count"] > 0
    assert failures["failures"] == []
    assert failures["omitted_failures"] is False
    for name, expected in evidence["evidence_file_sha256"].items():
        path = evidence_root / name
        assert _sha256(path) == expected
        if path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            assert path.read_text(encoding="utf-8") == canonical_json_text(
                value, trailing_newline=False
            )
    summary = (evidence_root / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith(
        "# Phase 4 Representation Ablations and Contribution Attribution\n"
    )
    assert PASS_STATEMENT in summary
    for phrase in (
        "Adaptive linear segmentation",
        "Geometric path simplification is insufficient",
        "Position preservation therefore does not imply dynamics preservation",
        "explicit velocity constraint",
        "serve different requirements",
        "does not imply compression",
        "No universally best method",
    ):
        assert phrase in summary
    repository_root = evidence_root.parents[2]
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(path.startswith("cache/") for path in tracked)
    assert not any(path.endswith(".parquet") for path in tracked)
