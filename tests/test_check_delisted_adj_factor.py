"""scripts/check_delisted_adj_factor.py 的离线冒烟：假 pro 走通全部判定分支。

脚本要真 token 才能跑，但它的结论（缺口只数 vs 5% 熔断阈值 → 该走哪条兜底口径）
直接决定设计，所以判定逻辑本身必须在这里钉住。全程不联网。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SCRIPT = (Path(__file__).resolve().parent.parent
          / "scripts" / "check_delisted_adj_factor.py")
_spec = importlib.util.spec_from_file_location("chk", SCRIPT)
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)

FIELDS = ["ts_code", "symbol", "name", "area", "industry", "market",
          "list_date", "delist_date"]

# 代码段互不重叠：在市 1..20，退市在窗口内 1001..1008，退市早于 2015 的 2001..2002
LISTED = [f"{i:06d}" for i in range(1, 21)]
DELIST_IN = [f"{i:06d}" for i in range(1001, 1009)]     # delist 2020 ⇒ 在 BASELINE_START 之后
DELIST_OUT = [f"{i:06d}" for i in range(2001, 2003)]    # delist 2011 ⇒ 应被 start > end 钳掉


def _rows(codes, list_date, delist_date=None):
    return [{
        "ts_code": f"{c}.SZ", "symbol": c, "name": f"S{c}",
        "area": "深圳", "industry": "x", "market": "主板",
        "list_date": list_date.strftime("%Y%m%d"),
        "delist_date": delist_date.strftime("%Y%m%d") if delist_date else None,
    } for c in codes]


def _parse_day(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


class FakePro:
    """bad_codes 的 adj_factor 返回空表；no_daily_codes 的 daily 返回空表；
    pre_only_codes 只在 BASELINE_START 之前有行情（退市前长期停牌的那种）。"""

    def __init__(self, bad_codes=(), no_daily_codes=(), pre_only_codes=()):
        self.bad = set(bad_codes)
        self.no_daily = set(no_daily_codes)
        self.pre_only = set(pre_only_codes)
        all_rows = (_rows(LISTED, date(2010, 1, 1))
                    + _rows(DELIST_IN, date(2010, 1, 1), date(2020, 6, 30))
                    + _rows(DELIST_OUT, date(2005, 1, 1), date(2011, 3, 1)))
        self._l = pd.DataFrame([r for r in all_rows if not r["delist_date"]])[FIELDS]
        self._d = pd.DataFrame([r for r in all_rows if r["delist_date"]])[FIELDS]

    def stock_basic(self, exchange=None, list_status=None, fields=None):
        return self._l if list_status == "L" else self._d

    def trade_cal(self, exchange=None, start_date=None, end_date=None):
        hi, days, d = _parse_day(end_date), [], _parse_day(start_date)
        while d <= hi:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return pd.DataFrame({"cal_date": [x.strftime("%Y%m%d") for x in days],
                             "is_open": [1] * len(days)})

    def daily(self, ts_code=None, start_date=None, end_date=None, freq=None):
        if ts_code.split(".")[0] in self.no_daily:
            return pd.DataFrame()
        hi = _parse_day(end_date)
        if ts_code.split(".")[0] in self.pre_only:
            hi = min(hi, chk.BASELINE_START - timedelta(days=1))
        days, d = [], _parse_day(start_date)
        while d <= hi and len(days) < 5:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        if not days:
            return pd.DataFrame()
        n = len(days)
        return pd.DataFrame({
            "ts_code": [ts_code] * n,
            "trade_date": [x.strftime("%Y%m%d") for x in days],
            "open": [10.0] * n, "high": [11.0] * n, "low": [9.5] * n,
            "close": [10.5] * n, "pre_close": [10.4] * n,
            "vol": [1e5] * n, "amount": [1e6] * n,
        })

    def adj_factor(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        if ts_code.split(".")[0] in self.bad:
            return pd.DataFrame()
        resp = self.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if resp.empty:
            return pd.DataFrame()          # 无列可镜像
        return pd.DataFrame({"ts_code": resp["ts_code"],
                             "trade_date": resp["trade_date"],
                             "adj_factor": [2.5] * len(resp)})


def _run(monkeypatch, capsys, pro, extra=()):
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-for-offline-smoke")
    monkeypatch.setattr(chk, "get_pro", lambda: pro)
    monkeypatch.setattr(sys, "argv", ["chk", "--sleep", "0", *extra])
    return chk.main(), capsys.readouterr().out


def test_out_of_scope_delisted_are_clamped_away(monkeypatch, capsys):
    """退市早于 BASELINE_START 的股不进有效清单，且无缺口时判风险不存在。"""
    rc, out = _run(monkeypatch, capsys, FakePro())
    assert rc == 0
    assert f"其中退市股 {len(DELIST_IN)} 只、在市股 {len(LISTED)} 只" in out
    assert "002001" not in out                      # 2011 年退市的没被测
    assert f"{len(DELIST_IN)} 只实查到因子、0 只缺因子" in out


def test_few_bad_lands_in_gap_branch(monkeypatch, capsys):
    """缺口 ≤ 5% ⇒ 健康轮 ⇒ 3 轮出列 ⇒ 带缺口提交（幸存者偏差换个形式回来）。"""
    rc, out = _run(monkeypatch, capsys, FakePro([DELIST_IN[0]]))
    assert rc == 1
    assert "1 只退市股取不到 adj_factor" in out
    assert "健康轮" in out and "带缺口提交" in out
    assert DELIST_IN[0] in out


def test_many_bad_lands_in_breaker_branch(monkeypatch, capsys):
    """缺口 > 5% ⇒ 每轮必撞整批熔断 ⇒ 全量基线永远建不起来。"""
    rc, out = _run(monkeypatch, capsys, FakePro(DELIST_IN))
    assert rc == 1
    assert f"{len(DELIST_IN)} 只退市股取不到 adj_factor" in out
    assert "每轮必撞整批熔断" in out


def test_limit_extrapolates_and_says_so(monkeypatch, capsys):
    """--limit 抽样时必须明说是外推值，不能把抽样数当准数报。"""
    rc, out = _run(monkeypatch, capsys, FakePro(DELIST_IN[:2]),
                   ["--limit", "4"])
    assert rc == 1
    assert "这是抽样（4/8）" in out
    assert "请去掉 --limit 重跑取准数" in out


def test_control_failure_aborts_with_env_verdict(monkeypatch, capsys):
    """对照组也挂 ⇒ 是环境/权限问题不是退市边界，退出码 2 且不给退市结论。"""
    rc, out = _run(monkeypatch, capsys, FakePro(LISTED))
    assert rc == 2
    assert "接口本身有问题" in out
    assert "== 退市组" not in out          # 没往下跑退市组


def test_empty_daily_is_not_counted_as_ok(monkeypatch, capsys):
    """daily 无行的股不能记成 ok：_attach_adj_factor 在空 df 上提前 return，
    因子根本没被查过，此时报「风险不存在」就是假绿。"""
    rc, out = _run(monkeypatch, capsys, FakePro(no_daily_codes=[DELIST_IN[0]]))
    assert rc == 1
    assert "daily_empty" in out
    assert "未被检验过" in out
    assert "风险不存在" not in out


def test_pre_baseline_suspension_is_benign_not_a_gap(monkeypatch, capsys):
    """最后交易日早于基线起点 ⇒ 钳制窗口本就没有交易日。Tushare 有它的数据，
    生产按 OK_EMPTY 视为成功，不能和「接口真没数据」混成一档报缺口。但这只的
    adj_factor 压根没被查过，脚本必须如实说「因子未实查」，不靠「无一缺失」糊过去。"""
    rc, out = _run(monkeypatch, capsys, FakePro(pre_only_codes=[DELIST_IN[0]]))
    assert rc == 0
    assert "outside_baseline" in out
    assert "因子未实查" in out
    assert "0 只缺因子" in out
    assert "OK_EMPTY" in out
    assert "daily_empty" not in out       # 不能落进未检验那一档


def test_missing_token_fails_fast_without_any_call(monkeypatch, capsys):
    """没 token 就立刻退，不能等 stock_basic 炸一屏鉴权栈。"""
    def tripwire():
        raise AssertionError("不该走到 get_pro")

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(chk, "get_pro", tripwire)
    monkeypatch.setattr(sys, "argv", ["chk", "--sleep", "0"])
    assert chk.main() == 2
    assert "TUSHARE_TOKEN" in capsys.readouterr().err
