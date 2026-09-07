"""Tests for canonical cross-table validation and exclusion reporting."""

import ast
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import importlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from shapely import to_wkb  # type: ignore[import-untyped]
from shapely.geometry import LineString, Point, Polygon  # type: ignore[import-untyped]

from kinematicweave import artifact_store
from kinematicweave.artifact_store import WrittenArtifact
from kinematicweave.canonical import canonical_sha256
from kinematicweave.data import av2_map, av2_motion, synthetic, validation
import kinematicweave.data.parquet_io as parquet_io_module
from kinematicweave.data.parquet_io import (
    agent_records_to_table,
    coordinate_frame_records_to_table,
    scenario_records_to_table,
    sort_canonical_table,
    trajectories_to_table,
    vector_map_elements_to_table,
)
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.data.validation import (
    CanonicalDatasetPaths,
    CanonicalExclusionRecord,
    CanonicalValidationArtifacts,
    CanonicalValidationConfig,
    CanonicalValidationReport,
    ExclusionReason,
    ValidationUnitType,
    canonical_exclusion_record_to_dict,
    canonical_exclusions_jsonl,
    canonical_validation_config_to_dict,
    canonical_validation_report_from_dict,
    canonical_validation_report_from_json,
    canonical_validation_report_to_canonical_json,
    canonical_validation_report_to_dict,
    canonical_validation_summary_markdown,
    materialize_canonical_validation_report,
    validate_and_materialize_canonical_parquet_dataset,
    validate_canonical_parquet_dataset,
    validate_canonical_tables,
    verify_canonical_validation_artifacts,
)
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    MapGeometryType,
    VectorMapElementRecord,
    geometry_to_canonical_wkb,
)
from kinematicweave.domain.records import AgentClass, OriginType
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError

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


@pytest.fixture(scope="module", autouse=True)
def _refresh_materializers_after_schema_reload() -> None:
    """Bind materializers to schema classes refreshed by import-safety tests."""
    importlib.reload(parquet_io_module)
    importlib.reload(synthetic)
    importlib.reload(av2_motion)
    importlib.reload(av2_map)


def _dataset(
    kinds: tuple[str, ...] = ("straight_constant_speed",),
) -> synthetic.SyntheticDataset:
    return synthetic.build_synthetic_dataset(kinds)


def _tables(
    *,
    source_map_available: bool = False,
    map_count: int = 0,
    kind: str = "straight_constant_speed",
) -> dict[str, pa.Table]:
    dataset = _dataset((kind,))
    scenarios = list(synthetic.synthetic_scenarios(dataset))
    if source_map_available:
        scenarios[0] = replace(scenarios[0], source_map_available=True)
    tables = {
        "scenario": scenario_records_to_table(scenarios),
        "frame": coordinate_frame_records_to_table(
            synthetic.synthetic_coordinate_frames(dataset)
        ),
        "agent": agent_records_to_table(synthetic.synthetic_agents(dataset)),
        "sample": trajectories_to_table(synthetic.synthetic_trajectories(dataset)),
    }
    if map_count:
        records = tuple(
            _map_record(
                scenarios[0].scenario_id,
                f"map:synthetic:lane:{index:02d}",
            )
            for index in range(map_count)
        )
        tables["map"] = vector_map_elements_to_table(records)
    return tables


def _all_dataset_tables(
    kinds: tuple[str, ...],
) -> dict[str, pa.Table]:
    dataset = _dataset(kinds)
    return {
        "scenario": scenario_records_to_table(synthetic.synthetic_scenarios(dataset)),
        "frame": coordinate_frame_records_to_table(
            synthetic.synthetic_coordinate_frames(dataset)
        ),
        "agent": agent_records_to_table(synthetic.synthetic_agents(dataset)),
        "sample": trajectories_to_table(synthetic.synthetic_trajectories(dataset)),
    }


def _map_record(
    scenario_id: str,
    element_id: str = "map:synthetic:lane:01",
    **overrides: Any,
) -> VectorMapElementRecord:
    values: dict[str, Any] = {
        "scenario_id": scenario_id,
        "map_element_id": element_id,
        "element_type": MapElementType.LANE_CENTERLINE,
        "geometry_type": MapGeometryType.LINESTRING,
        "geometry_wkb": geometry_to_canonical_wkb(LineString([(0.0, 0.0), (2.0, 0.0)])),
        "directionality": Directionality.DIRECTED,
        "parent_element_id": None,
        "successor_ids": (),
        "predecessor_ids": (),
        "left_neighbor_id": None,
        "right_neighbor_id": None,
        "semantic_attributes_json": '{"source":"test"}',
        "origin_type": OriginType.SYNTHETIC,
        "quality_flags": (),
    }
    values.update(overrides)
    return VectorMapElementRecord(**values)


def _mutate(
    table: pa.Table,
    row_index: int = 0,
    **changes: object,
) -> pa.Table:
    rows = cast(list[dict[str, object]], table.to_pylist())
    rows[row_index] = {**rows[row_index], **changes}
    changed = pa.Table.from_pylist(rows, schema=table.schema)
    schema_name = cast(bytes, table.schema.metadata[b"schema_name"]).decode()
    return sort_canonical_table(changed, schema_name)


def _duplicate_row(table: pa.Table, row_index: int = 0) -> pa.Table:
    rows = cast(list[dict[str, object]], table.to_pylist())
    rows.append(dict(rows[row_index]))
    changed = pa.Table.from_pylist(rows, schema=table.schema)
    schema_name = cast(bytes, table.schema.metadata[b"schema_name"]).decode()
    return sort_canonical_table(changed, schema_name)


def _validate(
    tables: dict[str, pa.Table],
    *,
    config: CanonicalValidationConfig | None = None,
) -> CanonicalValidationReport:
    return validate_canonical_tables(
        tables["scenario"],
        tables["frame"],
        tables["agent"],
        tables["sample"],
        tables.get("map"),
        config=CanonicalValidationConfig() if config is None else config,
    )


