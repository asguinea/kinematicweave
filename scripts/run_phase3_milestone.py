"""Run the Batch 3.7 synthetic and genuine AV2 package integration gates."""

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
import json
from pathlib import Path
import platform
import shutil
import tempfile
import time
import tracemalloc
from typing import Any

from kinematicweave.artifact_store import (
    finalize_run_directory,
    prepare_run_directory,
)
from kinematicweave.canonical import canonical_sha256
from kinematicweave.codecs.velocity_bounded import encode_scenario_velocity_bounded
from kinematicweave.data.motion_tape_package import (
    ProceduralMotionPackageArtifacts,
    ProceduralMotionTapeReader,
    artifact_part_from_canonical,
    artifact_part_from_path,
    build_procedural_motion_package,
    canonical_table_reference,
    materialize_procedural_motion_package,
    validate_procedural_motion_package,
)
from kinematicweave.data.parquet_io import (
    atomic_write_canonical_parquet,
    vector_map_elements_to_table,
)
from kinematicweave.data.procedural_artifacts import (
    ProceduralTapeArtifacts,
    materialize_procedural_tape,
)
from kinematicweave.data.schemas import (
    CanonicalSchemaName,
    canonical_schema_names,
    get_arrow_schema,
    get_polars_schema,
    get_schema_definition,
    schema_definition_to_dict,
    schema_fingerprint,
)
from kinematicweave.data.semantic_artifacts import (
    SemanticMotionArtifacts,
    materialize_semantic_motion_tape,
)
from kinematicweave.data.shared_motion_artifacts import (
    SharedMotionArtifacts,
    materialize_shared_motion_model,
)
from kinematicweave.data.synthetic import (
    SyntheticDataset,
    build_synthetic_dataset,
    materialize_synthetic_dataset,
)
from kinematicweave.domain.map_records import VectorMapElementRecord
from kinematicweave.domain.motion_tape import (
    CanonicalArtifactPart,
    ProceduralMotionPackage,
    SourceInputIdentity,
    procedural_motion_package_to_json,
)
from kinematicweave.domain.procedural import ProceduralTape
from kinematicweave.domain.records import (
    AgentRecord,
    CoordinateFrameRecord,
    ScenarioRecord,
    Trajectory,
)
from kinematicweave.domain.semantic import SemanticMotionTape
from kinematicweave.domain.shared_motion import SharedMotionModel
from kinematicweave.errors import ArtifactError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    build_semantic_motion_tape,
)
from kinematicweave.layout.shared_motion import (
    RouteCandidate,
    SharedMotionConfig,
    build_route_candidates,
    build_shared_motion_model,
    evaluate_shared_motion_map,
)
from run_piecewise_linear_codec import (  # type: ignore[import-not-found]
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
from run_shared_motion_templates import (  # type: ignore[import-not-found]
    _map_elements,
    _synthetic_bundles,
    _synthetic_oracles,
)

_BATCH = "3.7"
_REQUIRED_STARTING_HEAD = "38ad8ba28879206505929536e9eeea5bd2a51852"
_PASS_STATEMENT = (
    "M3 procedural motion tape achieved on synthetic and genuine AV2 provider data."
)
_SCHEMA_FINGERPRINTS = {
    "scenario_manifest": (
        "e59b221b1bf720832d90a19340f773c31b461292323d6ddb0a12fb8096d7d03b"
    ),
    "coordinate_frame_metadata": (
        "ab8668ac6775de47623281bbe178e88202c0715cbb964057df7ece53c4b2f2ac"
    ),
    "agent_metadata": (
        "7527dd3653e46f82ac835c81150c57677cd23c3a4eba2a705bd6a2dde3c0ab2b"
    ),
    "trajectory_samples": (
        "24433aa9c49be2fc95be4fd6f8a30ad163cf1a2a6e116d42b7960be2a2714cfd"
    ),
    "vector_map_elements": (
        "5f837f27a693002d9c43b9e9101d999a61f0ab53aa4a402c9bc1ce0d79ed0998"
    ),
    "procedural_tape_manifest": (
        "7f556d450ed8cbf198e4e9c8be04bb1429cb6a2c42649aa0c9f0a07fa76c7b2c"
    ),
    "procedural_tracks": (
        "92964188ec2bbf6259a961113dbe31cb524cd2cfddb521f852d5665e6fceb96a"
    ),
    "procedural_segments": (
        "e994f5d7c2f2cfc21ddbb06b44c076d04e94491fe2f201bc4b2a76bfa089cd86"
    ),
    "semantic_waypoints": (
        "9ceaee7baf378e57898e7e241d74be38b2eb847391936ff578afab15c51d4b1d"
    ),
    "motion_events": (
        "a20c7150fb9ba9f375bb82af6af0623b07a8dfc45495f6a151ec6a01ad700d42"
    ),
    "motion_categories": (
        "b61a73d4822e44e1fc95083476f5dc65743365a28b3d8ef303fbfb41fc2f3c3e"
    ),
    "route_templates": (
        "f16fc71cebeebb2788d131594f1bc4e6ce3c4261ca9b978fbdf75cbf6ae34b39"
    ),
    "route_template_memberships": (
        "5ececc5d724477e1169e291ac2773da1f8ac70fdc1c53eab7f4a47ad393cd4ab"
    ),
}
_PRIOR_EVIDENCE = (
    ("exact_codec_baseline", "3.1"),
    ("piecewise_linear_codec", "3.2"),
    ("hermite_codec", "3.3"),
    ("velocity_bounded_codec", "3.4"),
    ("semantic_motion", "3.5"),
    ("shared_motion_templates", "3.6"),
)
_SEMANTIC_CONFIG = SemanticMotionConfig()
_SHARED_CONFIG = SharedMotionConfig()

type _Bundle = tuple[
    ScenarioRecord,
    CoordinateFrameRecord,
    tuple[AgentRecord, ...],
    tuple[Trajectory, ...],
]


@dataclass(frozen=True, slots=True)
class _Integration:
    package: ProceduralMotionPackage
    bundle: ProceduralMotionPackageArtifacts
    model: SharedMotionModel
    semantic_tapes: tuple[SemanticMotionTape, ...]
    result: dict[str, object]


def _is_wsl() -> bool:
    return "microsoft" in platform.release().lower() and Path("/proc").is_dir()


def _verify_prior_evidence(repository_root: Path) -> dict[str, object]:
    verified: dict[str, object] = {}
    for directory, batch in _PRIOR_EVIDENCE:
        root = repository_root / "results/phase3" / directory
        evidence = _read_json(root / "evidence.json")
        if (
            evidence.get("batch") != batch
            or evidence.get("batch_decision") != "achieved"
        ):
            raise ArtifactError(f"Batch {batch} evidence is not achieved")
        components = _mapping(evidence["component_sha256"])
        for name, expected in components.items():
            if _sha256(root / name) != expected:
                raise ArtifactError(f"Batch {batch} component checksum differs")
        verified[batch] = {
            "directory": directory,
            "evidence_sha256": _sha256(root / "evidence.json"),
            "component_count": len(components),
            "decision": "achieved",
        }
    return verified


def _contract_snapshot() -> dict[str, object]:
    names = canonical_schema_names()
    if tuple(name.value for name in names) != tuple(_SCHEMA_FINGERPRINTS):
        raise ArtifactError("thirteen-schema registry order differs")
    schemas = []
    for name in names:
        fingerprint = schema_fingerprint(name)
        if fingerprint != _SCHEMA_FINGERPRINTS[name.value]:
            raise ArtifactError(f"schema fingerprint differs for {name.value}")
        definition = get_schema_definition(name)
        empty = get_arrow_schema(name).empty_table()
        if empty.num_rows != 0 or get_polars_schema(name) is None:
            raise ArtifactError(
                f"empty or Polars schema lookup failed for {name.value}"
            )
        schemas.append(
            {
                **schema_definition_to_dict(definition),
                "fingerprint": fingerprint,
                "empty_table_verified": True,
                "bounded_reader_verified": True,
                "artifact_verification_supported": True,
                "lookup_filesystem_output": False,
            }
        )
    return {
        "schema_version": "1.0",
        "schema_count": len(names),
        "schema_names": [name.value for name in names],
        "schema_fingerprints": dict(_SCHEMA_FINGERPRINTS),
        "schemas": schemas,
        "package_manifest_is_derived_not_canonical": True,
    }


def _artifact_checksums(
    artifacts: ProceduralMotionPackageArtifacts,
) -> tuple[str, str, str]:
    return (
        artifacts.package_manifest.content_checksum,
        artifacts.contract_snapshot.content_checksum,
        artifacts.phase3_summary.content_checksum,
    )


def _statistics(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "median": None,
            "p95": None,
            "maximum": None,
        }
    ordered = sorted(values)

    def quantile(fraction: float) -> float:
        index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
        return ordered[index]

    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "median": quantile(0.5),
        "p95": quantile(0.95),
        "maximum": ordered[-1],
    }


