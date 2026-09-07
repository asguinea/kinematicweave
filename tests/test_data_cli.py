"""Focused and integration tests for the data-foundation CLI."""

import argparse
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, NoReturn, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from kinematicweave.cli import build_parser, main
import kinematicweave.data.cli as data_cli
from kinematicweave.data.materialization import (
    MaterializationUnitSpec,
    materialization_unit_cache_key,
    materialize_cached_unit,
    verify_materialization_cache_entry,
)
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    trajectories_to_table,
)
import kinematicweave.data.registry as registry_module
from kinematicweave.data.registry import (
    DatasetAvailability,
    SourceChecksumMode,
    dataset_registry_to_dict,
    dataset_source_manifest_from_json,
    dataset_source_manifest_to_canonical_json,
    default_dataset_registry,
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
    validate_canonical_parquet_dataset,
)
from kinematicweave.domain.records import AgentClass

FIXED_DISCOVERY_TIME = "2025-01-02T03:04:05.006789Z"
CACHE_ROOT = Path("cache/materialization")


@pytest.fixture(autouse=True)
def _fixed_discovery_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registry_module,
        "utc_now_timestamp",
        lambda: FIXED_DISCOVERY_TIME,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    return root.resolve()


def _invoke(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    code = main(arguments)
    output = capsys.readouterr()
    return code, output.out, output.err


def _source_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _external_source(root: Path) -> Path:
    source = root / "external"
    (source / "nested").mkdir(parents=True)
    (source / "a.bin").write_bytes(b"alpha\x00")
    (source / "nested" / "b.txt").write_text("beta\n", encoding="utf-8")
    return source


def _synthetic_tables() -> dict[str, pa.Table]:
    dataset = build_synthetic_dataset()
    return {
        "scenario_manifest": scenario_records_to_table(synthetic_scenarios(dataset)),
        "coordinate_frame_metadata": coordinate_frame_records_to_table(
            synthetic_coordinate_frames(dataset)
        ),
        "agent_metadata": agent_records_to_table(synthetic_agents(dataset)),
        "trajectory_samples": trajectories_to_table(synthetic_trajectories(dataset)),
    }


def _write_synthetic_parquet(
    repository: Path,
    *,
    trajectory_parts: int = 1,
) -> CanonicalDatasetPaths:
    tables = _synthetic_tables()
    directory = repository / "canonical"
    directory.mkdir()
    paths: dict[str, tuple[Path, ...]] = {}
    for name, table in tables.items():
        if name == "trajectory_samples" and trajectory_parts == 2:
            midpoint = table.num_rows // 2
            partitions: tuple[pa.Table, ...] = (
                table.slice(0, midpoint),
                table.slice(midpoint),
            )
        else:
            partitions = (table,)
        written: list[Path] = []
        for index, partition in enumerate(partitions):
            path = directory / f"{name}-{index:02d}.parquet"
            pq.write_table(partition, path)
            written.append(path.relative_to(repository))
        paths[name] = tuple(written)
    return CanonicalDatasetPaths(
        scenario_manifest=paths["scenario_manifest"],
        coordinate_frame_metadata=paths["coordinate_frame_metadata"],
        agent_metadata=paths["agent_metadata"],
        trajectory_samples=paths["trajectory_samples"],
        vector_map_elements=(),
    )


def _validation_arguments(repository: Path, paths: CanonicalDatasetPaths) -> list[str]:
    arguments = [
        "data",
        "validate",
        "--repository-root",
        str(repository),
        "--minimum-valid-sample-count",
        "1",
        "--minimum-valid-duration-ns",
        "0",
    ]
    options = (
        ("--scenario-manifest", paths.scenario_manifest),
        ("--coordinate-frame-metadata", paths.coordinate_frame_metadata),
        ("--agent-metadata", paths.agent_metadata),
        ("--trajectory-samples", paths.trajectory_samples),
    )
    for option, values in options:
        for value in values:
            arguments.extend((option, Path(value).as_posix()))
    for value in paths.vector_map_elements:
        arguments.extend(("--vector-map-elements", Path(value).as_posix()))
    return arguments


def _cache_unit() -> MaterializationUnitSpec:
    return MaterializationUnitSpec(
        unit_id="unit:data-cli",
        operation_name="data-cli-fixture",
        operation_version="1.0",
        input_identity="a" * 64,
        parameter_identity="b" * 64,
        expected_output_paths=("nested/output.txt",),
        estimated_output_bytes=16,
    )


def _populate_cache(repository: Path) -> tuple[MaterializationUnitSpec, Path]:
    unit = _cache_unit()

    def worker(spec: MaterializationUnitSpec, root: Path) -> None:
        destination = root / "nested" / "output.txt"
        destination.parent.mkdir()
        destination.write_text(f"{spec.unit_id}\n", encoding="utf-8")

    materialize_cached_unit(
        repository,
        CACHE_ROOT,
        unit,
        worker,
        reserve_fraction=0,
    )
    incomplete = repository / CACHE_ROOT / ".temporary" / f"{'c' * 64}.partial"
    incomplete.mkdir(parents=True)
    (incomplete / "work.bin").write_bytes(b"incomplete")
    return unit, incomplete


def test_data_group_and_all_subcommand_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    with pytest.raises(SystemExit) as root_exit:
        main(["--help"])
    assert root_exit.value.code == 0
    root = capsys.readouterr()
    assert "data" in root.out
    assert root.err == ""

    with pytest.raises(SystemExit) as data_exit:
        main(["data", "--help"])
    assert data_exit.value.code == 0
    data_help = capsys.readouterr()
    commands = (
        "registry",
        "source-discover",
        "source-verify",
        "synthetic-check",
        "validate",
        "cache-scan",
        "cache-prune",
    )
    assert all(command in data_help.out for command in commands)
    assert data_help.err == ""
    for command in commands:
        with pytest.raises(SystemExit) as command_exit:
            main(["data", command, "--help"])
        assert command_exit.value.code == 0
        output = capsys.readouterr()
        assert output.out
        assert output.err == ""
        if command == "cache-prune":
            assert "--apply" in output.out
            assert "Mutating operation" in output.out


def test_registry_exact_deterministic_output_and_no_filesystem_access(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("registry command accessed the filesystem")

    monkeypatch.setattr(Path, "read_text", fail)
    monkeypatch.setattr(Path, "write_text", fail)
    first = _invoke(["data", "registry"], capsys)
    second = _invoke(["data", "registry"], capsys)
    assert first == second
    assert first[0] == 0 and first[2] == ""
    expected = dataset_registry_to_dict(default_dataset_registry())
    assert json.loads(first[1]) == expected
    assert [entry["dataset_id"] for entry in json.loads(first[1])["entries"]] == [
        "synthetic_kinematicweave",
        "av2_motion",
    ]
    assert first[1].endswith("\n") and not first[1].endswith("\n\n")


def test_generated_source_discovery_and_version_precedence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, stdout, stderr = _invoke(
        [
            "data",
            "source-discover",
            "--dataset-id",
            "synthetic_kinematicweave",
            "--dataset-version",
            "explicit",
            "--adapter-version",
            "2.0",
            "--checksum-mode",
            "size_only",
        ],
        capsys,
    )
    assert code == 0 and stderr == ""
    manifest = dataset_source_manifest_from_json(stdout)
    assert manifest.dataset_version == "explicit"
    assert manifest.adapter_version == "2.0"
    assert manifest.checksum_mode is SourceChecksumMode.SIZE_ONLY
    assert manifest.availability is DatasetAvailability.GENERATED
    assert manifest.file_count == 0


def test_generated_source_rejects_source_root(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, stdout, stderr = _invoke(
        [
            "data",
            "source-discover",
            "--dataset-id",
            "synthetic_kinematicweave",
            "--source-root",
            str(repository),
        ],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert "generated sources do not accept" in stderr
    assert "Traceback" not in stderr


@pytest.mark.parametrize("mode", ("sha256", "size_only"))
def test_external_source_discovery_modes_do_not_mutate_or_expose_root(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    source = _external_source(repository)
    before = _source_files(source)
    code, stdout, stderr = _invoke(
        [
            "data",
            "source-discover",
            "--dataset-id",
            "av2_motion",
            "--dataset-version",
            "2024.1",
            "--source-root",
            str(source),
            "--source-root-label",
            "external/av2",
            "--checksum-mode",
            mode,
        ],
        capsys,
    )
    assert code == 0 and stderr == ""
    manifest = dataset_source_manifest_from_json(stdout)
    assert manifest.availability is DatasetAvailability.AVAILABLE
    assert manifest.file_count == 2
    assert str(source) not in stdout
    if mode == "sha256":
        assert all(item.sha256 is not None for item in manifest.files)
    else:
        assert all(item.sha256 is None for item in manifest.files)
    assert _source_files(source) == before


def test_external_missing_source_is_reported_without_inference(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = repository / "missing"
    code, stdout, stderr = _invoke(
        [
            "data",
            "source-discover",
            "--dataset-id",
            "av2_motion",
            "--dataset-version",
            "2024.1",
            "--source-root",
            str(missing),
            "--source-root-label",
            "external/av2",
        ],
        capsys,
    )
    assert code == 0 and stderr == ""
    assert (
        dataset_source_manifest_from_json(stdout).availability
        is DatasetAvailability.MISSING
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["--dataset-id", "unknown"],
            "unknown dataset identifier",
        ),
        (
            ["--dataset-id", "av2_motion"],
            "dataset_version is required",
        ),
        (
            [
                "--dataset-id",
                "av2_motion",
                "--dataset-version",
                "1",
            ],
            "require source_root",
        ),
    ],
)
def test_source_discover_expected_failures(
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, stdout, stderr = _invoke(
        ["data", "source-discover", *arguments],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert message in stderr
    assert "Traceback" not in stderr


@pytest.mark.parametrize(
    ("limit_option", "limit"),
    (("--max-files", "1"), ("--max-total-bytes", "1")),
)
def test_source_discover_enforces_file_and_byte_limits(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    limit_option: str,
    limit: str,
) -> None:
    source = _external_source(repository)
    code, stdout, stderr = _invoke(
        [
            "data",
            "source-discover",
            "--dataset-id",
            "av2_motion",
            "--dataset-version",
            "1",
            "--source-root",
            str(source),
            "--source-root-label",
            "external",
            limit_option,
            limit,
        ],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert "exceeds" in stderr


def test_source_discover_rejects_boolean_like_integer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "data",
                "source-discover",
                "--dataset-id",
                "synthetic_kinematicweave",
                "--max-files",
                "true",
            ]
        )
    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "expected an integer" in output.err


def test_generated_source_manifest_verification(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, discovered, stderr = _invoke(
        ["data", "source-discover", "--dataset-id", "synthetic_kinematicweave"],
        capsys,
    )
    assert code == 0 and stderr == ""
    manifest_path = repository / "generated.json"
    manifest_path.write_text(discovered, encoding="utf-8")
    code, verified, stderr = _invoke(
        ["data", "source-verify", "--manifest", str(manifest_path)],
        capsys,
    )
    assert code == 0 and stderr == ""
    assert verified == discovered


def test_external_source_discover_save_verify_integration(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _external_source(repository)
    before = _source_files(source)
    code, discovered, stderr = _invoke(
        [
            "data",
            "source-discover",
            "--dataset-id",
            "av2_motion",
            "--dataset-version",
            "2024.1",
            "--source-root",
            str(source),
            "--source-root-label",
            "external/av2",
        ],
        capsys,
    )
    assert code == 0 and stderr == ""
    manifest_path = repository / "source-manifest.json"
    pretty = json.dumps(json.loads(discovered), indent=2)
    manifest_path.write_text(pretty, encoding="utf-8")
    code, verified, stderr = _invoke(
        [
            "data",
            "source-verify",
            "--manifest",
            str(manifest_path),
            "--source-root",
            str(source),
        ],
        capsys,
    )
    assert code == 0 and stderr == ""
    manifest = dataset_source_manifest_from_json(pretty)
    assert verified == dataset_source_manifest_to_canonical_json(manifest)
    assert _source_files(source) == before


def test_source_verify_detects_missing_and_altered_external_source(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _external_source(repository)
    _, discovered, _ = _invoke(
        [
            "data",
            "source-discover",
            "--dataset-id",
            "av2_motion",
            "--dataset-version",
            "1",
            "--source-root",
            str(source),
            "--source-root-label",
            "external",
        ],
        capsys,
    )
    manifest_path = repository / "manifest.json"
    manifest_path.write_text(discovered, encoding="utf-8")
    code, stdout, stderr = _invoke(
        ["data", "source-verify", "--manifest", str(manifest_path)],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert "requires source_root" in stderr
    (source / "a.bin").write_bytes(b"omega\x00")
    code, stdout, stderr = _invoke(
        [
            "data",
            "source-verify",
            "--manifest",
            str(manifest_path),
            "--source-root",
            str(source),
        ],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert "differs" in stderr


@pytest.mark.parametrize("kind", ("missing", "directory", "malformed", "utf8"))
def test_source_verify_manifest_input_failures(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    path = repository / kind
    if kind == "directory":
        path.mkdir()
    elif kind == "malformed":
        path.write_text("{", encoding="utf-8")
    elif kind == "utf8":
        path.write_bytes(b"\xff")
    code, stdout, stderr = _invoke(
        ["data", "source-verify", "--manifest", str(path)],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert stderr.startswith("error:")
    assert "Traceback" not in stderr


def test_synthetic_check_exact_counts_order_identity_and_no_writes(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(repository)
    before = tuple(repository.rglob("*"))
    first = _invoke(["data", "synthetic-check"], capsys)
    second = _invoke(["data", "synthetic-check"], capsys)
    assert first == second
    assert first[0] == 0 and first[2] == ""
    pairs = json.loads(first[1], object_pairs_hook=list)
    assert [key for key, _value in pairs] == [
        "schema_version",
        "dataset_id",
        "dataset_version",
        "scenario_count",
        "coordinate_frame_count",
        "agent_count",
        "trajectory_count",
        "sample_count",
        "included_scenario_count",
        "included_agent_count",
        "included_trajectory_count",
        "exclusion_count",
        "validation_report_identity",
    ]
    summary = dict(pairs)
    assert summary["schema_version"] == "1.0"
    assert (
        summary["scenario_count"],
        summary["coordinate_frame_count"],
        summary["agent_count"],
        summary["trajectory_count"],
        summary["sample_count"],
    ) == (16, 16, 27, 27, 293)
    assert summary["included_scenario_count"] == 16
    assert summary["included_agent_count"] == 27
    assert summary["included_trajectory_count"] == 27
    assert summary["exclusion_count"] == 0
    assert len(cast(str, summary["validation_report_identity"])) == 64
    assert tuple(repository.rglob("*")) == before


@pytest.mark.parametrize(
    "arguments",
    (
        ["--minimum-valid-sample-count", "0"],
        ["--minimum-valid-duration-ns", "-1"],
    ),
)
def test_synthetic_check_invalid_thresholds(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, stdout, stderr = _invoke(
        ["data", "synthetic-check", *arguments],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert stderr.startswith("error:")


def test_validate_matches_direct_api_and_preserves_inputs(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_synthetic_parquet(repository)
    before = _source_files(repository / "canonical")
    config = CanonicalValidationConfig(
        minimum_valid_sample_count=1,
        minimum_valid_duration_ns=0,
        allowed_agent_classes=tuple(AgentClass),
        require_source_map=False,
    )
    direct = validate_canonical_parquet_dataset(
        repository,
        paths,
        config=config,
        batch_size=65_536,
    )
    code, stdout, stderr = _invoke(
        _validation_arguments(repository, paths),
        capsys,
    )
    assert code == 0 and stderr == ""
    assert stdout == canonical_validation_report_to_canonical_json(direct)
    assert _source_files(repository / "canonical") == before
    assert not (repository / "artifacts").exists()


def test_validate_multiple_parts_allowed_class_subset_and_exclusion_success(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_synthetic_parquet(repository, trajectory_parts=2)
    arguments = _validation_arguments(repository, paths)
    arguments.extend(("--allowed-agent-class", "vehicle"))
    code, stdout, stderr = _invoke(arguments, capsys)
    assert code == 0 and stderr == ""
    report = json.loads(stdout)
    assert report["config"]["allowed_agent_classes"] == ["vehicle"]
    assert report["exclusions"] == []

    required_map_arguments = _validation_arguments(repository, paths)
    required_map_arguments.append("--require-source-map")
    code, stdout, stderr = _invoke(required_map_arguments, capsys)
    assert code == 0 and stderr == ""
    required_map_report = json.loads(stdout)
    assert required_map_report["included_scenario_ids"] == []
    assert len(required_map_report["exclusions"]) > 0


def test_validate_preserves_repeated_argument_order(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_synthetic_parquet(repository, trajectory_parts=2)
    captured: dict[str, object] = {}

    def fake(
        root: Path,
        supplied: CanonicalDatasetPaths,
        *,
        config: CanonicalValidationConfig,
        batch_size: int,
    ) -> Any:
        captured["paths"] = supplied
        return validate_canonical_parquet_dataset(
            root,
            supplied,
            config=config,
            batch_size=batch_size,
        )

    monkeypatch.setattr(data_cli, "validate_canonical_parquet_dataset", fake)
    code, _stdout, stderr = _invoke(
        _validation_arguments(repository, paths),
        capsys,
    )
    assert code == 0 and stderr == ""
    supplied = cast(CanonicalDatasetPaths, captured["paths"])
    assert supplied.trajectory_samples == paths.trajectory_samples


def test_validate_missing_duplicate_corrupt_and_invalid_options(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_synthetic_parquet(repository)
    missing_arguments = _validation_arguments(repository, paths)
    missing_arguments[
        missing_arguments.index("canonical/scenario_manifest-00.parquet")
    ] = "canonical/missing.parquet"
    code, stdout, stderr = _invoke(missing_arguments, capsys)
    assert code == 2 and stdout == "" and stderr.startswith("error:")

    duplicate_arguments = _validation_arguments(repository, paths)
    scenario_path = Path(paths.scenario_manifest[0]).as_posix()
    frame_path = Path(paths.coordinate_frame_metadata[0]).as_posix()
    duplicate_arguments[duplicate_arguments.index(frame_path)] = scenario_path
    code, stdout, stderr = _invoke(duplicate_arguments, capsys)
    assert code == 2 and stdout == ""
    assert "same path" in stderr

    corrupt_path = repository / paths.scenario_manifest[0]
    corrupt_path.write_bytes(b"not parquet")
    code, stdout, stderr = _invoke(
        _validation_arguments(repository, paths),
        capsys,
    )
    assert code == 2 and stdout == ""
    assert stderr.startswith("error:")

    fresh_repository = repository.parent / "fresh"
    fresh_repository.mkdir()
    fresh_paths = _write_synthetic_parquet(fresh_repository)
    invalid_batch = _validation_arguments(fresh_repository, fresh_paths)
    invalid_batch.extend(("--batch-size", "0"))
    code, stdout, stderr = _invoke(invalid_batch, capsys)
    assert code == 2 and stdout == ""
    assert "positive" in stderr

    with pytest.raises(SystemExit) as malformed:
        main(
            [
                *_validation_arguments(fresh_repository, fresh_paths),
                "--minimum-valid-sample-count",
                "malformed",
            ]
        )
    assert malformed.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "expected an integer" in output.err


def test_validate_unknown_agent_class_fails(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_synthetic_parquet(repository)
    arguments = _validation_arguments(repository, paths)
    arguments.extend(("--allowed-agent-class", "spaceship"))
    code, stdout, stderr = _invoke(arguments, capsys)
    assert code == 2 and stdout == ""
    assert "invalid value" in stderr


def test_cache_scan_missing_complete_and_incomplete_is_deterministic(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "data",
        "cache-scan",
        "--repository-root",
        str(repository),
    ]
    code, stdout, stderr = _invoke(arguments, capsys)
    assert code == 0 and stderr == ""
    assert json.loads(stdout)["entries"] == []
    unit, incomplete = _populate_cache(repository)
    before = _source_files(repository / CACHE_ROOT)
    first = _invoke(arguments, capsys)
    second = _invoke(arguments, capsys)
    assert first == second
    inventory = json.loads(first[1])
    assert len(inventory["entries"]) == 1
    assert inventory["incomplete_entry_directories"] == [
        incomplete.relative_to(repository).as_posix()
    ]
    assert _source_files(repository / CACHE_ROOT) == before
    key = materialization_unit_cache_key(unit)
    assert inventory["entries"][0]["cache_key"] == key


def test_cache_scan_corrupt_entry_fails_without_mutation(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unit, _incomplete = _populate_cache(repository)
    key = materialization_unit_cache_key(unit)
    output = (
        repository / CACHE_ROOT / "entries" / key[:2] / key / "nested" / "output.txt"
    )
    output.write_text("corrupt\n", encoding="utf-8")
    before = _source_files(repository / CACHE_ROOT)
    code, stdout, stderr = _invoke(
        [
            "data",
            "cache-scan",
            "--repository-root",
            str(repository),
        ],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert "differs" in stderr
    assert _source_files(repository / CACHE_ROOT) == before


def test_cache_prune_dry_run_apply_and_completed_entry_survival(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unit, incomplete = _populate_cache(repository)
    base = [
        "data",
        "cache-prune",
        "--repository-root",
        str(repository),
    ]
    code, stdout, stderr = _invoke(base, capsys)
    assert code == 0 and stderr == ""
    pairs = json.loads(stdout, object_pairs_hook=list)
    assert [key for key, _value in pairs] == [
        "schema_version",
        "cache_relative_root",
        "dry_run",
        "incomplete_entry_count",
        "incomplete_entry_directories",
    ]
    dry = dict(pairs)
    assert dry["dry_run"] is True
    assert dry["incomplete_entry_count"] == 1
    assert incomplete.exists()

    code, stdout, stderr = _invoke([*base, "--apply"], capsys)
    assert code == 0 and stderr == ""
    assert json.loads(stdout)["dry_run"] is False
    assert not incomplete.exists()
    key = materialization_unit_cache_key(unit)
    entry = CACHE_ROOT / "entries" / key[:2] / key
    verify_materialization_cache_entry(
        repository,
        entry,
        expected_unit=unit,
    )
    code, stdout, stderr = _invoke([*base, "--apply"], capsys)
    assert code == 0 and stderr == ""
    assert json.loads(stdout)["incomplete_entry_count"] == 0


def test_cache_prune_rejects_invalid_structure(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unexpected = repository / CACHE_ROOT / "unexpected.txt"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_text("unexpected", encoding="utf-8")
    code, stdout, stderr = _invoke(
        [
            "data",
            "cache-prune",
            "--repository-root",
            str(repository),
            "--apply",
        ],
        capsys,
    )
    assert code == 2 and stdout == ""
    assert stderr.startswith("error:")
    assert unexpected.exists()


def test_expected_failure_has_no_partial_json_or_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, stdout, stderr = _invoke(
        ["data", "source-discover", "--dataset-id", "unknown"],
        capsys,
    )
    assert code == 2
    assert stdout == ""
    assert stderr.startswith("error:")
    assert "Traceback" not in stderr


def test_data_cli_import_is_side_effect_free_and_has_no_prohibited_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("data CLI import attempted a side effect")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(Path, "write_text", fail)
    monkeypatch.setattr(Path, "write_bytes", fail)
    module_name = "_kinematicweave_data_cli_import_probe"
    specification = importlib.util.spec_from_file_location(
        module_name,
        data_cli.__file__,
    )
    assert specification is not None and specification.loader is not None
    imported = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = imported
    try:
        specification.loader.exec_module(imported)
    finally:
        del sys.modules[module_name]

    tree = ast.parse(Path(data_cli.__file__).read_text(encoding="utf-8"))
    roots = {
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    roots.update(
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert roots.isdisjoint(
        {
            "argoverse",
            "av2",
            "pandas",
            "scipy",
            "sklearn",
            "torch",
            "rerun",
        }
    )
    assert data_cli.__all__ == ["register_data_commands"]
