"""Tests for best-effort system and environment metadata capture."""

import ctypes
from dataclasses import FrozenInstanceError
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

import kinematicweave
from kinematicweave.errors import ValidationError
import kinematicweave.system_metadata as metadata_module
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
    capture_system_metadata,
    system_metadata_to_canonical_json,
    system_metadata_to_dict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sample_metadata() -> SystemMetadata:
    return SystemMetadata(
        schema_version="1.0",
        captured_at_utc="2025-01-02T03:04:05.006789Z",
        package_version="0.1.0a0",
        operating_system=OperatingSystemMetadata(
            system="Linux",
            release="6.1",
            version="Build 1",
            machine="x86_64",
        ),
        python=PythonMetadata(
            version="3.12.13",
            implementation="CPython",
            executable="python",
        ),
        git=GitMetadata(
            available=True,
            commit="abc123",
            dirty=False,
        ),
        cpu=CpuMetadata(
            processor=None,
            logical_cpu_count=16,
        ),
        memory=MemoryMetadata(total_bytes=32 * 1024**3),
        gpus=(
            GpuMetadata(
                name="GPU A",
                memory_total_bytes=8 * 1024**3,
                driver_version="555.42",
            ),
        ),
        cuda_version="12.5",
        disk=DiskMetadata(
            total_bytes=1000,
            used_bytes=400,
            free_bytes=600,
        ),
        wsl=WslMetadata(
            is_wsl=True,
            distribution="Ubuntu",
            version="2",
        ),
        environment_lock_id="a" * 64,
    )


