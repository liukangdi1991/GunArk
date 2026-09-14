# P1 选股详情分页（索引 + 分页契约 + 统一分页器）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `2026-09-14-pagination-and-artifact-index-design.md` 的 P1（含 P6 计数口径）：
选股详情端点数倍瘦身 + 服务端分页 + 前端统一分页器（后续回测明细复用）。

**Architecture:** 产物索引（`picks.parquet` + `summary.json`，自愈、原子写、失败降级）把分页成本
从 O(产物字节) 降到 O(页)；新增 `/api/selection-results/{key}/picks` 分页端点；前端新增
`usePagedQuery`（状态机 + 请求编排）与 `PagedTable`（渲染）两个单元，选股结果页接入。

**Tech Stack:** Python 3.11 + Polars + FastAPI；前端 React 18 + antd 5 + TS。

**基线：** `pytest -q` = 642 passed（~50 分钟，hermetic）。每个任务后除明确改写的用例外必须保持全绿；
前端每任务后 `npm run build` 必须通过。

---

### Task 1: 原子写工具归位（`atomic_write_parquet` → filesystem 层）

**Files:**
- Modify: `trendradar/infrastructure/filesystem/atomic.py`
- Modify: `trendradar/infrastructure/tushare/writer.py:44-58`
- Modify: `trendradar/infrastructure/tushare/stocklist.py:16`、`trendradar/infrastructure/tushare/runner.py:27`
- Test: `tests/infrastructure/test_atomic.py`、`tests/infrastructure/test_writer.py:6`

- [ ] **Step 1: 写失败测试**

`tests/infrastructure/test_atomic.py` 追加：

```python
def test_atomic_write_parquet_replaces_target(tmp_path):
    """原子写：目标已存在时被整体替换，且不留 .tmp 残渣。"""
    import polars as pl
    from trendradar.infrastructure.filesystem.atomic import atomic_write_parquet

    target = tmp_path / "x.parquet"
    atomic_write_parquet(pl.DataFrame({"a": [1]}), target)
    atomic_write_parquet(pl.DataFrame({"a": [2, 3]}), target)

    assert pl.read_parquet(target)["a"].to_list() == [2, 3]
    assert [p.name for p in tmp_path.iterdir()] == ["x.parquet"]
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_atomic.py -q`
Expected: FAIL（`ImportError: cannot import name 'atomic_write_parquet'`）

- [ ] **Step 3: 搬迁实现**

`trendradar/infrastructure/filesystem/atomic.py` 追加（顶部补 `from typing import TYPE_CHECKING`，
`if TYPE_CHECKING: import polars as pl`）：

```python
def atomic_write_parquet(df: "pl.DataFrame", target: Path) -> None:
    """parquet 原子写（tmp + os.replace）：读侧永远只见完整文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        os.close(fd)
        df.write_parquet(tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

`writer.py`：删掉本地定义，改 `from trendradar.infrastructure.filesystem.atomic import atomic_write_parquet`
（`stocklist.py:16`、`runner.py:27` 的 import 同步改；`tests/infrastructure/test_writer.py:6` 同步改）。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_atomic.py tests/infrastructure/test_writer.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/filesystem/atomic.py trendradar/infrastructure/tushare/{writer,stocklist,runner}.py tests/infrastructure/{test_atomic,test_writer}.py
git commit -m "refactor: atomic_write_parquet 归位 filesystem 层（索引模块复用，避免 storage→tushare 依赖）"
```

---

### Task 2: 选股产物索引（`artifact_index.py`）

**Files:**
- Create: `trendradar/infrastructure/storage/artifact_index.py`
- Test: `tests/infrastructure/test_artifact_index.py`

- [ ] **Step 1: 写失败测试**

