"""Typed, deterministic project configuration loading and serialization."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from typing import TypedDict, cast

from kinematicweave.canonical import canonical_json_text
from kinematicweave.errors import ConfigurationError, ValidationError
from kinematicweave.paths import normalize_relative_path
from kinematicweave.seeding import validate_root_seed

__all__ = [
    "PathsConfig",
    "ProjectConfig",
    "config_to_canonical_json",
    "config_to_dict",
    "default_config",
    "load_config",
]

_SCHEMA_VERSION = "1.0"
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "project_name", "root_seed", "paths"})
_PATH_FIELDS = frozenset({"data", "results", "reports", "figures", "qualitative"})
_SCALAR_OVERRIDE_FIELDS = frozenset({"schema_version", "project_name", "root_seed"})


def _coerce_relative_path(value: object, field_name: str) -> Path:
    if isinstance(value, Path):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ConfigurationError(f"{field_name} must be a path string")

    try:
        return normalize_relative_path(text)
    except ValidationError as error:
        detail = str(error).removeprefix("path ")
        raise ConfigurationError(f"{field_name} {detail}") from None


def _validate_schema_version(value: object) -> str:
    if not isinstance(value, str) or value != _SCHEMA_VERSION:
        raise ConfigurationError(f"schema_version must equal {_SCHEMA_VERSION!r}")
    return value


def _validate_project_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("project_name must be a nonempty string")
    return value.strip()


def _validate_root_seed(value: object) -> int:
    try:
        return validate_root_seed(value)
    except ValidationError as error:
        raise ConfigurationError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PathsConfig:
    """Repository-relative locations for project data and artifacts."""

    data: Path = Path("data")
    results: Path = Path("results")
    reports: Path = Path("reports")
    figures: Path = Path("figures")
    qualitative: Path = Path("qualitative")

    def __post_init__(self) -> None:
        """Normalize and validate every configured repository path."""
        object.__setattr__(self, "data", _coerce_relative_path(self.data, "paths.data"))
        object.__setattr__(
            self,
            "results",
            _coerce_relative_path(self.results, "paths.results"),
        )
        object.__setattr__(
            self,
            "reports",
            _coerce_relative_path(self.reports, "paths.reports"),
        )
        object.__setattr__(
            self,
            "figures",
            _coerce_relative_path(self.figures, "paths.figures"),
        )
        object.__setattr__(
            self,
            "qualitative",
            _coerce_relative_path(self.qualitative, "paths.qualitative"),
        )


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Resolved foundation-level project configuration."""

    schema_version: str = _SCHEMA_VERSION
    project_name: str = "KinematicWeave"
    root_seed: int = 0
    paths: PathsConfig = field(default_factory=PathsConfig)

    def __post_init__(self) -> None:
        """Normalize and validate all foundation-level fields."""
        object.__setattr__(
            self,
            "schema_version",
            _validate_schema_version(self.schema_version),
        )
        object.__setattr__(
            self,
            "project_name",
            _validate_project_name(self.project_name),
        )
        object.__setattr__(
            self,
            "root_seed",
            _validate_root_seed(self.root_seed),
        )
        if not isinstance(self.paths, PathsConfig):
            raise ConfigurationError("paths must be a PathsConfig instance")


class _ConfigValues(TypedDict):
    schema_version: object
    project_name: object
    root_seed: object
    paths: dict[str, object]


def default_config() -> ProjectConfig:
    """Return a new validated configuration containing built-in defaults."""
    return ProjectConfig()


def _default_values() -> _ConfigValues:
    config = default_config()
    return {
        "schema_version": config.schema_version,
        "project_name": config.project_name,
        "root_seed": config.root_seed,
        "paths": {
            "data": config.paths.data,
            "results": config.paths.results,
            "reports": config.paths.reports,
            "figures": config.paths.figures,
            "qualitative": config.paths.qualitative,
        },
    }


def _copy_string_mapping(
    values: Mapping[object, object],
    location: str,
) -> dict[str, object]:
    copied: dict[str, object] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise ConfigurationError(f"{location} keys must be strings")
        copied[key] = value
    return copied


def _reject_unknown_fields(
    values: Mapping[str, object],
    allowed_fields: frozenset[str],
    location: str,
) -> None:
    unknown_fields = sorted(set(values).difference(allowed_fields))
    if unknown_fields:
        rendered = ", ".join(repr(field) for field in unknown_fields)
        raise ConfigurationError(f"Unknown {location} field(s): {rendered}")


