"""Deterministic construction and validation of project identifiers."""

import unicodedata

from kinematicweave.errors import ValidationError

__all__ = [
    "make_identifier",
    "validate_identifier",
]


def _normalize_part(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValidationError(f"{label} must not contain control characters")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{label} must be nonempty")
    if ":" in normalized:
        raise ValidationError(f"{label} must not contain ':'")
    return normalized


def make_identifier(entity_type: str, *components: str) -> str:
    """Build a normalized deterministic identifier from explicit components.

    Raises:
        ValidationError: If the entity type or any component is invalid.
    """
    if not components:
        raise ValidationError("identifier requires at least one component")
    normalized_parts = [_normalize_part(entity_type, "entity_type")]
    normalized_parts.extend(
        _normalize_part(component, "identifier component") for component in components
    )
    return ":".join(normalized_parts)


def validate_identifier(value: str) -> str:
    """Validate and normalize a complete colon-delimited identifier.

    Raises:
        ValidationError: If the identifier is malformed.
    """
    if not isinstance(value, str):
        raise ValidationError("identifier must be a string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValidationError("identifier must not contain control characters")
    normalized = value.strip()
    parts = normalized.split(":")
    if len(parts) < 2:
        raise ValidationError("identifier requires an entity type and component")
    return make_identifier(parts[0], *parts[1:])
