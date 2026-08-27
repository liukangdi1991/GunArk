from datetime import date
import polars as pl
from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.selectors import register_all

_initialized = False


def _ensure_registered():
    global _initialized
    if not _initialized:
        register_all()
        _initialized = True


def get_defn(strategy_id):
    _ensure_registered()
    from trendradar.domain.strategy.registry import get
    return get(strategy_id)


make_bbi_kdj_b1_defn = lambda: get_defn("bbi_kdj_b1")
make_super_b1_defn = lambda: get_defn("super_b1")
make_bbi_short_long_defn = lambda: get_defn("bbi_short_long")
make_peak_kdj_defn = lambda: get_defn("peak_kdj")
make_ma60_volume_wave_defn = lambda: get_defn("ma60_volume_wave")
make_zxdkx_balance_defn = lambda: get_defn("zxdkx_balance")
make_perfect_b1_defn = lambda: get_defn("perfect_b1_v2")
make_big_bullish_volume_defn = lambda: get_defn("big_bullish_volume")
make_volume_spike_balance_defn = lambda: get_defn("volume_spike_balance")


def make_empty_df():
    return pl.DataFrame(
        schema={"code": pl.Utf8, "date": pl.Date, "open": pl.Float64,
                "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                "volume": pl.Float64}
    )


def make_ohlcv_df(code, days, trend=0.0, start_price=10.0):
    import numpy as np
    dates = pl.date_range(
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        interval="1d",
        eager=True,
    )[:days]
    prices = start_price * np.exp(trend * np.arange(days))
    np.random.seed(42)
    open_prices = prices * (1 + 0.005 * np.random.randn(days))
    close_prices = prices * (1 + 0.005 * np.random.randn(days))
    highs = np.maximum(open_prices, close_prices) * (1 + 0.01 * np.abs(np.random.randn(days)))
    lows = np.minimum(open_prices, close_prices) * (1 - 0.01 * np.abs(np.random.randn(days)))
    volumes = 1e6 + 1e5 * np.random.randn(days)
    return pl.DataFrame({
        "code": [code] * days,
        "date": dates,
        "open": open_prices,
        "high": highs,
        "low": lows,
        "close": close_prices,
        "volume": volumes,
    })
make_macd_ma_convergence_defn = lambda: get_defn("macd_ma_convergence")
