"""Immutable Parquet artifact set for exact procedural tapes."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

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
    procedural_segments_to_table,
    procedural_tape_manifest_to_table,
    procedural_tracks_to_table,
    read_canonical_parquet_table,
    verify_canonical_parquet_artifact,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.procedural import (
    ProceduralTape,
    ProceduralTrack,
    procedural_segment_from_dict,
    procedural_track_from_dict,
    validate_procedural_tape,
)
from kinematicweave.errors import ArtifactError, ValidationError

__all__ = [
    "ProceduralTapeArtifacts",
    "materialize_procedural_tape",
    "read_procedural_tape",
    "verify_procedural_tape_artifacts",
]

_MANIFEST_PATH = Path("tape_manifest.parquet")
_TRACKS_PATH = Path("procedural_tracks.parquet")
_SEGMENTS_PATH = Path("procedural_segments.parquet")
_SUMMARY_PATH = Path("codec_summary.json")
_ARTIFACT_PATHS = (
    _MANIFEST_PATH,
    _TRACKS_PATH,
    _SEGMENTS_PATH,
    _SUMMARY_PATH,
)


@dataclass(frozen=True, slots=True)
class ProceduralTapeArtifacts:
    """Complete immutable artifact identities for one procedural tape."""

    run_directory: RunDirectory
    tape_manifest: CanonicalParquetArtifact
    procedural_tracks: CanonicalParquetArtifact
    procedural_segments: CanonicalParquetArtifact
    codec_summary: WrittenArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.run_directory, RunDirectory):
            raise ValidationError("run_directory must be a RunDirectory")
        expected = (
            (self.tape_manifest, CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST),
            (self.procedural_tracks, CanonicalSchemaName.PROCEDURAL_TRACKS),
            (self.procedural_segments, CanonicalSchemaName.PROCEDURAL_SEGMENTS),
        )
        for artifact, schema_name in expected:
            if not isinstance(artifact, parquet_io.CanonicalParquetArtifact):
                raise ValidationError("Parquet artifacts must use canonical metadata")
            if artifact.schema_name is not schema_name:
                raise ValidationError("procedural artifact schema identity differs")
        if not isinstance(self.codec_summary, WrittenArtifact):
            raise ValidationError("codec_summary must be a WrittenArtifact")
        paths = (
            self.tape_manifest.written_artifact.relative_path,
            self.procedural_tracks.written_artifact.relative_path,
            self.procedural_segments.written_artifact.relative_path,
            self.codec_summary.relative_path,
        )
        expected_paths = tuple(
            self.run_directory.path.relative_to(self.run_directory.repository_root)
            / path
            for path in _ARTIFACT_PATHS
        )
        if paths != expected_paths:
            raise ValidationError("procedural artifact paths differ from the contract")


def _artifact_summary(artifact: CanonicalParquetArtifact) -> dict[str, object]:
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
    tape: ProceduralTape,
    manifest: CanonicalParquetArtifact,
    tracks: CanonicalParquetArtifact,
    segments: CanonicalParquetArtifact,
) -> dict[str, object]:
    return {
        "artifact_type": (
            "exact_procedural_tape"
            if tape.encoder_name == "kinematicweave.exact_hold_linear"
            else "compact_procedural_tape"
        ),
        "artifact_version": "1.0",
        "tape_id": tape.tape_id,
        "scenario_id": tape.scenario_id,
        "encoder_name": tape.encoder_name,
        "encoder_version": tape.encoder_version,
        "encoder_parameters_identity": tape.encoder_parameters_identity,
        "track_count": tape.track_count,
        "segment_count": tape.segment_count,
        "source_sample_count": tape.source_sample_count,
        "encoded_valid_sample_count": tape.encoded_valid_sample_count,
        "artifacts": [
            _artifact_summary(manifest),
            _artifact_summary(tracks),
            _artifact_summary(segments),
        ],
    }


def materialize_procedural_tape(
    repository_root: Path,
    results_root: str | Path,
    run_id: str,
    tape: ProceduralTape,
) -> ProceduralTapeArtifacts:
    """Write, finalize, reopen, and verify one immutable exact tape artifact set."""
    if not isinstance(tape, ProceduralTape):
        raise ValidationError("tape must be a ProceduralTape")
    validate_procedural_tape(tape)
    run_directory = prepare_run_directory(
        repository_root,
        results_root,
        run_id,
        required_bytes=max(1_000_000, tape.segment_count * 512),
    )
    manifest = atomic_write_canonical_parquet(
        run_directory,
        _MANIFEST_PATH,
        procedural_tape_manifest_to_table(tape),
        CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST,
    )
    tracks = atomic_write_canonical_parquet(
        run_directory,
        _TRACKS_PATH,
        procedural_tracks_to_table(tape.tracks),
        CanonicalSchemaName.PROCEDURAL_TRACKS,
    )
    segments = atomic_write_canonical_parquet(
        run_directory,
        _SEGMENTS_PATH,
        procedural_segments_to_table(
            tuple(segment for track in tape.tracks for segment in track.segments)
        ),
        CanonicalSchemaName.PROCEDURAL_SEGMENTS,
    )
    summary = atomic_write_canonical_json(
        run_directory,
        _SUMMARY_PATH,
        _summary_value(tape, manifest, tracks, segments),
    )
    artifacts = ProceduralTapeArtifacts(
        run_directory=run_directory,
        tape_manifest=manifest,
        procedural_tracks=tracks,
        procedural_segments=segments,
        codec_summary=summary,
    )
    finalize_run_directory(run_directory)
    verify_procedural_tape_artifacts(repository_root, artifacts, expected_tape=tape)
    return artifacts


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ArtifactError(f"{label} must be a string-keyed object")
    return value


def _read_tables(
    repository_root: Path,
    artifacts: ProceduralTapeArtifacts,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tables = (
        read_canonical_parquet_table(
            repository_root,
            (artifacts.tape_manifest.written_artifact.relative_path,),
            CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST,
        ),
        read_canonical_parquet_table(
            repository_root,
            (artifacts.procedural_tracks.written_artifact.relative_path,),
            CanonicalSchemaName.PROCEDURAL_TRACKS,
        ),
        read_canonical_parquet_table(
            repository_root,
            (artifacts.procedural_segments.written_artifact.relative_path,),
            CanonicalSchemaName.PROCEDURAL_SEGMENTS,
        ),
    )
    return tuple(table.to_pylist() for table in tables)


def read_procedural_tape(
    repository_root: Path,
    artifacts: ProceduralTapeArtifacts,
) -> ProceduralTape:
    """Reconstruct a validated procedural tape from canonical Parquet artifacts."""
    manifest_rows, track_rows, segment_rows = _read_tables(repository_root, artifacts)
    if len(manifest_rows) != 1:
        raise ArtifactError("procedural tape manifest must contain exactly one row")
    segments = tuple(
        procedural_segment_from_dict(_mapping(row, "segment row"))
        for row in segment_rows
    )
    tracks: list[ProceduralTrack] = []
    for row in track_rows:
        row_mapping = _mapping(row, "track row")
        track_id = row_mapping.get("procedural_track_id")
        track_segments = tuple(
            segment for segment in segments if segment.procedural_track_id == track_id
        )
        tracks.append(procedural_track_from_dict(row_mapping, track_segments))
    manifest = dict(_mapping(manifest_rows[0], "manifest row"))
    return ProceduralTape(**manifest, tracks=tuple(tracks))  # type: ignore[arg-type]


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def verify_procedural_tape_artifacts(
    repository_root: Path,
    artifacts: ProceduralTapeArtifacts,
    *,
    expected_tape: ProceduralTape | None = None,
) -> ProceduralTape:
    """Verify paths, checksums, schemas, counts, references, and replay records."""
    if not isinstance(artifacts, ProceduralTapeArtifacts):
        raise ValidationError("artifacts must be ProceduralTapeArtifacts")
    state = inspect_run_directory(
        repository_root,
        artifacts.run_directory.results_root.relative_to(repository_root),
        artifacts.run_directory.run_id,
    )
    if state is not RunDirectoryState.COMPLETE:
        raise ArtifactError("procedural tape run is not complete")
    for artifact in (
        artifacts.tape_manifest,
        artifacts.procedural_tracks,
        artifacts.procedural_segments,
    ):
        verify_canonical_parquet_artifact(repository_root, artifact)

    summary_path = repository_root / artifacts.codec_summary.relative_path
    size, checksum = _hash_file(summary_path)
    if size != artifacts.codec_summary.size_bytes:
        raise ArtifactError("codec summary size differs")
    if checksum != artifacts.codec_summary.content_checksum:
        raise ArtifactError("codec summary checksum differs")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError("codec summary is not readable canonical JSON") from error

    tape = read_procedural_tape(repository_root, artifacts)
    expected_summary = _summary_value(
        tape,
        artifacts.tape_manifest,
        artifacts.procedural_tracks,
        artifacts.procedural_segments,
    )
    if summary != expected_summary:
        raise ArtifactError("codec summary does not match verified artifacts")
    if expected_tape is not None and tape != expected_tape:
        raise ArtifactError("reconstructed procedural tape differs")
    partials = tuple(artifacts.run_directory.path.rglob("*.partial"))
    if partials:
        raise ArtifactError("completed procedural run contains partial files")
    regular_names = {
        path.name
        for path in artifacts.run_directory.path.iterdir()
        if path.is_file() and not path.name.startswith(".run.")
    }
    if regular_names != {path.name for path in _ARTIFACT_PATHS}:
        raise ArtifactError("procedural run contains an unexpected artifact file")
    return tape
