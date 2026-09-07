"""Focused tests for Batch 5.1 motion-only feasibility contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import LineString  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_sha256
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    MapGeometryType,
    VectorMapElementRecord,
    geometry_to_canonical_wkb,
)
from kinematicweave.domain.records import OriginType
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.layout_feasibility import (
    ALLOWED_MOTION_FILENAMES,
    MotionOnlyScenarioInput,
    _write_json,
    aggregation_analysis,
    layout_feasibility_configuration_identity,
    sha256_file,
    synthetic_feasibility_results,
    verify_stage_a_bundle,
)
from kinematicweave.experiments.layout_map_diagnostics import (
    MapScenarioInput,
    run_stage_b,
)


def _stage_a_fixture(root: Path, scenario_count: int = 1) -> Path:
    stage_a = root / "stage_a"
    stage_a.mkdir(parents=True)
    scenarios = [
        {
            "safe_scenario_id": f"{index + 1:016x}",
            "selection_rank": index + 1,
            "coordinate_frame": {
                "coordinate_frame_id": f"{index + 11:016x}",
                "parent_frame_present": False,
                "frame_type": "scenario_local",
                "origin_x_m": 0.0,
                "origin_y_m": 0.0,
                "origin_z_m": None,
                "axis_convention": "x_forward_y_left_z_up",
                "distance_unit": "m",
                "angle_unit": "rad",
                "timestamp_unit": "ns",
                "source_crs": None,
                "has_elevation": False,
                "transform_to_parent_present": False,
                "origin_type": "source",
            },
            "counts": {},
            "measurements": {},
            "existing_shared_template_comparison": {},
            "paths": [
                {
                    "safe_track_id": f"{index + 101:016x}",
                    "resampled_xy": [[0.0, 0.0], [10.0, 0.0]],
                    "path_length_m": 10.0,
                    "duration_s": 2.0,
                    "event_types": [],
                }
            ],
        }
        for index in range(scenario_count)
    ]
    bundle = {
        "schema_version": "1.0",
        "batch": "5.1",
        "stage": "A",
        "completed": True,
        "cohort_role": "development",
        "cohort_identity": "a" * 64,
        "cohort_manifest_sha256": "b" * 64,
        "scenario_count": scenario_count,
        "configuration_identity": (layout_feasibility_configuration_identity()),
        "access_audit": {
            "opened_file_count": scenario_count * 4,
            "opened_files": [],
            "opened_file_classes": list(ALLOWED_MOTION_FILENAMES),
            "map_file_open_count": 0,
            "map_paths_received": False,
            "pilot_scenario_access_count": 0,
            "test_scenario_access_count": 0,
        },
        "aggregate": {},
        "scenarios": scenarios,
        "all_zero_count_and_unfavorable_findings_retained": True,
        "production_layout_artifacts_created": False,
    }
    bundle_path = stage_a / "motion_feasibility_bundle.json"
    _write_json(bundle_path, bundle)
    bundle_hash = sha256_file(bundle_path)
    identity = canonical_sha256(
        "phase5-layout-feasibility-stage-a",
        {
            "bundle_sha256": bundle_hash,
            "cohort_identity": "a" * 64,
            "configuration_identity": (layout_feasibility_configuration_identity()),
            "scenario_count": scenario_count,
        },
    )
    _write_json(
        stage_a / "stage_a_manifest.json",
        {
            "schema_version": "1.0",
            "batch": "5.1",
            "stage": "A",
            "completed": True,
            "immutable": True,
            "stage_a_identity": identity,
            "configuration_identity": (layout_feasibility_configuration_identity()),
            "cohort_identity": "a" * 64,
            "scenario_count": scenario_count,
            "bundle_file": bundle_path.name,
            "bundle_sha256": bundle_hash,
            "allowed_file_set": [
                "motion_feasibility_bundle.json",
                "stage_a_manifest.json",
            ],
            "map_access_before_completion": False,
            "pilot_test_access_count": 0,
        },
    )
    return stage_a


def _lane_record() -> VectorMapElementRecord:
    return VectorMapElementRecord(
        scenario_id="scenario:synthetic",
        map_element_id="map-element:lane",
        element_type=MapElementType.LANE_CENTERLINE,
        geometry_type=MapGeometryType.LINESTRING,
        geometry_wkb=geometry_to_canonical_wkb(LineString([(0.0, 0.0), (10.0, 0.0)])),
        directionality=Directionality.DIRECTED,
        parent_element_id=None,
        successor_ids=(),
        predecessor_ids=(),
        left_neighbor_id=None,
        right_neighbor_id=None,
        semantic_attributes_json=None,
        origin_type=OriginType.SYNTHETIC,
        quality_flags=(),
    )


def test_synthetic_statistics_cover_required_edge_cases() -> None:
    result = synthetic_feasibility_results()
    assert result["all_checks_passed"] is True
    measurements = result["measurements"]
    assert measurements["repeated_directional"]["directional_agreement_pair_count"] == 1
    assert measurements["bidirectional"]["opposing_direction_pair_count"] == 1
    assert (
        measurements["disconnected_crossing"]["ambiguous_geometric_crossing_count"] == 1
    )
    assert (
        measurements["elevation_separated_crossing"][
            "elevation_separated_crossing_count"
        ]
        == 1
    )
    assert measurements["single_pass_sparse"]["multi_track_supported_track_count"] == 0


def test_stage_a_input_accepts_only_development_motion_files(
    tmp_path: Path,
) -> None:
    files = tuple(tmp_path / name for name in ALLOWED_MOTION_FILENAMES)
    value = MotionOnlyScenarioInput(
        safe_scenario_id="1" * 16,
        cohort_role="development",
        selection_rank=1,
        files=files,
        sha256_by_filename=tuple((path.name, "a" * 64) for path in files),
    )
    assert tuple(path.name for path in value.files) == ALLOWED_MOTION_FILENAMES
    with pytest.raises(ValidationError, match="development"):
        MotionOnlyScenarioInput(
            safe_scenario_id="1" * 16,
            cohort_role="pilot",
            selection_rank=1,
            files=files,
            sha256_by_filename=tuple((path.name, "a" * 64) for path in files),
        )
    map_files = (*files[:-1], tmp_path / "vector_map_elements.parquet")
    with pytest.raises(ValidationError, match="allowlisted"):
        MotionOnlyScenarioInput(
            safe_scenario_id="1" * 16,
            cohort_role="development",
            selection_rank=1,
            files=map_files,
            sha256_by_filename=tuple((path.name, "a" * 64) for path in map_files),
        )


def test_stage_a_verifier_rejects_incomplete_and_changed_bundles(
    tmp_path: Path,
) -> None:
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    with pytest.raises(ArtifactError, match="incomplete"):
        verify_stage_a_bundle(incomplete, expected_scenario_count=1)
    stage_a = _stage_a_fixture(tmp_path / "complete")
    verify_stage_a_bundle(stage_a, expected_scenario_count=1)
    with (stage_a / "motion_feasibility_bundle.json").open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write("\n")
    with pytest.raises(ArtifactError, match="checksum"):
        verify_stage_a_bundle(stage_a, expected_scenario_count=1)


def test_stage_b_rejects_output_inside_stage_a(tmp_path: Path) -> None:
    stage_a = _stage_a_fixture(tmp_path)
    with pytest.raises(ValidationError, match="inside"):
        run_stage_b(
            tmp_path,
            stage_a,
            (),
            stage_a / "diagnostics",
            expected_scenario_count=1,
        )


def test_stage_b_rejects_incomplete_stage_a_before_map_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    def fail_if_opened(*_args: object, **_kwargs: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("map input opened before Stage A verification")

    monkeypatch.setattr(
        "kinematicweave.experiments.layout_map_diagnostics._map_rows",
        fail_if_opened,
    )
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    with pytest.raises(ArtifactError, match="incomplete"):
        run_stage_b(
            tmp_path,
            incomplete,
            (),
            tmp_path / "stage_b",
            expected_scenario_count=1,
        )
    assert opened is False


def test_repeated_stage_b_preserves_stage_a_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_a = _stage_a_fixture(tmp_path)
    map_path = tmp_path / "vector_map_elements.parquet"
    map_path.write_bytes(b"synthetic map input")
    map_input = MapScenarioInput(
        safe_scenario_id="0000000000000001",
        cohort_role="development",
        selection_rank=1,
        vector_map_path=map_path,
        vector_map_sha256=sha256_file(map_path),
    )
    monkeypatch.setattr(
        "kinematicweave.experiments.layout_map_diagnostics._map_rows",
        lambda *_args, **_kwargs: (_lane_record(),),
    )
    first = run_stage_b(
        tmp_path,
        stage_a,
        (map_input,),
        tmp_path / "stage_b",
        expected_scenario_count=1,
    )
    second = run_stage_b(
        tmp_path,
        stage_a,
        (map_input,),
        tmp_path / "stage_b",
        expected_scenario_count=1,
    )
    assert first == second
    assert first["stage_a_snapshot_before"] == first["stage_a_snapshot_after"]


def test_aggregation_requires_proven_coordinate_compatibility(
    tmp_path: Path,
) -> None:
    stage_a_root = _stage_a_fixture(tmp_path)
    stage_a = verify_stage_a_bundle(stage_a_root, expected_scenario_count=1)
    result = aggregation_analysis(stage_a)
    assert result["coordinates_comparable_across_scenarios"] is False
    assert result["deterministic_grouping_without_maps"] == "scenario-local only"
    assert result["grouping_crosses_development_pilot_test_boundaries"] is False
    assert "defer" in result["recommendation"]
