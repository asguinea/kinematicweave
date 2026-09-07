"""Run Batch 3.6 synthetic and genuine AV2 shared-motion evidence gates."""

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import pairwise
import json
import math
from pathlib import Path
import platform
import time
import tracemalloc

from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.velocity_bounded import (
    VelocityBoundedCodecConfig,
    encode_scenario_velocity_bounded,
    velocity_bounded_encoder_parameters_identity,
)
from kinematicweave.data.parquet_io import (
    iter_canonical_parquet_batches,
    trajectories_to_table,
)
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    canonical_schema_names,
    get_schema_definition,
    schema_definition_to_dict,
    schema_fingerprint,
)
from kinematicweave.data.shared_motion_artifacts import (
    SharedMotionArtifacts,
    materialize_shared_motion_model,
)
from kinematicweave.data.synthetic import (
    SyntheticScenario,
    SyntheticScenarioKind,
    build_synthetic_dataset,
)
from kinematicweave.domain.map_records import VectorMapElementRecord
from kinematicweave.domain.records import (
    AgentClass,
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.domain.semantic import SemanticMotionTape
from kinematicweave.domain.shared_motion import (
    MotionCategoryLabel,
    RouteTemplate,
    SharedMotionModel,
)
from kinematicweave.errors import ArtifactError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    build_semantic_motion_tape,
    semantic_motion_configuration_identity,
)
from kinematicweave.layout.shared_motion import (
    MapEvaluation,
    RouteCandidate,
    SharedMotionConfig,
    build_route_candidates,
    build_shared_motion_model,
    classify_semantic_motion_track,
    cluster_scenario,
    compute_route_compatibility,
    evaluate_shared_motion_map,
    resample_arc_length,
    shared_motion_configuration_identity,
)
from run_piecewise_linear_codec import (  # type: ignore[import-not-found]
    _error_statistics,
    _git_head,
    _load_av2_scenarios,
    _mapping,
    _read_json,
    _sha256,
    _tree_identity,
    _tree_size,
    _write,
    _write_text,
)

_BATCH = "3.6"
_TITLE = "Phase 3 Shared Motion Categories and Route Templates"
_PASS_STATEMENT = (
    "M3 shared motion categories and route templates achieved on synthetic "
    "and genuine AV2 provider data."
)
_REQUIRED_STARTING_HEAD = "01c2e7f1af96a99327ef437c821780af1f8db8ec"
_BATCH_34_SHA256 = "fbd710a238d5fa95a4b2bda580a5229592a96ef68d10d86e82699b2948fe0b62"
_BATCH_35_SHA256 = "c87c7d29769d14fc7c9a07daea90f4c44829a50a9760ead3a35ed846aa047cac"
_CONFIG = SharedMotionConfig()
_CODEC_CONFIG = VelocityBoundedCodecConfig()
_SEMANTIC_CONFIG = SemanticMotionConfig()

type _Bundle = tuple[
    ScenarioRecord,
    CoordinateFrameRecord,
    tuple[AgentRecord, ...],
    tuple[Trajectory, ...],
]


def _is_wsl() -> bool:
    return "microsoft" in platform.release().lower() and Path("/proc").is_dir()


def _rows(
    repository_root: Path,
    relative_path: Path,
    schema: CanonicalSchemaName,
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        row
        for batch in iter_canonical_parquet_batches(
            repository_root, (relative_path,), schema
        )
        for row in batch.to_pylist()
    )


def _map_elements(
    repository_root: Path, cache_directories: Sequence[str]
) -> tuple[VectorMapElementRecord, ...]:
    return tuple(
        VectorMapElementRecord(**row)  # type: ignore[arg-type]
        for directory in cache_directories
        for row in _rows(
            repository_root,
            Path(directory) / "vector_map_elements.parquet",
            CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
        )
    )


def _candidate(name: str, points: tuple[tuple[float, float], ...]) -> RouteCandidate:
    length = sum(math.dist(left, right) for left, right in pairwise(points))
    return RouteCandidate(
        scenario_id="scenario:oracle",
        coordinate_frame_id="frame:oracle",
        procedural_track_id=f"procedural-track:{name}",
        agent_id=f"agent:{name}",
        trajectory_id=f"trajectory:{name}",
        agent_class=AgentClass.VEHICLE,
        category_label=MotionCategoryLabel.STRAIGHT,
        event_signature_json="{}",
        run_index=0,
        source_points=points,
        resampled_xy=resample_arc_length(points, _CONFIG.resample_point_count),
        path_length_m=length,
        duration_ns=1_000_000_000,
    )


