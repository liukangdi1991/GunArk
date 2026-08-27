"""Tushare client initialization."""

from __future__ import annotations

import os

import tushare as ts


def get_pro():
    """Initialize and return Tushare pro client using TUSHARE_TOKEN env var."""
    token = os.environ.get("TUSHARE_TOKEN", "")
    ts.set_token(token)
    return ts.pro_api()
