from __future__ import annotations


def test_strategy_imports_use_selectors_package():
    from selection.selectors import BBIKDJSelector, PerfectB1Selector, ZXDKXBalanceSelector

    assert BBIKDJSelector is not None
    assert PerfectB1Selector is not None
    assert ZXDKXBalanceSelector is not None


def test_runner_factory_import_uses_execution_package():
    from selection.execution import build_strategy_runner
    from selection.selectors import PerfectB1Selector

    runner = build_strategy_runner(PerfectB1Selector())

    assert runner is not None


def test_selection_new_package_imports_are_available():
    from selection.execution import DefaultSelectionRunner, StrategySelectionRunner, build_strategy_runner
    from selection.formulas.expressions import long_term_bull_bear_line, moving_average
    from selection.formulas.indicators import compute_zx_lines

    assert StrategySelectionRunner is not None
    assert DefaultSelectionRunner is not None
    assert build_strategy_runner is not None
    assert moving_average is not None
    assert long_term_bull_bear_line is not None
    assert compute_zx_lines is not None


def test_selection_batch_group_meta_exposes_range_and_keys(tmp_path):
    import json

    from web.services.selection_service import _selection_group_meta_from_jobs_root

    job_dir = tmp_path / "jobs" / "job_20260401_20260403"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "execution_id": "job_20260401_20260403",
                "execution_type": "selection_batch",
                "result": {
                    "trade_from": "2026-04-01",
                    "trade_to": "2026-04-03",
                    "results": [
                        {"execution_key": "selection_20260401", "selection_date": "2026-04-01"},
                        {"execution_key": "selection_20260403", "selection_date": "2026-04-03"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    meta = _selection_group_meta_from_jobs_root(tmp_path / "jobs")

    assert meta["selection_20260401"]["selection_from"] == "2026-04-01"
    assert meta["selection_20260401"]["selection_to"] == "2026-04-03"
    assert meta["selection_20260401"]["selection_group_key"] == "job_20260401_20260403"
    assert meta["selection_20260401"]["selection_execution_keys"] == [
        "selection_20260401",
        "selection_20260403",
    ]


def test_volume_spike_balance_selector_import_and_runner():
    from selection.execution import build_strategy_runner
    from selection.execution.runners import VolumeSpikeBalanceSelectionRunner
    from selection.selectors import VolumeSpikeBalanceSelector

    selector = VolumeSpikeBalanceSelector()
    runner = build_strategy_runner(selector)

    assert isinstance(runner, VolumeSpikeBalanceSelectionRunner)


def test_volume_spike_balance_requires_all_recent_days_sticky(monkeypatch):
    import polars as pl

    from selection.selectors import volume_spike_balance
    from selection.selectors.volume_spike_balance import VolumeSpikeBalanceSelector

    close_values = [10.0] * 40
    close_values[15] = 11.0
    volume_values = [1000.0] * 40
    volume_values[15] = 2500.0
    volume_values[39] = 900.0
    hist = pl.DataFrame(
        {
            "date": list(range(40)),
            "open": [10.0] * 40,
            "high": [10.5] * 40,
            "low": [9.5] * 40,
            "close": close_values,
            "volume": volume_values,
        }
    )

    sticky_short = pl.Series("SHORT_TERM_TREND_LINE", [19.6] * 40)
    long = pl.Series("LONG_TERM_BULL_BEAR_LINE", [20.0] * 40)

    selector = VolumeSpikeBalanceSelector(
        volume_spike_lookback=30,
        volume_spike_multiple=2.0,
        zx_stick_window=10,
        zx_stick_limit_threshold=0.04,
        min_history=40,
    )

    monkeypatch.setattr(volume_spike_balance, "compute_zx_lines", lambda _: (sticky_short, long))
    assert selector._passes_filters(hist) is True

    bad_short_values = [19.6] * 40
    bad_short_values[32] = 8.0
    bad_short = pl.Series("SHORT_TERM_TREND_LINE", bad_short_values)
    monkeypatch.setattr(volume_spike_balance, "compute_zx_lines", lambda _: (bad_short, long))
    assert selector._passes_filters(hist) is False


def test_volume_spike_balance_rejects_spike_not_older_than_20_days(monkeypatch):
    import polars as pl

    from selection.selectors import volume_spike_balance
    from selection.selectors.volume_spike_balance import VolumeSpikeBalanceSelector

    close_values = [10.0] * 40
    close_values[20] = 11.0
    volume_values = [1000.0] * 40
    volume_values[20] = 2500.0
    volume_values[39] = 900.0
    hist = pl.DataFrame(
        {
            "date": list(range(40)),
            "open": [10.0] * 40,
            "high": [10.5] * 40,
            "low": [9.5] * 40,
            "close": close_values,
            "volume": volume_values,
        }
    )

    selector = VolumeSpikeBalanceSelector(min_history=40)
    short = pl.Series("SHORT_TERM_TREND_LINE", [19.6] * 40)
    long = pl.Series("LONG_TERM_BULL_BEAR_LINE", [20.0] * 40)
    monkeypatch.setattr(volume_spike_balance, "compute_zx_lines", lambda _: (short, long))

    assert selector._passes_filters(hist) is False


def test_volume_spike_balance_does_not_require_close_above_long_line(monkeypatch):
    import polars as pl

    from selection.selectors import volume_spike_balance
    from selection.selectors.volume_spike_balance import VolumeSpikeBalanceSelector

    close_values = [10.0] * 40
    close_values[15] = 11.0
    volume_values = [1000.0] * 40
    volume_values[15] = 2500.0
    volume_values[39] = 900.0
    hist = pl.DataFrame(
        {
            "date": list(range(40)),
            "open": [10.0] * 40,
            "high": [10.5] * 40,
            "low": [9.5] * 40,
            "close": close_values,
            "volume": volume_values,
        }
    )

    selector = VolumeSpikeBalanceSelector(min_history=40)
    short = pl.Series("SHORT_TERM_TREND_LINE", [19.6] * 40)
    long = pl.Series("LONG_TERM_BULL_BEAR_LINE", [20.0] * 40)
    monkeypatch.setattr(volume_spike_balance, "compute_zx_lines", lambda _: (short, long))

    assert selector._passes_filters(hist) is True


def test_volume_spike_balance_requires_today_close_below_long_line(monkeypatch):
    import polars as pl

    from selection.selectors import volume_spike_balance
    from selection.selectors.volume_spike_balance import VolumeSpikeBalanceSelector

    close_values = [10.0] * 40
    close_values[15] = 11.0
    close_values[39] = 20.0
    volume_values = [1000.0] * 40
    volume_values[15] = 2500.0
    volume_values[39] = 900.0
    hist = pl.DataFrame(
        {
            "date": list(range(40)),
            "open": [10.0] * 40,
            "high": [20.5] * 40,
            "low": [9.5] * 40,
            "close": close_values,
            "volume": volume_values,
        }
    )

    selector = VolumeSpikeBalanceSelector(min_history=40)
    short = pl.Series("SHORT_TERM_TREND_LINE", [19.6] * 40)
    long = pl.Series("LONG_TERM_BULL_BEAR_LINE", [20.0] * 40)
    monkeypatch.setattr(volume_spike_balance, "compute_zx_lines", lambda _: (short, long))

    assert selector._passes_filters(hist) is False


def test_volume_spike_balance_does_not_require_spike_day_long_above_short(monkeypatch):
    import polars as pl

    from selection.selectors import volume_spike_balance
    from selection.selectors.volume_spike_balance import VolumeSpikeBalanceSelector

    close_values = [10.0] * 40
    close_values[15] = 11.0
    volume_values = [1000.0] * 40
    volume_values[15] = 2500.0
    volume_values[39] = 900.0
    hist = pl.DataFrame(
        {
            "date": list(range(40)),
            "open": [10.0] * 40,
            "high": [10.5] * 40,
            "low": [9.5] * 40,
            "close": close_values,
            "volume": volume_values,
        }
    )

    selector = VolumeSpikeBalanceSelector(min_history=40)
    short_values = [19.6] * 40
    long_values = [20.0] * 40
    short_values[15] = 21.0
    monkeypatch.setattr(
        volume_spike_balance,
        "compute_zx_lines",
        lambda _: (
            pl.Series("SHORT_TERM_TREND_LINE", short_values),
            pl.Series("LONG_TERM_BULL_BEAR_LINE", long_values),
        ),
    )

    assert selector._passes_filters(hist) is True


def test_volume_spike_balance_select_fetches_enough_history_for_spike_day_zx(monkeypatch):
    import math

    import polars as pl

    from selection.selectors import volume_spike_balance
    from selection.selectors.volume_spike_balance import VolumeSpikeBalanceSelector

    rows = 143
    close_values = [10.0] * rows
    volume_values = [1000.0] * rows
    close_values[120] = 11.0
    volume_values[120] = 2500.0
    volume_values[-1] = 900.0
    hist = pl.DataFrame(
        {
            "date": list(range(rows)),
            "open": [10.0] * rows,
            "high": [10.5] * rows,
            "low": [9.5] * rows,
            "close": close_values,
            "volume": volume_values,
        }
    )

    def fake_zx_lines(df):
        n = len(df)
        short = [19.6] * n
        long = [math.nan] * min(113, n) + [20.0] * max(0, n - 113)
        return pl.Series("SHORT_TERM_TREND_LINE", short), pl.Series("LONG_TERM_BULL_BEAR_LINE", long)

    selector = VolumeSpikeBalanceSelector()
    monkeypatch.setattr(volume_spike_balance, "compute_zx_lines", fake_zx_lines)

    assert selector.select(142, {"000001": hist}) == ["000001"]


def test_volume_spike_balance_requires_today_volume_low_since_spike(monkeypatch):
    import polars as pl

    from selection.selectors import volume_spike_balance
    from selection.selectors.volume_spike_balance import VolumeSpikeBalanceSelector

    close_values = [10.0] * 40
    close_values[15] = 11.0
    volume_values = [1000.0] * 40
    volume_values[15] = 2500.0
    volume_values[30] = 800.0
    volume_values[39] = 900.0
    hist = pl.DataFrame(
        {
            "date": list(range(40)),
            "open": [10.0] * 40,
            "high": [10.5] * 40,
            "low": [9.5] * 40,
            "close": close_values,
            "volume": volume_values,
        }
    )

    selector = VolumeSpikeBalanceSelector(min_history=40)
    short = pl.Series("SHORT_TERM_TREND_LINE", [19.6] * 40)
    long = pl.Series("LONG_TERM_BULL_BEAR_LINE", [20.0] * 40)
    monkeypatch.setattr(volume_spike_balance, "compute_zx_lines", lambda _: (short, long))

    assert selector._passes_filters(hist) is False
