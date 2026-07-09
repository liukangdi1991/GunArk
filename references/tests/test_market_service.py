from __future__ import annotations


def test_kline_pdf_to_polars_converts_without_using_from_pandas(monkeypatch):
    import pandas as pd
    import polars as pl

    from web.services import market_service

    def fail_from_pandas(*_args, **_kwargs):
        raise RuntimeError("from_pandas should not be used")

    monkeypatch.setattr(pl, "from_pandas", fail_from_pandas)
    pdf = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-04-02", "2026-04-01"]),
            "open": [10.1, 10.0],
            "close": [10.2, 10.1],
            "high": [10.3, 10.2],
            "low": [9.9, 9.8],
            "volume": [1234.0, 1000.0],
        }
    )

    result = market_service._kline_pdf_to_polars(pdf)

    assert result.schema["date"] == pl.Date
    assert result["date"].dt.strftime("%Y-%m-%d").to_list() == ["2026-04-01", "2026-04-02"]
    assert result["volume"].to_list() == [1000.0, 1234.0]


def test_fetch_market_data_stops_before_fetching_stock_when_cancelled(tmp_path, monkeypatch):
    import datetime as dt

    from web.schemas.market import FetchMarketRequest
    from web.services import market_service

    data_dir = tmp_path / "db"
    stocklist = tmp_path / "stocklist.csv"
    stocklist.write_text(
        "ts_code,symbol,name,area,industry\n000001.SZ,000001,平安银行,深圳,银行\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(market_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(market_service, "STOCKLIST", stocklist)
    monkeypatch.setattr(market_service, "_setup_network_env", lambda: None)
    monkeypatch.setattr(market_service, "_setup_tushare_client", lambda: None)
    monkeypatch.setattr(market_service, "_refresh_stocklist_from_tushare", lambda log=None: {"refreshed": False, "count": 1})
    monkeypatch.setattr(market_service, "_expected_latest_trade_date", lambda log=None: dt.date(2026, 4, 3))
    monkeypatch.setattr(market_service.trading_calendar, "ensure_coverage", lambda **_kwargs: {})

    fetched = {"count": 0}

    def fetch_one(*_args, **_kwargs):
        fetched["count"] += 1
        return "ok"

    monkeypatch.setattr(market_service, "_fetch_one_with_log", fetch_one)

    try:
        market_service.fetch_market_data(
            FetchMarketRequest(start="20260401", end="20260403"),
            should_cancel=lambda: True,
        )
    except market_service.MarketDataCancelled:
        pass
    else:
        raise AssertionError("fetch_market_data should raise MarketDataCancelled")

    assert fetched["count"] == 0
