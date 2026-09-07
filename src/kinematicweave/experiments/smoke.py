"""Deterministic end-to-end foundation smoke experiment."""

from dataclasses import dataclass
from pathlib import Path

from kinematicweave import __version__
from kinematicweave.artifact_store import (
    RunDirectory,
    RunDirectoryState,
    WrittenArtifact,
    atomic_write_canonical_json,
    atomic_write_text,
    finalize_run_directory,
    inspect_run_directory,
    list_partial_artifacts,
    prepare_run_directory,
    repository_relative_artifact_path,
    run_directory_name,
)
from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.config import config_to_canonical_json, config_to_dict, load_config
from kinematicweave.errors import ArtifactError, ExperimentError, ValidationError
from kinematicweave.identifiers import validate_identifier
from kinematicweave.manifests import (
    ArtifactManifest,
    ArtifactType,
    ExperimentalUnitType,
    ExperimentManifest,
    RunStatus,
    artifact_manifest_to_dict,
    experiment_manifest_to_dict,
    transition_experiment_manifest,
)
from kinematicweave.paths import normalize_relative_path
from kinematicweave.seeding import derive_seed
from kinematicweave.system_metadata import (
    SystemMetadata,
    capture_system_metadata,
    system_metadata_to_dict,
)
from kinematicweave.timestamps import utc_now_timestamp

__all__ = ["FoundationSmokeResult", "run_foundation_smoke"]

_SCHEMA_VERSION = "1.0"
_EXPERIMENT_ID = "experiment:foundation-smoke:v1"
_EXPERIMENT_VERSION = "1.0"
_DATASET_ID = "synthetic_foundation"
_SPLIT_NAME = "smoke"
_METHOD_IDS = ("foundation_smoke",)
_METRIC_NAMES = ("foundation.diagnostic_value",)
_PRODUCER = "kinematicweave.experiments.smoke"
_SYNTHETIC_PATH = Path("artifacts/foundation_smoke/synthetic_record.json")
_SYSTEM_METADATA_PATH = Path("artifacts/foundation_smoke/system_metadata.json")
_REPORT_PATH = Path("artifacts/foundation_smoke/report.md")
_PLANNED_MANIFEST_PATH = Path("manifests/experiment.planned.json")
_RUNNING_MANIFEST_PATH = Path("manifests/experiment.running.json")
_COMPLETE_MANIFEST_PATH = Path("manifests/experiment.complete.json")
_FAILED_MANIFEST_PATH = Path("manifests/experiment.failed.json")


@dataclass(frozen=True, slots=True)
class FoundationSmokeResult:
    """Validated immutable result of one completed foundation smoke run."""

    run_id: str
    run_directory: RunDirectory
    diagnostic_value: int
    synthetic_artifact: WrittenArtifact
    system_metadata_artifact: WrittenArtifact
    report_artifact: WrittenArtifact
    complete_manifest_path: Path

    def __post_init__(self) -> None:
        """Validate result identity, artifact records, and contained paths."""
        object.__setattr__(self, "run_id", validate_identifier(self.run_id))
        if not isinstance(self.run_directory, RunDirectory):
            raise ValidationError("run_directory must be a RunDirectory")
        if self.run_directory.run_id != self.run_id:
            raise ValidationError("run_directory run_id must match run_id")
        if (
            not isinstance(self.diagnostic_value, int)
            or isinstance(self.diagnostic_value, bool)
            or self.diagnostic_value < 0
        ):
            raise ValidationError(
                "diagnostic_value must be a nonnegative non-Boolean integer"
            )

        for field_name in (
            "synthetic_artifact",
            "system_metadata_artifact",
            "report_artifact",
        ):
            artifact = getattr(self, field_name)
            if not isinstance(artifact, WrittenArtifact):
                raise ValidationError(f"{field_name} must be a WrittenArtifact")
            _validate_result_path(self.run_directory, artifact.relative_path)

        complete_manifest_path = normalize_relative_path(self.complete_manifest_path)
        _validate_result_path(self.run_directory, complete_manifest_path)
        object.__setattr__(
            self,
            "complete_manifest_path",
            complete_manifest_path,
        )


def _validate_result_path(run_directory: RunDirectory, relative_path: Path) -> None:
    expected = repository_relative_artifact_path(
        run_directory,
        run_directory.repository_root / relative_path,
    )
    if expected != relative_path:
        raise ValidationError("result artifact path is not normalized")


