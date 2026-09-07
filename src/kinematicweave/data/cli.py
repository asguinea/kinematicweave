"""Command registration and thin handlers for data-foundation operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from kinematicweave.canonical import canonical_sha256
from kinematicweave.data.materialization import (
    materialization_cache_inventory_to_canonical_json,
    prune_incomplete_materialization_cache,
    scan_materialization_cache,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    trajectories_to_table,
)
from kinematicweave.data.registry import (
    dataset_registry_to_dict,
    dataset_source_manifest_from_json,
    dataset_source_manifest_to_canonical_json,
    default_dataset_registry,
    discover_dataset_source,
    get_dataset_registry_entry,
    verify_dataset_source,
)
from kinematicweave.data.synthetic import (
    build_synthetic_dataset,
    synthetic_agents,
    synthetic_coordinate_frames,
    synthetic_scenarios,
    synthetic_trajectories,
)
from kinematicweave.data.validation import (
    CanonicalDatasetPaths,
    CanonicalValidationConfig,
    canonical_validation_report_to_canonical_json,
    canonical_validation_report_to_dict,
    validate_canonical_parquet_dataset,
    validate_canonical_tables,
)
from kinematicweave.domain.records import AgentClass, validate_scenario_bundle
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.paths import normalize_relative_path

__all__ = ["register_data_commands"]

_DEFAULT_CACHE_ROOT = Path("cache/materialization")


def _integer_argument(value: str) -> int:
    if value.casefold() in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected an integer")
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected an integer") from None


def _repository_root(value: Path | None) -> Path:
    candidate = Path.cwd() if value is None else value
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationError("repository root must exist and be a directory") from None
    if not resolved.is_dir():
        raise ValidationError("repository root must exist and be a directory")
    return resolved


def _write_canonical(value: object) -> int:
    text = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.write(f"{text}\n")
    return 0


def _read_manifest(path: Path) -> str:
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise ArtifactError("manifest must be an existing regular file")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SchemaError("dataset source manifest is not valid UTF-8") from None
    except OSError as error:
        raise ArtifactError("dataset source manifest cannot be read") from error


def _registry_command(arguments: argparse.Namespace) -> int:
    del arguments
    return _write_canonical(dataset_registry_to_dict(default_dataset_registry()))


def _source_discover_command(arguments: argparse.Namespace) -> int:
    entry = get_dataset_registry_entry(
        arguments.dataset_id,
        registry=default_dataset_registry(),
    )
    manifest = discover_dataset_source(
        entry,
        dataset_version=arguments.dataset_version,
        adapter_version=arguments.adapter_version,
        source_root=arguments.source_root,
        source_root_label=arguments.source_root_label,
        checksum_mode=arguments.checksum_mode,
        max_files=arguments.max_files,
        max_total_bytes=arguments.max_total_bytes,
    )
    sys.stdout.write(dataset_source_manifest_to_canonical_json(manifest))
    return 0


def _source_verify_command(arguments: argparse.Namespace) -> int:
    manifest = dataset_source_manifest_from_json(_read_manifest(arguments.manifest))
    verify_dataset_source(manifest, source_root=arguments.source_root)
    sys.stdout.write(dataset_source_manifest_to_canonical_json(manifest))
    return 0


def _synthetic_check_command(arguments: argparse.Namespace) -> int:
    dataset = build_synthetic_dataset()
    scenarios = synthetic_scenarios(dataset)
    frames = synthetic_coordinate_frames(dataset)
    agents = synthetic_agents(dataset)
    trajectories = synthetic_trajectories(dataset)
    sample_count = sum(len(trajectory.samples) for trajectory in trajectories)
    counts = (
        len(scenarios),
        len(agents),
        len(trajectories),
        sample_count,
    )
    if counts != (16, 27, 27, 293):
        raise SchemaError("synthetic dataset counts differ from the approved contract")
    for bundle in dataset.scenarios:
        validate_scenario_bundle(
            bundle.scenario,
            bundle.coordinate_frame,
            bundle.agents,
            bundle.trajectories,
        )
    report = validate_canonical_tables(
        scenario_records_to_table(scenarios),
        coordinate_frame_records_to_table(frames),
        agent_records_to_table(agents),
        trajectories_to_table(trajectories),
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=arguments.minimum_valid_sample_count,
            minimum_valid_duration_ns=arguments.minimum_valid_duration_ns,
            allowed_agent_classes=tuple(AgentClass),
            require_source_map=False,
        ),
    )
    if not report.is_eligible:
        raise SchemaError("synthetic canonical validation report is not eligible")
    return _write_canonical(
        {
            "schema_version": "1.0",
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "scenario_count": len(scenarios),
            "coordinate_frame_count": len(frames),
            "agent_count": len(agents),
            "trajectory_count": len(trajectories),
            "sample_count": sample_count,
            "included_scenario_count": report.included_scenario_count,
            "included_agent_count": report.included_agent_count,
            "included_trajectory_count": report.included_trajectory_count,
            "exclusion_count": report.exclusion_count,
            "validation_report_identity": canonical_sha256(
                "canonical-validation-report",
                canonical_validation_report_to_dict(report),
            ),
        }
    )


def _validate_command(arguments: argparse.Namespace) -> int:
    paths = CanonicalDatasetPaths(
        scenario_manifest=tuple(arguments.scenario_manifest),
        coordinate_frame_metadata=tuple(arguments.coordinate_frame_metadata),
        agent_metadata=tuple(arguments.agent_metadata),
        trajectory_samples=tuple(arguments.trajectory_samples),
        vector_map_elements=tuple(arguments.vector_map_elements or ()),
    )
    config = CanonicalValidationConfig(
        minimum_valid_sample_count=arguments.minimum_valid_sample_count,
        minimum_valid_duration_ns=arguments.minimum_valid_duration_ns,
        allowed_agent_classes=tuple(arguments.allowed_agent_class or AgentClass),
        require_source_map=arguments.require_source_map,
    )
    report = validate_canonical_parquet_dataset(
        _repository_root(arguments.repository_root),
        paths,
        config=config,
        batch_size=arguments.batch_size,
    )
    sys.stdout.write(canonical_validation_report_to_canonical_json(report))
    return 0


def _cache_scan_command(arguments: argparse.Namespace) -> int:
    inventory = scan_materialization_cache(
        _repository_root(arguments.repository_root),
        arguments.cache_root,
    )
    sys.stdout.write(materialization_cache_inventory_to_canonical_json(inventory))
    return 0


def _cache_prune_command(arguments: argparse.Namespace) -> int:
    repository_root = _repository_root(arguments.repository_root)
    cache_root = normalize_relative_path(arguments.cache_root)
    dry_run = not arguments.apply
    incomplete = prune_incomplete_materialization_cache(
        repository_root,
        cache_root,
        dry_run=dry_run,
    )
    return _write_canonical(
        {
            "schema_version": "1.0",
            "cache_relative_root": cache_root.as_posix(),
            "dry_run": dry_run,
            "incomplete_entry_count": len(incomplete),
            "incomplete_entry_directories": list(incomplete),
        }
    )


def _add_validation_thresholds(
    parser: argparse.ArgumentParser, *, synthetic: bool
) -> None:
    parser.add_argument(
        "--minimum-valid-sample-count",
        type=_integer_argument,
        default=1 if synthetic else 10,
    )
    parser.add_argument(
        "--minimum-valid-duration-ns",
        type=_integer_argument,
        default=0 if synthetic else 1_000_000_000,
    )


def _add_cache_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--cache-root", type=Path, default=_DEFAULT_CACHE_ROOT)


def register_data_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the complete supported ``kinematicweave data`` command group."""
    data_parser = commands.add_parser(
        "data",
        help="Inspect and validate data-foundation state.",
    )
    subcommands = data_parser.add_subparsers(dest="data_command", required=True)

    registry = subcommands.add_parser(
        "registry",
        help="Print the approved dataset registry.",
    )
    registry.set_defaults(handler=_registry_command)

    discover = subcommands.add_parser(
        "source-discover",
        help="Discover a bounded generated or external dataset source.",
    )
    discover.add_argument("--dataset-id", required=True)
    discover.add_argument("--dataset-version")
    discover.add_argument("--adapter-version", default="1.0")
    discover.add_argument("--source-root", type=Path)
    discover.add_argument("--source-root-label")
    discover.add_argument(
        "--checksum-mode",
        choices=("sha256", "size_only"),
        default="sha256",
    )
    discover.add_argument("--max-files", type=_integer_argument, default=500_000)
    discover.add_argument("--max-total-bytes", type=_integer_argument)
    discover.set_defaults(handler=_source_discover_command)

    verify = subcommands.add_parser(
        "source-verify",
        help="Verify a source against a saved source manifest.",
    )
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--source-root", type=Path)
    verify.set_defaults(handler=_source_verify_command)

    synthetic_check = subcommands.add_parser(
        "synthetic-check",
        help="Build and validate the complete synthetic correctness dataset.",
    )
    _add_validation_thresholds(synthetic_check, synthetic=True)
    synthetic_check.set_defaults(handler=_synthetic_check_command)

    validate = subcommands.add_parser(
        "validate",
        help="Validate bounded canonical Parquet inputs without writing artifacts.",
    )
    for option in (
        "--scenario-manifest",
        "--coordinate-frame-metadata",
        "--agent-metadata",
        "--trajectory-samples",
    ):
        validate.add_argument(option, type=Path, action="append", required=True)
    validate.add_argument("--vector-map-elements", type=Path, action="append")
    validate.add_argument("--repository-root", type=Path)
    _add_validation_thresholds(validate, synthetic=False)
    validate.add_argument("--allowed-agent-class", action="append")
    validate.add_argument("--require-source-map", action="store_true")
    validate.add_argument("--batch-size", type=_integer_argument, default=65_536)
    validate.set_defaults(handler=_validate_command)

    cache_scan = subcommands.add_parser(
        "cache-scan",
        help="Verify and print materialization cache inventory.",
    )
    _add_cache_arguments(cache_scan)
    cache_scan.set_defaults(handler=_cache_scan_command)

    cache_prune = subcommands.add_parser(
        "cache-prune",
        help="List incomplete cache entries or explicitly remove them.",
    )
    _add_cache_arguments(cache_prune)
    cache_prune.add_argument(
        "--apply",
        action="store_true",
        help="Mutating operation: remove validated incomplete .partial entries.",
    )
    cache_prune.set_defaults(handler=_cache_prune_command)