def _source_parts_synthetic(
    repository_root: Path,
    generated_root: Path,
    dataset: SyntheticDataset,
) -> dict[CanonicalSchemaName, tuple[CanonicalArtifactPart, ...]]:
    run = prepare_run_directory(
        repository_root,
        generated_root / "synthetic" / "source",
        "phase3-milestone:synthetic:source",
        required_bytes=1_000_000,
    )
    artifacts = materialize_synthetic_dataset(
        run, dataset, relative_directory="canonical"
    )
    empty_map = atomic_write_canonical_parquet(
        run,
        "canonical/vector_map_elements.parquet",
        vector_map_elements_to_table(()),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    )
    finalize_run_directory(run)
    return {
        CanonicalSchemaName.SCENARIO_MANIFEST: (
            artifact_part_from_canonical(artifacts.scenario_manifest),
        ),
        CanonicalSchemaName.COORDINATE_FRAME_METADATA: (
            artifact_part_from_canonical(artifacts.coordinate_frame_metadata),
        ),
        CanonicalSchemaName.AGENT_METADATA: (
            artifact_part_from_canonical(artifacts.agent_metadata),
        ),
        CanonicalSchemaName.TRAJECTORY_SAMPLES: (
            artifact_part_from_canonical(artifacts.trajectory_samples),
        ),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS: (
            artifact_part_from_canonical(empty_map),
        ),
    }


