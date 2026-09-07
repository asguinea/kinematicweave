"""Tests for the velocity-aware cubic-Hermite codec."""

from dataclasses import FrozenInstanceError, replace
import importlib
import inspect
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from kinematicweave.codecs.exact import (
    cubic_hermite_basis,
    cubic_hermite_basis_derivative,
    evaluate_segment,
    replay_track,
)
from kinematicweave.codecs.hermite import (
    ENCODER_NAME,
    ENCODER_VERSION,
    HermiteCodecConfig,
    encode_canonical_parquet_hermite,
    encode_scenario_hermite,
    encode_trajectory_hermite,
    hermite_encoder_parameters_identity,
    summarize_hermite_tape,
    validate_hermite_replay,
    verify_hermite_artifacts,
)
from kinematicweave.codecs.piecewise_linear import (
    PiecewiseLinearCodecConfig,
    encode_scenario_piecewise_linear,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    procedural_segments_to_table,
    scenario_records_to_table,
    trajectories_to_table,
)
from kinematicweave.data.procedural_artifacts import materialize_procedural_tape
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    schema_fingerprint,
)
from kinematicweave.data.synthetic import (
    SyntheticScenarioKind,
    build_synthetic_scenario,
)
from kinematicweave.domain.procedural import (
    ProceduralPrimitiveType,
    ProceduralSegment,
)
from kinematicweave.domain.records import (
    AgentClass,
    OriginType,
    Trajectory,
    TrajectorySampleRecord,
)
from kinematicweave.errors import ArtifactError, ValidationError


