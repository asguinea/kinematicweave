"""Explicit structured logging for the KinematicWeave logger hierarchy."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import sys
from typing import TextIO, cast

from kinematicweave.errors import ConfigurationError, ValidationError

__all__ = ["LogContext", "configure_logging", "get_logger"]

_PROJECT_LOGGER_NAME = "kinematicweave"
_CONTEXT_FIELDS = (
    "run_id",
    "experiment_id",
    "scenario_id",
    "tile_id",
    "method_id",
    "stage",
)
_LEVEL_NAMES: Mapping[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.FATAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARN,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}
_STANDARD_LEVELS = frozenset(_LEVEL_NAMES.values())
_CONTEXT_RECORD_KEY = "_kinematicweave_context"


def _normalize_context_value(
    value: str | None,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be None or a nonempty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class LogContext:
    """Stable identifiers attached to every record from a logger adapter."""

    run_id: str | None = None
    experiment_id: str | None = None
    scenario_id: str | None = None
    tile_id: str | None = None
    method_id: str | None = None
    stage: str | None = None

    def __post_init__(self) -> None:
        """Normalize and validate every optional context value."""
        for field_name in _CONTEXT_FIELDS:
            value = cast(str | None, getattr(self, field_name))
            object.__setattr__(
                self,
                field_name,
                _normalize_context_value(value, field_name),
            )

    def _items(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (field_name, value)
            for field_name in _CONTEXT_FIELDS
            if (value := cast(str | None, getattr(self, field_name))) is not None
        )


def _timestamp_utc(record: logging.LogRecord) -> str:
    timestamp = datetime.fromtimestamp(record.created, tz=UTC)
    return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _context_items(
    record: logging.LogRecord,
) -> tuple[tuple[str, str], ...]:
    return cast(
        tuple[tuple[str, str], ...],
        getattr(record, _CONTEXT_RECORD_KEY, ()),
    )


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        """Format one human-readable record with ordered context."""
        parts = [
            _timestamp_utc(record),
            record.levelname,
            record.name,
            record.getMessage(),
        ]
        parts.extend(
            f"{field_name}={value}" for field_name, value in _context_items(record)
        )
        rendered = " ".join(parts)
        if record.exc_info is not None:
            exception = self.formatException(record.exc_info)
            return f"{rendered}\n{exception}"
        return rendered


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        """Format one compact JSON object with deterministic key order."""
        payload: dict[str, object] = {
            "timestamp_utc": _timestamp_utc(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name, value in _context_items(record):
            payload[field_name] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )


class _ProjectStreamHandler(logging.StreamHandler[TextIO]):
    _kinematicweave_owned = True


def _resolve_level(level: int | str) -> int:
    if isinstance(level, bool):
        raise ConfigurationError(f"Invalid logging level: {level!r}")
    if isinstance(level, int):
        if level in _STANDARD_LEVELS:
            return level
        raise ConfigurationError(f"Invalid logging level: {level!r}")
    normalized = level.strip().upper()
    if normalized in _LEVEL_NAMES:
        return _LEVEL_NAMES[normalized]
    raise ConfigurationError(f"Invalid logging level: {level!r}")


def configure_logging(
    *,
    level: int | str = "INFO",
    json_output: bool = False,
    stream: TextIO | None = None,
) -> None:
    """Configure only the project logger hierarchy for explicit output.

    Raises:
        ConfigurationError: If the requested logging level is invalid.
    """
    resolved_level = _resolve_level(level)
    project_logger = logging.getLogger(_PROJECT_LOGGER_NAME)
    for handler in tuple(project_logger.handlers):
        if bool(getattr(handler, "_kinematicweave_owned", False)):
            project_logger.removeHandler(handler)
            handler.close()

    handler = _ProjectStreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(_JsonFormatter() if json_output else _HumanFormatter())
    project_logger.addHandler(handler)
    project_logger.setLevel(resolved_level)
    project_logger.propagate = False


def _normalize_logger_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("logger name must be a string")
    normalized = name.strip()
    if not normalized or normalized == _PROJECT_LOGGER_NAME:
        return _PROJECT_LOGGER_NAME
    prefix = f"{_PROJECT_LOGGER_NAME}."
    if normalized.startswith(prefix):
        suffix = normalized.removeprefix(prefix).strip(".")
        return _PROJECT_LOGGER_NAME if not suffix else f"{prefix}{suffix}"
    return f"{prefix}{normalized.strip('.')}"


def get_logger(
    name: str,
    *,
    context: LogContext | None = None,
) -> logging.LoggerAdapter[logging.Logger]:
    """Return a project-namespaced logger carrying immutable context."""
    if context is not None and not isinstance(context, LogContext):
        raise ValidationError("context must be a LogContext instance")
    logger = logging.getLogger(_normalize_logger_name(name))
    context_items = () if context is None else context._items()
    return logging.LoggerAdapter(
        logger,
        {_CONTEXT_RECORD_KEY: context_items},
    )
