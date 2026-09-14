# P3/P4 实施计划：列表端点读索引 + jobs.result_json 瘦身

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans。步骤用 `- [ ]` 跟踪。

**Goal:** 回测历史列表不再解析 15.9MB `result.json`（改读索引）；作业终态不再把 12.88MB
明细写进 SQLite。

**Architecture:** 复用 P1 的索引模块（`.index/` 子目录、字段级签名、原子写、失败降级、启动预热），
为回测补一个同构摘要；presenter 改为「**facts → 展示行**」单一实现源，索引路径与回落路径在
facts 之后共用同一段代码。

**关键决策（与 §4.2.4 的修正）**：回测索引**从 `result.json` 构建**（而非 parquet）——与旧路径
逐字段一致（同一份输入 + 同一套算术），`trades.parquet` 只用于分页。构建一次性 0.2s（预热覆盖）。

---

### Task 1: 索引模块支持回测（facts 聚合 + 签名 + 预热扩展）

**Files:** Modify `trendradar/infrastructure/storage/artifact_index.py`；
Test `tests/infrastructure/test_artifact_index.py`

- [ ] **Step 1: 失败测试**

```python
def _write_backtest(bt_dir: Path, *, trades, skips=(), open_positions=(), equity=(), mtime=None) -> Path:
    bt_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "metrics": {"trade_count": len(trades), "total_return_pct": 1.5},
        "trade_count": len(trades), "skip_count": len(skips),
        "trades": list(trades), "skips": list(skips),
        "open_positions": list(open_positions), "equity_curve": list(equity),
    }
    path = bt_dir / "result.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_ensure_backtest_index_builds_summary(tmp_path):
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
        open_positions=[{"strategy": "b1", "code": "600519", "buy_date": "2026-08-07", "unrealized_pnl": 12.0}],
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
    assert s["source_signature"] == {"result.json": (bt_dir / "result.json").stat().st_mtime_ns}


def test_ensure_backtest_index_rebuilds_after_source_change(tmp_path):
    bt_dir = tmp_path / "backtest"
    path = _write_backtest(bt_dir, trades=[{"strategy": "b1", "code": "000001", "buy_date": "2026-08-01",
                                            "sell_date": "2026-08-05", "buy_price": 1.0, "shares": 1,
                                            "profit": 0.0, "return_pct": 0.0}])
    assert ensure_backtest_index("k1", bt_dir).summary["trade_count"] == 1
    _write_backtest(bt_dir, trades=[])
    future = time.time() + 10
    os.utime(path, (future, future))
    assert ensure_backtest_index("k1", bt_dir).summary["trade_count"] == 0


def test_warm_indexes_covers_backtests_too(tmp_path):
    root = tmp_path / "objects" / "executions"
    _write_signals(root / "s1" / "selection")
    _write_backtest(root / "b1" / "backtest", trades=[])

    assert warm_indexes(root) == 2
    assert (root / "s1" / "selection" / ".index" / "summary.json").is_file()
    assert (root / "b1" / "backtest" / ".index" / "summary.json").is_file()
```

- [ ] **Step 2: 跑测试确认 RED** → `pytest tests/infrastructure/test_artifact_index.py -q`

- [ ] **Step 3: 实现**

