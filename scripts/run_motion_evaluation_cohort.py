"""Acquire, materialize, validate, and freeze the Phase 4 AV2 cohort."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, cast

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.config import load_config
from kinematicweave.data import av2_acquisition, pilot, registry
from kinematicweave.data.materialization import (
    MaterializationDisposition,
    materialization_unit_cache_key,
    scan_materialization_cache,
)
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.experiments.motion_cohort import (
    CohortRole,
    CohortRunMeasurements,
    CohortValidationSummary,
    MotionCohortConfig,
    MotionCohortManifest,
    MotionCohortSelectionUnit,
    MotionCohortUnit,
    ResourcePilotResult,
    build_cohort_manifest,
    build_role_acquisition_plan,
    build_role_pilot_plan,
    cohort_dataset_version,
    cohort_validation_summaries,
    motion_cohort_manifest_from_json,
    motion_cohort_manifest_to_canonical_json,
    resource_pilot_result_to_canonical_json,
    select_motion_cohort,
    validation_status_by_source,
)
from kinematicweave.paths import normalize_relative_path

_EVIDENCE_NAMES = (
    "cohort_manifest.json",
    "train_source_manifest.json",
    "val_source_manifest.json",
    "acquisition_report.json",
    "materialization_report.json",
    "reuse_report.json",
    "validation_report.json",
    "resource_pilot.json",
    "cohort_statistics.json",
    "summary.md",
)
_ROLE_COUNTS = {
    CohortRole.DEVELOPMENT: 150,
    CohortRole.PILOT: 50,
    CohortRole.TEST: 300,
}
_PROBE_COUNTS = {
    CohortRole.DEVELOPMENT: 10,
    CohortRole.PILOT: 5,
    CohortRole.TEST: 10,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze the 500-scenario Phase 4 AV2 motion cohort."
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/external/av2_motion_phase4"),
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("cache/phase4_motion_cohort"),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("results/phase4/motion_cohort"),
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "s5cmd", "anonymous_s3_http"),
        default="auto",
    )
    return parser


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise ArtifactError(f"cannot atomically write {path.name}") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            tuple(arguments),
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
        raise ArtifactError(f"generated path is not ignored: {relative_path}")


def _verify_phase3_evidence(repository_root: Path) -> dict[str, str]:
    root = repository_root / "results/phase3/milestone"
    evidence = cast(
        dict[str, object],
        json.loads((root / "evidence.json").read_text(encoding="utf-8")),
    )
    components = cast(Mapping[str, object], evidence["component_sha256"])
    for name, expected in components.items():
        if _sha256_file(root / name) != expected:
            raise ArtifactError("Phase 3 milestone evidence checksum differs")
    if (
        evidence.get("milestone_decision") != "achieved"
        or evidence.get("genuine_av2_provider_gate") != "PASS"
    ):
        raise ArtifactError("Phase 3 milestone evidence is not accepted")
    return {
        "evidence_sha256": _sha256_file(root / "evidence.json"),
        "package_identity": cast(
            str,
            cast(Mapping[str, object], evidence["phase3_identities"])[
                "package_identity"
            ],
        ),
    }


def _peak_process_memory_bytes() -> int:
    try:
        resource_module: Any = importlib.import_module("resource")
        peak = resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss
        return int(peak if platform.system() == "Darwin" else peak * 1024)
    except (ImportError, OSError):
        raise ArtifactError("peak process memory could not be measured") from None


def _hardware(repository_root: Path) -> dict[str, object]:
    cpu = platform.processor()
    if not cpu:
        try:
            cpu = next(
                line.split(":", maxsplit=1)[1].strip()
                for line in Path("/proc/cpuinfo")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.startswith("model name")
            )
        except (OSError, StopIteration, IndexError):
            cpu = "unavailable"
    manufacturer: str | None = None
    model: str | None = None
    total_ram: int | None = None
    command = (
        "$c=Get-CimInstance Win32_ComputerSystem;"
        "[pscustomobject]@{manufacturer=$c.Manufacturer;model=$c.Model;"
        "total_ram_bytes=[int64]$c.TotalPhysicalMemory}|ConvertTo-Json -Compress"
    )
    result = _run(
        ("powershell.exe", "-NoProfile", "-Command", command),
        cwd=repository_root,
    )
    if result.returncode == 0:
        try:
            machine = cast(dict[str, object], json.loads(result.stdout))
            manufacturer = cast(str, machine.get("manufacturer"))
            model = cast(str, machine.get("model"))
            total_ram = cast(int, machine.get("total_ram_bytes"))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if manufacturer is None or "ASUS" not in manufacturer.upper():
        raise ArtifactError("execution did not verify the intended ASUS hardware")
    gpu = _run(
        (
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ),
        cwd=repository_root,
    )
    gpu_value = gpu.stdout.strip() if gpu.returncode == 0 else "unused/unavailable"
    uv = _run(("uv", "--version"), cwd=repository_root)
    if uv.returncode != 0:
        raise ArtifactError("uv version could not be measured")
    return {
        "manufacturer": manufacturer,
        "model": model,
        "cpu": cpu,
        "logical_cpu_count": os.cpu_count(),
        "total_ram_bytes": total_ram,
        "gpu": gpu_value,
        "operating_system": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "uv_version": uv.stdout.strip(),
        "wsl2": "microsoft" in platform.release().casefold(),
    }


def _catalog(
    partition: str,
    *,
    config: MotionCohortConfig,
    data_relative_root: Path,
    backend: str,
) -> tuple[
    tuple[av2_acquisition.Av2RemoteScenarioPair, ...],
    str,
    str,
    float,
    str,
]:
    acquisition_config = av2_acquisition.Av2AcquisitionConfig(
        partition=partition,
        scenario_count=300,
        root_seed=config.root_seed,
        assignment_namespace=f"phase4-motion-cohort-catalog:{partition}",
        local_relative_root=data_relative_root,
        backend=backend,
    )
    pairs, backend_name, backend_version, seconds = (
        av2_acquisition.inspect_av2_remote_catalog(acquisition_config)
    )
    identity = av2_acquisition.remote_catalog_identity(pairs)
    return pairs, backend_name, backend_version, seconds, identity


def _role_units(
    selection: Sequence[MotionCohortSelectionUnit],
    role: CohortRole,
    count: int | None = None,
) -> tuple[MotionCohortSelectionUnit, ...]:
    values = tuple(item for item in selection if item.cohort_role is role)
    expected = _ROLE_COUNTS[role]
    if len(values) != expected:
        raise ArtifactError("selected role count differs from frozen cohort")
    return values if count is None else values[:count]


def _checkpoint_report(
    path: Path,
    plan: av2_acquisition.Av2AcquisitionPlan,
    repository_root: Path,
) -> av2_acquisition.Av2AcquisitionReport | None:
    if not path.is_file():
        return None
    report = av2_acquisition.av2_acquisition_report_from_json(
        path.read_text(encoding="utf-8")
    )
    if report.plan_identity != av2_acquisition.av2_acquisition_plan_identity(plan):
        return None
    av2_acquisition.verify_acquired_files(repository_root, report)
    return report


def _prior_sha256(
    reports: Sequence[av2_acquisition.Av2AcquisitionReport | None],
) -> dict[Path, str]:
    return {
        item.relative_path: item.sha256
        for report in reports
        if report is not None
        for item in report.acquired_files
    }


def _acquire(
    repository_root: Path,
    checkpoint: Path,
    acquisition_config: av2_acquisition.Av2AcquisitionConfig,
    plan: av2_acquisition.Av2AcquisitionPlan,
    *,
    backend_name: str,
    backend_version: str,
    listing_seconds: float,
    prior_reports: Sequence[av2_acquisition.Av2AcquisitionReport | None],
) -> av2_acquisition.Av2AcquisitionReport:
    previous = _checkpoint_report(checkpoint, plan, repository_root)
    report = av2_acquisition.acquire_av2_plan(
        repository_root,
        acquisition_config,
        plan,
        backend_name=backend_name,
        backend_version=backend_version,
        listing_seconds=listing_seconds,
        prior_sha256=_prior_sha256((*prior_reports, previous)),
    )
    av2_acquisition.verify_acquired_files(repository_root, report)
    _atomic_write(
        checkpoint,
        av2_acquisition.av2_acquisition_report_to_canonical_json(report),
    )
    return report


def _source_manifest(
    repository_root: Path,
    source_relative_root: Path,
    reports: Sequence[av2_acquisition.Av2AcquisitionReport],
    *,
    dataset_version: str,
    checkpoint: Path,
) -> registry.DatasetSourceManifest:
    if checkpoint.is_file():
        manifest = registry.dataset_source_manifest_from_json(
            checkpoint.read_text(encoding="utf-8")
        )
        registry.verify_dataset_source(
            manifest,
            source_root=repository_root / source_relative_root,
        )
        return manifest
    files = []
    for report in reports:
        for item in report.acquired_files:
            try:
                relative = item.relative_path.relative_to(source_relative_root)
            except ValueError:
                raise ArtifactError(
                    "acquired source path is outside source manifest root"
                ) from None
            files.append(
                registry.DatasetSourceFile(
                    relative_path=relative,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                )
            )
    files = sorted(files, key=lambda item: item.relative_path.as_posix())
    if len({item.relative_path for item in files}) != len(files):
        raise ArtifactError("source manifest paths overlap")
    entry = registry.get_dataset_registry_entry("av2_motion")
    manifest = registry.DatasetSourceManifest(
        schema_version="1.0",
        dataset_id="av2_motion",
        dataset_version=dataset_version,
        adapter_name="av2_motion_adapter",
        adapter_version="1.0",
        source_kind="external_directory",
        source_root_label=source_relative_root.as_posix(),
        availability="available",
        checksum_mode="sha256",
        file_count=len(files),
        total_bytes=sum(item.size_bytes for item in files),
        files=tuple(files),
        license_reference=entry.license_reference,
        citation_reference=entry.citation_reference,
        discovered_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    registry.verify_dataset_source(
        manifest,
        source_root=repository_root / source_relative_root,
    )
    _atomic_write(
        checkpoint,
        registry.dataset_source_manifest_to_canonical_json(manifest),
    )
    return manifest


def _execute_roles(
    repository_root: Path,
    plans: Mapping[CohortRole, pilot.Av2PilotPlan],
    source_roots: Mapping[CohortRole, Path],
    cache_root: Path,
) -> dict[CohortRole, pilot.Av2PilotExecution]:
    return {
        role: pilot.execute_av2_pilot(
            repository_root,
            source_roots[role],
            cache_root,
            plans[role],
        )
        for role in CohortRole
    }


def _execution_totals(
    executions: Mapping[CohortRole, pilot.Av2PilotExecution],
) -> dict[str, int | float]:
    return {
        "scenario_count": sum(
            execution.report.selected_scenario_count
            for execution in executions.values()
        ),
        "trajectory_count": sum(
            execution.report.source_trajectory_count
            for execution in executions.values()
        ),
        "materialized_count": sum(
            execution.report.materialized_scenario_count
            for execution in executions.values()
        ),
        "reused_count": sum(
            execution.report.reused_scenario_count for execution in executions.values()
        ),
        "source_verification_seconds": sum(
            execution.report.resources.source_verification_seconds
            for execution in executions.values()
        ),
        "conversion_seconds": sum(
            execution.report.resources.materialization_seconds
            for execution in executions.values()
        ),
        "validation_seconds": sum(
            execution.report.resources.validation_seconds
            for execution in executions.values()
        ),
        "total_seconds": sum(
            execution.report.resources.total_seconds
            for execution in executions.values()
        ),
        "source_bytes": sum(
            execution.report.resources.selected_source_bytes
            for execution in executions.values()
        ),
        "cache_bytes": sum(
            execution.report.resources.cache_output_bytes
            for execution in executions.values()
        ),
    }


def _cache_snapshot(
    repository_root: Path,
    executions: Mapping[CohortRole, pilot.Av2PilotExecution],
) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(repository_root).as_posix(): (
            path.stat().st_size,
            _sha256_file(path),
        )
        for execution in executions.values()
        for result in execution.materialization_report.results
        for output in result.outputs
        for path in (
            repository_root / result.entry_relative_directory / output.relative_path,
        )
    }


def _run_measurements(
    run_kind: str,
    totals: Mapping[str, int | float],
    *,
    acquisition_seconds: float,
    elapsed_seconds: float,
    disk_before: int,
    disk_after: int,
) -> CohortRunMeasurements:
    return CohortRunMeasurements(
        run_kind=run_kind,
        scenario_count=cast(int, totals["scenario_count"]),
        trajectory_count=cast(int, totals["trajectory_count"]),
        materialized_count=cast(int, totals["materialized_count"]),
        reused_count=cast(int, totals["reused_count"]),
        acquisition_verification_seconds=acquisition_seconds
        + cast(float, totals["source_verification_seconds"]),
        conversion_seconds=cast(float, totals["conversion_seconds"]),
        validation_seconds=cast(float, totals["validation_seconds"]),
        total_seconds=elapsed_seconds,
        peak_process_memory_bytes=_peak_process_memory_bytes(),
        source_bytes=cast(int, totals["source_bytes"]),
        cache_bytes=cast(int, totals["cache_bytes"]),
        disk_free_before_bytes=disk_before,
        disk_free_after_bytes=disk_after,
        worker_count=1,
        cpu_only=True,
        gpu_use_count=0,
    )


def _run_to_dict(value: CohortRunMeasurements) -> dict[str, object]:
    return {
        "run_kind": value.run_kind,
        "scenario_count": value.scenario_count,
        "trajectory_count": value.trajectory_count,
        "materialized_count": value.materialized_count,
        "reused_count": value.reused_count,
        "acquisition_verification_seconds": value.acquisition_verification_seconds,
        "conversion_seconds": value.conversion_seconds,
        "validation_seconds": value.validation_seconds,
        "total_seconds": value.total_seconds,
        "peak_process_memory_bytes": value.peak_process_memory_bytes,
        "source_bytes": value.source_bytes,
        "cache_bytes": value.cache_bytes,
        "disk_free_before_bytes": value.disk_free_before_bytes,
        "disk_free_after_bytes": value.disk_free_after_bytes,
        "worker_count": value.worker_count,
        "cpu_only": value.cpu_only,
        "gpu_use_count": value.gpu_use_count,
        "scenarios_per_second": value.scenarios_per_second,
        "trajectories_per_second": value.trajectories_per_second,
    }


def _validation_to_dict(
    summary: CohortValidationSummary,
) -> dict[str, object]:
    return {
        "cohort_role": summary.cohort_role,
        "source_scenarios": summary.source_scenarios,
        "included_scenarios": summary.included_scenarios,
        "source_agents": summary.source_agents,
        "included_agents": summary.included_agents,
        "source_trajectories": summary.source_trajectories,
        "included_trajectories": summary.included_trajectories,
        "source_samples": summary.source_samples,
        "included_samples": summary.included_samples,
        "vector_map_elements": summary.vector_map_elements,
        "included_vector_map_elements": summary.included_vector_map_elements,
        "valid_run_count": summary.valid_run_count,
        "included_valid_run_count": summary.included_valid_run_count,
        "exclusion_reason_counts": list(summary.exclusion_reason_counts),
        "source_agent_class_counts": list(summary.source_agent_class_counts),
        "included_agent_class_counts": list(summary.included_agent_class_counts),
        "source_city_counts": list(summary.source_city_counts),
        "included_city_counts": list(summary.included_city_counts),
        "trajectory_sample_count_distribution": {
            name: getattr(
                summary.trajectory_sample_count_distribution,
                name,
            )
            for name in ("count", "minimum", "p25", "median", "p75", "maximum")
        },
        "trajectory_duration_seconds_distribution": {
            name: getattr(
                summary.trajectory_duration_seconds_distribution,
                name,
            )
            for name in ("count", "minimum", "p25", "median", "p75", "maximum")
        },
    }


def _cohort_units(
    selection: Sequence[MotionCohortSelectionUnit],
    reports: Mapping[CohortRole, av2_acquisition.Av2AcquisitionReport],
    executions: Mapping[CohortRole, pilot.Av2PilotExecution],
    statuses: Mapping[str, tuple[bool, str | None]],
) -> tuple[MotionCohortUnit, ...]:
    report_files = {
        item.remote_uri: item
        for report in reports.values()
        for item in report.acquired_files
    }
    cache_keys = {
        unit.unit_id.removeprefix("pilot-unit:av2:"): materialization_unit_cache_key(
            unit
        )
        for execution in executions.values()
        for unit in execution.materialization_plan.units
    }
    values = []
    for item in selection:
        source_id = item.remote_pair.source_scenario_id
        motion = report_files[item.remote_pair.motion.remote_uri]
        vector_map = report_files[item.remote_pair.vector_map.remote_uri]
        included, reason = statuses[source_id]
        values.append(
            MotionCohortUnit(
                provider_partition=item.provider_partition,
                cohort_role=item.cohort_role,
                selection_rank=item.selection_rank,
                source_scenario_id=source_id,
                motion_object_path=motion.relative_path,
                motion_size_bytes=motion.size_bytes,
                motion_sha256=motion.sha256,
                map_object_path=vector_map.relative_path,
                map_size_bytes=vector_map.size_bytes,
                map_sha256=vector_map.sha256,
                materialization_cache_key=cache_keys[source_id],
                validation_included=included,
                exclusion_reason=reason,
            )
        )
    return tuple(values)


def _role_report(
    executions: Mapping[CohortRole, pilot.Av2PilotExecution],
) -> dict[str, object]:
    return {
        role.value: {
            "plan_identity": execution.report.plan_identity,
            "materialization_plan_identity": (
                execution.report.materialization_plan_identity
            ),
            "validation_report_identity": execution.report.validation_report_identity,
            "selected_scenario_count": execution.report.selected_scenario_count,
            "materialized_scenario_count": execution.report.materialized_scenario_count,
            "reused_scenario_count": execution.report.reused_scenario_count,
            "cache_entry_count": len(execution.report.cache_entry_directories),
            "cache_output_bytes": execution.report.resources.cache_output_bytes,
            "source_verification_seconds": (
                execution.report.resources.source_verification_seconds
            ),
            "conversion_seconds": execution.report.resources.materialization_seconds,
            "validation_seconds": execution.report.resources.validation_seconds,
            "total_seconds": execution.report.resources.total_seconds,
        }
        for role, execution in executions.items()
    }


def _summary_markdown(
    manifest: MotionCohortManifest,
    full: CohortRunMeasurements,
    reuse: CohortRunMeasurements,
    resource_pilot: ResourcePilotResult,
    overall: Mapping[str, object],
) -> str:
    exclusions = cast(Sequence[Sequence[object]], overall["exclusion_reason_counts"])
    exclusion_total = sum(cast(int, item[1]) for item in exclusions)
    return "\n".join(
        (
            "# Phase 4 Frozen AV2 Motion Evaluation Cohort",
            "",
            "Phase 4 AV2 motion evaluation cohort frozen with 500 genuine provider scenarios.",
            "",
            f"- Cohort identity: `{manifest.cohort_identity}`",
            f"- Dataset version: `{manifest.dataset_version}`",
            f"- Official source: `{manifest.official_source}`",
            f"- Candidate scenarios (train/val): "
            f"{manifest.train_candidate_count}/{manifest.val_candidate_count}",
            "- Roles: 150 development, 50 pilot, 300 test",
            f"- Selected provider bytes: {manifest.selected_source_bytes}",
            f"- Source/included scenarios: "
            f"{overall['source_scenarios']}/{overall['included_scenarios']}",
            f"- Source/included trajectories: "
            f"{overall['source_trajectories']}/{overall['included_trajectories']}",
            f"- Source samples: {overall['source_samples']}",
            f"- Vector-map elements: {overall['vector_map_elements']}",
            f"- Exclusion records: {exclusion_total}",
            f"- First-run duration/cache/peak RSS: {full.total_seconds:.6f} s / "
            f"{full.cache_bytes} bytes / {full.peak_process_memory_bytes} bytes",
            f"- Reuse duration/reused entries: {reuse.total_seconds:.6f} s / "
            f"{reuse.reused_count}",
            f"- Probe projected versus actual duration: "
            f"{resource_pilot.projected_full_duration_seconds:.6f} s / "
            f"{resource_pilot.actual_full_duration_seconds:.6f} s",
            f"- Probe projected versus actual cache: "
            f"{resource_pilot.projected_full_cache_bytes} / "
            f"{resource_pilot.actual_full_cache_bytes} bytes",
            "",
            "Development may be used for implementation debugging and parameter-grid "
            "design. Pilot is reserved for final protocol confirmation. Test remains "
            "untouched until the frozen campaign. Cohort membership cannot change "
            "based on Phase 4 results.",
            "",
        )
    )


def _verify_evidence(evidence_root: Path) -> dict[str, object]:
    evidence = cast(
        dict[str, object],
        json.loads((evidence_root / "evidence.json").read_text(encoding="utf-8")),
    )
    checksums = cast(Mapping[str, object], evidence["evidence_file_sha256"])
    if set(checksums) != set(_EVIDENCE_NAMES):
        raise ArtifactError("cohort evidence checksum inventory differs")
    for name, expected in checksums.items():
        if _sha256_file(evidence_root / name) != expected:
            raise ArtifactError("cohort evidence file checksum differs")
    manifest_text = (evidence_root / "cohort_manifest.json").read_text(encoding="utf-8")
    manifest = motion_cohort_manifest_from_json(manifest_text)
    if (
        len(manifest.units) != 500
        or evidence.get("cohort_decision") != "frozen"
        or evidence.get("cohort_identity") != manifest.cohort_identity
    ):
        raise ArtifactError("cohort evidence decision or identity differs")
    return evidence


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = args.repository_root.resolve(strict=True)
    data_root = normalize_relative_path(args.data_root)
    cache_root = normalize_relative_path(args.cache_root)
    evidence_root = repository_root / normalize_relative_path(args.evidence_root)
    _require_ignored(repository_root, data_root / "provider-probe.parquet")
    _require_ignored(repository_root, cache_root / "cache-probe.parquet")
    phase3 = _verify_phase3_evidence(repository_root)
    project = load_config(repository_root / "configs/project.toml")
    config = MotionCohortConfig(root_seed=project.root_seed)
    hardware = _hardware(repository_root)
    disk_before = shutil.disk_usage(repository_root).free

    train = _catalog(
        "train",
        config=config,
        data_relative_root=data_root / "train",
        backend=args.backend,
    )
    val = _catalog(
        "val",
        config=config,
        data_relative_root=data_root / "val",
        backend=args.backend,
    )
    train_pairs, train_backend, train_backend_version, train_listing, train_id = train
    val_pairs, val_backend, val_backend_version, val_listing, val_id = val
    selection = select_motion_cohort(train_pairs, val_pairs, config=config)
    dataset_version = cohort_dataset_version(train_id, val_id)
    candidate_counts = {"train": len(train_pairs), "val": len(val_pairs)}
    catalog_data = {
        "train": (train_backend, train_backend_version, train_listing, train_id),
        "val": (val_backend, val_backend_version, val_listing, val_id),
    }
    role_source_roots = {
        CohortRole.DEVELOPMENT: data_root / "train" / dataset_version,
        CohortRole.PILOT: data_root / "train" / dataset_version,
        CohortRole.TEST: data_root / "val" / dataset_version,
    }
    role_local_roots = {
        CohortRole.DEVELOPMENT: data_root / "train",
        CohortRole.PILOT: data_root / "train",
        CohortRole.TEST: data_root / "val",
    }
    checkpoint_root = repository_root / cache_root / "acquisition"

    probe_reports: dict[CohortRole, av2_acquisition.Av2AcquisitionReport] = {}
    probe_plans: dict[CohortRole, pilot.Av2PilotPlan] = {}
    probe_acquisition_started = time.perf_counter()
    for role in CohortRole:
        units = _role_units(selection, role, _PROBE_COUNTS[role])
        partition = units[0].provider_partition
        backend_name, backend_version, listing_seconds, catalog_identity = catalog_data[
            partition
        ]
        acquisition_config, acquisition_plan = build_role_acquisition_plan(
            units,
            config=config,
            dataset_version=dataset_version,
            catalog_identity=catalog_identity,
            candidate_count=candidate_counts[partition],
            local_relative_root=role_local_roots[role],
            backend=backend_name,
        )
        report = _acquire(
            repository_root,
            checkpoint_root / f"probe_{role.value}.json",
            acquisition_config,
            acquisition_plan,
            backend_name=backend_name,
            backend_version=backend_version,
            listing_seconds=listing_seconds,
            prior_reports=tuple(probe_reports.values()),
        )
        probe_reports[role] = report
        probe_identity = canonical_sha256(
            "phase4-resource-probe-source",
            [
                {
                    "relative_path": item.relative_path.as_posix(),
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in report.acquired_files
            ],
        )
        probe_plans[role] = build_role_pilot_plan(
            units,
            report,
            config=config,
            dataset_version=dataset_version,
            candidate_count=candidate_counts[partition],
            source_relative_root=role_source_roots[role],
            source_manifest_identity=probe_identity,
        )
    probe_acquisition_seconds = time.perf_counter() - probe_acquisition_started
    probe_started = time.perf_counter()
    probe_executions = _execute_roles(
        repository_root,
        probe_plans,
        {role: repository_root / role_source_roots[role] for role in CohortRole},
        cache_root / "resource_probe",
    )
    probe_elapsed = probe_acquisition_seconds + (time.perf_counter() - probe_started)
    probe_totals = _execution_totals(probe_executions)
    probe_measurements = _run_measurements(
        "resource_probe",
        probe_totals,
        acquisition_seconds=probe_acquisition_seconds,
        elapsed_seconds=probe_elapsed,
        disk_before=disk_before,
        disk_after=shutil.disk_usage(repository_root).free,
    )

    acquisition_started = time.perf_counter()
    full_reports: dict[CohortRole, av2_acquisition.Av2AcquisitionReport] = {}
    acquisition_plans: dict[CohortRole, av2_acquisition.Av2AcquisitionPlan] = {}
    for role in CohortRole:
        units = _role_units(selection, role)
        partition = units[0].provider_partition
        backend_name, backend_version, listing_seconds, catalog_identity = catalog_data[
            partition
        ]
        acquisition_config, acquisition_plan = build_role_acquisition_plan(
            units,
            config=config,
            dataset_version=dataset_version,
            catalog_identity=catalog_identity,
            candidate_count=candidate_counts[partition],
            local_relative_root=role_local_roots[role],
            backend=backend_name,
        )
        acquisition_plans[role] = acquisition_plan
        full_reports[role] = _acquire(
            repository_root,
            checkpoint_root / f"full_{role.value}.json",
            acquisition_config,
            acquisition_plan,
            backend_name=backend_name,
            backend_version=backend_version,
            listing_seconds=listing_seconds,
            prior_reports=(
                *probe_reports.values(),
                *full_reports.values(),
            ),
        )
    acquisition_seconds = time.perf_counter() - acquisition_started

    train_manifest = _source_manifest(
        repository_root,
        role_source_roots[CohortRole.DEVELOPMENT],
        (
            full_reports[CohortRole.DEVELOPMENT],
            full_reports[CohortRole.PILOT],
        ),
        dataset_version=dataset_version,
        checkpoint=checkpoint_root / "train_source_manifest.json",
    )
    val_manifest = _source_manifest(
        repository_root,
        role_source_roots[CohortRole.TEST],
        (full_reports[CohortRole.TEST],),
        dataset_version=dataset_version,
        checkpoint=checkpoint_root / "val_source_manifest.json",
    )
    source_manifest_identities = {
        CohortRole.DEVELOPMENT: registry.dataset_source_manifest_identity(
            train_manifest
        ),
        CohortRole.PILOT: registry.dataset_source_manifest_identity(train_manifest),
        CohortRole.TEST: registry.dataset_source_manifest_identity(val_manifest),
    }
    full_plans = {
        role: build_role_pilot_plan(
            _role_units(selection, role),
            full_reports[role],
            config=config,
            dataset_version=dataset_version,
            candidate_count=candidate_counts[
                _role_units(selection, role)[0].provider_partition
            ],
            source_relative_root=role_source_roots[role],
            source_manifest_identity=source_manifest_identities[role],
        )
        for role in CohortRole
    }

    full_started = time.perf_counter()
    full_executions = _execute_roles(
        repository_root,
        full_plans,
        {role: repository_root / role_source_roots[role] for role in CohortRole},
        cache_root / "cohort",
    )
    full_elapsed = acquisition_seconds + (time.perf_counter() - full_started)
    full_totals = _execution_totals(full_executions)
    full_measurements = _run_measurements(
        "full_first_run",
        full_totals,
        acquisition_seconds=acquisition_seconds,
        elapsed_seconds=full_elapsed,
        disk_before=disk_before,
        disk_after=shutil.disk_usage(repository_root).free,
    )
    if cast(int, full_totals["scenario_count"]) != 500:
        raise ArtifactError("full materialization did not cover 500 scenarios")
    inventory = scan_materialization_cache(repository_root, cache_root / "cohort")
    if inventory.complete_entry_count != 500 or inventory.incomplete_entry_count != 0:
        raise ArtifactError("full cohort cache inventory is incomplete")
    before_reuse = _cache_snapshot(repository_root, full_executions)

    reuse_started = time.perf_counter()
    reuse_executions = _execute_roles(
        repository_root,
        full_plans,
        {role: repository_root / role_source_roots[role] for role in CohortRole},
        cache_root / "cohort",
    )
    reuse_elapsed = time.perf_counter() - reuse_started
    reuse_totals = _execution_totals(reuse_executions)
    reuse_measurements = _run_measurements(
        "full_reuse_run",
        reuse_totals,
        acquisition_seconds=0.0,
        elapsed_seconds=reuse_elapsed,
        disk_before=full_measurements.disk_free_after_bytes,
        disk_after=shutil.disk_usage(repository_root).free,
    )
    if (
        cast(int, reuse_totals["materialized_count"]) != 0
        or cast(int, reuse_totals["reused_count"]) != 500
        or any(
            result.disposition is not MaterializationDisposition.REUSED
            for execution in reuse_executions.values()
            for result in execution.materialization_report.results
        )
    ):
        raise ArtifactError("second full execution did not reuse all 500 entries")
    if _cache_snapshot(repository_root, reuse_executions) != before_reuse:
        raise ArtifactError("canonical cache output changed during reuse")

    summaries = cohort_validation_summaries(repository_root, reuse_executions)
    status = validation_status_by_source(repository_root, reuse_executions)
    cohort_units = _cohort_units(selection, full_reports, reuse_executions, status)
    manifest = build_cohort_manifest(
        cohort_units,
        config=config,
        dataset_version=dataset_version,
        train_candidate_count=len(train_pairs),
        val_candidate_count=len(val_pairs),
        train_source_manifest_identity=source_manifest_identities[
            CohortRole.DEVELOPMENT
        ],
        val_source_manifest_identity=source_manifest_identities[CohortRole.TEST],
    )
    overall = _validation_to_dict(summaries[-1])
    resource_pilot = ResourcePilotResult(
        selected_source_scenario_ids=tuple(
            item.remote_pair.source_scenario_id
            for role in CohortRole
            for item in _role_units(selection, role, _PROBE_COUNTS[role])
        ),
        development_count=10,
        pilot_count=5,
        test_count=10,
        measurements=probe_measurements,
        projected_full_duration_seconds=probe_measurements.total_seconds * 20.0,
        actual_full_duration_seconds=full_measurements.total_seconds,
        projected_full_cache_bytes=probe_measurements.cache_bytes * 20,
        actual_full_cache_bytes=full_measurements.cache_bytes,
    )

    acquisition_report = {
        "schema_version": "1.0",
        "dataset_version": dataset_version,
        "official_source": config.official_source,
        "train_candidate_count": len(train_pairs),
        "val_candidate_count": len(val_pairs),
        "selected_scenario_count": 500,
        "selected_file_count": 1_000,
        "selected_source_bytes": manifest.selected_source_bytes,
        "listing_seconds": {"train": train_listing, "val": val_listing},
        "roles": {
            role.value: {
                "plan_identity": av2_acquisition.av2_acquisition_plan_identity(
                    acquisition_plans[role]
                ),
                "backend": full_reports[role].backend,
                "backend_version": full_reports[role].backend_version,
                "downloaded_file_count": full_reports[role].downloaded_file_count,
                "reused_file_count": full_reports[role].reused_file_count,
                "downloaded_bytes": full_reports[role].downloaded_bytes,
                "reused_bytes": full_reports[role].reused_bytes,
                "download_seconds": full_reports[role].download_seconds,
            }
            for role in CohortRole
        },
        "source_pairs_verified": 500,
        "outcome_based_replacements": 0,
    }
    materialization_report = {
        "schema_version": "1.0",
        "measurements": _run_to_dict(full_measurements),
        "roles": _role_report(full_executions),
        "declared_output_count": 3_500,
        "complete_cache_entry_count": inventory.complete_entry_count,
        "incomplete_cache_entry_count": inventory.incomplete_entry_count,
    }
    reuse_report = {
        "schema_version": "1.0",
        "measurements": _run_to_dict(reuse_measurements),
        "roles": _role_report(reuse_executions),
        "reused_entry_count": 500,
        "conversion_worker_run_count": 0,
        "canonical_output_checksum_changes": 0,
    }
    validation_report = {
        "schema_version": "1.0",
        "summaries": [_validation_to_dict(item) for item in summaries],
        "role_validation_report_identities": {
            role.value: reuse_executions[role].report.validation_report_identity
            for role in CohortRole
        },
        "complete_validation": True,
        "membership_changed_by_validation": False,
    }
    statistics = {
        "schema_version": "1.0",
        "cohort_identity": manifest.cohort_identity,
        "summaries": [_validation_to_dict(item) for item in summaries],
    }
    summary = _summary_markdown(
        manifest,
        full_measurements,
        reuse_measurements,
        resource_pilot,
        overall,
    )

    payloads = {
        "cohort_manifest.json": motion_cohort_manifest_to_canonical_json(manifest),
        "train_source_manifest.json": (
            registry.dataset_source_manifest_to_canonical_json(train_manifest)
        ),
        "val_source_manifest.json": (
            registry.dataset_source_manifest_to_canonical_json(val_manifest)
        ),
        "acquisition_report.json": canonical_json_text(
            acquisition_report, trailing_newline=True
        ),
        "materialization_report.json": canonical_json_text(
            materialization_report, trailing_newline=True
        ),
        "reuse_report.json": canonical_json_text(reuse_report, trailing_newline=True),
        "validation_report.json": canonical_json_text(
            validation_report, trailing_newline=True
        ),
        "resource_pilot.json": resource_pilot_result_to_canonical_json(resource_pilot),
        "cohort_statistics.json": canonical_json_text(
            statistics, trailing_newline=True
        ),
        "summary.md": summary,
    }
    evidence_root.mkdir(parents=True, exist_ok=True)
    for name, text in payloads.items():
        _atomic_write(evidence_root / name, text)
    checksums = {name: _sha256_file(evidence_root / name) for name in _EVIDENCE_NAMES}
    evidence = {
        "schema_version": "1.0",
        "batch": "4.1",
        "dataset_id": "av2_motion",
        "dataset_version": dataset_version,
        "official_source": config.official_source,
        "provider_candidate_counts": candidate_counts,
        "cohort_role_counts": {role.value: _ROLE_COUNTS[role] for role in CohortRole},
        "cohort_identity": manifest.cohort_identity,
        "selected_source_bytes": manifest.selected_source_bytes,
        "selection_policy": config.selection_policy,
        "root_seed": config.root_seed,
        "phase3_verification": phase3,
        "resource_pilot": {
            "measurements": _run_to_dict(probe_measurements),
            "projected_full_duration_seconds": (
                resource_pilot.projected_full_duration_seconds
            ),
            "projected_full_cache_bytes": (resource_pilot.projected_full_cache_bytes),
        },
        "full_materialization": _run_to_dict(full_measurements),
        "full_reuse": _run_to_dict(reuse_measurements),
        "overall_validation": overall,
        "hardware": hardware,
        "evidence_file_sha256": checksums,
        "development_policy": ("implementation debugging and parameter-grid design"),
        "pilot_policy": "final protocol confirmation",
        "test_policy": "untouched until the frozen campaign",
        "membership_policy": "cannot change based on Phase 4 results",
        "provider_or_cache_data_tracked": False,
        "cohort_decision": "frozen",
        "pass_statement": (
            "Phase 4 AV2 motion evaluation cohort frozen with 500 genuine "
            "provider scenarios."
        ),
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
    except (ArtifactError, SchemaError, ValidationError) as error:
        sys.stderr.write(f"error: {error}\n")
        raise SystemExit(2) from None