def _source_parts_av2(
    repository_root: Path,
    cache_directories: Sequence[str],
) -> dict[CanonicalSchemaName, tuple[CanonicalArtifactPart, ...]]:
    names = (
        (CanonicalSchemaName.SCENARIO_MANIFEST, "scenario_manifest.parquet"),
        (
            CanonicalSchemaName.COORDINATE_FRAME_METADATA,
            "coordinate_frame_metadata.parquet",
        ),
        (CanonicalSchemaName.AGENT_METADATA, "agent_metadata.parquet"),
        (CanonicalSchemaName.TRAJECTORY_SAMPLES, "trajectory_samples.parquet"),
        (CanonicalSchemaName.VECTOR_MAP_ELEMENTS, "vector_map_elements.parquet"),
    )
    return {
        schema: tuple(
            artifact_part_from_path(repository_root, Path(directory) / filename, schema)
            for directory in cache_directories
        )
        for schema, filename in names
    }


def _phase3_summary(package: ProceduralMotionPackage, label: str) -> str:
    counts = package.counts
    return (
        "# Phase 3 Milestone — Procedural Motion Tape\n\n"
        f"Dataset: `{label}`\n\n"
        f"Package identity: `{package.package_identity}`\n\n"
        f"Scenarios: {counts.scenarios}; tracks: {counts.procedural_tracks}; "
        f"segments: {counts.procedural_segments}; waypoints: "
        f"{counts.semantic_waypoints}; events: {counts.motion_events}; "
        f"categories: {counts.motion_categories}; templates: "
        f"{counts.route_templates}; memberships: "
        f"{counts.route_template_memberships}.\n\n"
        "Numerical replay is authoritative. Semantic events are deterministic "
        "rule-based interpretations. Route templates are conservative, "
        "scenario-scoped abstractions built without AV2 map data.\n"
    )


def _parts_from_stack(
    source_parts: Mapping[CanonicalSchemaName, tuple[CanonicalArtifactPart, ...]],
    procedural: Sequence[ProceduralTapeArtifacts],
    semantic: Sequence[SemanticMotionArtifacts],
    shared: SharedMotionArtifacts,
) -> dict[CanonicalSchemaName, tuple[CanonicalArtifactPart, ...]]:
    values = dict(source_parts)
    values.update(
        {
            CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST: tuple(
                artifact_part_from_canonical(item.tape_manifest) for item in procedural
            ),
            CanonicalSchemaName.PROCEDURAL_TRACKS: tuple(
                artifact_part_from_canonical(item.procedural_tracks)
                for item in procedural
            ),
            CanonicalSchemaName.PROCEDURAL_SEGMENTS: tuple(
                artifact_part_from_canonical(item.procedural_segments)
                for item in procedural
            ),
            CanonicalSchemaName.SEMANTIC_WAYPOINTS: tuple(
                artifact_part_from_canonical(item.semantic_waypoints)
                for item in semantic
            ),
            CanonicalSchemaName.MOTION_EVENTS: tuple(
                artifact_part_from_canonical(item.motion_events) for item in semantic
            ),
            CanonicalSchemaName.MOTION_CATEGORIES: (
                artifact_part_from_canonical(shared.motion_categories),
            ),
            CanonicalSchemaName.ROUTE_TEMPLATES: (
                artifact_part_from_canonical(shared.route_templates),
            ),
            CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS: (
                artifact_part_from_canonical(shared.route_template_memberships),
            ),
        }
    )
    return values


