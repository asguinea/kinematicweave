"""Deterministic root-seed validation and derived-seed utilities."""

from kinematicweave.canonical import canonical_sha256
from kinematicweave.errors import ValidationError

__all__ = [
    "derive_seed",
    "validate_root_seed",
]

_MAX_ROOT_SEED = (1 << 63) - 1
_MIN_SIGNED_COMPONENT = -(1 << 63)
_MAX_SIGNED_COMPONENT = (1 << 63) - 1


def validate_root_seed(value: object) -> int:
    """Return a valid root seed in the inclusive unsigned 63-bit range.

    Raises:
        ValidationError: If the value is not an integer in the valid range.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError("root_seed must be an integer")
    if not 0 <= value <= _MAX_ROOT_SEED:
        raise ValidationError(f"root_seed must be between 0 and {_MAX_ROOT_SEED}")
    return value


def derive_seed(root_seed: int, *components: str | int) -> int:
    """Derive a stable unsigned 63-bit seed from ordered components.

    Raises:
        ValidationError: If the root seed or a component is invalid.
    """
    normalized_root_seed = validate_root_seed(root_seed)
    if not components:
        raise ValidationError("seed derivation requires at least one component")

    normalized_components: list[str | int] = []
    for component in components:
        if isinstance(component, bool):
            raise ValidationError("seed components must be strings or integers")
        if isinstance(component, int):
            if not _MIN_SIGNED_COMPONENT <= component <= _MAX_SIGNED_COMPONENT:
                raise ValidationError("integer seed components must fit signed 64-bit")
            normalized_components.append(component)
            continue
        if isinstance(component, str):
            normalized = component.strip()
            if not normalized:
                raise ValidationError("string seed components must be nonempty")
            normalized_components.append(normalized)
            continue
        raise ValidationError("seed components must be strings or integers")

    digest = bytes.fromhex(
        canonical_sha256(
            "seed",
            {
                "components": normalized_components,
                "root_seed": normalized_root_seed,
            },
        )
    )
    return int.from_bytes(digest[:8], byteorder="big") & _MAX_ROOT_SEED
