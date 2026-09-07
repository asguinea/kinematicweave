"""Immutable canonical scenario, agent, and trajectory records."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import math
import re

from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import validate_identifier

__all__ = [
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

_INT32_MAX = 2**31 - 1
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class AgentClass(StrEnum):
    """Canonical dynamic-agent classes."""

    VEHICLE = "vehicle"
    PEDESTRIAN = "pedestrian"
    CYCLIST = "cyclist"
    OTHER_DYNAMIC = "other_dynamic"
    UNKNOWN = "unknown"


class OriginType(StrEnum):
    """Canonical provenance categories."""

    SOURCE_GROUND_TRUTH = "source_ground_truth"
    SOURCE_OBSERVATION = "source_observation"
    SYNTHETIC = "synthetic"
    ORACLE_CONVERTED = "oracle_converted"
    INFERRED = "inferred"
    DECODED = "decoded"
    EDITED = "edited"
    DERIVED_METRIC = "derived_metric"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{field_name} must be nonempty")
    return normalized


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if not minimum <= value <= maximum:
        raise ValidationError(
            f"{field_name} must be in the range [{minimum}, {maximum}]"
        )
    return value


def _int64(value: object, field_name: str) -> int:
    return _integer(
        value,
        field_name,
        minimum=_INT64_MIN,
        maximum=_INT64_MAX,
    )


def _nonnegative_int32(value: object, field_name: str) -> int:
    return _integer(value, field_name, minimum=0, maximum=_INT32_MAX)


def _positive_int32(value: object, field_name: str) -> int:
    return _integer(value, field_name, minimum=1, maximum=_INT32_MAX)


def _finite_float(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    try:
        normalized = float(value)
    except OverflowError:
        raise ValidationError(f"{field_name} must be finite") from None
    if not math.isfinite(normalized):
        raise ValidationError(f"{field_name} must be finite")
    return normalized


def _optional_finite_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _finite_float(value, field_name)


def _actual_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a bool")
    return value


def _enum_value[EnumType: StrEnum](
    enum_type: type[EnumType],
    value: object,
    field_name: str,
) -> EnumType:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValidationError(f"{field_name} has invalid value {value!r}")


def _quality_flags(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("quality_flags must be a non-string sequence")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        flag = _required_text(item, "quality_flags item")
        if flag in seen:
            raise ValidationError("quality_flags must not contain duplicates")
        seen.add(flag)
        normalized.append(flag)
    return tuple(normalized)


def _optional_sha256(value: object) -> str | None:
    if value is None:
        return None
    normalized = _required_text(value, "source_checksum")
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValidationError(
            "source_checksum must be a lowercase 64-character SHA-256 digest"
        )
    return normalized


def _validate_elevation(
    has_elevation: bool,
    origin_z_m: float | None,
) -> None:
    if has_elevation and origin_z_m is None:
        raise ValidationError("origin_z_m is required when has_elevation is true")
    if not has_elevation and origin_z_m is not None:
        raise ValidationError("origin_z_m must be None when has_elevation is false")


@dataclass(frozen=True, slots=True)
class ScenarioRecord:
    """Validated canonical scenario-manifest record."""

    scenario_id: str
    dataset_id: str
    dataset_version: str
    split_name: str
    city_or_region: str | None
    source_scenario_id: str | None
    start_time_ns: int
    end_time_ns: int
    coordinate_frame_id: str
    origin_x_m: float
    origin_y_m: float
    origin_z_m: float | None
    source_crs: str | None
    has_elevation: bool
    agent_count: int
    source_map_available: bool
    quality_flags: Sequence[str]
    adapter_name: str
    adapter_version: str
    source_checksum: str | None

    def __post_init__(self) -> None:
        """Normalize fields and enforce scenario invariants."""
        object.__setattr__(self, "scenario_id", validate_identifier(self.scenario_id))
        object.__setattr__(
            self,
            "coordinate_frame_id",
            validate_identifier(self.coordinate_frame_id),
        )
        for field_name in (
            "dataset_id",
            "dataset_version",
            "split_name",
            "adapter_name",
            "adapter_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        for field_name in (
            "city_or_region",
            "source_scenario_id",
            "source_crs",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "start_time_ns",
            _int64(self.start_time_ns, "start_time_ns"),
        )
        object.__setattr__(
            self,
            "end_time_ns",
            _int64(self.end_time_ns, "end_time_ns"),
        )
        if self.start_time_ns > self.end_time_ns:
            raise ValidationError("start_time_ns must not exceed end_time_ns")
        for field_name in ("origin_x_m", "origin_y_m"):
            object.__setattr__(
                self,
                field_name,
                _finite_float(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "origin_z_m",
            _optional_finite_float(self.origin_z_m, "origin_z_m"),
        )
        object.__setattr__(
            self,
            "has_elevation",
            _actual_bool(self.has_elevation, "has_elevation"),
        )
        _validate_elevation(self.has_elevation, self.origin_z_m)
        object.__setattr__(
            self,
            "agent_count",
            _nonnegative_int32(self.agent_count, "agent_count"),
        )
        object.__setattr__(
            self,
            "source_map_available",
            _actual_bool(self.source_map_available, "source_map_available"),
        )
        object.__setattr__(self, "quality_flags", _quality_flags(self.quality_flags))
        object.__setattr__(
            self,
            "source_checksum",
            _optional_sha256(self.source_checksum),
        )


@dataclass(frozen=True, slots=True)
class CoordinateFrameRecord:
    """Validated canonical coordinate-frame metadata record."""

    scenario_id: str
    coordinate_frame_id: str
    parent_frame_id: str | None
    frame_type: str
    origin_x_m: float
    origin_y_m: float
    origin_z_m: float | None
    axis_convention: str
    distance_unit: str
    angle_unit: str
    timestamp_unit: str
    source_crs: str | None
    has_elevation: bool
    transform_to_parent_4x4: Sequence[float] | None
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        """Normalize fields and enforce coordinate-frame invariants."""
        object.__setattr__(self, "scenario_id", validate_identifier(self.scenario_id))
        object.__setattr__(
            self,
            "coordinate_frame_id",
            validate_identifier(self.coordinate_frame_id),
        )
        parent_frame_id = (
            None
            if self.parent_frame_id is None
            else validate_identifier(self.parent_frame_id)
        )
        object.__setattr__(self, "parent_frame_id", parent_frame_id)
        if self.coordinate_frame_id == self.parent_frame_id:
            raise ValidationError(
                "coordinate_frame_id must differ from parent_frame_id"
            )
        for field_name in ("frame_type", "axis_convention"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        for field_name, expected in (
            ("distance_unit", "m"),
            ("angle_unit", "rad"),
            ("timestamp_unit", "ns"),
        ):
            if getattr(self, field_name) != expected:
                raise ValidationError(f"{field_name} must equal {expected!r}")
        for field_name in ("origin_x_m", "origin_y_m"):
            object.__setattr__(
                self,
                field_name,
                _finite_float(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "origin_z_m",
            _optional_finite_float(self.origin_z_m, "origin_z_m"),
        )
        object.__setattr__(
            self,
            "source_crs",
            _optional_text(self.source_crs, "source_crs"),
        )
        object.__setattr__(
            self,
            "has_elevation",
            _actual_bool(self.has_elevation, "has_elevation"),
        )
        _validate_elevation(self.has_elevation, self.origin_z_m)
        if self.transform_to_parent_4x4 is not None:
            if isinstance(self.transform_to_parent_4x4, (str, bytes)) or not isinstance(
                self.transform_to_parent_4x4,
                Sequence,
            ):
                raise ValidationError(
                    "transform_to_parent_4x4 must be a numeric sequence"
                )
            transform = tuple(
                _finite_float(value, "transform_to_parent_4x4 item")
                for value in self.transform_to_parent_4x4
            )
            if len(transform) != 16:
                raise ValidationError(
                    "transform_to_parent_4x4 must contain exactly 16 values"
                )
            object.__setattr__(self, "transform_to_parent_4x4", transform)
        object.__setattr__(
            self,
            "origin_type",
            _enum_value(OriginType, self.origin_type, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _quality_flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """Validated canonical agent-metadata record."""

    scenario_id: str
    agent_id: str
    source_agent_id: str | None
    agent_class: AgentClass | str
    length_m: float | None
    width_m: float | None
    height_m: float | None
    first_time_ns: int
    last_time_ns: int
    sample_count: int
    is_focal_agent: bool
    is_ego_agent: bool
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        """Normalize fields and enforce agent invariants."""
        object.__setattr__(self, "scenario_id", validate_identifier(self.scenario_id))
        object.__setattr__(self, "agent_id", validate_identifier(self.agent_id))
        object.__setattr__(
            self,
            "source_agent_id",
            _optional_text(self.source_agent_id, "source_agent_id"),
        )
        object.__setattr__(
            self,
            "agent_class",
            _enum_value(AgentClass, self.agent_class, "agent_class"),
        )
        for field_name in ("length_m", "width_m", "height_m"):
            value = _optional_finite_float(getattr(self, field_name), field_name)
            if value is not None and value <= 0.0:
                raise ValidationError(f"{field_name} must be greater than zero")
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "first_time_ns",
            _int64(self.first_time_ns, "first_time_ns"),
        )
        object.__setattr__(
            self,
            "last_time_ns",
            _int64(self.last_time_ns, "last_time_ns"),
        )
        if self.first_time_ns > self.last_time_ns:
            raise ValidationError("first_time_ns must not exceed last_time_ns")
        object.__setattr__(
            self,
            "sample_count",
            _positive_int32(self.sample_count, "sample_count"),
        )
        object.__setattr__(
            self,
            "is_focal_agent",
            _actual_bool(self.is_focal_agent, "is_focal_agent"),
        )
        object.__setattr__(
            self,
            "is_ego_agent",
            _actual_bool(self.is_ego_agent, "is_ego_agent"),
        )
        object.__setattr__(
            self,
            "origin_type",
            _enum_value(OriginType, self.origin_type, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _quality_flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class TrajectorySampleRecord:
    """Validated canonical trajectory-sample record."""

    scenario_id: str
    agent_id: str
    trajectory_id: str
    sample_index: int
    timestamp_ns: int
    x_m: float
    y_m: float
    z_m: float | None
    heading_rad: float | None
    velocity_x_mps: float | None
    velocity_y_mps: float | None
    speed_mps: float | None
    acceleration_x_mps2: float | None
    acceleration_y_mps2: float | None
    is_observed: bool
    is_valid: bool
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        """Normalize fields and enforce sample invariants."""
        for field_name in ("scenario_id", "agent_id", "trajectory_id"):
            object.__setattr__(
                self,
                field_name,
                validate_identifier(getattr(self, field_name)),
            )
        object.__setattr__(
            self,
            "sample_index",
            _nonnegative_int32(self.sample_index, "sample_index"),
        )
        object.__setattr__(
            self,
            "timestamp_ns",
            _int64(self.timestamp_ns, "timestamp_ns"),
        )
        for field_name in ("x_m", "y_m"):
            object.__setattr__(
                self,
                field_name,
                _finite_float(getattr(self, field_name), field_name),
            )
        for field_name in (
            "z_m",
            "heading_rad",
            "velocity_x_mps",
            "velocity_y_mps",
            "speed_mps",
            "acceleration_x_mps2",
            "acceleration_y_mps2",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_finite_float(getattr(self, field_name), field_name),
            )
        if self.heading_rad is not None and not (
            -math.pi <= self.heading_rad < math.pi
        ):
            raise ValidationError("heading_rad must be in the interval [-pi, pi)")
        if self.speed_mps is not None and self.speed_mps < 0.0:
            raise ValidationError("speed_mps must not be negative")
        object.__setattr__(
            self,
            "is_observed",
            _actual_bool(self.is_observed, "is_observed"),
        )
        object.__setattr__(
            self,
            "is_valid",
            _actual_bool(self.is_valid, "is_valid"),
        )
        object.__setattr__(
            self,
            "origin_type",
            _enum_value(OriginType, self.origin_type, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _quality_flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class Trajectory:
    """Validated ordered canonical trajectory."""

    scenario_id: str
    agent_id: str
    trajectory_id: str
    samples: Sequence[TrajectorySampleRecord]
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        """Normalize fields and enforce trajectory ordering invariants."""
        for field_name in ("scenario_id", "agent_id", "trajectory_id"):
            object.__setattr__(
                self,
                field_name,
                validate_identifier(getattr(self, field_name)),
            )
        samples_value: object = self.samples
        if isinstance(samples_value, (str, bytes)) or not isinstance(
            samples_value,
            Sequence,
        ):
            raise ValidationError("samples must be a non-string sequence")
        samples = tuple(sample for sample in samples_value)
        if not samples:
            raise ValidationError("trajectory requires at least one sample")
        previous_timestamp: int | None = None
        for expected_index, sample in enumerate(samples):
            if not isinstance(sample, TrajectorySampleRecord):
                raise ValidationError(
                    "samples must contain TrajectorySampleRecord values"
                )
            if sample.scenario_id != self.scenario_id:
                raise ValidationError("sample scenario_id does not match trajectory")
            if sample.agent_id != self.agent_id:
                raise ValidationError("sample agent_id does not match trajectory")
            if sample.trajectory_id != self.trajectory_id:
                raise ValidationError("sample trajectory_id does not match trajectory")
            if sample.sample_index != expected_index:
                raise ValidationError(
                    "sample indices must be contiguous and start at zero"
                )
            if previous_timestamp is not None:
                if sample.timestamp_ns == previous_timestamp:
                    raise ValidationError("duplicate trajectory timestamp")
                if sample.timestamp_ns < previous_timestamp:
                    raise ValidationError(
                        "trajectory timestamps must be strictly increasing"
                    )
            previous_timestamp = sample.timestamp_ns
        object.__setattr__(self, "samples", samples)
        object.__setattr__(
            self,
            "origin_type",
            _enum_value(OriginType, self.origin_type, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _quality_flags(self.quality_flags))

    @property
    def sample_count(self) -> int:
        """Return the number of canonical samples."""
        return len(self.samples)

    @property
    def start_time_ns(self) -> int:
        """Return the first canonical sample timestamp in nanoseconds."""
        return self.samples[0].timestamp_ns

    @property
    def end_time_ns(self) -> int:
        """Return the last canonical sample timestamp in nanoseconds."""
        return self.samples[-1].timestamp_ns

    @property
    def has_elevation(self) -> bool:
        """Return whether any sample carries an elevation value."""
        return any(sample.z_m is not None for sample in self.samples)


def scenario_record_to_dict(record: ScenarioRecord) -> dict[str, object]:
    """Return a fresh schema-ordered dictionary for a scenario record."""
    return {
        "scenario_id": record.scenario_id,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "split_name": record.split_name,
        "city_or_region": record.city_or_region,
        "source_scenario_id": record.source_scenario_id,
        "start_time_ns": record.start_time_ns,
        "end_time_ns": record.end_time_ns,
        "coordinate_frame_id": record.coordinate_frame_id,
        "origin_x_m": record.origin_x_m,
        "origin_y_m": record.origin_y_m,
        "origin_z_m": record.origin_z_m,
        "source_crs": record.source_crs,
        "has_elevation": record.has_elevation,
        "agent_count": record.agent_count,
        "source_map_available": record.source_map_available,
        "quality_flags": list(record.quality_flags),
        "adapter_name": record.adapter_name,
        "adapter_version": record.adapter_version,
        "source_checksum": record.source_checksum,
    }


def coordinate_frame_record_to_dict(
    record: CoordinateFrameRecord,
) -> dict[str, object]:
    """Return a fresh schema-ordered dictionary for a coordinate frame."""
    return {
        "scenario_id": record.scenario_id,
        "coordinate_frame_id": record.coordinate_frame_id,
        "parent_frame_id": record.parent_frame_id,
        "frame_type": record.frame_type,
        "origin_x_m": record.origin_x_m,
        "origin_y_m": record.origin_y_m,
        "origin_z_m": record.origin_z_m,
        "axis_convention": record.axis_convention,
        "distance_unit": record.distance_unit,
        "angle_unit": record.angle_unit,
        "timestamp_unit": record.timestamp_unit,
        "source_crs": record.source_crs,
        "has_elevation": record.has_elevation,
        "transform_to_parent_4x4": (
            None
            if record.transform_to_parent_4x4 is None
            else list(record.transform_to_parent_4x4)
        ),
        "origin_type": _enum_value(
            OriginType,
            record.origin_type,
            "origin_type",
        ).value,
        "quality_flags": list(record.quality_flags),
    }


def agent_record_to_dict(record: AgentRecord) -> dict[str, object]:
    """Return a fresh schema-ordered dictionary for an agent record."""
    return {
        "scenario_id": record.scenario_id,
        "agent_id": record.agent_id,
        "source_agent_id": record.source_agent_id,
        "agent_class": _enum_value(
            AgentClass,
            record.agent_class,
            "agent_class",
        ).value,
        "length_m": record.length_m,
        "width_m": record.width_m,
        "height_m": record.height_m,
        "first_time_ns": record.first_time_ns,
        "last_time_ns": record.last_time_ns,
        "sample_count": record.sample_count,
        "is_focal_agent": record.is_focal_agent,
        "is_ego_agent": record.is_ego_agent,
        "origin_type": _enum_value(
            OriginType,
            record.origin_type,
            "origin_type",
        ).value,
        "quality_flags": list(record.quality_flags),
    }


def trajectory_sample_record_to_dict(
    record: TrajectorySampleRecord,
) -> dict[str, object]:
    """Return a fresh schema-ordered dictionary for a trajectory sample."""
    return {
        "scenario_id": record.scenario_id,
        "agent_id": record.agent_id,
        "trajectory_id": record.trajectory_id,
        "sample_index": record.sample_index,
        "timestamp_ns": record.timestamp_ns,
        "x_m": record.x_m,
        "y_m": record.y_m,
        "z_m": record.z_m,
        "heading_rad": record.heading_rad,
        "velocity_x_mps": record.velocity_x_mps,
        "velocity_y_mps": record.velocity_y_mps,
        "speed_mps": record.speed_mps,
        "acceleration_x_mps2": record.acceleration_x_mps2,
        "acceleration_y_mps2": record.acceleration_y_mps2,
        "is_observed": record.is_observed,
        "is_valid": record.is_valid,
        "origin_type": _enum_value(
            OriginType,
            record.origin_type,
            "origin_type",
        ).value,
        "quality_flags": list(record.quality_flags),
    }


def trajectory_to_sample_dicts(
    trajectory: Trajectory,
) -> tuple[dict[str, object], ...]:
    """Return fresh schema-ordered dictionaries for trajectory samples."""
    return tuple(
        trajectory_sample_record_to_dict(sample) for sample in trajectory.samples
    )


def validate_scenario_bundle(
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    agents: Sequence[AgentRecord],
    trajectories: Sequence[Trajectory],
) -> None:
    """Validate scenario-level frame, agent, trajectory, count, and time links.

    Raises:
        ValidationError: If the supplied canonical records are inconsistent.
    """
    if not isinstance(scenario, ScenarioRecord):
        raise ValidationError("scenario must be a ScenarioRecord")
    if not isinstance(coordinate_frame, CoordinateFrameRecord):
        raise ValidationError("coordinate_frame must be a CoordinateFrameRecord")
    agents_value: object = agents
    trajectories_value: object = trajectories
    if isinstance(agents_value, (str, bytes)) or not isinstance(
        agents_value,
        Sequence,
    ):
        raise ValidationError("agents must be a non-string sequence")
    if isinstance(trajectories_value, (str, bytes)) or not isinstance(
        trajectories_value,
        Sequence,
    ):
        raise ValidationError("trajectories must be a non-string sequence")
    agent_records = tuple(agent for agent in agents_value)
    trajectory_records = tuple(trajectory for trajectory in trajectories_value)
    if any(not isinstance(agent, AgentRecord) for agent in agent_records):
        raise ValidationError("agents must contain AgentRecord values")
    if any(not isinstance(trajectory, Trajectory) for trajectory in trajectory_records):
        raise ValidationError("trajectories must contain Trajectory values")

    if coordinate_frame.scenario_id != scenario.scenario_id:
        raise ValidationError("coordinate frame scenario_id does not match scenario")
    if coordinate_frame.coordinate_frame_id != scenario.coordinate_frame_id:
        raise ValidationError("coordinate_frame_id does not match scenario")
    for field_name in (
        "origin_x_m",
        "origin_y_m",
        "has_elevation",
        "origin_z_m",
        "source_crs",
    ):
        if getattr(coordinate_frame, field_name) != getattr(scenario, field_name):
            raise ValidationError(
                f"coordinate frame {field_name} does not match scenario"
            )
    if len(agent_records) != scenario.agent_count:
        raise ValidationError("agent count does not match scenario.agent_count")

    agent_ids = tuple(agent.agent_id for agent in agent_records)
    if len(agent_ids) != len(set(agent_ids)):
        raise ValidationError("agent identifiers must be unique")
    trajectory_ids = tuple(
        trajectory.trajectory_id for trajectory in trajectory_records
    )
    if len(trajectory_ids) != len(set(trajectory_ids)):
        raise ValidationError("trajectory identifiers must be unique")

    agents_by_id = {agent.agent_id: agent for agent in agent_records}
    trajectories_by_agent: dict[str, list[Trajectory]] = {}
    for agent in agent_records:
        if agent.scenario_id != scenario.scenario_id:
            raise ValidationError("agent scenario_id does not match scenario")
    for trajectory in trajectory_records:
        if trajectory.scenario_id != scenario.scenario_id:
            raise ValidationError("trajectory scenario_id does not match scenario")
        if trajectory.agent_id not in agents_by_id:
            raise ValidationError("trajectory references an unknown agent")
        trajectories_by_agent.setdefault(trajectory.agent_id, []).append(trajectory)

    for agent in agent_records:
        agent_trajectories = trajectories_by_agent.get(agent.agent_id, [])
        if len(agent_trajectories) != 1:
            raise ValidationError("every agent must have exactly one trajectory")
        trajectory = agent_trajectories[0]
        if agent.sample_count != trajectory.sample_count:
            raise ValidationError("agent sample_count does not match trajectory")
        if agent.first_time_ns != trajectory.start_time_ns:
            raise ValidationError("agent first_time_ns does not match trajectory")
        if agent.last_time_ns != trajectory.end_time_ns:
            raise ValidationError("agent last_time_ns does not match trajectory")
        if trajectory.start_time_ns < scenario.start_time_ns:
            raise ValidationError("trajectory starts before the scenario interval")
        if trajectory.end_time_ns > scenario.end_time_ns:
            raise ValidationError("trajectory ends after the scenario interval")
        if not scenario.has_elevation and trajectory.has_elevation:
            raise ValidationError(
                "trajectory contains elevation in a non-elevation scenario"
            )
