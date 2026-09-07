"""Repository-contained run directories and atomic artifact writing."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import errno
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import cast

from kinematicweave.canonical import canonical_json_bytes, canonical_sha256
from kinematicweave.errors import ArtifactError, ResourceLimitError, ValidationError
from kinematicweave.identifiers import validate_identifier
from kinematicweave.paths import normalize_relative_path

__all__ = [
    "DiskSpaceSnapshot",
    "RunDirectory",
    "RunDirectoryState",
    "WrittenArtifact",
    "atomic_write_bytes",
    "atomic_write_canonical_json",
    "atomic_write_generated_file",
    "atomic_write_text",
    "check_disk_space",
    "finalize_run_directory",
    "inspect_run_directory",
    "list_partial_artifacts",
    "prepare_run_directory",
    "repository_relative_artifact_path",
    "run_directory_name",
]

_PARTIAL_MARKER = ".run.partial"
_COMPLETE_MARKER = ".run.complete"
_TEMPORARY_DIRECTORY = ".temporary"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_DIRECTORY_FSYNC_UNSUPPORTED = {
    errno.EACCES,
    errno.EBADF,
    errno.EINVAL,
    errno.EISDIR,
    getattr(errno, "ENOTSUP", errno.EINVAL),
}


class RunDirectoryState(StrEnum):
    """Operational states for a repository run directory."""

    MISSING = "missing"
    PARTIAL = "partial"
    COMPLETE = "complete"


def _validated_absolute_path(value: object, field_name: str) -> Path:
    if not isinstance(value, Path):
        raise ValidationError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise ValidationError(f"{field_name} must be absolute")
    return value.resolve(strict=False)


def _is_strictly_beneath(path: Path, parent: Path) -> bool:
    return path != parent and path.is_relative_to(parent)


@dataclass(frozen=True, slots=True)
class RunDirectory:
    """Validated absolute paths for one repository-contained run."""

    repository_root: Path
    results_root: Path
    run_id: str
    path: Path
    artifacts_path: Path
    manifests_path: Path
    temporary_path: Path

    def __post_init__(self) -> None:
        """Normalize paths and enforce containment without creating directories."""
        repository_root = _validated_absolute_path(
            self.repository_root,
            "repository_root",
        )
        if not repository_root.is_dir():
            raise ValidationError("repository_root must exist and be a directory")

        normalized_paths = {
            field_name: _validated_absolute_path(getattr(self, field_name), field_name)
            for field_name in (
                "results_root",
                "path",
                "artifacts_path",
                "manifests_path",
                "temporary_path",
            )
        }
        results_root = normalized_paths["results_root"]
        run_path = normalized_paths["path"]
        if not _is_strictly_beneath(results_root, repository_root):
            raise ValidationError("results_root must be beneath repository_root")
        if not _is_strictly_beneath(run_path, results_root):
            raise ValidationError("path must be beneath results_root")
        for field_name in ("artifacts_path", "manifests_path", "temporary_path"):
            if not _is_strictly_beneath(normalized_paths[field_name], run_path):
                raise ValidationError(f"{field_name} must be beneath path")

        object.__setattr__(self, "repository_root", repository_root)
        for field_name, path in normalized_paths.items():
            object.__setattr__(self, field_name, path)
        object.__setattr__(self, "run_id", validate_identifier(self.run_id))


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


@dataclass(frozen=True, slots=True)
class WrittenArtifact:
    """Identity and repository-relative location of a completed write."""

    relative_path: Path
    size_bytes: int
    content_checksum: str

    def __post_init__(self) -> None:
        """Normalize and validate persistent artifact metadata."""
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
        if (
            not isinstance(self.content_checksum, str)
            or _SHA256_PATTERN.fullmatch(self.content_checksum) is None
        ):
            raise ValidationError(
                "content_checksum must be a lowercase 64-character SHA-256 digest"
            )


@dataclass(frozen=True, slots=True)
class DiskSpaceSnapshot:
    """Validated disk-capacity values for one write preflight."""

    total_bytes: int
    used_bytes: int
    free_bytes: int
    required_bytes: int
    reserve_bytes: int
    free_after_write_bytes: int

    def __post_init__(self) -> None:
        """Reject negative, Boolean, or non-integer capacity values."""
        for field_name in self.__dataclass_fields__:
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )


def run_directory_name(run_id: str) -> str:
    """Return the deterministic cross-platform directory name for a run."""
    normalized_run_id = validate_identifier(run_id)
    return f"run-{canonical_sha256('run-directory', normalized_run_id)[:24]}"


def _build_run_directory(
    repository_root: Path,
    results_root: str | Path,
    run_id: str,
) -> RunDirectory:
    if not isinstance(repository_root, Path):
        raise ValidationError("repository_root must be a Path")
    if not repository_root.is_absolute():
        raise ValidationError("repository_root must be absolute")
    resolved_repository_root = repository_root.resolve(strict=False)
    if not resolved_repository_root.is_dir():
        raise ValidationError("repository_root must exist and be a directory")

    normalized_results = normalize_relative_path(results_root)
    resolved_results_root = (resolved_repository_root / normalized_results).resolve(
        strict=False
    )
    if not _is_strictly_beneath(resolved_results_root, resolved_repository_root):
        raise ArtifactError("results_root resolves outside repository_root")

    run_path = (resolved_results_root / "runs" / run_directory_name(run_id)).resolve(
        strict=False
    )
    return RunDirectory(
        repository_root=resolved_repository_root,
        results_root=resolved_results_root,
        run_id=run_id,
        path=run_path,
        artifacts_path=run_path / "artifacts",
        manifests_path=run_path / "manifests",
        temporary_path=run_path / _TEMPORARY_DIRECTORY,
    )


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _marker_exists(path: Path) -> bool:
    if not _lexists(path):
        return False
    if path.is_symlink() or not path.is_file():
        raise ArtifactError(f"run marker is not a regular file: {path.name}")
    return True


def _inspect_state(run_directory: RunDirectory) -> RunDirectoryState:
    run_path = run_directory.path
    if not _lexists(run_path):
        return RunDirectoryState.MISSING
    if run_path.is_symlink() or not run_path.is_dir():
        raise ArtifactError("run path exists but is not a regular directory")

    partial = _marker_exists(run_path / _PARTIAL_MARKER)
    complete = _marker_exists(run_path / _COMPLETE_MARKER)
    if partial and complete:
        raise ArtifactError("run directory contains conflicting state markers")
    if partial:
        return RunDirectoryState.PARTIAL
    if complete:
        return RunDirectoryState.COMPLETE
    raise ArtifactError("existing run directory has no state marker")


def inspect_run_directory(
    repository_root: Path,
    results_root: str | Path,
    run_id: str,
) -> RunDirectoryState:
    """Inspect run state without changing the filesystem.

    Raises:
        ValidationError: If root, path, or identifier values are invalid.
        ArtifactError: If the existing run state is corrupt or escapes the repository.
    """
    return _inspect_state(_build_run_directory(repository_root, results_root, run_id))


def _reserve_fraction(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(
            "reserve_fraction must be a finite non-Boolean number in [0, 1)"
        )
    try:
        normalized = float(value)
    except OverflowError:
        raise ValidationError(
            "reserve_fraction must be a finite non-Boolean number in [0, 1)"
        ) from None
    if not math.isfinite(normalized) or normalized < 0 or normalized >= 1:
        raise ValidationError(
            "reserve_fraction must be a finite non-Boolean number in [0, 1)"
        )
    return normalized


def check_disk_space(
    path: Path,
    *,
    required_bytes: int = 0,
    reserve_fraction: float = 0.15,
) -> DiskSpaceSnapshot:
    """Return capacity after a proposed write or raise before disk exhaustion.

    Raises:
        ValidationError: If capacity inputs are invalid.
        ResourceLimitError: If free space or the configured reserve is insufficient.
        OSError: If disk capacity cannot be inspected.
    """
    if not isinstance(path, Path):
        raise ValidationError("path must be a Path")
    required = _nonnegative_int(required_bytes, "required_bytes")
    reserve = _reserve_fraction(reserve_fraction)
    usage = shutil.disk_usage(path)
    reserve_bytes = math.ceil(usage.total * reserve)
    if required > usage.free:
        raise ResourceLimitError(
            f"required bytes ({required}) exceed free bytes ({usage.free})"
        )
    free_after_write = usage.free - required
    if free_after_write < reserve_bytes:
        raise ResourceLimitError(
            "planned write would violate the configured free-space reserve"
        )
    return DiskSpaceSnapshot(
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        required_bytes=required,
        reserve_bytes=reserve_bytes,
        free_after_write_bytes=free_after_write,
    )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as error:
        if error.errno in _DIRECTORY_FSYNC_UNSUPPORTED:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in _DIRECTORY_FSYNC_UNSUPPORTED:
                raise
    finally:
        os.close(descriptor)


def _write_marker_exclusive(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise ArtifactError(f"state marker already exists: {path.name}") from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_approved_layout(run_directory: RunDirectory) -> None:
    for path in (
        run_directory.artifacts_path,
        run_directory.manifests_path,
        run_directory.temporary_path,
    ):
        if not _lexists(path) or path.is_symlink() or not path.is_dir():
            raise ArtifactError(
                f"approved run directory is missing or invalid: {path.name}"
            )


def prepare_run_directory(
    repository_root: Path,
    results_root: str | Path,
    run_id: str,
    *,
    resume: bool = False,
    required_bytes: int = 0,
    reserve_fraction: float = 0.15,
) -> RunDirectory:
    """Prepare a partial run directory after validating state and disk capacity.

    Raises:
        ValidationError: If an input value is invalid.
        ArtifactError: If an existing run cannot be resumed safely.
        ResourceLimitError: If the disk preflight fails.
        OSError: If directory or marker creation fails.
    """
    if not isinstance(resume, bool):
        raise ValidationError("resume must be a Boolean")
    run_directory = _build_run_directory(repository_root, results_root, run_id)
    state = _inspect_state(run_directory)
    if state is RunDirectoryState.COMPLETE:
        raise ArtifactError("completed run directories are immutable")

    check_disk_space(
        run_directory.repository_root,
        required_bytes=required_bytes,
        reserve_fraction=reserve_fraction,
    )
    if state is RunDirectoryState.PARTIAL:
        if not resume:
            raise ArtifactError("partial run requires resume=True")
        _validate_approved_layout(run_directory)
        return run_directory

    try:
        run_directory.path.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raced_state = _inspect_state(run_directory)
        if raced_state is RunDirectoryState.PARTIAL and resume:
            _validate_approved_layout(run_directory)
            return run_directory
        if raced_state is RunDirectoryState.COMPLETE:
            raise ArtifactError("completed run directories are immutable") from None
        raise ArtifactError("run directory was created concurrently") from None

    run_directory.artifacts_path.mkdir()
    run_directory.manifests_path.mkdir()
    run_directory.temporary_path.mkdir()
    _write_marker_exclusive(run_directory.path / _PARTIAL_MARKER, b"partial\n")
    _fsync_directory(run_directory.path)
    _fsync_directory(run_directory.path.parent)
    return run_directory


def _require_partial(run_directory: RunDirectory) -> None:
    state = _inspect_state(run_directory)
    if state is RunDirectoryState.MISSING:
        raise ArtifactError("run directory does not exist")
    if state is RunDirectoryState.COMPLETE:
        raise ArtifactError("completed run directories are immutable")


def _artifact_relative_path(relative_path: str | Path) -> Path:
    try:
        normalized = normalize_relative_path(relative_path)
    except ValidationError as error:
        raise ArtifactError(str(error)) from None
    if normalized.parts[0] in {
        _PARTIAL_MARKER,
        _COMPLETE_MARKER,
        _TEMPORARY_DIRECTORY,
    }:
        raise ArtifactError("artifact path is reserved for run-directory state")
    return normalized


def _resolved_contained_path(run_directory: RunDirectory, path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if not _is_strictly_beneath(resolved, run_directory.repository_root):
        raise ArtifactError("artifact path resolves outside repository_root")
    if not _is_strictly_beneath(resolved, run_directory.path):
        raise ArtifactError("artifact path resolves outside the run directory")
    return resolved


def repository_relative_artifact_path(
    run_directory: RunDirectory,
    path: Path,
) -> Path:
    """Return a normalized repository-relative path for a contained artifact.

    Raises:
        ValidationError: If path is not a Path.
        ArtifactError: If path escapes the repository or run directory.
    """
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be a RunDirectory")
    if not isinstance(path, Path):
        raise ValidationError("path must be a Path")
    resolved = _resolved_contained_path(run_directory, path)
    try:
        return normalize_relative_path(
            resolved.relative_to(run_directory.repository_root)
        )
    except (ValueError, ValidationError):
        raise ArtifactError("artifact path is not repository-relative") from None


def _cleanup_temporary(path: Path, original_error: BaseException) -> None:
    try:
        if path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            path.rmdir()
    except OSError as cleanup_error:
        original_error.add_note(f"temporary-file cleanup also failed: {cleanup_error}")


def _atomic_install_bytes(destination: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".partial",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            raise ArtifactError(
                f"artifact destination already exists: {destination.name}"
            ) from None
        temporary_path.unlink()
        _fsync_directory(destination.parent)
    except BaseException as error:
        if descriptor >= 0:
            os.close(descriptor)
        _cleanup_temporary(temporary_path, error)
        raise


def _prepare_artifact_destination(
    run_directory: RunDirectory,
    relative_path: str | Path,
) -> Path:
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be a RunDirectory")
    normalized_path = _artifact_relative_path(relative_path)
    _require_partial(run_directory)

    destination = run_directory.path / normalized_path
    _resolved_contained_path(run_directory, destination)
    if _lexists(destination):
        raise ArtifactError("artifact destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _resolved_contained_path(run_directory, destination)
    if _lexists(destination):
        raise ArtifactError("artifact destination already exists")
    return destination


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return size_bytes, digest.hexdigest()


def atomic_write_generated_file(
    run_directory: RunDirectory,
    relative_path: str | Path,
    writer: Callable[[Path], None],
) -> WrittenArtifact:
    """Generate and atomically install one file without replacing a destination.

    The callback receives only a unique destination-local temporary path. It must
    return ``None`` and leave a regular non-symlink file at that path.

    Raises:
        ValidationError: If the run-directory or callback value is invalid.
        ArtifactError: If run state, containment, callback output, or destination
            state is invalid.
        OSError: If the durable write or atomic installation fails.
    """
    if not callable(writer):
        raise ValidationError("writer must be callable")
    destination = _prepare_artifact_destination(run_directory, relative_path)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".partial",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        result = cast("Callable[[Path], object]", writer)(temporary_path)
        if result is not None:
            raise ArtifactError("writer must return None")
        if not _lexists(temporary_path):
            raise ArtifactError("writer did not create the generated file")
        if temporary_path.is_symlink() or not temporary_path.is_file():
            raise ArtifactError("writer output must be a regular non-symlink file")

        with temporary_path.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            raise ArtifactError(
                f"artifact destination already exists: {destination.name}"
            ) from None
        temporary_path.unlink()
        _fsync_directory(destination.parent)
    except BaseException as error:
        _cleanup_temporary(temporary_path, error)
        raise

    size_bytes, checksum = _hash_file(destination)
    return WrittenArtifact(
        relative_path=repository_relative_artifact_path(run_directory, destination),
        size_bytes=size_bytes,
        content_checksum=checksum,
    )


def atomic_write_bytes(
    run_directory: RunDirectory,
    relative_path: str | Path,
    data: bytes,
) -> WrittenArtifact:
    """Atomically install bytes once without replacing an existing artifact.

    Raises:
        ValidationError: If data or the run-directory value is invalid.
        ArtifactError: If run state, containment, or destination state is invalid.
        OSError: If the durable write or atomic installation fails.
    """
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be a RunDirectory")
    if not isinstance(data, bytes):
        raise ValidationError("data must be bytes")
    destination = _prepare_artifact_destination(run_directory, relative_path)

    _atomic_install_bytes(destination, data)
    return WrittenArtifact(
        relative_path=repository_relative_artifact_path(run_directory, destination),
        size_bytes=len(data),
        content_checksum=hashlib.sha256(data).hexdigest(),
    )


def atomic_write_text(
    run_directory: RunDirectory,
    relative_path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> WrittenArtifact:
    """Encode and atomically install exactly the supplied text."""
    if not isinstance(text, str):
        raise ValidationError("text must be a string")
    if not isinstance(encoding, str) or not encoding:
        raise ValidationError("encoding must be a nonempty string")
    return atomic_write_bytes(
        run_directory,
        relative_path,
        text.encode(encoding),
    )


def atomic_write_canonical_json(
    run_directory: RunDirectory,
    relative_path: str | Path,
    value: object,
) -> WrittenArtifact:
    """Canonically encode JSON without a trailing newline and install it once."""
    return atomic_write_bytes(
        run_directory,
        relative_path,
        canonical_json_bytes(value, trailing_newline=False),
    )


def list_partial_artifacts(
    run_directory: RunDirectory,
) -> tuple[Path, ...]:
    """Return sorted repository-relative temporary artifact paths."""
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be a RunDirectory")
    if _inspect_state(run_directory) is RunDirectoryState.MISSING:
        raise ArtifactError("run directory does not exist")

    partial_marker = run_directory.path / _PARTIAL_MARKER
    paths = [
        repository_relative_artifact_path(run_directory, path)
        for path in run_directory.path.rglob("*.partial")
        if path != partial_marker and (path.is_file() or path.is_symlink())
    ]
    return tuple(sorted(paths, key=Path.as_posix))


def _validate_finalization_directory(path: Path) -> None:
    if not _lexists(path) or path.is_symlink() or not path.is_dir():
        raise ArtifactError(f"required finalization directory is missing: {path.name}")


def finalize_run_directory(run_directory: RunDirectory) -> None:
    """Atomically mark a partial run complete after validating its layout.

    Raises:
        ValidationError: If run_directory has the wrong type.
        ArtifactError: If state, layout, or partial artifacts prevent completion.
        OSError: If durable marker installation fails.
    """
    if not isinstance(run_directory, RunDirectory):
        raise ValidationError("run_directory must be a RunDirectory")
    _require_partial(run_directory)
    partial_artifacts = list_partial_artifacts(run_directory)
    if partial_artifacts:
        raise ArtifactError("partial artifacts prevent run finalization")
    _validate_finalization_directory(run_directory.artifacts_path)
    _validate_finalization_directory(run_directory.manifests_path)

    _fsync_directory(run_directory.artifacts_path)
    _fsync_directory(run_directory.manifests_path)
    _fsync_directory(run_directory.path)

    partial_marker = run_directory.path / _PARTIAL_MARKER
    complete_marker = run_directory.path / _COMPLETE_MARKER
    if _lexists(complete_marker):
        raise ArtifactError("complete marker already exists")
    _atomic_install_bytes(complete_marker, b"complete\n")
    partial_marker.unlink()
    _fsync_directory(run_directory.path)
