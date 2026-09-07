"""Deterministic split generation and conservative leakage control."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    WrittenArtifact,
    atomic_write_text,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.data.parquet_io import validate_canonical_table
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.data.tiling import (
    GeographicTile,
    GeographicTileCollection,
    TileCoordinateSpace,
    geographic_tile_collection_to_dict,
)
from kinematicweave.data.validation import (
    CanonicalValidationReport,
    canonical_validation_report_to_dict,
)
from kinematicweave.domain.records import ScenarioRecord
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.identifiers import validate_identifier
from kinematicweave.paths import normalize_relative_path
from kinematicweave.seeding import derive_seed, validate_root_seed

__all__ = [
    "CanonicalSplitArtifacts",
    "CanonicalSplitManifest",
    "GeographicLeakageGroup",
    "SplitGenerationConfig",
    "SplitMembership",
    "SplitName",
    "SplitUnitType",
    "build_geographic_leakage_groups",
    "canonical_split_manifest_from_dict",
    "canonical_split_manifest_from_json",
    "canonical_split_manifest_identity",
    "canonical_split_manifest_to_canonical_json",
    "canonical_split_manifest_to_dict",
    "canonical_split_summary_markdown",
    "generate_layout_split_manifest",
    "generate_motion_split_manifest",
    "geographic_leakage_group_to_dict",
    "materialize_canonical_split_manifests",
    "split_generation_config_to_dict",
    "validate_split_manifest",
    "verify_canonical_split_artifacts",
]

_SCHEMA_VERSION = "1.0"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SplitName(StrEnum):
    """Canonical split names in allocation and serialization order."""

    SMOKE = "smoke"
    DEVELOPMENT = "development"
    PILOT = "pilot"
    TEST = "test"
    HELD_OUT_CITY = "held_out_city"


class SplitUnitType(StrEnum):
    """Units assigned by a canonical split manifest."""

    MOTION_SCENARIO = "motion_scenario"
    LAYOUT_TILE = "layout_tile"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty trimmed text")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    return None if value is None else _required_text(value, field_name)


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


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


def _identifier_tuple(
    value: object,
    field_name: str,
    *,
    required: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    normalized = tuple(
        validate_identifier(cast(str, item)) for item in cast(Sequence[object], value)
    )
    if required and not normalized:
        raise ValidationError(f"{field_name} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return normalized


def _text_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    normalized = tuple(
        _required_text(item, f"{field_name} item")
        for item in cast(Sequence[object], value)
    )
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return normalized


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


def _sha256(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return value


@dataclass(frozen=True, slots=True)
class SplitGenerationConfig:
    """Deterministic split allocation configuration."""

    root_seed: int
    assignment_namespace: str
    smoke_count: int
    development_count: int
    pilot_count: int
    test_count: int | None
    held_out_city: str | None

    def __post_init__(self) -> None:
        """Normalize values and enforce allocation configuration types."""
        object.__setattr__(self, "root_seed", validate_root_seed(self.root_seed))
        object.__setattr__(
            self,
            "assignment_namespace",
            _required_text(self.assignment_namespace, "assignment_namespace"),
        )
        for field_name in ("smoke_count", "development_count", "pilot_count"):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        if self.test_count is not None:
            object.__setattr__(
                self,
                "test_count",
                _nonnegative_int(self.test_count, "test_count"),
            )
        object.__setattr__(
            self,
            "held_out_city",
            _optional_text(self.held_out_city, "held_out_city"),
        )


@dataclass(frozen=True, slots=True)
class SplitMembership:
    """Ordered unit and leakage-group membership for one split."""

    split_name: SplitName | str
    unit_ids: Sequence[str]
    leakage_group_ids: Sequence[str]

    def __post_init__(self) -> None:
        """Copy and validate ordered membership identifiers."""
        object.__setattr__(
            self,
            "split_name",
            _enum_value(SplitName, self.split_name, "split_name"),
        )
        object.__setattr__(
            self,
            "unit_ids",
            _identifier_tuple(self.unit_ids, "unit_ids"),
        )
        object.__setattr__(
            self,
            "leakage_group_ids",
            _identifier_tuple(self.leakage_group_ids, "leakage_group_ids"),
        )


@dataclass(frozen=True, slots=True)
class GeographicLeakageGroup:
    """One transitive connected component of overlapping tile contexts."""

    leakage_group_id: str
    tile_ids: Sequence[str]
    scenario_ids: Sequence[str]
    city_or_regions: Sequence[str]
    context_min_x_m: float
    context_min_y_m: float
    context_max_x_m: float
    context_max_y_m: float

    def __post_init__(self) -> None:
        """Copy collections and validate group bounds."""
        object.__setattr__(
            self,
            "leakage_group_id",
            validate_identifier(self.leakage_group_id),
        )
        object.__setattr__(
            self,
            "tile_ids",
            _identifier_tuple(self.tile_ids, "tile_ids", required=True),
        )
        object.__setattr__(
            self,
            "scenario_ids",
            _identifier_tuple(self.scenario_ids, "scenario_ids"),
        )
        object.__setattr__(
            self,
            "city_or_regions",
            _text_tuple(self.city_or_regions, "city_or_regions"),
        )
        for field_name in (
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
        if (
            self.context_max_x_m <= self.context_min_x_m
            or self.context_max_y_m <= self.context_min_y_m
        ):
            raise ValidationError("group maximum bounds must exceed minimum bounds")


@dataclass(frozen=True, slots=True)
class CanonicalSplitManifest:
    """Immutable deterministic assignments for one canonical unit type."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    unit_type: SplitUnitType | str
    config: SplitGenerationConfig
    source_validation_report_identity: str
    source_tile_collection_identity: str | None
    source_unit_count: int
    memberships: Sequence[SplitMembership]
    leakage_groups: Sequence[GeographicLeakageGroup]

    def __post_init__(self) -> None:
        """Copy nested records and enforce motion/layout manifest invariants."""
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
        unit_type = _enum_value(SplitUnitType, self.unit_type, "unit_type")
        object.__setattr__(self, "unit_type", unit_type)
        if not isinstance(self.config, SplitGenerationConfig):
            raise ValidationError("config must be SplitGenerationConfig")
        validation_identity = _sha256(
            self.source_validation_report_identity,
            "source_validation_report_identity",
        )
        object.__setattr__(
            self,
            "source_validation_report_identity",
            validation_identity,
        )
        tile_identity = _sha256(
            self.source_tile_collection_identity,
            "source_tile_collection_identity",
            optional=True,
        )
        object.__setattr__(self, "source_tile_collection_identity", tile_identity)
        object.__setattr__(
            self,
            "source_unit_count",
            _nonnegative_int(self.source_unit_count, "source_unit_count"),
        )

        memberships_value: object = self.memberships
        if isinstance(memberships_value, (str, bytes)) or not isinstance(
            memberships_value, Sequence
        ):
            raise ValidationError("memberships must be a non-string sequence")
        memberships = tuple(cast(Sequence[object], memberships_value))
        if any(not isinstance(item, SplitMembership) for item in memberships):
            raise ValidationError("memberships must contain SplitMembership values")
        typed_memberships = cast(tuple[SplitMembership, ...], memberships)
        if tuple(
            _enum_value(SplitName, item.split_name, "split_name")
            for item in typed_memberships
        ) != tuple(SplitName):
            raise ValidationError(
                "memberships must contain every SplitName in enum order"
            )
        all_units = tuple(
            unit_id
            for membership in typed_memberships
            for unit_id in membership.unit_ids
        )
        if len(all_units) != len(set(all_units)):
            raise ValidationError("unit identifiers must be unique across memberships")
        if len(all_units) != self.source_unit_count:
            raise ValidationError("assigned unit count must equal source_unit_count")
        object.__setattr__(self, "memberships", typed_memberships)

        groups_value: object = self.leakage_groups
        if isinstance(groups_value, (str, bytes)) or not isinstance(
            groups_value, Sequence
        ):
            raise ValidationError("leakage_groups must be a non-string sequence")
        groups = tuple(cast(Sequence[object], groups_value))
        if any(not isinstance(item, GeographicLeakageGroup) for item in groups):
            raise ValidationError(
                "leakage_groups must contain GeographicLeakageGroup values"
            )
        typed_groups = cast(tuple[GeographicLeakageGroup, ...], groups)
        object.__setattr__(self, "leakage_groups", typed_groups)
        if unit_type is SplitUnitType.MOTION_SCENARIO:
            if tile_identity is not None:
                raise ValidationError(
                    "motion manifest source tile identity must be None"
                )
            if typed_groups:
                raise ValidationError("motion manifest leakage_groups must be empty")
            if any(item.leakage_group_ids for item in typed_memberships):
                raise ValidationError(
                    "motion memberships must not contain leakage groups"
                )
            return

        if tile_identity is None:
            raise ValidationError("layout manifest requires source tile identity")
        if not typed_groups:
            raise ValidationError("layout manifest requires leakage groups")
        group_ids = tuple(group.leakage_group_id for group in typed_groups)
        if len(group_ids) != len(set(group_ids)):
            raise ValidationError("leakage group identifiers must be unique")
        tile_ids = tuple(
            tile_id for group in typed_groups for tile_id in group.tile_ids
        )
        if len(tile_ids) != len(set(tile_ids)):
            raise ValidationError("tile identifiers must be unique across groups")
        if len(tile_ids) != self.source_unit_count:
            raise ValidationError("every source tile must occur in one leakage group")
        assigned_groups = tuple(
            group_id
            for membership in typed_memberships
            for group_id in membership.leakage_group_ids
        )
        if len(assigned_groups) != len(set(assigned_groups)):
            raise ValidationError("leakage groups must be unique across memberships")
        if set(assigned_groups) != set(group_ids):
            raise ValidationError(
                "every leakage group must occur in exactly one membership"
            )
        by_group = {group.leakage_group_id: group for group in typed_groups}
        for membership in typed_memberships:
            expanded = tuple(
                tile_id
                for group_id in membership.leakage_group_ids
                for tile_id in by_group[group_id].tile_ids
            )
            if len(membership.unit_ids) != len(expanded) or set(
                membership.unit_ids
            ) != set(expanded):
                raise ValidationError(
                    "layout unit_ids must equal ordered leakage-group expansion"
                )
            for group_id in membership.leakage_group_ids:
                group_tile_ids = by_group[group_id].tile_ids
                group_tile_set = set(group_tile_ids)
                observed = tuple(
                    tile_id
                    for tile_id in membership.unit_ids
                    if tile_id in group_tile_set
                )
                if observed != group_tile_ids:
                    raise ValidationError(
                        "layout unit_ids must preserve group tile order"
                    )

    @property
    def assigned_unit_count(self) -> int:
        """Return the number of assigned scenarios or tiles."""
        return sum(len(item.unit_ids) for item in self.memberships)

    def _count(self, split_name: SplitName) -> int:
        return len(self.memberships[tuple(SplitName).index(split_name)].unit_ids)

    @property
    def smoke_unit_count(self) -> int:
        """Return assigned smoke scenarios or tiles."""
        return self._count(SplitName.SMOKE)

    @property
    def development_unit_count(self) -> int:
        """Return assigned development scenarios or tiles."""
        return self._count(SplitName.DEVELOPMENT)

    @property
    def pilot_unit_count(self) -> int:
        """Return assigned pilot scenarios or tiles."""
        return self._count(SplitName.PILOT)

    @property
    def test_unit_count(self) -> int:
        """Return assigned test scenarios or tiles."""
        return self._count(SplitName.TEST)

    @property
    def held_out_unit_count(self) -> int:
        """Return assigned held-out-city scenarios or tiles."""
        return self._count(SplitName.HELD_OUT_CITY)


