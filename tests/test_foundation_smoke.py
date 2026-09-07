"""Tests for the deterministic foundation smoke pipeline."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, NoReturn, cast

import pytest

from kinematicweave.artifact_store import (
    RunDirectoryState,
    atomic_write_text,
    inspect_run_directory,
    list_partial_artifacts,
)
from kinematicweave.errors import ArtifactError, ExperimentError, ValidationError
from kinematicweave.experiments import smoke as smoke_module
from kinematicweave.experiments.smoke import (
    FoundationSmokeResult,
    run_foundation_smoke,
)
from kinematicweave.manifests import (
    RunStatus,
    artifact_manifest_from_json,
    experiment_manifest_from_json,
)
from kinematicweave.seeding import derive_seed
from kinematicweave.system_metadata import (
    CpuMetadata,
    DiskMetadata,
    GitMetadata,
    MemoryMetadata,
    OperatingSystemMetadata,
    PythonMetadata,
    SystemMetadata,
    WslMetadata,
    system_metadata_to_dict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_foundation_smoke.py"
GIT_COMMIT = "a" * 40
LOCK_ID = "b" * 64


def _sample_metadata(
    *,
    git_available: bool = True,
    environment_lock_id: str | None = LOCK_ID,
) -> SystemMetadata:
    return SystemMetadata(
        schema_version="1.0",
        captured_at_utc="2026-01-02T03:04:05.006789Z",
        package_version="0.1.0a0",
        operating_system=OperatingSystemMetadata(
            system="Linux",
            release="6.8",
            version="Build 1",
            machine="x86_64",
        ),
        python=PythonMetadata(
            version="3.12.13",
            implementation="CPython",
            executable="/temporary/python",
        ),
        git=GitMetadata(
            available=git_available,
            commit=GIT_COMMIT if git_available else None,
            dirty=False if git_available else None,
        ),
        cpu=CpuMetadata(processor="Synthetic CPU", logical_cpu_count=8),
        memory=MemoryMetadata(total_bytes=16 * 1024**3),
        gpus=(),
        cuda_version=None,
        disk=DiskMetadata(
            total_bytes=1000,
            used_bytes=400,
            free_bytes=600,
        ),
        wsl=WslMetadata(is_wsl=False, distribution=None, version=None),
        environment_lock_id=environment_lock_id,
    )


def _create_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    config_directory = repository / "configs"
    config_directory.mkdir(parents=True)
    (config_directory / "project.toml").write_text(
        (
            'schema_version = "1.0"\n'
            'project_name = "KinematicWeave Test"\n'
            "root_seed = 7\n\n"
            "[paths]\n"
            'data = "data"\n'
            'results = "results"\n'
            'reports = "reports"\n'
            'figures = "figures"\n'
            'qualitative = "qualitative"\n'
        ),
        encoding="utf-8",
    )
    return repository


def _mock_runtime(
    monkeypatch: pytest.MonkeyPatch,
    metadata: SystemMetadata | None = None,
) -> SystemMetadata:
    selected_metadata = _sample_metadata() if metadata is None else metadata
    monkeypatch.setattr(
        smoke_module,
        "capture_system_metadata",
        lambda repository_root: selected_metadata,
    )
    base = datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC)
    calls = 0

    def timestamp() -> str:
        nonlocal calls
        value = base + timedelta(seconds=calls)
        calls += 1
        return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    monkeypatch.setattr(smoke_module, "utc_now_timestamp", timestamp)
    return selected_metadata


def _successful_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str = "run:foundation-smoke:test",
) -> tuple[Path, FoundationSmokeResult, SystemMetadata]:
    repository = _create_repository(tmp_path)
    metadata = _mock_runtime(monkeypatch)
    result = run_foundation_smoke(repository, run_id=run_id)
    return repository, result, metadata


def _read_experiment_manifest(run_path: Path, name: str) -> Any:
    return experiment_manifest_from_json(
        (run_path / "manifests" / name).read_text(encoding="utf-8")
    )


def _read_artifact_manifest(run_path: Path, name: str) -> Any:
    return artifact_manifest_from_json(
        (run_path / "manifests" / name).read_text(encoding="utf-8")
    )


def _assert_written_artifact(repository: Path, artifact: Any) -> None:
    data = (repository / artifact.relative_path).read_bytes()
    assert len(data) == artifact.size_bytes
    assert hashlib.sha256(data).hexdigest() == artifact.content_checksum


def _load_script() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "_foundation_smoke_script_test",
        SCRIPT_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    finally:
        del sys.modules[specification.name]
    return module


def test_public_exports_are_exact() -> None:
    assert smoke_module.__all__ == [
        "FoundationSmokeResult",
        "run_foundation_smoke",
    ]


def test_successful_smoke_run_and_manifest_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, result, metadata = _successful_run(tmp_path, monkeypatch)
    run_path = result.run_directory.path

    assert result.diagnostic_value == 30
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.diagnostic_value = 31  # type: ignore[misc]

    expected_files = {
        ".run.complete",
        "artifacts/foundation_smoke/report.md",
        "artifacts/foundation_smoke/synthetic_record.json",
        "artifacts/foundation_smoke/system_metadata.json",
        "manifests/artifact.report.json",
        "manifests/artifact.synthetic.json",
        "manifests/artifact.system_metadata.json",
        "manifests/experiment.complete.json",
        "manifests/experiment.planned.json",
        "manifests/experiment.running.json",
    }
    actual_files = {
        path.relative_to(run_path).as_posix()
        for path in run_path.rglob("*")
        if path.is_file()
    }
    assert actual_files == expected_files

    payload = json.loads(
        (repository / result.synthetic_artifact.relative_path).read_text(
            encoding="utf-8"
        )
    )
    assert payload == {
        "artifact_kind": "foundation_smoke",
        "derived_seed": derive_seed(7, "foundation-smoke", "diagnostic"),
        "diagnostic_name": "sum_of_squares",
        "diagnostic_value": 30,
        "root_seed": 7,
        "schema_version": "1.0",
        "values": [1, 2, 3, 4],
    }

    for artifact in (
        result.synthetic_artifact,
        result.system_metadata_artifact,
        result.report_artifact,
    ):
        _assert_written_artifact(repository, artifact)

    planned = _read_experiment_manifest(
        run_path,
        "experiment.planned.json",
    )
    running = _read_experiment_manifest(
        run_path,
        "experiment.running.json",
    )
    complete = _read_experiment_manifest(
        run_path,
        "experiment.complete.json",
    )
    assert [planned.status, running.status, complete.status] == [
        RunStatus.PLANNED,
        RunStatus.RUNNING,
        RunStatus.COMPLETE,
    ]
    assert planned.start_time_utc is None
    assert planned.end_time_utc is None
    assert running.start_time_utc is not None
    assert running.end_time_utc is None
    assert complete.start_time_utc == running.start_time_utc
    assert complete.end_time_utc is not None
    assert complete.completed_unit_count == 1
    assert complete.failed_unit_count == 0
    assert complete.failure_summary is None
    assert complete.raw_result_paths == (result.synthetic_artifact.relative_path,)
    assert complete.log_paths == ()
    assert {
        planned.resolved_config_json,
        running.resolved_config_json,
        complete.resolved_config_json,
    } == {planned.resolved_config_json}

    synthetic_manifest = _read_artifact_manifest(
        run_path,
        "artifact.synthetic.json",
    )
    system_manifest = _read_artifact_manifest(
        run_path,
        "artifact.system_metadata.json",
    )
    report_manifest = _read_artifact_manifest(
        run_path,
        "artifact.report.json",
    )
    assert synthetic_manifest.path == result.synthetic_artifact.relative_path
    assert system_manifest.path == result.system_metadata_artifact.relative_path
    assert report_manifest.path == result.report_artifact.relative_path
    assert report_manifest.source_artifact_ids == (
        synthetic_manifest.artifact_id,
        system_manifest.artifact_id,
    )
    for manifest, artifact in (
        (synthetic_manifest, result.synthetic_artifact),
        (system_manifest, result.system_metadata_artifact),
        (report_manifest, result.report_artifact),
    ):
        assert manifest.run_id == result.run_id
        assert manifest.git_commit == GIT_COMMIT
        assert manifest.config_id is not None
        assert manifest.path == artifact.relative_path
        assert manifest.size_bytes == artifact.size_bytes
        assert manifest.content_checksum == artifact.content_checksum

    persisted_metadata = json.loads(
        (repository / result.system_metadata_artifact.relative_path).read_text(
            encoding="utf-8"
        )
    )
    assert persisted_metadata == system_metadata_to_dict(metadata)

    report = (repository / result.report_artifact.relative_path).read_text(
        encoding="utf-8"
    )
    for expected in (
        "# Foundation Smoke Report",
        result.run_id,
        "experiment:foundation-smoke:v1",
        "KinematicWeave Test",
        "sum_of_squares = 30",
        result.synthetic_artifact.relative_path.as_posix(),
        result.synthetic_artifact.content_checksum,
        result.system_metadata_artifact.relative_path.as_posix(),
        result.system_metadata_artifact.content_checksum,
        GIT_COMMIT,
        LOCK_ID,
        "Final intended status: `complete`",
    ):
        assert expected in report
    assert str(repository) not in report

    assert (
        inspect_run_directory(repository, "results", result.run_id)
        is RunDirectoryState.COMPLETE
    )
    assert list_partial_artifacts(result.run_directory) == ()
    assert not (run_path / ".run.partial").exists()
    with pytest.raises(ArtifactError, match="immutable"):
        atomic_write_text(result.run_directory, "artifacts/later.txt", "later")


def test_result_model_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, result, _ = _successful_run(tmp_path, monkeypatch)
    with pytest.raises(ValidationError, match="diagnostic_value"):
        FoundationSmokeResult(
            run_id=result.run_id,
            run_directory=result.run_directory,
            diagnostic_value=True,
            synthetic_artifact=result.synthetic_artifact,
            system_metadata_artifact=result.system_metadata_artifact,
            report_artifact=result.report_artifact,
            complete_manifest_path=result.complete_manifest_path,
        )


def test_completed_run_rejects_second_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, result, _ = _successful_run(tmp_path, monkeypatch)
    with pytest.raises(ArtifactError, match="immutable"):
        run_foundation_smoke(repository, run_id=result.run_id)


def test_failure_after_preparation_preserves_artifacts_and_partial_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _create_repository(tmp_path)
    _mock_runtime(monkeypatch)

    def fail_report(**kwargs: object) -> NoReturn:
        raise RuntimeError("injected report failure")

    monkeypatch.setattr(smoke_module, "_build_report", fail_report)
    with pytest.raises(RuntimeError, match="injected report failure"):
        run_foundation_smoke(repository, run_id="run:foundation-smoke:failure")

    results_runs = repository / "results" / "runs"
    run_path = next(results_runs.iterdir())
    failed = _read_experiment_manifest(run_path, "experiment.failed.json")
    assert failed.status is RunStatus.FAILED
    assert failed.end_time_utc is not None
    assert failed.failure_summary == "RuntimeError: injected report failure"
    assert (run_path / ".run.partial").is_file()
    assert not (run_path / ".run.complete").exists()
    assert (run_path / "artifacts/foundation_smoke/synthetic_record.json").is_file()
    assert (run_path / "artifacts/foundation_smoke/system_metadata.json").is_file()
    assert (
        inspect_run_directory(
            repository,
            "results",
            "run:foundation-smoke:failure",
        )
        is RunDirectoryState.PARTIAL
    )


@pytest.mark.parametrize(
    ("repository_factory", "run_id", "config_path", "results_root", "message"),
    [
        (
            lambda path: path / "missing",
            "run:valid",
            "configs/project.toml",
            None,
            "existing directory",
        ),
        (
            _create_repository,
            "invalid",
            "configs/project.toml",
            None,
            "identifier",
        ),
        (
            _create_repository,
            "run:valid",
            "../outside.toml",
            None,
            "inside repository_root",
        ),
        (
            _create_repository,
            "run:valid",
            "configs/project.toml",
            "../outside",
            "parent traversal",
        ),
    ],
)
def test_invalid_inputs_are_rejected_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_factory: Any,
    run_id: str,
    config_path: str,
    results_root: str | None,
    message: str,
) -> None:
    repository = repository_factory(tmp_path)
    _mock_runtime(monkeypatch)
    with pytest.raises(ValidationError, match=message):
        run_foundation_smoke(
            repository,
            run_id=run_id,
            config_path=config_path,
            results_root=results_root,
        )
    assert not (repository / "results").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        _sample_metadata(git_available=False),
        _sample_metadata(environment_lock_id=None),
    ],
)
def test_required_metadata_is_checked_before_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: SystemMetadata,
) -> None:
    repository = _create_repository(tmp_path)
    _mock_runtime(monkeypatch, metadata)
    with pytest.raises(ExperimentError):
        run_foundation_smoke(repository, run_id="run:metadata:missing")
    assert not (repository / "results").exists()


def test_run_id_has_no_default() -> None:
    parameter = inspect.signature(run_foundation_smoke).parameters["run_id"]
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError, match="run_id"):
        run_foundation_smoke(PROJECT_ROOT)  # type: ignore[call-arg]


def test_module_import_performs_no_file_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("module import attempted a file write")

    monkeypatch.setattr(Path, "write_text", fail)
    monkeypatch.setattr(Path, "write_bytes", fail)
    monkeypatch.setattr(Path, "mkdir", fail)

    specification = importlib.util.spec_from_file_location(
        "_foundation_smoke_import_probe",
        smoke_module.__file__,
    )
    assert specification is not None
    assert specification.loader is not None
    imported = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = imported
    try:
        specification.loader.exec_module(imported)
    finally:
        del sys.modules[specification.name]


def test_real_repository_integration_is_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, result, _ = _successful_run(
        tmp_path,
        monkeypatch,
        run_id="run:foundation-smoke:integration",
    )
    assert repository != PROJECT_ROOT
    assert result.run_directory.repository_root == repository.resolve()
    assert (
        _read_experiment_manifest(
            result.run_directory.path,
            "experiment.complete.json",
        ).status
        is RunStatus.COMPLETE
    )
    for name in (
        "artifact.synthetic.json",
        "artifact.system_metadata.json",
        "artifact.report.json",
    ):
        assert _read_artifact_manifest(result.run_directory.path, name).run_id == (
            result.run_id
        )


def test_script_success_and_stdout_stderr_separation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository, result, _ = _successful_run(tmp_path, monkeypatch)
    script = cast(Any, _load_script())
    monkeypatch.setattr(script, "run_foundation_smoke", lambda *args, **kwargs: result)

    assert script.main(["--run-id", result.run_id, "--repo-root", str(repository)]) == 0
    output = capsys.readouterr()
    assert f"run_id: {result.run_id}" in output.out
    assert "diagnostic_value: 30" in output.out
    assert output.err == ""


def test_script_missing_run_id_and_expected_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = cast(Any, _load_script())
    with pytest.raises(SystemExit) as missing:
        script.main([])
    assert missing.value.code == 2
    missing_output = capsys.readouterr()
    assert missing_output.out == ""
    assert "--run-id" in missing_output.err

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise ExperimentError("expected project failure")

    monkeypatch.setattr(script, "run_foundation_smoke", fail)
    assert script.main(["--run-id", "run:script:error"]) == 2
    error_output = capsys.readouterr()
    assert error_output.out == ""
    assert error_output.err == "error: expected project failure\n"


def test_script_contains_no_pipeline_implementation() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "ExperimentManifest",
        "ArtifactManifest",
        "prepare_run_directory",
        "atomic_write_",
        "finalize_run_directory",
    ):
        assert forbidden not in source
