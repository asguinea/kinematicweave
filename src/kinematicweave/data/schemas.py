"""Versioned canonical Arrow and Polars schema definitions."""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_bytes, canonical_sha256
from kinematicweave.errors import SchemaError

__all__ = [
    "CanonicalSchemaDefinition",
    "CanonicalSchemaName",
    "canonical_schema_names",
    "get_arrow_schema",
    "get_polars_schema",
    "get_schema_definition",
    "schema_definition_to_dict",
    "schema_fingerprint",
    "scientific_data_library_versions",
    "validate_arrow_schema",
]

_SCHEMA_VERSION = "1.0"
_METADATA_KEYS = (
    b"schema_name",
    b"schema_version",
    b"primary_key",
    b"canonical_order",
)


class CanonicalSchemaName(StrEnum):
    """Names of the canonical tabular schemas."""

    SCENARIO_MANIFEST = "scenario_manifest"
    COORDINATE_FRAME_METADATA = "coordinate_frame_metadata"
    AGENT_METADATA = "agent_metadata"
    TRAJECTORY_SAMPLES = "trajectory_samples"
    VECTOR_MAP_ELEMENTS = "vector_map_elements"
    PROCEDURAL_TAPE_MANIFEST = "procedural_tape_manifest"
    PROCEDURAL_TRACKS = "procedural_tracks"
    PROCEDURAL_SEGMENTS = "procedural_segments"
    SEMANTIC_WAYPOINTS = "semantic_waypoints"
    MOTION_EVENTS = "motion_events"
    MOTION_CATEGORIES = "motion_categories"
    ROUTE_TEMPLATES = "route_templates"
    ROUTE_TEMPLATE_MEMBERSHIPS = "route_template_memberships"


