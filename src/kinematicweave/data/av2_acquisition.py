"""Deterministic acquisition of official AV2 Motion Forecasting scenarios."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, cast
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
import xml.etree.ElementTree as ElementTree
import zipfile

from kinematicweave.artifact_store import check_disk_space
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import (
    ArtifactError,
    ResourceLimitError,
    SchemaError,
    ValidationError,
)
from kinematicweave.paths import normalize_relative_path, relative_path_text
from kinematicweave.seeding import derive_seed, validate_root_seed

__all__ = [
    "AV2_OFFICIAL_MOTION_ROOT",
    "Av2AcquiredFile",
    "Av2AcquisitionConfig",
    "Av2AcquisitionPlan",
    "Av2AcquisitionReport",
    "Av2RemoteObject",
    "Av2RemoteScenarioPair",
    "acquire_av2_plan",
    "av2_acquisition_plan_from_dict",
    "av2_acquisition_plan_from_json",
    "av2_acquisition_plan_identity",
    "av2_acquisition_plan_to_canonical_json",
    "av2_acquisition_plan_to_dict",
    "av2_acquisition_report_from_dict",
    "av2_acquisition_report_from_json",
    "av2_acquisition_report_to_canonical_json",
    "av2_acquisition_report_to_dict",
    "build_av2_acquisition_plan",
    "build_remote_scenario_pairs",
    "discover_av2_partition",
    "inspect_av2_remote",
    "inspect_av2_remote_catalog",
    "parse_anonymous_s3_page",
    "parse_s5cmd_listing",
    "remote_catalog_identity",
    "verify_acquired_files",
]

AV2_OFFICIAL_MOTION_ROOT = "s3://argoverse/datasets/av2/motion-forecasting/"
_SCHEMA_VERSION = "1.0"
_DATASET_ID = "av2_motion"
_PLAN_DOMAIN = "av2-provider-acquisition-plan"
_CATALOG_DOMAIN = "av2-provider-remote-catalog"
_READ_CHUNK_SIZE = 1024 * 1024
_DOWNLOAD_ATTEMPTS = 3
_COMMAND_TIMEOUT_SECONDS = 300.0
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PARTITION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_BACKENDS = frozenset({"auto", "s5cmd", "anonymous_s3_http"})
_DISPOSITIONS = frozenset({"downloaded", "reused"})

_REMOTE_OBJECT_FIELDS = ("remote_uri", "relative_key", "size_bytes", "etag")
_REMOTE_PAIR_FIELDS = ("source_scenario_id", "motion", "vector_map")
_CONFIG_FIELDS = (
    "remote_root",
    "partition",
    "scenario_count",
    "root_seed",
    "assignment_namespace",
    "local_relative_root",
    "backend",
)
_PLAN_FIELDS = (
    "schema_version",
    "dataset_id",
    "dataset_version",
    "remote_root",
    "partition",
    "root_seed",
    "assignment_namespace",
    "candidate_count",
    "selected_scenarios",
    "remote_catalog_identity",
)
_ACQUIRED_FILE_FIELDS = (
    "remote_uri",
    "relative_path",
    "size_bytes",
    "sha256",
    "disposition",
)
_REPORT_FIELDS = (
    "schema_version",
    "plan_identity",
    "backend",
    "backend_version",
    "selected_scenario_count",
    "candidate_count",
    "downloaded_file_count",
    "reused_file_count",
    "downloaded_bytes",
    "reused_bytes",
    "total_local_bytes",
    "listing_seconds",
    "download_seconds",
    "acquired_files",
)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be nonempty text")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


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


def _finite_nonnegative(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValidationError(f"{field_name} must be finite and nonnegative")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _source_id(value: object, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if _SOURCE_ID_PATTERN.fullmatch(normalized) is None or normalized in {".", ".."}:
        raise ValidationError(f"{field_name} is not a safe source identifier")
    return normalized


def _partition(value: object) -> str:
    normalized = _required_text(value, "partition").strip("/")
    if (
        not normalized
        or _PARTITION_PATTERN.fullmatch(normalized) is None
        or normalized in {".", ".."}
    ):
        raise ValidationError("partition must be one safe path component")
    return normalized


def _official_remote_root(value: object) -> str:
    normalized = _required_text(value, "remote_root")
    if not normalized.endswith("/"):
        normalized += "/"
    if normalized != AV2_OFFICIAL_MOTION_ROOT:
        raise ValidationError(
            "remote_root must be the official Argoverse Motion Forecasting root"
        )
    return normalized


def _relative_key(value: object) -> str:
    normalized = _required_text(value, "relative_key").replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized.startswith("/") or ".." in path.parts:
        raise ValidationError("relative_key must be a normalized relative S3 key")
    canonical = path.as_posix()
    if canonical != normalized or canonical in {".", ""}:
        raise ValidationError("relative_key must be normalized")
    return canonical


def _exact_mapping(
    value: object,
    expected: tuple[str, ...],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{label} must be an object")
    mapping = cast(Mapping[str, object], value)
    if any(not isinstance(key, str) for key in mapping):
        raise SchemaError(f"{label} field names must be strings")
    missing = set(expected) - set(mapping)
    unknown = set(mapping) - set(expected)
    if missing:
        raise SchemaError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise SchemaError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    return mapping


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a finite non-string sequence")
    return value


@dataclass(frozen=True, slots=True)
class Av2RemoteObject:
    """One provider object without any local filesystem path."""

    remote_uri: str
    relative_key: str
    size_bytes: int
    etag: str | None

    def __post_init__(self) -> None:
        """Validate official origin, normalized key, size, and optional ETag."""
        key = _relative_key(self.relative_key)
        uri = _required_text(self.remote_uri, "remote_uri")
        if uri != f"{AV2_OFFICIAL_MOTION_ROOT}{key}":
            raise ValidationError(
                "remote_uri must match the official root and relative_key"
            )
        object.__setattr__(self, "remote_uri", uri)
        object.__setattr__(self, "relative_key", key)
        object.__setattr__(
            self,
            "size_bytes",
            _nonnegative_int(self.size_bytes, "size_bytes"),
        )
        object.__setattr__(self, "etag", _optional_text(self.etag, "etag"))


@dataclass(frozen=True, slots=True)
class Av2RemoteScenarioPair:
    """One complete provider motion and paired vector-map scenario."""

    source_scenario_id: str
    motion: Av2RemoteObject
    vector_map: Av2RemoteObject

    def __post_init__(self) -> None:
        """Enforce exact provider filenames and matching scenario directories."""
        source_id = _source_id(self.source_scenario_id, "source_scenario_id")
        if not isinstance(self.motion, Av2RemoteObject) or not isinstance(
            self.vector_map, Av2RemoteObject
        ):
            raise ValidationError("motion and vector_map must be remote objects")
        if self.motion == self.vector_map:
            raise ValidationError("motion and vector_map must be distinct")
        motion = PurePosixPath(self.motion.relative_key)
        vector_map = PurePosixPath(self.vector_map.relative_key)
        if motion.name != f"scenario_{source_id}.parquet":
            raise ValidationError("motion filename differs from source_scenario_id")
        if vector_map.name != f"log_map_archive_{source_id}.json":
            raise ValidationError("map filename differs from source_scenario_id")
        if motion.parent != vector_map.parent or motion.parent.name != source_id:
            raise ValidationError("remote pair must share its scenario directory")
        object.__setattr__(self, "source_scenario_id", source_id)

    @property
    def total_remote_bytes(self) -> int:
        """Return exact bytes across the provider motion/map pair."""
        return self.motion.size_bytes + self.vector_map.size_bytes


@dataclass(frozen=True, slots=True)
class Av2AcquisitionConfig:
    """Validated configuration for bounded official AV2 acquisition."""

    remote_root: str = AV2_OFFICIAL_MOTION_ROOT
    partition: str = "val"
    scenario_count: int = 10
    root_seed: int = 0
    assignment_namespace: str = "av2-provider-evidence-v1"
    local_relative_root: Path = Path("data/external/av2_motion")
    backend: str = "auto"

    def __post_init__(self) -> None:
        """Normalize deterministic, path, and backend settings."""
        object.__setattr__(self, "remote_root", _official_remote_root(self.remote_root))
        object.__setattr__(self, "partition", _partition(self.partition))
        object.__setattr__(
            self,
            "scenario_count",
            _positive_int(self.scenario_count, "scenario_count"),
        )
        object.__setattr__(self, "root_seed", validate_root_seed(self.root_seed))
        object.__setattr__(
            self,
            "assignment_namespace",
            _required_text(self.assignment_namespace, "assignment_namespace"),
        )
        object.__setattr__(
            self,
            "local_relative_root",
            normalize_relative_path(self.local_relative_root),
        )
        backend = _required_text(self.backend, "backend").casefold()
        if backend not in _BACKENDS:
            raise ValidationError("backend must be auto, s5cmd, or anonymous_s3_http")
        object.__setattr__(self, "backend", backend)


@dataclass(frozen=True, slots=True)
class Av2AcquisitionPlan:
    """Immutable deterministic provider selection."""

    schema_version: str
    dataset_id: str
    dataset_version: str
    remote_root: str
    partition: str
    root_seed: int
    assignment_namespace: str
    candidate_count: int
    selected_scenarios: tuple[Av2RemoteScenarioPair, ...]
    remote_catalog_identity: str

    def __post_init__(self) -> None:
        """Validate plan identity inputs, selected uniqueness, and rank order."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        if self.dataset_id != _DATASET_ID:
            raise ValidationError(f"dataset_id must equal {_DATASET_ID!r}")
        object.__setattr__(
            self,
            "dataset_version",
            _required_text(self.dataset_version, "dataset_version"),
        )
        object.__setattr__(self, "remote_root", _official_remote_root(self.remote_root))
        object.__setattr__(self, "partition", _partition(self.partition))
        object.__setattr__(self, "root_seed", validate_root_seed(self.root_seed))
        namespace = _required_text(self.assignment_namespace, "assignment_namespace")
        object.__setattr__(self, "assignment_namespace", namespace)
        candidates = _nonnegative_int(self.candidate_count, "candidate_count")
        selected_value = tuple(_sequence(self.selected_scenarios, "selected_scenarios"))
        if not selected_value or any(
            not isinstance(item, Av2RemoteScenarioPair) for item in selected_value
        ):
            raise ValidationError(
                "selected_scenarios must contain remote scenario pairs"
            )
        selected = cast(tuple[Av2RemoteScenarioPair, ...], selected_value)
        if candidates < len(selected):
            raise ValidationError("candidate_count must cover selected_scenarios")
        ids = tuple(item.source_scenario_id for item in selected)
        motion_uris = tuple(item.motion.remote_uri for item in selected)
        map_uris = tuple(item.vector_map.remote_uri for item in selected)
        if any(
            len(values) != len(set(values)) for values in (ids, motion_uris, map_uris)
        ):
            raise ValidationError("selected scenario IDs and objects must be unique")
        if any(
            PurePosixPath(item.motion.relative_key).parts[0] != self.partition
            or PurePosixPath(item.vector_map.relative_key).parts[0] != self.partition
            for item in selected
        ):
            raise ValidationError("selected scenarios must use the plan partition")
        expected_order = tuple(
            sorted(
                selected,
                key=lambda pair: (
                    derive_seed(
                        self.root_seed,
                        "av2-provider-acquisition",
                        namespace,
                        pair.source_scenario_id,
                    ),
                    pair.source_scenario_id,
                ),
            )
        )
        if selected != expected_order:
            raise ValidationError(
                "selected_scenarios are not in deterministic rank order"
            )
        object.__setattr__(self, "candidate_count", candidates)
        object.__setattr__(self, "selected_scenarios", selected)
        object.__setattr__(
            self,
            "remote_catalog_identity",
            _sha256(self.remote_catalog_identity, "remote_catalog_identity"),
        )

    @property
    def selected_remote_bytes(self) -> int:
        """Return exact selected motion/map bytes."""
        return sum(item.total_remote_bytes for item in self.selected_scenarios)


