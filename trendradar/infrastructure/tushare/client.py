"""Tushare client initialization + 响应边界归一。"""

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


def response_lists(resp) -> dict:
    """Tushare 响应 → dict-of-lists，缺失值（float nan）归一为 None。

    pandas 3.0 起 str dtype 列的 to_dict 产出 nan 而非 None；polars 按列首
    样本推断类型时把 nan 当 float，整列被推断成 Float64，撞上后续字符串
    值直接 TypeError（"building Series of type Float64 ... 吉林"）。在
    pandas→polars 边界统一归一，缺失值始终是 None。
    """
    if resp is None:
        return {}
    data = resp.to_dict(orient="list")
    return {
        col: [None if isinstance(v, float) and v != v else v for v in values]
        for col, values in data.items()
    }