def _resolve_repository_root(repository_root: Path | None) -> Path:
    root = Path.cwd() if repository_root is None else repository_root
    if not isinstance(root, Path):
        raise ValidationError("repository_root must be a Path or None")
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise ValidationError(
            "repository_root must be an existing directory"
        ) from error
    if not resolved.is_dir():
        raise ValidationError("repository_root must be an existing directory")
    return resolved


def _resolve_config_path(repository_root: Path, config_path: str | Path) -> Path:
    if not isinstance(config_path, (str, Path)):
        raise ValidationError("config_path must be a string or Path")
    candidate = Path(config_path)
    if not candidate.is_absolute():
        candidate = repository_root / candidate
    resolved = candidate.resolve(strict=False)
    if resolved == repository_root or not resolved.is_relative_to(repository_root):
        raise ValidationError("config_path must resolve inside repository_root")
    return resolved


def _stable_host_id(metadata: SystemMetadata) -> str:
    stable_machine_fields = {
        "operating_system": {
            "system": metadata.operating_system.system,
            "release": metadata.operating_system.release,
            "version": metadata.operating_system.version,
            "machine": metadata.operating_system.machine,
        },
        "python": {
            "version": metadata.python.version,
            "implementation": metadata.python.implementation,
        },
        "cpu": {
            "processor": metadata.cpu.processor,
            "logical_cpu_count": metadata.cpu.logical_cpu_count,
        },
        "memory": {"total_bytes": metadata.memory.total_bytes},
        "gpus": [
            {
                "name": gpu.name,
                "memory_total_bytes": gpu.memory_total_bytes,
                "driver_version": gpu.driver_version,
            }
            for gpu in metadata.gpus
        ],
        "cuda_version": metadata.cuda_version,
        "wsl": {
            "is_wsl": metadata.wsl.is_wsl,
            "distribution": metadata.wsl.distribution,
            "version": metadata.wsl.version,
        },
    }
    return f"host:{canonical_sha256('foundation-smoke-host', stable_machine_fields)}"


def _validate_metadata(metadata: SystemMetadata) -> tuple[str, str]:
    if not metadata.git.available or metadata.git.commit is None:
        raise ExperimentError("foundation smoke requires an available Git commit")
    if metadata.environment_lock_id is None:
        raise ExperimentError("foundation smoke requires an environment lock identity")
    return metadata.git.commit, metadata.environment_lock_id


def _build_synthetic_payload(root_seed: int) -> dict[str, object]:
    values = [1, 2, 3, 4]
    return {
        "schema_version": _SCHEMA_VERSION,
        "artifact_kind": "foundation_smoke",
        "values": values,
        "diagnostic_name": "sum_of_squares",
        "diagnostic_value": sum(value * value for value in values),
        "root_seed": root_seed,
        "derived_seed": derive_seed(
            root_seed,
            "foundation-smoke",
            "diagnostic",
        ),
    }


def _artifact_id(run_component: str, kind: str) -> str:
    return validate_identifier(f"artifact:foundation-smoke:{run_component}:{kind}")


def _artifact_manifest(
    *,
    artifact_id: str,
    artifact_type: ArtifactType,
    written: WrittenArtifact,
    run_id: str,
    source_artifact_ids: tuple[str, ...],
    config_id: str,
    git_commit: str,
    created_time_utc: str,
    metadata: dict[str, object],
) -> ArtifactManifest:
    return ArtifactManifest(
        schema_version=_SCHEMA_VERSION,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        path=written.relative_path,
        producer=_PRODUCER,
        producer_version=__version__,
        run_id=run_id,
        source_artifact_ids=source_artifact_ids,
        config_id=config_id,
        git_commit=git_commit,
        content_checksum=written.content_checksum,
        size_bytes=written.size_bytes,
        created_time_utc=created_time_utc,
        metadata_json=canonical_json_text(metadata, trailing_newline=False),
    )


def _write_experiment_manifest(
    run_directory: RunDirectory,
    relative_path: Path,
    manifest: ExperimentManifest,
) -> WrittenArtifact:
    return atomic_write_canonical_json(
        run_directory,
        relative_path,
        experiment_manifest_to_dict(manifest),
    )


def _write_artifact_manifest(
    run_directory: RunDirectory,
    relative_path: Path,
    manifest: ArtifactManifest,
) -> WrittenArtifact:
    return atomic_write_canonical_json(
        run_directory,
        relative_path,
        artifact_manifest_to_dict(manifest),
    )