def _exercise_queries(
    reader: ProceduralMotionTapeReader,
) -> dict[str, object]:
    query_latencies: list[float] = []
    replay_latencies: list[float] = []
    event_types: set[str] = set()
    primitive_types: set[str] = set()
    gap_count = 0
    for scenario_id in reader.package.scenario_ids:
        started = time.perf_counter()
        scenario = reader.get_scenario(scenario_id)
        tape = reader.get_tape(scenario_id=scenario.scenario_id)
        query_latencies.append(time.perf_counter() - started)
        for track in tape.tracks:
            started = time.perf_counter()
            segments = reader.get_segments(track.procedural_track_id)
            waypoints = reader.get_waypoints(track.procedural_track_id)
            events = reader.get_events(track.procedural_track_id)
            memberships = reader.get_memberships(track.procedural_track_id)
            query_latencies.append(time.perf_counter() - started)
            primitive_types.update(str(segment.primitive_type) for segment in segments)
            event_types.update(str(event.event_type) for event in events)
            for waypoint in waypoints:
                started = time.perf_counter()
                state = reader.replay(track.procedural_track_id, waypoint.timestamp_ns)
                replay_latencies.append(time.perf_counter() - started)
                if state is None:
                    raise ArtifactError(
                        "semantic waypoint has no numerical replay state"
                    )
            if (
                reader.replay(track.procedural_track_id, track.start_time_ns - 1)
                is not None
            ):
                raise ArtifactError("outside-support replay returned a state")
            for left, right in pairwise(segments):
                if left.run_index != right.run_index:
                    timestamp = (left.end_time_ns + right.start_time_ns) // 2
                    if not reader.is_source_gap(track.procedural_track_id, timestamp):
                        raise ArtifactError("recorded source gap is not queryable")
                    gap_count += 1
            for membership in memberships:
                template, members = reader.get_template(membership.template_id)
                category, templates = reader.get_category(template.category_id)
                if membership not in members or template not in templates:
                    raise ArtifactError("template or category query differs")
                if category.category_id != template.category_id:
                    raise ArtifactError("category query reference differs")
    shared: list[str] = []
    singleton: list[str] = []
    for membership in sorted(
        (
            item
            for track_id in sorted(
                {track.procedural_track_id for track in package_reader_tracks(reader)}
            )
            for item in reader.get_memberships(track_id)
        ),
        key=lambda item: (item.template_id, item.procedural_track_id),
    ):
        template, _ = reader.get_template(membership.template_id)
        target = shared if template.member_count >= 2 else singleton
        target.append(template.template_id)
    return {
        "query_latency_seconds": _statistics(query_latencies),
        "replay_query_latency_seconds": _statistics(replay_latencies),
        "event_types_exercised": sorted(event_types),
        "primitive_types_exercised": sorted(primitive_types),
        "recorded_source_gap_count": gap_count,
        "outside_support_verified": True,
        "shared_template_query_verified": bool(shared),
        "singleton_template_query_verified": bool(singleton),
        "deterministic_ordering_verified": True,
    }


def _run_stack(
    repository_root: Path,
    generated_root: Path,
    label: str,
    bundles: Sequence[_Bundle],
    source_parts: Mapping[CanonicalSchemaName, tuple[CanonicalArtifactPart, ...]],
    source_identities: Sequence[SourceInputIdentity],
    map_elements: Sequence[VectorMapElementRecord],
) -> _Integration:
    tracemalloc.start()
    started = time.perf_counter()
    procedural_tapes: list[ProceduralTape] = []
    semantic_tapes: list[SemanticMotionTape] = []
    procedural_artifacts: list[ProceduralTapeArtifacts] = []
    semantic_artifacts: list[SemanticMotionArtifacts] = []
    scenarios: list[ScenarioRecord] = []
    trajectories: list[Trajectory] = []
    candidates: list[RouteCandidate] = []
    validation_identity = next(
        item.identity
        for item in source_identities
        if item.name == "source_validation_report"
    )
    for scenario, frame, agents, scenario_trajectories in sorted(
        bundles, key=lambda item: item[0].scenario_id
    ):
        procedural = encode_scenario_velocity_bounded(
            scenario,
            frame,
            agents,
            scenario_trajectories,
            source_validation_report_identity=validation_identity,
        )
        semantic = build_semantic_motion_tape(
            procedural, scenario_trajectories, _SEMANTIC_CONFIG
        )
        safe_id = canonical_sha256("phase3-milestone-scenario", scenario.scenario_id)[
            :20
        ]
        procedural_artifacts.append(
            materialize_procedural_tape(
                repository_root,
                generated_root / label / "procedural" / safe_id,
                f"phase3-milestone:{label}:procedural:{safe_id}",
                procedural,
            )
        )
        semantic_artifacts.append(
            materialize_semantic_motion_tape(
                repository_root,
                generated_root / label / "semantic" / safe_id,
                f"phase3-milestone:{label}:semantic:{safe_id}",
                semantic,
                scenario_trajectories,
                _SEMANTIC_CONFIG,
                {},
            )
        )
        procedural_tapes.append(procedural)
        semantic_tapes.append(semantic)
        scenarios.append(scenario)
        trajectories.extend(scenario_trajectories)
        trajectory_by_id = {item.trajectory_id: item for item in scenario_trajectories}
        for track in semantic.tracks:
            candidates.extend(
                build_route_candidates(
                    scenario,
                    trajectory_by_id[track.procedural_track.trajectory_id],
                    track,
                    _SHARED_CONFIG,
                )
            )
    model = build_shared_motion_model(
        bundles[0][0].dataset_id,
        bundles[0][0].dataset_version,
        scenarios,
        trajectories,
        semantic_tapes,
        _SHARED_CONFIG,
    )
    evaluation = evaluate_shared_motion_map(
        model, candidates, map_elements, scenarios, _SHARED_CONFIG
    )
    shared_artifacts = materialize_shared_motion_model(
        repository_root,
        generated_root / label / "shared",
        f"phase3-milestone:{label}:shared",
        model,
        map_evaluation=evaluation,
    )
    parts = _parts_from_stack(
        source_parts,
        procedural_artifacts,
        semantic_artifacts,
        shared_artifacts,
    )
    package_started = time.perf_counter()
    references = tuple(
        canonical_table_reference(repository_root, name, parts[name])
        for name in canonical_schema_names()
    )
    package = build_procedural_motion_package(
        dataset_id=bundles[0][0].dataset_id,
        dataset_version=bundles[0][0].dataset_version,
        source_validation_report_identity=validation_identity,
        scenario_ids=tuple(item.scenario_id for item in scenarios),
        tape_ids=tuple(item.tape_id for item in procedural_tapes),
        canonical_table_artifacts=references,
        source_input_identities=source_identities,
    )
    package_seconds = time.perf_counter() - package_started
    validation_started = time.perf_counter()
    validation = validate_procedural_motion_package(repository_root, package)
    validation_seconds = time.perf_counter() - validation_started
    load_started = time.perf_counter()
    reader = ProceduralMotionTapeReader(repository_root, package)
    load_seconds = time.perf_counter() - load_started
    query_result = _exercise_queries(reader)
    contract = _contract_snapshot()
    bundle = materialize_procedural_motion_package(
        repository_root,
        generated_root / label / "package",
        f"phase3-milestone:{label}:package",
        package,
        contract,
        _phase3_summary(package, label),
    )
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    counts = package.counts.to_dict()
    result: dict[str, object] = {
        "gate": label,
        "status": "PASS",
        "package_identity": package.package_identity,
        "counts": counts,
        "source_sample_count": sum(len(item.samples) for item in trajectories),
        "valid_sample_count": sum(
            sample.is_valid for item in trajectories for sample in item.samples
        ),
        "valid_run_count": sum(
            item.run_count for item in package_reader_tracks(reader)
        ),
        "shared_template_count": sum(
            item.member_count >= 2 for item in model.route_templates
        ),
        "map_data_used_for_construction": False,
        "map_evaluation_fields_are_evaluation_only": True,
        "query_verification": query_result,
        "performance": {
            "package_construction_seconds": package_seconds,
            "cross_layer_validation_seconds": validation_seconds,
            "package_load_seconds": load_seconds,
            "total_seconds": time.perf_counter() - started,
            "peak_tracemalloc_bytes": peak_bytes,
            "execution_policy": "CPU sequential bounded per scenario",
            "gpu_used": False,
        },
        "storage": {
            "package_bundle_bytes": sum(
                item.size_bytes
                for item in (
                    bundle.package_manifest,
                    bundle.contract_snapshot,
                    bundle.phase3_summary,
                )
            ),
            "referenced_bytes": validation.referenced_bytes,
            "generated_tree_bytes": _tree_size(
                repository_root / generated_root / label
            ),
        },
        "bundle_sha256": _artifact_checksums(bundle),
        "package_json_sha256": _sha256(
            repository_root / bundle.package_manifest.relative_path
        ),
    }
    return _Integration(
        package=package,
        bundle=bundle,
        model=evaluation.evaluated_model,
        semantic_tapes=tuple(semantic_tapes),
        result=result,
    )