def _completed(
    stdout: str,
    *,
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["command"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def test_dataclasses_are_immutable_and_slotted() -> None:
    """All public metadata records are frozen and omit instance dictionaries."""
    metadata = _sample_metadata()
    records = [
        metadata,
        metadata.operating_system,
        metadata.python,
        metadata.git,
        metadata.cpu,
        metadata.memory,
        metadata.gpus[0],
        metadata.disk,
        metadata.wsl,
    ]

    assert all(not hasattr(record, "__dict__") for record in records)
    with pytest.raises(FrozenInstanceError):
        metadata.package_version = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("value", ["", "   "])
def test_required_strings_are_validated(value: str) -> None:
    """Required metadata strings cannot be empty after trimming."""
    with pytest.raises(ValidationError, match="nonempty"):
        OperatingSystemMetadata(
            system=value,
            release="1",
            version="1",
            machine="x86_64",
        )
    with pytest.raises(ValidationError, match="nonempty"):
        GpuMetadata(name=value, memory_total_bytes=1, driver_version="1")


@pytest.mark.parametrize("value", [-1, 0, True])
def test_logical_cpu_counts_must_be_positive_integers(value: object) -> None:
    """Present logical CPU counts reject nonpositive and Boolean values."""
    with pytest.raises(ValidationError):
        CpuMetadata(processor=None, logical_cpu_count=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, True])
def test_byte_counts_reject_negative_and_boolean_values(value: object) -> None:
    """Byte quantities reject negative and Boolean inputs."""
    with pytest.raises(ValidationError):
        MemoryMetadata(total_bytes=value)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        GpuMetadata(
            name="GPU",
            memory_total_bytes=value,  # type: ignore[arg-type]
            driver_version="1",
        )
    with pytest.raises(ValidationError):
        DiskMetadata(
            total_bytes=100,
            used_bytes=value,  # type: ignore[arg-type]
            free_bytes=50,
        )


def test_schema_version_is_validated() -> None:
    """Only system-metadata schema version 1.0 is accepted."""
    values = system_metadata_to_dict(_sample_metadata())
    values["schema_version"] = "2.0"

    with pytest.raises(ValidationError, match="schema_version"):
        SystemMetadata(
            schema_version="2.0",
            captured_at_utc="2025-01-02T03:04:05.006789Z",
            package_version="0.1.0a0",
            operating_system=_sample_metadata().operating_system,
            python=_sample_metadata().python,
            git=_sample_metadata().git,
            cpu=_sample_metadata().cpu,
            memory=_sample_metadata().memory,
            gpus=_sample_metadata().gpus,
            cuda_version=None,
            disk=_sample_metadata().disk,
            wsl=_sample_metadata().wsl,
            environment_lock_id=None,
        )


def test_successful_capture_with_mocked_platform_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Capture composes normalized platform, resource, and repository data."""
    (tmp_path / "uv.lock").write_bytes(b"locked")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "release", lambda: "6.1-WSL2")
    monkeypatch.setattr(platform, "version", lambda: "Build 1")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform, "processor", lambda: "")
    monkeypatch.setattr(platform, "python_version", lambda: "3.12.13")
    monkeypatch.setattr(
        platform,
        "python_implementation",
        lambda: "CPython",
    )
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    monkeypatch.setattr(
        metadata_module, "utc_now_timestamp", lambda: "2025-01-01T00:00:00.000000Z"
    )
    monkeypatch.setattr(
        metadata_module,
        "_capture_git_metadata",
        lambda root: GitMetadata(available=True, commit="abc123", dirty=True),
    )
    monkeypatch.setattr(
        metadata_module,
        "_capture_total_memory",
        lambda system_name: 16 * 1024**3,
    )
    monkeypatch.setattr(
        metadata_module,
        "_capture_gpus",
        lambda: (
            GpuMetadata(
                name="GPU",
                memory_total_bytes=8 * 1024**3,
                driver_version="555.42",
            ),
        ),
    )
    monkeypatch.setattr(metadata_module, "_capture_cuda_version", lambda: "12.5")
    monkeypatch.setattr(
        metadata_module,
        "_read_proc_version",
        lambda: "microsoft-standard-WSL2",
    )
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1000, used=400, free=600),
    )

    metadata = capture_system_metadata(tmp_path)

    assert metadata.operating_system.system == "Linux"
    assert metadata.cpu == CpuMetadata(processor=None, logical_cpu_count=12)
    assert metadata.memory.total_bytes == 16 * 1024**3
    assert metadata.git.dirty is True
    assert metadata.gpus[0].name == "GPU"
    assert metadata.cuda_version == "12.5"
    assert metadata.wsl == WslMetadata(
        is_wsl=True,
        distribution="Ubuntu",
        version="2",
    )
    assert metadata.environment_lock_id == hashlib.sha256(b"locked").hexdigest()


@pytest.mark.parametrize(
    ("status_output", "expected_dirty"),
    [("", False), ("?? untracked.txt\n", True), (" M tracked.txt\n", True)],
)
def test_git_clean_and_dirty_parsing(
    monkeypatch: pytest.MonkeyPatch,
    status_output: str,
    expected_dirty: bool,
) -> None:
    """Git status includes tracked and untracked worktree changes."""
    responses = iter([_completed("abc123\n"), _completed(status_output)])
    monkeypatch.setattr(
        metadata_module,
        "_run_command",
        lambda arguments: next(responses),
    )

    metadata = metadata_module._capture_git_metadata(PROJECT_ROOT)

    assert metadata == GitMetadata(
        available=True,
        commit="abc123",
        dirty=expected_dirty,
    )


def test_git_unavailable_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Git and invalid repositories return unavailable metadata."""
    monkeypatch.setattr(metadata_module, "_run_command", lambda arguments: None)

    assert metadata_module._capture_git_metadata(PROJECT_ROOT) == GitMetadata(
        available=False,
        commit=None,
        dirty=None,
    )


def test_single_gpu_parsing_and_memory_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One nvidia-smi row is parsed and MiB is converted to bytes."""
    monkeypatch.setattr(
        metadata_module,
        "_run_command",
        lambda arguments: _completed("NVIDIA RTX, 8192, 555.42\n"),
    )

    assert metadata_module._capture_gpus() == (
        GpuMetadata(
            name="NVIDIA RTX",
            memory_total_bytes=8192 * 1024 * 1024,
            driver_version="555.42",
        ),
    )


def test_multiple_gpu_rows_preserve_reported_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple GPU rows produce an immutable ordered tuple."""
    monkeypatch.setattr(
        metadata_module,
        "_run_command",
        lambda arguments: _completed("GPU A, 4096, 550.1\nGPU B, 16384, 550.1\n"),
    )

    gpus = metadata_module._capture_gpus()

    assert [gpu.name for gpu in gpus] == ["GPU A", "GPU B"]
    assert gpus[1].memory_total_bytes == 16384 * 1024 * 1024


def test_nvidia_smi_unavailable_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing nvidia-smi executable produces an empty GPU tuple."""

    def unavailable(*args: object, **kwargs: object) -> NoReturn:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", unavailable)

    assert metadata_module._capture_gpus() == ()


def test_nvidia_smi_timeout_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded nvidia-smi timeout produces an empty GPU tuple."""

    def timeout(*args: object, **kwargs: object) -> NoReturn:
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5)

    monkeypatch.setattr(subprocess, "run", timeout)

    assert metadata_module._capture_gpus() == ()


