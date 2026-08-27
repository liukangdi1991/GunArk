"""Tests for Tushare syncer."""

from datetime import date
from unittest.mock import patch

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.syncer import (
    _response_to_df,
    _atomic_write_parquet,
)


def test_response_to_df_handles_empty_pandas():
    empty = pd.DataFrame()
    result = _response_to_df(empty, "000001")
    assert result.is_empty()


def test_response_to_df_renames_columns():
    data = pd.DataFrame([{
        "ts_code": "000001.SZ",
        "trade_date": "20240105",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "vol": 100000,
        "amount": 1000000,
    }])
    result = _response_to_df(data, "000001")
    assert "date" in result.columns
    assert result["date"][0] == date(2024, 1, 5)
    assert result["open"][0] == 10.0
    assert result["high"][0] == 11.0
    assert result["low"][0] == 9.5
    assert result["close"][0] == 10.5
    assert result["volume"][0] == 100000.0
    assert result["code"][0] == "000001"


def test_atomic_write_parquet_writes_file(tmp_path):
    target = tmp_path / "data.parquet"
    df = pl.DataFrame({"x": [1, 2, 3]})
    _atomic_write_parquet(df, target)
    assert target.exists()
    read_back = pl.read_parquet(target)
    assert read_back["x"].to_list() == [1, 2, 3]


def test_atomic_write_parquet_overwrites_existing(tmp_path):
    target = tmp_path / "data.parquet"
    old = pl.DataFrame({"x": [9, 9]})
    old.write_parquet(target)

    new = pl.DataFrame({"x": [1, 2, 3]})
    _atomic_write_parquet(new, target)

    read_back = pl.read_parquet(target)
    assert read_back["x"].to_list() == [1, 2, 3]


def test_atomic_write_parquet_cleans_up_tmp_on_error(tmp_path):
    target = tmp_path / "data.parquet"
    target.write_text("old content")

    with patch("polars.DataFrame.write_parquet", side_effect=RuntimeError("boom")):
        try:
            _atomic_write_parquet(pl.DataFrame({"x": [1]}), target)
        except RuntimeError:
            pass

    assert target.read_text() == "old content"


# --- Rate-limit root-cause fixes (Task 4) ---
def test_retry_consumes_bucket_tokens():
    from datetime import date
    from unittest.mock import MagicMock, patch

    from trendradar.infrastructure.tushare.rate_limit import TokenBucket
    from trendradar.infrastructure.tushare.syncer import _fetch_with_retry

    pro = MagicMock()
    pro.daily.side_effect = [Exception("boom"), None]  # first attempt fails
    bucket = TokenBucket(rate_per_min=1, burst=1)       # 1 token only
    with patch("trendradar.infrastructure.tushare.syncer.time.sleep"):
        # cancel_check=True makes acquire return False immediately once the
        # single token is spent (no 60s real-time wait).
        _fetch_with_retry(pro, "000001", date(2026, 1, 1), date(2026, 1, 31), 3,
                          bucket=bucket, cancel_check=lambda: True)
    assert pro.daily.call_count == 1  # second attempt starved: no token left


def test_rate_limit_error_abandons_without_retry():
    from datetime import date
    from unittest.mock import MagicMock, patch

    from trendradar.infrastructure.tushare.syncer import _fetch_with_retry

    pro = MagicMock()
    pro.daily.side_effect = [
        Exception("抱歉，您访问接口(daily)频率超限(300次/分钟)，具体频次详情：https://tushare.pro/document/1?doc_id=108。")
    ]
    with patch("trendradar.infrastructure.tushare.syncer.time.sleep"):
        result = _fetch_with_retry(pro, "000001", date(2026, 1, 1), date(2026, 1, 31), 3)
    assert result is None
    assert pro.daily.call_count == 1  # no hot retry on per-window rate limit


def test_to_ts_code_strips_exchange_suffix():
    """API 直调可传入带后缀代码（Tushare 官方文档示例格式），不得拼出 X.SH.SH 非法码污染 retry 队列。"""
    from trendradar.infrastructure.tushare.syncer import _to_ts_code
    assert _to_ts_code("600519.SH") == "600519.SH"  # 已带正确后缀 → 幂等
    assert _to_ts_code("000001.SZ") == "000001.SZ"
    assert _to_ts_code("830799.BJ") == "830799.BJ"
    # 错误后缀按号码段重新判定，不信任输入后缀：
    assert _to_ts_code("600519.sz") == "600519.SH"
    assert _to_ts_code("600519") == "600519.SH"    # 纯 6 位码回归不受影响


def test_to_ts_code_exchange_suffixes():
    from trendradar.infrastructure.tushare.syncer import _to_ts_code
    assert _to_ts_code("600519") == "600519.SH"   # 沪主板
    assert _to_ts_code("688981") == "688981.SH"   # 科创板
    assert _to_ts_code("900901") == "900901.SH"   # 沪B股
    assert _to_ts_code("920099") == "920099.BJ"   # 北交所新编号（回归：曾误判为 .SH）
    assert _to_ts_code("830799") == "830799.BJ"   # 北交所旧编号
    assert _to_ts_code("000001") == "000001.SZ"   # 深主板
    assert _to_ts_code("300750") == "300750.SZ"   # 创业板
