"""Tushare 抓取原语（自旧 syncer.py 迁移）+ 失败分类。见 spec §3.1/§3.7。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from trendradar.domain.market.sync.spec import FailureKind, is_bse_code

logger = logging.getLogger(__name__)

IP_BAN_ERROR_MSG = "每分钟最多访问该接口"
RATE_LIMIT_MSG = "频率超限"

ENV_MARKERS = (
    RATE_LIMIT_MSG,
    "最多访问该接口",
    "Connection",
    "Timeout",
    "timed out",
    "NewConnectionError",
    "502",
    "503",
    "504",
)
CODE_MARKERS = ("参数错误", "无此股票", "invalid parameter", "not found")

# 北交所改由 .BJ 后缀识别（stocklist.py），此处仅保留 gem/star
EXCLUDE_BOARD_PREFIXES = {"gem": ("300", "301"), "star": ("688", "689")}


@dataclass(frozen=True)
class FetchResult:
    df: pl.DataFrame | None
    kind: FailureKind | None  # None = 成功且有数据
    error: str | None = None


def classify_error(msg: str) -> FailureKind:
    for m in ENV_MARKERS:
        if m in msg:
            return FailureKind.ENV
    for m in CODE_MARKERS:
        if m in msg:
            return FailureKind.CODE
    return FailureKind.UNKNOWN


def _to_ts_code(code: str) -> str:
    # 与旧实现一致：先去后缀再补零，避免 "600519.SH" → "600519.SH.SH"
    code = str(code).split(".")[0].zfill(6)
    if code.startswith(("60", "68", "900", "901")):
        return f"{code}.SH"
    elif is_bse_code(code):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _response_to_df(resp, code: str) -> pl.DataFrame:
    if resp is None or not resp.to_dict(orient="list"):
        return pl.DataFrame()

    df = pl.DataFrame(resp.to_dict(orient="list"))

    column_map = {"trade_date": "date", "vol": "volume"}
    df = df.rename({k: v for k, v in column_map.items() if k in df.columns})

    if "date" in df.columns:
        df = df.with_columns(
            pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
        )

    for col_name in ["open", "high", "low", "close", "volume", "amount", "pre_close"]:
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Float64))

    df = df.with_columns(
        pl.lit(code).cast(pl.Utf8).alias("code"),
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
    )

    cols = ["code", "date", "open", "high", "low", "close", "volume",
            "amount", "pre_close", "adj_factor", "is_suspended"]
    df = df.select([c for c in cols if c in df.columns])
    return df.sort("date")


class AdjFactorUnavailable(RuntimeError):
    """adj_factor 有响应但取不到可用因子。

    静默保留占位 1.0 不可接受：1.0 会击穿下游 _qfq_scale 的守卫（`latest <= 0`
    不成立），指标全算在原价上；而 1.0 同时是「上市以来从未除权」的合法值，
    事后无法从数据本身区分故障与正常。部分匹配更不能留：真因子与 1.0 混在
    同一序列里会造出一个巨大的假跳空，比整列 1.0 更糟。
    """


def _attach_adj_factor(df: pl.DataFrame, adj_df) -> pl.DataFrame:
    """按 (code, date) 合并真实复权因子，覆盖占位 1.0；取不到就抛，不降级。"""
    if df.is_empty():
        return df
    if adj_df is None:
        raise AdjFactorUnavailable("adj_factor 未取到响应")
    if isinstance(adj_df, pl.DataFrame):
        adj = adj_df
    else:
        if not adj_df.to_dict(orient="list"):
            raise AdjFactorUnavailable("adj_factor 响应为空")
        adj = pl.DataFrame(adj_df.to_dict(orient="list"))
    if adj.is_empty() or "adj_factor" not in adj.columns:
        raise AdjFactorUnavailable("adj_factor 响应为空")
    if "trade_date" in adj.columns:
        adj = adj.with_columns(
            pl.col("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d").alias("date")
        )
    if "ts_code" in adj.columns:
        adj = adj.with_columns(pl.col("ts_code").str.slice(0, 6).alias("code"))
    adj = adj.select(["code", "date", "adj_factor"]).rename({"adj_factor": "_adj"})
    df = df.join(adj, on=["code", "date"], how="left")
    unmatched = df["_adj"].null_count()
    if unmatched:
        raise AdjFactorUnavailable(
            f"adj_factor 覆盖不全：{unmatched}/{df.height} 行未匹配到真实因子"
        )
    return df.with_columns(
        pl.coalesce([pl.col("_adj"), pl.col("adj_factor")]).alias("adj_factor")
    ).drop("_adj")


def _align_columns(local: pl.DataFrame, incoming: pl.DataFrame) -> pl.DataFrame:
    """合并前把 local 对齐到 incoming 的列集与列序（语义与旧实现一致）。"""
    for col in incoming.columns:
        if col not in local.columns:
            local = local.with_columns(
                pl.lit(None).cast(incoming.schema[col]).alias(col)
            )
    return local.select(incoming.columns)


def shard_ranges(start: date, end: date, max_rows: int = 5500) -> list:
    """Split [start, end] into date ranges each estimated under max_rows."""
    total_days = (end - start).days + 1
    est_trade_days = int(total_days / 7 * 5)
    if est_trade_days <= max_rows:
        return [(start, end)]
    shards = -(-est_trade_days // max_rows)
    span = -(-total_days // shards)
    ranges = []
    cur = start
    while cur <= end:
        seg_end = min(end, cur + timedelta(days=span - 1))
        ranges.append((cur, seg_end))
        cur = seg_end + timedelta(days=1)
    return ranges


def _exclude_prefixes(exclude_boards) -> tuple | None:
    if not exclude_boards:
        return None
    prefixes = []
    for b in exclude_boards:
        prefixes.extend(EXCLUDE_BOARD_PREFIXES.get(b, ()))
    return tuple(prefixes) or None


def filter_excluded_boards(df: pl.DataFrame, exclude_boards) -> pl.DataFrame:
    prefixes = _exclude_prefixes(exclude_boards)
    if prefixes is None or df.is_empty():
        return df
    expr = ~pl.col("code").str.starts_with(prefixes[0])
    for p in prefixes[1:]:
        expr = expr & ~pl.col("code").str.starts_with(p)
    return df.filter(expr)


def _retry_env_or_wait(kind: FailureKind, msg: str, attempt: int) -> bool:
    """是否继续重试：每分钟上限冷却 600s；其余 env（网络/5xx）与非 env 均指数退避重试。

    频率超限类 env 由调用方在调用前直接返回（不热重试同一窗口）。
    """
    if kind is FailureKind.ENV and IP_BAN_ERROR_MSG in msg:
        logger.warning("IP per-minute ban, cooling 600s (attempt %d)", attempt + 1)
        time.sleep(600)
        return True
    time.sleep(2 ** attempt)
    return True


def fetch_code_range(
    pro, code: str, start: date, end: date,
    bucket=None, cancel_check=None, max_retries: int = 3,
) -> FetchResult:
    """按股票拉 [start, end]（daily + adj_factor），返回带分类的结果。"""
    ts_code = _to_ts_code(code)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    last_error = ""
    for attempt in range(max_retries):
        if bucket is not None and not bucket.acquire(timeout=60.0, cancel_check=cancel_check):
            return FetchResult(None, FailureKind.ENV, "令牌桶超时或被取消")
        try:
            resp = pro.daily(ts_code=ts_code, start_date=start_s, end_date=end_s, freq="D")
            df = _response_to_df(resp, code)
            adj = pro.adj_factor(ts_code=ts_code, start_date=start_s, end_date=end_s)
            return FetchResult(_attach_adj_factor(df, adj), None)
        except AdjFactorUnavailable as e:
            # 本调用只为单只股票取因子，空/缺行就是这只股自己的问题 ⇒ code（计次，
            # 3 轮后出列，面板带缺口需显式确认）。判 env 则永不入账 sync_skipped，
            # bars 里留一个谁也不知道的洞。响应格式是好的，热重试也换不出数据。
            return FetchResult(None, FailureKind.CODE, str(e))
        except Exception as e:
            last_error = str(e)
            kind = classify_error(last_error)
            if kind is FailureKind.ENV and RATE_LIMIT_MSG in last_error:
                logger.warning("Rate limit on %s: %s", code, last_error)
                return FetchResult(None, kind, last_error)
            if not _retry_env_or_wait(kind, last_error, attempt):
                return FetchResult(None, kind, last_error)
    return FetchResult(None, classify_error(last_error) if last_error else FailureKind.UNKNOWN,
                       last_error or "retries exhausted")


def _normalize_day_df(resp) -> pl.DataFrame:
    if isinstance(resp, pl.DataFrame):
        df = resp
    else:
        df = pl.DataFrame(resp.to_dict(orient="list"))
    column_map = {"trade_date": "date", "vol": "volume"}
    df = df.rename({k: v for k, v in column_map.items() if k in df.columns})
    if df.is_empty():
        return df
    df = df.with_columns(
        pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
    )
    for col in ["open", "high", "low", "close", "volume", "amount", "pre_close"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64))
    df = df.with_columns(
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
        pl.col("ts_code").str.slice(0, 6).alias("code"),
    )
    cols = ["code", "date", "open", "high", "low", "close", "volume",
            "amount", "pre_close", "adj_factor", "is_suspended"]
    df = df.select([c for c in cols if c in df.columns]).sort("date")
    return df


def fetch_day_by_date(
    pro, day: date, bucket=None, cancel_check=None, max_retries: int = 3,
) -> FetchResult:
    """按交易日拉全市场（daily + adj_factor），各重试 max_retries 次。"""
    day_s = day.strftime("%Y%m%d")
    last_error = ""
    for attempt in range(max_retries):
        if bucket is not None and not bucket.acquire(timeout=60.0, cancel_check=cancel_check):
            return FetchResult(None, FailureKind.ENV, "令牌桶超时或被取消")
        try:
            resp = pro.daily(trade_date=day_s)
            if resp is None:
                return FetchResult(None, FailureKind.UNKNOWN, "daily 返回 None")
            if (isinstance(resp, pl.DataFrame) and resp.is_empty()) or (
                not isinstance(resp, pl.DataFrame) and not resp.to_dict(orient="list")
            ):
                return FetchResult(pl.DataFrame(), FailureKind.OK_EMPTY, None)
            df = _normalize_day_df(resp)
            adj = pro.adj_factor(trade_date=day_s)
            return FetchResult(_attach_adj_factor(df, adj), None)
        except AdjFactorUnavailable as e:
            # 这里是按 trade_date 一次取全市场，空响应说明整天的因子表还没出
            # （发布时序问题，下轮自己就好）⇒ env，不计次。与 fetch_code_range
            # 的 code 相反：那边一次只取一只，空就是那只股自己的事。
            # 两类都会让 run_incremental 整批中止，绝不写占位 1.0。
            return FetchResult(None, FailureKind.ENV, str(e))
        except Exception as e:
            last_error = str(e)
            kind = classify_error(last_error)
            if kind is FailureKind.ENV and RATE_LIMIT_MSG in last_error:
                logger.warning("Rate limit on day %s: %s", day, last_error)
                return FetchResult(None, kind, last_error)
            if not _retry_env_or_wait(kind, last_error, attempt):
                return FetchResult(None, kind, last_error)
    return FetchResult(None, classify_error(last_error) if last_error else FailureKind.UNKNOWN,
                       last_error or "retries exhausted")
