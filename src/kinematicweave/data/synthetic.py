"""Deterministic exact-ground-truth synthetic motion fixtures."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
from itertools import pairwise
import json
import math
from pathlib import Path
from typing import Any

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_canonical_json,
)
from kinematicweave.data.parquet_io import (
    CanonicalParquetArtifact,
    agent_records_to_table,
    atomic_write_canonical_parquet,
    coordinate_frame_records_to_table,
    read_canonical_parquet_table,
    scenario_records_to_table,
    trajectories_to_table,
    verify_canonical_parquet_artifact,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.records import (
    AgentClass,
    AgentRecord,
    CoordinateFrameRecord,
    OriginType,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
    validate_scenario_bundle,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import make_identifier
from kinematicweave.paths import normalize_relative_path

__all__ = [
    "SyntheticDataset",
    "SyntheticDatasetArtifacts",
    "SyntheticScenario",
    "SyntheticScenarioKind",
    "build_synthetic_dataset",
    "build_synthetic_scenario",
    "materialize_synthetic_dataset",
    "synthetic_agents",
    "synthetic_coordinate_frames",
    "synthetic_dataset_index_to_dict",
    "synthetic_scenario_kinds",
    "synthetic_scenarios",
    "synthetic_trajectories",
    "verify_synthetic_dataset_artifacts",
]

_SCHEMA_VERSION = "1.0"
_DATASET_ID = "synthetic_kinematicweave"
_DATASET_VERSION = "1.0"
_REGULAR_TIMESTAMPS = tuple(index * 1_000_000_000 for index in range(11))
_IRREGULAR_TIMESTAMPS = (
    0,
    400_000_000,
    1_100_000_000,
    2_500_000_000,
    4_000_000_000,
    6_200_000_000,
    9_000_000_000,
)
_INDEX_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "scenario_count",
    "agent_count",
    "trajectory_count",
    "scenarios",
)
_INDEX_SCENARIO_FIELDS = (
    "kind",
    "scenario_id",
    "coordinate_frame_id",
    "agent_ids",
    "trajectory_ids",
    "start_time_ns",
    "end_time_ns",
    "has_elevation",
)


class SyntheticScenarioKind(StrEnum):
    """Approved deterministic synthetic scenario kinds."""

    STRAIGHT_CONSTANT_SPEED = "straight_constant_speed"
    ACCELERATION_DECELERATION = "acceleration_deceleration"
    STOP = "stop"
    LEFT_TURN = "left_turn"
    RIGHT_TURN = "right_turn"
    IRREGULAR_SAMPLING = "irregular_sampling"
    MISSING_GAP = "missing_gap"
    T_JUNCTION = "t_junction"
    FOUR_WAY_JUNCTION = "four_way_junction"
    MERGE = "merge"
    SPLIT = "split"
    DISCONNECTED_PATHS = "disconnected_paths"
    CROSSING_DISCONNECTED = "crossing_disconnected"
    GRADE_SEPARATED_CROSSING = "grade_separated_crossing"
    REROUTE_AVAILABLE = "reroute_available"
    REROUTE_UNAVAILABLE = "reroute_unavailable"


@dataclass(frozen=True, slots=True)
class SyntheticScenario:
    """One validated deterministic synthetic scenario bundle."""

    kind: SyntheticScenarioKind
    scenario: ScenarioRecord
    coordinate_frame: CoordinateFrameRecord
    agents: tuple[AgentRecord, ...]
    trajectories: tuple[Trajectory, ...]

    def __post_init__(self) -> None:
        """Copy child sequences and validate the canonical bundle."""
        if not isinstance(self.kind, SyntheticScenarioKind):
            raise ValidationError("kind must use SyntheticScenarioKind")
        if not isinstance(self.scenario, ScenarioRecord):
            raise ValidationError("scenario must be a ScenarioRecord")
        if not isinstance(self.coordinate_frame, CoordinateFrameRecord):
            raise ValidationError("coordinate_frame must be a CoordinateFrameRecord")
        agents_value: object = self.agents
        trajectories_value: object = self.trajectories
        if isinstance(agents_value, (str, bytes)) or not isinstance(
            agents_value, Sequence
        ):
            raise ValidationError("agents must be a finite non-string sequence")
        if isinstance(trajectories_value, (str, bytes)) or not isinstance(
            trajectories_value, Sequence
        ):
            raise ValidationError("trajectories must be a finite non-string sequence")
        agents = tuple(agent for agent in agents_value)
        trajectories = tuple(trajectory for trajectory in trajectories_value)
        if any(not isinstance(agent, AgentRecord) for agent in agents):
            raise ValidationError("agents must contain AgentRecord values")
        if any(not isinstance(trajectory, Trajectory) for trajectory in trajectories):
            raise ValidationError("trajectories must contain Trajectory values")
        object.__setattr__(self, "agents", agents)
        object.__setattr__(self, "trajectories", trajectories)
        validate_scenario_bundle(
            self.scenario,
            self.coordinate_frame,
            agents,
            trajectories,
        )


@dataclass(frozen=True, slots=True)
class SyntheticDataset:
    """Ordered immutable collection of synthetic correctness scenarios."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    scenarios: tuple[SyntheticScenario, ...]

    def __post_init__(self) -> None:
        """Copy scenarios and enforce dataset-wide identity and uniqueness."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        if self.dataset_id != _DATASET_ID:
            raise ValidationError(f"dataset_id must equal {_DATASET_ID!r}")
        if self.dataset_version != _DATASET_VERSION:
            raise ValidationError(f"dataset_version must equal {_DATASET_VERSION!r}")
        scenarios_value: object = self.scenarios
        if isinstance(scenarios_value, (str, bytes)) or not isinstance(
            scenarios_value, Sequence
        ):
            raise ValidationError("scenarios must be a finite non-string sequence")
        scenarios = tuple(scenario for scenario in scenarios_value)
        if not scenarios:
            raise ValidationError("scenarios must contain at least one scenario")
        if any(not isinstance(scenario, SyntheticScenario) for scenario in scenarios):
            raise ValidationError("scenarios must contain SyntheticScenario values")
        kinds = tuple(scenario.kind for scenario in scenarios)
        if len(kinds) != len(set(kinds)):
            raise ValidationError("scenario kinds must be unique")
        identifiers = tuple(scenario.scenario.scenario_id for scenario in scenarios)
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError("scenario identifiers must be unique")
        for scenario in scenarios:
            if (
                scenario.scenario.dataset_id != self.dataset_id
                or scenario.scenario.dataset_version != self.dataset_version
            ):
                raise ValidationError(
                    "every scenario must use the dataset identifier and version"
                )
        object.__setattr__(self, "scenarios", scenarios)


@dataclass(frozen=True, slots=True)
class SyntheticDatasetArtifacts:
    """Physical artifact metadata for one materialized synthetic dataset."""

    dataset_id: str
    dataset_version: str
    scenario_index: WrittenArtifact
    scenario_manifest: CanonicalParquetArtifact
    coordinate_frame_metadata: CanonicalParquetArtifact
    agent_metadata: CanonicalParquetArtifact
    trajectory_samples: CanonicalParquetArtifact

    def __post_init__(self) -> None:
        """Validate dataset identity and each artifact's canonical schema."""
        if self.dataset_id != _DATASET_ID:
            raise ValidationError(f"dataset_id must equal {_DATASET_ID!r}")
        if self.dataset_version != _DATASET_VERSION:
            raise ValidationError(f"dataset_version must equal {_DATASET_VERSION!r}")
        if not isinstance(self.scenario_index, WrittenArtifact):
            raise ValidationError("scenario_index must be a WrittenArtifact")
        expected = (
            (
                "scenario_manifest",
                self.scenario_manifest,
                CanonicalSchemaName.SCENARIO_MANIFEST,
            ),
            (
                "coordinate_frame_metadata",
                self.coordinate_frame_metadata,
                CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            ),
            (
                "agent_metadata",
                self.agent_metadata,
                CanonicalSchemaName.AGENT_METADATA,
            ),
            (
                "trajectory_samples",
                self.trajectory_samples,
                CanonicalSchemaName.TRAJECTORY_SAMPLES,
            ),
        )
        for field_name, artifact, schema_name in expected:
            if not isinstance(artifact, CanonicalParquetArtifact):
                raise ValidationError(
                    f"{field_name} must be a CanonicalParquetArtifact"
                )
            if (
                artifact.schema_name is not schema_name
                or artifact.schema_version != _SCHEMA_VERSION
            ):
                raise ValidationError(
                    f"{field_name} uses an incompatible canonical schema"
                )


