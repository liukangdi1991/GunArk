# 回测买卖标记落到个股日K（B/S）实现计划

> **执行方式**：本仓库 `AGENTS.md` 规定「涉及子 Agent 或并行 Agent 必须先获得用户明确授权」——
> **未获授权时按内联执行**（逐任务做、每任务跑验证、每任务提交）。获授权才可改用 subagent 逐任务执行。
> 步骤用 `- [ ]` 勾选跟踪。

**Goal:** 从回测报告点进个股日K时，把该次回测在这只票上的买入/卖出以 `B`/`S` 标在对应 K 线上，
十字光标弹框里能看到成交明细（价格/股数/策略）。

**Architecture:** 后端新增一个**纯读**端点 `GET /api/backtest-results/{key}/marks?code=`，
复用既有 `_backtest_dataset_frame`（parquet 优先 → `result.json` 回落）；前端把 marks 按**日期**归并到 bar 上
（新增纯函数 `attachMarks`），再以自定义指标 `BTMARK` 的两个 `text` figure 画在主图；位置取 `bar.low`/`bar.high`，
与复权口径无关。

**Tech Stack:** FastAPI + Polars（后端）；React 18 + antd 5 + klinecharts 10.0.3（前端）。
无前端测试框架 ⇒ 纯函数用 Bun 直跑断言脚本，端到端用浏览器实测。

**Spec:** `docs/superpowers/specs/2026-09-15-backtest-marks-on-kline-design.md`（需求编号 M1–M7 引用该文）

---

## 文件结构（先定边界）

| 文件 | 职责 | 动作 |
|---|---|---|
| `trendradar/interfaces/api/presenters.py` | 新增 `backtest_marks(key, code)`：读两个数据集 → 标记录 → 排序（与 `backtest_rows_page` 同层） | 改 |
| `trendradar/interfaces/api/routes/backtest.py` | 新增只读路由 + 状态码映射（400/422/404/500），不写 polars | 改 |
| `tests/interfaces/test_api_contract.py` | marks 端点契约用例（复用既有 `client` fixture） | 追加 |
| `frontend/src/types/backtest.ts` | `BacktestMark` / `BacktestMarksResponse` | 改 |
| `frontend/src/services/backtests.ts` | `getBacktestMarks(key, code, {signal})` | 改 |
| `frontend/src/utils/backtestMarks.ts` | **新建**：`attachMarks(bars, marks)` 纯函数（日期→bar 归并、合并计数）+ `countMarks` | 建 |
| `frontend/src/types/kline.ts` | `KlineBar.bt_buy/bt_sell`、`BarMark` | 改 |
| `frontend/src/pages/Stocks/KlineChart.tsx` | `BTMARK` 指标注册 + 挂载/卸载 + `toKlineData` 带字段 + 弹框加一行 | 改 |
| `frontend/src/pages/Stocks/StockKlinePage.tsx` | `bt` 解析、marks 拉取、提示条、`payloadWithMarks` + 图表 key | 改 |
| `frontend/src/pages/Backtests/components/BacktestReportTables.tsx` | 交易明细 / 期末未平仓两张表的代码链接带 `bt` | 改 |

---

## Task 1: 后端契约用例（RED）

**Files:**
- Test: `tests/interfaces/test_api_contract.py`（文件末尾追加分节）

- [ ] **Step 1: 追加用例**

fixture 已有产物（`tests/interfaces/test_api_contract.py:219` 的 `client`）：
key `20260820_100100_backtest_c3d4`；trades：`bbi_kdj_b1`/000001（买 08-11 @10.0，卖 08-18 @10.8，4000 股，profit 3100.0，return_pct 7.8）、
`peak_kdj`/000333；skips：`bbi_kdj_b1`/600519；open_positions：`bbi_kdj_b1`/000002（买 08-11 @10.0，4000 股，unrealized_pnl -10810.0，unrealized_return_pct -27.0）。

