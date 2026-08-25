"""selection_service wires REQUIRES_MARKET_CAP strategies with per-day market cap."""
from datetime import date
from unittest.mock import patch

from trendradar.app.services import selection_service as svc


def test_build_market_cap_map_success_and_degrade():
    pro = object()
    dates = [date(2026, 8, 20), date(2026, 8, 21)]
    mc = {date(2026, 8, 21): {"000001": 1e6}}
    with patch.object(
        svc, "daily_basic_circ_mv",
        side_effect=[Exception("boom"), mc[date(2026, 8, 21)]],
    ):
        result = svc._build_market_cap_map(pro, dates)
    assert result[date(2026, 8, 21)] == {"000001": 1e6}
    assert result[date(2026, 8, 20)] is None  # 失败降级为 None，不抛错


def test_filter_warmup_keeps_only_candidate_codes():
    import polars as pl

    from trendradar.domain.strategy.protocol import WarmupResult
    from trendradar.app.services.selection_service import _filter_warmup

    w = WarmupResult(grouped={
        "000001": pl.DataFrame({"code": ["000001"], "date": [date(2026, 8, 21)]}),
        "000002": pl.DataFrame({"code": ["000002"], "date": [date(2026, 8, 21)]}),
    })
    dates_by_code = {"000001": [date(2026, 8, 21)], "000002": [date(2026, 8, 21)]}
    out = _filter_warmup(w, ["000001"], date(2026, 8, 21), dates_by_code)
    assert list(out.grouped) == ["000001"]
    # 候选集里的代码不在 warmup 中 → 跳过
    out2 = _filter_warmup(w, ["000001", "999999"], date(2026, 8, 21), dates_by_code)
    assert list(out2.grouped) == ["000001"]
    # warmup 为 None → None
    assert _filter_warmup(None, ["000001"], date(2026, 8, 21), dates_by_code) is None


def test_filter_warmup_truncates_history_at_trade_date():
    import polars as pl

    from trendradar.domain.strategy.protocol import WarmupResult
    from trendradar.app.services.selection_service import _filter_warmup

    hist = pl.DataFrame({
        "code": ["000001"] * 3,
        "date": [date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21)],
    })
    w = WarmupResult(grouped={"000001": hist})
    dates_by_code = {"000001": [date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21)]}
    out = _filter_warmup(w, ["000001"], date(2026, 8, 20), dates_by_code)
    assert out.grouped["000001"]["date"].to_list() == [date(2026, 8, 19), date(2026, 8, 20)]
