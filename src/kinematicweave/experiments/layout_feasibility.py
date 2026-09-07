"""Motion-only layout-induction feasibility measurements and Stage A freeze."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
from itertools import combinations, pairwise
import json
import math
from pathlib import Path
from typing import Any, cast

from shapely.geometry import LineString, Point  # type: ignore[import-untyped]

from kinematicweave.baselines.motion import (
    CanonicalScenarioBundle,
    included_motion_trajectories,
    read_canonical_scenario_bundle,
)
from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.velocity_bounded import (
    VelocityBoundedCodecConfig,
    encode_scenario_velocity_bounded,
    velocity_bounded_encoder_parameters_identity,
)
from kinematicweave.domain.records import CoordinateFrameRecord, Trajectory
from kinematicweave.domain.semantic import MotionEventType
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    build_semantic_motion_tape,
    semantic_motion_configuration_identity,
)
from kinematicweave.layout.shared_motion import (
    RouteCandidate,
    SharedMotionConfig,
    build_route_candidates,
    build_shared_motion_model,
    resample_arc_length,
    shared_motion_configuration_identity,
)

type Json = dict[str, Any]

SCHEMA_VERSION = "1.0"
BATCH = "5.1"
DEVELOPMENT_SCENARIO_COUNT = 150
VALIDATION_IDENTITY = "1644aac167733a396a9b3cf5a0e9baf0dfeaa4a4c5d913a4d7a2c43f03367a13"
ALLOWED_MOTION_FILENAMES = (
    "scenario_manifest.parquet",
    "coordinate_frame_metadata.parquet",
    "agent_metadata.parquet",
    "trajectory_samples.parquet",
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ArtifactError(f"expected a JSON object: {path}")
    return cast(Json, value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.temporary")
    temporary.write_text(
        _canonical_json(value),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_snapshot(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ArtifactError(f"artifact root is missing: {root}")
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def safe_identifier(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()
    return digest[:16]


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: Sequence[int | float]) -> Json:
    normalized = [float(value) for value in values]
    return {
        "count": len(normalized),
        "minimum": min(normalized) if normalized else None,
        "q25": _percentile(normalized, 0.25),
        "median": _percentile(normalized, 0.50),
        "q75": _percentile(normalized, 0.75),
        "p95": _percentile(normalized, 0.95),
        "maximum": max(normalized) if normalized else None,
        "mean": sum(normalized) / len(normalized) if normalized else None,
    }


@dataclass(frozen=True, slots=True)
class LayoutFeasibilityConfig:
    """Pre-map descriptive settings for the Batch 5.1 feasibility gate."""

    spatial_support_cell_m: float = 2.0
    endpoint_support_cell_m: float = 5.0
    crossing_endpoint_exclusion_m: float = 2.0
    crossing_turn_radius_m: float = 8.0
    crossing_turn_minimum_rad: float = math.radians(30.0)
    grade_separation_minimum_m: float = 3.0
    map_support_distance_m: float = 2.0
    map_orientation_maximum_rad: float = math.radians(30.0)
    map_sampling_interval_m: float = 2.0
    stored_path_point_count: int = 32

    def __post_init__(self) -> None:
        for name in (
            "spatial_support_cell_m",
            "endpoint_support_cell_m",
            "crossing_endpoint_exclusion_m",
            "crossing_turn_radius_m",
            "crossing_turn_minimum_rad",
            "grade_separation_minimum_m",
            "map_support_distance_m",
            "map_orientation_maximum_rad",
            "map_sampling_interval_m",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise ValidationError(f"{name} must be finite and positive")
            object.__setattr__(self, name, float(value))
        if (
            not isinstance(self.stored_path_point_count, int)
            or isinstance(self.stored_path_point_count, bool)
            or self.stored_path_point_count < 2
        ):
            raise ValidationError("stored_path_point_count must be at least two")


DEFAULT_CONFIG = LayoutFeasibilityConfig()
SHARED_CONFIG = SharedMotionConfig()
SEMANTIC_CONFIG = SemanticMotionConfig()
CODEC_CONFIG = VelocityBoundedCodecConfig()


def layout_feasibility_configuration_identity(
    config: LayoutFeasibilityConfig = DEFAULT_CONFIG,
) -> str:
    return canonical_sha256(
        "phase5-layout-feasibility-configuration",
        {
            "batch": BATCH,
            "descriptive_thresholds": {
                name: getattr(config, name) for name in config.__dataclass_fields__
            },
            "shared_motion_configuration_identity": (
                shared_motion_configuration_identity(SHARED_CONFIG)
            ),
            "semantic_configuration_identity": (
                semantic_motion_configuration_identity(SEMANTIC_CONFIG)
            ),
            "procedural_configuration_identity": (
                velocity_bounded_encoder_parameters_identity(CODEC_CONFIG)
            ),
            "classification_policy": "pre-map-qualified-descriptive-v1",
            "crossing_policy": "transition-or-elevation-supported-v1",
            "aggregation_policy": "metadata-proof-required-v1",
        },
    )


def feasibility_contract(
    config: LayoutFeasibilityConfig = DEFAULT_CONFIG,
) -> Json:
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "objective": (
            "descriptive motion-derived layout-induction feasibility without "
            "materializing layout objects"
        ),
        "configuration_identity": layout_feasibility_configuration_identity(config),
        "cohort": {
            "role": "development",
            "scenario_count": DEVELOPMENT_SCENARIO_COUNT,
            "pilot_access": "prohibited",
            "test_access": "prohibited",
            "replacement_after_execution": "prohibited",
            "exclusions_change_membership": False,
        },
        "stage_a": {
            "allowed_inputs": [
                "canonical motion",
                "procedural tracks derived from canonical motion",
                "semantic motion derived from procedural replay",
                "scenario-local shared-motion candidates and templates",
                "frozen split and coordinate-frame metadata",
            ],
            "map_inputs_allowed": False,
            "completion_required_before_stage_b": True,
            "bundle_is_immutable": True,
        },
        "stage_b": {
            "allowed_inputs": [
                "verified completed Stage A bundle",
                "canonical development maps for the same scenarios",
            ],
            "separate_artifact_root_required": True,
            "feedback_to_stage_a": False,
            "descriptive_only": True,
        },
        "descriptive_measurements": {
            name: getattr(config, name) for name in config.__dataclass_fields__
        },
        "shared_motion_threshold_source": ("accepted Batch 3.6 SharedMotionConfig"),
        "interpretation_rules": {
            "feasible_now": (
                "directly observable motion evidence exists; this does not "
                "authorize a production layout object"
            ),
            "feasible_only_with_aggregation": (
                "repeated support exists but a persistent estimate requires "
                "multiple observations"
            ),
            "feasible_only_as_partial_motion_supported_structure": (
                "motion supports observed portions or transitions but cannot "
                "establish completeness"
            ),
            "unsupported": (
                "the requested complete structure is not identifiable from "
                "the frozen evidence"
            ),
            "deferred": (
                "required physical or geographic information is absent or "
                "not proven comparable"
            ),
        },
        "threshold_status": (
            "predeclared descriptive engineering criteria, not hypothesis tests"
        ),
        "production_layout_induction_authorized": False,
    }


@dataclass(frozen=True, slots=True)
class MotionOnlyScenarioInput:
    """Exact allowlisted Stage A inputs for one development scenario."""

    safe_scenario_id: str
    cohort_role: str
    selection_rank: int
    files: tuple[Path, ...]
    sha256_by_filename: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.cohort_role != "development":
            raise ValidationError("Stage A accepts development inputs only")
        if self.selection_rank < 1:
            raise ValidationError("selection_rank must be positive")
        if len(self.safe_scenario_id) != 16:
            raise ValidationError("safe_scenario_id must contain 16 hex characters")
        if len(self.files) != len(ALLOWED_MOTION_FILENAMES):
            raise ValidationError("Stage A input file set is incomplete")
        filenames = tuple(path.name for path in self.files)
        if filenames != ALLOWED_MOTION_FILENAMES:
            raise ValidationError("Stage A input file set is not allowlisted")
        if any(
            path.name == "vector_map_elements.parquet" or "map_adapter" in path.name
            for path in self.files
        ):
            raise ValidationError("Stage A cannot receive map inputs")
        parents = {path.parent.resolve() for path in self.files}
        if len(parents) != 1:
            raise ValidationError("Stage A files must share one cache entry")
        hashes = dict(self.sha256_by_filename)
        if set(hashes) != set(ALLOWED_MOTION_FILENAMES):
            raise ValidationError("Stage A input hashes are incomplete")
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in hashes.values()
        ):
            raise ValidationError("Stage A input hashes must be SHA-256")


@dataclass(frozen=True, slots=True)
class _MotionPath:
    safe_track_id: str
    points: tuple[tuple[float, float, float | None], ...]
    path_length_m: float
    duration_s: float
    event_types: tuple[str, ...]


def _valid_points(
    trajectory: Trajectory,
) -> tuple[tuple[float, float, float | None], ...]:
    return tuple(
        (sample.x_m, sample.y_m, sample.z_m)
        for sample in trajectory.samples
        if sample.is_valid
    )


def _path_length(points: Sequence[tuple[float, float, float | None]]) -> float:
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in pairwise(points)
    )


def _valid_duration_s(trajectory: Trajectory) -> float:
    valid = tuple(sample for sample in trajectory.samples if sample.is_valid)
    if len(valid) < 2:
        return 0.0
    return (valid[-1].timestamp_ns - valid[0].timestamp_ns) / 1_000_000_000


def _cell(
    point: tuple[float, float, float | None],
    cell_size: float,
) -> tuple[int, int]:
    return math.floor(point[0] / cell_size), math.floor(point[1] / cell_size)


def _resampled_xy(
    points: Sequence[tuple[float, float, float | None]],
    count: int,
) -> tuple[tuple[float, float], ...]:
    return resample_arc_length(tuple((item[0], item[1]) for item in points), count)


def _compatible_xy(
    left: _MotionPath,
    right: _MotionPath,
    *,
    reverse_right: bool,
) -> bool:
    left_xy = _resampled_xy(left.points, SHARED_CONFIG.resample_point_count)
    right_xy = _resampled_xy(right.points, SHARED_CONFIG.resample_point_count)
    if reverse_right:
        right_xy = tuple(reversed(right_xy))
    distances = tuple(math.dist(a, b) for a, b in zip(left_xy, right_xy, strict=True))
    ratio = left.path_length_m / right.path_length_m
    return (
        distances[0] <= SHARED_CONFIG.maximum_start_distance_m
        and distances[-1] <= SHARED_CONFIG.maximum_end_distance_m
        and sum(distances) / len(distances) <= SHARED_CONFIG.maximum_mean_path_error_m
        and max(distances) <= SHARED_CONFIG.maximum_path_error_m
        and SHARED_CONFIG.minimum_path_length_ratio
        <= ratio
        <= SHARED_CONFIG.maximum_path_length_ratio
    )


def _z_at(
    path: _MotionPath,
    point: Point,
) -> float | None:
    line = LineString([(item[0], item[1]) for item in path.points])
    distance = line.project(point)
    traversed = 0.0
    for left, right in pairwise(path.points):
        segment = math.hypot(right[0] - left[0], right[1] - left[1])
        if traversed + segment >= distance and segment > 0.0:
            if left[2] is None or right[2] is None:
                return None
            fraction = float((distance - traversed) / segment)
            left_z = float(left[2])
            right_z = float(right[2])
            return float(left_z + fraction * (right_z - left_z))
        traversed += segment
    return path.points[-1][2]


def _turn_near(
    path: _MotionPath,
    point: Point,
    config: LayoutFeasibilityConfig,
) -> bool:
    points = path.points
    for before, anchor, after in zip(points, points[1:], points[2:], strict=False):
        if Point(anchor[0], anchor[1]).distance(point) > config.crossing_turn_radius_m:
            continue
        first = math.atan2(anchor[1] - before[1], anchor[0] - before[0])
        second = math.atan2(after[1] - anchor[1], after[0] - anchor[0])
        delta = abs(math.atan2(math.sin(second - first), math.cos(second - first)))
        if delta >= config.crossing_turn_minimum_rad:
            return True
    return False


def _intersection_points(geometry: object) -> tuple[Point, ...]:
    if isinstance(geometry, Point):
        return (geometry,)
    geoms = getattr(geometry, "geoms", ())
    return tuple(item for item in geoms if isinstance(item, Point))


def measure_motion_paths(
    paths: Sequence[_MotionPath],
    config: LayoutFeasibilityConfig = DEFAULT_CONFIG,
) -> Json:
    """Measure repeated spatial evidence without creating layout objects."""
    ordered = tuple(sorted(paths, key=lambda item: item.safe_track_id))
    directional_pairs = 0
    opposing_pairs = 0
    support_graph: dict[str, set[str]] = {path.safe_track_id: set() for path in ordered}
    for left, right in combinations(ordered, 2):
        if _compatible_xy(left, right, reverse_right=False):
            directional_pairs += 1
            support_graph[left.safe_track_id].add(right.safe_track_id)
            support_graph[right.safe_track_id].add(left.safe_track_id)
        elif _compatible_xy(left, right, reverse_right=True):
            opposing_pairs += 1
            support_graph[left.safe_track_id].add(right.safe_track_id)
            support_graph[right.safe_track_id].add(left.safe_track_id)

    cells_by_track = {
        path.safe_track_id: {
            _cell(point, config.spatial_support_cell_m) for point in path.points
        }
        for path in ordered
    }
    cell_tracks: dict[tuple[int, int], set[str]] = {}
    for track_id, cells in cells_by_track.items():
        for cell in cells:
            cell_tracks.setdefault(cell, set()).add(track_id)
    supported_cells = {
        cell for cell, track_ids in cell_tracks.items() if len(track_ids) >= 2
    }
    multi_track_ids = {
        track_id
        for track_id, cells in cells_by_track.items()
        if cells & supported_cells
    }

    starts: dict[tuple[int, int], set[str]] = {}
    ends: dict[tuple[int, int], set[str]] = {}
    for path in ordered:
        starts.setdefault(
            _cell(path.points[0], config.endpoint_support_cell_m), set()
        ).add(path.safe_track_id)
        ends.setdefault(
            _cell(path.points[-1], config.endpoint_support_cell_m), set()
        ).add(path.safe_track_id)
    repeated_start_cells = sum(len(value) >= 2 for value in starts.values())
    repeated_end_cells = sum(len(value) >= 2 for value in ends.values())
    potential_splits = sum(
        len(value) >= 2
        and len(
            {
                _cell(
                    next(
                        path.points[-1]
                        for path in ordered
                        if path.safe_track_id == track_id
                    ),
                    config.endpoint_support_cell_m,
                )
                for track_id in value
            }
        )
        >= 2
        for value in starts.values()
    )
    potential_merges = sum(
        len(value) >= 2
        and len(
            {
                _cell(
                    next(
                        path.points[0]
                        for path in ordered
                        if path.safe_track_id == track_id
                    ),
                    config.endpoint_support_cell_m,
                )
                for track_id in value
            }
        )
        >= 2
        for value in ends.values()
    )

    crossing_keys: dict[tuple[int, int], str] = {}
    for left, right in combinations(ordered, 2):
        left_line = LineString([(item[0], item[1]) for item in left.points])
        right_line = LineString([(item[0], item[1]) for item in right.points])
        intersection = left_line.intersection(right_line)
        for point in _intersection_points(intersection):
            if (
                min(
                    point.distance(Point(*left_line.coords[0])),
                    point.distance(Point(*left_line.coords[-1])),
                    point.distance(Point(*right_line.coords[0])),
                    point.distance(Point(*right_line.coords[-1])),
                )
                <= config.crossing_endpoint_exclusion_m
            ):
                continue
            key = (round(point.x), round(point.y))
            left_z = _z_at(left, point)
            right_z = _z_at(right, point)
            if (
                left_z is not None
                and right_z is not None
                and abs(left_z - right_z) >= config.grade_separation_minimum_m
            ):
                classification = "elevation_separated"
            elif any(_turn_near(path, point, config) for path in ordered):
                classification = "observed_transition_supported"
            else:
                classification = "geometric_only_ambiguous"
            precedence = {
                "geometric_only_ambiguous": 0,
                "observed_transition_supported": 1,
                "elevation_separated": 2,
            }
            previous = crossing_keys.get(key)
            if previous is None or precedence[classification] > precedence[previous]:
                crossing_keys[key] = classification

    visited: set[str] = set()
    components = 0
    for track_id in sorted(support_graph):
        if track_id in visited:
            continue
        components += 1
        stack = [track_id]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            stack.extend(sorted(support_graph[current] - visited))

    event_counts = Counter(
        event_type for path in ordered for event_type in path.event_types
    )
    turn_sequences = Counter(
        tuple(
            event_type
            for event_type in path.event_types
            if event_type in {"left_turn", "right_turn"}
        )
        for path in ordered
    )
    return {
        "track_count": len(ordered),
        "directional_agreement_pair_count": directional_pairs,
        "opposing_direction_pair_count": opposing_pairs,
        "multi_track_supported_track_count": len(multi_track_ids),
        "multi_track_supported_track_fraction": (
            len(multi_track_ids) / len(ordered) if ordered else 0.0
        ),
        "occupied_spatial_cell_count": len(cell_tracks),
        "repeated_spatial_cell_count": len(supported_cells),
        "repeated_start_cell_count": repeated_start_cells,
        "repeated_endpoint_cell_count": repeated_end_cells,
        "potential_merge_count": potential_merges,
        "potential_split_count": potential_splits,
        "geometric_crossing_count": len(crossing_keys),
        "observed_transition_crossing_count": sum(
            value == "observed_transition_supported" for value in crossing_keys.values()
        ),
        "elevation_separated_crossing_count": sum(
            value == "elevation_separated" for value in crossing_keys.values()
        ),
        "ambiguous_geometric_crossing_count": sum(
            value == "geometric_only_ambiguous" for value in crossing_keys.values()
        ),
        "event_count_by_type": dict(sorted(event_counts.items())),
        "observed_turn_sequences": {
            ",".join(sequence) if sequence else "none": count
            for sequence, count in sorted(turn_sequences.items())
        },
        "support_component_count": components,
        "fragmentation_ratio": components / len(ordered) if ordered else 0.0,
    }


def _synthetic_path(
    name: str,
    points: Sequence[tuple[float, float] | tuple[float, float, float]],
    events: Sequence[str] = (),
) -> _MotionPath:
    normalized = tuple(
        (
            float(point[0]),
            float(point[1]),
            float(point[2]) if len(point) == 3 else None,
        )
        for point in points
    )
    return _MotionPath(
        safe_track_id=name,
        points=normalized,
        path_length_m=_path_length(normalized),
        duration_s=10.0,
        event_types=tuple(events),
    )


def synthetic_feasibility_results(
    config: LayoutFeasibilityConfig = DEFAULT_CONFIG,
) -> Json:
    cases = {
        "repeated_directional": (
            _synthetic_path("a", ((0, 0), (10, 0), (20, 0))),
            _synthetic_path("b", ((0, 0.2), (10, 0.1), (20, 0.2))),
        ),
        "bidirectional": (
            _synthetic_path("a", ((0, 0), (10, 0), (20, 0))),
            _synthetic_path("b", ((20, 0.2), (10, 0.1), (0, 0.2))),
        ),
        "parallel_distinct": (
            _synthetic_path("a", ((0, 0), (10, 0), (20, 0))),
            _synthetic_path("b", ((0, 6), (10, 6), (20, 6))),
        ),
        "turn_and_connected_crossing": (
            _synthetic_path(
                "east_west",
                ((-10, 0), (0, 0), (10, 0)),
            ),
            _synthetic_path(
                "south_north",
                ((0, -10), (0, 0), (0, 10)),
            ),
            _synthetic_path(
                "observed_turn",
                ((-10, 0), (-2, 0), (0, 2), (0, 10)),
                ("left_turn",),
            ),
        ),
        "merge_and_split": (
            _synthetic_path("merge_a", ((-10, -5), (0, 0), (10, 0))),
            _synthetic_path("merge_b", ((-10, 5), (0, 0), (10, 0))),
            _synthetic_path("split_a", ((20, 0), (30, 0), (40, -5))),
            _synthetic_path("split_b", ((20, 0), (30, 0), (40, 5))),
        ),
        "disconnected_crossing": (
            _synthetic_path("east_west", ((-10, 0), (0, 0), (10, 0))),
            _synthetic_path("south_north", ((0, -10), (0, 0), (0, 10))),
        ),
        "repeated_stops_and_endpoints": (
            _synthetic_path(
                "stop_a",
                ((0, 0), (5, 0), (10, 0)),
                ("stop",),
            ),
            _synthetic_path(
                "stop_b",
                ((0, 0.2), (5, 0.1), (10, 0.2)),
                ("stop",),
            ),
        ),
        "single_pass_sparse": (_synthetic_path("only", ((0, 0), (10, 0), (20, 0))),),
        "elevation_separated_crossing": (
            _synthetic_path(
                "ground",
                ((-10, 0, 0), (0, 0, 0), (10, 0, 0)),
            ),
            _synthetic_path(
                "bridge",
                ((0, -10, 5), (0, 0, 5), (0, 10, 5)),
            ),
        ),
    }
    measurements = {
        name: measure_motion_paths(paths, config) for name, paths in cases.items()
    }
    checks = {
        "directional_support_detected": (
            measurements["repeated_directional"]["directional_agreement_pair_count"]
            == 1
        ),
        "opposing_support_detected": (
            measurements["bidirectional"]["opposing_direction_pair_count"] == 1
        ),
        "parallel_paths_remain_distinct": (
            measurements["parallel_distinct"]["directional_agreement_pair_count"] == 0
        ),
        "connected_crossing_requires_observed_transition": (
            measurements["turn_and_connected_crossing"][
                "observed_transition_crossing_count"
            ]
            >= 1
        ),
        "disconnected_crossing_remains_ambiguous": (
            measurements["disconnected_crossing"]["ambiguous_geometric_crossing_count"]
            == 1
        ),
        "elevation_crossing_remains_disconnected": (
            measurements["elevation_separated_crossing"][
                "elevation_separated_crossing_count"
            ]
            == 1
        ),
        "sparse_case_retained": (
            measurements["single_pass_sparse"]["multi_track_supported_track_count"] == 0
        ),
        "merge_and_split_evidence_detected": (
            measurements["merge_and_split"]["potential_merge_count"] >= 1
            and measurements["merge_and_split"]["potential_split_count"] >= 1
        ),
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise ArtifactError(f"synthetic feasibility checks failed: {failed}")
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "case_count": len(cases),
        "measurements": measurements,
        "checks": checks,
        "all_checks_passed": True,
        "layout_artifacts_created": False,
    }


def _scenario_paths(
    bundle: CanonicalScenarioBundle,
    safe_scenario_id: str,
) -> tuple[tuple[_MotionPath, ...], Json, Json]:
    canonical_eligible = included_motion_trajectories(bundle.trajectories)
    phase2_ids = {item.trajectory_id for item in canonical_eligible}
    excluded_phase2 = len(bundle.trajectories) - len(canonical_eligible)
    layout_eligible = tuple(
        trajectory
        for trajectory in canonical_eligible
        if _path_length(_valid_points(trajectory)) > 0.50
    )
    stationary = len(canonical_eligible) - len(layout_eligible)
    if not layout_eligible:
        return (
            (),
            {
                "canonical_track_count": len(bundle.trajectories),
                "phase2_eligible_track_count": len(canonical_eligible),
                "layout_eligible_track_count": 0,
                "excluded_track_count": excluded_phase2 + stationary,
                "exclusion_count_by_reason": {
                    "phase2_eligibility": excluded_phase2,
                    "stationary_or_near_stationary": stationary,
                },
            },
            {
                "shared_template_count": 0,
                "shared_template_track_count": 0,
                "shared_template_track_fraction": 0.0,
            },
        )

    agent_ids = {item.agent_id for item in canonical_eligible}
    agents = tuple(item for item in bundle.agents if item.agent_id in agent_ids)
    scenario = replace(bundle.scenario, agent_count=len(agents))
    procedural = encode_scenario_velocity_bounded(
        scenario,
        bundle.coordinate_frame,
        agents,
        canonical_eligible,
        CODEC_CONFIG,
        source_validation_report_identity=VALIDATION_IDENTITY,
    )
    semantic = build_semantic_motion_tape(
        procedural,
        canonical_eligible,
        SEMANTIC_CONFIG,
    )
    trajectory_by_id = {item.trajectory_id: item for item in canonical_eligible}
    events_by_trajectory: dict[str, tuple[str, ...]] = {}
    candidates: list[RouteCandidate] = []
    for semantic_track in semantic.tracks:
        trajectory_id = semantic_track.procedural_track.trajectory_id
        events_by_trajectory[trajectory_id] = tuple(
            MotionEventType(event.event_type).value for event in semantic_track.events
        )
        if trajectory_id in {item.trajectory_id for item in layout_eligible}:
            candidates.extend(
                build_route_candidates(
                    scenario,
                    trajectory_by_id[trajectory_id],
                    semantic_track,
                    SHARED_CONFIG,
                )
            )
    model = build_shared_motion_model(
        scenario.dataset_id,
        scenario.dataset_version,
        (scenario,),
        canonical_eligible,
        (semantic,),
        SHARED_CONFIG,
    )
    shared_template_ids = {
        item.template_id
        for item in model.route_templates
        if item.member_count >= SHARED_CONFIG.minimum_shared_template_members
    }
    shared_track_ids = {
        item.procedural_track_id
        for item in model.memberships
        if item.template_id in shared_template_ids
    }
    paths = tuple(
        _MotionPath(
            safe_track_id=safe_identifier(
                f"phase5-layout-track:{safe_scenario_id}",
                candidate.procedural_track_id,
            ),
            points=tuple(
                (
                    float(point[0]),
                    float(point[1]),
                    float(point[2]) if len(point) == 3 else None,
                )
                for point in candidate.source_points
            ),
            path_length_m=candidate.path_length_m,
            duration_s=candidate.duration_ns / 1_000_000_000,
            event_types=events_by_trajectory.get(candidate.trajectory_id, ()),
        )
        for candidate in candidates
    )
    counts = {
        "canonical_track_count": len(bundle.trajectories),
        "phase2_eligible_track_count": len(canonical_eligible),
        "layout_eligible_track_count": len(layout_eligible),
        "candidate_valid_run_count": len(paths),
        "excluded_track_count": excluded_phase2 + stationary,
        "exclusion_count_by_reason": {
            "phase2_eligibility": excluded_phase2,
            "stationary_or_near_stationary": stationary,
        },
        "phase2_eligible_identity_count": len(phase2_ids),
    }
    comparison = {
        "shared_template_count": len(shared_template_ids),
        "shared_template_track_count": len(shared_track_ids),
        "shared_template_track_fraction": (
            len(shared_track_ids) / len(canonical_eligible)
            if canonical_eligible
            else 0.0
        ),
        "singleton_templates_retained": True,
        "accepted_shared_motion_thresholds_reused": True,
    }
    return paths, counts, comparison


def _frame_record(frame: CoordinateFrameRecord) -> Json:
    return {
        "coordinate_frame_id": safe_identifier(
            "phase5-coordinate-frame", frame.coordinate_frame_id
        ),
        "parent_frame_present": frame.parent_frame_id is not None,
        "frame_type": frame.frame_type,
        "origin_x_m": frame.origin_x_m,
        "origin_y_m": frame.origin_y_m,
        "origin_z_m": frame.origin_z_m,
        "axis_convention": frame.axis_convention,
        "distance_unit": frame.distance_unit,
        "angle_unit": frame.angle_unit,
        "timestamp_unit": frame.timestamp_unit,
        "source_crs": frame.source_crs,
        "has_elevation": frame.has_elevation,
        "transform_to_parent_present": (frame.transform_to_parent_4x4 is not None),
        "origin_type": str(frame.origin_type),
    }


def _stored_path(path: _MotionPath, point_count: int) -> Json:
    xy = _resampled_xy(path.points, point_count)
    return {
        "safe_track_id": path.safe_track_id,
        "resampled_xy": [[x, y] for x, y in xy],
        "path_length_m": path.path_length_m,
        "duration_s": path.duration_s,
        "event_types": list(path.event_types),
    }


def run_stage_a(
    repository_root: Path,
    inputs: Sequence[MotionOnlyScenarioInput],
    stage_a_root: Path,
    *,
    cohort_identity: str,
    cohort_manifest_sha256: str,
    config: LayoutFeasibilityConfig = DEFAULT_CONFIG,
    require_full_cohort: bool = True,
) -> Json:
    """Execute and freeze map-hidden Stage A evidence."""
    ordered = tuple(sorted(inputs, key=lambda item: item.selection_rank))
    expected = DEVELOPMENT_SCENARIO_COUNT if require_full_cohort else len(ordered)
    if len(ordered) != expected:
        raise ValidationError(
            f"Stage A expected {expected} development scenarios, got {len(ordered)}"
        )
    if len({item.safe_scenario_id for item in ordered}) != len(ordered):
        raise ValidationError("Stage A safe scenario identifiers are not unique")
    if stage_a_root.exists() and any(stage_a_root.iterdir()):
        raise ArtifactError("Stage A artifact root must be new and empty")
    stage_a_root.mkdir(parents=True, exist_ok=True)

    scenario_records: list[Json] = []
    all_track_counts: list[int] = []
    all_durations: list[float] = []
    all_lengths: list[float] = []
    aggregate_counts: Counter[str] = Counter()
    aggregate_events: Counter[str] = Counter()
    shared_tracks = 0
    eligible_tracks = 0
    opened_files: list[Json] = []
    for item in ordered:
        for path in item.files:
            expected_hash = dict(item.sha256_by_filename)[path.name]
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                raise ArtifactError(
                    f"Stage A input checksum differs: {item.safe_scenario_id}/"
                    f"{path.name}"
                )
            opened_files.append(
                {
                    "safe_scenario_id": item.safe_scenario_id,
                    "file_class": path.name,
                    "sha256": actual_hash,
                }
            )
        cache_entry = item.files[0].parent
        bundle = read_canonical_scenario_bundle(
            repository_root,
            cache_entry,
        )
        paths, counts, comparison = _scenario_paths(
            bundle,
            item.safe_scenario_id,
        )
        measurements = measure_motion_paths(paths, config)
        all_track_counts.append(int(counts["layout_eligible_track_count"]))
        all_durations.extend(path.duration_s for path in paths)
        all_lengths.extend(path.path_length_m for path in paths)
        eligible_tracks += int(counts["layout_eligible_track_count"])
        shared_tracks += int(comparison["shared_template_track_count"])
        for key in (
            "canonical_track_count",
            "phase2_eligible_track_count",
            "layout_eligible_track_count",
            "candidate_valid_run_count",
            "excluded_track_count",
        ):
            aggregate_counts[key] += int(counts[key])
        for key in (
            "directional_agreement_pair_count",
            "opposing_direction_pair_count",
            "multi_track_supported_track_count",
            "occupied_spatial_cell_count",
            "repeated_spatial_cell_count",
            "repeated_start_cell_count",
            "repeated_endpoint_cell_count",
            "potential_merge_count",
            "potential_split_count",
            "geometric_crossing_count",
            "observed_transition_crossing_count",
            "elevation_separated_crossing_count",
            "ambiguous_geometric_crossing_count",
            "support_component_count",
        ):
            aggregate_counts[key] += int(measurements[key])
        aggregate_events.update(
            cast(Mapping[str, int], measurements["event_count_by_type"])
        )
        scenario_records.append(
            {
                "safe_scenario_id": item.safe_scenario_id,
                "selection_rank": item.selection_rank,
                "coordinate_frame": _frame_record(bundle.coordinate_frame),
                "counts": counts,
                "measurements": measurements,
                "existing_shared_template_comparison": comparison,
                "paths": [
                    _stored_path(path, config.stored_path_point_count) for path in paths
                ],
            }
        )

    aggregate = {
        "scenario_count": len(ordered),
        **dict(sorted(aggregate_counts.items())),
        "tracks_per_scenario": distribution(all_track_counts),
        "track_duration_s": distribution(all_durations),
        "track_path_length_m": distribution(all_lengths),
        "event_count_by_type": dict(sorted(aggregate_events.items())),
        "multi_track_supported_track_fraction": (
            aggregate_counts["multi_track_supported_track_count"]
            / aggregate_counts["candidate_valid_run_count"]
            if aggregate_counts["candidate_valid_run_count"]
            else 0.0
        ),
        "existing_shared_template_track_count": shared_tracks,
        "existing_shared_template_track_fraction": (
            shared_tracks / eligible_tracks if eligible_tracks else 0.0
        ),
    }
    bundle_value = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "stage": "A",
        "completed": True,
        "cohort_role": "development",
        "cohort_identity": cohort_identity,
        "cohort_manifest_sha256": cohort_manifest_sha256,
        "scenario_count": len(ordered),
        "configuration_identity": (layout_feasibility_configuration_identity(config)),
        "access_audit": {
            "opened_file_count": len(opened_files),
            "opened_files": opened_files,
            "opened_file_classes": list(ALLOWED_MOTION_FILENAMES),
            "map_file_open_count": 0,
            "map_paths_received": False,
            "pilot_scenario_access_count": 0,
            "test_scenario_access_count": 0,
        },
        "aggregate": aggregate,
        "scenarios": scenario_records,
        "all_zero_count_and_unfavorable_findings_retained": True,
        "production_layout_artifacts_created": False,
    }
    bundle_path = stage_a_root / "motion_feasibility_bundle.json"
    _write_json(bundle_path, bundle_value)
    bundle_hash = sha256_file(bundle_path)
    stage_a_identity = canonical_sha256(
        "phase5-layout-feasibility-stage-a",
        {
            "bundle_sha256": bundle_hash,
            "cohort_identity": cohort_identity,
            "configuration_identity": bundle_value["configuration_identity"],
            "scenario_count": len(ordered),
        },
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "stage": "A",
        "completed": True,
        "immutable": True,
        "stage_a_identity": stage_a_identity,
        "configuration_identity": bundle_value["configuration_identity"],
        "cohort_identity": cohort_identity,
        "scenario_count": len(ordered),
        "bundle_file": bundle_path.name,
        "bundle_sha256": bundle_hash,
        "allowed_file_set": [
            "motion_feasibility_bundle.json",
            "stage_a_manifest.json",
        ],
        "map_access_before_completion": False,
        "pilot_test_access_count": 0,
    }
    _write_json(stage_a_root / "stage_a_manifest.json", manifest)
    return verify_stage_a_bundle(
        stage_a_root,
        expected_scenario_count=len(ordered),
    )


def verify_stage_a_bundle(
    stage_a_root: Path,
    *,
    expected_scenario_count: int = DEVELOPMENT_SCENARIO_COUNT,
) -> Json:
    """Verify Stage A completion, exact file set, identity, and checksum."""
    manifest_path = stage_a_root / "stage_a_manifest.json"
    bundle_path = stage_a_root / "motion_feasibility_bundle.json"
    if not manifest_path.is_file() or not bundle_path.is_file():
        raise ArtifactError("Stage A bundle is incomplete")
    actual_files = sorted(
        path.name for path in stage_a_root.iterdir() if path.is_file()
    )
    expected_files = [
        "motion_feasibility_bundle.json",
        "stage_a_manifest.json",
    ]
    if actual_files != expected_files:
        raise ArtifactError("Stage A artifact root contains unexpected files")
    manifest = _read_json(manifest_path)
    bundle = _read_json(bundle_path)
    if manifest.get("completed") is not True or bundle.get("completed") is not True:
        raise ArtifactError("Stage A completion state is false")
    if manifest.get("immutable") is not True:
        raise ArtifactError("Stage A manifest is not immutable")
    if manifest.get("scenario_count") != expected_scenario_count:
        raise ArtifactError("Stage A scenario count differs")
    if bundle.get("scenario_count") != expected_scenario_count:
        raise ArtifactError("Stage A bundle scenario count differs")
    actual_hash = sha256_file(bundle_path)
    if manifest.get("bundle_sha256") != actual_hash:
        raise ArtifactError("Stage A bundle checksum differs")
    expected_identity = canonical_sha256(
        "phase5-layout-feasibility-stage-a",
        {
            "bundle_sha256": actual_hash,
            "cohort_identity": manifest["cohort_identity"],
            "configuration_identity": manifest["configuration_identity"],
            "scenario_count": expected_scenario_count,
        },
    )
    if manifest.get("stage_a_identity") != expected_identity:
        raise ArtifactError("Stage A identity differs")
    audit = cast(Json, bundle["access_audit"])
    if (
        audit.get("map_file_open_count") != 0
        or audit.get("map_paths_received") is not False
        or audit.get("pilot_scenario_access_count") != 0
        or audit.get("test_scenario_access_count") != 0
    ):
        raise ArtifactError("Stage A access boundary was violated")
    return {
        "manifest": manifest,
        "bundle": bundle,
        "snapshot": directory_snapshot(stage_a_root),
    }


def av2_motion_results(stage_a: Json) -> Json:
    bundle = cast(Json, stage_a["bundle"])
    aggregate = cast(Json, bundle["aggregate"])
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "stage": "A",
        "stage_a_identity": cast(Json, stage_a["manifest"])["stage_a_identity"],
        "stage_a_completed": True,
        "development_scenarios_analyzed": bundle["scenario_count"],
        "pilot_scenario_access_count": 0,
        "test_scenario_access_count": 0,
        "map_file_open_count": 0,
        "aggregate": aggregate,
        "all_zero_count_and_unfavorable_findings_retained": True,
        "production_layout_induction_implemented": False,
    }


def aggregation_analysis(stage_a: Json) -> Json:
    bundle = cast(Json, stage_a["bundle"])
    scenarios = cast(list[Json], bundle["scenarios"])
    frames = [cast(Json, item["coordinate_frame"]) for item in scenarios]
    frame_identity = canonical_sha256(
        "phase5-layout-feasibility-coordinate-frames",
        [
            {
                "safe_scenario_id": item["safe_scenario_id"],
                "coordinate_frame": item["coordinate_frame"],
            }
            for item in scenarios
        ],
    )
    crs_values = {frame["source_crs"] for frame in frames}
    parent_count = sum(bool(frame["parent_frame_present"]) for frame in frames)
    transform_count = sum(
        bool(frame["transform_to_parent_present"]) for frame in frames
    )
    comparable = (
        len(crs_values) == 1
        and None not in crs_values
        and parent_count == len(frames)
        and transform_count == len(frames)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "source": "Stage A coordinate-frame metadata only",
        "scenario_count": len(scenarios),
        "coordinate_frame_identity": frame_identity,
        "source_crs_values": sorted(
            "null" if value is None else str(value) for value in crs_values
        ),
        "parent_frame_metadata_count": parent_count,
        "transform_to_parent_metadata_count": transform_count,
        "coordinates_comparable_across_scenarios": comparable,
        "numerical_proximity_used_as_compatibility_evidence": False,
        "deterministic_grouping_without_maps": (
            "scenario-local only" if not comparable else "metadata-proven groups"
        ),
        "grouping_crosses_development_pilot_test_boundaries": False,
        "metadata_sufficient_to_prevent_false_geographic_joins": (comparable),
        "recommendation": (
            "scenario-local construction; defer multi-scenario geographic aggregation"
            if not comparable
            else "metadata-proven development-only grouping is feasible"
        ),
        "production_multi_scenario_induction_performed": False,
    }