```python
"""选股产物索引：picks.parquet + summary.json 的生成、新鲜度与降级。"""
from __future__ import annotations

import json
import os
import time
from datetime import date
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
    assert index.picks_path == sel_dir / "picks.parquet"
    df = pl.read_parquet(index.picks_path)
    assert df.columns == ["strategy", "date", "code"]
    assert df.height == 4
    # 固有排序：strategy → date → code
    assert df["strategy"].to_list() == ["B1战法", "B1战法", "B1战法", "填坑战法"]
    assert df["code"].to_list()[:3] == ["000001", "600519", "000001"]

    assert index.summary["total_picks"] == 4
    assert index.summary["unique_codes"] == 3
    assert index.summary["trade_days"] == 2
    assert index.summary["signal_from"] == "2026-09-01"
    assert index.summary["by_strategy_date"] == [
        {"strategy": "B1战法", "date": "2026-09-01", "count": 2, "elapsed_seconds": 0.5},
        {"strategy": "B1战法", "date": "2026-09-02", "count": 1, "elapsed_seconds": 0.25},
        {"strategy": "填坑战法", "date": "2026-09-02", "count": 1, "elapsed_seconds": 0.1},
    ]
    # 落盘：summary.json 与源 mtime 绑定
    saved = json.loads((sel_dir / "summary.json").read_text(encoding="utf-8"))
    assert saved["source_mtime_ns"] == (sel_dir / "signals.json").stat().st_mtime_ns


def test_ensure_selection_index_reuses_fresh_index(tmp_path, monkeypatch):
    """索引新鲜（mtime 未变）时不得重读 signals.json。"""
    sel_dir = tmp_path / "selection"
    _write_signals(sel_dir)
    ensure_selection_index("k1", sel_dir)

    import trendradar.infrastructure.storage.artifact_index as mod
    calls = {"n": 0}
    real = mod._read_signals
    monkeypatch.setattr(mod, "_read_signals", lambda p: (calls.__setitem__("n", calls["n"] + 1), real(p))[1])

    ensure_selection_index("k1", sel_dir)
    assert calls["n"] == 0


def test_ensure_selection_index_rebuilds_after_source_change(tmp_path):
    sel_dir = tmp_path / "selection"
    path = _write_signals(sel_dir)
    before = ensure_selection_index("k1", sel_dir).summary["total_picks"]

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["signals"] = payload["signals"][:1]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    future = time.time() + 10
    os.utime(path, (future, future))

    after = ensure_selection_index("k1", sel_dir)
    assert before == 4 and after.summary["total_picks"] == 2
    assert pl.read_parquet(after.picks_path).height == 2


def test_ensure_selection_index_missing_source_returns_none(tmp_path):
    assert ensure_selection_index("k1", tmp_path / "nope") is None


def test_ensure_selection_index_degrades_when_unwritable(tmp_path, monkeypatch):
    """索引写不进去（只读盘）时必须返回 None 而不是抛异常——调用方降级直读。"""
    sel_dir = tmp_path / "selection"
    _write_signals(sel_dir)

    def boom(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("trendradar.infrastructure.filesystem.atomic.atomic_write_parquet", boom)
    assert ensure_selection_index("k1", sel_dir) is None
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_artifact_index.py -q`
Expected: FAIL（`ModuleNotFoundError: artifact_index`）

- [ ] **Step 3: 实现**

`trendradar/infrastructure/storage/artifact_index.py`：

