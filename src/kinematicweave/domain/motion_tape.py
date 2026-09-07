"""Immutable dataset-level procedural-motion package records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import cast

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import ValidationError
from kinematicweave.paths import relative_path_text

__all__ = [
    "CanonicalArtifactPart",
    "CanonicalTableArtifactReference",
    "ProceduralMotionCounts",
    "ProceduralMotionPackage",
    "SourceInputIdentity",
    "create_procedural_motion_package",
    "procedural_motion_package_from_dict",
    "procedural_motion_package_from_json",
    "procedural_motion_package_identity",
    "procedural_motion_package_to_dict",
    "procedural_motion_package_to_json",
]

_SCHEMA_VERSION = "1.0"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COUNT_FIELDS = (
    "scenarios",
    "procedural_tracks",
    "procedural_segments",
    "semantic_waypoints",
    "motion_events",
    "motion_categories",
    "route_templates",
    "route_template_memberships",
)
_PART_FIELDS = (
    "repository_relative_path",
    "size_bytes",
    "sha256",
    "canonical_schema_name",
    "schema_fingerprint",
    "row_count",
)
_TABLE_FIELDS = (
    "canonical_schema_name",
    "schema_fingerprint",
    "row_count",
    "artifacts",
)
_SOURCE_IDENTITY_FIELDS = ("name", "identity")
_PACKAGE_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "source_validation_report_identity",
    "numerical_codec_name",
    "numerical_codec_version",
    "numerical_codec_parameters_identity",
    "semantic_detector_identity",
    "shared_motion_configuration_identity",
    "scenario_ids",
    "tape_ids",
    "counts",
    "canonical_table_artifacts",
    "canonical_schema_fingerprints",
    "source_input_identities",
    "package_identity",
)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _sha256(value: object, field_name: str) -> str:
    normalized = _text(value, field_name)
    if _SHA256.fullmatch(normalized) is None:
        raise ValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _nonnegative(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{field_name} must be a nonnegative integer")
    return value


def _strict_mapping(
    value: object,
    fields: tuple[str, ...],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValidationError(f"{label} must be a string-keyed object")
    missing = tuple(field for field in fields if field not in value)
    unknown = tuple(field for field in value if field not in fields)
    if missing:
        raise ValidationError(f"{label} is missing field {missing[0]!r}")
    if unknown:
        raise ValidationError(f"{label} has unknown field {unknown[0]!r}")
    return value


def _ordered_unique_text(
    value: object,
    field_name: str,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a non-string sequence")
    normalized = tuple(_text(item, f"{field_name} item") for item in value)
    if normalized != tuple(sorted(normalized)) or len(normalized) != len(
        set(normalized)
    ):
        raise ValidationError(f"{field_name} must be ordered and unique")
    return normalized


@dataclass(frozen=True, slots=True)
class ProceduralMotionCounts:
    """Aggregate row counts represented by a procedural-motion package."""

    scenarios: int
    procedural_tracks: int
    procedural_segments: int
    semantic_waypoints: int
    motion_events: int
    motion_categories: int
    route_templates: int
    route_template_memberships: int

    def __post_init__(self) -> None:
        for field_name in _COUNT_FIELDS:
            object.__setattr__(
                self,
                field_name,
                _nonnegative(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, int]:
        """Return fields in their frozen serialization order."""
        return {field_name: getattr(self, field_name) for field_name in _COUNT_FIELDS}

    @classmethod
    def from_dict(cls, value: object) -> "ProceduralMotionCounts":
        """Strictly reconstruct package counts."""
        mapping = _strict_mapping(value, _COUNT_FIELDS, "counts")
        return cls(**mapping)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CanonicalArtifactPart:
    """Physical identity of one canonical Parquet part."""

    repository_relative_path: str
    size_bytes: int
    sha256: str
    canonical_schema_name: str
    schema_fingerprint: str
    row_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "repository_relative_path",
            relative_path_text(self.repository_relative_path),
        )
        object.__setattr__(
            self, "size_bytes", _nonnegative(self.size_bytes, "size_bytes")
        )
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))
        object.__setattr__(
            self,
            "canonical_schema_name",
            _text(self.canonical_schema_name, "canonical_schema_name"),
        )
        object.__setattr__(
            self,
            "schema_fingerprint",
            _sha256(self.schema_fingerprint, "schema_fingerprint"),
        )
        object.__setattr__(self, "row_count", _nonnegative(self.row_count, "row_count"))

    def to_dict(self) -> dict[str, object]:
        """Return fields in their frozen serialization order."""
        return {
            "repository_relative_path": self.repository_relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "canonical_schema_name": self.canonical_schema_name,
            "schema_fingerprint": self.schema_fingerprint,
            "row_count": self.row_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CanonicalArtifactPart":
        """Strictly reconstruct one physical artifact reference."""
        mapping = _strict_mapping(value, _PART_FIELDS, "artifact reference")
        return cls(**mapping)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CanonicalTableArtifactReference:
    """One logical canonical table backed by ordered immutable Parquet parts."""

    canonical_schema_name: str
    schema_fingerprint: str
    row_count: int
    artifacts: Sequence[CanonicalArtifactPart]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "canonical_schema_name",
            _text(self.canonical_schema_name, "canonical_schema_name"),
        )
        object.__setattr__(
            self,
            "schema_fingerprint",
            _sha256(self.schema_fingerprint, "schema_fingerprint"),
        )
        object.__setattr__(self, "row_count", _nonnegative(self.row_count, "row_count"))
        value: object = self.artifacts
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("artifacts must be a non-string sequence")
        artifacts = tuple(value)
        if not artifacts or any(
            not isinstance(item, CanonicalArtifactPart) for item in artifacts
        ):
            raise ValidationError("artifacts must contain canonical artifact parts")
        if len({item.repository_relative_path for item in artifacts}) != len(artifacts):
            raise ValidationError("artifact paths must be unique within a table")
        if any(
            item.canonical_schema_name != self.canonical_schema_name
            or item.schema_fingerprint != self.schema_fingerprint
            for item in artifacts
        ):
            raise ValidationError("artifact part schema identity differs")
        if sum(item.row_count for item in artifacts) != self.row_count:
            raise ValidationError("table row_count differs from artifact parts")
        object.__setattr__(self, "artifacts", artifacts)

    def to_dict(self) -> dict[str, object]:
        """Return fields in their frozen serialization order."""
        return {
            "canonical_schema_name": self.canonical_schema_name,
            "schema_fingerprint": self.schema_fingerprint,
            "row_count": self.row_count,
            "artifacts": [item.to_dict() for item in self.artifacts],
        }

    @classmethod
    def from_dict(cls, value: object) -> "CanonicalTableArtifactReference":
        """Strictly reconstruct one logical table reference."""
        mapping = _strict_mapping(value, _TABLE_FIELDS, "table reference")
        artifacts = mapping["artifacts"]
        if not isinstance(artifacts, list):
            raise ValidationError("table artifacts must be a list")
        return cls(
            canonical_schema_name=mapping["canonical_schema_name"],  # type: ignore[arg-type]
            schema_fingerprint=mapping["schema_fingerprint"],  # type: ignore[arg-type]
            row_count=mapping["row_count"],  # type: ignore[arg-type]
            artifacts=tuple(
                CanonicalArtifactPart.from_dict(item) for item in artifacts
            ),
        )


@dataclass(frozen=True, slots=True)
class SourceInputIdentity:
    """Named immutable identity for one package source input."""

    name: str
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "identity", _text(self.identity, "identity"))

    def to_dict(self) -> dict[str, str]:
        """Return fields in their frozen serialization order."""
        return {"name": self.name, "identity": self.identity}

    @classmethod
    def from_dict(cls, value: object) -> "SourceInputIdentity":
        """Strictly reconstruct a source identity."""
        mapping = _strict_mapping(
            value, _SOURCE_IDENTITY_FIELDS, "source input identity"
        )
        return cls(name=mapping["name"], identity=mapping["identity"])  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ProceduralMotionPackage:
    """Frozen dataset-level package over the thirteen canonical motion tables."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    source_validation_report_identity: str
    numerical_codec_name: str
    numerical_codec_version: str
    numerical_codec_parameters_identity: str
    semantic_detector_identity: str
    shared_motion_configuration_identity: str
    scenario_ids: Sequence[str]
    tape_ids: Sequence[str]
    counts: ProceduralMotionCounts
    canonical_table_artifacts: Sequence[CanonicalTableArtifactReference]
    canonical_schema_fingerprints: Sequence[SourceInputIdentity]
    source_input_identities: Sequence[SourceInputIdentity]
    package_identity: str

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        for field_name in (
            "dataset_id",
            "dataset_version",
            "source_validation_report_identity",
            "numerical_codec_name",
            "numerical_codec_version",
            "numerical_codec_parameters_identity",
            "semantic_detector_identity",
            "shared_motion_configuration_identity",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "scenario_ids",
            _ordered_unique_text(self.scenario_ids, "scenario_ids"),
        )
        object.__setattr__(
            self, "tape_ids", _ordered_unique_text(self.tape_ids, "tape_ids")
        )
        if not isinstance(self.counts, ProceduralMotionCounts):
            raise ValidationError("counts must be ProceduralMotionCounts")
        if self.counts.scenarios != len(self.scenario_ids):
            raise ValidationError("scenario count differs from scenario_ids")
        if len(self.tape_ids) != len(self.scenario_ids):
            raise ValidationError("each scenario must have exactly one tape ID")
        for field_name, expected_type in (
            ("canonical_table_artifacts", CanonicalTableArtifactReference),
            ("canonical_schema_fingerprints", SourceInputIdentity),
            ("source_input_identities", SourceInputIdentity),
        ):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValidationError(f"{field_name} must be a non-string sequence")
            copied = tuple(value)
            if not copied or any(
                not isinstance(item, expected_type) for item in copied
            ):
                raise ValidationError(f"{field_name} contains an invalid record")
            object.__setattr__(self, field_name, copied)
        schema_names = tuple(
            item.canonical_schema_name for item in self.canonical_table_artifacts
        )
        if len(schema_names) != len(set(schema_names)):
            raise ValidationError("each canonical table must be represented once")
        paths = tuple(
            part.repository_relative_path
            for table in self.canonical_table_artifacts
            for part in table.artifacts
        )
        if len(paths) != len(set(paths)):
            raise ValidationError("package artifact paths must be globally unique")
        for field_name in (
            "canonical_schema_fingerprints",
            "source_input_identities",
        ):
            items = getattr(self, field_name)
            names = tuple(item.name for item in items)
            if names != tuple(sorted(names)) or len(names) != len(set(names)):
                raise ValidationError(f"{field_name} must be ordered by unique name")
        object.__setattr__(
            self,
            "package_identity",
            _sha256(self.package_identity, "package_identity"),
        )
        if self.package_identity != procedural_motion_package_identity(self):
            raise ValidationError("package_identity differs from canonical content")