def _sample(
    index: int,
    *,
    timestamp_ns: int | None = None,
    valid: bool = True,
    x_m: float | None = None,
    y_m: float = 0.0,
    z_m: float | None = None,
    heading_rad: float | None = 0.0,
    velocity_x_mps: float | None = 1.0,
    velocity_y_mps: float | None = 0.0,
) -> TrajectorySampleRecord:
    return TrajectorySampleRecord(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        sample_index=index,
        timestamp_ns=index * 1_000_000_000 if timestamp_ns is None else timestamp_ns,
        x_m=float(index) if x_m is None else x_m,
        y_m=y_m,
        z_m=z_m,
        heading_rad=heading_rad,
        velocity_x_mps=velocity_x_mps,
        velocity_y_mps=velocity_y_mps,
        speed_mps=None,
        acceleration_x_mps2=None,
        acceleration_y_mps2=None,
        is_observed=True,
        is_valid=valid,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def _trajectory(samples: tuple[TrajectorySampleRecord, ...]) -> Trajectory:
    return Trajectory(
        scenario_id="scenario:test",
        agent_id="agent:test",
        trajectory_id="trajectory:test",
        samples=samples,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=("fixture",),
    )


def test_hermite_basis_and_derivatives_have_exact_endpoint_contracts() -> None:
    assert cubic_hermite_basis(0.0) == (1.0, 0.0, 0.0, 0.0)
    assert cubic_hermite_basis(1.0) == (0.0, 0.0, 1.0, 0.0)
    assert cubic_hermite_basis_derivative(0.0) == (0.0, 1.0, 0.0, 0.0)
    assert cubic_hermite_basis_derivative(1.0) == (0.0, 0.0, 0.0, 1.0)
    with pytest.raises(ValidationError):
        cubic_hermite_basis(math.nan)
    with pytest.raises(ValidationError):
        cubic_hermite_basis_derivative(1.1)


@pytest.mark.parametrize(
    "value",
    (True, False, -0.1, math.inf, -math.inf, math.nan, "0.1", None),
)
def test_configuration_rejects_invalid_position_bounds(value: object) -> None:
    with pytest.raises(ValidationError):
        HermiteCodecConfig(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "primitives",
    (
        (),
        ("hold", "linear"),
        ("hold", "linear", "linear"),
        ("hold", "linear", "quadratic"),
        "hold",
    ),
)
def test_configuration_requires_the_unique_complete_vocabulary(
    primitives: object,
) -> None:
    with pytest.raises(ValidationError):
        HermiteCodecConfig(candidate_primitives=primitives)  # type: ignore[arg-type]


def test_configuration_is_frozen_normalized_ordered_and_fully_identified() -> None:
    default = HermiteCodecConfig()
    reordered = HermiteCodecConfig(
        candidate_primitives=(
            ProceduralPrimitiveType.CUBIC_HERMITE,
            ProceduralPrimitiveType.LINEAR,
            ProceduralPrimitiveType.HOLD,
        )
    )

    assert default.maximum_position_error_m == 0.1
    assert default.candidate_primitives[-1] is ProceduralPrimitiveType.CUBIC_HERMITE
    assert hermite_encoder_parameters_identity(default) != (
        hermite_encoder_parameters_identity(reordered)
    )
    with pytest.raises(FrozenInstanceError):
        default.maximum_position_error_m = 1.0  # type: ignore[misc]


def _manual_hermite_segment() -> ProceduralSegment:
    return ProceduralSegment(
        tape_id="tape:test",
        procedural_track_id="procedural-track:test",
        segment_id="procedural-segment:test",
        run_index=0,
        segment_index=0,
        primitive_type=ProceduralPrimitiveType.CUBIC_HERMITE,
        source_start_sample_index=0,
        source_end_sample_index=2,
        start_time_ns=0,
        end_time_ns=2_000_000_000,
        start_x_m=0.0,
        start_y_m=0.0,
        start_z_m=0.0,
        end_x_m=2.0,
        end_y_m=2.0,
        end_z_m=4.0,
        start_heading_rad=math.pi - 0.2,
        end_heading_rad=-math.pi + 0.2,
        start_velocity_x_mps=0.0,
        start_velocity_y_mps=2.0,
        end_velocity_x_mps=2.0,
        end_velocity_y_mps=0.0,
        parameter_values=(),
        origin_type=OriginType.INFERRED,
        quality_flags=(),
    )


def test_analytic_replay_preserves_endpoints_and_endpoint_derivatives() -> None:
    segment = _manual_hermite_segment()
    start = evaluate_segment(segment, 0)
    middle = evaluate_segment(segment, 1_000_000_000)
    end = evaluate_segment(segment, 2_000_000_000)

    assert start is not None and middle is not None and end is not None
    assert (start.x_m, start.y_m, start.velocity_x_mps, start.velocity_y_mps) == (
        0.0,
        0.0,
        0.0,
        2.0,
    )
    assert (end.x_m, end.y_m, end.velocity_x_mps, end.velocity_y_mps) == (
        2.0,
        2.0,
        2.0,
        0.0,
    )
    assert (middle.x_m, middle.y_m) == pytest.approx((0.5, 1.5))
    assert (middle.velocity_x_mps, middle.velocity_y_mps) == pytest.approx((1.0, 1.0))
    assert middle.z_m == 2.0
    assert abs(middle.heading_rad or 0.0) == pytest.approx(math.pi)


def test_hermite_segment_validation_requires_velocity_and_positive_duration() -> None:
    segment = _manual_hermite_segment()
    with pytest.raises(ValidationError, match="complete x/y velocity"):
        replace(segment, start_velocity_x_mps=None)
    with pytest.raises(ValidationError, match="duration"):
        replace(segment, end_time_ns=segment.start_time_ns)


def test_cubic_source_curve_uses_one_hermite_segment_and_beats_linear_velocity() -> (
    None
):
    samples = tuple(
        _sample(
            index,
            x_m=(index / 4.0) ** 3,
            velocity_x_mps=3.0 * (index / 4.0) ** 2 / 4.0,
        )
        for index in range(5)
    )
    trajectory = _trajectory(samples)
    track = encode_trajectory_hermite(
        trajectory,
        AgentClass.VEHICLE,
        HermiteCodecConfig(0.0),
    )
    validation = validate_hermite_replay(track, trajectory, HermiteCodecConfig(0.0))

    assert track.segment_count == 1
    assert track.segments[0].primitive_type is ProceduralPrimitiveType.CUBIC_HERMITE
    assert max(validation.position_errors_m) < 1.0e-15
    assert max(validation.velocity_errors_mps) < 1.0e-15


def test_missing_endpoint_velocity_excludes_only_hermite_candidates() -> None:
    trajectory = _trajectory(
        tuple(
            _sample(
                index,
                x_m=float(index),
                velocity_x_mps=None,
                velocity_y_mps=None,
            )
            for index in range(4)
        )
    )
    track = encode_trajectory_hermite(
        trajectory,
        AgentClass.VEHICLE,
        HermiteCodecConfig(0.0),
    )

    assert track.segment_count == 1
    assert track.segments[0].primitive_type is ProceduralPrimitiveType.LINEAR


def test_objective_prefers_lower_position_error_before_velocity_error() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0, velocity_x_mps=0.0),
            _sample(1, x_m=1.0, velocity_x_mps=1.0),
            _sample(2, x_m=2.0, velocity_x_mps=2.0),
        )
    )
    track = encode_trajectory_hermite(
        trajectory,
        AgentClass.VEHICLE,
        HermiteCodecConfig(1.0),
    )

    assert track.segment_count == 1
    assert track.segments[0].primitive_type is ProceduralPrimitiveType.LINEAR