def _reason_values(report: CanonicalValidationReport) -> tuple[str, ...]:
    return tuple(cast(ExclusionReason, item.reason).value for item in report.exclusions)


def _exclusion(
    *,
    unit_type: ValidationUnitType = ValidationUnitType.TRAJECTORY,
    unit_id: str = "trajectory:test:one",
    reason: ExclusionReason = ExclusionReason.INSUFFICIENT_SAMPLES,
    message: str = "Too few valid samples.",
    related_ids: tuple[str, ...] = (),
) -> CanonicalExclusionRecord:
    payload = {
        "unit_type": unit_type.value,
        "unit_id": unit_id,
        "reason": reason.value,
        "message": message,
        "related_ids": list(related_ids),
    }
    digest = canonical_sha256("canonical-exclusion", payload)
    return CanonicalExclusionRecord(
        exclusion_id=f"exclusion:{unit_type.value}:{digest[:24]}",
        unit_type=unit_type,
        unit_id=unit_id,
        reason=reason,
        message=message,
        related_ids=related_ids,
    )


def _run(tmp_path: Path, run_id: str = "run:validation:test") -> Any:
    return artifact_store.prepare_run_directory(
        tmp_path.resolve(),
        "results",
        run_id,
        reserve_fraction=0,
    )


def _write_tables(
    root: Path,
    tables: dict[str, pa.Table],
    *,
    split_samples: bool = False,
    split_maps: bool = False,
) -> CanonicalDatasetPaths:
    source = root / "canonical"
    source.mkdir(parents=True, exist_ok=True)
    names = {
        "scenario": "scenario_manifest.parquet",
        "frame": "coordinate_frame_metadata.parquet",
        "agent": "agent_metadata.parquet",
    }
    for key, name in names.items():
        pq.write_table(tables[key], source / name)
    sample_paths: tuple[Path, ...]
    if split_samples:
        midpoint = max(1, tables["sample"].num_rows // 2)
        pq.write_table(
            tables["sample"].slice(0, midpoint), source / "samples-0.parquet"
        )
        pq.write_table(tables["sample"].slice(midpoint), source / "samples-1.parquet")
        sample_paths = (
            Path("canonical/samples-0.parquet"),
            Path("canonical/samples-1.parquet"),
        )
    else:
        pq.write_table(tables["sample"], source / "trajectory_samples.parquet")
        sample_paths = (Path("canonical/trajectory_samples.parquet"),)
    map_paths: tuple[Path, ...] = ()
    if "map" in tables:
        if split_maps and tables["map"].num_rows > 1:
            midpoint = tables["map"].num_rows // 2
            pq.write_table(tables["map"].slice(0, midpoint), source / "map-0.parquet")
            pq.write_table(tables["map"].slice(midpoint), source / "map-1.parquet")
            map_paths = (
                Path("canonical/map-0.parquet"),
                Path("canonical/map-1.parquet"),
            )
        else:
            pq.write_table(tables["map"], source / "vector_map_elements.parquet")
            map_paths = (Path("canonical/vector_map_elements.parquet"),)
    return CanonicalDatasetPaths(
        scenario_manifest=("canonical/scenario_manifest.parquet",),
        coordinate_frame_metadata=("canonical/coordinate_frame_metadata.parquet",),
        agent_metadata=("canonical/agent_metadata.parquet",),
        trajectory_samples=sample_paths,
        vector_map_elements=map_paths,
    )


def test_exact_enums_public_api_and_dataclass_fields() -> None:
    assert tuple(item.value for item in ValidationUnitType) == (
        "dataset",
        "scenario",
        "coordinate_frame",
        "agent",
        "trajectory",
        "map_element",
    )
    assert tuple(item.value for item in ExclusionReason) == (
        "adapter_failure",
        "invalid_timestamps",
        "insufficient_samples",
        "insufficient_duration",
        "unsupported_agent_class",
        "no_usable_map",
        "insufficient_trajectory_coverage",
        "coordinate_frame_mismatch",
        "duplicate",
        "geographic_leakage_conflict",
        "invalid_geometry",
        "resource_limit_exclusion",
        "other_documented_reason",
    )
    assert validation.__all__ == [
        "CanonicalDatasetPaths",
        "CanonicalExclusionRecord",
        "CanonicalValidationArtifacts",
        "CanonicalValidationConfig",
        "CanonicalValidationReport",
        "ExclusionReason",
        "ValidationUnitType",
        "canonical_exclusion_record_to_dict",
        "canonical_exclusions_jsonl",
        "canonical_validation_config_to_dict",
        "canonical_validation_report_from_dict",
        "canonical_validation_report_from_json",
        "canonical_validation_report_to_canonical_json",
        "canonical_validation_report_to_dict",
        "canonical_validation_summary_markdown",
        "materialize_canonical_validation_report",
        "validate_and_materialize_canonical_parquet_dataset",
        "validate_canonical_parquet_dataset",
        "validate_canonical_tables",
        "verify_canonical_validation_artifacts",
    ]
    assert tuple(field.name for field in fields(CanonicalValidationConfig)) == (
        "minimum_valid_sample_count",
        "minimum_valid_duration_ns",
        "allowed_agent_classes",
        "require_source_map",
    )
    assert tuple(field.name for field in fields(CanonicalDatasetPaths)) == (
        "scenario_manifest",
        "coordinate_frame_metadata",
        "agent_metadata",
        "trajectory_samples",
        "vector_map_elements",
    )
    assert tuple(field.name for field in fields(CanonicalExclusionRecord)) == (
        "exclusion_id",
        "unit_type",
        "unit_id",
        "reason",
        "message",
        "related_ids",
    )
    assert tuple(field.name for field in fields(CanonicalValidationArtifacts)) == (
        "validation_report",
        "exclusions_jsonl",
        "summary_report",
    )


def test_models_are_frozen_slotted_and_copy_sequences() -> None:
    classes = [AgentClass.VEHICLE.value, AgentClass.PEDESTRIAN]
    config = CanonicalValidationConfig(allowed_agent_classes=classes)
    classes.clear()
    assert config.allowed_agent_classes == (
        AgentClass.VEHICLE,
        AgentClass.PEDESTRIAN,
    )
    assert not hasattr(config, "__dict__")
    with pytest.raises(FrozenInstanceError):
        config.require_source_map = True  # type: ignore[misc]
    related = ["agent:test:one"]
    record = _exclusion(related_ids=tuple(related))
    related.clear()
    assert record.related_ids == ("agent:test:one",)
    assert not hasattr(record, "__dict__")


def test_config_defaults_coercion_and_dictionary_order() -> None:
    config = CanonicalValidationConfig()
    assert config.minimum_valid_sample_count == 10
    assert config.minimum_valid_duration_ns == 1_000_000_000
    assert config.allowed_agent_classes == tuple(AgentClass)
    assert config.require_source_map is False
    value = canonical_validation_config_to_dict(config)
    assert tuple(value) == (
        "minimum_valid_sample_count",
        "minimum_valid_duration_ns",
        "allowed_agent_classes",
        "require_source_map",
    )
    assert value["allowed_agent_classes"] == [item.value for item in AgentClass]


@pytest.mark.parametrize(
    "kwargs",
    (
        {"minimum_valid_sample_count": 0},
        {"minimum_valid_sample_count": True},
        {"minimum_valid_duration_ns": -1},
        {"minimum_valid_duration_ns": True},
        {"minimum_valid_duration_ns": 2**63},
        {"allowed_agent_classes": ()},
        {"allowed_agent_classes": ("vehicle", "vehicle")},
        {"allowed_agent_classes": ("not-a-class",)},
        {"allowed_agent_classes": "vehicle"},
        {"require_source_map": 1},
    ),
)
def test_config_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CanonicalValidationConfig(**cast(Any, kwargs))


def test_dataset_paths_normalize_copy_and_allow_empty_maps() -> None:
    samples = ["parts\\samples.parquet"]
    paths = CanonicalDatasetPaths(
        scenario_manifest=("scenario.parquet",),
        coordinate_frame_metadata=("frame.parquet",),
        agent_metadata=("agents.parquet",),
        trajectory_samples=samples,
        vector_map_elements=(),
    )
    samples.clear()
    assert paths.trajectory_samples == (Path("parts/samples.parquet"),)
    assert paths.vector_map_elements == ()
    assert not hasattr(paths, "__dict__")


@pytest.mark.parametrize(
    "field_name",
    (
        "scenario_manifest",
        "coordinate_frame_metadata",
        "agent_metadata",
        "trajectory_samples",
    ),
)
def test_dataset_paths_require_first_four_groups(field_name: str) -> None:
    values: dict[str, object] = {
        "scenario_manifest": ("scenario.parquet",),
        "coordinate_frame_metadata": ("frame.parquet",),
        "agent_metadata": ("agent.parquet",),
        "trajectory_samples": ("samples.parquet",),
        "vector_map_elements": (),
    }
    values[field_name] = ()
    with pytest.raises(ValidationError, match="empty"):
        CanonicalDatasetPaths(**cast(Any, values))


def test_dataset_paths_reject_duplicates_traversal_and_cross_group_reuse() -> None:
    common = {
        "coordinate_frame_metadata": ("frame.parquet",),
        "agent_metadata": ("agent.parquet",),
        "trajectory_samples": ("samples.parquet",),
        "vector_map_elements": (),
    }
    with pytest.raises(ValidationError, match="duplicates"):
        CanonicalDatasetPaths(
            scenario_manifest=("same.parquet", "same.parquet"),
            **common,
        )
    with pytest.raises(ValidationError, match="two fields"):
        CanonicalDatasetPaths(
            scenario_manifest=("agent.parquet",),
            **common,
        )
    with pytest.raises(ValidationError, match="traversal"):
        CanonicalDatasetPaths(
            scenario_manifest=("../scenario.parquet",),
            **common,
        )


def test_exclusion_identity_validation_and_dictionary() -> None:
    record = _exclusion()
    assert record.exclusion_id.startswith("exclusion:trajectory:")
    assert len(record.exclusion_id.rsplit(":", 1)[1]) == 24
    value = canonical_exclusion_record_to_dict(record)
    assert tuple(value) == (
        "exclusion_id",
        "unit_type",
        "unit_id",
        "reason",
        "message",
        "related_ids",
    )
    assert value["reason"] == "insufficient_samples"
    assert value["related_ids"] == []
    with pytest.raises(ValidationError, match="does not match"):
        replace(record, exclusion_id="exclusion:trajectory:000000000000000000000000")
    with pytest.raises(ValidationError, match="duplicates"):
        _exclusion(related_ids=("agent:test:a", "agent:test:a"))
    with pytest.raises(ValidationError, match="identifier"):
        _exclusion(unit_id="plain-token")


def test_valid_motion_only_report_counts_properties_and_reason_zeros() -> None:
    report = _validate(_tables())
    assert (
        report.source_scenario_count,
        report.source_coordinate_frame_count,
        report.source_agent_count,
        report.source_trajectory_count,
    ) == (1, 1, 1, 1)
    assert report.source_sample_count == 11
    assert report.source_map_element_count == 0
    assert report.included_scenario_count == 1
    assert report.included_agent_count == 1
    assert report.included_trajectory_count == 1
    assert report.included_map_element_count == 0
    assert report.exclusion_count == 0
    assert report.is_eligible
    assert tuple(key for key, _count in report.exclusion_reason_counts) == tuple(
        item.value for item in ExclusionReason
    )
    assert all(count == 0 for _key, count in report.exclusion_reason_counts)


def test_valid_motion_map_report_and_map_order() -> None:
    tables = _tables(source_map_available=True, map_count=2)
    report = _validate(
        tables,
        config=CanonicalValidationConfig(require_source_map=True),
    )
    assert report.is_eligible
    assert report.included_map_element_ids == (
        "map:synthetic:lane:00",
        "map:synthetic:lane:01",
    )


def test_report_serialization_round_trip_jsonl_and_markdown() -> None:
    tables = _tables()
    report = _validate(
        tables,
        config=CanonicalValidationConfig(minimum_valid_sample_count=12),
    )
    value = canonical_validation_report_to_dict(report)
    assert tuple(value) == tuple(field.name for field in fields(report))
    assert canonical_validation_report_from_dict(value) == report
    text = canonical_validation_report_to_canonical_json(report)
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert canonical_validation_report_to_canonical_json(report) == text
    assert canonical_validation_report_from_json(text) == report
    jsonl = canonical_exclusions_jsonl(report)
    assert jsonl.endswith("\n") and "\n\n" not in jsonl
    assert len(jsonl.splitlines()) == report.exclusion_count
    summary = canonical_validation_summary_markdown(report)
    assert summary.startswith("# Canonical Data Validation Summary\n")
    assert summary.endswith("\n") and not summary.endswith("\n\n")
    assert "invalid_timestamps" in summary
    assert "geographic_leakage_conflict" in summary
    assert "Generated at" not in summary
    assert str(PROJECT_ROOT) not in summary
    assert canonical_validation_summary_markdown(report) == summary
    empty = _validate(tables)
    assert canonical_exclusions_jsonl(empty) == ""


@pytest.mark.parametrize(
    "value",
    (
        "",
        "{",
        "[]",
        "null",
    ),
)
def test_report_json_rejects_malformed_or_nonobject(value: str) -> None:
    with pytest.raises(SchemaError):
        canonical_validation_report_from_json(value)


def test_report_dictionary_rejects_unknown_missing_and_nested_invalid_fields() -> None:
    report = _validate(_tables())
    value = canonical_validation_report_to_dict(report)
    with pytest.raises(SchemaError, match="unknown"):
        canonical_validation_report_from_dict({**value, "extra": 1})
    missing = dict(value)
    missing.pop("dataset_id")
    with pytest.raises(SchemaError, match="missing"):
        canonical_validation_report_from_dict(missing)
    bad_config = dict(value)
    bad_config["config"] = {
        **cast(dict[str, object], value["config"]),
        "extra": True,
    }
    with pytest.raises(SchemaError, match="unknown"):
        canonical_validation_report_from_dict(bad_config)


def test_report_rejects_unsorted_exclusions_and_bad_counts() -> None:
    first = _exclusion()
    second = _exclusion(
        unit_type=ValidationUnitType.AGENT,
        unit_id="agent:test:one",
        reason=ExclusionReason.UNSUPPORTED_AGENT_CLASS,
        message="Unsupported.",
    )
    counts = tuple(
        (
            reason.value,
            int(reason is ExclusionReason.INSUFFICIENT_SAMPLES)
            + int(reason is ExclusionReason.UNSUPPORTED_AGENT_CLASS),
        )
        for reason in ExclusionReason
    )
    with pytest.raises(ValidationError, match="canonical order"):
        CanonicalValidationReport(
            schema_version="1.0",
            dataset_id="test",
            dataset_version="1",
            config=CanonicalValidationConfig(),
            source_scenario_count=1,
            source_coordinate_frame_count=1,
            source_agent_count=1,
            source_trajectory_count=1,
            source_sample_count=1,
            source_map_element_count=0,
            included_scenario_ids=(),
            included_agent_ids=(),
            included_trajectory_ids=(),
            included_map_element_ids=(),
            exclusions=(first, second),
            exclusion_reason_counts=counts,
        )
    report = _validate(_tables())
    bad_counts = list(report.exclusion_reason_counts)
    bad_counts[0] = (bad_counts[0][0], 1)
    with pytest.raises(ValidationError, match="do not match"):
        replace(report, exclusion_reason_counts=bad_counts)


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"dataset_id": "other"}, "mixed dataset identifiers"),
        ({"dataset_version": "other"}, "mixed dataset versions"),
    ),
)
def test_mixed_dataset_identity_is_structural(
    change: dict[str, object],
    message: str,
) -> None:
    tables = _all_dataset_tables(("straight_constant_speed", "stop"))
    tables["scenario"] = _mutate(tables["scenario"], 1, **change)
    with pytest.raises(SchemaError, match=message):
        _validate(tables)


