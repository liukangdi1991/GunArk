from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import polars as pl

from core.runtime import runtime_root

# ─────────────────────────── 股票名称 ─────────────────────────── #

def _load_stock_names() -> Dict[str, str]:
    """从 stocklist.csv 加载 code→name 映射"""
    stocklist_path = runtime_root() / "stocklist.csv"
    if not stocklist_path.exists():
        return {}
    try:
        df = pl.read_csv(stocklist_path, columns=["symbol", "name"])
        return {str(row[0]).zfill(6): str(row[1]) for row in df.iter_rows()}
    except Exception:
        return {}

_stock_name_cache: Optional[Dict[str, str]] = None

def get_stock_names() -> Dict[str, str]:
    global _stock_name_cache
    if _stock_name_cache is None:
        _stock_name_cache = _load_stock_names()
    return _stock_name_cache
