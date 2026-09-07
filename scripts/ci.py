"""Run the complete local quality gate and isolated foundation smoke check."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from kinematicweave.artifact_store import (
    RunDirectoryState,
    inspect_run_directory,
    list_partial_artifacts,
)
from kinematicweave.errors import ArtifactError, ExperimentError, KinematicWeaveError
from kinematicweave.experiments.smoke import FoundationSmokeResult, run_foundation_smoke
from kinematicweave.manifests import (
    RunStatus,
    artifact_manifest_from_json,
    artifact_manifest_to_canonical_json,
    experiment_manifest_from_json,
    experiment_manifest_to_canonical_json,
)

__all__ = ["main"]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RUN_ID = "run:ci:foundation-smoke"
_COPIED_INPUTS = (
    Path("configs/project.toml"),
    Path("uv.lock"),
)
_EXPERIMENT_MANIFESTS = (
    ("experiment.planned.json", RunStatus.PLANNED),
    ("experiment.running.json", RunStatus.RUNNING),
    ("experiment.complete.json", RunStatus.COMPLETE),
)


class _SubprocessFailure(Exception):
    """A checked subprocess returned a nonzero status."""

    def __init__(self, command: tuple[str, ...], return_code: int) -> None:
        self.command = command
        self.return_code = return_code
        super().__init__(f"{command[0]} exited with status {return_code}")


def _write_stdout(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def _write_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def _run_subprocess(command: tuple[str, ...], *, cwd: Path) -> int:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        shell=False,
    )
    return completed.returncode


def _run_checked_subprocess(command: tuple[str, ...], *, cwd: Path) -> None:
    return_code = _run_subprocess(command, cwd=cwd)
    if return_code != 0:
        raise _SubprocessFailure(command, return_code)


def _run_quality(repository_root: Path) -> int:
    return _run_subprocess(
        (
            sys.executable,
            str(repository_root / "scripts" / "quality.py"),
        ),
        cwd=repository_root,
    )


def _copy_smoke_inputs(source_root: Path, temporary_root: Path) -> None:
    for relative_path in _COPIED_INPUTS:
        source = source_root / relative_path
        destination = temporary_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    copied_files = tuple(
        sorted(
            (
                path.relative_to(temporary_root)
                for path in temporary_root.rglob("*")
                if path.is_file()
            ),
            key=Path.as_posix,
        )
    )
    if copied_files != _COPIED_INPUTS:
        raise ExperimentError("isolated smoke input copy was not minimal")


def _initialize_git_repository(repository_root: Path) -> None:
    commands = (
        ("git", "init", "--quiet"),
        ("git", "config", "--local", "user.name", "KinematicWeave CI"),
        ("git", "config", "--local", "user.email", "ci@example.invalid"),
        ("git", "config", "--local", "commit.gpgSign", "false"),
        ("git", "config", "--local", "core.autocrlf", "false"),
        ("git", "add", "--", "configs/project.toml", "uv.lock"),
        (
            "git",
            "commit",
            "--quiet",
            "-m",
            "temporary foundation smoke inputs",
        ),
    )
    for command in commands:
        _run_checked_subprocess(command, cwd=repository_root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_experiment_manifests(result: FoundationSmokeResult) -> None:
    for file_name, expected_status in _EXPERIMENT_MANIFESTS:
        path = result.run_directory.manifests_path / file_name
        manifest = experiment_manifest_from_json(path.read_text(encoding="utf-8"))
        if manifest.status is not expected_status:
            raise ExperimentError(
                f"unexpected lifecycle status in {file_name}: {manifest.status.value}"
            )
        round_trip = experiment_manifest_from_json(
            experiment_manifest_to_canonical_json(manifest)
        )
        if round_trip != manifest:
            raise ExperimentError(f"experiment manifest round trip failed: {file_name}")


def _verify_artifact_manifests(
    temporary_root: Path,
    result: FoundationSmokeResult,
) -> None:
    manifest_paths = tuple(
        sorted(
            result.run_directory.manifests_path.glob("artifact.*.json"),
            key=lambda path: path.name,
        )
    )
    if len(manifest_paths) != 3:
        raise ExperimentError("foundation smoke must produce three artifact manifests")

    for manifest_path in manifest_paths:
        manifest = artifact_manifest_from_json(
            manifest_path.read_text(encoding="utf-8")
        )
        round_trip = artifact_manifest_from_json(
            artifact_manifest_to_canonical_json(manifest)
        )
        if round_trip != manifest:
            raise ExperimentError(
                f"artifact manifest round trip failed: {manifest_path.name}"
            )

        artifact_path = temporary_root / manifest.path
        if not artifact_path.is_file():
            raise ExperimentError(
                f"artifact referenced by {manifest_path.name} does not exist"
            )
        if artifact_path.stat().st_size != manifest.size_bytes:
            raise ExperimentError(f"artifact size does not match {manifest_path.name}")
        if _sha256(artifact_path) != manifest.content_checksum:
            raise ExperimentError(
                f"artifact checksum does not match {manifest_path.name}"
            )


def _verify_smoke_result(
    temporary_root: Path,
    result: FoundationSmokeResult,
) -> None:
    if result.diagnostic_value != 30:
        raise ExperimentError("foundation smoke diagnostic value must equal 30")

    results_root = result.run_directory.results_root.relative_to(temporary_root)
    state = inspect_run_directory(temporary_root, results_root, _RUN_ID)
    if state is not RunDirectoryState.COMPLETE:
        raise ExperimentError("foundation smoke run did not finalize as complete")
    if list_partial_artifacts(result.run_directory):
        raise ExperimentError("foundation smoke run retained partial artifacts")

    _verify_experiment_manifests(result)
    _verify_artifact_manifests(temporary_root, result)

    report_path = temporary_root / result.report_artifact.relative_path
    if not report_path.is_file():
        raise ExperimentError("foundation smoke Markdown report is missing")


def _run_isolated_smoke(source_root: Path) -> None:
    with TemporaryDirectory(prefix="kinematicweave-ci-") as temporary_name:
        temporary_root = Path(temporary_name).resolve()
        _copy_smoke_inputs(source_root, temporary_root)
        _initialize_git_repository(temporary_root)

        result = run_foundation_smoke(
            temporary_root,
            run_id=_RUN_ID,
        )
        _verify_smoke_result(temporary_root, result)

        try:
            run_foundation_smoke(
                temporary_root,
                run_id=_RUN_ID,
            )
        except ArtifactError:
            pass
        else:
            raise ExperimentError(
                "completed foundation smoke run accepted a duplicate invocation"
            )


def main() -> int:
    """Run local quality and an isolated foundation smoke experiment."""
    _write_stdout("[ci] complete quality gate")
    quality_return_code = _run_quality(REPOSITORY_ROOT)
    if quality_return_code != 0:
        _write_stderr(f"[ci] quality gate failed with status {quality_return_code}")
        return quality_return_code

    _write_stdout("[ci] isolated foundation smoke")
    try:
        _run_isolated_smoke(REPOSITORY_ROOT)
    except _SubprocessFailure as error:
        _write_stderr(f"[ci] smoke subprocess failed: {error}")
        return error.return_code
    except (KinematicWeaveError, OSError) as error:
        _write_stderr(f"[ci] isolated smoke failed: {error}")
        return 2

    _write_stdout("[ci] all stages passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
