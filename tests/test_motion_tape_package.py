"""Tests for the frozen Phase 3 procedural-motion package."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from kinematicweave.artifact_store import (
    finalize_run_directory,
    prepare_run_directory,
)
from kinematicweave.codecs.velocity_bounded import encode_scenario_velocity_bounded
from kinematicweave.data.motion_tape_package import (
    ProceduralMotionTapeReader,
    artifact_part_from_canonical,
    build_procedural_motion_package,
    canonical_table_reference,
    materialize_procedural_motion_package,
    validate_procedural_motion_package,
    verify_procedural_motion_package_artifacts,
)
from kinematicweave.data.parquet_io import (
    atomic_write_canonical_parquet,
    vector_map_elements_to_table,
)
from kinematicweave.data.procedural_artifacts import materialize_procedural_tape
from kinematicweave.data.schemas import CanonicalSchemaName, canonical_schema_names
from kinematicweave.data.semantic_artifacts import materialize_semantic_motion_tape
from kinematicweave.data.shared_motion_artifacts import materialize_shared_motion_model
from kinematicweave.data.synthetic import (
    SyntheticDataset,
    build_synthetic_dataset,
    materialize_synthetic_dataset,
)
from kinematicweave.domain.motion_tape import (
    ProceduralMotionPackage,
    SourceInputIdentity,
    procedural_motion_package_from_json,
    procedural_motion_package_to_dict,
    procedural_motion_package_to_json,
)
from kinematicweave.domain.records import Trajectory
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.events.semantic_motion import (
    SemanticMotionConfig,
    build_semantic_motion_tape,
)
from kinematicweave.layout.shared_motion import (
    SharedMotionConfig,
    build_shared_motion_model,
)

type _PackageFixture = tuple[Path, SyntheticDataset, ProceduralMotionPackage]


@pytest.fixture(scope="module")
def package_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> _PackageFixture:
    root = tmp_path_factory.mktemp("motion-tape-package")
    dataset = build_synthetic_dataset()
    source_run = prepare_run_directory(
        root, "generated/source", "source:synthetic", required_bytes=1_000_000
    )
    source = materialize_synthetic_dataset(
        source_run, dataset, relative_directory="canonical"
    )
    empty_map = atomic_write_canonical_parquet(
        source_run,
        "canonical/vector_map_elements.parquet",
        vector_map_elements_to_table(()),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS,
    )
    finalize_run_directory(source_run)

    procedural_artifacts = []
    semantic_artifacts = []
    semantic_tapes = []
    scenarios = []
    trajectories: list[Trajectory] = []
    semantic_config = SemanticMotionConfig()
    for item in sorted(dataset.scenarios, key=lambda value: value.scenario.scenario_id):
        tape = encode_scenario_velocity_bounded(
            item.scenario,
            item.coordinate_frame,
            item.agents,
            item.trajectories,
            source_validation_report_identity="synthetic-validation:v1",
        )
        procedural_artifacts.append(
            materialize_procedural_tape(
                root,
                f"generated/procedural/{item.kind.value}",
                f"procedural:{item.kind.value}",
                tape,
            )
        )
        semantic = build_semantic_motion_tape(tape, item.trajectories, semantic_config)
        semantic_tapes.append(semantic)
        semantic_artifacts.append(
            materialize_semantic_motion_tape(
                root,
                f"generated/semantic/{item.kind.value}",
                f"semantic:{item.kind.value}",
                semantic,
                item.trajectories,
                semantic_config,
                {},
            )
        )
        scenarios.append(item.scenario)
        trajectories.extend(item.trajectories)
    shared = build_shared_motion_model(
        dataset.dataset_id,
        dataset.dataset_version,
        scenarios,
        trajectories,
        semantic_tapes,
        SharedMotionConfig(),
    )
    shared_artifacts = materialize_shared_motion_model(
        root,
        "generated/shared",
        "shared:synthetic",
        shared,
    )

    by_schema = {
        CanonicalSchemaName.SCENARIO_MANIFEST: (
            artifact_part_from_canonical(source.scenario_manifest),
        ),
        CanonicalSchemaName.COORDINATE_FRAME_METADATA: (
            artifact_part_from_canonical(source.coordinate_frame_metadata),
        ),
        CanonicalSchemaName.AGENT_METADATA: (
            artifact_part_from_canonical(source.agent_metadata),
        ),
        CanonicalSchemaName.TRAJECTORY_SAMPLES: (
            artifact_part_from_canonical(source.trajectory_samples),
        ),
        CanonicalSchemaName.VECTOR_MAP_ELEMENTS: (
            artifact_part_from_canonical(empty_map),
        ),
        CanonicalSchemaName.PROCEDURAL_TAPE_MANIFEST: tuple(
            artifact_part_from_canonical(item.tape_manifest)
            for item in procedural_artifacts
        ),
        CanonicalSchemaName.PROCEDURAL_TRACKS: tuple(
            artifact_part_from_canonical(item.procedural_tracks)
            for item in procedural_artifacts
        ),
        CanonicalSchemaName.PROCEDURAL_SEGMENTS: tuple(
            artifact_part_from_canonical(item.procedural_segments)
            for item in sorted(
                procedural_artifacts,
                key=lambda value: value.tape_manifest.written_artifact.relative_path,
            )
        ),
        CanonicalSchemaName.SEMANTIC_WAYPOINTS: tuple(
            artifact_part_from_canonical(item.semantic_waypoints)
            for item in semantic_artifacts
        ),
        CanonicalSchemaName.MOTION_EVENTS: tuple(
            artifact_part_from_canonical(item.motion_events)
            for item in semantic_artifacts
        ),
        CanonicalSchemaName.MOTION_CATEGORIES: (
            artifact_part_from_canonical(shared_artifacts.motion_categories),
        ),
        CanonicalSchemaName.ROUTE_TEMPLATES: (
            artifact_part_from_canonical(shared_artifacts.route_templates),
        ),
        CanonicalSchemaName.ROUTE_TEMPLATE_MEMBERSHIPS: (
            artifact_part_from_canonical(shared_artifacts.route_template_memberships),
        ),
    }
    references = tuple(
        canonical_table_reference(root, name, by_schema[name])
        for name in canonical_schema_names()
    )
    package = build_procedural_motion_package(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        source_validation_report_identity="synthetic-validation:v1",
        scenario_ids=tuple(item.scenario.scenario_id for item in dataset.scenarios),
        tape_ids=tuple(item.procedural_tape.tape_id for item in semantic_tapes),
        canonical_table_artifacts=references,
        source_input_identities=(
            SourceInputIdentity(
                name="synthetic_validation", identity="synthetic-validation:v1"
            ),
        ),
    )
    return root, dataset, package


def test_package_is_immutable_canonical_and_strict(
    package_fixture: _PackageFixture,
) -> None:
    _, _, package = package_fixture
    with pytest.raises(FrozenInstanceError):
        package.dataset_id = "changed"  # type: ignore[misc]
    text = procedural_motion_package_to_json(package)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert procedural_motion_package_from_json(text) == package
    assert (
        procedural_motion_package_to_json(procedural_motion_package_from_json(text))
        == text
    )
    assert len(package.canonical_table_artifacts) == 13
    assert tuple(
        item.canonical_schema_name for item in package.canonical_table_artifacts
    ) == tuple(item.value for item in canonical_schema_names())

    value = procedural_motion_package_to_dict(package)
    value["unknown"] = True
    with pytest.raises(ValidationError, match="unknown"):
        procedural_motion_package_from_json(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        )
    del value["unknown"]
    del value["dataset_id"]
    with pytest.raises(ValidationError, match="missing"):
        procedural_motion_package_from_json(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        )
    with pytest.raises(ValidationError, match="malformed") as raised:
        procedural_motion_package_from_json("{\n")
    assert raised.value.__cause__ is None


def test_cross_layer_validation_and_read_only_queries(
    package_fixture: _PackageFixture,
) -> None:
    root, dataset, package = package_fixture
    result = validate_procedural_motion_package(root, package)
    assert result.package_identity == package.package_identity
    assert result.artifact_count == 88
    assert result.map_data_used_for_construction is False
    assert package.counts.scenarios == 16
    assert package.counts.procedural_tracks == 27
    assert package.counts.procedural_segments == 39
    assert package.counts.semantic_waypoints == 156
    assert package.counts.motion_events == 17

    reader = ProceduralMotionTapeReader(root, package)
    scenario = reader.get_scenario(package.scenario_ids[0])
    tape = reader.get_tape(scenario_id=scenario.scenario_id)
    assert reader.get_tape(tape_id=tape.tape_id) == tape
    track = tape.tracks[0]
    assert reader.get_track(track.procedural_track_id) == track
    assert reader.get_segments(track.procedural_track_id) == track.segments
    assert reader.get_waypoints(track.procedural_track_id)
    for waypoint in reader.get_waypoints(track.procedural_track_id):
        assert (
            reader.replay(track.procedural_track_id, waypoint.timestamp_ns) is not None
        )
    assert reader.replay(track.procedural_track_id, track.start_time_ns - 1) is None
    with pytest.raises(ArtifactError, match="not found"):
        reader.get_track("procedural-track:missing")
    with pytest.raises(ValidationError, match="exactly one"):
        reader.get_tape()

    gap_scenario = next(
        item for item in dataset.scenarios if item.kind.value == "missing_gap"
    )
    gap_tape = reader.get_tape(scenario_id=gap_scenario.scenario.scenario_id)
    gap_track = gap_tape.tracks[0]
    left, right = gap_track.segments
    timestamp = (left.end_time_ns + right.start_time_ns) // 2
    assert reader.is_source_gap(gap_track.procedural_track_id, timestamp)
    assert reader.replay(gap_track.procedural_track_id, timestamp) is None

    membership = next(iter(reader.get_memberships(track.procedural_track_id)))
    template, members = reader.get_template(membership.template_id)
    category, templates = reader.get_category(template.category_id)
    assert membership in members
    assert template in templates
    assert category.category_id == template.category_id


def test_package_bundle_is_exact_and_deterministic(
    package_fixture: _PackageFixture,
) -> None:
    root, _, package = package_fixture
    snapshot = {
        "schema_version": "1.0",
        "schema_names": [item.value for item in canonical_schema_names()],
        "package_identity": package.package_identity,
    }
    summary = (
        "# Phase 3 Milestone - Procedural Motion Tape\n"
        f"\nPackage identity: `{package.package_identity}`\n"
    )
    first = materialize_procedural_motion_package(
        root, "generated/package/first", "package:first", package, snapshot, summary
    )
    second = materialize_procedural_motion_package(
        root,
        "generated/package/second",
        "package:second",
        package,
        snapshot,
        summary,
    )
    reconstructed, result = verify_procedural_motion_package_artifacts(
        root,
        first,
        expected_package=package,
        expected_contract_snapshot=snapshot,
        expected_phase3_summary=summary,
    )
    assert reconstructed == package
    assert result.package_identity == package.package_identity
    assert (
        first.package_manifest.content_checksum
        == second.package_manifest.content_checksum
    )
    assert (
        first.contract_snapshot.content_checksum
        == second.contract_snapshot.content_checksum
    )
    assert (
        first.phase3_summary.content_checksum == second.phase3_summary.content_checksum
    )


def test_package_rejects_identity_and_artifact_corruption(
    package_fixture: _PackageFixture,
) -> None:
    root, _, package = package_fixture
    with pytest.raises(ValidationError, match="package_identity"):
        replace(package, package_identity="0" * 64)
    first_table = package.canonical_table_artifacts[0]
    first_part = first_table.artifacts[0]
    corrupted_part = replace(first_part, sha256="0" * 64)
    corrupted_table = replace(first_table, artifacts=(corrupted_part,))
    tables = (corrupted_table, *package.canonical_table_artifacts[1:])
    value = procedural_motion_package_to_dict(package)
    value["canonical_table_artifacts"] = [item.to_dict() for item in tables]
    del value["package_identity"]
    # Rebuilding is the supported way to obtain a valid identity for changed content.
    corrupted = build_procedural_motion_package(
        dataset_id=package.dataset_id,
        dataset_version=package.dataset_version,
        source_validation_report_identity=package.source_validation_report_identity,
        scenario_ids=package.scenario_ids,
        tape_ids=package.tape_ids,
        canonical_table_artifacts=tables,
        source_input_identities=package.source_input_identities,
    )
    with pytest.raises(ArtifactError, match="checksum"):
        validate_procedural_motion_package(root, corrupted)
