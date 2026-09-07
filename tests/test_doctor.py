"""Tests for read-only repository and environment diagnostics."""

from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import NoReturn

import pytest

from kinematicweave.artifact_store import DiskSpaceSnapshot
import kinematicweave.doctor as doctor_module
from kinematicweave.doctor import (
    DoctorCheck,
    DoctorCheckStatus,
    DoctorReport,
    doctor_report_to_canonical_json,
    doctor_report_to_dict,
)
from kinematicweave.errors import ResourceLimitError, ValidationError
from kinematicweave.system_metadata import (
    CpuMetadata,
    DiskMetadata,
    GitMetadata,
    GpuMetadata,
    MemoryMetadata,
    OperatingSystemMetadata,
    PythonMetadata,
    SystemMetadata,
    WslMetadata,
)


def _metadata(
    *,
    python_version: str = "3.12.13",
    package_version: str = "0.1.0a0",
    git: GitMetadata | None = None,
    gpus: tuple[GpuMetadata, ...] = (),
    cuda_version: str | None = None,
    wsl: WslMetadata | None = None,
    lock_id: str | None = None,
) -> SystemMetadata:
    return SystemMetadata(
        schema_version="1.0",
        captured_at_utc="2025-01-02T03:04:05.006789Z",
        package_version=package_version,
        operating_system=OperatingSystemMetadata(
            system="Windows",
            release="11",
            version="build",
            machine="AMD64",
        ),
        python=PythonMetadata(
            version=python_version,
            implementation="CPython",
            executable="python",
        ),
        git=git or GitMetadata(available=True, commit="abc123", dirty=False),
        cpu=CpuMetadata(processor="CPU", logical_cpu_count=8),
        memory=MemoryMetadata(total_bytes=16 * 1024**3),
        gpus=gpus,
        cuda_version=cuda_version,
        disk=DiskMetadata(total_bytes=1000, used_bytes=400, free_bytes=600),
        wsl=wsl or WslMetadata(is_wsl=False, distribution=None, version=None),
        environment_lock_id=lock_id,
    )


def _check(status: DoctorCheckStatus = DoctorCheckStatus.PASS) -> DoctorCheck:
    return DoctorCheck(
        check_id="sample",
        status=status,
        message="sample message",
        details=(("first", "1"), ("second", "2")),
    )


def _report(*statuses: DoctorCheckStatus) -> DoctorReport:
    checks = tuple(
        DoctorCheck(
            check_id=f"check-{index}",
            status=status,
            message="checked",
            details=(),
        )
        for index, status in enumerate(statuses)
    )
    return DoctorReport(
        schema_version="1.0",
        overall_status=DoctorCheckStatus.PASS,
        checks=checks,
        system_metadata=_metadata(),
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    for directory in (
        "definitions",
        "configs",
        "scripts",
        "src/kinematicweave",
        "tests",
        "data",
        "results",
        "reports",
        "figures",
        "qualitative",
    ):
        (repository / directory).mkdir(parents=True, exist_ok=True)
    for definition in (
        "README.md",
        "design_freeze_report.md",
        "16_codex_batch_protocol.md",
        "17_codex_report_template.md",
    ):
        (repository / "definitions" / definition).write_text(
            "definition\n",
            encoding="utf-8",
        )
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repository / "uv.lock").write_bytes(b"lock")
    (repository / "README.md").write_text("KinematicWeave\n", encoding="utf-8")
    (repository / "configs" / "project.toml").write_text(
        "\n".join(
            (
                'schema_version = "1.0"',
                'project_name = "KinematicWeave"',
                "root_seed = 0",
                "",
                "[paths]",
                'data = "data"',
                'results = "results"',
                'reports = "reports"',
                'figures = "figures"',
                'qualitative = "qualitative"',
                "",
            )
        ),
        encoding="utf-8",
    )
    return repository


def _completed(
    output: str = "uv 0.11.31\n",
    *,
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["uv"],
        returncode=returncode,
        stdout=output,
        stderr="",
    )


def test_status_enum_values_are_exact() -> None:
    assert [status.value for status in DoctorCheckStatus] == [
        "pass",
        "warning",
        "fail",
    ]


def test_models_are_frozen_and_slotted() -> None:
    check = _check()
    report = _report(DoctorCheckStatus.PASS)
    assert not hasattr(check, "__dict__")
    assert not hasattr(report, "__dict__")
    with pytest.raises(FrozenInstanceError):
        check.message = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.checks = ()  # type: ignore[misc]


