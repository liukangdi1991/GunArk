"""kline_service 单元测试：404/503 异常映射与 meta 容错（不经过 HTTP 层，F2/M9）。"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from trendradar.app.services.kline_service import get_kline, get_stock_snapshot
from trendradar.domain.market.data_store import LocalParquetMarketStore
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    MarketDataUnavailable,
)
from trendradar.infrastructure.tushare import market_cap


@pytest.fixture(autouse=True)
def _clear_daily_basic_cache():
    """daily_basic 快照是进程级日缓存（日期不可变），测试间必须清空隔离。"""
    market_cap._SNAPSHOT_CACHE.clear()
    market_cap._MARKET_CAP_CACHE.clear()
    yield
    market_cap._SNAPSHOT_CACHE.clear()
    market_cap._MARKET_CAP_CACHE.clear()


def _store(tmp_path) -> LocalParquetMarketStore:
    return LocalParquetMarketStore(tmp_path / "market" / "bars")


def _write_bars(tmp_path, corrupt: bool = False, code: str = "000001") -> None:
    bars_dir = tmp_path / "market" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    if corrupt:
        (bars_dir / f"{code}.parquet").write_bytes(b"not-parquet")
        return
    rows = [{
        "code": code, "date": date(2026, 8, 18),
        "open": 9.9, "high": 10.2, "low": 9.7, "close": 10.0,
        "pre_close": 9.95, "volume": 100.0, "amount": 1000.0,
        "adj_factor": 1.0, "is_suspended": False,
    }]
    pl.DataFrame(rows, schema_overrides={"date": pl.Date}).write_parquet(
        bars_dir / f"{code}.parquet"
    )


def _write_meta(tmp_path, columns: dict) -> None:
    (tmp_path / "market").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(columns).write_parquet(tmp_path / "market" / "stock_meta.parquet")


def test_get_kline_503_when_bars_dir_missing(tmp_path):
    with pytest.raises(MarketDataUnavailable):
        get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)


def test_get_kline_404_when_file_missing(tmp_path):
    _write_bars(tmp_path)
    with pytest.raises(BarsUnavailable):
        get_kline(_store(tmp_path), "999999", KlinePeriod.DAILY, AdjustMode.QFQ)


def test_get_kline_503_when_corrupt_file(tmp_path):
    _write_bars(tmp_path, corrupt=True)
    with pytest.raises(MarketDataUnavailable):
        get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)


def test_get_kline_returns_series_with_meta(tmp_path):
    _write_bars(tmp_path)
    _write_meta(tmp_path, {"code": ["000001"], "name": ["平安银行"], "industry": ["银行"]})
    series = get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)
    assert series.name == "平安银行" and series.industry == "银行"
    assert series.adjust_degraded is False
    assert series.bars["close"].to_list() == [10.0]


def test_get_kline_meta_missing_falls_back_to_code(tmp_path):
    _write_bars(tmp_path)  # 不写 meta
    series = get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)
    assert series.name == "000001" and series.industry is None  # M9：不得按列直接索引


def test_get_kline_meta_without_name_column_falls_back(tmp_path):
    _write_bars(tmp_path)
    _write_meta(tmp_path, {"code": ["000001"]})
    series = get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)
    assert series.name == "000001" and series.industry is None


def test_get_stock_snapshot_maps_daily_basic(tmp_path):
    _write_bars(tmp_path)
    import pandas as pd

    class FakePro:
        def daily_basic(self, trade_date, fields):
            assert fields.startswith("ts_code")
            return pd.DataFrame([
                {"ts_code": "000001.SZ", "circ_mv": 2.214e6, "total_mv": 2.5e6,
                 "turnover_rate": 0.85, "pe_ttm": 5.2, "pb": 0.6},
                {"ts_code": "600519.SH", "circ_mv": 9e6, "total_mv": 9e6,
                 "turnover_rate": 0.3, "pe_ttm": 30.0, "pb": 9.0},
            ])

    store = _store(tmp_path)
    snap = get_stock_snapshot(store, "000001", FakePro())
    assert snap["circ_mv"] == pytest.approx(2.214e6)
    assert snap["turnover_rate"] == pytest.approx(0.85)
    assert snap["trade_date"] == "2026-08-18"


def test_get_stock_snapshot_unknown_code_yields_nulls(tmp_path):
    _write_bars(tmp_path)

    class FakePro:
        def daily_basic(self, trade_date, fields):
            return pd.DataFrame(columns=["ts_code", "circ_mv", "total_mv",
                                         "turnover_rate", "pe_ttm", "pb"])

    snap = get_stock_snapshot(_store(tmp_path), "999999", FakePro())
    assert snap["circ_mv"] is None and snap["code"] == "999999"


def test_get_stock_snapshot_pro_failure_is_graceful(tmp_path):
    _write_bars(tmp_path)

    class BadPro:
        def daily_basic(self, trade_date, fields):
            raise RuntimeError("no token")

    snap = get_stock_snapshot(_store(tmp_path), "000001", BadPro())
    assert snap["circ_mv"] is None and snap["code"] == "000001"