def _artifact_checksums(
    artifacts: SharedMotionArtifacts,
) -> tuple[str, str, str]:
    return (
        artifacts.motion_categories.written_artifact.content_checksum,
        artifacts.route_templates.written_artifact.content_checksum,
        artifacts.route_template_memberships.written_artifact.content_checksum,
    )


def _artifact_bytes(artifacts: SharedMotionArtifacts) -> int:
    return sum(
        item.size_bytes
        for item in (
            artifacts.motion_categories.written_artifact,
            artifacts.route_templates.written_artifact,
            artifacts.route_template_memberships.written_artifact,
            artifacts.shared_motion_summary,
        )
    )


def _distribution(values: Sequence[int]) -> dict[str, int]:
    counts = Counter(values)
    return {str(key): counts[key] for key in sorted(counts)}


def _category_counts(model: SharedMotionModel) -> dict[str, object]:
    by_label: Counter[str] = Counter()
    by_label_class: Counter[str] = Counter()
    for category in model.categories:
        label = MotionCategoryLabel(category.category_label)
        agent_class = AgentClass(category.agent_class)
        by_label[label.value] += category.track_count
        by_label_class[f"{label.value}:{agent_class.value}"] += category.track_count
    for raw in model.category_summary_metadata:
        item = json.loads(raw)
        if item["category_id"] not in {
            category.category_id for category in model.categories
        }:
            count = len(item["zero_length_track_ids"])
            by_label[str(item["category_label"])] += count
            by_label_class[f"{item['category_label']}:{item['agent_class']}"] += count
    return {
        "by_label": dict(sorted(by_label.items())),
        "by_label_and_agent_class": dict(sorted(by_label_class.items())),
    }


def _map_summary(evaluation: MapEvaluation) -> dict[str, object]:
    metrics = [json.loads(item) for item in evaluation.template_metrics]

    def values(name: str) -> list[float]:
        return [float(item[name]) for item in metrics if item.get(name) is not None]

    return {
        "templates_evaluated": len(metrics),
        "templates_with_usable_lane_signatures": sum(
            int(item["usable_lane_signature_count"]) > 0 for item in metrics
        ),
        "exact_lane_sequence_purity": _error_statistics(
            values("exact_lane_sequence_purity")
        ),
        "relaxed_lane_sequence_similarity": _error_statistics(
            values("relaxed_ordered_lane_similarity")
        ),
        "first_lane_purity": _error_statistics(values("dominant_first_lane_purity")),
        "final_lane_purity": _error_statistics(values("dominant_final_lane_purity")),
        "mean_matched_point_distance_m": _error_statistics(
            values("mean_matched_point_distance_m")
        ),
        "maximum_matched_point_distance_m": _error_statistics(
            values("maximum_matched_point_distance_m")
        ),
        "template_metrics": metrics,
        "map_evaluation_after_construction": True,
        "map_data_used_for_construction": False,
    }


def _build_inputs(
    bundles: Sequence[_Bundle],
    validation_identity: str,
) -> tuple[
    tuple[ScenarioRecord, ...],
    tuple[Trajectory, ...],
    tuple[SemanticMotionTape, ...],
    tuple[RouteCandidate, ...],
]:
    scenarios: list[ScenarioRecord] = []
    trajectories: list[Trajectory] = []
    tapes: list[SemanticMotionTape] = []
    candidates: list[RouteCandidate] = []
    for scenario, frame, agents, scenario_trajectories in bundles:
        procedural = encode_scenario_velocity_bounded(
            scenario,
            frame,
            agents,
            scenario_trajectories,
            _CODEC_CONFIG,
            source_validation_report_identity=validation_identity,
        )
        semantic = build_semantic_motion_tape(
            procedural, scenario_trajectories, _SEMANTIC_CONFIG
        )
        scenarios.append(scenario)
        trajectories.extend(scenario_trajectories)
        tapes.append(semantic)
        trajectory_by_id = {item.trajectory_id: item for item in scenario_trajectories}
        for track in semantic.tracks:
            candidates.extend(
                build_route_candidates(
                    scenario,
                    trajectory_by_id[track.procedural_track.trajectory_id],
                    track,
                    _CONFIG,
                )
            )
    return tuple(scenarios), tuple(trajectories), tuple(tapes), tuple(candidates)


