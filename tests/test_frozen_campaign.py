"""Focused tests for the frozen pilot-before-test campaign controls."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys
from typing import cast

import pytest

from kinematicweave.canonical import canonical_json_text
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.frozen_campaign import (
    CONFIGURATION_COUNT,
    FROZEN_COHORT_IDENTITY,
    FROZEN_MATRIX_IDENTITY,
    PILOT_SCENARIO_COUNT,
    PilotCompletion,
    checkpoint_output_identity,
    file_snapshot_identity,
    load_pilot_completion,
    read_pilot_units,
    read_test_units,
    write_pilot_completion,
)
from kinematicweave.experiments.motion_cohort import CohortRole, MotionCohortUnit


def _unit(role: CohortRole, rank: int) -> MotionCohortUnit:
    source_id = f"{role.value}-{rank:03d}"
    return MotionCohortUnit(
        provider_partition="val" if role is CohortRole.TEST else "train",
        cohort_role=role,
        selection_rank=rank,
        source_scenario_id=source_id,
        motion_object_path=Path(f"provider/{source_id}/scenario_{source_id}.parquet"),
        motion_size_bytes=100,
        motion_sha256=f"{rank:064x}",
        map_object_path=Path(f"provider/{source_id}/log_map_archive_{source_id}.json"),
        map_size_bytes=200,
        map_sha256=f"{rank + 1000:064x}",
        materialization_cache_key=f"{rank + 2000:064x}",
        validation_included=True,
        exclusion_reason=None,
    )


def _unit_dict(unit: MotionCohortUnit) -> dict[str, object]:
    return {
        "provider_partition": unit.provider_partition,
        "cohort_role": cast(CohortRole, unit.cohort_role).value,
        "selection_rank": unit.selection_rank,
        "source_scenario_id": unit.source_scenario_id,
        "motion_object_path": unit.motion_object_path.as_posix(),
        "motion_size_bytes": unit.motion_size_bytes,
        "motion_sha256": unit.motion_sha256,
        "map_object_path": unit.map_object_path.as_posix(),
        "map_size_bytes": unit.map_size_bytes,
        "map_sha256": unit.map_sha256,
        "materialization_cache_key": unit.materialization_cache_key,
        "validation_included": unit.validation_included,
        "exclusion_reason": unit.exclusion_reason,
    }


def _completion(**overrides: object) -> PilotCompletion:
    values: dict[str, object] = {
        "cohort_identity": FROZEN_COHORT_IDENTITY,
        "matrix_identity": FROZEN_MATRIX_IDENTITY,
        "pilot_scenario_identity": "1" * 64,
        "trajectory_membership_identity": "2" * 64,
        "cache_snapshot_identity": "3" * 64,
        "checkpoint_output_identity": "4" * 64,
        "scenario_count": PILOT_SCENARIO_COUNT,
        "configuration_count": CONFIGURATION_COUNT,
        "scenario_configuration_count": 900,
        "failure_count": 0,
        "protocol_changed": False,
        "resource_feasible": True,
    }
    values.update(overrides)
    return PilotCompletion(**values)  # type: ignore[arg-type]


def test_frozen_matrix_identity_matches_committed_protocol() -> None:
    root = Path(__file__).parents[1]
    matrix = json.loads(
        (root / "results/phase4/protocol_freeze/final_campaign_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    assert matrix["matrix_identity"] == FROZEN_MATRIX_IDENTITY
    assert len(matrix["methods"]) == CONFIGURATION_COUNT
    assert matrix["configuration_ids"] == [
        method["configuration_id"] for method in matrix["methods"]
    ]


def test_pilot_reader_stops_before_test_metadata(tmp_path: Path) -> None:
    development = [_unit(CohortRole.DEVELOPMENT, rank) for rank in range(1, 151)]
    pilot = [_unit(CohortRole.PILOT, rank) for rank in range(1, 51)]
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"units":['
        + ",".join(json.dumps(_unit_dict(unit)) for unit in (*development, *pilot))
        + ",TEST_METADATA_MUST_NOT_BE_READ",
        encoding="utf-8",
    )
    units = read_pilot_units(path)
    assert len(units) == PILOT_SCENARIO_COUNT
    assert {cast(CohortRole, unit.cohort_role) for unit in units} == {CohortRole.PILOT}


def test_test_reader_requires_passing_immutable_pilot_gate(tmp_path: Path) -> None:
    units = [_unit(CohortRole.TEST, rank) for rank in range(1, 301)]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"units":[' + ",".join(json.dumps(_unit_dict(unit)) for unit in units) + "]}",
        encoding="utf-8",
    )
    gate = tmp_path / "pilot_completion.json"
    with pytest.raises(ArtifactError, match="required before test access"):
        read_test_units(manifest, gate)
    write_pilot_completion(gate, _completion())
    parsed = read_test_units(manifest, gate)
    assert len(parsed) == 300
    assert {cast(CohortRole, unit.cohort_role) for unit in parsed} == {CohortRole.TEST}


def test_pilot_completion_is_canonical_and_immutable(tmp_path: Path) -> None:
    path = tmp_path / "pilot_completion.json"
    completion = _completion()
    write_pilot_completion(path, completion)
    assert load_pilot_completion(path) == completion
    assert path.read_text(encoding="utf-8") == canonical_json_text(completion.to_dict())
    write_pilot_completion(path, completion)
    with pytest.raises(ArtifactError, match="immutable"):
        write_pilot_completion(
            path,
            _completion(checkpoint_output_identity="5" * 64),
        )


def test_protocol_changes_and_incomplete_accounting_cannot_pass_gate() -> None:
    assert _completion().passed is True
    assert _completion(protocol_changed=True).passed is False
    assert _completion(scenario_configuration_count=899).passed is False
    with pytest.raises(ValidationError, match="does not pass"):
        write_pilot_completion(Path("unused.json"), _completion(failure_count=1))


def test_checkpoint_identity_requires_complete_role_accounting() -> None:
    hashes = [f"{index:064x}" for index in range(900)]
    identity = checkpoint_output_identity(hashes, "pilot")
    assert len(identity) == 64
    with pytest.raises(ValidationError, match="count differs"):
        checkpoint_output_identity(hashes[:-1], "pilot")
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        checkpoint_output_identity([*hashes[:-1], "corrupt"], "pilot")


def test_source_cache_snapshot_detects_mutation(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.json"
    first.write_bytes(b"frozen")
    second.write_text("{}", encoding="utf-8")
    before = file_snapshot_identity((first, second), tmp_path, "pilot")
    second.write_text('{"changed":true}', encoding="utf-8")
    after = file_snapshot_identity((first, second), tmp_path, "pilot")
    assert before != after


def test_campaign_runner_is_import_safe() -> None:
    root = Path(__file__).parents[1]
    scripts = root / "scripts"
    evidence_root = root / "results/phase4/frozen_campaign"
    before = (
        {
            path.name: path.read_bytes()
            for path in evidence_root.iterdir()
            if path.is_file()
        }
        if evidence_root.is_dir()
        else {}
    )
    sys.path.insert(0, str(scripts))
    try:
        namespace = runpy.run_path(
            str(scripts / "run_frozen_motion_campaign.py"),
            run_name="batch4_6_import_safety",
        )
    finally:
        sys.path.remove(str(scripts))
    assert callable(namespace["main"])
    after = (
        {
            path.name: path.read_bytes()
            for path in evidence_root.iterdir()
            if path.is_file()
        }
        if evidence_root.is_dir()
        else {}
    )
    assert after == before


def test_matched_budget_keeps_all_configurations_within_each_family() -> None:
    root = Path(__file__).parents[1]
    scripts = root / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        namespace = runpy.run_path(
            str(scripts / "run_frozen_motion_campaign.py"),
            run_name="batch4_6_budget_test",
        )
    finally:
        sys.path.remove(str(scripts))
    matrix = {
        "budget_targets": [
            {
                "dimension": "byte_ratio",
                "target": 0.48,
                "priority": "primary",
                "included_families": ["family"],
                "maximum_permitted_mismatch": 0.05,
            }
        ]
    }
    rows = [
        {
            "family": "family",
            "method_id": "below",
            "parameter_identity": "1" * 64,
            "byte_ratio": 0.47,
            "keyframe_ratio": 0.1,
        },
        {
            "family": "family",
            "method_id": "above",
            "parameter_identity": "2" * 64,
            "byte_ratio": 0.52,
            "keyframe_ratio": 0.2,
        },
    ]
    selected = namespace["_budget_selections"](matrix, rows)
    assert len(selected) == 1
    assert selected[0]["method_id"] == "below"
    assert selected[0]["actual_budget"] == 0.47
    assert selected[0]["relation"] == "below"