@pytest.mark.parametrize("field", ["check_id", "message"])
def test_check_requires_nonempty_trimmed_text(field: str) -> None:
    values = {
        "check_id": " check ",
        "status": DoctorCheckStatus.PASS,
        "message": " message ",
        "details": (),
    }
    normalized = DoctorCheck(**values)  # type: ignore[arg-type]
    assert normalized.check_id == "check"
    assert normalized.message == "message"

    values[field] = " "
    with pytest.raises(ValidationError, match="nonempty"):
        DoctorCheck(**values)  # type: ignore[arg-type]


def test_detail_order_is_preserved_and_keys_are_unique() -> None:
    check = _check()
    assert check.details == (("first", "1"), ("second", "2"))
    with pytest.raises(ValidationError, match="unique"):
        DoctorCheck(
            "check",
            DoctorCheckStatus.PASS,
            "message",
            (("same", "1"), ("same", "2")),
        )


@pytest.mark.parametrize(
    "details",
    [
        [("key", "value")],
        (("key", 1),),
        (("", "value"),),
        (("too", "many", "values"),),
    ],
)
def test_invalid_details_are_rejected(details: object) -> None:
    with pytest.raises(ValidationError):
        DoctorCheck(
            "check",
            DoctorCheckStatus.PASS,
            "message",
            details,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((DoctorCheckStatus.PASS,), DoctorCheckStatus.PASS),
        (
            (DoctorCheckStatus.PASS, DoctorCheckStatus.WARNING),
            DoctorCheckStatus.WARNING,
        ),
        (
            (DoctorCheckStatus.WARNING, DoctorCheckStatus.FAIL),
            DoctorCheckStatus.FAIL,
        ),
    ],
)
def test_overall_status_is_derived(
    statuses: tuple[DoctorCheckStatus, ...],
    expected: DoctorCheckStatus,
) -> None:
    report = _report(*statuses)
    assert report.overall_status is expected


def test_report_validation() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        DoctorReport("2.0", DoctorCheckStatus.PASS, (_check(),), _metadata())
    with pytest.raises(ValidationError, match="nonempty"):
        DoctorReport("1.0", DoctorCheckStatus.PASS, (), _metadata())


def test_repository_check_success_and_missing_entry(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    assert doctor_module._repository_check(repository).status is DoctorCheckStatus.PASS
    (repository / "README.md").unlink()
    failed = doctor_module._repository_check(repository)
    assert failed.status is DoctorCheckStatus.FAIL
    assert "README.md" in failed.details[0][1]


def test_definitions_check_success_and_failure(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    assert doctor_module._definitions_check(repository).status is DoctorCheckStatus.PASS
    (repository / "definitions" / "design_freeze_report.md").unlink()
    failed = doctor_module._definitions_check(repository)
    assert failed.status is DoctorCheckStatus.FAIL
    assert "design_freeze_report.md" in failed.details[0][1]


def test_project_configuration_valid_and_invalid(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    valid, config = doctor_module._project_configuration_check(repository, None)
    assert valid.status is DoctorCheckStatus.PASS
    assert valid.details == (
        ("schema_version", "1.0"),
        ("project_name", "KinematicWeave"),
    )
    assert config is not None

    (repository / "configs" / "project.toml").write_text(
        "unknown = true\n",
        encoding="utf-8",
    )
    invalid, config = doctor_module._project_configuration_check(repository, None)
    assert invalid.status is DoctorCheckStatus.FAIL
    assert config is None


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.12.0", DoctorCheckStatus.PASS),
        ("3.13.9", DoctorCheckStatus.PASS),
        ("3.11.9", DoctorCheckStatus.FAIL),
        ("3.14.0", DoctorCheckStatus.FAIL),
        ("unknown", DoctorCheckStatus.FAIL),
    ],
)
def test_python_supported_and_unsupported(
    version: str,
    expected: DoctorCheckStatus,
) -> None:
    assert (
        doctor_module._python_check(_metadata(python_version=version)).status
        is expected
    )


def test_package_metadata_match_and_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_module, "distribution_version", lambda name: "0.1.0a0")
    assert doctor_module._package_check(_metadata()).status is DoctorCheckStatus.PASS

    monkeypatch.setattr(doctor_module, "distribution_version", lambda name: "9.0")
    assert doctor_module._package_check(_metadata()).status is DoctorCheckStatus.FAIL


def test_uv_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kinematicweave.doctor.shutil.which", lambda name: "uv")
    monkeypatch.setattr(doctor_module, "_run_local_command", lambda args: _completed())
    check, executable = doctor_module._uv_check()
    assert check.status is DoctorCheckStatus.PASS
    assert executable == "uv"


