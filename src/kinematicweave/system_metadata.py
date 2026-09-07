"""Typed, best-effort capture of system and environment metadata."""

from collections.abc import Callable, Mapping
import ctypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import cast

from kinematicweave import __version__
from kinematicweave.canonical import canonical_json_text
from kinematicweave.errors import ValidationError
from kinematicweave.timestamps import utc_now_timestamp

__all__ = [
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

_SCHEMA_VERSION = "1.0"
_COMMAND_TIMEOUT_SECONDS = 5.0
_MIB_BYTES = 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CUDA_VERSION_PATTERN = re.compile(
    r"\bCUDA\s+Version\s*:\s*([0-9]+(?:\.[0-9]+)*)",
    re.IGNORECASE,
)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string or None")
    return value.strip() or None


def _optional_nonnegative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be an integer or None")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    normalized = _optional_nonnegative_int(value, field_name)
    if normalized is None:
        raise ValidationError(f"{field_name} must be an integer")
    return normalized


@dataclass(frozen=True, slots=True)
class OperatingSystemMetadata:
    """Operating-system identity reported by the current Python process."""

    system: str
    release: str
    version: str
    machine: str

    def __post_init__(self) -> None:
        """Validate and normalize required operating-system strings."""
        for field_name in ("system", "release", "version", "machine"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class PythonMetadata:
    """Python runtime identity for a metadata capture."""

    version: str
    implementation: str
    executable: str

    def __post_init__(self) -> None:
        """Validate and normalize required Python runtime strings."""
        for field_name in ("version", "implementation", "executable"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class GitMetadata:
    """Git revision and dirty-worktree state when available."""

    available: bool
    commit: str | None
    dirty: bool | None

    def __post_init__(self) -> None:
        """Validate availability and its dependent fields."""
        if not isinstance(self.available, bool):
            raise ValidationError("available must be a Boolean")
        if self.available:
            object.__setattr__(
                self,
                "commit",
                _required_text(self.commit, "commit"),
            )
            if not isinstance(self.dirty, bool):
                raise ValidationError("dirty must be a Boolean when Git is available")
        elif self.commit is not None or self.dirty is not None:
            raise ValidationError(
                "commit and dirty must be None when Git is unavailable"
            )


@dataclass(frozen=True, slots=True)
class CpuMetadata:
    """Processor description and logical CPU count."""

    processor: str | None
    logical_cpu_count: int | None

    def __post_init__(self) -> None:
        """Normalize the optional processor and validate the CPU count."""
        object.__setattr__(
            self,
            "processor",
            _optional_text(self.processor, "processor"),
        )
        count = _optional_nonnegative_int(
            self.logical_cpu_count,
            "logical_cpu_count",
        )
        if count == 0:
            raise ValidationError("logical_cpu_count must be positive when present")
        object.__setattr__(self, "logical_cpu_count", count)


@dataclass(frozen=True, slots=True)
class MemoryMetadata:
    """Best-effort physical-memory capacity."""

    total_bytes: int | None

    def __post_init__(self) -> None:
        """Validate the optional byte count."""
        object.__setattr__(
            self,
            "total_bytes",
            _optional_nonnegative_int(self.total_bytes, "total_bytes"),
        )


@dataclass(frozen=True, slots=True)
class GpuMetadata:
    """One NVIDIA GPU reported by nvidia-smi."""

    name: str
    memory_total_bytes: int
    driver_version: str

    def __post_init__(self) -> None:
        """Validate and normalize GPU metadata."""
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(
            self,
            "memory_total_bytes",
            _nonnegative_int(self.memory_total_bytes, "memory_total_bytes"),
        )
        object.__setattr__(
            self,
            "driver_version",
            _required_text(self.driver_version, "driver_version"),
        )


@dataclass(frozen=True, slots=True)
class DiskMetadata:
    """Capacity and usage for the repository filesystem."""

    total_bytes: int
    used_bytes: int
    free_bytes: int

    def __post_init__(self) -> None:
        """Validate disk byte counts and their basic consistency."""
        for field_name in ("total_bytes", "used_bytes", "free_bytes"):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name),
            )
        if self.used_bytes > self.total_bytes or self.free_bytes > self.total_bytes:
            raise ValidationError("disk usage values must not exceed total_bytes")


@dataclass(frozen=True, slots=True)
class WslMetadata:
    """Current-process Windows Subsystem for Linux identity."""

    is_wsl: bool
    distribution: str | None
    version: str | None

    def __post_init__(self) -> None:
        """Validate and normalize WSL metadata."""
        if not isinstance(self.is_wsl, bool):
            raise ValidationError("is_wsl must be a Boolean")
        object.__setattr__(
            self,
            "distribution",
            _optional_text(self.distribution, "distribution"),
        )
        normalized_version = _optional_text(self.version, "version")
        if normalized_version not in (None, "1", "2"):
            raise ValidationError("WSL version must be '1', '2', or None")
        object.__setattr__(self, "version", normalized_version)


@dataclass(frozen=True, slots=True)
class SystemMetadata:
    """Foundation-level metadata describing one capture environment."""

    schema_version: str
    captured_at_utc: str
    package_version: str
    operating_system: OperatingSystemMetadata
    python: PythonMetadata
    git: GitMetadata
    cpu: CpuMetadata
    memory: MemoryMetadata
    gpus: tuple[GpuMetadata, ...]
    cuda_version: str | None
    disk: DiskMetadata
    wsl: WslMetadata
    environment_lock_id: str | None

    def __post_init__(self) -> None:
        """Validate schema, scalar values, and nested metadata records."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        object.__setattr__(
            self,
            "captured_at_utc",
            _required_text(self.captured_at_utc, "captured_at_utc"),
        )
        object.__setattr__(
            self,
            "package_version",
            _required_text(self.package_version, "package_version"),
        )
        nested_types = (
            ("operating_system", self.operating_system, OperatingSystemMetadata),
            ("python", self.python, PythonMetadata),
            ("git", self.git, GitMetadata),
            ("cpu", self.cpu, CpuMetadata),
            ("memory", self.memory, MemoryMetadata),
            ("disk", self.disk, DiskMetadata),
            ("wsl", self.wsl, WslMetadata),
        )
        for field_name, value, expected_type in nested_types:
            if not isinstance(value, expected_type):
                raise ValidationError(f"{field_name} has an invalid metadata type")
        if not isinstance(self.gpus, tuple) or any(
            not isinstance(gpu, GpuMetadata) for gpu in self.gpus
        ):
            raise ValidationError("gpus must be a tuple of GpuMetadata records")
        object.__setattr__(
            self,
            "cuda_version",
            _optional_text(self.cuda_version, "cuda_version"),
        )
        lock_id = _optional_text(self.environment_lock_id, "environment_lock_id")
        if lock_id is not None:
            lock_id = lock_id.lower()
            if _SHA256_PATTERN.fullmatch(lock_id) is None:
                raise ValidationError(
                    "environment_lock_id must be a lowercase SHA-256 digest"
                )
        object.__setattr__(self, "environment_lock_id", lock_id)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _run_command(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _capture_git_metadata(repo_root: Path) -> GitMetadata:
    commit_result = _run_command(
        [
            "git",
            "-c",
            f"safe.directory={repo_root}",
            "-C",
            str(repo_root),
            "rev-parse",
            "--verify",
            "HEAD",
        ]
    )
    if commit_result is None or commit_result.returncode != 0:
        return GitMetadata(available=False, commit=None, dirty=None)
    commit = commit_result.stdout.strip()
    if not commit:
        return GitMetadata(available=False, commit=None, dirty=None)

    status_result = _run_command(
        [
            "git",
            "-c",
            f"safe.directory={repo_root}",
            "-C",
            str(repo_root),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ]
    )
    if status_result is None or status_result.returncode != 0:
        return GitMetadata(available=False, commit=None, dirty=None)
    return GitMetadata(
        available=True,
        commit=commit,
        dirty=bool(status_result.stdout),
    )


def _windows_total_memory() -> int | None:
    win_dll_factory = getattr(ctypes, "WinDLL", None)
    if win_dll_factory is None:
        return None
    try:
        kernel32 = win_dll_factory("kernel32", use_last_error=True)
        query = kernel32.GlobalMemoryStatusEx
        query.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
        query.restype = ctypes.c_int
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not query(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys)
    except (AttributeError, OSError, TypeError):
        return None


def _posix_total_memory() -> int | None:
    sysconf_value = getattr(os, "sysconf", None)
    if not callable(sysconf_value):
        return None
    sysconf = cast(Callable[[str], int], sysconf_value)
    try:
        physical_pages = sysconf("SC_PHYS_PAGES")
        page_size = sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return None
    if (
        not isinstance(physical_pages, int)
        or isinstance(physical_pages, bool)
        or not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or physical_pages <= 0
        or page_size <= 0
    ):
        return None
    return physical_pages * page_size


def _capture_total_memory(system_name: str) -> int | None:
    if system_name.casefold() == "windows":
        return _windows_total_memory()
    return _posix_total_memory()


def _parse_gpu_output(output: str) -> tuple[GpuMetadata, ...]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ()
    parsed: list[GpuMetadata] = []
    try:
        for line in lines:
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 3:
                return ()
            name, memory_mib_text, driver_version = fields
            memory_mib = int(memory_mib_text)
            if not name or memory_mib < 0 or not driver_version:
                return ()
            parsed.append(
                GpuMetadata(
                    name=name,
                    memory_total_bytes=memory_mib * _MIB_BYTES,
                    driver_version=driver_version,
                )
            )
    except (ValueError, ValidationError):
        return ()
    return tuple(parsed)


def _capture_gpus() -> tuple[GpuMetadata, ...]:
    result = _run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if result is None or result.returncode != 0:
        return ()
    return _parse_gpu_output(result.stdout)


def _capture_cuda_version() -> str | None:
    result = _run_command(["nvidia-smi"])
    if result is None or result.returncode != 0:
        return None
    match = _CUDA_VERSION_PATTERN.search(result.stdout)
    return match.group(1) if match is not None else None


def _read_proc_version() -> str | None:
    try:
        return Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _detect_wsl(
    system_name: str,
    release: str,
    version_text: str,
    proc_version: str | None,
    environment: Mapping[str, str],
) -> WslMetadata:
    if system_name.casefold() == "windows":
        return WslMetadata(is_wsl=False, distribution=None, version=None)

    kernel_evidence = " ".join(
        part for part in (release, version_text, proc_version or "") if part
    ).casefold()
    distribution = environment.get("WSL_DISTRO_NAME")
    has_environment_evidence = bool(distribution or environment.get("WSL_INTEROP"))
    has_kernel_evidence = "microsoft" in kernel_evidence or "wsl" in kernel_evidence
    if not has_environment_evidence and not has_kernel_evidence:
        return WslMetadata(is_wsl=False, distribution=None, version=None)

    wsl_version: str | None = None
    if "wsl2" in kernel_evidence or "microsoft-standard" in kernel_evidence:
        wsl_version = "2"
    elif "microsoft" in kernel_evidence:
        wsl_version = "1"
    return WslMetadata(
        is_wsl=True,
        distribution=distribution,
        version=wsl_version,
    )


def _environment_lock_id(repo_root: Path) -> str | None:
    lockfile = repo_root / "uv.lock"
    try:
        content = lockfile.read_bytes()
    except FileNotFoundError:
        return None
    return hashlib.sha256(content).hexdigest()


def _resolve_repo_root(repo_root: Path | None) -> Path:
    if repo_root is None:
        candidate = Path.cwd()
    elif isinstance(repo_root, Path):
        candidate = repo_root
    else:
        raise ValidationError("repo_root must be a Path or None")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationError("repo_root must exist and be a directory") from None
    if not resolved.is_dir():
        raise ValidationError("repo_root must exist and be a directory")
    return resolved


def _python_executable_text(repo_root: Path) -> str:
    executable_text = sys.executable
    if not executable_text.strip():
        return executable_text
    executable = Path(executable_text).resolve()
    try:
        return executable.relative_to(repo_root).as_posix()
    except ValueError:
        return str(executable)


def capture_system_metadata(repo_root: Path | None = None) -> SystemMetadata:
    """Capture point-in-time system metadata for a repository.

    Expected command or platform-feature absence is represented by unavailable
    metadata rather than an exception.

    Raises:
        ValidationError: If repo_root does not identify an existing directory.
        OSError: If required local filesystem capacity cannot be inspected.
    """
    resolved_root = _resolve_repo_root(repo_root)
    system_name = platform.system()
    release = platform.release()
    version_text = platform.version()
    disk_usage = shutil.disk_usage(resolved_root)
    return SystemMetadata(
        schema_version=_SCHEMA_VERSION,
        captured_at_utc=utc_now_timestamp(),
        package_version=__version__,
        operating_system=OperatingSystemMetadata(
            system=system_name,
            release=release,
            version=version_text,
            machine=platform.machine(),
        ),
        python=PythonMetadata(
            version=platform.python_version(),
            implementation=platform.python_implementation(),
            executable=_python_executable_text(resolved_root),
        ),
        git=_capture_git_metadata(resolved_root),
        cpu=CpuMetadata(
            processor=platform.processor(),
            logical_cpu_count=os.cpu_count(),
        ),
        memory=MemoryMetadata(
            total_bytes=_capture_total_memory(system_name),
        ),
        gpus=_capture_gpus(),
        cuda_version=_capture_cuda_version(),
        disk=DiskMetadata(
            total_bytes=disk_usage.total,
            used_bytes=disk_usage.used,
            free_bytes=disk_usage.free,
        ),
        wsl=_detect_wsl(
            system_name,
            release,
            version_text,
            _read_proc_version(),
            os.environ,
        ),
        environment_lock_id=_environment_lock_id(resolved_root),
    )


def system_metadata_to_dict(metadata: SystemMetadata) -> dict[str, object]:
    """Return a JSON-compatible dictionary in declared metadata field order."""
    return {
        "schema_version": metadata.schema_version,
        "captured_at_utc": metadata.captured_at_utc,
        "package_version": metadata.package_version,
        "operating_system": {
            "system": metadata.operating_system.system,
            "release": metadata.operating_system.release,
            "version": metadata.operating_system.version,
            "machine": metadata.operating_system.machine,
        },
        "python": {
            "version": metadata.python.version,
            "implementation": metadata.python.implementation,
            "executable": metadata.python.executable,
        },
        "git": {
            "available": metadata.git.available,
            "commit": metadata.git.commit,
            "dirty": metadata.git.dirty,
        },
        "cpu": {
            "processor": metadata.cpu.processor,
            "logical_cpu_count": metadata.cpu.logical_cpu_count,
        },
        "memory": {
            "total_bytes": metadata.memory.total_bytes,
        },
        "gpus": [
            {
                "name": gpu.name,
                "memory_total_bytes": gpu.memory_total_bytes,
                "driver_version": gpu.driver_version,
            }
            for gpu in metadata.gpus
        ],
        "cuda_version": metadata.cuda_version,
        "disk": {
            "total_bytes": metadata.disk.total_bytes,
            "used_bytes": metadata.disk.used_bytes,
            "free_bytes": metadata.disk.free_bytes,
        },
        "wsl": {
            "is_wsl": metadata.wsl.is_wsl,
            "distribution": metadata.wsl.distribution,
            "version": metadata.wsl.version,
        },
        "environment_lock_id": metadata.environment_lock_id,
    }


def system_metadata_to_canonical_json(metadata: SystemMetadata) -> str:
    """Serialize system metadata as canonical JSON with one final newline."""
    return canonical_json_text(
        system_metadata_to_dict(metadata),
        trailing_newline=True,
    )
