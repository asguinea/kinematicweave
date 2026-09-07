"""Focused tests for resumable deterministic data materialization."""

import ast
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, cast

import pytest

from kinematicweave.artifact_store import (
    WrittenArtifact,
    finalize_run_directory,
    prepare_run_directory,
)
from kinematicweave.data import materialization
from kinematicweave.data.materialization import (
    IncompleteEntryPolicy,
    MaterializationArtifacts,
    MaterializationCacheEntry,
    MaterializationCacheInventory,
    MaterializationCacheInventoryEntry,
    MaterializationDisposition,
    MaterializationOutput,
    MaterializationPlan,
    MaterializationRunReport,
    MaterializationUnitResult,
    MaterializationUnitSpec,
)
from kinematicweave.errors import (
    ArtifactError,
    ResourceLimitError,
    SchemaError,
    ValidationError,
)

EXPECTED_PUBLIC_API = [
    "IncompleteEntryPolicy",
    "MaterializationArtifacts",
    "MaterializationCacheEntry",
    "MaterializationCacheInventory",
    "MaterializationCacheInventoryEntry",
    "MaterializationDisposition",
    "MaterializationOutput",
    "MaterializationPlan",
    "MaterializationRunReport",
    "MaterializationUnitResult",
    "MaterializationUnitSpec",
    "execute_materialization_plan",
    "materialization_cache_entry_from_dict",
    "materialization_cache_entry_from_json",
    "materialization_cache_entry_to_canonical_json",
    "materialization_cache_entry_to_dict",
    "materialization_cache_inventory_from_dict",
    "materialization_cache_inventory_from_json",
    "materialization_cache_inventory_to_canonical_json",
    "materialization_cache_inventory_to_dict",
    "materialization_output_to_dict",
    "materialization_plan_from_dict",
    "materialization_plan_from_json",
    "materialization_plan_identity",
    "materialization_plan_to_canonical_json",
    "materialization_plan_to_dict",
    "materialization_run_report_from_dict",
    "materialization_run_report_from_json",
    "materialization_run_report_to_canonical_json",
    "materialization_run_report_to_dict",
    "materialization_unit_cache_key",
    "materialization_unit_spec_to_dict",
    "materialize_cached_unit",
    "materialize_materialization_artifacts",
    "prune_incomplete_materialization_cache",
    "scan_materialization_cache",
    "verify_materialization_artifacts",
    "verify_materialization_cache_entry",
]

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
CACHE_ROOT = Path("data/cache/materialization")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    return root.resolve()


def _unit(
    index: int = 1,
    *,
    unit_id: str | None = None,
    operation_version: str = "1.0",
    input_identity: str = HASH_A,
    parameter_identity: str = HASH_B,
    outputs: tuple[str, ...] = ("nested/data.txt", "payload.bin"),
    estimated_output_bytes: int = 64,
) -> MaterializationUnitSpec:
    return MaterializationUnitSpec(
        unit_id=unit_id or f"unit:materialization:{index}",
        operation_name="canonicalize",
        operation_version=operation_version,
        input_identity=input_identity,
        parameter_identity=parameter_identity,
        expected_output_paths=outputs,
        estimated_output_bytes=estimated_output_bytes,
    )


def _plan(count: int = 3) -> MaterializationPlan:
    return MaterializationPlan(
        schema_version="1.0",
        dataset_id="synthetic",
        dataset_version="1.0",
        units=tuple(
            _unit(
                index,
                input_identity=f"{index:x}" * 64,
            )
            for index in range(1, count + 1)
        ),
    )


def _content(unit: MaterializationUnitSpec, path: Path) -> bytes:
    if path.suffix == ".bin":
        return bytes([len(unit.unit_id) % 256, len(path.parts)]) + b"\x00\xff"
    return f"{unit.unit_id}\n{path.as_posix()}\n".encode()


def _worker(
    calls: list[str] | None = None,
) -> Any:
    def write(unit: MaterializationUnitSpec, root: Path) -> None:
        if calls is not None:
            calls.append(unit.unit_id)
        for raw_path in unit.expected_output_paths:
            path = Path(raw_path)
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_content(unit, path))

    return write


def _entry_directory(repository: Path, unit: MaterializationUnitSpec) -> Path:
    key = materialization.materialization_unit_cache_key(unit)
    return repository / CACHE_ROOT / "entries" / key[:2] / key


def _partial_directory(repository: Path, unit: MaterializationUnitSpec) -> Path:
    key = materialization.materialization_unit_cache_key(unit)
    return repository / CACHE_ROOT / ".temporary" / f"{key}.partial"


def _materialize(
    repository: Path,
    unit: MaterializationUnitSpec | None = None,
    *,
    worker: Any | None = None,
) -> MaterializationUnitResult:
    return materialization.materialize_cached_unit(
        repository,
        CACHE_ROOT,
        unit or _unit(),
        worker or _worker(),
        reserve_fraction=0,
    )


def _report_and_inventory(
    repository: Path,
    plan: MaterializationPlan | None = None,
) -> tuple[
    MaterializationPlan,
    MaterializationRunReport,
    MaterializationCacheInventory,
]:
    selected = plan or _plan()
    report = materialization.execute_materialization_plan(
        repository,
        CACHE_ROOT,
        selected,
        _worker(),
        reserve_fraction=0,
    )
    inventory = materialization.scan_materialization_cache(repository, CACHE_ROOT)
    return selected, report, inventory


