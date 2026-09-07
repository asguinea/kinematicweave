"""Tests for atomic artifacts and repository-contained run directories."""

from dataclasses import FrozenInstanceError
import hashlib
import importlib
import importlib.util
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from kinematicweave import artifact_store
from kinematicweave.artifact_store import (
    DiskSpaceSnapshot,
    RunDirectory,
    RunDirectoryState,
    WrittenArtifact,
    atomic_write_bytes,
    atomic_write_canonical_json,
    atomic_write_generated_file,
    atomic_write_text,
    check_disk_space,
    finalize_run_directory,
    inspect_run_directory,
    list_partial_artifacts,
    prepare_run_directory,
    repository_relative_artifact_path,
    run_directory_name,
)
from kinematicweave.canonical import canonical_json_bytes
from kinematicweave.config import config_to_canonical_json, load_config
from kinematicweave.errors import ArtifactError, ResourceLimitError, ValidationError
from kinematicweave.manifests import (
    ArtifactManifest,
    ArtifactType,
    ExperimentalUnitType,
    ExperimentManifest,
    RunStatus,
    artifact_manifest_from_json,
    artifact_manifest_to_dict,
    experiment_manifest_from_json,
    experiment_manifest_to_dict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run:artifact-store"
GIT_COMMIT = "a" * 40
LOCK_ID = "b" * 64


def _prepared(tmp_path: Path, *, run_id: str = RUN_ID) -> RunDirectory:
    repository = tmp_path / "repository"
    repository.mkdir()
    return prepare_run_directory(
        repository,
        "results",
        run_id,
        reserve_fraction=0,
    )


def _manual_run_directory(repository: Path, run_id: str = RUN_ID) -> RunDirectory:
    results = repository / "results"
    run_path = results / "runs" / run_directory_name(run_id)
    return RunDirectory(
        repository_root=repository,
        results_root=results,
        run_id=run_id,
        path=run_path,
        artifacts_path=run_path / "artifacts",
        manifests_path=run_path / "manifests",
        temporary_path=run_path / ".temporary",
    )


def _create_state(
    tmp_path: Path,
    marker: str | None,
) -> tuple[Path, RunDirectory]:
    repository = tmp_path / "repository"
    repository.mkdir()
    run_directory = _manual_run_directory(repository)
    run_directory.path.mkdir(parents=True)
    if marker is not None:
        (run_directory.path / marker).write_text(
            f"{marker.removeprefix('.run.')}\n",
            encoding="utf-8",
        )
    return repository, run_directory


def _disk_usage(
    monkeypatch: pytest.MonkeyPatch,
    *,
    total: int = 1000,
    used: int = 500,
    free: int = 500,
) -> None:
    monkeypatch.setattr(
        "kinematicweave.artifact_store.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=total, used=used, free=free),
    )


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")


def test_run_directory_state_values_are_exact() -> None:
    assert [state.value for state in RunDirectoryState] == [
        "missing",
        "partial",
        "complete",
    ]


def test_dataclasses_are_frozen_and_slotted(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    written = WrittenArtifact(Path("results/a"), 1, "a" * 64)
    snapshot = DiskSpaceSnapshot(10, 2, 8, 1, 1, 7)

    for value in (run_directory, written, snapshot):
        assert not hasattr(value, "__dict__")
        field_name = next(iter(value.__dataclass_fields__))
        with pytest.raises(FrozenInstanceError):
            setattr(value, field_name, getattr(value, field_name))


@pytest.mark.parametrize("size", [True, -1, 1.5])
def test_written_artifact_rejects_invalid_sizes(size: object) -> None:
    with pytest.raises(ValidationError):
        WrittenArtifact(Path("results/a"), size, "a" * 64)  # type: ignore[arg-type]


@pytest.mark.parametrize("checksum", ["A" * 64, "a" * 63, "g" * 64])
def test_written_artifact_rejects_invalid_checksums(checksum: str) -> None:
    with pytest.raises(ValidationError, match="content_checksum"):
        WrittenArtifact(Path("results/a"), 1, checksum)


def test_written_artifact_normalizes_relative_path() -> None:
    artifact = WrittenArtifact(Path("results/./run/value"), 0, "a" * 64)
    assert artifact.relative_path == Path("results/run/value")


@pytest.mark.parametrize("path", [Path("/absolute"), Path("../outside")])
def test_written_artifact_rejects_unsafe_paths(path: Path) -> None:
    with pytest.raises(ValidationError):
        WrittenArtifact(path, 0, "a" * 64)


@pytest.mark.parametrize("value", [True, -1, 1.0])
def test_disk_snapshot_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError):
        DiskSpaceSnapshot(value, 0, 0, 0, 0, 0)  # type: ignore[arg-type]


def test_run_directory_validates_roots_and_children(tmp_path: Path) -> None:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir()
    valid = _manual_run_directory(repository)
    assert valid.repository_root == repository

    with pytest.raises(ValidationError, match="repository_root must be absolute"):
        _manual_run_directory(Path("relative"))
    missing = (tmp_path / "missing").resolve()
    with pytest.raises(ValidationError, match="must exist"):
        _manual_run_directory(missing)
    with pytest.raises(ValidationError, match="results_root"):
        RunDirectory(
            repository,
            tmp_path / "outside",
            RUN_ID,
            valid.path,
            valid.artifacts_path,
            valid.manifests_path,
            valid.temporary_path,
        )
    with pytest.raises(ValidationError, match="artifacts_path"):
        RunDirectory(
            repository,
            valid.results_root,
            RUN_ID,
            valid.path,
            repository / "outside",
            valid.manifests_path,
            valid.temporary_path,
        )
    with pytest.raises(ValidationError, match="path must be beneath"):
        RunDirectory(
            repository,
            valid.results_root,
            RUN_ID,
            repository / "other-run",
            valid.artifacts_path,
            valid.manifests_path,
            valid.temporary_path,
        )


def test_module_all_is_exact() -> None:
    assert artifact_store.__all__ == [
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


def test_run_directory_name_is_deterministic_and_safe() -> None:
    first = run_directory_name(RUN_ID)
    assert first == run_directory_name(RUN_ID)
    assert first.startswith("run-")
    assert len(first) == 28
    assert first[4:].isalnum()
    assert first[4:] == first[4:].lower()
    assert run_directory_name("run:different") != first


def test_inspect_missing_state(tmp_path: Path) -> None:
    repository = tmp_path.resolve()
    assert (
        inspect_run_directory(repository, "results", RUN_ID)
        is RunDirectoryState.MISSING
    )


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        (".run.partial", RunDirectoryState.PARTIAL),
        (".run.complete", RunDirectoryState.COMPLETE),
    ],
)
def test_inspect_partial_and_complete_states(
    tmp_path: Path,
    marker: str,
    expected: RunDirectoryState,
) -> None:
    repository, _ = _create_state(tmp_path, marker)
    assert inspect_run_directory(repository, "results", RUN_ID) is expected


