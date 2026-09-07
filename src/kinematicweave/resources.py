"""Cross-platform process resource measurements."""

from __future__ import annotations

import importlib
import sys
from typing import Any

__all__ = ["peak_process_rss_bytes"]


def peak_process_rss_bytes() -> int:
    """Return peak resident memory in bytes, or zero when unavailable.

    The standard-library ``resource`` module reports bytes on macOS and
    kibibytes on other supported Unix platforms. Windows does not provide this
    module, so callers receive the explicit sentinel value zero rather than
    failing during import.
    """
    try:
        resource: Any = importlib.import_module("resource")
    except ModuleNotFoundError:
        return 0
    usage = resource.getrusage(resource.RUSAGE_SELF)
    scale = 1 if sys.platform == "darwin" else 1024
    return max(0, int(usage.ru_maxrss) * scale)
