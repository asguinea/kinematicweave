"""Immutable Parquet artifact bundle for semantic motion records."""

from collections.abc import Mapping, Sequence
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
    motion_events_to_table,
    read_canonical_parquet_table,
    semantic_waypoints_to_table,
    verify_canonical_parquet_artifact,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.procedural import ProceduralTape
from kinematicweave.domain.records import Trajectory
from kinematicweave.domain.semantic import (
    SemanticMotionTape,
    SemanticMotionTrack,
    motion_event_from_dict,
    semantic_waypoint_from_dict,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    semantic_motion_configuration_identity,
    summarize_semantic_motion_tape,
)

__all__ = [
    "SemanticMotionArtifacts",
    "materialize_semantic_motion_tape",
    "read_semantic_motion_tape",
    "verify_semantic_motion_artifacts",
]

_WAYPOINTS_PATH = Path("semantic_waypoints.parquet")
_EVENTS_PATH = Path("motion_events.parquet")
_SUMMARY_PATH = Path("semantic_summary.json")
_ARTIFACT_PATHS = (_WAYPOINTS_PATH, _EVENTS_PATH, _SUMMARY_PATH)


@dataclass(frozen=True, slots=True)
class SemanticMotionArtifacts:
    """Complete immutable identities for one semantic motion artifact set."""

    run_directory: RunDirectory
    semantic_waypoints: CanonicalParquetArtifact
    motion_events: CanonicalParquetArtifact
    semantic_summary: WrittenArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.run_directory, RunDirectory):
            raise ValidationError("run_directory must be a RunDirectory")
        if (
            not isinstance(
                self.semantic_waypoints,
                parquet_io.CanonicalParquetArtifact,
            )
            or self.semantic_waypoints.schema_name.value
            != CanonicalSchemaName.SEMANTIC_WAYPOINTS.value
        ):
            raise ValidationError("semantic_waypoints artifact identity differs")
        if (
            not isinstance(
                self.motion_events,
                parquet_io.CanonicalParquetArtifact,
            )
            or self.motion_events.schema_name.value
            != CanonicalSchemaName.MOTION_EVENTS.value
        ):
            raise ValidationError("motion_events artifact identity differs")
        if not isinstance(self.semantic_summary, WrittenArtifact):
            raise ValidationError("semantic_summary must be a WrittenArtifact")
        expected_root = self.run_directory.path.relative_to(
            self.run_directory.repository_root
        )
        actual = (
            self.semantic_waypoints.written_artifact.relative_path,
            self.motion_events.written_artifact.relative_path,
            self.semantic_summary.relative_path,
        )
        expected = tuple(expected_root / path for path in _ARTIFACT_PATHS)
        if actual != expected:
            raise ValidationError("semantic artifact paths differ from the contract")


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
    tape: SemanticMotionTape,
    trajectories: Sequence[Trajectory],
    config: SemanticMotionConfig,
    preservation_summary: Mapping[str, object],
    waypoints: CanonicalParquetArtifact,
    events: CanonicalParquetArtifact,
) -> dict[str, object]:
    semantic = summarize_semantic_motion_tape(tape, trajectories, config)
    return {
        "artifact_type": "semantic_motion",
        "artifact_version": "1.0",
        "tape_id": tape.procedural_tape.tape_id,
        "scenario_id": tape.procedural_tape.scenario_id,
        "detector_configuration_identity": tape.detector_configuration_identity,
        "source_validation_identity": tape.source_validation_identity,
        "procedural_encoder_name": tape.procedural_tape.encoder_name,
        "procedural_encoder_version": tape.procedural_tape.encoder_version,
        "procedural_encoder_parameters_identity": (
            tape.procedural_tape.encoder_parameters_identity
        ),
        "track_count": len(tape.tracks),
        "waypoint_count": semantic["waypoint_count"],
        "event_count": semantic["event_count"],
        "track_metadata": [
            {
                "procedural_track_id": track.procedural_track.procedural_track_id,
                "leading_invalid_sample_count": (track.leading_invalid_sample_count),
                "trailing_invalid_sample_count": (track.trailing_invalid_sample_count),
            }
            for track in tape.tracks
        ],
        "semantic_summary": semantic,
        "event_preservation_summary": dict(preservation_summary),
        "artifacts": [
            _artifact_summary(waypoints),
            _artifact_summary(events),
        ],
    }


