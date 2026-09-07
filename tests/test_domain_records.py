"""Tests for canonical scenario, agent, and trajectory records."""

import ast
from dataclasses import FrozenInstanceError, fields, replace
import json
import math
from pathlib import Path
import runpy
import subprocess
import sys
from typing import Any

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    canonical_schema_names,
    get_arrow_schema,
    get_polars_schema,
    get_schema_definition,
    validate_arrow_schema,
)
from kinematicweave.domain import records as records_module
from kinematicweave.domain.records import (
    AgentClass,
    AgentRecord,
    CoordinateFrameRecord,
    OriginType,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
    agent_record_to_dict,
    coordinate_frame_record_to_dict,
    scenario_record_to_dict,
    trajectory_sample_record_to_dict,
    trajectory_to_sample_dicts,
    validate_scenario_bundle,
)
from kinematicweave.errors import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECORDS_PATH = PROJECT_ROOT / "src" / "kinematicweave" / "domain" / "records.py"

SCENARIO_ID = "scenario:demo"
FRAME_ID = "frame:demo"
AGENT_ID = "agent:demo:001"
TRAJECTORY_ID = "trajectory:demo:001"


def _scenario(**overrides: Any) -> ScenarioRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "dataset_id": "synthetic",
        "dataset_version": "1.0",
        "split_name": "smoke",
        "city_or_region": None,
        "source_scenario_id": "source-001",
        "start_time_ns": 0,
        "end_time_ns": 10,
        "coordinate_frame_id": FRAME_ID,
        "origin_x_m": 0.0,
        "origin_y_m": 0.0,
        "origin_z_m": None,
        "source_crs": None,
        "has_elevation": False,
        "agent_count": 1,
        "source_map_available": False,
        "quality_flags": (),
        "adapter_name": "test_adapter",
        "adapter_version": "1.0",
        "source_checksum": None,
    }
    values.update(overrides)
    return ScenarioRecord(**values)


def _frame(**overrides: Any) -> CoordinateFrameRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "coordinate_frame_id": FRAME_ID,
        "parent_frame_id": None,
        "frame_type": "local_cartesian",
        "origin_x_m": 0.0,
        "origin_y_m": 0.0,
        "origin_z_m": None,
        "axis_convention": "x_forward_y_left_z_up",
        "distance_unit": "m",
        "angle_unit": "rad",
        "timestamp_unit": "ns",
        "source_crs": None,
        "has_elevation": False,
        "transform_to_parent_4x4": None,
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return CoordinateFrameRecord(**values)


def _agent(**overrides: Any) -> AgentRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "agent_id": AGENT_ID,
        "source_agent_id": "001",
        "agent_class": AgentClass.VEHICLE,
        "length_m": 4.5,
        "width_m": 1.75,
        "height_m": None,
        "first_time_ns": 0,
        "last_time_ns": 10,
        "sample_count": 2,
        "is_focal_agent": True,
        "is_ego_agent": False,
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return AgentRecord(**values)


def _sample(**overrides: Any) -> TrajectorySampleRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "agent_id": AGENT_ID,
        "trajectory_id": TRAJECTORY_ID,
        "sample_index": 0,
        "timestamp_ns": 0,
        "x_m": 0.0,
        "y_m": 1.0,
        "z_m": None,
        "heading_rad": None,
        "velocity_x_mps": None,
        "velocity_y_mps": None,
        "speed_mps": None,
        "acceleration_x_mps2": None,
        "acceleration_y_mps2": None,
        "is_observed": True,
        "is_valid": True,
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return TrajectorySampleRecord(**values)


def _trajectory(
    samples: object | None = None,
    **overrides: Any,
) -> Trajectory:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "agent_id": AGENT_ID,
        "trajectory_id": TRAJECTORY_ID,
        "samples": (
            _sample(),
            _sample(sample_index=1, timestamp_ns=10, x_m=2.0),
        )
        if samples is None
        else samples,
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return Trajectory(**values)


def _bundle() -> tuple[
    ScenarioRecord,
    CoordinateFrameRecord,
    AgentRecord,
    Trajectory,
]:
    return _scenario(), _frame(), _agent(), _trajectory()