def test_duplicate_primary_keys_are_structural() -> None:
    tables = _tables()
    tables["scenario"] = _duplicate_row(tables["scenario"])
    with pytest.raises(SchemaError, match="duplicate scenario"):
        _validate(tables)


def test_missing_scenario_agent_count_and_metadata_mismatch_are_structural() -> None:
    tables = _tables()
    missing = dict(tables)
    missing["agent"] = _mutate(
        tables["agent"],
        scenario_id="scenario:missing:value",
    )
    with pytest.raises(SchemaError, match="missing scenario"):
        _validate(missing)
    bad_count = dict(tables)
    bad_count["scenario"] = _mutate(tables["scenario"], agent_count=2)
    with pytest.raises(SchemaError, match="agent_count"):
        _validate(bad_count)
    bad_metadata = dict(tables)
    bad_metadata["agent"] = _mutate(
        tables["agent"],
        sample_count=10,
    )
    with pytest.raises(SchemaError, match="disagrees"):
        _validate(bad_metadata)


def test_multiple_trajectories_for_one_agent_is_structural() -> None:
    tables = _tables()
    rows = cast(list[dict[str, object]], tables["sample"].to_pylist())
    duplicate = [{**row, "trajectory_id": "trajectory:synthetic:extra"} for row in rows]
    table = pa.Table.from_pylist(rows + duplicate, schema=tables["sample"].schema)
    tables["sample"] = sort_canonical_table(
        table,
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    with pytest.raises(SchemaError, match="exactly one trajectory"):
        _validate(tables)


@pytest.mark.parametrize(
    ("table_name", "field_name", "value", "message"),
    (
        ("agent", "agent_class", "spaceship", "invalid value"),
        ("sample", "speed_mps", -1.0, "negative"),
        ("sample", "heading_rad", math.pi, "interval"),
    ),
)
def test_malformed_canonical_values_are_structural(
    table_name: str,
    field_name: str,
    value: object,
    message: str,
) -> None:
    tables = _tables()
    tables[table_name] = _mutate(
        tables[table_name],
        0,
        **{field_name: value},
    )
    with pytest.raises(SchemaError, match=message):
        _validate(tables)


@pytest.mark.parametrize(
    "changes",
    (
        {"origin_x_m": 1.0},
        {"source_crs": "different"},
        {"distance_unit": "km"},
        {"frame_type": "global"},
        {"has_elevation": True, "origin_z_m": 0.0},
    ),
)
def test_coordinate_frame_mismatch_excludes_scenario_and_descendants(
    changes: dict[str, object],
) -> None:
    tables = _tables()
    tables["frame"] = _mutate(tables["frame"], 0, **changes)
    report = _validate(tables)
    assert report.included_scenario_ids == ()
    assert report.included_agent_ids == ()
    assert report.included_trajectory_ids == ()
    assert _reason_values(report) == ("coordinate_frame_mismatch",)


def test_extra_coordinate_frame_row_is_structural() -> None:
    tables = _tables()
    rows = cast(list[dict[str, object]], tables["frame"].to_pylist())
    rows.append(
        {
            **rows[0],
            "coordinate_frame_id": "frame:synthetic:extra",
        }
    )
    table = pa.Table.from_pylist(rows, schema=tables["frame"].schema)
    tables["frame"] = sort_canonical_table(
        table,
        CanonicalSchemaName.COORDINATE_FRAME_METADATA,
    )
    with pytest.raises(SchemaError, match="exactly one"):
        _validate(tables)


def test_duplicate_source_scenarios_exclude_every_member_with_related_ids() -> None:
    tables = _all_dataset_tables(("straight_constant_speed", "stop"))
    tables["scenario"] = _mutate(
        _mutate(tables["scenario"], 0, source_scenario_id="same-source"),
        1,
        source_scenario_id="same-source",
    )
    report = _validate(tables)
    scenario_exclusions = [
        item
        for item in report.exclusions
        if item.unit_type is ValidationUnitType.SCENARIO
        and item.reason is ExclusionReason.DUPLICATE
    ]
    assert len(scenario_exclusions) == 2
    assert report.included_scenario_ids == ()
    assert all(len(item.related_ids) == 1 for item in scenario_exclusions)
    assert tuple(item.unit_id for item in scenario_exclusions) == tuple(
        sorted(item.unit_id for item in scenario_exclusions)
    )


def test_duplicate_source_agents_exclude_all_without_trajectory_duplicates() -> None:
    tables = _tables(kind="t_junction")
    rows = cast(list[dict[str, object]], tables["agent"].to_pylist())
    assert len(rows) >= 2
    rows[0]["source_agent_id"] = "same-agent"
    rows[1]["source_agent_id"] = "same-agent"
    tables["agent"] = sort_canonical_table(
        pa.Table.from_pylist(rows, schema=tables["agent"].schema),
        CanonicalSchemaName.AGENT_METADATA,
    )
    report = _validate(tables)
    duplicate_records = [
        item for item in report.exclusions if item.reason is ExclusionReason.DUPLICATE
    ]
    assert len(duplicate_records) == 2
    assert all(item.unit_type is ValidationUnitType.AGENT for item in duplicate_records)
    assert not any(
        item.unit_type is ValidationUnitType.TRAJECTORY for item in report.exclusions
    )


def test_trajectory_threshold_boundaries_and_stationary_tracks() -> None:
    tables = _tables()
    rows = cast(list[dict[str, object]], tables["sample"].to_pylist())
    duration = cast(int, rows[-1]["timestamp_ns"]) - cast(
        int,
        rows[0]["timestamp_ns"],
    )
    exact = _validate(
        tables,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=len(rows),
            minimum_valid_duration_ns=duration,
        ),
    )
    assert exact.is_eligible
    stationary = _validate(_tables(kind="stop"))
    assert stationary.is_eligible


