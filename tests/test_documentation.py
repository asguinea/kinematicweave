"""Consistency tests for public documentation."""

from __future__ import annotations

from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"
PUBLIC_DOCUMENT_PATHS = (
    README_PATH,
    PROJECT_ROOT / "CONTRIBUTING.md",
    PROJECT_ROOT / "SECURITY.md",
    PROJECT_ROOT / "CODE_OF_CONDUCT.md",
    PROJECT_ROOT / "DATA_LICENSE.md",
    PROJECT_ROOT / "docs" / "architecture.md",
    PROJECT_ROOT / "docs" / "data.md",
    PROJECT_ROOT / "docs" / "reproducibility.md",
    PROJECT_ROOT / "docs" / "foundation_setup.md",
    PROJECT_ROOT / "docs" / "troubleshooting.md",
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
MACHINE_ABSOLUTE_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|/(?:home|Users|tmp)/)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _relative_links(path: Path) -> tuple[str, ...]:
    links = []
    for target in MARKDOWN_LINK.findall(_read(path)):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        links.append(target.split("#", maxsplit=1)[0])
    return tuple(links)


def test_public_documentation_files_exist() -> None:
    assert all(path.is_file() for path in PUBLIC_DOCUMENT_PATHS)


def test_public_documentation_links_resolve() -> None:
    for path in PUBLIC_DOCUMENT_PATHS:
        for target in _relative_links(path):
            assert (path.parent / target).resolve().is_file(), (
                f"broken link in {path.relative_to(PROJECT_ROOT)}: {target}"
            )


def test_public_documentation_has_no_machine_specific_paths() -> None:
    for path in PUBLIC_DOCUMENT_PATHS:
        text = _read(path)
        assert MACHINE_ABSOLUTE_PATH.search(text) is None
        assert "example-user" not in text.casefold()


def test_readme_contains_validated_entry_points() -> None:
    readme = _read(README_PATH)
    for command in (
        "uv sync --frozen",
        "uv run --frozen kinematicweave --version",
        "uv run --frozen kinematicweave doctor",
        "uv run --frozen python scripts/quality.py",
        "uv run --frozen python scripts/run_foundation_smoke.py --run-id example:smoke",
        "uv run --frozen python scripts/run_phase4_milestone.py --verify-only",
        "uv run --frozen python scripts/run_layout_feasibility.py --verify-only",
    ):
        assert command in readme


def test_readme_states_scope_data_and_license_boundaries() -> None:
    readme = _read(README_PATH)
    for statement in (
        "does not establish a universal winner",
        "does not reconstruct complete road networks",
        "does not redistribute the Argoverse 2 source dataset",
        "CC BY-NC-SA 4.0",
        "Apache-2.0",
    ):
        assert statement in readme


def test_foundation_setup_lists_implemented_commands() -> None:
    setup = _read(PROJECT_ROOT / "docs" / "foundation_setup.md")
    for command in (
        "kinematicweave --version",
        "kinematicweave --help",
        "kinematicweave doctor",
        "kinematicweave doctor --json",
        "kinematicweave config validate",
        "kinematicweave config show",
        "kinematicweave artifact inspect-run",
        "python -m kinematicweave",
        "python scripts/run_foundation_smoke.py",
        "python scripts/quality.py",
        "python scripts/ci.py",
    ):
        assert command in setup


def test_troubleshooting_covers_safe_recovery() -> None:
    troubleshooting = _read(PROJECT_ROOT / "docs" / "troubleshooting.md")
    for exit_code in ("Exit code `0`", "Exit code `1`", "Exit code `2`"):
        assert exit_code in troubleshooting
    for statement in (
        "Do not bypass frozen validation",
        "Never delete a completed run directory",
        "Do not use a broad cleanup command",
        "Do not publish full environment-variable dumps",
        "Never commit local datasets",
    ):
        assert statement in troubleshooting


def test_public_documentation_has_no_placeholders() -> None:
    for path in PUBLIC_DOCUMENT_PATHS:
        folded = _read(path).casefold()
        for placeholder in ("todo", "fixme", "coming soon"):
            assert placeholder not in folded