def test_uv_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kinematicweave.doctor.shutil.which", lambda name: None)
    check, executable = doctor_module._uv_check()
    assert check.status is DoctorCheckStatus.FAIL
    assert executable is None


def test_uv_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kinematicweave.doctor.shutil.which", lambda name: "uv")

    def timeout(arguments: list[str]) -> NoReturn:
        raise subprocess.TimeoutExpired(arguments, timeout=10)

    monkeypatch.setattr(doctor_module, "_run_local_command", timeout)
    assert doctor_module._uv_check()[0].status is DoctorCheckStatus.FAIL


def test_uv_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kinematicweave.doctor.shutil.which", lambda name: "uv")
    monkeypatch.setattr(
        doctor_module,
        "_run_local_command",
        lambda args: _completed("broken", returncode=1),
    )
    assert doctor_module._uv_check()[0].status is DoctorCheckStatus.FAIL


def test_lockfile_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path)
    lock_id = hashlib.sha256(b"lock").hexdigest()
    monkeypatch.setattr(
        doctor_module,
        "_run_local_command",
        lambda args, cwd=None: _completed(),
    )
    check = doctor_module._lockfile_check(
        repository,
        _metadata(lock_id=lock_id),
        "uv",
    )
    assert check.status is DoctorCheckStatus.PASS
    assert check.details == (("environment_lock_id", lock_id),)


def test_lockfile_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path)
    lock_id = hashlib.sha256(b"lock").hexdigest()

    def timeout(arguments: list[str], *, cwd: Path | None = None) -> NoReturn:
        raise subprocess.TimeoutExpired(arguments, timeout=10)

    monkeypatch.setattr(doctor_module, "_run_local_command", timeout)
    check = doctor_module._lockfile_check(
        repository,
        _metadata(lock_id=lock_id),
        "uv",
    )
    assert check.status is DoctorCheckStatus.FAIL
    assert "timed out" in check.message


def test_lockfile_command_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    lock_id = hashlib.sha256(b"lock").hexdigest()
    monkeypatch.setattr(
        doctor_module,
        "_run_local_command",
        lambda args, cwd=None: _completed("stale", returncode=1),
    )
    check = doctor_module._lockfile_check(
        repository,
        _metadata(lock_id=lock_id),
        "uv",
    )
    assert check.status is DoctorCheckStatus.FAIL
    assert "not current" in check.message


def test_writable_directory_probes_are_removed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _, config = doctor_module._project_configuration_check(repository, None)
    assert config is not None
    check = doctor_module._writable_directories_check(repository, config)
    assert check.status is DoctorCheckStatus.PASS
    assert list(repository.rglob(".kinematicweave-doctor-*.probe")) == []


def test_unwritable_directory_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    _, config = doctor_module._project_configuration_check(repository, None)
    assert config is not None
    monkeypatch.setattr(
        doctor_module,
        "_probe_directory",
        lambda path: "permission denied" if path.name == "results" else None,
    )
    check = doctor_module._writable_directories_check(repository, config)
    assert check.status is DoctorCheckStatus.FAIL
    assert ("results", "permission denied") in check.details


def test_disk_success_and_reserve_failure() -> None:
    snapshot = DiskSpaceSnapshot(1000, 400, 600, 0, 150, 600)
    metadata = _metadata()
    passed = doctor_module._disk_space_check(snapshot, None, metadata, 0.15)
    failed = doctor_module._disk_space_check(
        None,
        ResourceLimitError("reserve violated"),
        metadata,
        0.75,
    )
    assert passed.status is DoctorCheckStatus.PASS
    assert failed.status is DoctorCheckStatus.FAIL
    assert failed.details[:4] == (
        ("total_bytes", "1000"),
        ("used_bytes", "400"),
        ("free_bytes", "600"),
        ("reserve_bytes", "750"),
    )


@pytest.mark.parametrize(
    ("git", "expected"),
    [
        (
            GitMetadata(available=True, commit="abc", dirty=False),
            DoctorCheckStatus.PASS,
        ),
        (
            GitMetadata(available=True, commit="abc", dirty=True),
            DoctorCheckStatus.WARNING,
        ),
        (
            GitMetadata(available=False, commit=None, dirty=None),
            DoctorCheckStatus.WARNING,
        ),
    ],
)
def test_git_clean_dirty_and_unavailable(
    git: GitMetadata,
    expected: DoctorCheckStatus,
) -> None:
    assert doctor_module._git_check(_metadata(git=git)).status is expected