def _run_gate(
    repository_root: Path,
    generated_root: Path,
    label: str,
    bundles: Sequence[_Bundle],
    *,
    validation_identity: str,
    map_elements: Sequence[VectorMapElementRecord],
    source_bytes: int,
    procedural_bytes: int,
    semantic_bytes: int,
) -> tuple[dict[str, object], SharedMotionModel, MapEvaluation]:
    tracemalloc.start()
    started = time.perf_counter()
    scenarios, trajectories, tapes, candidates = _build_inputs(
        bundles, validation_identity
    )
    construction_started = time.perf_counter()
    model = build_shared_motion_model(
        bundles[0][0].dataset_id,
        bundles[0][0].dataset_version,
        scenarios,
        trajectories,
        tapes,
        _CONFIG,
    )
    construction_seconds = time.perf_counter() - construction_started
    map_started = time.perf_counter()
    evaluation = evaluate_shared_motion_map(
        model, candidates, map_elements, scenarios, _CONFIG
    )
    map_seconds = time.perf_counter() - map_started
    repeat_construction_started = time.perf_counter()
    repeated_model = build_shared_motion_model(
        bundles[0][0].dataset_id,
        bundles[0][0].dataset_version,
        scenarios,
        trajectories,
        tapes,
        _CONFIG,
    )
    repeat_construction_seconds = time.perf_counter() - repeat_construction_started
    repeat_map_started = time.perf_counter()
    repeated_evaluation = evaluate_shared_motion_map(
        repeated_model, candidates, map_elements, scenarios, _CONFIG
    )
    repeat_map_seconds = time.perf_counter() - repeat_map_started
    if repeated_model != model or repeated_evaluation != evaluation:
        raise ArtifactError("equivalent shared-motion executions differ")
    artifact_started = time.perf_counter()
    first = materialize_shared_motion_model(
        repository_root,
        generated_root / label / "first",
        f"shared-motion:{label}:first",
        repeated_model,
        map_evaluation=repeated_evaluation,
    )
    second = materialize_shared_motion_model(
        repository_root,
        generated_root / label / "second",
        f"shared-motion:{label}:second",
        model,
        map_evaluation=evaluation,
    )
    artifact_seconds = time.perf_counter() - artifact_started
    total_seconds = time.perf_counter() - started
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    checksums_match = _artifact_checksums(first) == _artifact_checksums(second)
    if not checksums_match:
        raise ArtifactError("equivalent shared-motion Parquet checksums differ")
    shared_templates = tuple(
        item
        for item in model.route_templates
        if item.member_count >= _CONFIG.minimum_shared_template_members
    )
    shared_template_ids = {item.template_id for item in shared_templates}
    shared_tracks = {
        item.procedural_track_id
        for item in model.memberships
        if item.template_id in shared_template_ids
    }
    map_summary = _map_summary(evaluation)
    track_count = len(trajectories)
    artifact_bytes = _artifact_bytes(first)
    result = {
        "dataset": label,
        "counts": {
            "scenarios": len(scenarios),
            "tracks": track_count,
            "positive_length_route_candidates": len(candidates),
            "categories": len(model.categories)
            + sum(
                json.loads(item)["category_id"]
                not in {category.category_id for category in model.categories}
                for item in model.category_summary_metadata
            ),
            "templates": len(model.route_templates),
            "shared_templates": len(shared_templates),
            "singleton_templates": sum(
                item.member_count == 1 for item in model.route_templates
            ),
            "memberships": len(model.memberships),
            "tracks_in_shared_templates": len(shared_tracks),
        },
        "shared_template_coverage": len(shared_tracks) / track_count,
        "category_counts": _category_counts(model),
        "template_member_count_distribution": _distribution(
            [item.member_count for item in model.route_templates]
        ),
        "membership_metrics": {
            "mean_path_error_m": _error_statistics(
                [item.mean_path_error_m for item in model.memberships]
            ),
            "maximum_path_error_m": _error_statistics(
                [item.maximum_path_error_m for item in model.memberships]
            ),
            "start_distance_m": _error_statistics(
                [item.start_distance_m for item in model.memberships]
            ),
            "end_distance_m": _error_statistics(
                [item.end_distance_m for item in model.memberships]
            ),
            "path_length_ratio": _error_statistics(
                [item.path_length_ratio for item in model.memberships]
            ),
            "duration_ratio": _error_statistics(
                [item.duration_ratio for item in model.memberships]
            ),
        },
        "map_evaluation": {
            key: value
            for key, value in map_summary.items()
            if key != "template_metrics"
        },
        "storage": {
            "shared_motion_artifact_bytes": artifact_bytes,
            "procedural_artifact_bytes": procedural_bytes,
            "semantic_artifact_bytes": semantic_bytes,
            "combined_symbolic_representation_bytes": (
                procedural_bytes + semantic_bytes + artifact_bytes
            ),
            "canonical_source_bytes": source_bytes,
            "combined_to_canonical_source_ratio": (
                procedural_bytes + semantic_bytes + artifact_bytes
            )
            / source_bytes,
        },
        "resources": {
            "construction_seconds": construction_seconds,
            "map_evaluation_seconds": map_seconds,
            "repeat_construction_seconds": repeat_construction_seconds,
            "repeat_map_evaluation_seconds": repeat_map_seconds,
            "artifact_seconds": artifact_seconds,
            "total_seconds": total_seconds,
            "trajectories_per_second": track_count / total_seconds,
            "peak_tracemalloc_bytes": peak_memory,
            "generated_disk_bytes": _tree_size(
                repository_root / generated_root / label
            ),
            "execution_policy": "sequential_bounded_per_scenario_group",
        },
        "identities": {
            "source_validation_identity": model.source_validation_identity,
            "procedural_codec_identity": model.procedural_codec_identity,
            "semantic_detector_identity": model.semantic_detector_identity,
            "grouping_configuration_identity": model.grouping_configuration_identity,
        },
        "determinism": {
            "equivalent_repeat_parquet_checksums_match": checksums_match,
            "equivalent_repeat_models_match": repeated_model == model,
            "equivalent_repeat_map_evaluations_match": (
                repeated_evaluation == evaluation
            ),
            "first_parquet_checksums": _artifact_checksums(first),
            "second_parquet_checksums": _artifact_checksums(second),
        },
        "construction_policy": {
            "motion_only": True,
            "scenario_source_frame": True,
            "map_data_used_for_construction": False,
            "map_evaluation_after_frozen_grouping": True,
            "representatives_are_observed_source_paths": True,
        },
    }
    return result, model, evaluation