def _build_report(
    *,
    run_id: str,
    project_name: str,
    diagnostic_value: int,
    synthetic_artifact: WrittenArtifact,
    system_metadata_artifact: WrittenArtifact,
    git_commit: str,
    environment_lock_id: str,
) -> str:
    return (
        "# Foundation Smoke Report\n\n"
        f"- Run identifier: `{run_id}`\n"
        f"- Experiment identifier: `{_EXPERIMENT_ID}`\n"
        f"- Project: {project_name}\n"
        f"- Diagnostic: `sum_of_squares = {diagnostic_value}`\n"
        f"- Synthetic artifact: `{synthetic_artifact.relative_path.as_posix()}` "
        f"(`{synthetic_artifact.content_checksum}`)\n"
        "- System metadata artifact: "
        f"`{system_metadata_artifact.relative_path.as_posix()}` "
        f"(`{system_metadata_artifact.content_checksum}`)\n"
        f"- Git commit: `{git_commit}`\n"
        f"- Environment lock identity: `{environment_lock_id}`\n"
        "- Final intended status: `complete`\n"
    )


def _record_failure(
    run_directory: RunDirectory,
    latest_manifest: ExperimentManifest | None,
    error: Exception,
) -> None:
    if latest_manifest is None:
        raise ExperimentError("no valid experiment manifest exists for failure record")
    failed = transition_experiment_manifest(
        latest_manifest,
        RunStatus.FAILED,
        failed_unit_count=1,
        end_time_utc=utc_now_timestamp(),
        failure_summary=f"{type(error).__name__}: {error}",
    )
    _write_experiment_manifest(run_directory, _FAILED_MANIFEST_PATH, failed)