```python
"""产物索引：把大 JSON 产物物化成可分页的列式文件 + 小摘要。

分页/列表端点若每次整份解析 signals.json（最大 13MB）/result.json（最大 16MB），
成本是 O(产物字节)。索引把派生数据落到同目录：

    selection/picks.parquet   strategy,date,code（按固有全序排序）
    selection/summary.json    计数与窗口（列表/KPI 用，μs 级读取）

- 新作业落盘后调用 ensure_*（幂等）；
- 存量产物首访自愈一次（summary 内 source_mtime_ns 校验源产物是否变过）；
- 生成失败（只读盘等）返回 None，调用方降级直读 JSON——绝不因索引问题 5xx。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from trendradar.infrastructure.filesystem.atomic import (
    atomic_write_parquet,
    atomic_write_text,
)

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1
SUMMARY_NAME = "summary.json"
PICKS_NAME = "picks.parquet"


@dataclass(frozen=True)
class SelectionIndex:
    """selection 产物索引；picks_path/summary_path 均存在且新鲜。"""

    picks_path: Path
    summary_path: Path
    summary: dict[str, Any]


def _read_signals(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_from_signals(signals: dict, source_mtime_ns: int) -> tuple[dict, pl.DataFrame]:
    by_key: dict[tuple[str, str], dict] = {}
    rows: list[tuple[str, str, str]] = []
    for sig in signals.get("signals", []):
        name = sig.get("strategy_name") or sig.get("strategy_id") or "未知"
        day = sig.get("signal_date") or ""
        codes = [str(c) for c in (sig.get("codes") or [])]
        for code in codes:
            rows.append((name, day, code))
        row = by_key.setdefault(
            (name, day), {"strategy": name, "date": day, "count": 0, "elapsed_seconds": 0.0}
        )
        row["count"] += len(codes)
        row["elapsed_seconds"] += sig.get("elapsed_seconds") or 0.0

    by_strategy_date = [
        {**row, "elapsed_seconds": round(row["elapsed_seconds"], 3)}
        for row in (by_key[key] for key in sorted(by_key))
    ]
    if rows:
        frame = pl.DataFrame(rows, schema=["strategy", "date", "code"], orient="row")
        frame = frame.sort(["strategy", "date", "code"])
        total = frame.height
        unique_codes = int(frame["code"].n_unique())
        trade_days = int(frame["date"].n_unique())
    else:
        frame = pl.DataFrame(schema={"strategy": pl.Utf8, "date": pl.Utf8, "code": pl.Utf8})
        total = unique_codes = trade_days = 0

    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "source_mtime_ns": source_mtime_ns,
        "total_picks": total,
        "unique_codes": unique_codes,
        "trade_days": trade_days,
        "signal_from": signals.get("signal_from"),
        "signal_to": signals.get("signal_to"),
        "by_strategy_date": by_strategy_date,
    }, frame


def ensure_selection_index(execution_key: str, selection_dir: Path) -> SelectionIndex | None:
    """确保索引存在且新鲜；源产物缺失返回 None，写失败也返回 None（调用方降级）。"""
    signals_path = selection_dir / "signals.json"
    if not signals_path.is_file():
        return None

    summary_path = selection_dir / SUMMARY_NAME
    picks_path = selection_dir / PICKS_NAME
    source_mtime_ns = signals_path.stat().st_mtime_ns

    cached = _load_fresh_summary(summary_path, picks_path, source_mtime_ns)
    if cached is not None:
        return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=cached)

    try:
        signals = _read_signals(signals_path)
        summary, frame = _summary_from_signals(signals, source_mtime_ns)
        atomic_write_parquet(frame, picks_path)
        atomic_write_text(summary_path, json.dumps(summary, ensure_ascii=False))
        return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=summary)
    except Exception as exc:  # 索引是派生物：任何失败都降级，不影响主功能
        logger.warning("selection index build failed for %s: %s", execution_key, exc)
        return None


def _load_fresh_summary(summary_path: Path, picks_path: Path, source_mtime_ns: int) -> dict | None:
    if not (summary_path.is_file() and picks_path.is_file()):
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if summary.get("schema_version") != INDEX_SCHEMA_VERSION:
        return None
    if summary.get("source_mtime_ns") != source_mtime_ns:
        return None
    return summary
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_artifact_index.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/storage/artifact_index.py tests/infrastructure/test_artifact_index.py
git commit -m "feat(index): 选股产物索引（picks.parquet + summary.json，自愈/原子写/失败降级）"
```

---

### Task 3: worker 落盘后生成索引

**Files:**
- Modify: `trendradar/app/services/selection_service.py`（`submit_selection`/`submit_batch_selection` 的 worker 尾部）
- Modify: `trendradar/app/services/backtest_service.py`（`submit_selection_backtest` 的选股阶段尾部）
- Test: `tests/app/test_selection_index_hooked.py`

- [ ] **Step 1: 写失败测试**

