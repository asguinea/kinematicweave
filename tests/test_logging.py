"""Tests for explicit project-owned structured logging."""

from collections.abc import Iterator
import importlib
from io import StringIO
import json
import logging
import re
from typing import cast

import pytest

from kinematicweave.errors import ConfigurationError, ValidationError
import kinematicweave.logging as project_logging


@pytest.fixture(autouse=True)
def restore_logging_state() -> Iterator[None]:
    """Restore root and project logging state after every test."""
    root_logger = logging.getLogger()
    project_logger = logging.getLogger("kinematicweave")
    root_handlers = tuple(root_logger.handlers)
    root_level = root_logger.level
    project_handlers = tuple(project_logger.handlers)
    project_level = project_logger.level
    project_propagate = project_logger.propagate
    project_disabled = project_logger.disabled

    yield

    for handler in tuple(project_logger.handlers):
        project_logger.removeHandler(handler)
        if handler not in project_handlers:
            handler.close()
    for handler in project_handlers:
        project_logger.addHandler(handler)
    project_logger.setLevel(project_level)
    project_logger.propagate = project_propagate
    project_logger.disabled = project_disabled

    for handler in tuple(root_logger.handlers):
        root_logger.removeHandler(handler)
        if handler not in root_handlers:
            handler.close()
    for handler in root_handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(root_level)


def test_import_does_not_modify_root_handlers() -> None:
    """Importing the logging module leaves root handlers untouched."""
    root_logger = logging.getLogger()
    handlers_before = tuple(root_logger.handlers)

    importlib.reload(project_logging)

    assert tuple(root_logger.handlers) == handlers_before


def test_human_readable_output_includes_context() -> None:
    """Human output contains UTC metadata and present ordered context."""
    stream = StringIO()
    context = project_logging.LogContext(
        run_id="run-1",
        scenario_id="scenario-2",
        stage="load",
    )
    project_logging.configure_logging(stream=stream)

    project_logging.get_logger("config", context=context).info("loaded")

    output = stream.getvalue()
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T[^ ]+Z INFO kinematicweave\.config loaded "
        r"run_id=run-1 scenario_id=scenario-2 stage=load\n",
        output,
    )


def test_json_output_has_deterministic_key_order() -> None:
    """JSON output uses stable core and context key ordering."""
    stream = StringIO()
    context = project_logging.LogContext(
        run_id="run-1",
        experiment_id="experiment-2",
        tile_id="tile-3",
    )
    project_logging.configure_logging(json_output=True, stream=stream)

    project_logging.get_logger("runtime", context=context).warning("paused")

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert list(payload) == [
        "timestamp_utc",
        "level",
        "logger",
        "message",
        "run_id",
        "experiment_id",
        "tile_id",
    ]
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "kinematicweave.runtime"
    assert payload["message"] == "paused"


def test_context_omits_none_and_trims_values() -> None:
    """Context values are trimmed and absent values are omitted."""
    context = project_logging.LogContext(
        run_id="  run-1  ",
        method_id=None,
        stage="  encode  ",
    )
    stream = StringIO()
    project_logging.configure_logging(json_output=True, stream=stream)

    project_logging.get_logger("codecs", context=context).info("encoded")

    payload = json.loads(stream.getvalue())
    assert context.run_id == "run-1"
    assert context.stage == "encode"
    assert payload["run_id"] == "run-1"
    assert payload["stage"] == "encode"
    assert "method_id" not in payload


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_context_value_is_rejected(value: str) -> None:
    """Empty and whitespace-only context values fail validation."""
    with pytest.raises(ValidationError, match="run_id"):
        project_logging.LogContext(run_id=value)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("config", "kinematicweave.config"),
        ("kinematicweave.config", "kinematicweave.config"),
        ("", "kinematicweave"),
        ("   ", "kinematicweave"),
        ("kinematicweave", "kinematicweave"),
    ],
)
def test_logger_name_normalization(name: str, expected: str) -> None:
    """Logger names always remain under the project namespace."""
    assert project_logging.get_logger(name).logger.name == expected


def test_integer_level_handling() -> None:
    """Standard integer levels control project output."""
    stream = StringIO()
    project_logging.configure_logging(level=logging.WARNING, stream=stream)
    logger = project_logging.get_logger("metrics")

    logger.info("hidden")
    logger.warning("visible")

    assert "hidden" not in stream.getvalue()
    assert "visible" in stream.getvalue()


def test_case_insensitive_string_level() -> None:
    """Standard string levels are accepted case-insensitively."""
    stream = StringIO()
    project_logging.configure_logging(level="dEbUg", stream=stream)

    project_logging.get_logger("runtime").debug("visible")

    assert "DEBUG kinematicweave.runtime visible" in stream.getvalue()


@pytest.mark.parametrize("level", ["TRACE", 15, True])
def test_invalid_level_is_rejected(level: object) -> None:
    """Unknown names, nonstandard integers, and booleans are rejected."""
    with pytest.raises(ConfigurationError, match="Invalid logging level"):
        project_logging.configure_logging(
            level=cast(int | str, level),
            stream=StringIO(),
        )


def test_repeated_configuration_does_not_duplicate_messages() -> None:
    """Repeated explicit configuration replaces project handlers."""
    stream = StringIO()
    project_logging.configure_logging(stream=stream)
    project_logging.configure_logging(stream=stream)

    project_logging.get_logger("config").info("once")

    assert stream.getvalue().count("once") == 1
    assert len(stream.getvalue().splitlines()) == 1


def test_project_propagation_and_root_state() -> None:
    """Configuration is isolated to the non-propagating project logger."""
    root_logger = logging.getLogger()
    root_handlers = tuple(root_logger.handlers)
    root_level = root_logger.level

    project_logging.configure_logging(stream=StringIO())

    assert logging.getLogger("kinematicweave").propagate is False
    assert tuple(root_logger.handlers) == root_handlers
    assert root_logger.level == root_level


@pytest.mark.parametrize("json_output", [False, True])
def test_exception_information_is_included(json_output: bool) -> None:
    """Human and JSON records include supplied exception information."""
    stream = StringIO()
    project_logging.configure_logging(
        json_output=json_output,
        stream=stream,
    )
    logger = project_logging.get_logger("runtime")

    try:
        raise RuntimeError("broken")
    except RuntimeError:
        logger.exception("failed")

    output = stream.getvalue()
    assert "RuntimeError: broken" in output
    if json_output:
        payload = json.loads(output)
        assert "RuntimeError: broken" in payload["exception"]


def test_context_cannot_be_overwritten_by_record_extra() -> None:
    """Ordinary record extras cannot replace approved context."""
    stream = StringIO()
    context = project_logging.LogContext(run_id="approved")
    project_logging.configure_logging(json_output=True, stream=stream)
    logger = project_logging.get_logger("runtime", context=context)

    logger.info(
        "message",
        extra={
            "run_id": "replacement",
            "_kinematicweave_context": (("run_id", "replacement"),),
        },
    )

    payload = json.loads(stream.getvalue())
    assert payload["run_id"] == "approved"


def test_logging_public_exports() -> None:
    """The logging module exports only its intended public API."""
    assert project_logging.__all__ == [
        "LogContext",
        "configure_logging",
        "get_logger",
    ]