@dataclass(frozen=True, slots=True)
class CanonicalSplitArtifacts:
    """Physical motion, layout, and summary split artifacts."""

    motion_manifest: WrittenArtifact
    layout_manifest: WrittenArtifact
    split_summary: WrittenArtifact

    def __post_init__(self) -> None:
        """Validate artifact types and unique paths."""
        artifacts = (
            self.motion_manifest,
            self.layout_manifest,
            self.split_summary,
        )
        if any(not isinstance(item, WrittenArtifact) for item in artifacts):
            raise ValidationError("split artifacts must use WrittenArtifact")
        if len({item.relative_path for item in artifacts}) != len(artifacts):
            raise ValidationError("split artifact paths must be unique")


def _table_rows(table: pa.Table) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for batch in table.to_batches(max_chunksize=65_536):
        names = tuple(batch.schema.names)
        columns = tuple(batch.column(index) for index in range(batch.num_columns))
        for row_index in range(batch.num_rows):
            rows.append(
                {
                    name: column[row_index].as_py()
                    for name, column in zip(names, columns, strict=True)
                }
            )
    return tuple(rows)


def _scenario_records(
    scenario_manifest: pa.Table,
) -> tuple[ScenarioRecord, ...]:
    validate_canonical_table(
        scenario_manifest,
        CanonicalSchemaName.SCENARIO_MANIFEST,
    )
    records: list[ScenarioRecord] = []
    keys: set[str] = set()
    try:
        for row in _table_rows(scenario_manifest):
            record = ScenarioRecord(**cast(Any, row))
            if record.scenario_id in keys:
                raise SchemaError("duplicate canonical scenario identifier")
            keys.add(record.scenario_id)
            records.append(record)
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None
    return tuple(records)


