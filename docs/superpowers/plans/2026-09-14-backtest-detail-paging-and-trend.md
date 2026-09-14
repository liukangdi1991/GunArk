# P2 实施计划：回测明细分页 + 收益曲线服务端化

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans。步骤用 `- [ ]`。

**Goal:** `/backtest-results/{key}/report` 从 15.3MB 瘦到几十 KB；三张明细表改服务端分页；
收益曲线由后端聚合（前端只渲染）。

**Architecture:** 明细复用引擎已落盘的 `trades/skips/open_positions.parquet`（36,525 行 = 849KB）
+ 列投影 + 过滤下推 + 排序 + 切片；曲线为按 `(策略, 卖出日)` 的聚合，前端只画图。

**关键口径（必须与前端现行为逐字一致）**：`cost = buy_price × shares`（`total_cost` 两处产物都没有，
前端一直走这条兜底）；按 `sell_date` 分组（无卖出日的跳过）；`dailyReturnPct = cost>0 ? profit/cost*100 : 0`；
`cumulative*` 为按日期升序累加，`cumulativeReturnPct = cumCost>0 ? cumProfit/cumCost*100 : 0`；
胜负按 `profit>0 / profit<0`；**基线点（date="起点"）仍由前端插入**（纯展示）。

**白名单**（与前端现有可排序列对齐，只能多不能少）：
- trades：`sell_date/profit/return_pct/buy_date/industry/code`
- skips：`signal_date/buy_date/industry/code`
- open-positions：`buy_date/blocked_trading_days/unrealized_pnl/industry/code`
- `industry`/`name` 不在 parquet 中 → 服务端 join `stock_meta` 后排序（实测 8ms join / 62ms 全流程）

---

### Task 1: 明细分页 presenter + 三个路由

**Files:** Modify `presenters.py`、`routes/backtest.py`；Test `tests/interfaces/test_api_contract.py`

- [ ] **Step 1: 失败测试**

```python
def test_backtest_trades_page_contract(client):
    key = "20260820_100100_backtest_c3d4"
    page = client.get(f"/api/backtest-results/{key}/trades?limit=1").json()
    assert set(page) == {"items", "total", "offset", "limit"}
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["strategy_name"] == "B1战法"      # 展示名仍由注册表现查
    assert page["items"][0]["industry"]                        # join 补全


def test_backtest_trades_page_filters_and_sort(client):
    key = "20260820_100100_backtest_c3d4"
    by_profit = client.get(f"/api/backtest-results/{key}/trades?sort_by=profit&sort_dir=desc").json()
    assert [r["profit"] for r in by_profit["items"]] == sorted(
        [r["profit"] for r in by_profit["items"]], reverse=True
    )
    by_industry = client.get(f"/api/backtest-results/{key}/trades?sort_by=industry").json()
    assert by_industry["total"] == 2
    assert client.get(f"/api/backtest-results/{key}/trades?sort_by=drop_table").status_code == 400
    assert client.get(f"/api/backtest-results/{key}/trades?limit=201").status_code == 422
    assert client.get("/api/backtest-results/nope/trades").status_code == 404


def test_backtest_trades_page_tiles(client):
    """逐页翻完 == 全量序（默认序依赖 parquet 行序，同一前提钉子）。"""
    key = "20260820_100100_backtest_c3d4"
    full = [r["code"] for r in client.get(f"/api/backtest-results/{key}/trades?limit=50").json()["items"]]
    paged = [
        r["code"]
        for offset in range(len(full))
        for r in client.get(f"/api/backtest-results/{key}/trades?limit=1&offset={offset}").json()["items"]
    ]
    assert paged == full


def test_backtest_report_is_slim_with_trend(client):
    """报告瘦身：不再直出明细；曲线由服务端聚合，点数与口径与前端旧算法一致。"""
    key = "20260820_100100_backtest_c3d4"
    report = client.get(f"/api/backtest-results/{key}/report").json()

    assert set(report) == {"result", "trend", "counts"}
    assert report["counts"] == {"trade_count": 2, "skip_count": 0, "open_position_count": 0}
    assert len(report["trend"]) == 1
    series = report["trend"][0]
    assert series["strategy"] == "b1" and series["strategy_name"] == "B1战法"
    points = series["points"]
    assert [p["date"] for p in points] == ["2026-08-20", "2026-08-21"]
    first = points[0]
    # 口径：cost = buy_price × shares（产物无 total_cost）
    assert first["dailyCost"] == pytest.approx(100 * 10.0)
    assert first["dailyProfit"] == pytest.approx(300.0)
    assert first["dailyReturnPct"] == pytest.approx(30.0)
    assert first["cumulativeCost"] == pytest.approx(100 * 10.0)
    assert first["cumulativeReturnPct"] == pytest.approx(30.0)
    assert (first["tradeCount"], first["winCount"], first["lossCount"]) == (1, 1, 0)
```

- [ ] **Step 2: RED** → `pytest tests/interfaces/test_api_contract.py -q -k "backtest_trades_page or slim_with_trend"`

