"""Tests for typed project configuration resolution."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from kinematicweave.config import (
    PathsConfig,
    ProjectConfig,
    config_to_canonical_json,
    config_to_dict,
    default_config,
    load_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "configs" / "project.toml"
MAX_ROOT_SEED = (1 << 63) - 1


def _write_toml(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "project.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_built_in_defaults_are_typed_and_immutable() -> None:
    """Built-in defaults produce immutable slotted dataclasses."""
    config = default_config()

    assert config == ProjectConfig()
    assert config.paths == PathsConfig()
    assert config.schema_version == "1.0"
    assert config.project_name == "KinematicWeave"
    assert config.root_seed == 0
    assert config_to_dict(config)["paths"] == {
        "data": "data",
        "results": "results",
        "reports": "reports",
        "figures": "figures",
        "qualitative": "qualitative",
    }
    with pytest.raises(FrozenInstanceError):
        config.root_seed = 1  # type: ignore[misc]


def test_example_toml_matches_built_in_defaults() -> None:
    """The committed example resolves to the approved defaults."""
    assert load_config(EXAMPLE_CONFIG) == default_config()


def test_partial_toml_overrides_one_field(tmp_path: Path) -> None:
    """A partial TOML file preserves unspecified defaults."""
    config_path = _write_toml(tmp_path, 'project_name = "Research Tape"\n')

    config = load_config(config_path)

    assert config.project_name == "Research Tape"
    assert config.schema_version == "1.0"
    assert config.root_seed == 0
    assert config.paths == PathsConfig()


def test_explicit_override_takes_precedence_over_file(tmp_path: Path) -> None:
    """Explicit overrides take precedence over TOML values."""
    config_path = _write_toml(tmp_path, "root_seed = 10\n")

    config = load_config(config_path, {"root_seed": 20})

    assert config.root_seed == 20


def test_nested_dotted_path_overrides_merge_individually() -> None:
    """Dotted path overrides replace only their targeted path fields."""
    config = load_config(
        overrides={
            "paths.data": "workspace/input",
            "paths.results": "workspace/output",
        }
    )

    assert config.paths.data == Path("workspace/input")
    assert config.paths.results == Path("workspace/output")
    assert config.paths.reports == Path("reports")
    assert config.paths.figures == Path("figures")
    assert config.paths.qualitative == Path("qualitative")


def test_partial_paths_table_preserves_other_defaults(tmp_path: Path) -> None:
    """A partial paths table preserves unspecified path defaults."""
    config_path = _write_toml(
        tmp_path,
        '[paths]\ndata = "local/data"\n',
    )

    config = load_config(config_path)

    assert config.paths.data == Path("local/data")
    assert config.paths.results == Path("results")
    assert config.paths.reports == Path("reports")


def test_canonical_json_is_deterministic_and_compact() -> None:
    """Canonical serialization is sorted, compact, and repeatable."""
    config = default_config()

    first = config_to_canonical_json(config)
    second = config_to_canonical_json(config)

    assert first == second
    assert first == (
        '{"paths":{"data":"data","figures":"figures",'
        '"qualitative":"qualitative","reports":"reports",'
        '"results":"results"},"project_name":"KinematicWeave",'
        '"root_seed":0,"schema_version":"1.0"}\n'
    )
    assert first.endswith("\n")
    assert not first.endswith("\n\n")
    assert json.loads(first) == config_to_dict(config)


def test_canonical_json_uses_forward_slashes() -> None:
    """Canonical paths use forward slashes on every platform."""
    config = load_config(overrides={"paths.data": r"nested\data"})

    assert '"data":"nested/data"' in config_to_canonical_json(config)


def test_override_mapping_is_not_mutated() -> None:
    """Configuration resolution does not mutate caller mappings."""
    overrides: dict[str, object] = {
        "project_name": "Override Project",
        "paths.data": "override/data",
    }
    original = dict(overrides)

    load_config(overrides=overrides)

    assert overrides == original


def test_unknown_top_level_field_is_rejected(tmp_path: Path) -> None:
    """Unknown top-level TOML fields fail validation."""
    config_path = _write_toml(tmp_path, 'unknown = "value"\n')

    with pytest.raises(ValueError, match="Unknown configuration field"):
        load_config(config_path)


def test_unknown_nested_path_field_is_rejected(tmp_path: Path) -> None:
    """Unknown nested path fields fail validation."""
    config_path = _write_toml(tmp_path, '[paths]\nunknown = "value"\n')

    with pytest.raises(ValueError, match="Unknown paths field"):
        load_config(config_path)


@pytest.mark.parametrize(
    "override_key",
    ["", ".root_seed", "paths.", "paths..data", "paths.data.extra"],
)
def test_malformed_override_key_is_rejected(override_key: str) -> None:
    """Malformed dotted override keys fail validation."""
    with pytest.raises(ValueError, match=r"Override keys|Malformed"):
        load_config(overrides={override_key: 1})


@pytest.mark.parametrize("override_key", ["unknown", "paths.unknown"])
def test_unknown_override_key_is_rejected(override_key: str) -> None:
    """Unknown dotted override keys fail validation."""
    with pytest.raises(ValueError, match="Unknown override key"):
        load_config(overrides={override_key: "value"})


def test_scalar_paths_override_is_rejected() -> None:
    """The paths override must target one specific nested field."""
    with pytest.raises(ValueError, match="must target a specific path field"):
        load_config(overrides={"paths": "data"})


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    """Only schema version 1.0 is accepted."""
    config_path = _write_toml(tmp_path, 'schema_version = "2.0"\n')

    with pytest.raises(ValueError, match="schema_version"):
        load_config(config_path)


@pytest.mark.parametrize("project_name", ["", "   "])
def test_empty_project_name_is_rejected(project_name: str) -> None:
    """Empty and whitespace-only project names are rejected."""
    with pytest.raises(ValueError, match="project_name"):
        load_config(overrides={"project_name": project_name})


def test_project_name_is_trimmed() -> None:
    """Valid project names are normalized by trimming whitespace."""
    config = load_config(overrides={"project_name": "  KinematicWeave Lab  "})

    assert config.project_name == "KinematicWeave Lab"


@pytest.mark.parametrize("root_seed", [-1, MAX_ROOT_SEED + 1])
def test_out_of_range_root_seed_is_rejected(root_seed: int) -> None:
    """Root seeds outside the unsigned 63-bit range are rejected."""
    with pytest.raises(ValueError, match="root_seed"):
        load_config(overrides={"root_seed": root_seed})


def test_boolean_root_seed_is_rejected() -> None:
    """Boolean values are not accepted as integer root seeds."""
    with pytest.raises(ValueError, match="root_seed"):
        load_config(overrides={"root_seed": True})


@pytest.mark.parametrize("path_value", ["/absolute/data", "C:/absolute/data"])
def test_absolute_path_is_rejected(path_value: str) -> None:
    """Absolute POSIX and Windows paths are rejected on every platform."""
    with pytest.raises(ValueError, match="repository-relative"):
        load_config(overrides={"paths.data": path_value})


@pytest.mark.parametrize(
    "path_value",
    ["../outside", "data/../outside", r"data\..\outside"],
)
def test_parent_traversal_is_rejected(path_value: str) -> None:
    """Parent traversal is rejected with either path separator."""
    with pytest.raises(ValueError, match="parent traversal"):
        load_config(overrides={"paths.data": path_value})


def test_malformed_toml_has_clear_value_error(tmp_path: Path) -> None:
    """Malformed TOML is translated to a clear public ValueError."""
    config_path = _write_toml(tmp_path, 'project_name = ["unterminated"\n')

    with pytest.raises(ValueError, match="Invalid TOML"):
        load_config(config_path)


def test_scalar_paths_value_is_rejected(tmp_path: Path) -> None:
    """The TOML paths field must be a table rather than a scalar."""
    config_path = _write_toml(tmp_path, 'paths = "data"\n')

    with pytest.raises(ValueError, match="must be a TOML table"):
        load_config(config_path)
