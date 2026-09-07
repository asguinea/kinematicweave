"""Focused contracts for shared motion categories and route templates."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest
from shapely.geometry import LineString  # type: ignore[import-untyped]

from kinematicweave.codecs.velocity_bounded import encode_scenario_velocity_bounded
from kinematicweave.data.parquet_io import (
    motion_categories_to_table,
    route_template_memberships_to_table,
    route_templates_to_table,
)
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    canonical_schema_names,
    get_arrow_schema,
    get_polars_schema,
    get_schema_definition,
    schema_fingerprint,
)
from kinematicweave.data.shared_motion_artifacts import (
    materialize_shared_motion_model,
    verify_shared_motion_artifacts,
)
from kinematicweave.data.synthetic import SyntheticDataset, build_synthetic_dataset
from kinematicweave.domain.map_records import (
    Directionality,
    MapElementType,
    MapGeometryType,
    VectorMapElementRecord,
    geometry_to_canonical_wkb,
)
from kinematicweave.domain.records import (
    AgentClass,
    OriginType,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.domain.semantic import MotionEventType, SemanticMotionTape
from kinematicweave.domain.shared_motion import MotionCategoryLabel
from kinematicweave.events.semantic_motion import build_semantic_motion_tape
from kinematicweave.layout.shared_motion import (
    RouteCandidate,
    SharedMotionConfig,
    build_route_candidates,
    build_shared_motion_model,
    classify_motion_category,
    classify_semantic_motion_track,
    cluster_scenario,
    compute_route_compatibility,
    evaluate_shared_motion_map,
    resample_arc_length,
    shared_motion_configuration_identity,
)

_PREVIOUS_FINGERPRINTS = {
    "scenario_manifest": "e59b221b1bf720832d90a19340f773c31b461292323d6ddb0a12fb8096d7d03b",
    "coordinate_frame_metadata": "ab8668ac6775de47623281bbe178e88202c0715cbb964057df7ece53c4b2f2ac",
    "agent_metadata": "7527dd3653e46f82ac835c81150c57677cd23c3a4eba2a705bd6a2dde3c0ab2b",
    "trajectory_samples": "24433aa9c49be2fc95be4fd6f8a30ad163cf1a2a6e116d42b7960be2a2714cfd",
    "vector_map_elements": "5f837f27a693002d9c43b9e9101d999a61f0ab53aa4a402c9bc1ce0d79ed0998",
    "procedural_tape_manifest": "7f556d450ed8cbf198e4e9c8be04bb1429cb6a2c42649aa0c9f0a07fa76c7b2c",
    "procedural_tracks": "92964188ec2bbf6259a961113dbe31cb524cd2cfddb521f852d5665e6fceb96a",
    "procedural_segments": "e994f5d7c2f2cfc21ddbb06b44c076d04e94491fe2f201bc4b2a76bfa089cd86",
    "semantic_waypoints": "9ceaee7baf378e57898e7e241d74be38b2eb847391936ff578afab15c51d4b1d",
    "motion_events": "a20c7150fb9ba9f375bb82af6af0623b07a8dfc45495f6a151ec6a01ad700d42",
}


def _candidate(
    name: str,
    points: tuple[tuple[float, float], ...],
    *,
    category: MotionCategoryLabel = MotionCategoryLabel.STRAIGHT,
) -> RouteCandidate:
    return RouteCandidate(
        scenario_id="scenario:focused",
        coordinate_frame_id="frame:focused",
        procedural_track_id=f"procedural-track:{name}",
        agent_id=f"agent:{name}",
        trajectory_id=f"trajectory:{name}",
        agent_class=AgentClass.VEHICLE,
        category_label=category,
        event_signature_json="{}",
        run_index=0,
        source_points=points,
        resampled_xy=resample_arc_length(points, 32),
        path_length_m=LineString(points).length,
        duration_ns=1_000_000_000,
    )


def _synthetic_inputs() -> tuple[
    SyntheticDataset,
    tuple[ScenarioRecord, ...],
    tuple[Trajectory, ...],
    tuple[SemanticMotionTape, ...],
]:
    dataset = build_synthetic_dataset()
    tapes = []
    for item in dataset.scenarios:
        procedural = encode_scenario_velocity_bounded(
            item.scenario,
            item.coordinate_frame,
            item.agents,
            item.trajectories,
            source_validation_report_identity="synthetic-validation:v1",
        )
        tapes.append(build_semantic_motion_tape(procedural, item.trajectories))
    scenarios = tuple(item.scenario for item in dataset.scenarios)
    trajectories = tuple(
        trajectory for item in dataset.scenarios for trajectory in item.trajectories
    )
    return dataset, scenarios, trajectories, tuple(tapes)


def test_schema_additions_are_exact_and_previous_fingerprints_are_unchanged() -> None:
    assert tuple(item.value for item in canonical_schema_names())[-3:] == (
        "motion_categories",
        "route_templates",
        "route_template_memberships",
    )
    for name, expected in _PREVIOUS_FINGERPRINTS.items():
        assert schema_fingerprint(name) == expected
    expected_fields = {
        CanonicalSchemaName.MOTION_CATEGORIES: (
            "category_id",
            "category_label",
            "agent_class",
            "event_signature_json",
            "representative_template_id",
            "template_count",
            "track_count",
            "origin_type",
            "quality_flags",
        ),
        CanonicalSchemaName.ROUTE_TEMPLATES: (
            "template_id",
            "category_id",
            "scenario_id",
            "coordinate_frame_id",
            "representative_track_id",
            "member_count",
            "geometry_type",
            "geometry_wkb",
            "path_length_m",
            "duration_ns",
            "event_signature_json",
            "semantic_attributes_json",
            "origin_type",
            "quality_flags",
        ),
        CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS: (
            "template_id",
            "procedural_track_id",
            "membership_index",
            "scenario_id",
            "agent_id",
            "trajectory_id",
            "mean_path_error_m",
            "maximum_path_error_m",
            "start_distance_m",
            "end_distance_m",
            "path_length_ratio",
            "duration_ratio",
            "map_route_signature_json",
            "origin_type",
            "quality_flags",
        ),
    }
    for name, fields in expected_fields.items():
        assert tuple(get_arrow_schema(name).names) == fields
        assert tuple(get_polars_schema(name)) == fields
        assert get_schema_definition(name).version == "1.0"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resample_point_count", True),
        ("maximum_path_error_m", float("nan")),
        ("minimum_path_length_ratio", 0.0),
        ("minimum_shared_template_members", 1),
    ],
)
def test_configuration_is_fixed_frozen_and_validated(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        SharedMotionConfig(**{field: value})  # type: ignore[arg-type]
    first = SharedMotionConfig()
    assert first == SharedMotionConfig()
    assert shared_motion_configuration_identity(first) == (
        shared_motion_configuration_identity(SharedMotionConfig())
    )
    with pytest.raises(FrozenInstanceError):
        first.resample_point_count = 12  # type: ignore[misc]


def test_compatibility_boundaries_direction_and_complete_link_are_exact() -> None:
    a = _candidate("a", ((0.0, 0.0), (10.0, 0.0)))
    b = _candidate("b", ((0.0, 1.0), (10.0, 1.0)))
    c = _candidate("c", ((0.0, 2.0), (10.0, 2.0)))
    reverse = _candidate("reverse", ((10.0, 0.0), (0.0, 0.0)))
    assert compute_route_compatibility(a, b).compatible
    assert not compute_route_compatibility(a, reverse).compatible
    config = SharedMotionConfig(maximum_mean_path_error_m=1.5)
    assert cluster_scenario((c, a, b), config) == ((a, b), (c,))
    assert cluster_scenario((c, a, b), config) == cluster_scenario((c, a, b), config)


@pytest.mark.parametrize(
    ("events", "length", "expected"),
    [
        ((MotionEventType.GAP,), 0.0, MotionCategoryLabel.GAP_AFFECTED),
        ((), 0.5, MotionCategoryLabel.STATIONARY),
        ((MotionEventType.STOP,), 1.0, MotionCategoryLabel.STOP_AND_GO),
        (
            (MotionEventType.LEFT_TURN, MotionEventType.RIGHT_TURN),
            1.0,
            MotionCategoryLabel.MIXED,
        ),
        ((MotionEventType.LEFT_TURN,), 1.0, MotionCategoryLabel.LEFT_TURN),
        ((MotionEventType.RIGHT_TURN,), 1.0, MotionCategoryLabel.RIGHT_TURN),
        (
            (MotionEventType.ACCELERATION, MotionEventType.BRAKING),
            1.0,
            MotionCategoryLabel.MIXED,
        ),
        (
            (MotionEventType.ACCELERATION,),
            1.0,
            MotionCategoryLabel.ACCELERATING,
        ),
        ((MotionEventType.BRAKING,), 1.0, MotionCategoryLabel.BRAKING),
        ((), 1.0, MotionCategoryLabel.STRAIGHT),
    ],
)
def test_every_category_priority_branch(
    events: tuple[MotionEventType, ...],
    length: float,
    expected: MotionCategoryLabel,
) -> None:
    assert classify_motion_category(events, length) is expected


def test_complete_synthetic_set_is_categorized_and_deterministic() -> None:
    _dataset, scenarios, trajectories, tapes = _synthetic_inputs()
    model = build_shared_motion_model(
        "synthetic_kinematicweave", "1.0", scenarios, trajectories, tapes
    )
    repeated = build_shared_motion_model(
        "synthetic_kinematicweave", "1.0", scenarios, trajectories, tapes
    )
    assert model == repeated
    assert len(trajectories) == 27
    categorized = sum(item.track_count for item in model.categories) + sum(
        len(json.loads(item)["zero_length_track_ids"])
        for item in model.category_summary_metadata
        if json.loads(item)["category_id"]
        not in {category.category_id for category in model.categories}
    )
    assert categorized == 27
    assert len(model.memberships) == sum(
        track.procedural_track.run_count for tape in tapes for track in tape.tracks
    )
    assert all(
        json.loads(item.semantic_attributes_json)["map_data_used_for_construction"]
        is False
        for item in model.route_templates
    )
    category_by_kind = {
        item.kind: classify_semantic_motion_track(
            tapes[index].tracks[0], item.trajectories[0]
        )
        for index, item in enumerate(_dataset.scenarios)
        if len(item.trajectories) == 1
    }
    assert MotionCategoryLabel.LEFT_TURN in category_by_kind.values()
    assert MotionCategoryLabel.RIGHT_TURN in category_by_kind.values()
    assert MotionCategoryLabel.GAP_AFFECTED in category_by_kind.values()


def test_tables_artifacts_and_map_evaluation_round_trip(tmp_path: Path) -> None:
    _dataset, scenarios, trajectories, tapes = _synthetic_inputs()
    model = build_shared_motion_model(
        "synthetic_kinematicweave", "1.0", scenarios, trajectories, tapes
    )
    assert motion_categories_to_table(model.categories).num_rows == len(
        model.categories
    )
    assert route_templates_to_table(model.route_templates).num_rows == len(
        model.route_templates
    )
    assert route_template_memberships_to_table(model.memberships).num_rows == len(
        model.memberships
    )
    candidates = tuple(
        candidate
        for tape in tapes
        for track in tape.tracks
        for candidate in build_route_candidates(
            next(
                scenario
                for scenario in scenarios
                if scenario.scenario_id == track.procedural_track.scenario_id
            ),
            next(
                trajectory
                for trajectory in trajectories
                if trajectory.trajectory_id == track.procedural_track.trajectory_id
            ),
            track,
        )
    )
    first = candidates[0]
    lane = VectorMapElementRecord(
        scenario_id=first.scenario_id,
        map_element_id="map-element:focused-lane",
        element_type=MapElementType.LANE_CENTERLINE,
        geometry_type=MapGeometryType.LINESTRING,
        geometry_wkb=geometry_to_canonical_wkb(LineString(first.source_points)),
        directionality=Directionality.DIRECTED,
        parent_element_id=None,
        successor_ids=(),
        predecessor_ids=(),
        left_neighbor_id=None,
        right_neighbor_id=None,
        semantic_attributes_json=None,
        origin_type=OriginType.SOURCE_GROUND_TRUTH,
        quality_flags=(),
    )
    evaluated = evaluate_shared_motion_map(model, candidates, (lane,), scenarios)
    assert evaluated.evaluated_model.categories == model.categories
    assert evaluated.evaluated_model.route_templates == model.route_templates
    assert tuple(
        (item.template_id, item.procedural_track_id, item.membership_index)
        for item in evaluated.evaluated_model.memberships
    ) == tuple(
        (item.template_id, item.procedural_track_id, item.membership_index)
        for item in model.memberships
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    artifacts = materialize_shared_motion_model(
        repository,
        "results/generated",
        "shared-motion:test",
        model,
        map_evaluation=evaluated,
    )
    assert (
        verify_shared_motion_artifacts(
            repository,
            artifacts,
            expected_model=evaluated.evaluated_model,
            expected_map_evaluation=evaluated,
        )
        == evaluated.evaluated_model
    )
    artifact_names = {
        path.name
        for path in artifacts.run_directory.path.iterdir()
        if path.is_file() and not path.name.startswith(".run.")
    }
    assert artifact_names == {
        "motion_categories.parquet",
        "route_templates.parquet",
        "route_template_memberships.parquet",
        "shared_motion_summary.json",
    }