def test_inspect_rejects_conflicting_markers(tmp_path: Path) -> None:
    repository, run_directory = _create_state(tmp_path, ".run.partial")
    (run_directory.path / ".run.complete").write_text("complete\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="conflicting"):
        inspect_run_directory(repository, "results", RUN_ID)


def test_inspect_rejects_existing_directory_without_marker(tmp_path: Path) -> None:
    repository, _ = _create_state(tmp_path, None)
    with pytest.raises(ArtifactError, match="no state marker"):
        inspect_run_directory(repository, "results", RUN_ID)


def test_disk_space_returns_exact_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _disk_usage(monkeypatch)
    snapshot = check_disk_space(
        Path("."),
        required_bytes=200,
        reserve_fraction=0.15,
    )
    assert snapshot == DiskSpaceSnapshot(1000, 500, 500, 200, 150, 300)


def test_disk_space_accepts_exact_available_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disk_usage(monkeypatch)
    snapshot = check_disk_space(
        Path("."),
        required_bytes=350,
        reserve_fraction=0.15,
    )
    assert snapshot.free_after_write_bytes == snapshot.reserve_bytes == 150


def test_disk_space_accepts_zero_required_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disk_usage(monkeypatch)
    assert (
        check_disk_space(
            Path("."),
            required_bytes=0,
            reserve_fraction=0.5,
        ).free_after_write_bytes
        == 500
    )


def test_disk_space_rejects_insufficient_free_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disk_usage(monkeypatch)
    with pytest.raises(ResourceLimitError, match="exceed free"):
        check_disk_space(Path("."), required_bytes=501, reserve_fraction=0)


def test_disk_space_rejects_reserve_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disk_usage(monkeypatch)
    with pytest.raises(ResourceLimitError, match="reserve"):
        check_disk_space(Path("."), required_bytes=351, reserve_fraction=0.15)


@pytest.mark.parametrize("required", [True, -1, 1.5])
def test_disk_space_rejects_invalid_required_bytes(required: object) -> None:
    with pytest.raises(ValidationError, match="required_bytes"):
        check_disk_space(Path("."), required_bytes=required)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "fraction",
    [True, -0.1, 1, 1.1, 10**1000, math.nan, math.inf, -math.inf, "0.15"],
)
def test_disk_space_rejects_invalid_reserve_fractions(fraction: object) -> None:
    with pytest.raises(ValidationError, match="reserve_fraction"):
        check_disk_space(Path("."), reserve_fraction=fraction)  # type: ignore[arg-type]