@dataclass(frozen=True, slots=True)
class CanonicalSchemaDefinition:
    """Immutable definition of one versioned canonical table schema."""

    name: CanonicalSchemaName
    version: str
    arrow_schema: pa.Schema
    primary_key: tuple[str, ...]
    canonical_order: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate definition-level invariants."""
        if not isinstance(self.name, CanonicalSchemaName):
            raise SchemaError("schema name must use CanonicalSchemaName")
        if self.version != _SCHEMA_VERSION:
            raise SchemaError(f"schema version must be {_SCHEMA_VERSION}")
        if not isinstance(self.arrow_schema, pa.Schema):
            raise SchemaError("arrow_schema must be a pyarrow.Schema")
        _validate_field_tuple("primary key", self.primary_key, self.arrow_schema)
        _validate_field_tuple(
            "canonical order",
            self.canonical_order,
            self.arrow_schema,
        )


def _validate_field_tuple(
    label: str,
    fields: tuple[str, ...],
    schema: pa.Schema,
) -> None:
    if not isinstance(fields, tuple) or any(
        not isinstance(field, str) for field in fields
    ):
        raise SchemaError(f"{label} must be a tuple of field names")
    if len(fields) != len(set(fields)):
        raise SchemaError(f"{label} contains duplicate fields")
    missing = tuple(field for field in fields if field not in schema.names)
    if missing:
        raise SchemaError(f"{label} field does not exist: {missing[0]}")


def _schema_metadata(
    name: CanonicalSchemaName,
    primary_key: tuple[str, ...],
    canonical_order: tuple[str, ...],
) -> dict[bytes, bytes]:
    return {
        b"schema_name": name.value.encode("utf-8"),
        b"schema_version": _SCHEMA_VERSION.encode("utf-8"),
        b"primary_key": canonical_json_bytes(list(primary_key)),
        b"canonical_order": canonical_json_bytes(list(canonical_order)),
    }


def _definition(
    name: CanonicalSchemaName,
    fields: tuple[pa.Field, ...],
    primary_key: tuple[str, ...],
    canonical_order: tuple[str, ...],
) -> CanonicalSchemaDefinition:
    return CanonicalSchemaDefinition(
        name=name,
        version=_SCHEMA_VERSION,
        arrow_schema=pa.schema(
            fields,
            metadata=_schema_metadata(name, primary_key, canonical_order),
        ),
        primary_key=primary_key,
        canonical_order=canonical_order,
    )


_SCHEMA_DEFINITIONS = (
    _definition(
        CanonicalSchemaName.SCENARIO_MANIFEST,
        (
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("dataset_id", pa.string(), nullable=False),
            pa.field("dataset_version", pa.string(), nullable=False),
            pa.field("split_name", pa.string(), nullable=False),
            pa.field("city_or_region", pa.string()),
            pa.field("source_scenario_id", pa.string()),
            pa.field("start_time_ns", pa.int64(), nullable=False),
            pa.field("end_time_ns", pa.int64(), nullable=False),
            pa.field("coordinate_frame_id", pa.string(), nullable=False),
            pa.field("origin_x_m", pa.float64(), nullable=False),
            pa.field("origin_y_m", pa.float64(), nullable=False),
            pa.field("origin_z_m", pa.float64()),
            pa.field("source_crs", pa.string()),
            pa.field("has_elevation", pa.bool_(), nullable=False),
            pa.field("agent_count", pa.int32(), nullable=False),
            pa.field("source_map_available", pa.bool_(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
            pa.field("adapter_name", pa.string(), nullable=False),
            pa.field("adapter_version", pa.string(), nullable=False),
            pa.field("source_checksum", pa.string()),
        ),
        ("scenario_id",),
        ("scenario_id",),
    ),
    _definition(
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        (
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("coordinate_frame_id", pa.string(), nullable=False),
            pa.field("parent_frame_id", pa.string()),
            pa.field("frame_type", pa.string(), nullable=False),
            pa.field("origin_x_m", pa.float64(), nullable=False),
            pa.field("origin_y_m", pa.float64(), nullable=False),
            pa.field("origin_z_m", pa.float64()),
            pa.field("axis_convention", pa.string(), nullable=False),
            pa.field("distance_unit", pa.string(), nullable=False),
            pa.field("angle_unit", pa.string(), nullable=False),
            pa.field("timestamp_unit", pa.string(), nullable=False),
            pa.field("source_crs", pa.string()),
            pa.field("has_elevation", pa.bool_(), nullable=False),
            pa.field("transform_to_parent_4x4", pa.list_(pa.float64())),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("scenario_id", "coordinate_frame_id"),
        ("scenario_id", "coordinate_frame_id"),
    ),
    _definition(
        CanonicalSchemaName.AGENT_METADATA,
        (
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("agent_id", pa.string(), nullable=False),
            pa.field("source_agent_id", pa.string()),
            pa.field("agent_class", pa.string(), nullable=False),
            pa.field("length_m", pa.float32()),
            pa.field("width_m", pa.float32()),
            pa.field("height_m", pa.float32()),
            pa.field("first_time_ns", pa.int64(), nullable=False),
            pa.field("last_time_ns", pa.int64(), nullable=False),
            pa.field("sample_count", pa.int32(), nullable=False),
            pa.field("is_focal_agent", pa.bool_(), nullable=False),
            pa.field("is_ego_agent", pa.bool_(), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("scenario_id", "agent_id"),
        ("scenario_id", "agent_id"),
    ),
    _definition(
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        (
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("agent_id", pa.string(), nullable=False),
            pa.field("trajectory_id", pa.string(), nullable=False),
            pa.field("sample_index", pa.int32(), nullable=False),
            pa.field("timestamp_ns", pa.int64(), nullable=False),
            pa.field("x_m", pa.float64(), nullable=False),
            pa.field("y_m", pa.float64(), nullable=False),
            pa.field("z_m", pa.float64()),
            pa.field("heading_rad", pa.float64()),
            pa.field("velocity_x_mps", pa.float64()),
            pa.field("velocity_y_mps", pa.float64()),
            pa.field("speed_mps", pa.float64()),
            pa.field("acceleration_x_mps2", pa.float64()),
            pa.field("acceleration_y_mps2", pa.float64()),
            pa.field("is_observed", pa.bool_(), nullable=False),
            pa.field("is_valid", pa.bool_(), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("scenario_id", "trajectory_id", "sample_index"),
        ("scenario_id", "trajectory_id", "timestamp_ns"),
    ),
    _definition(
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        (
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("map_element_id", pa.string(), nullable=False),
            pa.field("element_type", pa.string(), nullable=False),
            pa.field("geometry_type", pa.string(), nullable=False),
            pa.field("geometry_wkb", pa.binary(), nullable=False),
            pa.field("directionality", pa.string(), nullable=False),
            pa.field("parent_element_id", pa.string()),
            pa.field("successor_ids", pa.list_(pa.string()), nullable=False),
            pa.field("predecessor_ids", pa.list_(pa.string()), nullable=False),
            pa.field("left_neighbor_id", pa.string()),
            pa.field("right_neighbor_id", pa.string()),
            pa.field("semantic_attributes_json", pa.string()),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("scenario_id", "map_element_id"),
        ("scenario_id", "element_type", "map_element_id"),
    ),
    _definition(
        CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST,
        (
            pa.field("tape_id", pa.string(), nullable=False),
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("coordinate_frame_id", pa.string(), nullable=False),
            pa.field("source_dataset_id", pa.string(), nullable=False),
            pa.field("source_dataset_version", pa.string(), nullable=False),
            pa.field("source_validation_report_identity", pa.string()),
            pa.field("encoder_name", pa.string(), nullable=False),
            pa.field("encoder_version", pa.string(), nullable=False),
            pa.field("encoder_parameters_identity", pa.string(), nullable=False),
            pa.field("start_time_ns", pa.int64(), nullable=False),
            pa.field("end_time_ns", pa.int64(), nullable=False),
            pa.field("track_count", pa.int32(), nullable=False),
            pa.field("segment_count", pa.int32(), nullable=False),
            pa.field("source_sample_count", pa.int64(), nullable=False),
            pa.field("encoded_valid_sample_count", pa.int64(), nullable=False),
            pa.field("has_elevation", pa.bool_(), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("tape_id",),
        ("scenario_id", "tape_id"),
    ),
    _definition(
        CanonicalSchemaName.PROCEDURAL_TRACKS,
        (
            pa.field("tape_id", pa.string(), nullable=False),
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("procedural_track_id", pa.string(), nullable=False),
            pa.field("agent_id", pa.string(), nullable=False),
            pa.field("trajectory_id", pa.string(), nullable=False),
            pa.field("agent_class", pa.string(), nullable=False),
            pa.field("start_time_ns", pa.int64(), nullable=False),
            pa.field("end_time_ns", pa.int64(), nullable=False),
            pa.field("source_sample_count", pa.int32(), nullable=False),
            pa.field("valid_sample_count", pa.int32(), nullable=False),
            pa.field("run_count", pa.int32(), nullable=False),
            pa.field("segment_count", pa.int32(), nullable=False),
            pa.field("has_elevation", pa.bool_(), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("tape_id", "procedural_track_id"),
        ("scenario_id", "tape_id"),
    ),
    _definition(
        CanonicalSchemaName.PROCEDURAL_SEGMENTS,
        (
            pa.field("tape_id", pa.string(), nullable=False),
            pa.field("procedural_track_id", pa.string(), nullable=False),
            pa.field("segment_id", pa.string(), nullable=False),
            pa.field("run_index", pa.int32(), nullable=False),
            pa.field("segment_index", pa.int32(), nullable=False),
            pa.field("primitive_type", pa.string(), nullable=False),
            pa.field("source_start_sample_index", pa.int32(), nullable=False),
            pa.field("source_end_sample_index", pa.int32(), nullable=False),
            pa.field("start_time_ns", pa.int64(), nullable=False),
            pa.field("end_time_ns", pa.int64(), nullable=False),
            pa.field("start_x_m", pa.float64(), nullable=False),
            pa.field("start_y_m", pa.float64(), nullable=False),
            pa.field("start_z_m", pa.float64()),
            pa.field("end_x_m", pa.float64(), nullable=False),
            pa.field("end_y_m", pa.float64(), nullable=False),
            pa.field("end_z_m", pa.float64()),
            pa.field("start_heading_rad", pa.float64()),
            pa.field("end_heading_rad", pa.float64()),
            pa.field("start_velocity_x_mps", pa.float64()),
            pa.field("start_velocity_y_mps", pa.float64()),
            pa.field("end_velocity_x_mps", pa.float64()),
            pa.field("end_velocity_y_mps", pa.float64()),
            pa.field("parameter_values", pa.list_(pa.float64()), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("tape_id", "procedural_track_id", "run_index", "segment_index"),
        ("tape_id", "procedural_track_id", "run_index", "segment_index"),
    ),
    _definition(
        CanonicalSchemaName.SEMANTIC_WAYPOINTS,
        (
            pa.field("tape_id", pa.string(), nullable=False),
            pa.field("procedural_track_id", pa.string(), nullable=False),
            pa.field("waypoint_id", pa.string(), nullable=False),
            pa.field("waypoint_index", pa.int32(), nullable=False),
            pa.field("waypoint_role", pa.string(), nullable=False),
            pa.field("timestamp_ns", pa.int64(), nullable=False),
            pa.field("source_sample_index", pa.int32()),
            pa.field("x_m", pa.float64(), nullable=False),
            pa.field("y_m", pa.float64(), nullable=False),
            pa.field("z_m", pa.float64()),
            pa.field("heading_rad", pa.float64()),
            pa.field("speed_mps", pa.float64()),
            pa.field("related_event_ids", pa.list_(pa.string()), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("tape_id", "procedural_track_id", "waypoint_id"),
        ("tape_id", "procedural_track_id", "waypoint_index", "waypoint_id"),
    ),
    _definition(
        CanonicalSchemaName.MOTION_EVENTS,
        (
            pa.field("tape_id", pa.string(), nullable=False),
            pa.field("procedural_track_id", pa.string(), nullable=False),
            pa.field("event_id", pa.string(), nullable=False),
            pa.field("event_index", pa.int32(), nullable=False),
            pa.field("event_type", pa.string(), nullable=False),
            pa.field("start_waypoint_id", pa.string(), nullable=False),
            pa.field("anchor_waypoint_id", pa.string(), nullable=False),
            pa.field("end_waypoint_id", pa.string(), nullable=False),
            pa.field("start_time_ns", pa.int64(), nullable=False),
            pa.field("anchor_time_ns", pa.int64(), nullable=False),
            pa.field("end_time_ns", pa.int64(), nullable=False),
            pa.field("magnitude_value", pa.float64()),
            pa.field("semantic_attributes_json", pa.string(), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("tape_id", "procedural_track_id", "event_id"),
        ("tape_id", "procedural_track_id", "event_index", "event_id"),
    ),
    _definition(
        CanonicalSchemaName.MOTION_CATEGORIES,
        (
            pa.field("category_id", pa.string(), nullable=False),
            pa.field("category_label", pa.string(), nullable=False),
            pa.field("agent_class", pa.string(), nullable=False),
            pa.field("event_signature_json", pa.string(), nullable=False),
            pa.field("representative_template_id", pa.string(), nullable=False),
            pa.field("template_count", pa.int32(), nullable=False),
            pa.field("track_count", pa.int32(), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("category_id",),
        ("category_label", "agent_class", "category_id"),
    ),
    _definition(
        CanonicalSchemaName.ROUTE_TEMPLATES,
        (
            pa.field("template_id", pa.string(), nullable=False),
            pa.field("category_id", pa.string(), nullable=False),
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("coordinate_frame_id", pa.string(), nullable=False),
            pa.field("representative_track_id", pa.string(), nullable=False),
            pa.field("member_count", pa.int32(), nullable=False),
            pa.field("geometry_type", pa.string(), nullable=False),
            pa.field("geometry_wkb", pa.binary(), nullable=False),
            pa.field("path_length_m", pa.float64(), nullable=False),
            pa.field("duration_ns", pa.int64(), nullable=False),
            pa.field("event_signature_json", pa.string(), nullable=False),
            pa.field("semantic_attributes_json", pa.string(), nullable=False),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("template_id",),
        ("scenario_id", "category_id", "template_id"),
    ),
    _definition(
        CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS,
        (
            pa.field("template_id", pa.string(), nullable=False),
            pa.field("procedural_track_id", pa.string(), nullable=False),
            pa.field("membership_index", pa.int32(), nullable=False),
            pa.field("scenario_id", pa.string(), nullable=False),
            pa.field("agent_id", pa.string(), nullable=False),
            pa.field("trajectory_id", pa.string(), nullable=False),
            pa.field("mean_path_error_m", pa.float64(), nullable=False),
            pa.field("maximum_path_error_m", pa.float64(), nullable=False),
            pa.field("start_distance_m", pa.float64(), nullable=False),
            pa.field("end_distance_m", pa.float64(), nullable=False),
            pa.field("path_length_ratio", pa.float64(), nullable=False),
            pa.field("duration_ratio", pa.float64(), nullable=False),
            pa.field("map_route_signature_json", pa.string()),
            pa.field("origin_type", pa.string(), nullable=False),
            pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
        ),
        ("template_id", "procedural_track_id"),
        ("template_id", "membership_index", "procedural_track_id"),
    ),
)


def canonical_schema_names() -> tuple[CanonicalSchemaName, ...]:
    """Return canonical schema names in their fixed project order."""
    return tuple(definition.name for definition in _SCHEMA_DEFINITIONS)


def _normalize_schema_name(name: CanonicalSchemaName | str) -> CanonicalSchemaName:
    if isinstance(name, CanonicalSchemaName):
        return name
    if isinstance(name, str):
        try:
            return CanonicalSchemaName(name)
        except ValueError:
            pass
    raise SchemaError(f"unknown canonical schema: {name!r}")


def get_schema_definition(
    name: CanonicalSchemaName | str,
) -> CanonicalSchemaDefinition:
    """Return the immutable definition for a canonical schema name."""
    normalized_name = _normalize_schema_name(name)
    for definition in _SCHEMA_DEFINITIONS:
        if definition.name is normalized_name:
            return definition
    raise SchemaError(f"unknown canonical schema: {normalized_name.value!r}")


def get_arrow_schema(name: CanonicalSchemaName | str) -> pa.Schema:
    """Return a canonical Arrow schema including project metadata."""
    return get_schema_definition(name).arrow_schema


def _polars_type(arrow_type: pa.DataType) -> pl.DataType:
    if arrow_type.equals(pa.string()):
        return pl.String()
    if arrow_type.equals(pa.bool_()):
        return pl.Boolean()
    if arrow_type.equals(pa.int32()):
        return pl.Int32()
    if arrow_type.equals(pa.int64()):
        return pl.Int64()
    if arrow_type.equals(pa.float32()):
        return pl.Float32()
    if arrow_type.equals(pa.float64()):
        return pl.Float64()
    if arrow_type.equals(pa.binary()):
        return pl.Binary()
    if arrow_type.equals(pa.list_(pa.string())):
        return pl.List(pl.String)
    if arrow_type.equals(pa.list_(pa.float64())):
        return pl.List(pl.Float64)
    raise SchemaError(f"unsupported Arrow logical type: {arrow_type}")


def get_polars_schema(
    name: CanonicalSchemaName | str,
) -> dict[str, pl.DataType]:
    """Return a fresh ordered Polars mapping for a canonical schema."""
    schema = get_arrow_schema(name)
    return {field.name: _polars_type(field.type) for field in schema}


def _logical_type_label(arrow_type: pa.DataType) -> str:
    labels = (
        (pa.string(), "string"),
        (pa.bool_(), "bool"),
        (pa.int32(), "int32"),
        (pa.int64(), "int64"),
        (pa.float32(), "float32"),
        (pa.float64(), "float64"),
        (pa.binary(), "binary"),
        (pa.list_(pa.string()), "list<string>"),
        (pa.list_(pa.float64()), "list<float64>"),
    )
    for expected_type, label in labels:
        if arrow_type.equals(expected_type):
            return label
    raise SchemaError(f"unsupported Arrow logical type: {arrow_type}")


def schema_definition_to_dict(
    definition: CanonicalSchemaDefinition,
) -> dict[str, object]:
    """Convert a canonical definition to plain JSON-compatible values."""
    if not isinstance(definition, CanonicalSchemaDefinition):
        raise SchemaError("definition must be a CanonicalSchemaDefinition")
    return {
        "name": definition.name.value,
        "version": definition.version,
        "fields": [
            {
                "name": field.name,
                "logical_type": _logical_type_label(field.type),
                "nullable": field.nullable,
            }
            for field in definition.arrow_schema
        ],
        "primary_key": list(definition.primary_key),
        "canonical_order": list(definition.canonical_order),
    }


def schema_fingerprint(name: CanonicalSchemaName | str) -> str:
    """Return the deterministic domain-separated fingerprint for a schema."""
    definition = get_schema_definition(name)
    return canonical_sha256(
        "canonical-schema",
        schema_definition_to_dict(definition),
    )


def validate_arrow_schema(
    schema: pa.Schema,
    expected: CanonicalSchemaName | str,
) -> None:
    """Validate fields and required metadata against a canonical schema.

    Raises:
        SchemaError: If fields, nullability, or project metadata are incompatible.
    """
    if not isinstance(schema, pa.Schema):
        raise SchemaError("schema must be a pyarrow.Schema")
    definition = get_schema_definition(expected)
    canonical = definition.arrow_schema
    if len(schema) != len(canonical):
        raise SchemaError(
            f"field count differs for {definition.name.value}: "
            f"expected {len(canonical)}, received {len(schema)}"
        )
    for index, (actual_field, expected_field) in enumerate(
        zip(schema, canonical, strict=True)
    ):
        if actual_field.name != expected_field.name:
            raise SchemaError(
                f"field {index} name differs: expected {expected_field.name!r}, "
                f"received {actual_field.name!r}"
            )
        if not actual_field.type.equals(expected_field.type):
            raise SchemaError(
                f"field {actual_field.name!r} type differs: "
                f"expected {expected_field.type}, received {actual_field.type}"
            )
        if actual_field.nullable != expected_field.nullable:
            raise SchemaError(
                f"field {actual_field.name!r} nullability differs: "
                f"expected {expected_field.nullable}, "
                f"received {actual_field.nullable}"
            )

    actual_metadata = schema.metadata or {}
    expected_metadata = canonical.metadata or {}
    for key in _METADATA_KEYS:
        if key not in actual_metadata:
            raise SchemaError(f"required schema metadata is missing: {key.decode()}")
        if actual_metadata[key] != expected_metadata[key]:
            raise SchemaError(f"schema metadata conflicts: {key.decode()}")


def scientific_data_library_versions() -> dict[str, str]:
    """Return installed scientific tabular-library versions in fixed order."""
    return {
        "numpy": np.__version__,
        "pyarrow": pa.__version__,
        "polars": pl.__version__,
    }