def _validation_identity(report: CanonicalValidationReport) -> str:
    if not isinstance(report, CanonicalValidationReport):
        raise ValidationError("validation_report must be CanonicalValidationReport")
    return canonical_sha256(
        "canonical-validation-report",
        canonical_validation_report_to_dict(report),
    )


def _ranked_units(
    unit_ids: Sequence[str],
    config: SplitGenerationConfig,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            unit_ids,
            key=lambda unit_id: (
                derive_seed(
                    config.root_seed,
                    "canonical-split",
                    config.assignment_namespace,
                    unit_id,
                ),
                unit_id,
            ),
        )
    )


def _allocate_units(
    ordinary_unit_ids: Sequence[str],
    config: SplitGenerationConfig,
) -> dict[SplitName, set[str]]:
    ranked = _ranked_units(ordinary_unit_ids, config)
    first_three = (
        config.smoke_count,
        config.development_count,
        config.pilot_count,
    )
    requested = sum(first_three)
    if config.test_count is None:
        if requested > len(ranked):
            raise ValidationError("ordinary split counts exceed available units")
        test_count = len(ranked) - requested
    else:
        test_count = config.test_count
        if requested + test_count != len(ranked):
            raise ValidationError(
                "explicit split counts must equal ordinary unit count"
            )
    counts = (
        (SplitName.SMOKE, config.smoke_count),
        (SplitName.DEVELOPMENT, config.development_count),
        (SplitName.PILOT, config.pilot_count),
        (SplitName.TEST, test_count),
    )
    selected: dict[SplitName, set[str]] = {
        split_name: set() for split_name in SplitName
    }
    offset = 0
    for split_name, count in counts:
        selected[split_name].update(ranked[offset : offset + count])
        offset += count
    if offset != len(ranked):
        raise ValidationError("split allocation left units unassigned")
    return selected


def _memberships_for_motion(
    scenarios: Sequence[ScenarioRecord],
    selected: Mapping[SplitName, set[str]],
    held_out_ids: set[str],
) -> tuple[SplitMembership, ...]:
    result: list[SplitMembership] = []
    for split_name in SplitName:
        identifiers = (
            held_out_ids
            if split_name is SplitName.HELD_OUT_CITY
            else selected[split_name]
        )
        result.append(
            SplitMembership(
                split_name=split_name,
                unit_ids=tuple(
                    scenario.scenario_id
                    for scenario in scenarios
                    if scenario.scenario_id in identifiers
                ),
                leakage_group_ids=(),
            )
        )
    return tuple(result)


