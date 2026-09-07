"""Tests for cross-platform process resource measurements."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

import kinematicweave.resources as resources


def test_peak_rss_is_nonnegative() -> None:
    assert resources.peak_process_rss_bytes() >= 0


def test_peak_rss_returns_zero_when_resource_module_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> None:
        raise ModuleNotFoundError

    monkeypatch.setattr(importlib, "import_module", missing)
    assert resources.peak_process_rss_bytes() == 0


def test_peak_rss_applies_unix_kibibyte_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = SimpleNamespace(
        RUSAGE_SELF=0,
        getrusage=lambda _scope: SimpleNamespace(ru_maxrss=123),
    )
    monkeypatch.setattr(importlib, "import_module", lambda _name: module)
    monkeypatch.setattr(sys, "platform", "linux")
    assert resources.peak_process_rss_bytes() == 123 * 1024
