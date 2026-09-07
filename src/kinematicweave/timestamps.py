"""UTC timestamp formatting utilities for provenance metadata."""

from datetime import UTC, datetime

from kinematicweave.errors import ValidationError

__all__ = [
    "format_utc_timestamp",
    "utc_now_timestamp",
]


def format_utc_timestamp(value: datetime) -> str:
    """Format a timezone-aware datetime as ISO 8601 UTC with microseconds.

    Raises:
        ValidationError: If the value is not a timezone-aware datetime.
    """
    if not isinstance(value, datetime):
        raise ValidationError("timestamp must be a datetime")
    if value.utcoffset() is None:
        raise ValidationError("timestamp must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_now_timestamp() -> str:
    """Return the current timezone-aware UTC time in canonical text form."""
    return format_utc_timestamp(datetime.now(UTC))
