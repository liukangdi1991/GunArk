"""Strategy default_params must actually reach the formula calls.

Regression: brick/oversold/ultimate declared rich default_params but their
selectors called compute_mt/compute_zx_lines without arguments, so the
hardcoded formula defaults were always used — parameterization was dead.
"""

import polars as pl

from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.selectors.brick_chart import BrickChartSelector
from trendradar.domain.strategy.selectors.oversold_bottom_fishing import (
    OversoldBottomFishingSelector,
)
from trendradar.domain.strategy.selectors.ultimate_brick_chart import (
    UltimateBrickChartSelector,
)
from .helpers import make_ohlcv_df


def _make_defn(selector_class, params):
    return StrategyDefinition(
        strategy_id="x", name="X", description="",
        selector_class=selector_class, default_params=params,
    )


def _patch_formulas(monkeypatch):
    import trendradar.domain.strategy.selectors.brick_chart as brick_mod
    import trendradar.domain.strategy.selectors.oversold_bottom_fishing as oversold_mod
    import trendradar.domain.strategy.selectors.ultimate_brick_chart as ultimate_mod
    import trendradar.domain.strategy.formulas.zxdkx as zx_mod

    captured = {}

    def fake_mt(high, low, close, n=4, m=6, t=4):
        captured["mt"] = (n, m, t)
        return pl.Series("mt", [0.0] * len(close))

    def fake_zx(df, m1=14, m2=28, m3=57, m4=114):
        captured["zx"] = (m1, m2, m3, m4)
        return (
            pl.Series("short", [1.0] * df.height),
            pl.Series("long", [2.0] * df.height),
        )

    # compute_mt 是模块级 import（绑定在 selector 模块名上）；zx_lines 是函数内 import
    for mod in (brick_mod, oversold_mod, ultimate_mod):
        monkeypatch.setattr(mod, "compute_mt", fake_mt)
    monkeypatch.setattr(zx_mod, "compute_zx_lines", fake_zx)
    return captured


def test_brick_chart_warmup_passes_params(monkeypatch):
    captured = _patch_formulas(monkeypatch)
    defn = _make_defn(
        BrickChartSelector,
        {"n": 5, "m": 7, "t": 3, "m1": 10, "m2": 20, "m3": 40, "m4": 80},
    )
    df = make_ohlcv_df("000001", 30)
    defn.selector_class(defn).warmup(df)
    assert captured["mt"] == (5, 7, 3)
    assert captured["zx"] == (10, 20, 40, 80)


def test_oversold_warmup_passes_params(monkeypatch):
    captured = _patch_formulas(monkeypatch)
    defn = _make_defn(
        OversoldBottomFishingSelector,
        {"n": 5, "m": 7, "t": 3, "m1": 10, "m2": 20, "m3": 40, "m4": 80,
         "ema1": 10, "dif_fast": 12, "dif_slow": 26,
         "every_neg": 5, "every_down": 4, "dd2_window": 5},
    )
    df = make_ohlcv_df("000001", 30)
    defn.selector_class(defn).warmup(df)
    assert captured["mt"] == (5, 7, 3)
    assert captured["zx"] == (10, 20, 40, 80)


def test_ultimate_brick_warmup_passes_params(monkeypatch):
    captured = _patch_formulas(monkeypatch)
    defn = _make_defn(
        UltimateBrickChartSelector,
        {"n": 5, "m": 7, "t": 3, "m1": 10, "m2": 20, "m3": 40, "m4": 80, "ema1": 10},
    )
    df = make_ohlcv_df("000001", 30)
    defn.selector_class(defn).warmup(df)
    assert captured["mt"] == (5, 7, 3)
    assert captured["zx"] == (10, 20, 40, 80)