def _assert_plain_json_value(value: object) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for item in value:
            _assert_plain_json_value(item)
        return
    if isinstance(value, dict):
        assert all(isinstance(key, str) for key in value)
        for item in value.values():
            _assert_plain_json_value(item)
        return
    pytest.fail(f"non-JSON-compatible value: {type(value).__name__}")


def test_enum_values_and_module_exports_are_exact() -> None:
    assert tuple(value.value for value in AgentClass) == (
        "vehicle",
        "pedestrian",
        "cyclist",
        "other_dynamic",
        "unknown",
    )
    assert tuple(value.value for value in OriginType) == (
        "source_ground_truth",
        "source_observation",
        "synthetic",
        "oracle_converted",
        "inferred",
        "decoded",
        "edited",
        "derived_metric",
    )
    assert records_module.__all__ == [
        "AgentClass",
        "AgentRecord",
        "CoordinateFrameRecord",
        "OriginType",
        "ScenarioRecord",
        "Trajectory",
        "TrajectorySampleRecord",
        "agent_record_to_dict",
        "coordinate_frame_record_to_dict",
        "scenario_record_to_dict",
        "trajectory_sample_record_to_dict",
        "trajectory_to_sample_dicts",
        "validate_scenario_bundle",
    ]


@pytest.mark.parametrize(
    ("record_type", "expected_names", "factory"),
    [
        (
            ScenarioRecord,
            (
                "scenario_id",
                "dataset_id",
                "dataset_version",
                "split_name",
                "city_or_region",
                "source_scenario_id",
                "start_time_ns",
                "end_time_ns",
                "coordinate_frame_id",
                "origin_x_m",
                "origin_y_m",
                "origin_z_m",
                "source_crs",
                "has_elevation",
                "agent_count",
                "source_map_available",
                "quality_flags",
                "adapter_name",
                "adapter_version",
                "source_checksum",
            ),
            _scenario,
        ),
        (
            CoordinateFrameRecord,
            (
                "scenario_id",
                "coordinate_frame_id",
                "parent_frame_id",
                "frame_type",
                "origin_x_m",
                "origin_y_m",
                "origin_z_m",
                "axis_convention",
                "distance_unit",
                "angle_unit",
                "timestamp_unit",
                "source_crs",
                "has_elevation",
                "transform_to_parent_4x4",
                "origin_type",
                "quality_flags",
            ),
            _frame,
        ),
        (
            AgentRecord,
            (
                "scenario_id",
                "agent_id",
                "source_agent_id",
                "agent_class",
                "length_m",
                "width_m",
                "height_m",
                "first_time_ns",
                "last_time_ns",
                "sample_count",
                "is_focal_agent",
                "is_ego_agent",
                "origin_type",
                "quality_flags",
            ),
            _agent,
        ),
        (
            TrajectorySampleRecord,
            (
                "scenario_id",
                "agent_id",
                "trajectory_id",
                "sample_index",
                "timestamp_ns",
                "x_m",
                "y_m",
                "z_m",
                "heading_rad",
                "velocity_x_mps",
                "velocity_y_mps",
                "speed_mps",
                "acceleration_x_mps2",
                "acceleration_y_mps2",
                "is_observed",
                "is_valid",
                "origin_type",
                "quality_flags",
            ),
            _sample,
        ),
        (
            Trajectory,
            (
                "scenario_id",
                "agent_id",
                "trajectory_id",
                "samples",
                "origin_type",
                "quality_flags",
            ),
            _trajectory,
        ),
    ],
)
def test_dataclasses_are_frozen_slotted_and_have_exact_fields(
    record_type: Any,
    expected_names: tuple[str, ...],
    factory: Any,
) -> None:
    record = factory()
    assert tuple(field.name for field in fields(record_type)) == expected_names
    assert not hasattr(record, "__dict__")
    with pytest.raises(FrozenInstanceError):
        record.quality_flags = ()


