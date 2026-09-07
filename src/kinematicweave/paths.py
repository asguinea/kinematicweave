"""Validation and normalization for repository-relative paths."""

from pathlib import Path, PurePosixPath, PureWindowsPath

from kinematicweave.errors import ValidationError

__all__ = [
    "normalize_relative_path",
    "relative_path_text",
]


def normalize_relative_path(path: str | Path) -> Path:
    """Return a normalized repository-relative path.

    Raises:
        ValidationError: If the path is empty, absolute, or traverses a parent.
    """
    if not isinstance(path, (str, Path)):
        raise ValidationError("path must be a string or Path")
    text = str(path)
    if not text:
        raise ValidationError("path must be nonempty")

    normalized_text = text.replace("\\", "/")
    posix_path = PurePosixPath(normalized_text)
    windows_path = PureWindowsPath(text)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
    ):
        raise ValidationError("path must be repository-relative")
    if ".." in posix_path.parts:
        raise ValidationError("path must not contain parent traversal")
    if not posix_path.parts:
        raise ValidationError("path must be nonempty")
    return Path(*posix_path.parts)


def relative_path_text(path: str | Path) -> str:
    """Return a normalized repository-relative path with forward slashes.

    Raises:
        ValidationError: If the path is empty, absolute, or traverses a parent.
    """
    return normalize_relative_path(path).as_posix()