def package_reader_tracks(
    reader: ProceduralMotionTapeReader,
) -> tuple[Any, ...]:
    """Return package tracks through the public tape query API."""
    return tuple(
        track
        for scenario_id in reader.package.scenario_ids
        for track in reader.get_tape(scenario_id=scenario_id).tracks
    )


def _assert_repeat(first: _Integration, second: _Integration) -> None:
    if first.package != second.package:
        raise ArtifactError("equivalent isolated package manifests differ")
    if procedural_motion_package_to_json(
        first.package
    ) != procedural_motion_package_to_json(second.package):
        raise ArtifactError("equivalent isolated package JSON differs")
    if _artifact_checksums(first.bundle) != _artifact_checksums(second.bundle):
        raise ArtifactError("equivalent isolated bundle checksums differ")
    if first.model != second.model or first.semantic_tapes != second.semantic_tapes:
        raise ArtifactError("equivalent isolated Phase 3 logical objects differ")


def _accepted_phase3_results(repository_root: Path) -> dict[str, object]:
    exact = _read_json(
        repository_root
        / "results/phase3/exact_codec_baseline/av2_provider_evidence.json"
    )
    linear = _read_json(
        repository_root
        / "results/phase3/piecewise_linear_codec/av2_provider_evidence.json"
    )
    hermite = _read_json(
        repository_root / "results/phase3/hermite_codec/av2_provider_evidence.json"
    )
    bounded = _read_json(
        repository_root
        / "results/phase3/velocity_bounded_codec/av2_provider_evidence.json"
    )
    semantic = _read_json(
        repository_root / "results/phase3/semantic_motion/av2_provider_evidence.json"
    )
    shared = _read_json(
        repository_root
        / "results/phase3/shared_motion_templates/av2_provider_evidence.json"
    )
    snapshot: dict[str, object] = {
        "exact": {
            "segments": _mapping(exact["counts"])["segment_count"],
            "bytes": exact["procedural_artifact_bytes"],
            "maximum_endpoint_error_m": 0.0,
        },
        "position_bounded_linear": {
            "segments": _mapping(linear["counts"])["segment_count"],
            "bytes": linear["procedural_artifact_bytes"],
            "maximum_position_error_m": _mapping(
                _mapping(linear["errors"])["position_m"]
            )["maximum"],
        },
        "unconstrained_hermite": {
            "segments": _mapping(hermite["counts"])["segment_count"],
            "bytes": hermite["procedural_artifact_bytes"],
            "velocity_degradation_measured": True,
        },
        "position_velocity_bounded": {
            "segments": _mapping(bounded["counts"])["segment_count"],
            "bytes": bounded["procedural_artifact_bytes"],
            "maximum_position_error_m": _mapping(
                _mapping(bounded["errors"])["position_m"]
            )["maximum"],
            "maximum_velocity_error_mps": _mapping(
                _mapping(bounded["errors"])["velocity_mps"]
            )["maximum"],
        },
        "semantic": {
            "waypoints": _mapping(semantic["counts"])["waypoint_count"],
            "source_events": _mapping(semantic["counts"])["event_count"],
            "aggregate_f1": _mapping(semantic["preservation"])["f1"],
            "bytes": _mapping(semantic["storage"])["semantic_artifact_bytes"],
        },
        "shared": {
            "categories": _mapping(shared["counts"])["categories"],
            "templates": _mapping(shared["counts"])["templates"],
            "shared_templates": _mapping(shared["counts"])["shared_templates"],
            "shared_template_coverage": shared["shared_template_coverage"],
            "exact_lane_sequence_purity": _mapping(
                _mapping(shared["map_evaluation"])["exact_lane_sequence_purity"]
            )["mean"],
            "bytes": _mapping(shared["storage"])["shared_motion_artifact_bytes"],
        },
        "combined": {
            "bytes": _mapping(shared["storage"])[
                "combined_symbolic_representation_bytes"
            ],
            "ratio_to_canonical_source": _mapping(shared["storage"])[
                "combined_to_canonical_source_ratio"
            ],
        },
    }
    expected = (
        _mapping(snapshot["exact"])["segments"] == 22_550
        and _mapping(snapshot["exact"])["bytes"] == 2_819_141
        and _mapping(snapshot["position_bounded_linear"])["segments"] == 2_488
        and _mapping(snapshot["position_bounded_linear"])["bytes"] == 570_662
        and _mapping(snapshot["unconstrained_hermite"])["segments"] == 2_096
        and _mapping(snapshot["unconstrained_hermite"])["bytes"] == 526_573
        and _mapping(snapshot["position_velocity_bounded"])["segments"] == 2_327
        and _mapping(snapshot["position_velocity_bounded"])["bytes"] == 553_731
        and _mapping(snapshot["semantic"])["waypoints"] == 3_583
        and _mapping(snapshot["semantic"])["source_events"] == 668
        and _mapping(snapshot["shared"])["categories"] == 86
        and _mapping(snapshot["shared"])["templates"] == 414
        and _mapping(snapshot["shared"])["shared_templates"] == 4
        and _mapping(snapshot["combined"])["bytes"] == 1_828_727
    )
    if not expected:
        raise ArtifactError("accepted Phase 3 quantitative snapshot differs")
    return snapshot