def test_sequence_inputs_are_copied_and_normalized() -> None:
    flags = [" first ", "second"]
    scenario = _scenario(quality_flags=flags)
    flags.append("later")
    assert scenario.quality_flags == ("first", "second")

    transform = [1 if index % 5 == 0 else 0 for index in range(16)]
    frame = _frame(transform_to_parent_4x4=transform)
    transform[0] = 9
    assert frame.transform_to_parent_4x4 == tuple(
        1.0 if index % 5 == 0 else 0.0 for index in range(16)
    )

    samples = [_sample(), _sample(sample_index=1, timestamp_ns=10)]
    trajectory = _trajectory(samples)
    samples.clear()
    assert trajectory.sample_count == 2
    assert isinstance(trajectory.samples, tuple)

    sample_tuple = (
        _sample(),
        _sample(sample_index=1, timestamp_ns=10),
    )
    tuple_trajectory = _trajectory(sample_tuple)
    assert tuple_trajectory.samples == sample_tuple
    assert tuple_trajectory.samples is not sample_tuple


def test_valid_complete_scenario_and_text_trimming() -> None:
    checksum = "a" * 64
    record = _scenario(
        scenario_id="  scenario:demo  ",
        dataset_id=" dataset ",
        dataset_version=" 1.2 ",
        split_name=" smoke ",
        city_or_region=" City ",
        source_scenario_id=" source ",
        coordinate_frame_id=" frame:demo ",
        source_crs=" CRS ",
        adapter_name=" adapter ",
        adapter_version=" 2 ",
        source_checksum=f" {checksum} ",
        quality_flags=[" flag-one ", "flag-two"],
    )
    assert record.scenario_id == SCENARIO_ID
    assert record.dataset_id == "dataset"
    assert record.dataset_version == "1.2"
    assert record.split_name == "smoke"
    assert record.city_or_region == "City"
    assert record.source_scenario_id == "source"
    assert record.source_crs == "CRS"
    assert record.adapter_name == "adapter"
    assert record.adapter_version == "2"
    assert record.source_checksum == checksum
    assert record.quality_flags == ("flag-one", "flag-two")


@pytest.mark.parametrize("field_name", ["scenario_id", "coordinate_frame_id"])
def test_scenario_rejects_invalid_identifiers(field_name: str) -> None:
    with pytest.raises(ValidationError, match="identifier"):
        _scenario(**{field_name: "invalid"})


@pytest.mark.parametrize("field_name", ["start_time_ns", "end_time_ns"])
@pytest.mark.parametrize("value", [True, 1.5, "1", 2**63, -(2**63) - 1])
def test_scenario_rejects_unsupported_timestamps(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match=field_name):
        _scenario(**{field_name: value})


def test_scenario_rejects_start_after_end() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        _scenario(start_time_ns=11)


@pytest.mark.parametrize("field_name", ["origin_x_m", "origin_y_m", "origin_z_m"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_scenario_rejects_nonfinite_coordinates(
    field_name: str,
    value: float,
) -> None:
    overrides: dict[str, object] = {field_name: value}
    if field_name == "origin_z_m":
        overrides["has_elevation"] = True
    with pytest.raises(ValidationError, match="finite"):
        _scenario(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"has_elevation": False, "origin_z_m": 1.0},
        {"has_elevation": True, "origin_z_m": None},
    ],
)
def test_scenario_rejects_inconsistent_elevation(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="origin_z_m"):
        _scenario(**overrides)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("agent_count", True),
        ("agent_count", -1),
        ("agent_count", 2**31),
        ("has_elevation", 1),
        ("source_map_available", 0),
    ],
)
def test_scenario_rejects_invalid_counts_and_booleans(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match=field_name):
        _scenario(**{field_name: value})


@pytest.mark.parametrize(
    "quality_flags",
    [
        "single",
        ["valid", " valid "],
        ["valid", "  "],
        ["valid", 1],
    ],
)
def test_scenario_rejects_invalid_quality_flags(quality_flags: object) -> None:
    with pytest.raises(ValidationError, match="quality_flags"):
        _scenario(quality_flags=quality_flags)


@pytest.mark.parametrize("checksum", ["A" * 64, "a" * 63, "g" * 64, ""])
def test_scenario_rejects_invalid_checksum(checksum: str) -> None:
    with pytest.raises(ValidationError, match="source_checksum"):
        _scenario(source_checksum=checksum)


def test_valid_coordinate_frames_with_and_without_transform() -> None:
    assert _frame().transform_to_parent_4x4 is None
    transform = tuple(1 if index % 5 == 0 else 0 for index in range(16))
    frame = _frame(
        transform_to_parent_4x4=transform,
        origin_type="source_observation",
    )
    assert frame.transform_to_parent_4x4 == tuple(float(value) for value in transform)
    assert frame.origin_type is OriginType.SOURCE_OBSERVATION