```python
# ---------------------------------------------------------------------------
# backtest marks（个股日K上的买卖标记）
# ---------------------------------------------------------------------------

BT_KEY = "20260820_100100_backtest_c3d4"


def test_marks_returns_buy_and_sell_for_trade(client):
    resp = client.get(f"/api/backtest-results/{BT_KEY}/marks?code=000001")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["execution_key"] == BT_KEY and payload["code"] == "000001"
    items = payload["items"]
    assert [i["side"] for i in items] == ["buy", "sell"]  # 按 (date, side) 升序
    buy, sell = items
    assert buy["date"] == "2026-08-11" and buy["price"] == 10.0 and buy["shares"] == 4000
    assert buy["strategy"] == "bbi_kdj_b1" and buy["strategy_name"] == "B1战法"
    assert buy["source"] == "trade" and buy["profit"] is None and buy["return_pct"] is None
    assert sell["date"] == "2026-08-18" and sell["price"] == 10.8
    assert sell["profit"] == 3100.0 and sell["return_pct"] == 7.8


def test_marks_open_position_yields_buy_only(client):
    resp = client.get(f"/api/backtest-results/{BT_KEY}/marks?code=000002")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    only = items[0]
    assert only["side"] == "buy" and only["source"] == "open_position"
    assert only["date"] == "2026-08-11" and only["price"] == 10.0 and only["shares"] == 4000
    assert only["profit"] == -10810.0 and only["return_pct"] == -27.0


def test_marks_empty_for_skipped_code(client):
    """600519 只出现在 skips 里：没有买卖点，但端点必须 200 空集合（不是 404）。"""
    resp = client.get(f"/api/backtest-results/{BT_KEY}/marks?code=600519")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_marks_unknown_execution_key_404(client):
    resp = client.get("/api/backtest-results/20260101_000000_backtest_zzzz/marks?code=000001")
    assert resp.status_code == 404


def test_marks_rejects_path_traversal_400(client):
    resp = client.get("/api/backtest-results/..%2Fetc/marks?code=000001")
    assert resp.status_code == 400


@pytest.mark.parametrize("bad_code", ["abc123", "00066", "1234567"])
def test_marks_rejects_bad_code_422(client, bad_code):
    resp = client.get(f"/api/backtest-results/{BT_KEY}/marks?code={bad_code}")
    assert resp.status_code == 422


def test_marks_survives_deleted_index(client):
    """索引目录是纯衍生加速器：删掉后 marks 仍逐字一致（回落 result.json）。"""
    first = client.get(f"/api/backtest-results/{BT_KEY}/marks?code=000001").json()
    index_dir = tmp_path_index_dir()
    if index_dir.is_dir():
        shutil.rmtree(index_dir)
    again = client.get(f"/api/backtest-results/{BT_KEY}/marks?code=000001").json()
    assert again == first


def test_marks_is_read_only(client):
    """纯读端点：不得顺手建索引（建索引是 worker 与启动预热的事）。"""
    index_dir = tmp_path_index_dir()
    if index_dir.is_dir():
        shutil.rmtree(index_dir)
    assert client.get(f"/api/backtest-results/{BT_KEY}/marks?code=000001").status_code == 200
    assert not index_dir.exists()
```

再加一条「自造产物」用例，覆盖 spec §7.1 的 3b/3c/3d（多策略同股 / trades 与 open_positions 并存 / 顺序稳定）——
按既有 `test_selection_picks_page_recovers_from_corrupt_index` 的写法直接往 `tmp_path/storage` 里写：

```python
def test_marks_multi_strategy_and_open_position_both_listed(client, tmp_path):
    key = "20260821_090000_backtest_m9n8"
    bt_dir = tmp_path / "storage" / "objects" / "executions" / key / "backtest"
    bt_dir.mkdir(parents=True)
    (bt_dir / "result.json").write_text(
        json.dumps(
            {
                "trades": [
                    {"strategy": "bbi_kdj_b1", "code": "000001", "signal_date": "2026-08-10",
                     "buy_date": "2026-08-11", "sell_date": "2026-08-18", "buy_price": 10.0,
                     "sell_price": 10.8, "shares": 100, "profit": 80.0, "return_pct": 8.0},
                    {"strategy": "peak_kdj", "code": "000001", "signal_date": "2026-08-10",
                     "buy_date": "2026-08-11", "sell_date": "2026-08-19", "buy_price": 10.1,
                     "sell_price": 9.9, "shares": 200, "profit": -40.0, "return_pct": -2.0},
                ],
                "open_positions": [
                    {"strategy": "bbi_kdj_b1", "code": "000001", "signal_date": "2026-08-12",
                     "buy_date": "2026-08-13", "target_sell_date": "2026-08-20", "shares": 300,
                     "entry_price": 9.5, "unrealized_pnl": 12.0, "unrealized_return_pct": 4.0},
                ],
                "skips": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    url = f"/api/backtest-results/{key}/marks?code=000001"
    items = client.get(url).json()["items"]
    assert len(items) == 5  # 2 买 + 2 卖（两策略）+ 1 未平仓买
    assert [(i["side"], i["source"], i["strategy"]) for i in items] == [
        ("buy", "trade", "bbi_kdj_b1"),
        ("buy", "trade", "peak_kdj"),
        ("buy", "open_position", "bbi_kdj_b1"),
        ("sell", "trade", "bbi_kdj_b1"),
        ("sell", "trade", "peak_kdj"),
    ]
    assert client.get(url).json() == client.get(url).json()  # 顺序稳定：两次逐字一致
```

`tmp_path_index_dir()` 是本文件内新加的取索引目录小工具（放在用例上方）：

```python
def tmp_path_index_dir() -> Path:
    from trendradar.interfaces.api.presenters import _executions_root

    return _executions_root() / BT_KEY / "backtest" / ".index"
```

`Path`/`shutil` 若未导入则在文件头补：`import shutil` 与 `from pathlib import Path`（先看文件头已有导入再决定）。

- [ ] **Step 2: 跑用例确认失败（RED）**

Run: `.venv/bin/python -m pytest tests/interfaces/test_api_contract.py -k marks -q`
Expected: FAIL —— 端点不存在，`resp.status_code == 404`（FastAPI 对未注册路径返回 404）