def run_foundation_smoke(
    repository_root: Path | None = None,
    *,
    run_id: str,
    config_path: str | Path = "configs/project.toml",
    results_root: str | Path | None = None,
) -> FoundationSmokeResult:
    """Run and finalize one deterministic foundation smoke experiment."""
    validated_run_id = validate_identifier(run_id)
    root = _resolve_repository_root(repository_root)
    resolved_config_path = _resolve_config_path(root, config_path)
    config = load_config(resolved_config_path)
    repository_config_path = normalize_relative_path(
        resolved_config_path.relative_to(root)
    )
    selected_results_root = normalize_relative_path(
        config.paths.results if results_root is None else results_root
    )
    metadata = capture_system_metadata(root)
    git_commit, environment_lock_id = _validate_metadata(metadata)
    resolved_config_json = config_to_canonical_json(config)
    config_id = f"config:{canonical_sha256('project-config', config_to_dict(config))}"
    host_id = _stable_host_id(metadata)

    run_directory: RunDirectory | None = None
    latest_manifest: ExperimentManifest | None = None
    try:
        run_directory = prepare_run_directory(
            root,
            selected_results_root,
            validated_run_id,
        )
        planned = ExperimentManifest(
            schema_version=_SCHEMA_VERSION,
            run_id=validated_run_id,
            experiment_id=_EXPERIMENT_ID,
            experiment_version=_EXPERIMENT_VERSION,
            status=RunStatus.PLANNED,
            config_path=repository_config_path,
            resolved_config_json=resolved_config_json,
            dataset_id=_DATASET_ID,
            split_name=_SPLIT_NAME,
            unit_type=ExperimentalUnitType.WORKLOAD,
            planned_unit_count=1,
            completed_unit_count=0,
            failed_unit_count=0,
            method_ids=_METHOD_IDS,
            metric_names=_METRIC_NAMES,
            seeds=(config.root_seed,),
            git_commit=git_commit,
            git_dirty=bool(metadata.git.dirty),
            python_version=metadata.python.version,
            environment_lock_id=environment_lock_id,
            host_id=host_id,
            start_time_utc=None,
            end_time_utc=None,
            raw_result_paths=(),
            log_paths=(),
            failure_summary=None,
        )
        latest_manifest = planned
        _write_experiment_manifest(
            run_directory,
            _PLANNED_MANIFEST_PATH,
            planned,
        )

        running = transition_experiment_manifest(
            planned,
            RunStatus.RUNNING,
            start_time_utc=utc_now_timestamp(),
        )
        latest_manifest = running
        _write_experiment_manifest(
            run_directory,
            _RUNNING_MANIFEST_PATH,
            running,
        )

        payload = _build_synthetic_payload(config.root_seed)
        diagnostic_value = payload["diagnostic_value"]
        if not isinstance(diagnostic_value, int) or isinstance(diagnostic_value, bool):
            raise ExperimentError("synthetic diagnostic produced an invalid value")
        synthetic_artifact = atomic_write_canonical_json(
            run_directory,
            _SYNTHETIC_PATH,
            payload,
        )
        run_component = run_directory_name(validated_run_id)
        synthetic_id = _artifact_id(run_component, "synthetic")
        synthetic_manifest = _artifact_manifest(
            artifact_id=synthetic_id,
            artifact_type=ArtifactType.RAW_RESULT,
            written=synthetic_artifact,
            run_id=validated_run_id,
            source_artifact_ids=(),
            config_id=config_id,
            git_commit=git_commit,
            created_time_utc=utc_now_timestamp(),
            metadata={
                "artifact_kind": "foundation_smoke",
                "diagnostic_name": "sum_of_squares",
            },
        )
        _write_artifact_manifest(
            run_directory,
            Path("manifests/artifact.synthetic.json"),
            synthetic_manifest,
        )

        system_metadata_artifact = atomic_write_canonical_json(
            run_directory,
            _SYSTEM_METADATA_PATH,
            system_metadata_to_dict(metadata),
        )
        system_metadata_id = _artifact_id(run_component, "system-metadata")
        system_metadata_manifest = _artifact_manifest(
            artifact_id=system_metadata_id,
            artifact_type=ArtifactType.DATA,
            written=system_metadata_artifact,
            run_id=validated_run_id,
            source_artifact_ids=(),
            config_id=config_id,
            git_commit=git_commit,
            created_time_utc=utc_now_timestamp(),
            metadata={"artifact_kind": "system_metadata"},
        )
        _write_artifact_manifest(
            run_directory,
            Path("manifests/artifact.system_metadata.json"),
            system_metadata_manifest,
        )

        report = _build_report(
            run_id=validated_run_id,
            project_name=config.project_name,
            diagnostic_value=diagnostic_value,
            synthetic_artifact=synthetic_artifact,
            system_metadata_artifact=system_metadata_artifact,
            git_commit=git_commit,
            environment_lock_id=environment_lock_id,
        )
        report_artifact = atomic_write_text(
            run_directory,
            _REPORT_PATH,
            report,
        )
        report_id = _artifact_id(run_component, "report")
        report_manifest = _artifact_manifest(
            artifact_id=report_id,
            artifact_type=ArtifactType.REPORT,
            written=report_artifact,
            run_id=validated_run_id,
            source_artifact_ids=(synthetic_id, system_metadata_id),
            config_id=config_id,
            git_commit=git_commit,
            created_time_utc=utc_now_timestamp(),
            metadata={"artifact_kind": "foundation_smoke_report"},
        )
        _write_artifact_manifest(
            run_directory,
            Path("manifests/artifact.report.json"),
            report_manifest,
        )

        complete = transition_experiment_manifest(
            running,
            RunStatus.COMPLETE,
            completed_unit_count=1,
            failed_unit_count=0,
            end_time_utc=utc_now_timestamp(),
            raw_result_paths=(synthetic_artifact.relative_path,),
            log_paths=(),
        )
        latest_manifest = complete
        complete_artifact = _write_experiment_manifest(
            run_directory,
            _COMPLETE_MANIFEST_PATH,
            complete,
        )
        finalize_run_directory(run_directory)
        if (
            inspect_run_directory(root, selected_results_root, validated_run_id)
            is not RunDirectoryState.COMPLETE
        ):
            raise ArtifactError("foundation smoke run did not finalize as complete")
        if list_partial_artifacts(run_directory):
            raise ArtifactError("foundation smoke run retained partial artifacts")

        return FoundationSmokeResult(
            run_id=validated_run_id,
            run_directory=run_directory,
            diagnostic_value=diagnostic_value,
            synthetic_artifact=synthetic_artifact,
            system_metadata_artifact=system_metadata_artifact,
            report_artifact=report_artifact,
            complete_manifest_path=complete_artifact.relative_path,
        )
    except Exception as error:
        if run_directory is not None:
            try:
                _record_failure(run_directory, latest_manifest, error)
            except Exception as recording_error:
                error.add_note(
                    "recording experiment failure also failed: "
                    f"{type(recording_error).__name__}: {recording_error}"
                )
                raise error.with_traceback(error.__traceback__) from recording_error
        raise