def test_insufficient_samples_no_valid_samples_and_duration() -> None:
    tables = _tables()
    too_few = _validate(
        tables,
        config=CanonicalValidationConfig(minimum_valid_sample_count=12),
    )
    assert "insufficient_samples" in _reason_values(too_few)
    invalid_rows = cast(list[dict[str, object]], tables["sample"].to_pylist())
    for row in invalid_rows:
        row["is_valid"] = False
    no_valid = dict(tables)
    no_valid["sample"] = pa.Table.from_pylist(
        invalid_rows,
        schema=tables["sample"].schema,
    )
    report = _validate(no_valid)
    assert "insufficient_samples" in _reason_values(report)
    assert "insufficient_duration" not in _reason_values(report)
    short = _validate(
        tables,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=1,
            minimum_valid_duration_ns=(
                cast(int, invalid_rows[-1]["timestamp_ns"])
                - cast(int, invalid_rows[0]["timestamp_ns"])
                + 1
            ),
        ),
    )
    assert "insufficient_duration" in _reason_values(short)


def test_invalid_diagnostic_samples_do_not_count_but_remain_eligible() -> None:
    tables = _tables()
    rows = cast(list[dict[str, object]], tables["sample"].to_pylist())
    rows[0]["is_valid"] = False
    tables["sample"] = pa.Table.from_pylist(
        rows,
        schema=tables["sample"].schema,
    )
    report = _validate(
        tables,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=len(rows) - 1,
            minimum_valid_duration_ns=0,
        ),
    )
    assert report.is_eligible
    assert report.source_sample_count == len(rows)