- [ ] **Step 3: 提交测试**

```bash
git add tests/interfaces/test_api_contract.py
git commit -m "test(marks): 钉住回测买卖标记端点契约（RED）"
```

---

## Task 2: presenter + 路由（GREEN）

**Files:**
- Modify: `trendradar/interfaces/api/presenters.py`（`backtest_rows_page` 之后）
- Modify: `trendradar/interfaces/api/routes/backtest.py`（`get_backtest_open_positions` 之后）

- [ ] **Step 1: presenter 实现**

```python
def backtest_marks(key: str, code: str) -> dict | None:
    """该回测在该股上的买卖标记（B/S）：一笔成交出一买一卖，期末未平仓只出买。

    返回 None 表示**该 key 没有回测产物**（路由据此 404）；产物在但该股无成交 → 空 items。
    纯读：不建索引、不写盘（索引由 worker 与启动预热负责）。
    """
    import polars as pl  # 与同文件其它函数一致：函数体内局部导入

    bt_dir = _executions_root() / key / "backtest"
    has_source = (bt_dir / "result.json").is_file()

    trades = _backtest_dataset_frame(key, "trades", bt_dir)
    positions = _backtest_dataset_frame(key, "open-positions", bt_dir)
    if trades is None and positions is None:
        return None if not has_source else {"execution_key": key, "code": code, "items": []}

    def mark(*, side: str, date_, price, shares, strategy: str, signal_date, source: str,
             profit, return_pct) -> dict:
        return {
            "side": side,
            "date": str(date_),
            "price": float(price) if price is not None else None,
            "shares": int(shares) if shares is not None else None,
            "strategy": strategy,
            "strategy_name": _strategy_display_name(strategy),
            "signal_date": str(signal_date) if signal_date is not None else None,
            "source": source,
            "profit": float(profit) if profit is not None else None,
            "return_pct": float(return_pct) if return_pct is not None else None,
        }

    items: list[dict] = []
    if trades is not None:
        for row in trades.filter(pl.col("code") == code).collect().iter_rows(named=True):
            items.append(
                mark(
                    side="buy", date_=row.get("buy_date"), price=row.get("buy_price"),
                    shares=row.get("shares"), strategy=row.get("strategy") or "",
                    signal_date=row.get("signal_date"), source="trade",
                    profit=None, return_pct=None,
                )
            )
            items.append(
                mark(
                    side="sell", date_=row.get("sell_date"), price=row.get("sell_price"),
                    shares=row.get("shares"), strategy=row.get("strategy") or "",
                    signal_date=row.get("signal_date"), source="trade",
                    profit=row.get("profit"), return_pct=row.get("return_pct"),
                )
            )
    if positions is not None:
        for row in positions.filter(pl.col("code") == code).collect().iter_rows(named=True):
            items.append(
                mark(
                    side="buy", date_=row.get("buy_date"), price=row.get("entry_price"),
                    shares=row.get("shares"), strategy=row.get("strategy") or "",
                    signal_date=row.get("signal_date"), source="open_position",
                    profit=row.get("unrealized_pnl"), return_pct=row.get("unrealized_return_pct"),
                )
            )

    items.sort(key=lambda m: (m["date"] or "", m["side"], m["strategy"], m.get("signal_date") or ""))
    return {"execution_key": key, "code": code, "items": items}
```

注意：本函数与 `backtest_rows_page` 同在 `presenters.py`，`_backtest_dataset_frame` / `_strategy_display_name` /
`_executions_root` 直接调用即可，**不要**写自引用 import；polars 按本文件惯例在函数体内局部导入。

- [ ] **Step 2: 路由实现**

在 `trendradar/interfaces/api/routes/backtest.py` 的 `get_backtest_open_positions` 之后追加：

```python
@router.get("/backtest-results/{execution_key}/marks")
def get_backtest_marks(
    execution_key: str,
    request: FastAPIRequest,
    code: str = Query(pattern=r"^\d{6}$"),
):
    """个股日K上的回测买卖标记（B/S）：只回该股该次回测的成交，不分页。"""
    from trendradar.interfaces.api.presenters import BacktestSourceUnreadable, backtest_marks

    if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
        raise HTTPException(status_code=400, detail="Invalid execution_key")
    try:
        payload = backtest_marks(execution_key, code)
    except BacktestSourceUnreadable as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"Backtest result not found for '{execution_key}'"
        )
    return payload
```

- [ ] **Step 3: 跑用例（GREEN）**

Run: `.venv/bin/python -m pytest tests/interfaces/test_api_contract.py -k marks -q`
Expected: PASS（8 项全绿）

- [ ] **Step 4: 跑整个契约文件（防回归）**

Run: `.venv/bin/python -m pytest tests/interfaces/test_api_contract.py -q`
Expected: 全绿（既有用例不受影响）

- [ ] **Step 5: 提交**

