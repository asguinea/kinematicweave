"""Resumable canonical artifacts for comparable motion baselines."""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import (
    RunDirectory,
    RunDirectoryState,
    WrittenArtifact,
    finalize_run_directory,
    inspect_run_directory,
    prepare_run_directory,
    run_directory_name,
)
from kinematicweave.data import parquet_io
from kinematicweave.data.parquet_io import (
    atomic_write_canonical_parquet,
    trajectories_to_table,
    verify_canonical_parquet_artifact,
)
from kinematicweave.data.procedural_artifacts import (
    ProceduralTapeArtifacts,
    materialize_procedural_tape,
    verify_procedural_tape_artifacts,
)
from kinematicweave.data.schemas import CanonicalSchemaName, schema_fingerprint
from kinematicweave.domain.procedural import ProceduralTape
from kinematicweave.domain.records import Trajectory
from kinematicweave.errors import ArtifactError, ValidationError

__all__ = [
    "EquivalentArtifactResult",
    "RawSampleArtifacts",
    "materialize_equivalent_procedural_artifacts",
    "materialize_equivalent_raw_artifacts",
]

_PROCEDURAL_FILES = (
    ("tape_manifest.parquet", CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST),
    ("procedural_tracks.parquet", CanonicalSchemaName.PROCEDURAL_TRACKS),
    ("procedural_segments.parquet", CanonicalSchemaName.PROCEDURAL_SEGMENTS),
)
_RAW_PATH = Path("trajectory_samples.parquet")


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _run_directory(
    repository_root: Path,
    results_root: Path,
    run_id: str,
) -> RunDirectory:
    root = (repository_root / results_root).resolve()
    path = root / "runs" / run_directory_name(run_id)
    return RunDirectory(
        repository_root=repository_root,
        results_root=root,
        run_id=run_id,
        path=path,
        artifacts_path=path / "artifacts",
        manifests_path=path / "manifests",
        temporary_path=path / ".temporary",
    )


def _parquet_artifact(
    run_directory: RunDirectory,
    filename: str,
    schema_name: CanonicalSchemaName,
) -> parquet_io.CanonicalParquetArtifact:
    path = run_directory.path / filename
    size, checksum = _hash_file(path)
    try:
        metadata = pq.ParquetFile(path).metadata
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise ArtifactError(
            f"invalid completed Parquet artifact: {filename}"
        ) from error
    artifact = parquet_io.CanonicalParquetArtifact(
        schema_name=schema_name,
        schema_version="1.0",
        schema_fingerprint=schema_fingerprint(schema_name),
        written_artifact=WrittenArtifact(
            relative_path=path.relative_to(run_directory.repository_root),
            size_bytes=size,
            content_checksum=checksum,
        ),
        row_count=metadata.num_rows,
        row_group_count=metadata.num_row_groups,
    )
    verify_canonical_parquet_artifact(run_directory.repository_root, artifact)
    return artifact


def _load_procedural(
    repository_root: Path,
    results_root: Path,
    run_id: str,
    expected_tape: ProceduralTape,
) -> ProceduralTapeArtifacts:
    run = _run_directory(repository_root, results_root, run_id)
    parquet = tuple(
        _parquet_artifact(run, filename, schema)
        for filename, schema in _PROCEDURAL_FILES
    )
    summary_path = run.path / "codec_summary.json"
    size, checksum = _hash_file(summary_path)
    artifacts = ProceduralTapeArtifacts(
        run_directory=run,
        tape_manifest=parquet[0],
        procedural_tracks=parquet[1],
        procedural_segments=parquet[2],
        codec_summary=WrittenArtifact(
            relative_path=summary_path.relative_to(repository_root),
            size_bytes=size,
            content_checksum=checksum,
        ),
    )
    verify_procedural_tape_artifacts(
        repository_root,
        artifacts,
        expected_tape=expected_tape,
    )
    return artifacts


def _retry_run_id(
    repository_root: Path,
    results_root: Path,
    base_run_id: str,
) -> str:
    candidate = base_run_id
    retry = 0
    while (
        inspect_run_directory(repository_root, results_root, candidate)
        is not RunDirectoryState.MISSING
    ):
        retry += 1
        candidate = f"{base_run_id}:retry-{retry}"
    return candidate


def _procedural_artifacts(
    repository_root: Path,
    results_root: Path,
    base_run_id: str,
    tape: ProceduralTape,
) -> ProceduralTapeArtifacts:
    state = inspect_run_directory(repository_root, results_root, base_run_id)
    if state is RunDirectoryState.COMPLETE:
        return _load_procedural(repository_root, results_root, base_run_id, tape)
    run_id = (
        base_run_id
        if state is RunDirectoryState.MISSING
        else _retry_run_id(repository_root, results_root, base_run_id)
    )
    return materialize_procedural_tape(
        repository_root,
        results_root,
        run_id,
        tape,
    )


