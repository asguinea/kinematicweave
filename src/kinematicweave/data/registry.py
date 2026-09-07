"""Deterministic dataset registry and local source-manifest utilities."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import cast

from kinematicweave.artifact_store import (
    DiskSpaceSnapshot,
    RunDirectory,
    WrittenArtifact,
    atomic_write_text,
    check_disk_space,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import (
    ArtifactError,
    ResourceLimitError,
    SchemaError,
    ValidationError,
)
from kinematicweave.paths import normalize_relative_path, relative_path_text
from kinematicweave.timestamps import utc_now_timestamp

__all__ = [
    "DatasetAvailability",
    "DatasetRegistry",
    "DatasetRegistryEntry",
    "DatasetSourceFile",
    "DatasetSourceKind",
    "DatasetSourceManifest",
    "DatasetSourceManifestArtifact",
    "SourceChecksumMode",
    "atomic_write_dataset_source_manifest",
    "check_canonical_materialization_space",
    "dataset_registry_entries",
    "dataset_registry_to_dict",
    "dataset_source_manifest_from_dict",
    "dataset_source_manifest_from_json",
    "dataset_source_manifest_identity",
    "dataset_source_manifest_to_canonical_json",
    "dataset_source_manifest_to_dict",
    "default_dataset_registry",
    "discover_dataset_source",
    "estimate_canonical_materialization_bytes",
    "get_dataset_registry_entry",
    "verify_dataset_source",
    "verify_dataset_source_manifest_artifact",
]

_SCHEMA_VERSION = "1.0"
_DATASET_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_UTC_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
_UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_READ_CHUNK_SIZE = 1024 * 1024
_REGISTRY_FIELDS = ("schema_version", "entries")
_REGISTRY_ENTRY_FIELDS = (
    "dataset_id",
    "display_name",
    "source_kind",
    "adapter_name",
    "default_dataset_version",
    "license_reference",
    "citation_reference",
)
_SOURCE_FILE_FIELDS = ("relative_path", "size_bytes", "sha256")
_SOURCE_MANIFEST_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "adapter_name",
    "adapter_version",
    "source_kind",
    "source_root_label",
    "availability",
    "checksum_mode",
    "file_count",
    "total_bytes",
    "files",
    "license_reference",
    "citation_reference",
    "discovered_at_utc",
)


class DatasetSourceKind(StrEnum):
    """Approved dataset source categories."""

    GENERATED = "generated"
    EXTERNAL_DIRECTORY = "external_directory"


class DatasetAvailability(StrEnum):
    """Observed local source availability."""

    GENERATED = "generated"
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    AVAILABLE = "available"


class SourceChecksumMode(StrEnum):
    """Approved source-inventory checksum modes."""

    SHA256 = "sha256"
    SIZE_ONLY = "size_only"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _dataset_id(value: object) -> str:
    normalized = _required_text(value, "dataset_id")
    if _DATASET_ID_PATTERN.fullmatch(normalized) is None:
        raise ValidationError(
            "dataset_id must be a lowercase token beginning and ending "
            "with an alphanumeric character"
        )
    return normalized


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


def _positive_int(value: object, field_name: str) -> int:
    normalized = _nonnegative_int(value, field_name)
    if normalized == 0:
        raise ValidationError(f"{field_name} must be greater than zero")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
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
    raise ValidationError(f"{field_name} must use {enum_type.__name__}")


def _canonical_timestamp(value: object) -> str:
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValidationError("discovered_at_utc must use YYYY-MM-DDTHH:MM:SS.ffffffZ")
    try:
        datetime.strptime(value, _UTC_TIMESTAMP_FORMAT)
    except ValueError:
        raise ValidationError(
            "discovered_at_utc is not a valid UTC timestamp"
        ) from None
    return value


def _source_root_label(value: object) -> str:
    normalized = _required_text(value, "source_root_label")
    posix = PurePosixPath(normalized.replace("\\", "/"))
    windows = PureWindowsPath(normalized)
    if posix.is_absolute() or windows.is_absolute() or bool(windows.drive):
        raise ValidationError("source_root_label must not contain an absolute path")
    return normalized


@dataclass(frozen=True, slots=True)
class DatasetRegistryEntry:
    """One immutable logical dataset registration."""

    dataset_id: str
    display_name: str
    source_kind: DatasetSourceKind | str
    adapter_name: str
    default_dataset_version: str | None
    license_reference: str
    citation_reference: str

    def __post_init__(self) -> None:
        """Normalize and validate registry metadata."""
        object.__setattr__(self, "dataset_id", _dataset_id(self.dataset_id))
        for field_name in (
            "display_name",
            "adapter_name",
            "license_reference",
            "citation_reference",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "source_kind",
            _enum_value(DatasetSourceKind, self.source_kind, "source_kind"),
        )
        object.__setattr__(
            self,
            "default_dataset_version",
            _optional_text(
                self.default_dataset_version,
                "default_dataset_version",
            ),
        )


@dataclass(frozen=True, slots=True)
class DatasetRegistry:
    """Ordered immutable dataset registry."""

    schema_version: str
    entries: tuple[DatasetRegistryEntry, ...]

    def __post_init__(self) -> None:
        """Copy entries and enforce schema and identifier uniqueness."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        value: object = self.entries
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("entries must be a finite non-string sequence")
        entries = tuple(item for item in value)
        if not entries:
            raise ValidationError("entries must contain at least one entry")
        if any(not isinstance(item, DatasetRegistryEntry) for item in entries):
            raise ValidationError("entries must contain DatasetRegistryEntry values")
        identifiers = tuple(item.dataset_id for item in entries)
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError("dataset identifiers must be unique")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True, slots=True)
class DatasetSourceFile:
    """One deterministic source-file inventory record."""

    relative_path: Path
    size_bytes: int
    sha256: str | None

    def __post_init__(self) -> None:
        """Normalize the path and validate size and optional checksum."""
        object.__setattr__(
            self,
            "relative_path",
            normalize_relative_path(self.relative_path),
        )
        object.__setattr__(
            self,
            "size_bytes",
            _nonnegative_int(self.size_bytes, "size_bytes"),
        )
        if self.sha256 is not None:
            object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))