@pytest.mark.parametrize("mode", ("duplicate", "decreasing", "outside"))
def test_invalid_timestamps_produce_exclusion(mode: str) -> None:
    tables = _tables()
    rows = cast(list[dict[str, object]], tables["sample"].to_pylist())
    if mode == "duplicate":
        rows[1]["timestamp_ns"] = rows[0]["timestamp_ns"]
    elif mode == "decreasing":
        rows[1]["timestamp_ns"], rows[2]["timestamp_ns"] = (
            rows[2]["timestamp_ns"],
            rows[1]["timestamp_ns"],
        )
    else:
        scenario_rows = cast(
            list[dict[str, object]],
            tables["scenario"].to_pylist(),
        )
        scenario_rows[0]["end_time_ns"] = cast(int, rows[-1]["timestamp_ns"]) - 1
        tables["scenario"] = pa.Table.from_pylist(
            scenario_rows,
            schema=tables["scenario"].schema,
        )
    if mode != "outside":
        tables["sample"] = sort_canonical_table(
            pa.Table.from_pylist(rows, schema=tables["sample"].schema),
            CanonicalSchemaName.TRAJECTORY_SAMPLES,
        )
    report = _validate(tables)
    assert "invalid_timestamps" in _reason_values(report)
    assert report.included_trajectory_ids == ()