@pytest.mark.parametrize(
    "output",
    ["malformed\n", "GPU, not-a-number, 555.42\n", "GPU, -1, 555.42\n"],
)
def test_malformed_nvidia_smi_output_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    """Malformed GPU rows invalidate the complete GPU result."""
    monkeypatch.setattr(
        metadata_module,
        "_run_command",
        lambda arguments: _completed(output),
    )

    assert metadata_module._capture_gpus() == ()


def test_driver_reported_cuda_version_is_extracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUDA metadata records the compatibility version shown by nvidia-smi."""
    monkeypatch.setattr(
        metadata_module,
        "_run_command",
        lambda arguments: _completed(
            "| NVIDIA-SMI 555.42  Driver Version: 555.42  CUDA Version: 12.5 |"
        ),
    )

    assert metadata_module._capture_cuda_version() == "12.5"


def test_windows_memory_detection_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows helper reads GlobalMemoryStatusEx physical memory."""

    class FakeQuery:
        argtypes: object = None
        restype: object = None

        def __call__(self, status_pointer: object) -> int:
            status = cast(Any, status_pointer)._obj
            status.ullTotalPhys = 32 * 1024**3
            return 1

    class FakeKernel32:
        GlobalMemoryStatusEx = FakeQuery()

    def fake_win_dll(name: str, *, use_last_error: bool) -> FakeKernel32:
        assert name == "kernel32"
        assert use_last_error is True
        return FakeKernel32()

    monkeypatch.setattr(ctypes, "WinDLL", fake_win_dll, raising=False)

    assert metadata_module._windows_total_memory() == 32 * 1024**3


def test_posix_memory_detection_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The POSIX helper multiplies physical pages by page size."""
    values = {"SC_PHYS_PAGES": 100, "SC_PAGE_SIZE": 4096}
    monkeypatch.setattr(
        os,
        "sysconf",
        values.__getitem__,
        raising=False,
    )

    assert metadata_module._posix_total_memory() == 409600


def test_unknown_memory_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable platform memory information is represented by None."""

    def unavailable(name: str) -> NoReturn:
        raise ValueError(name)

    monkeypatch.setattr(
        os,
        "sysconf",
        unavailable,
        raising=False,
    )

    assert metadata_module._posix_total_memory() is None