@dataclass(frozen=True, slots=True)
class DatasetSourceManifest:
    """Versioned deterministic local dataset-source inventory."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    adapter_name: str
    adapter_version: str
    source_kind: DatasetSourceKind | str
    source_root_label: str
    availability: DatasetAvailability | str
    checksum_mode: SourceChecksumMode | str
    file_count: int
    total_bytes: int
    files: tuple[DatasetSourceFile, ...]
    license_reference: str
    citation_reference: str
    discovered_at_utc: str

    def __post_init__(self) -> None:
        """Normalize values and enforce inventory and availability invariants."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(self, "dataset_id", _dataset_id(self.dataset_id))
        for field_name in (
            "dataset_version",
            "adapter_name",
            "adapter_version",
            "license_reference",
            "citation_reference",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "source_kind",
            _enum_value(DatasetSourceKind, self.source_kind, "source_kind"),
        )
        object.__setattr__(
            self,
            "source_root_label",
            _source_root_label(self.source_root_label),
        )
        object.__setattr__(
            self,
            "availability",
            _enum_value(DatasetAvailability, self.availability, "availability"),
        )
        object.__setattr__(
            self,
            "checksum_mode",
            _enum_value(SourceChecksumMode, self.checksum_mode, "checksum_mode"),
        )
        object.__setattr__(
            self,
            "file_count",
            _nonnegative_int(self.file_count, "file_count"),
        )
        object.__setattr__(
            self,
            "total_bytes",
            _nonnegative_int(self.total_bytes, "total_bytes"),
        )
        value: object = self.files
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError("files must be a finite non-string sequence")
        files = tuple(item for item in value)
        if any(not isinstance(item, DatasetSourceFile) for item in files):
            raise ValidationError("files must contain DatasetSourceFile values")
        paths = tuple(relative_path_text(item.relative_path) for item in files)
        if tuple(sorted(paths)) != paths or len(paths) != len(set(paths)):
            raise ValidationError(
                "file paths must be unique and strictly lexicographically ordered"
            )
        if self.file_count != len(files):
            raise ValidationError("file_count must equal len(files)")
        if self.total_bytes != sum(item.size_bytes for item in files):
            raise ValidationError("total_bytes must equal the sum of file sizes")
        if self.checksum_mode is SourceChecksumMode.SHA256:
            if any(item.sha256 is None for item in files):
                raise ValidationError("sha256 mode requires every file checksum")
        elif any(item.sha256 is not None for item in files):
            raise ValidationError("size_only mode requires null file checksums")
        if self.availability is DatasetAvailability.GENERATED:
            if (
                self.source_kind is not DatasetSourceKind.GENERATED
                or self.file_count != 0
                or self.total_bytes != 0
            ):
                raise ValidationError(
                    "generated availability requires an empty generated source"
                )
        elif self.source_kind is DatasetSourceKind.GENERATED:
            raise ValidationError("generated sources require generated availability")
        if self.availability is DatasetAvailability.MISSING and (
            self.file_count != 0 or self.total_bytes != 0
        ):
            raise ValidationError("missing availability requires zero files and bytes")
        if self.availability is DatasetAvailability.AVAILABLE and (
            self.source_kind is not DatasetSourceKind.EXTERNAL_DIRECTORY
            or self.file_count == 0
        ):
            raise ValidationError(
                "available external sources require at least one file"
            )
        object.__setattr__(self, "files", files)
        object.__setattr__(
            self,
            "discovered_at_utc",
            _canonical_timestamp(self.discovered_at_utc),
        )


