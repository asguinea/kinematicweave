"""Frozen AV2 motion-evaluation cohort contracts and bounded summaries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.data import av2_acquisition, pilot, validation
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.paths import normalize_relative_path
from kinematicweave.seeding import derive_seed, validate_root_seed

__all__ = [
    "CohortRole",
    "CohortRunMeasurements",
    "CohortValidationSummary",
    "DistributionSummary",
    "MotionCohortConfig",
    "MotionCohortManifest",
    "MotionCohortSelectionUnit",
    "MotionCohortUnit",
    "ResourcePilotResult",
    "build_cohort_manifest",
    "build_role_acquisition_plan",
    "build_role_pilot_plan",
    "cohort_dataset_version",
    "cohort_run_measurements_from_json",
    "cohort_run_measurements_to_canonical_json",
    "cohort_validation_summaries",
    "cohort_validation_summary_from_json",
    "cohort_validation_summary_to_canonical_json",
    "motion_cohort_manifest_from_json",
    "motion_cohort_manifest_identity",
    "motion_cohort_manifest_to_canonical_json",
    "resource_pilot_result_from_json",
    "resource_pilot_result_to_canonical_json",
    "select_motion_cohort",
    "validation_status_by_source",
]

_SCHEMA_VERSION = "1.0"
_DATASET_ID = "av2_motion"
_SELECTION_POLICY = "phase4-motion-cohort-ranking-v1"
_COHORT_DOMAIN = "phase4-motion-evaluation-cohort"
_ROLE_ORDER = {"development": 0, "pilot": 1, "test": 2}
_SHA256_HEX = frozenset("0123456789abcdef")


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty trimmed text")
    return value.strip()


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{field_name} must be a nonnegative integer")
    return value


def _positive_int(value: object, field_name: str) -> int:
    value = _nonnegative_int(value, field_name)
    if value == 0:
        raise ValidationError(f"{field_name} must be positive")
    return value


def _finite_nonnegative(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a finite nonnegative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValidationError(f"{field_name} must be a finite nonnegative number")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_HEX for character in value)
    ):
        raise ValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, Path)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    return cast(Sequence[object], value)


def _exact_mapping(
    value: object,
    fields: tuple[str, ...],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SchemaError(f"{label} must be a JSON object")
    mapping = cast(Mapping[str, object], value)
    missing = tuple(field for field in fields if field not in mapping)
    unknown = tuple(field for field in mapping if field not in fields)
    if missing:
        raise SchemaError(f"{label} is missing field: {missing[0]}")
    if unknown:
        raise SchemaError(f"{label} contains unknown field: {unknown[0]}")
    return mapping


def _json_mapping(text: str, label: str) -> Mapping[str, object]:
    if not isinstance(text, str):
        raise SchemaError(f"{label} JSON must be text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise SchemaError(f"{label} JSON is invalid") from None
    if not isinstance(value, Mapping):
        raise SchemaError(f"{label} JSON must contain an object")
    return cast(Mapping[str, object], value)


def _count_pairs(value: object, field_name: str) -> tuple[tuple[str, int], ...]:
    pairs: list[tuple[str, int]] = []
    for item in _sequence(value, field_name):
        values = _sequence(item, f"{field_name} item")
        if len(values) != 2:
            raise ValidationError(f"{field_name} items must be pairs")
        key = _required_text(values[0], f"{field_name} key")
        pairs.append((key, _nonnegative_int(values[1], f"{field_name} count")))
    if tuple(sorted(pairs)) != tuple(pairs):
        raise ValidationError(f"{field_name} must be key-sorted")
    if len({key for key, _count in pairs}) != len(pairs):
        raise ValidationError(f"{field_name} keys must be unique")
    return tuple(pairs)


class CohortRole(StrEnum):
    """Frozen Phase 4 cohort roles."""

    DEVELOPMENT = "development"
    PILOT = "pilot"
    TEST = "test"


def _role(value: object) -> CohortRole:
    if isinstance(value, CohortRole):
        return value
    try:
        return CohortRole(cast(Any, value))
    except (TypeError, ValueError):
        raise ValidationError("cohort_role must use CohortRole") from None


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionCohortConfig:
    """Frozen selection and provider configuration for Phase 4."""

    schema_version: str = _SCHEMA_VERSION
    dataset_id: str = _DATASET_ID
    official_source: str = av2_acquisition.AV2_OFFICIAL_MOTION_ROOT
    root_seed: int = 0
    selection_policy: str = _SELECTION_POLICY
    train_partition: str = "train"
    val_partition: str = "val"
    development_count: int = 150
    pilot_count: int = 50
    test_count: int = 300

    def __post_init__(self) -> None:
        """Enforce the approved exact 150/50/300 cohort contract."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError("schema_version must equal '1.0'")
        if self.dataset_id != _DATASET_ID:
            raise ValidationError("dataset_id must equal 'av2_motion'")
        if self.official_source != av2_acquisition.AV2_OFFICIAL_MOTION_ROOT:
            raise ValidationError("official_source must use the official AV2 root")
        object.__setattr__(self, "root_seed", validate_root_seed(self.root_seed))
        if self.selection_policy != _SELECTION_POLICY:
            raise ValidationError(f"selection_policy must equal {_SELECTION_POLICY!r}")
        if self.train_partition != "train" or self.val_partition != "val":
            raise ValidationError("provider partitions must be train and val")
        if (
            self.development_count,
            self.pilot_count,
            self.test_count,
        ) != (150, 50, 300):
            raise ValidationError("cohort role counts must equal 150, 50, and 300")


@dataclass(frozen=True, slots=True)
class MotionCohortSelectionUnit:
    """One outcome-blind selected provider scenario before acquisition."""

    provider_partition: str
    cohort_role: CohortRole | str
    selection_rank: int
    remote_pair: av2_acquisition.Av2RemoteScenarioPair

    def __post_init__(self) -> None:
        """Validate partition, role, rank, and provider pair agreement."""
        role = _role(self.cohort_role)
        partition = _required_text(self.provider_partition, "provider_partition")
        expected_partition = "val" if role is CohortRole.TEST else "train"
        if partition != expected_partition:
            raise ValidationError("cohort role uses the wrong provider partition")
        if not isinstance(self.remote_pair, av2_acquisition.Av2RemoteScenarioPair):
            raise ValidationError("remote_pair must be Av2RemoteScenarioPair")
        if not self.remote_pair.motion.relative_key.startswith(f"{partition}/"):
            raise ValidationError("remote pair differs from provider_partition")
        object.__setattr__(self, "cohort_role", role)
        object.__setattr__(
            self,
            "selection_rank",
            _positive_int(self.selection_rank, "selection_rank"),
        )


