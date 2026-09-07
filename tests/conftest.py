"""Shared fixtures that keep the test suite independent of host capacity."""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import shutil
from typing import NamedTuple

import pytest


class _DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.fixture(scope="session", autouse=True)
def stable_test_environment() -> Iterator[None]:
    """Prevent unrelated host utilization from invalidating small test writes."""
    monkeypatch = pytest.MonkeyPatch()
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", repository_root.as_posix())

    actual_disk_usage = shutil.disk_usage
    current = actual_disk_usage(".")
    minimum_free = current.total // 5
    if current.free < minimum_free:

        def disk_usage(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        ) -> _DiskUsage:
            actual = actual_disk_usage(path)
            free = max(actual.free, actual.total // 5)
            return _DiskUsage(actual.total, actual.total - free, free)

        monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    yield
    monkeypatch.undo()