def test_coordinate_frame_rejects_transform_length_and_nonfinite_values() -> None:
    with pytest.raises(ValidationError, match="exactly 16"):
        _frame(transform_to_parent_4x4=[0.0] * 15)
    transform = [0.0] * 16
    transform[7] = math.nan
    with pytest.raises(ValidationError, match="finite"):
        _frame(transform_to_parent_4x4=transform)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("distance_unit", "metre"),
        ("angle_unit", "degree"),
        ("timestamp_unit", "us"),
    ],
)
def test_coordinate_frame_rejects_invalid_units(
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError, match=field_name):
        _frame(**{field_name: value})


def test_coordinate_frame_rejects_self_parent_and_elevation_mismatch() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        _frame(parent_frame_id=FRAME_ID)
    with pytest.raises(ValidationError, match="origin_z_m"):
        _frame(has_elevation=True, origin_z_m=None)
    with pytest.raises(ValidationError, match="origin_z_m"):
        _frame(has_elevation=False, origin_z_m=1.0)


def test_valid_agent_and_every_agent_class() -> None:
    for agent_class in AgentClass:
        record = _agent(agent_class=agent_class.value, quality_flags=[" short_track "])
        assert record.agent_class is agent_class
        assert record.quality_flags == ("short_track",)


def test_agent_rejects_invalid_class_and_dimensions() -> None:
    with pytest.raises(ValidationError, match="agent_class"):
        _agent(agent_class="bus")
    for field_name in ("length_m", "width_m", "height_m"):
        for value in (0.0, -1.0, math.nan, math.inf):
            with pytest.raises(ValidationError, match=field_name):
                _agent(**{field_name: value})


@pytest.mark.parametrize("sample_count", [0, -1, True, 2**31])
def test_agent_rejects_invalid_sample_count(sample_count: object) -> None:
    with pytest.raises(ValidationError, match="sample_count"):
        _agent(sample_count=sample_count)


def test_agent_rejects_first_after_last_and_non_bool_flags() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        _agent(first_time_ns=11)
    with pytest.raises(ValidationError, match="is_focal_agent"):
        _agent(is_focal_agent=1)
    with pytest.raises(ValidationError, match="is_ego_agent"):
        _agent(is_ego_agent=0)


def test_valid_complete_and_optional_trajectory_samples() -> None:
    complete = _sample(
        x_m=1,
        y_m=2,
        z_m=3,
        heading_rad=0,
        velocity_x_mps=4,
        velocity_y_mps=5,
        speed_mps=6,
        acceleration_x_mps2=7,
        acceleration_y_mps2=8,
        origin_type="source_ground_truth",
        quality_flags=[" observed "],
    )
    assert (
        complete.x_m,
        complete.y_m,
        complete.z_m,
        complete.heading_rad,
    ) == (1.0, 2.0, 3.0, 0.0)
    assert complete.origin_type is OriginType.SOURCE_GROUND_TRUTH
    assert complete.quality_flags == ("observed",)

    optional = _sample()
    assert optional.z_m is None
    assert optional.heading_rad is None
    assert optional.velocity_x_mps is None
    assert optional.velocity_y_mps is None
    assert optional.speed_mps is None
    assert optional.acceleration_x_mps2 is None
    assert optional.acceleration_y_mps2 is None


