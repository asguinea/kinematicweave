"""Canonical JSON encoding and domain-separated hashing utilities."""

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
from typing import cast

from kinematicweave.errors import ValidationError

__all__ = [
    "canonical_json_bytes",
    "canonical_json_text",
    "canonical_sha256",
]

type _CanonicalValue = (
    None
    | bool
    | int
    | float
    | str
    | list["_CanonicalValue"]
    | dict[str, "_CanonicalValue"]
)

# Artifact identities are part of the persisted file format. Keep this byte
# namespace independent of the installable distribution name so a rebrand does
# not invalidate previously generated evidence. The hex encoding also prevents
# an implementation detail from being mistaken for user-facing branding.
_ARTIFACT_HASH_NAMESPACE = bytes.fromhex("3364746170653a")


def _encode_normalized(value: _CanonicalValue) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalize(value: object, active_containers: set[int]) -> _CanonicalValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("canonical JSON does not support non-finite floats")
        return 0.0 if value == 0.0 else value
    if isinstance(value, Path):
        return value.as_posix()

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_containers:
            raise ValidationError("canonical JSON does not support recursive values")
        active_containers.add(container_id)
        try:
            mapping = cast(Mapping[object, object], value)
            if any(not isinstance(key, str) for key in mapping):
                raise ValidationError("canonical JSON mapping keys must be strings")
            return {
                key: _normalize(mapping[key], active_containers)
                for key in sorted(cast(list[str], list(mapping)))
            }
        finally:
            active_containers.remove(container_id)

    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_containers:
            raise ValidationError("canonical JSON does not support recursive values")
        active_containers.add(container_id)
        try:
            return [_normalize(item, active_containers) for item in value]
        finally:
            active_containers.remove(container_id)

    if isinstance(value, (set, frozenset)):
        normalized_items = [_normalize(item, active_containers) for item in value]
        normalized_items.sort(key=_encode_normalized)
        return normalized_items

    raise ValidationError(
        f"canonical JSON does not support values of type {type(value).__name__}"
    )


def canonical_json_text(
    value: object,
    *,
    trailing_newline: bool = True,
) -> str:
    """Return deterministic compact JSON text for a supported value.

    Raises:
        ValidationError: If the value cannot be represented canonically.
    """
    encoded = _encode_normalized(_normalize(value, set()))
    return f"{encoded}\n" if trailing_newline else encoded


def canonical_json_bytes(
    value: object,
    *,
    trailing_newline: bool = False,
) -> bytes:
    """Return the canonical JSON representation encoded as UTF-8 bytes.

    Raises:
        ValidationError: If the value cannot be represented canonically.
    """
    return canonical_json_text(
        value,
        trailing_newline=trailing_newline,
    ).encode("utf-8")


def canonical_sha256(domain: str, value: object) -> str:
    """Return a lowercase SHA-256 digest with stable domain separation.

    Raises:
        ValidationError: If the domain or value is invalid.
    """
    if not isinstance(domain, str) or not domain.strip():
        raise ValidationError("hash domain must be a nonempty string")
    normalized_domain = domain.strip()
    payload = (
        _ARTIFACT_HASH_NAMESPACE
        + normalized_domain.encode("utf-8")
        + b":v1\n"
        + canonical_json_bytes(value)
    )
    return hashlib.sha256(payload).hexdigest()