def generate_motion_split_manifest(
    scenario_manifest: pa.Table,
    validation_report: CanonicalValidationReport,
    *,
    config: SplitGenerationConfig,
) -> CanonicalSplitManifest:
    """Generate deterministic scenario-level motion split assignments."""
    if not isinstance(config, SplitGenerationConfig):
        raise ValidationError("config must be SplitGenerationConfig")
    scenarios = _scenario_records(scenario_manifest)
    identity = _validation_identity(validation_report)
    included_ids = set(validation_report.included_scenario_ids)
    counts: dict[str, int] = {}
    included_scenarios: list[ScenarioRecord] = []
    for scenario in scenarios:
        if (
            scenario.dataset_id != validation_report.dataset_id
            or scenario.dataset_version != validation_report.dataset_version
        ):
            raise SchemaError(
                "scenario dataset identifier or version differs from report"
            )
        counts[scenario.scenario_id] = counts.get(scenario.scenario_id, 0) + 1
        if scenario.scenario_id in included_ids:
            included_scenarios.append(scenario)
    if any(counts.get(identifier, 0) != 1 for identifier in included_ids):
        raise SchemaError("every included scenario must exist exactly once")
    source_ids = [
        scenario.source_scenario_id
        for scenario in included_scenarios
        if scenario.source_scenario_id is not None
    ]
    if len(source_ids) != len(set(source_ids)):
        raise SchemaError("included scenarios contain duplicate source_scenario_id")

    held_out_ids: set[str] = set()
    if config.held_out_city is not None:
        held_out_ids = {
            scenario.scenario_id
            for scenario in included_scenarios
            if scenario.city_or_region == config.held_out_city
        }
        if not held_out_ids:
            raise ValidationError("configured held-out city has no scenarios")
    ordinary_ids = tuple(
        scenario.scenario_id
        for scenario in included_scenarios
        if scenario.scenario_id not in held_out_ids
    )
    selected = _allocate_units(ordinary_ids, config)
    memberships = _memberships_for_motion(
        included_scenarios,
        selected,
        held_out_ids,
    )
    return CanonicalSplitManifest(
        schema_version=_SCHEMA_VERSION,
        dataset_id=validation_report.dataset_id,
        dataset_version=validation_report.dataset_version,
        unit_type=SplitUnitType.MOTION_SCENARIO,
        config=config,
        source_validation_report_identity=identity,
        source_tile_collection_identity=None,
        source_unit_count=len(included_scenarios),
        memberships=memberships,
        leakage_groups=(),
    )


@dataclass(slots=True)
class _UnionFind:
    parent: list[int]
    rank: list[int]

    def find(self, index: int) -> int:
        """Return a component root with deterministic path compression."""
        root = index
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[index] != index:
            next_index = self.parent[index]
            self.parent[index] = root
            index = next_index
        return root

    def union(self, left: int, right: int) -> None:
        """Join two components with deterministic rank and index tie-breaking."""
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        left_rank = self.rank[left_root]
        right_rank = self.rank[right_root]
        if left_rank < right_rank or (
            left_rank == right_rank and left_root > right_root
        ):
            left_root, right_root = right_root, left_root
            left_rank, right_rank = right_rank, left_rank
        self.parent[right_root] = left_root
        if left_rank == right_rank:
            self.rank[left_root] += 1


def _positive_context_overlap(
    left: GeographicTile,
    right: GeographicTile,
) -> bool:
    left_spec = left.spec
    right_spec = right.spec
    overlap_width = min(
        left_spec.context_max_x_m,
        right_spec.context_max_x_m,
    ) - max(
        left_spec.context_min_x_m,
        right_spec.context_min_x_m,
    )
    overlap_height = min(
        left_spec.context_max_y_m,
        right_spec.context_max_y_m,
    ) - max(
        left_spec.context_min_y_m,
        right_spec.context_min_y_m,
    )
    return overlap_width > 0.0 and overlap_height > 0.0


def _scenario_lookup(
    scenario_manifest: pa.Table,
    dataset_id: str,
    dataset_version: str,
) -> tuple[dict[str, ScenarioRecord], dict[str, int]]:
    scenarios = _scenario_records(scenario_manifest)
    lookup: dict[str, ScenarioRecord] = {}
    ranks: dict[str, int] = {}
    for rank, scenario in enumerate(scenarios):
        if (
            scenario.dataset_id != dataset_id
            or scenario.dataset_version != dataset_version
        ):
            raise SchemaError(
                "scenario dataset identifier or version differs from tiles"
            )
        lookup[scenario.scenario_id] = scenario
        ranks[scenario.scenario_id] = rank
    return lookup, ranks