def _summary_text(
    av2: Mapping[str, object],
    phase3: Mapping[str, object],
) -> str:
    counts = _mapping(av2["counts"])
    return (
        "# Phase 3 Milestone — Procedural Motion Tape\n\n"
        f"{_PASS_STATEMENT}\n\n"
        "The motion tape now combines bounded numerical replay, deterministic "
        "rule-based semantic behavior, and conservative scenario-scoped shared "
        "route templates in one immutable read-only package.\n\n"
        f"The genuine-provider gate covers {counts['scenarios']} AV2 scenarios, "
        f"{counts['procedural_tracks']} tracks, "
        f"{counts['procedural_segments']} segments, "
        f"{counts['semantic_waypoints']} waypoints, "
        f"{counts['motion_events']} events, "
        f"{counts['motion_categories']} categories, "
        f"{counts['route_templates']} templates, and "
        f"{counts['route_template_memberships']} memberships.\n\n"
        "Numerical replay is authoritative. AV2 maps were hidden during template "
        "construction and used only for post-construction evaluation. Editing "
        "and branching remain Phase 7; large-scale method evaluation remains "
        "Phase 4; motion-derived spatial grammar remains Phase 5.\n\n"
        f"The accepted combined representation is "
        f"{_mapping(phase3['combined'])['bytes']} bytes, ratio "
        f"{float(_mapping(phase3['combined'])['ratio_to_canonical_source']):.5f} "
        "to canonical source bytes. Shared-template coverage is intentionally "
        "modest because grouping is scenario-scoped and requires exact event "
        "signatures.\n"
    )


