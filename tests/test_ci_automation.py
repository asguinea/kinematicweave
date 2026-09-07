"""Tests for local automation and the hosted quality workflow."""

import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Any, NoReturn, cast

import pytest

from kinematicweave.errors import ArtifactError
from kinematicweave.experiments import smoke as smoke_module
from kinematicweave.experiments.smoke import FoundationSmokeResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "quality.yml"
CI_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "ci.py"


def _load_ci_script(
    module_name: str = "_ci_automation_under_test",
) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        module_name,
        CI_SCRIPT_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    finally:
        del sys.modules[specification.name]
    return module


ci_module = cast(Any, _load_ci_script())


def _snapshot_files(root: Path) -> tuple[tuple[str, int, str], ...]:
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


def test_public_exports_are_exact() -> None:
    assert ci_module.__all__ == ["main"]


def test_module_import_performs_no_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("ci module import attempted execution")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", fail)
    monkeypatch.setattr(smoke_module, "run_foundation_smoke", fail)
    _load_ci_script("_ci_automation_import_probe")


def test_main_runs_quality_before_smoke_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []

    def quality(repository_root: Path) -> int:
        assert repository_root == PROJECT_ROOT
        events.append("quality")
        return 0

    def smoke(repository_root: Path) -> None:
        assert repository_root == PROJECT_ROOT
        events.append("smoke")

    monkeypatch.setattr(ci_module, "_run_quality", quality)
    monkeypatch.setattr(ci_module, "_run_isolated_smoke", smoke)

    assert ci_module.main() == 0
    assert events == ["quality", "smoke"]
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.splitlines() == [
        "[ci] complete quality gate",
        "[ci] isolated foundation smoke",
        "[ci] all stages passed",
    ]


def test_quality_failure_stops_smoke_and_propagates_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke_called = False

    def smoke(repository_root: Path) -> None:
        nonlocal smoke_called
        smoke_called = True

    monkeypatch.setattr(ci_module, "_run_quality", lambda repository_root: 19)
    monkeypatch.setattr(ci_module, "_run_isolated_smoke", smoke)

    assert ci_module.main() == 19
    assert not smoke_called
    output = capsys.readouterr()
    assert output.out == "[ci] complete quality gate\n"
    assert output.err == "[ci] quality gate failed with status 19\n"


@pytest.mark.parametrize(
    "error",
    [
        ArtifactError("expected project failure"),
        OSError("expected operating-system failure"),
    ],
)
def test_expected_smoke_errors_return_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    def fail(repository_root: Path) -> NoReturn:
        raise error

    monkeypatch.setattr(ci_module, "_run_quality", lambda repository_root: 0)
    monkeypatch.setattr(ci_module, "_run_isolated_smoke", fail)

    assert ci_module.main() == 2
    output = capsys.readouterr()
    assert output.out.endswith("[ci] isolated foundation smoke\n")
    assert output.err.startswith("[ci] isolated smoke failed: expected")


def test_smoke_subprocess_failure_propagates_originating_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(repository_root: Path) -> NoReturn:
        raise ci_module._SubprocessFailure(("git", "commit"), 23)

    monkeypatch.setattr(ci_module, "_run_quality", lambda repository_root: 0)
    monkeypatch.setattr(ci_module, "_run_isolated_smoke", fail)

    assert ci_module.main() == 23
    assert "git exited with status 23" in capsys.readouterr().err


def test_unexpected_smoke_errors_are_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(repository_root: Path) -> NoReturn:
        raise RuntimeError("unexpected programming error")

    monkeypatch.setattr(ci_module, "_run_quality", lambda repository_root: 0)
    monkeypatch.setattr(ci_module, "_run_isolated_smoke", fail)

    with pytest.raises(RuntimeError, match="unexpected programming error"):
        ci_module.main()


def test_quality_uses_active_python_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []

    def run(command: tuple[str, ...], *, cwd: Path) -> int:
        calls.append((command, cwd))
        return 0

    monkeypatch.setattr(ci_module, "_run_subprocess", run)

    assert ci_module._run_quality(PROJECT_ROOT) == 0
    assert calls == [
        (
            (
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "quality.py"),
            ),
            PROJECT_ROOT,
        )
    ]