def build_geographic_leakage_groups(
    tile_collection: GeographicTileCollection,
    scenario_manifest: pa.Table,
) -> tuple[GeographicLeakageGroup, ...]:
    """Build deterministic transitive groups of overlapping tile contexts."""
    if not isinstance(tile_collection, GeographicTileCollection):
        raise ValidationError("tile_collection must be GeographicTileCollection")
    scenario_by_id, scenario_ranks = _scenario_lookup(
        scenario_manifest,
        tile_collection.dataset_id,
        tile_collection.dataset_version,
    )
    tiles = tuple(tile_collection.tiles)
    coordinate_to_index = {
        (tile.spec.grid_x, tile.spec.grid_y): index for index, tile in enumerate(tiles)
    }
    union_find = _UnionFind(
        parent=list(range(len(tiles))),
        rank=[0] * len(tiles),
    )
    radius = max(
        1,
        math.ceil(
            2.0
            * tile_collection.config.context_buffer_m
            / tile_collection.config.tile_size_m
        ),
    )
    for index, tile in enumerate(tiles):
        for delta_x in range(-radius, radius + 1):
            for delta_y in range(-radius, radius + 1):
                neighbor_index = coordinate_to_index.get(
                    (
                        tile.spec.grid_x + delta_x,
                        tile.spec.grid_y + delta_y,
                    )
                )
                if neighbor_index is None or neighbor_index <= index:
                    continue
                if _positive_context_overlap(tile, tiles[neighbor_index]):
                    union_find.union(index, neighbor_index)

    components: dict[int, list[int]] = {}
    for index in range(len(tiles)):
        root = union_find.find(index)
        components.setdefault(root, []).append(index)

    groups: list[tuple[tuple[int, int, str], GeographicLeakageGroup]] = []
    for indices in components.values():
        ordered_indices = tuple(sorted(indices))
        tile_ids = tuple(tiles[index].spec.tile_id for index in ordered_indices)
        scenario_ids: list[str] = []
        scenario_seen: set[str] = set()
        for index in ordered_indices:
            for scenario_id in tiles[index].membership.context_scenario_ids:
                if scenario_id not in scenario_by_id:
                    raise SchemaError("tile references an unknown scenario")
                if scenario_id not in scenario_seen:
                    scenario_seen.add(scenario_id)
                    scenario_ids.append(scenario_id)
        cities = tuple(
            scenario.city_or_region
            for scenario in sorted(
                (scenario_by_id[scenario_id] for scenario_id in scenario_ids),
                key=lambda scenario: scenario_ranks[scenario.scenario_id],
            )
            if scenario.city_or_region is not None
        )
        unique_cities = tuple(dict.fromkeys(cities))
        digest = canonical_sha256(
            "geographic-leakage-group",
            {
                "dataset_id": tile_collection.dataset_id,
                "dataset_version": tile_collection.dataset_version,
                "coordinate_space": _enum_value(
                    TileCoordinateSpace,
                    tiles[ordered_indices[0]].spec.coordinate_space,
                    "coordinate_space",
                ).value,
                "tile_ids": list(tile_ids),
            },
        )
        group = GeographicLeakageGroup(
            leakage_group_id=(
                f"leakage-group:{tile_collection.dataset_id}:{digest[:24]}"
            ),
            tile_ids=tile_ids,
            scenario_ids=tuple(scenario_ids),
            city_or_regions=unique_cities,
            context_min_x_m=min(
                tiles[index].spec.context_min_x_m for index in ordered_indices
            ),
            context_min_y_m=min(
                tiles[index].spec.context_min_y_m for index in ordered_indices
            ),
            context_max_x_m=max(
                tiles[index].spec.context_max_x_m for index in ordered_indices
            ),
            context_max_y_m=max(
                tiles[index].spec.context_max_y_m for index in ordered_indices
            ),
        )
        minimum_x = min(tiles[index].spec.grid_x for index in ordered_indices)
        minimum_y = min(tiles[index].spec.grid_y for index in ordered_indices)
        groups.append(
            (
                (minimum_x, minimum_y, group.leakage_group_id),
                group,
            )
        )
    groups.sort(key=lambda item: item[0])
    result = tuple(group for _key, group in groups)
    assigned_tiles = tuple(tile_id for group in result for tile_id in group.tile_ids)
    if len(assigned_tiles) != len(tiles) or set(assigned_tiles) != {
        tile.spec.tile_id for tile in tiles
    }:
        raise SchemaError("every tile must occur in exactly one leakage group")
    return result


def _memberships_for_layout(
    groups: Sequence[GeographicLeakageGroup],
    selected: Mapping[SplitName, set[str]],
    held_out_group_ids: set[str],
    tile_order: Sequence[str],
) -> tuple[SplitMembership, ...]:
    result: list[SplitMembership] = []
    for split_name in SplitName:
        identifiers = (
            held_out_group_ids
            if split_name is SplitName.HELD_OUT_CITY
            else selected[split_name]
        )
        selected_groups = tuple(
            group for group in groups if group.leakage_group_id in identifiers
        )
        selected_tile_ids = {
            tile_id for group in selected_groups for tile_id in group.tile_ids
        }
        result.append(
            SplitMembership(
                split_name=split_name,
                unit_ids=tuple(
                    tile_id for tile_id in tile_order if tile_id in selected_tile_ids
                ),
                leakage_group_ids=tuple(
                    group.leakage_group_id for group in selected_groups
                ),
            )
        )
    return tuple(result)


def generate_layout_split_manifest(
    tile_collection: GeographicTileCollection,
    scenario_manifest: pa.Table,
    validation_report: CanonicalValidationReport,
    *,
    config: SplitGenerationConfig,
) -> CanonicalSplitManifest:
    """Generate deterministic indivisible leakage-group layout splits."""
    if not isinstance(tile_collection, GeographicTileCollection):
        raise ValidationError("tile_collection must be GeographicTileCollection")
    if not isinstance(config, SplitGenerationConfig):
        raise ValidationError("config must be SplitGenerationConfig")
    identity = _validation_identity(validation_report)
    if (
        tile_collection.dataset_id != validation_report.dataset_id
        or tile_collection.dataset_version != validation_report.dataset_version
    ):
        raise SchemaError("tile collection dataset differs from validation report")
    if tile_collection.validation_report_identity != identity:
        raise SchemaError("tile collection validation identity differs from report")
    groups = build_geographic_leakage_groups(
        tile_collection,
        scenario_manifest,
    )
    held_out_group_ids: set[str] = set()
    if config.held_out_city is not None:
        for group in groups:
            if config.held_out_city not in group.city_or_regions:
                continue
            other_cities = tuple(
                city for city in group.city_or_regions if city != config.held_out_city
            )
            if other_cities:
                raise ValidationError("held-out leakage group contains another city")
            held_out_group_ids.add(group.leakage_group_id)
        if not held_out_group_ids:
            raise ValidationError("configured held-out city has no leakage groups")
    ordinary_group_ids = tuple(
        group.leakage_group_id
        for group in groups
        if group.leakage_group_id not in held_out_group_ids
    )
    selected = _allocate_units(ordinary_group_ids, config)
    memberships = _memberships_for_layout(
        groups,
        selected,
        held_out_group_ids,
        tuple(tile.spec.tile_id for tile in tile_collection.tiles),
    )
    tile_identity = canonical_sha256(
        "geographic-tile-collection",
        geographic_tile_collection_to_dict(tile_collection),
    )
    return CanonicalSplitManifest(
        schema_version=_SCHEMA_VERSION,
        dataset_id=tile_collection.dataset_id,
        dataset_version=tile_collection.dataset_version,
        unit_type=SplitUnitType.LAYOUT_TILE,
        config=config,
        source_validation_report_identity=identity,
        source_tile_collection_identity=tile_identity,
        source_unit_count=tile_collection.tile_count,
        memberships=memberships,
        leakage_groups=groups,
    )


