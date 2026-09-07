"""Repository-wide integration tests for the completed Phase 1 foundation."""

import hashlib
from importlib import import_module, metadata
import json
from pathlib import Path
import pkgutil
import re
import shutil
import subprocess
import sys

import pytest

import kinematicweave
from kinematicweave.artifact_store import (
    RunDirectoryState,
    inspect_run_directory,
    list_partial_artifacts,
)
from kinematicweave.cli import build_parser
from kinematicweave.cli import main as cli_main
from kinematicweave.config import config_to_canonical_json, load_config
from kinematicweave.doctor import DoctorCheckStatus, run_doctor
from kinematicweave.errors import ArtifactError
from kinematicweave.experiments import smoke as smoke_module
from kinematicweave.experiments.smoke import run_foundation_smoke
from kinematicweave.manifests import (
    RunStatus,
    artifact_manifest_from_json,
    artifact_manifest_to_canonical_json,
    experiment_manifest_from_json,
    experiment_manifest_to_canonical_json,
)
from kinematicweave.system_metadata import (
    CpuMetadata,
    DiskMetadata,
    GitMetadata,
    MemoryMetadata,
    OperatingSystemMetadata,
    PythonMetadata,
    SystemMetadata,
    WslMetadata,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPLETION_REPORT = PROJECT_ROOT / "reports" / "phase1_completion_report.md"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "quality.yml"
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
ABSOLUTE_LOCAL_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|/(?:home|Users|tmp)/)")


def _file_snapshot(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(
            (candidate for candidate in root.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        )
    )


def _sample_metadata() -> SystemMetadata:
    return SystemMetadata(
        schema_version="1.0",
        captured_at_utc="2026-01-02T03:04:05.006789Z",
        package_version="0.1.0a0",
        operating_system=OperatingSystemMetadata(
            system="Test OS",
            release="1",
            version="1.0",
            machine="test-machine",
        ),
        python=PythonMetadata(
            version="3.12.13",
            implementation="CPython",
            executable="python",
        ),
        git=GitMetadata(available=True, commit="a" * 40, dirty=False),
        cpu=CpuMetadata(processor="Test CPU", logical_cpu_count=2),
        memory=MemoryMetadata(total_bytes=1024**3),
        gpus=(),
        cuda_version=None,
        disk=DiskMetadata(
            total_bytes=10 * 1024**3,
            used_bytes=2 * 1024**3,
            free_bytes=8 * 1024**3,
        ),
        wsl=WslMetadata(is_wsl=False, distribution=None, version=None),
        environment_lock_id="b" * 64,
    )


def _temporary_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "configs").mkdir(parents=True)
    shutil.copy2(
        PROJECT_ROOT / "configs" / "project.toml",
        repository / "configs" / "project.toml",
    )
    shutil.copy2(PROJECT_ROOT / "uv.lock", repository / "uv.lock")
    return repository