```python
"""选股 worker 落盘后必须已生成索引（新产物免自愈）。"""
from __future__ import annotations

import json

import polars as pl

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


class EmptyMarketStore:
    """与 tests/app/test_execution_registration.py 同款空宇宙 store（本测试只验索引接线）。"""

    def load_bars(self, codes, start, end, columns=None):
        return pl.DataFrame(schema={"code": pl.Utf8, "date": pl.Date, "close": pl.Float64})

    def latest_trade_date(self):
        return None

    def trading_dates(self, start, end):
        return []

    def stock_meta(self, codes=None):
        return pl.DataFrame(schema={"code": pl.Utf8, "name": pl.Utf8})

    def get_calendar(self):
        return []

    def get_row(self, code, dt):
        return None

    def get_previous_close(self, code, dt):
        return None

    def get_rows(self, code, start_date, end_date):
        return pl.DataFrame(schema={"code": pl.Utf8, "date": pl.Date, "close": pl.Float64})


def test_selection_worker_writes_index(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.domain.strategy.selectors import register_all

    register_all()

    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    executor = JobExecutor(JobStore(sc.db_path))

    from trendradar.app.services.selection_service import submit_selection

    job_id = submit_selection(
        executor,
        EmptyMarketStore(),
        {"start_date": "2026-08-01", "end_date": "2026-08-20", "strategies": ["bbi_kdj_b1"]},
        sc,
    )
    executor._jobs[job_id].future.result(timeout=15.0)

    assert executor.get_state(job_id)["status"] == "success"
    sel_dir = tmp_path / "storage" / "objects" / "executions" / job_id / "selection"
    assert (sel_dir / "picks.parquet").is_file()
    summary = json.loads((sel_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["total_picks"] == pl.read_parquet(sel_dir / "picks.parquet").height
    executor.shutdown(wait=True)
```

（空宇宙 → 0 信号，断言的是"文件存在且计数与 parquet 一致"这条接线；聚合语义由 Task 2 的单测覆盖。）

- [ ] **Step 2: 跑测试确认 RED** → `expected: FAIL（picks.parquet 不存在）`

- [ ] **Step 3: 两个 worker 尾部各加一行**

```python
        _write_selection_manifest(ctx.job_id, signal_set)
        _register_selection_metadata(ctx.job_id)

        from trendradar.infrastructure.runtime import runtime_root
        from trendradar.infrastructure.storage.artifact_index import ensure_selection_index

        ensure_selection_index(
            ctx.job_id,
            runtime_root() / "storage" / "objects" / "executions" / ctx.job_id / "selection",
        )
```

（`selection_backtest` 的 worker 同样调用——索引失败只记 WARNING，不影响作业终态。）

- [ ] **Step 4: 跑测试确认 GREEN**，并跑 `tests/app -q` 全绿
- [ ] **Step 5: Commit** `feat(index): 选股/选股回测 worker 落盘即建索引`

---

### Task 4: 分页 presenter + 路由

**Files:**
- Modify: `trendradar/interfaces/api/presenters.py`（新增 `selection_picks_page`）
- Modify: `trendradar/interfaces/api/routes/executions.py`（新增 `/picks` 路由，400 白名单校验）
- Test: `tests/interfaces/test_api_contract.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_api_contract.py`）

