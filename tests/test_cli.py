"""Tests for the kinematicweave command-line interface."""

import argparse
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from typing import NoReturn

import pytest

import kinematicweave
from kinematicweave.artifact_store import (
    finalize_run_directory,
    prepare_run_directory,
    run_directory_name,
)
import kinematicweave.cli as cli_module
from kinematicweave.cli import build_parser, main
from kinematicweave.doctor import (
    DoctorCheck,
    DoctorCheckStatus,
    DoctorReport,
)
from kinematicweave.errors import ValidationError
from kinematicweave.system_metadata import (
    CpuMetadata,
    DiskMetadata,
    GitMetadata,
    MemoryMetadata,
    OperatingSystemMetadata,
    PythonMetadata,
    SystemMetadata,
    WslMetadata,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> SystemMetadata:
    return SystemMetadata(
        schema_version="1.0",
        captured_at_utc="2025-01-02T03:04:05.006789Z",
        package_version=kinematicweave.__version__,
        operating_system=OperatingSystemMetadata("Windows", "11", "build", "AMD64"),
        python=PythonMetadata("3.12.13", "CPython", "python"),
        git=GitMetadata(True, "abc123", False),
        cpu=CpuMetadata("CPU", 8),
        memory=MemoryMetadata(16 * 1024**3),
        gpus=(),
        cuda_version=None,
        disk=DiskMetadata(1000, 400, 600),
        wsl=WslMetadata(False, None, None),
        environment_lock_id="a" * 64,
    )


def _report(status: DoctorCheckStatus) -> DoctorReport:
    return DoctorReport(
        schema_version="1.0",
        overall_status=status,
        checks=(
            DoctorCheck(
                check_id="sample",
                status=status,
                message="sample result",
                details=(),
            ),
        ),
        system_metadata=_metadata(),
    )


def _config_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "configs").mkdir(parents=True)
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


def _git_status() -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={PROJECT_ROOT}",
            "-C",
            str(PROJECT_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def test_parser_construction() -> None:
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert parser.prog == "kinematicweave"


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--help"])
    assert captured.value.code == 0
    output = capsys.readouterr()
    assert "doctor" in output.out
    assert "config" in output.out
    assert "artifact" in output.out
    assert "data" in output.out
    assert output.err == ""


def test_version_output_is_exact(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--version"])
    assert captured.value.code == 0
    output = capsys.readouterr()
    assert output.out == "kinematicweave 0.1.0a0\n"
    assert output.err == ""


def test_python_module_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["kinematicweave", "--version"])
    with pytest.raises(SystemExit) as captured:
        runpy.run_module("kinematicweave", run_name="__main__")
    assert captured.value.code == 0
    output = capsys.readouterr()
    assert output.out == "kinematicweave 0.1.0a0\n"
    assert output.err == ""


@pytest.mark.parametrize(
    ("status", "marker", "expected_code"),
    [
        (DoctorCheckStatus.PASS, "PASS", 0),
        (DoctorCheckStatus.WARNING, "WARN", 0),
        (DoctorCheckStatus.FAIL, "FAIL", 1),
    ],
)
def test_doctor_human_output_and_exit_codes(
    status: DoctorCheckStatus,
    marker: str,
    expected_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module, "run_doctor", lambda *args, **kwargs: _report(status)
    )
    assert main(["doctor"]) == expected_code
    output = capsys.readouterr()
    assert output.out == (f"{marker} sample: sample result\nOVERALL {marker}\n")
    assert output.err == ""


def test_doctor_json_output_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_doctor",
        lambda *args, **kwargs: _report(DoctorCheckStatus.WARNING),
    )
    assert main(["doctor", "--json"]) == 0
    output = capsys.readouterr()
    parsed = json.loads(output.out)
    assert parsed["overall_status"] == "warning"
    assert output.out.endswith("\n")
    assert output.err == ""


def test_config_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = _config_repository(tmp_path)
    assert main(["config", "validate", "--repo-root", str(repository)]) == 0
    output = capsys.readouterr()
    assert output.out == "Configuration valid: KinematicWeave (schema 1.0)\n"
    assert output.err == ""


def test_config_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = _config_repository(tmp_path)
    assert main(["config", "show", "--repo-root", str(repository)]) == 0
    output = capsys.readouterr()
    parsed = json.loads(output.out)
    assert parsed["project_name"] == "KinematicWeave"
    assert output.out.endswith("\n")
    assert output.err == ""


def test_invalid_config_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _config_repository(tmp_path)
    (repository / "configs" / "project.toml").write_text(
        "unknown = true\n",
        encoding="utf-8",
    )
    assert main(["config", "validate", "--repo-root", str(repository)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("error:")


def test_artifact_missing_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    assert (
        main(
            [
                "artifact",
                "inspect-run",
                "run:missing",
                "--repo-root",
                str(repository),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.out == "run_id: run:missing\nstate: missing\n"
    assert output.err == ""


def test_artifact_partial_and_complete_states(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    partial = prepare_run_directory(
        repository,
        "results",
        "run:partial",
        reserve_fraction=0,
    )
    assert (
        main(
            [
                "artifact",
                "inspect-run",
                "run:partial",
                "--repo-root",
                str(repository),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.endswith("state: partial\n")

    finalize_run_directory(partial)
    assert (
        main(
            [
                "artifact",
                "inspect-run",
                "run:partial",
                "--repo-root",
                str(repository),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.endswith("state: complete\n")


def test_artifact_json_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    assert (
        main(
            [
                "artifact",
                "inspect-run",
                "run:json",
                "--repo-root",
                str(repository),
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "run_directory_name": run_directory_name("run:json"),
        "run_id": "run:json",
        "state": "missing",
    }
    assert output.err == ""


def test_invalid_run_identifier_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "artifact",
                "inspect-run",
                "invalid",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert "identifier" in output.err


@pytest.mark.parametrize(
    "error",
    [ValidationError("expected validation"), OSError("expected I/O")],
)
def test_expected_errors_use_stderr_and_return_two(
    error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(cli_module, "inspect_run_directory", fail)
    assert (
        main(
            [
                "artifact",
                "inspect-run",
                "run:error",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"error: {error}\n"


@pytest.mark.parametrize(
    "command",
    ["codec", "layout", "runtime", "experiment", "report", "visualize"],
)
def test_scientific_placeholder_commands_are_absent(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        main([command])
    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "invalid choice" in output.err


def test_installed_entry_point_version_smoke() -> None:
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(
        [uv, "run", "--frozen", "kinematicweave", "--version"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout == "kinematicweave 0.1.0a0\n"
    assert result.stderr == ""


def test_real_repository_doctor_integration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status_before = _git_status()
    probes_before = tuple(PROJECT_ROOT.rglob(".kinematicweave-doctor-*.probe"))

    exit_code = main(["doctor", "--repo-root", str(PROJECT_ROOT)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    required_checks = {
        "repository",
        "definitions",
        "project_configuration",
        "python",
        "package",
        "data_foundation",
        "uv",
        "lockfile",
        "writable_directories",
        "disk_space",
        "git",
        "gpu",
        "wsl",
    }
    reported_checks = {
        line.split()[1].removesuffix(":")
        for line in output.out.splitlines()
        if not line.startswith("OVERALL")
    }
    assert reported_checks == required_checks
    assert output.out.splitlines()[-1] in {"OVERALL PASS", "OVERALL WARN"}
    assert tuple(PROJECT_ROOT.rglob(".kinematicweave-doctor-*.probe")) == probes_before
    assert _git_status() == status_before


def test_module_all_is_exact() -> None:
    assert cli_module.__all__ == ["build_parser", "main"]