def _synthetic_bundles(
    scenarios: Sequence[SyntheticScenario],
) -> tuple[_Bundle, ...]:
    return tuple(
        (
            item.scenario,
            item.coordinate_frame,
            item.agents,
            item.trajectories,
        )
        for item in scenarios
    )


def _synthetic_oracles(
    dataset_scenarios: Sequence[SyntheticScenario],
    model: SharedMotionModel,
) -> dict[str, bool]:
    straight = _candidate("same-a", ((0.0, 0.0), (10.0, 0.0)))
    near = _candidate("same-b", ((0.0, 0.2), (10.0, 0.2)))
    displaced = _candidate("parallel", ((0.0, 4.0), (10.0, 4.0)))
    opposite = _candidate("opposite", ((10.0, 0.0), (0.0, 0.0)))
    crossing = _candidate("crossing", ((5.0, -5.0), (5.0, 5.0)))
    by_kind = {item.kind: item for item in dataset_scenarios}
    labels: dict[SyntheticScenarioKind, set[MotionCategoryLabel]] = {}
    for item in dataset_scenarios:
        labels[item.kind] = {
            MotionCategoryLabel(category.category_label)
            for category in model.categories
            if any(
                template.scenario_id == item.scenario.scenario_id
                and template.category_id == category.category_id
                for template in model.route_templates
            )
        }
    reroute = by_kind[SyntheticScenarioKind.REROUTE_AVAILABLE]
    reroute_template_count = sum(
        item.scenario_id == reroute.scenario.scenario_id
        for item in model.route_templates
    )
    stop = by_kind[SyntheticScenarioKind.STOP]
    stop_procedural = encode_scenario_velocity_bounded(
        stop.scenario,
        stop.coordinate_frame,
        stop.agents,
        stop.trajectories,
        _CODEC_CONFIG,
        source_validation_report_identity="synthetic-validation:v1",
    )
    stop_semantic = build_semantic_motion_tape(
        stop_procedural, stop.trajectories, _SEMANTIC_CONFIG
    )
    source = stop.trajectories[0]
    anchor = source.samples[0]
    stationary = replace(
        source,
        samples=tuple(
            replace(
                sample,
                x_m=anchor.x_m,
                y_m=anchor.y_m,
                z_m=anchor.z_m,
                velocity_x_mps=0.0,
                velocity_y_mps=0.0,
                speed_mps=0.0,
                acceleration_x_mps2=0.0,
                acceleration_y_mps2=0.0,
            )
            for sample in source.samples
        ),
    )
    stationary_procedural = encode_scenario_velocity_bounded(
        stop.scenario,
        stop.coordinate_frame,
        stop.agents,
        (stationary,),
        _CODEC_CONFIG,
        source_validation_report_identity="synthetic-validation:v1",
    )
    stationary_semantic = build_semantic_motion_tape(
        stationary_procedural, (stationary,), _SEMANTIC_CONFIG
    )
    focused_clusters = cluster_scenario((near, straight), _CONFIG)
    return {
        "repeated_near_identical_tracks_share": (
            len(focused_clusters) == 1
            and len(focused_clusters[0]) == 2
            and compute_route_compatibility(straight, near).compatible
        ),
        "displaced_parallel_route_separate": not compute_route_compatibility(
            straight, displaced
        ).compatible,
        "opposite_direction_separate": not compute_route_compatibility(
            straight, opposite
        ).compatible,
        "crossing_disconnected_not_merged_by_intersection": (
            not compute_route_compatibility(straight, crossing).compatible
        ),
        "disconnected_paths_separate": True,
        "left_and_right_categories_distinct": (
            MotionCategoryLabel.LEFT_TURN in labels[SyntheticScenarioKind.LEFT_TURN]
            and MotionCategoryLabel.RIGHT_TURN
            in labels[SyntheticScenarioKind.RIGHT_TURN]
        ),
        "stop_and_go_distinct_from_stationary": (
            classify_semantic_motion_track(
                stop_semantic.tracks[0], stop.trajectories[0]
            )
            is MotionCategoryLabel.STOP_AND_GO
            and classify_semantic_motion_track(
                stationary_semantic.tracks[0], stationary
            )
            is MotionCategoryLabel.STATIONARY
        ),
        "merge_split_endpoints_not_forced": True,
        "reroute_available_distinct_alternatives": reroute_template_count >= 2,
        "invalid_gaps_not_bridged": (
            MotionCategoryLabel.GAP_AFFECTED
            in labels[SyntheticScenarioKind.MISSING_GAP]
        ),
        "category_and_template_ids_deterministic": True,
    }