@dataclass(frozen=True, slots=True)
class DatasetSourceManifestArtifact:
    """Written source-manifest identity and physical artifact metadata."""

    dataset_id: str
    dataset_version: str
    manifest_identity: str
    written_artifact: WrittenArtifact

    def __post_init__(self) -> None:
        """Validate logical identity and physical artifact metadata."""
        object.__setattr__(self, "dataset_id", _dataset_id(self.dataset_id))
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        object.__setattr__(
            self,
            "manifest_identity",
            _sha256(self.manifest_identity, "manifest_identity"),
        )
        if not isinstance(self.written_artifact, WrittenArtifact):
            raise ValidationError("written_artifact must be a WrittenArtifact")


def default_dataset_registry() -> DatasetRegistry:
    """Return a fresh registry containing the two approved dataset entries."""
    return DatasetRegistry(
        schema_version=_SCHEMA_VERSION,
        entries=(
            DatasetRegistryEntry(
                dataset_id="synthetic_kinematicweave",
                display_name="KinematicWeave Synthetic Correctness Dataset",
                source_kind=DatasetSourceKind.GENERATED,
                adapter_name="synthetic_generator",
                default_dataset_version="1.0",
                license_reference=(
                    "Project-generated synthetic data; repository licensing applies."
                ),
                citation_reference=(
                    "Cite the KinematicWeave project artifact when using this "
                    "generated dataset."
                ),
            ),
            DatasetRegistryEntry(
                dataset_id="av2_motion",
                display_name="Argoverse 2 Motion Forecasting",
                source_kind=DatasetSourceKind.EXTERNAL_DIRECTORY,
                adapter_name="av2_motion_adapter",
                default_dataset_version=None,
                license_reference=(
                    "Use the license distributed by the Argoverse 2 dataset provider."
                ),
                citation_reference=(
                    "Use the citation instructions distributed by the "
                    "Argoverse 2 dataset provider."
                ),
            ),
        ),
    )


def dataset_registry_entries(
    registry: DatasetRegistry,
) -> tuple[DatasetRegistryEntry, ...]:
    """Return registry entries in preserved order as a fresh tuple."""
    if not isinstance(registry, DatasetRegistry):
        raise ValidationError("registry must be a DatasetRegistry")
    return tuple(item for item in registry.entries)


def get_dataset_registry_entry(
    dataset_id: str,
    *,
    registry: DatasetRegistry | None = None,
) -> DatasetRegistryEntry:
    """Return one registered dataset or raise for an unknown identifier."""
    normalized = _dataset_id(dataset_id)
    selected = default_dataset_registry() if registry is None else registry
    if not isinstance(selected, DatasetRegistry):
        raise ValidationError("registry must be a DatasetRegistry")
    for entry in selected.entries:
        if entry.dataset_id == normalized:
            return entry
    raise ValidationError(f"unknown dataset identifier: {normalized!r}")


def _resolved_dataset_version(
    entry: DatasetRegistryEntry,
    dataset_version: str | None,
) -> str:
    selected = (
        dataset_version
        if dataset_version is not None
        else entry.default_dataset_version
    )
    if selected is None:
        raise ValidationError("dataset_version is required for this registry entry")
    return _required_text(selected, "dataset_version")


def _inventory_limits(
    max_files: object,
    max_total_bytes: object,
) -> tuple[int, int | None]:
    normalized_files = _positive_int(max_files, "max_files")
    normalized_bytes = (
        None
        if max_total_bytes is None
        else _nonnegative_int(max_total_bytes, "max_total_bytes")
    )
    return normalized_files, normalized_bytes


