import os
import sys
from pathlib import Path


def source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    if env := os.environ.get("TREND_RADAR_RUNTIME_ROOT"):
        return Path(env)
    if env := os.environ.get("TREND_RADAR_HOME"):
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    return source_root()


def storage_root() -> Path:
    return runtime_root() / "storage"