def _identity_value(package: ProceduralMotionPackage) -> dict[str, object]:
    value = procedural_motion_package_to_dict(package)
    del value["package_identity"]
    return value


def create_procedural_motion_package(
    *,
    dataset_id: str,
    dataset_version: str,
    source_validation_report_identity: str,
    numerical_codec_name: str,
    numerical_codec_version: str,
    numerical_codec_parameters_identity: str,
    semantic_detector_identity: str,
    shared_motion_configuration_identity: str,
    scenario_ids: Sequence[str],
    tape_ids: Sequence[str],
    counts: ProceduralMotionCounts,
    canonical_table_artifacts: Sequence[CanonicalTableArtifactReference],
    canonical_schema_fingerprints: Sequence[SourceInputIdentity],
    source_input_identities: Sequence[SourceInputIdentity],
) -> ProceduralMotionPackage:
    """Create a package with its canonical identity populated."""
    values: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source_validation_report_identity": source_validation_report_identity,
        "numerical_codec_name": numerical_codec_name,
        "numerical_codec_version": numerical_codec_version,
        "numerical_codec_parameters_identity": numerical_codec_parameters_identity,
        "semantic_detector_identity": semantic_detector_identity,
        "shared_motion_configuration_identity": (shared_motion_configuration_identity),
        "scenario_ids": list(scenario_ids),
        "tape_ids": list(tape_ids),
        "counts": counts.to_dict(),
        "canonical_table_artifacts": [
            item.to_dict() for item in canonical_table_artifacts
        ],
        "canonical_schema_fingerprints": [
            item.to_dict() for item in canonical_schema_fingerprints
        ],
        "source_input_identities": [item.to_dict() for item in source_input_identities],
    }
    identity = canonical_sha256("procedural-motion-package", values)
    return ProceduralMotionPackage(
        schema_version=_SCHEMA_VERSION,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source_validation_report_identity=source_validation_report_identity,
        numerical_codec_name=numerical_codec_name,
        numerical_codec_version=numerical_codec_version,
        numerical_codec_parameters_identity=numerical_codec_parameters_identity,
        semantic_detector_identity=semantic_detector_identity,
        shared_motion_configuration_identity=shared_motion_configuration_identity,
        scenario_ids=scenario_ids,
        tape_ids=tape_ids,
        counts=counts,
        canonical_table_artifacts=canonical_table_artifacts,
        canonical_schema_fingerprints=canonical_schema_fingerprints,
        source_input_identities=source_input_identities,
        package_identity=identity,
    )


