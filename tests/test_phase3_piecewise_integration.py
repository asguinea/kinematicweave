"""Committed integration checks for the Phase 3 compact-codec evidence gate."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "results/phase3/piecewise_linear_codec"
_EXACT_ROOT = _ROOT / "results/phase3/exact_codec_baseline"
_PASS_STATEMENT = (
    "M3 compact linear codec achieved on synthetic and genuine AV2 provider data."
)


def _json(root: Path, name: str) -> dict[str, Any]:
    value = json.loads((root / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_piecewise_evidence_checksums_and_exact_reference_are_complete() -> None:
    evidence = _json(_EVIDENCE_ROOT, "evidence.json")
    checksums = cast(dict[str, str], evidence["component_sha256"])
    baseline = cast(dict[str, Any], evidence["exact_baseline_reference"])

    assert checksums == {
        name: _sha256(_EVIDENCE_ROOT / name)
        for name in (
            "synthetic_evidence.json",
            "av2_provider_evidence.json",
            "summary.md",
        )
    }
    assert baseline == {
        "batch_decision": "achieved",
        "commit": "790dd32e18628ff8f29f13da41360f789d833341",
        "evidence_sha256": _sha256(_EXACT_ROOT / "evidence.json"),
    }
    assert evidence["batch_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS_STATEMENT
    assert _PASS_STATEMENT in (_EVIDENCE_ROOT / "summary.md").read_text(
        encoding="utf-8"
    )


def test_compact_synthetic_gate_covers_all_trajectories_and_respects_bound() -> None:
    evidence = _json(_EVIDENCE_ROOT, "synthetic_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    compression = cast(dict[str, float], evidence["compression"])
    errors = cast(dict[str, dict[str, float]], evidence["errors"])
    exact = cast(dict[str, int], evidence["exact_baseline"])

    assert evidence["status"] == "PASS"
    assert counts["scenario_count"] == 16
    assert counts["trajectory_count"] == 27
    assert counts["source_sample_count"] == 293
    assert counts["valid_sample_count"] == 291
    assert counts["invalid_sample_count"] == 2
    assert counts["valid_run_count"] == 28
    assert counts["segment_count"] <= exact["segment_count"] == 263
    assert compression["segment_reduction"] > 0
    assert errors["position_m"]["maximum"] <= 0.10
    assert evidence["invalid_gap_samples_with_state"] == 0
    assert evidence["run_endpoints_exact"] is True
    assert evidence["retained_breakpoints_exact"] is True
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True


def test_genuine_av2_gate_strictly_improves_segments_and_bytes() -> None:
    evidence = _json(_EVIDENCE_ROOT, "av2_provider_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    compression = cast(dict[str, float], evidence["compression"])
    errors = cast(dict[str, dict[str, float]], evidence["errors"])
    exact = cast(dict[str, int], evidence["exact_baseline"])
    provider = cast(dict[str, Any], evidence["provider_contract"])
    integrity = cast(dict[str, Any], evidence["input_integrity"])

    assert evidence["status"] == "PASS"
    assert provider["genuine_provider_evidence"] is True
    assert provider["official_remote_root"].startswith("s3://argoverse/")
    assert provider["validation_report_identity"]
    assert len(provider["selected_scenario_ids"]) == 10
    assert counts["scenario_count"] == 10
    assert counts["trajectory_count"] == 418
    assert counts["source_sample_count"] == 22_968
    assert counts["valid_sample_count"] == 22_968
    assert counts["valid_run_count"] == 418
    assert counts["segment_count"] < exact["segment_count"] == 22_550
    assert (
        evidence["procedural_artifact_bytes"]
        < (exact["procedural_artifact_bytes"])
        == 2_819_141
    )
    assert compression["segment_reduction"] > 0
    assert compression["byte_reduction"] > 0
    assert errors["position_m"]["maximum"] <= 0.10
    assert evidence["invalid_gap_samples_with_state"] == 0
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True
    assert integrity["source_and_cache_unchanged"] is True
    assert (
        integrity["source_tree_sha256_before"] == integrity["source_tree_sha256_after"]
    )
    assert integrity["cache_tree_sha256_before"] == integrity["cache_tree_sha256_after"]


def test_piecewise_configuration_and_checksums_are_deterministic() -> None:
    synthetic = _json(_EVIDENCE_ROOT, "synthetic_evidence.json")
    av2 = _json(_EVIDENCE_ROOT, "av2_provider_evidence.json")
    synthetic_config = cast(dict[str, Any], synthetic["configuration"])
    av2_config = cast(dict[str, Any], av2["configuration"])

    assert synthetic_config == av2_config
    assert synthetic_config["maximum_position_error_m"] == 0.10
    assert synthetic_config["encoder_parameters_identity"]
    assert synthetic["equivalent_repeat_parquet_checksums_match"] is True
    assert av2["equivalent_repeat_parquet_checksums_match"] is True
    assert len(synthetic["scenario_artifact_checksums"]) == 16
    assert len(av2["scenario_artifact_checksums"]) == 10


def test_no_provider_cache_or_generated_procedural_data_is_tracked() -> None:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={_ROOT.as_posix()}", "ls-files"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = tuple(line for line in completed.stdout.splitlines() if line)
    forbidden_prefixes = (
        "cache/",
        "data/external/",
        "results/generated/",
    )
    assert not any(path.startswith(forbidden_prefixes) for path in tracked)
    assert not any(path.endswith(".partial") for path in tracked)
    assert not any(
        path.endswith(".parquet")
        and ("provider" in path or "procedural" in path or "generated" in path)
        for path in tracked
    )
