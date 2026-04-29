from __future__ import annotations

from pathlib import Path

from backtest.storage import BacktestStorage


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"
STORAGE_ROOT = ROOT / "storage"

storage = BacktestStorage(STORAGE_ROOT)
