"""Tests for repository-relative path normalization."""

from pathlib import Path

import pytest

from kinematicweave.config import load_config
from kinematicweave.errors import ConfigurationError, ValidationError
from kinematicweave.paths import normalize_relative_path, relative_path_text


def test_simple_relative_path_is_preserved() -> None:
    """A simple repository-relative path remains relative."""
    assert normalize_relative_path("data/input") == Path("data/input")


@pytest.mark.parametrize(
    "value",
    [r"data\input\file.json", "data//input/./file.json"],
)
def test_separators_and_dot_components_are_normalized(value: str) -> None:
    """Either separator is accepted and harmless dot components disappear."""
    assert relative_path_text(value) == "data/input/file.json"


@pytest.mark.parametrize("value", ["", "."])
def test_empty_normalized_path_is_rejected(value: str) -> None:
    """Paths must identify at least one relative component."""
    with pytest.raises(ValidationError, match="nonempty"):
        normalize_relative_path(value)


@pytest.mark.parametrize("value", ["/absolute/data", "//server/share/data"])
def test_posix_absolute_paths_are_rejected(value: str) -> None:
    """POSIX absolute paths are invalid on every platform."""
    with pytest.raises(ValidationError, match="repository-relative"):
        normalize_relative_path(value)


@pytest.mark.parametrize(
    "value",
    [r"C:\absolute\data", "C:/absolute/data", r"\\server\share\data"],
)
def test_windows_absolute_paths_are_rejected(value: str) -> None:
    """Windows absolute and UNC paths are invalid on every platform."""
    with pytest.raises(ValidationError, match="repository-relative"):
        normalize_relative_path(value)


@pytest.mark.parametrize("value", ["C:data", "D:folder/file"])
def test_drive_qualified_paths_are_rejected(value: str) -> None:
    """Drive-qualified relative-looking paths are still machine-specific."""
    with pytest.raises(ValidationError, match="repository-relative"):
        normalize_relative_path(value)


@pytest.mark.parametrize(
    "value",
    ["../outside", "data/../outside", r"..\outside", r"data\..\outside"],
)
def test_parent_traversal_is_rejected(value: str) -> None:
    """Parent traversal is rejected with either separator style."""
    with pytest.raises(ValidationError, match="parent traversal"):
        normalize_relative_path(value)


def test_forward_slash_text_is_deterministic() -> None:
    """Portable text output is stable across repeated calls."""
    first = relative_path_text(r"results\run-01\metrics.json")
    assert first == "results/run-01/metrics.json"
    assert first == relative_path_text(r"results\run-01\metrics.json")


@pytest.mark.parametrize("value", ["/absolute/data", "../outside", ""])
def test_config_translates_path_errors(value: str) -> None:
    """Configuration keeps shared path failures inside its public error family."""
    with pytest.raises(ConfigurationError):
        load_config(overrides={"paths.data": value})


def test_public_exports_are_exact() -> None:
    """The module exposes only its intended public functions."""
    from kinematicweave import paths

    assert paths.__all__ == ["normalize_relative_path", "relative_path_text"]
