"""Committed integration checks for the Phase 3 Hermite evidence gate."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "results/phase3/hermite_codec"
_EXACT_ROOT = _ROOT / "results/phase3/exact_codec_baseline"
_LINEAR_ROOT = _ROOT / "results/phase3/piecewise_linear_codec"
_PASS_STATEMENT = (
    "M3 velocity-aware Hermite codec evaluated on synthetic and genuine AV2 "
    "provider data."
)


def _json(root: Path, name: str) -> dict[str, Any]:
    value = json.loads((root / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_hermite_evidence_checksums_and_baseline_references_are_complete() -> None:
    evidence = _json(_EVIDENCE_ROOT, "evidence.json")
    checksums = cast(dict[str, str], evidence["component_sha256"])
    exact = cast(dict[str, Any], evidence["exact_baseline_reference"])
    linear = cast(dict[str, Any], evidence["piecewise_linear_baseline_reference"])

    assert checksums == {
        name: _sha256(_EVIDENCE_ROOT / name)
        for name in (
            "synthetic_evidence.json",
            "av2_provider_evidence.json",
            "error_analysis.json",
            "summary.md",
        )
    }
    assert exact["batch_decision"] == "achieved"
    assert exact["evidence_sha256"] == _sha256(_EXACT_ROOT / "evidence.json")
    assert linear == {
        "batch_decision": "achieved",
        "commit": "66a36cace93a848220458b90acf60586e8707a63",
        "evidence_sha256": _sha256(_LINEAR_ROOT / "evidence.json"),
    }
    assert evidence["batch_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS_STATEMENT
    assert evidence["environment"]["wsl_used"] is True
    assert _PASS_STATEMENT in (_EVIDENCE_ROOT / "summary.md").read_text(
        encoding="utf-8"
    )


def test_hermite_synthetic_gate_covers_all_trajectories_and_improves_segments() -> None:
    evidence = _json(_EVIDENCE_ROOT, "synthetic_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    errors = cast(dict[str, dict[str, float]], evidence["errors"])
    linear = cast(dict[str, int], evidence["piecewise_linear_baseline"])

    assert evidence["status"] == "PASS"
    assert counts["scenario_count"] == 16
    assert counts["trajectory_count"] == 27
    assert counts["source_sample_count"] == 293
    assert counts["valid_sample_count"] == 291
    assert counts["invalid_sample_count"] == 2
    assert counts["valid_run_count"] == 28
    assert counts["segment_count"] == 39 <= linear["segment_count"] == 55
    assert counts["cubic_hermite_segment_count"] == 6
    assert counts["trajectories_using_cubic_hermite"] == 4
    assert errors["position_m"]["maximum"] <= 0.10
    assert evidence["invalid_gap_samples_with_state"] == 0
    assert evidence["run_endpoints_exact"] is True
    assert evidence["retained_breakpoints_exact"] is True
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True


def test_genuine_av2_gate_reports_measured_improvements_and_regressions() -> None:
    evidence = _json(_EVIDENCE_ROOT, "av2_provider_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    errors = cast(dict[str, dict[str, float]], evidence["errors"])
    provider = cast(dict[str, Any], evidence["provider_contract"])
    integrity = cast(dict[str, Any], evidence["input_integrity"])
    linear = cast(dict[str, int], evidence["piecewise_linear_baseline"])
    linear_errors = cast(
        dict[str, float],
        evidence["comparison_to_piecewise_linear"],
    )

    assert evidence["status"] == "PASS"
    assert provider["genuine_provider_evidence"] is True
    assert provider["official_remote_root"].startswith("s3://argoverse/")
    assert len(provider["selected_scenario_ids"]) == 10
    assert counts["scenario_count"] == 10
    assert counts["trajectory_count"] == 418
    assert counts["source_sample_count"] == 22_968
    assert counts["valid_sample_count"] == 22_968
    assert counts["valid_run_count"] == 418
    assert counts["segment_count"] == 2_096 < linear["segment_count"] == 2_488
    assert counts["cubic_hermite_segment_count"] == 969
    assert counts["trajectories_using_cubic_hermite"] == 324
    assert evidence["procedural_artifact_bytes"] == 526_573
    assert evidence["procedural_artifact_bytes"] < (linear["procedural_artifact_bytes"])
    assert errors["position_m"]["maximum"] <= 0.10
    assert errors["position_m"]["p95"] < linear_errors["position_p95_m"]
    assert errors["heading_rad"]["p95"] > linear_errors["heading_p95_rad"]
    assert errors["velocity_mps"]["p95"] > linear_errors["velocity_p95_mps"]
    assert errors["velocity_mps"]["maximum"] > (linear_errors["velocity_maximum_mps"])
    assert evidence["invalid_gap_samples_with_state"] == 0
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True
    assert integrity["source_and_cache_unchanged"] is True


def test_error_analysis_is_safe_complete_and_reports_interior_failures() -> None:
    analysis = _json(_EVIDENCE_ROOT, "error_analysis.json")
    rows = cast(list[dict[str, Any]], analysis["highest_velocity_error_trajectories"])

    assert analysis["trajectory_count"] == 418
    assert analysis["trajectories_using_cubic_hermite"] == 324
    assert analysis["raw_provider_paths_or_rows_committed"] is False
    assert len(rows) == 10
    assert all(len(row["trajectory_safe_id"]) == 64 for row in rows)
    assert all(row["maximum_position_error_m"] <= 0.10 for row in rows)
    assert all(
        row["worst_velocity_error_location"] in {"retained_breakpoint", "interior"}
        for row in rows
    )
    assert any(row["worst_velocity_error_location"] == "interior" for row in rows)
    assert all(row["primitive_composition"]["cubic_hermite"] > 0 for row in rows)


def test_no_provider_cache_or_generated_procedural_data_is_tracked() -> None:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={_ROOT.as_posix()}", "ls-files"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = tuple(line for line in completed.stdout.splitlines() if line)
    forbidden_prefixes = ("cache/", "data/external/", "results/generated/")

    assert not any(path.startswith(forbidden_prefixes) for path in tracked)
    assert not any(path.endswith(".partial") for path in tracked)
    assert not any(
        path.endswith(".parquet")
        and ("provider" in path or "procedural" in path or "generated" in path)
        for path in tracked
    )