def test_unsupported_agent_class_has_no_redundant_trajectory_exclusion() -> None:
    tables = _tables()
    report = _validate(
        tables,
        config=CanonicalValidationConfig(
            allowed_agent_classes=(AgentClass.PEDESTRIAN,),
        ),
    )
    assert "unsupported_agent_class" in _reason_values(report)
    assert "insufficient_trajectory_coverage" in _reason_values(report)
    assert not any(
        item.unit_type is ValidationUnitType.TRAJECTORY for item in report.exclusions
    )


def test_map_not_required_required_absent_and_source_flag_without_rows() -> None:
    absent = _validate(_tables())
    assert absent.is_eligible
    required = _validate(
        _tables(),
        config=CanonicalValidationConfig(require_source_map=True),
    )
    assert _reason_values(required) == ("no_usable_map",)
    flagged = _validate(
        _tables(source_map_available=True),
        config=CanonicalValidationConfig(require_source_map=True),
    )
    assert _reason_values(flagged) == ("no_usable_map",)


@pytest.mark.parametrize(
    ("geometry_wkb", "geometry_type"),
    (
        (b"not-wkb", "linestring"),
        (to_wkb(Point()), "point"),
        (
            to_wkb(
                Polygon(
                    [
                        (0.0, 0.0),
                        (2.0, 2.0),
                        (0.0, 2.0),
                        (2.0, 0.0),
                        (0.0, 0.0),
                    ]
                )
            ),
            "polygon",
        ),
        (
            geometry_to_canonical_wkb(LineString([(0.0, 0.0), (1.0, 0.0)])),
            "polygon",
        ),
    ),
)
def test_invalid_map_geometry_produces_exclusion(
    geometry_wkb: bytes,
    geometry_type: str,
) -> None:
    tables = _tables(source_map_available=True, map_count=1)
    tables["map"] = _mutate(
        tables["map"],
        geometry_wkb=geometry_wkb,
        geometry_type=geometry_type,
    )
    report = _validate(tables)
    assert "invalid_geometry" in _reason_values(report)
    assert report.included_map_element_ids == ()


def test_one_valid_map_survives_invalid_peer_and_satisfies_requirement() -> None:
    tables = _tables(source_map_available=True, map_count=2)
    tables["map"] = _mutate(tables["map"], 0, geometry_wkb=b"bad")
    report = _validate(
        tables,
        config=CanonicalValidationConfig(require_source_map=True),
    )
    assert report.included_scenario_count == 1
    assert report.included_map_element_count == 1
    assert _reason_values(report) == ("invalid_geometry",)


def test_invalid_only_required_map_adds_no_usable_map() -> None:
    tables = _tables(source_map_available=True, map_count=1)
    tables["map"] = _mutate(tables["map"], geometry_wkb=b"bad")
    report = _validate(
        tables,
        config=CanonicalValidationConfig(require_source_map=True),
    )
    assert set(_reason_values(report)) == {
        "no_usable_map",
        "invalid_geometry",
    }


def test_map_structural_references_semantics_enums_and_source_flag() -> None:
    tables = _tables(source_map_available=True, map_count=1)
    unresolved = dict(tables)
    unresolved["map"] = _mutate(
        tables["map"],
        successor_ids=["map:missing:centerline"],
    )
    with pytest.raises(SchemaError, match="does not resolve"):
        _validate(unresolved)
    semantics = dict(tables)
    semantics["map"] = _mutate(
        tables["map"],
        semantic_attributes_json="{",
    )
    with pytest.raises(SchemaError, match="semantic"):
        _validate(semantics)
    enum_value = dict(tables)
    enum_value["map"] = _mutate(
        tables["map"],
        element_type="not-an-element",
    )
    with pytest.raises(SchemaError, match="invalid value"):
        _validate(enum_value)
    false_flag = dict(tables)
    false_flag["scenario"] = _mutate(
        tables["scenario"],
        source_map_available=False,
    )
    with pytest.raises(SchemaError, match="source_map_available"):
        _validate(false_flag)


