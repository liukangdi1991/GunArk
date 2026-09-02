from datetime import date

import pandas as pd
import polars as pl
import pytest

from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.fetch import (
    AdjFactorUnavailable,
    classify_error,
    fetch_code_range,
    fetch_day_by_date,
    shard_ranges,
    _attach_adj_factor,
    _response_to_df,
    _to_ts_code,
    filter_excluded_boards,
)


def test_to_ts_code():
    assert _to_ts_code("600519") == "600519.SH"
    assert _to_ts_code("000001") == "000001.SZ"
    assert _to_ts_code("920099") == "920099.BJ"
    assert _to_ts_code("688001") == "688001.SH"
    assert _to_ts_code("600519.SH") == "600519.SH"  # 幂等，不产生 .SH.SH


def test_response_to_df_empty_and_rename():
    assert _response_to_df(pd.DataFrame(), "000001").is_empty()
    df = _response_to_df(pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "20240105",
        "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
        "vol": 100000, "amount": 1000000,
    }]), "000001")
    assert df["date"][0] == date(2024, 1, 5)
    assert df["volume"][0] == 100000.0
    assert df["code"][0] == "000001"
    assert df["adj_factor"][0] == 1.0


def test_shard_ranges_single_and_split():
    assert shard_ranges(date(2026, 1, 1), date(2026, 3, 1)) == [(date(2026, 1, 1), date(2026, 3, 1))]
    # 12 年 ≈ 3,130 交易日行 ≤ 5,500 → 单片（与旧实现一致）
    assert len(shard_ranges(date(2015, 1, 1), date(2026, 12, 31))) == 1
    segs = shard_ranges(date(1990, 1, 1), date(2026, 12, 31))
    assert len(segs) > 1
    assert segs[0][0] == date(1990, 1, 1)
    assert segs[-1][1] == date(2026, 12, 31)
    for (_, e), (s, _) in zip(segs, segs[1:]):
        assert e < s  # 无缝不重叠


def test_classify_error():
    assert classify_error("访问接口(daily)频率超限(300次/分钟)") is FailureKind.ENV
    assert classify_error("每分钟最多访问该接口") is FailureKind.ENV
    assert classify_error("每天最多访问该接口10000次") is FailureKind.ENV
    assert classify_error("Connection aborted") is FailureKind.ENV
    assert classify_error("Read timed out") is FailureKind.ENV
    assert classify_error("参数错误") is FailureKind.CODE
    assert classify_error("无此股票") is FailureKind.CODE
    assert classify_error(" totally weird ") is FailureKind.UNKNOWN


class FakeAdj:
    def to_dict(self, orient):
        return {}


_BAR = {"ts_code": "000001.SZ", "open": 10.0, "high": 11.0, "low": 9.5,
        "close": 10.5, "vol": 100000, "amount": 1000000}


def _bars(*days: str) -> pl.DataFrame:
    return pl.concat([
        _response_to_df(pd.DataFrame([{**_BAR, "trade_date": d}]), "000001") for d in days
    ])


def test_attach_adj_factor_empty_response_raises():
    """占位 1.0 会击穿下游 _qfq_scale 守卫（1.0 > 0，不退化原价），且 1.0 同时
    是「上市以来从未除权」的合法值，事后无法区分故障与正常 —— 取不到必须失败。"""
    with pytest.raises(AdjFactorUnavailable, match="响应为空"):
        _attach_adj_factor(_bars("20240105"), FakeAdj())
    with pytest.raises(AdjFactorUnavailable, match="未取到响应"):
        _attach_adj_factor(_bars("20240105"), None)


def test_attach_adj_factor_partial_coverage_raises():
    """真因子与占位 1.0 混在同一序列会造出巨大假跳空，比整列 1.0 更糟。"""
    with pytest.raises(AdjFactorUnavailable, match="1/2"):
        _attach_adj_factor(
            _bars("20240105", "20240108"),
            pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20240105",
                           "adj_factor": 118.3}]),
        )


def test_attach_adj_factor_full_coverage_merges():
    out = _attach_adj_factor(
        _bars("20240105", "20240108"),
        pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20240105", "adj_factor": 118.3},
            {"ts_code": "000001.SZ", "trade_date": "20240108", "adj_factor": 121.7},
        ]),
    )
    assert out["adj_factor"].to_list() == [118.3, 121.7]


class NoAdjPro:
    """daily 正常、adj_factor 返回空：接口通但没给数据"""

    def __init__(self):
        self.adj_calls = 0

    def daily(self, **kwargs):
        return pd.DataFrame([{**_BAR, "trade_date": "20240105"}])

    def adj_factor(self, **kwargs):
        self.adj_calls += 1
        return pd.DataFrame()