def materialize_semantic_motion_tape(
    repository_root: Path,
    results_root: str | Path,
    run_id: str,
    tape: SemanticMotionTape,
    trajectories: Sequence[Trajectory],
    config: SemanticMotionConfig,
    preservation_summary: Mapping[str, object],
) -> SemanticMotionArtifacts:
    """Write and verify one immutable semantic artifact bundle."""
    if not isinstance(tape, SemanticMotionTape):
        raise ValidationError("tape must be a SemanticMotionTape")
    if tape.detector_configuration_identity != (
        semantic_motion_configuration_identity(config)
    ):
        raise ValidationError("semantic detector configuration identity differs")
    waypoint_values = tuple(
        waypoint for track in tape.tracks for waypoint in track.waypoints
    )
    event_values = tuple(event for track in tape.tracks for event in track.events)
    run_directory = prepare_run_directory(
        repository_root,
        results_root,
        run_id,
        required_bytes=max(1_000_000, (len(waypoint_values) + len(event_values)) * 512),
    )
    waypoints = atomic_write_canonical_parquet(
        run_directory,
        _WAYPOINTS_PATH,
        semantic_waypoints_to_table(waypoint_values),
        CanonicalSchemaName.SEMANTIC_WAYPOINTS,
    )
    events = atomic_write_canonical_parquet(
        run_directory,
        _EVENTS_PATH,
        motion_events_to_table(event_values),
        CanonicalSchemaName.MOTION_EVENTS,
    )
    summary = atomic_write_canonical_json(
        run_directory,
        _SUMMARY_PATH,
        _summary_value(
            tape,
            trajectories,
            config,
            preservation_summary,
            waypoints,
            events,
        ),
    )
    artifacts = SemanticMotionArtifacts(
        run_directory=run_directory,
        semantic_waypoints=waypoints,
        motion_events=events,
        semantic_summary=summary,
    )
    finalize_run_directory(run_directory)
    verify_semantic_motion_artifacts(
        repository_root,
        artifacts,
        tape.procedural_tape,
        trajectories,
        config,
        expected_tape=tape,
        expected_preservation_summary=preservation_summary,
    )
    return artifacts


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ArtifactError(f"{label} must be a string-keyed object")
    return value