def procedural_motion_package_identity(package: ProceduralMotionPackage) -> str:
    """Return the domain-separated canonical identity of a package."""
    return canonical_sha256("procedural-motion-package", _identity_value(package))


def procedural_motion_package_to_dict(
    package: ProceduralMotionPackage,
) -> dict[str, object]:
    """Return a package in its frozen serialization order."""
    if not isinstance(package, ProceduralMotionPackage):
        raise ValidationError("package must be ProceduralMotionPackage")
    return {
        "schema_version": package.schema_version,
        "dataset_id": package.dataset_id,
        "dataset_version": package.dataset_version,
        "source_validation_report_identity": (
            package.source_validation_report_identity
        ),
        "numerical_codec_name": package.numerical_codec_name,
        "numerical_codec_version": package.numerical_codec_version,
        "numerical_codec_parameters_identity": (
            package.numerical_codec_parameters_identity
        ),
        "semantic_detector_identity": package.semantic_detector_identity,
        "shared_motion_configuration_identity": (
            package.shared_motion_configuration_identity
        ),
        "scenario_ids": list(package.scenario_ids),
        "tape_ids": list(package.tape_ids),
        "counts": package.counts.to_dict(),
        "canonical_table_artifacts": [
            item.to_dict() for item in package.canonical_table_artifacts
        ],
        "canonical_schema_fingerprints": [
            item.to_dict() for item in package.canonical_schema_fingerprints
        ],
        "source_input_identities": [
            item.to_dict() for item in package.source_input_identities
        ],
        "package_identity": package.package_identity,
    }


