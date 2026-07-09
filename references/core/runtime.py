from __future__ import annotations

import os
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    """Read-only application resources, e.g. bundled frontend assets."""
    configured = os.environ.get("TREND_RADAR_RESOURCE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(str(bundled_root)).resolve()
    return SOURCE_ROOT


def runtime_root() -> Path:
    """Mutable runtime home containing db, storage, configs.json and stocklist.csv."""
    configured = os.environ.get("TREND_RADAR_RUNTIME_ROOT") or os.environ.get("TREND_RADAR_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT
