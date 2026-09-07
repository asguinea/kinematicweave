"""Canonical vector-map geometry and immutable domain records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
import math

from shapely import from_wkb, get_coordinates, to_wkb  # type: ignore[import-untyped]
from shapely.errors import GEOSException  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_text
from kinematicweave.domain.records import OriginType
from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import validate_identifier

__all__ = [
    "Directionality",
    "MapElementType",
    "MapGeometryType",
    "VectorMapElementRecord",
    "geometry_from_canonical_wkb",
    "geometry_to_canonical_wkb",
    "vector_map_element_record_to_dict",
]


class MapElementType(StrEnum):
    """Canonical vector-map element categories."""

    LANE_CENTERLINE = "lane_centerline"
    LANE_BOUNDARY = "lane_boundary"
    ROAD_AREA = "road_area"
    CROSSWALK = "crosswalk"
    WALKWAY = "walkway"
    JUNCTION_AREA = "junction_area"
    ACCESS_ZONE = "access_zone"
    OTHER = "other"


class MapGeometryType(StrEnum):
    """Supported persistent map geometry types."""

    POINT = "point"
    LINESTRING = "linestring"
    POLYGON = "polygon"
    MULTILINESTRING = "multilinestring"
    MULTIPOLYGON = "multipolygon"


class Directionality(StrEnum):
    """Canonical map-element directionality."""

    DIRECTED = "directed"
    BIDIRECTIONAL = "bidirectional"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


_GEOMETRY_TYPES: dict[type[BaseGeometry], MapGeometryType] = {
    Point: MapGeometryType.POINT,
    LineString: MapGeometryType.LINESTRING,
    Polygon: MapGeometryType.POLYGON,
    MultiLineString: MapGeometryType.MULTILINESTRING,
    MultiPolygon: MapGeometryType.MULTIPOLYGON,
}


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _enum_value[EnumT: StrEnum](
    enum_type: type[EnumT],
    value: object,
    field_name: str,
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValidationError(f"{field_name} has invalid value {value!r}")


def _validated_geometry(geometry: object) -> BaseGeometry:
    if not isinstance(geometry, BaseGeometry):
        raise ValidationError("geometry must be a Shapely BaseGeometry")
    if isinstance(geometry, (GeometryCollection, LinearRing)):
        raise ValidationError(f"unsupported geometry type: {geometry.geom_type}")
    if type(geometry) not in _GEOMETRY_TYPES:
        raise ValidationError(f"unsupported geometry type: {geometry.geom_type}")
    if geometry.is_empty:
        raise ValidationError("geometry must not be empty")
    coordinates = get_coordinates(geometry, include_z=geometry.has_z)
    if coordinates.size == 0:
        raise ValidationError("geometry must contain coordinates")
    if any(
        not math.isfinite(float(value))
        for coordinate in coordinates
        for value in coordinate
    ):
        raise ValidationError("geometry coordinates must be finite")
    if not geometry.is_valid:
        raise ValidationError("geometry must be valid")
    return geometry


def geometry_to_canonical_wkb(geometry: BaseGeometry) -> bytes:
    """Encode supported geometry as deterministic little-endian ISO WKB."""
    validated = _validated_geometry(geometry)
    try:
        encoded = to_wkb(
            validated,
            hex=False,
            output_dimension=3 if validated.has_z else 2,
            byte_order=1,
            include_srid=False,
            flavor="iso",
        )
    except (GEOSException, TypeError, ValueError) as error:
        raise ValidationError("geometry cannot be encoded as canonical WKB") from error
    if not isinstance(encoded, bytes):
        raise ValidationError("canonical WKB encoder returned incompatible bytes")
    return encoded


def geometry_from_canonical_wkb(value: bytes) -> BaseGeometry:
    """Decode and validate canonical map geometry from WKB bytes."""
    if not isinstance(value, bytes):
        raise ValidationError("geometry_wkb must be bytes")
    try:
        geometry = from_wkb(value, on_invalid="raise")
    except (GEOSException, TypeError, ValueError) as error:
        raise ValidationError("geometry_wkb is malformed") from error
    return _validated_geometry(geometry)


def _references(
    value: object,
    field_name: str,
    owner_id: str,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    normalized = tuple(
        validate_identifier(_required_text(item, f"{field_name} item"))
        for item in value
    )
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    if owner_id in normalized:
        raise ValidationError(f"{field_name} must not contain a self reference")
    return normalized


def _optional_reference(
    value: object,
    field_name: str,
    owner_id: str,
) -> str | None:
    if value is None:
        return None
    normalized = validate_identifier(_required_text(value, field_name))
    if normalized == owner_id:
        raise ValidationError(f"{field_name} must not reference the element itself")
    return normalized


def _semantic_json(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("semantic_attributes_json must be a string or None")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValidationError("semantic_attributes_json is malformed") from error
    if not isinstance(decoded, Mapping):
        raise ValidationError("semantic_attributes_json must contain a JSON object")
    try:
        return canonical_json_text(decoded, trailing_newline=False)
    except ValidationError as error:
        raise ValidationError(
            "semantic_attributes_json is not canonicalizable"
        ) from error


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


@dataclass(frozen=True, slots=True)
class VectorMapElementRecord:
    """Validated canonical vector-map element."""

    scenario_id: str
    map_element_id: str
    element_type: MapElementType | str
    geometry_type: MapGeometryType | str
    geometry_wkb: bytes
    directionality: Directionality | str
    parent_element_id: str | None
    successor_ids: Sequence[str]
    predecessor_ids: Sequence[str]
    left_neighbor_id: str | None
    right_neighbor_id: str | None
    semantic_attributes_json: str | None
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        """Normalize fields and enforce geometry and reference integrity."""
        scenario_id = validate_identifier(self.scenario_id)
        element_id = validate_identifier(self.map_element_id)
        element_type = _enum_value(
            MapElementType,
            self.element_type,
            "element_type",
        )
        geometry_type = _enum_value(
            MapGeometryType,
            self.geometry_type,
            "geometry_type",
        )
        geometry = geometry_from_canonical_wkb(self.geometry_wkb)
        actual_geometry_type = _GEOMETRY_TYPES[type(geometry)]
        if actual_geometry_type is not geometry_type:
            raise ValidationError("geometry type does not match declared geometry_type")
        parent = _optional_reference(
            self.parent_element_id,
            "parent_element_id",
            element_id,
        )
        successors = _references(self.successor_ids, "successor_ids", element_id)
        predecessors = _references(
            self.predecessor_ids,
            "predecessor_ids",
            element_id,
        )
        left = _optional_reference(
            self.left_neighbor_id,
            "left_neighbor_id",
            element_id,
        )
        right = _optional_reference(
            self.right_neighbor_id,
            "right_neighbor_id",
            element_id,
        )
        if left is not None and left == right:
            raise ValidationError("left and right neighbor identifiers must differ")
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "map_element_id", element_id)
        object.__setattr__(self, "element_type", element_type)
        object.__setattr__(self, "geometry_type", geometry_type)
        object.__setattr__(
            self,
            "geometry_wkb",
            geometry_to_canonical_wkb(geometry),
        )
        object.__setattr__(
            self,
            "directionality",
            _enum_value(Directionality, self.directionality, "directionality"),
        )
        object.__setattr__(self, "parent_element_id", parent)
        object.__setattr__(self, "successor_ids", successors)
        object.__setattr__(self, "predecessor_ids", predecessors)
        object.__setattr__(self, "left_neighbor_id", left)
        object.__setattr__(self, "right_neighbor_id", right)
        object.__setattr__(
            self,
            "semantic_attributes_json",
            _semantic_json(self.semantic_attributes_json),
        )
        object.__setattr__(
            self,
            "origin_type",
            _enum_value(OriginType, self.origin_type, "origin_type"),
        )
        object.__setattr__(self, "quality_flags", _quality_flags(self.quality_flags))


def vector_map_element_record_to_dict(
    record: VectorMapElementRecord,
) -> dict[str, object]:
    """Return a fresh schema-ordered dictionary for one map element."""
    if not isinstance(record, VectorMapElementRecord):
        raise ValidationError("record must be a VectorMapElementRecord")
    return {
        "scenario_id": record.scenario_id,
        "map_element_id": record.map_element_id,
        "element_type": _enum_value(
            MapElementType,
            record.element_type,
            "element_type",
        ).value,
        "geometry_type": _enum_value(
            MapGeometryType,
            record.geometry_type,
            "geometry_type",
        ).value,
        "geometry_wkb": record.geometry_wkb,
        "directionality": _enum_value(
            Directionality,
            record.directionality,
            "directionality",
        ).value,
        "parent_element_id": record.parent_element_id,
        "successor_ids": list(record.successor_ids),
        "predecessor_ids": list(record.predecessor_ids),
        "left_neighbor_id": record.left_neighbor_id,
        "right_neighbor_id": record.right_neighbor_id,
        "semantic_attributes_json": record.semantic_attributes_json,
        "origin_type": _enum_value(
            OriginType,
            record.origin_type,
            "origin_type",
        ).value,
        "quality_flags": list(record.quality_flags),
    }
