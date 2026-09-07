"""Integration checks for committed Batch 4.7 statistical evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from kinematicweave.canonical import canonical_json_text
from kinematicweave.experiments.statistical_campaign import (
    FROZEN_BUDGET_CONTRACT_SHA256,
    FROZEN_COHORT_IDENTITY,
    FROZEN_MATRIX_IDENTITY,
    FROZEN_METRIC_IDENTITY,
    FROZEN_VALIDATION_IDENTITY,
    PASS_STATEMENT,
    STARTING_HEAD,
)

_REQUIRED_FILES = {
    "statistical_contract.json",
    "scenario_aggregates.json",
    "primary_byte_comparisons.json",
    "primary_keyframe_comparisons.json",
    "diagnostic_comparisons.json",
    "event_type_analysis.json",
    "pilot_test_consistency.json",
    "robustness_analysis.json",
    "multiplicity_report.json",
    "failure_report.json",
    "evidence.json",
    "summary.md",
}
_REQUIRED_METRICS = {
    "position_mean_m",
    "position_p95_m",
    "velocity_mean_mps",
    "velocity_p95_mps",
    "semantic_overall_f1",
    "semantic_stop_f1",
    "semantic_turn_f1",
    "semantic_acceleration_f1",
    "semantic_braking_f1",
    "encoding_seconds",
}


@pytest.fixture(scope="module")
def evidence_root() -> Path:
    return Path(__file__).parents[1] / "results/phase4/statistical_analysis"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_statistical_contract_and_evidence_gate(evidence_root: Path) -> None:
    evidence = _json(evidence_root / "evidence.json")
    contract = _json(evidence_root / "statistical_contract.json")
    assert {path.name for path in evidence_root.iterdir()} == _REQUIRED_FILES
    assert evidence["analysis_decision"] == "completed"
    assert evidence["pass_statement"] == PASS_STATEMENT
    assert evidence["starting_head"] == STARTING_HEAD
    assert evidence["cohort_identity"] == FROZEN_COHORT_IDENTITY
    assert evidence["matrix_identity"] == FROZEN_MATRIX_IDENTITY
    assert evidence["metric_identity"] == FROZEN_METRIC_IDENTITY
    assert evidence["validation_identity"] == FROZEN_VALIDATION_IDENTITY
    assert evidence["budget_contract_sha256"] == FROZEN_BUDGET_CONTRACT_SHA256
    assert evidence["independent_statistical_unit"] == "scenario"
    assert evidence["root_seed"] == 4_707
    assert evidence["pilot_resamples"] == 2_000
    assert evidence["test_resamples"] == 10_000
    assert evidence["environment"]["operating_system"] == "Ubuntu under WSL2"
    assert evidence["environment"]["gpu_used"] is False
    assert evidence["pilot_scenario_count"] == 50
    assert evidence["test_scenario_count"] == 300
    assert evidence["scenario_aggregate_count"] == 6_300
    assert evidence["analysis_failure_count"] == 0
    assert evidence["campaign_failure_count"] == 0
    assert evidence["pilot_and_test_pooled"] is False
    assert evidence["development_outcomes_used"] is False
    assert evidence["raw_and_exact_inferentially_compared"] is False
    assert evidence["final_method_selected"] is False
    assert evidence["determinism_verified"] is True
    assert (
        evidence["analysis_output_identity"]
        == evidence["repeat_analysis_output_identity"]
    )
    assert contract["independent_statistical_unit"] == "scenario"
    assert contract["nested_observations"] == ["trajectory", "sample", "event"]
    assert contract["primary_byte_target"] == 0.48
    assert contract["primary_keyframe_target"] == 0.12
    assert contract["diagnostic_byte_target"] == 0.71
    assert contract["diagnostic_keyframe_target"] == 0.23
    assert set(contract["required_metrics"]) == _REQUIRED_METRICS
    assert contract["confidence_interval"]["pilot_resamples"] == 2_000
    assert contract["confidence_interval"]["test_resamples"] == 10_000


@pytest.mark.parametrize(
    ("name", "condition", "expected_count", "expected_pairs"),
    [
        ("primary_byte_comparisons.json", "primary_byte", 420, 21),
        ("primary_keyframe_comparisons.json", "primary_keyframe", 420, 21),
        ("diagnostic_comparisons.json", None, 120, 6),
    ],
)
def test_complete_paired_families_and_statistical_fields(
    evidence_root: Path,
    name: str,
    condition: str | None,
    expected_count: int,
    expected_pairs: int,
) -> None:
    payload = _json(evidence_root / name)
    rows = payload["comparisons"]
    assert payload["comparison_count"] == len(rows) == expected_count
    assert {row["cohort_role"] for row in rows} == {"pilot", "test"}
    if condition is not None:
        assert {row["condition"] for row in rows} == {condition}
    for role in ("pilot", "test"):
        role_rows = [row for row in rows if row["cohort_role"] == role]
        repository_root = evidence_root.parents[2]
        for current_condition in {row["condition"] for row in role_rows}:
            condition_rows = [
                row for row in role_rows if row["condition"] == current_condition
            ]
            matched_name = (
                "matched_byte_results.json"
                if condition_rows[0]["dimension"] == "byte_ratio"
                else "matched_keyframe_results.json"
            )
            frozen = _json(
                repository_root / "results/phase4/frozen_campaign" / matched_name
            )
            expected_ids = {
                row["parameter_identity"]
                for row in frozen["selections"]
                if row["cohort_role"] == role
                and row["priority"] == condition_rows[0]["priority"]
                and row["target"] == condition_rows[0]["target"]
            }
            observed_ids = {
                row[f"method_{side}_parameter_identity"]
                for row in condition_rows
                for side in ("a", "b")
            }
            assert observed_ids == expected_ids
        pairs = {
            (
                row["condition"],
                row["method_a_parameter_identity"],
                row["method_b_parameter_identity"],
            )
            for row in role_rows
        }
        assert len(pairs) == expected_pairs
        assert {row["metric"] for row in role_rows} == _REQUIRED_METRICS
        assert all(row["valid_pair_count"] > 0 for row in role_rows)
        assert all(0.0 <= row["p_value_raw"] <= 1.0 for row in role_rows)
        assert all(0.0 <= row["adjusted_p_value"] <= 1.0 for row in role_rows)
        assert all(
            row["adjusted_p_value"] + 1e-15 >= row["p_value_raw"] for row in role_rows
        )
        assert all(
            row["confidence_interval_lower"] <= row["confidence_interval_upper"]
            for row in role_rows
        )
        assert all(
            row["method_b_wins"] + row["ties"] + row["method_b_losses"]
            == row["valid_pair_count"]
            for row in role_rows
        )
        assert all(
            row["practical_magnitude"]
            in {
                "negligible",
                "small",
                "moderate",
                "large",
                "undefined_zero_variance",
            }
            for row in role_rows
        )


def test_event_missingness_consistency_and_robustness(evidence_root: Path) -> None:
    events = _json(evidence_root / "event_type_analysis.json")
    consistency = _json(evidence_root / "pilot_test_consistency.json")
    robustness = _json(evidence_root / "robustness_analysis.json")
    failures = _json(evidence_root / "failure_report.json")
    assert events["analysis_count"] == 80
    assert {row["event_type"] for row in events["analyses"]} == {
        "stop",
        "turn",
        "acceleration",
        "braking",
    }
    assert all(
        row["all_scenario_presence_aware_sensitivity"]["count"] in {50, 300}
        for row in events["analyses"]
    )
    assert all(
        row["undefined_scenario_count"] >= row["source_absent_scenario_count"]
        for row in events["analyses"]
    )
    assert consistency["pilot_and_test_not_pooled"] is True
    assert consistency["check_count"] == 480
    assert 0 <= consistency["direction_agreement_count"] <= 480
    assert robustness["inferential_status"] == "descriptive_only"
    assert robustness["scenario_macro"]
    assert robustness["city_strata"]
    assert robustness["trajectory_agent_class_and_motion_category_strata"]
    assert failures["analysis_failure_count"] == 0
    assert failures["campaign_failure_count"] == 0
    assert failures["failures"] == []
    assert failures["omitted_failures"] is False
    assert failures["missing_pair_count"] > 0


def test_checksums_summary_and_generated_data_hygiene(evidence_root: Path) -> None:
    repository_root = evidence_root.parents[2]
    evidence = _json(evidence_root / "evidence.json")
    for name, expected in evidence["evidence_file_sha256"].items():
        assert _sha256(evidence_root / name) == expected
        if name.endswith(".json"):
            value = json.loads((evidence_root / name).read_text(encoding="utf-8"))
            assert (evidence_root / name).read_text(
                encoding="utf-8"
            ) == canonical_json_text(value, trailing_newline=False)
    summary = (evidence_root / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith("# Phase 4 Confirmatory Statistical Analysis\n")
    assert PASS_STATEMENT in summary
    assert "does not claim a universal winner" in summary
    assert "select a final method" in summary
    descriptor = evidence["derived_scenario_table"]
    assert descriptor["row_count"] == 6_300
    assert descriptor["sha256"] == descriptor["repeat_sha256"]
    ignored = subprocess.run(
        ["git", "check-ignore", descriptor["path"]],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert ignored.stdout.strip() == descriptor["path"]
