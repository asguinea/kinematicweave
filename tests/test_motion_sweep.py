"""Focused known-answer tests for the matched-budget sweep engine."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import runpy
from typing import cast

import pytest

from kinematicweave.canonical import canonical_json_text
from kinematicweave.data.motion_sweep_artifacts import (
    MotionSweepTable,
    read_sweep_parquet,
    verify_sweep_parquet,
    write_sweep_parquet,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.experiments.motion_cohort import CohortRole, MotionCohortUnit
from kinematicweave.experiments.motion_sweep import (
    BYTE_RATIO_TARGETS,
    KEYFRAME_RATIO_TARGETS,
    BudgetDimension,
    BudgetRelation,
    CheckpointStatus,
    ConfigurationBudgetRecord,
    ScenarioConfigurationExecution,
    SweepCheckpointState,
    SweepConfiguration,
    SweepFailureRecord,
    checkpoint_path,
    execution_order,
    frozen_parameter_grid,
    load_checkpoint,
    read_ranked_development_prefix,
    select_matched_budgets,
    write_checkpoint,
)


def _unit(rank: int) -> MotionCohortUnit:
    source_id = f"scenario-{rank:03d}"
    return MotionCohortUnit(
        provider_partition="train",
        cohort_role="development",
        selection_rank=rank,
        source_scenario_id=source_id,
        motion_object_path=Path(f"provider/{source_id}/scenario_{source_id}.parquet"),
        motion_size_bytes=100,
        motion_sha256="a" * 64,
        map_object_path=Path(f"provider/{source_id}/log_map_archive_{source_id}.json"),
        map_size_bytes=100,
        map_sha256="b" * 64,
        materialization_cache_key=f"{rank:064x}",
        validation_included=True,
        exclusion_reason=None,
    )


def _execution() -> ScenarioConfigurationExecution:
    point = frozen_parameter_grid()[0]
    return ScenarioConfigurationExecution(
        parameter_identity=point.parameter_identity,
        method_id=point.method_id,
        family=point.family,
        source_scenario_id="source",
        scenario_id="scenario:canonical",
        selection_rank=1,
        trajectory_ids=("trajectory:1",),
        source_sample_count=10,
        valid_sample_count=8,
        retained_keyframe_count=4,
        procedural_segment_count=3,
        serialized_representation_bytes=25,
        raw_canonical_bytes=100,
        exact_adjacent_segments=7,
        motion_summary_json=canonical_json_text({"position": {"mean": 0.1}}),
        semantic_summary_json=canonical_json_text({"overall": {"f1": 0.9}}),
        artifact_summary_json=canonical_json_text({"runs": []}),
        resource_summary_json=canonical_json_text({"total_seconds": 1.0}),
    )


def _scenario_row(parameter_identity: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "parameter_identity": parameter_identity,
        "method_id": "method",
        "family": "family",
        "source_scenario_id": "source",
        "scenario_id": "scenario",
        "selection_rank": 1,
        "trajectory_count": 1,
        "source_sample_count": 10,
        "valid_sample_count": 8,
        "retained_keyframe_count": 4,
        "procedural_segment_count": 3,
        "hold_segment_count": 0,
        "linear_segment_count": 3,
        "hermite_segment_count": 0,
        "serialized_representation_bytes": 25,
        "raw_canonical_bytes": 100,
        "exact_adjacent_segments": 7,
        "byte_ratio": 0.25,
        "keyframe_ratio": 0.5,
        "segment_ratio": 3 / 7,
        "position_mean_m": 0.1,
        "position_p95_m": 0.2,
        "heading_mean_rad": None,
        "velocity_mean_mps": 0.3,
        "semantic_micro_f1": 0.9,
        "encoding_seconds": 0.1,
        "artifact_writing_seconds": 0.2,
        "artifact_verification_seconds": 0.3,
        "replay_evaluation_seconds": 0.4,
        "semantic_redetection_seconds": 0.5,
        "total_seconds": 1.5,
        "peak_process_rss_bytes": 1000,
        "output_disk_bytes": 2000,
        "artifact_summary_json": canonical_json_text({"runs": []}),
        "motion_summary_json": canonical_json_text({"position": {}}),
        "semantic_summary_json": canonical_json_text({"overall": {}}),
    }


def test_frozen_grid_has_exact_order_counts_and_deterministic_identities() -> None:
    first = frozen_parameter_grid()
    second = frozen_parameter_grid()
    assert len(first) == 39
    assert first == second
    assert first[0].method_id == "raw_samples"
    assert first[1].method_id == "exact_adjacent_sample"
    counts: dict[str, int] = {}
    for point in first:
        counts[point.family] = counts.get(point.family, 0) + 1
    assert counts == {
        "raw_samples": 1,
        "exact_adjacent": 1,
        "uniform_linear": 5,
        "uniform_hermite": 5,
        "fixed_interval_linear": 5,
        "rdp_linear": 5,
        "position_bounded_linear": 4,
        "unconstrained_hermite": 4,
        "position_velocity_hybrid": 9,
    }
    assert len({point.parameter_identity for point in first}) == 39


def test_sweep_configuration_rejects_duplicate_parameters() -> None:
    point = frozen_parameter_grid()[0]
    with pytest.raises(ValidationError, match="duplicate parameter"):
        SweepConfiguration((point, point))
    with pytest.raises(ValidationError, match="sorted and unique"):
        SweepConfiguration((point,), byte_ratio_targets=(0.5, 0.25))


def test_execution_order_is_parameter_major_then_rank_major() -> None:
    points = frozen_parameter_grid()[:2]
    configuration = SweepConfiguration(points, subset_size=2)
    order = execution_order(configuration, (_unit(2), _unit(1)))
    assert [(point.method_id, unit.selection_rank) for point, unit in order] == [
        (points[0].method_id, 1),
        (points[0].method_id, 2),
        (points[1].method_id, 1),
        (points[1].method_id, 2),
    ]


def test_ranked_prefix_reader_stops_before_later_cohort_metadata(
    tmp_path: Path,
) -> None:
    units = [
        {
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
        for unit in (_unit(rank) for rank in range(1, 26))
    ]
    prefix = '{"cohort_identity":"x","units":['
    path = tmp_path / "manifest.json"
    path.write_text(
        prefix + ",".join(json.dumps(unit) for unit in units) + ",NOT_VALID_JSON",
        encoding="utf-8",
    )
    parsed = read_ranked_development_prefix(path, 25, chunk_size=31)
    assert [unit.selection_rank for unit in parsed] == list(range(1, 26))


def test_budget_selection_uses_largest_under_then_smallest_above() -> None:
    records = (
        ConfigurationBudgetRecord("family", "low", "a" * 64, 0.20, 0.10),
        ConfigurationBudgetRecord("family", "middle", "b" * 64, 0.40, 0.30),
        ConfigurationBudgetRecord("family", "high", "c" * 64, 0.80, 0.70),
    )
    selected = select_matched_budgets(
        records,
        (0.10, 0.40, 0.50),
        BudgetDimension.BYTE_RATIO,
    )
    assert [row.method_id for row in selected] == ["low", "middle", "middle"]
    assert [row.relation for row in selected] == [
        BudgetRelation.ABOVE,
        BudgetRelation.EQUAL,
        BudgetRelation.BELOW,
    ]
    assert [row.absolute_mismatch for row in selected] == pytest.approx(
        [0.10, 0.0, 0.10]
    )


def test_budget_ties_use_identity_and_do_not_accept_outcome_fields() -> None:
    records = (
        ConfigurationBudgetRecord("family", "later", "f" * 64, 0.5, 0.2),
        ConfigurationBudgetRecord("family", "earlier", "0" * 64, 0.5, 0.2),
    )
    selected = select_matched_budgets(
        records,
        (0.5,),
        BudgetDimension.KEYFRAME_RATIO,
    )
    assert selected[0].method_id == "earlier"
    assert set(selected[0].to_dict()) == {
        "schema_version",
        "dimension",
        "family",
        "target",
        "method_id",
        "parameter_identity",
        "actual_budget",
        "relation",
        "absolute_mismatch",
    }


def test_fixed_budget_target_contracts_are_exact() -> None:
    assert BYTE_RATIO_TARGETS == (0.25, 0.35, 0.50, 0.75, 1.00)
    assert KEYFRAME_RATIO_TARGETS == (0.05, 0.10, 0.20, 0.40, 0.80)


def test_execution_model_is_immutable_and_computes_actual_ratios() -> None:
    execution = _execution()
    assert execution.byte_ratio == 0.25
    assert execution.keyframe_ratio == 0.5
    assert execution.segment_ratio == pytest.approx(3 / 7)
    with pytest.raises(FrozenInstanceError):
        execution.method_id = "changed"  # type: ignore[misc]


def test_checkpoint_creation_reuse_and_completed_immutability(tmp_path: Path) -> None:
    point = frozen_parameter_grid()[0]
    unit = _unit(1)
    checkpoint = SweepCheckpointState.create(
        point,
        unit,
        CheckpointStatus.COMPLETED,
        1,
        {"execution": _execution().to_dict()},
    )
    path = checkpoint_path(tmp_path, point, unit.source_scenario_id)
    write_checkpoint(path, checkpoint)
    write_checkpoint(path, checkpoint)
    assert load_checkpoint(path) == checkpoint
    changed = SweepCheckpointState.create(
        point,
        unit,
        CheckpointStatus.COMPLETED,
        2,
        {"different": True},
    )
    with pytest.raises(ArtifactError, match="immutable"):
        write_checkpoint(path, changed)


def test_failed_checkpoint_can_retry_with_advanced_attempt(tmp_path: Path) -> None:
    point = frozen_parameter_grid()[0]
    unit = _unit(1)
    failure = SweepFailureRecord(
        point.parameter_identity,
        point.method_id,
        unit.source_scenario_id,
        1,
        1,
        "RuntimeError",
        "failed",
    )
    path = checkpoint_path(tmp_path, point, unit.source_scenario_id)
    write_checkpoint(
        path,
        SweepCheckpointState.create(
            point,
            unit,
            CheckpointStatus.FAILED,
            1,
            {"failure": failure.to_dict()},
        ),
    )
    completed = SweepCheckpointState.create(
        point,
        unit,
        CheckpointStatus.COMPLETED,
        2,
        {"execution": _execution().to_dict()},
    )
    write_checkpoint(path, completed)
    assert load_checkpoint(path) == completed


def test_checkpoint_rejects_corruption_and_nonadvanced_retry(tmp_path: Path) -> None:
    point = frozen_parameter_grid()[0]
    unit = _unit(1)
    path = checkpoint_path(tmp_path, point, unit.source_scenario_id)
    failed = SweepCheckpointState.create(
        point,
        unit,
        CheckpointStatus.FAILED,
        1,
        {"failure": {"reason": "test"}},
    )
    write_checkpoint(path, failed)
    with pytest.raises(ArtifactError, match="advance"):
        write_checkpoint(path, failed)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactError):
        load_checkpoint(path)


def test_interrupted_run_preserves_completed_units(tmp_path: Path) -> None:
    points = frozen_parameter_grid()[:2]
    units = (_unit(1), _unit(2))
    completed = SweepCheckpointState.create(
        points[0],
        units[0],
        CheckpointStatus.COMPLETED,
        1,
        {"result": "durable"},
    )
    path = checkpoint_path(tmp_path, points[0], units[0].source_scenario_id)
    write_checkpoint(path, completed)
    missing = [
        (point.method_id, unit.selection_rank)
        for point, unit in execution_order(
            SweepConfiguration(points, subset_size=2),
            units,
        )
        if not checkpoint_path(tmp_path, point, unit.source_scenario_id).exists()
    ]
    assert load_checkpoint(path) == completed
    assert missing == [
        (points[0].method_id, 2),
        (points[1].method_id, 1),
        (points[1].method_id, 2),
    ]


def test_sweep_parquet_round_trip_ordering_bounds_and_corruption(
    tmp_path: Path,
) -> None:
    first_identity = frozen_parameter_grid()[0].parameter_identity
    second_identity = frozen_parameter_grid()[1].parameter_identity
    rows = [_scenario_row(second_identity), _scenario_row(first_identity)]
    rows[0]["source_scenario_id"] = "source-b"
    rows[1]["source_scenario_id"] = "source-a"
    artifact = write_sweep_parquet(
        tmp_path / "scenario.parquet",
        rows,
        MotionSweepTable.SCENARIO_CONFIGURATION,
    )
    table = read_sweep_parquet(
        artifact.path,
        MotionSweepTable.SCENARIO_CONFIGURATION,
        maximum_rows=2,
    )
    keys = [
        (row["parameter_identity"], row["source_scenario_id"])
        for row in table.to_pylist()
    ]
    assert keys == sorted(keys)
    with pytest.raises(ArtifactError, match="bounded"):
        read_sweep_parquet(
            artifact.path,
            MotionSweepTable.SCENARIO_CONFIGURATION,
            maximum_rows=1,
        )
    with artifact.path.open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ArtifactError, match="bytes differ"):
        verify_sweep_parquet(artifact)


def test_sweep_parquet_rejects_duplicate_primary_keys(tmp_path: Path) -> None:
    row = _scenario_row(frozen_parameter_grid()[0].parameter_identity)
    with pytest.raises(SchemaError, match="primary keys"):
        write_sweep_parquet(
            tmp_path / "duplicate.parquet",
            (row, row),
            MotionSweepTable.SCENARIO_CONFIGURATION,
        )


def test_empty_failure_table_and_matched_selection_round_trip(tmp_path: Path) -> None:
    failure = write_sweep_parquet(
        tmp_path / "failures.parquet",
        (),
        MotionSweepTable.FAILURE,
    )
    assert failure.row_count == 0
    selection = select_matched_budgets(
        (
            ConfigurationBudgetRecord(
                "family",
                "method",
                frozen_parameter_grid()[0].parameter_identity,
                0.25,
                0.5,
            ),
        ),
        (0.25,),
        BudgetDimension.BYTE_RATIO,
    )
    artifact = write_sweep_parquet(
        tmp_path / "selection.parquet",
        (selection[0].to_dict(),),
        MotionSweepTable.MATCHED_BUDGET,
    )
    assert artifact.row_count == 1


def test_runner_is_import_safe() -> None:
    path = Path(__file__).parents[1] / "scripts" / "run_motion_sweep.py"
    module = runpy.run_path(str(path), run_name="motion_sweep_import_test")
    assert callable(module["main"])


def test_runner_verifies_accepted_evidence_without_decoding_withheld_units() -> None:
    root = Path(__file__).parents[1]
    path = root / "scripts" / "run_motion_sweep.py"
    module = runpy.run_path(str(path), run_name="motion_sweep_evidence_test")
    units, evidence = module["_verify_inputs"](
        root / "results/phase4/motion_cohort",
        root / "results/phase4/motion_baselines",
        root / "results/phase4/motion_metrics",
    )
    assert len(units) == 25
    assert [unit.selection_rank for unit in units] == list(range(1, 26))
    assert evidence["cohort_identity"] == (
        "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
    )