def _git_status() -> str:
    completed = subprocess.run(
        (
            "git",
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            "status",
            "--porcelain=v1",
        ),
        cwd=PROJECT_ROOT,
        check=True,
        shell=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_required_phase1_repository_structure() -> None:
    for directory in (
        "configs",
        "data",
        "definitions",
        "docs",
        "experiments",
        "figures",
        "qualitative",
        "reports",
        "results",
        "scripts",
        "src/kinematicweave",
        "tests",
    ):
        assert (PROJECT_ROOT / directory).is_dir()

    for file_name in (
        "README.md",
        "LICENSE",
        "pyproject.toml",
        "uv.lock",
        "configs/project.toml",
        "definitions/design_freeze_report.md",
        ".github/workflows/quality.yml",
        "docs/foundation_setup.md",
        "docs/asus_runbook.md",
        "docs/troubleshooting.md",
        "reports/phase1_completion_report.md",
        "scripts/quality.py",
        "scripts/ci.py",
        "scripts/run_foundation_smoke.py",
    ):
        assert (PROJECT_ROOT / file_name).is_file()
    assert not (PROJECT_ROOT / "reports" / ".gitkeep").exists()


def test_package_metadata_and_configuration_are_deterministic() -> None:
    assert kinematicweave.__version__ == "0.1.0a0"
    config = load_config(PROJECT_ROOT / "configs" / "project.toml")
    first = config_to_canonical_json(config)
    second = config_to_canonical_json(config)
    assert first == second
    assert json.loads(first)["project_name"] == "KinematicWeave"

    entry_points = metadata.entry_points(group="console_scripts")
    assert any(
        entry_point.name == "kinematicweave"
        and entry_point.value == "kinematicweave.cli:main"
        for entry_point in entry_points
    )


def test_cli_exposes_only_implemented_commands() -> None:
    parser = build_parser()
    choices: set[str] = set()
    for action in parser._actions:
        action_choices = getattr(action, "choices", None)
        if isinstance(action_choices, dict):
            choices.update(str(choice) for choice in action_choices)
    assert choices == {"artifact", "config", "data", "doctor"}
    for scientific_command in (
        "codec",
        "experiment",
        "layout",
        "report",
        "runtime",
        "visualize",
    ):
        assert scientific_command not in choices


def test_cli_version_and_configuration_validation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as version_exit:
        cli_main(["--version"])
    assert version_exit.value.code == 0
    assert capsys.readouterr().out == "kinematicweave 0.1.0a0\n"

    assert cli_main(["config", "validate", "--repo-root", str(PROJECT_ROOT)]) == 0
    assert capsys.readouterr().out == (
        "Configuration valid: KinematicWeave (schema 1.0)\n"
    )


def test_doctor_contains_all_required_checks() -> None:
    report = run_doctor(PROJECT_ROOT)
    assert {check.check_id for check in report.checks} == {
        "definitions",
        "data_foundation",
        "disk_space",
        "git",
        "gpu",
        "lockfile",
        "package",
        "project_configuration",
        "python",
        "repository",
        "uv",
        "writable_directories",
        "wsl",
    }
    assert report.overall_status in {
        DoctorCheckStatus.PASS,
        DoctorCheckStatus.WARNING,
    }


def test_isolated_foundation_pipeline_and_repository_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_results_before = _file_snapshot(PROJECT_ROOT / "results")
    definitions_before = _file_snapshot(PROJECT_ROOT / "definitions")
    repository = _temporary_repository(tmp_path)
    monkeypatch.setattr(
        smoke_module,
        "capture_system_metadata",
        lambda repository_root: _sample_metadata(),
    )

    result = run_foundation_smoke(
        repository,
        run_id="run:phase1:integration",
    )
    assert result.diagnostic_value == 30
    assert (
        inspect_run_directory(repository, "results", result.run_id)
        is RunDirectoryState.COMPLETE
    )
    assert list_partial_artifacts(result.run_directory) == ()

    expected_lifecycle = (
        ("experiment.planned.json", RunStatus.PLANNED),
        ("experiment.running.json", RunStatus.RUNNING),
        ("experiment.complete.json", RunStatus.COMPLETE),
    )
    for file_name, expected_status in expected_lifecycle:
        text = (result.run_directory.manifests_path / file_name).read_text(
            encoding="utf-8"
        )
        experiment_manifest = experiment_manifest_from_json(text)
        assert experiment_manifest.status is expected_status
        assert (
            experiment_manifest_from_json(
                experiment_manifest_to_canonical_json(experiment_manifest)
            )
            == experiment_manifest
        )

    artifact_manifests = tuple(
        sorted(result.run_directory.manifests_path.glob("artifact.*.json"))
    )
    assert len(artifact_manifests) == 3
    for manifest_path in artifact_manifests:
        artifact_manifest = artifact_manifest_from_json(
            manifest_path.read_text(encoding="utf-8")
        )
        assert (
            artifact_manifest_from_json(
                artifact_manifest_to_canonical_json(artifact_manifest)
            )
            == artifact_manifest
        )
        artifact_path = repository / artifact_manifest.path
        data = artifact_path.read_bytes()
        assert artifact_path.is_file()
        assert len(data) == artifact_manifest.size_bytes
        assert hashlib.sha256(data).hexdigest() == artifact_manifest.content_checksum

    with pytest.raises(ArtifactError, match="immutable"):
        run_foundation_smoke(
            repository,
            run_id="run:phase1:integration",
        )

    assert _file_snapshot(PROJECT_ROOT / "results") == real_results_before
    assert _file_snapshot(PROJECT_ROOT / "definitions") == definitions_before


def test_automation_and_hosted_workflow_remain_foundation_only() -> None:
    assert (PROJECT_ROOT / "scripts" / "quality.py").is_file()
    assert (PROJECT_ROOT / "scripts" / "ci.py").is_file()
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    for required in (
        "push:",
        "pull_request:",
        "workflow_dispatch:",
        "contents: read",
        "runs-on: ${{ matrix.os }}",
        "timeout-minutes: 30",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b",
        'version: "0.11.31"',
        "uv python install ${{ matrix.python-version }}",
        "uv sync --frozen --python ${{ matrix.python-version }}",
        "uv run --frozen python scripts/ci.py",
        "git diff --exit-code",
        "git diff --cached --exit-code",
    ):
        assert required in workflow
    for forbidden in (
        "pull_request_target",
        "secrets.",
        "upload-artifact",
        "dataset",
        "gpu",
        "publish",
        "deploy",
    ):
        assert forbidden not in workflow.casefold()


def test_readme_records_m1_and_phase2_completion() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# KinematicWeave\n")
    assert "## Quick start" in readme
    assert "## Reproducing the evidence" in readme
    assert "docs/reproducibility.md" in readme
    assert "DATA_LICENSE.md" in readme


def test_completion_report_records_all_batches_and_acceptance_items() -> None:
    report = COMPLETION_REPORT.read_text(encoding="utf-8")
    for batch_number in range(1, 16):
        assert f"Batch 1.{batch_number} —" in report
    for acceptance_item in (
        "Install from the committed lockfile",
        "Run all static and automated checks",
        "Execute the synthetic foundation smoke pipeline",
        "Produce deterministic configuration output",
        "Capture system, Git, package, GPU, disk, and lockfile metadata",
        "Create valid experiment and artifact manifests",
        "Write and finalize artifacts atomically",
        "Inspect the environment through `kinematicweave doctor`",
        "Execute local CI without changing tracked files",
        "Run without requiring a GPU, WSL, datasets, or network access",
    ):
        assert acceptance_item in report


def test_completion_report_is_safe_and_scoped() -> None:
    report = COMPLETION_REPORT.read_text(encoding="utf-8")
    assert ABSOLUTE_LOCAL_PATH.search(report) is None
    assert "example-user" not in report.casefold()
    assert "kinematicweave-phase1-clone-" not in report
    for placeholder in ("todo", "fixme", "coming soon"):
        assert placeholder not in report.casefold()
    for limitation in (
        "scientific datasets are not implemented",
        "trajectory codecs are not implemented",
        "layout inference is not implemented",
        "deterministic runtime and editing are not implemented",
        "the viewer is not implemented",
    ):
        assert limitation in report.casefold()
    assert "scientific methods are implemented" not in report.casefold()


def test_relative_markdown_links_resolve() -> None:
    markdown_files = (
        PROJECT_ROOT / "README.md",
        *tuple((PROJECT_ROOT / "docs").glob("*.md")),
        COMPLETION_REPORT,
    )
    for markdown_file in markdown_files:
        text = markdown_file.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative_target = target.split("#", maxsplit=1)[0]
            assert (markdown_file.parent / relative_target).resolve().is_file()


def test_real_results_contain_only_approved_provider_evidence() -> None:
    paths = {
        item[0]
        for item in _file_snapshot(PROJECT_ROOT / "results")
        if not item[0].startswith("generated/")
    }
    assert paths == {
        ".gitkeep",
        "phase2/av2_provider_pilot/acquisition_plan.json",
        "phase2/av2_provider_pilot/acquisition_report.json",
        "phase2/av2_provider_pilot/evidence.json",
        "phase2/av2_provider_pilot/pilot_plan.json",
        "phase2/av2_provider_pilot/pilot_report_first_run.json",
        "phase2/av2_provider_pilot/pilot_report_reuse_run.json",
        "phase2/av2_provider_pilot/source_manifest.json",
        "phase2/av2_provider_pilot/summary.md",
        "phase2/av2_provider_pilot/validation_report.json",
        "phase3/exact_codec_baseline/av2_provider_evidence.json",
        "phase3/exact_codec_baseline/evidence.json",
        "phase3/exact_codec_baseline/schema_snapshot.json",
        "phase3/exact_codec_baseline/summary.md",
        "phase3/exact_codec_baseline/synthetic_evidence.json",
        "phase3/piecewise_linear_codec/av2_provider_evidence.json",
        "phase3/piecewise_linear_codec/evidence.json",
        "phase3/piecewise_linear_codec/summary.md",
        "phase3/piecewise_linear_codec/synthetic_evidence.json",
        "phase3/hermite_codec/av2_provider_evidence.json",
        "phase3/hermite_codec/error_analysis.json",
        "phase3/hermite_codec/evidence.json",
        "phase3/hermite_codec/summary.md",
        "phase3/hermite_codec/synthetic_evidence.json",
        "phase3/velocity_bounded_codec/av2_provider_evidence.json",
        "phase3/velocity_bounded_codec/error_analysis.json",
        "phase3/velocity_bounded_codec/evidence.json",
        "phase3/velocity_bounded_codec/performance_analysis.json",
        "phase3/velocity_bounded_codec/summary.md",
        "phase3/velocity_bounded_codec/synthetic_evidence.json",
        "phase3/semantic_motion/av2_provider_evidence.json",
        "phase3/semantic_motion/error_analysis.json",
        "phase3/semantic_motion/evidence.json",
        "phase3/semantic_motion/preservation_analysis.json",
        "phase3/semantic_motion/schema_snapshot.json",
        "phase3/semantic_motion/summary.md",
        "phase3/semantic_motion/synthetic_evidence.json",
        "phase3/shared_motion_templates/av2_provider_evidence.json",
        "phase3/shared_motion_templates/error_analysis.json",
        "phase3/shared_motion_templates/evidence.json",
        "phase3/shared_motion_templates/map_evaluation.json",
        "phase3/shared_motion_templates/schema_snapshot.json",
        "phase3/shared_motion_templates/summary.md",
        "phase3/shared_motion_templates/synthetic_evidence.json",
        "phase3/milestone/av2_provider_integration.json",
        "phase3/milestone/contract_snapshot.json",
        "phase3/milestone/evidence.json",
        "phase3/milestone/phase3_results.json",
        "phase3/milestone/summary.md",
        "phase3/milestone/synthetic_integration.json",
        "phase4/motion_cohort/acquisition_report.json",
        "phase4/motion_cohort/cohort_manifest.json",
        "phase4/motion_cohort/cohort_statistics.json",
        "phase4/motion_cohort/evidence.json",
        "phase4/motion_cohort/materialization_report.json",
        "phase4/motion_cohort/resource_pilot.json",
        "phase4/motion_cohort/reuse_report.json",
        "phase4/motion_cohort/summary.md",
        "phase4/motion_cohort/train_source_manifest.json",
        "phase4/motion_cohort/val_source_manifest.json",
        "phase4/motion_cohort/validation_report.json",
        "phase4/motion_baselines/baseline_contract.json",
        "phase4/motion_baselines/development_results.json",
        "phase4/motion_baselines/evidence.json",
        "phase4/motion_baselines/failure_report.json",
        "phase4/motion_baselines/performance_report.json",
        "phase4/motion_baselines/summary.md",
        "phase4/motion_metrics/audit_report.json",
        "phase4/motion_metrics/development_metric_summary.json",
        "phase4/motion_metrics/event_preservation_summary.json",
        "phase4/motion_metrics/evidence.json",
        "phase4/motion_metrics/failure_report.json",
        "phase4/motion_metrics/metrics_contract.json",
        "phase4/motion_metrics/performance_report.json",
        "phase4/motion_metrics/summary.md",
        "phase4/motion_sweep/evidence.json",
        "phase4/motion_sweep/exploratory_results.json",
        "phase4/motion_sweep/failure_report.json",
        "phase4/motion_sweep/matched_byte_results.json",
        "phase4/motion_sweep/matched_keyframe_results.json",
        "phase4/motion_sweep/performance_report.json",
        "phase4/motion_sweep/summary.md",
        "phase4/motion_sweep/sweep_contract.json",
        "phase4/protocol_freeze/development_audit.json",
        "phase4/protocol_freeze/evidence.json",
        "phase4/protocol_freeze/final_budget_contract.json",
        "phase4/protocol_freeze/final_campaign_matrix.json",
        "phase4/protocol_freeze/resource_projection.json",
        "phase4/protocol_freeze/summary.md",
        "phase4/protocol_freeze/supplemental_grid.json",
        "phase4/protocol_freeze/supplemental_results.json",
        "phase4/frozen_campaign/campaign_contract.json",
        "phase4/frozen_campaign/determinism_report.json",
        "phase4/frozen_campaign/evidence.json",
        "phase4/frozen_campaign/failure_report.json",
        "phase4/frozen_campaign/matched_byte_results.json",
        "phase4/frozen_campaign/matched_keyframe_results.json",
        "phase4/frozen_campaign/performance_report.json",
        "phase4/frozen_campaign/pilot_completion.json",
        "phase4/frozen_campaign/pilot_results.json",
        "phase4/frozen_campaign/summary.md",
        "phase4/frozen_campaign/test_results.json",
        "phase4/statistical_analysis/diagnostic_comparisons.json",
        "phase4/statistical_analysis/event_type_analysis.json",
        "phase4/statistical_analysis/evidence.json",
        "phase4/statistical_analysis/failure_report.json",
        "phase4/statistical_analysis/multiplicity_report.json",
        "phase4/statistical_analysis/pilot_test_consistency.json",
        "phase4/statistical_analysis/primary_byte_comparisons.json",
        "phase4/statistical_analysis/primary_keyframe_comparisons.json",
        "phase4/statistical_analysis/robustness_analysis.json",
        "phase4/statistical_analysis/scenario_aggregates.json",
        "phase4/statistical_analysis/statistical_contract.json",
        "phase4/statistical_analysis/summary.md",
        "phase4/ablation_analysis/ablation_contract.json",
        "phase4/ablation_analysis/adaptive_segmentation_ablation.json",
        "phase4/ablation_analysis/bounded_method_tradeoff.json",
        "phase4/ablation_analysis/contribution_attribution.json",
        "phase4/ablation_analysis/evidence.json",
        "phase4/ablation_analysis/failure_report.json",
        "phase4/ablation_analysis/interpolation_ablation.json",
        "phase4/ablation_analysis/primitive_vocabulary_ablation.json",
        "phase4/ablation_analysis/robustness_analysis.json",
        "phase4/ablation_analysis/serialization_overhead.json",
        "phase4/ablation_analysis/summary.md",
        "phase4/ablation_analysis/temporal_vs_geometric_ablation.json",
        "phase4/ablation_analysis/velocity_constraint_ablation.json",
        "phase4/qualitative_motion/evidence.json",
        "phase4/qualitative_motion/failure_analysis.json",
        "phase4/qualitative_motion/figure_manifest.json",
        "phase4/qualitative_motion/figures/01_matched_budget_overview.png",
        "phase4/qualitative_motion/figures/01_matched_budget_overview.svg",
        "phase4/qualitative_motion/figures/02_error_storage_tradeoff.png",
        "phase4/qualitative_motion/figures/02_error_storage_tradeoff.svg",
        "phase4/qualitative_motion/figures/03_adaptive_breakpoint_example.png",
        "phase4/qualitative_motion/figures/03_adaptive_breakpoint_example.svg",
        "phase4/qualitative_motion/figures/04_temporal_vs_geometric.png",
        "phase4/qualitative_motion/figures/04_temporal_vs_geometric.svg",
        "phase4/qualitative_motion/figures/05_hermite_dynamics_failure.png",
        "phase4/qualitative_motion/figures/05_hermite_dynamics_failure.svg",
        "phase4/qualitative_motion/figures/06_velocity_constraint_recovery.png",
        "phase4/qualitative_motion/figures/06_velocity_constraint_recovery.svg",
        "phase4/qualitative_motion/figures/07_semantic_event_preservation.png",
        "phase4/qualitative_motion/figures/07_semantic_event_preservation.svg",
        "phase4/qualitative_motion/figures/08_failure_exception_gallery.png",
        "phase4/qualitative_motion/figures/08_failure_exception_gallery.svg",
        "phase4/qualitative_motion/figures/replay_preview.png",
        "phase4/qualitative_motion/qualitative_motion.mp4",
        "phase4/qualitative_motion/replay_sequence_manifest.json",
        "phase4/qualitative_motion/selected_examples.json",
        "phase4/qualitative_motion/selection_contract.json",
        "phase4/qualitative_motion/summary.md",
        "phase4/milestone/claim_evidence_matrix.json",
        "phase4/milestone/contract_snapshot.json",
        "phase4/milestone/evidence.json",
        "phase4/milestone/final_results.json",
        "phase4/milestone/benchmark_figure_manifest.json",
        "phase4/milestone/benchmark_tables.json",
        "phase4/milestone/reproduction_manifest.json",
        "phase4/milestone/summary.md",
        "phase5/layout_feasibility/aggregation_analysis.json",
        "phase5/layout_feasibility/av2_motion_results.json",
        "phase5/layout_feasibility/claim_feasibility_matrix.json",
        "phase5/layout_feasibility/evidence.json",
        "phase5/layout_feasibility/failure_report.json",
        "phase5/layout_feasibility/feasibility_contract.json",
        "phase5/layout_feasibility/post_freeze_map_diagnostics.json",
        "phase5/layout_feasibility/summary.md",
        "phase5/layout_feasibility/synthetic_results.json",
        "phase5/motion_support/artifact_manifest.json",
        "phase5/motion_support/av2_results.json",
        "phase5/motion_support/construction_contract.json",
        "phase5/motion_support/evidence.json",
        "phase5/motion_support/failure_report.json",
        "phase5/motion_support/reproduction.md",
        "phase5/motion_support/resource_measurements.json",
        "phase5/motion_support/summary.md",
        "phase5/motion_support/synthetic_results.json",
        "benchmark_figures/figure1_procedural_overview/evidence.json",
        "benchmark_figures/figure1_procedural_overview/"
        "candidate_03_plotted_values.json",
        "benchmark_figures/figure1_procedural_overview/final_candidate_comparison.json",
        "benchmark_figures/figure1_procedural_overview/figure_manifest.json",
        "benchmark_figures/figure1_procedural_overview/plotted_values.json",
        "benchmark_figures/figure1_procedural_overview/selected_candidates.json",
        "benchmark_figures/figure1_procedural_overview/selection_contract.json",
        "benchmark_figures/figure1_procedural_overview/summary.md",
        "benchmark_figures/figure1_procedural_overview_compact/evidence.json",
        "benchmark_figures/figure1_procedural_overview_compact/figure_manifest.json",
        "benchmark_figures/figure1_procedural_overview_compact/layout_recommendation.json",
        "benchmark_figures/figure1_procedural_overview_compact/plotted_values.json",
        "benchmark_figures/figure1_procedural_overview_compact/summary.md",
        "benchmark_figures/figure2_rate_distortion/evidence.json",
        "benchmark_figures/figure2_rate_distortion/figure_contract.json",
        "benchmark_figures/figure2_rate_distortion/figure_manifest.json",
        "benchmark_figures/figure2_rate_distortion/plotted_values.json",
        "benchmark_figures/figure2_rate_distortion/primary_table_values.json",
        "benchmark_figures/figure2_rate_distortion/principal_contrasts.json",
        "benchmark_figures/figure2_rate_distortion/summary.md",
        "benchmark_figures/figure2_rate_distortion_compact/evidence.json",
        "benchmark_figures/figure2_rate_distortion_compact/figure_manifest.json",
        "benchmark_figures/figure2_rate_distortion_compact/layout_recommendation.json",
        "benchmark_figures/figure2_rate_distortion_compact/plotted_values.json",
        "benchmark_figures/figure2_rate_distortion_compact/summary.md",
        "benchmark_figures/figure2_rate_distortion_compact/table_values.json",
        "showcase_media/showcase_media_manifest.json",
        "showcase_media/summary.md",
    }
    assert not (PROJECT_ROOT / "results" / "runs").exists()


def test_importing_project_modules_does_not_change_repository_state() -> None:
    status_before = _git_status()
    results_before = _file_snapshot(PROJECT_ROOT / "results")
    definitions_before = _file_snapshot(PROJECT_ROOT / "definitions")
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        for module in pkgutil.walk_packages(
            kinematicweave.__path__,
            prefix=f"{kinematicweave.__name__}.",
        ):
            import_module(module.name)
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
    assert _git_status() == status_before
    assert _file_snapshot(PROJECT_ROOT / "results") == results_before
    assert _file_snapshot(PROJECT_ROOT / "definitions") == definitions_before