def test_lane_topology_and_boundary_parent_rules_are_structural() -> None:
    tables = _tables(source_map_available=True, map_count=2)
    rows = cast(list[dict[str, object]], tables["map"].to_pylist())
    rows[0]["element_type"] = "road_area"
    rows[0]["geometry_type"] = "linestring"
    rows[0]["successor_ids"] = [cast(str, rows[1]["map_element_id"])]
    tables["map"] = sort_canonical_table(
        pa.Table.from_pylist(rows, schema=tables["map"].schema),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    )
    with pytest.raises(SchemaError, match="non-centerline"):
        _validate(tables)

    boundary_tables = _tables(source_map_available=True)
    scenario_id = cast(
        str,
        boundary_tables["scenario"].column("scenario_id")[0].as_py(),
    )
    boundary = _map_record(
        scenario_id,
        "map:synthetic:boundary:one",
        element_type=MapElementType.LANE_BOUNDARY,
        directionality=Directionality.NOT_APPLICABLE,
    )
    boundary_tables["map"] = vector_map_elements_to_table((boundary,))
    with pytest.raises(SchemaError, match="parent"):
        _validate(boundary_tables)


def test_scenario_without_eligible_trajectory_gets_coverage_exclusion() -> None:
    report = _validate(
        _tables(),
        config=CanonicalValidationConfig(minimum_valid_sample_count=100),
    )
    assert "insufficient_samples" in _reason_values(report)
    assert "insufficient_trajectory_coverage" in _reason_values(report)
    assert report.included_scenario_ids == ()
    assert report.included_agent_ids == ()
    assert report.included_trajectory_ids == ()


def test_bounded_parquet_matches_in_memory_across_small_multipart_batches(
    tmp_path: Path,
) -> None:
    tables = _tables(source_map_available=True, map_count=3)
    config = CanonicalValidationConfig(
        minimum_valid_sample_count=1,
        minimum_valid_duration_ns=0,
        require_source_map=True,
    )
    expected = _validate(tables, config=config)
    paths = _write_tables(
        tmp_path,
        tables,
        split_samples=True,
        split_maps=True,
    )
    actual = validate_canonical_parquet_dataset(
        tmp_path,
        paths,
        config=config,
        batch_size=2,
    )
    assert actual == expected


def test_bounded_parquet_empty_optional_maps_and_invalid_batch_size(
    tmp_path: Path,
) -> None:
    tables = _tables()
    paths = _write_tables(tmp_path, tables)
    assert validate_canonical_parquet_dataset(
        tmp_path,
        paths,
        batch_size=1,
    ) == _validate(tables)
    for value in (0, -1, True):
        with pytest.raises(ValidationError):
            validate_canonical_parquet_dataset(
                tmp_path,
                paths,
                batch_size=cast(Any, value),
            )


def test_bounded_implementation_avoids_full_table_collection() -> None:
    source = Path(validation.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    bounded = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "validate_canonical_parquet_dataset"
    )
    attributes = {
        node.attr for node in ast.walk(bounded) if isinstance(node, ast.Attribute)
    }
    assert "to_pylist" not in attributes
    assert "collect" not in attributes
    assert "iter_canonical_parquet_batches" in source


def test_materialization_paths_bytes_verification_and_overwrite(
    tmp_path: Path,
) -> None:
    report = _validate(
        _tables(),
        config=CanonicalValidationConfig(minimum_valid_sample_count=12),
    )
    run = _run(tmp_path)
    artifacts = materialize_canonical_validation_report(run, report)
    prefix = run.path.relative_to(tmp_path)
    assert artifacts.validation_report.relative_path.relative_to(prefix) == Path(
        "artifacts/canonical_validation/validation_report.json"
    )
    assert artifacts.exclusions_jsonl.relative_path.relative_to(prefix) == Path(
        "artifacts/canonical_validation/exclusions.jsonl"
    )
    assert artifacts.summary_report.relative_path.relative_to(prefix) == Path(
        "artifacts/canonical_validation/validation_summary.md"
    )
    assert verify_canonical_validation_artifacts(tmp_path, artifacts) == report
    for artifact in (
        artifacts.validation_report,
        artifacts.exclusions_jsonl,
        artifacts.summary_report,
    ):
        path = tmp_path / artifact.relative_path
        assert path.stat().st_size == artifact.size_bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            artifact.content_checksum
        )
    assert tuple(run.manifests_path.iterdir()) == ()
    with pytest.raises(ArtifactError, match="exists"):
        materialize_canonical_validation_report(run, report)


def _rewritten_artifact(
    root: Path,
    artifact: WrittenArtifact,
    data: bytes,
) -> WrittenArtifact:
    path = root / artifact.relative_path
    path.write_bytes(data)
    return WrittenArtifact(
        relative_path=artifact.relative_path,
        size_bytes=len(data),
        content_checksum=hashlib.sha256(data).hexdigest(),
    )


@pytest.mark.parametrize("target", ("report", "jsonl", "markdown"))
def test_verification_rejects_internally_inconsistent_artifacts(
    tmp_path: Path,
    target: str,
) -> None:
    report = _validate(
        _tables(),
        config=CanonicalValidationConfig(minimum_valid_sample_count=12),
    )
    run = _run(tmp_path)
    artifacts = materialize_canonical_validation_report(run, report)
    if target == "report":
        changed = replace(
            artifacts,
            validation_report=_rewritten_artifact(
                tmp_path,
                artifacts.validation_report,
                b"{}\n",
            ),
        )
    elif target == "jsonl":
        changed = replace(
            artifacts,
            exclusions_jsonl=_rewritten_artifact(
                tmp_path,
                artifacts.exclusions_jsonl,
                b'{"altered":true}\n',
            ),
        )
    else:
        changed = replace(
            artifacts,
            summary_report=_rewritten_artifact(
                tmp_path,
                artifacts.summary_report,
                b"# Altered\n",
            ),
        )
    with pytest.raises(SchemaError):
        verify_canonical_validation_artifacts(tmp_path, changed)


def test_materialization_rejects_finalized_run(tmp_path: Path) -> None:
    run = _run(tmp_path)
    artifact_store.finalize_run_directory(run)
    with pytest.raises(ArtifactError, match="complete"):
        materialize_canonical_validation_report(run, _validate(_tables()))


