"""Tests for dataset registry and local source manifests."""

import ast
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import importlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pytest

import kinematicweave.artifact_store as artifact_store
from kinematicweave.data import registry
from kinematicweave.errors import (
    ArtifactError,
    ResourceLimitError,
    SchemaError,
    ValidationError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "kinematicweave" / "data" / "registry.py"
TIMESTAMP = "2026-07-25T00:00:00.000000Z"
SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture(autouse=True)
def _refresh_registry_module() -> None:
    """Use classes current after import-safety tests reload dependencies."""
    importlib.reload(registry)


def _external_entry() -> registry.DatasetRegistryEntry:
    return registry.get_dataset_registry_entry("av2_motion")


def _generated_entry() -> registry.DatasetRegistryEntry:
    return registry.get_dataset_registry_entry("synthetic_kinematicweave")


def _source_file(
    path: str = "part.bin",
    size: int = 3,
    checksum: str | None = SHA_A,
) -> registry.DatasetSourceFile:
    return registry.DatasetSourceFile(Path(path), size, checksum)


def _manifest(
    *,
    source_kind: str = "external_directory",
    availability: str = "available",
    checksum_mode: str = "sha256",
    files: tuple[registry.DatasetSourceFile, ...] | None = None,
    file_count: int | None = None,
    total_bytes: int | None = None,
    source_root_label: str = "mock-av2",
    discovered_at_utc: str = TIMESTAMP,
    dataset_version: str = "2026.1",
    adapter_version: str = "1.0",
    license_reference: str = "Provider license.",
    citation_reference: str = "Provider citation.",
) -> registry.DatasetSourceManifest:
    selected_files = (_source_file(),) if files is None else files
    return registry.DatasetSourceManifest(
        schema_version="1.0",
        dataset_id="av2_motion",
        dataset_version=dataset_version,
        adapter_name="av2_motion_adapter",
        adapter_version=adapter_version,
        source_kind=source_kind,
        source_root_label=source_root_label,
        availability=availability,
        checksum_mode=checksum_mode,
        file_count=len(selected_files) if file_count is None else file_count,
        total_bytes=(
            sum(item.size_bytes for item in selected_files)
            if total_bytes is None
            else total_bytes
        ),
        files=selected_files,
        license_reference=license_reference,
        citation_reference=citation_reference,
        discovered_at_utc=discovered_at_utc,
    )


def _generated_manifest(
    *,
    minimum_checksum_mode: str = "sha256",
) -> registry.DatasetSourceManifest:
    return registry.DatasetSourceManifest(
        schema_version="1.0",
        dataset_id="synthetic_kinematicweave",
        dataset_version="1.0",
        adapter_name="synthetic_generator",
        adapter_version="1.0",
        source_kind="generated",
        source_root_label="generated",
        availability="generated",
        checksum_mode=minimum_checksum_mode,
        file_count=0,
        total_bytes=0,
        files=(),
        license_reference="Project license.",
        citation_reference="Project citation.",
        discovered_at_utc=TIMESTAMP,
    )


def _create_source(root: Path) -> dict[str, bytes]:
    expected = {
        "alpha.txt": b"alpha",
        "nested/empty.bin": b"",
        "nested/zeta.bin": b"zeta-data",
    }
    for relative_path, data in expected.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return expected


def _discover(
    root: Path,
    *,
    checksum_mode: str = "sha256",
    max_files: int = 500_000,
    max_total_bytes: int | None = None,
) -> registry.DatasetSourceManifest:
    return registry.discover_dataset_source(
        _external_entry(),
        dataset_version="2026.1",
        source_root=root,
        source_root_label="mock-av2",
        checksum_mode=checksum_mode,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
    )


def _run_directory(
    tmp_path: Path,
    run_id: str = "registry-test",
) -> artifact_store.RunDirectory:
    return artifact_store.prepare_run_directory(
        tmp_path,
        "results",
        f"test:{run_id}",
        reserve_fraction=0.0,
    )


def _materialized_artifact(
    tmp_path: Path,
) -> tuple[
    Path,
    artifact_store.RunDirectory,
    registry.DatasetSourceManifest,
    registry.DatasetSourceManifestArtifact,
]:
    source = tmp_path / "source"
    source.mkdir(parents=True)
    _create_source(source)
    manifest = _discover(source)
    run = _run_directory(tmp_path)
    artifact = registry.atomic_write_dataset_source_manifest(
        run,
        "artifacts/dataset_source.json",
        manifest,
    )
    return source, run, manifest, artifact


def _plain_json_value(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(_plain_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _plain_json_value(item)
            for key, item in value.items()
        )
    return False


def test_exact_enums_and_public_api() -> None:
    assert tuple(item.value for item in registry.DatasetSourceKind) == (
        "generated",
        "external_directory",
    )
    assert tuple(item.value for item in registry.DatasetAvailability) == (
        "generated",
        "missing",
        "incomplete",
        "available",
    )
    assert tuple(item.value for item in registry.SourceChecksumMode) == (
        "sha256",
        "size_only",
    )
    assert registry.__all__ == [
        "DatasetAvailability",
        "DatasetRegistry",
        "DatasetRegistryEntry",
        "DatasetSourceFile",
        "DatasetSourceKind",
        "DatasetSourceManifest",
        "DatasetSourceManifestArtifact",
        "SourceChecksumMode",
        "atomic_write_dataset_source_manifest",
        "check_canonical_materialization_space",
        "dataset_registry_entries",
        "dataset_registry_to_dict",
        "dataset_source_manifest_from_dict",
        "dataset_source_manifest_from_json",
        "dataset_source_manifest_identity",
        "dataset_source_manifest_to_canonical_json",
        "dataset_source_manifest_to_dict",
        "default_dataset_registry",
        "discover_dataset_source",
        "estimate_canonical_materialization_bytes",
        "get_dataset_registry_entry",
        "verify_dataset_source",
        "verify_dataset_source_manifest_artifact",
    ]


@pytest.mark.parametrize(
    "model_name,field_names",
    [
        (
            "DatasetRegistryEntry",
            (
                "dataset_id",
                "display_name",
                "source_kind",
                "adapter_name",
                "default_dataset_version",
                "license_reference",
                "citation_reference",
            ),
        ),
        ("DatasetRegistry", ("schema_version", "entries")),
        ("DatasetSourceFile", ("relative_path", "size_bytes", "sha256")),
        (
            "DatasetSourceManifest",
            (
                "schema_version",
                "dataset_id",
                "dataset_version",
                "adapter_name",
                "adapter_version",
                "source_kind",
                "source_root_label",
                "availability",
                "checksum_mode",
                "file_count",
                "total_bytes",
                "files",
                "license_reference",
                "citation_reference",
                "discovered_at_utc",
            ),
        ),
        (
            "DatasetSourceManifestArtifact",
            (
                "dataset_id",
                "dataset_version",
                "manifest_identity",
                "written_artifact",
            ),
        ),
    ],
)
def test_models_are_frozen_slotted_and_have_exact_fields(
    model_name: str,
    field_names: tuple[str, ...],
) -> None:
    model = getattr(registry, model_name)
    assert tuple(field.name for field in fields(model)) == field_names
    assert "__slots__" in model.__dict__
    if model_name == "DatasetRegistryEntry":
        instance = _generated_entry()
        with pytest.raises(FrozenInstanceError):
            instance.dataset_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "dataset_id",
    (
        "",
        "UPPER",
        "_leading",
        "trailing_",
        "-leading",
        "trailing-",
        ".leading",
        "trailing.",
        "has space",
        "slash/value",
    ),
)
def test_registry_entry_rejects_invalid_dataset_identifiers(
    dataset_id: str,
) -> None:
    with pytest.raises(ValidationError):
        registry.DatasetRegistryEntry(
            dataset_id,
            "Name",
            "generated",
            "adapter",
            "1.0",
            "License",
            "Citation",
        )


def test_registry_entry_normalizes_text_and_rejects_invalid_text() -> None:
    entry = registry.DatasetRegistryEntry(
        "valid.id-2",
        "  Name  ",
        "generated",
        "  adapter  ",
        "  1.0  ",
        "  License  ",
        "  Citation  ",
    )
    assert (
        entry.display_name,
        entry.adapter_name,
        entry.default_dataset_version,
        entry.license_reference,
        entry.citation_reference,
    ) == ("Name", "adapter", "1.0", "License", "Citation")
    with pytest.raises(ValidationError):
        replace(entry, display_name=" ")
    with pytest.raises(ValidationError):
        replace(entry, source_kind="unknown")
    with pytest.raises(ValidationError):
        replace(entry, default_dataset_version=" ")


def test_registry_copies_ordered_entries_and_rejects_invalid_collections() -> None:
    first = _generated_entry()
    second = _external_entry()
    supplied: Any = [second, first]
    value = registry.DatasetRegistry("1.0", supplied)
    supplied.clear()
    assert value.entries == (second, first)
    assert isinstance(value.entries, tuple)
    with pytest.raises(ValidationError):
        registry.DatasetRegistry("2.0", (first,))
    with pytest.raises(ValidationError):
        registry.DatasetRegistry("1.0", ())
    with pytest.raises(ValidationError):
        registry.DatasetRegistry("1.0", (first, first))
    with pytest.raises(ValidationError):
        registry.DatasetRegistry("1.0", cast(Any, ("bad",)))


def test_source_file_validation_and_normalization() -> None:
    value = registry.DatasetSourceFile(Path("nested\\file.bin"), 0, None)
    assert value.relative_path.as_posix() == "nested/file.bin"
    assert value.size_bytes == 0
    for path in ("", "../escape", "/absolute", "C:\\absolute"):
        with pytest.raises(ValidationError):
            registry.DatasetSourceFile(cast(Path, path), 0, None)
    for size in (-1, True, 1.5):
        with pytest.raises(ValidationError):
            registry.DatasetSourceFile(Path("file"), cast(int, size), None)
    for digest in ("A" * 64, "a" * 63, "not-a-checksum"):
        with pytest.raises(ValidationError):
            registry.DatasetSourceFile(Path("file"), 0, digest)


def test_manifest_copies_files_and_validates_counts_and_order() -> None:
    first = _source_file("a.bin", 1)
    second = _source_file("b.bin", 2)
    supplied: Any = [first, second]
    value = _manifest(files=supplied, file_count=2, total_bytes=3)
    supplied.clear()
    assert value.files == (first, second)
    with pytest.raises(ValidationError, match="file_count"):
        _manifest(files=(first,), file_count=2)
    with pytest.raises(ValidationError, match="total_bytes"):
        _manifest(files=(first,), total_bytes=2)
    with pytest.raises(ValidationError, match="ordered"):
        _manifest(files=(second, first))
    with pytest.raises(ValidationError, match="ordered"):
        _manifest(files=(first, first), file_count=2, total_bytes=2)


def test_manifest_checksum_mode_consistency() -> None:
    with pytest.raises(ValidationError, match="requires every"):
        _manifest(files=(_source_file(checksum=None),))
    with pytest.raises(ValidationError, match="null"):
        _manifest(checksum_mode="size_only")
    size_only = _manifest(
        checksum_mode="size_only",
        files=(_source_file(checksum=None),),
    )
    assert size_only.files[0].sha256 is None


def test_manifest_availability_invariants_and_timestamp() -> None:
    generated = _generated_manifest()
    assert generated.file_count == generated.total_bytes == 0
    with pytest.raises(ValidationError):
        _manifest(source_kind="generated")
    with pytest.raises(ValidationError):
        _manifest(availability="generated")
    with pytest.raises(ValidationError):
        _manifest(availability="missing")
    with pytest.raises(ValidationError):
        _manifest(files=(), availability="available")
    assert _manifest(files=(), availability="incomplete").file_count == 0
    with pytest.raises(ValidationError):
        _manifest(discovered_at_utc="2026-07-25")
    with pytest.raises(ValidationError):
        _manifest(discovered_at_utc="2026-02-30T00:00:00.000000Z")


def test_manifest_rejects_invalid_schema_text_counts_and_label() -> None:
    value = _manifest()
    with pytest.raises(ValidationError):
        replace(value, schema_version="2.0")
    for field_name in (
        "dataset_version",
        "adapter_name",
        "adapter_version",
        "license_reference",
        "citation_reference",
    ):
        with pytest.raises(ValidationError):
            replace(value, **cast(Any, {field_name: " "}))
    for field_name in ("file_count", "total_bytes"):
        with pytest.raises(ValidationError):
            replace(value, **cast(Any, {field_name: True}))
    for label in ("/absolute", "C:\\absolute"):
        with pytest.raises(ValidationError):
            replace(value, source_root_label=label)


def test_manifest_artifact_validation() -> None:
    written = artifact_store.WrittenArtifact(Path("artifact.json"), 0, SHA_A)
    artifact = registry.DatasetSourceManifestArtifact(
        "av2_motion",
        " 2026.1 ",
        SHA_B,
        written,
    )
    assert artifact.dataset_version == "2026.1"
    for changes in (
        {"dataset_id": "Invalid"},
        {"dataset_version": " "},
        {"manifest_identity": "bad"},
        {"written_artifact": "bad"},
    ):
        with pytest.raises(ValidationError):
            replace(artifact, **cast(Any, changes))


def test_default_registry_has_exact_fresh_entries() -> None:
    first = registry.default_dataset_registry()
    second = registry.default_dataset_registry()
    assert first == second
    assert first is not second
    assert first.entries is not second.entries
    assert tuple(entry.dataset_id for entry in first.entries) == (
        "synthetic_kinematicweave",
        "av2_motion",
    )
    synthetic, av2 = first.entries
    assert (
        synthetic.display_name,
        str(synthetic.source_kind),
        synthetic.adapter_name,
        synthetic.default_dataset_version,
    ) == (
        "KinematicWeave Synthetic Correctness Dataset",
        "generated",
        "synthetic_generator",
        "1.0",
    )
    assert (
        av2.display_name,
        str(av2.source_kind),
        av2.adapter_name,
        av2.default_dataset_version,
    ) == (
        "Argoverse 2 Motion Forecasting",
        "external_directory",
        "av2_motion_adapter",
        None,
    )


def test_registry_entries_lookup_and_default_isolation() -> None:
    value = registry.default_dataset_registry()
    assert registry.dataset_registry_entries(value) == value.entries
    assert registry.dataset_registry_entries(value) is not value.entries
    assert registry.get_dataset_registry_entry("av2_motion") == value.entries[1]
    custom = registry.DatasetRegistry("1.0", (value.entries[1],))
    assert (
        registry.get_dataset_registry_entry("av2_motion", registry=custom)
        == custom.entries[0]
    )
    with pytest.raises(ValidationError):
        registry.get_dataset_registry_entry("unknown")
    with pytest.raises(ValidationError):
        registry.dataset_registry_entries(cast(Any, "bad"))
    mutable = list(registry.dataset_registry_entries(value))
    mutable.clear()
    assert len(registry.default_dataset_registry().entries) == 2


def test_generated_discovery_uses_defaults_without_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("generated discovery touched the filesystem")

    monkeypatch.setattr(Path, "exists", fail)
    monkeypatch.setattr(Path, "is_dir", fail)
    manifest = registry.discover_dataset_source(_generated_entry())
    assert manifest.dataset_version == "1.0"
    assert str(manifest.availability) == "generated"
    assert manifest.source_root_label == "generated"
    assert manifest.files == ()
    assert manifest.file_count == manifest.total_bytes == 0


def test_generated_discovery_version_precedence_and_invalid_inputs(
    tmp_path: Path,
) -> None:
    manifest = registry.discover_dataset_source(
        _generated_entry(),
        dataset_version=" 2.0 ",
    )
    assert manifest.dataset_version == "2.0"
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(
            _generated_entry(),
            source_root=tmp_path,
        )
    versionless = replace(_generated_entry(), default_dataset_version=None)
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(versionless)
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(_generated_entry(), max_files=True)
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(
            _generated_entry(),
            max_total_bytes=-1,
        )


def test_external_discovery_requires_version_root_and_label(tmp_path: Path) -> None:
    entry = _external_entry()
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(entry)
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(entry, dataset_version="1")
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(
            entry,
            dataset_version="1",
            source_root=tmp_path,
        )
    with pytest.raises(ValidationError):
        registry.discover_dataset_source(
            entry,
            dataset_version="1",
            source_root=tmp_path,
            source_root_label="/absolute",
        )


def test_external_discovery_missing_non_directory_and_empty(tmp_path: Path) -> None:
    missing = _discover(tmp_path / "missing")
    assert str(missing.availability) == "missing"
    assert missing.files == ()
    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError):
        _discover(file_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    incomplete = _discover(empty)
    assert str(incomplete.availability) == "incomplete"
    assert incomplete.file_count == incomplete.total_bytes == 0


def test_external_sha256_inventory_is_sorted_complete_and_nonmutating(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = _create_source(root)
    before = {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }
    manifest = _discover(root)
    assert str(manifest.availability) == "available"
    assert [item.relative_path.as_posix() for item in manifest.files] == sorted(
        expected
    )
    assert manifest.file_count == 3
    assert manifest.total_bytes == sum(map(len, expected.values()))
    assert [item.size_bytes for item in manifest.files] == [
        len(expected[path]) for path in sorted(expected)
    ]
    assert [item.sha256 for item in manifest.files] == [
        hashlib.sha256(expected[path]).hexdigest() for path in sorted(expected)
    ]
    assert manifest.source_root_label == "mock-av2"
    serialized = registry.dataset_source_manifest_to_dict(manifest)
    assert str(root) not in json.dumps(serialized)
    assert vars(registry)["_READ_CHUNK_SIZE"] == 1024 * 1024
    after = {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_size_only_inventory_does_not_read_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "file.bin").write_bytes(b"content")

    def fail_open(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("size-only discovery opened file content")

    monkeypatch.setattr(Path, "open", fail_open)
    manifest = _discover(root, checksum_mode="size_only")
    assert manifest.files == (registry.DatasetSourceFile(Path("file.bin"), 7, None),)


def test_inventory_limits_stop_without_source_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a").write_bytes(b"aa")
    (root / "b").write_bytes(b"bb")
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    original = cast(
        Callable[[Path], str],
        vars(registry)["_hash_source_file"],
    )
    hashed: list[str] = []

    def track_hash(path: Path) -> str:
        hashed.append(path.name)
        return original(path)

    monkeypatch.setattr(registry, "_hash_source_file", track_hash)
    with pytest.raises(ResourceLimitError, match="max_files"):
        _discover(root, max_files=1)
    assert len(hashed) == 1
    hashed.clear()
    with pytest.raises(ResourceLimitError, match="max_total_bytes"):
        _discover(root, max_total_bytes=3)
    assert len(hashed) == 1
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_inventory_rejects_disappearing_and_changing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "file"
    target.write_bytes(b"abc")
    original = cast(
        Callable[[Path], str],
        vars(registry)["_hash_source_file"],
    )

    def remove_after_hash(path: Path) -> str:
        digest = original(path)
        path.unlink()
        return digest

    monkeypatch.setattr(registry, "_hash_source_file", remove_after_hash)
    with pytest.raises(ArtifactError, match="disappeared"):
        _discover(root)

    target.write_bytes(b"abc")

    def grow_after_hash(path: Path) -> str:
        digest = original(path)
        with path.open("ab") as stream:
            stream.write(b"d")
        return digest

    monkeypatch.setattr(registry, "_hash_source_file", grow_after_hash)
    with pytest.raises(ArtifactError, match="changed size"):
        _discover(root)


def test_inventory_rejects_symlinks_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "target"
    target.write_bytes(b"x")
    link = root / "link"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")
    with pytest.raises(ArtifactError, match="symbolic"):
        _discover(root)


def test_registry_and_manifest_dictionary_order_and_plain_values() -> None:
    registry_dict = registry.dataset_registry_to_dict(
        registry.default_dataset_registry()
    )
    assert tuple(registry_dict) == ("schema_version", "entries")
    registry_entries = cast(list[dict[str, object]], registry_dict["entries"])
    assert tuple(registry_entries[0]) == (
        "dataset_id",
        "display_name",
        "source_kind",
        "adapter_name",
        "default_dataset_version",
        "license_reference",
        "citation_reference",
    )
    manifest_dict = registry.dataset_source_manifest_to_dict(_manifest())
    assert tuple(manifest_dict) == (
        "schema_version",
        "dataset_id",
        "dataset_version",
        "adapter_name",
        "adapter_version",
        "source_kind",
        "source_root_label",
        "availability",
        "checksum_mode",
        "file_count",
        "total_bytes",
        "files",
        "license_reference",
        "citation_reference",
        "discovered_at_utc",
    )
    manifest_files = cast(list[dict[str, object]], manifest_dict["files"])
    assert tuple(manifest_files[0]) == (
        "relative_path",
        "size_bytes",
        "sha256",
    )
    assert _plain_json_value(registry_dict)
    assert _plain_json_value(manifest_dict)


def test_canonical_json_is_deterministic_with_exact_trailing_newline() -> None:
    manifest = _manifest()
    first = registry.dataset_source_manifest_to_canonical_json(manifest)
    second = registry.dataset_source_manifest_to_canonical_json(manifest)
    assert first == second
    assert first.endswith("\n")
    assert not first.endswith("\n\n")
    assert "\r" not in first
    assert json.loads(first) == registry.dataset_source_manifest_to_dict(manifest)


def test_manifest_dictionary_and_json_round_trips() -> None:
    manifest = _manifest()
    value = registry.dataset_source_manifest_to_dict(manifest)
    assert registry.dataset_source_manifest_from_dict(value) == manifest
    text = registry.dataset_source_manifest_to_canonical_json(manifest)
    assert registry.dataset_source_manifest_from_json(text) == manifest


@pytest.mark.parametrize(
    "mutation",
    ("unknown", "missing", "enum", "files_not_list", "file_unknown"),
)
def test_manifest_deserialization_rejects_schema_errors(mutation: str) -> None:
    value = registry.dataset_source_manifest_to_dict(_manifest())
    if mutation == "unknown":
        value["unexpected"] = True
    elif mutation == "missing":
        del value["dataset_version"]
    elif mutation == "enum":
        value["availability"] = "not-valid"
    elif mutation == "files_not_list":
        value["files"] = {}
    else:
        files_value = cast(list[dict[str, object]], value["files"])
        files_value[0]["unexpected"] = True
    with pytest.raises(SchemaError):
        registry.dataset_source_manifest_from_dict(value)


@pytest.mark.parametrize("text", ("{", "[]", "null", '"string"'))
def test_manifest_json_rejects_malformed_or_non_object_roots(text: str) -> None:
    with pytest.raises(SchemaError) as captured:
        registry.dataset_source_manifest_from_json(text)
    assert captured.value.__cause__ is None


def test_manifest_identity_is_stable_and_ignores_time_and_root_label() -> None:
    manifest = _manifest()
    identity = registry.dataset_source_manifest_identity(manifest)
    assert len(identity) == 64
    assert identity == registry.dataset_source_manifest_identity(manifest)
    assert identity == registry.dataset_source_manifest_identity(
        replace(
            manifest,
            source_root_label="different-logical-root",
            discovered_at_utc="2027-01-01T00:00:00.000000Z",
        )
    )


def test_manifest_identity_changes_for_logical_content() -> None:
    manifest = _manifest()
    identity = registry.dataset_source_manifest_identity(manifest)
    changed_file = _source_file("other.bin", 3, SHA_A)
    changes = (
        replace(manifest, files=(changed_file,)),
        replace(
            manifest,
            files=(_source_file(size=4),),
            total_bytes=4,
        ),
        replace(manifest, files=(_source_file(checksum=SHA_B),)),
        replace(manifest, dataset_version="2027.1"),
        replace(manifest, adapter_version="2.0"),
        replace(
            manifest,
            checksum_mode="size_only",
            files=(_source_file(checksum=None),),
        ),
        replace(manifest, license_reference="Different license."),
        replace(manifest, citation_reference="Different citation."),
    )
    assert all(
        registry.dataset_source_manifest_identity(item) != identity for item in changes
    )


def test_generated_and_missing_source_verification() -> None:
    registry.verify_dataset_source(_generated_manifest(), source_root=None)
    with pytest.raises(ValidationError):
        registry.verify_dataset_source(
            _generated_manifest(),
            source_root=Path("unexpected"),
        )
    missing = _manifest(files=(), availability="missing")
    with pytest.raises(ArtifactError):
        registry.verify_dataset_source(missing, source_root=None)


def test_external_source_verification_sha256_and_size_only(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _create_source(root)
    sha_manifest = _discover(root)
    size_manifest = _discover(root, checksum_mode="size_only")
    registry.verify_dataset_source(sha_manifest, source_root=root)
    registry.verify_dataset_source(size_manifest, source_root=root)
    (root / "alpha.txt").write_bytes(b"ALPHA")
    with pytest.raises(ArtifactError):
        registry.verify_dataset_source(sha_manifest, source_root=root)
    registry.verify_dataset_source(size_manifest, source_root=root)


@pytest.mark.parametrize("change", ("missing", "additional", "size"))
def test_external_source_verification_detects_inventory_changes(
    tmp_path: Path,
    change: str,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _create_source(root)
    manifest = _discover(root)
    if change == "missing":
        (root / "alpha.txt").unlink()
    elif change == "additional":
        (root / "new.bin").write_bytes(b"new")
    else:
        (root / "alpha.txt").write_bytes(b"longer")
    with pytest.raises(ArtifactError):
        registry.verify_dataset_source(manifest, source_root=root)


def test_materialization_estimate_exact_rounding_floor_and_generated() -> None:
    manifest = _manifest()
    assert (
        registry.estimate_canonical_materialization_bytes(
            manifest,
            expansion_factor=1.5,
        )
        == 5
    )
    assert (
        registry.estimate_canonical_materialization_bytes(
            manifest,
            expansion_factor=0.1,
        )
        == 1
    )
    assert (
        registry.estimate_canonical_materialization_bytes(
            manifest,
            expansion_factor=0,
            minimum_bytes=10,
        )
        == 10
    )
    assert (
        registry.estimate_canonical_materialization_bytes(
            _generated_manifest(),
            minimum_bytes=123,
        )
        == 123
    )


@pytest.mark.parametrize(
    "factor",
    (True, -1, float("nan"), float("inf"), "1.5"),
)
def test_materialization_estimate_rejects_invalid_factors(factor: object) -> None:
    with pytest.raises(ValidationError):
        registry.estimate_canonical_materialization_bytes(
            _manifest(),
            expansion_factor=cast(float, factor),
        )
    with pytest.raises(ValidationError):
        registry.estimate_canonical_materialization_bytes(
            _manifest(),
            minimum_bytes=cast(int, True),
        )
    with pytest.raises(ValidationError):
        registry.estimate_canonical_materialization_bytes(
            _manifest(),
            minimum_bytes=-1,
        )


def test_materialization_space_check_passes_exact_estimate(tmp_path: Path) -> None:
    snapshot = registry.check_canonical_materialization_space(
        tmp_path,
        _manifest(),
        expansion_factor=2,
        minimum_bytes=1,
        reserve_fraction=0,
    )
    assert snapshot.required_bytes == 6
    assert snapshot.free_after_write_bytes == snapshot.free_bytes - 6


def test_materialization_space_propagates_resource_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fail(
        path: Path,
        *,
        required_bytes: int,
        reserve_fraction: float,
    ) -> artifact_store.DiskSpaceSnapshot:
        observed.update(
            path=path,
            required_bytes=required_bytes,
            reserve_fraction=reserve_fraction,
        )
        raise ResourceLimitError("insufficient disk")

    monkeypatch.setattr(registry, "check_disk_space", fail)
    with pytest.raises(ResourceLimitError):
        registry.check_canonical_materialization_space(
            tmp_path,
            _manifest(),
            expansion_factor=2,
            reserve_fraction=0.25,
        )
    assert observed == {
        "path": tmp_path,
        "required_bytes": 6,
        "reserve_fraction": 0.25,
    }


def test_manifest_artifact_write_and_round_trip(tmp_path: Path) -> None:
    _, run, manifest, artifact = _materialized_artifact(tmp_path)
    expected = (
        Path("results") / "runs" / run.path.name / "artifacts" / "dataset_source.json"
    )
    assert artifact.written_artifact.relative_path == expected
    path = tmp_path / expected
    data = path.read_bytes()
    assert data.decode("utf-8") == (
        registry.dataset_source_manifest_to_canonical_json(manifest)
    )
    assert artifact.written_artifact.size_bytes == len(data)
    assert (
        artifact.written_artifact.content_checksum == hashlib.sha256(data).hexdigest()
    )
    assert artifact.manifest_identity == registry.dataset_source_manifest_identity(
        manifest
    )
    assert (
        registry.verify_dataset_source_manifest_artifact(
            tmp_path,
            artifact,
        )
        == manifest
    )
    assert not any(run.manifests_path.iterdir())
    assert not artifact_store.list_partial_artifacts(run)


def test_manifest_artifact_overwrite_and_finalization_rejection(
    tmp_path: Path,
) -> None:
    _, run, manifest, _ = _materialized_artifact(tmp_path)
    with pytest.raises(ArtifactError):
        registry.atomic_write_dataset_source_manifest(
            run,
            "artifacts/dataset_source.json",
            manifest,
        )
    artifact_store.finalize_run_directory(run)
    with pytest.raises(ArtifactError):
        registry.atomic_write_dataset_source_manifest(
            run,
            "artifacts/another.json",
            manifest,
        )


def test_manifest_artifact_rejects_altered_bytes_and_wrong_identity(
    tmp_path: Path,
) -> None:
    _, _, _, artifact = _materialized_artifact(tmp_path)
    path = tmp_path / artifact.written_artifact.relative_path
    path.write_bytes(path.read_bytes().replace(b"mock-av2", b"mock-av3"))
    with pytest.raises(ArtifactError, match="checksum"):
        registry.verify_dataset_source_manifest_artifact(tmp_path, artifact)

    source, run, manifest, artifact = _materialized_artifact(tmp_path / "second")
    assert source.is_dir()
    assert run.path.is_dir()
    wrong_dataset = replace(artifact, dataset_id="other")
    with pytest.raises(SchemaError, match="metadata"):
        registry.verify_dataset_source_manifest_artifact(
            tmp_path / "second",
            wrong_dataset,
        )
    wrong_identity = replace(artifact, manifest_identity=SHA_A)
    if wrong_identity.manifest_identity == artifact.manifest_identity:
        wrong_identity = replace(artifact, manifest_identity=SHA_B)
    with pytest.raises(SchemaError, match="logical identity"):
        registry.verify_dataset_source_manifest_artifact(
            tmp_path / "second",
            wrong_identity,
        )
    assert manifest.dataset_id == "av2_motion"


def test_module_import_is_side_effect_free_and_dependency_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse(MODULE_PATH.read_text("utf-8"))
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    forbidden = {
        "argoverse",
        "av2",
        "geopandas",
        "httpx",
        "networkx",
        "requests",
        "rerun",
        "shapely",
        "socket",
        "subprocess",
        "torch",
        "urllib",
    }
    assert imports.isdisjoint(forbidden)
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.rglob("*"))
    importlib.reload(registry)
    assert tuple(tmp_path.rglob("*")) == before


def test_dataset_registry_integration(tmp_path: Path) -> None:
    external = registry.get_dataset_registry_entry(
        "av2_motion",
        registry=registry.default_dataset_registry(),
    )
    source = tmp_path / "external-source"
    source.mkdir()
    expected = _create_source(source)
    before = {
        path.relative_to(source).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in source.rglob("*")
        if path.is_file()
    }
    manifest = registry.discover_dataset_source(
        external,
        dataset_version="2026.1",
        adapter_version="1.0",
        source_root=source,
        source_root_label="integration-av2",
        checksum_mode="sha256",
        max_files=10,
        max_total_bytes=1024,
    )
    assert [item.relative_path.as_posix() for item in manifest.files] == sorted(
        expected
    )
    registry.verify_dataset_source(manifest, source_root=source)
    assert registry.estimate_canonical_materialization_bytes(
        manifest,
        expansion_factor=1.5,
    ) == math.ceil(sum(map(len, expected.values())) * 1.5)
    snapshot = registry.check_canonical_materialization_space(
        tmp_path,
        manifest,
        reserve_fraction=0,
    )
    assert snapshot.required_bytes > 0
    run = _run_directory(tmp_path, "integration")
    artifact = registry.atomic_write_dataset_source_manifest(
        run,
        "artifacts/source_manifest.json",
        manifest,
    )
    assert (
        registry.verify_dataset_source_manifest_artifact(
            tmp_path,
            artifact,
        )
        == manifest
    )
    artifact_store.finalize_run_directory(run)
    with pytest.raises(ArtifactError):
        registry.atomic_write_dataset_source_manifest(
            run,
            "artifacts/later.json",
            manifest,
        )
    after = {
        path.relative_to(source).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in source.rglob("*")
        if path.is_file()
    }
    assert after == before
