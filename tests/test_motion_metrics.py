"""Focused known-answer tests for frozen Phase 4 motion metrics."""

from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import runpy

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from kinematicweave.codecs.exact import encode_trajectory_exact
from kinematicweave.data.motion_metric_artifacts import (
    MotionMetricTable,
    read_metric_parquet,
    verify_metric_parquet,
    write_metric_parquet,
)
from kinematicweave.domain.procedural import ReplayState
from kinematicweave.domain.records import (
    AgentClass,
    OriginType,
    Trajectory,
    TrajectorySampleRecord,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.metrics.motion import (
    ArtifactFileMetric,
    ErrorStatistics,
    EvaluationFailure,
    ScenarioArtifactRuntimeMetrics,
    TrajectoryMotionMetrics,
    descriptive_statistics,
    deterministic_quantile,
    evaluate_trajectory_motion,
    heading_error_rad,
    position_error_m,
    trajectory_macro_statistics,
    velocity_error_mps,
)
from kinematicweave.metrics.semantic import evaluate_trajectory_events


def _sample(
    index: int,
    *,
    valid: bool = True,
    x_m: float | None = None,
    y_m: float = 0.0,
    z_m: float | None = None,
    heading_rad: float | None = 0.0,
    velocity_x_mps: float | None = 1.0,
    velocity_y_mps: float | None = 0.0,
    speed_mps: float | None = 1.0,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        sample_index=index,
        timestamp_ns=index * 100_000_000,
        x_m=float(index) if x_m is None else x_m,
        y_m=y_m,
        z_m=z_m,
        heading_rad=heading_rad,
        velocity_x_mps=velocity_x_mps,
        velocity_y_mps=velocity_y_mps,
        speed_mps=speed_mps,
        acceleration_x_mps2=None,
        acceleration_y_mps2=None,
        is_observed=True,
        is_valid=valid,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _trajectory(
    samples: tuple[TrajectorySampleRecord, ...] | None = None,
) -> Trajectory:
    return Trajectory(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        samples=samples or (_sample(0), _sample(1), _sample(2)),
        origin_type=OriginType.SYNTHETIC,
        quality_flags=("fixture",),
    )


def _state(
    *,
    x_m: float,
    y_m: float,
    z_m: float | None = None,
    heading_rad: float | None = 0.0,
    velocity_x_mps: float | None = 1.0,
    velocity_y_mps: float | None = 0.0,
) -> ReplayState:
    return ReplayState(
        procedural_track_id="procedural-track:test",
        timestamp_ns=0,
        x_m=x_m,
        y_m=y_m,
        z_m=z_m,
        heading_rad=heading_rad,
        velocity_x_mps=velocity_x_mps,
        velocity_y_mps=velocity_y_mps,
    )


def _motion_record() -> TrajectoryMotionMetrics:
    return evaluate_trajectory_motion(
        "raw_samples", "configuration:test", _trajectory(), None
    ).metrics


def test_quantile_policy_interpolates_explicitly_and_empty_is_null() -> None:
    assert deterministic_quantile((), 0.95) is None
    assert deterministic_quantile((0.0, 10.0), 0.95) == pytest.approx(9.5)
    assert deterministic_quantile((3.0, 1.0, 2.0), 0.5) == 2.0
    with pytest.raises(ValidationError):
        deterministic_quantile((1.0,), 1.1)


def test_descriptive_statistics_include_sums_and_empty_contract() -> None:
    empty = descriptive_statistics(())
    assert empty == ErrorStatistics(0, None, None, None, None, 0.0, 0.0)
    result = descriptive_statistics((1.0, 2.0, 3.0, 4.0))
    assert result.mean == 2.5
    assert result.median == 2.5
    assert result.p95 == pytest.approx(3.85)
    assert result.maximum == 4.0
    assert result.sum == 10.0
    assert result.sum_of_squares == 30.0


def test_macro_and_micro_aggregation_are_distinct() -> None:
    vectors = ((0.0,), (10.0, 10.0, 10.0))
    macro = trajectory_macro_statistics(vectors)
    micro = descriptive_statistics(tuple(value for row in vectors for value in row))
    assert macro.mean == 5.0
    assert micro.mean == 7.5


def test_position_error_uses_2d_when_source_z_absent_and_3d_when_present() -> None:
    source_2d = _sample(0, x_m=0.0, y_m=0.0, z_m=None)
    source_3d = _sample(0, x_m=0.0, y_m=0.0, z_m=0.0)
    replay = _state(x_m=3.0, y_m=4.0, z_m=12.0)
    assert position_error_m(source_2d, replay) == 5.0
    assert position_error_m(source_3d, replay) == 13.0
    with pytest.raises(ValidationError, match="elevation"):
        position_error_m(source_3d, _state(x_m=0.0, y_m=0.0))


def test_heading_wrap_and_missing_values() -> None:
    source = _sample(0, heading_rad=math.pi - 0.1)
    replay = _state(x_m=0.0, y_m=0.0, heading_rad=-math.pi + 0.1)
    assert heading_error_rad(source, replay) == pytest.approx(0.2)
    assert heading_error_rad(_sample(0, heading_rad=None), replay) is None


def test_velocity_vector_error_and_missing_values() -> None:
    source = _sample(0, velocity_x_mps=1.0, velocity_y_mps=2.0)
    replay = _state(x_m=0.0, y_m=0.0, velocity_x_mps=4.0, velocity_y_mps=6.0)
    assert velocity_error_mps(source, replay) == 5.0
    assert velocity_error_mps(_sample(0, velocity_x_mps=None), replay) is None


def test_raw_motion_exactness_endpoint_counts_gaps_and_hash_determinism() -> None:
    trajectory = _trajectory(
        (
            _sample(0),
            _sample(1, valid=False),
            _sample(2),
            _sample(3),
        )
    )
    first = evaluate_trajectory_motion(
        "raw_samples", "configuration:test", trajectory, None
    ).metrics
    second = evaluate_trajectory_motion(
        "raw_samples", "configuration:test", trajectory, None
    ).metrics
    assert max(first.position_errors_m) == 0.0
    assert first.endpoint_position_errors_m == (0.0, 0.0, 0.0, 0.0)
    assert first.invalid_source_timestamp_count == 1
    assert first.midpoint_probe_count == 1
    assert first.unexpected_replay_state_count == 0
    assert first.gap_preservation_passed
    assert first.replay_hash == second.replay_hash


def test_exact_codec_has_zero_source_timestamp_error() -> None:
    trajectory = _trajectory()
    track = encode_trajectory_exact(trajectory, AgentClass.VEHICLE)
    result = evaluate_trajectory_motion(
        "exact_adjacent_sample", "configuration:exact", trajectory, track
    ).metrics
    assert result.position_errors_m == (0.0, 0.0, 0.0)
    assert result.endpoint_position_errors_m == (0.0, 0.0)
    assert result.segment_count == 2
    assert result.retained_keyframe_count == 3


def test_raw_semantic_events_are_exact_for_every_event_type() -> None:
    trajectory = _trajectory()
    track = encode_trajectory_exact(trajectory, AgentClass.VEHICLE)
    replay = evaluate_trajectory_motion(
        "raw_samples", "configuration:test", trajectory, None
    ).replay_trajectory
    rows = evaluate_trajectory_events(
        "raw_samples", "configuration:test", trajectory, replay, track
    )
    assert len(rows) == 6
    assert all(row.precision == row.recall == row.f1 == 1.0 for row in rows)
    assert all(row.unmatched_source_count == 0 for row in rows)
    assert all(row.unmatched_replay_count == 0 for row in rows)


def test_models_are_immutable_strict_and_canonical() -> None:
    record = _motion_record()
    assert TrajectoryMotionMetrics.from_dict(record.to_dict()) == record
    assert record.to_canonical_json().endswith("\n")
    with pytest.raises(FrozenInstanceError):
        record.method_id = "changed"  # type: ignore[misc]
    changed = record.to_dict()
    changed["extra"] = 1
    with pytest.raises(ValidationError, match="fields"):
        TrajectoryMotionMetrics.from_dict(changed)
    stats = descriptive_statistics((1.0,))
    assert ErrorStatistics.from_dict(stats.to_dict()) == stats


def test_representation_counts_do_not_fabricate_trajectory_bytes() -> None:
    fields = _motion_record().to_dict()
    assert "serialized_bytes" not in fields
    assert fields["retained_keyframe_count"] == 3
    assert fields["segment_count"] == 0


def test_runtime_categories_and_failure_records_are_explicit() -> None:
    runtime = ScenarioArtifactRuntimeMetrics(
        "raw_samples",
        "configuration:test",
        "scenario:test",
        "completed",
        (ArtifactFileMetric("samples.parquet", 10, "a" * 64, 3, 1),),
        10,
        0,
        10,
        20,
        1.0,
        0.0,
        2.0,
        3.0,
        4.0,
        10.0,
        100,
    )
    assert runtime.to_dict()["artifact_writing_seconds"] == 0.0
    failure = EvaluationFailure(
        "method",
        "configuration",
        "scenario",
        "trajectory",
        "replay",
        "ValidationError",
        "failed",
    )
    assert failure.to_dict()["failure_stage"] == "replay"


def test_metric_parquet_round_trip_ordering_empty_failure_and_corruption(
    tmp_path: Path,
) -> None:
    record = _motion_record().to_dict()
    artifact = write_metric_parquet(
        tmp_path / "motion.parquet",
        (record,),
        MotionMetricTable.TRAJECTORY_MOTION,
    )
    table = read_metric_parquet(
        artifact.path,
        MotionMetricTable.TRAJECTORY_MOTION,
        maximum_rows=1,
    )
    assert table.to_pylist() == [record]
    assert pq.ParquetFile(artifact.path).metadata.num_row_groups == 1
    failure = write_metric_parquet(
        tmp_path / "failures.parquet",
        (),
        MotionMetricTable.EVALUATION_FAILURE,
    )
    assert failure.row_count == 0
    with pytest.raises(ArtifactError, match="already exists"):
        write_metric_parquet(
            artifact.path, (record,), MotionMetricTable.TRAJECTORY_MOTION
        )
    with artifact.path.open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ArtifactError, match="bytes differ"):
        verify_metric_parquet(artifact)


def test_metric_table_rejects_duplicate_primary_keys(tmp_path: Path) -> None:
    record = _motion_record().to_dict()
    with pytest.raises(SchemaError, match="primary keys"):
        write_metric_parquet(
            tmp_path / "duplicate.parquet",
            (record, record),
            MotionMetricTable.TRAJECTORY_MOTION,
        )


def test_campaign_script_is_import_safe() -> None:
    path = Path(__file__).parents[1] / "scripts" / "run_motion_metrics.py"
    module = runpy.run_path(str(path), run_name="motion_metrics_import_test")
    assert callable(module["main"])
