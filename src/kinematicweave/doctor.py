"""Read-only repository and environment diagnostics for KinematicWeave."""

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import kinematicweave
from kinematicweave.artifact_store import DiskSpaceSnapshot, check_disk_space
from kinematicweave.canonical import canonical_json_text
from kinematicweave.config import ProjectConfig, load_config
from kinematicweave.errors import (
    ConfigurationError,
    ResourceLimitError,
    ValidationError,
)
from kinematicweave.system_metadata import (
    SystemMetadata,
    capture_system_metadata,
    system_metadata_to_dict,
)

__all__ = [
    "DoctorCheck",
    "DoctorCheckStatus",
    "DoctorReport",
    "doctor_report_to_canonical_json",
    "doctor_report_to_dict",
    "run_doctor",
]

_SCHEMA_VERSION = "1.0"
_COMMAND_TIMEOUT_SECONDS = 10.0
_REQUIRED_REPOSITORY_DIRECTORIES = (
    "definitions",
    "configs",
    "scripts",
    "src/kinematicweave",
    "tests",
)
_REQUIRED_REPOSITORY_FILES = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
)
_REQUIRED_DEFINITION_FILES = (
    "definitions/README.md",
    "definitions/design_freeze_report.md",
    "definitions/16_codex_batch_protocol.md",
    "definitions/17_codex_report_template.md",
)