```python
def test_selection_picks_page_contract(client):
    """分页契约：items/total/offset/limit；默认全序 strategy→date→code。"""
    key = "20260820_100000_selection_a1b2"
    resp = client.get(f"/api/selection-results/{key}/picks?limit=2&offset=0")
    assert resp.status_code == 200
    page = resp.json()
    assert set(page) == {"items", "total", "offset", "limit"}
    assert page["total"] == 4 and page["offset"] == 0 and page["limit"] == 2
    assert [p["code"] for p in page["items"]] == ["000001", "000002"]  # a1b2 fixture 顺序
    assert page["items"][0]["name"]                    # 展示字段已补全
    tail = client.get(f"/api/selection-results/{key}/picks?limit=2&offset=2").json()
    assert [p["code"] for p in tail["items"]] == ["600519", "000333"]
    assert client.get(f"/api/selection-results/{key}/picks?offset=99").json()["items"] == []


def test_selection_picks_page_filters_and_sort(client):
    key = "20260820_100000_selection_a1b2"
    only_b1 = client.get(f"/api/selection-results/{key}/picks?strategies=B1战法").json()
    assert only_b1["total"] == 3
    both = client.get(f"/api/selection-results/{key}/picks?strategies=B1战法&strategies=填坑战法").json()
    assert both["total"] == 4
    day = client.get(f"/api/selection-results/{key}/picks?dates=2026-08-20").json()
    assert day["total"] == 4
    empty_day = client.get(f"/api/selection-results/{key}/picks?dates=1999-01-01").json()
    assert empty_day["total"] == 0 and empty_day["items"] == []
    desc = client.get(f"/api/selection-results/{key}/picks?sort_by=code&sort_dir=desc&limit=1").json()
    assert desc["items"][0]["code"] == "600519"


def test_selection_picks_page_rejects_bad_params(client):
    key = "20260820_100000_selection_a1b2"
    assert client.get(f"/api/selection-results/{key}/picks?sort_by=drop_table").status_code == 400
    assert client.get(f"/api/selection-results/{key}/picks?limit=0").status_code == 422
    assert client.get(f"/api/selection-results/{key}/picks?limit=201").status_code == 422
    assert client.get("/api/selection-results/nope/picks").status_code == 404


def test_selection_detail_has_counts_without_picks(client):
    """详情端点瘦身：不再带 picks，改带 counts（前端分页拉取）。"""
    payload = client.get("/api/selection-results/20260820_100000_selection_a1b2").json()
    assert "picks" not in payload
    assert payload["counts"] == {"total_picks": 4, "unique_codes": 4, "trade_days": 1}
    assert len(payload["result"]["summary"]) == 3
```

**Fixture 同步改动**（`_write_selection_artifacts`，`test_api_contract.py:30`）：`bbi_kdj_b1` 的
`codes` 由 `["000001", "600519"]` 改为 `["000001", "000002", "600519"]`，使该 key 共 4 条 picks、
4 只股票、1 个交易日、3 条 (策略,日期) 摘要行（既有断言 `total_picks == 4` 即由此而来）。

- [ ] **Step 2: 跑测试确认 RED**

Run: `.venv/bin/python -m pytest tests/interfaces/test_api_contract.py -q -k "picks_page or counts_without_picks"`
Expected: FAIL（404 / KeyError: 'counts'）

- [ ] **Step 3: 实现**

`presenters.py`：

```python
PICKS_SORT_KEYS = {
    "strategy": ("strategy", "date", "code"),
    "date": ("date", "strategy", "code"),
    "code": ("code", "strategy", "date"),
}


def _picks_lazy_filtered(path: Path, strategies: list[str] | None, dates: list[str] | None):
    """picks.parquet 的惰性帧 + 过滤（过滤在切片前生效）。"""
    import polars as pl

    frame = pl.scan_parquet(path)
    if strategies:
        frame = frame.filter(pl.col("strategy").is_in(strategies))
    if dates:
        frame = frame.filter(pl.col("date").is_in(dates))
    return frame


def selection_picks_page(
    key: str,
    *,
    offset: int = 0,
    limit: int = 50,
    strategies: list[str] | None = None,
    dates: list[str] | None = None,
    sort_by: str | None = None,
    sort_dir: str = "asc",
) -> dict | None:
    """选股明细分页（spec §3/§4）。索引缺失或写入失败时降级直读 signals.json。

    返回 None 表示该 execution_key 没有选股产物（路由据此 404）。
    """
    import polars as pl

    from trendradar.infrastructure.storage.artifact_index import ensure_selection_index

    sel_dir = _executions_root() / key / "selection"
    index = ensure_selection_index(key, sel_dir)

    if index is not None:
        frame = _picks_lazy_filtered(index.picks_path, strategies, dates)
        total = int(frame.select(pl.len()).collect().item())
        if sort_by:  # 默认（None）= 产物固有全序 (strategy,date,code)，免排序
            keys = list(PICKS_SORT_KEYS[sort_by])
            frame = frame.sort(keys, descending=[sort_dir == "desc"] + [False] * (len(keys) - 1))
        rows = frame.slice(offset, limit).collect().to_dicts()
    else:
        signals = _read_json(sel_dir / "signals.json")
        if signals is None:
            return None
        rows_all = [
            row
            for row in _selection_picks(key, signals)
            if _picks_matches(row, strategies, dates)
        ]
        keys = list(PICKS_SORT_KEYS[sort_by or "strategy"])
        # 稳定排序：先按 tiebreaker 升序，再按主键（primary 方向），与 parquet 分支同序
        rows_all.sort(key=lambda r: tuple(r[k] for k in keys[1:]))
        rows_all.sort(key=lambda r: r[keys[0]], reverse=sort_dir == "desc")
        total = len(rows_all)
        rows = rows_all[offset : offset + limit]

    meta = _stock_meta_by_code()
    items = [
        {
            **row,
            "name": meta.get(row["code"], ("", ""))[0],
            "industry": meta.get(row["code"], ("", ""))[1],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def _picks_matches(row: dict, strategies: list[str] | None, dates: list[str] | None) -> bool:
    if strategies and row["strategy"] not in strategies:
        return False
    if dates and row["date"] not in dates:
        return False
    return True
```