def test_primitive_order_breaks_an_exact_objective_tie() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0, velocity_x_mps=1.0),
            _sample(1, x_m=1.0, velocity_x_mps=1.0),
            _sample(2, x_m=2.0, velocity_x_mps=1.0),
        )
    )
    default = encode_trajectory_hermite(trajectory, AgentClass.VEHICLE)
    hermite_first = encode_trajectory_hermite(
        trajectory,
        AgentClass.VEHICLE,
        HermiteCodecConfig(
            candidate_primitives=(
                ProceduralPrimitiveType.CUBIC_HERMITE,
                ProceduralPrimitiveType.LINEAR,
                ProceduralPrimitiveType.HOLD,
            )
        ),
    )

    assert default.segments[0].primitive_type is ProceduralPrimitiveType.LINEAR
    assert (
        hermite_first.segments[0].primitive_type
        is ProceduralPrimitiveType.CUBIC_HERMITE
    )


def test_irregular_timestamps_drive_hermite_position_and_velocity() -> None:
    trajectory = _trajectory(
        (
            _sample(0, timestamp_ns=0, x_m=0.0, velocity_x_mps=0.0),
            _sample(
                1,
                timestamp_ns=1_000_000_000,
                x_m=0.0625,
                velocity_x_mps=0.1875,
            ),
            _sample(
                2,
                timestamp_ns=4_000_000_000,
                x_m=4.0,
                velocity_x_mps=3.0,
            ),
        )
    )
    track = encode_trajectory_hermite(
        trajectory,
        AgentClass.VEHICLE,
        HermiteCodecConfig(
            0.0,
            (
                ProceduralPrimitiveType.CUBIC_HERMITE,
                ProceduralPrimitiveType.LINEAR,
                ProceduralPrimitiveType.HOLD,
            ),
        ),
    )
    validation = validate_hermite_replay(track, trajectory, HermiteCodecConfig(0.0))

    assert track.segment_count == 1
    assert track.segments[0].primitive_type is ProceduralPrimitiveType.CUBIC_HERMITE
    assert max(validation.position_errors_m) < 1.0e-15
    assert max(validation.velocity_errors_mps) < 1.0e-15


def test_unstable_overshooting_hermite_candidate_is_rejected() -> None:
    trajectory = _trajectory(
        (
            _sample(0, x_m=0.0, velocity_x_mps=1.0e308),
            _sample(1, x_m=1.0, velocity_x_mps=1.0),
            _sample(2, x_m=2.0, velocity_x_mps=1.0e308),
        )
    )
    track = encode_trajectory_hermite(
        trajectory,
        AgentClass.VEHICLE,
        HermiteCodecConfig(
            0.0,
            (
                ProceduralPrimitiveType.CUBIC_HERMITE,
                ProceduralPrimitiveType.LINEAR,
                ProceduralPrimitiveType.HOLD,
            ),
        ),
    )

    assert track.segment_count == 2
    for segment in track.segments:
        state = evaluate_segment(
            segment,
            (segment.start_time_ns + segment.end_time_ns) // 2,
        )
        assert state is not None
        assert math.isfinite(state.x_m)
        assert state.velocity_x_mps is not None
        assert math.isfinite(state.velocity_x_mps)


def test_gaps_singletons_and_elevation_transitions_are_preserved() -> None:
    trajectory = _trajectory(
        (
            _sample(0),
            _sample(1),
            _sample(2, valid=False),
            _sample(3),
            _sample(4, z_m=1.0),
            _sample(5, z_m=2.0),
        )
    )
    track = encode_trajectory_hermite(trajectory, AgentClass.VEHICLE)
    validation = validate_hermite_replay(track, trajectory)

    assert track.run_count == 2
    assert replay_track(track, trajectory.samples[2].timestamp_ns) is None
    assert any(
        segment.start_time_ns == segment.end_time_ns for segment in track.segments
    )
    assert validation.invalid_gap_samples_with_state == 0
    assert validation.run_endpoints_exact
    assert validation.retained_breakpoints_exact


@pytest.mark.parametrize("kind", tuple(SyntheticScenarioKind))
def test_candidate_superset_never_uses_more_segments_than_piecewise_linear(
    kind: SyntheticScenarioKind,
) -> None:
    scenario = build_synthetic_scenario(kind)
    piecewise = encode_scenario_piecewise_linear(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        PiecewiseLinearCodecConfig(0.1),
        source_validation_report_identity="synthetic-validation:v1",
    )
    hermite = encode_scenario_hermite(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        HermiteCodecConfig(0.1),
        source_validation_report_identity="synthetic-validation:v1",
    )

    assert hermite.segment_count <= piecewise.segment_count
    assert summarize_hermite_tape(
        hermite,
        scenario.trajectories,
    )["retained_breakpoints_exact"]


