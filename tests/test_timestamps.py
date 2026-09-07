"""Tests for canonical UTC timestamp formatting."""

from datetime import UTC, datetime, timedelta, timezone
import re

import pytest

from kinematicweave.errors import ValidationError
from kinematicweave.timestamps import format_utc_timestamp, utc_now_timestamp


def test_aware_utc_datetime_has_exact_microseconds() -> None:
    """UTC values use the fixed six-digit ISO 8601 representation."""
    value = datetime(2025, 1, 2, 3, 4, 5, 6789, tzinfo=UTC)

    assert format_utc_timestamp(value) == "2025-01-02T03:04:05.006789Z"


def test_non_utc_datetime_is_converted() -> None:
    """Aware datetimes in other offsets are converted to UTC."""
    source_zone = timezone(timedelta(hours=2, minutes=30))
    value = datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=source_zone)

    assert format_utc_timestamp(value) == "2025-01-02T00:34:05.123456Z"


def test_naive_datetime_is_rejected() -> None:
    """Naive datetimes cannot silently assume a local timezone."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        format_utc_timestamp(datetime(2025, 1, 2, 3, 4, 5))


def test_utc_now_timestamp_is_parseable() -> None:
    """Current timestamps use the fixed UTC form and parse successfully."""
    timestamp = utc_now_timestamp()

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", timestamp)
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo == UTC


def test_public_exports_are_exact() -> None:
    """The module exposes only its intended public functions."""
    from kinematicweave import timestamps

    assert timestamps.__all__ == ["format_utc_timestamp", "utc_now_timestamp"]