```bash
git add trendradar/interfaces/api/presenters.py trendradar/interfaces/api/routes/backtest.py
git commit -m "feat(api): 回测买卖标记端点 /backtest-results/{key}/marks（B/S 数据源）"
```

---

## Task 3: 前端类型与服务函数（**必须先做**：Task 4 的 `KlineBar.bt_buy` 依赖本任务的类型）

**Files:**
- Modify: `frontend/src/types/backtest.ts`
- Modify: `frontend/src/types/kline.ts`
- Modify: `frontend/src/services/backtests.ts`

- [ ] **Step 1: 类型**

```ts
/** 回测在个股上的一次买卖（B/S 标记数据源；来源见 source）。 */
export interface BacktestMark {
  side: "buy" | "sell";
  date: string;
  price: number | null;
  shares: number | null;
  strategy: string;
  /** 注册表查不到已删除策略时为 null，显示层回落 strategy */
  strategy_name?: string | null;
  signal_date?: string | null;
  source: "trade" | "open_position";
  /** 买入标记恒为 null；卖出带该笔盈亏/收益率 */
  profit: number | null;
  return_pct: number | null;
}

export interface BacktestMarksResponse {
  execution_key: string;
  code: string;
  items: BacktestMark[];
}
```

- [ ] **Step 2: K 线类型（`frontend/src/types/kline.ts`）**

文件头补 `import type { BacktestMark } from "./backtest";`，并新增：

```ts
/** 挂在 bar 上的回测标记（由 utils/backtestMarks.attachMarks 写入）。 */
export interface BarMark {
  /** 显示文本：`B`、`S`、`B×2`、`S×3` */
  label: string;
  count: number;
  /** 画图用的 y 值：买入取 bar.low、卖出取 bar.high */
  value: number | null;
  items: BacktestMark[];
}
```

`KlineBar` 末尾追加两个字段：

```ts
  /** 回测买卖标记（仅当 URL 带 bt 时由 attachMarks 写入） */
  bt_buy?: BarMark | null;
  bt_sell?: BarMark | null;
```

- [ ] **Step 3: 服务函数**（`getBacktestRows` 之后）

```ts
/** 个股日K上的回测买卖标记（不分页）：失败只影响提示条，不影响 K 线。 */
export function getBacktestMarks(
  executionKey: string,
  code: string,
  signal?: AbortSignal,
): Promise<BacktestMarksResponse> {
  const params = new URLSearchParams({ code });
  return requestJson<BacktestMarksResponse>(
    `/api/backtest-results/${encodeURIComponent(executionKey)}/marks?${params.toString()}`,
    { signal },
  );
}
```

- [ ] **Step 4: 类型检查**

Run: `cd frontend && npx tsc -b`
Expected: 无输出（通过）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/types/backtest.ts frontend/src/types/kline.ts frontend/src/services/backtests.ts
git commit -m "feat(ui): marks 类型（响应 + bar 挂载字段）与取数函数"
```

---

## Task 4: `attachMarks` 纯函数（先 RED 后 GREEN）

**Files:**
- Create: `frontend/src/utils/backtestMarks.ts`
- Throwaway: `/tmp/check-attach-marks.ts`（**不进仓库**，验证完删除）

- [ ] **Step 1: 写断言脚本（RED）**

创建 `/tmp/check-attach-marks.ts`：

```ts
import { attachMarks, countMarks } from "/workspace/github/GunArk/frontend/src/utils/backtestMarks";

const bar = (date: string, low = 10, high = 11) =>
  ({ date, low, high, open: 10, close: 10.5, volume: 1, amount: 1, timestamp: 0 } as any);
const mark = (side: "buy" | "sell", date: string, strategy = "s1") =>
  ({ side, date, price: 10, shares: 100, strategy, strategy_name: null, source: "trade", profit: null, return_pct: null } as any);

