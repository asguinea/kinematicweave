"""Committed integration checks for Batch 3.6 shared-motion evidence."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, cast

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "results/phase3/shared_motion_templates"
_PASS_STATEMENT = (
    "M3 shared motion categories and route templates achieved on synthetic "
    "and genuine AV2 provider data."
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((_EVIDENCE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shared_motion_evidence_hashes_schemas_and_decision_are_complete() -> None:
    evidence = _json("evidence.json")
    checksums = cast(dict[str, str], evidence["component_sha256"])
    schemas = cast(dict[str, str], evidence["schema_fingerprints"])
    names = (
        "schema_snapshot.json",
        "synthetic_evidence.json",
        "av2_provider_evidence.json",
        "map_evaluation.json",
        "error_analysis.json",
        "summary.md",
    )
    assert checksums == {name: _sha256(_EVIDENCE_ROOT / name) for name in names}
    assert evidence["batch_decision"] == "achieved"
    assert evidence["pass_statement"] == _PASS_STATEMENT
    assert evidence["environment"]["wsl_used"] is True
    assert set(schemas) == {
        "motion_categories",
        "route_templates",
        "route_template_memberships",
    }
    assert all(len(value) == 64 for value in schemas.values())
    summary = (_EVIDENCE_ROOT / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith(
        "# Phase 3 Shared Motion Categories and Route Templates\n"
    )
    assert _PASS_STATEMENT in summary


def test_complete_synthetic_oracle_gate_and_independent_repeat_pass() -> None:
    evidence = _json("synthetic_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    oracles = cast(dict[str, bool], evidence["synthetic_oracles"])
    determinism = cast(dict[str, Any], evidence["determinism"])
    assert counts["scenarios"] == 16
    assert counts["tracks"] == 27
    assert counts["positive_length_route_candidates"] == counts["memberships"]
    assert all(oracles.values())
    assert evidence["every_eligible_track_categorized"] is True
    assert evidence["every_positive_length_run_has_one_template"] is True
    assert determinism["equivalent_repeat_models_match"] is True
    assert determinism["equivalent_repeat_map_evaluations_match"] is True
    assert determinism["equivalent_repeat_parquet_checksums_match"] is True


def test_genuine_av2_grouping_and_map_only_evaluation_pass() -> None:
    evidence = _json("av2_provider_evidence.json")
    counts = cast(dict[str, int], evidence["counts"])
    provider = cast(dict[str, Any], evidence["provider_contract"])
    integrity = cast(dict[str, Any], evidence["input_integrity"])
    policy = cast(dict[str, Any], evidence["construction_policy"])
    determinism = cast(dict[str, Any], evidence["determinism"])
    assert provider["genuine_provider_evidence"] is True
    assert len(provider["selected_scenario_ids"]) == 10
    assert counts["scenarios"] == 10
    assert counts["tracks"] == 418
    assert counts["positive_length_route_candidates"] == counts["memberships"]
    assert counts["shared_templates"] > 0
    assert counts["tracks_in_shared_templates"] > 0
    assert 0.0 < evidence["shared_template_coverage"] <= 1.0
    assert integrity["source_and_cache_unchanged"] is True
    assert policy["motion_only"] is True
    assert policy["map_data_used_for_construction"] is False
    assert policy["map_evaluation_after_frozen_grouping"] is True
    assert determinism["equivalent_repeat_models_match"] is True
    assert determinism["equivalent_repeat_map_evaluations_match"] is True
    assert determinism["equivalent_repeat_parquet_checksums_match"] is True

    map_evidence = _json("map_evaluation.json")
    assert map_evidence["map_evaluation_after_construction"] is True
    assert map_evidence["map_data_used_for_construction"] is False
    assert map_evidence["templates_with_usable_lane_signatures"] > 0
    assert (
        map_evidence["scenario_local_map_translated_to_source_frame_for_evaluation"]
        is True
    )


def test_diagnostics_are_safe_and_generated_provider_data_is_not_tracked() -> None:
    analysis = _json("error_analysis.json")
    assert analysis["provider_rows_or_private_paths_committed"] is False
    for key in (
        "lowest_purity_shared_templates",
        "highest_error_shared_templates",
    ):
        rows = cast(list[dict[str, Any]], analysis[key])
        assert len(rows) <= 10
        assert all(len(row["template_safe_id"]) == 16 for row in rows)
        assert all(
            all(len(identifier) == 16 for identifier in row["member_safe_ids"])
            for row in rows
        )
        assert all(row["likely_cause_labels"] for row in rows)

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
    assert not any(path.endswith(".partial") for path in tracked)
    assert not any(
        path.endswith(".parquet")
        and ("shared_motion" in path or "provider" in path or "generated" in path)
        for path in tracked
    )