- [ ] **Step 3: 实现**

```python
BACKTEST_DATASETS = {
    "trades": ("trades", "trades.parquet"),
    "skips": ("skips", "skips.parquet"),
    "open-positions": ("open_positions", "open_positions.parquet"),
}
BACKTEST_SORT_KEYS = {
    "trades": {"sell_date": ("sell_date", "code"), "profit": ("profit", "code"),
               "return_pct": ("return_pct", "code"), "buy_date": ("buy_date", "code"),
               "industry": ("industry", "code"), "code": ("code",)},
    "skips": {"signal_date": ("signal_date", "code"), "buy_date": ("buy_date", "code"),
              "industry": ("industry", "code"), "code": ("code",)},
    "open-positions": {"buy_date": ("buy_date", "code"), "blocked_trading_days": ("blocked_trading_days", "code"),
                       "unrealized_pnl": ("unrealized_pnl", "code"), "industry": ("industry", "code"),
                       "code": ("code",)},
}


def backtest_rows_page(key, dataset, *, offset=0, limit=50, strategies=None, sort_by=None, sort_dir="asc"):
    """明细分页（trades/skips/open-positions 共用）：parquet 优先，缺则回落 result.json。

    行业/名称不在 parquet 里 → 需要排序或展示时 join stock_meta（只作用于当页 + 排序）。
    """
    # 1) 取帧：scan_parquet → 过滤 strategies → 需要列
    # 2) total = 过滤后计数
    # 3) 排序：白名单键 + 兜底次序（非索引列先 join stock_meta）
    # 4) slice(offset, limit) → 补 name/industry/strategy_name
```

```python
@router.get("/backtest-results/{execution_key}/trades")
def get_backtest_trades(execution_key, request, offset=Query(0, ge=0), limit=Query(50, ge=1, le=200),
                        strategies: list[str] | None = Query(None), sort_by=None,
                        sort_dir: str = Query("asc", pattern="^(asc|desc)$")):
    # 同 skips / open-positions（dataset 不同，白名单不同）
```

- [ ] **Step 4: GREEN** + `pytest tests/interfaces -q`

---

### Task 2: 报告瘦身 + 曲线聚合

- [ ] **Step 1: 失败测试**：Task 1 的 `test_backtest_report_is_slim_with_trend` 已覆盖形状与口径。
- [ ] **Step 2: 实现**

```python
def _backtest_trend(key: str, bt_dir: Path) -> list[dict]:
    """按 (策略, 卖出日) 聚合：口径与前端旧算法逐字一致（见计划头注）。"""
    # 源：trades.parquet（快）→ 缺失时用 result.json 的 trades 建帧
    # cost = when(buy_price>0 & shares>0).then(buy_price*shares).otherwise(0)
    # group_by(strategy, sell_date) → dailyCost/dailyProfit/tradeCount/winCount/lossCount
    # 排序后按 strategy 计算 cum_sum 与比率
```

`backtest_result_payload(include_report=True)` 返回 `{"result": run, "trend": _backtest_trend(...),
"counts": {trade_count, skip_count, open_position_count}}`（去掉 trades/skips/open_positions/equity/artifacts）。

- [ ] **Step 3: GREEN**

---

### Task 3: 前端改造

**Files:** `types/backtest.ts`、`services/backtests.ts`、`pages/Backtests/BacktestReportPage.tsx`、
`pages/Backtests/components/BacktestReportTables.tsx`、`.../TradeReturnTrendCards.tsx`

- [ ] **Step 1:** 类型与服务（分页响应、trend 形状、报告新形状；`getBacktestTrades/Skips/OpenPositions` 带 signal）。
- [ ] **Step 2:** 三张表换 `PagedTable` + `usePagedQuery`：`queryKey = key + "|" + selectedStrategies.join(",")`；
  列定义保留原渲染，去掉本地 sorter、白名单列改 `sorter: true`；`industry` 列可排（服务端 join）。
- [ ] **Step 3:** `TradeReturnTrendCards` 改为吃 `trend`（按选中策略过滤），删掉本地聚合与 `tradeCost`；
  基线点保留前端插入。
- [ ] **Step 4:** 报告页接线（KPI 不变；筛选变更同时影响三张表与曲线）。
- [ ] **Step 5:** `npm run build`；浏览器实测：翻页/排序/勾选策略过滤/曲线点数与数值。

---

### Task 4: 验收

- [ ] **Step 1:** 全量 `pytest -q`（673+ 新增）全绿。
- [ ] **Step 2:** 冷启动临时实例计时：`/report` 15.3MB → **≤60KB** 且 ≤0.4s；
  `/trades?limit=15` ≤0.3s（首访）/ ≤0.1s（稳态）；`/skips`、`/open-positions` 同。
- [ ] **Step 3:** 浏览器端到端（最坏样本 36,525 笔）：三表分页/排序/过滤、曲线渲染且点数 = 58。
- [ ] **Step 4:** 更新 backlog（P2 完成）与 spec 状态；给用户「改了什么/实测/回退」。
