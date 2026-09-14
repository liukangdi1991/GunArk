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
    # 落盘：summary.json 与源产物 mtime 绑定（新鲜度判据）
    saved = json.loads((sel_dir / ".index" / "summary.json").read_text(encoding="utf-8"))
    assert saved["source_mtime_ns"] == (sel_dir / "signals.json").stat().st_mtime_ns


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
