"""Acquire and execute the corrective ten-scenario AV2 provider pilot."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import ctypes
from ctypes import wintypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any, cast

from kinematicweave.canonical import canonical_json_text
from kinematicweave.config import load_config
from kinematicweave.data import pilot, registry, validation
from kinematicweave.data.av2_acquisition import (
    AV2_OFFICIAL_MOTION_ROOT,
    Av2AcquisitionConfig,
    Av2AcquisitionPlan,
    acquire_av2_plan,
    av2_acquisition_plan_identity,
    av2_acquisition_plan_to_canonical_json,
    av2_acquisition_report_to_canonical_json,
    inspect_av2_remote,
    verify_acquired_files,
)
from kinematicweave.data.materialization import (
    MaterializationDisposition,
    scan_materialization_cache,
)
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.paths import normalize_relative_path, relative_path_text
from kinematicweave.system_metadata import (
    capture_system_metadata,
    system_metadata_to_dict,
)

_EVIDENCE_FILES = (
    "acquisition_plan.json",
    "acquisition_report.json",
    "source_manifest.json",
    "pilot_plan.json",
    "pilot_report_first_run.json",
    "pilot_report_reuse_run.json",
    "validation_report.json",
    "summary.md",
    "evidence.json",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire and execute the real-provider AV2 evidence gate."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument("--official-remote-root", default=AV2_OFFICIAL_MOTION_ROOT)
    parser.add_argument("--partition", default="val")
    parser.add_argument("--scenario-count", type=int, default=10)
    parser.add_argument(
        "--data-root", type=Path, default=Path("data/external/av2_motion")
    )
    parser.add_argument(
        "--cache-root", type=Path, default=Path("cache/av2_provider_pilot")
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("results/phase2/av2_provider_pilot"),
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "s5cmd", "anonymous_s3_http"),
        default="auto",
    )
    return parser


def _run(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            cwd=cwd,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ArtifactError(f"command failed to execute: {arguments[0]}") from error


def _require_ignored(repository_root: Path, relative_path: Path) -> None:
    result = _run(
        (
            "git",
            "-c",
            f"safe.directory={repository_root}",
            "check-ignore",
            "-q",
            relative_path.as_posix(),
        ),
        cwd=repository_root,
    )
    if result.returncode != 0:
        raise ArtifactError(
            f"provider or cache path is not ignored by Git: {relative_path.as_posix()}"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _file_snapshot(paths: tuple[Path, ...]) -> dict[str, tuple[int, str]]:
    return {
        path.as_posix(): (path.stat().st_size, _sha256_file(path)) for path in paths
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def _prior_sha256(
    evidence_root: Path,
    data_relative_root: Path,
    dataset_version: str,
) -> dict[Path, str]:
    path = evidence_root / "source_manifest.json"
    if not path.is_file():
        return {}
    manifest = registry.dataset_source_manifest_from_json(
        path.read_text(encoding="utf-8")
    )
    if manifest.dataset_version != dataset_version:
        return {}
    return {
        data_relative_root / dataset_version / item.relative_path: item.sha256
        for item in manifest.files
        if item.sha256 is not None
    }


def _reconstruct_prior_sha256(
    repository_root: Path,
    data_relative_root: Path,
    plan: Av2AcquisitionPlan,
) -> tuple[dict[Path, str], registry.DatasetSourceManifest | None]:
    source_relative_root = data_relative_root / plan.dataset_version
    source_root = repository_root / source_relative_root
    if not source_root.is_dir():
        return {}, None
    manifest = _source_manifest(
        repository_root,
        source_root,
        source_relative_root,
        plan.dataset_version,
        plan.selected_remote_bytes,
    )
    expected = {Path(item.motion.relative_key) for item in plan.selected_scenarios} | {
        Path(item.vector_map.relative_key) for item in plan.selected_scenarios
    }
    observed = {item.relative_path for item in manifest.files}
    if observed != expected:
        raise ArtifactError(
            "preserved provider installation differs from the acquisition plan"
        )
    return (
        {
            data_relative_root / plan.dataset_version / item.relative_path: item.sha256
            for item in manifest.files
            if item.sha256 is not None
        },
        manifest,
    )


def _source_manifest(
    repository_root: Path,
    source_root: Path,
    source_relative_root: Path,
    dataset_version: str,
    expected_bytes: int,
) -> registry.DatasetSourceManifest:
    entry = registry.get_dataset_registry_entry("av2_motion")
    manifest = registry.discover_dataset_source(
        entry,
        dataset_version=dataset_version,
        adapter_version="1.0",
        source_root=source_root,
        source_root_label=relative_path_text(source_relative_root),
        checksum_mode=registry.SourceChecksumMode.SHA256,
        max_files=20,
        max_total_bytes=expected_bytes,
    )
    registry.verify_dataset_source(manifest, source_root=source_root)
    if manifest.file_count != 20 or manifest.total_bytes != expected_bytes:
        raise ArtifactError("selected provider installation differs from acquisition")
    return manifest


def _pilot_plan(
    manifest: registry.DatasetSourceManifest,
    acquisition_ids: tuple[str, ...],
    *,
    partition: str,
    dataset_version: str,
    root_seed: int,
) -> pilot.Av2PilotPlan:
    config = pilot.Av2PilotConfig(
        source_partition=Path(partition),
        dataset_version=dataset_version,
        canonical_split_name="pilot",
        scenario_count=10,
        root_seed=root_seed,
        assignment_namespace="av2-provider-evidence-v1",
        inclusion_policy="dynamic_only",
        centerline_point_count=50,
        minimum_valid_sample_count=10,
        minimum_valid_duration_ns=1_000_000_000,
        row_group_size=65_536,
        validation_batch_size=65_536,
        materialization_expansion_factor=1.5,
        reserve_fraction=0.15,
    )
    candidates = pilot.discover_av2_pilot_candidates(manifest, config=config)
    indexed = {item.source_scenario_id: item for item in candidates}
    if set(indexed) != set(acquisition_ids):
        raise ArtifactError("source manifest candidates differ from acquisition plan")
    return pilot.Av2PilotPlan(
        schema_version="1.0",
        dataset_id="av2_motion",
        dataset_version=dataset_version,
        source_partition=Path(partition),
        source_manifest_identity=registry.dataset_source_manifest_identity(manifest),
        config=config,
        candidate_count=len(candidates),
        selected_scenarios=tuple(indexed[item] for item in acquisition_ids),
    )


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _peak_memory() -> tuple[int, str]:
    if os.name == "nt":
        try:
            windows_ctypes: Any = ctypes
            kernel32 = windows_ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = windows_ctypes.WinDLL("psapi", use_last_error=True)
            get_process = kernel32.GetCurrentProcess
            get_process.restype = wintypes.HANDLE
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            query = psapi.GetProcessMemoryInfo
            query.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            query.restype = wintypes.BOOL
            if not query(
                get_process(), ctypes.byref(counters), ctypes.sizeof(counters)
            ):
                raise OSError(
                    windows_ctypes.get_last_error(),
                    "GetProcessMemoryInfo",
                )
            return int(counters.PeakWorkingSetSize), "windows-psapi-peak-working-set"
        except (AttributeError, OSError):
            pass
    try:
        resource_module: Any = importlib.import_module("resource")
        peak = resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss
        factor = 1 if platform.system() == "Darwin" else 1024
        return int(peak * factor), "posix-resource-ru_maxrss"
    except (ImportError, OSError):
        raise ArtifactError("peak process memory could not be measured") from None


def _machine_identity() -> dict[str, str | None]:
    if os.name != "nt":
        return {"manufacturer": None, "model": None}
    try:
        winreg: Any = importlib.import_module("winreg")

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\BIOS",
        ) as key:
            manufacturer = str(
                winreg.QueryValueEx(key, "SystemManufacturer")[0]
            ).strip()
            model = str(winreg.QueryValueEx(key, "SystemProductName")[0]).strip()
        return {
            "manufacturer": manufacturer or None,
            "model": model or None,
        }
    except (OSError, ValueError):
        return {"manufacturer": None, "model": None}


def _sanitized_hardware(repository_root: Path) -> dict[str, object]:
    raw = system_metadata_to_dict(capture_system_metadata(repository_root))
    operating_system = dict(cast(Mapping[str, object], raw["operating_system"]))
    python = dict(cast(Mapping[str, object], raw["python"]))
    python.pop("executable", None)
    cpu = dict(cast(Mapping[str, object], raw["cpu"]))
    memory = dict(cast(Mapping[str, object], raw["memory"]))
    uv = _run(("uv", "--version"), cwd=repository_root)
    if uv.returncode != 0 or not uv.stdout.strip():
        raise ArtifactError("uv version could not be measured")
    return {
        "operating_system": operating_system,
        "machine": _machine_identity(),
        "processor": cpu.get("processor"),
        "logical_cpu_count": cpu.get("logical_cpu_count"),
        "total_ram_bytes": memory.get("total_bytes"),
        "python_version": python.get("version"),
        "python_implementation": python.get("implementation"),
        "uv_version": uv.stdout.strip(),
    }


def _pilot_measurements(report: pilot.Av2PilotReport) -> dict[str, object]:
    resources = report.resources
    return {
        "source_verification_seconds": resources.source_verification_seconds,
        "materialization_seconds": resources.materialization_seconds,
        "validation_seconds": resources.validation_seconds,
        "total_seconds": resources.total_seconds,
        "materialized_scenario_count": report.materialized_scenario_count,
        "reused_scenario_count": report.reused_scenario_count,
        "cache_output_bytes": resources.cache_output_bytes,
        "scenarios_per_second": report.scenarios_per_second,
    }


def _counts(report: pilot.Av2PilotReport) -> tuple[dict[str, int], dict[str, int]]:
    return (
        {
            "scenarios": report.source_scenario_count,
            "coordinate_frames": report.source_coordinate_frame_count,
            "agents": report.source_agent_count,
            "trajectories": report.source_trajectory_count,
            "trajectory_samples": report.source_sample_count,
            "vector_map_elements": report.source_map_element_count,
        },
        {
            "scenarios": report.included_scenario_count,
            "agents": report.included_agent_count,
            "trajectories": report.included_trajectory_count,
            "map_elements": report.included_map_element_count,
        },
    )


def _map_reference_omissions(
    repository_root: Path,
    execution: pilot.Av2PilotExecution,
    selected_ids: tuple[str, ...],
) -> dict[str, object]:
    counts: dict[str, int] = {}
    for result in execution.materialization_report.results:
        summary_path = (
            repository_root
            / result.entry_relative_directory
            / "map_adapter_summary.json"
        )
        decoded = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            raise ArtifactError("map adapter summary must contain an object")
        source_id = decoded.get("source_map_id")
        count = decoded.get("omitted_external_lane_reference_count")
        if (
            not isinstance(source_id, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise ArtifactError("map adapter omission provenance is malformed")
        counts[source_id] = count
    if set(counts) != set(selected_ids):
        raise ArtifactError("map omission summaries differ from selected scenarios")
    ordered = [
        {
            "scenario_id": source_id,
            "omitted_external_lane_reference_count": counts[source_id],
        }
        for source_id in selected_ids
    ]
    return {
        "total_omitted_external_lane_reference_count": sum(counts.values()),
        "scenario_counts": ordered,
        "canonical_topology_references_are_local": True,
        "fabricated_lane_nodes": 0,
    }


def _summary(
    *,
    dataset_version: str,
    partition: str,
    selected_ids: tuple[str, ...],
    acquisition: dict[str, object],
    first: dict[str, object],
    second: dict[str, object],
    source_counts: dict[str, int],
    included_counts: dict[str, int],
    exclusion_count: int,
    reason_counts: dict[str, int],
    peak_memory_bytes: int,
    peak_memory_method: str,
    disk: dict[str, int],
    hardware: dict[str, object],
    checksums: dict[str, str],
    reproduction_command: str,
    historical_failed_download: dict[str, object] | None,
    map_reference_omissions: dict[str, object],
) -> str:
    machine = hardware["machine"]
    assert isinstance(machine, dict)
    lines = [
        "# Phase 2 AV2 Provider-Data Evidence",
        "",
        "## 1. Evidence decision",
        "",
        "M2 is achieved with genuine AV2 provider-data evidence.",
        "",
        "## 2. Official source and partition",
        "",
        f"- Source: `{AV2_OFFICIAL_MOTION_ROOT}`",
        f"- Partition: `{partition}`",
        f"- Dataset version: `{dataset_version}`",
        "",
        "## 3. Deterministic selection",
        "",
        f"- Selected scenarios: {len(selected_ids)}",
        *[f"- `{source_id}`" for source_id in selected_ids],
        "",
        "## 4. Downloaded provider data",
        "",
        f"- Backend: `{acquisition['backend']}` `{acquisition['backend_version']}`",
        f"- Candidate scenarios: {acquisition['candidate_count']}",
        f"- Selected remote bytes: {acquisition['selected_remote_bytes']}",
        f"- Downloaded bytes: {acquisition['downloaded_bytes']}",
        f"- Reused download bytes: {acquisition['reused_bytes']}",
        f"- Listing seconds: {float(cast(float, acquisition['listing_seconds'])):.6f}",
        f"- Download seconds: {float(cast(float, acquisition['download_seconds'])):.6f}",
        (
            "- Failed-attempt provider files: "
            f"{historical_failed_download['downloaded_file_count']} files, "
            f"{historical_failed_download['downloaded_bytes']} bytes; "
            "stage timing was not persisted."
            if historical_failed_download is not None
            else "- Failed-attempt provider files: none recovered."
        ),
        "",
        "## 5. First-run materialization",
        "",
        f"- Materialized scenarios: {first['materialized_scenario_count']}",
        f"- Reused scenarios: {first['reused_scenario_count']}",
        f"- Materialization seconds: {float(cast(float, first['materialization_seconds'])):.6f}",
        f"- Validation seconds: {float(cast(float, first['validation_seconds'])):.6f}",
        f"- Total seconds: {float(cast(float, first['total_seconds'])):.6f}",
        f"- Scenarios per second: {float(cast(float, first['scenarios_per_second'])):.6f}",
        "",
        "## 6. Second-run cache reuse",
        "",
        f"- Materialized scenarios: {second['materialized_scenario_count']}",
        f"- Reused scenarios: {second['reused_scenario_count']}",
        f"- Reuse seconds: {float(cast(float, second['materialization_seconds'])):.6f}",
        f"- Validation seconds: {float(cast(float, second['validation_seconds'])):.6f}",
        f"- Total seconds: {float(cast(float, second['total_seconds'])):.6f}",
        "",
        "## 7. Canonical data counts",
        "",
        *[f"- Source {key}: {value}" for key, value in source_counts.items()],
        *[f"- Included {key}: {value}" for key, value in included_counts.items()],
        "",
        "## 8. Eligibility and exclusions",
        "",
        f"- Exclusions: {exclusion_count}",
        *[f"- `{key}`: {value}" for key, value in reason_counts.items()],
        "",
        "## 9. Runtime, memory, and disk measurements",
        "",
        f"- Peak process memory bytes: {peak_memory_bytes}",
        f"- Peak-memory method: `{peak_memory_method}`",
        f"- Disk free before acquisition: {disk['before_acquisition_bytes']}",
        f"- Disk free after acquisition: {disk['after_acquisition_bytes']}",
        f"- Disk free after materialization: {disk['after_materialization_bytes']}",
        "- CPU-only pipeline: yes",
        "- GPU use: 0",
        "",
        "## 10. Hardware used",
        "",
        f"- Manufacturer: `{machine.get('manufacturer')}`",
        f"- Model: `{machine.get('model')}`",
        f"- Processor: `{hardware['processor']}`",
        f"- Logical CPUs: {hardware['logical_cpu_count']}",
        f"- Total RAM bytes: {hardware['total_ram_bytes']}",
        f"- Operating system: `{hardware['operating_system']}`",
        f"- Python: `{hardware['python_version']}`",
        f"- uv: `{hardware['uv_version']}`",
        "",
        "## 11. Reproduction command",
        "",
        "```text",
        reproduction_command,
        "```",
        "",
        "## 12. Evidence-file checksums",
        "",
        *[f"- `{name}`: `{digest}`" for name, digest in sorted(checksums.items())],
        "",
        "The complete cross-file checksum set, including this summary, is recorded in `evidence.json`.",
        "",
        "## 13. Provider incompatibilities corrected",
        "",
        "- Official motion Parquet stores `start_timestamp` and `end_timestamp` as `double`; the adapter now accepts only finite, exactly integral float64 values that losslessly normalize to signed int64 nanoseconds without rounding.",
        "- Official scenario-local maps can reference lanes outside the local crop; the adapter now omits those references from canonical topology, records the exact omitted IDs in semantic provenance, and adds relation-specific quality flags without fabricating geometry or nodes.",
        (
            "- Omitted external lane references across the ten scenarios: "
            f"{map_reference_omissions['total_omitted_external_lane_reference_count']}"
        ),
        "- Canonical unresolved lane references: 0",
        "- Fabricated canonical lane nodes: 0",
        "",
        "## 14. Remaining limitations",
        "",
        "- This is a deterministic ten-scenario provider compatibility and laptop evidence pass, not a final statistical campaign.",
        "- Stage timings are measured observations and are not claimed to be deterministic.",
        "",
    ]
    return "\n".join(lines)


def _verify_evidence(evidence_root: Path) -> dict[str, object]:
    names = tuple(
        path.name
        for path in sorted(evidence_root.iterdir(), key=lambda item: item.name)
        if path.is_file()
    )
    if names != tuple(sorted(_EVIDENCE_FILES)):
        raise ArtifactError("evidence directory does not contain the exact file set")
    evidence = json.loads((evidence_root / "evidence.json").read_text("utf-8"))
    if not isinstance(evidence, dict):
        raise ArtifactError("evidence.json must contain an object")
    checksums = evidence.get("evidence_file_sha256")
    if not isinstance(checksums, dict):
        raise ArtifactError("evidence checksums are missing")
    expected_names = set(_EVIDENCE_FILES) - {"evidence.json"}
    if set(checksums) != expected_names:
        raise ArtifactError("evidence checksum file set differs")
    for name, expected in checksums.items():
        if _sha256_file(evidence_root / name) != expected:
            raise ArtifactError(f"evidence checksum differs: {name}")
    if evidence.get("milestone_decision") != "achieved":
        raise ArtifactError("provider milestone was not achieved")
    return evidence


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = args.repository_root.resolve(strict=True)
    data_relative_root = normalize_relative_path(args.data_root)
    cache_relative_root = normalize_relative_path(args.cache_root)
    evidence_relative_root = normalize_relative_path(args.evidence_root)
    evidence_root = repository_root / evidence_relative_root
    existing_evidence: dict[str, object] = {}
    existing_evidence_path = evidence_root / "evidence.json"
    if existing_evidence_path.is_file():
        decoded = json.loads(existing_evidence_path.read_text(encoding="utf-8"))
        if isinstance(decoded, dict):
            existing_evidence = decoded
    if args.scenario_count != 10:
        raise ValidationError(
            "the committed provider evidence run requires 10 scenarios"
        )
    _require_ignored(
        repository_root,
        data_relative_root / "provider-ignore-probe.parquet",
    )
    _require_ignored(
        repository_root,
        cache_relative_root / "cache-ignore-probe.parquet",
    )
    project_config = load_config(repository_root / "configs" / "project.toml")
    acquisition_config = Av2AcquisitionConfig(
        remote_root=args.official_remote_root,
        partition=args.partition,
        scenario_count=args.scenario_count,
        root_seed=project_config.root_seed,
        assignment_namespace="av2-provider-evidence-v1",
        local_relative_root=data_relative_root,
        backend=args.backend,
    )
    disk_before = shutil.disk_usage(repository_root).free
    plan, backend_name, backend_version, listing_seconds = inspect_av2_remote(
        acquisition_config
    )
    prior = _prior_sha256(
        evidence_root,
        data_relative_root,
        plan.dataset_version,
    )
    reconstructed_manifest: registry.DatasetSourceManifest | None = None
    recovered_failed_download = False
    if not prior:
        prior, reconstructed_manifest = _reconstruct_prior_sha256(
            repository_root,
            data_relative_root,
            plan,
        )
        recovered_failed_download = bool(prior)
    acquisition_report = acquire_av2_plan(
        repository_root,
        acquisition_config,
        plan,
        backend_name=backend_name,
        backend_version=backend_version,
        listing_seconds=listing_seconds,
        prior_sha256=prior,
    )
    verify_acquired_files(repository_root, acquisition_report)
    disk_after_acquisition = shutil.disk_usage(repository_root).free
    source_relative_root = data_relative_root / plan.dataset_version
    source_root = repository_root / source_relative_root
    manifest = (
        reconstructed_manifest
        if reconstructed_manifest is not None
        else _source_manifest(
            repository_root,
            source_root,
            source_relative_root,
            plan.dataset_version,
            acquisition_report.total_local_bytes,
        )
    )
    manifest_identity = registry.dataset_source_manifest_identity(manifest)
    selected_ids = tuple(item.source_scenario_id for item in plan.selected_scenarios)
    pilot_plan = _pilot_plan(
        manifest,
        selected_ids,
        partition=plan.partition,
        dataset_version=plan.dataset_version,
        root_seed=project_config.root_seed,
    )
    source_paths = tuple(
        repository_root / item.relative_path
        for item in acquisition_report.acquired_files
    )
    source_snapshot = _file_snapshot(source_paths)
    first = pilot.execute_av2_pilot(
        repository_root,
        source_root,
        cache_relative_root,
        pilot_plan,
    )
    if not first.validation_report.is_eligible:
        raise ArtifactError("provider pilot canonical validation did not pass")
    if (
        first.report.materialized_scenario_count == 10
        and first.report.reused_scenario_count == 0
    ):
        first_successful_report = first.report
    elif (
        first.report.materialized_scenario_count == 0
        and first.report.reused_scenario_count == 10
    ):
        prior_first_path = evidence_root / "pilot_report_first_run.json"
        if not prior_first_path.is_file():
            raise ArtifactError(
                "cache is complete but first-run evidence is unavailable"
            )
        first_successful_report = pilot.av2_pilot_report_from_json(
            prior_first_path.read_text(encoding="utf-8")
        )
        if (
            first_successful_report.plan_identity
            != pilot.av2_pilot_plan_identity(pilot_plan)
            or first_successful_report.selected_scenario_ids != selected_ids
            or first_successful_report.materialized_scenario_count != 10
            or first_successful_report.reused_scenario_count != 0
            or first_successful_report.materialization_plan_identity
            != first.report.materialization_plan_identity
        ):
            raise ArtifactError(
                "preserved first-run evidence differs from the current pilot"
            )
    else:
        raise ArtifactError(
            "provider pilot did not materialize or reuse all ten scenarios"
        )
    first_outputs = tuple(
        repository_root / result.entry_relative_directory / output.relative_path
        for result in first.materialization_report.results
        for output in result.outputs
    )
    if len(first_outputs) != 70:
        raise ArtifactError("first provider pilot did not create seventy outputs")
    cache_snapshot = _file_snapshot(first_outputs)
    second = pilot.execute_av2_pilot(
        repository_root,
        source_root,
        cache_relative_root,
        pilot_plan,
    )
    if (
        second.report.materialized_scenario_count != 0
        or second.report.reused_scenario_count != 10
        or any(
            result.disposition is not MaterializationDisposition.REUSED
            for result in second.materialization_report.results
        )
    ):
        raise ArtifactError("second provider pilot did not reuse all ten scenarios")
    if _file_snapshot(first_outputs) != cache_snapshot:
        raise ArtifactError("cache output bytes changed during reuse execution")
    if _file_snapshot(source_paths) != source_snapshot:
        raise ArtifactError("provider source files changed during conversion")
    inventory = scan_materialization_cache(repository_root, cache_relative_root)
    if inventory.complete_entry_count != 10 or inventory.incomplete_entry_count != 0:
        raise ArtifactError("materialization cache inventory is incomplete")
    map_reference_omissions = _map_reference_omissions(
        repository_root,
        second,
        selected_ids,
    )
    disk_after_materialization = shutil.disk_usage(repository_root).free
    peak_memory_bytes, peak_memory_method = _peak_memory()
    hardware = _sanitized_hardware(repository_root)
    machine = hardware["machine"]
    if (
        not isinstance(machine, dict)
        or "ASUS" not in str(machine.get("manufacturer", "")).upper()
    ):
        raise ArtifactError("execution did not occur on the intended ASUS hardware")

    evidence_root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "acquisition_plan.json": av2_acquisition_plan_to_canonical_json(plan),
        "acquisition_report.json": av2_acquisition_report_to_canonical_json(
            acquisition_report
        ),
        "source_manifest.json": registry.dataset_source_manifest_to_canonical_json(
            manifest
        ),
        "pilot_plan.json": pilot.av2_pilot_plan_to_canonical_json(pilot_plan),
        "pilot_report_first_run.json": pilot.av2_pilot_report_to_canonical_json(
            first_successful_report
        ),
        "pilot_report_reuse_run.json": pilot.av2_pilot_report_to_canonical_json(
            second.report
        ),
        "validation_report.json": validation.canonical_validation_report_to_canonical_json(
            second.validation_report
        ),
    }
    for name, text in payloads.items():
        _atomic_write(evidence_root / name, text)
    first_measurements = _pilot_measurements(first_successful_report)
    second_measurements = _pilot_measurements(second.report)
    source_counts, included_counts = _counts(second.report)
    reason_counts: dict[str, int] = {
        cast(str, item[0]): cast(int, item[1])
        for item in second.validation_report.exclusion_reason_counts
    }
    acquisition_measurements = {
        "backend": acquisition_report.backend,
        "backend_version": acquisition_report.backend_version,
        "candidate_count": acquisition_report.candidate_count,
        "selected_scenario_count": acquisition_report.selected_scenario_count,
        "selected_remote_bytes": plan.selected_remote_bytes,
        "downloaded_bytes": acquisition_report.downloaded_bytes,
        "reused_bytes": acquisition_report.reused_bytes,
        "listing_seconds": acquisition_report.listing_seconds,
        "download_seconds": acquisition_report.download_seconds,
    }
    prior_historical = existing_evidence.get("historical_failed_attempt_acquisition")
    historical_failed_download: dict[str, object] | None = (
        dict(cast(Mapping[str, object], prior_historical))
        if isinstance(prior_historical, Mapping)
        else None
    )
    if recovered_failed_download:
        historical_failed_download = {
            "downloaded_file_count": len(acquisition_report.acquired_files),
            "downloaded_bytes": acquisition_report.total_local_bytes,
            "listing_seconds": None,
            "download_seconds": None,
        }
    disk = {
        "before_acquisition_bytes": disk_before,
        "after_acquisition_bytes": disk_after_acquisition,
        "after_materialization_bytes": disk_after_materialization,
    }
    reproduction_command = (
        "uv run --frozen python scripts/run_av2_provider_pilot.py "
        "--repository-root . --scenario-count 10 "
        "--data-root data/external/av2_motion "
        "--cache-root cache/av2_provider_pilot "
        "--evidence-root results/phase2/av2_provider_pilot --backend auto"
    )
    preliminary_checksums = {
        name: _sha256_file(evidence_root / name) for name in payloads
    }
    summary = _summary(
        dataset_version=plan.dataset_version,
        partition=plan.partition,
        selected_ids=selected_ids,
        acquisition=acquisition_measurements,
        first=first_measurements,
        second=second_measurements,
        source_counts=source_counts,
        included_counts=included_counts,
        exclusion_count=second.report.exclusion_count,
        reason_counts=reason_counts,
        peak_memory_bytes=peak_memory_bytes,
        peak_memory_method=peak_memory_method,
        disk=disk,
        hardware=hardware,
        checksums=preliminary_checksums,
        reproduction_command=reproduction_command,
        historical_failed_download=historical_failed_download,
        map_reference_omissions=map_reference_omissions,
    )
    _atomic_write(evidence_root / "summary.md", summary)
    cross_file_checksums = {
        name: _sha256_file(evidence_root / name)
        for name in _EVIDENCE_FILES
        if name != "evidence.json"
    }
    evidence = {
        "schema_version": "1.0",
        "dataset_id": "av2_motion",
        "dataset_version": plan.dataset_version,
        "official_remote_root": plan.remote_root,
        "provider_partition": plan.partition,
        "acquisition_plan_identity": av2_acquisition_plan_identity(plan),
        "source_manifest_identity": manifest_identity,
        "pilot_plan_identity": pilot.av2_pilot_plan_identity(pilot_plan),
        "materialization_plan_identity": second.report.materialization_plan_identity,
        "validation_report_identity": second.report.validation_report_identity,
        "selected_scenario_ids": list(selected_ids),
        "acquisition_backend": {
            "name": acquisition_report.backend,
            "version": acquisition_report.backend_version,
        },
        "acquisition_measurements": acquisition_measurements,
        "historical_failed_attempt_acquisition": historical_failed_download,
        "first_run_pilot_measurements": first_measurements,
        "second_run_reuse_measurements": second_measurements,
        "source_counts": source_counts,
        "included_counts": included_counts,
        "exclusions": {
            "count": second.report.exclusion_count,
            "reason_counts": reason_counts,
        },
        "resources": {
            "peak_process_memory_bytes": peak_memory_bytes,
            "peak_memory_method": peak_memory_method,
            "disk_free_bytes": disk,
            "cpu_only": True,
            "gpu_use_count": 0,
        },
        "provider_timestamp_physical_schema": {
            "start_timestamp": "double",
            "end_timestamp": "double",
            "num_timestamps": "int64",
        },
        "canonical_timestamp_normalization": (
            "finite exact integral float64 to signed int64 nanoseconds; no rounding"
        ),
        "provider_map_external_reference_handling": map_reference_omissions,
        "provider_incompatibilities_corrected": [
            {
                "issue": ("official motion timestamp endpoints use Arrow double"),
                "correction": (
                    "accept finite exactly integral float64 endpoints and "
                    "losslessly normalize to signed int64 nanoseconds without rounding"
                ),
            },
            {
                "issue": (
                    "official scenario-local maps contain lane references "
                    "outside the local archive"
                ),
                "correction": (
                    "retain locally resolving topology; record omitted external "
                    "IDs in semantic provenance and relation-specific quality flags; "
                    "fabricate no lane geometry or nodes"
                ),
            },
        ],
        "hardware": hardware,
        "evidence_file_sha256": cross_file_checksums,
        "milestone_decision": "achieved",
    }
    _atomic_write(
        evidence_root / "evidence.json",
        canonical_json_text(evidence, trailing_newline=True),
    )
    _verify_evidence(evidence_root)
    sys.stdout.write(canonical_json_text(evidence))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArtifactError, ValidationError) as error:
        sys.stderr.write(f"error: {error}\n")
        raise SystemExit(2) from None