class DoctorCheckStatus(StrEnum):
    """Outcome states for one doctor check."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One immutable diagnostic result."""

    check_id: str
    status: DoctorCheckStatus
    message: str
    details: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """Validate text, status, and ordered unique detail pairs."""
        object.__setattr__(
            self,
            "check_id",
            _required_text(self.check_id, "check_id"),
        )
        if not isinstance(self.status, DoctorCheckStatus):
            raise ValidationError("status must be a DoctorCheckStatus")
        object.__setattr__(self, "message", _required_text(self.message, "message"))
        if not isinstance(self.details, tuple):
            raise ValidationError("details must be an immutable tuple")

        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for detail in self.details:
            if not isinstance(detail, tuple) or len(detail) != 2:
                raise ValidationError("details must contain key/value string pairs")
            key, value = detail
            normalized_key = _required_text(key, "detail key")
            if not isinstance(value, str):
                raise ValidationError("detail values must be strings")
            if normalized_key in seen:
                raise ValidationError("detail keys must be unique")
            seen.add(normalized_key)
            normalized.append((normalized_key, value))
        object.__setattr__(self, "details", tuple(normalized))


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Ordered doctor checks with a derived overall status."""

    schema_version: str
    overall_status: DoctorCheckStatus
    checks: tuple[DoctorCheck, ...]
    system_metadata: SystemMetadata

    def __post_init__(self) -> None:
        """Validate report contents and derive the overall status."""
        if self.schema_version != _SCHEMA_VERSION:
            raise ValidationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
        if not isinstance(self.overall_status, DoctorCheckStatus):
            raise ValidationError("overall_status must be a DoctorCheckStatus")
        if (
            not isinstance(self.checks, tuple)
            or not self.checks
            or any(not isinstance(check, DoctorCheck) for check in self.checks)
        ):
            raise ValidationError("checks must be a nonempty tuple of DoctorCheck")
        if not isinstance(self.system_metadata, SystemMetadata):
            raise ValidationError("system_metadata must be a SystemMetadata")

        statuses = {check.status for check in self.checks}
        if DoctorCheckStatus.FAIL in statuses:
            derived = DoctorCheckStatus.FAIL
        elif DoctorCheckStatus.WARNING in statuses:
            derived = DoctorCheckStatus.WARNING
        else:
            derived = DoctorCheckStatus.PASS
        object.__setattr__(self, "overall_status", derived)


def _resolve_repository_root(repository_root: Path | None) -> Path:
    if repository_root is None:
        candidate = Path.cwd()
    elif isinstance(repository_root, Path):
        candidate = repository_root
    else:
        raise ValidationError("repository_root must be a Path or None")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationError("repository_root must exist and be a directory") from None
    if not resolved.is_dir():
        raise ValidationError("repository_root must exist and be a directory")
    return resolved


def _repository_check(repository_root: Path) -> DoctorCheck:
    invalid = tuple(
        [
            entry
            for entry in _REQUIRED_REPOSITORY_DIRECTORIES
            if not (repository_root / entry).is_dir()
        ]
        + [
            entry
            for entry in _REQUIRED_REPOSITORY_FILES
            if not (repository_root / entry).is_file()
        ]
    )
    if invalid:
        return DoctorCheck(
            "repository",
            DoctorCheckStatus.FAIL,
            "required repository entries are missing or have the wrong type",
            (("invalid", ", ".join(invalid)),),
        )
    entry_count = len(_REQUIRED_REPOSITORY_DIRECTORIES) + len(
        _REQUIRED_REPOSITORY_FILES
    )
    return DoctorCheck(
        "repository",
        DoctorCheckStatus.PASS,
        "required repository entries are present",
        (("entry_count", str(entry_count)),),
    )


def _definitions_check(repository_root: Path) -> DoctorCheck:
    missing = tuple(
        path
        for path in _REQUIRED_DEFINITION_FILES
        if not (repository_root / path).is_file()
    )
    if missing:
        return DoctorCheck(
            "definitions",
            DoctorCheckStatus.FAIL,
            "required definition files are missing",
            (("missing", ", ".join(missing)),),
        )
    return DoctorCheck(
        "definitions",
        DoctorCheckStatus.PASS,
        "required definition files are present",
        (("file_count", str(len(_REQUIRED_DEFINITION_FILES))),),
    )


def _resolved_config_path(
    repository_root: Path,
    config_path: Path | None,
) -> Path:
    if config_path is None:
        return repository_root / "configs" / "project.toml"
    if not isinstance(config_path, Path):
        raise ValidationError("config_path must be a Path or None")
    return config_path if config_path.is_absolute() else repository_root / config_path


def _project_configuration_check(
    repository_root: Path,
    config_path: Path | None,
) -> tuple[DoctorCheck, ProjectConfig | None]:
    path = _resolved_config_path(repository_root, config_path)
    try:
        config = load_config(path)
    except (ConfigurationError, OSError) as error:
        return (
            DoctorCheck(
                "project_configuration",
                DoctorCheckStatus.FAIL,
                "project configuration is invalid",
                (("error", str(error)),),
            ),
            None,
        )
    return (
        DoctorCheck(
            "project_configuration",
            DoctorCheckStatus.PASS,
            "project configuration is valid",
            (
                ("schema_version", config.schema_version),
                ("project_name", config.project_name),
            ),
        ),
        config,
    )


def _python_check(system_metadata: SystemMetadata) -> DoctorCheck:
    version_text = system_metadata.python.version
    try:
        major, minor, *_ = (int(part) for part in version_text.split("."))
    except ValueError:
        return DoctorCheck(
            "python",
            DoctorCheckStatus.FAIL,
            "Python version could not be interpreted",
            (
                ("version", version_text),
                ("executable", system_metadata.python.executable),
            ),
        )
    supported = (major, minor) >= (3, 12) and (major, minor) < (3, 14)
    return DoctorCheck(
        "python",
        DoctorCheckStatus.PASS if supported else DoctorCheckStatus.FAIL,
        "Python version is supported" if supported else "Python version is unsupported",
        (
            ("version", version_text),
            ("executable", system_metadata.python.executable),
        ),
    )


def _package_check(system_metadata: SystemMetadata) -> DoctorCheck:
    try:
        installed_version = distribution_version("kinematicweave")
    except PackageNotFoundError:
        return DoctorCheck(
            "package",
            DoctorCheckStatus.FAIL,
            "installed kinematicweave distribution metadata is unavailable",
            (("import_version", kinematicweave.__version__),),
        )
    versions = (
        kinematicweave.__version__,
        installed_version,
        system_metadata.package_version,
    )
    matches = len(set(versions)) == 1
    return DoctorCheck(
        "package",
        DoctorCheckStatus.PASS if matches else DoctorCheckStatus.FAIL,
        "package versions agree" if matches else "package version mismatch",
        (
            ("import_version", kinematicweave.__version__),
            ("distribution_version", installed_version),
            ("metadata_version", system_metadata.package_version),
        ),
    )


def _run_local_command(
    arguments: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )


def _uv_check() -> tuple[DoctorCheck, str | None]:
    executable = shutil.which("uv")
    if executable is None:
        return (
            DoctorCheck(
                "uv",
                DoctorCheckStatus.FAIL,
                "uv executable is unavailable",
                (),
            ),
            None,
        )
    try:
        result = _run_local_command([executable, "--version"])
    except subprocess.TimeoutExpired:
        return (
            DoctorCheck(
                "uv",
                DoctorCheckStatus.FAIL,
                "uv version check timed out",
                (("executable", executable),),
            ),
            executable,
        )
    except OSError as error:
        return (
            DoctorCheck(
                "uv",
                DoctorCheckStatus.FAIL,
                "uv version check failed",
                (("error", str(error)),),
            ),
            executable,
        )
    output = result.stdout.strip() or result.stderr.strip()
    if result.returncode != 0:
        return (
            DoctorCheck(
                "uv",
                DoctorCheckStatus.FAIL,
                "uv version command failed",
                (
                    ("returncode", str(result.returncode)),
                    ("output", output),
                ),
            ),
            executable,
        )
    return (
        DoctorCheck(
            "uv",
            DoctorCheckStatus.PASS,
            "uv is available",
            (("version", output),),
        ),
        executable,
    )


def _lockfile_check(
    repository_root: Path,
    system_metadata: SystemMetadata,
    uv_executable: str | None,
) -> DoctorCheck:
    lockfile = repository_root / "uv.lock"
    if not lockfile.is_file():
        return DoctorCheck(
            "lockfile",
            DoctorCheckStatus.FAIL,
            "uv.lock is missing",
            (),
        )
    try:
        lock_id = hashlib.sha256(lockfile.read_bytes()).hexdigest()
    except OSError as error:
        return DoctorCheck(
            "lockfile",
            DoctorCheckStatus.FAIL,
            "uv.lock could not be read",
            (("error", str(error)),),
        )
    if system_metadata.environment_lock_id != lock_id:
        return DoctorCheck(
            "lockfile",
            DoctorCheckStatus.FAIL,
            "captured environment lock identity does not match uv.lock",
            (
                ("captured", system_metadata.environment_lock_id or ""),
                ("calculated", lock_id),
            ),
        )
    if uv_executable is None:
        return DoctorCheck(
            "lockfile",
            DoctorCheckStatus.FAIL,
            "uv.lock cannot be checked because uv is unavailable",
            (("environment_lock_id", lock_id),),
        )
    try:
        result = _run_local_command(
            [uv_executable, "lock", "--check"],
            cwd=repository_root,
        )
    except subprocess.TimeoutExpired:
        return DoctorCheck(
            "lockfile",
            DoctorCheckStatus.FAIL,
            "uv lock check timed out",
            (("environment_lock_id", lock_id),),
        )
    except OSError as error:
        return DoctorCheck(
            "lockfile",
            DoctorCheckStatus.FAIL,
            "uv lock check failed",
            (("error", str(error)),),
        )
    output = result.stdout.strip() or result.stderr.strip()
    if result.returncode != 0:
        return DoctorCheck(
            "lockfile",
            DoctorCheckStatus.FAIL,
            "uv.lock is not current",
            (
                ("returncode", str(result.returncode)),
                ("output", output),
            ),
        )
    return DoctorCheck(
        "lockfile",
        DoctorCheckStatus.PASS,
        "uv.lock is present, identified, and current",
        (("environment_lock_id", lock_id),),
    )


def _probe_directory(path: Path) -> str | None:
    descriptor = -1
    probe_path: Path | None = None
    error_message: str | None = None
    try:
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".kinematicweave-doctor-",
            suffix=".probe",
            dir=path,
        )
        probe_path = Path(probe_name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(b"kinematicweave doctor\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        error_message = str(error)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError as error:
                error_message = f"probe cleanup failed: {error}"
    return error_message


def _writable_directories_check(
    repository_root: Path,
    config: ProjectConfig | None,
) -> DoctorCheck:
    if config is None:
        return DoctorCheck(
            "writable_directories",
            DoctorCheckStatus.FAIL,
            "configured directories cannot be checked",
            (("error", "project configuration is unavailable"),),
        )

    details: list[tuple[str, str]] = []
    failures: list[str] = []
    for name in ("data", "results", "reports", "figures", "qualitative"):
        configured_path = getattr(config.paths, name)
        try:
            resolved = (repository_root / configured_path).resolve(strict=True)
        except (OSError, RuntimeError):
            failures.append(name)
            details.append((name, "missing or inaccessible"))
            continue
        if resolved == repository_root or not resolved.is_relative_to(repository_root):
            failures.append(name)
            details.append((name, "resolves outside repository"))
            continue
        if not resolved.is_dir():
            failures.append(name)
            details.append((name, "not a directory"))
            continue
        probe_error = _probe_directory(resolved)
        if probe_error is not None:
            failures.append(name)
            details.append((name, probe_error))
        else:
            details.append((name, "writable"))

    return DoctorCheck(
        "writable_directories",
        DoctorCheckStatus.FAIL if failures else DoctorCheckStatus.PASS,
        (
            "one or more configured directories are not writable"
            if failures
            else "configured directories are writable"
        ),
        tuple(details),
    )


def _disk_space_check(
    snapshot: DiskSpaceSnapshot | None,
    error: ResourceLimitError | None,
    system_metadata: SystemMetadata,
    required_free_fraction: float,
) -> DoctorCheck:
    if error is not None:
        disk = system_metadata.disk
        return DoctorCheck(
            "disk_space",
            DoctorCheckStatus.FAIL,
            "disk reserve requirement is not satisfied",
            (
                ("total_bytes", str(disk.total_bytes)),
                ("used_bytes", str(disk.used_bytes)),
                ("free_bytes", str(disk.free_bytes)),
                (
                    "reserve_bytes",
                    str(math.ceil(disk.total_bytes * required_free_fraction)),
                ),
                ("error", str(error)),
            ),
        )
    if snapshot is None:
        raise AssertionError("disk snapshot or error is required")
    return DoctorCheck(
        "disk_space",
        DoctorCheckStatus.PASS,
        "disk reserve requirement is satisfied",
        (
            ("total_bytes", str(snapshot.total_bytes)),
            ("used_bytes", str(snapshot.used_bytes)),
            ("free_bytes", str(snapshot.free_bytes)),
            ("reserve_bytes", str(snapshot.reserve_bytes)),
        ),
    )


def _git_check(system_metadata: SystemMetadata) -> DoctorCheck:
    git = system_metadata.git
    if not git.available:
        return DoctorCheck(
            "git",
            DoctorCheckStatus.WARNING,
            "Git metadata is unavailable",
            (),
        )
    details = (("commit", git.commit or ""),)
    if git.dirty:
        return DoctorCheck(
            "git",
            DoctorCheckStatus.WARNING,
            "Git worktree is dirty",
            details,
        )
    return DoctorCheck(
        "git",
        DoctorCheckStatus.PASS,
        "Git worktree is clean",
        details,
    )


def _gpu_check(system_metadata: SystemMetadata) -> DoctorCheck:
    if not system_metadata.gpus:
        return DoctorCheck(
            "gpu",
            DoctorCheckStatus.WARNING,
            "no GPU was detected; CPU operation remains supported",
            (("gpu_count", "0"),),
        )
    details: list[tuple[str, str]] = [
        ("gpu_count", str(len(system_metadata.gpus))),
        ("gpu_names", ", ".join(gpu.name for gpu in system_metadata.gpus)),
    ]
    if system_metadata.cuda_version is not None:
        details.append(("cuda_compatibility", system_metadata.cuda_version))
    return DoctorCheck(
        "gpu",
        DoctorCheckStatus.PASS,
        "one or more GPUs were detected",
        tuple(details),
    )


def _wsl_check(system_metadata: SystemMetadata) -> DoctorCheck:
    wsl = system_metadata.wsl
    if wsl.is_wsl:
        return DoctorCheck(
            "wsl",
            DoctorCheckStatus.PASS,
            "running under Windows Subsystem for Linux",
            (
                ("distribution", wsl.distribution or "unknown"),
                ("version", wsl.version or "unknown"),
            ),
        )
    return DoctorCheck(
        "wsl",
        DoctorCheckStatus.PASS,
        "running outside Windows Subsystem for Linux",
        (("platform", system_metadata.operating_system.system),),
    )


def _data_foundation_details() -> tuple[tuple[str, str], ...]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import shapely  # type: ignore[import-untyped]
    from shapely.geometry import LineString  # type: ignore[import-untyped]

    from kinematicweave.canonical import canonical_sha256
    from kinematicweave.data.parquet_io import (
        agent_records_to_table,
        coordinate_frame_records_to_table,
        scenario_records_to_table,
        trajectories_to_table,
    )
    from kinematicweave.data.registry import default_dataset_registry
    from kinematicweave.data.schemas import (
        canonical_schema_names,
        get_arrow_schema,
        get_polars_schema,
        schema_fingerprint,
        scientific_data_library_versions,
        validate_arrow_schema,
    )
    from kinematicweave.data.synthetic import (
        SyntheticScenarioKind,
        build_synthetic_scenario,
    )
    from kinematicweave.data.validation import (
        CanonicalValidationConfig,
        validate_canonical_tables,
    )
    from kinematicweave.domain.map_records import (
        geometry_from_canonical_wkb,
        geometry_to_canonical_wkb,
    )
    from kinematicweave.domain.records import AgentClass, validate_scenario_bundle

    versions = scientific_data_library_versions()
    versions["shapely"] = shapely.__version__

    schema_names = canonical_schema_names()
    if len(schema_names) != 13:
        raise ValidationError("canonical schema registry must contain thirteen schemas")
    fingerprints: list[str] = []
    for name in schema_names:
        schema = get_arrow_schema(name)
        validate_arrow_schema(schema, name)
        fingerprint = schema_fingerprint(name)
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ValidationError("canonical schema fingerprint is invalid")
        fingerprints.append(f"{name.value}={fingerprint}")
        empty = pa.Table.from_batches([], schema=schema)
        if empty.num_rows != 0 or not empty.schema.equals(schema):
            raise ValidationError("empty canonical Arrow table is invalid")
        polars_schema = get_polars_schema(name)
        if tuple(polars_schema) != tuple(field.name for field in schema):
            raise ValidationError("canonical Polars schema differs from Arrow")

    line = LineString(((0.0, 0.0), (1.0, 2.0), (3.0, 2.0)))
    encoded = geometry_to_canonical_wkb(line)
    decoded = geometry_from_canonical_wkb(encoded)
    if geometry_to_canonical_wkb(decoded) != encoded:
        raise ValidationError("canonical geometry WKB round trip is unstable")

    registry_ids = tuple(
        entry.dataset_id for entry in default_dataset_registry().entries
    )
    if registry_ids != ("synthetic_kinematicweave", "av2_motion"):
        raise ValidationError(
            "default dataset registry differs from the approved order"
        )

    synthetic = build_synthetic_scenario(SyntheticScenarioKind.STRAIGHT_CONSTANT_SPEED)
    validate_scenario_bundle(
        synthetic.scenario,
        synthetic.coordinate_frame,
        synthetic.agents,
        synthetic.trajectories,
    )
    report = validate_canonical_tables(
        scenario_records_to_table((synthetic.scenario,)),
        coordinate_frame_records_to_table((synthetic.coordinate_frame,)),
        agent_records_to_table(synthetic.agents),
        trajectories_to_table(synthetic.trajectories),
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=1,
            minimum_valid_duration_ns=0,
            allowed_agent_classes=tuple(AgentClass),
            require_source_map=False,
        ),
    )
    if not report.is_eligible:
        raise ValidationError("straight synthetic scenario is not eligible")

    return (
        ("numpy_version", versions["numpy"]),
        ("pyarrow_version", versions["pyarrow"]),
        ("polars_version", versions["polars"]),
        ("shapely_version", versions["shapely"]),
        ("schema_count", str(len(schema_names))),
        ("schema_names", ", ".join(name.value for name in schema_names)),
        ("schema_fingerprints", ", ".join(fingerprints)),
        ("arrow_empty_table_count", str(len(schema_names))),
        ("polars_schema_count", str(len(schema_names))),
        ("geometry_wkb_sha256", hashlib.sha256(encoded).hexdigest()),
        ("registry_ids", ", ".join(registry_ids)),
        ("synthetic_scenario_id", synthetic.scenario.scenario_id),
        ("synthetic_agent_count", str(len(synthetic.agents))),
        ("synthetic_trajectory_count", str(len(synthetic.trajectories))),
        (
            "synthetic_validation_identity",
            canonical_sha256(
                "doctor-data-foundation",
                {
                    "scenario_id": synthetic.scenario.scenario_id,
                    "included_scenarios": list(report.included_scenario_ids),
                    "included_trajectories": list(report.included_trajectory_ids),
                },
            ),
        ),
    )


def _data_foundation_check() -> DoctorCheck:
    try:
        details = _data_foundation_details()
    except Exception as error:  # noqa: BLE001 - diagnostics must report component faults
        return DoctorCheck(
            "data_foundation",
            DoctorCheckStatus.FAIL,
            "data foundation is unavailable or invalid",
            (
                ("error_type", type(error).__name__),
                ("error", str(error)),
            ),
        )
    return DoctorCheck(
        "data_foundation",
        DoctorCheckStatus.PASS,
        "data foundation is available and valid",
        details,
    )


def run_doctor(
    repository_root: Path | None = None,
    *,
    config_path: Path | None = None,
    required_free_fraction: float = 0.15,
) -> DoctorReport:
    """Run ordered read-only diagnostics and return an immutable report.

    Raises:
        ValidationError: If root, configuration-path, or reserve inputs are invalid.
        OSError: If required system metadata cannot be captured.
    """
    resolved_root = _resolve_repository_root(repository_root)
    disk_snapshot: DiskSpaceSnapshot | None = None
    disk_error: ResourceLimitError | None = None
    try:
        disk_snapshot = check_disk_space(
            resolved_root,
            required_bytes=0,
            reserve_fraction=required_free_fraction,
        )
    except ResourceLimitError as error:
        disk_error = error

    system_metadata = capture_system_metadata(resolved_root)
    configuration_check, config = _project_configuration_check(
        resolved_root,
        config_path,
    )
    uv_check, uv_executable = _uv_check()
    checks = (
        _repository_check(resolved_root),
        _definitions_check(resolved_root),
        configuration_check,
        _python_check(system_metadata),
        _package_check(system_metadata),
        _data_foundation_check(),
        uv_check,
        _lockfile_check(resolved_root, system_metadata, uv_executable),
        _writable_directories_check(resolved_root, config),
        _disk_space_check(
            disk_snapshot,
            disk_error,
            system_metadata,
            required_free_fraction,
        ),
        _git_check(system_metadata),
        _gpu_check(system_metadata),
        _wsl_check(system_metadata),
    )
    return DoctorReport(
        schema_version=_SCHEMA_VERSION,
        overall_status=DoctorCheckStatus.PASS,
        checks=checks,
        system_metadata=system_metadata,
    )


def doctor_report_to_dict(report: DoctorReport) -> dict[str, object]:
    """Return an ordered JSON-compatible doctor report."""
    if not isinstance(report, DoctorReport):
        raise ValidationError("report must be a DoctorReport")
    return {
        "schema_version": report.schema_version,
        "overall_status": report.overall_status.value,
        "checks": [
            {
                "check_id": check.check_id,
                "status": check.status.value,
                "message": check.message,
                "details": [
                    {"key": key, "value": value} for key, value in check.details
                ],
            }
            for check in report.checks
        ],
        "system_metadata": system_metadata_to_dict(report.system_metadata),
    }


def doctor_report_to_canonical_json(report: DoctorReport) -> str:
    """Serialize a doctor report deterministically with one final newline."""
    return canonical_json_text(
        doctor_report_to_dict(report),
        trailing_newline=True,
    )
