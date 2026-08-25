"""Selection must judge each trading day using only data up to that day.

Regression guard for the lookahead (future-function) bug: selectors take
hist.row(-1) as "the judged day", so the runner must truncate each code's
history at context.trade_date. Otherwise every day is judged on the last row
of the whole range (future data), inflating backtest returns.
"""

from datetime import date, timedelta

import polars as pl


def _make_ctx():
    class FakeCtx:
        def __init__(self):
            self.job_id = "no-lookahead-test"
            self.cancelled = False

        def log(self, message, level="INFO"):
            pass

        def update_progress(self, current, total, message=""):
            pass

        def check_cancelled(self):
            return self.cancelled

        def fail(self, error):
            pass

    return FakeCtx()


def _make_market():
    """40 days of bars; day 25 (2026-01-26) is a big bullish volume spike.

    All other days are flat (close ~= open, 1e6 volume) — including the last
    row of the range, so judging on the range-end row never selects the stock.
    """
    n_days = 40
    start = date(2026, 1, 1)
    spike_idx = 25  # 2026-01-26

    rows = []
    for i in range(n_days):
        d = start + timedelta(days=i)
        if i == spike_idx:
            o, c, h, l, v = 10.00, 10.80, 10.85, 9.95, 3_000_000.0
        else:
            o, c, h, l, v = 10.00, 10.01, 10.05, 9.95, 1_000_000.0
        rows.append({"code": "000001", "date": d, "open": o, "high": h,
                     "low": l, "close": c, "volume": v})

    df = pl.DataFrame(rows)

    class FakeMarket:
        def trading_dates(self, start, end):
            return sorted(
                d for d in df["date"].unique().to_list() if start <= d <= end
            )

        def load_bars(self, codes, start, end, columns=None):
            out = df.filter(pl.col("code").is_in(codes))
            if columns is not None:
                out = out.select(columns)
            return out

    return FakeMarket()


def test_mid_range_day_judged_with_only_past_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema
    from trendradar.app.services.selection_service import _run_selection

    register_all()
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())

    result = _run_selection(
        _make_ctx(),
        _make_market(),
        {
            "start_date": "2026-01-20",
            "end_date": "2026-02-09",
            "codes": ["000001"],
            "strategies": ["big_bullish_volume"],
        },
        sc,
    )

    spike_day = date(2026, 1, 26)
    assert any(
        s.signal_date == spike_day and "000001" in s.codes
        for s in result.signals
    )