def test_fetch_code_range_missing_adj_factor_is_code_no_retry():
    pro = NoAdjPro()
    r = fetch_code_range(pro, "000001", date(2024, 1, 5), date(2024, 1, 5))
    assert r.df is None
    # 一次只取一只 ⇒ 空响应是这只股自己的问题 ⇒ code（计次，3 轮后出列）。
    # 判 env 就永不入 sync_skipped，bars 里留一个看不见的洞。
    assert r.kind is FailureKind.CODE
    assert "响应为空" in r.error
    assert pro.adj_calls == 1                 # 响应格式是好的，不做 1+2+4s 热重试


def test_fetch_day_by_date_missing_adj_factor_is_env_no_retry():
    pro = NoAdjPro()
    r = fetch_day_by_date(pro, date(2024, 1, 5))
    # 按 trade_date 取全市场 ⇒ 空响应是整天因子表没出（发布时序）⇒ env，不计次
    assert r.df is None and r.kind is FailureKind.ENV
    assert pro.adj_calls == 1


class RateLimitPro:
    def daily(self, **kwargs):
        raise RuntimeError("访问接口(daily)频率超限(300次/分钟)")


class FlakyPro:
    def __init__(self, fails: int):
        self.fails = fails
        self.calls = 0

    def daily(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError("Connection aborted")
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": "20240105",
            "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
            "vol": 100000, "amount": 1000000,
        }])

    def adj_factor(self, **kwargs):
        # 与 daily 同一行集：FlakyPro 验的是重试，不能顺带触发因子硬失败
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": "20240105", "adj_factor": 3.5,
        }])


def test_fetch_code_range_rate_limit_is_env_no_retry():
    pro = RateLimitPro()
    result = fetch_code_range(pro, "000001", date(2024, 1, 1), date(2024, 1, 31), max_retries=3)
    assert result.kind is FailureKind.ENV
    assert result.df is None
    assert "频率超限" in result.error


def test_fetch_code_range_retries_transient_and_succeeds(monkeypatch):
    import trendradar.infrastructure.tushare.fetch as fetch

    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    pro = FlakyPro(fails=2)
    result = fetch_code_range(pro, "000001", date(2024, 1, 5), date(2024, 1, 5), max_retries=3)
    assert result.kind is None
    assert result.df is not None and result.df.height == 1
    assert pro.calls == 3


def test_fetch_code_range_bucket_timeout_is_env():
    class NoTokenBucket:
        def acquire(self, timeout=60.0, cancel_check=None):
            return False

    pro = FlakyPro(fails=0)
    result = fetch_code_range(pro, "000001", date(2024, 1, 5), date(2024, 1, 5),
                              bucket=NoTokenBucket())
    assert result.kind is FailureKind.ENV


def test_fetch_day_by_date_success_and_shape():
    class DayPro:
        def __init__(self):
            self.daily_kwargs = None

        def daily(self, **kwargs):
            self.daily_kwargs = kwargs
            return pd.DataFrame([{
                "ts_code": "000001.SZ", "trade_date": "20260827",
                "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                "vol": 100000, "amount": 1000000,
            }])

        def adj_factor(self, **kwargs):
            return pd.DataFrame([{
                "ts_code": "000001.SZ", "trade_date": "20260827", "adj_factor": 3.5,
            }])

    pro = DayPro()
    result = fetch_day_by_date(pro, date(2026, 8, 27))
    assert result.kind is None
    assert pro.daily_kwargs == {"trade_date": "20260827"}
    assert result.df["code"][0] == "000001"
    assert result.df["date"][0] == date(2026, 8, 27)
    assert result.df["adj_factor"][0] == 3.5          # 真因子覆盖了占位 1.0


def test_fetch_day_by_date_none_response_is_unknown():
    class NonePro:
        def daily(self, **kwargs):
            return None

    result = fetch_day_by_date(NonePro(), date(2026, 8, 27), max_retries=1)
    assert result.kind is FailureKind.UNKNOWN


def test_filter_excluded_boards_gem_star():
    df = pl.DataFrame({"code": ["300001", "688001", "600519", "000001"]})
    out = filter_excluded_boards(df, ["gem", "star"])
    assert sorted(out["code"].to_list()) == ["000001", "600519"]
    assert filter_excluded_boards(df, None).height == 4
    assert filter_excluded_boards(df, []).height == 4
