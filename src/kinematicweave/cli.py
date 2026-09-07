"""Argument parsing and command dispatch for the kinematicweave CLI."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

import kinematicweave
from kinematicweave.artifact_store import inspect_run_directory, run_directory_name
from kinematicweave.canonical import canonical_json_text
from kinematicweave.config import config_to_canonical_json, load_config
from kinematicweave.data.cli import register_data_commands
from kinematicweave.doctor import (
    DoctorCheckStatus,
    doctor_report_to_canonical_json,
    run_doctor,
)
from kinematicweave.errors import KinematicWeaveError, ValidationError

__all__ = ["build_parser", "main"]


def _repository_root(value: Path | None) -> Path:
    candidate = Path.cwd() if value is None else value
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationError("repository root must exist and be a directory") from None
    if not resolved.is_dir():
        raise ValidationError("repository root must exist and be a directory")
    return resolved


def _config_path(repository_root: Path, value: Path | None) -> Path:
    if value is None:
        return repository_root / "configs" / "project.toml"
    return value if value.is_absolute() else repository_root / value


def _doctor_command(arguments: argparse.Namespace) -> int:
    report = run_doctor(
        arguments.repo_root,
        config_path=arguments.config,
        required_free_fraction=arguments.required_free_fraction,
    )
    if arguments.json:
        sys.stdout.write(doctor_report_to_canonical_json(report))
    else:
        markers = {
            DoctorCheckStatus.PASS: "PASS",
            DoctorCheckStatus.WARNING: "WARN",
            DoctorCheckStatus.FAIL: "FAIL",
        }
        for check in report.checks:
            sys.stdout.write(
                f"{markers[check.status]} {check.check_id}: {check.message}\n"
            )
        sys.stdout.write(f"OVERALL {markers[report.overall_status]}\n")
    return 1 if report.overall_status is DoctorCheckStatus.FAIL else 0


def _config_validate_command(arguments: argparse.Namespace) -> int:
    repository_root = _repository_root(arguments.repo_root)
    config = load_config(_config_path(repository_root, arguments.path))
    sys.stdout.write(
        f"Configuration valid: {config.project_name} (schema {config.schema_version})\n"
    )
    return 0


def _config_show_command(arguments: argparse.Namespace) -> int:
    repository_root = _repository_root(arguments.repo_root)
    config = load_config(_config_path(repository_root, arguments.path))
    sys.stdout.write(config_to_canonical_json(config))
    return 0


def _artifact_inspect_run_command(arguments: argparse.Namespace) -> int:
    repository_root = _repository_root(arguments.repo_root)
    state = inspect_run_directory(
        repository_root,
        arguments.results_root,
        arguments.run_id,
    )
    if arguments.json:
        sys.stdout.write(
            canonical_json_text(
                {
                    "run_id": arguments.run_id,
                    "state": state.value,
                    "run_directory_name": run_directory_name(arguments.run_id),
                },
                trailing_newline=True,
            )
        )
    else:
        sys.stdout.write(f"run_id: {arguments.run_id}\n")
        sys.stdout.write(f"state: {state.value}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the complete supported command parser."""
    parser = argparse.ArgumentParser(
        prog="kinematicweave",
        description="Inspect the KinematicWeave project environment and foundation state.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"kinematicweave {kinematicweave.__version__}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor_parser = commands.add_parser(
        "doctor",
        help="Inspect repository and environment readiness.",
    )
    doctor_parser.add_argument("--repo-root", type=Path)
    doctor_parser.add_argument("--config", type=Path)
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument(
        "--required-free-fraction",
        type=float,
        default=0.15,
    )
    doctor_parser.set_defaults(handler=_doctor_command)

    config_parser = commands.add_parser(
        "config",
        help="Inspect project configuration.",
    )
    config_commands = config_parser.add_subparsers(
        dest="config_command",
        required=True,
    )
    for name, handler in (
        ("show", _config_show_command),
        ("validate", _config_validate_command),
    ):
        subcommand = config_commands.add_parser(name)
        subcommand.add_argument("--path", type=Path)
        subcommand.add_argument("--repo-root", type=Path)
        subcommand.set_defaults(handler=handler)

    artifact_parser = commands.add_parser(
        "artifact",
        help="Inspect artifact state.",
    )
    artifact_commands = artifact_parser.add_subparsers(
        dest="artifact_command",
        required=True,
    )
    inspect_parser = artifact_commands.add_parser(
        "inspect-run",
        help="Inspect one run directory without modifying it.",
    )
    inspect_parser.add_argument("run_id")
    inspect_parser.add_argument("--repo-root", type=Path)
    inspect_parser.add_argument("--results-root", type=Path, default=Path("results"))
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=_artifact_inspect_run_command)
    register_data_commands(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, dispatch one command, and translate expected failures."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        handler = arguments.handler
        return int(handler(arguments))
    except (KinematicWeaveError, OSError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 2
