"""Tests for deterministic seed validation and derivation."""

import pytest

from kinematicweave.config import load_config
from kinematicweave.errors import ConfigurationError, ValidationError
from kinematicweave.seeding import derive_seed, validate_root_seed

MAX_ROOT_SEED = (1 << 63) - 1


@pytest.mark.parametrize("value", [0, MAX_ROOT_SEED])
def test_root_seed_boundaries_are_valid(value: int) -> None:
    """Both inclusive root-seed boundaries are accepted."""
    assert validate_root_seed(value) == value


@pytest.mark.parametrize("value", [True, False])
def test_boolean_root_seeds_are_rejected(value: bool) -> None:
    """Booleans are not treated as integer seeds."""
    with pytest.raises(ValidationError, match="integer"):
        validate_root_seed(value)


@pytest.mark.parametrize("value", [-1, MAX_ROOT_SEED + 1])
def test_out_of_range_root_seeds_are_rejected(value: int) -> None:
    """Root seeds outside the unsigned 63-bit range are invalid."""
    with pytest.raises(ValidationError, match="between"):
        validate_root_seed(value)


def test_seed_derivation_is_deterministic_and_in_range() -> None:
    """Repeated experiment-style derivation produces a valid stable seed."""
    components = ("MOT-01", "M5", "scenario:demo")

    first = derive_seed(0, *components)

    assert first == 966224205245294457
    assert first == derive_seed(0, *components)
    assert 0 <= first <= MAX_ROOT_SEED


def test_component_order_changes_the_seed() -> None:
    """Ordered derivation components are semantically significant."""
    assert derive_seed(0, "experiment", "method") != derive_seed(
        0,
        "method",
        "experiment",
    )


def test_integer_components_are_supported() -> None:
    """Signed 64-bit integer components participate in derivation."""
    assert derive_seed(7, "replicate", 0) == derive_seed(7, "replicate", 0)
    assert derive_seed(7, -(1 << 63)) != derive_seed(7, (1 << 63) - 1)


@pytest.mark.parametrize("component", ["", "   "])
def test_empty_string_components_are_rejected(component: str) -> None:
    """String components must be nonempty after trimming."""
    with pytest.raises(ValidationError, match="nonempty"):
        derive_seed(0, component)


def test_missing_derivation_components_are_rejected() -> None:
    """A root seed alone is insufficient for derivation."""
    with pytest.raises(ValidationError, match="at least one"):
        derive_seed(0)


@pytest.mark.parametrize("component", [-(1 << 63) - 1, 1 << 63])
def test_oversized_integer_components_are_rejected(component: int) -> None:
    """Integer components must fit the signed 64-bit range."""
    with pytest.raises(ValidationError, match="signed 64-bit"):
        derive_seed(0, component)


def test_config_translates_root_seed_errors() -> None:
    """Configuration exposes shared seed failures as ConfigurationError."""
    with pytest.raises(ConfigurationError, match="root_seed"):
        load_config(overrides={"root_seed": True})


def test_public_exports_are_exact() -> None:
    """The module exposes only its intended public functions."""
    from kinematicweave import seeding

    assert seeding.__all__ == ["derive_seed", "validate_root_seed"]