def synthetic_scenario_kinds() -> tuple[SyntheticScenarioKind, ...]:
    """Return all synthetic kinds in their fixed canonical order."""
    return tuple(SyntheticScenarioKind)


def _normalize_kind(value: SyntheticScenarioKind | str) -> SyntheticScenarioKind:
    if isinstance(value, SyntheticScenarioKind):
        return value
    if isinstance(value, str):
        try:
            return SyntheticScenarioKind(value)
        except ValueError:
            pass
    raise ValidationError(f"unknown synthetic scenario kind: {value!r}")


def _normalize_heading(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def _derive_kinematics(
    timestamps: tuple[int, ...],
    positions: tuple[tuple[float, float], ...],
) -> tuple[
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
]:
    count = len(positions)
    if count < 2:
        raise ValidationError("synthetic tracks must contain at least two samples")
    velocities: list[tuple[float, float]] = []
    for index in range(count):
        left = max(0, index - 1)
        right = min(count - 1, index + 1)
        elapsed = (timestamps[right] - timestamps[left]) / 1_000_000_000
        velocities.append(
            (
                (positions[right][0] - positions[left][0]) / elapsed,
                (positions[right][1] - positions[left][1]) / elapsed,
            )
        )
    headings: list[float] = []
    previous_heading = 0.0
    for velocity_x, velocity_y in velocities:
        if math.hypot(velocity_x, velocity_y) > 1e-12:
            previous_heading = _normalize_heading(math.atan2(velocity_y, velocity_x))
        headings.append(previous_heading)
    accelerations: list[tuple[float, float]] = []
    for index in range(count):
        left = max(0, index - 1)
        right = min(count - 1, index + 1)
        elapsed = (timestamps[right] - timestamps[left]) / 1_000_000_000
        accelerations.append(
            (
                (velocities[right][0] - velocities[left][0]) / elapsed,
                (velocities[right][1] - velocities[left][1]) / elapsed,
            )
        )
    return (
        tuple(value[0] for value in velocities),
        tuple(value[1] for value in velocities),
        tuple(headings),
        tuple(value[0] for value in accelerations),
        tuple(value[1] for value in accelerations),
    )


def _track(
    kind: SyntheticScenarioKind,
    role: str,
    positions: Sequence[tuple[float, float]],
    *,
    timestamps: Sequence[int] = _REGULAR_TIMESTAMPS,
    z_values: Sequence[float | None] | None = None,
    velocity_x: Sequence[float] | None = None,
    velocity_y: Sequence[float] | None = None,
    headings: Sequence[float] | None = None,
    acceleration_x: Sequence[float] | None = None,
    acceleration_y: Sequence[float] | None = None,
    observed: Sequence[bool] | None = None,
) -> tuple[AgentRecord, Trajectory]:
    timestamps_tuple = tuple(value for value in timestamps)
    positions_tuple = tuple((float(x), float(y)) for x, y in positions)
    if len(timestamps_tuple) != len(positions_tuple) or not positions_tuple:
        raise ValidationError("track timestamps and positions must have equal length")
    derived = _derive_kinematics(timestamps_tuple, positions_tuple)
    velocity_x_tuple = tuple(derived[0] if velocity_x is None else velocity_x)
    velocity_y_tuple = tuple(derived[1] if velocity_y is None else velocity_y)
    headings_tuple = tuple(derived[2] if headings is None else headings)
    acceleration_x_tuple = tuple(
        derived[3] if acceleration_x is None else acceleration_x
    )
    acceleration_y_tuple = tuple(
        derived[4] if acceleration_y is None else acceleration_y
    )
    z_tuple = (
        tuple(None for _ in positions_tuple)
        if z_values is None
        else tuple(value for value in z_values)
    )
    observed_tuple = (
        tuple(True for _ in positions_tuple)
        if observed is None
        else tuple(value for value in observed)
    )
    values = (
        velocity_x_tuple,
        velocity_y_tuple,
        headings_tuple,
        acceleration_x_tuple,
        acceleration_y_tuple,
        z_tuple,
        observed_tuple,
    )
    if any(len(value) != len(positions_tuple) for value in values):
        raise ValidationError("track attribute lengths must match positions")

    scenario_id = make_identifier("scenario", "synthetic", kind.value)
    agent_id = make_identifier("agent", "synthetic", kind.value, role)
    trajectory_id = make_identifier(
        "trajectory",
        "synthetic",
        kind.value,
        role,
    )
    samples = tuple(
        TrajectorySampleRecord(
            scenario_id=scenario_id,
            agent_id=agent_id,
            trajectory_id=trajectory_id,
            sample_index=index,
            timestamp_ns=timestamp,
            x_m=positions_tuple[index][0],
            y_m=positions_tuple[index][1],
            z_m=z_tuple[index],
            heading_rad=_normalize_heading(float(headings_tuple[index])),
            velocity_x_mps=float(velocity_x_tuple[index]),
            velocity_y_mps=float(velocity_y_tuple[index]),
            speed_mps=math.hypot(
                velocity_x_tuple[index],
                velocity_y_tuple[index],
            ),
            acceleration_x_mps2=float(acceleration_x_tuple[index]),
            acceleration_y_mps2=float(acceleration_y_tuple[index]),
            is_observed=observed_tuple[index],
            is_valid=observed_tuple[index],
            origin_type=OriginType.SYNTHETIC,
            quality_flags=() if observed_tuple[index] else ("missing_observation",),
        )
        for index, timestamp in enumerate(timestamps_tuple)
    )
    trajectory = Trajectory(
        scenario_id=scenario_id,
        agent_id=agent_id,
        trajectory_id=trajectory_id,
        samples=samples,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )
    agent = AgentRecord(
        scenario_id=scenario_id,
        agent_id=agent_id,
        source_agent_id=None,
        agent_class=AgentClass.VEHICLE,
        length_m=4.5,
        width_m=1.8,
        height_m=1.5,
        first_time_ns=trajectory.start_time_ns,
        last_time_ns=trajectory.end_time_ns,
        sample_count=trajectory.sample_count,
        is_focal_agent=False,
        is_ego_agent=False,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )
    return agent, trajectory


def _straight_track(
    kind: SyntheticScenarioKind,
    role: str = "vehicle",
    *,
    timestamps: Sequence[int] = _REGULAR_TIMESTAMPS,
    observed: Sequence[bool] | None = None,
) -> tuple[AgentRecord, Trajectory]:
    positions = tuple((timestamp / 1_000_000_000, 0.0) for timestamp in timestamps)
    count = len(positions)
    return _track(
        kind,
        role,
        positions,
        timestamps=timestamps,
        velocity_x=(1.0,) * count,
        velocity_y=(0.0,) * count,
        headings=(0.0,) * count,
        acceleration_x=(0.0,) * count,
        acceleration_y=(0.0,) * count,
        observed=observed,
    )


def _acceleration_track(
    kind: SyntheticScenarioKind,
) -> tuple[AgentRecord, Trajectory]:
    speeds = (0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 1.6, 1.2, 0.8, 0.4, 0.0)
    positions = [0.0]
    for previous, current in pairwise(speeds):
        positions.append(positions[-1] + (previous + current) / 2.0)
    accelerations = tuple(
        speeds[min(index + 1, 10)] - speeds[max(index - 1, 0)] for index in range(11)
    )
    accelerations = tuple(
        value / (1.0 if index in {0, 10} else 2.0)
        for index, value in enumerate(accelerations)
    )
    return _track(
        kind,
        "vehicle",
        tuple((position, 0.0) for position in positions),
        velocity_x=speeds,
        velocity_y=(0.0,) * 11,
        headings=(0.0,) * 11,
        acceleration_x=accelerations,
        acceleration_y=(0.0,) * 11,
    )


def _stop_track(kind: SyntheticScenarioKind) -> tuple[AgentRecord, Trajectory]:
    positions = (0.0, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    speeds = (1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    return _track(
        kind,
        "vehicle",
        tuple((position, 0.0) for position in positions),
        velocity_x=speeds,
        velocity_y=(0.0,) * 11,
        headings=(0.0,) * 11,
        acceleration_x=(0.0, 0.0, -1.0, -1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0),
        acceleration_y=(0.0,) * 11,
    )


def _turn_track(
    kind: SyntheticScenarioKind,
    direction: float,
) -> tuple[AgentRecord, Trajectory]:
    radius = 5.0
    angular_rate = math.pi / 20.0
    angles = tuple(index * math.pi / 20.0 for index in range(11))
    positions = tuple(
        (
            radius * math.sin(angle),
            direction * radius * (1.0 - math.cos(angle)),
        )
        for angle in angles
    )
    headings = tuple(direction * angle for angle in angles)
    speed = radius * angular_rate
    velocity_x = tuple(speed * math.cos(angle) for angle in angles)
    velocity_y = tuple(direction * speed * math.sin(angle) for angle in angles)
    acceleration_x = tuple(
        -radius * angular_rate**2 * math.sin(angle) for angle in angles
    )
    acceleration_y = tuple(
        direction * radius * angular_rate**2 * math.cos(angle) for angle in angles
    )
    return _track(
        kind,
        "vehicle",
        positions,
        velocity_x=velocity_x,
        velocity_y=velocity_y,
        headings=headings,
        acceleration_x=acceleration_x,
        acceleration_y=acceleration_y,
    )


def _junction_tracks(
    kind: SyntheticScenarioKind,
) -> tuple[tuple[AgentRecord, Trajectory], ...]:
    if kind is SyntheticScenarioKind.T_JUNCTION:
        south = tuple((0.0, float(index - 5)) for index in range(6))
        west_turn = south + tuple((-float(index), 0.0) for index in range(1, 6))
        east_turn = south + tuple((float(index), 0.0) for index in range(1, 6))
        west_east = tuple((float(index - 5), 0.0) for index in range(11))
        return (
            _track(kind, "south_to_west", west_turn),
            _track(kind, "south_to_east", east_turn),
            _track(kind, "west_to_east", west_east),
        )
    return (
        _track(
            kind, "northbound", tuple((0.0, float(index - 5)) for index in range(11))
        ),
        _track(
            kind, "southbound", tuple((0.0, float(5 - index)) for index in range(11))
        ),
        _track(
            kind, "eastbound", tuple((float(index - 5), 0.0) for index in range(11))
        ),
        _track(
            kind, "westbound", tuple((float(5 - index), 0.0) for index in range(11))
        ),
    )


def _multi_track(
    kind: SyntheticScenarioKind,
) -> tuple[tuple[AgentRecord, Trajectory], ...]:
    x_values = tuple(float(index - 5) for index in range(11))
    if kind is SyntheticScenarioKind.MERGE:
        return tuple(
            _track(
                kind,
                role,
                tuple(
                    (
                        x,
                        side * (1.0 - min(index, 5) / 5.0) * 2.0,
                    )
                    for index, x in enumerate(x_values)
                ),
            )
            for role, side in (("lower_approach", -1.0), ("upper_approach", 1.0))
        )
    if kind is SyntheticScenarioKind.SPLIT:
        return tuple(
            _track(
                kind,
                role,
                tuple(
                    (
                        x,
                        side * max(index - 5, 0) / 5.0 * 2.0,
                    )
                    for index, x in enumerate(x_values)
                ),
            )
            for role, side in (("lower_branch", -1.0), ("upper_branch", 1.0))
        )
    if kind is SyntheticScenarioKind.DISCONNECTED_PATHS:
        return (
            _track(kind, "lower", tuple((x, -5.0) for x in x_values)),
            _track(kind, "upper", tuple((x, 5.0) for x in x_values)),
        )
    if kind in {
        SyntheticScenarioKind.CROSSING_DISCONNECTED,
        SyntheticScenarioKind.GRADE_SEPARATED_CROSSING,
    }:
        elevation = kind is SyntheticScenarioKind.GRADE_SEPARATED_CROSSING
        return (
            _track(
                kind,
                "horizontal",
                tuple((x, 0.0) for x in x_values),
                z_values=(0.0,) * 11 if elevation else None,
            ),
            _track(
                kind,
                "vertical",
                tuple((0.0, x) for x in x_values),
                z_values=(5.0,) * 11 if elevation else None,
            ),
        )
    if kind is SyntheticScenarioKind.REROUTE_AVAILABLE:
        detour_y = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0)
        return (
            _track(kind, "direct_route", tuple((x, 0.0) for x in x_values)),
            _track(
                kind,
                "alternate_route",
                tuple(zip(x_values, detour_y, strict=True)),
            ),
        )
    return (
        _track(
            kind,
            "only_route",
            tuple(
                (x, 0.5 * math.sin(index * math.pi / 10.0))
                for index, x in enumerate(x_values)
            ),
        ),
    )


def _tracks_for_kind(
    kind: SyntheticScenarioKind,
) -> tuple[tuple[AgentRecord, Trajectory], ...]:
    if kind is SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED:
        return (_straight_track(kind),)
    if kind is SyntheticScenarioKind.ACCELERATION_DECELERATION:
        return (_acceleration_track(kind),)
    if kind is SyntheticScenarioKind.STOP:
        return (_stop_track(kind),)
    if kind is SyntheticScenarioKind.LEFT_TURN:
        return (_turn_track(kind, 1.0),)
    if kind is SyntheticScenarioKind.RIGHT_TURN:
        return (_turn_track(kind, -1.0),)
    if kind is SyntheticScenarioKind.IRREGULAR_SAMPLING:
        return (_straight_track(kind, timestamps=_IRREGULAR_TIMESTAMPS),)
    if kind is SyntheticScenarioKind.MISSING_GAP:
        observed = tuple(index not in {4, 5} for index in range(11))
        return (_straight_track(kind, observed=observed),)
    if kind in {
        SyntheticScenarioKind.T_JUNCTION,
        SyntheticScenarioKind.FOUR_WAY_JUNCTION,
    }:
        return _junction_tracks(kind)
    return _multi_track(kind)


def build_synthetic_scenario(
    kind: SyntheticScenarioKind | str,
) -> SyntheticScenario:
    """Build one fresh deterministic synthetic scenario bundle."""
    normalized_kind = _normalize_kind(kind)
    tracks = _tracks_for_kind(normalized_kind)
    agents = tuple(track[0] for track in tracks)
    trajectories = tuple(track[1] for track in tracks)
    scenario_id = make_identifier("scenario", "synthetic", normalized_kind.value)
    frame_id = make_identifier(
        "frame",
        "synthetic",
        normalized_kind.value,
        "local",
    )
    has_elevation = normalized_kind is SyntheticScenarioKind.GRADE_SEPARATED_CROSSING
    start_time_ns = min(trajectory.start_time_ns for trajectory in trajectories)
    end_time_ns = max(trajectory.end_time_ns for trajectory in trajectories)
    scenario = ScenarioRecord(
        scenario_id=scenario_id,
        dataset_id=_DATASET_ID,
        dataset_version=_DATASET_VERSION,
        split_name="synthetic",
        city_or_region="synthetic",
        source_scenario_id=None,
        start_time_ns=start_time_ns,
        end_time_ns=end_time_ns,
        coordinate_frame_id=frame_id,
        origin_x_m=0.0,
        origin_y_m=0.0,
        origin_z_m=0.0 if has_elevation else None,
        source_crs=None,
        has_elevation=has_elevation,
        agent_count=len(agents),
        source_map_available=False,
        quality_flags=(),
        adapter_name="synthetic_generator",
        adapter_version="1.0",
        source_checksum=None,
    )
    frame = CoordinateFrameRecord(
        scenario_id=scenario_id,
        coordinate_frame_id=frame_id,
        parent_frame_id=None,
        frame_type="local_cartesian",
        origin_x_m=0.0,
        origin_y_m=0.0,
        origin_z_m=0.0 if has_elevation else None,
        axis_convention="right_handed_x_y_z_up",
        distance_unit="m",
        angle_unit="rad",
        timestamp_unit="ns",
        source_crs=None,
        has_elevation=has_elevation,
        transform_to_parent_4x4=None,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )
    return SyntheticScenario(
        kind=normalized_kind,
        scenario=scenario,
        coordinate_frame=frame,
        agents=agents,
        trajectories=trajectories,
    )


def build_synthetic_dataset(
    kinds: Sequence[SyntheticScenarioKind | str] | None = None,
) -> SyntheticDataset:
    """Build all synthetic scenarios or an ordered caller-selected subset."""
    if kinds is None:
        normalized_kinds = synthetic_scenario_kinds()
    else:
        kinds_value: object = kinds
        if isinstance(kinds_value, (str, bytes)) or not isinstance(
            kinds_value, Sequence
        ):
            raise ValidationError("kinds must be a finite non-string sequence")
        copied = tuple(value for value in kinds_value)
        if not copied:
            raise ValidationError("kinds must contain at least one scenario kind")
        normalized_kinds = tuple(_normalize_kind(value) for value in copied)
    if len(normalized_kinds) != len(set(normalized_kinds)):
        raise ValidationError("synthetic scenario kinds must be unique")
    return SyntheticDataset(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=_DATASET_VERSION,
        scenarios=tuple(build_synthetic_scenario(kind) for kind in normalized_kinds),
    )


def synthetic_scenarios(
    dataset: SyntheticDataset,
) -> tuple[ScenarioRecord, ...]:
    """Return scenario records in dataset order."""
    if not isinstance(dataset, SyntheticDataset):
        raise ValidationError("dataset must be a SyntheticDataset")
    return tuple(item.scenario for item in dataset.scenarios)


def synthetic_coordinate_frames(
    dataset: SyntheticDataset,
) -> tuple[CoordinateFrameRecord, ...]:
    """Return coordinate frames in dataset order."""
    if not isinstance(dataset, SyntheticDataset):
        raise ValidationError("dataset must be a SyntheticDataset")
    return tuple(item.coordinate_frame for item in dataset.scenarios)


def synthetic_agents(dataset: SyntheticDataset) -> tuple[AgentRecord, ...]:
    """Return agents in dataset and scenario order."""
    if not isinstance(dataset, SyntheticDataset):
        raise ValidationError("dataset must be a SyntheticDataset")
    return tuple(agent for item in dataset.scenarios for agent in item.agents)


def synthetic_trajectories(dataset: SyntheticDataset) -> tuple[Trajectory, ...]:
    """Return trajectories in dataset and scenario order."""
    if not isinstance(dataset, SyntheticDataset):
        raise ValidationError("dataset must be a SyntheticDataset")
    return tuple(
        trajectory for item in dataset.scenarios for trajectory in item.trajectories
    )


def synthetic_dataset_index_to_dict(
    dataset: SyntheticDataset,
) -> dict[str, object]:
    """Return the deterministic JSON-compatible synthetic dataset index."""
    scenarios = [
        {
            "kind": item.kind.value,
            "scenario_id": item.scenario.scenario_id,
            "coordinate_frame_id": item.coordinate_frame.coordinate_frame_id,
            "agent_ids": [agent.agent_id for agent in item.agents],
            "trajectory_ids": [
                trajectory.trajectory_id for trajectory in item.trajectories
            ],
            "start_time_ns": item.scenario.start_time_ns,
            "end_time_ns": item.scenario.end_time_ns,
            "has_elevation": item.scenario.has_elevation,
        }
        for item in dataset.scenarios
    ]
    return {
        "schema_version": dataset.schema_version,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "scenario_count": len(dataset.scenarios),
        "agent_count": len(synthetic_agents(dataset)),
        "trajectory_count": len(synthetic_trajectories(dataset)),
        "scenarios": scenarios,
    }


def materialize_synthetic_dataset(
    run_directory: RunDirectory,
    dataset: SyntheticDataset,
    *,
    relative_directory: str | Path = "artifacts/synthetic_dataset",
    row_group_size: int = 65_536,
) -> SyntheticDatasetArtifacts:
    """Atomically materialize the index and four canonical Parquet tables."""
    if not isinstance(dataset, SyntheticDataset):
        raise ValidationError("dataset must be a SyntheticDataset")
    try:
        directory = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    index = atomic_write_canonical_json(
        run_directory,
        directory / "scenario_index.json",
        synthetic_dataset_index_to_dict(dataset),
    )
    scenario_manifest = atomic_write_canonical_parquet(
        run_directory,
        directory / "scenario_manifest.parquet",
        scenario_records_to_table(synthetic_scenarios(dataset)),
        CanonicalSchemaName.SCENARIO_MANIFEST,
        row_group_size=row_group_size,
    )
    coordinate_frames = atomic_write_canonical_parquet(
        run_directory,
        directory / "coordinate_frame_metadata.parquet",
        coordinate_frame_records_to_table(synthetic_coordinate_frames(dataset)),
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        row_group_size=row_group_size,
    )
    agents = atomic_write_canonical_parquet(
        run_directory,
        directory / "agent_metadata.parquet",
        agent_records_to_table(synthetic_agents(dataset)),
        CanonicalSchemaName.AGENT_METADATA,
        row_group_size=row_group_size,
    )
    samples = atomic_write_canonical_parquet(
        run_directory,
        directory / "trajectory_samples.parquet",
        trajectories_to_table(synthetic_trajectories(dataset)),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        row_group_size=row_group_size,
    )
    return SyntheticDatasetArtifacts(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        scenario_index=index,
        scenario_manifest=scenario_manifest,
        coordinate_frame_metadata=coordinate_frames,
        agent_metadata=agents,
        trajectory_samples=samples,
    )


def _verified_index_bytes(
    repository_root: Path,
    artifact: WrittenArtifact,
) -> bytes:
    if not isinstance(repository_root, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("repository_root must be a directory")
    path = root / artifact.relative_path
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("synthetic scenario index is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("synthetic scenario index resolves outside repository")
    if not resolved.is_file():
        raise ArtifactError("synthetic scenario index is not a regular file")
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise ArtifactError("synthetic scenario index cannot be read") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("synthetic scenario index size differs")
    if hashlib.sha256(data).hexdigest() != artifact.content_checksum:
        raise ArtifactError("synthetic scenario index checksum differs")
    return data


def _validate_index(value: object) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, dict)
        or len(value) != len(_INDEX_FIELDS)
        or set(value) != set(_INDEX_FIELDS)
    ):
        raise SchemaError("synthetic scenario index fields are incompatible")
    if value["schema_version"] != _SCHEMA_VERSION:
        raise SchemaError("synthetic scenario index schema version is incompatible")
    if value["dataset_id"] != _DATASET_ID:
        raise SchemaError("synthetic scenario index dataset identifier is incompatible")
    if value["dataset_version"] != _DATASET_VERSION:
        raise SchemaError("synthetic scenario index dataset version is incompatible")
    scenarios_value = value["scenarios"]
    if not isinstance(scenarios_value, list) or not scenarios_value:
        raise SchemaError("synthetic scenario index scenarios are invalid")
    scenarios: list[dict[str, Any]] = []
    kinds: list[SyntheticScenarioKind] = []
    scenario_ids: list[str] = []
    all_agent_ids: list[str] = []
    all_trajectory_ids: list[str] = []
    agent_count = 0
    trajectory_count = 0
    for item in scenarios_value:
        if (
            not isinstance(item, dict)
            or len(item) != len(_INDEX_SCENARIO_FIELDS)
            or set(item) != set(_INDEX_SCENARIO_FIELDS)
        ):
            raise SchemaError("synthetic scenario index entry fields are incompatible")
        try:
            kind = SyntheticScenarioKind(item["kind"])
        except (TypeError, ValueError):
            raise SchemaError("synthetic scenario index kind is invalid") from None
        expected_scenario = make_identifier("scenario", "synthetic", kind.value)
        expected_frame = make_identifier("frame", "synthetic", kind.value, "local")
        if (
            item["scenario_id"] != expected_scenario
            or item["coordinate_frame_id"] != expected_frame
        ):
            raise SchemaError("synthetic scenario index identifiers are invalid")
        agent_ids = item["agent_ids"]
        trajectory_ids = item["trajectory_ids"]
        if (
            not isinstance(agent_ids, list)
            or not agent_ids
            or not all(isinstance(identifier, str) for identifier in agent_ids)
            or not isinstance(trajectory_ids, list)
            or len(trajectory_ids) != len(agent_ids)
            or not all(isinstance(identifier, str) for identifier in trajectory_ids)
        ):
            raise SchemaError("synthetic scenario index child identifiers are invalid")
        agent_prefix = f"agent:synthetic:{kind.value}:"
        trajectory_prefix = f"trajectory:synthetic:{kind.value}:"
        if any(
            not agent_id.startswith(agent_prefix)
            or not trajectory_id.startswith(trajectory_prefix)
            or agent_id.removeprefix(agent_prefix)
            != trajectory_id.removeprefix(trajectory_prefix)
            or not agent_id.removeprefix(agent_prefix)
            for agent_id, trajectory_id in zip(
                agent_ids,
                trajectory_ids,
                strict=True,
            )
        ):
            raise SchemaError("synthetic scenario index child identifiers are invalid")
        if (
            not isinstance(item["start_time_ns"], int)
            or isinstance(item["start_time_ns"], bool)
            or not isinstance(item["end_time_ns"], int)
            or isinstance(item["end_time_ns"], bool)
            or item["start_time_ns"] > item["end_time_ns"]
            or not isinstance(item["has_elevation"], bool)
        ):
            raise SchemaError("synthetic scenario index interval is invalid")
        kinds.append(kind)
        scenario_ids.append(expected_scenario)
        all_agent_ids.extend(agent_ids)
        all_trajectory_ids.extend(trajectory_ids)
        agent_count += len(agent_ids)
        trajectory_count += len(trajectory_ids)
        scenarios.append(item)
    if len(kinds) != len(set(kinds)) or len(scenario_ids) != len(set(scenario_ids)):
        raise SchemaError("synthetic scenario index contains duplicate scenarios")
    if len(all_agent_ids) != len(set(all_agent_ids)) or len(all_trajectory_ids) != len(
        set(all_trajectory_ids)
    ):
        raise SchemaError("synthetic scenario index contains duplicate children")
    expected_counts = (
        ("scenario_count", len(scenarios)),
        ("agent_count", agent_count),
        ("trajectory_count", trajectory_count),
    )
    for field_name, expected in expected_counts:
        actual = value[field_name]
        if (
            not isinstance(actual, int)
            or isinstance(actual, bool)
            or actual != expected
        ):
            raise SchemaError(f"synthetic scenario index {field_name} differs")
    return tuple(scenarios)


def verify_synthetic_dataset_artifacts(
    repository_root: Path,
    artifacts: SyntheticDatasetArtifacts,
) -> None:
    """Verify synthetic index bytes, canonical tables, identities, and counts."""
    if not isinstance(artifacts, SyntheticDatasetArtifacts):
        raise ValidationError("artifacts must be SyntheticDatasetArtifacts")
    data = _verified_index_bytes(repository_root, artifacts.scenario_index)
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SchemaError("synthetic scenario index is malformed") from error
    scenarios = _validate_index(decoded)
    parquet_artifacts = (
        artifacts.scenario_manifest,
        artifacts.coordinate_frame_metadata,
        artifacts.agent_metadata,
        artifacts.trajectory_samples,
    )
    for artifact in parquet_artifacts:
        verify_canonical_parquet_artifact(repository_root, artifact)

    tables = tuple(
        read_canonical_parquet_table(
            repository_root,
            (artifact.written_artifact.relative_path,),
            artifact.schema_name,
        )
        for artifact in parquet_artifacts
    )
    expected_scenario_ids = [item["scenario_id"] for item in scenarios]
    expected_frame_ids = [item["coordinate_frame_id"] for item in scenarios]
    expected_agent_ids = [
        identifier for item in scenarios for identifier in item["agent_ids"]
    ]
    expected_trajectory_ids = [
        identifier for item in scenarios for identifier in item["trajectory_ids"]
    ]
    expected_agent_pairs = sorted(
        (item["scenario_id"], agent_id)
        for item in scenarios
        for agent_id in item["agent_ids"]
    )
    expected_trajectory_triples = sorted(
        (item["scenario_id"], agent_id, trajectory_id)
        for item in scenarios
        for agent_id, trajectory_id in zip(
            item["agent_ids"],
            item["trajectory_ids"],
            strict=True,
        )
    )
    if tables[0].num_rows != len(scenarios):
        raise SchemaError("synthetic scenario table row count differs")
    if tables[1].num_rows != len(scenarios):
        raise SchemaError("synthetic coordinate-frame row count differs")
    if tables[2].num_rows != len(expected_agent_ids):
        raise SchemaError("synthetic agent table row count differs")
    if sorted(tables[0].column("scenario_id").to_pylist()) != sorted(
        expected_scenario_ids
    ):
        raise SchemaError("synthetic scenario table identifiers differ")
    if set(tables[0].column("dataset_id").to_pylist()) != {artifacts.dataset_id}:
        raise SchemaError("synthetic scenario table dataset identifier differs")
    if set(tables[0].column("dataset_version").to_pylist()) != {
        artifacts.dataset_version
    }:
        raise SchemaError("synthetic scenario table dataset version differs")
    if sorted(tables[1].column("coordinate_frame_id").to_pylist()) != sorted(
        expected_frame_ids
    ):
        raise SchemaError("synthetic coordinate-frame identifiers differ")
    if sorted(tables[1].column("scenario_id").to_pylist()) != sorted(
        expected_scenario_ids
    ):
        raise SchemaError("synthetic coordinate-frame scenarios differ")
    if sorted(tables[2].column("agent_id").to_pylist()) != sorted(expected_agent_ids):
        raise SchemaError("synthetic agent identifiers differ")
    actual_agent_pairs = sorted(
        zip(
            tables[2].column("scenario_id").to_pylist(),
            tables[2].column("agent_id").to_pylist(),
            strict=True,
        )
    )
    if actual_agent_pairs != expected_agent_pairs:
        raise SchemaError("synthetic agent scenarios differ")
    actual_trajectory_ids = sorted(set(tables[3].column("trajectory_id").to_pylist()))
    if actual_trajectory_ids != sorted(expected_trajectory_ids):
        raise SchemaError("synthetic trajectory identifiers differ")
    actual_trajectory_triples = sorted(
        set(
            zip(
                tables[3].column("scenario_id").to_pylist(),
                tables[3].column("agent_id").to_pylist(),
                tables[3].column("trajectory_id").to_pylist(),
                strict=True,
            )
        )
    )
    if actual_trajectory_triples != expected_trajectory_triples:
        raise SchemaError("synthetic trajectory references differ")
