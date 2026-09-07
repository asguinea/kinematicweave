"""Focused and integration tests for the laptop-scale AV2 pilot."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from kinematicweave.artifact_store import (
    DiskSpaceSnapshot,
    WrittenArtifact,
    finalize_run_directory,
    prepare_run_directory,
)
from kinematicweave.data import pilot, registry, schemas
from kinematicweave.data.materialization import (
    MaterializationDisposition,
    materialization_unit_cache_key,
    scan_materialization_cache,
)
from kinematicweave.errors import (
    ArtifactError,
    ResourceLimitError,
    SchemaError,
    ValidationError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOTION_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "av2_motion" / "scenario_fixture.json"
)
MAP_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "av2_map"
    / "log_map_archive_fixture-scenario-001.json"
)
CACHE_ROOT = Path("data/cache/av2-pilot")
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
EXPECTED_OUTPUTS = (
    "motion_adapter_summary.json",
    "map_adapter_summary.json",
    "scenario_manifest.parquet",
    "coordinate_frame_metadata.parquet",
    "agent_metadata.parquet",
    "trajectory_samples.parquet",
    "vector_map_elements.parquet",
)
EXPECTED_PUBLIC_API = [
    "Av2PilotArtifacts",
    "Av2PilotConfig",
    "Av2PilotExecution",
    "Av2PilotPlan",
    "Av2PilotReport",
    "Av2PilotResourceMeasurements",
    "Av2PilotSourcePair",
    "av2_pilot_config_to_dict",
    "av2_pilot_materialization_plan",
    "av2_pilot_plan_from_dict",
    "av2_pilot_plan_from_json",
    "av2_pilot_plan_identity",
    "av2_pilot_plan_to_canonical_json",
    "av2_pilot_report_from_dict",
    "av2_pilot_report_from_json",
    "av2_pilot_report_to_canonical_json",
    "av2_pilot_report_to_dict",
    "av2_pilot_resource_measurements_to_dict",
    "av2_pilot_source_pair_to_dict",
    "av2_pilot_summary_markdown",
    "build_av2_pilot_plan",
    "discover_av2_pilot_candidates",
    "execute_av2_pilot",
    "materialize_av2_pilot_artifacts",
    "verify_av2_pilot_artifacts",
    "verify_av2_pilot_sources",
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _motion_payload() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(MOTION_FIXTURE.read_text(encoding="utf-8")),
    )


def _motion_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("observed", pa.bool_()),
            pa.field("track_id", pa.string()),
            pa.field("object_type", pa.string()),
            pa.field("object_category", pa.int64()),
            pa.field("timestep", pa.int64()),
            pa.field("position_x", pa.float64()),
            pa.field("position_y", pa.float64()),
            pa.field("heading", pa.float64()),
            pa.field("velocity_x", pa.float64()),
            pa.field("velocity_y", pa.float64()),
            pa.field("scenario_id", pa.string()),
            pa.field("start_timestamp", pa.int64()),
            pa.field("end_timestamp", pa.int64()),
            pa.field("num_timestamps", pa.int64()),
            pa.field("focal_track_id", pa.string()),
            pa.field("city", pa.string()),
        ]
    )


def _write_scenario(
    source_root: Path,
    partition: Path,
    scenario_id: str,
    *,
    coordinate_offset: float = 0.0,
) -> tuple[Path, Path]:
    directory = source_root / partition / scenario_id
    directory.mkdir(parents=True)
    payload = _motion_payload()
    source_rows = cast(list[dict[str, object]], payload["rows"])
    constants = (
        "start_timestamp",
        "end_timestamp",
        "num_timestamps",
        "focal_track_id",
        "city",
    )
    rows = [
        {
            **row,
            "position_x": cast(float, row["position_x"]) + coordinate_offset,
            "position_y": cast(float, row["position_y"]) + coordinate_offset,
            "scenario_id": scenario_id,
            **{name: payload[name] for name in constants},
        }
        for row in source_rows
    ]
    motion = directory / f"scenario_{scenario_id}.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=_motion_schema()), motion)
    vector_map = directory / f"log_map_archive_{scenario_id}.json"
    vector_map.write_bytes(MAP_FIXTURE.read_bytes())
    return motion, vector_map


def _source_file(path: Path, source_root: Path) -> registry.DatasetSourceFile:
    return registry.DatasetSourceFile(
        relative_path=path.relative_to(source_root),
        size_bytes=path.stat().st_size,
        sha256=_digest(path),
    )


def _manifest(
    files: tuple[registry.DatasetSourceFile, ...],
    *,
    dataset_id: str = "av2_motion",
    dataset_version: str = "1.1",
    source_kind: registry.DatasetSourceKind | str = "external_directory",
    availability: registry.DatasetAvailability | str = "available",
    checksum_mode: registry.SourceChecksumMode | str = "sha256",
) -> registry.DatasetSourceManifest:
    ordered = tuple(sorted(files, key=lambda item: item.relative_path.as_posix()))
    return registry.DatasetSourceManifest(
        schema_version="1.0",
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        adapter_name="av2_motion_adapter",
        adapter_version="1.0",
        source_kind=source_kind,
        source_root_label="test-source",
        availability=availability,
        checksum_mode=checksum_mode,
        file_count=len(ordered),
        total_bytes=sum(item.size_bytes for item in ordered),
        files=ordered,
        license_reference="test license",
        citation_reference="test citation",
        discovered_at_utc="2026-01-01T00:00:00.000000Z",
    )


def _installation(
    tmp_path: Path,
    *,
    scenario_ids: tuple[str, ...] = (
        "fixture-scenario-001",
        "fixture-scenario-002",
        "fixture-scenario-003",
        "fixture-scenario-004",
    ),
) -> tuple[Path, Path, registry.DatasetSourceManifest, dict[Path, str]]:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir()
    source_root = (tmp_path / "source").resolve()
    partition = Path("train")
    paths = tuple(
        path
        for index, scenario_id in enumerate(scenario_ids)
        for path in _write_scenario(
            source_root,
            partition,
            scenario_id,
            coordinate_offset=float(index),
        )
    )
    manifest = _manifest(tuple(_source_file(path, source_root) for path in paths))
    source_hashes = {path: _digest(path) for path in paths}
    return repository, source_root, manifest, source_hashes


def _config(
    scenario_count: int = 1,
    *,
    root_seed: int = 0,
    namespace: str = "av2-real-data-pilot-v1",
    minimum_valid_sample_count: int = 1,
    minimum_valid_duration_ns: int = 0,
    validation_batch_size: int = 2,
    expansion: float = 0.0,
) -> pilot.Av2PilotConfig:
    return pilot.Av2PilotConfig(
        source_partition=Path("train"),
        dataset_version="1.1",
        scenario_count=scenario_count,
        root_seed=root_seed,
        assignment_namespace=namespace,
        minimum_valid_sample_count=minimum_valid_sample_count,
        minimum_valid_duration_ns=minimum_valid_duration_ns,
        validation_batch_size=validation_batch_size,
        materialization_expansion_factor=expansion,
        reserve_fraction=0,
    )


def _pair(
    scenario_id: str = "fixture-scenario-001",
    *,
    motion_sha256: str = HASH_A,
) -> pilot.Av2PilotSourcePair:
    parent = Path("train") / scenario_id
    return pilot.Av2PilotSourcePair(
        source_scenario_id=scenario_id,
        motion_relative_path=parent / f"scenario_{scenario_id}.parquet",
        map_relative_path=parent / f"log_map_archive_{scenario_id}.json",
        motion_size_bytes=100,
        map_size_bytes=200,
        motion_sha256=motion_sha256,
        map_sha256=HASH_B,
    )


def _model_plan(
    *,
    config: pilot.Av2PilotConfig | None = None,
    pairs: tuple[pilot.Av2PilotSourcePair, ...] | None = None,
) -> pilot.Av2PilotPlan:
    selected_config = config or _config()
    selected_pairs = pairs or (_pair(),)
    return pilot.Av2PilotPlan(
        schema_version="1.0",
        dataset_id="av2_motion",
        dataset_version=selected_config.dataset_version,
        source_partition=selected_config.source_partition,
        source_manifest_identity=HASH_C,
        config=selected_config,
        candidate_count=len(selected_pairs),
        selected_scenarios=selected_pairs,
    )


def _resources(
    *,
    selected_source_bytes: int = 300,
    total_seconds: float = 4.0,
) -> pilot.Av2PilotResourceMeasurements:
    return pilot.Av2PilotResourceMeasurements(
        source_verification_seconds=1.0,
        materialization_seconds=2.0,
        validation_seconds=1.0,
        total_seconds=total_seconds,
        selected_source_bytes=selected_source_bytes,
        cache_output_bytes=400,
        disk_free_before_bytes=10_000,
        disk_free_after_bytes=9_600,
    )


def _report(
    *,
    resources: pilot.Av2PilotResourceMeasurements | None = None,
) -> pilot.Av2PilotReport:
    return pilot.Av2PilotReport(
        schema_version="1.0",
        dataset_id="av2_motion",
        dataset_version="1.1",
        plan_identity=HASH_A,
        materialization_plan_identity=HASH_B,
        validation_report_identity=HASH_C,
        selected_scenario_ids=("fixture-scenario-001",),
        candidate_scenario_count=1,
        selected_scenario_count=1,
        materialized_scenario_count=1,
        reused_scenario_count=0,
        source_scenario_count=1,
        source_coordinate_frame_count=1,
        source_agent_count=2,
        source_trajectory_count=2,
        source_sample_count=10,
        source_map_element_count=5,
        included_scenario_count=1,
        included_agent_count=2,
        included_trajectory_count=2,
        included_map_element_count=5,
        exclusion_count=0,
        cache_relative_root=CACHE_ROOT,
        cache_entry_directories=(CACHE_ROOT / "entries/aa/entry",),
        resources=resources or _resources(),
    )


def _written(path: Path, repository: Path) -> WrittenArtifact:
    data = path.read_bytes()
    return WrittenArtifact(
        relative_path=path.relative_to(repository),
        size_bytes=len(data),
        content_checksum=hashlib.sha256(data).hexdigest(),
    )


def test_exact_public_api_and_model_fields() -> None:
    assert pilot.__all__ == EXPECTED_PUBLIC_API
    expected = {
        pilot.Av2PilotConfig: (
            "source_partition",
            "dataset_version",
            "canonical_split_name",
            "scenario_count",
            "root_seed",
            "assignment_namespace",
            "motion_adapter_version",
            "map_adapter_version",
            "inclusion_policy",
            "centerline_point_count",
            "minimum_valid_sample_count",
            "minimum_valid_duration_ns",
            "row_group_size",
            "validation_batch_size",
            "materialization_expansion_factor",
            "reserve_fraction",
        ),
        pilot.Av2PilotSourcePair: (
            "source_scenario_id",
            "motion_relative_path",
            "map_relative_path",
            "motion_size_bytes",
            "map_size_bytes",
            "motion_sha256",
            "map_sha256",
        ),
        pilot.Av2PilotPlan: (
            "schema_version",
            "dataset_id",
            "dataset_version",
            "source_partition",
            "source_manifest_identity",
            "config",
            "candidate_count",
            "selected_scenarios",
        ),
        pilot.Av2PilotResourceMeasurements: (
            "source_verification_seconds",
            "materialization_seconds",
            "validation_seconds",
            "total_seconds",
            "selected_source_bytes",
            "cache_output_bytes",
            "disk_free_before_bytes",
            "disk_free_after_bytes",
        ),
        pilot.Av2PilotReport: (
            "schema_version",
            "dataset_id",
            "dataset_version",
            "plan_identity",
            "materialization_plan_identity",
            "validation_report_identity",
            "selected_scenario_ids",
            "candidate_scenario_count",
            "selected_scenario_count",
            "materialized_scenario_count",
            "reused_scenario_count",
            "source_scenario_count",
            "source_coordinate_frame_count",
            "source_agent_count",
            "source_trajectory_count",
            "source_sample_count",
            "source_map_element_count",
            "included_scenario_count",
            "included_agent_count",
            "included_trajectory_count",
            "included_map_element_count",
            "exclusion_count",
            "cache_relative_root",
            "cache_entry_directories",
            "resources",
        ),
        pilot.Av2PilotExecution: (
            "plan",
            "materialization_plan",
            "materialization_report",
            "dataset_paths",
            "validation_report",
            "report",
        ),
        pilot.Av2PilotArtifacts: (
            "pilot_plan",
            "pilot_report",
            "pilot_summary",
        ),
    }
    for model, names in expected.items():
        assert tuple(field.name for field in fields(model)) == names
        assert cast(Any, model).__slots__ == names


def test_models_are_frozen_and_config_defaults_are_exact() -> None:
    config = pilot.Av2PilotConfig(
        source_partition=Path("train"),
        dataset_version="1.1",
        scenario_count=5,
        root_seed=0,
    )
    assert config.canonical_split_name == "pilot"
    assert config.assignment_namespace == "av2-real-data-pilot-v1"
    assert config.motion_adapter_version == "1.0"
    assert config.map_adapter_version == "1.0"
    assert cast(Any, config.inclusion_policy).value == "dynamic_only"
    assert config.centerline_point_count == 50
    assert config.minimum_valid_sample_count == 10
    assert config.minimum_valid_duration_ns == 1_000_000_000
    assert config.row_group_size == 65_536
    assert config.validation_batch_size == 65_536
    assert config.materialization_expansion_factor == 1.5
    assert config.reserve_fraction == 0.15
    with pytest.raises(FrozenInstanceError):
        config.root_seed = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("source_partition", "../escape"),
        ("dataset_version", " "),
        ("canonical_split_name", ""),
        ("scenario_count", 0),
        ("scenario_count", True),
        ("root_seed", -1),
        ("root_seed", True),
        ("assignment_namespace", ""),
        ("motion_adapter_version", ""),
        ("map_adapter_version", ""),
        ("inclusion_policy", "invalid"),
        ("centerline_point_count", 1),
        ("centerline_point_count", True),
        ("minimum_valid_sample_count", 0),
        ("minimum_valid_duration_ns", -1),
        ("minimum_valid_duration_ns", 2**63),
        ("row_group_size", 0),
        ("validation_batch_size", True),
        ("materialization_expansion_factor", -0.1),
        ("materialization_expansion_factor", math.inf),
        ("reserve_fraction", 1.0),
        ("reserve_fraction", True),
    ],
)
def test_config_rejects_invalid_values(field_name: str, value: object) -> None:
    values: dict[str, object] = {
        "source_partition": Path("train"),
        "dataset_version": "1.1",
        "scenario_count": 1,
        "root_seed": 0,
    }
    values[field_name] = value
    with pytest.raises(ValidationError):
        pilot.Av2PilotConfig(**cast(Any, values))


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("source_scenario_id", "bad:id"),
        ("source_scenario_id", "bad\nid"),
        ("motion_relative_path", "../scenario.parquet"),
        ("motion_size_bytes", -1),
        ("motion_size_bytes", True),
        ("map_size_bytes", -1),
        ("motion_sha256", "A" * 64),
        ("map_sha256", "short"),
    ],
)
def test_source_pair_rejects_invalid_values(
    field_name: str,
    value: object,
) -> None:
    values = pilot.av2_pilot_source_pair_to_dict(_pair())
    values[field_name] = value
    with pytest.raises(ValidationError):
        pilot.Av2PilotSourcePair(**cast(Any, values))


def test_source_pair_requires_exact_filenames_and_parent() -> None:
    pair = _pair()
    assert pair.total_source_bytes == 300
    with pytest.raises(ValidationError, match="motion filename"):
        replace(pair, motion_relative_path=Path("train/x/scenario_wrong.parquet"))
    with pytest.raises(ValidationError, match="share one parent"):
        replace(
            pair,
            map_relative_path=Path(
                "other/fixture-scenario-001/log_map_archive_fixture-scenario-001.json"
            ),
        )


def test_plan_validates_counts_uniqueness_and_derived_bytes() -> None:
    config = _config(2)
    pairs = (_pair("fixture-scenario-001"), _pair("fixture-scenario-002"))
    plan = _model_plan(config=config, pairs=pairs)
    assert plan.selected_scenario_count == 2
    assert plan.selected_motion_bytes == 200
    assert plan.selected_map_bytes == 400
    assert plan.selected_total_source_bytes == 600
    with pytest.raises(ValidationError, match="selected count"):
        replace(plan, selected_scenarios=(pairs[0],))
    with pytest.raises(ValidationError, match="unique"):
        replace(plan, selected_scenarios=(pairs[0], pairs[0]))
    with pytest.raises(ValidationError, match="candidate_count"):
        replace(plan, candidate_count=1)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("source_verification_seconds", -1),
        ("materialization_seconds", math.nan),
        ("validation_seconds", True),
        ("selected_source_bytes", -1),
        ("cache_output_bytes", True),
    ],
)
def test_resource_measurements_reject_invalid_values(
    field_name: str,
    value: object,
) -> None:
    values = pilot.av2_pilot_resource_measurements_to_dict(_resources())
    values[field_name] = value
    with pytest.raises(ValidationError):
        pilot.Av2PilotResourceMeasurements(**cast(Any, values))


def test_report_validates_counts_and_derived_properties() -> None:
    report = _report()
    assert report.is_eligible
    assert report.scenarios_per_second == 0.25
    assert report.source_mebibytes == 300 / 1_048_576
    assert report.output_mebibytes == 400 / 1_048_576
    assert replace(report, included_scenario_count=0).is_eligible is False
    assert (
        replace(
            report,
            resources=pilot.Av2PilotResourceMeasurements(
                source_verification_seconds=0,
                materialization_seconds=0,
                validation_seconds=0,
                total_seconds=0,
                selected_source_bytes=300,
                cache_output_bytes=400,
                disk_free_before_bytes=10_000,
                disk_free_after_bytes=9_600,
            ),
        ).scenarios_per_second
        is None
    )
    with pytest.raises(ValidationError, match="materialized and reused"):
        replace(report, reused_scenario_count=1)
    with pytest.raises(ValidationError, match="exceeds"):
        replace(report, included_agent_count=3)


def test_candidate_discovery_exact_layout_order_and_orphans() -> None:
    files = (
        registry.DatasetSourceFile(
            Path("train/b/scenario_b.parquet"),
            2,
            HASH_A,
        ),
        registry.DatasetSourceFile(
            Path("train/b/log_map_archive_b.json"),
            3,
            HASH_B,
        ),
        registry.DatasetSourceFile(
            Path("train/a/scenario_a.parquet"),
            5,
            HASH_C,
        ),
        registry.DatasetSourceFile(
            Path("train/a/log_map_archive_a.json"),
            7,
            HASH_A,
        ),
        registry.DatasetSourceFile(
            Path("train/orphan/scenario_orphan.parquet"),
            1,
            HASH_B,
        ),
        registry.DatasetSourceFile(
            Path("other/c/log_map_archive_c.json"),
            1,
            HASH_C,
        ),
        registry.DatasetSourceFile(Path("train/README.txt"), 1, HASH_A),
    )
    candidates = pilot.discover_av2_pilot_candidates(
        _manifest(files),
        config=_config(2),
    )
    assert tuple(item.source_scenario_id for item in candidates) == ("a", "b")
    assert tuple(item.total_source_bytes for item in candidates) == (12, 5)


def test_candidate_discovery_rejects_mismatched_filename() -> None:
    files = (
        registry.DatasetSourceFile(
            Path("train/a/scenario_wrong.parquet"),
            1,
            HASH_A,
        ),
        registry.DatasetSourceFile(
            Path("train/a/log_map_archive_a.json"),
            1,
            HASH_B,
        ),
    )
    with pytest.raises(ValidationError, match="motion filename"):
        pilot.discover_av2_pilot_candidates(
            _manifest(files),
            config=_config(),
        )


@pytest.mark.parametrize(
    "manifest_change",
    [
        {"dataset_id": "synthetic_kinematicweave"},
        {"dataset_version": "2.0"},
        {"source_kind": "generated"},
        {"availability": "missing"},
        {"checksum_mode": "size_only"},
    ],
)
def test_candidate_discovery_rejects_incompatible_manifest(
    manifest_change: dict[str, object],
) -> None:
    pair: tuple[registry.DatasetSourceFile, ...] = (
        registry.DatasetSourceFile(
            Path("train/a/scenario_a.parquet"),
            1,
            HASH_A,
        ),
        registry.DatasetSourceFile(
            Path("train/a/log_map_archive_a.json"),
            1,
            HASH_B,
        ),
    )
    if manifest_change.get("source_kind") == "generated":
        manifest_change["source_kind"] = registry.DatasetSourceKind.GENERATED
        manifest_change["availability"] = registry.DatasetAvailability.GENERATED
        pair = ()
    if manifest_change.get("availability") == "missing":
        manifest_change["availability"] = registry.DatasetAvailability.MISSING
        pair = ()
    if manifest_change.get("checksum_mode") == "size_only":
        manifest_change["checksum_mode"] = registry.SourceChecksumMode.SIZE_ONLY
        pair = tuple(
            registry.DatasetSourceFile(item.relative_path, item.size_bytes, None)
            for item in pair
        )
    manifest = _manifest(pair, **cast(Any, manifest_change))
    with pytest.raises(ValidationError):
        pilot.discover_av2_pilot_candidates(manifest, config=_config())


def test_candidate_discovery_performs_no_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = (
        registry.DatasetSourceFile(
            Path("train/a/scenario_a.parquet"),
            1,
            HASH_A,
        ),
        registry.DatasetSourceFile(
            Path("train/a/log_map_archive_a.json"),
            1,
            HASH_B,
        ),
    )

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("filesystem access is forbidden")

    monkeypatch.setattr(Path, "open", forbidden)
    assert (
        len(
            pilot.discover_av2_pilot_candidates(
                _manifest(files),
                config=_config(),
            )
        )
        == 1
    )


def test_selection_is_stable_manifest_order_independent_and_seeded(
    tmp_path: Path,
) -> None:
    _repository, _source, manifest, _hashes = _installation(tmp_path)
    first = pilot.build_av2_pilot_plan(manifest, config=_config(3))
    repeated = pilot.build_av2_pilot_plan(manifest, config=_config(3))
    reversed_manifest = _manifest(tuple(reversed(manifest.files)))
    reordered = pilot.build_av2_pilot_plan(
        reversed_manifest,
        config=_config(3),
    )
    changed_seed = pilot.build_av2_pilot_plan(
        manifest,
        config=_config(3, root_seed=7),
    )
    changed_namespace = pilot.build_av2_pilot_plan(
        manifest,
        config=_config(3, namespace="alternate"),
    )
    assert first == repeated == reordered
    assert first.source_manifest_identity == (
        registry.dataset_source_manifest_identity(manifest)
    )
    assert first != changed_seed
    assert first != changed_namespace
    assert len(first.selected_scenarios) == 3
    assert len(pilot.av2_pilot_plan_identity(first)) == 64


def test_selection_rejects_insufficient_candidates(tmp_path: Path) -> None:
    _repository, _source, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    with pytest.raises(ValidationError, match="insufficient"):
        pilot.build_av2_pilot_plan(manifest, config=_config(2))


def test_selected_source_verification_reads_only_selected_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    _repository, source_root, manifest, hashes = _installation(tmp_path)
    plan = pilot.build_av2_pilot_plan(manifest, config=_config(2))
    unselected = next(
        item
        for item in manifest.files
        if all(
            item.relative_path
            not in (pair.motion_relative_path, pair.map_relative_path)
            for pair in plan.selected_scenarios
        )
    )
    (source_root / unselected.relative_path).write_bytes(b"not selected")
    pilot.verify_av2_pilot_sources(source_root, plan)
    for pair in plan.selected_scenarios:
        for relative in (pair.motion_relative_path, pair.map_relative_path):
            assert _digest(source_root / relative) == hashes[source_root / relative]


@pytest.mark.parametrize("failure", ["missing", "size", "checksum"])
def test_selected_source_verification_rejects_artifact_failures(
    tmp_path: Path,
    failure: str,
) -> None:
    _repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    plan = pilot.build_av2_pilot_plan(manifest, config=_config())
    pair = plan.selected_scenarios[0]
    path = source_root / pair.motion_relative_path
    if failure == "missing":
        path.unlink()
    elif failure == "size":
        path.write_bytes(path.read_bytes() + b"x")
    else:
        altered = replace(pair, motion_sha256=HASH_A)
        plan = replace(plan, selected_scenarios=(altered,))
    with pytest.raises(ArtifactError):
        pilot.verify_av2_pilot_sources(source_root, plan)


def test_selected_source_verification_rejects_invalid_schema(
    tmp_path: Path,
) -> None:
    _repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    path = source_root / manifest.files[1].relative_path
    if path.suffix != ".parquet":
        path = source_root / manifest.files[0].relative_path
    pq.write_table(pa.table({"wrong": [1]}), path)
    files = tuple(
        _source_file(source_root / item.relative_path, source_root)
        for item in manifest.files
    )
    plan = pilot.build_av2_pilot_plan(_manifest(files), config=_config())
    with pytest.raises(SchemaError):
        pilot.verify_av2_pilot_sources(source_root, plan)


def test_materialization_plan_exact_units_outputs_and_identities() -> None:
    config = _config(2, expansion=1.5)
    pairs = (
        _pair("fixture-scenario-001"),
        _pair("fixture-scenario-002", motion_sha256=HASH_C),
    )
    plan = _model_plan(config=config, pairs=pairs)
    materialization = pilot.av2_pilot_materialization_plan(plan)
    assert tuple(unit.unit_id for unit in materialization.units) == (
        "pilot-unit:av2:fixture-scenario-001",
        "pilot-unit:av2:fixture-scenario-002",
    )
    assert all(
        tuple(Path(path).as_posix() for path in unit.expected_output_paths)
        == EXPECTED_OUTPUTS
        for unit in materialization.units
    )
    assert materialization.units[0].estimated_output_bytes == 450
    assert (
        materialization.units[0].input_identity
        != materialization.units[1].input_identity
    )
    threshold_change = replace(
        plan,
        config=replace(config, minimum_valid_sample_count=99),
    )
    assert (
        pilot.av2_pilot_materialization_plan(threshold_change)
        .units[0]
        .parameter_identity
        == materialization.units[0].parameter_identity
    )
    adapter_change = replace(
        plan,
        config=replace(config, map_adapter_version="2.0"),
    )
    assert (
        pilot.av2_pilot_materialization_plan(adapter_change).units[0].parameter_identity
        != materialization.units[0].parameter_identity
    )


def test_plain_serialization_field_order_round_trip_and_mutation_resistance() -> None:
    plan = _model_plan()
    plan_value = pilot.av2_pilot_plan_to_dict(plan)
    assert tuple(plan_value) == (
        "schema_version",
        "dataset_id",
        "dataset_version",
        "source_partition",
        "source_manifest_identity",
        "config",
        "candidate_count",
        "selected_scenarios",
    )
    text = pilot.av2_pilot_plan_to_canonical_json(plan)
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert pilot.av2_pilot_plan_from_json(text) == plan
    cast(dict[str, object], plan_value["config"])["root_seed"] = 9
    cast(list[object], plan_value["selected_scenarios"]).clear()
    assert plan.config.root_seed == 0
    assert plan.selected_scenario_count == 1

    report = _report()
    report_text = pilot.av2_pilot_report_to_canonical_json(report)
    assert pilot.av2_pilot_report_from_json(report_text) == report
    assert tuple(pilot.av2_pilot_report_to_dict(report)) == (
        "schema_version",
        "dataset_id",
        "dataset_version",
        "plan_identity",
        "materialization_plan_identity",
        "validation_report_identity",
        "selected_scenario_ids",
        "candidate_scenario_count",
        "selected_scenario_count",
        "materialized_scenario_count",
        "reused_scenario_count",
        "source_scenario_count",
        "source_coordinate_frame_count",
        "source_agent_count",
        "source_trajectory_count",
        "source_sample_count",
        "source_map_element_count",
        "included_scenario_count",
        "included_agent_count",
        "included_trajectory_count",
        "included_map_element_count",
        "exclusion_count",
        "cache_relative_root",
        "cache_entry_directories",
        "resources",
    )


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        '{"schema_version":"1.0"}',
        (
            '{"schema_version":"1.0","dataset_id":"av2_motion",'
            '"dataset_version":"1.1","source_partition":"train",'
            f'"source_manifest_identity":"{HASH_A}","config":{{}},'
            '"candidate_count":0,"selected_scenarios":[],"extra":1}'
        ),
    ],
)
def test_strict_json_rejects_malformed_nonobject_missing_or_unknown(
    text: str,
) -> None:
    with pytest.raises(SchemaError):
        pilot.av2_pilot_plan_from_json(text)


def test_summary_contains_quantitative_contract_without_machine_paths() -> None:
    summary = pilot.av2_pilot_summary_markdown(_report())
    assert summary.startswith("# AV2 Laptop-Scale Pilot Summary\n")
    assert summary.endswith("\n") and not summary.endswith("\n\n")
    for text in (
        "Selected scenarios: 1",
        "Materialized scenarios: 1",
        "Selected motion/map source bytes: 300",
        "Source samples: 10",
        "Included trajectories: 2",
        "Scenarios per second: 0.250000",
        "Final eligibility: eligible",
        "sequential and CPU-oriented",
    ):
        assert text in summary
    assert str(PROJECT_ROOT) not in summary


def test_end_to_end_four_candidate_three_scenario_pilot_and_artifacts(
    tmp_path: Path,
) -> None:
    repository, source_root, manifest, source_hashes = _installation(tmp_path)
    plan = pilot.build_av2_pilot_plan(manifest, config=_config(3))
    pilot.verify_av2_pilot_sources(source_root, plan)
    execution = pilot.execute_av2_pilot(
        repository,
        source_root,
        CACHE_ROOT,
        plan,
    )
    assert execution.report.materialized_scenario_count == 3
    assert execution.report.reused_scenario_count == 0
    assert execution.report.source_scenario_count == 3
    assert execution.report.source_coordinate_frame_count == 3
    assert execution.report.source_agent_count == 9
    assert execution.report.source_trajectory_count == 9
    assert execution.report.source_sample_count == 39
    assert execution.report.source_map_element_count > 0
    assert execution.validation_report.config.require_source_map
    assert execution.report.resources.selected_source_bytes == (
        plan.selected_total_source_bytes
    )
    assert math.isfinite(execution.report.resources.total_seconds)
    assert execution.report.resources.total_seconds >= 0

    schema_names = (
        schemas.CanonicalSchemaName.SCENARIO_MANIFEST,
        schemas.CanonicalSchemaName.COORDINATE_FRAME_METADATA,
        schemas.CanonicalSchemaName.AGENT_METADATA,
        schemas.CanonicalSchemaName.TRAJECTORY_SAMPLES,
        schemas.CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    )
    for result in execution.materialization_report.results:
        directory = repository / result.entry_relative_directory
        observed = tuple(
            sorted(
                path.name
                for path in directory.iterdir()
                if path.is_file() and path.name != "cache_entry.json"
            )
        )
        assert observed == tuple(sorted(EXPECTED_OUTPUTS))
        for filename, schema_name in zip(
            EXPECTED_OUTPUTS[2:],
            schema_names,
            strict=True,
        ):
            schemas.validate_arrow_schema(
                pq.ParquetFile(directory / filename).schema_arrow,
                schema_name,
            )

    run = prepare_run_directory(
        repository,
        "results",
        "pilot:integration",
        reserve_fraction=0,
    )
    artifacts = pilot.materialize_av2_pilot_artifacts(run, execution)
    verified_plan, verified_report = pilot.verify_av2_pilot_artifacts(
        repository,
        artifacts,
    )
    assert verified_plan == plan
    assert verified_report == execution.report
    finalize_run_directory(run)
    inventory = scan_materialization_cache(repository, CACHE_ROOT)
    assert inventory.complete_entry_count == 3
    assert inventory.incomplete_entry_count == 0
    assert not any(run.manifests_path.iterdir())
    assert not any(path.suffix == ".parquet" for path in run.path.rglob("*.parquet"))
    assert all(_digest(path) == digest for path, digest in source_hashes.items())


def test_second_execution_reuses_all_entries_without_adapter_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, source_root, manifest, _hashes = _installation(tmp_path)
    plan = pilot.build_av2_pilot_plan(manifest, config=_config(3))
    first = pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    output_hashes = {
        repository / result.entry_relative_directory / output.relative_path: (
            output.sha256
        )
        for result in first.materialization_report.results
        for output in result.outputs
    }

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("adapter worker must not run for complete cache hits")

    monkeypatch.setattr(
        cast(Any, pilot).av2_motion,
        "load_av2_motion_scenario",
        forbidden,
    )
    monkeypatch.setattr(
        cast(Any, pilot).av2_map,
        "load_av2_vector_map",
        forbidden,
    )
    second = pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    assert second.report.selected_scenario_ids == first.report.selected_scenario_ids
    assert second.report.materialized_scenario_count == 0
    assert second.report.reused_scenario_count == 3
    assert all(
        result.disposition is MaterializationDisposition.REUSED
        for result in second.materialization_report.results
    )
    assert all(_digest(path) == digest for path, digest in output_hashes.items())


def test_failure_preserves_checkpoint_and_resume_reuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, source_root, manifest, _hashes = _installation(tmp_path)
    plan = pilot.build_av2_pilot_plan(manifest, config=_config(3))
    original = cast(Any, pilot).av2_motion.load_av2_motion_scenario
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SchemaError("controlled conversion failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        cast(Any, pilot).av2_motion,
        "load_av2_motion_scenario",
        fail_second,
    )
    with pytest.raises(SchemaError, match="controlled"):
        pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    inventory = scan_materialization_cache(repository, CACHE_ROOT)
    assert inventory.complete_entry_count == 1
    assert inventory.incomplete_entry_count == 0

    monkeypatch.setattr(
        cast(Any, pilot).av2_motion,
        "load_av2_motion_scenario",
        original,
    )
    resumed = pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    assert tuple(
        result.disposition for result in resumed.materialization_report.results
    ) == (
        MaterializationDisposition.REUSED,
        MaterializationDisposition.MATERIALIZED,
        MaterializationDisposition.MATERIALIZED,
    )
    final_inventory = scan_materialization_cache(repository, CACHE_ROOT)
    assert final_inventory.complete_entry_count == 3
    assert final_inventory.incomplete_entry_count == 0


def test_source_verification_failure_creates_no_cache_state(
    tmp_path: Path,
) -> None:
    repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    plan = pilot.build_av2_pilot_plan(manifest, config=_config())
    pair = plan.selected_scenarios[0]
    (source_root / pair.motion_relative_path).write_bytes(b"changed")
    with pytest.raises(ArtifactError):
        pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    assert not (repository / CACHE_ROOT).exists()


def test_total_disk_preflight_failure_creates_no_cache_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    plan = pilot.build_av2_pilot_plan(manifest, config=_config())
    calls = 0

    def disk_check(
        _path: Path,
        *,
        required_bytes: int = 0,
        reserve_fraction: float = 0.15,
    ) -> DiskSpaceSnapshot:
        del reserve_fraction
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ResourceLimitError("controlled low disk")
        return DiskSpaceSnapshot(
            total_bytes=10_000,
            used_bytes=1_000,
            free_bytes=9_000,
            required_bytes=required_bytes,
            reserve_bytes=0,
            free_after_write_bytes=9_000 - required_bytes,
        )

    monkeypatch.setattr(pilot, "check_disk_space", disk_check)
    with pytest.raises(ResourceLimitError, match="controlled"):
        pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    assert not (repository / CACHE_ROOT).exists()


def test_corrupt_complete_entry_fails_without_repair(tmp_path: Path) -> None:
    repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    plan = pilot.build_av2_pilot_plan(manifest, config=_config())
    first = pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    result = first.materialization_report.results[0]
    output = repository / result.entry_relative_directory / EXPECTED_OUTPUTS[0]
    output.write_bytes(output.read_bytes() + b"x")
    with pytest.raises(ArtifactError):
        pilot.execute_av2_pilot(repository, source_root, CACHE_ROOT, plan)
    assert output.read_bytes().endswith(b"x")


def test_structurally_valid_eligibility_exclusions_are_reported(
    tmp_path: Path,
) -> None:
    repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    config = _config(
        minimum_valid_sample_count=100,
        minimum_valid_duration_ns=10_000_000_000,
    )
    plan = pilot.build_av2_pilot_plan(manifest, config=config)
    execution = pilot.execute_av2_pilot(
        repository,
        source_root,
        CACHE_ROOT,
        plan,
    )
    assert execution.report.source_scenario_count == 1
    assert execution.report.exclusion_count > 0
    assert not execution.report.is_eligible


def test_artifact_overwrite_finalized_and_tamper_protections(
    tmp_path: Path,
) -> None:
    repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    plan = pilot.build_av2_pilot_plan(manifest, config=_config())
    execution = pilot.execute_av2_pilot(
        repository,
        source_root,
        CACHE_ROOT,
        plan,
    )
    run = prepare_run_directory(
        repository,
        "results",
        "artifact:protection",
        reserve_fraction=0,
    )
    artifacts = pilot.materialize_av2_pilot_artifacts(run, execution)
    with pytest.raises(ArtifactError, match="already exists"):
        pilot.materialize_av2_pilot_artifacts(run, execution)
    finalize_run_directory(run)
    with pytest.raises(ArtifactError, match="immutable"):
        pilot.materialize_av2_pilot_artifacts(
            run,
            execution,
            relative_directory="artifacts/another-pilot",
        )

    summary_path = repository / artifacts.pilot_summary.relative_path
    summary_path.write_text("altered\n", encoding="utf-8")
    altered = replace(
        artifacts,
        pilot_summary=_written(summary_path, repository),
    )
    with pytest.raises(SchemaError, match="summary"):
        pilot.verify_av2_pilot_artifacts(repository, altered)


def test_changed_source_checksum_changes_cache_entry(tmp_path: Path) -> None:
    repository, source_root, manifest, _hashes = _installation(
        tmp_path,
        scenario_ids=("fixture-scenario-001",),
    )
    first_plan = pilot.build_av2_pilot_plan(manifest, config=_config())
    first = pilot.execute_av2_pilot(
        repository,
        source_root,
        CACHE_ROOT,
        first_plan,
    )
    pair = first_plan.selected_scenarios[0]
    motion_path = source_root / pair.motion_relative_path
    table = pq.read_table(motion_path)
    rows = cast(list[dict[str, object]], table.to_pylist())
    rows[0]["position_x"] = cast(float, rows[0]["position_x"]) + 0.25
    pq.write_table(pa.Table.from_pylist(rows, schema=_motion_schema()), motion_path)
    changed_manifest = _manifest(
        tuple(
            _source_file(source_root / item.relative_path, source_root)
            for item in manifest.files
        )
    )
    changed_plan = pilot.build_av2_pilot_plan(changed_manifest, config=_config())
    changed = pilot.execute_av2_pilot(
        repository,
        source_root,
        CACHE_ROOT,
        changed_plan,
    )
    assert (
        first.materialization_report.results[0].cache_key
        != changed.materialization_report.results[0].cache_key
    )
    assert changed.report.materialized_scenario_count == 1
    assert (
        len(tuple((repository / CACHE_ROOT / "entries").rglob("cache_entry.json"))) == 2
    )


def test_import_contract_has_no_side_effect_or_prohibited_imports() -> None:
    source = Path(pilot.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    prohibited = {
        "av2",
        "pandas",
        "scipy",
        "sklearn",
        "networkx",
        "torch",
        "rerun",
        "subprocess",
    }
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not imports.intersection(prohibited)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {
            "open",
            "read_text",
            "read_bytes",
            "write_text",
            "write_bytes",
            "mkdir",
        }
        for node in tree.body
    )


def test_materialization_key_helper_matches_plan() -> None:
    plan = pilot.av2_pilot_materialization_plan(_model_plan())
    unit = plan.units[0]
    assert len(materialization_unit_cache_key(unit)) == 64