def test_encoding_is_deterministic_and_schema_fingerprint_is_unchanged() -> None:
    scenario = build_synthetic_scenario(SyntheticScenarioKind.LEFT_TURN)
    first = encode_scenario_hermite(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        source_validation_report_identity="synthetic-validation:v1",
    )
    second = encode_scenario_hermite(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        source_validation_report_identity="synthetic-validation:v1",
    )
    table = procedural_segments_to_table(
        tuple(segment for track in first.tracks for segment in track.segments)
    )

    assert first == second
    assert first.encoder_name == ENCODER_NAME
    assert first.encoder_version == ENCODER_VERSION
    assert "cubic_hermite" in table.column("primitive_type").to_pylist()
    assert schema_fingerprint(CanonicalSchemaName.PROCEDURAL_SEGMENTS) == (
        "e994f5d7c2f2cfc21ddbb06b44c076d04e94491fe2f201bc4b2a76bfa089cd86"
    )


def test_scenario_artifacts_round_trip_and_detect_corruption(tmp_path: Path) -> None:
    scenario = build_synthetic_scenario(SyntheticScenarioKind.LEFT_TURN)
    tape = encode_scenario_hermite(
        scenario.scenario,
        scenario.coordinate_frame,
        scenario.agents,
        scenario.trajectories,
        source_validation_report_identity="synthetic-validation:v1",
    )
    artifacts = materialize_procedural_tape(
        tmp_path,
        "generated",
        "hermite:test",
        tape,
    )
    verified, summary = verify_hermite_artifacts(
        tmp_path,
        artifacts,
        scenario.trajectories,
        expected_tape=tape,
    )

    assert verified == tape
    assert summary == summarize_hermite_tape(tape, scenario.trajectories)
    summary_path = tmp_path / artifacts.codec_summary.relative_path
    summary_path.write_bytes(summary_path.read_bytes() + b"x")
    with pytest.raises(ArtifactError, match=r"size|checksum"):
        verify_hermite_artifacts(
            tmp_path,
            artifacts,
            scenario.trajectories,
        )


def test_bounded_parquet_api_uses_record_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = build_synthetic_scenario(SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED)
    tables = {
        CanonicalSchemaName.SCENARIO_MANIFEST: scenario_records_to_table(
            (scenario.scenario,)
        ),
        CanonicalSchemaName.COORDINATE_FRAME_METADATA: (
            coordinate_frame_records_to_table((scenario.coordinate_frame,))
        ),
        CanonicalSchemaName.AGENT_METADATA: agent_records_to_table(scenario.agents),
        CanonicalSchemaName.TRAJECTORY_SAMPLES: trajectories_to_table(
            scenario.trajectories
        ),
    }
    calls: list[tuple[CanonicalSchemaName, int]] = []

    def batches(
        repository_root: Path,
        relative_paths: Any,
        expected: CanonicalSchemaName,
        *,
        batch_size: int,
    ) -> Any:
        del repository_root, relative_paths
        calls.append((expected, batch_size))
        return iter(tables[expected].to_batches(max_chunksize=2))

    piecewise_module = importlib.import_module("kinematicweave.codecs.piecewise_linear")
    monkeypatch.setattr(piecewise_module, "iter_canonical_parquet_batches", batches)
    tape = encode_canonical_parquet_hermite(
        Path.cwd(),
        scenario_paths=("scenario.parquet",),
        coordinate_frame_paths=("frame.parquet",),
        agent_paths=("agents.parquet",),
        trajectory_sample_paths=("samples.parquet",),
        source_validation_report_identity="synthetic-validation:v1",
        batch_size=2,
    )
    source = inspect.getsource(encode_canonical_parquet_hermite)

    assert tape.track_count == len(scenario.trajectories)
    assert [item[0] for item in calls] == [
        CanonicalSchemaName.SCENARIO_MANIFEST,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        CanonicalSchemaName.AGENT_METADATA,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    ]
    assert "to_pylist" not in source
    assert "collect" not in source


def test_import_is_quiet_and_has_no_data_side_effects(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import kinematicweave.codecs.hermite"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == ""
    assert completed.stderr == ""
    assert tuple(tmp_path.iterdir()) == ()