`routes/executions.py`：

```python
@router.get("/selection-results/{execution_key}/picks")
def get_selection_picks(
    execution_key: str,
    request: FastAPIRequest,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    strategies: list[str] | None = Query(default=None),
    dates: list[str] | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
):
    from trendradar.interfaces.api.presenters import PICKS_SORT_KEYS, selection_picks_page

    if _invalid_execution_key(execution_key):
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    if sort_by is not None and sort_by not in PICKS_SORT_KEYS:
        raise HTTPException(status_code=400, detail=f"非法 sort_by: {sort_by}")
    payload = selection_picks_page(
        execution_key, offset=offset, limit=limit,
        strategies=strategies, dates=dates, sort_by=sort_by, sort_dir=sort_dir,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Selection result not found for '{execution_key}'")
    return payload
```

其中 `_invalid_execution_key` 是模块级小工具（把既有两处内联判断收敛到它，路由侧唯一实现源）：

```python
def _invalid_execution_key(key: str) -> bool:
    """路径遍历防护：/、\\、.. 一律拒绝。"""
    return "/" in key or "\\" in key or ".." in key
```

```python
def _selection_counts(key: str, signals: dict | None) -> dict:
    """计数口径（spec §9.1）：索引优先；索引不可用时由 signals 现算。"""
    from trendradar.infrastructure.storage.artifact_index import ensure_selection_index

    index = ensure_selection_index(key, _executions_root() / key / "selection")
    if index is not None:
        summary = index.summary
        return {
            "total_picks": summary["total_picks"],
            "unique_codes": summary["unique_codes"],
            "trade_days": summary["trade_days"],
        }
    signals = signals if signals is not None else {}
    codes = [str(c) for sig in signals.get("signals", []) for c in (sig.get("codes") or [])]
    days = {sig.get("signal_date") for sig in signals.get("signals", []) if sig.get("signal_date")}
    return {"total_picks": len(codes), "unique_codes": len(set(codes)), "trade_days": len(days)}
```

`selection_result_payload`：`include_detail=True` 时返回值由
`{"result", "artifacts", "picks"}` 改为 `{"result", "artifacts", "counts"}`（`picks` 交给分页端点；
`counts` 用上面的 `_selection_counts(key, signals)`）。同文件把 `_PICKS_SORT_KEYS` 改名为公开的
`PICKS_SORT_KEYS`（路由与 presenter 共用同一白名单，单一实现源）。

- [ ] **Step 4: 跑测试确认 GREEN** + `pytest tests/interfaces -q` 全绿
- [ ] **Step 5: Commit** `feat(api): 选股明细分页端点 + 详情端点瘦身（counts 取代 picks 全量）`

---

### Task 5: 前端类型与服务

**Files:**
- Modify: `frontend/src/types/selection.ts`
- Modify: `frontend/src/services/selections.ts`

- [ ] **Step 1: 改类型**

```ts
export interface SelectionCounts {
  total_picks: number;
  unique_codes: number;
  trade_days: number;
}

export interface SelectionResultDetailResponse {
  result: SelectionResult;
  artifacts: SelectionArtifact[];
  counts: SelectionCounts;          // picks 移到分页端点
}

export interface SelectionPicksPage {
  items: SelectionPick[];
  total: number;
  offset: number;
  limit: number;
}
```

