from datetime import date

import pandas as pd
import polars as pl

from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.runner import run_incremental
from trendradar.infrastructure.tushare.stocklist import EffectiveList


def _eff(expected_counts: dict[date, int]) -> EffectiveList:
    # 各日 expected 相同（测试场景均如此）：行数取其一，而非逐日累加
    rows = [(date(2010, 1, 1), None)] * max(expected_counts.values())
    return EffectiveList((), {}, {}, tuple(rows), {})


def _day_resp(day: date, n_stocks: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"ts_code": f"{i:06d}.SZ", "trade_date": day.strftime("%Y%m%d"),
         "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
         "vol": 100, "amount": 1000}
        for i in range(n_stocks)
    ])


class FakeAdj:
    def to_dict(self, orient):
        return {}


class DaySeqPro:
    """按日期返回预设行数；未知日期返回空。"""

    def __init__(self, day_counts: dict[date, int], fail_days: dict[date, str] | None = None):
        self.day_counts = day_counts
        self.fail_days = fail_days or {}
        self.calls = []

    def daily(self, **kwargs):
        day = kwargs["trade_date"]
        self.calls.append(day)
        if day in self.fail_days:
            raise RuntimeError(self.fail_days[day])
        n = self.day_counts.get(date.fromisoformat(
            f"{day[:4]}-{day[4:6]}-{day[6:]}"))
        if not n:
            return pd.DataFrame()
        return _day_resp(date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}"), n)

    def adj_factor(self, **kwargs):
        return FakeAdj()


D1, D2, D3 = date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)


def test_run_incremental_all_pass():
    pro = DaySeqPro({D1: 10, D2: 10, D3: 10})
    result = run_incremental(pro, [D1, D2, D3], effective=_eff({D1: 10, D2: 10, D3: 10}),
                             exclude_boards=None)
    assert not result.aborted
    assert result.claimed_days == [D1, D2, D3]
    assert result.doubtful_days == []
    assert result.all_days.height == 30


def test_run_incremental_doubtful_day_still_written_not_claimed():
    # R6：0.5 比值 → 写盘不入账
    pro = DaySeqPro({D1: 10, D2: 5, D3: 10})
    result = run_incremental(pro, [D1, D2, D3], effective=_eff({D1: 10, D2: 10, D3: 10}),
                             exclude_boards=None)
    assert not result.aborted
    assert result.claimed_days == [D1, D3]
    assert result.doubtful_days == [D2]
    assert result.all_days.height == 25  # doubtful 日数据照常累积


def test_run_incremental_env_failure_aborts_batch():
    # R5：第 2 天网络失败 → 立即中止，不拉后续天
    pro = DaySeqPro({D1: 10, D3: 10}, fail_days={D2.strftime("%Y%m%d"): "Connection aborted"})
    import trendradar.infrastructure.tushare.fetch as fetch
    import unittest.mock as mock
    with mock.patch.object(fetch.time, "sleep", lambda s: None):
        result = run_incremental(pro, [D1, D2, D3],
                                 effective=_eff({D1: 10, D2: 10, D3: 10}),
                                 exclude_boards=None)
    assert result.aborted
    assert result.failure_kind is FailureKind.ENV
    assert D3.strftime("%Y%m%d") not in pro.calls  # 后续天未拉


def test_run_incremental_cancel_aborts():
    pro = DaySeqPro({D1: 10, D2: 10})
    result = run_incremental(pro, [D1, D2], effective=_eff({D1: 10, D2: 10}),
                             exclude_boards=None, cancel_check=lambda: True)
    assert result.aborted
    assert result.cancelled


def test_run_incremental_empty_response_is_doubtful():
    # 幽灵日（官方日历有、接口 0 行）：写不进任何行、不入账
    pro = DaySeqPro({D1: 10})  # D2 返回空
    result = run_incremental(pro, [D1, D2], effective=_eff({D1: 10, D2: 10}),
                             exclude_boards=None)
    assert not result.aborted
    assert result.claimed_days == [D1]
    assert result.doubtful_days == [D2]
