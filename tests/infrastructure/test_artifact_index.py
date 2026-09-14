"""选股产物索引：picks.parquet + summary.json 的生成、新鲜度与降级。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import polars as pl

from trendradar.infrastructure.storage.artifact_index import ensure_selection_index


def _write_signals(sel_dir: Path, mtime: float | None = None) -> Path:
    sel_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "signal_from": "2026-09-01",
        "signal_to": "2026-09-02",
        "signals": [
            {"strategy_id": "b1", "strategy_name": "B1战法", "signal_date": "2026-09-01",
             "codes": ["000001", "600519"], "elapsed_seconds": 0.5},
            {"strategy_id": "b1", "strategy_name": "B1战法", "signal_date": "2026-09-02",
             "codes": ["000001"], "elapsed_seconds": 0.25},
            {"strategy_id": "pk", "strategy_name": "填坑战法", "signal_date": "2026-09-02",
             "codes": ["000333"], "elapsed_seconds": 0.1},
        ],
    }
    path = sel_dir / "signals.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_ensure_selection_index_builds_parquet_and_summary(tmp_path):
    sel_dir = tmp_path / "selection"
    _write_signals(sel_dir)

    index = ensure_selection_index("k1", sel_dir)

    assert index is not None
    assert index.picks_path == sel_dir / ".index" / "picks.parquet"
    df = pl.read_parquet(index.picks_path)
    assert df.columns == ["strategy", "date", "code"]
    assert df.height == 4
    # 固有全序：strategy → date → code
    assert df["strategy"].to_list() == ["B1战法", "B1战法", "B1战法", "填坑战法"]
    assert df["code"].to_list() == ["000001", "600519", "000001", "000333"]

    assert index.summary["total_picks"] == 4
    assert index.summary["unique_codes"] == 3
    assert index.summary["trade_days"] == 2
    assert index.summary["signal_from"] == "2026-09-01"
    assert index.summary["signal_to"] == "2026-09-02"
    assert index.summary["by_strategy_date"] == [
        {"strategy": "B1战法", "date": "2026-09-01", "count": 2, "elapsed_seconds": 0.5},
        {"strategy": "B1战法", "date": "2026-09-02", "count": 1, "elapsed_seconds": 0.25},
        {"strategy": "填坑战法", "date": "2026-09-02", "count": 1, "elapsed_seconds": 0.1},
    ]
    # 落盘：summary.json 与来源文件签名绑定（新鲜度判据；字段级而非单值）
    saved = json.loads((sel_dir / ".index" / "summary.json").read_text(encoding="utf-8"))
    assert saved["source_signature"] == {
        "signals.json": (sel_dir / "signals.json").stat().st_mtime_ns
    }


def test_ensure_selection_index_reuses_fresh_index(tmp_path, monkeypatch):
    """索引新鲜（源 mtime 未变）时必须直接复用，不得重读 signals.json。"""
    sel_dir = tmp_path / "selection"
    _write_signals(sel_dir)
    ensure_selection_index("k1", sel_dir)

    import trendradar.infrastructure.storage.artifact_index as mod

    calls: list[Path] = []
    real = mod._read_signals

    def spy(path: Path) -> dict:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(mod, "_read_signals", spy)
    again = ensure_selection_index("k1", sel_dir)

    assert calls == []
    assert again is not None and again.summary["total_picks"] == 4


def test_ensure_selection_index_rebuilds_after_source_change(tmp_path):
    sel_dir = tmp_path / "selection"
    path = _write_signals(sel_dir)
    before = ensure_selection_index("k1", sel_dir)
    assert before.summary["total_picks"] == 4

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["signals"] = payload["signals"][:1]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    future = time.time() + 10
    os.utime(path, (future, future))

    after = ensure_selection_index("k1", sel_dir)
    assert after is not None
    assert after.summary["total_picks"] == 2
    assert pl.read_parquet(after.picks_path).height == 2


def test_ensure_selection_index_empty_signals(tmp_path):
    """空信号集也要产出可用索引（0 行 parquet + 全零计数），而不是 None。"""
    sel_dir = tmp_path / "selection"
    sel_dir.mkdir(parents=True)
    (sel_dir / "signals.json").write_text(
        json.dumps({"signal_from": None, "signal_to": None, "signals": []}), encoding="utf-8"
    )

    index = ensure_selection_index("k1", sel_dir)

    assert index is not None
    assert index.summary["total_picks"] == 0
    assert index.summary["trade_days"] == 0
    assert pl.read_parquet(index.picks_path).height == 0


def test_ensure_selection_index_missing_source_returns_none(tmp_path):
    assert ensure_selection_index("k1", tmp_path / "nope") is None


def test_ensure_selection_index_degrades_when_unwritable(tmp_path, monkeypatch):
    """索引写不进去（只读盘）时必须返回 None，而不是抛异常——调用方据此降级直读。"""
    sel_dir = tmp_path / "selection"
    _write_signals(sel_dir)

    import trendradar.infrastructure.storage.artifact_index as mod

    def boom(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(mod, "atomic_write_parquet", boom)
    assert ensure_selection_index("k1", sel_dir) is None


def test_ensure_selection_index_degrades_on_broken_json(tmp_path):
    """signals.json 损坏时返回 None（降级），并已清掉可能残留的半成品索引。"""
    sel_dir = tmp_path / "selection"
    sel_dir.mkdir(parents=True)
    (sel_dir / "signals.json").write_text("{not json", encoding="utf-8")

    assert ensure_selection_index("k1", sel_dir) is None


def test_summarize_signals_is_the_single_aggregation_source(tmp_path):
    """聚合（摘要字段 + picks 帧）是公开的单一实现源：索引构建与 presenter 回落路径共用。"""
    from trendradar.infrastructure.storage.artifact_index import summarize_signals

    sel_dir = tmp_path / "selection"
    _write_signals(sel_dir)
    signals = json.loads((sel_dir / "signals.json").read_text(encoding="utf-8"))

    fields, frame = summarize_signals(signals)

    assert fields["total_picks"] == 4
    assert fields["unique_codes"] == 3
    assert fields["trade_days"] == 2
    assert fields["signal_from"] == "2026-09-01"
    assert fields["signal_to"] == "2026-09-02"
    assert [row["count"] for row in fields["by_strategy_date"]] == [2, 1, 1]
    assert frame.columns == ["strategy", "date", "code"]
    assert frame.height == 4


def test_warm_indexes_builds_missing_and_skips_broken(tmp_path):
    """启动预热：补齐缺失索引、跳过无源/损坏产物、不抛异常、幂等。"""
    from trendradar.infrastructure.storage.artifact_index import warm_indexes

    root = tmp_path / "objects" / "executions"
    _write_signals(root / "k1" / "selection")
    _write_signals(root / "k2" / "selection")
    (root / "k3" / "selection").mkdir(parents=True)  # 无 signals.json
    broken = root / "k4" / "selection"
    broken.mkdir(parents=True)
    (broken / "signals.json").write_text("{not json", encoding="utf-8")

    assert warm_indexes(root) == 2  # k1/k2 新建，k3/k4 跳过
    for key in ("k1", "k2"):
        assert (root / key / "selection" / ".index" / "summary.json").is_file()
    assert not (root / "k4" / "selection" / ".index").exists()

    assert warm_indexes(root) == 0  # 幂等：已就绪的不重建
    assert warm_indexes(tmp_path / "nope") == 0  # 目录不存在时安全返回


def _write_backtest(
    bt_dir: Path, *, trades, skips=(), open_positions=(), equity=(), mtime: float | None = None
) -> Path:
    bt_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "metrics": {"trade_count": len(trades), "total_return_pct": 1.5},
        "trade_count": len(trades),
        "skip_count": len(skips),
        "trades": list(trades),
        "skips": list(skips),
        "open_positions": list(open_positions),
        "equity_curve": list(equity),
    }
    path = bt_dir / "result.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_ensure_backtest_index_builds_summary(tmp_path):
    """回测索引：机器事实（窗口/计数/每策略聚合）与来源签名。"""
    from trendradar.infrastructure.storage.artifact_index import ensure_backtest_index

    bt_dir = tmp_path / "backtest"
    _write_backtest(
        bt_dir,
        trades=[
            {"strategy": "b1", "code": "000001", "buy_date": "2026-08-01", "sell_date": "2026-08-05",
             "buy_price": 10.0, "shares": 100, "profit": 50.0, "return_pct": 5.0},
            {"strategy": "b1", "code": "000002", "buy_date": "2026-08-02", "sell_date": "2026-08-06",
             "buy_price": 20.0, "shares": 50, "profit": -10.0, "return_pct": -1.0},
        ],
        skips=[{"strategy": "pk", "code": "000333", "buy_date": "2026-08-03"}],
        open_positions=[
            {"strategy": "b1", "code": "600519", "buy_date": "2026-08-07", "unrealized_pnl": 12.0}
        ],
    )

    index = ensure_backtest_index("k1", bt_dir)

    assert index is not None
    s = index.summary
    assert (s["window_from"], s["window_to"]) == ("2026-08-01", "2026-08-07")
    assert (s["trade_count"], s["skip_count"], s["open_position_count"]) == (2, 1, 1)
    by = {row["strategy"]: row for row in s["by_strategy"]}
    assert by["b1"]["trade_count"] == 2 and by["b1"]["wins"] == 1
    assert by["b1"]["realized_profit_sum"] == 40.0
    assert by["b1"]["invested_notional_sum"] == 2000.0
    assert by["b1"]["open_positions"] == 1 and by["b1"]["unrealized_pnl"] == 12.0
    assert by["pk"]["skip_count"] == 1
    assert s["source_signature"] == {
        "result.json": (bt_dir / "result.json").stat().st_mtime_ns
    }


def test_ensure_backtest_index_prefers_equity_window(tmp_path):
    """有净值曲线时窗口取曲线首尾（与旧实现同口径）。"""
    from trendradar.infrastructure.storage.artifact_index import ensure_backtest_index

    bt_dir = tmp_path / "backtest"
    _write_backtest(
        bt_dir,
        trades=[{"strategy": "b1", "code": "000001", "buy_date": "2026-08-01",
                 "sell_date": "2026-08-05", "buy_price": 1.0, "shares": 1,
                 "profit": 0.0, "return_pct": 0.0}],
        equity=[{"date": "2026-07-30", "value": 1.0}, {"date": "2026-08-10", "value": 1.1}],
    )

    assert ensure_backtest_index("k1", bt_dir).summary["window_from"] == "2026-07-30"
    assert ensure_backtest_index("k1", bt_dir).summary["window_to"] == "2026-08-10"


def test_ensure_backtest_index_rebuilds_after_source_change(tmp_path):
    from trendradar.infrastructure.storage.artifact_index import ensure_backtest_index

    bt_dir = tmp_path / "backtest"
    path = _write_backtest(
        bt_dir,
        trades=[{"strategy": "b1", "code": "000001", "buy_date": "2026-08-01",
                 "sell_date": "2026-08-05", "buy_price": 1.0, "shares": 1,
                 "profit": 0.0, "return_pct": 0.0}],
    )
    assert ensure_backtest_index("k1", bt_dir).summary["trade_count"] == 1

    _write_backtest(bt_dir, trades=[])
    future = time.time() + 10
    os.utime(path, (future, future))

    assert ensure_backtest_index("k1", bt_dir).summary["trade_count"] == 0


def test_ensure_backtest_index_degrades_on_broken_json(tmp_path):
    from trendradar.infrastructure.storage.artifact_index import ensure_backtest_index

    bt_dir = tmp_path / "backtest"
    bt_dir.mkdir(parents=True)
    (bt_dir / "result.json").write_text("{not json", encoding="utf-8")

    assert ensure_backtest_index("k1", bt_dir) is None


def test_warm_indexes_covers_backtests_too(tmp_path):
    """启动预热同时覆盖回测产物（两类索引同构）。"""
    from trendradar.infrastructure.storage.artifact_index import warm_indexes

    root = tmp_path / "objects" / "executions"
    _write_signals(root / "s1" / "selection")
    _write_backtest(root / "b1" / "backtest", trades=[])

    assert warm_indexes(root) == 2
    assert (root / "s1" / "selection" / ".index" / "summary.json").is_file()
    assert (root / "b1" / "backtest" / ".index" / "summary.json").is_file()
