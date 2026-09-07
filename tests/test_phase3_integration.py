"""Committed integration checks for the Phase 3 exact-codec evidence gate."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "results/phase3/exact_codec_baseline"
_PASS_STATEMENT = (
    "M3 exact-codec baseline achieved on synthetic and genuine AV2 provider data."
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((_EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exact_codec_evidence_component_checksums_are_complete() -> None:
    evidence = _json("evidence.json")
    checksums = cast(dict[str, str], evidence["component_sha256"])
    assert checksums == {
        name: _sha256(_EVIDENCE_ROOT / name)
        for name in (
            "schema_snapshot.json",
            "synthetic_evidence.json",
            "av2_provider_evidence.json",
            "summary.md",
        )
    }
    assert evidence["batch_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS_STATEMENT
    assert _PASS_STATEMENT in (_EVIDENCE_ROOT / "summary.md").read_text(
        encoding="utf-8"
    )


def test_genuine_av2_exact_codec_gate_covers_the_approved_inclusion_set() -> None:
    evidence = _json("av2_provider_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
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
    assert counts["segment_count"] == 22_550
    assert evidence["maximum_source_endpoint_position_error_m"] == 0.0
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True
    assert integrity["source_and_cache_unchanged"] is True
    assert (
        integrity["source_tree_sha256_before"] == integrity["source_tree_sha256_after"]
    )
    assert integrity["cache_tree_sha256_before"] == integrity["cache_tree_sha256_after"]


def test_synthetic_exact_codec_gate_is_complete_and_gap_safe() -> None:
    evidence = _json("synthetic_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])

    assert evidence["status"] == "PASS"
    assert counts["scenario_count"] == 16
    assert counts["trajectory_count"] == 27
    assert counts["source_sample_count"] == 293
    assert counts["valid_sample_count"] == 291
    assert counts["invalid_sample_count"] == 2
    assert counts["hold_segment_count"] == 3
    assert counts["linear_segment_count"] == 260
    assert evidence["maximum_source_endpoint_position_error_m"] == 0.0
    assert evidence["replayed_valid_sample_count"] == 291
    assert evidence["equivalent_repeat_parquet_checksums_match"] is True


def test_phase3_schema_snapshot_preserves_old_and_records_new_fingerprints() -> None:
    snapshot = _json("schema_snapshot.json")
    rows = {
        row["name"]: row["fingerprint"]
        for row in cast(list[dict[str, Any]], snapshot["schemas"])
    }
    assert rows["scenario_manifest"] == (
        "e59b221b1bf720832d90a19340f773c31b461292323d6ddb0a12fb8096d7d03b"
    )
    assert rows["procedural_tape_manifest"] == (
        "7f556d450ed8cbf198e4e9c8be04bb1429cb6a2c42649aa0c9f0a07fa76c7b2c"
    )
    assert rows["procedural_tracks"] == (
        "92964188ec2bbf6259a961113dbe31cb524cd2cfddb521f852d5665e6fceb96a"
    )
    assert rows["procedural_segments"] == (
        "e994f5d7c2f2cfc21ddbb06b44c076d04e94491fe2f201bc4b2a76bfa089cd86"
    )


def test_no_provider_or_generated_procedural_data_is_tracked() -> None:
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