let failed = 0;
const eq = (name: string, got: unknown, want: unknown) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `\n  got=${JSON.stringify(got)}\n  want=${JSON.stringify(want)}`}`);
};

// 日线：命中与不命中
const bars = [bar("2026-08-10"), bar("2026-08-11"), bar("2026-08-12")];
let out = attachMarks(bars, [mark("buy", "2026-08-11"), mark("sell", "2026-08-12")]);
eq("日线命中买入", out[1].bt_buy?.label, "B");
eq("日线命中卖出", out[2].bt_sell?.label, "S");
eq("未命中不写字段", out[0].bt_buy ?? null, null);
eq("命中计数", [out[1].bt_buy?.count, out[2].bt_sell?.count], [1, 1]);
eq("买入值取 low、卖出值取 high", [out[1].bt_buy?.value, out[2].bt_sell?.value], [10, 11]);
eq("原数组不被改写", bars[1].bt_buy ?? null, null);

// 同根同向多笔 → 计数
out = attachMarks(bars, [mark("buy", "2026-08-11", "a"), mark("buy", "2026-08-11", "b")]);
eq("同根同向合并为 ×2", out[1].bt_buy?.label, "B×2");
eq("明细保留两笔", out[1].bt_buy?.items.length, 2);

// 同根既有买又有卖
out = attachMarks(bars, [mark("buy", "2026-08-11"), mark("sell", "2026-08-11")]);
eq("同根买卖各自独立", [out[1].bt_buy?.label, out[1].bt_sell?.label], ["B", "S"]);

// 周线（bar.date 是周期最后一个交易日）：08-11/08-13 都归到 08-14
const weekly = [bar("2026-08-07"), bar("2026-08-14"), bar("2026-08-21")];
out = attachMarks(weekly, [mark("buy", "2026-08-11"), mark("buy", "2026-08-13")]);
eq("周线：同一周两笔都落到 08-14 那根", [out[1].bt_buy?.label, out[1].bt_buy?.items.length], ["B×2", 2]);
eq("周线：不提前落到 08-07", out[0].bt_buy ?? null, null);

// 成交日晚于最后一根 → 丢弃
out = attachMarks(bars, [mark("sell", "2026-09-01")]);
eq("区间外丢弃", out.some((b) => b.bt_sell), false);

// 空 marks / 空 bars
eq("空 marks 原样返回", attachMarks(bars, []).some((b) => b.bt_buy || b.bt_sell), false);
eq("空 bars 不炸", attachMarks([], [mark("buy", "2026-08-11")]).length, 0);

// 计数（提示条用）
eq("计数", countMarks([mark("buy", "2026-08-11"), mark("buy", "2026-08-12"), mark("sell", "2026-08-13")]),
  { buy: 2, sell: 1 });

console.log(failed === 0 ? "\n全部通过" : `\n${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
```

- [ ] **Step 2: 跑脚本确认失败**

Run: `bun /tmp/check-attach-marks.ts`
Expected: 报错「Cannot find module .../backtestMarks」（模块还没建）

- [ ] **Step 3: 实现纯函数**

创建 `frontend/src/utils/backtestMarks.ts`：

```ts
import type { BacktestMark } from "../types/backtest";
import type { KlineBar } from "../types/kline";

/**
 * 把回测买卖标记归并到 K 线 bar 上（spec §5.3）。
 *
 * 规则与周期无关：一笔成交归到**第一根 `bar.date >= 成交日`** 的 bar——
 * 日线退化为精确匹配，周/月线（bar.date 是该周期最后一个交易日）即「落到所属周期那根」。
 * 位置只由 bar 决定（买取下沿 low、卖取上沿 high），与复权口径无关（spec §4.3）。
 *
 * 返回新数组与新 bar 对象（不改写入参）。
 */
export function attachMarks(bars: KlineBar[], marks: BacktestMark[]): KlineBar[] {
  if (!bars.length || !marks.length) return bars;

  const byIndex = new Map<number, { buy: BacktestMark[]; sell: BacktestMark[] }>();
  for (const m of marks) {
    const idx = firstBarIndexOnOrAfter(bars, m.date);
    if (idx < 0) continue; // 成交日晚于最后一根：丢弃（回测与K线同源，正常不可达）
    const bucket = byIndex.get(idx) ?? { buy: [], sell: [] };
    (m.side === "sell" ? bucket.sell : bucket.buy).push(m);
    byIndex.set(idx, bucket);
  }
  if (byIndex.size === 0) return bars;

  return bars.map((bar, index) => {
    const bucket = byIndex.get(index);
    if (!bucket) return bar;
    const next: KlineBar = { ...bar };
    if (bucket.buy.length) next.bt_buy = toBarMark("B", bucket.buy, bar.low);
    if (bucket.sell.length) next.bt_sell = toBarMark("S", bucket.sell, bar.high);
    return next;
  });
}

/** 二分找第一根 date >= target 的 bar（bars 升序；找不到返回 -1）。 */
function firstBarIndexOnOrAfter(bars: KlineBar[], target: string): number {
  let lo = 0;
  let hi = bars.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((bars[mid].date ?? "") >= target) {
      found = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return found;
}

function toBarMark(letter: string, items: BacktestMark[], value: number | null): BarMarkLike {
  const sorted = [...items].sort((a, b) =>
    `${a.date}${a.strategy}`.localeCompare(`${b.date}${b.strategy}`),
  );
  return {
    label: sorted.length > 1 ? `${letter}×${sorted.length}` : letter,
    count: sorted.length,
    value: value ?? null,
    items: sorted,
  };
}

/** 提示条计数（笔数，不是 bar 数）。 */
export function countMarks(marks: BacktestMark[]): { buy: number; sell: number } {
  let buy = 0;
  let sell = 0;
  for (const m of marks) {
    if (m.side === "sell") sell++;
    else buy++;
  }
  return { buy, sell };
}

type BarMarkLike = NonNullable<KlineBar["bt_buy"]>;
```

- [ ] **Step 4: 跑脚本（GREEN）**

Run: `bun /tmp/check-attach-marks.ts`
Expected: 全部 `PASS` + 「全部通过」（退出码 0）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/backtestMarks.ts
git commit -m "feat(ui): attachMarks——把回测买卖按日期归并到 K 线 bar（含周期合并与边界）"
```

---

## Task 5: 图表指标 BTMARK + 数据字段 + 弹框行

**Files:**
- Modify: `frontend/src/pages/Stocks/KlineChart.tsx`
（类型已在 Task 3 落地）

- [ ] **Step 1: 指标注册 + 常量**

`frontend/src/pages/Stocks/KlineChart.tsx` 顶部常量区追加：

```ts
const BT_BUY_BG = "#ef232a"; // 买入标记底色（用户确认默认值）
const BT_SELL_BG = "#14b143"; // 卖出标记底色
const BT_TEXT = "#ffffff";
const BT_OFFSET = 12; // 标记离 K 线沿的像素距离
```

新增注册函数（放在 `ensureZtMacdRegistered` 之后）：

```ts
let btMarksRegistered = false;

interface BtMarkResult {
  bt_buy: number | null;
  bt_sell: number | null;
  bt_buy_text: string;
  bt_sell_text: string;
}

/** 回测买卖标记：值只当坐标用（买=low、卖=high），字母靠 attrs 覆写 text。
 *  10.0.3 的 text figure 默认把「值」同时当 y 与 text，值给字母会 convertToPixel(NaN) 静默不画。 */
function ensureBtMarksRegistered() {
  if (btMarksRegistered) return;
  registerIndicator<BtMarkResult>({
    name: "BTMARK",
    shortName: "回测买卖",
    figures: [
      {
        key: "bt_buy",
        type: "text",
        attrs: (p) => {
          const y = (p.coordinate.current as Record<string, number>).bt_buy;
          return { text: (p.data?.current as BtMarkResult | null)?.bt_buy_text ?? "", y: clampY(y + BT_OFFSET, p.bounding) };
        },
        styles: () => btMarkStyles(BT_BUY_BG),
      },
      {
        key: "bt_sell",
        type: "text",
        attrs: (p) => {
          const y = (p.coordinate.current as Record<string, number>).bt_sell;
          return { text: (p.data?.current as BtMarkResult | null)?.bt_sell_text ?? "", y: clampY(y - BT_OFFSET, p.bounding) };
        },
        styles: () => btMarkStyles(BT_SELL_BG),
      },
    ],
    calc: (dataList) =>
      dataList.map((k) => {
        const bar = k as unknown as KlineBar;
        return {
          bt_buy: bar.bt_buy ? (bar.low ?? null) : null,
          bt_sell: bar.bt_sell ? (bar.high ?? null) : null,
          bt_buy_text: bar.bt_buy?.label ?? "",
          bt_sell_text: bar.bt_sell?.label ?? "",
        };
      }),
  });
  btMarksRegistered = true;
}

/** 标记样式：小号白字 + 圆角底色（用户确认的默认观感）。 */
function btMarkStyles(background: string) {
  return {
    color: BT_TEXT,
    backgroundColor: background,
    size: 10,
    weight: "600" as const,
    paddingLeft: 4,
    paddingRight: 4,
    paddingTop: 1,
    paddingBottom: 1,
    borderRadius: 2,
  };
}

/** 贴边时不让标记跑出窗格（bounding 缺省时不夹取）。 */
function clampY(y: number, bounding?: { top: number; bottom: number } | null) {
  if (!bounding) return y;
  return Math.min(Math.max(y, bounding.top + 8), bounding.bottom - 8);
}
```

`ensureAllRegistered()` 里追加一行 `ensureBtMarksRegistered();`。

- [ ] **Step 2: `toKlineData` 带字段**

`toKlineData` 的 map 里追加（自定义字段随行 → 弹框与 calc 都能读到，M12）：

```ts
        bt_buy: b.bt_buy ?? null,
        bt_sell: b.bt_sell ?? null,
```

- [ ] **Step 3: 主图挂载/卸载**

在「主图/副图指标随菜单选择增删」的 effect **之后**追加：

```ts
  // 回测买卖标记：有标记才挂 BTMARK，清除/无标记则移除（不进图例：figures 无 title）
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const hasMarks = Boolean(payload?.bars.some((b) => b.bt_buy || b.bt_sell));
    const exists = chart.getIndicators().some((i) => i.name === "BTMARK");
    if (hasMarks && !exists) {
      chart.createIndicator({ name: "BTMARK", paneId: "candle_pane" }, true);
    } else if (!hasMarks && exists) {
      chart.removeIndicator({ name: "BTMARK" });
    }
  }, [payload]);
```

- [ ] **Step 4: 弹框加一行**

把 `candleLegends` 结尾的 `return [ …8 行… ]` 原样改名为 `const base = [ …8 行内容一字不动… ]`，
再在其后追加 3 行（不新造结构，仍是 `{title, value}` 对）：

```ts
  const marks = markLegendLine(cur);
  return marks ? [...base, marks] : base;
```

新增两个小函数（放在 `candleLegends` 之前）：

```ts
/** 单笔明细：`B 78.66×600 · 单针下20`；卖出带收益率。 */
function markText(m: BacktestMark, letter: string): string {
  const parts = [`${letter} ${fmtP(m.price)}×${m.shares ?? "—"}`];
  if (m.side === "sell" && m.return_pct != null) parts.push(`${m.return_pct.toFixed(2)}%`);
  parts.push(m.strategy_name || m.strategy);
  return parts.join(" · ");
}

/** 弹框里的回测行：买段在前、卖段在后，段内按日期序，多笔用 ` / ` 分隔。 */
function markLegendLine(bar: KlineBar): TooltipLegend | null {
  const segments: string[] = [];
  if (bar.bt_buy?.items.length) {
    segments.push(bar.bt_buy.items.map((m) => markText(m, "B")).join(" / "));
  }
  if (bar.bt_sell?.items.length) {
    segments.push(bar.bt_sell.items.map((m) => markText(m, "S")).join(" / "));
  }
  if (!segments.length) return null;
  return {
    title: { text: "回测", color: NEUTRAL },
    value: { text: segments.join(" / "), color: NEUTRAL },
  };
}
```

并在 `KlineChart.tsx` 顶部补类型导入：`import type { BacktestMark } from "../../types/backtest";`

- [ ] **Step 5: 类型检查 + 构建**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 通过，产出新 dist

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/Stocks/KlineChart.tsx
git commit -m "feat(ui): 图表回测买卖标记 BTMARK（text figure）+ 弹框明细行"
```

---

## Task 6: 个股页接线（bt / 拉取 / 提示条 / 图表 key）

**Files:**
- Modify: `frontend/src/pages/Stocks/StockKlinePage.tsx`

- [ ] **Step 1: 状态与取数**

在 `snapshot` 状态之后追加：

```tsx
  // —— 回测买卖标记（M1/M7）：只在 URL 带合法 bt 时拉一次；失败只影响提示条 ——
  const btKey = useMemo(() => {
    const raw = initialParams.current.get("bt");
    return raw && /^[A-Za-z0-9_]{1,128}$/.test(raw) ? raw : null;
  }, []);
  const [marks, setMarks] = useState<BacktestMark[] | null>(null);
  const [marksRev, setMarksRev] = useState(0);
  const [marksLoading, setMarksLoading] = useState(Boolean(btKey));
  const [marksError, setMarksError] = useState("");
  const [btCleared, setBtCleared] = useState(false);

  useEffect(() => {
    if (!btKey || !codeValid || btCleared) {
      setMarksLoading(false);
      return;
    }
    const controller = new AbortController();
    setMarksLoading(true);
    getBacktestMarks(btKey, code, { signal: controller.signal })
      .then((resp) => {
        setMarks(resp.items);
        setMarksRev((n) => n + 1);
        setMarksError("");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setMarks([]);
        setMarksRev((n) => n + 1);
        setMarksError(err instanceof Error ? err.message : "加载回测标记失败。");
      })
      .finally(() => setMarksLoading(false));
    return () => controller.abort();
  }, [btKey, code, codeValid, btCleared]);
```

`useKline` 之后追加派生值：

```tsx
  const payloadWithMarks = useMemo(
    () => (payload && marks && marks.length ? { ...payload, bars: attachMarks(payload.bars, marks) } : payload),
    [payload, marks],
  );
  const markCounts = useMemo(() => countMarks(marks ?? []), [marks]);
  const clearBt = useCallback(() => {
    setBtCleared(true);
    setMarks(null);
    setMarksRev((n) => n + 1);
    replaceSearchParams((params) => {
      const had = params.has("bt");
      params.delete("bt");
      return had;
    });
  }, []);
```

- [ ] **Step 2: 提示条**

`adjust_degraded` 的 Alert 之后插入：

```tsx
      {btKey && !btCleared && (
        <Alert
          type={marksError ? "warning" : "info"}
          showIcon
          message={
            marksLoading
              ? "正在加载回测标记…"
              : marksError
                ? `回测标记加载失败：${marksError}`
                : markCounts.buy + markCounts.sell === 0
                  ? "该回测在这只票上没有成交"
                  : `正在显示回测 ${btKey} 的买卖标记（B ${markCounts.buy} / S ${markCounts.sell}）`
          }
          action={
            <Space>
              <Link to={`/backtests/${encodeURIComponent(btKey)}`}>查看报告</Link>
              <Button type="link" size="small" onClick={clearBt}>
                清除
              </Button>
            </Space>
          }
        />
      )}
```

- [ ] **Step 3: 图表用带标记的数据 + 数据版本 key**

把结尾的渲染改成：

```tsx
      {!loading && !error && payloadWithMarks && (
        <KlineChart
          key={`${payloadWithMarks.code}-${payloadWithMarks.period}-${payloadWithMarks.adjust}-${marksRev}`}
          payload={payloadWithMarks}
          mainOverlays={mainOverlays}
          subIndicators={subIndicators}
        />
      )}
```

> 为什么带 key：`KlineChart` 只在挂载时读数据（`getBars` 由 `init` 触发，全文件无 `resetData`），
> 仅换 `payload` 不会重画；marks 晚于 K 线到达时必须重挂载才看得到（spec §5.3）。

- [ ] **Step 4: 导入**

补：`import { Link } from "react-router-dom";`、`import { useCallback, useMemo, useRef, useState } from "react";`（按实际已有导入调整）、
`import { getBacktestMarks, /* 既有 */ } from "../../services/backtests";`、
`import type { BacktestMark } from "../../types/backtest";`、
`import { attachMarks, countMarks } from "../../utils/backtestMarks";`

- [ ] **Step 5: 类型检查 + 构建**

Run: `cd frontend && npx tsc -b && npm run build`
Expected: 通过

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/Stocks/StockKlinePage.tsx
git commit -m "feat(ui): 个股页按 bt 拉取并渲染回测买卖标记（可清除）"
```

---

## Task 7: 回测两张表的入口链接带 bt

**Files:**
- Modify: `frontend/src/pages/Backtests/components/BacktestReportTables.tsx:105`（tradeColumns 代码列）、`:176`（openPositionColumns 代码列）

- [ ] **Step 1: 交易明细表（代码列）**

```tsx
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?bt=${encodeURIComponent(record.execution_key)}`}>
        {record.code}
      </Link>
    ),
