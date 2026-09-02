from datetime import date
from unittest import mock

import pandas as pd
import polars as pl

from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.runner import (
    StockOutcome,
    run_backfill,
    run_full,
    run_incremental,
)
from trendradar.infrastructure.tushare.stocklist import EffectiveList


def _eff(expected_counts: dict[date, int]) -> EffectiveList:
    # 各日 expected 相同（测试场景均如此）：行数取其一，而非逐日累加
    rows = [(date(2010, 1, 1), None)] * max(expected_counts.values())
    return EffectiveList((), tuple(rows), {})


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


class CodeRangePro:
    """按 (code) 返回预设响应；可指定失败类别。"""

    def __init__(self, codes_ok: list[str], fail: dict[str, str] | None = None):
        self.codes_ok = set(codes_ok)
        self.fail = fail or {}
        self.calls = []

    def daily(self, **kwargs):
        code = kwargs["ts_code"].split(".")[0]
        self.calls.append(code)
        if code in self.fail:
            raise RuntimeError(self.fail[code])
        return pd.DataFrame([{
            "ts_code": kwargs["ts_code"], "trade_date": "20260825",
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "vol": 100, "amount": 1000,
        }])

    def adj_factor(self, **kwargs):
        return FakeAdj()


def _patch_sleep():
    import trendradar.infrastructure.tushare.fetch as fetch
    return mock.patch.object(fetch.time, "sleep", lambda s: None)


def test_run_full_writes_staging_files(tmp_path):
    staging = tmp_path / "staging"
    pro = CodeRangePro(["000001", "000002"])
    with _patch_sleep():
        outcomes, cancelled = run_full(
            pro,
            [("000001", date(2026, 8, 25), date(2026, 8, 25)),
             ("000002", date(2026, 8, 25), date(2026, 8, 25))],
            staging, max_workers=2,
        )
    assert not cancelled
    assert all(o.ok for o in outcomes)
    assert (staging / "000001.parquet").exists()
    assert (staging / "000002.parquet").exists()


def test_run_full_failure_classification(tmp_path):
    staging = tmp_path / "staging"
    pro = CodeRangePro(["000001"], fail={"000002": "参数错误", "000003": "Connection aborted"})
    with _patch_sleep():
        outcomes, cancelled = run_full(
            pro,
            [("000001", D1, D1), ("000002", D1, D1), ("000003", D1, D1)],
            staging, max_workers=3,
        )
    by_code = {o.code: o for o in outcomes}
    assert by_code["000001"].ok
    assert by_code["000002"].kind is FailureKind.CODE
    assert by_code["000003"].kind is FailureKind.ENV
    assert not (staging / "000002.parquet").exists()


def test_run_full_cancel_preserves_written(tmp_path):
    staging = tmp_path / "staging"
    pro = CodeRangePro(["000001", "000002"])
    flag = {"cancel": False}

    def progress(cur, total, msg):
        flag["cancel"] = True  # 第一条完成后即取消

    with _patch_sleep():
        outcomes, cancelled = run_full(
            pro, [("000001", D1, D1), ("000002", D1, D1)], staging,
            max_workers=1, progress=progress, cancel_check=lambda: flag["cancel"],
        )
    assert cancelled
    # R10：已写入的 staging 文件原地保留（续传）
    assert (staging / "000001.parquet").exists()


def test_run_backfill_merges_into_bars_and_never_touches_ledger(tmp_path):
    # R9 执行侧：补齐只写文件
    bars = tmp_path / "bars"
    pro = CodeRangePro(["000001"])
    with _patch_sleep():
        outcomes = run_backfill(pro, [("000001", D1, D1)], bars)
    assert outcomes[0].ok
    out = pl.read_parquet(bars / "000001.parquet")
    assert out.height == 1
