"""Tushare client initialization."""

from __future__ import annotations

import os

import tushare as ts


def get_pro():
    """Initialize and return Tushare pro client using TUSHARE_TOKEN env var.

    env 未设置时跳过 set_token，保留 tushare 自存凭据（~/tk.csv）——
    无条件 set_token("") 会把已存 token 清空（2026-09-14 实际事故，加固）。
    两者皆无时 pro_api 首次调用会抛错，由调用方兜底。
    """
    token = os.environ.get("TUSHARE_TOKEN", "")
    if token:
        ts.set_token(token)
    return ts.pro_api()