def test_disk_usage_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Capture measures the filesystem containing the resolved repository."""
    captured_paths: list[Path] = []

    def disk_usage(path: Path) -> SimpleNamespace:
        captured_paths.append(path)
        return SimpleNamespace(total=2000, used=750, free=1250)

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(
        metadata_module,
        "_capture_git_metadata",
        lambda root: GitMetadata(available=False, commit=None, dirty=None),
    )
    monkeypatch.setattr(metadata_module, "_capture_gpus", tuple)
    monkeypatch.setattr(metadata_module, "_capture_cuda_version", lambda: None)
    monkeypatch.setattr(metadata_module, "_capture_total_memory", lambda system: None)
    monkeypatch.setattr(metadata_module, "_read_proc_version", lambda: None)

    metadata = capture_system_metadata(tmp_path)

    assert captured_paths == [tmp_path.resolve()]
    assert metadata.disk == DiskMetadata(
        total_bytes=2000,
        used_bytes=750,
        free_bytes=1250,
    )


def test_native_windows_is_not_wsl() -> None:
    """Native Windows remains non-WSL even when WSL variables are present."""
    metadata = metadata_module._detect_wsl(
        "Windows",
        "11",
        "build",
        None,
        {"WSL_DISTRO_NAME": "Ubuntu"},
    )

    assert metadata == WslMetadata(is_wsl=False, distribution=None, version=None)


@pytest.mark.parametrize(
    ("kernel_text", "expected_version"),
    [
        ("4.4.0-Microsoft", "1"),
        ("5.15.90.1-microsoft-standard-WSL2", "2"),
    ],
)
def test_wsl_versions_and_distribution_are_detected(
    kernel_text: str,
    expected_version: str,
) -> None:
    """Kernel evidence distinguishes WSL 1 and WSL 2 where possible."""
    metadata = metadata_module._detect_wsl(
        "Linux",
        kernel_text,
        "",
        kernel_text,
        {"WSL_DISTRO_NAME": "Ubuntu-22.04"},
    )

    assert metadata == WslMetadata(
        is_wsl=True,
        distribution="Ubuntu-22.04",
        version=expected_version,
    )


def test_lockfile_identity_uses_raw_bytes(tmp_path: Path) -> None:
    """The lock identity is a path-independent SHA-256 of raw bytes."""
    content = b"version = 1\r\nraw = true\n"
    (tmp_path / "uv.lock").write_bytes(content)

    assert (
        metadata_module._environment_lock_id(tmp_path)
        == hashlib.sha256(content).hexdigest()
    )


def test_missing_lockfile_returns_none(tmp_path: Path) -> None:
    """A repository without uv.lock has no environment lock identity."""
    assert metadata_module._environment_lock_id(tmp_path) is None


def test_invalid_repo_roots_are_rejected(tmp_path: Path) -> None:
    """Missing paths and regular files cannot serve as repository roots."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")

    with pytest.raises(ValidationError, match="directory"):
        capture_system_metadata(tmp_path / "missing")
    with pytest.raises(ValidationError, match="directory"):
        capture_system_metadata(file_path)


def test_dictionary_field_order_and_gpu_list() -> None:
    """Dictionary output follows declaration order and GPUs become a list."""
    serialized = system_metadata_to_dict(_sample_metadata())

    assert list(serialized) == [
        "schema_version",
        "captured_at_utc",
        "package_version",
        "operating_system",
        "python",
        "git",
        "cpu",
        "memory",
        "gpus",
        "cuda_version",
        "disk",
        "wsl",
        "environment_lock_id",
    ]
    assert isinstance(serialized["gpus"], list)


def test_canonical_json_is_deterministic_with_one_newline() -> None:
    """Repeated serialization is identical and has one final newline."""
    metadata = _sample_metadata()

    first = system_metadata_to_canonical_json(metadata)
    second = system_metadata_to_canonical_json(metadata)

    assert first == second
    assert first.endswith("\n")
    assert not first.endswith("\n\n")
    assert json.loads(first)["schema_version"] == "1.0"


def test_public_exports_are_exact() -> None:
    """The module exposes only the approved records and functions."""
    assert metadata_module.__all__ == [
        "CpuMetadata",
        "DiskMetadata",
        "GitMetadata",
        "GpuMetadata",
        "MemoryMetadata",
        "OperatingSystemMetadata",
        "PythonMetadata",
        "SystemMetadata",
        "WslMetadata",
        "capture_system_metadata",
        "system_metadata_to_canonical_json",
        "system_metadata_to_dict",
    ]


def test_real_repository_capture_and_serialization() -> None:
    """The actual repository can be described without serializing its path."""
    metadata = capture_system_metadata(PROJECT_ROOT)
    serialized = system_metadata_to_canonical_json(metadata)
    parsed = json.loads(serialized)
    escaped_root = json.dumps(str(PROJECT_ROOT.resolve()))[1:-1]

    assert metadata.package_version == kinematicweave.__version__
    assert metadata.environment_lock_id is not None
    assert re.fullmatch(r"[0-9a-f]{64}", metadata.environment_lock_id)
    assert metadata.disk.total_bytes >= metadata.disk.used_bytes
    assert metadata.disk.total_bytes >= metadata.disk.free_bytes
    assert parsed["schema_version"] == "1.0"
    assert str(PROJECT_ROOT.resolve()) not in serialized
    assert escaped_root not in serialized


def test_module_import_has_no_capture_or_write_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reloading the module invokes no external command or filesystem write."""

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("import attempted a side effect")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(Path, "write_text", fail)
    monkeypatch.setattr(Path, "write_bytes", fail)

    importlib.reload(metadata_module)
