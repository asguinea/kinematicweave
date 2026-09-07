"""Tests for the project-owned exception hierarchy."""

from pathlib import Path

import pytest

from kinematicweave.config import load_config
import kinematicweave.errors as errors


def test_exception_hierarchy() -> None:
    """Every public exception has the approved inheritance behavior."""
    assert issubclass(errors.KinematicWeaveError, Exception)
    assert issubclass(errors.ConfigurationError, errors.KinematicWeaveError)
    assert issubclass(errors.ConfigurationError, ValueError)
    assert issubclass(errors.ValidationError, errors.KinematicWeaveError)
    assert issubclass(errors.ValidationError, ValueError)
    assert issubclass(errors.SchemaError, errors.ValidationError)
    assert issubclass(errors.ResourceLimitError, errors.KinematicWeaveError)
    assert issubclass(errors.ExperimentError, errors.KinematicWeaveError)
    assert issubclass(errors.ArtifactError, errors.KinematicWeaveError)


def test_value_error_compatibility() -> None:
    """Configuration and validation errors remain ValueError instances."""
    assert isinstance(errors.ConfigurationError("invalid"), ValueError)
    assert isinstance(errors.ValidationError("invalid"), ValueError)
    assert isinstance(errors.SchemaError("invalid"), ValueError)


def test_errors_public_exports() -> None:
    """The errors module exports exactly the approved hierarchy."""
    assert errors.__all__ == [
        "ArtifactError",
        "ConfigurationError",
        "ExperimentError",
        "KinematicWeaveError",
        "ResourceLimitError",
        "SchemaError",
        "ValidationError",
    ]


@pytest.mark.parametrize(
    ("path", "overrides"),
    [
        (None, {"root_seed": True}),
        (None, {"paths.data": "../outside"}),
    ],
)
def test_configuration_failures_use_project_error(
    path: Path | None,
    overrides: dict[str, object],
) -> None:
    """Representative configuration failures use ConfigurationError."""
    with pytest.raises(errors.ConfigurationError):
        load_config(path, overrides)


def test_malformed_toml_uses_project_error_without_parser_cause(
    tmp_path: Path,
) -> None:
    """Malformed TOML has a clear ConfigurationError without a cause."""
    config_path = tmp_path / "invalid.toml"
    config_path.write_text('project_name = ["unterminated"\n', encoding="utf-8")

    with pytest.raises(errors.ConfigurationError) as captured:
        load_config(config_path)

    assert "Invalid TOML" in str(captured.value)
    assert captured.value.__cause__ is None