def _selection_key(unit: MotionCohortSelectionUnit) -> tuple[int, int, str]:
    return (
        _ROLE_ORDER[_role(unit.cohort_role).value],
        unit.selection_rank,
        unit.remote_pair.source_scenario_id,
    )


def _ranked(
    pairs: Sequence[av2_acquisition.Av2RemoteScenarioPair],
    *,
    config: MotionCohortConfig,
    role: CohortRole,
    partition: str,
) -> tuple[tuple[int, av2_acquisition.Av2RemoteScenarioPair], ...]:
    values = tuple(pairs)
    if not values or any(
        not isinstance(item, av2_acquisition.Av2RemoteScenarioPair) for item in values
    ):
        raise ValidationError("provider catalog must contain remote scenario pairs")
    if len({item.source_scenario_id for item in values}) != len(values):
        raise ValidationError("provider catalog scenario IDs must be unique")
    ordered = sorted(
        values,
        key=lambda item: (
            derive_seed(
                config.root_seed,
                _SELECTION_POLICY,
                role.value,
                partition,
                item.source_scenario_id,
            ),
            item.source_scenario_id,
        ),
    )
    return tuple(enumerate(ordered, start=1))


def select_motion_cohort(
    train_pairs: Sequence[av2_acquisition.Av2RemoteScenarioPair],
    val_pairs: Sequence[av2_acquisition.Av2RemoteScenarioPair],
    *,
    config: MotionCohortConfig,
) -> tuple[MotionCohortSelectionUnit, ...]:
    """Select the exact listing-order-independent 150/50/300 cohort."""
    if not isinstance(config, MotionCohortConfig):
        raise ValidationError("config must be MotionCohortConfig")
    development_ranked = _ranked(
        train_pairs,
        config=config,
        role=CohortRole.DEVELOPMENT,
        partition=config.train_partition,
    )
    if len(development_ranked) < config.development_count + config.pilot_count:
        raise ValidationError("insufficient train provider candidates")
    development = development_ranked[: config.development_count]
    development_ids = {pair.source_scenario_id for _rank, pair in development}
    pilot_ranked = _ranked(
        train_pairs,
        config=config,
        role=CohortRole.PILOT,
        partition=config.train_partition,
    )
    pilot = tuple(
        (rank, pair)
        for rank, pair in pilot_ranked
        if pair.source_scenario_id not in development_ids
    )[: config.pilot_count]
    test_ranked = _ranked(
        val_pairs,
        config=config,
        role=CohortRole.TEST,
        partition=config.val_partition,
    )
    test = test_ranked[: config.test_count]
    if len(pilot) != config.pilot_count or len(test) != config.test_count:
        raise ValidationError("insufficient disjoint provider candidates")
    units = (
        *(
            MotionCohortSelectionUnit(
                config.train_partition,
                CohortRole.DEVELOPMENT,
                rank,
                pair,
            )
            for rank, pair in development
        ),
        *(
            MotionCohortSelectionUnit(
                config.train_partition,
                CohortRole.PILOT,
                rank,
                pair,
            )
            for rank, pair in pilot
        ),
        *(
            MotionCohortSelectionUnit(
                config.val_partition,
                CohortRole.TEST,
                rank,
                pair,
            )
            for rank, pair in test
        ),
    )
    identifiers = tuple(unit.remote_pair.source_scenario_id for unit in units)
    if len(units) != 500 or len(identifiers) != len(set(identifiers)):
        raise ValidationError("selected cohort must contain 500 unique scenarios")
    return tuple(sorted(units, key=_selection_key))


def cohort_dataset_version(train_catalog: str, val_catalog: str) -> str:
    """Return one version identity over both official provider catalogs."""
    return (
        "official-s3-phase4-"
        + canonical_sha256(
            "phase4-av2-catalogs",
            {
                "train": _sha256(train_catalog, "train_catalog"),
                "val": _sha256(val_catalog, "val_catalog"),
            },
        )[:12]
    )


def build_role_acquisition_plan(
    units: Sequence[MotionCohortSelectionUnit],
    *,
    config: MotionCohortConfig,
    dataset_version: str,
    catalog_identity: str,
    candidate_count: int,
    local_relative_root: Path,
    backend: str,
) -> tuple[
    av2_acquisition.Av2AcquisitionConfig,
    av2_acquisition.Av2AcquisitionPlan,
]:
    """Build an acquisition-native plan for an already frozen role subset."""
    selected = tuple(units)
    if not selected:
        raise ValidationError("units must not be empty")
    role = _role(selected[0].cohort_role)
    partition = selected[0].provider_partition
    if any(
        _role(item.cohort_role) is not role or item.provider_partition != partition
        for item in selected
    ):
        raise ValidationError("one acquisition plan must contain one cohort role")
    namespace = f"phase4-motion-cohort-acquisition-v1:{role.value}:{partition}"
    acquisition_config = av2_acquisition.Av2AcquisitionConfig(
        partition=partition,
        scenario_count=len(selected),
        root_seed=config.root_seed,
        assignment_namespace=namespace,
        local_relative_root=local_relative_root,
        backend=backend,
    )
    pairs = tuple(
        sorted(
            (item.remote_pair for item in selected),
            key=lambda pair: (
                derive_seed(
                    config.root_seed,
                    "av2-provider-acquisition",
                    namespace,
                    pair.source_scenario_id,
                ),
                pair.source_scenario_id,
            ),
        )
    )
    plan = av2_acquisition.Av2AcquisitionPlan(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=_required_text(dataset_version, "dataset_version"),
        remote_root=config.official_source,
        partition=partition,
        root_seed=config.root_seed,
        assignment_namespace=namespace,
        candidate_count=_positive_int(candidate_count, "candidate_count"),
        selected_scenarios=pairs,
        remote_catalog_identity=_sha256(catalog_identity, "remote_catalog_identity"),
    )
    return acquisition_config, plan