def test_complete_synthetic_integration_uses_temporary_repository(
    tmp_path: Path,
) -> None:
    dataset = synthetic.build_synthetic_dataset()
    run = _run(tmp_path, "run:validation:synthetic")
    source = synthetic.materialize_synthetic_dataset(run, dataset)
    paths = CanonicalDatasetPaths(
        scenario_manifest=(source.scenario_manifest.written_artifact.relative_path,),
        coordinate_frame_metadata=(
            source.coordinate_frame_metadata.written_artifact.relative_path,
        ),
        agent_metadata=(source.agent_metadata.written_artifact.relative_path,),
        trajectory_samples=(source.trajectory_samples.written_artifact.relative_path,),
        vector_map_elements=(),
    )
    report, artifacts = validate_and_materialize_canonical_parquet_dataset(
        run,
        paths,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=1,
            minimum_valid_duration_ns=0,
        ),
        batch_size=3,
    )
    assert (
        report.source_scenario_count,
        report.source_agent_count,
        report.source_trajectory_count,
        report.source_sample_count,
    ) == (16, 27, 27, 293)
    assert report.is_eligible
    assert verify_canonical_validation_artifacts(tmp_path, artifacts) == report
    artifact_store.finalize_run_directory(run)
    assert artifact_store.list_partial_artifacts(run) == ()


def _motion_rows() -> list[dict[str, object]]:
    payload = cast(
        dict[str, object],
        json.loads(MOTION_FIXTURE.read_text(encoding="utf-8")),
    )
    fields_to_copy = (
        "scenario_id",
        "start_timestamp",
        "end_timestamp",
        "num_timestamps",
        "focal_track_id",
        "city",
    )
    return [
        {
            **row,
            **{field: payload[field] for field in fields_to_copy},
        }
        for row in cast(list[dict[str, object]], payload["rows"])
    ]


def test_av2_motion_map_integration_and_required_map(tmp_path: Path) -> None:
    motion_root = tmp_path / "motion-source"
    map_root = tmp_path / "map-source"
    motion_root.mkdir()
    map_root.mkdir()
    pq.write_table(
        pa.Table.from_pylist(_motion_rows()), motion_root / "scenario.parquet"
    )
    map_path = map_root / MAP_FIXTURE.name
    map_path.write_bytes(MAP_FIXTURE.read_bytes())
    motion = av2_motion.load_av2_motion_scenario(
        motion_root,
        "scenario.parquet",
        config=av2_motion.Av2MotionAdapterConfig(
            "1.1",
            "train",
            source_map_available=True,
        ),
    )
    map_conversion = av2_map.load_av2_vector_map(
        map_root,
        map_path.name,
        scenario=motion.scenario,
        coordinate_frame=motion.coordinate_frame,
    )
    run = _run(tmp_path, "run:validation:av2")
    motion_artifacts = av2_motion.materialize_av2_motion_scenario(run, motion)
    map_artifacts = av2_map.materialize_av2_vector_map(run, map_conversion)
    paths = CanonicalDatasetPaths(
        scenario_manifest=(
            motion_artifacts.scenario_manifest.written_artifact.relative_path,
        ),
        coordinate_frame_metadata=(
            motion_artifacts.coordinate_frame_metadata.written_artifact.relative_path,
        ),
        agent_metadata=(
            motion_artifacts.agent_metadata.written_artifact.relative_path,
        ),
        trajectory_samples=(
            motion_artifacts.trajectory_samples.written_artifact.relative_path,
        ),
        vector_map_elements=(
            map_artifacts.vector_map_elements.written_artifact.relative_path,
        ),
    )
    report, artifacts = validate_and_materialize_canonical_parquet_dataset(
        run,
        paths,
        config=CanonicalValidationConfig(
            minimum_valid_sample_count=1,
            minimum_valid_duration_ns=0,
            require_source_map=True,
        ),
        batch_size=2,
    )
    assert report.is_eligible
    assert report.source_map_element_count == 11
    assert report.included_map_element_count == 11
    assert verify_canonical_validation_artifacts(tmp_path, artifacts) == report
    artifact_store.finalize_run_directory(run)
    assert artifact_store.list_partial_artifacts(run) == ()


def test_corruption_integration_exclusion_then_structural_failure_writes_nothing(
    tmp_path: Path,
) -> None:
    tables = _tables()
    paths = _write_tables(tmp_path, tables)
    run = _run(tmp_path, "run:validation:corruption")
    report = validate_canonical_parquet_dataset(
        tmp_path,
        paths,
        config=CanonicalValidationConfig(minimum_valid_sample_count=100),
        batch_size=2,
    )
    assert "insufficient_samples" in _reason_values(report)

    corrupt_rows = cast(list[dict[str, object]], tables["sample"].to_pylist())
    for row in corrupt_rows:
        row["agent_id"] = "agent:missing:value"
    corrupt = sort_canonical_table(
        pa.Table.from_pylist(corrupt_rows, schema=tables["sample"].schema),
        CanonicalSchemaName.TRAJECTORY_SAMPLES,
    )
    pq.write_table(corrupt, tmp_path / paths.trajectory_samples[0])
    with pytest.raises(SchemaError, match="missing agent"):
        validate_and_materialize_canonical_parquet_dataset(
            run,
            paths,
            batch_size=2,
        )
    assert not (run.path / "artifacts" / "canonical_validation").exists()


def test_import_surface_has_no_io_or_forbidden_dependencies() -> None:
    source = Path(validation.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots.isdisjoint(
        {
            "argoverse",
            "av2",
            "pandas",
            "scipy",
            "sklearn",
            "networkx",
            "torch",
            "rerun",
            "subprocess",
            "requests",
            "urllib",
        }
    )
    assert not any(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        for node in tree.body
    )
