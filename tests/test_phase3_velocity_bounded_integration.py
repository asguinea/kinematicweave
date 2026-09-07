"""Committed integration checks for the Phase 3 dual-bound codec evidence."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "results/phase3/velocity_bounded_codec"
_PASS_STATEMENT = (
    "M3 position-and-velocity-bounded codec evaluated on synthetic and genuine "
    "AV2 provider data."
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((_EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_evidence_checksums_identity_and_pass_statement_are_complete() -> None:
    evidence = _json("evidence.json")
    checksums = cast(dict[str, str], evidence["component_sha256"])
    configuration = cast(dict[str, Any], evidence["configuration"])

    assert checksums == {
        name: _sha256(_EVIDENCE_ROOT / name)
        for name in (
            "synthetic_evidence.json",
            "av2_provider_evidence.json",
            "error_analysis.json",
            "performance_analysis.json",
            "summary.md",
        )
    }
    assert evidence["batch_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS_STATEMENT
    assert evidence["environment"]["wsl_used"] is True
    assert configuration["maximum_position_error_m"] == 0.10
    assert configuration["maximum_velocity_error_mps"] == 1.00
    assert configuration["candidate_primitives"] == [
        "hold",
        "linear",
        "cubic_hermite",
    ]
    summary = (_EVIDENCE_ROOT / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith("# Phase 3 Position-and-Velocity-Bounded Codec\n")
    assert _PASS_STATEMENT in summary


def test_synthetic_gate_covers_all_trajectories_and_both_bounds() -> None:
    evidence = _json("synthetic_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    errors = cast(dict[str, dict[str, float]], evidence["errors"])

    assert evidence["status"] == "PASS"
    assert counts["scenario_count"] == 16
    assert counts["trajectory_count"] == 27
    assert counts["source_sample_count"] == 293
    assert counts["valid_sample_count"] == 291
    assert counts["invalid_sample_count"] == 2
    assert counts["valid_run_count"] == 28
    assert counts["segment_count"] == 39 <= 263
    assert errors["position_m"]["maximum"] <= 0.10
    assert errors["velocity_mps"]["maximum"] <= 1.00
    assert evidence["invalid_gap_samples_with_state"] == 0
    assert evidence["run_endpoints_exact"] is True
    assert evidence["retained_breakpoints_exact"] is True
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True


def test_genuine_av2_gate_is_complete_bounded_and_compact() -> None:
    evidence = _json("av2_provider_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    errors = cast(dict[str, dict[str, float]], evidence["errors"])
    provider = cast(dict[str, Any], evidence["provider_contract"])
    integrity = cast(dict[str, Any], evidence["input_integrity"])

    assert evidence["status"] == "PASS"
    assert provider["genuine_provider_evidence"] is True
    assert provider["official_remote_root"].startswith("s3://argoverse/")
    assert len(provider["selected_scenario_ids"]) == 10
    assert counts["scenario_count"] == 10
    assert counts["trajectory_count"] == 418
    assert counts["source_sample_count"] == 22_968
    assert counts["valid_run_count"] == 418
    assert counts["segment_count"] == 2_327 < 22_550
    assert counts["segment_count"] < 2_488
    assert counts["cubic_hermite_segment_count"] == 589
    assert evidence["procedural_artifact_bytes"] == 553_731 < 2_819_141
    assert errors["position_m"]["maximum"] <= 0.10
    assert errors["velocity_mps"]["maximum"] <= 1.00
    assert evidence["invalid_gap_samples_with_state"] == 0
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True
    assert integrity["source_and_cache_unchanged"] is True


def test_diagnostics_and_performance_report_velocity_tradeoff() -> None:
    analysis = _json("error_analysis.json")
    performance = _json("performance_analysis.json")
    rows = cast(list[dict[str, Any]], analysis["highest_velocity_error_trajectories"])
    additions = cast(dict[str, int], analysis["segments_added_relative_to_batch_3_3"])
    rejections = cast(dict[str, int], analysis["candidate_rejections"])

    assert analysis["trajectory_count"] == 418
    assert additions["sum"] == 231
    assert additions["positive_count"] == 122
    assert additions["maximum"] == 9
    assert rejections["position"] == 970_377
    assert rejections["velocity"] == 17_349
    assert len(rows) == 10
    assert all(len(row["trajectory_safe_id"]) == 64 for row in rows)
    assert all(row["maximum_position_error_m"] <= 0.10 for row in rows)
    assert all(row["maximum_velocity_error_mps"] <= 1.00 for row in rows)
    assert all(
        row["worst_velocity_error_location"] in {"retained_breakpoint", "interior"}
        for row in rows
    )
    assert performance["material_runtime_improvement"] is True
    assert performance["encoding_speedup_over_batch_3_3"] > 5.0


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
