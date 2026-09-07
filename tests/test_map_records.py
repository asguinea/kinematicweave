"""Tests for canonical vector-map geometry and records."""

import ast
from dataclasses import FrozenInstanceError, fields
import json
import math
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import (  # type: ignore[import-untyped]
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
)

from kinematicweave.domain import map_records
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    MapGeometryType,
    VectorMapElementRecord,
    geometry_from_canonical_wkb,
    geometry_to_canonical_wkb,
    vector_map_element_record_to_dict,
)
from kinematicweave.domain.records import OriginType
from kinematicweave.errors import ValidationError

SCENARIO_ID = "scenario:map:test"
ELEMENT_ID = "map:test:lane:1:centerline"


def _record(**overrides: Any) -> VectorMapElementRecord:
    values: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "map_element_id": ELEMENT_ID,
        "element_type": MapElementType.LANE_CENTERLINE,
        "geometry_type": MapGeometryType.LINESTRING,
        "geometry_wkb": geometry_to_canonical_wkb(
            LineString([(0.0, 0.0, 1.0), (2.0, 1.0, 1.5)])
        ),
        "directionality": Directionality.DIRECTED,
        "parent_element_id": None,
        "successor_ids": ("map:test:lane:2:centerline",),
        "predecessor_ids": ("map:test:lane:0:centerline",),
        "left_neighbor_id": "map:test:lane:3:centerline",
        "right_neighbor_id": "map:test:lane:4:centerline",
        "semantic_attributes_json": '{"z":2,"a":1}',
        "origin_type": OriginType.SOURCE_GROUND_TRUTH,
        "quality_flags": (" source ", "verified"),
    }
    values.update(overrides)
    return VectorMapElementRecord(**values)


def test_exact_enums_and_public_api() -> None:
    assert tuple(item.value for item in MapElementType) == (
        "lane_centerline",
        "lane_boundary",
        "road_area",
        "crosswalk",
        "walkway",
        "junction_area",
        "access_zone",
        "other",
    )
    assert tuple(item.value for item in MapGeometryType) == (
        "point",
        "linestring",
        "polygon",
        "multilinestring",
        "multipolygon",
    )
    assert tuple(item.value for item in Directionality) == (
        "directed",
        "bidirectional",
        "unknown",
        "not_applicable",
    )
    assert map_records.__all__ == [
        "Directionality",
        "MapElementType",
        "MapGeometryType",
        "VectorMapElementRecord",
        "geometry_from_canonical_wkb",
        "geometry_to_canonical_wkb",
        "vector_map_element_record_to_dict",
    ]


def test_record_has_exact_fields_and_is_frozen_slotted() -> None:
    assert tuple(field.name for field in fields(VectorMapElementRecord)) == (
        "scenario_id",
        "map_element_id",
        "element_type",
        "geometry_type",
        "geometry_wkb",
        "directionality",
        "parent_element_id",
        "successor_ids",
        "predecessor_ids",
        "left_neighbor_id",
        "right_neighbor_id",
        "semantic_attributes_json",
        "origin_type",
        "quality_flags",
    )
    record = _record()
    assert not hasattr(record, "__dict__")
    with pytest.raises(FrozenInstanceError):
        record.scenario_id = "scenario:other"  # type: ignore[misc]


def test_record_coerces_enums_flags_references_and_json() -> None:
    successors = ["map:test:lane:2:centerline"]
    record = _record(
        element_type="lane_centerline",
        geometry_type="linestring",
        directionality="directed",
        origin_type="source_ground_truth",
        successor_ids=successors,
    )
    successors.clear()
    assert record.element_type is MapElementType.LANE_CENTERLINE
    assert record.geometry_type is MapGeometryType.LINESTRING
    assert record.directionality is Directionality.DIRECTED
    assert record.origin_type is OriginType.SOURCE_GROUND_TRUTH
    assert record.successor_ids == ("map:test:lane:2:centerline",)
    assert record.predecessor_ids == ("map:test:lane:0:centerline",)
    assert record.quality_flags == ("source", "verified")
    assert record.semantic_attributes_json == '{"a":1,"z":2}'


@pytest.mark.parametrize(
    "geometry",
    [
        Point(1.0, 2.0),
        LineString([(0.0, 0.0), (1.0, 2.0)]),
        Polygon([(0.0, 0.0), (2.0, 0.0), (1.0, 1.0), (0.0, 0.0)]),
        MultiLineString([[(0.0, 0.0), (1.0, 0.0)]]),
        MultiPolygon([Polygon([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)])]),
    ],
)
def test_canonical_2d_wkb_is_little_endian_and_stable(geometry: object) -> None:
    first = geometry_to_canonical_wkb(geometry)
    decoded = geometry_from_canonical_wkb(first)
    second = geometry_to_canonical_wkb(decoded)
    assert first[0] == 1
    assert first == second
    assert not decoded.has_z