- [ ] **Step 2: 加分页服务**

```ts
export interface SelectionPicksQuery {
  offset: number;
  limit: number;
  strategies?: string[];
  dates?: string[];
  sortBy?: string;
  sortDir?: "asc" | "desc";
}

export function getSelectionPicks(
  executionKey: string,
  query: SelectionPicksQuery,
  signal?: AbortSignal,
): Promise<SelectionPicksPage> {
  const params = new URLSearchParams({ offset: String(query.offset), limit: String(query.limit) });
  for (const s of query.strategies ?? []) params.append("strategies", s);
  for (const d of query.dates ?? []) params.append("dates", d);
  if (query.sortBy) { params.set("sort_by", query.sortBy); params.set("sort_dir", query.sortDir ?? "asc"); }
  return requestJson<SelectionPicksPage>(
    `/api/selection-results/${encodeURIComponent(executionKey)}/picks?${params.toString()}`,
    { signal },
  );
}
```

- [ ] **Step 3: `npm run build` 通过**（页面此时会因缺 picks 编译不过——Task 6 一并修，允许本步骤 build 暂红）

- [ ] **Step 4: Commit**（与 Task 6 合并提交）

---

### Task 6: 前端统一分页器（`usePagedQuery` + `PagedTable`）

**Files:**
- Create: `frontend/src/hooks/usePagedQuery.ts`
- Create: `frontend/src/components/PagedTable.tsx`

- [ ] **Step 1: hook（状态机 + 请求编排）**

```ts
import { useCallback, useEffect, useRef, useState } from "react";

export interface PageResult<T> { items: T[]; total: number; offset: number; limit: number; }

export interface PagedRequest {
  offset: number;
  limit: number;
  sortBy?: string;
  sortDir: "asc" | "desc";
  signal: AbortSignal;
}

interface QueryState { offset: number; limit: number; sortBy?: string; sortDir: "asc" | "desc"; }

export interface PagedQuery<T> extends QueryState {
  items: T[];
  total: number;
  loading: boolean;
  error: string;
  update: (patch: Partial<QueryState>) => void;
}

/** 服务端分页的统一状态机：abort + 序号守卫；queryKey 变化回第 1 页；越界自动回退。 */
export function usePagedQuery<T>(options: {
  fetchPage: (req: PagedRequest) => Promise<PageResult<T>>;
  queryKey: string;
  initialLimit?: number;
}): PagedQuery<T> {
  const { queryKey, fetchPage, initialLimit = 15 } = options;
  const [state, setState] = useState<QueryState>({ offset: 0, limit: initialLimit, sortDir: "asc" });
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const seqRef = useRef(0);
  const keyRef = useRef(queryKey);
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;

  useEffect(() => {
    const keyChanged = keyRef.current !== queryKey;
    keyRef.current = queryKey;
    if (keyChanged && state.offset !== 0) {
      // 过滤器/执行 key 变了：回第 1 页，交给下一次 effect 拉取（本次不发请求，避免双请求）
      setState((cur) => ({ ...cur, offset: 0 }));
      return;
    }

    const controller = new AbortController();
    const seq = ++seqRef.current;
    setLoading(true);
    fetchRef.current({ ...state, signal: controller.signal })
      .then((page) => {
        if (seq !== seqRef.current) return;
        setItems(page.items);
        setTotal(page.total);
        setError("");
        if (page.items.length === 0 && page.total > 0 && state.offset >= page.total) {
          setState((cur) => ({ ...cur, offset: Math.floor((page.total - 1) / cur.limit) * cur.limit }));
        }
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        // 失败保留上一页数据（不清空 items）：分页翻页失败不该变成白屏
        setError(err instanceof Error ? err.message : "加载失败。");
      })
      .finally(() => { if (seq === seqRef.current) setLoading(false); });
    return () => controller.abort();
  }, [state, queryKey]);

  const update = useCallback(
    (patch: Partial<QueryState>) => setState((cur) => ({ ...cur, ...patch })),
    [],
  );

  return { items, total, loading, error, ...state, update };
}
```

