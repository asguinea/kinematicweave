"""Committed integration checks for Phase 3 semantic-motion evidence."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "results/phase3/semantic_motion"
_PASS_STATEMENT = (
    "M3 semantic motion layer achieved on synthetic and genuine AV2 provider data."
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((_EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_semantic_evidence_hashes_schemas_and_decision_are_complete() -> None:
    evidence = _json("evidence.json")
    checksums = cast(dict[str, str], evidence["component_sha256"])
    schemas = cast(dict[str, str], evidence["schema_fingerprints"])

    assert checksums == {
        name: _sha256(_EVIDENCE_ROOT / name)
        for name in (
            "schema_snapshot.json",
            "synthetic_evidence.json",
            "av2_provider_evidence.json",
            "preservation_analysis.json",
            "error_analysis.json",
            "summary.md",
        )
    }
    assert evidence["batch_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS_STATEMENT
    assert evidence["environment"]["wsl_used"] is True
    assert schemas == {
        "semantic_waypoints": (
            "9ceaee7baf378e57898e7e241d74be38b2eb847391936ff578afab15c51d4b1d"
        ),
        "motion_events": (
            "a20c7150fb9ba9f375bb82af6af0623b07a8dfc45495f6a151ec6a01ad700d42"
        ),
    }
    summary = (_EVIDENCE_ROOT / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith("# Phase 3 Semantic Waypoints and Motion Events\n")
    assert _PASS_STATEMENT in summary


def test_complete_synthetic_oracle_and_preservation_gate_passes() -> None:
    evidence = _json("synthetic_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    replay = cast(dict[str, dict[str, float]], evidence["waypoint_replay_errors"])
    oracles = cast(list[dict[str, Any]], evidence["synthetic_oracles"])

    assert evidence["status"] == "PASS"
    assert counts["scenario_count"] == 16
    assert counts["trajectory_count"] == 27
    assert counts["source_sample_count"] == 293
    assert counts["waypoint_count"] > 0
    assert counts["event_count"] > 0
    assert len(oracles) == 16
    assert all(oracle["passed"] is True for oracle in oracles)
    assert evidence["all_synthetic_oracles_pass"] is True
    assert evidence["gap_preservation_exact"] is True
    assert evidence["preservation"]["f1"] == 1.0
    assert replay["position_m"]["maximum"] <= 0.10
    assert replay["velocity_mps"]["maximum"] <= 1.00
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True


def test_genuine_av2_gate_reports_complete_honest_semantic_measurements() -> None:
    evidence = _json("av2_provider_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    event_counts = cast(dict[str, int], evidence["event_counts_by_type"])
    replay = cast(dict[str, dict[str, float]], evidence["waypoint_replay_errors"])
    preservation = cast(dict[str, Any], evidence["preservation"])
    provider = cast(dict[str, Any], evidence["provider_contract"])
    integrity = cast(dict[str, Any], evidence["input_integrity"])
    storage = cast(dict[str, int], evidence["storage"])

    assert evidence["status"] == "PASS"
    assert provider["genuine_provider_evidence"] is True
    assert len(provider["selected_scenario_ids"]) == 10
    assert counts["scenario_count"] == 10
    assert counts["trajectory_count"] == 418
    assert counts["source_sample_count"] == 22_968
    assert counts["waypoint_count"] == 3_583
    assert counts["event_count"] == sum(event_counts.values()) == 668
    assert event_counts["gap"] == 0
    assert event_counts["stop"] > 0
    assert event_counts["left_turn"] > 0
    assert event_counts["right_turn"] > 0
    assert event_counts["acceleration"] > 0
    assert event_counts["braking"] > 0
    assert 0.0 <= preservation["f1"] <= 1.0
    assert evidence["gap_preservation_exact"] is True
    assert replay["position_m"]["maximum"] <= 0.10
    assert replay["velocity_mps"]["maximum"] <= 1.00
    assert storage["semantic_artifact_bytes"] > 0
    assert storage["procedural_artifact_bytes"] == 553_731
    assert integrity["source_and_cache_unchanged"] is True
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True


def test_diagnostics_are_safe_complete_and_interpretable() -> None:
    analysis = _json("error_analysis.json")
    rows = cast(
        list[dict[str, Any]],
        analysis["lowest_event_preservation_f1_tracks"],
    )

    assert analysis["trajectory_count"] == 418
    assert analysis["raw_provider_paths_or_rows_committed"] is False
    assert len(rows) == 10
    assert all(len(row["trajectory_safe_id"]) == 64 for row in rows)
    assert all(0.0 <= row["event_preservation_f1"] <= 1.0 for row in rows)
    assert all(
        set(row["associations"])
        == {
            "sparse_sampling",
            "heading_absence",
            "velocity_derivation",
            "event_overlap",
            "short_duration_events",
            "codec_interpolation",
        }
        for row in rows
    )
    assert all(
        sum(row["source_event_counts_by_type"].values())
        >= sum(row["unmatched_source_events"].values())
        for row in rows
    )


def test_no_provider_cache_or_generated_semantic_parquet_is_tracked() -> None:
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
        and ("semantic" in path or "provider" in path or "generated" in path)
        for path in tracked
    )
