from datetime import date


class YearShardingPro:
    def __init__(self):
        self.calls = []

    def trade_cal(self, exchange, start_date, end_date):
        self.calls.append((start_date, end_date))
        import pandas as pd
        s = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
        e = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
        rows = []
        d = s
        while d <= e:
            rows.append({"cal_date": d.strftime("%Y%m%d"), "is_open": 1})
            d += __import__("datetime").timedelta(days=1)
        return pd.DataFrame(rows)


def test_fetch_shards_by_year(tmp_path):
    """Regression: a multi-year range must be sharded (row ceiling is 6000)."""
    from trendradar.infrastructure.tushare.calendar import fetch_trade_calendar

    pro = YearShardingPro()
    dates = fetch_trade_calendar(pro, date(2020, 1, 1), date(2026, 8, 21))
    assert len(pro.calls) == 7  # one call per year
    assert date(2020, 1, 1) in dates
    assert date(2026, 8, 21) in dates
    assert dates == sorted(dates)
