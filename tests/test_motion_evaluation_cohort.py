"""Focused tests for the frozen Phase 4 AV2 motion cohort."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path

import pytest

from kinematicweave.canonical import canonical_json_text
from kinematicweave.data import av2_acquisition, pilot
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    CohortRunMeasurements,
    CohortValidationSummary,
    DistributionSummary,
    MotionCohortConfig,
    MotionCohortManifest,
    MotionCohortSelectionUnit,
    MotionCohortUnit,
    ResourcePilotResult,
    build_cohort_manifest,
    build_role_acquisition_plan,
    build_role_pilot_plan,
    cohort_dataset_version,
    cohort_run_measurements_from_json,
    cohort_run_measurements_to_canonical_json,
    cohort_validation_summary_from_json,
    cohort_validation_summary_to_canonical_json,
    motion_cohort_manifest_from_json,
    motion_cohort_manifest_to_canonical_json,
    resource_pilot_result_from_json,
    resource_pilot_result_to_canonical_json,
    select_motion_cohort,
)


def _remote(
    scenario_id: str,
    partition: str,
    name: str,
    size: int,
) -> av2_acquisition.Av2RemoteObject:
    key = f"{partition}/{scenario_id}/{name}"
    return av2_acquisition.Av2RemoteObject(
        remote_uri=f"{av2_acquisition.AV2_OFFICIAL_MOTION_ROOT}{key}",
        relative_key=key,
        size_bytes=size,
        etag=hashlib.md5(key.encode(), usedforsecurity=False).hexdigest(),
    )


def _pair(
    scenario_id: str,
    partition: str,
) -> av2_acquisition.Av2RemoteScenarioPair:
    return av2_acquisition.Av2RemoteScenarioPair(
        source_scenario_id=scenario_id,
        motion=_remote(
            scenario_id,
            partition,
            f"scenario_{scenario_id}.parquet",
            100,
        ),
        vector_map=_remote(
            scenario_id,
            partition,
            f"log_map_archive_{scenario_id}.json",
            50,
        ),
    )


def _catalog(
    prefix: str,
    partition: str,
    count: int,
) -> tuple[av2_acquisition.Av2RemoteScenarioPair, ...]:
    return tuple(_pair(f"{prefix}-{index:04d}", partition) for index in range(count))


@pytest.fixture(scope="module")
def selection() -> tuple[MotionCohortSelectionUnit, ...]:
    return select_motion_cohort(
        _catalog("train", "train", 600),
        _catalog("val", "val", 400),
        config=MotionCohortConfig(),
    )


def _manifest(
    selection: tuple[MotionCohortSelectionUnit, ...],
    *,
    first_motion_sha: str = "a" * 64,
) -> MotionCohortManifest:
    units = tuple(
        MotionCohortUnit(
            provider_partition=item.provider_partition,
            cohort_role=item.cohort_role,
            selection_rank=item.selection_rank,
            source_scenario_id=item.remote_pair.source_scenario_id,
            motion_object_path=Path("data/external/av2_motion_phase4")
            / item.provider_partition
            / item.remote_pair.source_scenario_id
            / f"scenario_{item.remote_pair.source_scenario_id}.parquet",
            motion_size_bytes=item.remote_pair.motion.size_bytes,
            motion_sha256=(
                first_motion_sha
                if index == 0
                else hashlib.sha256(f"motion:{index}".encode()).hexdigest()
            ),
            map_object_path=Path("data/external/av2_motion_phase4")
            / item.provider_partition
            / item.remote_pair.source_scenario_id
            / f"log_map_archive_{item.remote_pair.source_scenario_id}.json",
            map_size_bytes=item.remote_pair.vector_map.size_bytes,
            map_sha256=hashlib.sha256(f"map:{index}".encode()).hexdigest(),
            materialization_cache_key=hashlib.sha256(
                f"cache:{index}".encode()
            ).hexdigest(),
            validation_included=True,
            exclusion_reason=None,
        )
        for index, item in enumerate(selection)
    )
    return build_cohort_manifest(
        units,
        config=MotionCohortConfig(),
        dataset_version="official-s3-phase4-fixture",
        train_candidate_count=600,
        val_candidate_count=400,
        train_source_manifest_identity="b" * 64,
        val_source_manifest_identity="c" * 64,
    )


def test_selection_is_exact_disjoint_and_listing_order_independent(
    selection: tuple[MotionCohortSelectionUnit, ...],
) -> None:
    assert len(selection) == 500
    counts = {
        role: sum(item.cohort_role is role for item in selection) for role in CohortRole
    }
    assert counts == {
        CohortRole.DEVELOPMENT: 150,
        CohortRole.PILOT: 50,
        CohortRole.TEST: 300,
    }
    identifiers = tuple(item.remote_pair.source_scenario_id for item in selection)
    assert len(identifiers) == len(set(identifiers))
    reversed_selection = select_motion_cohort(
        tuple(reversed(_catalog("train", "train", 600))),
        tuple(reversed(_catalog("val", "val", 400))),
        config=MotionCohortConfig(),
    )
    assert reversed_selection == selection


def test_selection_depends_only_on_seed_role_partition_and_source_id() -> None:
    baseline = select_motion_cohort(
        _catalog("train", "train", 600),
        _catalog("val", "val", 400),
        config=MotionCohortConfig(),
    )
    resized = tuple(
        replace(
            pair,
            motion=replace(pair.motion, size_bytes=pair.motion.size_bytes + 999),
        )
        for pair in _catalog("train", "train", 600)
    )
    changed_sizes = select_motion_cohort(
        resized,
        _catalog("val", "val", 400),
        config=MotionCohortConfig(),
    )
    assert tuple(item.remote_pair.source_scenario_id for item in baseline) == tuple(
        item.remote_pair.source_scenario_id for item in changed_sizes
    )


def test_config_and_units_are_frozen_and_reject_wrong_contract(
    selection: tuple[MotionCohortSelectionUnit, ...],
) -> None:
    with pytest.raises(FrozenInstanceError):
        selection[0].selection_rank = 9  # type: ignore[misc]
    with pytest.raises(ValidationError, match="role counts"):
        MotionCohortConfig(development_count=149)
    with pytest.raises(ValidationError, match="wrong provider partition"):
        MotionCohortSelectionUnit(
            "val",
            CohortRole.DEVELOPMENT,
            1,
            _pair("wrong-partition", "val"),
        )


def test_manifest_identity_round_trip_and_checksum_sensitivity(
    selection: tuple[MotionCohortSelectionUnit, ...],
) -> None:
    manifest = _manifest(selection)
    text = motion_cohort_manifest_to_canonical_json(manifest)
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert motion_cohort_manifest_from_json(text) == manifest
    changed = _manifest(selection, first_motion_sha="d" * 64)
    assert changed.cohort_identity != manifest.cohort_identity
    with pytest.raises(FrozenInstanceError):
        manifest.dataset_version = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("mutation", ("missing", "unknown", "noncanonical"))
def test_manifest_strict_deserialization(
    selection: tuple[MotionCohortSelectionUnit, ...],
    mutation: str,
) -> None:
    manifest = _manifest(selection)
    payload = json.loads(motion_cohort_manifest_to_canonical_json(manifest))
    if mutation == "missing":
        payload.pop("dataset_version")
        text = canonical_json_text(payload, trailing_newline=True)
    elif mutation == "unknown":
        payload["unknown"] = True
        text = canonical_json_text(payload, trailing_newline=True)
    else:
        text = json.dumps(payload) + "\n"
    with pytest.raises(SchemaError):
        motion_cohort_manifest_from_json(text)


def test_dataset_version_is_sensitive_to_both_partition_catalogs() -> None:
    baseline = cohort_dataset_version("a" * 64, "b" * 64)
    assert baseline == cohort_dataset_version("a" * 64, "b" * 64)
    assert baseline != cohort_dataset_version("c" * 64, "b" * 64)
    assert baseline != cohort_dataset_version("a" * 64, "c" * 64)


def test_role_acquisition_and_pilot_plans_preserve_frozen_source(
    selection: tuple[MotionCohortSelectionUnit, ...],
) -> None:
    unit = next(
        item for item in selection if item.cohort_role is CohortRole.DEVELOPMENT
    )
    config, plan = build_role_acquisition_plan(
        (unit,),
        config=MotionCohortConfig(),
        dataset_version="official-s3-phase4-fixture",
        catalog_identity="a" * 64,
        candidate_count=600,
        local_relative_root=Path("data/external/av2_motion_phase4/train"),
        backend="anonymous_s3_http",
    )
    source_root = config.local_relative_root / plan.dataset_version
    acquired = tuple(
        av2_acquisition.Av2AcquiredFile(
            remote_uri=remote.remote_uri,
            relative_path=source_root / remote.relative_key,
            size_bytes=remote.size_bytes,
            sha256=hashlib.sha256(remote.relative_key.encode()).hexdigest(),
            disposition="downloaded",
        )
        for remote in (unit.remote_pair.motion, unit.remote_pair.vector_map)
    )
    report = av2_acquisition.Av2AcquisitionReport(
        schema_version="1.0",
        plan_identity=av2_acquisition.av2_acquisition_plan_identity(plan),
        backend="anonymous_s3_http",
        backend_version="fixture",
        selected_scenario_count=1,
        candidate_count=600,
        downloaded_file_count=2,
        reused_file_count=0,
        downloaded_bytes=150,
        reused_bytes=0,
        total_local_bytes=150,
        listing_seconds=0.1,
        download_seconds=0.2,
        acquired_files=tuple(sorted(acquired, key=lambda item: item.relative_path)),
    )
    materialization = build_role_pilot_plan(
        (unit,),
        report,
        config=MotionCohortConfig(),
        dataset_version=plan.dataset_version,
        candidate_count=600,
        source_relative_root=source_root,
        source_manifest_identity="b" * 64,
    )
    assert isinstance(materialization, pilot.Av2PilotPlan)
    assert materialization.selected_scenarios[0].source_scenario_id == (
        unit.remote_pair.source_scenario_id
    )
    assert materialization.config.canonical_split_name == "development"


def _measurements(kind: str = "fixture") -> CohortRunMeasurements:
    return CohortRunMeasurements(
        run_kind=kind,
        scenario_count=25,
        trajectory_count=100,
        materialized_count=25,
        reused_count=0,
        acquisition_verification_seconds=1.0,
        conversion_seconds=2.0,
        validation_seconds=3.0,
        total_seconds=6.0,
        peak_process_memory_bytes=1_000_000,
        source_bytes=2_000_000,
        cache_bytes=3_000_000,
        disk_free_before_bytes=10_000_000,
        disk_free_after_bytes=5_000_000,
        worker_count=1,
        cpu_only=True,
        gpu_use_count=0,
    )


def test_measurement_validation_and_round_trip() -> None:
    value = _measurements()
    text = cohort_run_measurements_to_canonical_json(value)
    assert cohort_run_measurements_from_json(text) == value
    assert value.scenarios_per_second == 25 / 6
    with pytest.raises(ValidationError, match="cover scenarios"):
        replace(value, reused_count=1)


def test_validation_summary_and_resource_pilot_round_trip() -> None:
    distribution = DistributionSummary(3, 1.0, 1.5, 2.0, 2.5, 3.0)
    summary = CohortValidationSummary(
        "development",
        150,
        149,
        1000,
        900,
        1000,
        900,
        10_000,
        9_000,
        5_000,
        4_900,
        1_000,
        900,
        (("insufficient_samples", 100),),
        (("vehicle", 1000),),
        (("vehicle", 900),),
        (("MIA", 150),),
        (("MIA", 149),),
        distribution,
        distribution,
    )
    text = cohort_validation_summary_to_canonical_json(summary)
    assert cohort_validation_summary_from_json(text) == summary
    result = ResourcePilotResult(
        tuple(f"scenario-{index:02d}" for index in range(25)),
        10,
        5,
        10,
        _measurements(),
        120.0,
        100.0,
        60_000_000,
        55_000_000,
    )
    resource_text = resource_pilot_result_to_canonical_json(result)
    assert resource_pilot_result_from_json(resource_text) == result


def test_catalog_inspection_fallback_honors_requested_train_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = av2_acquisition._Backend("s5cmd", "fixture", Path("s5cmd"))
    monkeypatch.setattr(av2_acquisition, "_resolve_backend", lambda _value: backend)
    monkeypatch.setattr(
        av2_acquisition,
        "_s5cmd_list",
        lambda *_args: (_ for _ in ()).throw(ArtifactError("listing failed")),
    )
    monkeypatch.setattr(
        av2_acquisition, "_http_partitions", lambda _root: ("train", "val")
    )
    monkeypatch.setattr(
        av2_acquisition,
        "_http_objects",
        lambda _root, partition: tuple(
            remote
            for pair in _catalog("train", partition, 300)
            for remote in (pair.motion, pair.vector_map)
        ),
    )
    config = av2_acquisition.Av2AcquisitionConfig(
        partition="train",
        scenario_count=300,
        backend="auto",
    )
    pairs, name, _version, _seconds = av2_acquisition.inspect_av2_remote_catalog(config)
    assert name == "anonymous_s3_http"
    assert len(pairs) == 300
    assert all(pair.motion.relative_key.startswith("train/") for pair in pairs)
