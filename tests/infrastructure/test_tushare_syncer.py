"""Tests for Tushare syncer."""

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.syncer import (
    sync_kline,
    _is_up_to_date,
    _response_to_df,
    _atomic_write_parquet,
)


def test_is_up_to_date_returns_false_for_missing_file(tmp_path):
    path = tmp_path / "000001.parquet"
    assert _is_up_to_date(path, date(2024, 12, 31)) is False


def test_is_up_to_date_returns_true_when_latest_gte_target(tmp_path):
    path = tmp_path / "000001.parquet"
    df = pl.DataFrame({"date": [date(2024, 12, 15), date(2024, 12, 20)]})
    df.write_parquet(path)
    assert _is_up_to_date(path, date(2024, 12, 20)) is True


def test_is_up_to_date_returns_false_when_latest_lt_target(tmp_path):
    path = tmp_path / "000001.parquet"
    df = pl.DataFrame({"date": [date(2024, 12, 15)]})
    df.write_parquet(path)
    assert _is_up_to_date(path, date(2024, 12, 31)) is False


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


class TestSyncKline:
    """Tests for sync_kline using mocked Tushare pro API."""

    def test_skips_up_to_date_code(self, tmp_path):
        bars_dir = tmp_path / "bars"
        bars_dir.mkdir()
        df = pl.DataFrame({
            "date": [date(2024, 12, 25), date(2024, 12, 30), date(2024, 12, 31)],
            "open": [1.0, 2.0, 3.0],
            "high": [1.0, 2.0, 3.0],
            "low": [1.0, 2.0, 3.0],
            "close": [1.0, 2.0, 3.0],
            "volume": [1.0, 2.0, 3.0],
            "amount": [1.0, 2.0, 3.0],
            "adj_factor": [1.0, 1.0, 1.0],
            "is_suspended": [False, False, False],
            "code": ["000001", "000001", "000001"],
        })
        df.write_parquet(bars_dir / "000001.parquet")

        mock_pro = MagicMock()
        with patch("trendradar.infrastructure.tushare.syncer.get_pro", return_value=mock_pro):
            result = sync_kline(
                ["000001"],
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                bars_dir=bars_dir,
            )

        assert result["skipped"] == 1
        assert result["synced"] == 0
        mock_pro.daily.assert_not_called()

    def test_syncs_new_data(self, tmp_path):
        bars_dir = tmp_path / "bars"
        bars_dir.mkdir()

        mock_resp = pd.DataFrame([{
            "ts_code": "000001.SZ",
            "trade_date": "20240110",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "vol": 100000,
            "amount": 1000000,
        }])
        mock_pro = MagicMock()
        mock_pro.daily.return_value = mock_resp

        with patch("trendradar.infrastructure.tushare.syncer.get_pro", return_value=mock_pro):
            result = sync_kline(
                ["000001"],
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                bars_dir=bars_dir,
            )

        assert result["synced"] == 1
        assert result["skipped"] == 0
        assert result["failed"] == 0

        out_file = bars_dir / "000001.parquet"
        assert out_file.exists()
        read_back = pl.read_parquet(out_file)
        assert read_back["date"][0] == date(2024, 1, 10)

    def test_handles_empty_response(self, tmp_path):
        bars_dir = tmp_path / "bars"
        bars_dir.mkdir()

        mock_pro = MagicMock()
        mock_pro.daily.return_value = pd.DataFrame()

        with patch("trendradar.infrastructure.tushare.syncer.get_pro", return_value=mock_pro):
            result = sync_kline(
                ["000001"],
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                bars_dir=bars_dir,
            )

        assert result["empty"] == 1
        assert result["synced"] == 0

    def test_handles_failure(self, tmp_path):
        bars_dir = tmp_path / "bars"
        bars_dir.mkdir()

        mock_pro = MagicMock()
        mock_pro.daily.side_effect = RuntimeError("network error")

        with patch("trendradar.infrastructure.tushare.syncer.get_pro", return_value=mock_pro):
            result = sync_kline(
                ["000001"],
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                bars_dir=bars_dir,
            )

        assert result["failed"] == 1
        assert result["synced"] == 0

    def test_progress_callback(self, tmp_path):
        bars_dir = tmp_path / "bars"
        bars_dir.mkdir()

        mock_resp = pd.DataFrame([{
            "ts_code": "000001.SZ",
            "trade_date": "20240110",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "vol": 100000,
            "amount": 1000000,
        }])
        mock_pro = MagicMock()
        mock_pro.daily.return_value = mock_resp

        progress_calls = []

        def progress(idx, total, code):
            progress_calls.append((idx, total, code))

        with patch("trendradar.infrastructure.tushare.syncer.get_pro", return_value=mock_pro):
            sync_kline(
                ["000001", "000002"],
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                bars_dir=bars_dir,
                progress=progress,
            )

        assert len(progress_calls) == 2
        assert progress_calls[0] == (1, 2, "000001")
        assert progress_calls[1] == (2, 2, "000002")

    def test_cancel_check_breaks_early(self, tmp_path):
        bars_dir = tmp_path / "bars"
        bars_dir.mkdir()

        mock_resp = pd.DataFrame([{
            "ts_code": "000001.SZ",
            "trade_date": "20240110",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "vol": 100000,
            "amount": 1000000,
        }])
        mock_pro = MagicMock()
        mock_pro.daily.return_value = mock_resp

        call_count = [0]

        def cancel_check():
            call_count[0] += 1
            return call_count[0] >= 2

        with patch("trendradar.infrastructure.tushare.syncer.get_pro", return_value=mock_pro):
            result = sync_kline(
                ["000001", "000002", "000003"],
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                bars_dir=bars_dir,
                cancel_check=cancel_check,
            )

        assert result["synced"] <= 1


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


def test_to_ts_code_exchange_suffixes():
    from trendradar.infrastructure.tushare.syncer import _to_ts_code
    assert _to_ts_code("600519") == "600519.SH"   # 沪主板
    assert _to_ts_code("688981") == "688981.SH"   # 科创板
    assert _to_ts_code("900901") == "900901.SH"   # 沪B股
    assert _to_ts_code("920099") == "920099.BJ"   # 北交所新编号（回归：曾误判为 .SH）
    assert _to_ts_code("830799") == "830799.BJ"   # 北交所旧编号
    assert _to_ts_code("000001") == "000001.SZ"   # 深主板
    assert _to_ts_code("300750") == "300750.SZ"   # 创业板
