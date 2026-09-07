"""Deterministic source-frame geographic tiling and artifact handling."""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
from shapely import affinity, get_coordinates  # type: ignore[import-untyped]
from shapely.errors import GEOSException  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_text,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.data.parquet_io import (
    iter_canonical_parquet_batches,
    read_canonical_parquet_table,
    validate_canonical_table,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.data.validation import (
    CanonicalDatasetPaths,
    CanonicalValidationReport,
    canonical_validation_report_to_dict,
)
from kinematicweave.domain.map_records import (
    VectorMapElementRecord,
    geometry_from_canonical_wkb,
    geometry_to_canonical_wkb,
)
from kinematicweave.domain.records import (
    AgentRecord,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import validate_identifier
from kinematicweave.paths import normalize_relative_path

__all__ = [
    "GeographicTile",
    "GeographicTileArtifacts",
    "GeographicTileCollection",
    "GeographicTileConfig",
    "GeographicTileCoverage",
    "GeographicTileMembership",
    "GeographicTileSpec",
    "TileCoordinateSpace",
    "build_geographic_tiles",
    "build_geographic_tiles_from_parquet",
    "candidate_tile_indices_for_bounds",
    "clip_map_element_to_tile",
    "clip_trajectory_to_tile",
    "geographic_tile_collection_from_dict",
    "geographic_tile_collection_from_json",
    "geographic_tile_collection_to_dict",
    "geographic_tile_config_to_dict",
    "geographic_tile_spec",
    "geographic_tile_summary_markdown",
    "geographic_tile_to_dict",
    "geographic_tiles_jsonl",
    "map_geometry_in_source_frame",
    "materialize_geographic_tiles",
    "tile_indices_for_point",
    "trajectory_geometry_in_source_frame",
    "verify_geographic_tile_artifacts",
]

_SCHEMA_VERSION = "1.0"
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class TileCoordinateSpace(StrEnum):
    """Coordinate spaces supported by geographic tiling."""

    SOURCE_XY = "source_xy"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty trimmed text")
    return value.strip()


def _finite_float(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean number")
    try:
        normalized = float(value)
    except OverflowError:
        raise ValidationError(f"{field_name} must be finite") from None
    if not math.isfinite(normalized):
        raise ValidationError(f"{field_name} must be finite")
    return normalized


def _nonnegative_float(value: object, field_name: str) -> float:
    normalized = _finite_float(value, field_name)
    if normalized < 0.0:
        raise ValidationError(f"{field_name} must not be negative")
    return normalized


def _int64(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise ValidationError(f"{field_name} exceeds signed int64")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    normalized = _int64(value, field_name)
    if normalized < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return normalized


def _positive_int(value: object, field_name: str) -> int:
    normalized = _nonnegative_int(value, field_name)
    if normalized == 0:
        raise ValidationError(f"{field_name} must be positive")
    return normalized


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


def _identifier_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    normalized = tuple(
        validate_identifier(cast(str, item)) for item in cast(Sequence[object], value)
    )
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return normalized


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-9)


@dataclass(frozen=True, slots=True)
class GeographicTileConfig:
    """Configurable development-time geographic grid parameters."""

    tile_size_m: float = 100.0
    context_buffer_m: float = 10.0
    grid_origin_x_m: float = 0.0
    grid_origin_y_m: float = 0.0
    minimum_trajectory_length_m: float = 0.0

    def __post_init__(self) -> None:
        """Normalize numeric values and enforce grid constraints."""
        for field_name in (
            "tile_size_m",
            "context_buffer_m",
            "grid_origin_x_m",
            "grid_origin_y_m",
            "minimum_trajectory_length_m",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_float(getattr(self, field_name), field_name),
            )
        if self.tile_size_m <= 0.0:
            raise ValidationError("tile_size_m must be greater than zero")
        if self.context_buffer_m < 0.0:
            raise ValidationError("context_buffer_m must not be negative")
        if self.context_buffer_m >= self.tile_size_m:
            raise ValidationError(
                "context_buffer_m must be strictly less than tile_size_m"
            )
        if self.minimum_trajectory_length_m < 0.0:
            raise ValidationError("minimum_trajectory_length_m must not be negative")


_DEFAULT_CONFIG = GeographicTileConfig()


@dataclass(frozen=True, slots=True)
class GeographicTileSpec:
    """Stable identity and source-frame bounds for one geographic tile."""

    tile_id: str
    coordinate_space: TileCoordinateSpace | str
    grid_x: int
    grid_y: int
    tile_size_m: float
    context_buffer_m: float
    interior_min_x_m: float
    interior_min_y_m: float
    interior_max_x_m: float
    interior_max_y_m: float
    context_min_x_m: float
    context_min_y_m: float
    context_max_x_m: float
    context_max_y_m: float

    def __post_init__(self) -> None:
        """Normalize fields and enforce exact interior/context relationships."""
        object.__setattr__(self, "tile_id", validate_identifier(self.tile_id))
        object.__setattr__(
            self,
            "coordinate_space",
            _enum_value(
                TileCoordinateSpace,
                self.coordinate_space,
                "coordinate_space",
            ),
        )
        for field_name in ("grid_x", "grid_y"):
            object.__setattr__(
                self,
                field_name,
                _int64(getattr(self, field_name), field_name),
            )
        for field_name in (
            "tile_size_m",
            "context_buffer_m",
            "interior_min_x_m",
            "interior_min_y_m",
            "interior_max_x_m",
            "interior_max_y_m",
            "context_min_x_m",
            "context_min_y_m",
            "context_max_x_m",
            "context_max_y_m",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_float(getattr(self, field_name), field_name),
            )
        if self.tile_size_m <= 0.0:
            raise ValidationError("tile_size_m must be greater than zero")
        if self.context_buffer_m < 0.0:
            raise ValidationError("context_buffer_m must not be negative")
        if self.context_buffer_m >= self.tile_size_m:
            raise ValidationError(
                "context_buffer_m must be strictly less than tile_size_m"
            )
        if (
            self.interior_max_x_m <= self.interior_min_x_m
            or self.interior_max_y_m <= self.interior_min_y_m
            or self.context_max_x_m <= self.context_min_x_m
            or self.context_max_y_m <= self.context_min_y_m
        ):
            raise ValidationError("tile maximum bounds must exceed minimum bounds")
        expected = (
            (
                self.interior_max_x_m - self.interior_min_x_m,
                self.tile_size_m,
                "interior width",
            ),
            (
                self.interior_max_y_m - self.interior_min_y_m,
                self.tile_size_m,
                "interior height",
            ),
            (
                self.context_min_x_m,
                self.interior_min_x_m - self.context_buffer_m,
                "context minimum x",
            ),
            (
                self.context_min_y_m,
                self.interior_min_y_m - self.context_buffer_m,
                "context minimum y",
            ),
            (
                self.context_max_x_m,
                self.interior_max_x_m + self.context_buffer_m,
                "context maximum x",
            ),
            (
                self.context_max_y_m,
                self.interior_max_y_m + self.context_buffer_m,
                "context maximum y",
            ),
        )
        for actual, wanted, label in expected:
            if not _close(actual, wanted):
                raise ValidationError(f"{label} is inconsistent with tile bounds")


@dataclass(frozen=True, slots=True)
class GeographicTileMembership:
    """Ordered interior and buffered-context membership identifiers."""

    interior_scenario_ids: Sequence[str]
    interior_agent_ids: Sequence[str]
    interior_trajectory_ids: Sequence[str]
    context_scenario_ids: Sequence[str]
    context_agent_ids: Sequence[str]
    context_trajectory_ids: Sequence[str]
    interior_map_element_ids: Sequence[str]
    context_map_element_ids: Sequence[str]

    def __post_init__(self) -> None:
        """Copy collections and enforce interior-as-subset invariants."""
        for field_name in (
            "interior_scenario_ids",
            "interior_agent_ids",
            "interior_trajectory_ids",
            "context_scenario_ids",
            "context_agent_ids",
            "context_trajectory_ids",
            "interior_map_element_ids",
            "context_map_element_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _identifier_tuple(getattr(self, field_name), field_name),
            )
        pairs = (
            ("scenario", self.interior_scenario_ids, self.context_scenario_ids),
            ("agent", self.interior_agent_ids, self.context_agent_ids),
            (
                "trajectory",
                self.interior_trajectory_ids,
                self.context_trajectory_ids,
            ),
            (
                "map element",
                self.interior_map_element_ids,
                self.context_map_element_ids,
            ),
        )
        for label, interior, context in pairs:
            if not set(interior).issubset(context):
                raise ValidationError(
                    f"interior {label} identifiers must be context members"
                )


@dataclass(frozen=True, slots=True)
class GeographicTileCoverage:
    """Planar motion, map, and valid-sample coverage for one tile."""

    interior_valid_sample_count: int
    context_valid_sample_count: int
    interior_trajectory_length_m: float
    context_trajectory_length_m: float
    interior_map_length_m: float
    context_map_length_m: float
    interior_map_area_m2: float
    context_map_area_m2: float

    def __post_init__(self) -> None:
        """Normalize measurements and enforce context coverage dominance."""
        for field_name in (
            "interior_valid_sample_count",
            "context_valid_sample_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        for field_name in (
            "interior_trajectory_length_m",
            "context_trajectory_length_m",
            "interior_map_length_m",
            "context_map_length_m",
            "interior_map_area_m2",
            "context_map_area_m2",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_float(getattr(self, field_name), field_name),
            )
        pairs = (
            (
                self.interior_valid_sample_count,
                self.context_valid_sample_count,
                "valid sample count",
            ),
            (
                self.interior_trajectory_length_m,
                self.context_trajectory_length_m,
                "trajectory length",
            ),
            (
                self.interior_map_length_m,
                self.context_map_length_m,
                "map length",
            ),
            (
                self.interior_map_area_m2,
                self.context_map_area_m2,
                "map area",
            ),
        )
        for interior, context, label in pairs:
            if context < interior:
                raise ValidationError(f"context {label} must cover interior")


@dataclass(frozen=True, slots=True)
class GeographicTile:
    """One validated geographic tile with membership and coverage."""

    spec: GeographicTileSpec
    membership: GeographicTileMembership
    coverage: GeographicTileCoverage

    def __post_init__(self) -> None:
        """Enforce tile record types and minimum motion content."""
        if not isinstance(self.spec, GeographicTileSpec):
            raise ValidationError("spec must be GeographicTileSpec")
        if not isinstance(self.membership, GeographicTileMembership):
            raise ValidationError("membership must be GeographicTileMembership")
        if not isinstance(self.coverage, GeographicTileCoverage):
            raise ValidationError("coverage must be GeographicTileCoverage")
        if not self.membership.interior_trajectory_ids:
            raise ValidationError("tile requires an interior trajectory")
        if not self.membership.interior_scenario_ids:
            raise ValidationError("tile requires an interior scenario")


@dataclass(frozen=True, slots=True)
class GeographicTileCollection:
    """Deterministically ordered geographic tiles for one validated dataset."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    config: GeographicTileConfig
    validation_report_identity: str
    tiles: Sequence[GeographicTile]

    def __post_init__(self) -> None:
        """Copy tiles and enforce collection identity and ordering."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self,
            "dataset_id",
            _required_text(self.dataset_id, "dataset_id"),
        )
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        if not isinstance(self.config, GeographicTileConfig):
            raise ValidationError("config must be GeographicTileConfig")
        if (
            not isinstance(self.validation_report_identity, str)
            or _SHA256_PATTERN.fullmatch(self.validation_report_identity) is None
        ):
            raise ValidationError(
                "validation_report_identity must be a lowercase SHA-256 digest"
            )
        value: object = self.tiles
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("tiles must be a non-string sequence")
        tiles = tuple(cast(Sequence[object], value))
        if not tiles:
            raise ValidationError("tiles must not be empty")
        if any(not isinstance(tile, GeographicTile) for tile in tiles):
            raise ValidationError("tiles must contain GeographicTile values")
        typed_tiles = cast(tuple[GeographicTile, ...], tiles)
        if len({tile.spec.tile_id for tile in typed_tiles}) != len(typed_tiles):
            raise ValidationError("tile identifiers must be unique")
        coordinates = tuple(
            (tile.spec.grid_x, tile.spec.grid_y) for tile in typed_tiles
        )
        if len(set(coordinates)) != len(coordinates):
            raise ValidationError("tile grid coordinates must be unique")
        expected = tuple(
            sorted(
                typed_tiles,
                key=lambda tile: (
                    tile.spec.grid_x,
                    tile.spec.grid_y,
                    tile.spec.tile_id,
                ),
            )
        )
        if typed_tiles != expected:
            raise ValidationError("tiles are not in canonical grid order")
        object.__setattr__(self, "tiles", typed_tiles)

    @property
    def tile_count(self) -> int:
        """Return the number of motion-created geographic tiles."""
        return len(self.tiles)

    @property
    def total_interior_trajectory_count(self) -> int:
        """Return summed interior trajectory memberships across tiles."""
        return sum(len(tile.membership.interior_trajectory_ids) for tile in self.tiles)

    @property
    def total_context_trajectory_count(self) -> int:
        """Return summed context trajectory memberships across tiles."""
        return sum(len(tile.membership.context_trajectory_ids) for tile in self.tiles)


@dataclass(frozen=True, slots=True)
class GeographicTileArtifacts:
    """Physical artifacts for one geographic tile collection."""

    tile_index: WrittenArtifact
    tile_manifests: WrittenArtifact
    tile_summary: WrittenArtifact

    def __post_init__(self) -> None:
        """Validate artifact types and unique repository-relative paths."""
        artifacts = (
            self.tile_index,
            self.tile_manifests,
            self.tile_summary,
        )
        if any(not isinstance(item, WrittenArtifact) for item in artifacts):
            raise ValidationError("tile artifacts must use WrittenArtifact")
        if len({item.relative_path for item in artifacts}) != len(artifacts):
            raise ValidationError("tile artifact paths must be unique")


def _tile_hash_payload(
    dataset_id: str,
    dataset_version: str,
    grid_x: int,
    grid_y: int,
    config: GeographicTileConfig,
) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "coordinate_space": TileCoordinateSpace.SOURCE_XY.value,
        "grid_x": grid_x,
        "grid_y": grid_y,
        "tile_size_m": config.tile_size_m,
        "grid_origin_x_m": config.grid_origin_x_m,
        "grid_origin_y_m": config.grid_origin_y_m,
    }


def geographic_tile_spec(
    dataset_id: str,
    dataset_version: str,
    grid_x: int,
    grid_y: int,
    *,
    config: GeographicTileConfig = _DEFAULT_CONFIG,
) -> GeographicTileSpec:
    """Build one stable source-frame tile specification."""
    normalized_dataset_id = _required_text(dataset_id, "dataset_id")
    normalized_dataset_version = _required_text(
        dataset_version,
        "dataset_version",
    )
    normalized_x = _int64(grid_x, "grid_x")
    normalized_y = _int64(grid_y, "grid_y")
    if not isinstance(config, GeographicTileConfig):
        raise ValidationError("config must be GeographicTileConfig")
    min_x = config.grid_origin_x_m + normalized_x * config.tile_size_m
    min_y = config.grid_origin_y_m + normalized_y * config.tile_size_m
    max_x = min_x + config.tile_size_m
    max_y = min_y + config.tile_size_m
    digest = canonical_sha256(
        "geographic-tile",
        _tile_hash_payload(
            normalized_dataset_id,
            normalized_dataset_version,
            normalized_x,
            normalized_y,
            config,
        ),
    )
    return GeographicTileSpec(
        tile_id=f"tile:{normalized_dataset_id}:{digest[:24]}",
        coordinate_space=TileCoordinateSpace.SOURCE_XY,
        grid_x=normalized_x,
        grid_y=normalized_y,
        tile_size_m=config.tile_size_m,
        context_buffer_m=config.context_buffer_m,
        interior_min_x_m=min_x,
        interior_min_y_m=min_y,
        interior_max_x_m=max_x,
        interior_max_y_m=max_y,
        context_min_x_m=min_x - config.context_buffer_m,
        context_min_y_m=min_y - config.context_buffer_m,
        context_max_x_m=max_x + config.context_buffer_m,
        context_max_y_m=max_y + config.context_buffer_m,
    )


def tile_indices_for_point(
    x_m: float,
    y_m: float,
    *,
    config: GeographicTileConfig = _DEFAULT_CONFIG,
) -> tuple[int, int]:
    """Return half-open grid ownership for one finite source-frame point."""
    if not isinstance(config, GeographicTileConfig):
        raise ValidationError("config must be GeographicTileConfig")
    x_value = _finite_float(x_m, "x_m")
    y_value = _finite_float(y_m, "y_m")
    grid_x = math.floor((x_value - config.grid_origin_x_m) / config.tile_size_m)
    grid_y = math.floor((y_value - config.grid_origin_y_m) / config.tile_size_m)
    return _int64(grid_x, "grid_x"), _int64(grid_y, "grid_y")


def candidate_tile_indices_for_bounds(
    min_x_m: float,
    min_y_m: float,
    max_x_m: float,
    max_y_m: float,
    *,
    config: GeographicTileConfig = _DEFAULT_CONFIG,
    include_context: bool = False,
) -> tuple[tuple[int, int], ...]:
    """Return ordered grid boxes that may intersect finite source bounds."""
    if not isinstance(config, GeographicTileConfig):
        raise ValidationError("config must be GeographicTileConfig")
    if not isinstance(include_context, bool):
        raise ValidationError("include_context must be a Boolean")
    min_x = _finite_float(min_x_m, "min_x_m")
    min_y = _finite_float(min_y_m, "min_y_m")
    max_x = _finite_float(max_x_m, "max_x_m")
    max_y = _finite_float(max_y_m, "max_y_m")
    if max_x < min_x or max_y < min_y:
        raise ValidationError("maximum bounds must not be below minimum bounds")
    buffer = config.context_buffer_m if include_context else 0.0
    size = config.tile_size_m
    first_x = math.ceil((min_x - config.grid_origin_x_m - size - buffer) / size)
    last_x = math.floor((max_x - config.grid_origin_x_m + buffer) / size)
    first_y = math.ceil((min_y - config.grid_origin_y_m - size - buffer) / size)
    last_y = math.floor((max_y - config.grid_origin_y_m + buffer) / size)
    for value, label in (
        (first_x, "minimum grid_x"),
        (last_x, "maximum grid_x"),
        (first_y, "minimum grid_y"),
        (last_y, "maximum grid_y"),
    ):
        _int64(value, label)
    return tuple(
        (grid_x, grid_y)
        for grid_x in range(first_x, last_x + 1)
        for grid_y in range(first_y, last_y + 1)
    )


def _geometry_is_finite(geometry: BaseGeometry) -> bool:
    if isinstance(
        geometry,
        (GeometryCollection, MultiLineString, MultiPolygon),
    ):
        return all(_geometry_is_finite(child) for child in geometry.geoms)
    coordinates = get_coordinates(geometry, include_z=geometry.has_z)
    return bool(coordinates.size) and all(
        math.isfinite(float(value))
        for coordinate in coordinates
        for value in coordinate
    )


def _validated_output_geometry(
    geometry: BaseGeometry,
    *,
    allowed_types: tuple[type[BaseGeometry], ...],
    label: str,
) -> BaseGeometry:
    if type(geometry) not in allowed_types:
        raise ValidationError(f"{label} has unsupported type {geometry.geom_type}")
    if geometry.is_empty:
        raise ValidationError(f"{label} must not be empty")
    if not geometry.is_valid:
        raise ValidationError(f"{label} must be valid")
    if not _geometry_is_finite(geometry):
        raise ValidationError(f"{label} coordinates must be finite")
    return geometry


def trajectory_geometry_in_source_frame(
    scenario: ScenarioRecord,
    trajectory: Trajectory,
) -> BaseGeometry | None:
    """Build nonstationary valid trajectory runs in source x-y coordinates."""
    if not isinstance(scenario, ScenarioRecord):
        raise ValidationError("scenario must be ScenarioRecord")
    if not isinstance(trajectory, Trajectory):
        raise ValidationError("trajectory must be Trajectory")
    if scenario.scenario_id != trajectory.scenario_id:
        raise ValidationError("scenario and trajectory identifiers do not agree")
    runs: list[LineString] = []
    current: list[TrajectorySampleRecord] = []

    def finish_run() -> None:
        if len(current) < 2:
            current.clear()
            return
        use_z = all(sample.z_m is not None for sample in current)
        coordinates: list[tuple[float, ...]] = []
        for sample in current:
            x_m = sample.x_m + scenario.origin_x_m
            y_m = sample.y_m + scenario.origin_y_m
            if use_z:
                coordinates.append((x_m, y_m, cast(float, sample.z_m)))
            else:
                coordinates.append((x_m, y_m))
        try:
            line = LineString(coordinates)
        except (GEOSException, TypeError, ValueError) as error:
            raise ValidationError("trajectory run geometry is malformed") from error
        current.clear()
        if line.length <= 0.0:
            return
        runs.append(
            cast(
                LineString,
                _validated_output_geometry(
                    line,
                    allowed_types=(LineString,),
                    label="trajectory run",
                ),
            )
        )

    for sample in trajectory.samples:
        if sample.is_valid:
            current.append(sample)
        else:
            finish_run()
    finish_run()
    if not runs:
        return None
    if len(runs) == 1:
        return runs[0]
    try:
        multiline = MultiLineString(runs)
    except (GEOSException, TypeError, ValueError) as error:
        raise ValidationError("trajectory geometry is malformed") from error
    return _validated_output_geometry(
        multiline,
        allowed_types=(MultiLineString,),
        label="trajectory geometry",
    )


def _line_components(geometry: BaseGeometry) -> tuple[LineString, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, LineString):
        return (geometry,) if geometry.length > 0.0 else ()
    if isinstance(geometry, MultiLineString):
        return tuple(
            line for line in geometry.geoms if not line.is_empty and line.length > 0.0
        )
    if isinstance(geometry, GeometryCollection):
        return tuple(
            line for child in geometry.geoms for line in _line_components(child)
        )
    return ()


def _lineal_intersection(
    geometry: BaseGeometry,
    bounds: tuple[float, float, float, float],
) -> BaseGeometry | None:
    try:
        intersection = geometry.intersection(box(*bounds))
    except (GEOSException, TypeError, ValueError) as error:
        raise ValidationError("trajectory clipping failed") from error
    components = _line_components(intersection)
    if not components:
        return None
    output: BaseGeometry
    if len(components) == 1:
        output = components[0]
    else:
        try:
            output = MultiLineString(components)
        except (GEOSException, TypeError, ValueError) as error:
            raise ValidationError(
                "trajectory clipping produced invalid lines"
            ) from error
    return _validated_output_geometry(
        output,
        allowed_types=(LineString, MultiLineString),
        label="clipped trajectory",
    )


def _tile_bounds(
    tile: GeographicTileSpec,
    use_context: bool,
) -> tuple[float, float, float, float]:
    if use_context:
        return (
            tile.context_min_x_m,
            tile.context_min_y_m,
            tile.context_max_x_m,
            tile.context_max_y_m,
        )
    return (
        tile.interior_min_x_m,
        tile.interior_min_y_m,
        tile.interior_max_x_m,
        tile.interior_max_y_m,
    )


def clip_trajectory_to_tile(
    scenario: ScenarioRecord,
    trajectory: Trajectory,
    tile: GeographicTileSpec,
    *,
    use_context: bool = False,
) -> BaseGeometry | None:
    """Clip valid source-frame motion to a tile interior or context box."""
    if not isinstance(tile, GeographicTileSpec):
        raise ValidationError("tile must be GeographicTileSpec")
    if not isinstance(use_context, bool):
        raise ValidationError("use_context must be a Boolean")
    geometry = trajectory_geometry_in_source_frame(scenario, trajectory)
    if geometry is None:
        return None
    return _lineal_intersection(geometry, _tile_bounds(tile, use_context))


def map_geometry_in_source_frame(
    scenario: ScenarioRecord,
    element: VectorMapElementRecord,
) -> BaseGeometry:
    """Translate one canonical map geometry back into source x-y coordinates."""
    if not isinstance(scenario, ScenarioRecord):
        raise ValidationError("scenario must be ScenarioRecord")
    if not isinstance(element, VectorMapElementRecord):
        raise ValidationError("element must be VectorMapElementRecord")
    if scenario.scenario_id != element.scenario_id:
        raise ValidationError("scenario and map element identifiers do not agree")
    geometry = geometry_from_canonical_wkb(element.geometry_wkb)
    try:
        translated = affinity.translate(
            geometry,
            xoff=scenario.origin_x_m,
            yoff=scenario.origin_y_m,
            zoff=0.0,
        )
    except (GEOSException, TypeError, ValueError) as error:
        raise ValidationError("map geometry translation failed") from error
    geometry_to_canonical_wkb(translated)
    return translated


def _polygon_components(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, Polygon):
        return (geometry,) if geometry.area > 0.0 else ()
    if isinstance(geometry, MultiPolygon):
        return tuple(
            polygon
            for polygon in geometry.geoms
            if not polygon.is_empty and polygon.area > 0.0
        )
    if isinstance(geometry, GeometryCollection):
        return tuple(
            polygon
            for child in geometry.geoms
            for polygon in _polygon_components(child)
        )
    return ()


def _map_intersection(
    geometry: BaseGeometry,
    bounds: tuple[float, float, float, float],
) -> BaseGeometry | None:
    try:
        intersection = geometry.intersection(box(*bounds))
    except (GEOSException, TypeError, ValueError) as error:
        raise ValidationError("map clipping failed") from error
    if intersection.is_empty:
        return None
    output: BaseGeometry | None
    if isinstance(geometry, Point):
        output = intersection if isinstance(intersection, Point) else None
    elif isinstance(geometry, (LineString, MultiLineString)):
        lines = _line_components(intersection)
        output = (
            None
            if not lines
            else lines[0]
            if len(lines) == 1
            else MultiLineString(lines)
        )
    elif isinstance(geometry, (Polygon, MultiPolygon)):
        polygons = _polygon_components(intersection)
        output = (
            None
            if not polygons
            else polygons[0]
            if len(polygons) == 1
            else MultiPolygon(polygons)
        )
    else:
        raise ValidationError(f"unsupported source map type {geometry.geom_type}")
    if output is None or output.is_empty:
        return None
    geometry_to_canonical_wkb(output)
    return output


def clip_map_element_to_tile(
    scenario: ScenarioRecord,
    element: VectorMapElementRecord,
    tile: GeographicTileSpec,
    *,
    use_context: bool = False,
) -> BaseGeometry | None:
    """Clip one source-frame map element to a tile interior or context."""
    if not isinstance(tile, GeographicTileSpec):
        raise ValidationError("tile must be GeographicTileSpec")
    if not isinstance(use_context, bool):
        raise ValidationError("use_context must be a Boolean")
    geometry = map_geometry_in_source_frame(scenario, element)
    return _map_intersection(geometry, _tile_bounds(tile, use_context))


@dataclass(slots=True)
class _TileAccumulator:
    spec: GeographicTileSpec
    has_interior_motion: bool
    interior_trajectories: dict[str, tuple[str, str]]
    context_trajectories: dict[str, tuple[str, str]]
    interior_map_ids: list[str]
    context_map_ids: list[str]
    interior_map_seen: set[str]
    context_map_seen: set[str]
    interior_valid_sample_count: int
    context_valid_sample_count: int
    interior_trajectory_lengths: list[float]
    context_trajectory_lengths: list[float]
    interior_map_lengths: list[float]
    context_map_lengths: list[float]
    interior_map_areas: list[float]
    context_map_areas: list[float]


def _new_accumulator(spec: GeographicTileSpec) -> _TileAccumulator:
    return _TileAccumulator(
        spec=spec,
        has_interior_motion=False,
        interior_trajectories={},
        context_trajectories={},
        interior_map_ids=[],
        context_map_ids=[],
        interior_map_seen=set(),
        context_map_seen=set(),
        interior_valid_sample_count=0,
        context_valid_sample_count=0,
        interior_trajectory_lengths=[],
        context_trajectory_lengths=[],
        interior_map_lengths=[],
        context_map_lengths=[],
        interior_map_areas=[],
        context_map_areas=[],
    )


def _batch_rows(batch: pa.RecordBatch) -> Iterator[dict[str, object]]:
    names = tuple(batch.schema.names)
    columns = tuple(batch.column(index) for index in range(batch.num_columns))
    for row_index in range(batch.num_rows):
        yield {
            name: column[row_index].as_py()
            for name, column in zip(names, columns, strict=True)
        }


def _table_rows(table: pa.Table) -> Iterator[dict[str, object]]:
    for batch in table.to_batches(max_chunksize=65_536):
        yield from _batch_rows(batch)


def _schema_error(error: Exception) -> SchemaError:
    return SchemaError(str(error))


def _scenario_inputs(
    table: pa.Table,
    report: CanonicalValidationReport,
) -> tuple[
    dict[str, ScenarioRecord],
    dict[str, int],
    set[str],
]:
    scenarios: dict[str, ScenarioRecord] = {}
    ranks: dict[str, int] = {}
    counts: dict[str, int] = {}
    try:
        for rank, row in enumerate(_table_rows(table)):
            scenario = ScenarioRecord(**cast(Any, row))
            key = scenario.scenario_id
            counts[key] = counts.get(key, 0) + 1
            if key in scenarios:
                raise SchemaError("duplicate scenario identifier")
            if (
                scenario.dataset_id != report.dataset_id
                or scenario.dataset_version != report.dataset_version
            ):
                raise SchemaError(
                    "scenario dataset identifier or version differs from report"
                )
            scenarios[key] = scenario
            ranks[key] = rank
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    included = set(report.included_scenario_ids)
    for identifier in included:
        if counts.get(identifier, 0) != 1:
            raise SchemaError("every included scenario must exist exactly once")
    return scenarios, ranks, included


def _agent_inputs(
    table: pa.Table,
    scenarios: Mapping[str, ScenarioRecord],
    report: CanonicalValidationReport,
) -> tuple[
    dict[tuple[str, str], AgentRecord],
    dict[str, int],
    set[str],
]:
    agents: dict[tuple[str, str], AgentRecord] = {}
    ranks: dict[str, int] = {}
    counts: dict[str, int] = {}
    try:
        for rank, row in enumerate(_table_rows(table)):
            agent = AgentRecord(**cast(Any, row))
            key = (agent.scenario_id, agent.agent_id)
            if key in agents:
                raise SchemaError("duplicate agent primary key")
            if agent.scenario_id not in scenarios:
                raise SchemaError("agent references an unknown scenario")
            agents[key] = agent
            counts[agent.agent_id] = counts.get(agent.agent_id, 0) + 1
            if agent.agent_id not in ranks:
                ranks[agent.agent_id] = rank
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    included = set(report.included_agent_ids)
    for identifier in included:
        if counts.get(identifier, 0) != 1:
            raise SchemaError("every included agent must exist exactly once")
    return agents, ranks, included


def _accumulator(
    accumulators: dict[tuple[int, int], _TileAccumulator],
    index: tuple[int, int],
    report: CanonicalValidationReport,
    config: GeographicTileConfig,
) -> _TileAccumulator:
    current = accumulators.get(index)
    if current is None:
        current = _new_accumulator(
            geographic_tile_spec(
                report.dataset_id,
                report.dataset_version,
                index[0],
                index[1],
                config=config,
            )
        )
        accumulators[index] = current
    return current


def _source_sample_point(
    scenario: ScenarioRecord,
    sample: TrajectorySampleRecord,
) -> tuple[float, float]:
    return (
        sample.x_m + scenario.origin_x_m,
        sample.y_m + scenario.origin_y_m,
    )


def _inside_context(
    x_m: float,
    y_m: float,
    spec: GeographicTileSpec,
) -> bool:
    return (
        spec.context_min_x_m <= x_m <= spec.context_max_x_m
        and spec.context_min_y_m <= y_m <= spec.context_max_y_m
    )


def _aggregate_trajectory(
    scenario: ScenarioRecord,
    trajectory: Trajectory,
    accumulators: dict[tuple[int, int], _TileAccumulator],
    report: CanonicalValidationReport,
    config: GeographicTileConfig,
) -> None:
    geometry = trajectory_geometry_in_source_frame(scenario, trajectory)
    if geometry is None or geometry.length <= config.minimum_trajectory_length_m:
        return
    min_x, min_y, max_x, max_y = cast(
        tuple[float, float, float, float],
        geometry.bounds,
    )
    for index in candidate_tile_indices_for_bounds(
        min_x,
        min_y,
        max_x,
        max_y,
        config=config,
    ):
        current = _accumulator(accumulators, index, report, config)
        clipped = _lineal_intersection(
            geometry,
            _tile_bounds(current.spec, False),
        )
        if clipped is None:
            continue
        current.has_interior_motion = True
        current.interior_trajectories[trajectory.trajectory_id] = (
            trajectory.scenario_id,
            trajectory.agent_id,
        )
        current.interior_trajectory_lengths.append(float(clipped.length))

    for index in candidate_tile_indices_for_bounds(
        min_x,
        min_y,
        max_x,
        max_y,
        config=config,
        include_context=True,
    ):
        current = _accumulator(accumulators, index, report, config)
        clipped = _lineal_intersection(
            geometry,
            _tile_bounds(current.spec, True),
        )
        if clipped is None:
            continue
        current.context_trajectories[trajectory.trajectory_id] = (
            trajectory.scenario_id,
            trajectory.agent_id,
        )
        current.context_trajectory_lengths.append(float(clipped.length))

    for sample in trajectory.samples:
        if not sample.is_valid:
            continue
        x_m, y_m = _source_sample_point(scenario, sample)
        owner = tile_indices_for_point(x_m, y_m, config=config)
        _accumulator(
            accumulators,
            owner,
            report,
            config,
        ).interior_valid_sample_count += 1
        for index in candidate_tile_indices_for_bounds(
            x_m,
            y_m,
            x_m,
            y_m,
            config=config,
            include_context=True,
        ):
            current = _accumulator(accumulators, index, report, config)
            if _inside_context(x_m, y_m, current.spec):
                current.context_valid_sample_count += 1


def _consume_trajectory_batches(
    batches: Iterable[pa.RecordBatch],
    scenarios: Mapping[str, ScenarioRecord],
    agents: Mapping[tuple[str, str], AgentRecord],
    included_scenarios: set[str],
    included_agents: set[str],
    report: CanonicalValidationReport,
    config: GeographicTileConfig,
    accumulators: dict[tuple[int, int], _TileAccumulator],
) -> None:
    included_trajectories = set(report.included_trajectory_ids)
    seen_included: set[str] = set()
    current_key: tuple[str, str] | None = None
    current_samples: list[TrajectorySampleRecord] = []

    def finish() -> None:
        nonlocal current_samples
        if not current_samples:
            return
        first = current_samples[0]
        agent = agents.get((first.scenario_id, first.agent_id))
        if agent is None:
            raise SchemaError("trajectory references an unknown agent")
        try:
            trajectory = Trajectory(
                scenario_id=first.scenario_id,
                agent_id=first.agent_id,
                trajectory_id=first.trajectory_id,
                samples=tuple(current_samples),
                origin_type=agent.origin_type,
                quality_flags=agent.quality_flags,
            )
        except ValidationError as error:
            raise _schema_error(error) from None
        if trajectory.trajectory_id in included_trajectories:
            if trajectory.trajectory_id in seen_included:
                raise SchemaError("every included trajectory must exist exactly once")
            if (
                trajectory.scenario_id not in included_scenarios
                or trajectory.agent_id not in included_agents
            ):
                raise SchemaError(
                    "included trajectory references an excluded scenario or agent"
                )
            seen_included.add(trajectory.trajectory_id)
            _aggregate_trajectory(
                scenarios[trajectory.scenario_id],
                trajectory,
                accumulators,
                report,
                config,
            )
        current_samples = []

    try:
        for batch in batches:
            for row in _batch_rows(batch):
                sample = TrajectorySampleRecord(**cast(Any, row))
                if sample.scenario_id not in scenarios:
                    raise SchemaError("trajectory references an unknown scenario")
                if (sample.scenario_id, sample.agent_id) not in agents:
                    raise SchemaError("trajectory references an unknown agent")
                key = (sample.scenario_id, sample.trajectory_id)
                if current_key is None:
                    current_key = key
                elif key != current_key:
                    if key < current_key:
                        raise SchemaError(
                            "trajectory groups are not in canonical order"
                        )
                    finish()
                    current_key = key
                if current_samples and (sample.agent_id != current_samples[0].agent_id):
                    raise SchemaError("one trajectory references multiple agents")
                current_samples.append(sample)
        finish()
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    if seen_included != included_trajectories:
        raise SchemaError("every included trajectory must exist exactly once")


def _measure_map_geometry(geometry: BaseGeometry) -> tuple[float, float]:
    if isinstance(geometry, (LineString, MultiLineString)):
        return float(geometry.length), 0.0
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return 0.0, float(geometry.area)
    if isinstance(geometry, Point):
        return 0.0, 0.0
    raise ValidationError(f"unsupported clipped map type {geometry.geom_type}")


def _add_map_membership(
    current: _TileAccumulator,
    element_id: str,
    geometry: BaseGeometry,
    *,
    use_context: bool,
) -> None:
    length, area = _measure_map_geometry(geometry)
    if use_context:
        if element_id not in current.context_map_seen:
            current.context_map_seen.add(element_id)
            current.context_map_ids.append(element_id)
        current.context_map_lengths.append(length)
        current.context_map_areas.append(area)
    else:
        if element_id not in current.interior_map_seen:
            current.interior_map_seen.add(element_id)
            current.interior_map_ids.append(element_id)
        current.interior_map_lengths.append(length)
        current.interior_map_areas.append(area)


def _consume_map_batches(
    batches: Iterable[pa.RecordBatch],
    scenarios: Mapping[str, ScenarioRecord],
    report: CanonicalValidationReport,
    accumulators: Mapping[tuple[int, int], _TileAccumulator],
    config: GeographicTileConfig,
) -> None:
    included = set(report.included_map_element_ids)
    seen_included: set[str] = set()
    counts: dict[str, int] = {}
    try:
        for batch in batches:
            for row in _batch_rows(batch):
                element = VectorMapElementRecord(**cast(Any, row))
                if element.scenario_id not in scenarios:
                    raise SchemaError("map element references an unknown scenario")
                counts[element.map_element_id] = (
                    counts.get(element.map_element_id, 0) + 1
                )
                if element.map_element_id not in included:
                    continue
                if element.map_element_id in seen_included:
                    raise SchemaError(
                        "every included map element must exist exactly once"
                    )
                seen_included.add(element.map_element_id)
                scenario = scenarios[element.scenario_id]
                geometry = map_geometry_in_source_frame(scenario, element)
                min_x, min_y, max_x, max_y = cast(
                    tuple[float, float, float, float],
                    geometry.bounds,
                )
                candidates = candidate_tile_indices_for_bounds(
                    min_x,
                    min_y,
                    max_x,
                    max_y,
                    config=config,
                    include_context=True,
                )
                for index in candidates:
                    current = accumulators.get(index)
                    if current is None or not current.has_interior_motion:
                        continue
                    interior = _map_intersection(
                        geometry,
                        _tile_bounds(current.spec, False),
                    )
                    context = _map_intersection(
                        geometry,
                        _tile_bounds(current.spec, True),
                    )
                    if interior is not None:
                        _add_map_membership(
                            current,
                            element.map_element_id,
                            interior,
                            use_context=False,
                        )
                    if context is not None:
                        _add_map_membership(
                            current,
                            element.map_element_id,
                            context,
                            use_context=True,
                        )
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    for identifier in included:
        if counts.get(identifier, 0) != 1:
            raise SchemaError("every included map element must exist exactly once")


def _ordered_membership_ids(
    values: Mapping[str, tuple[str, str]],
    scenario_ranks: Mapping[str, int],
    agent_ranks: Mapping[str, int],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    trajectory_ids = tuple(
        sorted(
            values,
            key=lambda identifier: (
                values[identifier][0],
                identifier,
            ),
        )
    )
    scenario_ids = tuple(
        sorted(
            {scenario_id for scenario_id, _agent_id in values.values()},
            key=lambda identifier: scenario_ranks[identifier],
        )
    )
    agent_ids = tuple(
        sorted(
            {agent_id for _scenario_id, agent_id in values.values()},
            key=lambda identifier: agent_ranks[identifier],
        )
    )
    return scenario_ids, agent_ids, trajectory_ids


def _finish_collection(
    report: CanonicalValidationReport,
    config: GeographicTileConfig,
    accumulators: Mapping[tuple[int, int], _TileAccumulator],
    scenario_ranks: Mapping[str, int],
    agent_ranks: Mapping[str, int],
) -> GeographicTileCollection:
    tiles: list[GeographicTile] = []
    for index in sorted(accumulators):
        current = accumulators[index]
        if not current.has_interior_motion:
            continue
        interior_scenarios, interior_agents, interior_trajectories = (
            _ordered_membership_ids(
                current.interior_trajectories,
                scenario_ranks,
                agent_ranks,
            )
        )
        context_scenarios, context_agents, context_trajectories = (
            _ordered_membership_ids(
                current.context_trajectories,
                scenario_ranks,
                agent_ranks,
            )
        )
        membership = GeographicTileMembership(
            interior_scenario_ids=interior_scenarios,
            interior_agent_ids=interior_agents,
            interior_trajectory_ids=interior_trajectories,
            context_scenario_ids=context_scenarios,
            context_agent_ids=context_agents,
            context_trajectory_ids=context_trajectories,
            interior_map_element_ids=tuple(current.interior_map_ids),
            context_map_element_ids=tuple(current.context_map_ids),
        )
        coverage = GeographicTileCoverage(
            interior_valid_sample_count=current.interior_valid_sample_count,
            context_valid_sample_count=current.context_valid_sample_count,
            interior_trajectory_length_m=math.fsum(current.interior_trajectory_lengths),
            context_trajectory_length_m=math.fsum(current.context_trajectory_lengths),
            interior_map_length_m=math.fsum(current.interior_map_lengths),
            context_map_length_m=math.fsum(current.context_map_lengths),
            interior_map_area_m2=math.fsum(current.interior_map_areas),
            context_map_area_m2=math.fsum(current.context_map_areas),
        )
        tiles.append(
            GeographicTile(
                spec=current.spec,
                membership=membership,
                coverage=coverage,
            )
        )
    if not tiles:
        raise SchemaError("included motion does not create a geographic tile")
    identity = canonical_sha256(
        "canonical-validation-report",
        canonical_validation_report_to_dict(report),
    )
    return GeographicTileCollection(
        schema_version=_SCHEMA_VERSION,
        dataset_id=report.dataset_id,
        dataset_version=report.dataset_version,
        config=config,
        validation_report_identity=identity,
        tiles=tuple(tiles),
    )


def _build_from_batches(
    scenario_manifest: pa.Table,
    agent_metadata: pa.Table,
    trajectory_batches: Iterable[pa.RecordBatch],
    validation_report: CanonicalValidationReport,
    map_batches: Iterable[pa.RecordBatch],
    *,
    config: GeographicTileConfig,
) -> GeographicTileCollection:
    if not isinstance(validation_report, CanonicalValidationReport):
        raise ValidationError("validation_report must be CanonicalValidationReport")
    if not isinstance(config, GeographicTileConfig):
        raise ValidationError("config must be GeographicTileConfig")
    scenarios, scenario_ranks, included_scenarios = _scenario_inputs(
        scenario_manifest,
        validation_report,
    )
    agents, agent_ranks, included_agents = _agent_inputs(
        agent_metadata,
        scenarios,
        validation_report,
    )
    accumulators: dict[tuple[int, int], _TileAccumulator] = {}
    _consume_trajectory_batches(
        trajectory_batches,
        scenarios,
        agents,
        included_scenarios,
        included_agents,
        validation_report,
        config,
        accumulators,
    )
    if not any(current.has_interior_motion for current in accumulators.values()):
        raise SchemaError("included motion does not create a geographic tile")
    _consume_map_batches(
        map_batches,
        scenarios,
        validation_report,
        accumulators,
        config,
    )
    return _finish_collection(
        validation_report,
        config,
        accumulators,
        scenario_ranks,
        agent_ranks,
    )


def build_geographic_tiles(
    scenario_manifest: pa.Table,
    agent_metadata: pa.Table,
    trajectory_samples: pa.Table,
    validation_report: CanonicalValidationReport,
    vector_map_elements: pa.Table | None = None,
    *,
    config: GeographicTileConfig = _DEFAULT_CONFIG,
) -> GeographicTileCollection:
    """Build deterministic geographic tiles from canonical in-memory tables."""
    validate_canonical_table(
        scenario_manifest,
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )
    validate_canonical_table(
        agent_metadata,
        CanonicalSchemaName.AGENT_METADATA,
    )
    validate_canonical_table(
        trajectory_samples,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    if vector_map_elements is not None:
        validate_canonical_table(
            vector_map_elements,
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        )
    return _build_from_batches(
        scenario_manifest,
        agent_metadata,
        trajectory_samples.to_batches(max_chunksize=65_536),
        validation_report,
        (
            ()
            if vector_map_elements is None
            else vector_map_elements.to_batches(max_chunksize=65_536)
        ),
        config=config,
    )


def _repository_root(value: object) -> Path:
    if not isinstance(value, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        root = value.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("repository_root must be a directory")
    return root


def build_geographic_tiles_from_parquet(
    repository_root: Path,
    paths: CanonicalDatasetPaths,
    validation_report: CanonicalValidationReport,
    *,
    config: GeographicTileConfig = _DEFAULT_CONFIG,
    batch_size: int = 65_536,
) -> GeographicTileCollection:
    """Build tiles while retaining at most one current trajectory's samples."""
    root = _repository_root(repository_root)
    if not isinstance(paths, CanonicalDatasetPaths):
        raise ValidationError("paths must be CanonicalDatasetPaths")
    normalized_batch_size = _positive_int(batch_size, "batch_size")
    scenario_manifest = read_canonical_parquet_table(
        root,
        paths.scenario_manifest,
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )
    agent_metadata = read_canonical_parquet_table(
        root,
        paths.agent_metadata,
        CanonicalSchemaName.AGENT_METADATA,
    )
    trajectory_batches = iter_canonical_parquet_batches(
        root,
        paths.trajectory_samples,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
        batch_size=normalized_batch_size,
    )
    map_batches: Iterable[pa.RecordBatch]
    if paths.vector_map_elements:
        map_batches = iter_canonical_parquet_batches(
            root,
            paths.vector_map_elements,
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
            batch_size=normalized_batch_size,
        )
    else:
        map_batches = ()
    return _build_from_batches(
        scenario_manifest,
        agent_metadata,
        trajectory_batches,
        validation_report,
        map_batches,
        config=config,
    )


def geographic_tile_config_to_dict(
    config: GeographicTileConfig,
) -> dict[str, object]:
    """Return an ordered JSON-compatible tile configuration."""
    if not isinstance(config, GeographicTileConfig):
        raise ValidationError("config must be GeographicTileConfig")
    return {
        "tile_size_m": config.tile_size_m,
        "context_buffer_m": config.context_buffer_m,
        "grid_origin_x_m": config.grid_origin_x_m,
        "grid_origin_y_m": config.grid_origin_y_m,
        "minimum_trajectory_length_m": config.minimum_trajectory_length_m,
    }


def _membership_to_dict(
    membership: GeographicTileMembership,
) -> dict[str, object]:
    return {
        field.name: list(cast(Sequence[str], getattr(membership, field.name)))
        for field in fields(GeographicTileMembership)
    }


def _coverage_to_dict(
    coverage: GeographicTileCoverage,
) -> dict[str, object]:
    return {
        field.name: cast(int | float, getattr(coverage, field.name))
        for field in fields(GeographicTileCoverage)
    }


def geographic_tile_to_dict(
    tile: GeographicTile,
) -> dict[str, object]:
    """Return one exact ordered JSON-compatible tile dictionary."""
    if not isinstance(tile, GeographicTile):
        raise ValidationError("tile must be GeographicTile")
    spec = tile.spec
    return {
        "tile_id": spec.tile_id,
        "coordinate_space": _enum_value(
            TileCoordinateSpace,
            spec.coordinate_space,
            "coordinate_space",
        ).value,
        "grid_x": spec.grid_x,
        "grid_y": spec.grid_y,
        "tile_size_m": spec.tile_size_m,
        "context_buffer_m": spec.context_buffer_m,
        "interior_bounds": [
            spec.interior_min_x_m,
            spec.interior_min_y_m,
            spec.interior_max_x_m,
            spec.interior_max_y_m,
        ],
        "context_bounds": [
            spec.context_min_x_m,
            spec.context_min_y_m,
            spec.context_max_x_m,
            spec.context_max_y_m,
        ],
        "membership": _membership_to_dict(tile.membership),
        "coverage": _coverage_to_dict(tile.coverage),
    }


def geographic_tile_collection_to_dict(
    collection: GeographicTileCollection,
) -> dict[str, object]:
    """Return the complete ordered JSON-compatible tile collection."""
    if not isinstance(collection, GeographicTileCollection):
        raise ValidationError("collection must be GeographicTileCollection")
    return {
        "schema_version": collection.schema_version,
        "dataset_id": collection.dataset_id,
        "dataset_version": collection.dataset_version,
        "config": geographic_tile_config_to_dict(collection.config),
        "validation_report_identity": collection.validation_report_identity,
        "tile_count": collection.tile_count,
        "total_interior_trajectory_count": (collection.total_interior_trajectory_count),
        "total_context_trajectory_count": (collection.total_context_trajectory_count),
        "tile_ids": [tile.spec.tile_id for tile in collection.tiles],
        "tiles": [geographic_tile_to_dict(tile) for tile in collection.tiles],
    }


def geographic_tiles_jsonl(
    collection: GeographicTileCollection,
) -> str:
    """Serialize one compact canonical tile object per line."""
    if not isinstance(collection, GeographicTileCollection):
        raise ValidationError("collection must be GeographicTileCollection")
    return "".join(
        canonical_json_text(
            geographic_tile_to_dict(tile),
            trailing_newline=True,
        )
        for tile in collection.tiles
    )


def _number_text(value: float) -> str:
    normalized = 0.0 if value == 0.0 else value
    return format(normalized, ".12g")


def geographic_tile_summary_markdown(
    collection: GeographicTileCollection,
) -> str:
    """Render a deterministic human-readable geographic tile summary."""
    if not isinstance(collection, GeographicTileCollection):
        raise ValidationError("collection must be GeographicTileCollection")
    interior_samples = sum(
        tile.coverage.interior_valid_sample_count for tile in collection.tiles
    )
    context_samples = sum(
        tile.coverage.context_valid_sample_count for tile in collection.tiles
    )
    interior_trajectory_length = math.fsum(
        tile.coverage.interior_trajectory_length_m for tile in collection.tiles
    )
    context_trajectory_length = math.fsum(
        tile.coverage.context_trajectory_length_m for tile in collection.tiles
    )
    interior_map_length = math.fsum(
        tile.coverage.interior_map_length_m for tile in collection.tiles
    )
    context_map_length = math.fsum(
        tile.coverage.context_map_length_m for tile in collection.tiles
    )
    interior_map_area = math.fsum(
        tile.coverage.interior_map_area_m2 for tile in collection.tiles
    )
    context_map_area = math.fsum(
        tile.coverage.context_map_area_m2 for tile in collection.tiles
    )
    lines = [
        "# Geographic Tile Summary",
        "",
        f"- Dataset: `{collection.dataset_id}`",
        f"- Dataset version: `{collection.dataset_version}`",
        f"- Coordinate space: `{TileCoordinateSpace.SOURCE_XY.value}`",
        f"- Tile size (m): {_number_text(collection.config.tile_size_m)}",
        (f"- Context buffer (m): {_number_text(collection.config.context_buffer_m)}"),
        (
            "- Grid origin (m): "
            f"[{_number_text(collection.config.grid_origin_x_m)}, "
            f"{_number_text(collection.config.grid_origin_y_m)}]"
        ),
        f"- Tile count: {collection.tile_count}",
        (
            "- Interior trajectory memberships: "
            f"{collection.total_interior_trajectory_count}"
        ),
        (
            "- Context trajectory memberships: "
            f"{collection.total_context_trajectory_count}"
        ),
        f"- Interior valid samples: {interior_samples}",
        f"- Context valid samples: {context_samples}",
        (
            "- Interior trajectory length (m): "
            f"{_number_text(interior_trajectory_length)}"
        ),
        (f"- Context trajectory length (m): {_number_text(context_trajectory_length)}"),
        f"- Interior map length (m): {_number_text(interior_map_length)}",
        f"- Context map length (m): {_number_text(context_map_length)}",
        f"- Interior map area (m^2): {_number_text(interior_map_area)}",
        f"- Context map area (m^2): {_number_text(context_map_area)}",
        "",
        (
            "| Tile ID | Grid x | Grid y | Interior trajectories | "
            "Context trajectories | Interior samples | Context samples |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        (
            f"| `{tile.spec.tile_id}` | {tile.spec.grid_x} | "
            f"{tile.spec.grid_y} | "
            f"{len(tile.membership.interior_trajectory_ids)} | "
            f"{len(tile.membership.context_trajectory_ids)} | "
            f"{tile.coverage.interior_valid_sample_count} | "
            f"{tile.coverage.context_valid_sample_count} |"
        )
        for tile in collection.tiles
    )
    lines.append("")
    return "\n".join(lines)


def _exact_mapping(
    value: object,
    expected_fields: tuple[str, ...],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SchemaError(f"{label} must be a JSON object")
    mapping = cast(Mapping[str, object], value)
    unknown = tuple(key for key in mapping if key not in expected_fields)
    missing = tuple(key for key in expected_fields if key not in mapping)
    if unknown:
        raise SchemaError(f"{label} contains unknown field: {unknown[0]}")
    if missing:
        raise SchemaError(f"{label} is missing field: {missing[0]}")
    return mapping


def _sequence(
    value: object,
    label: str,
    *,
    length: int | None = None,
) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SchemaError(f"{label} must be an array")
    sequence = cast(Sequence[object], value)
    if length is not None and len(sequence) != length:
        raise SchemaError(f"{label} must contain exactly {length} values")
    return sequence


_CONFIG_FIELDS = tuple(field.name for field in fields(GeographicTileConfig))
_MEMBERSHIP_FIELDS = tuple(field.name for field in fields(GeographicTileMembership))
_COVERAGE_FIELDS = tuple(field.name for field in fields(GeographicTileCoverage))
_TILE_FIELDS = (
    "tile_id",
    "coordinate_space",
    "grid_x",
    "grid_y",
    "tile_size_m",
    "context_buffer_m",
    "interior_bounds",
    "context_bounds",
    "membership",
    "coverage",
)
_COLLECTION_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "config",
    "validation_report_identity",
    "tile_count",
    "total_interior_trajectory_count",
    "total_context_trajectory_count",
    "tile_ids",
    "tiles",
)


def _tile_from_dict(value: object) -> GeographicTile:
    mapping = _exact_mapping(value, _TILE_FIELDS, "tile")
    interior_bounds = _sequence(
        mapping["interior_bounds"],
        "interior_bounds",
        length=4,
    )
    context_bounds = _sequence(
        mapping["context_bounds"],
        "context_bounds",
        length=4,
    )
    membership_mapping = _exact_mapping(
        mapping["membership"],
        _MEMBERSHIP_FIELDS,
        "membership",
    )
    coverage_mapping = _exact_mapping(
        mapping["coverage"],
        _COVERAGE_FIELDS,
        "coverage",
    )
    try:
        spec = GeographicTileSpec(
            tile_id=cast(str, mapping["tile_id"]),
            coordinate_space=cast(str, mapping["coordinate_space"]),
            grid_x=cast(int, mapping["grid_x"]),
            grid_y=cast(int, mapping["grid_y"]),
            tile_size_m=cast(float, mapping["tile_size_m"]),
            context_buffer_m=cast(float, mapping["context_buffer_m"]),
            interior_min_x_m=cast(float, interior_bounds[0]),
            interior_min_y_m=cast(float, interior_bounds[1]),
            interior_max_x_m=cast(float, interior_bounds[2]),
            interior_max_y_m=cast(float, interior_bounds[3]),
            context_min_x_m=cast(float, context_bounds[0]),
            context_min_y_m=cast(float, context_bounds[1]),
            context_max_x_m=cast(float, context_bounds[2]),
            context_max_y_m=cast(float, context_bounds[3]),
        )
        membership = GeographicTileMembership(**cast(Any, dict(membership_mapping)))
        coverage = GeographicTileCoverage(**cast(Any, dict(coverage_mapping)))
        return GeographicTile(
            spec=spec,
            membership=membership,
            coverage=coverage,
        )
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None


def geographic_tile_collection_from_dict(
    value: Mapping[str, object],
) -> GeographicTileCollection:
    """Reconstruct a complete collection from one exact JSON mapping."""
    mapping = _exact_mapping(value, _COLLECTION_FIELDS, "tile collection")
    config_mapping = _exact_mapping(
        mapping["config"],
        _CONFIG_FIELDS,
        "config",
    )
    tile_values = _sequence(mapping["tiles"], "tiles")
    tile_ids = _sequence(mapping["tile_ids"], "tile_ids")
    try:
        config = GeographicTileConfig(**cast(Any, dict(config_mapping)))
        tiles = tuple(_tile_from_dict(item) for item in tile_values)
        collection = GeographicTileCollection(
            schema_version=cast(str, mapping["schema_version"]),
            dataset_id=cast(str, mapping["dataset_id"]),
            dataset_version=cast(str, mapping["dataset_version"]),
            config=config,
            validation_report_identity=cast(
                str,
                mapping["validation_report_identity"],
            ),
            tiles=tiles,
        )
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise _schema_error(error) from None
    derived: tuple[tuple[str, object], ...] = (
        ("tile_count", collection.tile_count),
        (
            "total_interior_trajectory_count",
            collection.total_interior_trajectory_count,
        ),
        (
            "total_context_trajectory_count",
            collection.total_context_trajectory_count,
        ),
        (
            "tile_ids",
            [tile.spec.tile_id for tile in collection.tiles],
        ),
    )
    for field_name, expected in derived:
        actual: object = (
            list(tile_ids) if field_name == "tile_ids" else mapping[field_name]
        )
        if actual != expected:
            raise SchemaError(f"{field_name} does not match reconstructed collection")
    return collection


def geographic_tile_collection_from_json(
    text: str,
) -> GeographicTileCollection:
    """Parse and reconstruct a complete collection from JSON text."""
    if not isinstance(text, str):
        raise SchemaError("tile collection JSON must be text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError("tile collection JSON is malformed") from None
    if not isinstance(value, Mapping):
        raise SchemaError("tile collection JSON root must be an object")
    return geographic_tile_collection_from_dict(cast(Mapping[str, object], value))


def materialize_geographic_tiles(
    run_directory: RunDirectory,
    collection: GeographicTileCollection,
    *,
    relative_directory: str | Path = "artifacts/geographic_tiles",
) -> GeographicTileArtifacts:
    """Atomically write the exact three deterministic tile artifacts."""
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be RunDirectory")
    if not isinstance(collection, GeographicTileCollection):
        raise ValidationError("collection must be GeographicTileCollection")
    try:
        directory = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    tile_index = atomic_write_text(
        run_directory,
        directory / "tile_index.json",
        canonical_json_text(geographic_tile_collection_to_dict(collection)),
    )
    manifests = atomic_write_text(
        run_directory,
        directory / "tile_manifests.jsonl",
        geographic_tiles_jsonl(collection),
    )
    summary = atomic_write_text(
        run_directory,
        directory / "tile_summary.md",
        geographic_tile_summary_markdown(collection),
    )
    return GeographicTileArtifacts(
        tile_index=tile_index,
        tile_manifests=manifests,
        tile_summary=summary,
    )


def _verified_artifact_bytes(
    repository_root: Path,
    artifact: WrittenArtifact,
) -> bytes:
    root = _repository_root(repository_root)
    if not isinstance(artifact, WrittenArtifact):
        raise ValidationError("artifact must be WrittenArtifact")
    candidate = root / artifact.relative_path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("geographic tile artifact is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("geographic tile artifact resolves outside repository")
    if candidate.is_symlink() or not resolved.is_file():
        raise ArtifactError("geographic tile artifact must be a regular file")
    digest = hashlib.sha256()
    data = bytearray()
    try:
        with resolved.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                data.extend(chunk)
    except OSError as error:
        raise ArtifactError("cannot read geographic tile artifact") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("geographic tile artifact size differs")
    if digest.hexdigest() != artifact.content_checksum:
        raise ArtifactError("geographic tile artifact SHA-256 differs")
    return bytes(data)


def _utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise SchemaError(f"{label} is not valid UTF-8") from None


def verify_geographic_tile_artifacts(
    repository_root: Path,
    artifacts: GeographicTileArtifacts,
) -> GeographicTileCollection:
    """Verify exact tile artifact bytes and return the parsed collection."""
    if not isinstance(artifacts, GeographicTileArtifacts):
        raise ValidationError("artifacts must be GeographicTileArtifacts")
    index_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.tile_index),
        "tile_index.json",
    )
    collection = geographic_tile_collection_from_json(index_text)
    expected_index = canonical_json_text(geographic_tile_collection_to_dict(collection))
    if index_text != expected_index:
        raise SchemaError("tile_index.json is not canonical")
    manifests_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.tile_manifests),
        "tile_manifests.jsonl",
    )
    if manifests_text != geographic_tiles_jsonl(collection):
        raise SchemaError("tile_manifests.jsonl differs from tile index")
    summary_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.tile_summary),
        "tile_summary.md",
    )
    if summary_text != geographic_tile_summary_markdown(collection):
        raise SchemaError("tile_summary.md differs from tile index")
    return collection