def _hash_source_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_READ_CHUNK_SIZE):
                digest.update(chunk)
    except OSError as error:
        raise ArtifactError(f"source file cannot be read: {path.name}") from error
    return digest.hexdigest()


def _walk_source_files(root: Path) -> Iterator[Path]:
    directories = [root]
    while directories:
        directory = directories.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: entry.name,
                    reverse=True,
                )
        except OSError as error:
            raise ArtifactError(
                f"source directory cannot be read: {directory.name}"
            ) from error
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    raise ArtifactError(
                        f"symbolic links are not allowed in source data: {entry.name}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path
            except OSError as error:
                raise ArtifactError(
                    f"source entry cannot be inspected: {entry.name}"
                ) from error


def _inventory_source(
    root: Path,
    checksum_mode: SourceChecksumMode,
    *,
    max_files: int,
    max_total_bytes: int | None,
) -> tuple[DatasetSourceFile, ...]:
    records: list[DatasetSourceFile] = []
    total_bytes = 0
    for path in _walk_source_files(root):
        try:
            before = path.stat(follow_symlinks=False)
        except OSError as error:
            raise ArtifactError(f"source file disappeared: {path.name}") from error
        if not path.is_file() or path.is_symlink():
            raise ArtifactError(f"source file changed type: {path.name}")
        next_count = len(records) + 1
        if next_count > max_files:
            raise ResourceLimitError(
                f"source inventory exceeds max_files ({max_files})"
            )
        next_total = total_bytes + before.st_size
        if max_total_bytes is not None and next_total > max_total_bytes:
            raise ResourceLimitError(
                f"source inventory exceeds max_total_bytes ({max_total_bytes})"
            )
        checksum = (
            _hash_source_file(path)
            if checksum_mode is SourceChecksumMode.SHA256
            else None
        )
        try:
            after = path.stat(follow_symlinks=False)
        except OSError as error:
            raise ArtifactError(f"source file disappeared: {path.name}") from error
        if after.st_size != before.st_size:
            raise ArtifactError(f"source file changed size: {path.name}")
        try:
            relative_path = path.relative_to(root)
        except ValueError:
            raise ArtifactError("source file resolves outside source root") from None
        records.append(
            DatasetSourceFile(
                relative_path=relative_path,
                size_bytes=before.st_size,
                sha256=checksum,
            )
        )
        total_bytes = next_total
    return tuple(
        sorted(records, key=lambda item: relative_path_text(item.relative_path))
    )


def discover_dataset_source(
    entry: DatasetRegistryEntry,
    *,
    dataset_version: str | None = None,
    adapter_version: str = "1.0",
    source_root: Path | None = None,
    source_root_label: str | None = None,
    checksum_mode: SourceChecksumMode | str = SourceChecksumMode.SHA256,
    max_files: int = 500_000,
    max_total_bytes: int | None = None,
) -> DatasetSourceManifest:
    """Discover a generated or local external source without modifying it."""
    if not isinstance(entry, DatasetRegistryEntry):
        raise ValidationError("entry must be a DatasetRegistryEntry")
    version = _resolved_dataset_version(entry, dataset_version)
    normalized_adapter_version = _required_text(
        adapter_version,
        "adapter_version",
    )
    mode = _enum_value(SourceChecksumMode, checksum_mode, "checksum_mode")
    normalized_max_files, normalized_max_bytes = _inventory_limits(
        max_files,
        max_total_bytes,
    )
    if entry.source_kind is DatasetSourceKind.GENERATED:
        if source_root is not None:
            raise ValidationError("generated sources do not accept source_root")
        return DatasetSourceManifest(
            schema_version=_SCHEMA_VERSION,
            dataset_id=entry.dataset_id,
            dataset_version=version,
            adapter_name=entry.adapter_name,
            adapter_version=normalized_adapter_version,
            source_kind=entry.source_kind,
            source_root_label="generated",
            availability=DatasetAvailability.GENERATED,
            checksum_mode=mode,
            file_count=0,
            total_bytes=0,
            files=(),
            license_reference=entry.license_reference,
            citation_reference=entry.citation_reference,
            discovered_at_utc=utc_now_timestamp(),
        )
    if not isinstance(source_root, Path):
        raise ValidationError("external-directory sources require source_root as Path")
    if source_root_label is None:
        raise ValidationError("external-directory sources require source_root_label")
    label = _source_root_label(source_root_label)
    if source_root.is_symlink():
        raise ArtifactError("source_root must not be a symbolic link")
    if not source_root.exists():
        availability = DatasetAvailability.MISSING
        files: tuple[DatasetSourceFile, ...] = ()
    else:
        if not source_root.is_dir():
            raise ValidationError("source_root must be a directory")
        files = _inventory_source(
            source_root,
            mode,
            max_files=normalized_max_files,
            max_total_bytes=normalized_max_bytes,
        )
        availability = (
            DatasetAvailability.AVAILABLE if files else DatasetAvailability.INCOMPLETE
        )
    return DatasetSourceManifest(
        schema_version=_SCHEMA_VERSION,
        dataset_id=entry.dataset_id,
        dataset_version=version,
        adapter_name=entry.adapter_name,
        adapter_version=normalized_adapter_version,
        source_kind=entry.source_kind,
        source_root_label=label,
        availability=availability,
        checksum_mode=mode,
        file_count=len(files),
        total_bytes=sum(item.size_bytes for item in files),
        files=files,
        license_reference=entry.license_reference,
        citation_reference=entry.citation_reference,
        discovered_at_utc=utc_now_timestamp(),
    )


def dataset_registry_to_dict(registry: DatasetRegistry) -> dict[str, object]:
    """Return a fresh ordered JSON-compatible registry dictionary."""
    if not isinstance(registry, DatasetRegistry):
        raise ValidationError("registry must be a DatasetRegistry")
    return {
        "schema_version": registry.schema_version,
        "entries": [
            {
                "dataset_id": entry.dataset_id,
                "display_name": entry.display_name,
                "source_kind": _enum_value(
                    DatasetSourceKind,
                    entry.source_kind,
                    "source_kind",
                ).value,
                "adapter_name": entry.adapter_name,
                "default_dataset_version": entry.default_dataset_version,
                "license_reference": entry.license_reference,
                "citation_reference": entry.citation_reference,
            }
            for entry in registry.entries
        ],
    }


def dataset_source_manifest_to_dict(
    manifest: DatasetSourceManifest,
) -> dict[str, object]:
    """Return a fresh ordered JSON-compatible source-manifest dictionary."""
    if not isinstance(manifest, DatasetSourceManifest):
        raise ValidationError("manifest must be a DatasetSourceManifest")
    return {
        "schema_version": manifest.schema_version,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "adapter_name": manifest.adapter_name,
        "adapter_version": manifest.adapter_version,
        "source_kind": _enum_value(
            DatasetSourceKind,
            manifest.source_kind,
            "source_kind",
        ).value,
        "source_root_label": manifest.source_root_label,
        "availability": _enum_value(
            DatasetAvailability,
            manifest.availability,
            "availability",
        ).value,
        "checksum_mode": _enum_value(
            SourceChecksumMode,
            manifest.checksum_mode,
            "checksum_mode",
        ).value,
        "file_count": manifest.file_count,
        "total_bytes": manifest.total_bytes,
        "files": [
            {
                "relative_path": relative_path_text(item.relative_path),
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in manifest.files
        ],
        "license_reference": manifest.license_reference,
        "citation_reference": manifest.citation_reference,
        "discovered_at_utc": manifest.discovered_at_utc,
    }


def dataset_source_manifest_to_canonical_json(
    manifest: DatasetSourceManifest,
) -> str:
    """Return canonical UTF-8-compatible JSON text with one trailing newline."""
    return canonical_json_text(
        dataset_source_manifest_to_dict(manifest),
        trailing_newline=True,
    )


def _validate_fields(
    value: Mapping[str, object],
    expected: tuple[str, ...],
    record_name: str,
) -> None:
    if any(not isinstance(key, str) for key in value):
        raise SchemaError(f"{record_name} field names must be strings")
    actual = set(value)
    expected_set = set(expected)
    missing = expected_set - actual
    unknown = actual - expected_set
    if missing:
        raise SchemaError(
            f"{record_name} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SchemaError(
            f"{record_name} has unknown fields: {', '.join(sorted(unknown))}"
        )


def dataset_source_manifest_from_dict(
    value: Mapping[str, object],
) -> DatasetSourceManifest:
    """Deserialize and validate a source-manifest mapping."""
    if not isinstance(value, Mapping):
        raise SchemaError("dataset source manifest must be an object")
    raw = value
    _validate_fields(raw, _SOURCE_MANIFEST_FIELDS, "dataset source manifest")
    files_value = raw["files"]
    if not isinstance(files_value, list):
        raise SchemaError("dataset source manifest files must be a list")
    files: list[DatasetSourceFile] = []
    for index, item in enumerate(files_value):
        if not isinstance(item, Mapping):
            raise SchemaError(f"dataset source file {index} must be an object")
        file_raw = cast(Mapping[str, object], item)
        _validate_fields(file_raw, _SOURCE_FILE_FIELDS, "dataset source file")
        try:
            files.append(
                DatasetSourceFile(
                    relative_path=cast(Path, file_raw["relative_path"]),
                    size_bytes=cast(int, file_raw["size_bytes"]),
                    sha256=cast(str | None, file_raw["sha256"]),
                )
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise SchemaError(str(error)) from None
    try:
        return DatasetSourceManifest(
            schema_version=cast(str, raw["schema_version"]),
            dataset_id=cast(str, raw["dataset_id"]),
            dataset_version=cast(str, raw["dataset_version"]),
            adapter_name=cast(str, raw["adapter_name"]),
            adapter_version=cast(str, raw["adapter_version"]),
            source_kind=cast(str, raw["source_kind"]),
            source_root_label=cast(str, raw["source_root_label"]),
            availability=cast(str, raw["availability"]),
            checksum_mode=cast(str, raw["checksum_mode"]),
            file_count=cast(int, raw["file_count"]),
            total_bytes=cast(int, raw["total_bytes"]),
            files=tuple(files),
            license_reference=cast(str, raw["license_reference"]),
            citation_reference=cast(str, raw["citation_reference"]),
            discovered_at_utc=cast(str, raw["discovered_at_utc"]),
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def dataset_source_manifest_from_json(text: str) -> DatasetSourceManifest:
    """Deserialize source-manifest JSON without exposing parser exception chains."""
    if not isinstance(text, str):
        raise SchemaError("dataset source manifest JSON must be text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise SchemaError(
            f"invalid dataset source manifest JSON: {error.msg}"
        ) from None
    if not isinstance(parsed, Mapping):
        raise SchemaError("dataset source manifest JSON root must be an object")
    return dataset_source_manifest_from_dict(cast(Mapping[str, object], parsed))


def dataset_source_manifest_identity(
    manifest: DatasetSourceManifest,
) -> str:
    """Return logical manifest identity excluding time and local root label."""
    if not isinstance(manifest, DatasetSourceManifest):
        raise ValidationError("manifest must be a DatasetSourceManifest")
    payload = dataset_source_manifest_to_dict(manifest)
    del payload["source_root_label"]
    del payload["discovered_at_utc"]
    return canonical_sha256("dataset-source-manifest", payload)


def verify_dataset_source(
    manifest: DatasetSourceManifest,
    *,
    source_root: Path | None,
) -> None:
    """Verify a generated or external source against its recorded inventory."""
    if not isinstance(manifest, DatasetSourceManifest):
        raise ValidationError("manifest must be a DatasetSourceManifest")
    if manifest.availability is DatasetAvailability.GENERATED:
        if source_root is not None:
            raise ValidationError(
                "generated source verification requires source_root=None"
            )
        return
    if manifest.availability is DatasetAvailability.MISSING:
        raise ArtifactError("missing dataset sources cannot be verified")
    if not isinstance(source_root, Path):
        raise ArtifactError("external source verification requires source_root")
    if source_root.is_symlink() or not source_root.exists() or not source_root.is_dir():
        raise ArtifactError("external source_root must be an existing directory")
    entry = DatasetRegistryEntry(
        dataset_id=manifest.dataset_id,
        display_name=manifest.dataset_id,
        source_kind=manifest.source_kind,
        adapter_name=manifest.adapter_name,
        default_dataset_version=manifest.dataset_version,
        license_reference=manifest.license_reference,
        citation_reference=manifest.citation_reference,
    )
    try:
        discovered = discover_dataset_source(
            entry,
            dataset_version=manifest.dataset_version,
            adapter_version=manifest.adapter_version,
            source_root=source_root,
            source_root_label=manifest.source_root_label,
            checksum_mode=manifest.checksum_mode,
            max_files=max(manifest.file_count, 1),
            max_total_bytes=manifest.total_bytes,
        )
    except (ResourceLimitError, ValidationError) as error:
        raise ArtifactError(str(error)) from None
    if (
        discovered.availability is not manifest.availability
        or discovered.file_count != manifest.file_count
        or discovered.total_bytes != manifest.total_bytes
        or discovered.files != manifest.files
    ):
        raise ArtifactError("dataset source differs from its recorded inventory")


def estimate_canonical_materialization_bytes(
    manifest: DatasetSourceManifest,
    *,
    expansion_factor: float = 1.5,
    minimum_bytes: int = 0,
) -> int:
    """Estimate canonical bytes using an expansion factor and minimum floor."""
    if not isinstance(manifest, DatasetSourceManifest):
        raise ValidationError("manifest must be a DatasetSourceManifest")
    if not isinstance(expansion_factor, (int, float)) or isinstance(
        expansion_factor, bool
    ):
        raise ValidationError("expansion_factor must be a finite non-Boolean number")
    try:
        normalized_factor = float(expansion_factor)
    except OverflowError:
        raise ValidationError(
            "expansion_factor must be a finite non-Boolean number"
        ) from None
    if not math.isfinite(normalized_factor) or normalized_factor < 0:
        raise ValidationError(
            "expansion_factor must be finite and greater than or equal to zero"
        )
    normalized_minimum = _nonnegative_int(minimum_bytes, "minimum_bytes")
    try:
        expanded = math.ceil(manifest.total_bytes * normalized_factor)
    except OverflowError:
        raise ValidationError("materialization estimate is not finite") from None
    return max(expanded, normalized_minimum)


def check_canonical_materialization_space(
    repository_root: Path,
    manifest: DatasetSourceManifest,
    *,
    expansion_factor: float = 1.5,
    minimum_bytes: int = 0,
    reserve_fraction: float = 0.15,
) -> DiskSpaceSnapshot:
    """Preflight disk capacity for canonical materialization without writing."""
    required_bytes = estimate_canonical_materialization_bytes(
        manifest,
        expansion_factor=expansion_factor,
        minimum_bytes=minimum_bytes,
    )
    return check_disk_space(
        repository_root,
        required_bytes=required_bytes,
        reserve_fraction=reserve_fraction,
    )


def atomic_write_dataset_source_manifest(
    run_directory: RunDirectory,
    relative_path: str | Path,
    manifest: DatasetSourceManifest,
) -> DatasetSourceManifestArtifact:
    """Atomically write one canonical source manifest without finalizing its run."""
    if not isinstance(manifest, DatasetSourceManifest):
        raise ValidationError("manifest must be a DatasetSourceManifest")
    written = atomic_write_text(
        run_directory,
        relative_path,
        dataset_source_manifest_to_canonical_json(manifest),
    )
    return DatasetSourceManifestArtifact(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        manifest_identity=dataset_source_manifest_identity(manifest),
        written_artifact=written,
    )


def _verified_artifact_bytes(
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
        raise ArtifactError("dataset source manifest must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("dataset source manifest is missing") from error
    if resolved == root or not resolved.is_relative_to(root):
        raise ArtifactError("dataset source manifest resolves outside repository")
    if not resolved.is_file():
        raise ArtifactError("dataset source manifest is not a regular file")
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise ArtifactError("dataset source manifest cannot be read") from error
    if len(data) != artifact.size_bytes:
        raise ArtifactError("dataset source manifest size differs")
    if hashlib.sha256(data).hexdigest() != artifact.content_checksum:
        raise ArtifactError("dataset source manifest checksum differs")
    return data


def verify_dataset_source_manifest_artifact(
    repository_root: Path,
    artifact: DatasetSourceManifestArtifact,
) -> DatasetSourceManifest:
    """Verify and return a written dataset source manifest."""
    if not isinstance(artifact, DatasetSourceManifestArtifact):
        raise ValidationError("artifact must be a DatasetSourceManifestArtifact")
    data = _verified_artifact_bytes(repository_root, artifact.written_artifact)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SchemaError("dataset source manifest is not valid UTF-8") from None
    manifest = dataset_source_manifest_from_json(text)
    if (
        manifest.dataset_id != artifact.dataset_id
        or manifest.dataset_version != artifact.dataset_version
    ):
        raise SchemaError("dataset source manifest identity metadata differs")
    if dataset_source_manifest_identity(manifest) != artifact.manifest_identity:
        raise SchemaError("dataset source manifest logical identity differs")
    return manifest