```

- [ ] **Step 2: 期末未平仓表（代码列）**

同上（该表保证至少有一个 B）。

- [ ] **Step 3: 跳过记录表不动**

`skipColumns` 保持 `to={`/stocks/${record.code}`}`（未成交 ⇒ 无买卖点）。

- [ ] **Step 4: 类型检查 + 构建 + 提交**

Run: `cd frontend && npx tsc -b && npm run build`

```bash
git add frontend/src/pages/Backtests/components/BacktestReportTables.tsx
git commit -m "feat(ui): 回测明细/未平仓的代码链接带上 bt（个股页显示买卖标记）"
```

---

## Task 8: 端到端验收（真实数据）

**Files:** 无（只验证；发现缺陷则回到对应任务修）

固定数据：key `20260915_031528_backtest_d740308f`，code `000661`（买 2026-08-03 @78.655728 ×600 单针下20；卖 2026-08-04 @77.154566 ×600，-2.02%）。

- [ ] **Step 1: 起服务**

Run: `hub start trendradar-8000`（若已在跑则跳过；dist 已在 Task 5–7 重建）
Expected: `:8000` 可用

- [ ] **Step 2: 从报告点进个股**

浏览器：打开 `/backtests/20260915_031528_backtest_d740308f` → 「交易明细」点 `000661`
Expected: URL 为 `/stocks/000661?bt=20260915_031528_backtest_d740308f`；页面出现提示条
`正在显示回测 … 的买卖标记（B 1 / S 1）`

- [ ] **Step 3: 断言图上真有 B/S（画布取证，不看代码）**

在页面里挂 `fillText` 记录器，然后**用 CDP 改视口尺寸**（容器随 ResizeObserver 变化 → 触发真实重绘；
仅在页面内 `dispatchEvent(new Event("resize"))` 不会改变容器尺寸、图表不重绘，实测无效）：

```js
const proto = CanvasRenderingContext2D.prototype;
window.__texts = [];
const orig = proto.fillText;
proto.fillText = function (t, ...r) { window.__texts.push(String(t)); return orig.call(this, t, ...r); };
```

（记录器装好后，在 CDP 侧执行一次 `setViewport` 改动 —— 例如 1200×800 → 1180×800 —— 等 1s 再读 `window.__texts`。）

再看 `[...new Set(window.__texts)].filter((t) => t === "B" || t === "S")`
Expected: 含 `["B","S"]`（size=10 的白字 + 底色方块）
（若该日不在默认视窗内：用鼠标在图上向右拖拽把 8 月初移进视窗后重试）

- [ ] **Step 4: 弹框明细**

把鼠标移到 2026-08-03 那根 bar 上（同一 x 附近左右微移触发十字光标）
Expected: 弹框末尾出现 `回测  B 78.66×600 · 单针下20`

- [ ] **Step 5: 周期/复权切换后仍在**

切「周」→ Expected: 提示条不变、`B`/`S` 仍在（落在当周那根）；切「不复权」→ 标记仍贴 K 线沿
（`k-line-chart-id` 会变，属既有重建机制）

- [ ] **Step 6: 清除**

点提示条「清除」
Expected: 标记消失（`__texts` 里不再有 B/S）、URL 无 `bt`、**页面不跳顶**（滚动位置不变）

- [ ] **Step 7: 刷新与负例**

1. 带 `bt` 刷新 → 标记仍在；
2. 从选股页进同一只票 → 无标记、无提示条；
3. 手改 `bt=does_not_exist_123` → K 线照常渲染 + 提示条一行「回测标记加载失败：…」

- [ ] **Step 8: 全量回归 + 收尾**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿（此前 685+ 项）
Run: `rm -f /tmp/check-attach-marks.ts`
Expected: 临时脚本删除
Run: `git status --short`
Expected: 干净（无未提交改动；dist 已 gitignore）
