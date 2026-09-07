"""Tests for deterministic official AV2 provider acquisition."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import hashlib
import importlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kinematicweave.canonical import canonical_json_text
from kinematicweave.data import av2_acquisition as acquisition
from kinematicweave.data import registry
from kinematicweave.data.av2_acquisition import (
    AV2_OFFICIAL_MOTION_ROOT,
    Av2AcquiredFile,
    Av2AcquisitionConfig,
    Av2AcquisitionPlan,
    Av2AcquisitionReport,
    Av2RemoteObject,
    Av2RemoteScenarioPair,
    acquire_av2_plan,
    av2_acquisition_plan_from_json,
    av2_acquisition_plan_identity,
    av2_acquisition_plan_to_canonical_json,
    av2_acquisition_report_from_json,
    av2_acquisition_report_to_canonical_json,
    build_av2_acquisition_plan,
    build_remote_scenario_pairs,
    discover_av2_partition,
    parse_anonymous_s3_page,
    parse_s5cmd_listing,
    remote_catalog_identity,
    verify_acquired_files,
)
from kinematicweave.errors import (
    ArtifactError,
    ResourceLimitError,
    SchemaError,
    ValidationError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_av2_provider_pilot.py"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _remote(
    scenario_id: str,
    name: str,
    *,
    size: int = 10,
    etag: str | None = "etag",
    partition: str = "val",
) -> Av2RemoteObject:
    key = f"{partition}/{scenario_id}/{name}"
    return Av2RemoteObject(
        remote_uri=f"{AV2_OFFICIAL_MOTION_ROOT}{key}",
        relative_key=key,
        size_bytes=size,
        etag=etag,
    )


def _pair(
    scenario_id: str,
    *,
    motion_size: int = 10,
    map_size: int = 20,
) -> Av2RemoteScenarioPair:
    return Av2RemoteScenarioPair(
        source_scenario_id=scenario_id,
        motion=_remote(
            scenario_id,
            f"scenario_{scenario_id}.parquet",
            size=motion_size,
        ),
        vector_map=_remote(
            scenario_id,
            f"log_map_archive_{scenario_id}.json",
            size=map_size,
        ),
    )


def _pairs(count: int = 12) -> tuple[Av2RemoteScenarioPair, ...]:
    return tuple(_pair(f"scenario-{index:03d}") for index in range(count))


def _config(
    *,
    count: int = 3,
    seed: int = 0,
    namespace: str = "av2-provider-evidence-v1",
) -> Av2AcquisitionConfig:
    return Av2AcquisitionConfig(
        scenario_count=count,
        root_seed=seed,
        assignment_namespace=namespace,
    )


def _plan(*, count: int = 1) -> Av2AcquisitionPlan:
    return build_av2_acquisition_plan(
        _pairs(max(count, 3)), config=_config(count=count)
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _xml_page(
    contents: tuple[tuple[str, int, str], ...],
    *,
    prefixes: tuple[str, ...] = (),
    token: str | None = None,
) -> bytes:
    content_xml = "".join(
        (
            "<Contents>"
            f"<Key>{key}</Key><ETag>&quot;{etag}&quot;</ETag><Size>{size}</Size>"
            "</Contents>"
        )
        for key, size, etag in contents
    )
    prefix_xml = "".join(
        f"<CommonPrefixes><Prefix>{prefix}</Prefix></CommonPrefixes>"
        for prefix in prefixes
    )
    truncated = "true" if token is not None else "false"
    token_xml = (
        f"<NextContinuationToken>{token}</NextContinuationToken>"
        if token is not None
        else ""
    )
    return (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<IsTruncated>{truncated}</IsTruncated>{content_xml}{prefix_xml}{token_xml}"
        "</ListBucketResult>"
    ).encode()


def test_models_are_frozen_slotted_and_have_exact_fields() -> None:
    expected = {
        Av2RemoteObject: ("remote_uri", "relative_key", "size_bytes", "etag"),
        Av2RemoteScenarioPair: ("source_scenario_id", "motion", "vector_map"),
        Av2AcquisitionConfig: (
            "remote_root",
            "partition",
            "scenario_count",
            "root_seed",
            "assignment_namespace",
            "local_relative_root",
            "backend",
        ),
        Av2AcquisitionPlan: (
            "schema_version",
            "dataset_id",
            "dataset_version",
            "remote_root",
            "partition",
            "root_seed",
            "assignment_namespace",
            "candidate_count",
            "selected_scenarios",
            "remote_catalog_identity",
        ),
        Av2AcquiredFile: (
            "remote_uri",
            "relative_path",
            "size_bytes",
            "sha256",
            "disposition",
        ),
        Av2AcquisitionReport: (
            "schema_version",
            "plan_identity",
            "backend",
            "backend_version",
            "selected_scenario_count",
            "candidate_count",
            "downloaded_file_count",
            "reused_file_count",
            "downloaded_bytes",
            "reused_bytes",
            "total_local_bytes",
            "listing_seconds",
            "download_seconds",
            "acquired_files",
        ),
    }
    for model, names in expected.items():
        assert tuple(item.name for item in fields(model)) == names
        assert "__slots__" in model.__dict__
    value = _pair("scenario-001")
    with pytest.raises(FrozenInstanceError):
        value.source_scenario_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("remote_root", "s3://other/datasets/av2/motion-forecasting/"),
        ("remote_root", "https://argoverse.s3.amazonaws.com/"),
        ("partition", "../val"),
        ("scenario_count", 0),
        ("scenario_count", True),
        ("backend", "aws"),
    ],
)
def test_invalid_acquisition_config_is_rejected(field: str, value: object) -> None:
    values: dict[str, object] = {
        "remote_root": AV2_OFFICIAL_MOTION_ROOT,
        "partition": "val",
        "scenario_count": 10,
        "root_seed": 0,
        "assignment_namespace": "namespace",
        "local_relative_root": Path("data/external/av2_motion"),
        "backend": "auto",
    }
    values[field] = value
    with pytest.raises(ValidationError):
        Av2AcquisitionConfig(**cast(Any, values))


@pytest.mark.parametrize("size", [-1, True, 1.5])
def test_invalid_remote_object_size_is_rejected(size: object) -> None:
    with pytest.raises(ValidationError, match="size_bytes"):
        Av2RemoteObject(
            remote_uri=f"{AV2_OFFICIAL_MOTION_ROOT}val/id/scenario_id.parquet",
            relative_key="val/id/scenario_id.parquet",
            size_bytes=cast(Any, size),
            etag="etag",
        )


def test_remote_object_rejects_absolute_traversal_and_uri_mismatch() -> None:
    for key in ("/val/id/file", "val/../file", r"val\id\file"):
        with pytest.raises(ValidationError):
            Av2RemoteObject(
                remote_uri=f"{AV2_OFFICIAL_MOTION_ROOT}{key}",
                relative_key=key,
                size_bytes=1,
                etag="etag",
            )
    with pytest.raises(ValidationError, match="remote_uri"):
        Av2RemoteObject(
            remote_uri=f"{AV2_OFFICIAL_MOTION_ROOT}val/other/file",
            relative_key="val/id/file",
            size_bytes=1,
            etag="etag",
        )


def test_pair_validates_exact_filenames_parent_and_total_bytes() -> None:
    pair = _pair("scenario-001", motion_size=11, map_size=23)
    assert pair.total_remote_bytes == 34
    with pytest.raises(ValidationError, match="motion filename"):
        Av2RemoteScenarioPair(
            source_scenario_id="scenario-001",
            motion=_remote("scenario-001", "scenario_wrong.parquet"),
            vector_map=pair.vector_map,
        )
    with pytest.raises(ValidationError, match="share"):
        Av2RemoteScenarioPair(
            source_scenario_id="scenario-001",
            motion=pair.motion,
            vector_map=_remote(
                "scenario-002",
                "log_map_archive_scenario-001.json",
            ),
        )


def test_plan_round_trip_is_canonical_strict_and_hashed() -> None:
    plan = _plan(count=3)
    text = av2_acquisition_plan_to_canonical_json(plan)
    assert text == av2_acquisition_plan_to_canonical_json(
        av2_acquisition_plan_from_json(text)
    )
    assert text.endswith("\n")
    assert re.fullmatch(r"[0-9a-f]{64}", av2_acquisition_plan_identity(plan))
    payload = json.loads(text)
    payload["unknown"] = True
    with pytest.raises(SchemaError, match="unknown"):
        av2_acquisition_plan_from_json(json.dumps(payload))


def test_report_round_trip_enforces_counts_and_durations() -> None:
    plan = _plan(count=1)
    files = tuple(
        sorted(
            (
                Av2AcquiredFile(
                    remote_uri=item.remote_uri,
                    relative_path=Path("data") / Path(item.relative_key),
                    size_bytes=item.size_bytes,
                    sha256=SHA_A if index == 0 else SHA_B,
                    disposition="downloaded",
                )
                for index, item in enumerate(
                    (
                        plan.selected_scenarios[0].motion,
                        plan.selected_scenarios[0].vector_map,
                    )
                )
            ),
            key=lambda item: item.relative_path.as_posix(),
        )
    )
    report = Av2AcquisitionReport(
        schema_version="1.0",
        plan_identity=av2_acquisition_plan_identity(plan),
        backend="s5cmd",
        backend_version="v2.3.0",
        selected_scenario_count=1,
        candidate_count=3,
        downloaded_file_count=2,
        reused_file_count=0,
        downloaded_bytes=sum(item.size_bytes for item in files),
        reused_bytes=0,
        total_local_bytes=sum(item.size_bytes for item in files),
        listing_seconds=0.1,
        download_seconds=0.2,
        acquired_files=files,
    )
    text = av2_acquisition_report_to_canonical_json(report)
    assert av2_acquisition_report_from_json(text) == report
    payload = json.loads(text)
    payload["download_seconds"] = float("nan")
    with pytest.raises(SchemaError, match="finite"):
        av2_acquisition_report_from_json(json.dumps(payload))


def test_parse_s5cmd_structured_and_text_listing() -> None:
    scenario_id = "scenario-001"
    motion = f"val/{scenario_id}/scenario_{scenario_id}.parquet"
    structured = json.dumps(
        {
            "key": f"{AV2_OFFICIAL_MOTION_ROOT}{motion}",
            "etag": "abc",
            "type": "file",
            "size": 123,
        }
    )
    directory = json.dumps(
        {
            "key": f"{AV2_OFFICIAL_MOTION_ROOT}val/{scenario_id}/",
            "type": "directory",
        }
    )
    parsed = parse_s5cmd_listing(f"{directory}\n{structured}\n")
    assert len(parsed) == 1
    assert parsed[0].relative_key == motion
    assert parsed[0].etag == "abc"
    text = f"2023/03/24 20:46:16        123  {AV2_OFFICIAL_MOTION_ROOT}{motion}\n"
    fallback = parse_s5cmd_listing(text)
    assert fallback[0].relative_key == motion
    assert fallback[0].etag is None


@pytest.mark.parametrize(
    "text",
    [
        "{bad json",
        json.dumps({"key": "x", "type": "unknown", "size": 1}),
        "not a listing record",
    ],
)
def test_parse_s5cmd_malformed_records_are_rejected(text: str) -> None:
    with pytest.raises(SchemaError):
        parse_s5cmd_listing(text)


def test_parse_anonymous_s3_page_objects_prefixes_and_pagination() -> None:
    root_key = "datasets/av2/motion-forecasting/"
    key = f"{root_key}val/id/scenario_id.parquet"
    page = _xml_page(
        ((key, 123, "etag"),),
        prefixes=(f"{root_key}val/",),
        token="next-token",
    )
    objects, prefixes, token = parse_anonymous_s3_page(page)
    assert objects[0].relative_key == "val/id/scenario_id.parquet"
    assert objects[0].etag == "etag"
    assert prefixes == ("val",)
    assert token == "next-token"


def test_parse_anonymous_s3_page_rejects_malformed_and_bad_pagination() -> None:
    with pytest.raises(SchemaError, match="malformed"):
        parse_anonymous_s3_page(b"<bad")
    missing_token = (
        b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<IsTruncated>true</IsTruncated></ListBucketResult>"
    )
    with pytest.raises(SchemaError, match="continuation"):
        parse_anonymous_s3_page(missing_token)


def test_partition_discovery_prefers_observed_val() -> None:
    assert discover_av2_partition(("train", "val", "test")) == "val"
    assert discover_av2_partition(("validation", "train")) == "validation"
    with pytest.raises(ArtifactError, match="validation"):
        discover_av2_partition(("train", "test"))


def test_pair_discovery_filters_unrelated_and_incomplete_objects() -> None:
    complete = _pair("scenario-001")
    incomplete = _remote(
        "scenario-002",
        "scenario_scenario-002.parquet",
    )
    unrelated = _remote("scenario-003", "notes.txt")
    train = _remote(
        "scenario-004",
        "scenario_scenario-004.parquet",
        partition="train",
    )
    pairs = build_remote_scenario_pairs(
        (complete.vector_map, unrelated, train, complete.motion, incomplete),
        partition="val",
    )
    assert pairs == (complete,)


def test_pair_discovery_rejects_duplicate_and_mismatched_objects() -> None:
    pair = _pair("scenario-001")
    with pytest.raises(ValidationError, match="duplicate motion"):
        build_remote_scenario_pairs(
            (pair.motion, pair.motion, pair.vector_map),
            partition="val",
        )
    mismatched = _remote("scenario-001", "scenario_other.parquet")
    with pytest.raises(ValidationError, match="differs"):
        build_remote_scenario_pairs((mismatched,), partition="val")


def test_selection_is_exact_stable_and_listing_order_independent() -> None:
    pairs = _pairs(12)
    first = build_av2_acquisition_plan(pairs, config=_config(count=10))
    second = build_av2_acquisition_plan(
        tuple(reversed(pairs)), config=_config(count=10)
    )
    assert first == second
    assert len(first.selected_scenarios) == 10
    assert first.candidate_count == 12
    assert first.dataset_version == (
        f"official-s3-{first.remote_catalog_identity[:12]}"
    )
    assert remote_catalog_identity(pairs) == remote_catalog_identity(
        tuple(reversed(pairs))
    )


def test_selection_changes_with_seed_or_namespace_without_outcome_input() -> None:
    pairs = _pairs(30)
    baseline = build_av2_acquisition_plan(pairs, config=_config(count=10))
    changed_seed = build_av2_acquisition_plan(pairs, config=_config(count=10, seed=1))
    changed_namespace = build_av2_acquisition_plan(
        pairs, config=_config(count=10, namespace="changed")
    )
    baseline_ids = tuple(
        item.source_scenario_id for item in baseline.selected_scenarios
    )
    assert baseline_ids != tuple(
        item.source_scenario_id for item in changed_seed.selected_scenarios
    )
    assert baseline_ids != tuple(
        item.source_scenario_id for item in changed_namespace.selected_scenarios
    )
    assert not hasattr(Av2RemoteScenarioPair, "eligibility")


def test_selection_rejects_insufficient_candidates() -> None:
    with pytest.raises(ResourceLimitError, match="insufficient"):
        build_av2_acquisition_plan(_pairs(2), config=_config(count=3))


def test_download_retry_uses_partial_and_completes_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _remote("scenario-001", "scenario_scenario-001.parquet", size=4)
    destination = tmp_path / "scenario.parquet"
    attempts = 0
    partial_names: list[str] = []

    def transfer(_remote: Av2RemoteObject, partial: Path) -> None:
        nonlocal attempts
        attempts += 1
        partial_names.append(partial.name)
        partial.write_bytes(b"bad" if attempts == 1 else b"data")

    monkeypatch.setattr(acquisition, "_download_http", transfer)
    size, digest = acquisition._download_with_retry(
        remote,
        destination,
        backend=acquisition._Backend(
            name="anonymous_s3_http",
            version="test",
        ),
        sleep=lambda _seconds: None,
    )
    assert attempts == 2
    assert partial_names == ["scenario.parquet.partial"] * 2
    assert destination.read_bytes() == b"data"
    assert not destination.with_name("scenario.parquet.partial").exists()
    assert size == 4
    assert digest == _digest(b"data")


def test_download_failure_never_promotes_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _remote("scenario-001", "scenario_scenario-001.parquet", size=4)
    destination = tmp_path / "scenario.parquet"

    def fail(_remote: Av2RemoteObject, partial: Path) -> None:
        partial.write_bytes(b"x")
        raise ArtifactError("interrupted")

    monkeypatch.setattr(acquisition, "_download_http", fail)
    with pytest.raises(ArtifactError, match="interrupted"):
        acquisition._download_with_retry(
            remote,
            destination,
            backend=acquisition._Backend(
                name="anonymous_s3_http",
                version="test",
            ),
            sleep=lambda _seconds: None,
        )
    assert not destination.exists()
    assert not destination.with_name("scenario.parquet.partial").exists()


def test_http_download_uses_bounded_reads_and_validates_content_length(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * (acquisition._READ_CHUNK_SIZE + 7)
    reads: list[int] = []

    class Response:
        def __init__(self) -> None:
            self.status = 200
            self.headers = {"Content-Length": str(len(payload))}
            self.offset = 0

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            reads.append(size)
            offset = self.offset
            chunk = payload[offset : offset + size]
            self.offset = offset + len(chunk)
            return chunk

    monkeypatch.setattr(
        cast(Any, acquisition).urlrequest,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    remote = _remote("scenario-001", "scenario_scenario-001.parquet", size=len(payload))
    partial = tmp_path / "download.partial"
    acquisition._download_http(remote, partial)
    assert partial.read_bytes() == payload
    assert reads and max(reads) == acquisition._READ_CHUNK_SIZE


def test_acquisition_download_then_manifest_verified_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    config = _config(count=1)
    plan = _plan(count=1)
    backend = acquisition._Backend(
        name="anonymous_s3_http",
        version="test",
    )
    monkeypatch.setattr(acquisition, "_resolve_backend", lambda _name: backend)
    monkeypatch.setattr(
        acquisition,
        "check_disk_space",
        lambda *_args, **_kwargs: SimpleNamespace(free_bytes=10**9),
    )
    payloads = {
        item.remote_uri: bytes([index + 1]) * item.size_bytes
        for index, item in enumerate(
            (
                plan.selected_scenarios[0].motion,
                plan.selected_scenarios[0].vector_map,
            )
        )
    }
    calls = 0

    def download(
        remote: Av2RemoteObject,
        destination: Path,
        _backend: object,
    ) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        data = payloads[remote.remote_uri]
        destination.write_bytes(data)
        return len(data), _digest(data)

    first = acquire_av2_plan(
        repository,
        config,
        plan,
        backend_name="anonymous_s3_http",
        backend_version="test",
        listing_seconds=0.1,
        download=download,
    )
    assert calls == 2
    assert first.downloaded_file_count == 2
    known = {item.relative_path: item.sha256 for item in first.acquired_files}
    second = acquire_av2_plan(
        repository,
        config,
        plan,
        backend_name="anonymous_s3_http",
        backend_version="test",
        listing_seconds=0.1,
        prior_sha256=known,
        download=download,
    )
    assert calls == 2
    assert second.reused_file_count == 2
    assert second.reused_bytes == first.total_local_bytes
    verify_acquired_files(repository, second)


def test_corrupt_local_file_is_redownloaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    config = _config(count=1)
    plan = _plan(count=1)
    backend = acquisition._Backend(
        name="anonymous_s3_http",
        version="test",
    )
    monkeypatch.setattr(acquisition, "_resolve_backend", lambda _name: backend)
    monkeypatch.setattr(
        acquisition,
        "check_disk_space",
        lambda *_args, **_kwargs: SimpleNamespace(free_bytes=10**9),
    )
    calls = 0

    def download(
        remote: Av2RemoteObject,
        destination: Path,
        _backend: object,
    ) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        data = b"x" * remote.size_bytes
        destination.write_bytes(data)
        return len(data), _digest(data)

    initial = acquire_av2_plan(
        repository,
        config,
        plan,
        backend_name="anonymous_s3_http",
        backend_version="test",
        listing_seconds=0,
        download=download,
    )
    corrupt = repository / initial.acquired_files[0].relative_path
    corrupt.write_bytes(b"z" * corrupt.stat().st_size)
    known = {item.relative_path: item.sha256 for item in initial.acquired_files}
    repeated = acquire_av2_plan(
        repository,
        config,
        plan,
        backend_name="anonymous_s3_http",
        backend_version="test",
        listing_seconds=0,
        prior_sha256=known,
        download=download,
    )
    assert calls == 3
    assert repeated.downloaded_file_count == 1
    assert repeated.reused_file_count == 1


def test_acquisition_runs_disk_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    config = _config(count=1)
    plan = _plan(count=1)
    monkeypatch.setattr(
        acquisition,
        "_resolve_backend",
        lambda _name: acquisition._Backend(
            name="anonymous_s3_http",
            version="test",
        ),
    )

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise ResourceLimitError("insufficient disk")

    monkeypatch.setattr(acquisition, "check_disk_space", refuse)
    with pytest.raises(ResourceLimitError, match="disk"):
        acquire_av2_plan(
            repository,
            config,
            plan,
            backend_name="anonymous_s3_http",
            backend_version="test",
            listing_seconds=0,
        )


def test_acquisition_rejects_symlink_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    data = repository / "data"
    try:
        data.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    monkeypatch.setattr(
        acquisition,
        "_resolve_backend",
        lambda _name: acquisition._Backend(
            name="anonymous_s3_http",
            version="test",
        ),
    )
    monkeypatch.setattr(
        acquisition,
        "check_disk_space",
        lambda *_args, **_kwargs: SimpleNamespace(free_bytes=10**9),
    )
    with pytest.raises(ArtifactError, match="symbolic"):
        acquire_av2_plan(
            repository,
            _config(count=1),
            _plan(count=1),
            backend_name="anonymous_s3_http",
            backend_version="test",
            listing_seconds=0,
        )


def test_source_manifest_covers_selected_sha256_files_without_absolute_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    pair = _pair("scenario-001", motion_size=4, map_size=3)
    for remote, data in ((pair.motion, b"data"), (pair.vector_map, b"map")):
        path = source / Path(remote.relative_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = registry.discover_dataset_source(
        registry.get_dataset_registry_entry("av2_motion"),
        dataset_version="official-s3-test",
        adapter_version="1.0",
        source_root=source,
        source_root_label="data/external/av2_motion/official-s3-test",
        checksum_mode=registry.SourceChecksumMode.SHA256,
        max_files=2,
        max_total_bytes=7,
    )
    registry.verify_dataset_source(manifest, source_root=source)
    assert manifest.file_count == 2
    assert all(item.sha256 is not None for item in manifest.files)
    text = registry.dataset_source_manifest_to_canonical_json(manifest)
    assert str(tmp_path) not in text


def test_provider_script_import_has_no_io_and_no_forbidden_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        cast(Any, acquisition).urlrequest,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    importlib.reload(acquisition)
    assert calls == []
    source = acquisition.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    for forbidden in ("import pandas", "import torch", "import boto3", "import av2"):
        assert forbidden not in text
    assert "AWS_ACCESS_KEY" not in text


def test_provider_script_declares_exact_evidence_files_and_no_old_env_gate() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    for name in (
        "acquisition_plan.json",
        "acquisition_report.json",
        "source_manifest.json",
        "pilot_plan.json",
        "pilot_report_first_run.json",
        "pilot_report_reuse_run.json",
        "validation_report.json",
        "evidence.json",
        "summary.md",
    ):
        assert name in text
    assert "KINEMATICWEAVE_AV2_PILOT_SOURCE_ROOT" not in text
    assert "shell=False" in text


def test_fixture_only_evidence_cannot_achieve_milestone(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    names = (
        "acquisition_plan.json",
        "acquisition_report.json",
        "source_manifest.json",
        "pilot_plan.json",
        "pilot_report_first_run.json",
        "pilot_report_reuse_run.json",
        "validation_report.json",
        "summary.md",
    )
    for name in names:
        (evidence_root / name).write_text("{}\n", encoding="utf-8")
    checksums = {
        name: hashlib.sha256((evidence_root / name).read_bytes()).hexdigest()
        for name in names
    }
    evidence = {
        "schema_version": "1.0",
        "provider_partition": "fixture",
        "first_run_pilot_measurements": {"materialized_scenario_count": 0},
        "second_run_reuse_measurements": {"reused_scenario_count": 0},
        "evidence_file_sha256": checksums,
        "milestone_decision": "not_achieved",
    }
    (evidence_root / "evidence.json").write_text(
        canonical_json_text(evidence, trailing_newline=True),
        encoding="utf-8",
    )
    namespace: dict[str, object] = {}
    exec(compile(SCRIPT_PATH.read_text("utf-8"), str(SCRIPT_PATH), "exec"), namespace)
    verify = cast(Any, namespace["_verify_evidence"])
    with pytest.raises(ArtifactError, match="not achieved"):
        verify(evidence_root)


def test_evidence_checksum_verification_rejects_tampering(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    names = (
        "acquisition_plan.json",
        "acquisition_report.json",
        "source_manifest.json",
        "pilot_plan.json",
        "pilot_report_first_run.json",
        "pilot_report_reuse_run.json",
        "validation_report.json",
        "summary.md",
    )
    for name in names:
        (evidence_root / name).write_text("{}\n", encoding="utf-8")
    checksums = {
        name: hashlib.sha256((evidence_root / name).read_bytes()).hexdigest()
        for name in names
    }
    evidence = {
        "evidence_file_sha256": checksums,
        "milestone_decision": "achieved",
    }
    (evidence_root / "evidence.json").write_text(
        canonical_json_text(evidence, trailing_newline=True),
        encoding="utf-8",
    )
    namespace: dict[str, object] = {}
    exec(compile(SCRIPT_PATH.read_text("utf-8"), str(SCRIPT_PATH), "exec"), namespace)
    verify = cast(Any, namespace["_verify_evidence"])
    assert verify(evidence_root)["milestone_decision"] == "achieved"
    (evidence_root / "summary.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match=r"summary\.md"):
        verify(evidence_root)