def test_canonical_3d_wkb_preserves_z_and_line_direction() -> None:
    geometry = LineString([(3.0, 2.0, 1.0), (0.0, -1.0, 4.0)])
    encoded = geometry_to_canonical_wkb(geometry)
    decoded = geometry_from_canonical_wkb(encoded)
    assert encoded[0] == 1
    assert decoded.has_z
    assert list(decoded.coords) == [(3.0, 2.0, 1.0), (0.0, -1.0, 4.0)]
    assert geometry_to_canonical_wkb(decoded) == encoded


def test_polygon_vertex_order_is_preserved() -> None:
    vertices = [(2.0, 0.0), (2.0, 2.0), (0.0, 1.0), (2.0, 0.0)]
    decoded = geometry_from_canonical_wkb(geometry_to_canonical_wkb(Polygon(vertices)))
    assert list(decoded.exterior.coords) == vertices


@pytest.mark.parametrize(
    ("geometry", "message"),
    [
        (Point(), "empty"),
        (
            Polygon([(0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0), (0.0, 0.0)]),
            "valid",
        ),
        (GeometryCollection([Point(0.0, 0.0)]), "unsupported"),
        (LinearRing([(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)]), "unsupported"),
        (LineString([(0.0, 0.0), (math.nan, 1.0)]), "finite"),
        (Point(math.inf, 0.0), "valid|finite"),
    ],
)
def test_geometry_rejects_invalid_values(geometry: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        geometry_to_canonical_wkb(geometry)


def test_geometry_rejects_malformed_wkb_and_wrong_input_types() -> None:
    with pytest.raises(ValidationError, match="malformed"):
        geometry_from_canonical_wkb(b"not-wkb")
    with pytest.raises(ValidationError, match="bytes"):
        geometry_from_canonical_wkb("not-bytes")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="BaseGeometry"):
        geometry_to_canonical_wkb(object())


def test_record_rejects_geometry_type_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        _record(
            geometry_type="polygon",
            geometry_wkb=geometry_to_canonical_wkb(
                LineString([(0.0, 0.0), (1.0, 1.0)])
            ),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"successor_ids": ("map:test:lane:2", "map:test:lane:2")}, "duplicates"),
        ({"successor_ids": (ELEMENT_ID,)}, "self reference"),
        ({"predecessor_ids": (ELEMENT_ID,)}, "self reference"),
        ({"parent_element_id": ELEMENT_ID}, "itself"),
        ({"left_neighbor_id": ELEMENT_ID}, "itself"),
        (
            {
                "left_neighbor_id": "map:test:lane:3",
                "right_neighbor_id": "map:test:lane:3",
            },
            "must differ",
        ),
        ({"parent_element_id": "invalid"}, "identifier"),
        ({"quality_flags": ("same", " same ")}, "duplicates"),
        ({"quality_flags": "flag"}, "non-string sequence"),
    ],
)
def test_record_rejects_invalid_references_and_flags(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _record(**overrides)


@pytest.mark.parametrize(
    "value",
    ("[]", "1", "null", "{bad", '{"value":NaN}'),
)
def test_record_rejects_invalid_semantic_json(value: str) -> None:
    with pytest.raises(ValidationError, match="semantic_attributes_json"):
        _record(semantic_attributes_json=value)


def test_dictionary_conversion_is_exact_plain_fresh_and_detached() -> None:
    record = _record()
    first = vector_map_element_record_to_dict(record)
    second = vector_map_element_record_to_dict(record)
    assert tuple(first) == tuple(field.name for field in fields(record))
    assert first == second
    assert first is not second
    assert isinstance(first["geometry_wkb"], bytes)
    assert first["element_type"] == "lane_centerline"
    assert first["geometry_type"] == "linestring"
    assert first["directionality"] == "directed"
    assert first["origin_type"] == "source_ground_truth"
    assert first["successor_ids"] == ["map:test:lane:2:centerline"]
    assert first["parent_element_id"] is None
    assert json.loads(record.semantic_attributes_json or "") == {"a": 1, "z": 2}
    cast_list = first["successor_ids"]
    assert isinstance(cast_list, list)
    cast_list.clear()
    assert record.successor_ids == ("map:test:lane:2:centerline",)


def test_map_record_module_import_surface_is_safe() -> None:
    source = Path(map_records.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots.isdisjoint(
        {
            "argoverse",
            "av2",
            "pandas",
            "geopandas",
            "fiona",
            "osgeo",
            "networkx",
            "torch",
            "rerun",
            "subprocess",
            "requests",
            "urllib",
        }
    )
    assert not any(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        for node in tree.body
    )