def _written_artifact(path: Path, repository: Path) -> WrittenArtifact:
    data = path.read_bytes()
    return WrittenArtifact(
        relative_path=path.relative_to(repository),
        size_bytes=len(data),
        content_checksum=hashlib.sha256(data).hexdigest(),
    )


def test_exact_public_api_enum_values_and_model_fields() -> None:
    assert materialization.__all__ == EXPECTED_PUBLIC_API
    assert tuple(item.value for item in MaterializationDisposition) == (
        "materialized",
        "reused",
    )
    assert tuple(item.value for item in IncompleteEntryPolicy) == ("restart", "error")
    expected_fields = {
        MaterializationUnitSpec: (
            "unit_id",
            "operation_name",
            "operation_version",
            "input_identity",
            "parameter_identity",
            "expected_output_paths",
            "estimated_output_bytes",
        ),
        MaterializationPlan: (
            "schema_version",
            "dataset_id",
            "dataset_version",
            "units",
        ),
        MaterializationOutput: ("relative_path", "size_bytes", "sha256"),
        MaterializationCacheEntry: (
            "schema_version",
            "cache_key",
            "unit",
            "outputs",
        ),
        MaterializationUnitResult: (
            "unit_id",
            "cache_key",
            "disposition",
            "entry_relative_directory",
            "outputs",
        ),
        MaterializationRunReport: (
            "schema_version",
            "dataset_id",
            "dataset_version",
            "plan_identity",
            "cache_relative_root",
            "results",
        ),
        MaterializationCacheInventoryEntry: (
            "cache_key",
            "unit_id",
            "entry_relative_directory",
            "output_count",
            "total_output_bytes",
        ),
        MaterializationCacheInventory: (
            "schema_version",
            "cache_relative_root",
            "entries",
            "incomplete_entry_directories",
        ),
        MaterializationArtifacts: (
            "plan_manifest",
            "run_report",
            "cache_inventory",
        ),
    }
    for model, names in expected_fields.items():
        assert tuple(field.name for field in fields(model)) == names
        assert hasattr(model, "__slots__")


def test_models_are_frozen_copy_sequences_and_coerce_text_and_enums() -> None:
    paths = ["nested\\data.txt", "payload.bin"]
    unit = MaterializationUnitSpec(
        unit_id=" unit:model ",
        operation_name=" operation ",
        operation_version=" version ",
        input_identity=HASH_A,
        parameter_identity=HASH_B,
        expected_output_paths=paths,
        estimated_output_bytes=0,
    )
    paths.append("later.txt")
    assert unit.unit_id == "unit:model"
    assert unit.operation_name == "operation"
    assert unit.operation_version == "version"
    assert unit.expected_output_paths == (
        Path("nested/data.txt"),
        Path("payload.bin"),
    )
    output = MaterializationOutput("nested\\data.txt", 1, HASH_C)
    result = MaterializationUnitResult(
        "unit:model",
        HASH_A,
        "reused",
        "cache\\entry",
        (output,),
    )
    assert result.disposition is MaterializationDisposition.REUSED
    assert result.entry_relative_directory == Path("cache/entry")
    with pytest.raises(FrozenInstanceError):
        cast(Any, unit).unit_id = "unit:changed"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"input_identity": "A" * 64}, "input_identity"),
        ({"parameter_identity": "a" * 63}, "parameter_identity"),
        ({"estimated_output_bytes": True}, "non-Boolean"),
        ({"estimated_output_bytes": -1}, "negative"),
        ({"operation_name": " "}, "operation_name"),
        ({"operation_version": ""}, "operation_version"),
        ({"expected_output_paths": ()}, "must not be empty"),
        ({"expected_output_paths": ("x", "x")}, "duplicates"),
        ({"expected_output_paths": ("../x",)}, "parent traversal"),
        ({"expected_output_paths": ("C:\\absolute.txt",)}, "repository-relative"),
        ({"expected_output_paths": (".temporary/x",)}, "reserved"),
        ({"expected_output_paths": ("cache_entry.json",)}, "reserved"),
        ({"expected_output_paths": ("x", "x/y")}, "path-prefix"),
    ],
)
def test_unit_validation(changes: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "unit_id": "unit:validation",
        "operation_name": "operation",
        "operation_version": "1",
        "input_identity": HASH_A,
        "parameter_identity": HASH_B,
        "expected_output_paths": ("data.txt",),
        "estimated_output_bytes": 0,
    }
    values.update(changes)
    with pytest.raises(ValidationError, match=message):
        MaterializationUnitSpec(**cast(Any, values))


def test_plan_rejects_duplicate_unit_ids_and_cache_keys() -> None:
    first = _unit()
    with pytest.raises(ValidationError, match="unit identifiers"):
        MaterializationPlan("1.0", "data", "1", (first, first))
    equivalent = replace(first, unit_id="unit:equivalent")
    with pytest.raises(ValidationError, match="cache keys"):
        MaterializationPlan("1.0", "data", "1", (first, equivalent))