def procedural_motion_package_from_dict(
    value: object,
) -> ProceduralMotionPackage:
    """Strictly reconstruct and validate a package dictionary."""
    mapping = _strict_mapping(value, _PACKAGE_FIELDS, "procedural motion package")
    table_values = mapping["canonical_table_artifacts"]
    fingerprint_values = mapping["canonical_schema_fingerprints"]
    source_values = mapping["source_input_identities"]
    for item, label in (
        (table_values, "canonical_table_artifacts"),
        (fingerprint_values, "canonical_schema_fingerprints"),
        (source_values, "source_input_identities"),
    ):
        if not isinstance(item, list):
            raise ValidationError(f"{label} must be a list")
    table_list = cast(list[object], table_values)
    fingerprint_list = cast(list[object], fingerprint_values)
    source_list = cast(list[object], source_values)
    return ProceduralMotionPackage(
        schema_version=mapping["schema_version"],  # type: ignore[arg-type]
        dataset_id=mapping["dataset_id"],  # type: ignore[arg-type]
        dataset_version=mapping["dataset_version"],  # type: ignore[arg-type]
        source_validation_report_identity=mapping["source_validation_report_identity"],  # type: ignore[arg-type]
        numerical_codec_name=mapping["numerical_codec_name"],  # type: ignore[arg-type]
        numerical_codec_version=mapping["numerical_codec_version"],  # type: ignore[arg-type]
        numerical_codec_parameters_identity=mapping[
            "numerical_codec_parameters_identity"
        ],  # type: ignore[arg-type]
        semantic_detector_identity=mapping["semantic_detector_identity"],  # type: ignore[arg-type]
        shared_motion_configuration_identity=mapping[
            "shared_motion_configuration_identity"
        ],  # type: ignore[arg-type]
        scenario_ids=mapping["scenario_ids"],  # type: ignore[arg-type]
        tape_ids=mapping["tape_ids"],  # type: ignore[arg-type]
        counts=ProceduralMotionCounts.from_dict(mapping["counts"]),
        canonical_table_artifacts=tuple(
            CanonicalTableArtifactReference.from_dict(item) for item in table_list
        ),
        canonical_schema_fingerprints=tuple(
            SourceInputIdentity.from_dict(item) for item in fingerprint_list
        ),
        source_input_identities=tuple(
            SourceInputIdentity.from_dict(item) for item in source_list
        ),
        package_identity=mapping["package_identity"],  # type: ignore[arg-type]
    )


def procedural_motion_package_to_json(package: ProceduralMotionPackage) -> str:
    """Return canonical UTF-8 JSON text with exactly one trailing newline."""
    return canonical_json_text(procedural_motion_package_to_dict(package))


def procedural_motion_package_from_json(value: str | bytes) -> ProceduralMotionPackage:
    """Strictly parse canonical package JSON without leaking parser chains."""
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeError:
            raise ValidationError("package JSON must be UTF-8") from None
    elif isinstance(value, str):
        text = value
    else:
        raise ValidationError("package JSON must be text or bytes")
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise ValidationError("package JSON must have exactly one trailing newline")
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ValidationError("package JSON is malformed") from None
    package = procedural_motion_package_from_dict(decoded)
    if procedural_motion_package_to_json(package) != text:
        raise ValidationError("package JSON is not canonical")
    return package
