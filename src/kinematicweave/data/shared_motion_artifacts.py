"""Atomic four-file artifact bundle for shared-motion models."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from kinematicweave.artifact_store import (
    RunDirectory,
    RunDirectoryState,
    WrittenArtifact,
    atomic_write_canonical_json,
    finalize_run_directory,
    inspect_run_directory,
    prepare_run_directory,
)
from kinematicweave.data import parquet_io
from kinematicweave.data.parquet_io import (
    CanonicalParquetArtifact,
    atomic_write_canonical_parquet,
    motion_categories_to_table,
    read_canonical_parquet_table,
    route_template_memberships_to_table,
    route_templates_to_table,
    verify_canonical_parquet_artifact,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.records import AgentClass
from kinematicweave.domain.shared_motion import (
    MotionCategoryLabel,
    SharedMotionModel,
    motion_category_from_dict,
    route_template_from_dict,
    route_template_membership_from_dict,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.layout.shared_motion import (
    MapEvaluation,
    SharedMotionConfig,
    shared_motion_configuration_identity,
)

__all__ = [
    "SharedMotionArtifacts",
    "materialize_shared_motion_model",
    "read_shared_motion_model",
    "verify_shared_motion_artifacts",
]

_CATEGORIES_PATH = Path("motion_categories.parquet")
_TEMPLATES_PATH = Path("route_templates.parquet")
_MEMBERSHIPS_PATH = Path("route_template_memberships.parquet")
_SUMMARY_PATH = Path("shared_motion_summary.json")
_ARTIFACT_PATHS = (
    _CATEGORIES_PATH,
    _TEMPLATES_PATH,
    _MEMBERSHIPS_PATH,
    _SUMMARY_PATH,
)
_DEFAULT_CONFIG = SharedMotionConfig()


@dataclass(frozen=True, slots=True)
class SharedMotionArtifacts:
    run_directory: RunDirectory
    motion_categories: CanonicalParquetArtifact
    route_templates: CanonicalParquetArtifact
    route_template_memberships: CanonicalParquetArtifact
    shared_motion_summary: WrittenArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.run_directory, RunDirectory):
            raise ValidationError("run_directory must be a RunDirectory")
        expected_schemas = (
            (self.motion_categories, CanonicalSchemaName.MOTION_CATEGORIES),
            (self.route_templates, CanonicalSchemaName.ROUTE_TEMPLATES),
            (
                self.route_template_memberships,
                CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS,
            ),
        )
        if any(
            not isinstance(artifact, parquet_io.CanonicalParquetArtifact)
            or artifact.schema_name.value != expected.value
            for artifact, expected in expected_schemas
        ):
            raise ValidationError("shared-motion Parquet artifact identity differs")
        if not isinstance(self.shared_motion_summary, WrittenArtifact):
            raise ValidationError("shared_motion_summary must be a WrittenArtifact")
        root = self.run_directory.path.relative_to(self.run_directory.repository_root)
        actual = (
            self.motion_categories.written_artifact.relative_path,
            self.route_templates.written_artifact.relative_path,
            self.route_template_memberships.written_artifact.relative_path,
            self.shared_motion_summary.relative_path,
        )
        if actual != tuple(root / path for path in _ARTIFACT_PATHS):
            raise ValidationError("shared-motion artifact paths differ")


def _artifact_value(artifact: CanonicalParquetArtifact) -> dict[str, object]:
    return {
        "path": artifact.written_artifact.relative_path.as_posix(),
        "size_bytes": artifact.written_artifact.size_bytes,
        "sha256": artifact.written_artifact.content_checksum,
        "schema_name": artifact.schema_name.value,
        "schema_version": artifact.schema_version,
        "schema_fingerprint": artifact.schema_fingerprint,
        "row_count": artifact.row_count,
        "row_group_count": artifact.row_group_count,
    }


def _summary_value(
    model: SharedMotionModel,
    map_evaluation: MapEvaluation | None,
    categories: CanonicalParquetArtifact,
    templates: CanonicalParquetArtifact,
    memberships: CanonicalParquetArtifact,
) -> dict[str, object]:
    map_metrics = (
        []
        if map_evaluation is None
        else [json.loads(item) for item in map_evaluation.template_metrics]
    )
    return {
        "artifact_type": "shared_motion",
        "artifact_version": "1.0",
        "dataset_id": model.dataset_id,
        "dataset_version": model.dataset_version,
        "source_validation_identity": model.source_validation_identity,
        "procedural_codec_identity": model.procedural_codec_identity,
        "semantic_detector_identity": model.semantic_detector_identity,
        "grouping_configuration_identity": model.grouping_configuration_identity,
        "category_count": len(model.categories),
        "template_count": len(model.route_templates),
        "membership_count": len(model.memberships),
        "category_summary_metadata": [
            json.loads(item) for item in model.category_summary_metadata
        ],
        "map_evaluation_performed_after_construction": map_evaluation is not None,
        "map_data_used_for_construction": False,
        "map_template_metrics": map_metrics,
        "artifacts": [
            _artifact_value(categories),
            _artifact_value(templates),
            _artifact_value(memberships),
        ],
    }


def materialize_shared_motion_model(
    repository_root: Path,
    results_root: str | Path,
    run_id: str,
    model: SharedMotionModel,
    *,
    map_evaluation: MapEvaluation | None = None,
) -> SharedMotionArtifacts:
    """Write, finalize, and verify one immutable shared-motion artifact bundle."""
    if not isinstance(model, SharedMotionModel):
        raise ValidationError("model must be a SharedMotionModel")
    persistent_model = (
        map_evaluation.evaluated_model if map_evaluation is not None else model
    )
    if map_evaluation is not None and (
        persistent_model.categories != model.categories
        or persistent_model.route_templates != model.route_templates
        or tuple(
            (
                item.template_id,
                item.procedural_track_id,
                item.membership_index,
                item.scenario_id,
            )
            for item in persistent_model.memberships
        )
        != tuple(
            (
                item.template_id,
                item.procedural_track_id,
                item.membership_index,
                item.scenario_id,
            )
            for item in model.memberships
        )
    ):
        raise ValidationError("map evaluation changed frozen grouping structure")
    run_directory = prepare_run_directory(
        repository_root,
        results_root,
        run_id,
        required_bytes=max(1_000_000, len(model.memberships) * 2_048),
    )
    categories = atomic_write_canonical_parquet(
        run_directory,
        _CATEGORIES_PATH,
        motion_categories_to_table(persistent_model.categories),
        CanonicalSchemaName.MOTION_CATEGORIES,
    )
    templates = atomic_write_canonical_parquet(
        run_directory,
        _TEMPLATES_PATH,
        route_templates_to_table(persistent_model.route_templates),
        CanonicalSchemaName.ROUTE_TEMPLATES,
    )
    memberships = atomic_write_canonical_parquet(
        run_directory,
        _MEMBERSHIPS_PATH,
        route_template_memberships_to_table(persistent_model.memberships),
        CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS,
    )
    summary = atomic_write_canonical_json(
        run_directory,
        _SUMMARY_PATH,
        _summary_value(
            persistent_model,
            map_evaluation,
            categories,
            templates,
            memberships,
        ),
    )
    artifacts = SharedMotionArtifacts(
        run_directory=run_directory,
        motion_categories=categories,
        route_templates=templates,
        route_template_memberships=memberships,
        shared_motion_summary=summary,
    )
    finalize_run_directory(run_directory)
    verify_shared_motion_artifacts(
        repository_root,
        artifacts,
        expected_model=persistent_model,
        expected_map_evaluation=map_evaluation,
    )
    return artifacts


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError("shared-motion summary is not readable JSON") from error
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ArtifactError("shared-motion summary must be a string-keyed object")
    return value


def read_shared_motion_model(
    repository_root: Path,
    artifacts: SharedMotionArtifacts,
) -> SharedMotionModel:
    """Reconstruct the immutable model from canonical records and summary."""
    categories = tuple(
        motion_category_from_dict(row)
        for row in read_canonical_parquet_table(
            repository_root,
            (artifacts.motion_categories.written_artifact.relative_path,),
            CanonicalSchemaName.MOTION_CATEGORIES,
        ).to_pylist()
    )
    categories = tuple(
        sorted(
            categories,
            key=lambda item: (
                list(MotionCategoryLabel).index(
                    MotionCategoryLabel(item.category_label)
                ),
                list(AgentClass).index(AgentClass(item.agent_class)),
                item.event_signature_json,
                item.category_id,
            ),
        )
    )
    templates = tuple(
        route_template_from_dict(row)
        for row in read_canonical_parquet_table(
            repository_root,
            (artifacts.route_templates.written_artifact.relative_path,),
            CanonicalSchemaName.ROUTE_TEMPLATES,
        ).to_pylist()
    )
    memberships = tuple(
        route_template_membership_from_dict(row)
        for row in read_canonical_parquet_table(
            repository_root,
            (artifacts.route_template_memberships.written_artifact.relative_path,),
            CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS,
        ).to_pylist()
    )
    summary = _read_json(
        repository_root / artifacts.shared_motion_summary.relative_path
    )
    metadata = summary.get("category_summary_metadata")
    if not isinstance(metadata, list):
        raise ArtifactError("category_summary_metadata is invalid")
    return SharedMotionModel(
        dataset_id=str(summary["dataset_id"]),
        dataset_version=str(summary["dataset_version"]),
        source_validation_identity=str(summary["source_validation_identity"]),
        procedural_codec_identity=str(summary["procedural_codec_identity"]),
        semantic_detector_identity=str(summary["semantic_detector_identity"]),
        grouping_configuration_identity=str(summary["grouping_configuration_identity"]),
        categories=categories,
        route_templates=templates,
        memberships=memberships,
        category_summary_metadata=tuple(
            json.dumps(item, allow_nan=False, separators=(",", ":"), sort_keys=True)
            for item in metadata
        ),
    )


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def verify_shared_motion_artifacts(
    repository_root: Path,
    artifacts: SharedMotionArtifacts,
    *,
    expected_model: SharedMotionModel | None = None,
    expected_map_evaluation: MapEvaluation | None = None,
    config: SharedMotionConfig = _DEFAULT_CONFIG,
) -> SharedMotionModel:
    """Verify identities, records, references, construction policy, and bytes."""
    state = inspect_run_directory(
        repository_root,
        artifacts.run_directory.results_root.relative_to(repository_root),
        artifacts.run_directory.run_id,
    )
    if state is not RunDirectoryState.COMPLETE:
        raise ArtifactError("shared-motion run is not complete")
    for artifact in (
        artifacts.motion_categories,
        artifacts.route_templates,
        artifacts.route_template_memberships,
    ):
        verify_canonical_parquet_artifact(repository_root, artifact)
    summary_path = repository_root / artifacts.shared_motion_summary.relative_path
    size, checksum = _hash_file(summary_path)
    if (
        size != artifacts.shared_motion_summary.size_bytes
        or checksum != artifacts.shared_motion_summary.content_checksum
    ):
        raise ArtifactError("shared-motion summary identity differs")
    model = read_shared_motion_model(repository_root, artifacts)
    if model.grouping_configuration_identity != shared_motion_configuration_identity(
        config
    ):
        raise ArtifactError("grouping configuration identity differs")
    if expected_model is not None and model != expected_model:
        differing = tuple(
            name
            for name in model.__dataclass_fields__
            if getattr(model, name) != getattr(expected_model, name)
        )
        raise ArtifactError(f"reconstructed shared-motion model differs in {differing}")
    for template in model.route_templates:
        attributes = json.loads(template.semantic_attributes_json)
        if (
            attributes.get("grouping_scope") != "scenario_source_frame"
            or attributes.get("map_data_used_for_construction") is not False
        ):
            raise ArtifactError("template construction policy differs")
    summary = _read_json(summary_path)
    expected_summary = _summary_value(
        model,
        expected_map_evaluation,
        artifacts.motion_categories,
        artifacts.route_templates,
        artifacts.route_template_memberships,
    )
    if summary != expected_summary:
        raise ArtifactError("shared-motion summary differs from verified records")
    if tuple(artifacts.run_directory.path.rglob("*.partial")):
        raise ArtifactError("completed shared-motion run contains partial files")
    names = {
        path.name
        for path in artifacts.run_directory.path.iterdir()
        if path.is_file() and not path.name.startswith(".run.")
    }
    if names != {path.name for path in _ARTIFACT_PATHS}:
        raise ArtifactError("shared-motion run contains unexpected artifact files")
    return model