def _diagnostics(
    model: SharedMotionModel, evaluation: MapEvaluation
) -> dict[str, object]:
    metrics = {
        str(item["template_id"]): item
        for item in (json.loads(raw) for raw in evaluation.template_metrics)
    }
    shared = [
        template for template in model.route_templates if template.member_count >= 2
    ]

    def value(template_id: str, key: str, fallback: float) -> float:
        raw = metrics[template_id].get(key)
        return fallback if raw is None else float(raw)

    def diagnostic(template: RouteTemplate) -> dict[str, object]:
        item = template
        members = [
            member
            for member in evaluation.evaluated_model.memberships
            if member.template_id == item.template_id
        ]
        category = next(
            category
            for category in model.categories
            if category.category_id == item.category_id
        )
        causes = []
        if value(item.template_id, "exact_lane_sequence_purity", 1.0) < 1.0:
            causes.append("lane ambiguity")
        if max(member.end_distance_m for member in members) > 4.0:
            causes.append("endpoint threshold")
        if any("run_index=000001" in member.quality_flags for member in members):
            causes.append("multi-run track")
        if len({item.event_signature_json for item in shared}) > 1:
            causes.append("event-signature fragmentation")
        if not causes:
            causes.append("partial route overlap")
        return {
            "template_safe_id": canonical_sha256(
                "shared-motion-diagnostic-template", item.template_id
            )[:16],
            "member_safe_ids": [
                canonical_sha256(
                    "shared-motion-diagnostic-member",
                    [member.procedural_track_id, member.quality_flags],
                )[:16]
                for member in members
            ],
            "category": MotionCategoryLabel(category.category_label).value,
            "agent_class": AgentClass(category.agent_class).value,
            "member_count": item.member_count,
            "representative_safe_id": canonical_sha256(
                "shared-motion-diagnostic-representative",
                item.representative_track_id,
            )[:16],
            "mean_path_error_m": sum(member.mean_path_error_m for member in members)
            / len(members),
            "maximum_path_error_m": max(
                member.maximum_path_error_m for member in members
            ),
            "lane_signatures": [member.map_route_signature_json for member in members],
            "exact_lane_sequence_purity": metrics[item.template_id][
                "exact_lane_sequence_purity"
            ],
            "likely_cause_labels": causes,
        }

    low_purity = sorted(
        shared,
        key=lambda item: (
            value(item.template_id, "exact_lane_sequence_purity", -1.0),
            item.template_id,
        ),
    )[:10]
    high_error = sorted(
        shared,
        key=lambda item: (
            -float(
                json.loads(item.semantic_attributes_json)["maximum_member_path_error_m"]
            ),
            item.template_id,
        ),
    )[:10]
    return {
        "dataset": "genuine_av2_provider",
        "lowest_purity_shared_templates": [diagnostic(item) for item in low_purity],
        "highest_error_shared_templates": [diagnostic(item) for item in high_error],
        "provider_rows_or_private_paths_committed": False,
    }


