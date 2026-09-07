"""daily_basic_circ_mv: per-day full-market float market cap with in-memory cache."""
from datetime import date
from unittest.mock import MagicMock

from trendradar.infrastructure.tushare import market_cap as mc


def test_fetch_populates_and_caches(tmp_path):
    mc._MARKET_CAP_CACHE.clear()
    pro = MagicMock()
    resp = MagicMock()
    resp.to_dict.return_value = [
        {"ts_code": "000001.SZ", "circ_mv": 22141886.585},
        {"ts_code": "600519.SH", "circ_mv": 159114100.0},
        {"ts_code": "000002.SZ", "circ_mv": None},   # 缺失 → 跳过
    ]
    pro.daily_basic.return_value = resp

    result = mc.daily_basic_circ_mv(pro, date(2026, 8, 21))
    assert result == {"000001": 22141886.585, "600519": 159114100.0}
    assert pro.daily_basic.call_count == 1

    # cache hit: no second API call
    mc.daily_basic_circ_mv(pro, date(2026, 8, 21))
    assert pro.daily_basic.call_count == 1
    mc._MARKET_CAP_CACHE.clear()


def test_nan_circ_mv_skipped(tmp_path):
    mc._MARKET_CAP_CACHE.clear()
    pro = MagicMock()
    resp = MagicMock()
    resp.to_dict.return_value = [{"ts_code": "000001.SZ", "circ_mv": float("nan")}]
    pro.daily_basic.return_value = resp
    result = mc.daily_basic_circ_mv(pro, date(2026, 8, 21))
    assert result == {}
    mc._MARKET_CAP_CACHE.clear()


def test_none_response_returns_empty_without_cache(tmp_path):
    """接口返回 None（异常/限流）：返回空 dict、不抛 AttributeError、不缓存失败结果。

    空结果会让市值 gate 全部跳过（降级可见），但不固化——下次调用仍会重试接口。
    """
    mc._MARKET_CAP_CACHE.clear()
    pro = MagicMock()
    pro.daily_basic.return_value = None
    result = mc.daily_basic_circ_mv(pro, date(2026, 8, 21))
    assert result == {}
    assert date(2026, 8, 21) not in mc._MARKET_CAP_CACHE  # 失败不缓存，保留重试机会
    mc._MARKET_CAP_CACHE.clear()