@pytest.mark.parametrize(
    "field_name",
    [
        "x_m",
        "y_m",
        "z_m",
        "heading_rad",
        "velocity_x_mps",
        "velocity_y_mps",
        "speed_mps",
        "acceleration_x_mps2",
        "acceleration_y_mps2",
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_sample_rejects_nonfinite_numeric_values(
    field_name: str,
    value: float,
) -> None:
    with pytest.raises(ValidationError, match="finite"):
        _sample(**{field_name: value})


def test_sample_heading_boundaries_and_negative_speed() -> None:
    assert _sample(heading_rad=-math.pi).heading_rad == -math.pi
    below_pi = math.nextafter(math.pi, -math.inf)
    assert _sample(heading_rad=below_pi).heading_rad == below_pi
    with pytest.raises(ValidationError, match="heading_rad"):
        _sample(heading_rad=math.pi)
    with pytest.raises(ValidationError, match="speed_mps"):
        _sample(speed_mps=-0.1)


def test_sample_rejects_boolean_index_and_non_bool_state_flags() -> None:
    with pytest.raises(ValidationError, match="sample_index"):
        _sample(sample_index=True)
    with pytest.raises(ValidationError, match="is_observed"):
        _sample(is_observed=1)
    with pytest.raises(ValidationError, match="is_valid"):
        _sample(is_valid=0)


def test_valid_trajectory_and_derived_properties() -> None:
    trajectory = _trajectory(
        [
            _sample(z_m=None),
            _sample(sample_index=1, timestamp_ns=10, z_m=2),
        ],
        origin_type="decoded",
        quality_flags=[" complete "],
    )
    assert trajectory.sample_count == 2
    assert trajectory.start_time_ns == 0
    assert trajectory.end_time_ns == 10
    assert trajectory.has_elevation is True
    assert trajectory.origin_type is OriginType.DECODED
    assert trajectory.quality_flags == ("complete",)


def test_trajectory_rejects_empty_and_reference_mismatches() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        _trajectory([])
    mismatch_cases = (
        _sample(scenario_id="scenario:other"),
        _sample(agent_id="agent:other"),
        _sample(trajectory_id="trajectory:other"),
    )
    for sample in mismatch_cases:
        with pytest.raises(ValidationError, match="does not match"):
            _trajectory([sample])


def test_trajectory_rejects_indices_and_timestamp_order() -> None:
    with pytest.raises(ValidationError, match="indices"):
        _trajectory([_sample(sample_index=1)])
    with pytest.raises(ValidationError, match="duplicate"):
        _trajectory(
            [
                _sample(),
                _sample(sample_index=1, timestamp_ns=0),
            ]
        )
    with pytest.raises(ValidationError, match="strictly increasing"):
        _trajectory(
            [
                _sample(timestamp_ns=2),
                _sample(sample_index=1, timestamp_ns=1),
            ]
        )


def test_repeated_trajectory_construction_is_deterministic() -> None:
    first = _trajectory()
    second = _trajectory()
    assert first == second
    assert hash(first) == hash(second)


def test_dictionary_conversions_match_schema_order_and_plain_values() -> None:
    scenario, frame, agent, trajectory = _bundle()
    conversions = (
        (
            scenario_record_to_dict(scenario),
            CanonicalSchemaName.SCENARIO_MANIFEST,
        ),
        (
            coordinate_frame_record_to_dict(frame),
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        ),
        (
            agent_record_to_dict(agent),
            CanonicalSchemaName.AGENT_METADATA,
        ),
    )
    for converted, schema_name in conversions:
        assert list(converted) == get_arrow_schema(schema_name).names
        _assert_plain_json_value(converted)
        json.dumps(converted, allow_nan=False)

    sample_dicts = trajectory_to_sample_dicts(trajectory)
    assert len(sample_dicts) == 2
    for converted in sample_dicts:
        assert (
            list(converted)
            == get_arrow_schema(CanonicalSchemaName.TRAJECTORY_SAMPLES).names
        )
        _assert_plain_json_value(converted)
        json.dumps(converted, allow_nan=False)

    assert conversions[1][0]["transform_to_parent_4x4"] is None
    assert conversions[0][0]["origin_z_m"] is None
    assert conversions[2][0]["agent_class"] == "vehicle"
    assert sample_dicts[0]["origin_type"] == "synthetic"


def test_dictionary_conversions_are_fresh_and_mutation_safe() -> None:
    scenario, frame, agent, trajectory = _bundle()
    first_scenario = scenario_record_to_dict(scenario)
    second_scenario = scenario_record_to_dict(scenario)
    assert first_scenario == second_scenario
    assert first_scenario is not second_scenario
    quality_flags = first_scenario["quality_flags"]
    assert isinstance(quality_flags, list)
    quality_flags.append("caller")
    assert scenario.quality_flags == ()
    assert scenario_record_to_dict(scenario)["quality_flags"] == []

    first_samples = trajectory_to_sample_dicts(trajectory)
    second_samples = trajectory_to_sample_dicts(trajectory)
    assert first_samples == second_samples
    assert first_samples is not second_samples
    first_samples[0]["x_m"] = 999.0
    assert trajectory.samples[0].x_m == 0.0

    assert coordinate_frame_record_to_dict(frame) is not (
        coordinate_frame_record_to_dict(frame)
    )
    assert agent_record_to_dict(agent) is not agent_record_to_dict(agent)
    assert trajectory_sample_record_to_dict(trajectory.samples[0]) is not (
        trajectory_sample_record_to_dict(trajectory.samples[0])
    )


def test_valid_and_empty_scenario_bundles() -> None:
    scenario, frame, agent, trajectory = _bundle()
    validate_scenario_bundle(scenario, frame, [agent], [trajectory])
    validate_scenario_bundle(
        _scenario(agent_count=0),
        _frame(),
        [],
        [],
    )


@pytest.mark.parametrize(
    ("scenario", "frame", "message"),
    [
        (_scenario(), _frame(scenario_id="scenario:other"), "scenario_id"),
        (
            _scenario(),
            _frame(coordinate_frame_id="frame:other"),
            "coordinate_frame_id",
        ),
        (_scenario(), _frame(origin_x_m=1.0), "origin_x_m"),
        (_scenario(source_crs="A"), _frame(source_crs="B"), "source_crs"),
        (
            _scenario(has_elevation=True, origin_z_m=1.0),
            _frame(),
            "has_elevation",
        ),
    ],
)
def test_bundle_rejects_frame_mismatches(
    scenario: ScenarioRecord,
    frame: CoordinateFrameRecord,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        validate_scenario_bundle(scenario, frame, [], [])


def test_bundle_rejects_agent_count_and_duplicate_identifiers() -> None:
    scenario, frame, agent, trajectory = _bundle()
    with pytest.raises(ValidationError, match="agent count"):
        validate_scenario_bundle(scenario, frame, [], [])

    with pytest.raises(ValidationError, match="agent identifiers"):
        validate_scenario_bundle(
            replace(scenario, agent_count=2),
            frame,
            [agent, agent],
            [trajectory],
        )

    second_agent_id = "agent:demo:002"
    second_agent = replace(agent, agent_id=second_agent_id)
    second_trajectory = _trajectory(
        [
            _sample(agent_id=second_agent_id),
            _sample(agent_id=second_agent_id, sample_index=1, timestamp_ns=10),
        ],
        agent_id=second_agent_id,
    )
    with pytest.raises(ValidationError, match="trajectory identifiers"):
        validate_scenario_bundle(
            replace(scenario, agent_count=2),
            frame,
            [agent, second_agent],
            [trajectory, second_trajectory],
        )


def test_bundle_rejects_missing_extra_and_unknown_agent_trajectories() -> None:
    scenario, frame, agent, trajectory = _bundle()
    with pytest.raises(ValidationError, match="exactly one"):
        validate_scenario_bundle(scenario, frame, [agent], [])

    extra = _trajectory(
        [_sample(trajectory_id="trajectory:demo:002")],
        trajectory_id="trajectory:demo:002",
    )
    with pytest.raises(ValidationError, match="exactly one"):
        validate_scenario_bundle(scenario, frame, [agent], [trajectory, extra])

    with pytest.raises(ValidationError, match="unknown agent"):
        validate_scenario_bundle(
            replace(scenario, agent_count=0),
            frame,
            [],
            [trajectory],
        )


@pytest.mark.parametrize(
    ("agent", "message"),
    [
        (_agent(sample_count=3), "sample_count"),
        (_agent(first_time_ns=-1), "first_time_ns"),
        (_agent(last_time_ns=11), "last_time_ns"),
    ],
)
def test_bundle_rejects_agent_sample_metadata_mismatch(
    agent: AgentRecord,
    message: str,
) -> None:
    scenario, frame, _, trajectory = _bundle()
    with pytest.raises(ValidationError, match=message):
        validate_scenario_bundle(scenario, frame, [agent], [trajectory])


def test_bundle_rejects_scenario_membership_and_interval_errors() -> None:
    scenario, frame, agent, trajectory = _bundle()
    with pytest.raises(ValidationError, match="agent scenario_id"):
        validate_scenario_bundle(
            scenario,
            frame,
            [replace(agent, scenario_id="scenario:other")],
            [trajectory],
        )
    other_trajectory = _trajectory(
        [
            _sample(scenario_id="scenario:other"),
            _sample(
                scenario_id="scenario:other",
                sample_index=1,
                timestamp_ns=10,
            ),
        ],
        scenario_id="scenario:other",
    )
    with pytest.raises(ValidationError, match="trajectory scenario_id"):
        validate_scenario_bundle(scenario, frame, [agent], [other_trajectory])
    with pytest.raises(ValidationError, match="starts before"):
        validate_scenario_bundle(
            replace(scenario, start_time_ns=1),
            frame,
            [agent],
            [trajectory],
        )
    with pytest.raises(ValidationError, match="ends after"):
        validate_scenario_bundle(
            replace(scenario, end_time_ns=9),
            frame,
            [agent],
            [trajectory],
        )


def test_bundle_rejects_elevation_in_non_elevation_scenario() -> None:
    scenario, frame, agent, _ = _bundle()
    trajectory = _trajectory(
        [
            _sample(z_m=1.0),
            _sample(sample_index=1, timestamp_ns=10, z_m=None),
        ]
    )
    with pytest.raises(ValidationError, match="elevation"):
        validate_scenario_bundle(scenario, frame, [agent], [trajectory])


def test_bundle_copies_and_does_not_mutate_supplied_sequences() -> None:
    scenario, frame, agent, trajectory = _bundle()
    agents = [agent]
    trajectories = [trajectory]
    agents_before = list(agents)
    trajectories_before = list(trajectories)
    validate_scenario_bundle(scenario, frame, agents, trajectories)
    assert agents == agents_before
    assert trajectories == trajectories_before


def test_records_integrate_with_all_batch_21_schemas_without_writes(
    tmp_path: Path,
) -> None:
    assert canonical_schema_names()[:5] == (
        CanonicalSchemaName.SCENARIO_MANIFEST,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        CanonicalSchemaName.AGENT_METADATA,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    )
    for name in canonical_schema_names()[:5]:
        assert get_schema_definition(name).version == "1.0"

    scenario, frame, agent, trajectory = _bundle()
    validate_scenario_bundle(scenario, frame, [agent], [trajectory])
    rows_by_schema: dict[CanonicalSchemaName, list[dict[str, object]]] = {
        CanonicalSchemaName.SCENARIO_MANIFEST: [scenario_record_to_dict(scenario)],
        CanonicalSchemaName.COORDINATE_FRAME_METADATA: [
            coordinate_frame_record_to_dict(frame)
        ],
        CanonicalSchemaName.AGENT_METADATA: [agent_record_to_dict(agent)],
        CanonicalSchemaName.TRAJECTORY_SAMPLES: list(
            trajectory_to_sample_dicts(trajectory)
        ),
    }

    for name, rows in rows_by_schema.items():
        arrow_table = pa.Table.from_pylist(rows, schema=get_arrow_schema(name))
        validate_arrow_schema(arrow_table.schema, name)
        assert arrow_table.to_pylist() == rows
        polars_frame = pl.DataFrame(rows, schema=get_polars_schema(name), strict=True)
        assert polars_frame.height == len(rows)
        assert polars_frame.to_dicts() == rows

    assert tuple(tmp_path.iterdir()) == ()


def test_production_module_imports_only_standard_and_kinematicweave_modules() -> None:
    tree = ast.parse(RECORDS_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert imported_roots == {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "math",
        "re",
        "kinematicweave",
    } - {"__future__"}


def test_isolated_import_loads_no_forbidden_library() -> None:
    code = (
        "import sys; import kinematicweave.domain.records; "
        "forbidden=('numpy','pyarrow','polars','argoverse','geopandas',"
        "'shapely','torch','rerun'); "
        "print(','.join(name for name in forbidden if name in sys.modules))"
    )
    completed = subprocess.run(
        (sys.executable, "-c", code),
        check=True,
        shell=False,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == "\n"
    assert completed.stderr == ""


def test_import_performs_no_writes_or_subprocess_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbidden_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("records import attempted a forbidden side effect")

    monkeypatch.setattr(subprocess, "run", forbidden_call)
    monkeypatch.setattr(subprocess, "Popen", forbidden_call)
    before = tuple(tmp_path.iterdir())
    monkeypatch.chdir(tmp_path)
    assert records_module.__file__ is not None
    runpy.run_path(records_module.__file__)
    assert tuple(tmp_path.iterdir()) == before