def _read_summary(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError("semantic summary is not readable JSON") from error
    return _mapping(value, "semantic summary")


def read_semantic_motion_tape(
    repository_root: Path,
    artifacts: SemanticMotionArtifacts,
    procedural_tape: ProceduralTape,
) -> SemanticMotionTape:
    """Reconstruct a semantic tape from its canonical artifacts."""
    waypoint_rows = read_canonical_parquet_table(
        repository_root,
        (artifacts.semantic_waypoints.written_artifact.relative_path,),
        CanonicalSchemaName.SEMANTIC_WAYPOINTS,
    ).to_pylist()
    event_rows = read_canonical_parquet_table(
        repository_root,
        (artifacts.motion_events.written_artifact.relative_path,),
        CanonicalSchemaName.MOTION_EVENTS,
    ).to_pylist()
    waypoints = tuple(
        semantic_waypoint_from_dict(_mapping(row, "waypoint row"))
        for row in waypoint_rows
    )
    events = tuple(
        motion_event_from_dict(_mapping(row, "event row")) for row in event_rows
    )
    summary = _read_summary(repository_root / artifacts.semantic_summary.relative_path)
    metadata_value = summary.get("track_metadata")
    if not isinstance(metadata_value, list):
        raise ArtifactError("semantic summary track_metadata is invalid")
    metadata = {
        str(_mapping(item, "track metadata")["procedural_track_id"]): _mapping(
            item,
            "track metadata",
        )
        for item in metadata_value
    }
    tracks: list[SemanticMotionTrack] = []
    for procedural_track in procedural_tape.tracks:
        track_id = procedural_track.procedural_track_id
        try:
            track_metadata = metadata[track_id]
        except KeyError as error:
            raise ArtifactError("semantic summary omits a procedural track") from error
        tracks.append(
            SemanticMotionTrack(
                procedural_track=procedural_track,
                waypoints=tuple(
                    item for item in waypoints if item.procedural_track_id == track_id
                ),
                events=tuple(
                    item for item in events if item.procedural_track_id == track_id
                ),
                leading_invalid_sample_count=track_metadata[
                    "leading_invalid_sample_count"
                ],  # type: ignore[arg-type]
                trailing_invalid_sample_count=track_metadata[
                    "trailing_invalid_sample_count"
                ],  # type: ignore[arg-type]
            )
        )
    return SemanticMotionTape(
        procedural_tape=procedural_tape,
        tracks=tuple(tracks),
        detector_configuration_identity=str(summary["detector_configuration_identity"]),
        source_validation_identity=(
            None
            if summary.get("source_validation_identity") is None
            else str(summary["source_validation_identity"])
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


def verify_semantic_motion_artifacts(
    repository_root: Path,
    artifacts: SemanticMotionArtifacts,
    procedural_tape: ProceduralTape,
    trajectories: Sequence[Trajectory],
    config: SemanticMotionConfig,
    *,
    expected_tape: SemanticMotionTape | None = None,
    expected_preservation_summary: Mapping[str, object] | None = None,
) -> tuple[SemanticMotionTape, Mapping[str, object]]:
    """Verify semantic paths, hashes, schemas, references, replay, and summary."""
    if not isinstance(artifacts, SemanticMotionArtifacts):
        raise ValidationError("artifacts must be SemanticMotionArtifacts")
    state = inspect_run_directory(
        repository_root,
        artifacts.run_directory.results_root.relative_to(repository_root),
        artifacts.run_directory.run_id,
    )
    if state is not RunDirectoryState.COMPLETE:
        raise ArtifactError("semantic run is not complete")
    verify_canonical_parquet_artifact(repository_root, artifacts.semantic_waypoints)
    verify_canonical_parquet_artifact(repository_root, artifacts.motion_events)
    summary_path = repository_root / artifacts.semantic_summary.relative_path
    size, checksum = _hash_file(summary_path)
    if size != artifacts.semantic_summary.size_bytes:
        raise ArtifactError("semantic summary size differs")
    if checksum != artifacts.semantic_summary.content_checksum:
        raise ArtifactError("semantic summary checksum differs")
    summary = _read_summary(summary_path)
    tape = read_semantic_motion_tape(repository_root, artifacts, procedural_tape)
    preservation = _mapping(
        summary.get("event_preservation_summary"),
        "event preservation summary",
    )
    expected_summary = _summary_value(
        tape,
        trajectories,
        config,
        preservation,
        artifacts.semantic_waypoints,
        artifacts.motion_events,
    )
    if summary != expected_summary:
        raise ArtifactError("semantic summary does not match verified artifacts")
    if expected_tape is not None and tape != expected_tape:
        raise ArtifactError("reconstructed semantic tape differs")
    if (
        expected_preservation_summary is not None
        and preservation != expected_preservation_summary
    ):
        raise ArtifactError("event preservation summary differs")
    if tuple(artifacts.run_directory.path.rglob("*.partial")):
        raise ArtifactError("completed semantic run contains partial files")
    regular_names = {
        path.name
        for path in artifacts.run_directory.path.iterdir()
        if path.is_file() and not path.name.startswith(".run.")
    }
    if regular_names != {path.name for path in _ARTIFACT_PATHS}:
        raise ArtifactError("semantic run contains unexpected artifact files")
    return tape, preservation
