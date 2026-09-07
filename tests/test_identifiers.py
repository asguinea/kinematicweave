"""Tests for deterministic project identifiers."""

import pytest

from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import make_identifier, validate_identifier


def test_identifier_construction_and_trimming() -> None:
    """Entity types and components are trimmed while preserving case."""
    assert make_identifier(" Scenario ", " AV2 ", " AbC123 ") == ("Scenario:AV2:AbC123")


def test_identifier_validation_round_trip() -> None:
    """A constructed identifier validates to the same normalized text."""
    identifier = make_identifier("scenario", "av2", "abc123")

    assert validate_identifier(identifier) == identifier
    assert validate_identifier(f"  {identifier}  ") == identifier


def test_identifier_output_is_deterministic() -> None:
    """Repeated construction produces identical output."""
    first = make_identifier("method", "M5")
    assert first == make_identifier("method", "M5")


def test_missing_component_is_rejected() -> None:
    """Construction requires at least one component."""
    with pytest.raises(ValidationError, match="at least one"):
        make_identifier("scenario")


@pytest.mark.parametrize(
    ("entity_type", "component"),
    [("", "demo"), ("   ", "demo"), ("scenario", ""), ("scenario", "  ")],
)
def test_empty_parts_are_rejected(entity_type: str, component: str) -> None:
    """Empty entity types and components are invalid."""
    with pytest.raises(ValidationError, match="nonempty"):
        make_identifier(entity_type, component)


@pytest.mark.parametrize(
    ("entity_type", "component"),
    [("scenario:scope", "demo"), ("scenario", "scope:demo")],
)
def test_embedded_colons_are_rejected(entity_type: str, component: str) -> None:
    """Individual identifier parts cannot contain the separator."""
    with pytest.raises(ValidationError, match="contain ':'"):
        make_identifier(entity_type, component)


@pytest.mark.parametrize(
    "value", ["scenario\tdemo", "scenario\ndemo", "scenario\x7fdemo"]
)
def test_control_characters_are_rejected(value: str) -> None:
    """Tabs, newlines, and other control characters are invalid."""
    with pytest.raises(ValidationError, match="control"):
        make_identifier("scenario", value)


@pytest.mark.parametrize(
    "value",
    ["scenario", ":demo", "scenario:", "scenario::demo", "  "],
)
def test_malformed_complete_identifiers_are_rejected(value: str) -> None:
    """Complete identifiers require nonempty colon-delimited parts."""
    with pytest.raises(ValidationError):
        validate_identifier(value)


def test_public_exports_are_exact() -> None:
    """The module exposes only its intended public functions."""
    from kinematicweave import identifiers

    assert identifiers.__all__ == ["make_identifier", "validate_identifier"]