@dataclass(frozen=True, slots=True)
class RawSampleArtifacts:
    """One canonical raw trajectory-sample Parquet artifact."""

    run_directory: RunDirectory
    trajectory_samples: parquet_io.CanonicalParquetArtifact

    def __post_init__(self) -> None:
        if (
            self.trajectory_samples.schema_name
            is not CanonicalSchemaName.TRAJECTORY_SAMPLES
        ):
            raise ValidationError("raw sample artifact uses the wrong schema")


def _load_raw(
    repository_root: Path,
    results_root: Path,
    run_id: str,
) -> RawSampleArtifacts:
    run = _run_directory(repository_root, results_root, run_id)
    artifact = _parquet_artifact(
        run,
        _RAW_PATH.name,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    return RawSampleArtifacts(run, artifact)


def _raw_artifacts(
    repository_root: Path,
    results_root: Path,
    base_run_id: str,
    trajectories: tuple[Trajectory, ...],
) -> RawSampleArtifacts:
    state = inspect_run_directory(repository_root, results_root, base_run_id)
    if state is RunDirectoryState.COMPLETE:
        return _load_raw(repository_root, results_root, base_run_id)
    run_id = (
        base_run_id
        if state is RunDirectoryState.MISSING
        else _retry_run_id(repository_root, results_root, base_run_id)
    )
    run = prepare_run_directory(
        repository_root,
        results_root,
        run_id,
        required_bytes=max(
            1_000_000, sum(item.sample_count for item in trajectories) * 256
        ),
    )
    artifact = atomic_write_canonical_parquet(
        run,
        _RAW_PATH,
        trajectories_to_table(trajectories),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    finalize_run_directory(run)
    verify_canonical_parquet_artifact(repository_root, artifact)
    return RawSampleArtifacts(run, artifact)


@dataclass(frozen=True, slots=True)
class EquivalentArtifactResult:
    """Serialized-size and checksum evidence from two equivalent writes."""

    encoded_bytes: int
    generated_disk_bytes: int
    content_checksums: tuple[str, ...]
    checksum_agreement: bool
    first_run_id: str
    repeat_run_id: str


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def materialize_equivalent_procedural_artifacts(
    repository_root: Path,
    results_root: Path,
    run_label: str,
    tape: ProceduralTape,
    *,
    verifier: Callable[[ProceduralTapeArtifacts], object] | None = None,
) -> EquivalentArtifactResult:
    """Materialize or reopen two equivalent procedural artifacts and compare them."""
    first = _procedural_artifacts(
        repository_root,
        results_root,
        f"{run_label}:first",
        tape,
    )
    repeat = _procedural_artifacts(
        repository_root,
        results_root,
        f"{run_label}:repeat",
        tape,
    )
    if verifier is not None:
        verifier(first)
        verifier(repeat)
    first_items = (
        first.tape_manifest,
        first.procedural_tracks,
        first.procedural_segments,
    )
    repeat_items = (
        repeat.tape_manifest,
        repeat.procedural_tracks,
        repeat.procedural_segments,
    )
    checksums = tuple(item.written_artifact.content_checksum for item in first_items)
    agreement = checksums == tuple(
        item.written_artifact.content_checksum for item in repeat_items
    )
    if not agreement:
        raise ArtifactError("equivalent procedural Parquet checksums differ")
    return EquivalentArtifactResult(
        encoded_bytes=sum(item.written_artifact.size_bytes for item in first_items)
        + first.codec_summary.size_bytes,
        generated_disk_bytes=_tree_size(first.run_directory.path)
        + _tree_size(repeat.run_directory.path),
        content_checksums=checksums,
        checksum_agreement=True,
        first_run_id=first.run_directory.run_id,
        repeat_run_id=repeat.run_directory.run_id,
    )


def materialize_equivalent_raw_artifacts(
    repository_root: Path,
    results_root: Path,
    run_label: str,
    trajectories: tuple[Trajectory, ...],
) -> EquivalentArtifactResult:
    """Materialize or reopen two canonical raw-sample files and compare them."""
    first = _raw_artifacts(
        repository_root,
        results_root,
        f"{run_label}:first",
        trajectories,
    )
    repeat = _raw_artifacts(
        repository_root,
        results_root,
        f"{run_label}:repeat",
        trajectories,
    )
    checksum = first.trajectory_samples.written_artifact.content_checksum
    agreement = checksum == repeat.trajectory_samples.written_artifact.content_checksum
    if not agreement:
        raise ArtifactError("equivalent raw-sample Parquet checksums differ")
    return EquivalentArtifactResult(
        encoded_bytes=first.trajectory_samples.written_artifact.size_bytes,
        generated_disk_bytes=_tree_size(first.run_directory.path)
        + _tree_size(repeat.run_directory.path),
        content_checksums=(checksum,),
        checksum_agreement=True,
        first_run_id=first.run_directory.run_id,
        repeat_run_id=repeat.run_directory.run_id,
    )
