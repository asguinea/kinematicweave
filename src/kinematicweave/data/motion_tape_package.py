"""Verification, persistence, and read-only access for motion-tape packages."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from itertools import pairwise
import json
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.data import parquet_io
from kinematicweave.artifact_store import (
    RunDirectory,
    RunDirectoryState,
    WrittenArtifact,
    atomic_write_text,
    finalize_run_directory,
    inspect_run_directory,
    prepare_run_directory,
)
from kinematicweave.canonical import canonical_json_text
from kinematicweave.codecs.exact import replay_track
from kinematicweave.codecs.velocity_bounded import (
    ENCODER_NAME,
    ENCODER_VERSION,
    VelocityBoundedCodecConfig,
    velocity_bounded_encoder_parameters_identity,
)
from kinematicweave.data.parquet_io import (
    CanonicalParquetArtifact,
    iter_canonical_parquet_batches,
)
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    canonical_schema_names,
    get_schema_definition,
    schema_fingerprint,
    validate_arrow_schema,
)
from kinematicweave.domain.motion_tape import (
    CanonicalArtifactPart,
    CanonicalTableArtifactReference,
    ProceduralMotionCounts,
    ProceduralMotionPackage,
    SourceInputIdentity,
    create_procedural_motion_package,
    procedural_motion_package_from_json,
    procedural_motion_package_to_json,
)
from kinematicweave.domain.procedural import (
    ProceduralSegment,
    ProceduralTape,
    ProceduralTrack,
    ReplayState,
    procedural_segment_from_dict,
    procedural_track_from_dict,
)
from kinematicweave.domain.records import ScenarioRecord
from kinematicweave.domain.semantic import (
    MotionEvent,
    SemanticWaypoint,
    motion_event_from_dict,
    semantic_waypoint_from_dict,
)
from kinematicweave.domain.shared_motion import (
    MotionCategory,
    RouteTemplate,
    RouteTemplateMembership,
    motion_category_from_dict,
    route_template_from_dict,
    route_template_membership_from_dict,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    semantic_motion_configuration_identity,
)
from kinematicweave.layout.shared_motion import (
    SharedMotionConfig,
    shared_motion_configuration_identity,
)
from kinematicweave.paths import normalize_relative_path

__all__ = [
    "PackageValidationResult",
    "ProceduralMotionPackageArtifacts",
    "ProceduralMotionTapeReader",
    "artifact_part_from_canonical",
    "artifact_part_from_path",
    "build_procedural_motion_package",
    "canonical_table_reference",
    "load_procedural_motion_package",
    "materialize_procedural_motion_package",
    "validate_procedural_motion_package",
    "verify_procedural_motion_package_artifacts",
]

_PACKAGE_PATH = Path("procedural_motion_package.json")
_CONTRACT_PATH = Path("phase3_contract_snapshot.json")
_SUMMARY_PATH = Path("phase3_summary.md")
_BUNDLE_PATHS = (_PACKAGE_PATH, _CONTRACT_PATH, _SUMMARY_PATH)
_CODEC_CONFIG = VelocityBoundedCodecConfig()
_SEMANTIC_CONFIG = SemanticMotionConfig()
_SHARED_CONFIG = SharedMotionConfig()

_COUNT_SCHEMAS = {
    CanonicalSchemaName.SCENARIO_MANIFEST: "scenarios",
    CanonicalSchemaName.PROCEDURAL_TRACKS: "procedural_tracks",
    CanonicalSchemaName.PROCEDURAL_SEGMENTS: "procedural_segments",
    CanonicalSchemaName.SEMANTIC_WAYPOINTS: "semantic_waypoints",
    CanonicalSchemaName.MOTION_EVENTS: "motion_events",
    CanonicalSchemaName.MOTION_CATEGORIES: "motion_categories",
    CanonicalSchemaName.ROUTE_TEMPLATES: "route_templates",
    CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS: ("route_template_memberships"),
}


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise ArtifactError(f"cannot read artifact: {path.name}") from error
    return size, digest.hexdigest()


def _contained_path(repository_root: Path, relative_path: str | Path) -> Path:
    if not isinstance(repository_root, Path):
        raise ValidationError("repository_root must be a Path")
    try:
        root = repository_root.resolve(strict=True)
        relative = normalize_relative_path(relative_path)
        path = (root / relative).resolve(strict=True)
    except (OSError, ValidationError) as error:
        raise ArtifactError("package artifact path is invalid or missing") from error
    if path == root or not path.is_relative_to(root):
        raise ArtifactError("package artifact resolves outside repository")
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("package artifact must be a regular non-symlink file")
    return path


def artifact_part_from_canonical(
    artifact: CanonicalParquetArtifact,
) -> CanonicalArtifactPart:
    """Convert verified canonical artifact metadata to a package reference."""
    if not isinstance(artifact, parquet_io.CanonicalParquetArtifact):
        raise ValidationError("artifact must be CanonicalParquetArtifact")
    return CanonicalArtifactPart(
        repository_relative_path=artifact.written_artifact.relative_path.as_posix(),
        size_bytes=artifact.written_artifact.size_bytes,
        sha256=artifact.written_artifact.content_checksum,
        canonical_schema_name=artifact.schema_name.value,
        schema_fingerprint=artifact.schema_fingerprint,
        row_count=artifact.row_count,
    )


def artifact_part_from_path(
    repository_root: Path,
    relative_path: str | Path,
    schema_name: CanonicalSchemaName,
) -> CanonicalArtifactPart:
    """Inspect an existing immutable canonical Parquet file for package use."""
    if not isinstance(schema_name, CanonicalSchemaName):
        raise ValidationError("schema_name must use CanonicalSchemaName")
    path = _contained_path(repository_root, relative_path)
    try:
        parquet = pq.ParquetFile(path)
        validate_arrow_schema(parquet.schema_arrow, schema_name)
        row_count = parquet.metadata.num_rows
    except SchemaError:
        raise
    except Exception as error:
        raise ArtifactError(
            f"invalid canonical Parquet artifact: {path.name}"
        ) from error
    size, checksum = _hash_file(path)
    return CanonicalArtifactPart(
        repository_relative_path=path.relative_to(repository_root.resolve()).as_posix(),
        size_bytes=size,
        sha256=checksum,
        canonical_schema_name=schema_name.value,
        schema_fingerprint=schema_fingerprint(schema_name),
        row_count=row_count,
    )


def canonical_table_reference(
    repository_root: Path,
    schema_name: CanonicalSchemaName,
    artifacts: Sequence[CanonicalArtifactPart],
) -> CanonicalTableArtifactReference:
    """Create one logical table with parts sorted by frozen canonical keys."""
    if not isinstance(schema_name, CanonicalSchemaName):
        raise ValidationError("schema_name must use CanonicalSchemaName")
    definition = get_schema_definition(schema_name)

    def part_key(part: CanonicalArtifactPart) -> tuple[bool, tuple[object, ...], str]:
        first_key: tuple[object, ...] = ()
        for batch in iter_canonical_parquet_batches(
            repository_root,
            (part.repository_relative_path,),
            schema_name,
            batch_size=1,
        ):
            row = batch.to_pylist()[0]
            first_key = tuple(row[field] for field in definition.canonical_order)
            break
        return (not first_key, first_key, part.repository_relative_path)

    ordered = tuple(sorted(artifacts, key=part_key))
    return CanonicalTableArtifactReference(
        canonical_schema_name=schema_name.value,
        schema_fingerprint=schema_fingerprint(schema_name),
        row_count=sum(item.row_count for item in ordered),
        artifacts=ordered,
    )


def build_procedural_motion_package(
    *,
    dataset_id: str,
    dataset_version: str,
    source_validation_report_identity: str,
    scenario_ids: Sequence[str],
    tape_ids: Sequence[str],
    canonical_table_artifacts: Sequence[CanonicalTableArtifactReference],
    source_input_identities: Sequence[SourceInputIdentity],
) -> ProceduralMotionPackage:
    """Build the frozen package using the accepted Phase 3 configurations."""
    by_name = {item.canonical_schema_name: item for item in canonical_table_artifacts}
    try:
        count_values = {
            count_name: by_name[schema_name.value].row_count
            for schema_name, count_name in _COUNT_SCHEMAS.items()
        }
    except KeyError as error:
        raise ValidationError("a required count table is missing") from error
    counts = ProceduralMotionCounts(**count_values)
    fingerprints = tuple(
        SourceInputIdentity(name=name.value, identity=schema_fingerprint(name))
        for name in sorted(canonical_schema_names(), key=lambda item: item.value)
    )
    return create_procedural_motion_package(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source_validation_report_identity=source_validation_report_identity,
        numerical_codec_name=ENCODER_NAME,
        numerical_codec_version=ENCODER_VERSION,
        numerical_codec_parameters_identity=(
            velocity_bounded_encoder_parameters_identity(_CODEC_CONFIG)
        ),
        semantic_detector_identity=semantic_motion_configuration_identity(
            _SEMANTIC_CONFIG
        ),
        shared_motion_configuration_identity=shared_motion_configuration_identity(
            _SHARED_CONFIG
        ),
        scenario_ids=tuple(sorted(scenario_ids)),
        tape_ids=tuple(sorted(tape_ids)),
        counts=counts,
        canonical_table_artifacts=canonical_table_artifacts,
        canonical_schema_fingerprints=fingerprints,
        source_input_identities=tuple(
            sorted(source_input_identities, key=lambda item: item.name)
        ),
    )


def _reference(
    package: ProceduralMotionPackage,
    schema_name: CanonicalSchemaName,
) -> CanonicalTableArtifactReference:
    try:
        return next(
            item
            for item in package.canonical_table_artifacts
            if item.canonical_schema_name == schema_name.value
        )
    except StopIteration:
        raise ArtifactError(f"package omits table {schema_name.value}") from None


def _rows(
    repository_root: Path,
    package: ProceduralMotionPackage,
    schema_name: CanonicalSchemaName,
) -> tuple[dict[str, Any], ...]:
    reference = _reference(package, schema_name)
    paths = tuple(item.repository_relative_path for item in reference.artifacts)
    rows: list[dict[str, Any]] = []
    for batch in iter_canonical_parquet_batches(
        repository_root, paths, schema_name, batch_size=8_192
    ):
        rows.extend(batch.to_pylist())
    if len(rows) != reference.row_count:
        raise ArtifactError(f"row count differs for {schema_name.value}")
    return tuple(rows)


def _verify_artifact_parts(
    repository_root: Path,
    package: ProceduralMotionPackage,
) -> int:
    total_bytes = 0
    for table in package.canonical_table_artifacts:
        expected = CanonicalSchemaName(table.canonical_schema_name)
        if table.schema_fingerprint != schema_fingerprint(expected):
            raise ArtifactError(f"schema fingerprint differs for {expected.value}")
        for part in table.artifacts:
            path = _contained_path(repository_root, part.repository_relative_path)
            size, checksum = _hash_file(path)
            if size != part.size_bytes or checksum != part.sha256:
                raise ArtifactError(f"artifact size or checksum differs: {path.name}")
            try:
                parquet = pq.ParquetFile(path)
                validate_arrow_schema(parquet.schema_arrow, expected)
                rows = parquet.metadata.num_rows
            except SchemaError:
                raise
            except Exception as error:
                raise ArtifactError(
                    f"cannot inspect package Parquet artifact: {path.name}"
                ) from error
            if rows != part.row_count:
                raise ArtifactError(f"artifact row count differs: {path.name}")
            total_bytes += size
    return total_bytes


def _unique(
    rows: Sequence[Mapping[str, object]], fields: Sequence[str], label: str
) -> None:
    keys = tuple(tuple(row[field] for field in fields) for row in rows)
    if len(keys) != len(set(keys)):
        raise ArtifactError(f"{label} primary keys are not unique")


@dataclass(frozen=True, slots=True)
class PackageValidationResult:
    """Deterministic summary of successful cross-layer package validation."""

    package_identity: str
    artifact_count: int
    referenced_bytes: int
    row_counts: tuple[tuple[str, int], ...]
    map_data_used_for_construction: bool


def validate_procedural_motion_package(
    repository_root: Path,
    package: ProceduralMotionPackage,
) -> PackageValidationResult:
    """Verify frozen schemas, bytes, identities, and all cross-layer references."""
    if not isinstance(package, ProceduralMotionPackage):
        raise ValidationError("package must be ProceduralMotionPackage")
    expected_names = canonical_schema_names()
    actual_names = tuple(
        CanonicalSchemaName(item.canonical_schema_name)
        for item in package.canonical_table_artifacts
    )
    if actual_names != expected_names:
        raise ArtifactError("package canonical table registry order differs")
    expected_fingerprints = tuple(
        (name.value, schema_fingerprint(name))
        for name in sorted(expected_names, key=lambda item: item.value)
    )
    actual_fingerprints = tuple(
        (item.name, item.identity) for item in package.canonical_schema_fingerprints
    )
    if actual_fingerprints != expected_fingerprints:
        raise ArtifactError("package schema fingerprint snapshot differs")
    if (
        package.numerical_codec_name != ENCODER_NAME
        or package.numerical_codec_version != ENCODER_VERSION
        or package.numerical_codec_parameters_identity
        != velocity_bounded_encoder_parameters_identity(_CODEC_CONFIG)
        or package.semantic_detector_identity
        != semantic_motion_configuration_identity(_SEMANTIC_CONFIG)
        or package.shared_motion_configuration_identity
        != shared_motion_configuration_identity(_SHARED_CONFIG)
    ):
        raise ArtifactError("package Phase 3 method identity differs")

    referenced_bytes = _verify_artifact_parts(repository_root, package)
    tables = {name: _rows(repository_root, package, name) for name in expected_names}
    for name in expected_names:
        definition = get_schema_definition(name)
        _unique(tables[name], definition.primary_key, name.value)

    scenarios = tables[CanonicalSchemaName.SCENARIO_MANIFEST]
    frames = tables[CanonicalSchemaName.COORDINATE_FRAME_METADATA]
    agents = tables[CanonicalSchemaName.AGENT_METADATA]
    samples = tables[CanonicalSchemaName.TRAJECTORY_SAMPLES]
    maps = tables[CanonicalSchemaName.VECTOR_MAP_ELEMENTS]
    tapes = tables[CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST]
    tracks = tables[CanonicalSchemaName.PROCEDURAL_TRACKS]
    segments = tables[CanonicalSchemaName.PROCEDURAL_SEGMENTS]
    waypoints = tables[CanonicalSchemaName.SEMANTIC_WAYPOINTS]
    events = tables[CanonicalSchemaName.MOTION_EVENTS]
    categories = tables[CanonicalSchemaName.MOTION_CATEGORIES]
    templates = tables[CanonicalSchemaName.ROUTE_TEMPLATES]
    memberships = tables[CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS]

    scenario_by_id = {str(row["scenario_id"]): row for row in scenarios}
    frame_keys = {
        (str(row["scenario_id"]), str(row["coordinate_frame_id"])) for row in frames
    }
    agent_keys = {(str(row["scenario_id"]), str(row["agent_id"])) for row in agents}
    trajectory_keys = {
        (
            str(row["scenario_id"]),
            str(row["agent_id"]),
            str(row["trajectory_id"]),
        )
        for row in samples
    }
    if tuple(sorted(scenario_by_id)) != tuple(package.scenario_ids):
        raise ArtifactError("package scenario identifiers differ from source table")
    if any(
        row["dataset_id"] != package.dataset_id
        or row["dataset_version"] != package.dataset_version
        for row in scenarios
    ):
        raise ArtifactError("canonical scenario dataset identity differs")
    if any(str(row["scenario_id"]) not in scenario_by_id for row in maps):
        raise ArtifactError("map element references an unknown scenario")

    tape_by_id = {str(row["tape_id"]): row for row in tapes}
    if tuple(sorted(tape_by_id)) != tuple(package.tape_ids):
        raise ArtifactError("package tape identifiers differ from tape manifests")
    if len(tapes) != len(scenarios):
        raise ArtifactError("each scenario must have exactly one tape")
    for row in tapes:
        scenario_id = str(row["scenario_id"])
        if (
            scenario_id not in scenario_by_id
            or (
                scenario_id,
                str(row["coordinate_frame_id"]),
            )
            not in frame_keys
        ):
            raise ArtifactError("tape scenario or frame reference does not resolve")
        if (
            row["source_dataset_id"] != package.dataset_id
            or row["source_dataset_version"] != package.dataset_version
            or row["source_validation_report_identity"]
            != package.source_validation_report_identity
            or row["encoder_name"] != package.numerical_codec_name
            or row["encoder_version"] != package.numerical_codec_version
            or row["encoder_parameters_identity"]
            != package.numerical_codec_parameters_identity
        ):
            raise ArtifactError("tape package identity differs across layers")

    track_by_id = {str(row["procedural_track_id"]): row for row in tracks}
    for row in tracks:
        tape = tape_by_id.get(str(row["tape_id"]))
        if tape is None or row["scenario_id"] != tape["scenario_id"]:
            raise ArtifactError("procedural track tape reference does not resolve")
        key = (
            str(row["scenario_id"]),
            str(row["agent_id"]),
            str(row["trajectory_id"]),
        )
        if key not in trajectory_keys or key[:2] not in agent_keys:
            raise ArtifactError("procedural track source reference does not resolve")

    segments_by_track: dict[str, list[Mapping[str, object]]] = {}
    for row in segments:
        track_id = str(row["procedural_track_id"])
        track = track_by_id.get(track_id)
        if track is None or row["tape_id"] != track["tape_id"]:
            raise ArtifactError("procedural segment track reference does not resolve")
        segments_by_track.setdefault(track_id, []).append(row)
    for track_id, track in track_by_id.items():
        values = segments_by_track.get(track_id, [])
        if len(values) != track["segment_count"]:
            raise ArtifactError("procedural track segment_count differs")
        run_indices = {cast(int, row["run_index"]) for row in values}
        if run_indices != set(range(int(track["run_count"]))):
            raise ArtifactError("procedural track run_count differs")
    for tape_id, tape in tape_by_id.items():
        owned_tracks = [row for row in tracks if row["tape_id"] == tape_id]
        if (
            len(owned_tracks) != tape["track_count"]
            or sum(int(row["segment_count"]) for row in owned_tracks)
            != tape["segment_count"]
            or sum(int(row["source_sample_count"]) for row in owned_tracks)
            != tape["source_sample_count"]
            or sum(int(row["valid_sample_count"]) for row in owned_tracks)
            != tape["encoded_valid_sample_count"]
        ):
            raise ArtifactError("procedural tape aggregate counts differ")

    waypoint_by_id = {str(row["waypoint_id"]): row for row in waypoints}
    waypoint_tracks = {str(row["procedural_track_id"]) for row in waypoints}
    if waypoint_tracks != set(track_by_id):
        raise ArtifactError("semantic tracks differ from procedural tracks")
    for row in waypoints:
        track = track_by_id.get(str(row["procedural_track_id"]))
        if track is None or row["tape_id"] != track["tape_id"]:
            raise ArtifactError("semantic waypoint track reference does not resolve")

    event_by_id = {str(row["event_id"]): row for row in events}
    for row in events:
        track_id = str(row["procedural_track_id"])
        track = track_by_id.get(track_id)
        if track is None or row["tape_id"] != track["tape_id"]:
            raise ArtifactError("motion event track reference does not resolve")
        for field_name in (
            "start_waypoint_id",
            "anchor_waypoint_id",
            "end_waypoint_id",
        ):
            waypoint = waypoint_by_id.get(str(row[field_name]))
            if waypoint is None or waypoint["procedural_track_id"] != track_id:
                raise ArtifactError("motion event waypoint reference does not resolve")
    for row in waypoints:
        track_id = row["procedural_track_id"]
        for event_id in row["related_event_ids"]:
            event = event_by_id.get(str(event_id))
            if event is None or event["procedural_track_id"] != track_id:
                raise ArtifactError("waypoint related event reference does not resolve")

    category_by_id = {str(row["category_id"]): row for row in categories}
    template_by_id = {str(row["template_id"]): row for row in templates}
    for row in templates:
        if str(row["category_id"]) not in category_by_id:
            raise ArtifactError("route template category reference does not resolve")
        if (
            str(row["representative_track_id"]) not in track_by_id
            or (
                str(row["scenario_id"]),
                str(row["coordinate_frame_id"]),
            )
            not in frame_keys
        ):
            raise ArtifactError("route template representative or frame is invalid")
        try:
            attributes = json.loads(str(row["semantic_attributes_json"]))
        except json.JSONDecodeError:
            raise ArtifactError("route template attributes are invalid") from None
        if attributes.get("map_data_used_for_construction") is not False:
            raise ArtifactError("map data was used for template construction")
        route_template_from_dict(row)

    members_by_template: dict[str, list[Mapping[str, object]]] = {}
    for row in memberships:
        template_id = str(row["template_id"])
        template = template_by_id.get(template_id)
        track = track_by_id.get(str(row["procedural_track_id"]))
        if (
            template is None
            or track is None
            or row["scenario_id"] != template["scenario_id"]
            or row["scenario_id"] != track["scenario_id"]
            or row["agent_id"] != track["agent_id"]
            or row["trajectory_id"] != track["trajectory_id"]
        ):
            raise ArtifactError("route-template membership reference is invalid")
        members_by_template.setdefault(template_id, []).append(row)
    for template_id, template in template_by_id.items():
        members = members_by_template.get(template_id, [])
        if len(members) != template["member_count"]:
            raise ArtifactError("route template member_count differs")
        if not any(
            row["procedural_track_id"] == template["representative_track_id"]
            for row in members
        ):
            raise ArtifactError("route template representative is not a member")
    for category_id, category in category_by_id.items():
        category_templates = [
            row for row in templates if row["category_id"] == category_id
        ]
        category_tracks = {
            str(member["procedural_track_id"])
            for template in category_templates
            for member in members_by_template.get(str(template["template_id"]), [])
        }
        if (
            len(category_templates) != category["template_count"]
            or str(category["representative_template_id"]) not in template_by_id
            or template_by_id[str(category["representative_template_id"])][
                "category_id"
            ]
            != category_id
            or len(category_tracks) != category["track_count"]
        ):
            raise ArtifactError("motion category aggregate counts differ")

    actual_counts = {
        count_name: len(tables[schema_name])
        for schema_name, count_name in _COUNT_SCHEMAS.items()
    }
    if package.counts.to_dict() != actual_counts:
        raise ArtifactError("package aggregate counts differ from canonical tables")
    row_counts = tuple((name.value, len(tables[name])) for name in expected_names)
    return PackageValidationResult(
        package_identity=package.package_identity,
        artifact_count=sum(
            len(item.artifacts) for item in package.canonical_table_artifacts
        ),
        referenced_bytes=referenced_bytes,
        row_counts=row_counts,
        map_data_used_for_construction=False,
    )


class ProceduralMotionTapeReader:
    """Immutable, deterministic query and replay view over a verified package."""

    __slots__ = (
        "_categories",
        "_events",
        "_memberships",
        "_package",
        "_repository_root",
        "_scenarios",
        "_segments",
        "_tapes",
        "_templates",
        "_tracks",
        "_waypoints",
    )

    def __init__(
        self,
        repository_root: Path,
        package: ProceduralMotionPackage,
        *,
        validate: bool = True,
    ) -> None:
        if validate:
            validate_procedural_motion_package(repository_root, package)
        self._repository_root = repository_root.resolve()
        self._package = package
        self._scenarios = {
            str(row["scenario_id"]): ScenarioRecord(**row)
            for row in _rows(
                repository_root, package, CanonicalSchemaName.SCENARIO_MANIFEST
            )
        }
        segment_rows = _rows(
            repository_root, package, CanonicalSchemaName.PROCEDURAL_SEGMENTS
        )
        segments = tuple(procedural_segment_from_dict(row) for row in segment_rows)
        self._segments = {
            track_id: tuple(
                sorted(
                    (item for item in segments if item.procedural_track_id == track_id),
                    key=lambda item: (item.run_index, item.segment_index),
                )
            )
            for track_id in {item.procedural_track_id for item in segments}
        }
        track_rows = _rows(
            repository_root, package, CanonicalSchemaName.PROCEDURAL_TRACKS
        )
        self._tracks = {
            str(row["procedural_track_id"]): procedural_track_from_dict(
                row, self._segments[str(row["procedural_track_id"])]
            )
            for row in track_rows
        }
        tape_rows = _rows(
            repository_root, package, CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST
        )
        self._tapes = {
            str(row["tape_id"]): ProceduralTape(
                **row,
                tracks=tuple(
                    sorted(
                        (
                            track
                            for track in self._tracks.values()
                            if track.tape_id == row["tape_id"]
                        ),
                        key=lambda item: item.procedural_track_id,
                    )
                ),
            )
            for row in tape_rows
        }
        self._waypoints = tuple(
            semantic_waypoint_from_dict(row)
            for row in _rows(
                repository_root, package, CanonicalSchemaName.SEMANTIC_WAYPOINTS
            )
        )
        self._events = tuple(
            motion_event_from_dict(row)
            for row in _rows(
                repository_root, package, CanonicalSchemaName.MOTION_EVENTS
            )
        )
        self._categories = {
            str(row["category_id"]): motion_category_from_dict(row)
            for row in _rows(
                repository_root, package, CanonicalSchemaName.MOTION_CATEGORIES
            )
        }
        self._templates = {
            str(row["template_id"]): route_template_from_dict(row)
            for row in _rows(
                repository_root, package, CanonicalSchemaName.ROUTE_TEMPLATES
            )
        }
        self._memberships = tuple(
            route_template_membership_from_dict(row)
            for row in _rows(
                repository_root,
                package,
                CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS,
            )
        )

    @property
    def package(self) -> ProceduralMotionPackage:
        """Return the immutable package manifest."""
        return self._package

    @staticmethod
    def _found[ValueT](
        values: Mapping[str, ValueT], identifier: str, label: str
    ) -> ValueT:
        try:
            return values[identifier]
        except KeyError:
            raise ArtifactError(f"{label} not found: {identifier}") from None

    def get_scenario(self, scenario_id: str) -> ScenarioRecord:
        """Return one canonical scenario by identifier."""
        return self._found(self._scenarios, scenario_id, "scenario")

    def get_tape(
        self, *, scenario_id: str | None = None, tape_id: str | None = None
    ) -> ProceduralTape:
        """Return one procedural tape by exactly one supported identifier."""
        if (scenario_id is None) == (tape_id is None):
            raise ValidationError("provide exactly one of scenario_id or tape_id")
        if tape_id is not None:
            return self._found(self._tapes, tape_id, "tape")
        matches = tuple(
            tape for tape in self._tapes.values() if tape.scenario_id == scenario_id
        )
        if len(matches) != 1:
            raise ArtifactError(f"tape not found for scenario: {scenario_id}")
        return matches[0]

    def get_track(self, procedural_track_id: str) -> ProceduralTrack:
        """Return one procedural track by identifier."""
        return self._found(self._tracks, procedural_track_id, "procedural track")

    def get_segments(self, procedural_track_id: str) -> tuple[ProceduralSegment, ...]:
        """Return numerical segments in canonical run order."""
        self.get_track(procedural_track_id)
        return self._segments[procedural_track_id]

    def get_waypoints(self, procedural_track_id: str) -> tuple[SemanticWaypoint, ...]:
        """Return semantic waypoints in deterministic index order."""
        self.get_track(procedural_track_id)
        return tuple(
            sorted(
                (
                    item
                    for item in self._waypoints
                    if item.procedural_track_id == procedural_track_id
                ),
                key=lambda item: (item.waypoint_index, item.waypoint_id),
            )
        )

    def get_events(self, procedural_track_id: str) -> tuple[MotionEvent, ...]:
        """Return motion events in deterministic index order."""
        self.get_track(procedural_track_id)
        return tuple(
            sorted(
                (
                    item
                    for item in self._events
                    if item.procedural_track_id == procedural_track_id
                ),
                key=lambda item: (item.event_index, item.event_id),
            )
        )

    def get_memberships(
        self, procedural_track_id: str
    ) -> tuple[RouteTemplateMembership, ...]:
        """Return route-template memberships in deterministic template order."""
        self.get_track(procedural_track_id)
        return tuple(
            sorted(
                (
                    item
                    for item in self._memberships
                    if item.procedural_track_id == procedural_track_id
                ),
                key=lambda item: (item.template_id, item.membership_index),
            )
        )

    def get_template(
        self, template_id: str
    ) -> tuple[RouteTemplate, tuple[RouteTemplateMembership, ...]]:
        """Return one route template and all members."""
        template = self._found(self._templates, template_id, "route template")
        members = tuple(
            sorted(
                (item for item in self._memberships if item.template_id == template_id),
                key=lambda item: (
                    item.membership_index,
                    item.procedural_track_id,
                ),
            )
        )
        return template, members

    def get_category(
        self, category_id: str
    ) -> tuple[MotionCategory, tuple[RouteTemplate, ...]]:
        """Return one motion category and its templates."""
        category = self._found(self._categories, category_id, "motion category")
        templates = tuple(
            sorted(
                (
                    item
                    for item in self._templates.values()
                    if item.category_id == category_id
                ),
                key=lambda item: item.template_id,
            )
        )
        return category, templates

    def replay(self, procedural_track_id: str, timestamp_ns: int) -> ReplayState | None:
        """Replay the accepted Batch 3.4 representation at an integer timestamp."""
        if not isinstance(timestamp_ns, int) or isinstance(timestamp_ns, bool):
            raise ValidationError("timestamp_ns must be a non-Boolean integer")
        return replay_track(self.get_track(procedural_track_id), timestamp_ns)

    def is_source_gap(self, procedural_track_id: str, timestamp_ns: int) -> bool:
        """Return whether an in-support timestamp lies strictly inside a source gap."""
        track = self.get_track(procedural_track_id)
        if not isinstance(timestamp_ns, int) or isinstance(timestamp_ns, bool):
            raise ValidationError("timestamp_ns must be a non-Boolean integer")
        if timestamp_ns <= track.start_time_ns or timestamp_ns >= track.end_time_ns:
            return False
        segments = self.get_segments(procedural_track_id)
        if replay_track(track, timestamp_ns) is not None:
            return False
        return any(
            left.end_time_ns < timestamp_ns < right.start_time_ns
            for left, right in pairwise(segments)
            if left.run_index != right.run_index
        )


def load_procedural_motion_package(
    repository_root: Path,
    relative_path: str | Path,
) -> tuple[ProceduralMotionPackage, ProceduralMotionTapeReader]:
    """Load canonical package JSON, verify all references, and return a reader."""
    path = _contained_path(repository_root, relative_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ArtifactError("package JSON cannot be read as UTF-8") from error
    package = procedural_motion_package_from_json(text)
    return package, ProceduralMotionTapeReader(repository_root, package)


@dataclass(frozen=True, slots=True)
class ProceduralMotionPackageArtifacts:
    """Exactly-three-file immutable package artifact bundle."""

    run_directory: RunDirectory
    package_manifest: WrittenArtifact
    contract_snapshot: WrittenArtifact
    phase3_summary: WrittenArtifact


def materialize_procedural_motion_package(
    repository_root: Path,
    results_root: str | Path,
    run_id: str,
    package: ProceduralMotionPackage,
    contract_snapshot: Mapping[str, object],
    phase3_summary: str,
) -> ProceduralMotionPackageArtifacts:
    """Validate references, atomically write the three-file bundle, and verify it."""
    validate_procedural_motion_package(repository_root, package)
    if not isinstance(contract_snapshot, Mapping) or any(
        not isinstance(key, str) for key in contract_snapshot
    ):
        raise ValidationError("contract_snapshot must be a string-keyed mapping")
    if (
        not isinstance(phase3_summary, str)
        or not phase3_summary.startswith("# Phase 3 Milestone")
        or not phase3_summary.endswith("\n")
        or phase3_summary.endswith("\n\n")
    ):
        raise ValidationError("phase3_summary must be deterministic milestone Markdown")
    run_directory = prepare_run_directory(
        repository_root, results_root, run_id, required_bytes=1_000_000
    )
    manifest = atomic_write_text(
        run_directory,
        _PACKAGE_PATH,
        procedural_motion_package_to_json(package),
    )
    snapshot = atomic_write_text(
        run_directory,
        _CONTRACT_PATH,
        canonical_json_text(dict(contract_snapshot)),
    )
    summary = atomic_write_text(run_directory, _SUMMARY_PATH, phase3_summary)
    artifacts = ProceduralMotionPackageArtifacts(
        run_directory=run_directory,
        package_manifest=manifest,
        contract_snapshot=snapshot,
        phase3_summary=summary,
    )
    finalize_run_directory(run_directory)
    verify_procedural_motion_package_artifacts(
        repository_root,
        artifacts,
        expected_package=package,
        expected_contract_snapshot=contract_snapshot,
        expected_phase3_summary=phase3_summary,
    )
    return artifacts


def _verify_written(repository_root: Path, artifact: WrittenArtifact) -> bytes:
    path = _contained_path(repository_root, artifact.relative_path)
    data = path.read_bytes()
    if (
        len(data) != artifact.size_bytes
        or hashlib.sha256(data).hexdigest() != artifact.content_checksum
    ):
        raise ArtifactError(f"bundle artifact identity differs: {path.name}")
    return data


def verify_procedural_motion_package_artifacts(
    repository_root: Path,
    artifacts: ProceduralMotionPackageArtifacts,
    *,
    expected_package: ProceduralMotionPackage | None = None,
    expected_contract_snapshot: Mapping[str, object] | None = None,
    expected_phase3_summary: str | None = None,
) -> tuple[ProceduralMotionPackage, PackageValidationResult]:
    """Verify exactly three files, reconstruct the package, and rerun validation."""
    if not isinstance(artifacts, ProceduralMotionPackageArtifacts):
        raise ValidationError("artifacts must be ProceduralMotionPackageArtifacts")
    state = inspect_run_directory(
        repository_root,
        artifacts.run_directory.results_root.relative_to(repository_root),
        artifacts.run_directory.run_id,
    )
    if state is not RunDirectoryState.COMPLETE:
        raise ArtifactError("package bundle is not complete")
    expected_paths = tuple(
        artifacts.run_directory.path.relative_to(repository_root) / path
        for path in _BUNDLE_PATHS
    )
    actual_paths = (
        artifacts.package_manifest.relative_path,
        artifacts.contract_snapshot.relative_path,
        artifacts.phase3_summary.relative_path,
    )
    if actual_paths != expected_paths:
        raise ArtifactError("package bundle paths differ")
    package_bytes = _verify_written(repository_root, artifacts.package_manifest)
    snapshot_bytes = _verify_written(repository_root, artifacts.contract_snapshot)
    summary_bytes = _verify_written(repository_root, artifacts.phase3_summary)
    package = procedural_motion_package_from_json(package_bytes)
    if expected_package is not None and package != expected_package:
        raise ArtifactError("reconstructed package differs")
    try:
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
        summary = summary_bytes.decode("utf-8")
    except (UnicodeError, json.JSONDecodeError):
        raise ArtifactError("bundle text artifact is malformed") from None
    if canonical_json_text(snapshot).encode("utf-8") != snapshot_bytes:
        raise ArtifactError("contract snapshot is not canonical JSON")
    if expected_contract_snapshot is not None and snapshot != dict(
        expected_contract_snapshot
    ):
        raise ArtifactError("contract snapshot differs")
    if expected_phase3_summary is not None and summary != expected_phase3_summary:
        raise ArtifactError("Phase 3 package summary differs")
    regular_names = {
        path.name
        for path in artifacts.run_directory.path.iterdir()
        if path.is_file() and not path.name.startswith(".run.")
    }
    if regular_names != {path.name for path in _BUNDLE_PATHS}:
        raise ArtifactError("package bundle contains unexpected files")
    if tuple(artifacts.run_directory.path.rglob("*.partial")):
        raise ArtifactError("package bundle contains a partial file")
    return package, validate_procedural_motion_package(repository_root, package)