def build_role_pilot_plan(
    units: Sequence[MotionCohortSelectionUnit],
    acquisition_report: av2_acquisition.Av2AcquisitionReport,
    *,
    config: MotionCohortConfig,
    dataset_version: str,
    candidate_count: int,
    source_relative_root: Path,
    source_manifest_identity: str,
) -> pilot.Av2PilotPlan:
    """Build an accepted seven-file materialization plan for one cohort role."""
    selected = tuple(sorted(units, key=_selection_key))
    if not selected:
        raise ValidationError("units must not be empty")
    role = _role(selected[0].cohort_role)
    if any(_role(item.cohort_role) is not role for item in selected):
        raise ValidationError("one pilot plan must contain one cohort role")
    by_uri = {item.remote_uri: item for item in acquisition_report.acquired_files}
    pairs: list[pilot.Av2PilotSourcePair] = []
    for item in selected:
        motion = by_uri.get(item.remote_pair.motion.remote_uri)
        vector_map = by_uri.get(item.remote_pair.vector_map.remote_uri)
        if motion is None or vector_map is None:
            raise ArtifactError("acquisition report is missing a selected source pair")
        try:
            motion_relative = motion.relative_path.relative_to(source_relative_root)
            map_relative = vector_map.relative_path.relative_to(source_relative_root)
        except ValueError:
            raise ArtifactError(
                "acquired source path is outside its source root"
            ) from None
        pairs.append(
            pilot.Av2PilotSourcePair(
                source_scenario_id=item.remote_pair.source_scenario_id,
                motion_relative_path=motion_relative,
                map_relative_path=map_relative,
                motion_size_bytes=motion.size_bytes,
                map_size_bytes=vector_map.size_bytes,
                motion_sha256=motion.sha256,
                map_sha256=vector_map.sha256,
            )
        )
    pilot_config = pilot.Av2PilotConfig(
        source_partition=Path(selected[0].provider_partition),
        dataset_version=dataset_version,
        canonical_split_name=role.value,
        scenario_count=len(selected),
        root_seed=config.root_seed,
        assignment_namespace=f"phase4-motion-cohort-v1:{role.value}",
        inclusion_policy="dynamic_only",
        centerline_point_count=50,
        minimum_valid_sample_count=10,
        minimum_valid_duration_ns=1_000_000_000,
        row_group_size=65_536,
        validation_batch_size=65_536,
        materialization_expansion_factor=1.5,
        reserve_fraction=0.15,
    )
    return pilot.Av2PilotPlan(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=dataset_version,
        source_partition=Path(selected[0].provider_partition),
        source_manifest_identity=_sha256(
            source_manifest_identity, "source_manifest_identity"
        ),
        config=pilot_config,
        candidate_count=candidate_count,
        selected_scenarios=tuple(pairs),
    )


@dataclass(frozen=True, slots=True)
class MotionCohortUnit:
    """One acquired, materialized, and validation-annotated cohort unit."""

    provider_partition: str
    cohort_role: CohortRole | str
    selection_rank: int
    source_scenario_id: str
    motion_object_path: Path
    motion_size_bytes: int
    motion_sha256: str
    map_object_path: Path
    map_size_bytes: int
    map_sha256: str
    materialization_cache_key: str
    validation_included: bool
    exclusion_reason: str | None

    def __post_init__(self) -> None:
        """Validate exact source, cache, role, and post-selection status fields."""
        role = _role(self.cohort_role)
        partition = _required_text(self.provider_partition, "provider_partition")
        if partition != ("val" if role is CohortRole.TEST else "train"):
            raise ValidationError("cohort unit role and partition disagree")
        source_id = _required_text(self.source_scenario_id, "source_scenario_id")
        motion = normalize_relative_path(self.motion_object_path)
        vector_map = normalize_relative_path(self.map_object_path)
        if motion.name != f"scenario_{source_id}.parquet":
            raise ValidationError("motion object path differs from source scenario")
        if vector_map.name != f"log_map_archive_{source_id}.json":
            raise ValidationError("map object path differs from source scenario")
        included = self.validation_included
        if not isinstance(included, bool):
            raise ValidationError("validation_included must be Boolean")
        reason = self.exclusion_reason
        if included and reason is not None:
            raise ValidationError("included units must not have an exclusion reason")
        if not included:
            reason = _required_text(reason, "exclusion_reason")
        object.__setattr__(self, "cohort_role", role)
        object.__setattr__(
            self, "selection_rank", _positive_int(self.selection_rank, "selection_rank")
        )
        object.__setattr__(self, "source_scenario_id", source_id)
        object.__setattr__(self, "motion_object_path", motion)
        object.__setattr__(self, "map_object_path", vector_map)
        object.__setattr__(
            self,
            "motion_size_bytes",
            _positive_int(self.motion_size_bytes, "motion_size_bytes"),
        )
        object.__setattr__(
            self, "map_size_bytes", _positive_int(self.map_size_bytes, "map_size_bytes")
        )
        object.__setattr__(
            self, "motion_sha256", _sha256(self.motion_sha256, "motion_sha256")
        )
        object.__setattr__(self, "map_sha256", _sha256(self.map_sha256, "map_sha256"))
        object.__setattr__(
            self,
            "materialization_cache_key",
            _sha256(self.materialization_cache_key, "materialization_cache_key"),
        )
        object.__setattr__(self, "exclusion_reason", reason)


_CONFIG_FIELDS = (
    "schema_version",
    "dataset_id",
    "official_source",
    "root_seed",
    "selection_policy",
    "train_partition",
    "val_partition",
    "development_count",
    "pilot_count",
    "test_count",
)
_UNIT_FIELDS = (
    "provider_partition",
    "cohort_role",
    "selection_rank",
    "source_scenario_id",
    "motion_object_path",
    "motion_size_bytes",
    "motion_sha256",
    "map_object_path",
    "map_size_bytes",
    "map_sha256",
    "materialization_cache_key",
    "validation_included",
    "exclusion_reason",
)
_MANIFEST_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "official_source",
    "root_seed",
    "selection_policy",
    "train_candidate_count",
    "val_candidate_count",
    "train_source_manifest_identity",
    "val_source_manifest_identity",
    "units",
    "cohort_identity",
)