def test_cache_entry_contract_and_derived_properties() -> None:
    unit = _unit(outputs=("a.txt", "b.bin"))
    outputs = (
        MaterializationOutput("a.txt", 3, HASH_A),
        MaterializationOutput("b.bin", 5, HASH_B),
    )
    entry = MaterializationCacheEntry(
        "1.0",
        materialization.materialization_unit_cache_key(unit),
        unit,
        outputs,
    )
    assert entry.output_count == 2
    assert entry.total_output_bytes == 8
    with pytest.raises(ValidationError, match="exactly match"):
        replace(entry, outputs=tuple(reversed(outputs)))
    with pytest.raises(ValidationError, match="cache_key"):
        replace(entry, cache_key=HASH_C)


def test_report_inventory_invariants_and_derived_properties() -> None:
    output = MaterializationOutput("a.txt", 7, HASH_A)
    result = MaterializationUnitResult(
        "unit:one", HASH_A, "materialized", "cache/one", (output,)
    )
    report = MaterializationRunReport("1.0", "data", "1", HASH_B, "cache", (result,))
    assert (
        report.unit_count,
        report.materialized_count,
        report.reused_count,
        report.total_output_bytes,
    ) == (1, 1, 0, 7)
    inventory_entry = MaterializationCacheInventoryEntry(
        HASH_A, "unit:one", "cache/one", 1, 7
    )
    inventory = MaterializationCacheInventory(
        "1.0",
        "cache",
        (inventory_entry,),
        (f"cache/.temporary/{HASH_B}.partial",),
    )
    assert (
        inventory.complete_entry_count,
        inventory.incomplete_entry_count,
        inventory.total_output_bytes,
    ) == (1, 1, 7)
    with pytest.raises(ValidationError, match="lexicographically"):
        MaterializationCacheInventory(
            "1.0",
            "cache",
            (
                replace(inventory_entry, cache_key=HASH_B, unit_id="unit:two"),
                inventory_entry,
            ),
            (),
        )
    with pytest.raises(ValidationError, match=r"direct \.partial"):
        replace(inventory, incomplete_entry_directories=("cache/not-partial",))


def test_boolean_counts_are_rejected() -> None:
    with pytest.raises(ValidationError, match="non-Boolean"):
        MaterializationOutput("a", True, HASH_A)
    with pytest.raises(ValidationError, match="non-Boolean"):
        MaterializationCacheInventoryEntry(HASH_A, "unit:one", "cache/one", True, 0)


def test_cache_key_identity_inputs() -> None:
    base = _unit()
    key = materialization.materialization_unit_cache_key(base)
    assert materialization.materialization_unit_cache_key(base) == key
    assert (
        materialization.materialization_unit_cache_key(
            replace(base, unit_id="unit:other")
        )
        == key
    )
    assert (
        materialization.materialization_unit_cache_key(
            replace(base, estimated_output_bytes=999)
        )
        == key
    )
    for changed in (
        replace(base, operation_version="2"),
        replace(base, input_identity=HASH_C),
        replace(base, parameter_identity=HASH_C),
        replace(base, expected_output_paths=("different.txt",)),
    ):
        assert materialization.materialization_unit_cache_key(changed) != key


def test_plan_identity_is_deterministic_and_order_sensitive() -> None:
    plan = _plan(2)
    assert materialization.materialization_plan_identity(plan) == (
        materialization.materialization_plan_identity(plan)
    )
    reversed_plan = replace(plan, units=tuple(reversed(plan.units)))
    assert materialization.materialization_plan_identity(reversed_plan) != (
        materialization.materialization_plan_identity(plan)
    )


def _serialization_cases() -> tuple[
    tuple[Any, Any, Any, Any],
    ...,
]:
    unit = _unit(outputs=("a.txt",))
    output = MaterializationOutput("a.txt", 3, HASH_C)
    entry = MaterializationCacheEntry(
        "1.0",
        materialization.materialization_unit_cache_key(unit),
        unit,
        (output,),
    )
    result = MaterializationUnitResult(
        unit.unit_id,
        entry.cache_key,
        "materialized",
        f"cache/entries/{entry.cache_key[:2]}/{entry.cache_key}",
        (output,),
    )
    plan = MaterializationPlan("1.0", "data", "1", (unit,))
    report = MaterializationRunReport(
        "1.0",
        "data",
        "1",
        materialization.materialization_plan_identity(plan),
        "cache",
        (result,),
    )
    inventory = MaterializationCacheInventory(
        "1.0",
        "cache",
        (
            MaterializationCacheInventoryEntry(
                entry.cache_key,
                unit.unit_id,
                result.entry_relative_directory,
                1,
                3,
            ),
        ),
        (),
    )
    return (
        (
            plan,
            materialization.materialization_plan_to_dict,
            materialization.materialization_plan_to_canonical_json,
            materialization.materialization_plan_from_json,
        ),
        (
            entry,
            materialization.materialization_cache_entry_to_dict,
            materialization.materialization_cache_entry_to_canonical_json,
            materialization.materialization_cache_entry_from_json,
        ),
        (
            report,
            materialization.materialization_run_report_to_dict,
            materialization.materialization_run_report_to_canonical_json,
            materialization.materialization_run_report_from_json,
        ),
        (
            inventory,
            materialization.materialization_cache_inventory_to_dict,
            materialization.materialization_cache_inventory_to_canonical_json,
            materialization.materialization_cache_inventory_from_json,
        ),
    )


