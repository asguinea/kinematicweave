"""Post-freeze development-map diagnostics for Batch 5.1 Stage B."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
import math
from pathlib import Path
from typing import cast

from shapely.affinity import translate  # type: ignore[import-untyped]
from shapely.geometry import LineString, Point  # type: ignore[import-untyped]
from shapely.strtree import STRtree  # type: ignore[import-untyped]

from kinematicweave.data.parquet_io import iter_canonical_parquet_batches
from kinematicweave.data.schemas import CanonicalSchemaName
from kinematicweave.domain.map_records import (
    MapElementType,
    VectorMapElementRecord,
    geometry_from_canonical_wkb,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.layout_feasibility import (
    BATCH,
    DEFAULT_CONFIG,
    DEVELOPMENT_SCENARIO_COUNT,
    Json,
    LayoutFeasibilityConfig,
    _read_json,
    _write_json,
    directory_snapshot,
    distribution,
    sha256_file,
    verify_stage_a_bundle,
)


@dataclass(frozen=True, slots=True)
class MapScenarioInput:
    """One exact, development-only canonical map input for Stage B."""

    safe_scenario_id: str
    cohort_role: str
    selection_rank: int
    vector_map_path: Path
    vector_map_sha256: str

    def __post_init__(self) -> None:
        if self.cohort_role != "development":
            raise ValidationError("Stage B accepts development maps only")
        if len(self.safe_scenario_id) != 16:
            raise ValidationError("safe_scenario_id must contain 16 hex characters")
        if self.selection_rank < 1:
            raise ValidationError("selection_rank must be positive")
        if self.vector_map_path.name != "vector_map_elements.parquet":
            raise ValidationError("Stage B requires canonical vector-map elements")
        if len(self.vector_map_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.vector_map_sha256
        ):
            raise ValidationError("vector_map_sha256 must be SHA-256")


def _map_rows(
    repository_root: Path,
    path: Path,
) -> tuple[VectorMapElementRecord, ...]:
    try:
        relative = path.resolve().relative_to(repository_root.resolve())
    except ValueError:
        raise ArtifactError("canonical map path is outside repository root") from None
    return tuple(
        VectorMapElementRecord(**row)
        for batch in iter_canonical_parquet_batches(
            repository_root,
            (relative,),
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        )
        for row in batch.to_pylist()
    )


def _angle_difference(left: float, right: float) -> float:
    return abs(math.atan2(math.sin(left - right), math.cos(left - right)))


def _line_heading(line: LineString, point: Point) -> float | None:
    distance = line.project(point)
    span = max(min(line.length * 0.01, 1.0), 0.05)
    before = line.interpolate(max(0.0, distance - span))
    after = line.interpolate(min(line.length, distance + span))
    if before.equals(after):
        return None
    return math.atan2(after.y - before.y, after.x - before.x)


def _sample_line(line: LineString, interval: float) -> tuple[Point, ...]:
    if line.length <= 0.0:
        return ()
    count = max(2, math.ceil(line.length / interval) + 1)
    return tuple(
        line.interpolate(line.length * index / (count - 1)) for index in range(count)
    )


def _nearest_index(tree: STRtree, point: Point) -> int | None:
    if len(tree.geometries) == 0:
        return None
    return int(tree.nearest(point))


def _lane_sequence(
    points: Sequence[Sequence[float]],
    tree: STRtree,
    lane_ids: Sequence[str],
    maximum_distance: float,
) -> tuple[str, ...]:
    sequence: list[str] = []
    for raw in points:
        point = Point(float(raw[0]), float(raw[1]))
        index = _nearest_index(tree, point)
        if index is None:
            continue
        if point.distance(tree.geometries[index]) > maximum_distance:
            continue
        lane_id = lane_ids[index]
        if not sequence or sequence[-1] != lane_id:
            sequence.append(lane_id)
    return tuple(sequence)


def _scenario_diagnostics(
    scenario: Json,
    elements: Sequence[VectorMapElementRecord],
    config: LayoutFeasibilityConfig,
) -> Json:
    frame = cast(Json, scenario["coordinate_frame"])
    origin_x = float(frame["origin_x_m"])
    origin_y = float(frame["origin_y_m"])
    origin_z = float(frame["origin_z_m"]) if frame["origin_z_m"] is not None else 0.0
    lanes: list[LineString] = []
    lane_ids: list[str] = []
    lane_records: dict[str, VectorMapElementRecord] = {}
    for element in elements:
        if element.element_type is not MapElementType.LANE_CENTERLINE:
            continue
        geometry = translate(
            geometry_from_canonical_wkb(element.geometry_wkb),
            xoff=origin_x,
            yoff=origin_y,
            zoff=origin_z,
        )
        if isinstance(geometry, LineString) and geometry.length > 0.0:
            lanes.append(geometry)
            lane_ids.append(element.map_element_id)
            lane_records[element.map_element_id] = element
    lane_tree = STRtree(lanes)
    motion_lines: list[LineString] = []
    motion_distances: list[float] = []
    orientation_differences: list[float] = []
    point_count = 0
    point_within_support = 0
    transition_count = 0
    consistent_transition_count = 0
    turning_track_count = 0
    turning_track_with_lane_sequence = 0
    paths = cast(list[Json], scenario["paths"])
    for path in paths:
        points = cast(list[list[float]], path["resampled_xy"])
        line = LineString([(float(item[0]), float(item[1])) for item in points])
        if line.length <= 0.0:
            continue
        motion_lines.append(line)
        sequence = _lane_sequence(
            points,
            lane_tree,
            lane_ids,
            maximum_distance=5.0,
        )
        event_types = cast(list[str], path["event_types"])
        if any(item in {"left_turn", "right_turn"} for item in event_types):
            turning_track_count += 1
            if sequence:
                turning_track_with_lane_sequence += 1
        for left_id, right_id in pairwise(sequence):
            transition_count += 1
            left_record = lane_records[left_id]
            right_record = lane_records[right_id]
            if (
                right_id in left_record.successor_ids
                or left_id in right_record.predecessor_ids
            ):
                consistent_transition_count += 1
        for left, right in pairwise(points):
            left_point = Point(float(left[0]), float(left[1]))
            right_point = Point(float(right[0]), float(right[1]))
            midpoint = Point(
                (left_point.x + right_point.x) / 2.0,
                (left_point.y + right_point.y) / 2.0,
            )
            index = _nearest_index(lane_tree, midpoint)
            if index is None:
                continue
            distance = float(midpoint.distance(lanes[index]))
            motion_distances.append(distance)
            point_count += 1
            point_within_support += distance <= config.map_support_distance_m
            motion_heading = math.atan2(
                right_point.y - left_point.y,
                right_point.x - left_point.x,
            )
            lane_heading = _line_heading(lanes[index], midpoint)
            if lane_heading is not None:
                orientation_differences.append(
                    _angle_difference(motion_heading, lane_heading)
                )

    motion_tree = STRtree(motion_lines)
    map_sample_count = 0
    supported_map_sample_count = 0
    for lane in lanes:
        for point in _sample_line(lane, config.map_sampling_interval_m):
            map_sample_count += 1
            index = _nearest_index(motion_tree, point)
            if index is not None:
                supported_map_sample_count += (
                    point.distance(motion_lines[index]) <= config.map_support_distance_m
                )
    return {
        "safe_scenario_id": scenario["safe_scenario_id"],
        "lane_centerline_count": len(lanes),
        "motion_path_count": len(motion_lines),
        "motion_to_map_distance_m": distribution(motion_distances),
        "motion_point_within_support_count": point_within_support,
        "motion_point_count": point_count,
        "motion_point_within_support_fraction": (
            point_within_support / point_count if point_count else 0.0
        ),
        "orientation_difference_rad": distribution(orientation_differences),
        "orientation_agreement_count": sum(
            value <= config.map_orientation_maximum_rad
            for value in orientation_differences
        ),
        "orientation_comparison_count": len(orientation_differences),
        "orientation_agreement_fraction": (
            sum(
                value <= config.map_orientation_maximum_rad
                for value in orientation_differences
            )
            / len(orientation_differences)
            if orientation_differences
            else 0.0
        ),
        "mapped_structure_sample_count": map_sample_count,
        "mapped_structure_supported_sample_count": supported_map_sample_count,
        "mapped_structure_supported_fraction": (
            supported_map_sample_count / map_sample_count if map_sample_count else 0.0
        ),
        "observed_lane_transition_count": transition_count,
        "map_consistent_transition_count": consistent_transition_count,
        "map_consistent_transition_fraction": (
            consistent_transition_count / transition_count if transition_count else 0.0
        ),
        "turning_track_count": turning_track_count,
        "turning_track_with_lane_sequence_count": (turning_track_with_lane_sequence),
    }


def run_stage_b(
    repository_root: Path,
    stage_a_root: Path,
    map_inputs: Sequence[MapScenarioInput],
    stage_b_root: Path,
    *,
    config: LayoutFeasibilityConfig = DEFAULT_CONFIG,
    expected_scenario_count: int = DEVELOPMENT_SCENARIO_COUNT,
) -> Json:
    """Verify frozen Stage A, then execute separate map diagnostics."""
    stage_a = verify_stage_a_bundle(
        stage_a_root,
        expected_scenario_count=expected_scenario_count,
    )
    before = cast(dict[str, str], stage_a["snapshot"])
    stage_a_resolved = stage_a_root.resolve()
    stage_b_resolved = stage_b_root.resolve()
    if (
        stage_b_resolved == stage_a_resolved
        or stage_a_resolved in stage_b_resolved.parents
    ):
        raise ValidationError("Stage B cannot write inside the Stage A root")
    ordered = tuple(sorted(map_inputs, key=lambda item: item.selection_rank))
    if len(ordered) != expected_scenario_count:
        raise ValidationError("Stage B map scenario count differs")
    if len({item.safe_scenario_id for item in ordered}) != len(ordered):
        raise ValidationError("Stage B safe scenario identifiers are not unique")
    bundle = cast(Json, stage_a["bundle"])
    scenarios = cast(list[Json], bundle["scenarios"])
    scenario_by_safe_id = {str(item["safe_scenario_id"]): item for item in scenarios}
    if set(scenario_by_safe_id) != {item.safe_scenario_id for item in ordered}:
        raise ArtifactError("Stage B map inputs do not match Stage A scenarios")

    diagnostics: list[Json] = []
    map_hashes: dict[str, str] = {}
    for item in ordered:
        actual_hash = sha256_file(item.vector_map_path)
        if actual_hash != item.vector_map_sha256:
            raise ArtifactError(
                f"Stage B map checksum differs: {item.safe_scenario_id}"
            )
        map_hashes[item.safe_scenario_id] = actual_hash
        elements = _map_rows(repository_root, item.vector_map_path)
        diagnostics.append(
            _scenario_diagnostics(
                scenario_by_safe_id[item.safe_scenario_id],
                elements,
                config,
            )
        )

    all_motion_distances: list[float] = []
    supported_motion_points = 0
    motion_points = 0
    supported_map_samples = 0
    map_samples = 0
    orientation_agreements = 0
    orientation_comparisons = 0
    transitions = 0
    consistent_transitions = 0
    scenarios_without_motion_support = 0
    for diagnostic in diagnostics:
        distance = cast(Json, diagnostic["motion_to_map_distance_m"])
        if distance["count"]:
            all_motion_distances.append(float(distance["mean"]))
        supported_motion_points += int(diagnostic["motion_point_within_support_count"])
        motion_points += int(diagnostic["motion_point_count"])
        supported_map_samples += int(
            diagnostic["mapped_structure_supported_sample_count"]
        )
        map_samples += int(diagnostic["mapped_structure_sample_count"])
        orientation_agreements += int(diagnostic["orientation_agreement_count"])
        orientation_comparisons += int(diagnostic["orientation_comparison_count"])
        transitions += int(diagnostic["observed_lane_transition_count"])
        consistent_transitions += int(diagnostic["map_consistent_transition_count"])
        scenarios_without_motion_support += (
            int(diagnostic["mapped_structure_supported_sample_count"]) == 0
        )
    result = {
        "schema_version": "1.0",
        "batch": BATCH,
        "stage": "B",
        "completed": True,
        "development_scenarios_analyzed": len(ordered),
        "pilot_scenario_access_count": 0,
        "test_scenario_access_count": 0,
        "stage_a_verified_before_map_access": True,
        "stage_a_identity": cast(Json, stage_a["manifest"])["stage_a_identity"],
        "stage_a_bundle_sha256": cast(Json, stage_a["manifest"])["bundle_sha256"],
        "map_input_count": len(ordered),
        "map_input_sha256_by_safe_scenario": dict(sorted(map_hashes.items())),
        "aggregate": {
            "scenario_mean_motion_to_map_distance_m": distribution(
                all_motion_distances
            ),
            "motion_point_within_support_fraction": (
                supported_motion_points / motion_points if motion_points else 0.0
            ),
            "mapped_structure_supported_fraction": (
                supported_map_samples / map_samples if map_samples else 0.0
            ),
            "orientation_agreement_fraction": (
                orientation_agreements / orientation_comparisons
                if orientation_comparisons
                else 0.0
            ),
            "map_consistent_observed_transition_fraction": (
                consistent_transitions / transitions if transitions else 0.0
            ),
            "scenario_without_mapped_structure_support_count": (
                scenarios_without_motion_support
            ),
            "motion_point_count": motion_points,
            "mapped_structure_sample_count": map_samples,
            "observed_lane_transition_count": transitions,
        },
        "scenarios": diagnostics,
        "diagnostics_used_for_tuning": False,
        "feedback_to_stage_a": False,
        "production_layout_artifacts_created": False,
    }
    stage_b_root.mkdir(parents=True, exist_ok=True)
    output = stage_b_root / "post_freeze_map_diagnostics.json"
    _write_json(output, result)
    after = directory_snapshot(stage_a_root)
    if after != before:
        raise ArtifactError("Stage B altered the frozen Stage A bundle")
    reread = _read_json(output)
    if reread != result:
        raise ArtifactError("Stage B diagnostics did not round-trip")
    return {
        "diagnostics": result,
        "stage_a_snapshot_before": before,
        "stage_a_snapshot_after": after,
        "stage_a_byte_identical_after_stage_b": True,
        "output_sha256": sha256_file(output),
    }
