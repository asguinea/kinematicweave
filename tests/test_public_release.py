"""Guardrails for a clean, neutral public repository snapshot."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".cff",
    ".csv",
    ".json",
    ".md",
    ".py",
    ".tex",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
OLD_IDENTITY = re.compile(r"3d[ _-]?tape|tape3d", flags=re.IGNORECASE)
RESTRICTED_CONTEXT = re.compile(
    r"\b(?:paper|poster|submission)\b|under review|camera[- ]ready",
    flags=re.IGNORECASE,
)
MACHINE_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|/(?:home|Users)/[^/]+/)")


def _snapshot_files() -> tuple[Path, ...]:
    completed = subprocess.run(
        (
            "git",
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        PROJECT_ROOT / line
        for line in completed.stdout.splitlines()
        if line and (PROJECT_ROOT / line).is_file()
    )


def test_snapshot_paths_use_only_public_identity() -> None:
    for path in _snapshot_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        assert OLD_IDENTITY.search(relative) is None
        assert RESTRICTED_CONTEXT.search(relative) is None


def test_snapshot_text_has_no_development_identity_or_release_context() -> None:
    this_file = Path(__file__).resolve()
    for path in _snapshot_files():
        if (
            path == this_file
            or path.name == "LICENSE"
            or path.suffix not in TEXT_SUFFIXES
        ):
            continue
        text = path.read_text(encoding="utf-8")
        assert OLD_IDENTITY.search(text) is None, path.relative_to(PROJECT_ROOT)
        assert RESTRICTED_CONTEXT.search(text) is None, path.relative_to(PROJECT_ROOT)


def test_committed_evidence_has_no_user_specific_paths() -> None:
    for path in (PROJECT_ROOT / "results").rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        assert MACHINE_PATH.search(text) is None, path.relative_to(PROJECT_ROOT)


def test_unrelated_local_pdfs_are_ignored() -> None:
    for name in ("sample_paper1.pdf", "sample_paper2.pdf", "sample_paper3.pdf"):
        completed = subprocess.run(
            (
                "git",
                "-c",
                f"safe.directory={PROJECT_ROOT.as_posix()}",
                "check-ignore",
                "--quiet",
                name,
            ),
            cwd=PROJECT_ROOT,
            check=False,
        )
        assert completed.returncode == 0
