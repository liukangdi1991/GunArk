from __future__ import annotations

from pathlib import Path

from core.runtime import resource_root, runtime_root
from core.storage import AppStorage


ROOT = runtime_root()
RESOURCE_ROOT = resource_root()
FRONTEND_DIST_DIR = RESOURCE_ROOT / "frontend" / "dist"
STORAGE_ROOT = ROOT / "storage"

storage = AppStorage(STORAGE_ROOT)