def test_prepare_creates_only_approved_layout_and_marker(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    assert run_directory.artifacts_path.is_dir()
    assert run_directory.manifests_path.is_dir()
    assert run_directory.temporary_path.is_dir()
    assert (run_directory.path / ".run.partial").read_bytes() == b"partial\n"
    assert sorted(path.name for path in run_directory.path.iterdir()) == [
        ".run.partial",
        ".temporary",
        "artifacts",
        "manifests",
    ]


def test_prepare_partial_requires_explicit_resume(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    with pytest.raises(ArtifactError, match="resume=True"):
        prepare_run_directory(
            run_directory.repository_root,
            "results",
            RUN_ID,
            reserve_fraction=0,
        )

    resumed = prepare_run_directory(
        run_directory.repository_root,
        "results",
        RUN_ID,
        resume=True,
        reserve_fraction=0,
    )
    assert resumed == run_directory


def test_prepare_rejects_complete_run(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    finalize_run_directory(run_directory)
    with pytest.raises(ArtifactError, match="immutable"):
        prepare_run_directory(
            run_directory.repository_root,
            "results",
            RUN_ID,
            resume=True,
            reserve_fraction=0,
        )


def test_prepare_rejects_invalid_repository_root(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must exist"):
        prepare_run_directory(
            (tmp_path / "missing").resolve(),
            "results",
            RUN_ID,
            reserve_fraction=0,
        )


@pytest.mark.parametrize("results_root", [Path("/absolute"), "../outside"])
def test_prepare_rejects_unsafe_results_root(
    tmp_path: Path,
    results_root: Path | str,
) -> None:
    with pytest.raises(ValidationError):
        prepare_run_directory(
            tmp_path.resolve(),
            results_root,
            RUN_ID,
            reserve_fraction=0,
        )


def test_disk_preflight_failure_creates_no_run(tmp_path: Path) -> None:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir()
    with pytest.raises(ResourceLimitError):
        prepare_run_directory(
            repository,
            "results",
            RUN_ID,
            required_bytes=10**30,
            reserve_fraction=0,
        )
    assert (
        inspect_run_directory(repository, "results", RUN_ID)
        is RunDirectoryState.MISSING
    )


def test_repeated_path_calculation_is_deterministic(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    resumed = prepare_run_directory(
        run_directory.repository_root,
        "results",
        RUN_ID,
        resume=True,
        reserve_fraction=0,
    )
    assert resumed.path == run_directory.path


def test_atomic_write_bytes_records_exact_content(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    data = b"\x00artifact\xff"
    written = atomic_write_bytes(run_directory, "artifacts/value.bin", data)
    destination = run_directory.repository_root / written.relative_path

    assert destination.read_bytes() == data
    assert written.size_bytes == len(data)
    assert written.content_checksum == hashlib.sha256(data).hexdigest()
    assert written.relative_path.is_relative_to(Path("results"))
    assert not written.relative_path.is_absolute()


def test_atomic_text_and_json_write_exact_bytes(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    text = "héllo\n"
    text_written = atomic_write_text(run_directory, "artifacts/nested/text.txt", text)
    value = {"z": 2, "a": [1, True]}
    json_written = atomic_write_canonical_json(
        run_directory,
        "manifests/value.json",
        value,
    )

    assert (
        run_directory.repository_root / text_written.relative_path
    ).read_bytes() == (text.encode("utf-8"))
    assert (
        run_directory.repository_root / json_written.relative_path
    ).read_bytes() == (canonical_json_bytes(value))
    assert not canonical_json_bytes(value).endswith(b"\n")


def test_atomic_generated_file_records_multichunk_content(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    chunks = (b"first", b"\x00second", b"\xffthird")
    temporary_paths: list[Path] = []

    def write_chunks(path: Path) -> None:
        temporary_paths.append(path)
        assert path.parent == run_directory.artifacts_path
        assert path.name.endswith(".partial")
        with path.open("wb") as stream:
            for chunk in chunks:
                stream.write(chunk)

    written = atomic_write_generated_file(
        run_directory,
        "artifacts/generated.bin",
        write_chunks,
    )
    expected = b"".join(chunks)
    destination = run_directory.repository_root / written.relative_path

    assert destination.read_bytes() == expected
    assert written.size_bytes == len(expected)
    assert written.content_checksum == hashlib.sha256(expected).hexdigest()
    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()
    assert list_partial_artifacts(run_directory) == ()


def test_atomic_generated_file_rejects_destination_conflict(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    atomic_write_bytes(run_directory, "artifacts/generated.bin", b"winner")
    called = False

    def writer(path: Path) -> None:
        nonlocal called
        called = True
        path.write_bytes(b"loser")

    with pytest.raises(ArtifactError, match="already exists"):
        atomic_write_generated_file(
            run_directory,
            "artifacts/generated.bin",
            writer,
        )
    assert called is False
    assert (run_directory.artifacts_path / "generated.bin").read_bytes() == b"winner"


def test_atomic_generated_file_preserves_writer_error_and_cleans_up(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)
    error = RuntimeError("writer failed")

    def writer(path: Path) -> None:
        path.write_bytes(b"partial")
        raise error

    with pytest.raises(RuntimeError, match="writer failed") as captured:
        atomic_write_generated_file(
            run_directory,
            "artifacts/generated.bin",
            writer,
        )
    assert captured.value is error
    assert not (run_directory.artifacts_path / "generated.bin").exists()
    assert list_partial_artifacts(run_directory) == ()


def test_atomic_generated_file_requires_regular_file_output(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)

    def remove_output(path: Path) -> None:
        path.unlink()

    with pytest.raises(ArtifactError, match="did not create"):
        atomic_write_generated_file(
            run_directory,
            "artifacts/missing.bin",
            remove_output,
        )

    def create_directory(path: Path) -> None:
        path.unlink()
        path.mkdir()

    with pytest.raises(ArtifactError, match="regular non-symlink"):
        atomic_write_generated_file(
            run_directory,
            "artifacts/directory.bin",
            create_directory,
        )
    assert list_partial_artifacts(run_directory) == ()


def test_atomic_generated_file_rejects_symlink_output(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")

    def create_symlink(path: Path) -> None:
        path.unlink()
        _symlink_or_skip(path, outside)

    with pytest.raises(ArtifactError, match="regular non-symlink"):
        atomic_write_generated_file(
            run_directory,
            "artifacts/symlink.bin",
            create_symlink,
        )
    assert outside.read_bytes() == b"outside"
    assert list_partial_artifacts(run_directory) == ()


def test_atomic_generated_file_rejects_reserved_and_complete_run_writes(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)

    def writer(path: Path) -> None:
        path.write_bytes(b"value")

    with pytest.raises(ArtifactError, match="reserved"):
        atomic_write_generated_file(run_directory, ".temporary/value.bin", writer)

    finalize_run_directory(run_directory)
    with pytest.raises(ArtifactError, match="immutable"):
        atomic_write_generated_file(
            run_directory,
            "artifacts/late.bin",
            writer,
        )


def test_atomic_generated_file_requires_none_return(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)

    def writer(path: Path) -> object:
        path.write_bytes(b"value")
        return object()

    with pytest.raises(ArtifactError, match="return None"):
        atomic_write_generated_file(
            run_directory,
            "artifacts/generated.bin",
            writer,  # type: ignore[arg-type]
        )
    assert list_partial_artifacts(run_directory) == ()


def test_atomic_write_rejects_existing_destination(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    atomic_write_bytes(run_directory, "artifacts/value.bin", b"first")
    with pytest.raises(ArtifactError, match="already exists"):
        atomic_write_bytes(run_directory, "artifacts/value.bin", b"second")
    assert (run_directory.artifacts_path / "value.bin").read_bytes() == b"first"


@pytest.mark.parametrize(
    "relative_path",
    [
        ".run.partial",
        ".run.complete",
        ".temporary",
        ".temporary/caller.partial",
    ],
)
def test_atomic_write_rejects_reserved_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    run_directory = _prepared(tmp_path)
    with pytest.raises(ArtifactError, match="reserved"):
        atomic_write_bytes(run_directory, relative_path, b"value")


@pytest.mark.parametrize(
    "relative_path", ["/absolute", "../outside", "a/../../outside"]
)
def test_atomic_write_rejects_absolute_and_traversal_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    run_directory = _prepared(tmp_path)
    with pytest.raises(ArtifactError):
        atomic_write_bytes(run_directory, relative_path, b"value")


def test_atomic_write_rejects_destination_symlink_escape(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = run_directory.artifacts_path / "linked.bin"
    _symlink_or_skip(link, outside)

    with pytest.raises(ArtifactError, match="outside"):
        atomic_write_bytes(run_directory, "artifacts/linked.bin", b"replacement")
    assert outside.read_bytes() == b"outside"


def test_atomic_write_rejects_resolved_destination_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = _prepared(tmp_path)
    destination = run_directory.artifacts_path / "linked.bin"
    outside = (tmp_path / "outside.bin").resolve()
    real_resolve = Path.resolve

    def resolve_path(path: Path, strict: bool = False) -> Path:
        if path == destination:
            return outside
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_path)
    with pytest.raises(ArtifactError, match="outside"):
        atomic_write_bytes(run_directory, "artifacts/linked.bin", b"replacement")


def test_failed_write_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = _prepared(tmp_path)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr("kinematicweave.artifact_store.os.fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_bytes(run_directory, "artifacts/value.bin", b"value")

    assert not (run_directory.artifacts_path / "value.bin").exists()
    assert list_partial_artifacts(run_directory) == ()


def test_concurrent_destination_winner_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = _prepared(tmp_path)
    destination = run_directory.artifacts_path / "race.bin"
    real_link = os.link

    def install_winner(source: Path, target: Path) -> None:
        Path(target).write_bytes(b"winner")
        real_link(source, target)

    monkeypatch.setattr("kinematicweave.artifact_store.os.link", install_winner)
    with pytest.raises(ArtifactError, match="already exists"):
        atomic_write_bytes(run_directory, "artifacts/race.bin", b"loser")

    assert destination.read_bytes() == b"winner"
    assert list_partial_artifacts(run_directory) == ()


def test_partial_listing_is_recursive_sorted_and_filtered(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    first = run_directory.artifacts_path / "a.partial"
    second = run_directory.manifests_path / "nested" / "z.partial"
    second.parent.mkdir()
    first.write_bytes(b"a")
    second.write_bytes(b"z")
    (run_directory.artifacts_path / "ordinary.txt").write_text("ok", encoding="utf-8")

    assert list_partial_artifacts(run_directory) == tuple(
        sorted(
            (
                repository_relative_artifact_path(run_directory, first),
                repository_relative_artifact_path(run_directory, second),
            ),
            key=Path.as_posix,
        )
    )


def test_finalize_replaces_marker_and_blocks_later_writes(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    finalize_run_directory(run_directory)

    assert not (run_directory.path / ".run.partial").exists()
    assert (run_directory.path / ".run.complete").read_bytes() == b"complete\n"
    assert (
        inspect_run_directory(
            run_directory.repository_root,
            "results",
            RUN_ID,
        )
        is RunDirectoryState.COMPLETE
    )
    with pytest.raises(ArtifactError, match="immutable"):
        atomic_write_text(run_directory, "artifacts/late.txt", "late")
    with pytest.raises(ArtifactError, match="immutable"):
        finalize_run_directory(run_directory)


def test_partial_artifact_blocks_finalization(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    (run_directory.artifacts_path / "unfinished.partial").write_bytes(b"partial")
    with pytest.raises(ArtifactError, match="partial artifacts"):
        finalize_run_directory(run_directory)
    assert (run_directory.path / ".run.partial").is_file()


@pytest.mark.parametrize("missing_name", ["artifacts", "manifests"])
def test_missing_required_directory_blocks_finalization(
    tmp_path: Path,
    missing_name: str,
) -> None:
    run_directory = _prepared(tmp_path)
    getattr(run_directory, f"{missing_name}_path").rmdir()
    with pytest.raises(ArtifactError, match="missing"):
        finalize_run_directory(run_directory)


def test_repository_relative_artifact_path_accepts_contained_file(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)
    path = run_directory.artifacts_path / "value.bin"
    path.write_bytes(b"value")
    relative = repository_relative_artifact_path(run_directory, path)
    assert relative == path.relative_to(run_directory.repository_root)


def test_repository_relative_artifact_path_rejects_repository_escape(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)
    outside = tmp_path / "outside.bin"
    with pytest.raises(ArtifactError, match="repository_root"):
        repository_relative_artifact_path(run_directory, outside)


def test_repository_relative_artifact_path_rejects_run_escape(tmp_path: Path) -> None:
    run_directory = _prepared(tmp_path)
    elsewhere = run_directory.repository_root / "elsewhere.bin"
    with pytest.raises(ArtifactError, match="run directory"):
        repository_relative_artifact_path(run_directory, elsewhere)


def test_repository_relative_artifact_path_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    run_directory = _prepared(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = run_directory.artifacts_path / "linked.bin"
    _symlink_or_skip(link, outside)
    with pytest.raises(ArtifactError, match="outside"):
        repository_relative_artifact_path(run_directory, link)


def test_repository_relative_path_rejects_resolved_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = _prepared(tmp_path)
    link = run_directory.artifacts_path / "linked.bin"
    outside = (tmp_path / "outside.bin").resolve()
    real_resolve = Path.resolve

    def resolve_path(path: Path, strict: bool = False) -> Path:
        if path == link:
            return outside
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_path)
    with pytest.raises(ArtifactError, match="outside"):
        repository_relative_artifact_path(run_directory, link)


def test_import_performs_no_filesystem_changes(tmp_path: Path) -> None:
    before = tuple(tmp_path.iterdir())
    module_name = "_kinematicweave_artifact_store_import_probe"
    specification = importlib.util.spec_from_file_location(
        module_name,
        artifact_store.__file__,
    )
    assert specification is not None
    assert specification.loader is not None
    imported = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = imported
    try:
        specification.loader.exec_module(imported)
    finally:
        del sys.modules[module_name]
    assert tuple(tmp_path.iterdir()) == before


def test_config_manifest_and_artifact_integration(tmp_path: Path) -> None:
    config = load_config(PROJECT_ROOT / "configs" / "project.toml")
    repository = tmp_path / "repository"
    repository.mkdir()
    run_directory = prepare_run_directory(
        repository,
        config.paths.results,
        "run:integration",
        reserve_fraction=0,
    )

    sample = atomic_write_bytes(
        run_directory,
        "artifacts/sample.bin",
        b"sample artifact",
    )
    experiment = ExperimentManifest(
        schema_version="1.0",
        run_id="run:integration",
        experiment_id="experiment:integration",
        experiment_version="1.0",
        status=RunStatus.PLANNED,
        config_path=Path("configs/project.toml"),
        resolved_config_json=config_to_canonical_json(config),
        dataset_id="dataset-integration",
        split_name="validation",
        unit_type=ExperimentalUnitType.SCENARIO,
        planned_unit_count=1,
        completed_unit_count=0,
        failed_unit_count=0,
        method_ids=("method-demo",),
        metric_names=("metric-demo",),
        seeds=(config.root_seed,),
        git_commit=GIT_COMMIT,
        git_dirty=False,
        python_version="3.12.13",
        environment_lock_id=LOCK_ID,
        host_id="host-integration",
        start_time_utc=None,
        end_time_utc=None,
        raw_result_paths=(),
        log_paths=(),
        failure_summary=None,
    )
    artifact = ArtifactManifest(
        schema_version="1.0",
        artifact_id="artifact:sample",
        artifact_type=ArtifactType.RAW_RESULT,
        path=sample.relative_path,
        producer="kinematicweave.experiments",
        producer_version="0.1.0a0",
        run_id=experiment.run_id,
        source_artifact_ids=(),
        config_id=None,
        git_commit=GIT_COMMIT,
        content_checksum=sample.content_checksum,
        size_bytes=sample.size_bytes,
        created_time_utc=None,
        metadata_json=None,
    )

    experiment_written = atomic_write_canonical_json(
        run_directory,
        "manifests/experiment.json",
        experiment_manifest_to_dict(experiment),
    )
    artifact_written = atomic_write_canonical_json(
        run_directory,
        "manifests/artifact.json",
        artifact_manifest_to_dict(artifact),
    )
    for written in (sample, experiment_written, artifact_written):
        content = (repository / written.relative_path).read_bytes()
        assert written.size_bytes == len(content)
        assert written.content_checksum == hashlib.sha256(content).hexdigest()
    assert list_partial_artifacts(run_directory) == ()

    finalize_run_directory(run_directory)
    assert (
        inspect_run_directory(
            repository,
            config.paths.results,
            experiment.run_id,
        )
        is RunDirectoryState.COMPLETE
    )
    with pytest.raises(ArtifactError):
        atomic_write_bytes(run_directory, "artifacts/late.bin", b"late")

    experiment_bytes = (repository / experiment_written.relative_path).read_bytes()
    artifact_bytes = (repository / artifact_written.relative_path).read_bytes()
    assert experiment_manifest_from_json(experiment_bytes.decode()) == experiment
    assert artifact_manifest_from_json(artifact_bytes.decode()) == artifact