@dataclass(frozen=True, slots=True)
class Av2AcquiredFile:
    """One downloaded or manifest-verified local provider file."""

    remote_uri: str
    relative_path: Path
    size_bytes: int
    sha256: str
    disposition: str

    def __post_init__(self) -> None:
        """Validate official URI, repository-relative path, hash, and disposition."""
        uri = _required_text(self.remote_uri, "remote_uri")
        if not uri.startswith(AV2_OFFICIAL_MOTION_ROOT):
            raise ValidationError("remote_uri must use the official AV2 root")
        object.__setattr__(self, "remote_uri", uri)
        object.__setattr__(
            self, "relative_path", normalize_relative_path(self.relative_path)
        )
        object.__setattr__(
            self, "size_bytes", _nonnegative_int(self.size_bytes, "size_bytes")
        )
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))
        disposition = _required_text(self.disposition, "disposition").casefold()
        if disposition not in _DISPOSITIONS:
            raise ValidationError("disposition must be downloaded or reused")
        object.__setattr__(self, "disposition", disposition)


@dataclass(frozen=True, slots=True)
class Av2AcquisitionReport:
    """Measured result of one bounded acquisition pass."""

    schema_version: str
    plan_identity: str
    backend: str
    backend_version: str
    selected_scenario_count: int
    candidate_count: int
    downloaded_file_count: int
    reused_file_count: int
    downloaded_bytes: int
    reused_bytes: int
    total_local_bytes: int
    listing_seconds: float
    download_seconds: float
    acquired_files: tuple[Av2AcquiredFile, ...]

    def __post_init__(self) -> None:
        """Validate counts, byte accounting, measurements, and file ordering."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self, "plan_identity", _sha256(self.plan_identity, "plan_identity")
        )
        backend = _required_text(self.backend, "backend").casefold()
        if backend not in {"s5cmd", "anonymous_s3_http"}:
            raise ValidationError("report backend must be a concrete backend")
        object.__setattr__(self, "backend", backend)
        object.__setattr__(
            self,
            "backend_version",
            _required_text(self.backend_version, "backend_version"),
        )
        for field_name in (
            "selected_scenario_count",
            "candidate_count",
            "downloaded_file_count",
            "reused_file_count",
            "downloaded_bytes",
            "reused_bytes",
            "total_local_bytes",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        if self.candidate_count < self.selected_scenario_count:
            raise ValidationError("candidate_count must cover selected count")
        files_value = tuple(_sequence(self.acquired_files, "acquired_files"))
        if any(not isinstance(item, Av2AcquiredFile) for item in files_value):
            raise ValidationError("acquired_files must contain Av2AcquiredFile values")
        files = cast(tuple[Av2AcquiredFile, ...], files_value)
        if len(files) != self.selected_scenario_count * 2:
            raise ValidationError("acquired file count must be twice scenario count")
        paths = tuple(relative_path_text(item.relative_path) for item in files)
        if tuple(sorted(paths)) != paths or len(paths) != len(set(paths)):
            raise ValidationError("acquired_files must be uniquely path-sorted")
        downloaded = tuple(item for item in files if item.disposition == "downloaded")
        reused = tuple(item for item in files if item.disposition == "reused")
        checks = (
            (self.downloaded_file_count, len(downloaded), "downloaded_file_count"),
            (self.reused_file_count, len(reused), "reused_file_count"),
            (
                self.downloaded_bytes,
                sum(item.size_bytes for item in downloaded),
                "downloaded_bytes",
            ),
            (
                self.reused_bytes,
                sum(item.size_bytes for item in reused),
                "reused_bytes",
            ),
            (
                self.total_local_bytes,
                sum(item.size_bytes for item in files),
                "total_local_bytes",
            ),
        )
        for actual, expected, label in checks:
            if actual != expected:
                raise ValidationError(f"{label} differs from acquired_files")
        object.__setattr__(
            self,
            "listing_seconds",
            _finite_nonnegative(self.listing_seconds, "listing_seconds"),
        )
        object.__setattr__(
            self,
            "download_seconds",
            _finite_nonnegative(self.download_seconds, "download_seconds"),
        )
        object.__setattr__(self, "acquired_files", files)


@dataclass(frozen=True, slots=True)
class _Backend:
    name: str
    version: str
    executable: Path | None = None


def _remote_object_to_dict(value: Av2RemoteObject) -> dict[str, object]:
    return {
        "remote_uri": value.remote_uri,
        "relative_key": value.relative_key,
        "size_bytes": value.size_bytes,
        "etag": value.etag,
    }


def _remote_pair_to_dict(value: Av2RemoteScenarioPair) -> dict[str, object]:
    return {
        "source_scenario_id": value.source_scenario_id,
        "motion": _remote_object_to_dict(value.motion),
        "vector_map": _remote_object_to_dict(value.vector_map),
    }


def _config_to_dict(value: Av2AcquisitionConfig) -> dict[str, object]:
    return {
        "remote_root": value.remote_root,
        "partition": value.partition,
        "scenario_count": value.scenario_count,
        "root_seed": value.root_seed,
        "assignment_namespace": value.assignment_namespace,
        "local_relative_root": relative_path_text(value.local_relative_root),
        "backend": value.backend,
    }


def av2_acquisition_plan_to_dict(
    plan: Av2AcquisitionPlan,
) -> dict[str, object]:
    """Return a fresh ordered JSON-compatible acquisition plan."""
    if not isinstance(plan, Av2AcquisitionPlan):
        raise ValidationError("plan must be Av2AcquisitionPlan")
    return {
        "schema_version": plan.schema_version,
        "dataset_id": plan.dataset_id,
        "dataset_version": plan.dataset_version,
        "remote_root": plan.remote_root,
        "partition": plan.partition,
        "root_seed": plan.root_seed,
        "assignment_namespace": plan.assignment_namespace,
        "candidate_count": plan.candidate_count,
        "selected_scenarios": [
            _remote_pair_to_dict(item) for item in plan.selected_scenarios
        ],
        "remote_catalog_identity": plan.remote_catalog_identity,
    }


def av2_acquisition_plan_to_canonical_json(plan: Av2AcquisitionPlan) -> str:
    """Serialize an acquisition plan as canonical JSON."""
    return canonical_json_text(
        av2_acquisition_plan_to_dict(plan), trailing_newline=True
    )


def _acquired_file_to_dict(value: Av2AcquiredFile) -> dict[str, object]:
    return {
        "remote_uri": value.remote_uri,
        "relative_path": relative_path_text(value.relative_path),
        "size_bytes": value.size_bytes,
        "sha256": value.sha256,
        "disposition": value.disposition,
    }


def av2_acquisition_report_to_dict(
    report: Av2AcquisitionReport,
) -> dict[str, object]:
    """Return a fresh ordered JSON-compatible acquisition report."""
    if not isinstance(report, Av2AcquisitionReport):
        raise ValidationError("report must be Av2AcquisitionReport")
    return {
        "schema_version": report.schema_version,
        "plan_identity": report.plan_identity,
        "backend": report.backend,
        "backend_version": report.backend_version,
        "selected_scenario_count": report.selected_scenario_count,
        "candidate_count": report.candidate_count,
        "downloaded_file_count": report.downloaded_file_count,
        "reused_file_count": report.reused_file_count,
        "downloaded_bytes": report.downloaded_bytes,
        "reused_bytes": report.reused_bytes,
        "total_local_bytes": report.total_local_bytes,
        "listing_seconds": report.listing_seconds,
        "download_seconds": report.download_seconds,
        "acquired_files": [
            _acquired_file_to_dict(item) for item in report.acquired_files
        ],
    }


def av2_acquisition_report_to_canonical_json(
    report: Av2AcquisitionReport,
) -> str:
    """Serialize an acquisition report as canonical JSON."""
    return canonical_json_text(
        av2_acquisition_report_to_dict(report), trailing_newline=True
    )


def _remote_object_from_value(value: object) -> Av2RemoteObject:
    mapping = _exact_mapping(value, _REMOTE_OBJECT_FIELDS, "remote object")
    return Av2RemoteObject(
        remote_uri=cast(Any, mapping["remote_uri"]),
        relative_key=cast(Any, mapping["relative_key"]),
        size_bytes=cast(Any, mapping["size_bytes"]),
        etag=cast(Any, mapping["etag"]),
    )


def _remote_pair_from_value(value: object) -> Av2RemoteScenarioPair:
    mapping = _exact_mapping(value, _REMOTE_PAIR_FIELDS, "remote scenario pair")
    return Av2RemoteScenarioPair(
        source_scenario_id=cast(Any, mapping["source_scenario_id"]),
        motion=_remote_object_from_value(mapping["motion"]),
        vector_map=_remote_object_from_value(mapping["vector_map"]),
    )


def av2_acquisition_plan_from_dict(
    value: Mapping[str, object],
) -> Av2AcquisitionPlan:
    """Strictly deserialize an acquisition plan mapping."""
    mapping = _exact_mapping(value, _PLAN_FIELDS, "acquisition plan")
    selected = mapping["selected_scenarios"]
    if not isinstance(selected, list):
        raise SchemaError("selected_scenarios must be a JSON array")
    try:
        return Av2AcquisitionPlan(
            schema_version=cast(Any, mapping["schema_version"]),
            dataset_id=cast(Any, mapping["dataset_id"]),
            dataset_version=cast(Any, mapping["dataset_version"]),
            remote_root=cast(Any, mapping["remote_root"]),
            partition=cast(Any, mapping["partition"]),
            root_seed=cast(Any, mapping["root_seed"]),
            assignment_namespace=cast(Any, mapping["assignment_namespace"]),
            candidate_count=cast(Any, mapping["candidate_count"]),
            selected_scenarios=tuple(
                _remote_pair_from_value(item) for item in selected
            ),
            remote_catalog_identity=cast(Any, mapping["remote_catalog_identity"]),
        )
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def _acquired_file_from_value(value: object) -> Av2AcquiredFile:
    mapping = _exact_mapping(value, _ACQUIRED_FILE_FIELDS, "acquired file")
    return Av2AcquiredFile(
        remote_uri=cast(Any, mapping["remote_uri"]),
        relative_path=cast(Any, mapping["relative_path"]),
        size_bytes=cast(Any, mapping["size_bytes"]),
        sha256=cast(Any, mapping["sha256"]),
        disposition=cast(Any, mapping["disposition"]),
    )


def av2_acquisition_report_from_dict(
    value: Mapping[str, object],
) -> Av2AcquisitionReport:
    """Strictly deserialize an acquisition report mapping."""
    mapping = _exact_mapping(value, _REPORT_FIELDS, "acquisition report")
    acquired = mapping["acquired_files"]
    if not isinstance(acquired, list):
        raise SchemaError("acquired_files must be a JSON array")
    try:
        return Av2AcquisitionReport(
            schema_version=cast(Any, mapping["schema_version"]),
            plan_identity=cast(Any, mapping["plan_identity"]),
            backend=cast(Any, mapping["backend"]),
            backend_version=cast(Any, mapping["backend_version"]),
            selected_scenario_count=cast(Any, mapping["selected_scenario_count"]),
            candidate_count=cast(Any, mapping["candidate_count"]),
            downloaded_file_count=cast(Any, mapping["downloaded_file_count"]),
            reused_file_count=cast(Any, mapping["reused_file_count"]),
            downloaded_bytes=cast(Any, mapping["downloaded_bytes"]),
            reused_bytes=cast(Any, mapping["reused_bytes"]),
            total_local_bytes=cast(Any, mapping["total_local_bytes"]),
            listing_seconds=cast(Any, mapping["listing_seconds"]),
            download_seconds=cast(Any, mapping["download_seconds"]),
            acquired_files=tuple(_acquired_file_from_value(item) for item in acquired),
        )
    except (TypeError, ValidationError) as error:
        raise SchemaError(str(error)) from None


def _json_mapping(text: str, label: str) -> Mapping[str, object]:
    if not isinstance(text, str):
        raise SchemaError(f"{label} JSON must be text")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise SchemaError(f"{label} JSON is malformed") from None
    if not isinstance(parsed, Mapping):
        raise SchemaError(f"{label} JSON root must be an object")
    return cast(Mapping[str, object], parsed)


def av2_acquisition_plan_from_json(text: str) -> Av2AcquisitionPlan:
    """Strictly parse acquisition plan JSON."""
    return av2_acquisition_plan_from_dict(_json_mapping(text, "acquisition plan"))


def av2_acquisition_report_from_json(text: str) -> Av2AcquisitionReport:
    """Strictly parse acquisition report JSON."""
    return av2_acquisition_report_from_dict(_json_mapping(text, "acquisition report"))


def av2_acquisition_plan_identity(plan: Av2AcquisitionPlan) -> str:
    """Return the canonical logical acquisition-plan identity."""
    return canonical_sha256(_PLAN_DOMAIN, av2_acquisition_plan_to_dict(plan))


def parse_s5cmd_listing(
    text: str,
    *,
    remote_root: str = AV2_OFFICIAL_MOTION_ROOT,
) -> tuple[Av2RemoteObject, ...]:
    """Parse structured s5cmd output, with its stable text form as fallback."""
    root = _official_remote_root(remote_root)
    if not isinstance(text, str):
        raise SchemaError("s5cmd listing must be text")
    objects: list[Av2RemoteObject] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.lstrip("\ufeff").strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                raise SchemaError(
                    f"malformed s5cmd JSON record at line {line_number}"
                ) from None
            if not isinstance(value, Mapping):
                raise SchemaError("s5cmd JSON record must be an object")
            record = cast(Mapping[str, object], value)
            record_type = record.get("type")
            if record_type == "directory":
                continue
            if record_type != "file":
                raise SchemaError("s5cmd record has an invalid type")
            uri = record.get("key")
            size = record.get("size")
            etag = record.get("etag")
        else:
            match = re.fullmatch(
                r"\S+\s+\S+\s+(\d+)\s+(s3://\S+)",
                line,
            )
            if match is None:
                raise SchemaError(f"malformed s5cmd text record at line {line_number}")
            size = int(match.group(1))
            uri = match.group(2)
            etag = None
        if not isinstance(uri, str) or not uri.startswith(root):
            raise SchemaError("s5cmd object is outside the configured remote root")
        try:
            objects.append(
                Av2RemoteObject(
                    remote_uri=uri,
                    relative_key=uri[len(root) :],
                    size_bytes=cast(Any, size),
                    etag=cast(Any, etag),
                )
            )
        except ValidationError as error:
            raise SchemaError(str(error)) from None
    return tuple(sorted(objects, key=lambda item: (item.relative_key, item.remote_uri)))


def parse_anonymous_s3_page(
    xml_bytes: bytes,
    *,
    remote_root: str = AV2_OFFICIAL_MOTION_ROOT,
) -> tuple[tuple[Av2RemoteObject, ...], tuple[str, ...], str | None]:
    """Parse one anonymous S3 ListObjectsV2 XML response."""
    root = _official_remote_root(remote_root)
    if not isinstance(xml_bytes, bytes):
        raise SchemaError("S3 listing page must be bytes")
    try:
        document = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        raise SchemaError("anonymous S3 listing XML is malformed") from None
    namespace_match = re.match(r"\{([^}]+)\}", document.tag)
    namespace = {"s3": namespace_match.group(1)} if namespace_match is not None else {}
    prefix = "s3:" if namespace else ""

    def find_text(node: ElementTree.Element, name: str) -> str | None:
        child = node.find(f"{prefix}{name}", namespace)
        return child.text if child is not None else None

    objects: list[Av2RemoteObject] = []
    root_key = root.removeprefix("s3://argoverse/")
    for content in document.findall(f"{prefix}Contents", namespace):
        key = find_text(content, "Key")
        size_text = find_text(content, "Size")
        etag_text = find_text(content, "ETag")
        if key is None or size_text is None:
            raise SchemaError("anonymous S3 object record is incomplete")
        if not key.startswith(root_key):
            raise SchemaError("anonymous S3 object is outside the remote root")
        try:
            size = int(size_text)
        except ValueError:
            raise SchemaError("anonymous S3 object size is invalid") from None
        relative = key[len(root_key) :]
        if not relative:
            continue
        try:
            objects.append(
                Av2RemoteObject(
                    remote_uri=f"s3://argoverse/{key}",
                    relative_key=relative,
                    size_bytes=size,
                    etag=etag_text.strip('"') if etag_text else None,
                )
            )
        except ValidationError as error:
            raise SchemaError(str(error)) from None
    common_prefixes: list[str] = []
    for common in document.findall(f"{prefix}CommonPrefixes", namespace):
        key = find_text(common, "Prefix")
        if key is None or not key.startswith(root_key):
            raise SchemaError("anonymous S3 common prefix is invalid")
        relative = key[len(root_key) :].strip("/")
        if relative:
            common_prefixes.append(relative)
    truncated_text = find_text(document, "IsTruncated")
    truncated = truncated_text is not None and truncated_text.casefold() == "true"
    token = find_text(document, "NextContinuationToken")
    if truncated and not token:
        raise SchemaError("truncated S3 listing is missing a continuation token")
    if not truncated:
        token = None
    return (
        tuple(sorted(objects, key=lambda item: item.relative_key)),
        tuple(sorted(set(common_prefixes))),
        token,
    )


def _s5cmd_partition_records(text: str, remote_root: str) -> tuple[str, ...]:
    partitions: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise SchemaError(
                f"malformed s5cmd root record at line {line_number}"
            ) from None
        if not isinstance(value, Mapping):
            raise SchemaError("s5cmd root record must be an object")
        record = cast(Mapping[str, object], value)
        if record.get("type") != "directory":
            continue
        key = record.get("key")
        if not isinstance(key, str) or not key.startswith(remote_root):
            raise SchemaError("s5cmd partition record is outside remote root")
        relative = key[len(remote_root) :].strip("/")
        if "/" not in relative and relative:
            partitions.append(_partition(relative))
    return tuple(sorted(set(partitions)))


def discover_av2_partition(partitions: Sequence[str]) -> str:
    """Choose the observed provider validation partition deterministically."""
    normalized = tuple(sorted({_partition(item) for item in partitions}))
    for preferred in ("val", "validation"):
        if preferred in normalized:
            return preferred
    raise ArtifactError(
        "official AV2 root does not expose a recognized validation partition"
    )


def build_remote_scenario_pairs(
    objects: Sequence[Av2RemoteObject],
    *,
    partition: str,
) -> tuple[Av2RemoteScenarioPair, ...]:
    """Return complete provider pairs in lexical scenario order."""
    normalized_partition = _partition(partition)
    indexed: dict[str, dict[str, Av2RemoteObject]] = {}
    for item in objects:
        if not isinstance(item, Av2RemoteObject):
            raise ValidationError("objects must contain Av2RemoteObject values")
        path = PurePosixPath(item.relative_key)
        if len(path.parts) != 3 or path.parts[0] != normalized_partition:
            continue
        scenario_id = _source_id(path.parts[1], "source scenario ID")
        motion_name = f"scenario_{scenario_id}.parquet"
        map_name = f"log_map_archive_{scenario_id}.json"
        kind: str | None = None
        if path.name.startswith("scenario_") and path.suffix == ".parquet":
            if path.name != motion_name:
                raise ValidationError(
                    "motion filename differs from its scenario directory"
                )
            kind = "motion"
        elif path.name.startswith("log_map_archive_") and path.suffix == ".json":
            if path.name != map_name:
                raise ValidationError(
                    "map filename differs from its scenario directory"
                )
            kind = "vector_map"
        if kind is None:
            continue
        values = indexed.setdefault(scenario_id, {})
        if kind in values:
            raise ValidationError(f"duplicate {kind} object for scenario")
        values[kind] = item
    pairs: list[Av2RemoteScenarioPair] = []
    for source_id in sorted(indexed):
        values = indexed[source_id]
        if "motion" not in values or "vector_map" not in values:
            continue
        pairs.append(
            Av2RemoteScenarioPair(
                source_scenario_id=source_id,
                motion=values["motion"],
                vector_map=values["vector_map"],
            )
        )
    return tuple(pairs)


def remote_catalog_identity(
    pairs: Sequence[Av2RemoteScenarioPair],
) -> str:
    """Return a canonical identity for the complete normalized provider catalog."""
    values = tuple(pairs)
    if not values or any(
        not isinstance(item, Av2RemoteScenarioPair) for item in values
    ):
        raise ValidationError("pairs must contain remote scenario pairs")
    ordered = tuple(sorted(values, key=lambda item: item.source_scenario_id))
    ids = tuple(item.source_scenario_id for item in ordered)
    if len(ids) != len(set(ids)):
        raise ValidationError("remote catalog scenario IDs must be unique")
    return canonical_sha256(
        _CATALOG_DOMAIN,
        [_remote_pair_to_dict(item) for item in ordered],
    )


def build_av2_acquisition_plan(
    pairs: Sequence[Av2RemoteScenarioPair],
    *,
    config: Av2AcquisitionConfig,
) -> Av2AcquisitionPlan:
    """Select an exact, listing-order-independent provider subset."""
    if not isinstance(config, Av2AcquisitionConfig):
        raise ValidationError("config must be Av2AcquisitionConfig")
    values = tuple(pairs)
    if any(not isinstance(item, Av2RemoteScenarioPair) for item in values):
        raise ValidationError("pairs must contain remote scenario pairs")
    ordered_catalog = tuple(sorted(values, key=lambda item: item.source_scenario_id))
    if len({item.source_scenario_id for item in ordered_catalog}) != len(
        ordered_catalog
    ):
        raise ValidationError("remote candidate scenario IDs must be unique")
    if len(ordered_catalog) < config.scenario_count:
        raise ResourceLimitError("insufficient complete AV2 provider scenario pairs")
    catalog_identity = remote_catalog_identity(ordered_catalog)
    ranked = tuple(
        sorted(
            ordered_catalog,
            key=lambda pair: (
                derive_seed(
                    config.root_seed,
                    "av2-provider-acquisition",
                    config.assignment_namespace,
                    pair.source_scenario_id,
                ),
                pair.source_scenario_id,
            ),
        )
    )
    return Av2AcquisitionPlan(
        schema_version=_SCHEMA_VERSION,
        dataset_id=_DATASET_ID,
        dataset_version=f"official-s3-{catalog_identity[:12]}",
        remote_root=config.remote_root,
        partition=config.partition,
        root_seed=config.root_seed,
        assignment_namespace=config.assignment_namespace,
        candidate_count=len(ordered_catalog),
        selected_scenarios=ranked[: config.scenario_count],
        remote_catalog_identity=catalog_identity,
    )


def _run(
    arguments: Sequence[str],
    *,
    timeout: float = _COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            tuple(arguments),
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ArtifactError(f"command failed to execute: {arguments[0]}") from error


def _s5cmd_version(executable: Path) -> str:
    result = _run((str(executable), "version"), timeout=30.0)
    if result.returncode != 0 or not result.stdout.strip():
        raise ArtifactError(
            f"s5cmd version failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _safe_extract_s5cmd(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            path = PurePosixPath(info.filename.replace("\\", "/"))
            mode = (info.external_attr >> 16) & 0o170000
            if path.is_absolute() or ".." in path.parts or mode == 0o120000:
                raise ArtifactError("official s5cmd archive contains an unsafe member")
        bundle.extractall(destination)
    executable_name = "s5cmd.exe" if os.name == "nt" else "s5cmd"
    matches = tuple(destination.rglob(executable_name))
    if len(matches) != 1 or not matches[0].is_file():
        raise ArtifactError("official s5cmd archive did not contain one executable")
    return matches[0]


def _install_official_s5cmd() -> Path:
    if os.name != "nt":
        raise ArtifactError("automatic official s5cmd installation supports Windows")
    tool_root = Path(tempfile.gettempdir()) / "kinematicweave-s5cmd"
    existing = tool_root / "s5cmd.exe"
    if existing.is_file():
        _s5cmd_version(existing)
        return existing
    request = urlrequest.Request(
        "https://api.github.com/repos/peak/s5cmd/releases/latest",
        headers={"User-Agent": "kinematicweave-av2-acquisition"},
    )
    try:
        with urlrequest.urlopen(request, timeout=30.0) as response:
            payload = json.load(response)
    except (OSError, ValueError, urlerror.URLError) as error:
        raise ArtifactError(
            "official s5cmd release metadata could not be read"
        ) from error
    if not isinstance(payload, Mapping):
        raise ArtifactError("official s5cmd release metadata is malformed")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ArtifactError("official s5cmd release has no asset list")
    candidates = [
        item
        for item in assets
        if isinstance(item, Mapping)
        and isinstance(item.get("name"), str)
        and cast(str, item["name"]).endswith("Windows-64bit.zip")
        and isinstance(item.get("browser_download_url"), str)
    ]
    if len(candidates) != 1:
        raise ArtifactError("official s5cmd Windows asset could not be identified")
    asset = cast(Mapping[str, object], candidates[0])
    url = cast(str, asset["browser_download_url"])
    archive = tool_root / cast(str, asset["name"])
    tool_root.mkdir(parents=True, exist_ok=True)
    partial = archive.with_name(f"{archive.name}.partial")
    download_request = urlrequest.Request(
        url, headers={"User-Agent": "kinematicweave-av2-acquisition"}
    )
    try:
        with (
            urlrequest.urlopen(download_request, timeout=60.0) as response,
            partial.open("wb") as stream,
        ):
            while chunk := response.read(_READ_CHUNK_SIZE):
                stream.write(chunk)
        os.replace(partial, archive)
    except (OSError, urlerror.URLError) as error:
        partial.unlink(missing_ok=True)
        raise ArtifactError("official s5cmd asset download failed") from error
    executable = _safe_extract_s5cmd(archive, tool_root)
    _s5cmd_version(executable)
    return executable


def _resolve_backend(requested: str) -> _Backend:
    if requested in {"auto", "s5cmd"}:
        errors: list[str] = []
        located = shutil.which("s5cmd")
        candidates = ((Path(located),) if located is not None else ()) + (
            Path(tempfile.gettempdir()) / "kinematicweave-s5cmd" / "s5cmd.exe",
        )
        for candidate in candidates:
            if candidate.is_file():
                try:
                    return _Backend(
                        name="s5cmd",
                        version=_s5cmd_version(candidate),
                        executable=candidate,
                    )
                except ArtifactError as error:
                    errors.append(str(error))
        try:
            executable = _install_official_s5cmd()
            return _Backend(
                name="s5cmd",
                version=_s5cmd_version(executable),
                executable=executable,
            )
        except ArtifactError as error:
            errors.append(str(error))
            if requested == "s5cmd":
                raise ArtifactError("; ".join(errors)) from None
    return _Backend(
        name="anonymous_s3_http",
        version="python-stdlib-urllib-listobjectsv2-v1",
    )


def _s5cmd_list(backend: _Backend, uri: str) -> str:
    if backend.executable is None:
        raise ArtifactError("s5cmd backend has no executable")
    result = _run(
        (
            str(backend.executable),
            "--json",
            "--no-sign-request",
            "ls",
            uri,
        )
    )
    if result.returncode != 0:
        raise ArtifactError(
            f"s5cmd ls failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _anonymous_s3_url(
    *,
    prefix: str,
    delimiter: str | None = None,
    continuation_token: str | None = None,
) -> str:
    query: dict[str, str] = {"list-type": "2", "prefix": prefix}
    if delimiter is not None:
        query["delimiter"] = delimiter
    if continuation_token is not None:
        query["continuation-token"] = continuation_token
    return f"https://argoverse.s3.amazonaws.com/?{urlparse.urlencode(query)}"


def _http_page(url: str) -> bytes:
    request = urlrequest.Request(url, headers={"User-Agent": "kinematicweave-av2-acquisition"})
    try:
        with urlrequest.urlopen(request, timeout=60.0) as response:
            if response.status != 200:
                raise ArtifactError(
                    f"anonymous S3 listing returned HTTP {response.status}"
                )
            declared = response.headers.get("Content-Length")
            payload = cast(bytes, response.read(16 * 1024 * 1024))
            if response.read(1):
                raise ResourceLimitError("anonymous S3 listing page exceeds 16 MiB")
    except (OSError, urlerror.URLError) as error:
        raise ArtifactError(f"anonymous S3 listing failed: {url}") from error
    if declared is not None and len(payload) != int(declared):
        raise ArtifactError("anonymous S3 listing content length differs")
    return payload


def _http_partitions(remote_root: str) -> tuple[str, ...]:
    root_key = remote_root.removeprefix("s3://argoverse/")
    payload = _http_page(_anonymous_s3_url(prefix=root_key, delimiter="/"))
    _objects, prefixes, token = parse_anonymous_s3_page(
        payload, remote_root=remote_root
    )
    if token is not None:
        raise ArtifactError("unexpected paginated AV2 root partition listing")
    return tuple(item.split("/", 1)[0] for item in prefixes)


def _http_objects(remote_root: str, partition: str) -> tuple[Av2RemoteObject, ...]:
    root_key = remote_root.removeprefix("s3://argoverse/")
    token: str | None = None
    objects: list[Av2RemoteObject] = []
    seen_tokens: set[str] = set()
    while True:
        payload = _http_page(
            _anonymous_s3_url(
                prefix=f"{root_key}{partition}/",
                continuation_token=token,
            )
        )
        page, _prefixes, next_token = parse_anonymous_s3_page(
            payload, remote_root=remote_root
        )
        objects.extend(page)
        if next_token is None:
            break
        if next_token in seen_tokens:
            raise ArtifactError("anonymous S3 pagination token repeated")
        seen_tokens.add(next_token)
        token = next_token
    return tuple(sorted(objects, key=lambda item: item.relative_key))


def inspect_av2_remote(
    config: Av2AcquisitionConfig,
) -> tuple[Av2AcquisitionPlan, str, str, float]:
    """Discover the official layout and return plan, backend, version, and duration."""
    pairs, backend_name, backend_version, listing_seconds = inspect_av2_remote_catalog(
        config
    )
    plan = build_av2_acquisition_plan(pairs, config=config)
    return plan, backend_name, backend_version, listing_seconds


def inspect_av2_remote_catalog(
    config: Av2AcquisitionConfig,
) -> tuple[tuple[Av2RemoteScenarioPair, ...], str, str, float]:
    """Return the complete normalized official partition catalog and backend."""
    if not isinstance(config, Av2AcquisitionConfig):
        raise ValidationError("config must be Av2AcquisitionConfig")
    started = time.perf_counter()
    backend = _resolve_backend(config.backend)
    try:
        if backend.name == "s5cmd":
            root_listing = _s5cmd_list(backend, config.remote_root)
            partitions = _s5cmd_partition_records(root_listing, config.remote_root)
            observed_partition = discover_av2_partition(partitions)
            if config.partition != observed_partition:
                if config.partition not in partitions:
                    raise ArtifactError(
                        "requested partition is absent from the official AV2 root"
                    )
                observed_partition = config.partition
            listing = _s5cmd_list(
                backend, f"{config.remote_root}{observed_partition}/*"
            )
            objects = parse_s5cmd_listing(listing, remote_root=config.remote_root)
        else:
            partitions = _http_partitions(config.remote_root)
            observed_partition = discover_av2_partition(partitions)
            if config.partition != observed_partition:
                if config.partition not in partitions:
                    raise ArtifactError(
                        "requested partition is absent from the official AV2 root"
                    )
                observed_partition = config.partition
            objects = _http_objects(config.remote_root, observed_partition)
    except ArtifactError:
        if config.backend != "auto" or backend.name != "s5cmd":
            raise
        backend = _Backend(
            name="anonymous_s3_http",
            version="python-stdlib-urllib-listobjectsv2-v1",
        )
        partitions = _http_partitions(config.remote_root)
        observed_partition = discover_av2_partition(partitions)
        if config.partition != observed_partition:
            if config.partition not in partitions:
                raise ArtifactError(
                    "requested partition is absent from the official AV2 root"
                ) from None
            observed_partition = config.partition
        objects = _http_objects(config.remote_root, observed_partition)
    pairs = build_remote_scenario_pairs(objects, partition=observed_partition)
    if len(pairs) < config.scenario_count:
        raise ResourceLimitError("insufficient complete AV2 provider scenario pairs")
    return pairs, backend.name, backend.version, time.perf_counter() - started


def _assert_no_symlink_components(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ArtifactError("provider destination contains a symbolic link")


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_READ_CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise ArtifactError(f"provider file cannot be read: {path.name}") from error
    return size, digest.hexdigest()


def _s3_https_url(remote_uri: str) -> str:
    key = remote_uri.removeprefix("s3://argoverse/")
    if key == remote_uri:
        raise ValidationError("remote_uri must use the official Argoverse bucket")
    return f"https://argoverse.s3.amazonaws.com/{urlparse.quote(key, safe='/')}"


def _download_http(remote: Av2RemoteObject, partial: Path) -> None:
    request = urlrequest.Request(
        _s3_https_url(remote.remote_uri),
        headers={"User-Agent": "kinematicweave-av2-acquisition"},
    )
    try:
        with (
            urlrequest.urlopen(request, timeout=120.0) as response,
            partial.open("wb") as stream,
        ):
            if response.status != 200:
                raise ArtifactError(
                    f"provider download returned HTTP {response.status}"
                )
            declared = response.headers.get("Content-Length")
            if declared is None or int(declared) != remote.size_bytes:
                raise ArtifactError("provider HTTP content length differs")
            size = 0
            while chunk := response.read(_READ_CHUNK_SIZE):
                stream.write(chunk)
                size += len(chunk)
            if size != remote.size_bytes:
                raise ArtifactError("provider download byte count differs")
    except (OSError, ValueError, urlerror.URLError) as error:
        raise ArtifactError("anonymous provider object download failed") from error


def _download_s5cmd(
    executable: Path,
    remote: Av2RemoteObject,
    partial: Path,
) -> None:
    result = _run(
        (
            str(executable),
            "--no-sign-request",
            "cp",
            remote.remote_uri,
            str(partial),
        )
    )
    if result.returncode != 0:
        raise ArtifactError(
            f"s5cmd cp failed ({result.returncode}): {result.stderr.strip()}"
        )


def _download_with_retry(
    remote: Av2RemoteObject,
    destination: Path,
    *,
    backend: _Backend,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, str]:
    partial = destination.with_name(f"{destination.name}.partial")
    last_error: ArtifactError | None = None
    for attempt in range(_DOWNLOAD_ATTEMPTS):
        partial.unlink(missing_ok=True)
        try:
            if backend.name == "s5cmd":
                if backend.executable is None:
                    raise ArtifactError("s5cmd backend has no executable")
                _download_s5cmd(backend.executable, remote, partial)
            else:
                _download_http(remote, partial)
            if partial.is_symlink() or not partial.is_file():
                raise ArtifactError("provider download did not create a regular file")
            size, digest = _hash_file(partial)
            if size != remote.size_bytes:
                raise ArtifactError("provider download size differs")
            os.replace(partial, destination)
            return size, digest
        except ArtifactError as error:
            last_error = error
            partial.unlink(missing_ok=True)
            if attempt + 1 < _DOWNLOAD_ATTEMPTS:
                sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def acquire_av2_plan(
    repository_root: Path,
    config: Av2AcquisitionConfig,
    plan: Av2AcquisitionPlan,
    *,
    backend_name: str,
    backend_version: str,
    listing_seconds: float,
    prior_sha256: Mapping[Path, str] | None = None,
    download: Callable[[Av2RemoteObject, Path, _Backend], tuple[int, str]]
    | None = None,
) -> Av2AcquisitionReport:
    """Download or manifest-reuse every selected provider object."""
    if not isinstance(repository_root, Path):
        raise ArtifactError("repository_root must be a Path")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    if not root.is_dir():
        raise ArtifactError("repository_root must be a directory")
    if not isinstance(config, Av2AcquisitionConfig):
        raise ValidationError("config must be Av2AcquisitionConfig")
    if not isinstance(plan, Av2AcquisitionPlan):
        raise ValidationError("plan must be Av2AcquisitionPlan")
    if (
        plan.remote_root != config.remote_root
        or plan.partition != config.partition
        or len(plan.selected_scenarios) != config.scenario_count
        or plan.root_seed != config.root_seed
        or plan.assignment_namespace != config.assignment_namespace
    ):
        raise ValidationError("acquisition plan differs from configuration")
    if backend_name not in {"s5cmd", "anonymous_s3_http"}:
        raise ValidationError("backend_name must identify a concrete backend")
    backend = _resolve_backend(backend_name)
    if backend.name != backend_name:
        raise ArtifactError("resolved acquisition backend differs")
    if backend_version != backend.version:
        raise ArtifactError("acquisition backend version changed after listing")
    check_disk_space(
        root,
        required_bytes=plan.selected_remote_bytes * 4,
        reserve_fraction=0.15,
    )
    destination_root = root / config.local_relative_root / plan.dataset_version
    destination_root.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(root, destination_root)
    known = {} if prior_sha256 is None else dict(prior_sha256)
    acquired: list[Av2AcquiredFile] = []
    started = time.perf_counter()

    def transfer(
        remote: Av2RemoteObject, target: Path, selected_backend: _Backend
    ) -> tuple[int, str]:
        if download is not None:
            return download(remote, target, selected_backend)
        return _download_with_retry(remote, target, backend=selected_backend)

    selected_objects = tuple(
        item
        for pair in plan.selected_scenarios
        for item in (pair.motion, pair.vector_map)
    )
    for remote in sorted(selected_objects, key=lambda item: item.relative_key):
        relative = (
            config.local_relative_root
            / plan.dataset_version
            / Path(remote.relative_key)
        )
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(root, destination.parent)
        if destination.is_symlink():
            raise ArtifactError("provider destination must not be a symbolic link")
        expected_digest = known.get(relative)
        disposition = "downloaded"
        if expected_digest is not None and destination.is_file():
            size, digest = _hash_file(destination)
            if size == remote.size_bytes and digest == expected_digest:
                disposition = "reused"
            else:
                size, digest = transfer(remote, destination, backend)
        else:
            size, digest = transfer(remote, destination, backend)
        if size != remote.size_bytes:
            raise ArtifactError("acquired provider size differs from remote catalog")
        acquired.append(
            Av2AcquiredFile(
                remote_uri=remote.remote_uri,
                relative_path=relative,
                size_bytes=size,
                sha256=digest,
                disposition=disposition,
            )
        )
    report = Av2AcquisitionReport(
        schema_version=_SCHEMA_VERSION,
        plan_identity=av2_acquisition_plan_identity(plan),
        backend=backend.name,
        backend_version=backend.version,
        selected_scenario_count=len(plan.selected_scenarios),
        candidate_count=plan.candidate_count,
        downloaded_file_count=sum(
            item.disposition == "downloaded" for item in acquired
        ),
        reused_file_count=sum(item.disposition == "reused" for item in acquired),
        downloaded_bytes=sum(
            item.size_bytes for item in acquired if item.disposition == "downloaded"
        ),
        reused_bytes=sum(
            item.size_bytes for item in acquired if item.disposition == "reused"
        ),
        total_local_bytes=sum(item.size_bytes for item in acquired),
        listing_seconds=listing_seconds,
        download_seconds=time.perf_counter() - started,
        acquired_files=tuple(
            sorted(acquired, key=lambda item: relative_path_text(item.relative_path))
        ),
    )
    verify_acquired_files(root, report)
    return report


def verify_acquired_files(
    repository_root: Path,
    report: Av2AcquisitionReport,
) -> None:
    """Verify every acquired provider file against report size and SHA-256."""
    if not isinstance(report, Av2AcquisitionReport):
        raise ValidationError("report must be Av2AcquisitionReport")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as error:
        raise ArtifactError("repository_root does not exist") from error
    for item in report.acquired_files:
        path = root / item.relative_path
        _assert_no_symlink_components(root, path)
        if not path.is_file() or path.is_symlink():
            raise ArtifactError("acquired provider file is missing or unsafe")
        size, digest = _hash_file(path)
        if size != item.size_bytes or digest != item.sha256:
            raise ArtifactError("acquired provider file differs from its report")
