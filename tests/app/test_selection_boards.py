"""R26：boards 板块过滤——宇宙派生、交集语义、空宇宙日志、market 列探测。"""

from datetime import date, timedelta

import polars as pl
import pytest


def _make_ctx():
    class FakeCtx:
        def __init__(self):
            self.job_id = "boards-test"
            self.cancelled = False
            self.logs = []

        def log(self, message, level="INFO"):
            self.logs.append(message)

        def update_progress(self, current, total, message=""):
            pass

        def check_cancelled(self):
            return self.cancelled

    return FakeCtx()


def _meta_df():
    return pl.DataFrame({
        "code": ["000001", "600519", "920001", "300001"],
        "market": ["主板", "主板", "北交所", "创业板"],
    })


def _make_market(meta=None):
    """load_bars 收到的 codes 被记录 —— 宇宙派生结果的直接观测点。"""
    received = {}

    class FakeMarket:
        def trading_dates(self, start, end):
            d0, n = date(2026, 1, 1), 10
            return [d0 + timedelta(days=i) for i in range(n)]

        def stock_meta(self):
            return meta if meta is not None else _meta_df()

        def load_bars(self, codes, start, end, columns=None):
            received["codes"] = list(codes)
            rows = [{"code": c, "date": self.trading_dates(start, end)[0],
                     "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2,
                     "volume": 1e6} for c in codes]
            return pl.DataFrame(rows)

    return FakeMarket(), received


def _store(tmp_path):
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    return sc



_REQ = {"start_date": "2026-01-01", "end_date": "2026-01-10",
        "strategies": ["big_bullish_volume"]}

def test_whitelist_order_independent(tmp_path):
    """复审 W3 排序回归：同一白名单不同传入顺序 → load_bars 收到同一排序
    （跨输入确定性，performance-optimization plan 承诺）。"""
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, received1 = _make_market()
    _run_selection(_make_ctx(), market, {**_REQ, "codes": ["600519", "000001"]}, _store(tmp_path))
    market2, received2 = _make_market()
    _run_selection(_make_ctx(), market2, {**_REQ, "codes": ["000001", "600519"]}, _store(tmp_path))
    assert received1["codes"] == received2["codes"] == ["000001", "600519"]


def test_boards_filter_universe(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, received = _make_market()
    _run_selection(_make_ctx(), market, {**_REQ, "boards": ["北交所"]}, _store(tmp_path))
    assert received["codes"] == ["920001"]          # 宇宙 = market ∈ boards


def test_boards_plus_codes_intersection(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, received = _make_market()
    req = {**_REQ, "boards": ["主板", "北交所"], "codes": ["600519", "920001", "300001"]}
    _run_selection(_make_ctx(), market, req, _store(tmp_path))
    assert received["codes"] == ["600519", "920001"]   # 交集：300001 创业板被滤掉


def test_default_universe_unchanged(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, received = _make_market()
    _run_selection(_make_ctx(), market, dict(_REQ), _store(tmp_path))
    assert received["codes"] == ["000001", "300001", "600519", "920001"]  # 全宇宙、排序不变


def test_empty_universe_logs_board_reason(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, _ = _make_market()
    ctx = _make_ctx()
    _run_selection(ctx, market, {**_REQ, "boards": ["科创板"]}, _store(tmp_path))
    assert any("板块过滤后宇宙为空" in m and "科创板" in m for m in ctx.logs)


def test_validate_rejects_invalid_board(tmp_path):
    from trendradar.app.services.selection_service import validate_selection_request
    with pytest.raises(ValueError, match="未知板块"):
        validate_selection_request({"boards": ["Hack"]}, _store(tmp_path), _make_market()[0])


def test_validate_rejects_missing_market_column(tmp_path):
    from trendradar.app.services.selection_service import validate_selection_request
    no_market = _meta_df().drop("market")
    market, _ = _make_market(meta=no_market)
    with pytest.raises(ValueError, match="market"):
        validate_selection_request({"boards": ["北交所"]}, _store(tmp_path), market)