```python
BACKTEST_RESULT_NAME = "result.json"

@dataclass(frozen=True)
class BacktestIndex:
    summary_path: Path
    summary: dict


def summarize_backtest_result(result: dict) -> dict:
    """result.json → 机器事实（窗口/计数/每策略聚合）。**回测聚合的单一实现源**。

    presenter 的展示行、索引落盘都从这里出发，保证两条读路径逐字段一致。
    区间口径与原 `_backtest_window` 相同：有净值曲线用曲线首尾，否则用
    trades(buy/sell) / open_positions(buy, mark||target_sell) / skips(buy) 的 min/max。
    """
    trades = result.get("trades") or []
    skips = result.get("skips") or []
    open_positions = result.get("open_positions") or []
    equity = result.get("equity_curve") or []

    by_strategy: dict[str, dict] = {}

    def row_for(sid: str) -> dict:
        return by_strategy.setdefault(
            sid,
            {"strategy": sid, "trade_count": 0, "skip_count": 0, "open_positions": 0, "wins": 0,
             "realized_profit_sum": 0.0, "invested_notional_sum": 0.0, "unrealized_pnl": 0.0},
        )

    for t in trades:
        row = row_for(str(t.get("strategy") or "未知"))
        row["trade_count"] += 1
        row["realized_profit_sum"] += float(t.get("profit") or 0.0)
        row["invested_notional_sum"] += float(t.get("buy_price") or 0.0) * float(t.get("shares") or 0)
        if float(t.get("return_pct") or 0.0) > 0:
            row["wins"] += 1
    for s in skips:
        row_for(str(s.get("strategy") or "未知"))["skip_count"] += 1
    for p in open_positions:
        row = row_for(str(p.get("strategy") or "未知"))
        row["open_positions"] += 1
        row["unrealized_pnl"] += float(p.get("unrealized_pnl") or 0.0)

    if equity:
        window = (equity[0].get("date"), equity[-1].get("date"))
    else:
        dates: list[str] = []
        for t in trades:
            dates += [d for d in (t.get("buy_date"), t.get("sell_date")) if d]
        for p in open_positions:
            dates += [d for d in (p.get("buy_date"), p.get("mark_date") or p.get("target_sell_date")) if d]
        dates += [s.get("buy_date") for s in skips if s.get("buy_date")]
        window = (min(dates), max(dates)) if dates else (None, None)

    return {
        "window_from": window[0], "window_to": window[1],
        "trade_count": len(trades), "skip_count": len(skips), "open_position_count": len(open_positions),
        "by_strategy": [by_strategy[k] for k in sorted(by_strategy)],
    }


def backtest_source_signature(bt_dir: Path) -> dict[str, int] | None:
    path = bt_dir / BACKTEST_RESULT_NAME
    try:
        if not path.is_file():
            return None
        return {BACKTEST_RESULT_NAME: path.stat().st_mtime_ns}
    except OSError:
        return None


def ensure_backtest_index(execution_key: str, bt_dir: Path, *, force: bool = False) -> BacktestIndex | None:
    """与选股索引同构：签名校验 / 原子写 / 失败降级（沿用同一把写锁与 INDEX_* 常量）。"""
    signature = backtest_source_signature(bt_dir)
    if signature is None:
        return None
    summary_path = bt_dir / INDEX_DIRNAME / SUMMARY_NAME
    if not force:
        cached = _load_fresh_summary(summary_path, signature)
        if cached is not None:
            return BacktestIndex(summary_path=summary_path, summary=cached)
    try:
        result = _read_signals(bt_dir / BACKTEST_RESULT_NAME)   # 复用 JSON 读取
        summary = {"schema_version": INDEX_SCHEMA_VERSION, "source_signature": signature,
                   **summarize_backtest_result(result)}
        with INDEX_WRITE_LOCK:
            atomic_write_text(summary_path, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:
        logger.warning("backtest index build failed for %s: %s", execution_key, exc)
        return None
    return BacktestIndex(summary_path=summary_path, summary=summary)
```

并把 `_load_fresh_summary` 改为「不再要求 picks.parquet 存在」（回测索引只有 summary）：
拆成 `_load_fresh_summary(summary_path, signature, *, require_picks: Path | None = None)`；
`warm_indexes` 对每个执行目录分别尝试 selection 与 backtest。

- [ ] **Step 4: GREEN**；`pytest tests/infrastructure/test_artifact_index.py -q`

---

### Task 2: presenter 改「facts → 展示行」单一实现源 + 列表读索引