> 关键点：`keyChanged && offset !== 0` 时只改 offset 并 return（下一次 effect 用新 key 拉），
> 避免「旧 key 的第 N 页」与「新 key 的第 1 页」各发一次请求。

- [ ] **Step 2: 表格组件（渲染 + antd 分页/排序桥接）**

```tsx
import { Alert, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { PagedQuery } from "../hooks/usePagedQuery";

export interface PagedTableProps<T extends object> {
  query: PagedQuery<T>;
  columns: ColumnsType<T>;
  rowKey: (record: T) => string;
  scrollX?: number;
  emptyText?: string;
}

/** 服务端分页表：antd Table 的受控分页/排序桥接到 PagedQuery，错误不清空已有行。 */
export function PagedTable<T extends object>({ query, columns, rowKey, scrollX, emptyText }: PagedTableProps<T>) {
  return (
    <>
      {query.error ? (
        <Alert type="error" showIcon message="明细加载失败" description={query.error} style={{ marginBottom: 12 }} />
      ) : null}
      <Table<T>
        rowKey={rowKey}
        columns={columns}
        dataSource={query.items}
        loading={query.loading}
        size="middle"
        locale={{ emptyText }}
        scroll={scrollX ? { x: scrollX } : undefined}
        pagination={{
          current: Math.floor(query.offset / query.limit) + 1,
          pageSize: query.limit,
          total: query.total,
          showSizeChanger: true,
          showTotal: (value) => `共 ${value} 条`,
        }}
        onChange={(pagination, _filters, sorter) => {
          const limit = pagination.pageSize ?? query.limit;
          const offset = ((pagination.current ?? 1) - 1) * limit;
          const single = Array.isArray(sorter) ? sorter[0] : sorter;
          query.update({
            offset,
            limit,
            sortBy: single?.order ? String(single.field) : undefined,
            sortDir: single?.order === "descend" ? "desc" : "asc",
          });
        }}
      />
    </>
  );
}
```

- [ ] **Step 3: `npm run build` 通过，Commit**

---

### Task 7: 选股结果页接入（含 P6 计数口径）

**Files:**
- Modify: `frontend/src/pages/Selections/SelectionResultPage.tsx`

- [ ] **Step 1:** `picks` 相关 state 删除；明细表换成 `PagedTable` + `usePagedQuery`，
  `queryKey = executionKey + "|" + selectedSummaryKeys.join(",")`，
  `fetchPage` 把选中的摘要行翻译成 `strategies=[...]`, `dates=[...]`（去重），
  `sortBy` 白名单列 `code/date/strategy`，`industry` 保持本地不可排序（后端白名单无此列）。
- [ ] **Step 2:** KPI 修正（P6）：`选中股票` 用 `query.total`（当前过滤下的条数）；
  副标题按 `counts` 输出——`trade_days > 1` → `N 条信号 · M 只（去重）· K 个交易日`，
  否则 `N 只`。
- [ ] **Step 3:** 策略摘要表保留（数据来自 `result.summary`），勾选联动不变。
- [ ] **Step 4:** `npm run build` + 浏览器实测：翻页、页大小、按代码排序、勾选过滤后页码回退、空过滤。
- [ ] **Step 5: Commit** `feat(ui): 选股结果页接入统一分页器；计数口径修正（条数/去重只数/交易日）`

---

### Task 8: 回归与性能验收

- [ ] **Step 1:** `.venv/bin/python -m pytest -q`（期望 642+新增 全绿）
- [ ] **Step 2:** 冷启动临时实例，量测（对照 spec §11 验收线）：
  - `GET /api/selection-results/20260821_175312_selection_ecd13a9e` → ≤0.5s、≤200KB
  - `GET .../picks?limit=50` → ≤0.3s（含首访自愈；二次 ≤0.1s）
  - 存量 10 个选股 run：首次列表访问后索引齐备（逐个 `summary.json` 存在）
- [ ] **Step 3:** 浏览器烟测截图/记录（首屏、翻页、过滤）
- [ ] **Step 4:** Commit 收尾 + 更新 `.superpowers/review-backlog.md`（记录已做项与遗留）。
