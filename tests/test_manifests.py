"""Tests for immutable experiment and artifact manifests."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from kinematicweave.canonical import canonical_sha256
from kinematicweave.config import config_to_canonical_json, load_config
from kinematicweave.errors import ExperimentError, SchemaError, ValidationError
from kinematicweave.manifests import (
    ArtifactManifest,
    ArtifactType,
    ExperimentalUnitType,
    ExperimentManifest,
    RunStatus,
    artifact_manifest_from_dict,
    artifact_manifest_from_json,
    artifact_manifest_identity,
    artifact_manifest_to_canonical_json,
    artifact_manifest_to_dict,
    experiment_manifest_from_dict,
    experiment_manifest_from_json,
    experiment_manifest_to_canonical_json,
    experiment_manifest_to_dict,
    transition_experiment_manifest,
    validate_run_status_transition,
)
from kinematicweave.system_metadata import (
    capture_system_metadata,
    system_metadata_to_dict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_TIME = "2025-01-02T03:04:05.000000Z"
END_TIME = "2025-01-02T04:05:06.000000Z"
GIT_COMMIT = "a" * 40
LOCK_ID = "b" * 64
CHECKSUM = "c" * 64


def _experiment(**changes: object) -> ExperimentManifest:
    values: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": "run:demo",
        "experiment_id": "experiment:MOT-01",
        "experiment_version": "1.0",
        "status": RunStatus.PLANNED,
        "config_path": Path("configs/project.toml"),
        "resolved_config_json": '{"project":"KinematicWeave","seed":0}',
        "dataset_id": "dataset-demo",
        "split_name": "validation",
        "unit_type": ExperimentalUnitType.SCENARIO,
        "planned_unit_count": 10,
        "completed_unit_count": 0,
        "failed_unit_count": 0,
        "method_ids": ("M1", "M5"),
        "metric_names": ("error", "size"),
        "seeds": (0, 1),
        "git_commit": GIT_COMMIT,
        "git_dirty": False,
        "python_version": "3.12.13",
        "environment_lock_id": LOCK_ID,
        "host_id": "host-demo",
        "start_time_utc": None,
        "end_time_utc": None,
        "raw_result_paths": (),
        "log_paths": (),
        "failure_summary": None,
    }
    values.update(changes)
    return ExperimentManifest(**values)


def _artifact(**changes: object) -> ArtifactManifest:
    values: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_id": "artifact:metrics",
        "artifact_type": ArtifactType.RAW_RESULT,
        "path": Path("results/run-demo/metrics.json"),
        "producer": "kinematicweave.experiments",
        "producer_version": "0.1.0a0",
        "run_id": "run:demo",
        "source_artifact_ids": ("artifact:input-a", "artifact:input-b"),
        "config_id": "config-demo",
        "git_commit": GIT_COMMIT,
        "content_checksum": CHECKSUM,
        "size_bytes": 128,
        "created_time_utc": END_TIME,
        "metadata_json": '{"z":2,"a":1}',
    }
    values.update(changes)
    return ArtifactManifest(**values)


def _experiment_for_status(status: RunStatus) -> ExperimentManifest:
    if status is RunStatus.PLANNED:
        return _experiment()
    if status is RunStatus.RUNNING:
        return _experiment(status=status, start_time_utc=START_TIME)
    if status is RunStatus.PARTIAL:
        return _experiment(
            status=status,
            start_time_utc=START_TIME,
            completed_unit_count=3,
            failed_unit_count=1,
        )
    if status is RunStatus.COMPLETE:
        return _experiment(
            status=status,
            start_time_utc=START_TIME,
            end_time_utc=END_TIME,
            completed_unit_count=9,
            failed_unit_count=1,
        )
    if status is RunStatus.FAILED:
        return _experiment(
            status=status,
            start_time_utc=START_TIME,
            end_time_utc=END_TIME,
            completed_unit_count=3,
            failed_unit_count=1,
            failure_summary="worker failed",
        )
    return _experiment(
        status=RunStatus.CANCELLED,
        end_time_utc=END_TIME,
    )


def test_enum_values_are_exact() -> None:
    """Manifest enums expose the approved closed value sets."""
    assert [status.value for status in RunStatus] == [
        "planned",
        "running",
        "partial",
        "complete",
        "failed",
        "cancelled",
    ]
    assert [unit_type.value for unit_type in ExperimentalUnitType] == [
        "trajectory",
        "scenario",
        "tile",
        "edit_scenario",
        "tape",
        "workload",
    ]
    assert [artifact_type.value for artifact_type in ArtifactType] == [
        "data",
        "tape",
        "raw_result",
        "aggregate",
        "figure",
        "table",
        "report",
        "qualitative_image",
        "qualitative_video",
        "log",
    ]


def test_public_exports_are_exact() -> None:
    """The manifest module exports only approved public symbols."""
    from kinematicweave import manifests

    assert manifests.__all__ == [
        "ArtifactManifest",
        "ArtifactType",
        "ExperimentManifest",
        "ExperimentalUnitType",
        "RunStatus",
        "artifact_manifest_from_dict",
        "artifact_manifest_from_json",
        "artifact_manifest_identity",
        "artifact_manifest_to_canonical_json",
        "artifact_manifest_to_dict",
        "experiment_manifest_from_dict",
        "experiment_manifest_from_json",
        "experiment_manifest_to_canonical_json",
        "experiment_manifest_to_dict",
        "transition_experiment_manifest",
        "validate_run_status_transition",
    ]


def test_valid_planned_manifest_is_normalized() -> None:
    """A planned manifest validates and normalizes required text."""
    manifest = _experiment(
        experiment_version=" 1.0 ",
        dataset_id=" dataset-demo ",
        split_name=" validation ",
        python_version=" 3.12.13 ",
        host_id=" host-demo ",
    )

    assert manifest.experiment_version == "1.0"
    assert manifest.dataset_id == "dataset-demo"
    assert manifest.split_name == "validation"
    assert manifest.python_version == "3.12.13"
    assert manifest.host_id == "host-demo"


@pytest.mark.parametrize("status", list(RunStatus))
def test_each_run_status_has_a_valid_manifest(status: RunStatus) -> None:
    """Every approved run status has a valid explicit representation."""
    assert _experiment_for_status(status).status is status


def test_experiment_manifest_is_immutable_and_slotted() -> None:
    """Experiment manifests are frozen and omit instance dictionaries."""
    manifest = _experiment()

    assert not hasattr(manifest, "__dict__")
    with pytest.raises(FrozenInstanceError):
        manifest.status = RunStatus.RUNNING  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("run_id", "not-an-identifier"),
        ("experiment_id", "not-an-identifier"),
    ],
)
def test_experiment_identifiers_are_validated(
    field_name: str,
    value: str,
) -> None:
    """Run and experiment identifiers use the shared identifier contract."""
    with pytest.raises(ValidationError):
        _experiment(**{field_name: value})


def test_invalid_experiment_schema_version_is_rejected() -> None:
    """Experiment manifests require schema version 1.0."""
    with pytest.raises(ValidationError, match="schema_version"):
        _experiment(schema_version="2.0")


@pytest.mark.parametrize("git_commit", ["ABC" + "a" * 37, "a" * 39, "g" * 40])
def test_invalid_experiment_git_commit_is_rejected(git_commit: str) -> None:
    """Git revisions must be lowercase 40- or 64-character hexadecimal IDs."""
    with pytest.raises(ValidationError, match="git_commit"):
        _experiment(git_commit=git_commit)


@pytest.mark.parametrize("lock_id", ["B" * 64, "b" * 63, "z" * 64])
def test_invalid_environment_lock_id_is_rejected(lock_id: str) -> None:
    """Environment lock identities must be lowercase SHA-256 digests."""
    with pytest.raises(ValidationError, match="environment_lock_id"):
        _experiment(environment_lock_id=lock_id)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("planned_unit_count", True),
        ("completed_unit_count", False),
        ("failed_unit_count", -1),
    ],
)
def test_invalid_experiment_counts_are_rejected(
    field_name: str,
    value: object,
) -> None:
    """Counts reject Booleans and negative values."""
    with pytest.raises(ValidationError):
        _experiment(**{field_name: value})


def test_experiment_count_overflow_is_rejected() -> None:
    """Completed and failed units cannot exceed the plan."""
    with pytest.raises(ValidationError, match="must not exceed"):
        _experiment(completed_unit_count=8, failed_unit_count=3)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("method_ids", ("M1", "M1")),
        ("metric_names", ("error", "error")),
        ("seeds", (0, 0)),
        ("raw_result_paths", (Path("results/a"), Path("results/a"))),
        ("log_paths", (Path("logs/a"), Path("logs/a"))),
    ],
)
def test_duplicate_experiment_collections_are_rejected(
    field_name: str,
    value: object,
) -> None:
    """Manifest tuple fields reject normalized duplicates."""
    with pytest.raises(ValidationError, match="duplicates"):
        _experiment(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("config_path", Path("../outside.toml")),
        ("raw_result_paths", (Path("../outside.json"),)),
        ("log_paths", (Path("C:/absolute.log"),)),
    ],
)
def test_invalid_experiment_paths_are_rejected(
    field_name: str,
    value: object,
) -> None:
    """Every experiment path uses repository-relative validation."""
    with pytest.raises(ValidationError):
        _experiment(**{field_name: value})


@pytest.mark.parametrize("value", ["{broken", "[]", "42"])
def test_invalid_resolved_configuration_json_is_rejected(value: str) -> None:
    """Resolved configuration must be a valid JSON object."""
    with pytest.raises(ValidationError, match="resolved_config_json"):
        _experiment(resolved_config_json=value)


def test_resolved_configuration_json_is_canonicalized() -> None:
    """Resolved configuration becomes compact sorted JSON without a newline."""
    manifest = _experiment(resolved_config_json='{ "z": 2, "a": {"b": 1} }\n')

    assert manifest.resolved_config_json == '{"a":{"b":1},"z":2}'
    assert not manifest.resolved_config_json.endswith("\n")


@pytest.mark.parametrize(
    "changes",
    [
        {"completed_unit_count": 1},
        {"failed_unit_count": 1},
        {"start_time_utc": START_TIME},
        {"end_time_utc": END_TIME},
    ],
)
def test_planned_status_invariants(changes: dict[str, object]) -> None:
    """Planned runs require zero progress and no timestamps."""
    with pytest.raises(ValidationError, match="planned status"):
        _experiment(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": RunStatus.RUNNING},
        {
            "status": RunStatus.RUNNING,
            "start_time_utc": START_TIME,
            "end_time_utc": END_TIME,
        },
        {"status": RunStatus.PARTIAL},
        {
            "status": RunStatus.PARTIAL,
            "start_time_utc": START_TIME,
            "end_time_utc": END_TIME,
        },
    ],
)
def test_active_status_timestamp_invariants(changes: dict[str, object]) -> None:
    """Running and partial runs require a start and forbid an end."""
    with pytest.raises(ValidationError):
        _experiment(**changes)


def test_complete_status_invariants() -> None:
    """Complete runs account for every unit and have no failure summary."""
    with pytest.raises(ValidationError, match="all planned"):
        _experiment(
            status=RunStatus.COMPLETE,
            completed_unit_count=9,
        )
    with pytest.raises(ValidationError, match="failure_summary"):
        _experiment(
            status=RunStatus.COMPLETE,
            completed_unit_count=10,
            failure_summary="unexpected",
        )


def test_failed_and_cancelled_status_invariants() -> None:
    """Failed and cancelled runs require explicit terminal information."""
    with pytest.raises(ValidationError, match="end_time"):
        _experiment(status=RunStatus.FAILED, failure_summary="failed")
    with pytest.raises(ValidationError, match="failure_summary"):
        _experiment(status=RunStatus.FAILED, end_time_utc=END_TIME)
    with pytest.raises(ValidationError, match="end_time"):
        _experiment(status=RunStatus.CANCELLED)


@pytest.mark.parametrize(
    "field_name",
    ["start_time_utc", "end_time_utc"],
)
def test_invalid_experiment_timestamps_are_rejected(field_name: str) -> None:
    """Optional experiment timestamps require canonical UTC text."""
    changes: dict[str, object] = {
        "status": RunStatus.RUNNING,
        "start_time_utc": START_TIME,
    }
    changes[field_name] = "2025-01-02T03:04:05Z"

    with pytest.raises(ValidationError, match="ffffffZ"):
        _experiment(**changes)


def test_start_after_end_is_rejected() -> None:
    """A run cannot end before it starts."""
    with pytest.raises(ValidationError, match="later"):
        _experiment(
            status=RunStatus.FAILED,
            start_time_utc=END_TIME,
            end_time_utc=START_TIME,
            failure_summary="failed",
        )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.PLANNED, RunStatus.RUNNING),
        (RunStatus.PLANNED, RunStatus.CANCELLED),
        (RunStatus.RUNNING, RunStatus.PARTIAL),
        (RunStatus.RUNNING, RunStatus.COMPLETE),
        (RunStatus.RUNNING, RunStatus.FAILED),
        (RunStatus.RUNNING, RunStatus.CANCELLED),
        (RunStatus.PARTIAL, RunStatus.RUNNING),
        (RunStatus.PARTIAL, RunStatus.COMPLETE),
        (RunStatus.PARTIAL, RunStatus.FAILED),
        (RunStatus.PARTIAL, RunStatus.CANCELLED),
    ],
)
def test_every_allowed_status_transition(
    current: RunStatus,
    target: RunStatus,
) -> None:
    """The complete approved transition graph is accepted."""
    validate_run_status_transition(current, target)


def test_rejected_status_transitions() -> None:
    """Same-state, terminal, and skipped transitions fail explicitly."""
    with pytest.raises(ExperimentError, match="same-state"):
        validate_run_status_transition(RunStatus.RUNNING, RunStatus.RUNNING)
    with pytest.raises(ExperimentError):
        validate_run_status_transition(RunStatus.COMPLETE, RunStatus.RUNNING)
    with pytest.raises(ExperimentError):
        validate_run_status_transition(RunStatus.FAILED, RunStatus.CANCELLED)
    with pytest.raises(ExperimentError):
        validate_run_status_transition(RunStatus.PLANNED, RunStatus.COMPLETE)


def test_transition_helper_returns_new_manifest_without_mutation() -> None:
    """Transitioning preserves the original and returns a validated replacement."""
    planned = _experiment()

    running = transition_experiment_manifest(
        planned,
        RunStatus.RUNNING,
        start_time_utc=START_TIME,
        raw_result_paths=("results/run-demo.json",),
        log_paths=("logs/run-demo.log",),
    )

    assert running is not planned
    assert running.status is RunStatus.RUNNING
    assert running.start_time_utc == START_TIME
    assert running.raw_result_paths == (Path("results/run-demo.json"),)
    assert planned.status is RunStatus.PLANNED
    assert planned.start_time_utc is None
    assert planned.raw_result_paths == ()


def test_transition_helper_enforces_target_and_invents_no_timestamp() -> None:
    """Missing target-state timestamps are not generated automatically."""
    planned = _experiment()

    with pytest.raises(ValidationError, match="start_time"):
        transition_experiment_manifest(planned, RunStatus.RUNNING)
    with pytest.raises(ValidationError, match="end_time"):
        transition_experiment_manifest(planned, RunStatus.CANCELLED)

    running = _experiment_for_status(RunStatus.RUNNING)
    with pytest.raises(ValidationError, match="all planned"):
        transition_experiment_manifest(running, RunStatus.COMPLETE)


def test_valid_artifact_manifest_is_normalized() -> None:
    """Artifact text, paths, source order, and metadata are normalized."""
    manifest = _artifact(
        path=Path("results") / "." / "metrics.json",
        producer=" kinematicweave.experiments ",
        producer_version=" 0.1.0a0 ",
        metadata_json='{ "z": 2, "a": 1 }',
    )

    assert manifest.path == Path("results/metrics.json")
    assert manifest.producer == "kinematicweave.experiments"
    assert manifest.producer_version == "0.1.0a0"
    assert manifest.source_artifact_ids == (
        "artifact:input-a",
        "artifact:input-b",
    )
    assert manifest.metadata_json == '{"a":1,"z":2}'


def test_artifact_manifest_is_immutable_and_slotted() -> None:
    """Artifact manifests are frozen and omit instance dictionaries."""
    manifest = _artifact()

    assert not hasattr(manifest, "__dict__")
    with pytest.raises(FrozenInstanceError):
        manifest.size_bytes = 0  # type: ignore[misc]


def test_duplicate_artifact_sources_are_rejected() -> None:
    """Source artifact references preserve order but reject duplicates."""
    with pytest.raises(ValidationError, match="duplicates"):
        _artifact(
            source_artifact_ids=("artifact:input", "artifact:input"),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("artifact_id", "invalid"),
        ("run_id", "invalid"),
    ],
)
def test_artifact_identifiers_are_validated(
    field_name: str,
    value: str,
) -> None:
    """Artifact and optional run identifiers use the shared contract."""
    with pytest.raises(ValidationError):
        _artifact(**{field_name: value})


@pytest.mark.parametrize("checksum", ["C" * 64, "c" * 63, "z" * 64])
def test_invalid_artifact_checksum_is_rejected(checksum: str) -> None:
    """Optional artifact checksums must be lowercase SHA-256 digests."""
    with pytest.raises(ValidationError, match="content_checksum"):
        _artifact(content_checksum=checksum)


@pytest.mark.parametrize("size", [True, -1])
def test_invalid_artifact_size_is_rejected(size: object) -> None:
    """Artifact sizes reject Booleans and negative values."""
    with pytest.raises(ValidationError, match="size_bytes"):
        _artifact(size_bytes=size)


@pytest.mark.parametrize("metadata_json", ["{broken", "[]", "42"])
def test_invalid_artifact_metadata_json_is_rejected(metadata_json: str) -> None:
    """Optional metadata JSON must contain a valid object."""
    with pytest.raises(ValidationError, match="metadata_json"):
        _artifact(metadata_json=metadata_json)


def test_artifact_optional_fields_support_explicit_none() -> None:
    """Nullable artifact fields remain explicit and serializable."""
    manifest = _artifact(
        run_id=None,
        source_artifact_ids=(),
        config_id=None,
        content_checksum=None,
        created_time_utc=None,
        metadata_json=None,
    )

    serialized = artifact_manifest_to_dict(manifest)
    assert serialized["run_id"] is None
    assert serialized["config_id"] is None
    assert serialized["content_checksum"] is None
    assert serialized["created_time_utc"] is None
    assert serialized["metadata_json"] is None


def _contains_forbidden_value(value: object) -> bool:
    if isinstance(value, (Path, RunStatus, ExperimentalUnitType, ArtifactType)):
        return True
    if isinstance(value, dict):
        return any(_contains_forbidden_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_value(item) for item in value)
    return False


def test_dictionary_field_order_and_plain_values() -> None:
    """Dictionary writers preserve field order and contain only plain values."""
    experiment_dict = experiment_manifest_to_dict(_experiment())
    artifact_dict = artifact_manifest_to_dict(_artifact())

    assert list(experiment_dict) == list(ExperimentManifest.__dataclass_fields__)
    assert list(artifact_dict) == list(ArtifactManifest.__dataclass_fields__)
    assert not _contains_forbidden_value(experiment_dict)
    assert not _contains_forbidden_value(artifact_dict)


@pytest.mark.parametrize(
    "serializer",
    [
        lambda: experiment_manifest_to_canonical_json(_experiment()),
        lambda: artifact_manifest_to_canonical_json(_artifact()),
    ],
)
def test_canonical_serialization_is_deterministic_with_one_newline(
    serializer: Any,
) -> None:
    """Manifest JSON is byte-stable and ends with exactly one newline."""
    first = serializer()
    second = serializer()

    assert first == second
    assert first.endswith("\n")
    assert not first.endswith("\n\n")


def test_manifest_dictionary_and_json_round_trips() -> None:
    """Both manifest types round-trip without information loss."""
    experiment = _experiment()
    artifact = _artifact()

    assert (
        experiment_manifest_from_dict(experiment_manifest_to_dict(experiment))
        == experiment
    )
    assert (
        experiment_manifest_from_json(experiment_manifest_to_canonical_json(experiment))
        == experiment
    )
    assert artifact_manifest_from_dict(artifact_manifest_to_dict(artifact)) == artifact
    assert (
        artifact_manifest_from_json(artifact_manifest_to_canonical_json(artifact))
        == artifact
    )


@pytest.mark.parametrize(
    ("reader", "manifest_dict"),
    [
        (experiment_manifest_from_dict, experiment_manifest_to_dict(_experiment())),
        (artifact_manifest_from_dict, artifact_manifest_to_dict(_artifact())),
    ],
)
def test_unknown_and_missing_fields_are_rejected(
    reader: Any,
    manifest_dict: dict[str, object],
) -> None:
    """Schema readers reject unknown and missing fields."""
    with_unknown = dict(manifest_dict)
    with_unknown["unexpected"] = True
    with pytest.raises(SchemaError, match="Unknown"):
        reader(with_unknown)

    with_missing = dict(manifest_dict)
    del with_missing[next(iter(with_missing))]
    with pytest.raises(SchemaError, match="Missing"):
        reader(with_missing)


@pytest.mark.parametrize(
    "reader",
    [experiment_manifest_from_json, artifact_manifest_from_json],
)
def test_malformed_and_non_object_json_are_rejected(reader: Any) -> None:
    """JSON readers reject malformed text and non-object roots as SchemaError."""
    with pytest.raises(SchemaError, match="Invalid"):
        reader("{broken")
    with pytest.raises(SchemaError, match="root"):
        reader("[]")


def test_validation_failures_are_translated_to_schema_error() -> None:
    """Public readers translate model validation failures to SchemaError."""
    value = experiment_manifest_to_dict(_experiment())
    value["status"] = "unknown"

    with pytest.raises(SchemaError, match="invalid value") as captured:
        experiment_manifest_from_dict(value)

    assert captured.value.__cause__ is None


def test_artifact_identity_is_stable_and_excludes_location_and_time() -> None:
    """Artifact identity excludes path and creation time."""
    manifest = _artifact()
    identity = artifact_manifest_identity(manifest)

    assert identity == artifact_manifest_identity(manifest)
    assert identity == artifact_manifest_identity(
        replace(
            manifest,
            path=Path("archive/moved.json"),
            created_time_utc="2030-01-01T00:00:00.000000Z",
        )
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"content_checksum": "d" * 64},
        {"size_bytes": 129},
        {"producer_version": "0.2.0"},
        {"source_artifact_ids": ("artifact:different",)},
    ],
)
def test_artifact_identity_changes_with_logical_provenance(
    changed: dict[str, object],
) -> None:
    """Content, size, producer, and source changes alter identity."""
    manifest = _artifact()
    changed_manifest = _artifact(**changed)

    assert artifact_manifest_identity(changed_manifest) != artifact_manifest_identity(
        manifest
    )


def test_integration_with_config_and_system_metadata(tmp_path: Path) -> None:
    """Real foundations compose into round-trippable manifests without writes."""
    target = tmp_path / "not-written.json"
    assert not target.exists()

    system_metadata = capture_system_metadata(PROJECT_ROOT)
    config = load_config(PROJECT_ROOT / "configs" / "project.toml")
    host_id = canonical_sha256(
        "system-metadata",
        system_metadata_to_dict(system_metadata),
    )
    planned = _experiment(
        resolved_config_json=config_to_canonical_json(config),
        git_commit=system_metadata.git.commit or GIT_COMMIT,
        git_dirty=system_metadata.git.dirty or False,
        python_version=system_metadata.python.version,
        environment_lock_id=system_metadata.environment_lock_id or LOCK_ID,
        host_id=host_id,
        seeds=(config.root_seed,),
    )
    running = transition_experiment_manifest(
        planned,
        RunStatus.RUNNING,
        start_time_utc=START_TIME,
    )
    artifact = _artifact(
        path=Path(target.name),
        run_id=running.run_id,
        git_commit=running.git_commit,
    )

    assert (
        experiment_manifest_from_json(experiment_manifest_to_canonical_json(running))
        == running
    )
    assert (
        artifact_manifest_from_json(artifact_manifest_to_canonical_json(artifact))
        == artifact
    )
    assert not target.exists()