@pytest.mark.parametrize(
    ("record", "to_dict", "to_json", "from_json"),
    _serialization_cases(),
)
def test_serialization_is_deterministic_plain_and_round_trips(
    record: object,
    to_dict: Any,
    to_json: Any,
    from_json: Any,
) -> None:
    mapping = to_dict(record)
    encoded = to_json(record)
    assert encoded == to_json(record)
    assert encoded.endswith("\n") and not encoded.endswith("\n\n")
    assert from_json(encoded) == record
    assert json.loads(encoded) == mapping

    def assert_plain(value: object) -> None:
        assert not isinstance(value, (Path, tuple, MaterializationDisposition))
        if isinstance(value, dict):
            for key, item in value.items():
                assert isinstance(key, str)
                assert_plain(item)
        elif isinstance(value, list):
            for item in value:
                assert_plain(item)

    assert_plain(mapping)


def test_dictionary_field_order_and_caller_mutation() -> None:
    plan = _plan(1)
    mapping = materialization.materialization_plan_to_dict(plan)
    assert tuple(mapping) == (
        "schema_version",
        "dataset_id",
        "dataset_version",
        "units",
    )
    unit_mapping = cast(list[dict[str, object]], mapping["units"])[0]
    assert tuple(unit_mapping) == tuple(
        field.name for field in fields(MaterializationUnitSpec)
    )
    cast(list[str], unit_mapping["expected_output_paths"]).append("mutation.txt")
    assert len(plan.units[0].expected_output_paths) == 2


@pytest.mark.parametrize("text", ("{", "[]", "null", '"text"'))
def test_json_rejects_malformed_or_non_object_roots(text: str) -> None:
    with pytest.raises(SchemaError):
        materialization.materialization_plan_from_json(text)


def test_strict_deserialization_rejects_unknown_missing_and_invalid_enum() -> None:
    plan = _plan(1)
    mapping = materialization.materialization_plan_to_dict(plan)
    mapping["unknown"] = 1
    with pytest.raises(SchemaError, match="unknown"):
        materialization.materialization_plan_from_dict(mapping)
    mapping = materialization.materialization_plan_to_dict(plan)
    del mapping["dataset_id"]
    with pytest.raises(SchemaError, match="missing"):
        materialization.materialization_plan_from_dict(mapping)
    _, report, _ = _report_objects_without_io(plan)
    report_mapping = materialization.materialization_run_report_to_dict(report)
    cast(list[dict[str, object]], report_mapping["results"])[0]["disposition"] = "bad"
    with pytest.raises(SchemaError, match="invalid value"):
        materialization.materialization_run_report_from_dict(report_mapping)


def _report_objects_without_io(
    plan: MaterializationPlan,
) -> tuple[
    MaterializationPlan,
    MaterializationRunReport,
    MaterializationCacheInventory,
]:
    results: list[MaterializationUnitResult] = []
    inventory_entries: list[MaterializationCacheInventoryEntry] = []
    for unit in plan.units:
        key = materialization.materialization_unit_cache_key(unit)
        output = MaterializationOutput(unit.expected_output_paths[0], 1, HASH_A)
        directory = CACHE_ROOT / "entries" / key[:2] / key
        results.append(
            MaterializationUnitResult(unit.unit_id, key, "reused", directory, (output,))
        )
        inventory_entries.append(
            MaterializationCacheInventoryEntry(key, unit.unit_id, directory, 1, 1)
        )
    report = MaterializationRunReport(
        "1.0",
        plan.dataset_id,
        plan.dataset_version,
        materialization.materialization_plan_identity(plan),
        CACHE_ROOT,
        tuple(results),
    )
    inventory = MaterializationCacheInventory(
        "1.0",
        CACHE_ROOT,
        tuple(sorted(inventory_entries, key=lambda item: item.cache_key)),
        (),
    )
    return plan, report, inventory


