"""Deterministic motion-only route-template construction and map evaluation."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import combinations, pairwise
import json
import math

from shapely.affinity import translate  # type: ignore[import-untyped]
from shapely.geometry import LineString, Point  # type: ignore[import-untyped]

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.domain.map_records import (
    MapElementType,
    VectorMapElementRecord,
    geometry_from_canonical_wkb,
    geometry_to_canonical_wkb,
)
from kinematicweave.domain.records import (
    AgentClass,
    OriginType,
    ScenarioRecord,
    Trajectory,
    TrajectorySampleRecord,
)
from kinematicweave.domain.semantic import (
    MotionEventType,
    SemanticMotionTape,
    SemanticMotionTrack,
)
from kinematicweave.domain.shared_motion import (
    MotionCategory,
    MotionCategoryLabel,
    RouteTemplate,
    RouteTemplateMembership,
    SharedMotionModel,
)
from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import make_identifier

__all__ = [
    "MapEvaluation",
    "RouteCandidate",
    "RouteCompatibility",
    "SharedMotionConfig",
    "build_route_candidates",
    "build_shared_motion_model",
    "classify_motion_category",
    "classify_semantic_motion_track",
    "cluster_scenario",
    "compute_route_compatibility",
    "evaluate_shared_motion_map",
    "event_signature_json",
    "resample_arc_length",
    "shared_motion_configuration_identity",
]


def _number(value: object, field_name: str, *, nonnegative: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or (nonnegative and normalized < 0.0):
        raise ValidationError(f"{field_name} must be finite and nonnegative")
    return normalized


def _positive_int(value: object, field_name: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    if not minimum <= value <= 1_000_000:
        raise ValidationError(f"{field_name} is outside its valid range")
    return value


@dataclass(frozen=True, slots=True)
class SharedMotionConfig:
    resample_point_count: int = 32
    maximum_start_distance_m: float = 5.0
    maximum_end_distance_m: float = 5.0
    maximum_mean_path_error_m: float = 1.5
    maximum_path_error_m: float = 3.0
    minimum_path_length_ratio: float = 0.67
    maximum_path_length_ratio: float = 1.50
    minimum_shared_template_members: int = 2
    map_match_maximum_distance_m: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resample_point_count",
            _positive_int(self.resample_point_count, "resample_point_count", minimum=2),
        )
        object.__setattr__(
            self,
            "minimum_shared_template_members",
            _positive_int(
                self.minimum_shared_template_members,
                "minimum_shared_template_members",
                minimum=2,
            ),
        )
        for name in (
            "maximum_start_distance_m",
            "maximum_end_distance_m",
            "maximum_mean_path_error_m",
            "maximum_path_error_m",
            "map_match_maximum_distance_m",
        ):
            object.__setattr__(
                self, name, _number(getattr(self, name), name, nonnegative=True)
            )
        minimum = _number(self.minimum_path_length_ratio, "minimum_path_length_ratio")
        maximum = _number(self.maximum_path_length_ratio, "maximum_path_length_ratio")
        if minimum <= 0.0 or maximum < minimum:
            raise ValidationError("path-length ratio interval is invalid")
        object.__setattr__(self, "minimum_path_length_ratio", minimum)
        object.__setattr__(self, "maximum_path_length_ratio", maximum)


_DEFAULT_CONFIG = SharedMotionConfig()


def shared_motion_configuration_identity(config: SharedMotionConfig) -> str:
    if not isinstance(config, SharedMotionConfig):
        raise ValidationError("config must be a SharedMotionConfig")
    return canonical_sha256(
        "shared-motion-configuration",
        {
            "thresholds": {
                name: getattr(config, name) for name in config.__dataclass_fields__
            },
            "resampling_policy": "normalized_planar_arc_length:v1",
            "category_policy": "fixed_priority:v1",
            "spatial_scope": "canonical_scenario_source_xy:v1",
            "clustering_algorithm": "deterministic_greedy_complete_link:v1",
            "representative_selection_policy": "observed_medoid:v1",
            "map_evaluation_policy": "post_construction_nearest_lane:v1",
            "ordering_and_tie_break_version": "v1",
        },
    )


def event_signature_json(track: SemanticMotionTrack) -> str:
    """Return the canonical event signature for one semantic track."""
    if not isinstance(track, SemanticMotionTrack):
        raise ValidationError("track must be a SemanticMotionTrack")
    types = tuple(MotionEventType(event.event_type) for event in track.events)
    counts = Counter(item.value for item in types)
    signature = {
        "ordered_event_types": [item.value for item in types],
        "event_count_by_type": {
            event_type.value: counts[event_type.value] for event_type in MotionEventType
        },
        "gap_count": counts[MotionEventType.GAP.value],
        "stop_count": counts[MotionEventType.STOP.value],
        "signed_turn_event_sequence": [
            item.value
            for item in types
            if item in {MotionEventType.LEFT_TURN, MotionEventType.RIGHT_TURN}
        ],
        "acceleration_braking_sequence": [
            item.value
            for item in types
            if item in {MotionEventType.ACCELERATION, MotionEventType.BRAKING}
        ],
    }
    return canonical_json_text(signature, trailing_newline=False)


def _valid_runs(
    trajectory: Trajectory,
) -> tuple[tuple[TrajectorySampleRecord, ...], ...]:
    runs: list[tuple[TrajectorySampleRecord, ...]] = []
    active: list[TrajectorySampleRecord] = []
    for sample in trajectory.samples:
        if sample.is_valid:
            active.append(sample)
        elif active:
            runs.append(tuple(active))
            active = []
    if active:
        runs.append(tuple(active))
    return tuple(runs)


def _path_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in pairwise(points)
    )


def classify_semantic_motion_track(
    track: SemanticMotionTrack,
    trajectory: Trajectory,
) -> MotionCategoryLabel:
    """Assign exactly one fixed-priority category label."""
    if track.procedural_track.trajectory_id != trajectory.trajectory_id:
        raise ValidationError("semantic track and trajectory references differ")
    types = tuple(MotionEventType(event.event_type) for event in track.events)
    length = 0.0
    for run in _valid_runs(trajectory):
        length += _path_length(tuple((sample.x_m, sample.y_m) for sample in run))
    return classify_motion_category(types, length)


def classify_motion_category(
    event_types: Sequence[MotionEventType | str],
    total_valid_path_length_m: float,
) -> MotionCategoryLabel:
    """Apply the fixed category priority to event types and valid path length."""
    types = {MotionEventType(item) for item in event_types}
    length = _number(
        total_valid_path_length_m,
        "total_valid_path_length_m",
        nonnegative=True,
    )
    if MotionEventType.GAP in types:
        return MotionCategoryLabel.GAP_AFFECTED
    if length <= 0.50:
        return MotionCategoryLabel.STATIONARY
    if MotionEventType.STOP in types:
        return MotionCategoryLabel.STOP_AND_GO
    if {MotionEventType.LEFT_TURN, MotionEventType.RIGHT_TURN} <= types:
        return MotionCategoryLabel.MIXED
    if MotionEventType.LEFT_TURN in types:
        return MotionCategoryLabel.LEFT_TURN
    if MotionEventType.RIGHT_TURN in types:
        return MotionCategoryLabel.RIGHT_TURN
    if {MotionEventType.ACCELERATION, MotionEventType.BRAKING} <= types:
        return MotionCategoryLabel.MIXED
    if MotionEventType.ACCELERATION in types:
        return MotionCategoryLabel.ACCELERATING
    if MotionEventType.BRAKING in types:
        return MotionCategoryLabel.BRAKING
    return MotionCategoryLabel.STRAIGHT


def resample_arc_length(
    points: Sequence[tuple[float, float]], point_count: int
) -> tuple[tuple[float, float], ...]:
    """Resample a positive-length ordered path at normalized arc length."""
    count = _positive_int(point_count, "point_count", minimum=2)
    copied = tuple((_number(item[0], "x"), _number(item[1], "y")) for item in points)
    if len(copied) < 2:
        raise ValidationError("resampling requires at least two points")
    cumulative = [0.0]
    compact = [copied[0]]
    for point in copied[1:]:
        distance = math.dist(compact[-1], point)
        if distance > 0.0:
            cumulative.append(cumulative[-1] + distance)
            compact.append(point)
    if len(compact) < 2 or cumulative[-1] <= 0.0:
        raise ValidationError("resampling requires positive path length")
    result: list[tuple[float, float]] = []
    segment = 0
    for index in range(count):
        target = cumulative[-1] * index / (count - 1)
        while segment + 1 < len(cumulative) - 1 and cumulative[segment + 1] < target:
            segment += 1
        span = cumulative[segment + 1] - cumulative[segment]
        fraction = (target - cumulative[segment]) / span
        left, right = compact[segment], compact[segment + 1]
        result.append(
            (
                left[0] + fraction * (right[0] - left[0]),
                left[1] + fraction * (right[1] - left[1]),
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    scenario_id: str
    coordinate_frame_id: str
    procedural_track_id: str
    agent_id: str
    trajectory_id: str
    agent_class: AgentClass
    category_label: MotionCategoryLabel
    event_signature_json: str
    run_index: int
    source_points: tuple[tuple[float, ...], ...]
    resampled_xy: tuple[tuple[float, float], ...]
    path_length_m: float
    duration_ns: int

    @property
    def order_key(self) -> tuple[str, int]:
        return self.procedural_track_id, self.run_index


def build_route_candidates(
    scenario: ScenarioRecord,
    trajectory: Trajectory,
    semantic_track: SemanticMotionTrack,
    config: SharedMotionConfig = _DEFAULT_CONFIG,
) -> tuple[RouteCandidate, ...]:
    """Build one positive-length candidate per valid source trajectory run."""
    if scenario.scenario_id != trajectory.scenario_id:
        raise ValidationError("scenario and trajectory references differ")
    if semantic_track.procedural_track.trajectory_id != trajectory.trajectory_id:
        raise ValidationError("semantic track and trajectory references differ")
    try:
        agent_class = AgentClass(semantic_track.procedural_track.agent_class)
    except ValueError:
        raise ValidationError("procedural track has invalid agent_class") from None
    label = classify_semantic_motion_track(semantic_track, trajectory)
    signature = event_signature_json(semantic_track)
    candidates: list[RouteCandidate] = []
    for run_index, raw_run in enumerate(_valid_runs(trajectory)):
        run = tuple(raw_run)
        xy = tuple(
            (sample.x_m + scenario.origin_x_m, sample.y_m + scenario.origin_y_m)
            for sample in run
        )
        length = _path_length(xy)
        if length <= 0.0:
            continue
        points: list[tuple[float, ...]] = []
        for sample in run:
            x = sample.x_m + scenario.origin_x_m
            y = sample.y_m + scenario.origin_y_m
            if sample.z_m is None:
                points.append((x, y))
            else:
                z_origin = (
                    scenario.origin_z_m if scenario.origin_z_m is not None else 0.0
                )
                points.append((x, y, sample.z_m + z_origin))
        candidates.append(
            RouteCandidate(
                scenario_id=scenario.scenario_id,
                coordinate_frame_id=scenario.coordinate_frame_id,
                procedural_track_id=semantic_track.procedural_track.procedural_track_id,
                agent_id=trajectory.agent_id,
                trajectory_id=trajectory.trajectory_id,
                agent_class=agent_class,
                category_label=label,
                event_signature_json=signature,
                run_index=run_index,
                source_points=tuple(points),
                resampled_xy=resample_arc_length(xy, config.resample_point_count),
                path_length_m=length,
                duration_ns=run[-1].timestamp_ns - run[0].timestamp_ns,
            )
        )
    return tuple(candidates)


@dataclass(frozen=True, slots=True)
class RouteCompatibility:
    compatible: bool
    mean_path_error_m: float
    maximum_path_error_m: float
    start_distance_m: float
    end_distance_m: float
    path_length_ratio: float


def compute_route_compatibility(
    left: RouteCandidate,
    right: RouteCandidate,
    config: SharedMotionConfig = _DEFAULT_CONFIG,
) -> RouteCompatibility:
    """Compute exact directed-order compatibility metrics for two candidates."""
    if len(left.resampled_xy) != len(right.resampled_xy):
        raise ValidationError("route candidates use different resampling counts")
    distances = tuple(
        math.dist(a, b)
        for a, b in zip(left.resampled_xy, right.resampled_xy, strict=True)
    )
    if not distances or any(not math.isfinite(value) for value in distances):
        raise ValidationError("route compatibility produced invalid distances")
    ratio = left.path_length_m / right.path_length_m
    mean_error = sum(distances) / len(distances)
    maximum_error = max(distances)
    start = distances[0]
    end = distances[-1]
    same_group = (
        left.scenario_id == right.scenario_id
        and left.agent_class is right.agent_class
        and left.category_label is right.category_label
        and left.event_signature_json == right.event_signature_json
        and left.procedural_track_id != right.procedural_track_id
    )
    compatible = (
        same_group
        and start <= config.maximum_start_distance_m
        and end <= config.maximum_end_distance_m
        and config.minimum_path_length_ratio
        <= ratio
        <= config.maximum_path_length_ratio
        and mean_error <= config.maximum_mean_path_error_m
        and maximum_error <= config.maximum_path_error_m
    )
    return RouteCompatibility(
        compatible=compatible,
        mean_path_error_m=mean_error,
        maximum_path_error_m=maximum_error,
        start_distance_m=start,
        end_distance_m=end,
        path_length_ratio=ratio,
    )


class _CompatibilityCache:
    def __init__(self, config: SharedMotionConfig) -> None:
        self._config = config
        self._values: dict[
            tuple[tuple[str, int], tuple[str, int]], RouteCompatibility
        ] = {}

    def get(self, left: RouteCandidate, right: RouteCandidate) -> RouteCompatibility:
        low, high = sorted((left.order_key, right.order_key))
        key = (low, high)
        if key not in self._values:
            self._values[key] = compute_route_compatibility(left, right, self._config)
        return self._values[key]


def cluster_scenario(
    candidates: Sequence[RouteCandidate],
    config: SharedMotionConfig = _DEFAULT_CONFIG,
) -> tuple[tuple[RouteCandidate, ...], ...]:
    """Apply deterministic greedy complete-link clustering to one scenario."""
    ordered = tuple(sorted(candidates, key=lambda item: item.order_key))
    if ordered and len({item.scenario_id for item in ordered}) != 1:
        raise ValidationError("cluster_scenario accepts one scenario")
    cache = _CompatibilityCache(config)
    clusters: list[list[RouteCandidate]] = []
    for candidate in ordered:
        for cluster in clusters:
            if all(cache.get(candidate, member).compatible for member in cluster):
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])
    for cluster in clusters:
        if any(
            not cache.get(left, right).compatible
            for left, right in combinations(cluster, 2)
        ):
            raise ValidationError("complete-link invariant failed")
    return tuple(tuple(cluster) for cluster in clusters)


def _medoid(
    cluster: Sequence[RouteCandidate], cache: _CompatibilityCache
) -> RouteCandidate:
    def key(candidate: RouteCandidate) -> tuple[float, float, tuple[str, int], str]:
        errors = tuple(
            cache.get(candidate, other).mean_path_error_m
            for other in cluster
            if other is not candidate
        )
        maxima = tuple(
            cache.get(candidate, other).maximum_path_error_m
            for other in cluster
            if other is not candidate
        )
        return (
            sum(errors),
            max(maxima, default=0.0),
            candidate.order_key,
            candidate.procedural_track_id,
        )

    return min(cluster, key=key)


def _category_id(candidate: RouteCandidate) -> str:
    digest = canonical_sha256(
        "motion-category",
        {
            "category_label": candidate.category_label.value,
            "agent_class": candidate.agent_class.value,
            "event_signature_json": candidate.event_signature_json,
        },
    )
    return make_identifier("motion-category", digest[:32])


def _template_id(category_id: str, cluster: Sequence[RouteCandidate]) -> str:
    digest = canonical_sha256(
        "route-template",
        {
            "category_id": category_id,
            "scenario_id": cluster[0].scenario_id,
            "members": [[item.procedural_track_id, item.run_index] for item in cluster],
        },
    )
    return make_identifier("route-template", digest[:32])


def _template_error(template: RouteTemplate) -> float:
    value = json.loads(template.semantic_attributes_json)
    return float(value["mean_member_path_error_m"])


def build_shared_motion_model(
    dataset_id: str,
    dataset_version: str,
    scenarios: Sequence[ScenarioRecord],
    trajectories: Sequence[Trajectory],
    semantic_tapes: Sequence[SemanticMotionTape],
    config: SharedMotionConfig = _DEFAULT_CONFIG,
) -> SharedMotionModel:
    """Build the complete map-hidden shared-motion model."""
    scenario_by_id = {item.scenario_id: item for item in scenarios}
    trajectory_by_id = {item.trajectory_id: item for item in trajectories}
    semantic_tracks = tuple(track for tape in semantic_tapes for track in tape.tracks)
    if len(semantic_tracks) != len(trajectory_by_id):
        raise ValidationError("semantic and trajectory counts differ")
    candidates: list[RouteCandidate] = []
    categorized: dict[tuple[MotionCategoryLabel, AgentClass, str], set[str]] = {}
    zero_length: dict[tuple[MotionCategoryLabel, AgentClass, str], set[str]] = {}
    for track in semantic_tracks:
        trajectory = trajectory_by_id[track.procedural_track.trajectory_id]
        scenario = scenario_by_id[trajectory.scenario_id]
        label = classify_semantic_motion_track(track, trajectory)
        agent_class = AgentClass(track.procedural_track.agent_class)
        signature = event_signature_json(track)
        key = (label, agent_class, signature)
        categorized.setdefault(key, set()).add(
            track.procedural_track.procedural_track_id
        )
        built = build_route_candidates(scenario, trajectory, track, config)
        if not built:
            zero_length.setdefault(key, set()).add(
                track.procedural_track.procedural_track_id
            )
        candidates.extend(built)

    templates: list[RouteTemplate] = []
    memberships: list[RouteTemplateMembership] = []
    candidate_cache = _CompatibilityCache(config)
    scenario_groups: dict[str, list[RouteCandidate]] = {}
    for candidate in candidates:
        scenario_groups.setdefault(candidate.scenario_id, []).append(candidate)
    for scenario_id in sorted(scenario_groups):
        grouped: dict[
            tuple[AgentClass, MotionCategoryLabel, str], list[RouteCandidate]
        ] = {}
        for candidate in scenario_groups[scenario_id]:
            grouped.setdefault(
                (
                    candidate.agent_class,
                    candidate.category_label,
                    candidate.event_signature_json,
                ),
                [],
            ).append(candidate)
        for group_key in sorted(
            grouped,
            key=lambda item: (
                list(AgentClass).index(item[0]),
                list(MotionCategoryLabel).index(item[1]),
                item[2],
            ),
        ):
            for cluster in cluster_scenario(grouped[group_key], config):
                representative = _medoid(cluster, candidate_cache)
                category_id = _category_id(representative)
                template_id = _template_id(category_id, cluster)
                measured = [
                    (item, compute_route_compatibility(item, representative, config))
                    for item in cluster
                ]
                measured.sort(
                    key=lambda item: (
                        item[0] is not representative,
                        item[1].mean_path_error_m,
                        item[1].maximum_path_error_m,
                        item[0].order_key,
                    )
                )
                mean_errors = [item[1].mean_path_error_m for item in measured]
                start_distances = [item[1].start_distance_m for item in measured]
                end_distances = [item[1].end_distance_m for item in measured]
                attributes = {
                    "grouping_scope": "scenario_source_frame",
                    "representative_run_index": representative.run_index,
                    "resample_point_count": config.resample_point_count,
                    "shared_template": len(cluster)
                    >= config.minimum_shared_template_members,
                    "member_count": len(cluster),
                    "mean_member_path_error_m": sum(mean_errors) / len(mean_errors),
                    "maximum_member_path_error_m": max(
                        item[1].maximum_path_error_m for item in measured
                    ),
                    "mean_start_distance_m": sum(start_distances)
                    / len(start_distances),
                    "mean_end_distance_m": sum(end_distances) / len(end_distances),
                    "path_length_min_m": min(item.path_length_m for item in cluster),
                    "path_length_max_m": max(item.path_length_m for item in cluster),
                    "duration_min_ns": min(item.duration_ns for item in cluster),
                    "duration_max_ns": max(item.duration_ns for item in cluster),
                    "map_data_used_for_construction": False,
                    "internal_run_identity": "quality_flag_run_index",
                }
                geometry = LineString(representative.source_points)
                templates.append(
                    RouteTemplate(
                        template_id=template_id,
                        category_id=category_id,
                        scenario_id=scenario_id,
                        coordinate_frame_id=representative.coordinate_frame_id,
                        representative_track_id=(representative.procedural_track_id),
                        member_count=len(cluster),
                        geometry_type="LineString",
                        geometry_wkb=geometry_to_canonical_wkb(geometry),
                        path_length_m=representative.path_length_m,
                        duration_ns=representative.duration_ns,
                        event_signature_json=representative.event_signature_json,
                        semantic_attributes_json=canonical_json_text(
                            attributes, trailing_newline=False
                        ),
                        origin_type=OriginType.INFERRED,
                        quality_flags=(),
                    )
                )
                for index, (candidate, measurement) in enumerate(measured):
                    memberships.append(
                        RouteTemplateMembership(
                            template_id=template_id,
                            procedural_track_id=candidate.procedural_track_id,
                            membership_index=index,
                            scenario_id=candidate.scenario_id,
                            agent_id=candidate.agent_id,
                            trajectory_id=candidate.trajectory_id,
                            mean_path_error_m=measurement.mean_path_error_m,
                            maximum_path_error_m=measurement.maximum_path_error_m,
                            start_distance_m=measurement.start_distance_m,
                            end_distance_m=measurement.end_distance_m,
                            path_length_ratio=(
                                candidate.path_length_m / representative.path_length_m
                            ),
                            duration_ratio=(
                                candidate.duration_ns / representative.duration_ns
                            ),
                            map_route_signature_json=None,
                            origin_type=OriginType.INFERRED,
                            quality_flags=(f"run_index={candidate.run_index:06d}",),
                        )
                    )

    templates.sort(
        key=lambda item: (item.scenario_id, item.category_id, item.template_id)
    )
    memberships.sort(
        key=lambda item: (
            item.template_id,
            item.membership_index,
            item.procedural_track_id,
        )
    )
    category_records: list[MotionCategory] = []
    summary_metadata: list[str] = []
    candidate_by_key = {
        (item.category_label, item.agent_class, item.event_signature_json): item
        for item in candidates
    }
    category_keys = sorted(
        categorized,
        key=lambda item: (
            list(MotionCategoryLabel).index(item[0]),
            list(AgentClass).index(item[1]),
            item[2],
        ),
    )
    for key in category_keys:
        exemplar = candidate_by_key.get(key)
        if exemplar is None:
            digest = canonical_sha256(
                "motion-category",
                {
                    "category_label": key[0].value,
                    "agent_class": key[1].value,
                    "event_signature_json": key[2],
                },
            )
            summary_metadata.append(
                canonical_json_text(
                    {
                        "category_id": make_identifier("motion-category", digest[:32]),
                        "category_label": key[0].value,
                        "agent_class": key[1].value,
                        "event_signature_json": key[2],
                        "zero_length_track_ids": sorted(zero_length.get(key, set())),
                        "route_geometry_fabricated": False,
                    },
                    trailing_newline=False,
                )
            )
            continue
        category_id = _category_id(exemplar)
        category_templates = [
            item for item in templates if item.category_id == category_id
        ]
        category_representative = min(
            category_templates,
            key=lambda item: (
                -item.member_count,
                _template_error(item),
                templates.index(item),
            ),
        )
        category_records.append(
            MotionCategory(
                category_id=category_id,
                category_label=key[0],
                agent_class=key[1],
                event_signature_json=key[2],
                representative_template_id=category_representative.template_id,
                template_count=len(category_templates),
                track_count=len(categorized[key]),
                origin_type=OriginType.INFERRED,
                quality_flags=(),
            )
        )
        if zero_length.get(key):
            summary_metadata.append(
                canonical_json_text(
                    {
                        "category_id": category_id,
                        "zero_length_track_ids": sorted(zero_length[key]),
                        "route_geometry_fabricated": False,
                    },
                    trailing_newline=False,
                )
            )
    category_records.sort(
        key=lambda item: (
            list(MotionCategoryLabel).index(MotionCategoryLabel(item.category_label)),
            list(AgentClass).index(AgentClass(item.agent_class)),
            item.event_signature_json,
            item.category_id,
        )
    )
    first_tape = semantic_tapes[0]
    codec_identity = first_tape.procedural_tape.encoder_parameters_identity
    detector_identity = first_tape.detector_configuration_identity
    validation_identity = first_tape.source_validation_identity or "validation:none"
    if any(
        tape.procedural_tape.encoder_parameters_identity != codec_identity
        or tape.detector_configuration_identity != detector_identity
        or (tape.source_validation_identity or "validation:none") != validation_identity
        for tape in semantic_tapes
    ):
        raise ValidationError("input provenance identities differ")
    return SharedMotionModel(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source_validation_identity=validation_identity,
        procedural_codec_identity=codec_identity,
        semantic_detector_identity=detector_identity,
        grouping_configuration_identity=shared_motion_configuration_identity(config),
        categories=tuple(category_records),
        route_templates=tuple(templates),
        memberships=tuple(memberships),
        category_summary_metadata=tuple(sorted(summary_metadata)),
    )


@dataclass(frozen=True, slots=True)
class MapEvaluation:
    evaluated_model: SharedMotionModel
    template_metrics: tuple[str, ...]


def _run_index(membership: RouteTemplateMembership) -> int:
    for flag in membership.quality_flags:
        if flag.startswith("run_index="):
            return int(flag.split("=", 1)[1])
    raise ValidationError("membership omits internal run identity")


def _lane_signature(
    candidate: RouteCandidate,
    lanes: Sequence[tuple[str, object]],
    maximum_distance: float,
) -> tuple[str | None, tuple[float, ...]]:
    matched: list[str] = []
    distances: list[float] = []
    for xy in candidate.resampled_xy:
        point = Point(xy)
        choices = sorted(
            ((float(point.distance(geometry)), lane_id) for lane_id, geometry in lanes),
            key=lambda item: (item[0], item[1]),
        )
        if choices and choices[0][0] <= maximum_distance:
            distance, lane_id = choices[0]
            distances.append(distance)
            if not matched or matched[-1] != lane_id:
                matched.append(lane_id)
    if not matched:
        return None, tuple(distances)
    return (
        canonical_json_text({"lane_ids": matched}, trailing_newline=False),
        tuple(distances),
    )


def _edit_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    if not left and not right:
        return 1.0
    previous = list(range(len(right) + 1))
    for i, left_value in enumerate(left, start=1):
        current = [i]
        for j, right_value in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left_value != right_value),
                )
            )
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


def evaluate_shared_motion_map(
    model: SharedMotionModel,
    candidates: Sequence[RouteCandidate],
    map_elements: Sequence[VectorMapElementRecord],
    scenarios: Sequence[ScenarioRecord],
    config: SharedMotionConfig = _DEFAULT_CONFIG,
) -> MapEvaluation:
    """Evaluate a frozen model against maps without changing its construction."""
    candidate_by_key = {
        (item.procedural_track_id, item.run_index): item for item in candidates
    }
    scenario_by_id = {item.scenario_id: item for item in scenarios}
    if set(item.scenario_id for item in map_elements) - set(scenario_by_id):
        raise ValidationError("map element scenario reference does not resolve")
    lanes_by_scenario: dict[str, list[tuple[str, object]]] = {}
    for element in map_elements:
        if element.element_type is MapElementType.LANE_CENTERLINE:
            scenario = scenario_by_id[element.scenario_id]
            lanes_by_scenario.setdefault(element.scenario_id, []).append(
                (
                    element.map_element_id,
                    translate(
                        geometry_from_canonical_wkb(element.geometry_wkb),
                        xoff=scenario.origin_x_m,
                        yoff=scenario.origin_y_m,
                        zoff=(
                            scenario.origin_z_m
                            if scenario.origin_z_m is not None
                            else 0.0
                        ),
                    ),
                )
            )
    evaluated_memberships: list[RouteTemplateMembership] = []
    matched_distances: dict[str, list[float]] = {}
    for membership in model.memberships:
        candidate = candidate_by_key[
            (membership.procedural_track_id, _run_index(membership))
        ]
        signature, distances = _lane_signature(
            candidate,
            lanes_by_scenario.get(membership.scenario_id, ()),
            config.map_match_maximum_distance_m,
        )
        matched_distances.setdefault(membership.template_id, []).extend(distances)
        evaluated_memberships.append(
            replace(membership, map_route_signature_json=signature)
        )
    evaluated = replace(model, memberships=tuple(evaluated_memberships))
    metrics: list[str] = []
    for template in model.route_templates:
        members = [
            item
            for item in evaluated.memberships
            if item.template_id == template.template_id
            and item.map_route_signature_json is not None
        ]
        sequences = [
            tuple(json.loads(item.map_route_signature_json)["lane_ids"])
            for item in members
            if item.map_route_signature_json is not None
        ]
        counts = Counter(sequences)
        dominant = min(
            (
                sequence
                for sequence, count in counts.items()
                if count == max(counts.values())
            ),
            default=(),
        )
        first = Counter(sequence[0] for sequence in sequences)
        final = Counter(sequence[-1] for sequence in sequences)
        template_distances = matched_distances.get(template.template_id, [])
        metrics.append(
            canonical_json_text(
                {
                    "template_id": template.template_id,
                    "usable_lane_signature_count": len(sequences),
                    "dominant_exact_lane_sequence_count": counts.get(dominant, 0),
                    "exact_lane_sequence_purity": (
                        counts.get(dominant, 0) / len(sequences) if sequences else None
                    ),
                    "dominant_first_lane_purity": (
                        max(first.values()) / len(sequences) if sequences else None
                    ),
                    "dominant_final_lane_purity": (
                        max(final.values()) / len(sequences) if sequences else None
                    ),
                    "relaxed_ordered_lane_similarity": (
                        sum(
                            _edit_similarity(sequence, dominant)
                            for sequence in sequences
                        )
                        / len(sequences)
                        if sequences
                        else None
                    ),
                    "mean_matched_point_distance_m": (
                        sum(template_distances) / len(template_distances)
                        if template_distances
                        else None
                    ),
                    "maximum_matched_point_distance_m": (
                        max(template_distances) if template_distances else None
                    ),
                },
                trailing_newline=False,
            )
        )
    return MapEvaluation(evaluated_model=evaluated, template_metrics=tuple(metrics))
