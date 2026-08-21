"""Sync completion markers.

sync_done.json        — trading days fully synced (all stocks), day dimension.
sync_retry_codes.json — stocks that failed in by-stock mode; retried as a
                        subset on the next by-stock run.
Both atomically written.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_sync_done(path: Path, dates: set[date]) -> None:
    _atomic_write_json(path, {"dates": sorted(d.isoformat() for d in dates)})


def load_sync_done(path: Path) -> set[date]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {date.fromisoformat(d) for d in data.get("dates", [])}
    except Exception:
        return set()


def save_retry_codes(path: Path, codes: list[str]) -> None:
    _atomic_write_json(path, {"codes": sorted(set(codes))})


def load_retry_codes(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return sorted(set(data.get("codes", [])))
    except Exception:
        return []
