"""Tests for canonical JSON encoding and stable hashing."""

from pathlib import Path

import pytest

from kinematicweave.canonical import (
    canonical_json_bytes,
    canonical_json_text,
    canonical_sha256,
)
from kinematicweave.errors import ValidationError


def test_mapping_keys_and_nested_mappings_are_sorted() -> None:
    """Mapping keys are sorted at every nesting level."""
    value = {"z": {"b": 2, "a": 1}, "a": 0}

    assert canonical_json_text(value, trailing_newline=False) == (
        '{"a":0,"z":{"a":1,"b":2}}'
    )


def test_ordered_sequences_preserve_order() -> None:
    """Lists and tuples retain their semantic order."""
    assert (
        canonical_json_text(
            {"list": [2, 1], "tuple": (3, 2)},
            trailing_newline=False,
        )
        == '{"list":[2,1],"tuple":[3,2]}'
    )


def test_unordered_sets_are_sorted_deterministically() -> None:
    """Sets and frozen sets use canonical serialized ordering."""
    value = {"set": {"z", "a"}, "frozen": frozenset({3, 1, 2})}

    expected = '{"frozen":[1,2,3],"set":["a","z"]}'
    assert canonical_json_text(value, trailing_newline=False) == expected
    assert canonical_json_text(value, trailing_newline=False) == expected


def test_paths_use_forward_slashes_and_utf8_is_preserved() -> None:
    """Paths use portable separators and Unicode is not ASCII-escaped."""
    value = {"label": "München", "path": Path("nested") / "file.json"}

    assert canonical_json_text(value, trailing_newline=False) == (
        '{"label":"München","path":"nested/file.json"}'
    )


def test_trailing_newline_modes_are_exact() -> None:
    """Text and byte helpers honor their distinct newline defaults."""
    assert canonical_json_text({"a": 1}) == '{"a":1}\n'
    assert canonical_json_text({"a": 1}, trailing_newline=False) == '{"a":1}'
    assert canonical_json_bytes({"a": 1}) == b'{"a":1}'
    assert canonical_json_bytes({"a": 1}, trailing_newline=True) == b'{"a":1}\n'


def test_finite_floats_and_negative_zero_are_normalized() -> None:
    """Finite floats remain numeric and negative zero loses its sign."""
    assert (
        canonical_json_text(
            [-0.0, 1.25],
            trailing_newline=False,
        )
        == "[0.0,1.25]"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_rejected(value: float) -> None:
    """NaN and both infinities are outside the canonical value domain."""
    with pytest.raises(ValidationError, match="non-finite"):
        canonical_json_text(value)


def test_non_string_mapping_keys_are_rejected() -> None:
    """Canonical mappings require string keys."""
    with pytest.raises(ValidationError, match="keys must be strings"):
        canonical_json_text({1: "value"})


def test_unsupported_values_are_rejected() -> None:
    """Objects outside the supported canonical domain fail explicitly."""
    with pytest.raises(ValidationError, match="does not support"):
        canonical_json_text(object())


def test_recursive_values_are_rejected() -> None:
    """Recursive containers fail with a project validation error."""
    value: list[object] = []
    value.append(value)

    with pytest.raises(ValidationError, match="recursive"):
        canonical_json_text(value)


def test_sha256_is_stable_and_domain_separated() -> None:
    """Known input has a stable digest and domains produce distinct hashes."""
    value = {"nested": {"b": 2, "a": 1}}

    digest = canonical_sha256("example", value)

    assert digest == "e22a43c42e2e88794027df10c0777d776c7e51904ce7899ab99c2bbe0f125e35"
    assert digest == canonical_sha256(" example ", value)
    assert digest != canonical_sha256("other", value)
    assert len(digest) == 64
    assert digest == digest.lower()


@pytest.mark.parametrize("domain", ["", "   "])
def test_invalid_hash_domain_is_rejected(domain: str) -> None:
    """Hash domains must be nonempty after trimming."""
    with pytest.raises(ValidationError, match="domain"):
        canonical_sha256(domain, {})


def test_public_exports_are_exact() -> None:
    """The module exposes only its intended public functions."""
    from kinematicweave import canonical

    assert canonical.__all__ == [
        "canonical_json_bytes",
        "canonical_json_text",
        "canonical_sha256",
    ]