def _split_by_tile(
    manifest: CanonicalSplitManifest,
) -> dict[str, SplitName]:
    return {
        unit_id: _enum_value(SplitName, membership.split_name, "split_name")
        for membership in manifest.memberships
        for unit_id in membership.unit_ids
    }


def _validate_no_cross_split_overlap(
    manifest: CanonicalSplitManifest,
    tile_collection: GeographicTileCollection,
) -> None:
    split_by_tile = _split_by_tile(manifest)
    tiles = tuple(tile_collection.tiles)
    expected_tile_ids = {tile.spec.tile_id for tile in tiles}
    if set(split_by_tile) != expected_tile_ids:
        raise SchemaError("layout manifest tile identifiers differ from collection")
    coordinate_to_index = {
        (tile.spec.grid_x, tile.spec.grid_y): index for index, tile in enumerate(tiles)
    }
    radius = max(
        1,
        math.ceil(
            2.0
            * tile_collection.config.context_buffer_m
            / tile_collection.config.tile_size_m
        ),
    )
    for index, tile in enumerate(tiles):
        for delta_x in range(-radius, radius + 1):
            for delta_y in range(-radius, radius + 1):
                neighbor_index = coordinate_to_index.get(
                    (
                        tile.spec.grid_x + delta_x,
                        tile.spec.grid_y + delta_y,
                    )
                )
                if neighbor_index is None or neighbor_index <= index:
                    continue
                neighbor = tiles[neighbor_index]
                if split_by_tile[tile.spec.tile_id] != split_by_tile[
                    neighbor.spec.tile_id
                ] and _positive_context_overlap(tile, neighbor):
                    raise SchemaError("tiles in different splits have buffered overlap")


def validate_split_manifest(
    manifest: CanonicalSplitManifest,
    *,
    scenario_manifest: pa.Table,
    validation_report: CanonicalValidationReport,
    tile_collection: GeographicTileCollection | None = None,
) -> None:
    """Regenerate and validate one motion or layout split manifest."""
    if not isinstance(manifest, CanonicalSplitManifest):
        raise ValidationError("manifest must be CanonicalSplitManifest")
    if (
        manifest.dataset_id != validation_report.dataset_id
        or manifest.dataset_version != validation_report.dataset_version
    ):
        raise SchemaError("split manifest dataset differs from validation report")
    try:
        unit_type = _enum_value(SplitUnitType, manifest.unit_type, "unit_type")
        if unit_type is SplitUnitType.MOTION_SCENARIO:
            regenerated = generate_motion_split_manifest(
                scenario_manifest,
                validation_report,
                config=manifest.config,
            )
        else:
            if tile_collection is None:
                raise SchemaError("tile_collection is required for layout validation")
            regenerated = generate_layout_split_manifest(
                tile_collection,
                scenario_manifest,
                validation_report,
                config=manifest.config,
            )
            _validate_no_cross_split_overlap(manifest, tile_collection)
    except SchemaError:
        raise
    except ValidationError as error:
        raise SchemaError(str(error)) from None
    if manifest != regenerated:
        raise SchemaError("split manifest differs from deterministic regeneration")


def split_generation_config_to_dict(
    config: SplitGenerationConfig,
) -> dict[str, object]:
    """Return an ordered JSON-compatible split configuration."""
    if not isinstance(config, SplitGenerationConfig):
        raise ValidationError("config must be SplitGenerationConfig")
    return {
        "root_seed": config.root_seed,
        "assignment_namespace": config.assignment_namespace,
        "smoke_count": config.smoke_count,
        "development_count": config.development_count,
        "pilot_count": config.pilot_count,
        "test_count": config.test_count,
        "held_out_city": config.held_out_city,
    }


def geographic_leakage_group_to_dict(
    group: GeographicLeakageGroup,
) -> dict[str, object]:
    """Return one ordered JSON-compatible leakage group."""
    if not isinstance(group, GeographicLeakageGroup):
        raise ValidationError("group must be GeographicLeakageGroup")
    return {
        "leakage_group_id": group.leakage_group_id,
        "tile_ids": list(group.tile_ids),
        "scenario_ids": list(group.scenario_ids),
        "city_or_regions": list(group.city_or_regions),
        "context_min_x_m": group.context_min_x_m,
        "context_min_y_m": group.context_min_y_m,
        "context_max_x_m": group.context_max_x_m,
        "context_max_y_m": group.context_max_y_m,
    }


