"""kline_service 单元测试：404/503 异常映射与 meta 容错（不经过 HTTP 层，F2/M9）。"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from trendradar.app.services.kline_service import get_kline
from trendradar.domain.market.data_store import LocalParquetMarketStore
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    MarketDataUnavailable,
)


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