def _config_to_dict(config: MotionCohortConfig) -> dict[str, object]:
    return {name: getattr(config, name) for name in _CONFIG_FIELDS}


def _unit_to_dict(unit: MotionCohortUnit) -> dict[str, object]:
    return {
        "provider_partition": unit.provider_partition,
        "cohort_role": _role(unit.cohort_role).value,
        "selection_rank": unit.selection_rank,
        "source_scenario_id": unit.source_scenario_id,
        "motion_object_path": unit.motion_object_path.as_posix(),
        "motion_size_bytes": unit.motion_size_bytes,
        "motion_sha256": unit.motion_sha256,
        "map_object_path": unit.map_object_path.as_posix(),
        "map_size_bytes": unit.map_size_bytes,
        "map_sha256": unit.map_sha256,
        "materialization_cache_key": unit.materialization_cache_key,
        "validation_included": unit.validation_included,
        "exclusion_reason": unit.exclusion_reason,
    }


@dataclass(frozen=True, slots=True)
class MotionCohortManifest:
    """The exact frozen 500-scenario provider-backed cohort manifest."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    official_source: str
    root_seed: int
    selection_policy: str
    train_candidate_count: int
    val_candidate_count: int
    train_source_manifest_identity: str
    val_source_manifest_identity: str
    units: tuple[MotionCohortUnit, ...]
    cohort_identity: str

    def __post_init__(self) -> None:
        """Enforce role counts, ordering, disjointness, and canonical identity."""
        if self.schema_version != _SCHEMA_VERSION or self.dataset_id != _DATASET_ID:
            raise ValidationError("cohort manifest schema or dataset differs")
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        if self.official_source != av2_acquisition.AV2_OFFICIAL_MOTION_ROOT:
            raise ValidationError("official_source differs from AV2")
        object.__setattr__(self, "root_seed", validate_root_seed(self.root_seed))
        if self.selection_policy != _SELECTION_POLICY:
            raise ValidationError("selection_policy differs from frozen policy")
        for field_name in ("train_candidate_count", "val_candidate_count"):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), field_name),
            )
        for field_name in (
            "train_source_manifest_identity",
            "val_source_manifest_identity",
        ):
            object.__setattr__(
                self, field_name, _sha256(getattr(self, field_name), field_name)
            )
        units = tuple(self.units)
        if len(units) != 500 or any(
            not isinstance(item, MotionCohortUnit) for item in units
        ):
            raise ValidationError("cohort manifest must contain 500 units")
        if units != tuple(
            sorted(
                units,
                key=lambda item: (
                    _ROLE_ORDER[_role(item.cohort_role).value],
                    item.selection_rank,
                    item.source_scenario_id,
                ),
            )
        ):
            raise ValidationError("cohort units are not in canonical role/rank order")
        roles = Counter(_role(item.cohort_role).value for item in units)
        if roles != {"development": 150, "pilot": 50, "test": 300}:
            raise ValidationError("cohort manifest role counts differ")
        identifiers = tuple(item.source_scenario_id for item in units)
        paths = tuple(
            path.as_posix()
            for item in units
            for path in (item.motion_object_path, item.map_object_path)
        )
        if len(identifiers) != len(set(identifiers)) or len(paths) != len(set(paths)):
            raise ValidationError("cohort scenario IDs and source paths must be unique")
        object.__setattr__(self, "units", units)
        identity = _sha256(self.cohort_identity, "cohort_identity")
        object.__setattr__(self, "cohort_identity", identity)
        if identity != motion_cohort_manifest_identity(self):
            raise ValidationError("cohort_identity differs from manifest content")

    @property
    def selected_source_bytes(self) -> int:
        """Return exact selected provider bytes."""
        return sum(item.motion_size_bytes + item.map_size_bytes for item in self.units)


def motion_cohort_manifest_identity(manifest: MotionCohortManifest) -> str:
    """Return the frozen identity over selection and source checksums only."""
    return canonical_sha256(
        _COHORT_DOMAIN,
        {
            "dataset_id": manifest.dataset_id,
            "dataset_version": manifest.dataset_version,
            "official_source": manifest.official_source,
            "root_seed": manifest.root_seed,
            "selection_policy": manifest.selection_policy,
            "units": [
                {
                    "provider_partition": item.provider_partition,
                    "cohort_role": _role(item.cohort_role).value,
                    "selection_rank": item.selection_rank,
                    "source_scenario_id": item.source_scenario_id,
                    "motion_size_bytes": item.motion_size_bytes,
                    "motion_sha256": item.motion_sha256,
                    "map_size_bytes": item.map_size_bytes,
                    "map_sha256": item.map_sha256,
                }
                for item in manifest.units
            ],
        },
    )


def _manifest_to_dict(manifest: MotionCohortManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "official_source": manifest.official_source,
        "root_seed": manifest.root_seed,
        "selection_policy": manifest.selection_policy,
        "train_candidate_count": manifest.train_candidate_count,
        "val_candidate_count": manifest.val_candidate_count,
        "train_source_manifest_identity": manifest.train_source_manifest_identity,
        "val_source_manifest_identity": manifest.val_source_manifest_identity,
        "units": [_unit_to_dict(item) for item in manifest.units],
        "cohort_identity": manifest.cohort_identity,
    }


def motion_cohort_manifest_to_canonical_json(
    manifest: MotionCohortManifest,
) -> str:
    """Serialize one strict cohort manifest to canonical JSON."""
    if not isinstance(manifest, MotionCohortManifest):
        raise ValidationError("manifest must be MotionCohortManifest")
    return canonical_json_text(_manifest_to_dict(manifest), trailing_newline=True)


def motion_cohort_manifest_from_json(text: str) -> MotionCohortManifest:
    """Strictly deserialize one canonical cohort manifest."""
    mapping = _exact_mapping(
        _json_mapping(text, "motion cohort manifest"),
        _MANIFEST_FIELDS,
        "motion cohort manifest",
    )
    units = tuple(
        MotionCohortUnit(
            **cast(
                dict[str, Any],
                _exact_mapping(item, _UNIT_FIELDS, "motion cohort unit"),
            )
        )
        for item in _sequence(mapping["units"], "units")
    )
    manifest = MotionCohortManifest(
        schema_version=cast(str, mapping["schema_version"]),
        dataset_id=cast(str, mapping["dataset_id"]),
        dataset_version=cast(str, mapping["dataset_version"]),
        official_source=cast(str, mapping["official_source"]),
        root_seed=cast(int, mapping["root_seed"]),
        selection_policy=cast(str, mapping["selection_policy"]),
        train_candidate_count=cast(int, mapping["train_candidate_count"]),
        val_candidate_count=cast(int, mapping["val_candidate_count"]),
        train_source_manifest_identity=cast(
            str, mapping["train_source_manifest_identity"]
        ),
        val_source_manifest_identity=cast(str, mapping["val_source_manifest_identity"]),
        units=units,
        cohort_identity=cast(str, mapping["cohort_identity"]),
    )
    if text != motion_cohort_manifest_to_canonical_json(manifest):
        raise SchemaError("motion cohort manifest JSON is not canonical")
    return manifest


def build_cohort_manifest(
    units: Sequence[MotionCohortUnit],
    *,
    config: MotionCohortConfig,
    dataset_version: str,
    train_candidate_count: int,
    val_candidate_count: int,
    train_source_manifest_identity: str,
    val_source_manifest_identity: str,
) -> MotionCohortManifest:
    """Build the final identity-bearing manifest after validation."""
    values = dict(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=dataset_version,
        official_source=config.official_source,
        root_seed=config.root_seed,
        selection_policy=config.selection_policy,
        train_candidate_count=train_candidate_count,
        val_candidate_count=val_candidate_count,
        train_source_manifest_identity=train_source_manifest_identity,
        val_source_manifest_identity=val_source_manifest_identity,
        units=tuple(units),
    )
    temporary = object.__new__(MotionCohortManifest)
    for key, value in values.items():
        object.__setattr__(temporary, key, value)
    object.__setattr__(temporary, "cohort_identity", "0" * 64)
    return MotionCohortManifest(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=dataset_version,
        official_source=config.official_source,
        root_seed=config.root_seed,
        selection_policy=config.selection_policy,
        train_candidate_count=train_candidate_count,
        val_candidate_count=val_candidate_count,
        train_source_manifest_identity=train_source_manifest_identity,
        val_source_manifest_identity=val_source_manifest_identity,
        units=tuple(units),
        cohort_identity=motion_cohort_manifest_identity(temporary),
    )


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    """Deterministic five-number summary for one finite sample."""

    count: int
    minimum: float
    p25: float
    median: float
    p75: float
    maximum: float

    def __post_init__(self) -> None:
        """Validate finite ordered quantiles."""
        object.__setattr__(self, "count", _positive_int(self.count, "count"))
        values = tuple(
            _finite_nonnegative(getattr(self, name), name)
            for name in ("minimum", "p25", "median", "p75", "maximum")
        )
        if tuple(sorted(values)) != values:
            raise ValidationError("distribution quantiles must be ordered")
        for name, value in zip(
            ("minimum", "p25", "median", "p75", "maximum"), values, strict=True
        ):
            object.__setattr__(self, name, value)


def _distribution(values: Sequence[int | float]) -> DistributionSummary:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValidationError("distribution input must not be empty")

    def quantile(probability: float) -> float:
        position = probability * (len(ordered) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return DistributionSummary(
        len(ordered),
        ordered[0],
        quantile(0.25),
        quantile(0.5),
        quantile(0.75),
        ordered[-1],
    )


@dataclass(frozen=True, slots=True)
class CohortValidationSummary:
    """Bounded canonical counts and distributions for one role or overall."""

    cohort_role: str
    source_scenarios: int
    included_scenarios: int
    source_agents: int
    included_agents: int
    source_trajectories: int
    included_trajectories: int
    source_samples: int
    included_samples: int
    vector_map_elements: int
    included_vector_map_elements: int
    valid_run_count: int
    included_valid_run_count: int
    exclusion_reason_counts: tuple[tuple[str, int], ...]
    source_agent_class_counts: tuple[tuple[str, int], ...]
    included_agent_class_counts: tuple[tuple[str, int], ...]
    source_city_counts: tuple[tuple[str, int], ...]
    included_city_counts: tuple[tuple[str, int], ...]
    trajectory_sample_count_distribution: DistributionSummary
    trajectory_duration_seconds_distribution: DistributionSummary

    def __post_init__(self) -> None:
        """Validate counts, pair summaries, and distributions."""
        if self.cohort_role not in {*_ROLE_ORDER, "overall"}:
            raise ValidationError("cohort_role must be a frozen role or overall")
        for name in (
            "source_scenarios",
            "included_scenarios",
            "source_agents",
            "included_agents",
            "source_trajectories",
            "included_trajectories",
            "source_samples",
            "included_samples",
            "vector_map_elements",
            "included_vector_map_elements",
            "valid_run_count",
            "included_valid_run_count",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))
        for included, source in (
            (self.included_scenarios, self.source_scenarios),
            (self.included_agents, self.source_agents),
            (self.included_trajectories, self.source_trajectories),
            (self.included_samples, self.source_samples),
            (self.included_vector_map_elements, self.vector_map_elements),
            (self.included_valid_run_count, self.valid_run_count),
        ):
            if included > source:
                raise ValidationError("included validation count exceeds source count")
        for name in (
            "exclusion_reason_counts",
            "source_agent_class_counts",
            "included_agent_class_counts",
            "source_city_counts",
            "included_city_counts",
        ):
            object.__setattr__(self, name, _count_pairs(getattr(self, name), name))
        if not isinstance(
            self.trajectory_sample_count_distribution, DistributionSummary
        ) or not isinstance(
            self.trajectory_duration_seconds_distribution, DistributionSummary
        ):
            raise ValidationError("trajectory distributions must be summaries")


@dataclass(slots=True)
class _Scan:
    source_scenarios: int
    included_scenarios: int
    source_agents: int
    included_agents: int
    source_trajectories: int
    included_trajectories: int
    source_samples: int
    included_samples: int
    map_elements: int
    included_map_elements: int
    valid_runs: int
    included_valid_runs: int
    exclusion_counts: Counter[str]
    source_classes: Counter[str]
    included_classes: Counter[str]
    source_cities: Counter[str]
    included_cities: Counter[str]
    sample_counts: list[int]
    durations: list[float]


def _empty_scan() -> _Scan:
    return _Scan(
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        Counter(),
        Counter(),
        Counter(),
        Counter(),
        Counter(),
        [],
        [],
    )


def _scan_execution(
    repository_root: Path,
    execution: pilot.Av2PilotExecution,
) -> _Scan:
    scan = _empty_scan()
    report = execution.validation_report
    included_scenarios = set(report.included_scenario_ids)
    included_agents = set(report.included_agent_ids)
    included_trajectories = set(report.included_trajectory_ids)
    for reason, count in report.exclusion_reason_counts:
        scan.exclusion_counts[cast(str, reason)] += cast(int, count)
    for result in execution.materialization_report.results:
        root = repository_root / result.entry_relative_directory
        scenario_file = pq.ParquetFile(root / "scenario_manifest.parquet")
        for batch in scenario_file.iter_batches(
            batch_size=1,
            columns=["scenario_id", "city_or_region"],
        ):
            scenario_id = cast(str, batch.column(0)[0].as_py())
            city = cast(str | None, batch.column(1)[0].as_py()) or "unknown"
            scan.source_scenarios += 1
            scan.source_cities[city] += 1
            if scenario_id in included_scenarios:
                scan.included_scenarios += 1
                scan.included_cities[city] += 1
        agent_file = pq.ParquetFile(root / "agent_metadata.parquet")
        for batch in agent_file.iter_batches(
            batch_size=65_536,
            columns=[
                "agent_id",
                "agent_class",
                "sample_count",
                "first_time_ns",
                "last_time_ns",
            ],
        ):
            for agent_id, agent_class, samples, first, last in zip(
                *(column.to_pylist() for column in batch.columns),
                strict=True,
            ):
                scan.source_agents += 1
                scan.source_classes[cast(str, agent_class)] += 1
                scan.sample_counts.append(cast(int, samples))
                scan.durations.append((cast(int, last) - cast(int, first)) / 1e9)
                if cast(str, agent_id) in included_agents:
                    scan.included_agents += 1
                    scan.included_classes[cast(str, agent_class)] += 1
        trajectory_file = pq.ParquetFile(root / "trajectory_samples.parquet")
        seen_trajectories: set[str] = set()
        previous_valid: dict[str, bool] = {}
        for batch in trajectory_file.iter_batches(
            batch_size=65_536,
            columns=["trajectory_id", "is_valid"],
        ):
            for trajectory_id, valid in zip(
                batch.column(0).to_pylist(),
                batch.column(1).to_pylist(),
                strict=True,
            ):
                identifier = cast(str, trajectory_id)
                is_valid = cast(bool, valid)
                seen_trajectories.add(identifier)
                scan.source_samples += 1
                included = identifier in included_trajectories
                if included:
                    scan.included_samples += 1
                if is_valid and not previous_valid.get(identifier, False):
                    scan.valid_runs += 1
                    if included:
                        scan.included_valid_runs += 1
                previous_valid[identifier] = is_valid
        scan.source_trajectories += len(seen_trajectories)
        scan.included_trajectories += len(
            seen_trajectories.intersection(included_trajectories)
        )
        map_file = pq.ParquetFile(root / "vector_map_elements.parquet")
        for batch in map_file.iter_batches(
            batch_size=65_536,
            columns=["map_element_id"],
        ):
            identifiers = cast(list[str], batch.column(0).to_pylist())
            scan.map_elements += len(identifiers)
            scan.included_map_elements += sum(
                item in set(report.included_map_element_ids) for item in identifiers
            )
    if scan.source_samples != report.source_sample_count:
        raise ArtifactError("bounded cohort scan differs from validation sample count")
    return scan


def _combine(scans: Sequence[_Scan]) -> _Scan:
    result = _empty_scan()
    for scan in scans:
        for name in (
            "source_scenarios",
            "included_scenarios",
            "source_agents",
            "included_agents",
            "source_trajectories",
            "included_trajectories",
            "source_samples",
            "included_samples",
            "map_elements",
            "included_map_elements",
            "valid_runs",
            "included_valid_runs",
        ):
            setattr(result, name, getattr(result, name) + getattr(scan, name))
        for name in (
            "exclusion_counts",
            "source_classes",
            "included_classes",
            "source_cities",
            "included_cities",
        ):
            getattr(result, name).update(getattr(scan, name))
        result.sample_counts.extend(scan.sample_counts)
        result.durations.extend(scan.durations)
    return result


def _pairs(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counter.items()))


def _summary(role: str, scan: _Scan) -> CohortValidationSummary:
    return CohortValidationSummary(
        role,
        scan.source_scenarios,
        scan.included_scenarios,
        scan.source_agents,
        scan.included_agents,
        scan.source_trajectories,
        scan.included_trajectories,
        scan.source_samples,
        scan.included_samples,
        scan.map_elements,
        scan.included_map_elements,
        scan.valid_runs,
        scan.included_valid_runs,
        _pairs(scan.exclusion_counts),
        _pairs(scan.source_classes),
        _pairs(scan.included_classes),
        _pairs(scan.source_cities),
        _pairs(scan.included_cities),
        _distribution(scan.sample_counts),
        _distribution(scan.durations),
    )


def cohort_validation_summaries(
    repository_root: Path,
    executions: Mapping[CohortRole, pilot.Av2PilotExecution],
) -> tuple[CohortValidationSummary, ...]:
    """Return bounded development, pilot, test, and overall summaries."""
    scans = {
        role: _scan_execution(repository_root, executions[role]) for role in CohortRole
    }
    return (
        *(_summary(role.value, scans[role]) for role in CohortRole),
        _summary("overall", _combine(tuple(scans.values()))),
    )


def validation_status_by_source(
    repository_root: Path,
    executions: Mapping[CohortRole, pilot.Av2PilotExecution],
) -> dict[str, tuple[bool, str | None]]:
    """Map provider scenario IDs to post-selection validation status."""
    statuses: dict[str, tuple[bool, str | None]] = {}
    for execution in executions.values():
        report = execution.validation_report
        included = set(report.included_scenario_ids)
        scenario_reasons = {
            item.unit_id: cast(validation.ExclusionReason, item.reason).value
            for item in report.exclusions
            if item.unit_type is validation.ValidationUnitType.SCENARIO
        }
        for result in execution.materialization_report.results:
            path = repository_root / result.entry_relative_directory
            table = pq.read_table(
                path / "scenario_manifest.parquet",
                columns=["scenario_id", "source_scenario_id"],
            )
            if table.num_rows != 1:
                raise ArtifactError("cohort cache scenario table must contain one row")
            canonical_id = cast(str, table.column(0)[0].as_py())
            source_id = cast(str, table.column(1)[0].as_py())
            is_included = canonical_id in included
            reason = None if is_included else scenario_reasons.get(canonical_id)
            if not is_included and reason is None:
                reason = "other_documented_reason"
            statuses[source_id] = (is_included, reason)
    if len(statuses) != 500:
        raise ArtifactError("validation status does not cover 500 source scenarios")
    return statuses


_DISTRIBUTION_FIELDS = ("count", "minimum", "p25", "median", "p75", "maximum")
_VALIDATION_FIELDS = (
    "cohort_role",
    "source_scenarios",
    "included_scenarios",
    "source_agents",
    "included_agents",
    "source_trajectories",
    "included_trajectories",
    "source_samples",
    "included_samples",
    "vector_map_elements",
    "included_vector_map_elements",
    "valid_run_count",
    "included_valid_run_count",
    "exclusion_reason_counts",
    "source_agent_class_counts",
    "included_agent_class_counts",
    "source_city_counts",
    "included_city_counts",
    "trajectory_sample_count_distribution",
    "trajectory_duration_seconds_distribution",
)


def _distribution_to_dict(value: DistributionSummary) -> dict[str, object]:
    return {name: getattr(value, name) for name in _DISTRIBUTION_FIELDS}


def _validation_to_dict(value: CohortValidationSummary) -> dict[str, object]:
    return {
        **{
            name: getattr(value, name)
            for name in _VALIDATION_FIELDS
            if name
            not in {
                "trajectory_sample_count_distribution",
                "trajectory_duration_seconds_distribution",
            }
        },
        "trajectory_sample_count_distribution": _distribution_to_dict(
            value.trajectory_sample_count_distribution
        ),
        "trajectory_duration_seconds_distribution": _distribution_to_dict(
            value.trajectory_duration_seconds_distribution
        ),
    }


def cohort_validation_summary_to_canonical_json(
    value: CohortValidationSummary,
) -> str:
    """Serialize a validation summary canonically."""
    return canonical_json_text(_validation_to_dict(value), trailing_newline=True)


def cohort_validation_summary_from_json(text: str) -> CohortValidationSummary:
    """Strictly deserialize a validation summary."""
    mapping = _exact_mapping(
        _json_mapping(text, "cohort validation summary"),
        _VALIDATION_FIELDS,
        "cohort validation summary",
    )

    def distribution(name: str) -> DistributionSummary:
        value = _exact_mapping(
            mapping[name], _DISTRIBUTION_FIELDS, f"{name} distribution"
        )
        return DistributionSummary(**cast(dict[str, Any], value))

    summary = CohortValidationSummary(
        cohort_role=cast(str, mapping["cohort_role"]),
        source_scenarios=cast(int, mapping["source_scenarios"]),
        included_scenarios=cast(int, mapping["included_scenarios"]),
        source_agents=cast(int, mapping["source_agents"]),
        included_agents=cast(int, mapping["included_agents"]),
        source_trajectories=cast(int, mapping["source_trajectories"]),
        included_trajectories=cast(int, mapping["included_trajectories"]),
        source_samples=cast(int, mapping["source_samples"]),
        included_samples=cast(int, mapping["included_samples"]),
        vector_map_elements=cast(int, mapping["vector_map_elements"]),
        included_vector_map_elements=cast(int, mapping["included_vector_map_elements"]),
        valid_run_count=cast(int, mapping["valid_run_count"]),
        included_valid_run_count=cast(int, mapping["included_valid_run_count"]),
        exclusion_reason_counts=_count_pairs(
            mapping["exclusion_reason_counts"], "exclusion_reason_counts"
        ),
        source_agent_class_counts=_count_pairs(
            mapping["source_agent_class_counts"], "source_agent_class_counts"
        ),
        included_agent_class_counts=_count_pairs(
            mapping["included_agent_class_counts"],
            "included_agent_class_counts",
        ),
        source_city_counts=_count_pairs(
            mapping["source_city_counts"], "source_city_counts"
        ),
        included_city_counts=_count_pairs(
            mapping["included_city_counts"], "included_city_counts"
        ),
        trajectory_sample_count_distribution=distribution(
            "trajectory_sample_count_distribution"
        ),
        trajectory_duration_seconds_distribution=distribution(
            "trajectory_duration_seconds_distribution"
        ),
    )
    if text != cohort_validation_summary_to_canonical_json(summary):
        raise SchemaError("cohort validation summary JSON is not canonical")
    return summary


@dataclass(frozen=True, slots=True)
class CohortRunMeasurements:
    """Measured acquisition, conversion, validation, disk, and throughput data."""

    run_kind: str
    scenario_count: int
    trajectory_count: int
    materialized_count: int
    reused_count: int
    acquisition_verification_seconds: float
    conversion_seconds: float
    validation_seconds: float
    total_seconds: float
    peak_process_memory_bytes: int
    source_bytes: int
    cache_bytes: int
    disk_free_before_bytes: int
    disk_free_after_bytes: int
    worker_count: int
    cpu_only: bool
    gpu_use_count: int

    def __post_init__(self) -> None:
        """Validate measured counts, durations, resources, and run posture."""
        object.__setattr__(self, "run_kind", _required_text(self.run_kind, "run_kind"))
        for name in (
            "scenario_count",
            "trajectory_count",
            "materialized_count",
            "reused_count",
            "peak_process_memory_bytes",
            "source_bytes",
            "cache_bytes",
            "disk_free_before_bytes",
            "disk_free_after_bytes",
            "worker_count",
            "gpu_use_count",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))
        if self.materialized_count + self.reused_count != self.scenario_count:
            raise ValidationError("materialized and reused counts must cover scenarios")
        for name in (
            "acquisition_verification_seconds",
            "conversion_seconds",
            "validation_seconds",
            "total_seconds",
        ):
            object.__setattr__(
                self, name, _finite_nonnegative(getattr(self, name), name)
            )
        if not isinstance(self.cpu_only, bool):
            raise ValidationError("cpu_only must be Boolean")
        if self.worker_count != 1 or not self.cpu_only or self.gpu_use_count != 0:
            raise ValidationError("cohort execution must be single-worker CPU-only")

    @property
    def scenarios_per_second(self) -> float:
        return self.scenario_count / self.total_seconds

    @property
    def trajectories_per_second(self) -> float:
        return self.trajectory_count / self.total_seconds


_RUN_FIELDS = (
    "run_kind",
    "scenario_count",
    "trajectory_count",
    "materialized_count",
    "reused_count",
    "acquisition_verification_seconds",
    "conversion_seconds",
    "validation_seconds",
    "total_seconds",
    "peak_process_memory_bytes",
    "source_bytes",
    "cache_bytes",
    "disk_free_before_bytes",
    "disk_free_after_bytes",
    "worker_count",
    "cpu_only",
    "gpu_use_count",
)


def cohort_run_measurements_to_canonical_json(
    value: CohortRunMeasurements,
) -> str:
    """Serialize run measurements canonically."""
    return canonical_json_text(
        {name: getattr(value, name) for name in _RUN_FIELDS},
        trailing_newline=True,
    )


def cohort_run_measurements_from_json(text: str) -> CohortRunMeasurements:
    """Strictly deserialize run measurements."""
    mapping = _exact_mapping(
        _json_mapping(text, "cohort run measurements"),
        _RUN_FIELDS,
        "cohort run measurements",
    )
    result = CohortRunMeasurements(**cast(dict[str, Any], mapping))
    if text != cohort_run_measurements_to_canonical_json(result):
        raise SchemaError("cohort run measurements JSON is not canonical")
    return result


@dataclass(frozen=True, slots=True)
class ResourcePilotResult:
    """Measured 25-scenario probe with full-cohort projection comparison."""

    selected_source_scenario_ids: tuple[str, ...]
    development_count: int
    pilot_count: int
    test_count: int
    measurements: CohortRunMeasurements
    projected_full_duration_seconds: float
    actual_full_duration_seconds: float
    projected_full_cache_bytes: int
    actual_full_cache_bytes: int

    def __post_init__(self) -> None:
        """Validate exact probe composition and measured projections."""
        identifiers = tuple(
            _required_text(item, "selected_source_scenario_ids item")
            for item in self.selected_source_scenario_ids
        )
        if len(identifiers) != 25 or len(identifiers) != len(set(identifiers)):
            raise ValidationError("resource pilot must contain 25 unique scenarios")
        object.__setattr__(self, "selected_source_scenario_ids", identifiers)
        if (self.development_count, self.pilot_count, self.test_count) != (10, 5, 10):
            raise ValidationError("resource pilot role counts must equal 10, 5, 10")
        if not isinstance(self.measurements, CohortRunMeasurements):
            raise ValidationError("measurements must be CohortRunMeasurements")
        if self.measurements.scenario_count != 25:
            raise ValidationError("resource pilot measurements must cover 25 scenarios")
        for name in ("projected_full_duration_seconds", "actual_full_duration_seconds"):
            object.__setattr__(
                self, name, _finite_nonnegative(getattr(self, name), name)
            )
        for name in ("projected_full_cache_bytes", "actual_full_cache_bytes"):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))


_RESOURCE_FIELDS = (
    "selected_source_scenario_ids",
    "development_count",
    "pilot_count",
    "test_count",
    "measurements",
    "projected_full_duration_seconds",
    "actual_full_duration_seconds",
    "projected_full_cache_bytes",
    "actual_full_cache_bytes",
)


def resource_pilot_result_to_canonical_json(value: ResourcePilotResult) -> str:
    """Serialize the resource pilot result canonically."""
    payload = {
        "selected_source_scenario_ids": list(value.selected_source_scenario_ids),
        "development_count": value.development_count,
        "pilot_count": value.pilot_count,
        "test_count": value.test_count,
        "measurements": {
            name: getattr(value.measurements, name) for name in _RUN_FIELDS
        },
        "projected_full_duration_seconds": value.projected_full_duration_seconds,
        "actual_full_duration_seconds": value.actual_full_duration_seconds,
        "projected_full_cache_bytes": value.projected_full_cache_bytes,
        "actual_full_cache_bytes": value.actual_full_cache_bytes,
    }
    return canonical_json_text(payload, trailing_newline=True)


def resource_pilot_result_from_json(text: str) -> ResourcePilotResult:
    """Strictly deserialize the resource pilot result."""
    mapping = _exact_mapping(
        _json_mapping(text, "resource pilot result"),
        _RESOURCE_FIELDS,
        "resource pilot result",
    )
    measurements = CohortRunMeasurements(
        **cast(
            dict[str, Any],
            _exact_mapping(mapping["measurements"], _RUN_FIELDS, "measurements"),
        )
    )
    result = ResourcePilotResult(
        selected_source_scenario_ids=tuple(
            cast(str, item)
            for item in _sequence(
                mapping["selected_source_scenario_ids"],
                "selected_source_scenario_ids",
            )
        ),
        development_count=cast(int, mapping["development_count"]),
        pilot_count=cast(int, mapping["pilot_count"]),
        test_count=cast(int, mapping["test_count"]),
        measurements=measurements,
        projected_full_duration_seconds=cast(
            float, mapping["projected_full_duration_seconds"]
        ),
        actual_full_duration_seconds=cast(
            float, mapping["actual_full_duration_seconds"]
        ),
        projected_full_cache_bytes=cast(int, mapping["projected_full_cache_bytes"]),
        actual_full_cache_bytes=cast(int, mapping["actual_full_cache_bytes"]),
    )
    if text != resource_pilot_result_to_canonical_json(result):
        raise SchemaError("resource pilot result JSON is not canonical")
    return result