def test_successful_nested_materialization_and_verified_reuse(repository: Path) -> None:
    calls: list[str] = []
    unit = _unit()
    first = _materialize(repository, unit, worker=_worker(calls))
    assert first.disposition is MaterializationDisposition.MATERIALIZED
    assert first.unit_id == unit.unit_id
    assert tuple(output.relative_path for output in first.outputs) == (
        Path("nested/data.txt"),
        Path("payload.bin"),
    )
    assert first.total_output_bytes == sum(
        len(_content(unit, Path(path))) for path in unit.expected_output_paths
    )
    entry_directory = _entry_directory(repository, unit)
    manifest_text = (entry_directory / "cache_entry.json").read_text(encoding="utf-8")
    entry = materialization.verify_materialization_cache_entry(
        repository,
        entry_directory.relative_to(repository),
        expected_unit=unit,
    )
    assert (
        manifest_text
        == materialization.materialization_cache_entry_to_canonical_json(entry)
    )
    assert manifest_text.endswith("\n") and not manifest_text.endswith("\n\n")
    before = {
        path: path.read_bytes() for path in entry_directory.rglob("*") if path.is_file()
    }
    second = _materialize(repository, unit, worker=_worker(calls))
    assert second.disposition is MaterializationDisposition.REUSED
    assert calls == [unit.unit_id]
    assert before == {
        path: path.read_bytes() for path in entry_directory.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize(
    ("worker", "match"),
    [
        (lambda unit, root: "not-none", "return None"),
        (lambda unit, root: None, "missing"),
    ],
)
def test_invalid_worker_completion_leaves_no_complete_entry(
    repository: Path,
    worker: Any,
    match: str,
) -> None:
    unit = _unit()
    with pytest.raises(ArtifactError, match=match):
        _materialize(repository, unit, worker=worker)
    assert not _entry_directory(repository, unit).exists()
    assert not _partial_directory(repository, unit).exists()


def test_worker_unexpected_file_and_directory_are_rejected(repository: Path) -> None:
    unit = _unit(outputs=("expected.txt",))

    def extra_file(spec: MaterializationUnitSpec, root: Path) -> None:
        (root / "expected.txt").write_text("ok", encoding="utf-8")
        (root / "extra.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(ArtifactError, match="unexpected regular file"):
        _materialize(repository, unit, worker=extra_file)

    def extra_directory(spec: MaterializationUnitSpec, root: Path) -> None:
        (root / "expected.txt").write_text("ok", encoding="utf-8")
        (root / "extra").mkdir()

    with pytest.raises(ArtifactError, match="unexpected directory"):
        _materialize(repository, unit, worker=extra_directory)


def test_worker_created_symlink_is_rejected_when_supported(
    repository: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")
    probe = tmp_path / "probe"
    try:
        probe.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    probe.unlink()
    unit = _unit(outputs=("output.txt",))

    def symlink_worker(spec: MaterializationUnitSpec, root: Path) -> None:
        (root / "output.txt").symlink_to(target)

    with pytest.raises(ArtifactError, match="symbolic link"):
        _materialize(repository, unit, worker=symlink_worker)


def test_worker_exception_is_preserved_and_cleanup_is_best_effort(
    repository: Path,
) -> None:
    failure = RuntimeError("controlled worker failure")

    def failing_worker(unit: MaterializationUnitSpec, root: Path) -> None:
        (root / "partial.txt").write_text("partial", encoding="utf-8")
        raise failure

    with pytest.raises(RuntimeError) as caught:
        _materialize(repository, worker=failing_worker)
    assert caught.value is failure
    assert not _entry_directory(repository, _unit()).exists()
    assert not _partial_directory(repository, _unit()).exists()


def test_disk_preflight_failure_creates_no_working_directory(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        raise ResourceLimitError("insufficient space")

    monkeypatch.setattr(materialization, "check_disk_space", reject)
    with pytest.raises(ResourceLimitError, match="insufficient"):
        _materialize(repository)
    assert not (repository / CACHE_ROOT).exists()


@pytest.mark.parametrize("value", (True, -0.1, 1, float("inf"), float("nan")))
def test_invalid_reserve_fraction(repository: Path, value: object) -> None:
    with pytest.raises(ValidationError, match="reserve_fraction"):
        materialization.materialize_cached_unit(
            repository,
            CACHE_ROOT,
            _unit(),
            _worker(),
            reserve_fraction=cast(Any, value),
        )


def test_corrupt_completed_manifest_is_not_repaired(repository: Path) -> None:
    unit = _unit()
    _materialize(repository, unit)
    manifest_path = _entry_directory(repository, unit) / "cache_entry.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    calls: list[str] = []
    with pytest.raises(SchemaError):
        _materialize(repository, unit, worker=_worker(calls))
    assert calls == []
    assert manifest_path.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize("alteration", ("size", "checksum"))
def test_altered_output_is_rejected_without_repair(
    repository: Path,
    alteration: str,
) -> None:
    unit = _unit()
    _materialize(repository, unit)
    output = _entry_directory(repository, unit) / "nested/data.txt"
    original = output.read_bytes()
    output.write_bytes(
        original + b"x" if alteration == "size" else b"X" * len(original)
    )
    with pytest.raises(ArtifactError):
        _materialize(repository, unit, worker=_worker([]))
    assert output.read_bytes() != original


def test_incomplete_error_preserves_and_restart_rebuilds(repository: Path) -> None:
    unit = _unit()
    partial = _partial_directory(repository, unit)
    partial.mkdir(parents=True)
    marker = partial / "stale.txt"
    marker.write_text("stale", encoding="utf-8")
    with pytest.raises(ArtifactError, match="already exists"):
        materialization.materialize_cached_unit(
            repository,
            CACHE_ROOT,
            unit,
            _worker(),
            incomplete_policy="error",
            reserve_fraction=0,
        )
    assert marker.read_text(encoding="utf-8") == "stale"
    result = materialization.materialize_cached_unit(
        repository,
        CACHE_ROOT,
        unit,
        _worker(),
        incomplete_policy=IncompleteEntryPolicy.RESTART,
        reserve_fraction=0,
    )
    assert result.disposition is MaterializationDisposition.MATERIALIZED
    assert not partial.exists()


def test_incomplete_symlink_is_rejected_when_supported(
    repository: Path,
    tmp_path: Path,
) -> None:
    unit = _unit()
    partial = _partial_directory(repository, unit)
    partial.parent.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    try:
        partial.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    with pytest.raises(ArtifactError, match="symbolic link"):
        _materialize(repository, unit)
    assert target.exists()


def test_restart_never_removes_completed_entry(repository: Path) -> None:
    unit = _unit()
    _materialize(repository, unit)
    entry = _entry_directory(repository, unit)
    before = (entry / "nested/data.txt").read_bytes()
    partial = _partial_directory(repository, unit)
    partial.mkdir(parents=True)
    result = materialization.materialize_cached_unit(
        repository,
        CACHE_ROOT,
        unit,
        _worker([]),
        incomplete_policy="restart",
        reserve_fraction=0,
    )
    assert result.disposition is MaterializationDisposition.REUSED
    assert partial.exists()
    assert (entry / "nested/data.txt").read_bytes() == before


def test_competing_valid_entry_becomes_reused(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = _unit()

    def race(source: Any, destination: Any) -> None:
        shutil.copytree(Path(source), Path(destination))
        raise FileExistsError(errno_value(), "competing entry")

    monkeypatch.setattr(os, "rename", race)
    result = _materialize(repository, unit)
    assert result.disposition is MaterializationDisposition.REUSED
    assert not _partial_directory(repository, unit).exists()
    materialization.verify_materialization_cache_entry(
        repository,
        _entry_directory(repository, unit).relative_to(repository),
        expected_unit=unit,
    )


def errno_value() -> int:
    return getattr(os, "EEXIST", 17)


def test_competing_inconsistent_entry_is_rejected(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = _unit()

    def race(source: Any, destination: Any) -> None:
        shutil.copytree(Path(source), Path(destination))
        (Path(destination) / "nested/data.txt").write_bytes(b"corrupt")
        raise FileExistsError(errno_value(), "competing entry")

    monkeypatch.setattr(os, "rename", race)
    with pytest.raises(ArtifactError):
        _materialize(repository, unit)
    assert _entry_directory(repository, unit).exists()
    assert not _partial_directory(repository, unit).exists()


def test_equivalent_key_with_changed_label_is_not_relabelled(repository: Path) -> None:
    original = _unit()
    _materialize(repository, original)
    relabelled = replace(original, unit_id="unit:different-label")
    with pytest.raises(SchemaError, match="expected_unit"):
        _materialize(repository, relabelled, worker=_worker([]))


def test_plan_execution_order_reuse_and_report(repository: Path) -> None:
    plan = _plan()
    calls: list[str] = []
    first = materialization.execute_materialization_plan(
        repository,
        CACHE_ROOT,
        plan,
        _worker(calls),
        reserve_fraction=0,
    )
    assert calls == [unit.unit_id for unit in plan.units]
    assert first.materialized_count == 3
    assert first.reused_count == 0
    assert first.plan_identity == materialization.materialization_plan_identity(plan)
    assert tuple(result.unit_id for result in first.results) == tuple(
        unit.unit_id for unit in plan.units
    )
    calls.clear()
    second = materialization.execute_materialization_plan(
        repository,
        CACHE_ROOT,
        plan,
        _worker(calls),
        reserve_fraction=0,
    )
    assert calls == []
    assert second.materialized_count == 0
    assert second.reused_count == 3


def test_interrupted_plan_preserves_checkpoint_and_resumes(repository: Path) -> None:
    plan = _plan()
    first_bytes: bytes | None = None

    def failing_worker(unit: MaterializationUnitSpec, root: Path) -> None:
        nonlocal first_bytes
        if unit == plan.units[1]:
            raise RuntimeError("controlled interruption")
        _worker()(unit, root)
        if unit == plan.units[0]:
            first_bytes = _content(unit, Path(unit.expected_output_paths[0]))

    with pytest.raises(RuntimeError, match="controlled interruption"):
        materialization.execute_materialization_plan(
            repository,
            CACHE_ROOT,
            plan,
            failing_worker,
            reserve_fraction=0,
        )
    first_path = _entry_directory(repository, plan.units[0]) / Path(
        plan.units[0].expected_output_paths[0]
    )
    assert first_path.read_bytes() == first_bytes
    assert not _entry_directory(repository, plan.units[1]).exists()
    resumed = materialization.execute_materialization_plan(
        repository,
        CACHE_ROOT,
        plan,
        _worker(),
        reserve_fraction=0,
    )
    assert tuple(item.disposition for item in resumed.results) == (
        MaterializationDisposition.REUSED,
        MaterializationDisposition.MATERIALIZED,
        MaterializationDisposition.MATERIALIZED,
    )
    assert first_path.read_bytes() == first_bytes
    all_reused = materialization.execute_materialization_plan(
        repository,
        CACHE_ROOT,
        plan,
        _worker([]),
        reserve_fraction=0,
    )
    assert all(
        result.disposition is MaterializationDisposition.REUSED
        for result in all_reused.results
    )


def test_changed_logical_input_creates_a_different_entry(repository: Path) -> None:
    original = _unit()
    changed = replace(
        original,
        unit_id="unit:changed",
        input_identity=HASH_C,
    )
    _materialize(repository, original)
    _materialize(repository, changed)
    assert _entry_directory(repository, original) != _entry_directory(
        repository, changed
    )
    assert _entry_directory(repository, original).is_dir()
    assert _entry_directory(repository, changed).is_dir()


def test_missing_cache_scan_and_complete_inventory(repository: Path) -> None:
    empty = materialization.scan_materialization_cache(repository, CACHE_ROOT)
    assert empty.entries == ()
    assert empty.incomplete_entry_directories == ()
    plan, report, inventory = _report_and_inventory(repository)
    assert inventory.complete_entry_count == len(plan.units)
    assert tuple(entry.cache_key for entry in inventory.entries) == tuple(
        sorted(result.cache_key for result in report.results)
    )
    expected_bytes = sum(
        sum(len(_content(unit, Path(path))) for path in unit.expected_output_paths)
        for unit in plan.units
    )
    assert inventory.total_output_bytes == expected_bytes


def test_scan_discovers_incomplete_entries_in_lexical_order(repository: Path) -> None:
    for key in (HASH_C, HASH_A):
        partial = repository / CACHE_ROOT / ".temporary" / f"{key}.partial"
        partial.mkdir(parents=True)
        (partial / "work.bin").write_bytes(b"x")
    inventory = materialization.scan_materialization_cache(repository, CACHE_ROOT)
    assert tuple(
        Path(path).name for path in inventory.incomplete_entry_directories
    ) == (
        f"{HASH_A}.partial",
        f"{HASH_C}.partial",
    )


@pytest.mark.parametrize(
    "relative_path",
    (
        "unexpected.txt",
        "entries/bad",
        f".temporary/{HASH_A}.wrong",
    ),
)
def test_scan_rejects_unexpected_cache_structure(
    repository: Path,
    relative_path: str,
) -> None:
    path = repository / CACHE_ROOT / relative_path
    if "." in path.name:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unexpected", encoding="utf-8")
    else:
        path.mkdir(parents=True)
    with pytest.raises(ArtifactError):
        materialization.scan_materialization_cache(repository, CACHE_ROOT)


def test_scan_rejects_corrupt_complete_entry(repository: Path) -> None:
    unit = _unit()
    _materialize(repository, unit)
    (_entry_directory(repository, unit) / "payload.bin").write_bytes(b"changed")
    with pytest.raises(ArtifactError):
        materialization.scan_materialization_cache(repository, CACHE_ROOT)


def test_scan_rejects_symlink_when_supported(
    repository: Path,
    tmp_path: Path,
) -> None:
    cache = repository / CACHE_ROOT
    cache.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    link = cache / "entries"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    with pytest.raises(ArtifactError, match="symbolic link"):
        materialization.scan_materialization_cache(repository, CACHE_ROOT)


def test_prune_dry_run_and_explicit_removal_preserve_complete_entries(
    repository: Path,
) -> None:
    unit = _unit()
    _materialize(repository, unit)
    partial = repository / CACHE_ROOT / ".temporary" / f"{HASH_C}.partial"
    partial.mkdir(parents=True)
    (partial / "work.txt").write_text("work", encoding="utf-8")
    expected = (partial.relative_to(repository).as_posix(),)
    assert (
        materialization.prune_incomplete_materialization_cache(repository, CACHE_ROOT)
        == expected
    )
    assert partial.exists()
    assert (
        materialization.prune_incomplete_materialization_cache(
            repository, CACHE_ROOT, dry_run=False
        )
        == expected
    )
    assert not partial.exists()
    assert _entry_directory(repository, unit).is_dir()
    assert (
        materialization.prune_incomplete_materialization_cache(
            repository, CACHE_ROOT, dry_run=False
        )
        == ()
    )
    with pytest.raises(ValidationError, match="Boolean"):
        materialization.prune_incomplete_materialization_cache(
            repository, CACHE_ROOT, dry_run=cast(Any, 1)
        )


def test_operational_artifacts_exact_paths_bytes_and_verification(
    repository: Path,
) -> None:
    plan, report, inventory = _report_and_inventory(repository)
    run = prepare_run_directory(
        repository,
        "results",
        "run:materialization",
        reserve_fraction=0,
    )
    artifacts = materialization.materialize_materialization_artifacts(
        run, plan, report, inventory
    )
    assert tuple(
        artifact.relative_path.as_posix()
        for artifact in (
            artifacts.plan_manifest,
            artifacts.run_report,
            artifacts.cache_inventory,
        )
    ) == (
        f"{run.path.relative_to(repository).as_posix()}"
        "/artifacts/materialization/materialization_plan.json",
        f"{run.path.relative_to(repository).as_posix()}"
        "/artifacts/materialization/materialization_report.json",
        f"{run.path.relative_to(repository).as_posix()}"
        "/artifacts/materialization/cache_inventory.json",
    )
    assert materialization.verify_materialization_artifacts(repository, artifacts) == (
        plan,
        report,
        inventory,
    )
    for artifact in (
        artifacts.plan_manifest,
        artifacts.run_report,
        artifacts.cache_inventory,
    ):
        data = (repository / artifact.relative_path).read_bytes()
        assert len(data) == artifact.size_bytes
        assert hashlib.sha256(data).hexdigest() == artifact.content_checksum
    assert tuple(run.manifests_path.iterdir()) == ()
    assert not (run.path / "experiment_manifest.json").exists()
    finalize_run_directory(run)


def test_cross_object_mismatch_prevents_all_writes(repository: Path) -> None:
    plan, report, inventory = _report_and_inventory(repository)
    run = prepare_run_directory(
        repository, "results", "run:mismatch", reserve_fraction=0
    )
    bad_report = replace(report, dataset_version="different")
    with pytest.raises(ValidationError, match="dataset"):
        materialization.materialize_materialization_artifacts(
            run, plan, bad_report, inventory
        )
    assert tuple(run.artifacts_path.iterdir()) == ()


def test_operational_artifact_overwrite_and_finalized_run_rejection(
    repository: Path,
) -> None:
    plan, report, inventory = _report_and_inventory(repository)
    run = prepare_run_directory(
        repository, "results", "run:immutable", reserve_fraction=0
    )
    materialization.materialize_materialization_artifacts(run, plan, report, inventory)
    with pytest.raises(ArtifactError, match="already exists"):
        materialization.materialize_materialization_artifacts(
            run, plan, report, inventory
        )
    finalize_run_directory(run)
    with pytest.raises(ArtifactError, match="immutable"):
        materialization.materialize_materialization_artifacts(
            run,
            plan,
            report,
            inventory,
            relative_directory="artifacts/other",
        )


@pytest.mark.parametrize(
    "artifact_name", ("plan_manifest", "run_report", "cache_inventory")
)
def test_altered_operational_artifact_is_rejected(
    repository: Path,
    artifact_name: str,
) -> None:
    plan, report, inventory = _report_and_inventory(repository)
    run = prepare_run_directory(
        repository, "results", f"run:altered:{artifact_name}", reserve_fraction=0
    )
    artifacts = materialization.materialize_materialization_artifacts(
        run, plan, report, inventory
    )
    artifact = cast(WrittenArtifact, getattr(artifacts, artifact_name))
    path = repository / artifact.relative_path
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ArtifactError):
        materialization.verify_materialization_artifacts(repository, artifacts)


def test_malformed_operational_content_with_matching_metadata_is_schema_error(
    repository: Path,
) -> None:
    plan, report, inventory = _report_and_inventory(repository)
    run = prepare_run_directory(
        repository, "results", "run:malformed", reserve_fraction=0
    )
    artifacts = materialization.materialize_materialization_artifacts(
        run, plan, report, inventory
    )
    path = repository / artifacts.run_report.relative_path
    path.write_text("{}\n", encoding="utf-8")
    changed = replace(
        artifacts,
        run_report=_written_artifact(path, repository),
    )
    with pytest.raises(SchemaError):
        materialization.verify_materialization_artifacts(repository, changed)


def test_three_unit_integration_materialize_artifact_reuse_and_finalize(
    repository: Path,
) -> None:
    plan = _plan()
    calls: list[str] = []
    first = materialization.execute_materialization_plan(
        repository, CACHE_ROOT, plan, _worker(calls), reserve_fraction=0
    )
    assert first.materialized_count == 3
    inventory = materialization.scan_materialization_cache(repository, CACHE_ROOT)
    before = {
        path.relative_to(repository): path.read_bytes()
        for path in (repository / CACHE_ROOT).rglob("*")
        if path.is_file()
    }
    run = prepare_run_directory(
        repository, "results", "run:integration:first", reserve_fraction=0
    )
    artifacts = materialization.materialize_materialization_artifacts(
        run, plan, first, inventory
    )
    materialization.verify_materialization_artifacts(repository, artifacts)
    calls.clear()
    second = materialization.execute_materialization_plan(
        repository, CACHE_ROOT, plan, _worker(calls), reserve_fraction=0
    )
    assert second.reused_count == 3
    assert calls == []
    assert before == {
        path.relative_to(repository): path.read_bytes()
        for path in (repository / CACHE_ROOT).rglob("*")
        if path.is_file()
    }
    finalize_run_directory(run)
    assert not tuple((repository / CACHE_ROOT).rglob("*.partial"))


def test_incomplete_state_integration_error_restart_scan_and_prune(
    repository: Path,
) -> None:
    unit = _unit()
    stale = _partial_directory(repository, unit)
    stale.mkdir(parents=True)
    (stale / "stale.bin").write_bytes(b"stale")
    with pytest.raises(ArtifactError):
        materialization.materialize_cached_unit(
            repository,
            CACHE_ROOT,
            unit,
            _worker(),
            incomplete_policy="error",
            reserve_fraction=0,
        )
    assert stale.exists()
    completed = materialization.materialize_cached_unit(
        repository,
        CACHE_ROOT,
        unit,
        _worker(),
        incomplete_policy="restart",
        reserve_fraction=0,
    )
    assert completed.disposition is MaterializationDisposition.MATERIALIZED
    another = repository / CACHE_ROOT / ".temporary" / f"{HASH_C}.partial"
    another.mkdir(parents=True)
    inventory = materialization.scan_materialization_cache(repository, CACHE_ROOT)
    assert inventory.complete_entry_count == 1
    assert inventory.incomplete_entry_count == 1
    assert materialization.prune_incomplete_materialization_cache(
        repository, CACHE_ROOT
    ) == (another.relative_to(repository).as_posix(),)
    assert another.exists()
    materialization.prune_incomplete_materialization_cache(
        repository, CACHE_ROOT, dry_run=False
    )
    assert not another.exists()
    materialization.verify_materialization_cache_entry(
        repository,
        _entry_directory(repository, unit).relative_to(repository),
        expected_unit=unit,
    )


def test_module_imports_and_source_have_no_prohibited_runtime_behavior() -> None:
    source_path = Path(materialization.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint(
        {
            "argoverse",
            "av2",
            "geopandas",
            "networkx",
            "numpy",
            "pandas",
            "polars",
            "pyarrow",
            "rerun",
            "shapely",
            "torch",
        }
    )
    prohibited_calls = {"Popen", "run", "call", "check_call", "check_output", "urlopen"}
    top_level_calls: set[str] = set()
    for statement in tree.body:
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
        ):
            top_level_calls.add(statement.value.func.id)
    assert top_level_calls.isdisjoint(prohibited_calls)
    assert "logging" not in imported_roots
