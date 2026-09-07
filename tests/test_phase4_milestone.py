"""Focused contract tests for the Phase 4 milestone package."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kinematicweave.reporting.phase4_milestone import (
    APPROXIMATION_ORDER,
    BATCH_LEDGER,
    DEFERRED_CLAIMS,
    METHOD_TAXONOMY,
    verify_phase4_milestone,
)

ROOT = Path(__file__).resolve().parents[1]
MILESTONE = ROOT / "results/phase4/milestone"


def _json(name: str) -> dict[str, Any]:
    value = json.loads((MILESTONE / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_lightweight_verification_accepts_committed_evidence() -> None:
    evidence = verify_phase4_milestone(ROOT)
    assert evidence["milestone_decision"] == "achieved"
    assert evidence["prior_evidence_modified"] is False
    assert evidence["phase5_started"] is False


def test_contract_snapshot_freezes_exact_phase4_identity() -> None:
    contract = _json("contract_snapshot.json")
    assert contract["batch_commit_ledger"] == BATCH_LEDGER
    assert contract["cohort"] == {
        "cohort_identity": "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166",
        "development": 150,
        "pilot": 50,
        "scenario_count": 500,
        "test": 300,
        "validation_identity": "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13",
    }
    assert contract["representation_count"] == 18
    assert contract["budgets"] == {
        "diagnostic": {
            "keyframe_ratio": 0.23,
            "serialized_byte_ratio": 0.71,
        },
        "primary": {
            "keyframe_ratio": 0.12,
            "serialized_byte_ratio": 0.48,
        },
    }
    assert contract["method_taxonomy"] == METHOD_TAXONOMY


def test_primary_tables_trace_every_selected_approximation() -> None:
    tables = _json("benchmark_tables.json")["tables"]
    assert isinstance(tables, dict)
    source = json.loads(
        (ROOT / "results/phase4/frozen_campaign/test_results.json").read_text(
            encoding="utf-8"
        )
    )
    source_by_method = {row["method_id"]: row for row in source["configurations"]}
    for table_name in (
        "primary_matched_byte_test",
        "primary_matched_keyframe_test",
    ):
        rows = tables[table_name]["rows"]
        assert tuple(row["family"] for row in rows) == APPROXIMATION_ORDER
        for row in rows:
            accepted = source_by_method[row["method_id"]]
            precise = row["full_precision"]
            assert precise["achieved_byte_ratio"] == accepted["byte_ratio"]
            assert precise["keyframe_ratio"] == accepted["keyframe_ratio"]
            assert precise["segment_ratio"] == accepted["segment_ratio"]
            assert (
                precise["position_error_m"]["p95"]
                == accepted["motion_summary"]["position"]["p95"]
            )
            assert (
                precise["velocity_error_mps"]["maximum"]
                == accepted["motion_summary"]["velocity"]["maximum"]
            )
            assert (
                precise["semantic_f1"]["overall"]
                == accepted["semantic_summary"]["overall"]["f1"]
            )


def test_principal_ablations_retain_corrected_statistics() -> None:
    tables = _json("benchmark_tables.json")["tables"]
    rows = tables["principal_ablations"]["rows"]
    assert len(rows) == 6
    assert all(row["pilot_direction_agreement"] for row in rows)
    assert all(row["corrected_p_value"] <= 0.001 for row in rows)
    assert all(len(row["confidence_interval_95"]) == 2 for row in rows)
    assert {row["contrast_id"] for row in rows} == {
        "uniform_linear_vs_uniform_hermite",
        "adaptive_linear_vs_uniform_linear",
        "temporal_bounded_vs_rdp",
        "bounded_linear_vs_unconstrained_hermite",
        "unconstrained_hermite_vs_hybrid",
        "bounded_linear_vs_hybrid",
    }


def test_claim_matrix_qualifies_every_supported_claim() -> None:
    matrix = _json("claim_evidence_matrix.json")
    assert matrix["claim_count"] == 10
    assert tuple(matrix["phase4_does_not_prove"]) == DEFERRED_CLAIMS
    assert matrix["universal_winner_declared"] is False
    for claim in matrix["claims"]:
        assert claim["evidence_files"]
        assert claim["test_or_invariant_status"]
        assert claim["important_qualification"]
        assert claim["unsupported_broader_interpretations"]
        for path in claim["evidence_files"]:
            assert (ROOT / path).is_file()


def test_figure_and_replay_manifest_cover_accepted_outputs() -> None:
    manifest = _json("benchmark_figure_manifest.json")
    assert manifest["qualitative_figure_count"] == 8
    assert len(manifest["qualitative_figures"]) == 8
    for figure in manifest["qualitative_figures"]:
        assert {output["format"] for output in figure["outputs"]} == {
            "png",
            "svg",
        }
        assert all(output["verified"] for output in figure["outputs"])
    replay = manifest["replay"]
    assert replay["frame_count"] == 36
    assert replay["frame_checksums_verified"] is True
    assert replay["encoder"]["repeat_encode_byte_identical"] is True


def test_reproduction_manifest_separates_lightweight_and_expensive_paths() -> None:
    manifest = _json("reproduction_manifest.json")
    lightweight = manifest["lightweight_verification"]
    expensive = manifest["complete_expensive_empirical_reproduction"]
    assert lightweight["requires_provider_download"] is False
    assert lightweight["requires_expensive_empirical_reproduction"] is False
    assert expensive["requires_official_provider_data"] is True
    assert lightweight["commands"] == [
        "uv sync --frozen",
        ("uv run --frozen python scripts/run_phase4_milestone.py --verify-only"),
        (
            "uv run --frozen pytest -q tests/test_phase4_milestone.py "
            "tests/test_phase4_milestone_integration.py"
        ),
    ]
