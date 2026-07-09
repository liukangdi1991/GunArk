"""Tushare 行情数据访问 primitives，供 Web 服务复用。"""
from __future__ import annotations

import datetime as dt
import os
import warnings
from typing import Optional

import pandas as pd
import tushare as ts

warnings.filterwarnings("ignore")

COOLDOWN_SECS = 600
BAN_PATTERNS = (
    "访问频繁", "请稍后", "超过频率", "频繁访问",
    "too many requests", "429",
    "forbidden", "403",
    "max retries exceeded"
)


def _looks_like_ip_ban(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(pat in msg for pat in BAN_PATTERNS)


class RateLimitError(RuntimeError):
    pass


pro: Optional[ts.pro_api] = None


def _resolve_date_arg(value: str) -> str:
    """将命令行日期参数标准化为 YYYYMMDD。"""
    return dt.date.today().strftime("%Y%m%d") if str(value).lower() == "today" else value


def _setup_network_env() -> None:
    """统一设置 Tushare 访问相关环境变量。"""
    os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def _setup_tushare_client() -> None:
    """初始化 Tushare 客户端并写入全局 pro。"""
    ts_token = os.environ.get("TUSHARE_TOKEN")
    if not ts_token:
        raise RuntimeError("缺少 TUSHARE_TOKEN 环境变量，请在 deploy/.env 或运行环境中配置。")
    ts.set_token(ts_token)
    global pro
    pro = ts.pro_api()


def _to_ts_code(code: str) -> str:
    """将 6 位股票代码转换为 Tushare ts_code。"""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def _get_kline_tushare(code: str, start: str, end: str) -> pd.DataFrame:
    """从 Tushare 拉取单只股票日线数据。"""
    ts_code = _to_ts_code(code)
    try:
        df = ts.pro_bar(
            ts_code=ts_code,
            adj="qfq",
            start_date=start,
            end_date=end,
            freq="D",
            api=pro
        )
    except Exception as e:
        if _looks_like_ip_ban(e):
            raise RateLimitError(str(e)) from e
        raise

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"trade_date": "date", "vol": "volume"})[
        ["date", "open", "close", "high", "low", "volume"]
    ].copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def validate(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    if df["date"].isna().any():
        raise ValueError("存在缺失日期！")
    if (df["date"] > pd.Timestamp.today()).any():
        raise ValueError("数据包含未来日期，可能抓取错误！")
    return df
