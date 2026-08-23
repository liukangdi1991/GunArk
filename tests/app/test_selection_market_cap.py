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
