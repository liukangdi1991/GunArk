from datetime import date

import polars as pl

from trendradar.domain.market.sync.selfcheck import (
    ROW_COUNT_RATIO,
    coverage_ok,
    doubtful_by_row_count,
    doubtful_detail,
    expected_trading_count,
    file_structure_ok,
    ledger_subset_ok,
)

EFFECTIVE = [
    (date(2010, 1, 1), None),                    # 在市
    (date(2010, 1, 1), date(2016, 6, 30)),       # 退市
    (date(2020, 3, 1), None),                    # 晚上市
]


def test_expected_trading_count():
    assert expected_trading_count(EFFECTIVE, date(2015, 6, 1)) == 2
    assert expected_trading_count(EFFECTIVE, date(2017, 1, 1)) == 1  # 退市股已出
    assert expected_trading_count(EFFECTIVE, date(2016, 6, 30)) == 2  # 退市当日仍计
    assert expected_trading_count(EFFECTIVE, date(2021, 1, 1)) == 2


def test_doubtful_by_row_count_075_threshold():
    eff = [(date(2010, 1, 1), None)] * 100  # expected = 100
    assert doubtful_by_row_count({date(2015, 6, 1): 75}, eff) == []  # 恰 0.75 通过
    assert doubtful_by_row_count({date(2015, 6, 1): 74}, eff) == [date(2015, 6, 1)]
    # 2015 实测场景 2335/2733 = 0.854 应通过
    eff2 = [(date(2010, 1, 1), None)] * 2733
    assert doubtful_by_row_count({date(2015, 6, 1): 2335}, eff2) == []
    # 半截响应 ~0.5 应报警
    assert doubtful_by_row_count({date(2015, 6, 1): 1366}, eff2) == [date(2015, 6, 1)]


def test_doubtful_exempts_already_booked_days():
    """已入账日豁免：全量重建重拉历史时行数不变，不重复拦截
    （2026-09-09 审查：2015-07 停牌日已人工确认后，每次全量仍重复报警失败）。"""
    eff = [(date(2010, 1, 1), None)] * 100
    d1, d2 = date(2015, 7, 8), date(2015, 7, 9)
    rows = {d1: 50, d2: 50}
    assert doubtful_by_row_count(rows, eff) == [d1, d2]
    assert doubtful_by_row_count(rows, eff, already_booked={d1}) == [d2]
    assert doubtful_by_row_count(rows, eff, already_booked={d1, d2}) == []


def test_doubtful_detail_fields():
    eff = [(date(2010, 1, 1), None)] * 100
    detail = doubtful_detail({date(2015, 7, 8): 50, date(2015, 6, 1): 60}, eff)
    assert [r["day"] for r in detail] == [date(2015, 6, 1), date(2015, 7, 8)]  # 升序
    assert detail[0] == {"day": date(2015, 6, 1), "actual": 60,
                         "expected": 100, "ratio": 0.6}


def test_coverage_ok():
    assert coverage_ok({date(2026, 8, 26), date(2026, 8, 27)}, {date(2026, 8, 27)})
    assert not coverage_ok({date(2026, 8, 26)}, {date(2026, 8, 27)})


def _bars_df(rows):
    return pl.DataFrame(rows)


def test_file_structure_ok_valid():
    df = _bars_df([
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": date(2026, 8, 27), "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
    ])
    assert file_structure_ok(df)


def test_file_structure_ok_rejects_duplicate_dates():
    df = _bars_df([
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    ])
    assert not file_structure_ok(df)


def test_file_structure_ok_rejects_descending_dates():
    df = _bars_df([
        {"date": date(2026, 8, 27), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    ])
    assert not file_structure_ok(df)


def test_file_structure_ok_rejects_nan_close():
    df = _bars_df([
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": float("nan")},
    ])
    assert not file_structure_ok(df)


def test_file_structure_ok_empty_is_ok():
    assert file_structure_ok(pl.DataFrame())


def test_ledger_subset_ok():
    assert ledger_subset_ok({date(2026, 8, 27)}, {date(2026, 8, 26), date(2026, 8, 27)})
    assert not ledger_subset_ok({date(2026, 8, 28)}, {date(2026, 8, 27)})


def test_ratio_constant():
    assert ROW_COUNT_RATIO == 0.75