def test_gpu_present_and_absent() -> None:
    absent = doctor_module._gpu_check(_metadata())
    present = doctor_module._gpu_check(
        _metadata(
            gpus=(GpuMetadata("GPU", 8 * 1024**3, "555.42"),),
            cuda_version="12.5",
        )
    )
    assert absent.status is DoctorCheckStatus.WARNING
    assert present.status is DoctorCheckStatus.PASS
    assert ("cuda_compatibility", "12.5") in present.details


def test_native_and_wsl_behavior() -> None:
    native = doctor_module._wsl_check(_metadata())
    wsl = doctor_module._wsl_check(_metadata(wsl=WslMetadata(True, "Ubuntu", "2")))
    assert native.status is DoctorCheckStatus.PASS
    assert ("platform", "Windows") in native.details
    assert wsl.status is DoctorCheckStatus.PASS
    assert ("distribution", "Ubuntu") in wsl.details


def test_data_foundation_check_covers_libraries_schemas_geometry_and_synthetic() -> (
    None
):
    check = doctor_module._data_foundation_check()
    assert check.status is DoctorCheckStatus.PASS
    details = dict(check.details)
    assert all(
        details[f"{name}_version"] for name in ("numpy", "pyarrow", "polars", "shapely")
    )
    assert details["schema_count"] == "13"
    schema_names = details["schema_names"].split(", ")
    assert len(schema_names) == 13
    fingerprints = details["schema_fingerprints"].split(", ")
    assert len(fingerprints) == 13
    assert all(len(item.split("=", maxsplit=1)[1]) == 64 for item in fingerprints)
    assert details["arrow_empty_table_count"] == "13"
    assert details["polars_schema_count"] == "13"
    assert len(details["geometry_wkb_sha256"]) == 64
    assert details["registry_ids"] == "synthetic_kinematicweave, av2_motion"
    assert details["synthetic_scenario_id"].endswith("straight_constant_speed")
    assert details["synthetic_agent_count"] == "1"
    assert details["synthetic_trajectory_count"] == "1"


def test_data_foundation_component_failure_becomes_failed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> NoReturn:
        raise RuntimeError("simulated data component failure")

    monkeypatch.setattr(doctor_module, "_data_foundation_details", fail)
    check = doctor_module._data_foundation_check()
    assert check.status is DoctorCheckStatus.FAIL
    assert check.details == (
        ("error_type", "RuntimeError"),
        ("error", "simulated data component failure"),
    )


def test_data_foundation_check_performs_no_filesystem_or_cache_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("data foundation check accessed the filesystem")

    for method_name in (
        "iterdir",
        "rglob",
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
    ):
        monkeypatch.setattr(Path, method_name, fail)
    assert doctor_module._data_foundation_check().status is DoctorCheckStatus.PASS


def test_dictionary_field_order_and_detail_records() -> None:
    serialized = doctor_report_to_dict(_report(DoctorCheckStatus.PASS))
    assert list(serialized) == [
        "schema_version",
        "overall_status",
        "checks",
        "system_metadata",
    ]
    checks = serialized["checks"]
    assert isinstance(checks, list)
    assert checks[0]["details"] == []


def test_canonical_json_is_deterministic_with_one_newline() -> None:
    report = _report(DoctorCheckStatus.PASS, DoctorCheckStatus.WARNING)
    first = doctor_report_to_canonical_json(report)
    second = doctor_report_to_canonical_json(report)
    assert first == second
    assert first.endswith("\n")
    assert not first.endswith("\n\n")
    assert json.loads(first)["overall_status"] == "warning"


def test_import_performs_no_subprocess_or_file_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("import attempted a side effect")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr("kinematicweave.doctor.tempfile.mkstemp", fail)
    monkeypatch.setattr(Path, "write_text", fail)
    monkeypatch.setattr(Path, "write_bytes", fail)

    module_name = "_kinematicweave_doctor_import_probe"
    specification = importlib.util.spec_from_file_location(
        module_name,
        doctor_module.__file__,
    )
    assert specification is not None
    assert specification.loader is not None
    imported = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = imported
    try:
        specification.loader.exec_module(imported)
    finally:
        del sys.modules[module_name]


def test_module_all_is_exact() -> None:
    assert doctor_module.__all__ == [
        "DoctorCheck",
        "DoctorCheckStatus",
        "DoctorReport",
        "doctor_report_to_canonical_json",
        "doctor_report_to_dict",
        "run_doctor",
    ]