**Files:** Modify `trendradar/interfaces/api/presenters.py`；Test `test_api_contract.py`

- [ ] **Step 1: 失败测试（等价性 + 列表不再读大文件）**

```python
def test_backtest_list_matches_detail_summary(client, monkeypatch):
    """列表与详情的汇总必须逐字段一致（索引路径 vs 直读路径）。"""
    listed = client.get("/api/backtest-results?limit=10").json()["results"]
    run = next(r for r in listed if r["execution_key"] == "20260820_100100_backtest_c3d4")
    detail = client.get("/api/backtest-results/20260820_100100_backtest_c3d4").json()["result"]
    assert run == detail


def test_backtest_list_reads_index_not_result_json(client, tmp_path):
    """列表走索引：把 result.json 挪走（模拟不可解析）后列表仍正常，且不读它。"""
    # 先生成索引（等价于预热/首访）
    assert client.get("/api/backtest-results/20260820_100100_backtest_c3d4").status_code == 200

    import trendradar.interfaces.api.presenters as presenters

    reads: list[str] = []
    real = presenters._read_json

    def spy(path, default=None):
        reads.append(Path(path).name)
        return real(path, default)

    monkeypatch.setattr(presenters, "_read_json", spy)
    listed = client.get("/api/backtest-results?limit=10").json()["results"]

    assert any(r["execution_key"] == "20260820_100100_backtest_c3d4" for r in listed)
    assert "result.json" not in reads, f"列表不该再解析大产物：{reads}"
```

- [ ] **Step 2: RED**

- [ ] **Step 3: 实现**：presenter 端新增 `_backtest_facts(key, bt_dir)`（索引优先，回落
  `summarize_backtest_result(_read_json(result.json))`）+ 把现有 `_backtest_summary(result, metrics)`
  拆成 `_backtest_summary_rows(facts, metrics, config, window)`（facts 之后共用）；`_backtest_window`
  删除（窗口来自 facts）。`backtest_result_payload(include_report=False)` 不再读 `result.json`。

- [ ] **Step 4: GREEN** + `pytest tests/interfaces -q`

---

### Task 3: worker 落盘建索引 + `jobs.result_json` 瘦身（P4）

**Files:** Modify `trendradar/app/services/backtest_service.py`；Test `tests/app/test_backtest_index_hooked.py`

- [ ] **Step 1: 失败测试**：跑一次真实回测（复用 `tests/app` 既有夹具手法：小宇宙 + JobExecutor），断言
  `backtest/.index/summary.json` 存在，且 `jobs.result_json` 长度 < 4096 且不含 `trades` 键。

- [ ] **Step 2: RED**

- [ ] **Step 3: 实现**

```python
        ensure_backtest_index(ctx.job_id, out_dir)      # 落盘后建索引（幂等）

    ctx.succeed(                                        # P4：只存摘要，明细只落盘
        {
            "metrics": output.get("metrics"),
            "trade_count": output.get("trade_count", 0),
            "skip_count": output.get("skip_count", 0),
            "open_position_count": len(output.get("open_positions") or []),
        }
    )
```

- [ ] **Step 4: GREEN** + `pytest tests/app -q`

---

### Task 4: 验收

- [ ] **Step 1:** 全量 `pytest -q`（当前 664 项）全绿。
- [ ] **Step 2:** 冷启动临时实例计时：
  - `GET /api/backtest-results?limit=50`：改造前 0.29s → 目标 **≤50ms**
  - `GET /api/backtest-results/{key}`（详情 run 段）：目标 ≤50ms
  - 14 个存量 run 的 `backtest/.index/summary.json` 由启动预热自动补齐
  - `jobs.result_json` 行长 ≤4KB（新作业）
- [ ] **Step 3:** 浏览器烟测（回测工作台列表 + 报告页仍正常；报告页仍 15MB → P2 再瘦）。
- [ ] **Step 4:** 更新 backlog 与 spec 状态。