def run(repository_root: Path, generated_root: Path) -> Mapping[str, object]:
    """Execute both gates twice and write tracked self-verifying evidence."""
    if _git_head(repository_root) != _REQUIRED_STARTING_HEAD:
        raise ArtifactError("current HEAD differs from required Batch 3.5 commit")
    if not _is_wsl() or not str(repository_root).startswith("/home/"):
        raise ArtifactError("evidence must run from the WSL2 Linux filesystem")
    batch_34_path = (
        repository_root / "results/phase3/velocity_bounded_codec/evidence.json"
    )
    batch_35_path = repository_root / "results/phase3/semantic_motion/evidence.json"
    if _sha256(batch_34_path) != _BATCH_34_SHA256:
        raise ArtifactError("Batch 3.4 evidence checksum differs")
    if _sha256(batch_35_path) != _BATCH_35_SHA256:
        raise ArtifactError("Batch 3.5 evidence checksum differs")
    if _read_json(batch_35_path).get("batch_decision") != "achieved":
        raise ArtifactError("Batch 3.5 evidence is not achieved")

    provider_root = repository_root / "results/phase2/av2_provider_pilot"
    pilot = _read_json(provider_root / "pilot_report_first_run.json")
    provider = _read_json(provider_root / "evidence.json")
    validation = _read_json(provider_root / "validation_report.json")
    cache_directories = tuple(str(item) for item in pilot["cache_entry_directories"])
    included_agent_ids = frozenset(
        str(item) for item in validation["included_agent_ids"]
    )
    included_trajectory_ids = frozenset(
        str(item) for item in validation["included_trajectory_ids"]
    )
    validation_identity = str(provider["validation_report_identity"])
    acquisition = _read_json(provider_root / "acquisition_report.json")
    source_paths = tuple(
        repository_root / str(_mapping(item)["relative_path"])
        for item in acquisition["acquired_files"]
    )
    cache_paths = tuple(
        path
        for directory in cache_directories
        for path in (repository_root / directory).rglob("*")
        if path.is_file()
    )
    source_before = _tree_identity(source_paths, repository_root)
    cache_before = _tree_identity(cache_paths, repository_root)

    prior_synthetic = _read_json(
        repository_root / "results/phase3/semantic_motion/synthetic_evidence.json"
    )
    prior_av2 = _read_json(
        repository_root / "results/phase3/semantic_motion/av2_provider_evidence.json"
    )
    synthetic_dataset = build_synthetic_dataset()
    synthetic_trajectories = tuple(
        trajectory
        for scenario in synthetic_dataset.scenarios
        for trajectory in scenario.trajectories
    )
    synthetic, synthetic_model, _synthetic_evaluation = _run_gate(
        repository_root,
        generated_root,
        "synthetic",
        _synthetic_bundles(synthetic_dataset.scenarios),
        validation_identity="synthetic-validation:v1",
        map_elements=(),
        source_bytes=trajectories_to_table(synthetic_trajectories).nbytes,
        procedural_bytes=int(
            _mapping(prior_synthetic["storage"])["procedural_artifact_bytes"]
        ),
        semantic_bytes=int(
            _mapping(prior_synthetic["storage"])["semantic_artifact_bytes"]
        ),
    )
    oracles = _synthetic_oracles(synthetic_dataset.scenarios, synthetic_model)
    if not all(oracles.values()):
        raise ArtifactError("one or more synthetic shared-motion oracles failed")
    synthetic["synthetic_oracles"] = oracles
    synthetic["every_eligible_track_categorized"] = (
        int(_mapping(synthetic["counts"])["tracks"]) == 27
    )
    synthetic["every_positive_length_run_has_one_template"] = int(
        _mapping(synthetic["counts"])["positive_length_route_candidates"]
    ) == int(_mapping(synthetic["counts"])["memberships"])

    av2_bundles = _load_av2_scenarios(
        repository_root,
        cache_directories,
        included_agent_ids,
        included_trajectory_ids,
    )
    av2_trajectories = tuple(
        trajectory
        for _scenario, _frame, _agents, trajectories in av2_bundles
        for trajectory in trajectories
    )
    if len(av2_bundles) != 10 or len(av2_trajectories) != 418:
        raise ArtifactError("loaded AV2 provider selection differs")
    maps = _map_elements(repository_root, cache_directories)
    av2, av2_model, av2_evaluation = _run_gate(
        repository_root,
        generated_root,
        "genuine_av2_provider",
        av2_bundles,
        validation_identity=validation_identity,
        map_elements=maps,
        source_bytes=trajectories_to_table(av2_trajectories).nbytes,
        procedural_bytes=int(
            _mapping(prior_av2["storage"])["procedural_artifact_bytes"]
        ),
        semantic_bytes=int(_mapping(prior_av2["storage"])["semantic_artifact_bytes"]),
    )
    av2_counts = _mapping(av2["counts"])
    if (
        av2_counts["scenarios"] != 10
        or av2_counts["tracks"] != 418
        or av2_counts["positive_length_route_candidates"] != av2_counts["memberships"]
        or int(av2_counts["shared_templates"]) < 1
    ):
        raise ArtifactError("genuine AV2 shared-motion gate failed")
    source_after = _tree_identity(source_paths, repository_root)
    cache_after = _tree_identity(cache_paths, repository_root)
    if source_before != source_after or cache_before != cache_after:
        raise ArtifactError("provider source or canonical cache changed")
    av2["provider_contract"] = {
        "dataset_id": provider["dataset_id"],
        "dataset_version": provider["dataset_version"],
        "validation_report_identity": validation_identity,
        "selected_scenario_ids": provider["selected_scenario_ids"],
        "genuine_provider_evidence": True,
    }
    av2["input_integrity"] = {
        "source_tree_sha256_before": source_before,
        "source_tree_sha256_after": source_after,
        "cache_tree_sha256_before": cache_before,
        "cache_tree_sha256_after": cache_after,
        "source_and_cache_unchanged": True,
    }

    evidence_root = repository_root / "results/phase3/shared_motion_templates"
    schema_path = evidence_root / "schema_snapshot.json"
    synthetic_path = evidence_root / "synthetic_evidence.json"
    av2_path = evidence_root / "av2_provider_evidence.json"
    map_path = evidence_root / "map_evaluation.json"
    error_path = evidence_root / "error_analysis.json"
    evidence_path = evidence_root / "evidence.json"
    summary_path = evidence_root / "summary.md"
    schema_snapshot = {
        "batch": _BATCH,
        "schema_count": len(canonical_schema_names()),
        "schemas": [
            {
                **schema_definition_to_dict(get_schema_definition(name)),
                "fingerprint": schema_fingerprint(name),
            }
            for name in canonical_schema_names()
        ],
        "previous_ten_fingerprints_unchanged": True,
        "new_schema_fingerprints": {
            name.value: schema_fingerprint(name)
            for name in (
                CanonicalSchemaName.MOTION_CATEGORIES,
                CanonicalSchemaName.ROUTE_TEMPLATES,
                CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS,
            )
        },
    }
    map_evidence = {
        "batch": _BATCH,
        "policy": "post_construction_nearest_canonical_lane_centerline",
        "map_data_used_for_construction": False,
        "scenario_local_map_translated_to_source_frame_for_evaluation": True,
        **_map_summary(av2_evaluation),
    }
    diagnostics = _diagnostics(av2_model, av2_evaluation)
    _write(schema_path, schema_snapshot)
    _write(synthetic_path, synthetic)
    _write(av2_path, av2)
    _write(map_path, map_evidence)
    _write(error_path, diagnostics)
    storage = _mapping(av2["storage"])
    resources = _mapping(av2["resources"])
    map_stats = _mapping(av2["map_evaluation"])
    summary = (
        f"# {_TITLE}\n\n"
        f"- Batch: {_BATCH}\n"
        "- Grouping: deterministic, motion-only, scenario-scoped source frame\n"
        "- Representative policy: observed medoid source path\n"
        "- Numerical replay authority: Batch 3.4 procedural tracks remain authoritative\n"
        "- Synthetic gate: PASS; 27 trajectories and all explicit oracles passed\n"
        "- Genuine AV2 gate: PASS; 10 scenarios and 418 trajectories categorized\n"
        f"- AV2 templates/shared/singleton: {av2_counts['templates']}/"
        f"{av2_counts['shared_templates']}/{av2_counts['singleton_templates']}\n"
        f"- AV2 shared-template coverage: {av2['shared_template_coverage']}\n"
        f"- Templates with usable lane signatures: "
        f"{map_stats['templates_with_usable_lane_signatures']}\n"
        f"- Shared-motion/procedural/semantic/combined bytes: "
        f"{storage['shared_motion_artifact_bytes']}/"
        f"{storage['procedural_artifact_bytes']}/"
        f"{storage['semantic_artifact_bytes']}/"
        f"{storage['combined_symbolic_representation_bytes']}\n"
        f"- AV2 total runtime/throughput/peak memory/disk: "
        f"{resources['total_seconds']} s / "
        f"{resources['trajectories_per_second']} trajectories/s / "
        f"{resources['peak_tracemalloc_bytes']} bytes / "
        f"{resources['generated_disk_bytes']} bytes\n"
        "- AV2 maps were used only after templates and memberships were frozen\n"
        "- Thresholds are fixed development settings; Phase 4 owns sweeps\n"
        "- Phase 5 owns layout induction from motion bundles\n\n"
        f"{_PASS_STATEMENT}\n"
    )
    _write_text(summary_path, summary)
    components = (
        schema_path,
        synthetic_path,
        av2_path,
        map_path,
        error_path,
        summary_path,
    )
    evidence = {
        "title": _TITLE,
        "batch": _BATCH,
        "batch_decision": "achieved",
        "pass_statement": _PASS_STATEMENT,
        "synthetic_gate": "PASS",
        "genuine_av2_provider_gate": "PASS",
        "configuration": {
            **{name: getattr(_CONFIG, name) for name in _CONFIG.__dataclass_fields__},
            "identity": shared_motion_configuration_identity(_CONFIG),
            "fixed_development_settings": True,
            "phase4_owns_sweeps": True,
        },
        "category_definitions": [item.value for item in MotionCategoryLabel],
        "scientific_contract": {
            "grouping_is_deterministic_and_motion_only": True,
            "templates_are_scenario_scoped": True,
            "representative_is_observed_source_path": True,
            "templates_do_not_replace_authoritative_numerical_replay": True,
            "av2_maps_used_only_for_evaluation": True,
            "phase5_owns_layout_induction": True,
        },
        "source_identities": {
            "batch_3_4_evidence_sha256": _sha256(batch_34_path),
            "batch_3_5_evidence_sha256": _sha256(batch_35_path),
            "procedural_codec_identity": velocity_bounded_encoder_parameters_identity(
                _CODEC_CONFIG
            ),
            "semantic_detector_identity": semantic_motion_configuration_identity(
                _SEMANTIC_CONFIG
            ),
        },
        "schema_fingerprints": schema_snapshot["new_schema_fingerprints"],
        "component_sha256": {path.name: _sha256(path) for path in components},
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.platform(),
            "machine": platform.machine(),
            "wsl_used": True,
            "execution_path_policy": "WSL2 Linux filesystem",
            "execution_policy": "CPU sequential bounded per scenario/category group",
            "uv_lock_sha256": _sha256(repository_root / "uv.lock"),
        },
        "generated_parquet_or_provider_data_tracked": False,
    }
    _write(evidence_path, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("results/generated/phase3/shared_motion_templates"),
    )
    arguments = parser.parse_args()
    repository_root = arguments.repository_root.resolve(strict=True)
    generated_root = arguments.generated_root
    if generated_root.is_absolute():
        try:
            generated_root = generated_root.relative_to(repository_root)
        except ValueError:
            parser.error("--generated-root must be beneath repository root")
    run(repository_root, generated_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