def _membership_to_dict(membership: SplitMembership) -> dict[str, object]:
    return {
        "split_name": _enum_value(
            SplitName,
            membership.split_name,
            "split_name",
        ).value,
        "unit_ids": list(membership.unit_ids),
        "leakage_group_ids": list(membership.leakage_group_ids),
    }


def canonical_split_manifest_to_dict(
    manifest: CanonicalSplitManifest,
) -> dict[str, object]:
    """Return the complete ordered JSON-compatible split manifest."""
    if not isinstance(manifest, CanonicalSplitManifest):
        raise ValidationError("manifest must be CanonicalSplitManifest")
    return {
        "schema_version": manifest.schema_version,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "unit_type": _enum_value(
            SplitUnitType,
            manifest.unit_type,
            "unit_type",
        ).value,
        "config": split_generation_config_to_dict(manifest.config),
        "source_validation_report_identity": (
            manifest.source_validation_report_identity
        ),
        "source_tile_collection_identity": (manifest.source_tile_collection_identity),
        "source_unit_count": manifest.source_unit_count,
        "memberships": [
            _membership_to_dict(membership) for membership in manifest.memberships
        ],
        "leakage_groups": [
            geographic_leakage_group_to_dict(group) for group in manifest.leakage_groups
        ],
    }


def canonical_split_manifest_to_canonical_json(
    manifest: CanonicalSplitManifest,
) -> str:
    """Serialize a split manifest as canonical JSON with one newline."""
    return canonical_json_text(canonical_split_manifest_to_dict(manifest))


def canonical_split_manifest_identity(
    manifest: CanonicalSplitManifest,
) -> str:
    """Return the domain-separated identity of a split manifest."""
    return canonical_sha256(
        "canonical-split-manifest",
        canonical_split_manifest_to_dict(manifest),
    )


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


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SchemaError(f"{label} must be an array")
    return cast(Sequence[object], value)


_CONFIG_FIELDS = tuple(field.name for field in fields(SplitGenerationConfig))
_MEMBERSHIP_FIELDS = tuple(field.name for field in fields(SplitMembership))
_GROUP_FIELDS = tuple(field.name for field in fields(GeographicLeakageGroup))
_MANIFEST_FIELDS = tuple(field.name for field in fields(CanonicalSplitManifest))


