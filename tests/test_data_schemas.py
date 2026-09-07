"""Tests for canonical Arrow and Polars schema definitions."""

from dataclasses import FrozenInstanceError, fields
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
from typing import Any, cast

import numpy as np
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from kinematicweave.canonical import canonical_json_bytes
from kinematicweave.data import schemas as schemas_module
from kinematicweave.data.schemas import (
    CanonicalSchemaDefinition,
    CanonicalSchemaName,
    canonical_schema_names,
    get_arrow_schema,
    get_polars_schema,
    get_schema_definition,
    schema_definition_to_dict,
    schema_fingerprint,
    scientific_data_library_versions,
    validate_arrow_schema,
)
from kinematicweave.errors import SchemaError

type ExpectedField = tuple[str, pa.DataType, bool]
type ExpectedSchema = tuple[
    tuple[ExpectedField, ...],
    tuple[str, ...],
    tuple[str, ...],
]

EXPECTED_SCHEMAS: dict[CanonicalSchemaName, ExpectedSchema] = {
    CanonicalSchemaName.SCENARIO_MANIFEST: (
        (
            ("scenario_id", pa.string(), False),
            ("dataset_id", pa.string(), False),
            ("dataset_version", pa.string(), False),
            ("split_name", pa.string(), False),
            ("city_or_region", pa.string(), True),
            ("source_scenario_id", pa.string(), True),
            ("start_time_ns", pa.int64(), False),
            ("end_time_ns", pa.int64(), False),
            ("coordinate_frame_id", pa.string(), False),
            ("origin_x_m", pa.float64(), False),
            ("origin_y_m", pa.float64(), False),
            ("origin_z_m", pa.float64(), True),
            ("source_crs", pa.string(), True),
            ("has_elevation", pa.bool_(), False),
            ("agent_count", pa.int32(), False),
            ("source_map_available", pa.bool_(), False),
            ("quality_flags", pa.list_(pa.string()), False),
            ("adapter_name", pa.string(), False),
            ("adapter_version", pa.string(), False),
            ("source_checksum", pa.string(), True),
        ),
        ("scenario_id",),
        ("scenario_id",),
    ),
    CanonicalSchemaName.COORDINATE_FRAME_METADATA: (
        (
            ("scenario_id", pa.string(), False),
            ("coordinate_frame_id", pa.string(), False),
            ("parent_frame_id", pa.string(), True),
            ("frame_type", pa.string(), False),
            ("origin_x_m", pa.float64(), False),
            ("origin_y_m", pa.float64(), False),
            ("origin_z_m", pa.float64(), True),
            ("axis_convention", pa.string(), False),
            ("distance_unit", pa.string(), False),
            ("angle_unit", pa.string(), False),
            ("timestamp_unit", pa.string(), False),
            ("source_crs", pa.string(), True),
            ("has_elevation", pa.bool_(), False),
            ("transform_to_parent_4x4", pa.list_(pa.float64()), True),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("scenario_id", "coordinate_frame_id"),
        ("scenario_id", "coordinate_frame_id"),
    ),
    CanonicalSchemaName.AGENT_METADATA: (
        (
            ("scenario_id", pa.string(), False),
            ("agent_id", pa.string(), False),
            ("source_agent_id", pa.string(), True),
            ("agent_class", pa.string(), False),
            ("length_m", pa.float32(), True),
            ("width_m", pa.float32(), True),
            ("height_m", pa.float32(), True),
            ("first_time_ns", pa.int64(), False),
            ("last_time_ns", pa.int64(), False),
            ("sample_count", pa.int32(), False),
            ("is_focal_agent", pa.bool_(), False),
            ("is_ego_agent", pa.bool_(), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("scenario_id", "agent_id"),
        ("scenario_id", "agent_id"),
    ),
    CanonicalSchemaName.TRAJECTORY_SAMPLES: (
        (
            ("scenario_id", pa.string(), False),
            ("agent_id", pa.string(), False),
            ("trajectory_id", pa.string(), False),
            ("sample_index", pa.int32(), False),
            ("timestamp_ns", pa.int64(), False),
            ("x_m", pa.float64(), False),
            ("y_m", pa.float64(), False),
            ("z_m", pa.float64(), True),
            ("heading_rad", pa.float64(), True),
            ("velocity_x_mps", pa.float64(), True),
            ("velocity_y_mps", pa.float64(), True),
            ("speed_mps", pa.float64(), True),
            ("acceleration_x_mps2", pa.float64(), True),
            ("acceleration_y_mps2", pa.float64(), True),
            ("is_observed", pa.bool_(), False),
            ("is_valid", pa.bool_(), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("scenario_id", "trajectory_id", "sample_index"),
        ("scenario_id", "trajectory_id", "timestamp_ns"),
    ),
    CanonicalSchemaName.VECTOR_MAP_ELEMENTS: (
        (
            ("scenario_id", pa.string(), False),
            ("map_element_id", pa.string(), False),
            ("element_type", pa.string(), False),
            ("geometry_type", pa.string(), False),
            ("geometry_wkb", pa.binary(), False),
            ("directionality", pa.string(), False),
            ("parent_element_id", pa.string(), True),
            ("successor_ids", pa.list_(pa.string()), False),
            ("predecessor_ids", pa.list_(pa.string()), False),
            ("left_neighbor_id", pa.string(), True),
            ("right_neighbor_id", pa.string(), True),
            ("semantic_attributes_json", pa.string(), True),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("scenario_id", "map_element_id"),
        ("scenario_id", "element_type", "map_element_id"),
    ),
    CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST: (
        (
            ("tape_id", pa.string(), False),
            ("scenario_id", pa.string(), False),
            ("coordinate_frame_id", pa.string(), False),
            ("source_dataset_id", pa.string(), False),
            ("source_dataset_version", pa.string(), False),
            ("source_validation_report_identity", pa.string(), True),
            ("encoder_name", pa.string(), False),
            ("encoder_version", pa.string(), False),
            ("encoder_parameters_identity", pa.string(), False),
            ("start_time_ns", pa.int64(), False),
            ("end_time_ns", pa.int64(), False),
            ("track_count", pa.int32(), False),
            ("segment_count", pa.int32(), False),
            ("source_sample_count", pa.int64(), False),
            ("encoded_valid_sample_count", pa.int64(), False),
            ("has_elevation", pa.bool_(), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("tape_id",),
        ("scenario_id", "tape_id"),
    ),
    CanonicalSchemaName.PROCEDURAL_TRACKS: (
        (
            ("tape_id", pa.string(), False),
            ("scenario_id", pa.string(), False),
            ("procedural_track_id", pa.string(), False),
            ("agent_id", pa.string(), False),
            ("trajectory_id", pa.string(), False),
            ("agent_class", pa.string(), False),
            ("start_time_ns", pa.int64(), False),
            ("end_time_ns", pa.int64(), False),
            ("source_sample_count", pa.int32(), False),
            ("valid_sample_count", pa.int32(), False),
            ("run_count", pa.int32(), False),
            ("segment_count", pa.int32(), False),
            ("has_elevation", pa.bool_(), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("tape_id", "procedural_track_id"),
        ("scenario_id", "tape_id"),
    ),
    CanonicalSchemaName.PROCEDURAL_SEGMENTS: (
        (
            ("tape_id", pa.string(), False),
            ("procedural_track_id", pa.string(), False),
            ("segment_id", pa.string(), False),
            ("run_index", pa.int32(), False),
            ("segment_index", pa.int32(), False),
            ("primitive_type", pa.string(), False),
            ("source_start_sample_index", pa.int32(), False),
            ("source_end_sample_index", pa.int32(), False),
            ("start_time_ns", pa.int64(), False),
            ("end_time_ns", pa.int64(), False),
            ("start_x_m", pa.float64(), False),
            ("start_y_m", pa.float64(), False),
            ("start_z_m", pa.float64(), True),
            ("end_x_m", pa.float64(), False),
            ("end_y_m", pa.float64(), False),
            ("end_z_m", pa.float64(), True),
            ("start_heading_rad", pa.float64(), True),
            ("end_heading_rad", pa.float64(), True),
            ("start_velocity_x_mps", pa.float64(), True),
            ("start_velocity_y_mps", pa.float64(), True),
            ("end_velocity_x_mps", pa.float64(), True),
            ("end_velocity_y_mps", pa.float64(), True),
            ("parameter_values", pa.list_(pa.float64()), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("tape_id", "procedural_track_id", "run_index", "segment_index"),
        ("tape_id", "procedural_track_id", "run_index", "segment_index"),
    ),
    CanonicalSchemaName.SEMANTIC_WAYPOINTS: (
        (
            ("tape_id", pa.string(), False),
            ("procedural_track_id", pa.string(), False),
            ("waypoint_id", pa.string(), False),
            ("waypoint_index", pa.int32(), False),
            ("waypoint_role", pa.string(), False),
            ("timestamp_ns", pa.int64(), False),
            ("source_sample_index", pa.int32(), True),
            ("x_m", pa.float64(), False),
            ("y_m", pa.float64(), False),
            ("z_m", pa.float64(), True),
            ("heading_rad", pa.float64(), True),
            ("speed_mps", pa.float64(), True),
            ("related_event_ids", pa.list_(pa.string()), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("tape_id", "procedural_track_id", "waypoint_id"),
        ("tape_id", "procedural_track_id", "waypoint_index", "waypoint_id"),
    ),
    CanonicalSchemaName.MOTION_EVENTS: (
        (
            ("tape_id", pa.string(), False),
            ("procedural_track_id", pa.string(), False),
            ("event_id", pa.string(), False),
            ("event_index", pa.int32(), False),
            ("event_type", pa.string(), False),
            ("start_waypoint_id", pa.string(), False),
            ("anchor_waypoint_id", pa.string(), False),
            ("end_waypoint_id", pa.string(), False),
            ("start_time_ns", pa.int64(), False),
            ("anchor_time_ns", pa.int64(), False),
            ("end_time_ns", pa.int64(), False),
            ("magnitude_value", pa.float64(), True),
            ("semantic_attributes_json", pa.string(), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("tape_id", "procedural_track_id", "event_id"),
        ("tape_id", "procedural_track_id", "event_index", "event_id"),
    ),
    CanonicalSchemaName.MOTION_CATEGORIES: (
        (
            ("category_id", pa.string(), False),
            ("category_label", pa.string(), False),
            ("agent_class", pa.string(), False),
            ("event_signature_json", pa.string(), False),
            ("representative_template_id", pa.string(), False),
            ("template_count", pa.int32(), False),
            ("track_count", pa.int32(), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("category_id",),
        ("category_label", "agent_class", "category_id"),
    ),
    CanonicalSchemaName.ROUTE_TEMPLATES: (
        (
            ("template_id", pa.string(), False),
            ("category_id", pa.string(), False),
            ("scenario_id", pa.string(), False),
            ("coordinate_frame_id", pa.string(), False),
            ("representative_track_id", pa.string(), False),
            ("member_count", pa.int32(), False),
            ("geometry_type", pa.string(), False),
            ("geometry_wkb", pa.binary(), False),
            ("path_length_m", pa.float64(), False),
            ("duration_ns", pa.int64(), False),
            ("event_signature_json", pa.string(), False),
            ("semantic_attributes_json", pa.string(), False),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("template_id",),
        ("scenario_id", "category_id", "template_id"),
    ),
    CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS: (
        (
            ("template_id", pa.string(), False),
            ("procedural_track_id", pa.string(), False),
            ("membership_index", pa.int32(), False),
            ("scenario_id", pa.string(), False),
            ("agent_id", pa.string(), False),
            ("trajectory_id", pa.string(), False),
            ("mean_path_error_m", pa.float64(), False),
            ("maximum_path_error_m", pa.float64(), False),
            ("start_distance_m", pa.float64(), False),
            ("end_distance_m", pa.float64(), False),
            ("path_length_ratio", pa.float64(), False),
            ("duration_ratio", pa.float64(), False),
            ("map_route_signature_json", pa.string(), True),
            ("origin_type", pa.string(), False),
            ("quality_flags", pa.list_(pa.string()), False),
        ),
        ("template_id", "procedural_track_id"),
        ("template_id", "membership_index", "procedural_track_id"),
    ),
}

EXPECTED_POLARS_TYPES: dict[pa.DataType, pl.DataType] = {
    pa.string(): pl.String(),
    pa.bool_(): pl.Boolean(),
    pa.int32(): pl.Int32(),
    pa.int64(): pl.Int64(),
    pa.float32(): pl.Float32(),
    pa.float64(): pl.Float64(),
    pa.binary(): pl.Binary(),
    pa.list_(pa.string()): pl.List(pl.String),
    pa.list_(pa.float64()): pl.List(pl.Float64),
}


def _metadata_for(
    name: CanonicalSchemaName,
    primary_key: tuple[str, ...],
    canonical_order: tuple[str, ...],
) -> dict[bytes, bytes]:
    return {
        b"schema_name": name.value.encode("utf-8"),
        b"schema_version": b"1.0",
        b"primary_key": canonical_json_bytes(list(primary_key)),
        b"canonical_order": canonical_json_bytes(list(canonical_order)),
    }


def _replace_field(
    schema: pa.Schema,
    index: int,
    field: pa.Field,
) -> pa.Schema:
    return pa.schema(
        tuple(
            field if position == index else existing
            for position, existing in enumerate(schema)
        ),
        metadata=schema.metadata,
    )


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


def test_scientific_dependencies_import_and_report_versions() -> None:
    assert np.__version__
    assert pa.__version__
    assert pl.__version__

    first = scientific_data_library_versions()
    second = scientific_data_library_versions()
    assert list(first) == ["numpy", "pyarrow", "polars"]
    assert first == {
        "numpy": np.__version__,
        "pyarrow": pa.__version__,
        "polars": pl.__version__,
    }
    assert all(isinstance(version, str) and version for version in first.values())
    assert first is not second


def test_schema_enum_values_and_canonical_order_are_exact() -> None:
    assert tuple(member.value for member in CanonicalSchemaName) == (
        "scenario_manifest",
        "coordinate_frame_metadata",
        "agent_metadata",
        "trajectory_samples",
        "vector_map_elements",
        "procedural_tape_manifest",
        "procedural_tracks",
        "procedural_segments",
        "semantic_waypoints",
        "motion_events",
        "motion_categories",
        "route_templates",
        "route_template_memberships",
    )
    assert canonical_schema_names() == tuple(EXPECTED_SCHEMAS)


def test_definition_is_frozen_slotted_and_typed() -> None:
    definition = get_schema_definition(CanonicalSchemaName.SCENARIO_MANIFEST)
    assert [field.name for field in fields(definition)] == [
        "name",
        "version",
        "arrow_schema",
        "primary_key",
        "canonical_order",
    ]
    assert not hasattr(definition, "__dict__")
    with pytest.raises(FrozenInstanceError):
        definition.version = "2.0"  # type: ignore[misc]
    assert isinstance(definition.name, CanonicalSchemaName)
    assert isinstance(definition.arrow_schema, pa.Schema)
    assert isinstance(definition.primary_key, tuple)
    assert isinstance(definition.canonical_order, tuple)


def test_invalid_definition_version_is_rejected() -> None:
    with pytest.raises(SchemaError, match="version"):
        CanonicalSchemaDefinition(
            name=CanonicalSchemaName.SCENARIO_MANIFEST,
            version="2.0",
            arrow_schema=pa.schema([pa.field("scenario_id", pa.string())]),
            primary_key=("scenario_id",),
            canonical_order=("scenario_id",),
        )


@pytest.mark.parametrize(
    ("primary_key", "canonical_order", "message"),
    [
        (("missing",), ("scenario_id",), "primary key field"),
        (("scenario_id",), ("missing",), "canonical order field"),
        (
            ("scenario_id", "scenario_id"),
            ("scenario_id",),
            "primary key contains duplicate",
        ),
        (
            ("scenario_id",),
            ("scenario_id", "scenario_id"),
            "canonical order contains duplicate",
        ),
    ],
)
def test_invalid_definition_fields_are_rejected(
    primary_key: tuple[str, ...],
    canonical_order: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(SchemaError, match=message):
        CanonicalSchemaDefinition(
            name=CanonicalSchemaName.SCENARIO_MANIFEST,
            version="1.0",
            arrow_schema=pa.schema([pa.field("scenario_id", pa.string())]),
            primary_key=primary_key,
            canonical_order=canonical_order,
        )


def test_definition_requires_immutable_field_tuples() -> None:
    with pytest.raises(SchemaError, match="tuple"):
        CanonicalSchemaDefinition(
            name=CanonicalSchemaName.SCENARIO_MANIFEST,
            version="1.0",
            arrow_schema=pa.schema([pa.field("scenario_id", pa.string())]),
            primary_key=["scenario_id"],  # type: ignore[arg-type]
            canonical_order=("scenario_id",),
        )


def test_module_all_is_exact() -> None:
    assert schemas_module.__all__ == [
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


@pytest.mark.parametrize("name", tuple(EXPECTED_SCHEMAS))
def test_arrow_schema_contract_is_exact(name: CanonicalSchemaName) -> None:
    expected_fields, expected_primary_key, expected_order = EXPECTED_SCHEMAS[name]
    definition = get_schema_definition(name)
    schema = definition.arrow_schema

    assert definition.version == "1.0"
    assert definition.primary_key == expected_primary_key
    assert definition.canonical_order == expected_order
    assert (
        tuple((field.name, field.type, field.nullable) for field in schema)
        == expected_fields
    )
    assert schema.metadata == _metadata_for(
        name,
        expected_primary_key,
        expected_order,
    )
    assert all(not pa.types.is_timestamp(field.type) for field in schema)
    validate_arrow_schema(schema, name)


@pytest.mark.parametrize("name", tuple(EXPECTED_SCHEMAS))
def test_polars_schema_has_type_parity_and_is_fresh(
    name: CanonicalSchemaName,
) -> None:
    arrow_schema = get_arrow_schema(name)
    first = get_polars_schema(name)
    second = get_polars_schema(name)

    assert list(first) == arrow_schema.names
    assert first == {
        field.name: EXPECTED_POLARS_TYPES[field.type] for field in arrow_schema
    }
    assert first is not second
    first["caller_field"] = pl.String()
    assert "caller_field" not in get_polars_schema(name)


@pytest.mark.parametrize("name", tuple(EXPECTED_SCHEMAS))
def test_definition_dictionary_is_plain_and_deterministic(
    name: CanonicalSchemaName,
) -> None:
    definition = get_schema_definition(name)
    first = schema_definition_to_dict(definition)
    second = schema_definition_to_dict(definition)

    assert list(first) == [
        "name",
        "version",
        "fields",
        "primary_key",
        "canonical_order",
    ]
    assert first == second
    assert first is not second
    assert first["name"] == name.value
    assert first["version"] == "1.0"
    assert first["primary_key"] == list(definition.primary_key)
    assert first["canonical_order"] == list(definition.canonical_order)
    field_records = cast(list[dict[str, object]], first["fields"])
    for field_record in field_records:
        assert list(field_record) == ["name", "logical_type", "nullable"]
    _assert_plain_json_value(first)
    json.dumps(first, allow_nan=False)


def test_dictionary_uses_only_stable_logical_type_labels() -> None:
    labels = {
        field["logical_type"]
        for name in canonical_schema_names()
        for field in cast(
            list[dict[str, object]],
            schema_definition_to_dict(get_schema_definition(name))["fields"],
        )
    }
    assert labels == {
        "string",
        "bool",
        "int32",
        "int64",
        "float32",
        "float64",
        "binary",
        "list<string>",
        "list<float64>",
    }


def test_schema_fingerprints_are_stable_distinct_and_lowercase_hex() -> None:
    first = tuple(schema_fingerprint(name) for name in canonical_schema_names())
    second = tuple(schema_fingerprint(name.value) for name in canonical_schema_names())
    assert first == second
    assert len(set(first)) == 13
    assert all(re.fullmatch(r"[0-9a-f]{64}", fingerprint) for fingerprint in first)
    assert first == (
        "e59b221b1bf720832d90a19340f773c31b461292323d6ddb0a12fb8096d7d03b",
        "ab8668ac6775de47623281bbe178e88202c0715cbb964057df7ece53c4b2f2ac",
        "7527dd3653e46f82ac835c81150c57677cd23c3a4eba2a705bd6a2dde3c0ab2b",
        "24433aa9c49be2fc95be4fd6f8a30ad163cf1a2a6e116d42b7960be2a2714cfd",
        "5f837f27a693002d9c43b9e9101d999a61f0ab53aa4a402c9bc1ce0d79ed0998",
        "7f556d450ed8cbf198e4e9c8be04bb1429cb6a2c42649aa0c9f0a07fa76c7b2c",
        "92964188ec2bbf6259a961113dbe31cb524cd2cfddb521f852d5665e6fceb96a",
        "e994f5d7c2f2cfc21ddbb06b44c076d04e94491fe2f201bc4b2a76bfa089cd86",
        "9ceaee7baf378e57898e7e241d74be38b2eb847391936ff578afab15c51d4b1d",
        "a20c7150fb9ba9f375bb82af6af0623b07a8dfc45495f6a151ec6a01ad700d42",
        "b61a73d4822e44e1fc95083476f5dc65743365a28b3d8ef303fbfb41fc2f3c3e",
        "f16fc71cebeebb2788d131594f1bc4e6ce3c4261ca9b978fbdf75cbf6ae34b39",
        "5ececc5d724477e1169e291ac2773da1f8ac70fdc1c53eab7f4a47ad393cd4ab",
    )


def test_fingerprint_uses_canonical_schema_domain_and_plain_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, object]] = []

    def fake_hash(domain: str, value: object) -> str:
        captured.append((domain, value))
        return "a" * 64

    monkeypatch.setattr(schemas_module, "canonical_sha256", fake_hash)
    assert schema_fingerprint("scenario_manifest") == "a" * 64
    assert captured == [
        (
            "canonical-schema",
            schema_definition_to_dict(
                get_schema_definition(CanonicalSchemaName.SCENARIO_MANIFEST)
            ),
        )
    ]
    payload = json.dumps(captured[0][1])
    assert re.search(r"(?:[A-Za-z]:[\\/]|/(?:home|Users|tmp)/)", payload) is None
    assert np.__version__ not in payload
    assert pa.__version__ not in payload
    assert pl.__version__ not in payload


@pytest.mark.parametrize("name", tuple(EXPECTED_SCHEMAS))
def test_lookup_accepts_enum_and_exact_string(name: CanonicalSchemaName) -> None:
    assert get_schema_definition(name) is get_schema_definition(name.value)
    assert get_arrow_schema(name) is get_schema_definition(name).arrow_schema


@pytest.mark.parametrize("name", ["unknown", "Scenario_Manifest", "", 1, None])
def test_unknown_schema_lookup_is_rejected(name: Any) -> None:
    with pytest.raises(SchemaError, match="unknown canonical schema"):
        get_schema_definition(name)


def test_arrow_validation_rejects_missing_and_additional_fields() -> None:
    name = CanonicalSchemaName.AGENT_METADATA
    canonical = get_arrow_schema(name)
    with pytest.raises(SchemaError, match="field count"):
        validate_arrow_schema(
            pa.schema(tuple(canonical)[:-1], metadata=canonical.metadata),
            name,
        )
    with pytest.raises(SchemaError, match="field count"):
        validate_arrow_schema(
            pa.schema(
                (*tuple(canonical), pa.field("extra", pa.string())),
                metadata=canonical.metadata,
            ),
            name,
        )


def test_arrow_validation_rejects_reordered_fields() -> None:
    name = CanonicalSchemaName.AGENT_METADATA
    canonical = get_arrow_schema(name)
    reordered = pa.schema(
        (canonical[1], canonical[0], *tuple(canonical)[2:]),
        metadata=canonical.metadata,
    )
    with pytest.raises(SchemaError, match="name differs"):
        validate_arrow_schema(reordered, name)


def test_arrow_validation_rejects_changed_type_and_nullability() -> None:
    name = CanonicalSchemaName.AGENT_METADATA
    canonical = get_arrow_schema(name)
    changed_type = _replace_field(
        canonical,
        0,
        pa.field("scenario_id", pa.int64(), nullable=False),
    )
    with pytest.raises(SchemaError, match="type differs"):
        validate_arrow_schema(changed_type, name)

    changed_nullability = _replace_field(
        canonical,
        0,
        pa.field("scenario_id", pa.string(), nullable=True),
    )
    with pytest.raises(SchemaError, match="nullability differs"):
        validate_arrow_schema(changed_nullability, name)


@pytest.mark.parametrize(
    ("metadata_key", "replacement", "message"),
    [
        (b"schema_name", None, "missing"),
        (b"schema_name", b"trajectory_samples", "conflicts"),
        (b"schema_version", b"2.0", "conflicts"),
        (b"primary_key", b'["agent_id"]', "conflicts"),
        (b"canonical_order", b'["agent_id"]', "conflicts"),
    ],
)
def test_arrow_validation_rejects_invalid_required_metadata(
    metadata_key: bytes,
    replacement: bytes | None,
    message: str,
) -> None:
    name = CanonicalSchemaName.AGENT_METADATA
    canonical = get_arrow_schema(name)
    metadata = dict(canonical.metadata or {})
    if replacement is None:
        del metadata[metadata_key]
    else:
        metadata[metadata_key] = replacement
    with pytest.raises(SchemaError, match=message):
        validate_arrow_schema(canonical.with_metadata(metadata), name)


def test_arrow_validation_tolerates_unrelated_metadata() -> None:
    name = CanonicalSchemaName.AGENT_METADATA
    canonical = get_arrow_schema(name)
    metadata = dict(canonical.metadata or {})
    metadata[b"producer"] = b"test"
    validate_arrow_schema(canonical.with_metadata(metadata), name)


def test_arrow_validation_rejects_wrong_expected_name() -> None:
    with pytest.raises(SchemaError, match=r"field count|name differs"):
        validate_arrow_schema(
            get_arrow_schema(CanonicalSchemaName.AGENT_METADATA),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )


@pytest.mark.parametrize("name", tuple(EXPECTED_SCHEMAS))
def test_empty_arrow_and_polars_integration_writes_no_file(
    name: CanonicalSchemaName,
    tmp_path: Path,
) -> None:
    canonical = get_arrow_schema(name)
    arrow_table = pa.Table.from_batches([], schema=canonical)
    assert arrow_table.num_rows == 0
    validate_arrow_schema(arrow_table.schema, name)

    polars_frame = pl.DataFrame(schema=get_polars_schema(name))
    assert polars_frame.height == 0
    converted = polars_frame.to_arrow().cast(canonical.remove_metadata())
    converted = converted.replace_schema_metadata(canonical.metadata)
    validate_arrow_schema(converted.schema, name)
    assert tuple(tmp_path.iterdir()) == ()


def test_import_has_no_writes_or_subprocess_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbidden_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("schema import attempted a forbidden side effect")

    monkeypatch.setattr(subprocess, "run", forbidden_call)
    monkeypatch.setattr(subprocess, "Popen", forbidden_call)
    before = tuple(tmp_path.iterdir())
    monkeypatch.chdir(tmp_path)
    assert schemas_module.__file__ is not None
    runpy.run_path(schemas_module.__file__)
    assert tuple(tmp_path.iterdir()) == before


def test_import_loads_no_dataset_geometry_gpu_or_viewer_library() -> None:
    forbidden_prefixes = (
        "argoverse",
        "geopandas",
        "rerun",
        "torch",
    )
    assert not any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for module_name in sys.modules
        for prefix in forbidden_prefixes
    )
