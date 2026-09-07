"""Strict direct-JSON adapter for one AV2 scenario vector map."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum, StrEnum
import hashlib
from itertools import pairwise
import json
import math
from pathlib import Path
import re
from typing import Any, cast
import unicodedata

from shapely.geometry import LineString, Polygon  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_canonical_json,
)
from kinematicweave.canonical import canonical_json_text
from kinematicweave.data.parquet_io import (
    CanonicalParquetArtifact,
    atomic_write_canonical_parquet,
    read_canonical_parquet_table,
    vector_map_elements_to_table,
    verify_canonical_parquet_artifact,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    MapGeometryType,
    VectorMapElementRecord,
    geometry_from_canonical_wkb,
    geometry_to_canonical_wkb,
)
from kinematicweave.domain.records import (
    CoordinateFrameRecord,
    OriginType,
    ScenarioRecord,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import make_identifier, validate_identifier
from kinematicweave.paths import normalize_relative_path, relative_path_text

__all__ = [
    "Av2LaneMarkType",
    "Av2LaneType",
    "Av2VectorMapAdapterConfig",
    "Av2VectorMapArtifacts",
    "Av2VectorMapConversion",
    "Av2VectorMapSourceSummary",
    "av2_vector_map_conversion_summary_to_dict",
    "inspect_av2_vector_map_file",
    "load_av2_vector_map",
    "materialize_av2_vector_map",
    "verify_av2_vector_map_artifacts",
]

_SCHEMA_VERSION = "1.0"
_ADAPTER_NAME = "av2_vector_map_adapter"
_DATASET_ID = "av2_motion"
_SOURCE_CRS = "av2_city_map"
_READ_CHUNK_SIZE = 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_FILENAME_PATTERN = re.compile(r"log_map_archive_(.+)\.json")


class Av2LaneType(StrEnum):
    """Supported AV2 lane types."""

    VEHICLE = "VEHICLE"
    BIKE = "BIKE"
    BUS = "BUS"


class Av2LaneMarkType(StrEnum):
    """Supported AV2 lane-boundary mark types."""

    DASH_SOLID_YELLOW = "DASH_SOLID_YELLOW"
    DASH_SOLID_WHITE = "DASH_SOLID_WHITE"
    DASHED_WHITE = "DASHED_WHITE"
    DASHED_YELLOW = "DASHED_YELLOW"
    DOUBLE_SOLID_YELLOW = "DOUBLE_SOLID_YELLOW"
    DOUBLE_SOLID_WHITE = "DOUBLE_SOLID_WHITE"
    DOUBLE_DASH_YELLOW = "DOUBLE_DASH_YELLOW"
    DOUBLE_DASH_WHITE = "DOUBLE_DASH_WHITE"
    SOLID_YELLOW = "SOLID_YELLOW"
    SOLID_WHITE = "SOLID_WHITE"
    SOLID_DASH_WHITE = "SOLID_DASH_WHITE"
    SOLID_DASH_YELLOW = "SOLID_DASH_YELLOW"
    SOLID_BLUE = "SOLID_BLUE"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


def _positive_int(value: object, field_name: str) -> int:
    normalized = _nonnegative_int(value, field_name)
    if normalized == 0:
        raise ValidationError(f"{field_name} must be positive")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return value


def _enum_value[EnumT: Enum](
    enum_type: type[EnumT],
    value: object,
    field_name: str,
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must use {enum_type.__name__}")
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must use {enum_type.__name__}") from None


def _safe_source_map_id(value: object, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if ":" in normalized or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise ValidationError(
            f"{field_name} must not contain colons or control characters"
        )
    return normalized


def _count_summary(value: object) -> tuple[tuple[str, int], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("element_type_counts must be a finite sequence")
    normalized: list[tuple[str, int]] = []
    for item in value:
        if (
            isinstance(item, (str, bytes))
            or not isinstance(item, Sequence)
            or len(item) != 2
        ):
            raise ValidationError("element_type_counts items must be (str, int) pairs")
        key, count = item
        if not isinstance(key, str):
            raise ValidationError("element_type_counts keys must be strings")
        normalized.append((key, _nonnegative_int(count, "element type count")))
    if tuple(key for key, _count in normalized) != tuple(
        item.value for item in MapElementType
    ):
        raise ValidationError("element_type_counts keys are out of canonical order")
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class Av2VectorMapAdapterConfig:
    """Configuration for strict AV2 vector-map conversion."""

    adapter_version: str = "1.0"
    centerline_point_count: int = 50
    max_source_bytes: int = 67_108_864

    def __post_init__(self) -> None:
        """Normalize and validate adapter configuration."""
        object.__setattr__(
            self,
            "adapter_version",
            _required_text(self.adapter_version, "adapter_version"),
        )
        point_count = _nonnegative_int(
            self.centerline_point_count,
            "centerline_point_count",
        )
        if point_count < 2:
            raise ValidationError("centerline_point_count must be at least two")
        object.__setattr__(self, "centerline_point_count", point_count)
        object.__setattr__(
            self,
            "max_source_bytes",
            _positive_int(self.max_source_bytes, "max_source_bytes"),
        )


_DEFAULT_CONFIG = Av2VectorMapAdapterConfig()


@dataclass(frozen=True, slots=True)
class Av2VectorMapSourceSummary:
    """Validated source identity and entity counts."""

    source_relative_path: Path
    source_checksum: str
    source_map_id: str
    lane_segment_count: int
    drivable_area_count: int
    pedestrian_crossing_count: int
    omitted_external_lane_reference_count: int

    def __post_init__(self) -> None:
        """Normalize and validate source summary fields."""
        object.__setattr__(
            self,
            "source_relative_path",
            normalize_relative_path(self.source_relative_path),
        )
        object.__setattr__(
            self,
            "source_checksum",
            _sha256(self.source_checksum, "source_checksum"),
        )
        object.__setattr__(
            self,
            "source_map_id",
            _safe_source_map_id(self.source_map_id, "source_map_id"),
        )
        for field_name in (
            "lane_segment_count",
            "drivable_area_count",
            "pedestrian_crossing_count",
            "omitted_external_lane_reference_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class Av2VectorMapConversion:
    """Canonical map elements and source provenance for one AV2 scenario."""

    source_relative_path: Path
    source_checksum: str
    source_map_id: str
    adapter_version: str
    dataset_version: str
    centerline_point_count: int
    scenario_id: str
    coordinate_frame_id: str
    lane_segment_count: int
    drivable_area_count: int
    pedestrian_crossing_count: int
    omitted_external_lane_reference_count: int
    elements: tuple[VectorMapElementRecord, ...]
    element_type_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        """Copy child records and enforce map referential integrity."""
        object.__setattr__(
            self,
            "source_relative_path",
            normalize_relative_path(self.source_relative_path),
        )
        object.__setattr__(
            self,
            "source_checksum",
            _sha256(self.source_checksum, "source_checksum"),
        )
        object.__setattr__(
            self,
            "source_map_id",
            _safe_source_map_id(self.source_map_id, "source_map_id"),
        )
        for field_name in ("adapter_version", "dataset_version"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        point_count = _nonnegative_int(
            self.centerline_point_count,
            "centerline_point_count",
        )
        if point_count < 2:
            raise ValidationError("centerline_point_count must be at least two")
        object.__setattr__(self, "centerline_point_count", point_count)
        object.__setattr__(
            self,
            "scenario_id",
            validate_identifier(self.scenario_id),
        )
        object.__setattr__(
            self,
            "coordinate_frame_id",
            validate_identifier(self.coordinate_frame_id),
        )
        for field_name in (
            "lane_segment_count",
            "drivable_area_count",
            "pedestrian_crossing_count",
            "omitted_external_lane_reference_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        value: object = self.elements
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("elements must be a finite sequence")
        elements = tuple(item for item in value)
        if any(not isinstance(item, VectorMapElementRecord) for item in elements):
            raise ValidationError("elements must contain VectorMapElementRecord values")
        object.__setattr__(self, "elements", elements)
        identifiers = tuple(item.map_element_id for item in elements)
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError("map element identifiers must be unique")
        if any(item.scenario_id != self.scenario_id for item in elements):
            raise ValidationError("all map elements must use scenario_id")
        counts = _count_summary(self.element_type_counts)
        object.__setattr__(self, "element_type_counts", counts)
        if sum(count for _key, count in counts) != len(elements):
            raise ValidationError("element_type_counts must total len(elements)")
        counted = {
            element_type.value: sum(
                item.element_type is element_type for item in elements
            )
            for element_type in MapElementType
        }
        if dict(counts) != counted:
            raise ValidationError("element_type_counts differ from elements")
        if counted[MapElementType.LANE_CENTERLINE.value] != self.lane_segment_count:
            raise ValidationError("lane_segment_count differs from centerlines")
        if counted[MapElementType.ROAD_AREA.value] != self.drivable_area_count:
            raise ValidationError("drivable_area_count differs from road areas")
        if counted[MapElementType.CROSSWALK.value] != (self.pedestrian_crossing_count):
            raise ValidationError("pedestrian_crossing_count differs from crosswalks")
        by_id = {item.map_element_id: item for item in elements}
        for element in elements:
            references = (
                *element.successor_ids,
                *element.predecessor_ids,
                element.parent_element_id,
                element.left_neighbor_id,
                element.right_neighbor_id,
            )
            if any(
                reference is not None and reference not in by_id
                for reference in references
            ):
                raise ValidationError("map element reference does not resolve")
            has_lane_graph = bool(
                element.successor_ids
                or element.predecessor_ids
                or element.left_neighbor_id
                or element.right_neighbor_id
            )
            if (
                has_lane_graph
                and element.element_type is not MapElementType.LANE_CENTERLINE
            ):
                raise ValidationError(
                    "only lane centerlines may contain lane graph references"
                )
            if element.element_type is MapElementType.LANE_BOUNDARY:
                parent = by_id.get(element.parent_element_id or "")
                if (
                    parent is None
                    or parent.element_type is not MapElementType.LANE_CENTERLINE
                ):
                    raise ValidationError(
                        "lane boundary parent must resolve to a lane centerline"
                    )


@dataclass(frozen=True, slots=True)
class Av2VectorMapArtifacts:
    """Physical artifacts for one AV2 vector-map conversion."""

    adapter_summary: WrittenArtifact
    vector_map_elements: CanonicalParquetArtifact

    def __post_init__(self) -> None:
        """Validate adapter artifact identities."""
        if not isinstance(self.adapter_summary, WrittenArtifact):
            raise ValidationError("adapter_summary must be a WrittenArtifact")
        if not isinstance(self.vector_map_elements, CanonicalParquetArtifact):
            raise ValidationError(
                "vector_map_elements must be a CanonicalParquetArtifact"
            )
        if (
            self.vector_map_elements.schema_name
            is not CanonicalSchemaName.VECTOR_MAP_ELEMENTS
            or self.vector_map_elements.schema_version != _SCHEMA_VERSION
        ):
            raise ValidationError(
                "vector_map_elements uses an incompatible canonical schema"
            )


type _Point3D = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class _SourceLane:
    lane_id: int
    is_intersection: bool
    lane_type: Av2LaneType
    right_boundary: tuple[_Point3D, ...]
    left_boundary: tuple[_Point3D, ...]
    right_mark: Av2LaneMarkType
    left_mark: Av2LaneMarkType
    right_neighbor_id: int | None
    left_neighbor_id: int | None
    predecessors: tuple[int, ...]
    successors: tuple[int, ...]
    external_right_neighbor_id: int | None
    external_left_neighbor_id: int | None
    external_predecessors: tuple[int, ...]
    external_successors: tuple[int, ...]

    @property
    def omitted_external_reference_count(self) -> int:
        """Return the number of source references omitted from canonical topology."""
        return (
            len(self.external_predecessors)
            + len(self.external_successors)
            + int(self.external_left_neighbor_id is not None)
            + int(self.external_right_neighbor_id is not None)
        )


@dataclass(frozen=True, slots=True)
class _SourceArea:
    area_id: int
    boundary: tuple[_Point3D, ...]


@dataclass(frozen=True, slots=True)
class _SourceCrossing:
    crossing_id: int
    edge1: tuple[_Point3D, ...]
    edge2: tuple[_Point3D, ...]


@dataclass(frozen=True, slots=True)
class _ParsedMap:
    lanes: tuple[_SourceLane, ...]
    areas: tuple[_SourceArea, ...]
    crossings: tuple[_SourceCrossing, ...]


def _source_error(error: ValidationError) -> SchemaError:
    return SchemaError(str(error))


def _source_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SchemaError(f"{field_name} must be a non-Boolean integer")
    return value


def _source_optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _source_int(value, field_name)


def _source_float(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SchemaError(f"{field_name} must be numeric")
    try:
        normalized = float(value)
    except OverflowError:
        raise SchemaError(f"{field_name} must be finite") from None
    if not math.isfinite(normalized):
        raise SchemaError(f"{field_name} must be finite")
    return normalized


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SchemaError(f"{field_name} must be a JSON object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SchemaError(f"{field_name} must be a JSON array")
    return cast(Sequence[object], value)


def _point(value: object, field_name: str) -> _Point3D:
    raw = _mapping(value, field_name)
    for name in ("x", "y", "z"):
        if name not in raw:
            raise SchemaError(f"{field_name} is missing {name!r}")
    return (
        _source_float(raw["x"], f"{field_name}.x"),
        _source_float(raw["y"], f"{field_name}.y"),
        _source_float(raw["z"], f"{field_name}.z"),
    )


def _points(value: object, field_name: str) -> tuple[_Point3D, ...]:
    return tuple(
        _point(item, f"{field_name}[{index}]")
        for index, item in enumerate(_sequence(value, field_name))
    )


def _distance(first: _Point3D, second: _Point3D) -> float:
    return math.sqrt(
        (second[0] - first[0]) ** 2
        + (second[1] - first[1]) ** 2
        + (second[2] - first[2]) ** 2
    )


def _polyline_length(points: tuple[_Point3D, ...]) -> float:
    return sum(_distance(first, second) for first, second in pairwise(points))


def _boundary(value: object, field_name: str) -> tuple[_Point3D, ...]:
    points = _points(value, field_name)
    if len(points) < 2:
        raise SchemaError(f"{field_name} must contain at least two points")
    length = _polyline_length(points)
    if not math.isfinite(length) or length <= 0.0:
        raise SchemaError(f"{field_name} must have positive 3D length")
    geometry = LineString(points)
    if geometry.is_empty or not geometry.is_valid:
        raise SchemaError(f"{field_name} is not valid line geometry")
    return points


def _source_ids(value: object, field_name: str) -> tuple[int, ...]:
    normalized = tuple(
        _source_int(item, f"{field_name} item") for item in _sequence(value, field_name)
    )
    if len(normalized) != len(set(normalized)):
        raise SchemaError(f"{field_name} must not contain duplicates")
    return normalized


def _source_enum[EnumT: Enum](
    enum_type: type[EnumT],
    value: object,
    field_name: str,
) -> EnumT:
    try:
        return _enum_value(enum_type, value, field_name)
    except ValidationError as error:
        raise _source_error(error) from None


def _entity_id(key: str, raw: Mapping[str, object], field_name: str) -> int:
    try:
        key_id = int(key)
    except ValueError:
        raise SchemaError(f"{field_name} key must parse as an integer") from None
    if "id" not in raw:
        raise SchemaError(f"{field_name} is missing 'id'")
    entity_id = _source_int(raw["id"], f"{field_name}.id")
    if key_id != entity_id:
        raise SchemaError(f"{field_name} key and id field differ")
    return entity_id


def _parse_lanes(value: object) -> tuple[_SourceLane, ...]:
    collection = _mapping(value, "lane_segments")
    lanes: list[_SourceLane] = []
    seen: set[int] = set()
    required = (
        "id",
        "is_intersection",
        "lane_type",
        "right_lane_boundary",
        "left_lane_boundary",
        "right_lane_mark_type",
        "left_lane_mark_type",
        "right_neighbor_id",
        "left_neighbor_id",
        "predecessors",
        "successors",
    )
    for key, value in collection.items():
        raw = _mapping(value, f"lane_segments[{key!r}]")
        missing = tuple(name for name in required if name not in raw)
        if missing:
            raise SchemaError(f"lane segment is missing {missing[0]!r}")
        lane_id = _entity_id(key, raw, "lane segment")
        if lane_id in seen:
            raise SchemaError("lane segment IDs must be unique")
        seen.add(lane_id)
        is_intersection = raw["is_intersection"]
        if not isinstance(is_intersection, bool):
            raise SchemaError("is_intersection must be a bool")
        lanes.append(
            _SourceLane(
                lane_id=lane_id,
                is_intersection=is_intersection,
                lane_type=_source_enum(
                    Av2LaneType,
                    raw["lane_type"],
                    "lane_type",
                ),
                right_boundary=_boundary(
                    raw["right_lane_boundary"],
                    "right_lane_boundary",
                ),
                left_boundary=_boundary(
                    raw["left_lane_boundary"],
                    "left_lane_boundary",
                ),
                right_mark=_source_enum(
                    Av2LaneMarkType,
                    raw["right_lane_mark_type"],
                    "right_lane_mark_type",
                ),
                left_mark=_source_enum(
                    Av2LaneMarkType,
                    raw["left_lane_mark_type"],
                    "left_lane_mark_type",
                ),
                right_neighbor_id=_source_optional_int(
                    raw["right_neighbor_id"],
                    "right_neighbor_id",
                ),
                left_neighbor_id=_source_optional_int(
                    raw["left_neighbor_id"],
                    "left_neighbor_id",
                ),
                predecessors=_source_ids(raw["predecessors"], "predecessors"),
                successors=_source_ids(raw["successors"], "successors"),
                external_right_neighbor_id=None,
                external_left_neighbor_id=None,
                external_predecessors=(),
                external_successors=(),
            )
        )
    lanes.sort(key=lambda item: item.lane_id)
    lane_ids = {item.lane_id for item in lanes}
    normalized: list[_SourceLane] = []
    for lane in lanes:
        references = (
            *lane.predecessors,
            *lane.successors,
            lane.left_neighbor_id,
            lane.right_neighbor_id,
        )
        if lane.lane_id in references:
            raise SchemaError("source lane references must not be self references")
        if (
            lane.left_neighbor_id is not None
            and lane.left_neighbor_id == lane.right_neighbor_id
        ):
            raise SchemaError("left and right source neighbors must differ")
        normalized.append(
            replace(
                lane,
                predecessors=tuple(
                    sorted(item for item in lane.predecessors if item in lane_ids)
                ),
                successors=tuple(
                    sorted(item for item in lane.successors if item in lane_ids)
                ),
                left_neighbor_id=(
                    lane.left_neighbor_id if lane.left_neighbor_id in lane_ids else None
                ),
                right_neighbor_id=(
                    lane.right_neighbor_id
                    if lane.right_neighbor_id in lane_ids
                    else None
                ),
                external_predecessors=tuple(
                    sorted(item for item in lane.predecessors if item not in lane_ids)
                ),
                external_successors=tuple(
                    sorted(item for item in lane.successors if item not in lane_ids)
                ),
                external_left_neighbor_id=(
                    lane.left_neighbor_id
                    if lane.left_neighbor_id is not None
                    and lane.left_neighbor_id not in lane_ids
                    else None
                ),
                external_right_neighbor_id=(
                    lane.right_neighbor_id
                    if lane.right_neighbor_id is not None
                    and lane.right_neighbor_id not in lane_ids
                    else None
                ),
            )
        )
    return tuple(normalized)


def _area_boundary(value: object, field_name: str) -> tuple[_Point3D, ...]:
    points = _points(value, field_name)
    if len(set(points)) < 3:
        raise SchemaError(f"{field_name} must contain at least three distinct points")
    closed = points if points[0] == points[-1] else (*points, points[0])
    geometry = Polygon(closed)
    if geometry.is_empty or not geometry.is_valid:
        raise SchemaError(f"{field_name} is not valid polygon geometry")
    return tuple(closed)


def _parse_areas(value: object) -> tuple[_SourceArea, ...]:
    collection = _mapping(value, "drivable_areas")
    areas: list[_SourceArea] = []
    seen: set[int] = set()
    for key, value in collection.items():
        raw = _mapping(value, f"drivable_areas[{key!r}]")
        if "area_boundary" not in raw:
            raise SchemaError("drivable area is missing 'area_boundary'")
        area_id = _entity_id(key, raw, "drivable area")
        if area_id in seen:
            raise SchemaError("drivable area IDs must be unique")
        seen.add(area_id)
        areas.append(
            _SourceArea(
                area_id=area_id,
                boundary=_area_boundary(
                    raw["area_boundary"],
                    "area_boundary",
                ),
            )
        )
    return tuple(sorted(areas, key=lambda item: item.area_id))


def _parse_crossings(value: object) -> tuple[_SourceCrossing, ...]:
    collection = _mapping(value, "pedestrian_crossings")
    crossings: list[_SourceCrossing] = []
    seen: set[int] = set()
    for key, value in collection.items():
        raw = _mapping(value, f"pedestrian_crossings[{key!r}]")
        for name in ("edge1", "edge2"):
            if name not in raw:
                raise SchemaError(f"pedestrian crossing is missing {name!r}")
        crossing_id = _entity_id(key, raw, "pedestrian crossing")
        if crossing_id in seen:
            raise SchemaError("pedestrian crossing IDs must be unique")
        seen.add(crossing_id)
        edge1 = _points(raw["edge1"], "edge1")
        edge2 = _points(raw["edge2"], "edge2")
        if len(edge1) != 2 or len(edge2) != 2:
            raise SchemaError("pedestrian crossing edges must contain two points")
        ring = (edge1[0], edge1[1], edge2[1], edge2[0], edge1[0])
        geometry = Polygon(ring)
        if geometry.is_empty or not geometry.is_valid:
            raise SchemaError("pedestrian crossing is not valid polygon geometry")
        crossings.append(
            _SourceCrossing(
                crossing_id=crossing_id,
                edge1=edge1,
                edge2=edge2,
            )
        )
    return tuple(sorted(crossings, key=lambda item: item.crossing_id))


def _parse_source_root(value: object) -> _ParsedMap:
    root = _mapping(value, "source map")
    for name in ("lane_segments", "drivable_areas"):
        if name not in root:
            raise SchemaError(f"source map is missing {name!r}")
    return _ParsedMap(
        lanes=_parse_lanes(root["lane_segments"]),
        areas=_parse_areas(root["drivable_areas"]),
        crossings=_parse_crossings(root.get("pedestrian_crossings", {})),
    )


def _unique_object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SchemaError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _validated_source_path(
    source_root: Path,
    relative_path: str | Path,
    config: Av2VectorMapAdapterConfig,
) -> tuple[Path, Path, str]:
    if not isinstance(source_root, Path):
        raise ArtifactError("source_root must be a Path")
    try:
        root = source_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("source_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("source_root must be a directory")
    try:
        normalized = normalize_relative_path(relative_path)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    match = _FILENAME_PATTERN.fullmatch(normalized.name)
    if match is None:
        raise ArtifactError("source map filename does not follow the AV2 pattern")
    try:
        source_map_id = _safe_source_map_id(match.group(1), "source_map_id")
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    candidate = root / normalized
    if candidate.is_symlink():
        raise ArtifactError("source map file must not be a symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("source map file is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("source map file resolves outside source_root")
    if not resolved.is_file():
        raise ArtifactError("source map path must identify a regular file")
    try:
        size = resolved.stat().st_size
    except OSError as error:
        raise ArtifactError("source map file cannot be inspected") from error
    if size > config.max_source_bytes:
        raise ArtifactError("source map file exceeds max_source_bytes")
    return normalized, resolved, source_map_id


def _read_source(
    path: Path,
    *,
    max_source_bytes: int,
) -> tuple[str, object]:
    digest = hashlib.sha256()
    content = bytearray()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_READ_CHUNK_SIZE):
                content.extend(chunk)
                digest.update(chunk)
                if len(content) > max_source_bytes:
                    raise ArtifactError("source map file exceeds max_source_bytes")
    except ArtifactError:
        raise
    except OSError as error:
        raise ArtifactError("source map file cannot be read") from error
    try:
        text = bytes(content).decode("utf-8")
    except UnicodeDecodeError as error:
        raise SchemaError("source map is not valid UTF-8") from error
    try:
        decoded = json.loads(text, object_pairs_hook=_unique_object_pairs)
    except SchemaError:
        raise
    except json.JSONDecodeError as error:
        raise SchemaError("source map JSON is malformed") from error
    return digest.hexdigest(), decoded


def _inspect_source(
    source_root: Path,
    relative_path: str | Path,
    config: Av2VectorMapAdapterConfig,
) -> tuple[Av2VectorMapSourceSummary, _ParsedMap]:
    if not isinstance(config, Av2VectorMapAdapterConfig):
        raise ValidationError("config must be an Av2VectorMapAdapterConfig")
    normalized, path, source_map_id = _validated_source_path(
        source_root,
        relative_path,
        config,
    )
    checksum, decoded = _read_source(
        path,
        max_source_bytes=config.max_source_bytes,
    )
    parsed = _parse_source_root(decoded)
    return (
        Av2VectorMapSourceSummary(
            source_relative_path=normalized,
            source_checksum=checksum,
            source_map_id=source_map_id,
            lane_segment_count=len(parsed.lanes),
            drivable_area_count=len(parsed.areas),
            pedestrian_crossing_count=len(parsed.crossings),
            omitted_external_lane_reference_count=sum(
                lane.omitted_external_reference_count for lane in parsed.lanes
            ),
        ),
        parsed,
    )


def inspect_av2_vector_map_file(
    source_root: Path,
    relative_path: str | Path,
    *,
    config: Av2VectorMapAdapterConfig = _DEFAULT_CONFIG,
) -> Av2VectorMapSourceSummary:
    """Inspect and fully validate one AV2 vector-map JSON source."""
    summary, _parsed = _inspect_source(source_root, relative_path, config)
    return summary


def _validate_frame_alignment(
    scenario: ScenarioRecord,
    frame: CoordinateFrameRecord,
    source_map_id: str,
) -> None:
    if not isinstance(scenario, ScenarioRecord):
        raise ValidationError("scenario must be a ScenarioRecord")
    if not isinstance(frame, CoordinateFrameRecord):
        raise ValidationError("coordinate_frame must be a CoordinateFrameRecord")
    checks = (
        (scenario.dataset_id == _DATASET_ID, "scenario dataset is not av2_motion"),
        (
            scenario.source_scenario_id is not None,
            "scenario source_scenario_id is required",
        ),
        (
            scenario.source_scenario_id == source_map_id,
            "scenario source ID differs from source map ID",
        ),
        (scenario.source_map_available, "scenario source map is not available"),
        (
            frame.scenario_id == scenario.scenario_id,
            "coordinate-frame scenario differs",
        ),
        (
            frame.coordinate_frame_id == scenario.coordinate_frame_id,
            "coordinate-frame identifier differs",
        ),
        (
            scenario.source_crs == _SOURCE_CRS,
            "scenario source CRS differs",
        ),
        (frame.source_crs == _SOURCE_CRS, "coordinate-frame source CRS differs"),
        (frame.frame_type == "local_cartesian", "frame type differs"),
        (
            frame.axis_convention == "right_handed_x_y_z_up",
            "axis convention differs",
        ),
        (frame.distance_unit == "m", "distance unit differs"),
        (frame.angle_unit == "rad", "angle unit differs"),
        (frame.timestamp_unit == "ns", "timestamp unit differs"),
        (
            frame.origin_x_m == scenario.origin_x_m
            and frame.origin_y_m == scenario.origin_y_m,
            "coordinate-frame origin differs from scenario origin",
        ),
        (
            math.isfinite(frame.origin_x_m) and math.isfinite(frame.origin_y_m),
            "coordinate-frame origin must be finite",
        ),
    )
    for valid, message in checks:
        if not valid:
            raise SchemaError(message)


def _local_points(
    points: tuple[_Point3D, ...],
    frame: CoordinateFrameRecord,
) -> tuple[_Point3D, ...]:
    return tuple((x - frame.origin_x_m, y - frame.origin_y_m, z) for x, y, z in points)


def _resample_polyline(
    points: tuple[_Point3D, ...],
    count: int,
) -> tuple[_Point3D, ...]:
    segment_lengths = tuple(
        _distance(first, second) for first, second in pairwise(points)
    )
    total = sum(segment_lengths)
    if not math.isfinite(total) or total <= 0.0:
        raise SchemaError("cannot interpolate a zero-length boundary")
    cumulative = [0.0]
    for length in segment_lengths:
        cumulative.append(cumulative[-1] + length)
    result: list[_Point3D] = []
    segment_index = 0
    for index in range(count):
        target = total * index / (count - 1)
        while (
            segment_index < len(segment_lengths) - 1
            and target > cumulative[segment_index + 1]
        ):
            segment_index += 1
        length = segment_lengths[segment_index]
        if length <= 0.0:
            while (
                segment_index < len(segment_lengths) - 1
                and segment_lengths[segment_index] <= 0.0
            ):
                segment_index += 1
            length = segment_lengths[segment_index]
        fraction = (target - cumulative[segment_index]) / length
        start = points[segment_index]
        end = points[segment_index + 1]
        point = tuple(
            start[axis] + fraction * (end[axis] - start[axis]) for axis in range(3)
        )
        if any(not math.isfinite(value) for value in point):
            raise SchemaError("centerline interpolation produced nonfinite values")
        result.append(cast(_Point3D, point))
    return tuple(result)


def _map_id(source_map_id: str, *parts: str) -> str:
    try:
        return make_identifier("map", "av2", source_map_id, *parts)
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def _lane_centerline_id(source_map_id: str, lane_id: int) -> str:
    return _map_id(source_map_id, "lane", str(lane_id), "centerline")


def _semantic_attributes(**values: object) -> str:
    try:
        return canonical_json_text(values, trailing_newline=False)
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def _map_record(**values: Any) -> VectorMapElementRecord:
    try:
        return VectorMapElementRecord(**values)
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def _lane_elements(
    lane: _SourceLane,
    *,
    source_map_id: str,
    scenario_id: str,
    frame: CoordinateFrameRecord,
    centerline_point_count: int,
) -> tuple[VectorMapElementRecord, ...]:
    centerline_id = _lane_centerline_id(source_map_id, lane.lane_id)
    left_id = _map_id(
        source_map_id,
        "lane",
        str(lane.lane_id),
        "left-boundary",
    )
    right_id = _map_id(
        source_map_id,
        "lane",
        str(lane.lane_id),
        "right-boundary",
    )
    left = _local_points(lane.left_boundary, frame)
    right = _local_points(lane.right_boundary, frame)
    left_resampled = _resample_polyline(left, centerline_point_count)
    right_resampled = _resample_polyline(right, centerline_point_count)
    centerline = tuple(
        (
            (left_point[0] + right_point[0]) / 2.0,
            (left_point[1] + right_point[1]) / 2.0,
            (left_point[2] + right_point[2]) / 2.0,
        )
        for left_point, right_point in zip(
            left_resampled,
            right_resampled,
            strict=True,
        )
    )
    common: dict[str, object] = {
        "scenario_id": scenario_id,
        "origin_type": OriginType.SOURCE_GROUND_TRUTH,
        "quality_flags": (),
    }
    provenance: dict[str, object] = {}
    quality_flags: list[str] = []
    if lane.external_predecessors:
        provenance["omitted_external_predecessor_ids"] = list(
            lane.external_predecessors
        )
        quality_flags.append("source_external_predecessors_omitted")
    if lane.external_successors:
        provenance["omitted_external_successor_ids"] = list(lane.external_successors)
        quality_flags.append("source_external_successors_omitted")
    if lane.external_left_neighbor_id is not None:
        provenance["omitted_external_left_neighbor_id"] = lane.external_left_neighbor_id
        quality_flags.append("source_external_left_neighbor_omitted")
    if lane.external_right_neighbor_id is not None:
        provenance["omitted_external_right_neighbor_id"] = (
            lane.external_right_neighbor_id
        )
        quality_flags.append("source_external_right_neighbor_omitted")
    centerline_record = _map_record(
        **{**common, "quality_flags": tuple(quality_flags)},
        map_element_id=centerline_id,
        element_type=MapElementType.LANE_CENTERLINE,
        geometry_type=MapGeometryType.LINESTRING,
        geometry_wkb=geometry_to_canonical_wkb(LineString(centerline)),
        directionality=Directionality.DIRECTED,
        parent_element_id=None,
        successor_ids=tuple(
            _lane_centerline_id(source_map_id, item) for item in lane.successors
        ),
        predecessor_ids=tuple(
            _lane_centerline_id(source_map_id, item) for item in lane.predecessors
        ),
        left_neighbor_id=(
            None
            if lane.left_neighbor_id is None
            else _lane_centerline_id(source_map_id, lane.left_neighbor_id)
        ),
        right_neighbor_id=(
            None
            if lane.right_neighbor_id is None
            else _lane_centerline_id(source_map_id, lane.right_neighbor_id)
        ),
        semantic_attributes_json=_semantic_attributes(
            source_kind="lane_centerline",
            source_lane_id=lane.lane_id,
            lane_type=lane.lane_type.value,
            is_intersection=lane.is_intersection,
            centerline_point_count=centerline_point_count,
            source_geometry_has_z=True,
            **provenance,
        ),
    )

    def boundary_record(
        side: str,
        element_id: str,
        points: tuple[_Point3D, ...],
        mark: Av2LaneMarkType,
    ) -> VectorMapElementRecord:
        return _map_record(
            **common,
            map_element_id=element_id,
            element_type=MapElementType.LANE_BOUNDARY,
            geometry_type=MapGeometryType.LINESTRING,
            geometry_wkb=geometry_to_canonical_wkb(LineString(points)),
            directionality=Directionality.NOT_APPLICABLE,
            parent_element_id=centerline_id,
            successor_ids=(),
            predecessor_ids=(),
            left_neighbor_id=None,
            right_neighbor_id=None,
            semantic_attributes_json=_semantic_attributes(
                source_kind="lane_boundary",
                source_lane_id=lane.lane_id,
                boundary_side=side,
                lane_mark_type=mark.value,
                source_geometry_has_z=True,
            ),
        )

    return (
        centerline_record,
        boundary_record("left", left_id, left, lane.left_mark),
        boundary_record("right", right_id, right, lane.right_mark),
    )


def _area_element(
    area: _SourceArea,
    *,
    source_map_id: str,
    scenario_id: str,
    frame: CoordinateFrameRecord,
) -> VectorMapElementRecord:
    return _map_record(
        scenario_id=scenario_id,
        map_element_id=_map_id(
            source_map_id,
            "drivable-area",
            str(area.area_id),
        ),
        element_type=MapElementType.ROAD_AREA,
        geometry_type=MapGeometryType.POLYGON,
        geometry_wkb=geometry_to_canonical_wkb(
            Polygon(_local_points(area.boundary, frame))
        ),
        directionality=Directionality.NOT_APPLICABLE,
        parent_element_id=None,
        successor_ids=(),
        predecessor_ids=(),
        left_neighbor_id=None,
        right_neighbor_id=None,
        semantic_attributes_json=_semantic_attributes(
            source_kind="drivable_area",
            source_area_id=area.area_id,
            source_geometry_has_z=True,
        ),
        origin_type=OriginType.SOURCE_GROUND_TRUTH,
        quality_flags=(),
    )


def _crossing_element(
    crossing: _SourceCrossing,
    *,
    source_map_id: str,
    scenario_id: str,
    frame: CoordinateFrameRecord,
) -> VectorMapElementRecord:
    edge1 = _local_points(crossing.edge1, frame)
    edge2 = _local_points(crossing.edge2, frame)
    ring = (edge1[0], edge1[1], edge2[1], edge2[0], edge1[0])
    return _map_record(
        scenario_id=scenario_id,
        map_element_id=_map_id(
            source_map_id,
            "crosswalk",
            str(crossing.crossing_id),
        ),
        element_type=MapElementType.CROSSWALK,
        geometry_type=MapGeometryType.POLYGON,
        geometry_wkb=geometry_to_canonical_wkb(Polygon(ring)),
        directionality=Directionality.BIDIRECTIONAL,
        parent_element_id=None,
        successor_ids=(),
        predecessor_ids=(),
        left_neighbor_id=None,
        right_neighbor_id=None,
        semantic_attributes_json=_semantic_attributes(
            source_kind="pedestrian_crossing",
            source_crossing_id=crossing.crossing_id,
            source_geometry_has_z=True,
        ),
        origin_type=OriginType.SOURCE_GROUND_TRUTH,
        quality_flags=(),
    )


def load_av2_vector_map(
    source_root: Path,
    relative_path: str | Path,
    *,
    scenario: ScenarioRecord,
    coordinate_frame: CoordinateFrameRecord,
    config: Av2VectorMapAdapterConfig = _DEFAULT_CONFIG,
) -> Av2VectorMapConversion:
    """Load and convert one AV2 vector-map JSON file."""
    summary, parsed = _inspect_source(source_root, relative_path, config)
    _validate_frame_alignment(scenario, coordinate_frame, summary.source_map_id)
    elements = (
        tuple(
            element
            for lane in parsed.lanes
            for element in _lane_elements(
                lane,
                source_map_id=summary.source_map_id,
                scenario_id=scenario.scenario_id,
                frame=coordinate_frame,
                centerline_point_count=config.centerline_point_count,
            )
        )
        + tuple(
            _area_element(
                area,
                source_map_id=summary.source_map_id,
                scenario_id=scenario.scenario_id,
                frame=coordinate_frame,
            )
            for area in parsed.areas
        )
        + tuple(
            _crossing_element(
                crossing,
                source_map_id=summary.source_map_id,
                scenario_id=scenario.scenario_id,
                frame=coordinate_frame,
            )
            for crossing in parsed.crossings
        )
    )
    counts = tuple(
        (
            element_type.value,
            sum(item.element_type is element_type for item in elements),
        )
        for element_type in MapElementType
    )
    try:
        return Av2VectorMapConversion(
            source_relative_path=summary.source_relative_path,
            source_checksum=summary.source_checksum,
            source_map_id=summary.source_map_id,
            adapter_version=config.adapter_version,
            dataset_version=scenario.dataset_version,
            centerline_point_count=config.centerline_point_count,
            scenario_id=scenario.scenario_id,
            coordinate_frame_id=coordinate_frame.coordinate_frame_id,
            lane_segment_count=summary.lane_segment_count,
            drivable_area_count=summary.drivable_area_count,
            pedestrian_crossing_count=summary.pedestrian_crossing_count,
            omitted_external_lane_reference_count=(
                summary.omitted_external_lane_reference_count
            ),
            elements=elements,
            element_type_counts=counts,
        )
    except ValidationError as error:
        raise SchemaError(str(error)) from None


def av2_vector_map_conversion_summary_to_dict(
    conversion: Av2VectorMapConversion,
) -> dict[str, object]:
    """Return the ordered JSON-compatible AV2 vector-map summary."""
    if not isinstance(conversion, Av2VectorMapConversion):
        raise ValidationError("conversion must be an Av2VectorMapConversion")
    return {
        "schema_version": _SCHEMA_VERSION,
        "adapter_name": _ADAPTER_NAME,
        "adapter_version": conversion.adapter_version,
        "dataset_id": _DATASET_ID,
        "dataset_version": conversion.dataset_version,
        "source_relative_path": relative_path_text(conversion.source_relative_path),
        "source_checksum": conversion.source_checksum,
        "source_map_id": conversion.source_map_id,
        "canonical_scenario_id": conversion.scenario_id,
        "canonical_coordinate_frame_id": conversion.coordinate_frame_id,
        "centerline_point_count": conversion.centerline_point_count,
        "source_lane_segment_count": conversion.lane_segment_count,
        "source_drivable_area_count": conversion.drivable_area_count,
        "source_pedestrian_crossing_count": (conversion.pedestrian_crossing_count),
        "omitted_external_lane_reference_count": (
            conversion.omitted_external_lane_reference_count
        ),
        "canonical_element_count": len(conversion.elements),
        "element_type_counts": [
            [key, count] for key, count in conversion.element_type_counts
        ],
        "canonical_map_element_ids": [
            item.map_element_id for item in conversion.elements
        ],
    }


def materialize_av2_vector_map(
    run_directory: RunDirectory,
    conversion: Av2VectorMapConversion,
    *,
    relative_directory: str | Path = "artifacts/av2_vector_map",
    row_group_size: int = 65_536,
) -> Av2VectorMapArtifacts:
    """Materialize one AV2 map summary and canonical Parquet table."""
    if not isinstance(conversion, Av2VectorMapConversion):
        raise ValidationError("conversion must be an Av2VectorMapConversion")
    try:
        directory = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    summary = atomic_write_canonical_json(
        run_directory,
        directory / "adapter_summary.json",
        av2_vector_map_conversion_summary_to_dict(conversion),
    )
    table = atomic_write_canonical_parquet(
        run_directory,
        directory / "vector_map_elements.parquet",
        vector_map_elements_to_table(conversion.elements),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        row_group_size=row_group_size,
    )
    return Av2VectorMapArtifacts(
        adapter_summary=summary,
        vector_map_elements=table,
    )


def _verified_summary_bytes(
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
    if path.is_symlink():
        raise ArtifactError("adapter summary must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("adapter summary is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("adapter summary resolves outside repository")
    if not resolved.is_file():
        raise ArtifactError("adapter summary is not a regular file")
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise ArtifactError("adapter summary cannot be read") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("adapter summary size differs")
    if hashlib.sha256(data).hexdigest() != artifact.content_checksum:
        raise ArtifactError("adapter summary checksum differs")
    return data


def verify_av2_vector_map_artifacts(
    repository_root: Path,
    conversion: Av2VectorMapConversion,
    artifacts: Av2VectorMapArtifacts,
) -> None:
    """Verify AV2 map summary, canonical rows, geometry, and references."""
    if not isinstance(conversion, Av2VectorMapConversion):
        raise ValidationError("conversion must be an Av2VectorMapConversion")
    if not isinstance(artifacts, Av2VectorMapArtifacts):
        raise ValidationError("artifacts must be an Av2VectorMapArtifacts")
    data = _verified_summary_bytes(repository_root, artifacts.adapter_summary)
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SchemaError("adapter summary is malformed") from error
    expected_summary = av2_vector_map_conversion_summary_to_dict(conversion)
    if not isinstance(decoded, Mapping) or decoded != expected_summary:
        raise SchemaError("adapter summary content differs from conversion")
    verify_canonical_parquet_artifact(
        repository_root,
        artifacts.vector_map_elements,
    )
    table = read_canonical_parquet_table(
        repository_root,
        (artifacts.vector_map_elements.written_artifact.relative_path,),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    )
    expected_rows = vector_map_elements_to_table(conversion.elements).to_pylist()
    actual_rows = table.to_pylist()
    if actual_rows != expected_rows:
        raise SchemaError("canonical vector-map table content differs")
    try:
        reconstructed = tuple(
            VectorMapElementRecord(**cast(Any, row)) for row in actual_rows
        )
        for element in reconstructed:
            geometry_from_canonical_wkb(element.geometry_wkb)
        Av2VectorMapConversion(
            source_relative_path=conversion.source_relative_path,
            source_checksum=conversion.source_checksum,
            source_map_id=conversion.source_map_id,
            adapter_version=conversion.adapter_version,
            dataset_version=conversion.dataset_version,
            centerline_point_count=conversion.centerline_point_count,
            scenario_id=conversion.scenario_id,
            coordinate_frame_id=conversion.coordinate_frame_id,
            lane_segment_count=conversion.lane_segment_count,
            drivable_area_count=conversion.drivable_area_count,
            pedestrian_crossing_count=conversion.pedestrian_crossing_count,
            omitted_external_lane_reference_count=(
                conversion.omitted_external_lane_reference_count
            ),
            elements=reconstructed,
            element_type_counts=conversion.element_type_counts,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise SchemaError(str(error)) from None