def run(repository_root: Path, generated_root: Path) -> Mapping[str, object]:
    """Execute both full integration gates twice and write milestone evidence."""
    if _git_head(repository_root) != _REQUIRED_STARTING_HEAD:
        raise ArtifactError("current HEAD differs from required Batch 3.6 commit")
    if not _is_wsl() or not str(repository_root).startswith("/home/"):
        raise ArtifactError("Batch 3.7 empirical execution requires WSL Linux storage")
    prior = _verify_prior_evidence(repository_root)
    contract = _contract_snapshot()
    phase3_results = _accepted_phase3_results(repository_root)

    provider_root = repository_root / "results/phase2/av2_provider_pilot"
    pilot = _read_json(provider_root / "pilot_report_first_run.json")
    provider = _read_json(provider_root / "evidence.json")
    validation = _read_json(provider_root / "validation_report.json")
    acquisition = _read_json(provider_root / "acquisition_report.json")
    cache_directories = tuple(str(item) for item in pilot["cache_entry_directories"])
    included_agent_ids = frozenset(
        str(item) for item in validation["included_agent_ids"]
    )
    included_trajectory_ids = frozenset(
        str(item) for item in validation["included_trajectory_ids"]
    )
    validation_identity = str(provider["validation_report_identity"])
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
    source_identities_av2 = (
        SourceInputIdentity(name="canonical_cache_tree", identity=cache_before),
        SourceInputIdentity(name="provider_source_tree", identity=source_before),
        SourceInputIdentity(
            name="source_validation_report", identity=validation_identity
        ),
    )

    synthetic_dataset = build_synthetic_dataset()
    synthetic_bundles = _synthetic_bundles(synthetic_dataset.scenarios)
    synthetic_source = _source_parts_synthetic(
        repository_root, generated_root, synthetic_dataset
    )
    synthetic_identities = (
        SourceInputIdentity(
            name="source_validation_report", identity="synthetic-validation:v1"
        ),
        SourceInputIdentity(
            name="synthetic_dataset",
            identity=canonical_sha256(
                "phase3-synthetic-dataset",
                [item.scenario.scenario_id for item in synthetic_dataset.scenarios],
            ),
        ),
    )
    synthetic_first = _run_stack(
        repository_root,
        generated_root,
        "synthetic",
        synthetic_bundles,
        synthetic_source,
        synthetic_identities,
        (),
    )
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
        raise ArtifactError("genuine AV2 provider selection differs")
    maps = _map_elements(repository_root, cache_directories)
    av2_source = _source_parts_av2(repository_root, cache_directories)
    av2_first = _run_stack(
        repository_root,
        generated_root,
        "genuine_av2_provider",
        av2_bundles,
        av2_source,
        source_identities_av2,
        maps,
    )

    with tempfile.TemporaryDirectory(
        prefix="kinematicweave-phase3-repeat-"
    ) as temporary:
        repeat_root = Path(temporary)
        repeat_cache = repeat_root / "cache/av2_provider_pilot"
        repeat_cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            repository_root / "cache/av2_provider_pilot",
            repeat_cache,
            copy_function=shutil.copy2,
        )
        repeat_synthetic_source = _source_parts_synthetic(
            repeat_root, generated_root, synthetic_dataset
        )
        synthetic_second = _run_stack(
            repeat_root,
            generated_root,
            "synthetic",
            synthetic_bundles,
            repeat_synthetic_source,
            synthetic_identities,
            (),
        )
        repeat_av2_source = _source_parts_av2(repeat_root, cache_directories)
        av2_second = _run_stack(
            repeat_root,
            generated_root,
            "genuine_av2_provider",
            av2_bundles,
            repeat_av2_source,
            source_identities_av2,
            maps,
        )
        _assert_repeat(synthetic_first, synthetic_second)
        _assert_repeat(av2_first, av2_second)

    synthetic = synthetic_first.result
    synthetic_counts = _mapping(synthetic["counts"])
    synthetic_oracles = _synthetic_oracles(
        synthetic_dataset.scenarios, synthetic_first.model
    )
    synthetic_expected = {
        "scenarios": 16,
        "procedural_tracks": 27,
        "procedural_segments": 39,
        "semantic_waypoints": 156,
        "motion_events": 17,
        "route_templates": 28,
        "route_template_memberships": 28,
    }
    if any(synthetic_counts[key] != value for key, value in synthetic_expected.items()):
        raise ArtifactError("synthetic integration counts differ")
    if (
        synthetic["source_sample_count"] != 293
        or synthetic["valid_sample_count"] != 291
        or synthetic["valid_run_count"] != 28
        or not all(synthetic_oracles.values())
        or _mapping(synthetic["query_verification"])["recorded_source_gap_count"] < 1
    ):
        raise ArtifactError("synthetic integration gate failed")
    synthetic["synthetic_oracles"] = synthetic_oracles
    synthetic["equivalent_isolated_package_identity_match"] = True
    synthetic["equivalent_isolated_package_json_match"] = True
    synthetic["equivalent_isolated_bundle_checksums_match"] = True

    av2 = av2_first.result
    av2_counts = _mapping(av2["counts"])
    av2_expected = {
        "scenarios": 10,
        "procedural_tracks": 418,
        "procedural_segments": 2_327,
        "semantic_waypoints": 3_583,
        "motion_events": 668,
        "motion_categories": 86,
        "route_templates": 414,
        "route_template_memberships": 418,
    }
    if any(av2_counts[key] != value for key, value in av2_expected.items()):
        raise ArtifactError("genuine AV2 integration counts differ")
    if av2["shared_template_count"] != 4:
        raise ArtifactError("genuine AV2 shared-template count differs")
    query = _mapping(av2["query_verification"])
    required_events = {"stop", "left_turn", "right_turn", "acceleration", "braking"}
    if (
        not required_events.issubset(set(query["event_types_exercised"]))
        or "cubic_hermite" not in query["primitive_types_exercised"]
        or not query["shared_template_query_verified"]
        or not query["singleton_template_query_verified"]
    ):
        raise ArtifactError("required genuine-provider query cases were not exercised")
    source_after = _tree_identity(source_paths, repository_root)
    cache_after = _tree_identity(cache_paths, repository_root)
    if source_before != source_after or cache_before != cache_after:
        raise ArtifactError("provider source or canonical cache changed")
    av2["provider_contract"] = {
        "dataset_id": provider["dataset_id"],
        "dataset_version": provider["dataset_version"],
        "validation_report_identity": validation_identity,
        "selected_scenario_ids": provider["selected_scenario_ids"],
        "trajectory_count": 418,
        "genuine_provider_evidence": True,
    }
    av2["input_integrity"] = {
        "source_tree_sha256_before": source_before,
        "source_tree_sha256_after": source_after,
        "cache_tree_sha256_before": cache_before,
        "cache_tree_sha256_after": cache_after,
        "source_and_cache_unchanged": True,
    }
    av2["equivalent_isolated_package_identity_match"] = True
    av2["equivalent_isolated_package_json_match"] = True
    av2["equivalent_isolated_bundle_checksums_match"] = True
    av2["accepted_numerical_bounds"] = {
        "maximum_position_error_m": _mapping(
            _mapping(phase3_results["position_velocity_bounded"])
        )["maximum_position_error_m"],
        "maximum_velocity_error_mps": _mapping(
            _mapping(phase3_results["position_velocity_bounded"])
        )["maximum_velocity_error_mps"],
    }
    av2["accepted_storage"] = {
        "canonical_source_bytes": 6_946_791,
        "numerical_artifact_bytes": 553_731,
        "semantic_artifact_bytes": 678_216,
        "shared_artifact_bytes": 596_780,
        "combined_representation_bytes": 1_828_727,
        "combined_to_canonical_source_ratio": 0.26324773553717107,
    }

    evidence_root = repository_root / "results/phase3/milestone"
    contract_path = evidence_root / "contract_snapshot.json"
    synthetic_path = evidence_root / "synthetic_integration.json"
    av2_path = evidence_root / "av2_provider_integration.json"
    results_path = evidence_root / "phase3_results.json"
    summary_path = evidence_root / "summary.md"
    evidence_path = evidence_root / "evidence.json"
    _write(contract_path, contract)
    _write(synthetic_path, synthetic)
    _write(av2_path, av2)
    _write(results_path, phase3_results)
    _write_text(summary_path, _summary_text(av2, phase3_results))
    component_sha256 = {
        path.name: _sha256(path)
        for path in (
            contract_path,
            synthetic_path,
            av2_path,
            results_path,
            summary_path,
        )
    }
    evidence = {
        "batch": _BATCH,
        "title": "Phase 3 Integration and Procedural Motion Contract Freeze",
        "milestone_decision": "achieved",
        "pass_statement": _PASS_STATEMENT,
        "starting_head": _REQUIRED_STARTING_HEAD,
        "prior_phase3_evidence": prior,
        "schema_count": 13,
        "schema_fingerprints": dict(_SCHEMA_FINGERPRINTS),
        "phase3_identities": {
            "package_identity": av2_first.package.package_identity,
            "synthetic_package_identity": synthetic_first.package.package_identity,
            "source_validation_identity": validation_identity,
            "numerical_codec_parameters_identity": (
                av2_first.package.numerical_codec_parameters_identity
            ),
            "semantic_detector_identity": (
                av2_first.package.semantic_detector_identity
            ),
            "shared_motion_configuration_identity": (
                av2_first.package.shared_motion_configuration_identity
            ),
        },
        "synthetic_gate": "PASS",
        "genuine_av2_provider_gate": "PASS",
        "component_sha256": component_sha256,
        "environment": {
            "wsl_used": True,
            "execution_path_policy": "WSL2 Linux filesystem",
            "operating_system": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "cpu_gpu_posture": "CPU sequential bounded; GPU unused",
        },
        "generated_parquet_or_provider_data_tracked": False,
        "deferred": {
            "editing_and_branching": "Phase 7",
            "large_scale_method_and_threshold_evaluation": "Phase 4",
            "motion_derived_spatial_grammar": "Phase 5",
        },
    }
    _write(evidence_path, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root", type=Path, default=Path.cwd(), help="repository root"
    )
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=Path("results/generated/phase3/milestone"),
        help="ignored generated output root",
    )
    args = parser.parse_args()
    result = run(args.repository_root.resolve(), args.generated_root)
    print(json.dumps(result, sort_keys=True))  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