def canonical_split_manifest_from_dict(
    value: Mapping[str, object],
) -> CanonicalSplitManifest:
    """Reconstruct a split manifest from one exact JSON mapping."""
    mapping = _exact_mapping(value, _MANIFEST_FIELDS, "split manifest")
    config_mapping = _exact_mapping(
        mapping["config"],
        _CONFIG_FIELDS,
        "split config",
    )
    membership_values = _sequence(mapping["memberships"], "memberships")
    group_values = _sequence(mapping["leakage_groups"], "leakage_groups")
    try:
        config = SplitGenerationConfig(**cast(Any, dict(config_mapping)))
        memberships = tuple(
            SplitMembership(
                **cast(
                    Any,
                    dict(
                        _exact_mapping(
                            item,
                            _MEMBERSHIP_FIELDS,
                            "split membership",
                        )
                    ),
                )
            )
            for item in membership_values
        )
        groups = tuple(
            GeographicLeakageGroup(
                **cast(
                    Any,
                    dict(
                        _exact_mapping(
                            item,
                            _GROUP_FIELDS,
                            "leakage group",
                        )
                    ),
                )
            )
            for item in group_values
        )
        return CanonicalSplitManifest(
            schema_version=cast(str, mapping["schema_version"]),
            dataset_id=cast(str, mapping["dataset_id"]),
            dataset_version=cast(str, mapping["dataset_version"]),
            unit_type=cast(str, mapping["unit_type"]),
            config=config,
            source_validation_report_identity=cast(
                str,
                mapping["source_validation_report_identity"],
            ),
            source_tile_collection_identity=cast(
                str | None,
                mapping["source_tile_collection_identity"],
            ),
            source_unit_count=cast(int, mapping["source_unit_count"]),
            memberships=memberships,
            leakage_groups=groups,
        )
    except SchemaError:
        raise
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def canonical_split_manifest_from_json(
    text: str,
) -> CanonicalSplitManifest:
    """Parse and reconstruct one canonical split manifest."""
    if not isinstance(text, str):
        raise SchemaError("split manifest JSON must be text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError("split manifest JSON is malformed") from None
    if not isinstance(value, Mapping):
        raise SchemaError("split manifest JSON root must be an object")
    return canonical_split_manifest_from_dict(cast(Mapping[str, object], value))


def _manifest_for_type(
    manifest: CanonicalSplitManifest,
    expected: SplitUnitType,
    label: str,
) -> CanonicalSplitManifest:
    if not isinstance(manifest, CanonicalSplitManifest):
        raise ValidationError(f"{label} must be CanonicalSplitManifest")
    if _enum_value(SplitUnitType, manifest.unit_type, "unit_type") is not expected:
        raise ValidationError(f"{label} has the wrong unit_type")
    return manifest


def _display_optional(value: str | None) -> str:
    return "None" if value is None else value


def canonical_split_summary_markdown(
    motion_manifest: CanonicalSplitManifest,
    layout_manifest: CanonicalSplitManifest,
) -> str:
    """Render a deterministic combined motion/layout split summary."""
    motion = _manifest_for_type(
        motion_manifest,
        SplitUnitType.MOTION_SCENARIO,
        "motion_manifest",
    )
    layout = _manifest_for_type(
        layout_manifest,
        SplitUnitType.LAYOUT_TILE,
        "layout_manifest",
    )
    if (
        motion.dataset_id != layout.dataset_id
        or motion.dataset_version != layout.dataset_version
    ):
        raise ValidationError("motion and layout manifest datasets must match")
    largest_group = max(
        (len(group.tile_ids) for group in layout.leakage_groups),
        default=0,
    )
    lines = [
        "# Canonical Split Summary",
        "",
        f"- Dataset: `{motion.dataset_id}`",
        f"- Dataset version: `{motion.dataset_version}`",
        f"- Motion root seed: {motion.config.root_seed}",
        f"- Motion assignment namespace: `{motion.config.assignment_namespace}`",
        f"- Layout root seed: {layout.config.root_seed}",
        f"- Layout assignment namespace: `{layout.config.assignment_namespace}`",
        (f"- Motion held-out city: `{_display_optional(motion.config.held_out_city)}`"),
        (f"- Layout held-out city: `{_display_optional(layout.config.held_out_city)}`"),
        f"- Total leakage groups: {len(layout.leakage_groups)}",
        f"- Largest leakage group tile count: {largest_group}",
        "",
        "## Motion Scenarios",
        "",
        "| Split | Scenario count |",
        "|---|---:|",
    ]
    lines.extend(
        (
            f"| `{_enum_value(SplitName, membership.split_name, 'split_name').value}` "
            f"| {len(membership.unit_ids)} |"
        )
        for membership in motion.memberships
    )
    lines.extend(
        (
            "",
            "## Layout Tiles",
            "",
            "| Split | Leakage-group count | Tile count |",
            "|---|---:|---:|",
        )
    )
    lines.extend(
        (
            f"| `{_enum_value(SplitName, membership.split_name, 'split_name').value}` | "
            f"{len(membership.leakage_group_ids)} | "
            f"{len(membership.unit_ids)} |"
        )
        for membership in layout.memberships
    )
    lines.extend(
        (
            "",
            "Layout leakage groups are indivisible across splits.",
            "",
        )
    )
    return "\n".join(lines)


def materialize_canonical_split_manifests(
    run_directory: RunDirectory,
    motion_manifest: CanonicalSplitManifest,
    layout_manifest: CanonicalSplitManifest,
    *,
    relative_directory: str | Path = "artifacts/canonical_splits",
) -> CanonicalSplitArtifacts:
    """Atomically write the exact motion, layout, and summary artifacts."""
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be RunDirectory")
    motion = _manifest_for_type(
        motion_manifest,
        SplitUnitType.MOTION_SCENARIO,
        "motion_manifest",
    )
    layout = _manifest_for_type(
        layout_manifest,
        SplitUnitType.LAYOUT_TILE,
        "layout_manifest",
    )
    if (
        motion.dataset_id != layout.dataset_id
        or motion.dataset_version != layout.dataset_version
    ):
        raise ValidationError("motion and layout manifest datasets must match")
    try:
        directory = normalize_relative_path(relative_directory)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    motion_artifact = atomic_write_text(
        run_directory,
        directory / "motion_splits.json",
        canonical_split_manifest_to_canonical_json(motion),
    )
    layout_artifact = atomic_write_text(
        run_directory,
        directory / "layout_splits.json",
        canonical_split_manifest_to_canonical_json(layout),
    )
    summary_artifact = atomic_write_text(
        run_directory,
        directory / "split_summary.md",
        canonical_split_summary_markdown(motion, layout),
    )
    return CanonicalSplitArtifacts(
        motion_manifest=motion_artifact,
        layout_manifest=layout_artifact,
        split_summary=summary_artifact,
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
        raise ArtifactError("canonical split artifact is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("canonical split artifact resolves outside repository")
    if candidate.is_symlink() or not resolved.is_file():
        raise ArtifactError("canonical split artifact must be a regular file")
    digest = hashlib.sha256()
    data = bytearray()
    try:
        with resolved.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                data.extend(chunk)
    except OSError as error:
        raise ArtifactError("cannot read canonical split artifact") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("canonical split artifact size differs")
    if digest.hexdigest() != artifact.content_checksum:
        raise ArtifactError("canonical split artifact SHA-256 differs")
    return bytes(data)


def _utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise SchemaError(f"{label} is not valid UTF-8") from None


def verify_canonical_split_artifacts(
    repository_root: Path,
    artifacts: CanonicalSplitArtifacts,
) -> tuple[CanonicalSplitManifest, CanonicalSplitManifest]:
    """Verify split artifact bytes and return motion then layout manifests."""
    if not isinstance(artifacts, CanonicalSplitArtifacts):
        raise ValidationError("artifacts must be CanonicalSplitArtifacts")
    motion_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.motion_manifest),
        "motion_splits.json",
    )
    layout_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.layout_manifest),
        "layout_splits.json",
    )
    motion = canonical_split_manifest_from_json(motion_text)
    layout = canonical_split_manifest_from_json(layout_text)
    if motion_text != canonical_split_manifest_to_canonical_json(motion):
        raise SchemaError("motion_splits.json is not canonical")
    if layout_text != canonical_split_manifest_to_canonical_json(layout):
        raise SchemaError("layout_splits.json is not canonical")
    try:
        expected_summary = canonical_split_summary_markdown(motion, layout)
    except ValidationError as error:
        raise SchemaError(str(error)) from None
    summary_text = _utf8(
        _verified_artifact_bytes(repository_root, artifacts.split_summary),
        "split_summary.md",
    )
    if summary_text != expected_summary:
        raise SchemaError("split_summary.md differs from split manifests")
    return motion, layout