def test_subprocess_execution_explicitly_disables_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(
        command: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        shell: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        observed.update(
            command=command,
            cwd=cwd,
            check=check,
            shell=shell,
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)

    assert ci_module._run_subprocess(("git", "--version"), cwd=tmp_path) == 0
    assert observed == {
        "command": ("git", "--version"),
        "cwd": tmp_path,
        "check": False,
        "shell": False,
    }


def test_copied_smoke_input_set_is_minimal(tmp_path: Path) -> None:
    ci_module._copy_smoke_inputs(PROJECT_ROOT, tmp_path)
    assert tuple(
        sorted(
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    ) == ("configs/project.toml", "uv.lock")


def test_real_isolated_smoke_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable for the isolated CI integration test")

    results_before = _snapshot_files(PROJECT_ROOT / "results")
    temporary_paths: list[Path] = []
    smoke_calls = 0
    real_temporary_directory = tempfile.TemporaryDirectory
    real_run_foundation_smoke = smoke_module.run_foundation_smoke

    def tracking_temporary_directory(
        *,
        prefix: str,
    ) -> tempfile.TemporaryDirectory[str]:
        temporary = real_temporary_directory(prefix=prefix)
        temporary_paths.append(Path(temporary.name))
        return temporary

    def observing_run_foundation_smoke(
        repository_root: Path | None = None,
        *,
        run_id: str,
        config_path: str | Path = "configs/project.toml",
        results_root: str | Path | None = None,
    ) -> FoundationSmokeResult:
        nonlocal smoke_calls
        smoke_calls += 1
        assert repository_root is not None
        if smoke_calls == 1:
            assert (repository_root / ".git").is_dir()
            copied_files = tuple(
                sorted(
                    path.relative_to(repository_root).as_posix()
                    for path in repository_root.rglob("*")
                    if path.is_file() and ".git" not in path.parts
                )
            )
            assert copied_files == ("configs/project.toml", "uv.lock")
            commit_count = subprocess.run(
                ("git", "rev-list", "--count", "HEAD"),
                cwd=repository_root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            assert commit_count.returncode == 0
            assert commit_count.stdout.strip() == "1"
            for key, expected in (
                ("user.name", "KinematicWeave CI"),
                ("user.email", "ci@example.invalid"),
                ("core.autocrlf", "false"),
            ):
                configured = subprocess.run(
                    ("git", "config", "--local", "--get", key),
                    cwd=repository_root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                assert configured.returncode == 0
                assert configured.stdout.strip() == expected
        return real_run_foundation_smoke(
            repository_root,
            run_id=run_id,
            config_path=config_path,
            results_root=results_root,
        )

    monkeypatch.setattr(
        ci_module,
        "TemporaryDirectory",
        tracking_temporary_directory,
    )
    monkeypatch.setattr(
        ci_module,
        "run_foundation_smoke",
        observing_run_foundation_smoke,
    )

    ci_module._run_isolated_smoke(PROJECT_ROOT)

    assert smoke_calls == 2
    assert temporary_paths
    assert all(not path.exists() for path in temporary_paths)
    assert _snapshot_files(PROJECT_ROOT / "results") == results_before


def test_workflow_has_required_security_and_commands() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "on:\n  push:\n  pull_request:\n  workflow_dispatch:" in workflow
    assert "pull_request_target" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert workflow.count("runs-on:") == 1
    assert "runs-on: ${{ matrix.os }}" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "matrix:\n        include:" in workflow
    assert "os: ubuntu-latest" in workflow
    assert "os: windows-latest" in workflow
    assert 'python-version: "3.12"' in workflow
    assert 'python-version: "3.13"' in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in workflow
    assert 'version: "0.11.31"' in workflow
    assert "cache-dependency-glob: uv.lock" in workflow
    assert "uv python install ${{ matrix.python-version }}" in workflow
    assert "uv sync --frozen --python ${{ matrix.python-version }}" in workflow
    assert "uv run --frozen python scripts/ci.py" in workflow
    assert "git diff --exit-code" in workflow
    assert "git diff --cached --exit-code" in workflow
    assert "cancel-in-progress: true" in workflow

    lowercase = workflow.lower()
    for forbidden in (
        "pull_request_target",
        "secrets.",
        "upload-artifact",
        "publish",
        "deploy",
        "dataset",
        "gpu",
        "schedule:",
    ):
        assert forbidden not in lowercase


def test_readme_documents_validated_local_ci_command() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "uv run --frozen python scripts/ci.py" in readme
    assert "complete quality gate" in readme
    assert "isolated foundation smoke run" in readme
