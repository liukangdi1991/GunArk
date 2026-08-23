"""Tushare client initialization."""

from __future__ import annotations

import os

import tushare as ts


def get_pro():
    """Initialize and return Tushare pro client using TUSHARE_TOKEN env var."""
    token = os.environ.get("TUSHARE_TOKEN", "")
    ts.set_token(token)
    return ts.pro_api()


def validate_token() -> bool:
    """Check if token is valid by calling a lightweight API."""
    try:
        pro = get_pro()
        result = pro.trade_cal(exchange="SSE", start_date="20200101", end_date="20200101")
        return result is not None and bool(result.to_dict(orient="list"))
    except Exception:
        return False