def _set_scalar_value(
    resolved: _ConfigValues,
    field_name: str,
    value: object,
) -> None:
    if field_name == "schema_version":
        resolved["schema_version"] = value
    elif field_name == "project_name":
        resolved["project_name"] = value
    elif field_name == "root_seed":
        resolved["root_seed"] = value
    else:
        raise ConfigurationError(f"Unknown scalar configuration field: {field_name!r}")


def _merge_file_values(
    resolved: _ConfigValues,
    file_values: Mapping[str, object],
) -> None:
    _reject_unknown_fields(file_values, _TOP_LEVEL_FIELDS, "configuration")
    for field_name in _SCALAR_OVERRIDE_FIELDS:
        if field_name in file_values:
            _set_scalar_value(resolved, field_name, file_values[field_name])

    if "paths" not in file_values:
        return
    raw_paths = file_values["paths"]
    if not isinstance(raw_paths, Mapping):
        raise ConfigurationError("Configuration field 'paths' must be a TOML table")
    path_values = _copy_string_mapping(raw_paths, "paths")
    _reject_unknown_fields(path_values, _PATH_FIELDS, "paths")
    for field_name, value in path_values.items():
        resolved["paths"][field_name] = value


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as config_file:
            parsed = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationError(
            f"Invalid TOML in configuration file {path}: {error}"
        ) from None
    return _copy_string_mapping(
        cast(Mapping[object, object], parsed),
        "configuration",
    )


def _apply_overrides(
    resolved: _ConfigValues,
    overrides: Mapping[str, object],
) -> None:
    for key, value in overrides.items():
        if not isinstance(key, str) or not key:
            raise ConfigurationError("Override keys must be nonempty strings")
        key_parts = key.split(".")
        if any(not part for part in key_parts) or len(key_parts) > 2:
            raise ConfigurationError(f"Malformed override key: {key!r}")
        if len(key_parts) == 1:
            if key in _SCALAR_OVERRIDE_FIELDS:
                _set_scalar_value(resolved, key, value)
                continue
            if key == "paths":
                raise ConfigurationError(
                    "Override key 'paths' must target a specific path field"
                )
            raise ConfigurationError(f"Unknown override key: {key!r}")

        section_name, field_name = key_parts
        if section_name != "paths" or field_name not in _PATH_FIELDS:
            raise ConfigurationError(f"Unknown override key: {key!r}")
        resolved["paths"][field_name] = value


def _build_config(values: _ConfigValues) -> ProjectConfig:
    path_values = values["paths"]
    paths = PathsConfig(
        data=_coerce_relative_path(path_values["data"], "paths.data"),
        results=_coerce_relative_path(path_values["results"], "paths.results"),
        reports=_coerce_relative_path(path_values["reports"], "paths.reports"),
        figures=_coerce_relative_path(path_values["figures"], "paths.figures"),
        qualitative=_coerce_relative_path(
            path_values["qualitative"], "paths.qualitative"
        ),
    )
    return ProjectConfig(
        schema_version=_validate_schema_version(values["schema_version"]),
        project_name=_validate_project_name(values["project_name"]),
        root_seed=_validate_root_seed(values["root_seed"]),
        paths=paths,
    )


def load_config(
    path: Path | None = None,
    overrides: Mapping[str, object] | None = None,
) -> ProjectConfig:
    """Resolve defaults, an optional TOML file, and dotted overrides.

    Raises:
        ConfigurationError: If TOML, fields, overrides, or values are invalid.
        OSError: If the requested configuration file cannot be read.
    """
    resolved = _default_values()
    if path is not None:
        _merge_file_values(resolved, _load_toml(path))
    if overrides is not None:
        _apply_overrides(resolved, overrides)
    return _build_config(resolved)


def config_to_dict(config: ProjectConfig) -> dict[str, object]:
    """Return a fresh serializable mapping for a resolved configuration."""
    return {
        "schema_version": config.schema_version,
        "project_name": config.project_name,
        "root_seed": config.root_seed,
        "paths": {
            "data": config.paths.data.as_posix(),
            "results": config.paths.results.as_posix(),
            "reports": config.paths.reports.as_posix(),
            "figures": config.paths.figures.as_posix(),
            "qualitative": config.paths.qualitative.as_posix(),
        },
    }


def config_to_canonical_json(config: ProjectConfig) -> str:
    """Serialize a resolved configuration as deterministic canonical JSON."""
    return canonical_json_text(
        config_to_dict(config),
        trailing_newline=True,
    )
