# 个股K线图页实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **注意（本仓库约定）**：AGENTS.md 规定涉及子 Agent 需用户明确授权。选择执行方式时如需派子 Agent，先获用户授权。

**Goal:** 按 spec `docs/superpowers/specs/2026-09-07-stock-kline-chart-design.md`（v2.6，`93e6f7af`）实现 `/stocks/:code` 个股K线页：日/周/月、前复权/不复权、MA(34/55/144/233)+多空线/短期趋势线主图、VOL/MACD 默认副图，选股与回测报告双入口。

**Architecture:** 后端薄只读端点 `GET /api/stocks/{code}/kline`（route 注入 `app.state.market_store` → `kline_service.get_kline` → domain 纯函数 `adjust.py`/`kline.py`），复权/聚合/zx 线全部 polars 后端算；前端 klinecharts 10.0.3 渲染（`setDataLoader`/`resetData` 单通路），React.lazy 懒加载。

**Tech Stack:** Python 3.11 + FastAPI + polars（后端）；React 18 + TypeScript + antd 5 + klinecharts 10.0.3（前端）；pytest（后端测试，先 RED 后 GREEN）。

**基线：** `pytest -q` 当前 536 绿；前端 `npm run build`（tsc -b + vite build）通过。每任务后保持全绿。

---

## 文件结构

```text
新增（后端）
  trendradar/domain/market/adjust.py            # qfq 守卫与缩放（纯函数）
  trendradar/domain/market/kline.py             # 枚举/异常/KlineSeries/聚合/编排（纯函数）
  trendradar/app/services/kline_service.py      # get_kline：404/503 判别 + meta 容错 + 组装
  trendradar/interfaces/api/routes/stocks.py    # GET /api/stocks/{code}/kline
  tests/domain/market/test_kline.py             # domain：聚合/qfq/编排（TDD 主战场）
  tests/app/test_kline_service.py               # service：异常映射与 meta 容错
  tests/interfaces/test_kline_api.py            # API 契约：形状/11键/404/503/422/degraded

修改（后端）
  trendradar/interfaces/api/schemas/market.py   # 补 KlineBar/KlineResponse
  trendradar/interfaces/api/app.py              # include_router(stocks) + GZipMiddleware

新增（前端）
  frontend/src/types/kline.ts                   # 响应类型（文件头标 spec 版本）
  frontend/src/pages/Stocks/useKline.ts         # 取数 hook：abort + 序号守卫 + 状态机
  frontend/src/pages/Stocks/KlineChart.tsx      # klinecharts 封装：loader/样式/指标/生命周期
  frontend/src/pages/Stocks/StockKlinePage.tsx  # 页面：URL 驱动/信息栏/工具栏/错误态

修改（前端）
  frontend/src/services/marketData.ts           # getKline()
  frontend/src/routes/AppRouter.tsx             # /stocks/:code 懒加载路由
  frontend/src/layouts/AppShell.tsx             # selectedKey 加 /stocks 分支
  frontend/src/pages/Selections/SelectionResultPage.tsx      # code/name 列加链接
  frontend/src/pages/Backtests/components/BacktestReportTables.tsx  # 三张表 code 列加链接

文档
  README.md                                     # API 段补 kline 端点
```

---

### Task 0: 数据重建 runbook（上线前置，独立可并行，需 TUSHARE_TOKEN）

**Files:** 无代码改动；运维动作 + 判据核验。可与 Task 1-8 并行，**不阻塞编码**（未重建时 `adjust_degraded=true` 常亮是预期行为）。

- [ ] **Step 1: 备份现库**

```bash
cp -r storage/market storage/market.bak-$(date +%Y%m%d)
```

- [ ] **Step 2: 配置 token 并触发全量同步**

`deploy/.env`（或环境变量）设 `TUSHARE_TOKEN` 后启动服务，然后：

```bash
curl -X POST http://127.0.0.1:8000/api/market-data/sync \
  -H "Content-Type: application/json" \
  -d '{"force": true}'
```

同步为后台作业：用 `GET /api/executions/{job_id}` 轮询终态；BASELINE_START=2015-01-01（`domain/market/sync/spec.py:10`），跑批时间受 token 配额限制。

- [ ] **Step 3: 按判据验收（spec §1）**

```bash
.venv/bin/python -c "
import polars as pl
df = pl.read_parquet('storage/market/bars/000034.parquet')  # 有除权历史的票
print('rows:', df.height)
print('factor n_unique:', df['adj_factor'].n_unique())
assert 'pre_close' in df.columns and df['pre_close'].null_count() <= 1
assert df['adj_factor'].n_unique() > 1
print('重建判据通过')
"
```

Expected: 判据通过。**不得**抽恒 1.0 新股判重建失败（spec R2）。完成后 `git status` 应无仓库内变更（storage 不入 git）。

---

### Task 1: domain 聚合 `aggregate_bars`（TDD）

**Files:**
- Create: `trendradar/domain/market/kline.py`
- Test: `tests/domain/market/test_kline.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/domain/market/test_kline.py`：

```python
"""个股K线 domain 测试：聚合不变量、qfq 守卫/缩放、编排判别式断言（spec §7）。"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from trendradar.domain.market.adjust import apply_qfq
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    aggregate_bars,
    build_kline_series,
)


def _daily(dates, closes, factors, pre_close="real"):
    """构造日线 fixture。pre_close="real" 全真值（已重建形态）；None 无该列（存量形态）；
    list 逐行给定（增量合并形态，可含 null）。"""
    rows = []
    for i, (d, c, f) in enumerate(zip(dates, closes, factors)):
        rows.append({
            "code": "000001",
            "date": d,
            "open": c - 0.1,
            "high": c + 0.2,
            "low": c - 0.3,
            "close": c,
            "volume": 100.0 + i,
            "amount": 1000.0 + i,
            "adj_factor": f,
            "is_suspended": False,
        })
    if pre_close == "real":
        for r in rows:
            r["pre_close"] = r["close"] - 0.05
    elif pre_close is not None:
        for r, pc in zip(rows, pre_close):
            r["pre_close"] = pc
    return pl.DataFrame(rows)


def test_weekly_cross_year_stays_one_bar():
    # 2025-12-29(一)/12-31/2026-01-02 同一自然周——禁止 (year, week) 劈分（M16）
    df = _daily(
        dates=[date(2025, 12, 29), date(2025, 12, 31), date(2026, 1, 2)],
        closes=[10.0, 11.0, 12.0],
        factors=[1.0, 1.0, 1.0],
    )
    weekly = aggregate_bars(df, "weekly")
    assert weekly.height == 1
    row = weekly.row(0, named=True)
    assert row["date"] == date(2026, 1, 2)          # 组内最后交易日
    assert row["open"] == pytest.approx(9.9)        # 首日 open
    assert row["high"] == pytest.approx(12.2)       # max high
    assert row["low"] == pytest.approx(9.7)         # min low
    assert row["close"] == pytest.approx(12.0)      # 末日 close
    assert row["pre_close"] == pytest.approx(9.95)  # 组内首日 pre_close（可比昨收）
    assert row["volume"] == pytest.approx(303.0)
    assert row["amount"] == pytest.approx(3003.0)


def test_monthly_and_volume_conservation():
    df = _daily(
        dates=[date(2025, 1, 2), date(2025, 1, 31), date(2025, 2, 5)],
        closes=[10.0, 10.5, 11.0],
        factors=[1.0, 1.0, 1.0],
    )
    monthly = aggregate_bars(df, "monthly")
    assert monthly.height == 2
    assert monthly["date"].to_list() == [date(2025, 1, 31), date(2025, 2, 5)]
    assert monthly["volume"].sum() == pytest.approx(df["volume"].sum())  # 守恒
    assert monthly["close"].to_list() == pytest.approx([10.5, 11.0])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/domain/market/test_kline.py -v
```

Expected: FAIL（`ModuleNotFoundError: trendradar.domain.market.kline` 或 ImportError）。

- [ ] **Step 3: 最小实现**

创建 `trendradar/domain/market/kline.py`：

```python
"""个股 K 线序列领域计算：枚举、异常、聚合、编排。纯 polars，无 IO。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import polars as pl

from trendradar.domain.market.adjust import apply_qfq
from trendradar.domain.strategy.formulas.zxdkx import compute_zx_lines


class KlinePeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AdjustMode(str, Enum):
    QFQ = "qfq"
    NONE = "none"


class BarsUnavailable(RuntimeError):
    """bars 文件缺失或 0 行 → route 显式 404。"""


class MarketDataUnavailable(RuntimeError):
    """bars 目录缺失或读 IO 异常 → route 显式 503。"""


@dataclass(frozen=True)
class KlineSeries:
    """service 组装的域对象（F1/F6/G1）：degraded 与 meta 结果走独立通道。"""

    bars: pl.DataFrame
    adjust_degraded: bool
    name: str
    industry: str | None


def aggregate_bars(df: pl.DataFrame, period: str) -> pl.DataFrame:
    """自然周（周一锚）/自然月聚合；组内无 bar 不产 bar（停牌整周/月，N3）。"""
    if period == "weekly":
        key = pl.col("date").dt.truncate("1w")
    else:
        key = pl.col("date").dt.truncate("1mo")
    return (
        df.sort("date")
        .group_by(key.alias("_k"))
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("pre_close").first(),
            pl.col("volume").sum(),
            pl.col("amount").sum(),
            pl.col("date").last().alias("date"),
        )
        .drop("_k")
        .sort("date")
    )
```

同目录创建空的 `trendradar/domain/market/adjust.py`（下一任务填充）：

```python
"""qfq 守卫与缩放（kline 专用）。语义钉住 strategy/formulas/b1.py:58 _qfq_scale。"""
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/domain/market/test_kline.py -v
```

Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add trendradar/domain/market/kline.py trendradar/domain/market/adjust.py tests/domain/market/test_kline.py
git commit -m "feat: K线 domain 聚合——周(周一锚)/月聚合与守恒不变量（M16）"
```

---

### Task 2: domain 复权 `apply_qfq`（TDD）

**Files:**
- Modify: `trendradar/domain/market/adjust.py`
- Test: `tests/domain/market/test_kline.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/domain/market/test_kline.py` 末尾追加：

```python
# ---------- qfq 守卫与缩放（spec §4.3.1 / §4.4 / R2/R7） ----------


def test_qfq_scales_by_latest_factor_literal_oracle():
    # R1 oracle：全>0、不含 1.0、相邻比 1.25 不越带 → 守卫全过；钉死「分母=最新因子」
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(4)],
        closes=[10.0, 10.1, 10.2, 10.3],
        factors=[1.2, 1.2, 1.5, 1.5],
    )
    out, degraded = apply_qfq(df)
    assert degraded is False
    assert out["close"].to_list() == pytest.approx(
        [10.0 * 1.2 / 1.5, 10.1 * 1.2 / 1.5, 10.2 * 1.2 / 1.5, 10.3]
    )
    assert out["pre_close"].to_list()[0] == pytest.approx(9.95 * 1.2 / 1.5)  # R3


def test_qfq_pre_close_scaled_with_bar():
    # R3：qfq 档 pre_close 随同 bar 缩放，行内量纲一致
    df = _daily(
        dates=[date(2025, 3, 3), date(2025, 3, 4)],
        closes=[10.0, 10.1],
        factors=[1.2, 1.2],
    )
    out, _ = apply_qfq(df)
    assert out["pre_close"].to_list() == pytest.approx([9.95 * 1.2 / 1.2, 10.05 * 1.2 / 1.2])


def test_qfq_degrades_on_null_factor():
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.2, None])
    out, degraded = apply_qfq(df)
    assert degraded is True
    assert out["close"].to_list() == [10.0, 10.1]  # 整列退化，禁止部分缩放


def test_qfq_degrades_on_zero_and_nan_factor():
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.2, 0.0])
    out, degraded = apply_qfq(df)
    assert degraded is True and out["close"].to_list() == [10.0, 10.1]
    df2 = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                 factors=[1.2, float("nan")])  # M1：NaN 的 null_count()==0，须显式查
    out2, degraded2 = apply_qfq(df2)
    assert degraded2 is True and out2["close"].to_list() == [10.0, 10.1]


def test_qfq_degrades_on_factor_ratio_out_of_band():
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.0, 4.0])  # 4× > 3× 越带（B2）
    out, degraded = apply_qfq(df)
    assert degraded is True
    assert out["close"].to_list() == [10.0, 10.1]


def test_missing_factor_column_degrades():
    df = _daily(dates=[date(2025, 3, 3)], closes=[10.0], factors=[1.0]).drop("adj_factor")
    out, degraded = apply_qfq(df)
    assert degraded is True and out["close"].to_list() == [10.0]


def test_legacy_const_one_degrades_only_when_not_rebuilt():
    # 未重建（无 pre_close 列）：恒 1.0 → degraded（B1 故障态）
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.0, 1.0], pre_close=None)
    out, degraded = apply_qfq(df)
    assert degraded is True and out["close"].to_list() == [10.0, 10.1]
    # 已重建（pre_close 真值）：恒 1.0 是合法形态（上市从未除权的新股，R2）
    df2 = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                 factors=[1.0, 1.0])
    out2, degraded2 = apply_qfq(df2)
    assert degraded2 is False and out2["close"].to_list() == [10.0, 10.1]


def test_incremental_merged_legacy_file_still_degrades():
    # R7 穿透用例：旧文件 + _align_columns 式增量合并（旧行 pre_close=null、新行真因子）
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(5)],
        closes=[10.0, 10.05, 10.1, 10.2, 10.3],
        factors=[1.0, 1.0, 1.0, 2.0, 2.0],
        pre_close=[None, None, None, 10.15, 10.25],  # null_count=3 > 1 → 判未重建
    )
    out, degraded = apply_qfq(df)
    assert degraded is True  # 混合守卫仍触发，F ≤ 3 不漏
    assert out["close"].to_list() == [10.0, 10.05, 10.1, 10.2, 10.3]


def test_rebuilt_legit_split_stock_scales():
    # 已重建 + 合法除权形态（上市 1.0 前缀 + 除权后抬升）→ 正常缩放，不误降级
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(5)],
        closes=[10.0, 10.05, 10.1, 10.2, 10.3],
        factors=[1.0, 1.0, 1.0, 2.0, 2.0],
        pre_close=[9.95, 10.0, 10.05, 10.10, 10.15],
    )
    out, degraded = apply_qfq(df)
    assert degraded is False
    assert out["close"].to_list() == pytest.approx([5.0, 5.025, 5.05, 10.2, 10.3])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/domain/market/test_kline.py -v
```

Expected: 新增 9 条 FAIL（`ImportError: cannot import name 'apply_qfq'`），原有 2 条 PASS。

- [ ] **Step 3: 实现 apply_qfq**

覆盖 `trendradar/domain/market/adjust.py`：

```python
"""qfq 守卫与缩放（kline 专用）。语义钉住 strategy/formulas/b1.py:58 _qfq_scale，
守卫有意更严：列内 null 整列退化（参照副本是逐行跳过），差异被 §7 测试钉住。"""
from __future__ import annotations

import polars as pl


def _rebuild_marker(df: pl.DataFrame) -> bool:
    """重建标志 = 文件含 pre_close 列且 null_count ≤ 1（R7，复用 spec §1 判据）。

    仅判「含列」会被重建前的常规增量同步击穿：_align_columns 把旧行 pre_close 补
    null、新行带真值，合并文件从此「含列」且 adj_factor 恰成混合列。阈值失效方向
    是误报 degraded（可见），不是漏报。
    """
    if "pre_close" not in df.columns:
        return False
    return df["pre_close"].null_count() <= 1


def apply_qfq(df: pl.DataFrame) -> tuple[pl.DataFrame, bool]:
    """前复权：scale = adj_factor / 最新因子，OHLC 与 pre_close × scale（R3）；
    volume/amount 永不缩放。返回 (缩放后 df, adjust_degraded)。

    守卫整列语义（任一命中 → 整列退化原价，禁止部分缩放）：
    基础守卫（两态常开）：因子列缺失 / 含 null / ≤0 / NaN / 相邻比越带(>3× 或 <1/3×)；
    1.0 特征守卫（仅重建标志不成立）：恒 1.0；含 1.0 与非 1.0 混合。
    """
    if "adj_factor" not in df.columns:
        return df, True
    factors = df["adj_factor"]
    if factors.null_count() or factors.is_nan().any() or (factors <= 0).any():
        return df, True
    latest = factors[-1]
    if not _rebuild_marker(df):
        if factors.n_unique() == 1 and factors[0] == 1.0:
            return df, True
        if (factors == 1.0).any() and (factors != 1.0).any():
            return df, True
    ratio = factors / factors.shift(1)
    bounded = ratio.drop_nulls()
    if (bounded > 3.0).any() or (bounded < (1.0 / 3.0)).any():
        return df, True
    scaled = df.with_columns(
        (pl.col(c) * (pl.col("adj_factor") / latest)).alias(c)
        for c in ("open", "high", "low", "close", "pre_close")
        if c in df.columns
    )
    return scaled, False
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/domain/market/test_kline.py -v
```

Expected: 11 passed。

- [ ] **Step 5: 提交**

```bash
git add trendradar/domain/market/adjust.py tests/domain/market/test_kline.py
git commit -m "feat: qfq 守卫与缩放——整列守卫分组、重建标志门控（R2/R7）、字面量 oracle（R1）"
```

---

### Task 3: domain 编排 `build_kline_series`（TDD）

**Files:**
- Modify: `trendradar/domain/market/kline.py`
- Test: `tests/domain/market/test_kline.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# ---------- 编排（spec §4.3 / M5 判别式断言） ----------


def test_build_qfq_vs_none_discriminates_and_last_bar_equal():
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(4)],
        closes=[10.0, 10.1, 10.2, 10.3],
        factors=[1.2, 1.2, 1.5, 1.5],
    )
    qfq_bars, qfq_degraded = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.QFQ)
    none_bars, none_degraded = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.NONE)
    assert qfq_degraded is False and none_degraded is False
    assert qfq_bars["close"].to_list() != none_bars["close"].to_list()
    assert qfq_bars["close"][-1] == pytest.approx(none_bars["close"][-1])  # 末根 scale=1
    assert qfq_bars["close"].to_list() == [8.0, 8.08, 8.16, 10.3]  # round(4) 后逐值


def test_build_adjust_precedes_aggregate_literal_oracle():
    # 除权步 1.2→1.5 落在同一自然周：周线 open/pre_close 取缩放后首日值
    df = _daily(
        dates=[date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6)],
        closes=[10.0, 10.1, 10.2, 10.3],
        factors=[1.2, 1.2, 1.5, 1.5],
    )
    weekly, degraded = build_kline_series(df, KlinePeriod.WEEKLY, AdjustMode.QFQ)
    assert degraded is False
    assert weekly.height == 1
    row = weekly.row(0, named=True)
    assert row["open"] == pytest.approx(9.9 * 1.2 / 1.5)
    assert row["close"] == pytest.approx(10.3)
    assert row["pre_close"] == pytest.approx(9.95 * 1.2 / 1.5)


def test_build_attaches_zx_with_null_warmup():
    closes = [10.0 + 0.1 * i for i in range(20)]
    df = _daily(
        dates=[date(2025, 3, 3) + timedelta(days=i) for i in range(20)],
        closes=closes,
        factors=[1.0] * 20,
    )
    bars, degraded = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.QFQ)
    assert degraded is False
    assert bars["zx_short"].head(13).null_count() == 13  # MA14 窗口不足为 null（N14）
    assert bars["zx_short"][13] == pytest.approx(sum(closes[:14]) / 14)


def test_build_tolerates_missing_pre_close_column():
    # N3：存量文件无 pre_close 列（none 档）也要能出序列
    df = _daily(dates=[date(2025, 3, 3), date(2025, 3, 4)], closes=[10.0, 10.1],
                factors=[1.0, 1.0], pre_close=None)
    bars, _ = build_kline_series(df, KlinePeriod.DAILY, AdjustMode.NONE)
    assert bars["pre_close"].null_count() == 2  # 容错补 null 列
    assert bars["close"].to_list() == [10.0, 10.1]


def test_build_empty_raises_bars_unavailable():
    df = _daily(dates=[date(2025, 3, 3)], closes=[10.0], factors=[1.0])
    with pytest.raises(BarsUnavailable):
        build_kline_series(df.head(0), KlinePeriod.DAILY, AdjustMode.QFQ)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/domain/market/test_kline.py -v
```

Expected: 新增 5 条 FAIL（`ImportError: cannot import name 'build_kline_series'`）。

- [ ] **Step 3: 实现 build_kline_series**

在 `trendradar/domain/market/kline.py` 的 `aggregate_bars` 之后追加：

```python
def _ensure_optional_columns(df: pl.DataFrame) -> pl.DataFrame:
    """列访问容错（N3）：存量文件可能缺 pre_close 列（空帧 schema 才有）。"""
    if "pre_close" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("pre_close"))
    return df


def build_kline_series(
    df: pl.DataFrame, period: KlinePeriod, adjust: AdjustMode
) -> tuple[pl.DataFrame, bool]:
    """编排：sort → 0 行短路 → qfq → 聚合 → 附 zx 两线 → round(4)。

    返回 (bars, adjust_degraded)。KlineSeries 由 service 独家构造（G1）：
    domain 无 meta，不造 name/industry 占位值。
    """
    if df.is_empty():
        raise BarsUnavailable("无该股行情数据")
    df = _ensure_optional_columns(df).sort("date")
    degraded = False
    if adjust == AdjustMode.QFQ:
        df, degraded = apply_qfq(df)
    if period == KlinePeriod.DAILY:
        out = df
    else:
        out = aggregate_bars(df, period.value)
    short, long_ = compute_zx_lines(out)
    out = out.with_columns(
        short.alias("zx_short"),
        long_.alias("zx_long"),
    )
    out = out.with_columns(
        pl.col(c).round(4)
        for c in ("open", "high", "low", "close", "pre_close", "zx_short", "zx_long")
        if c in out.columns
    )
    out = out.select(
        "date", "open", "high", "low", "close", "pre_close",
        "volume", "amount", "zx_short", "zx_long",
    )
    return out, degraded
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

```bash
.venv/bin/python -m pytest tests/domain/market/test_kline.py -v && .venv/bin/python -m pytest -q
```

Expected: test_kline 16 passed；全量 536+16=552 passed（无回归）。

- [ ] **Step 5: 提交**

```bash
git add trendradar/domain/market/kline.py tests/domain/market/test_kline.py
git commit -m "feat: K线编排——qfq先于聚合、zx随行附线、round(4)、判别式断言（M5）"
```

---

### Task 4: service `get_kline`（TDD）

**Files:**
- Create: `trendradar/app/services/kline_service.py`
- Test: `tests/app/test_kline_service.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/app/test_kline_service.py`：

```python
"""kline_service 单元测试：404/503 异常映射与 meta 容错（不经过 HTTP 层，F2/M9）。"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from trendradar.app.services.kline_service import get_kline
from trendradar.domain.market.data_store import LocalParquetMarketStore
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    MarketDataUnavailable,
)


def _store(tmp_path) -> LocalParquetMarketStore:
    return LocalParquetMarketStore(tmp_path / "market" / "bars")


def _write_bars(tmp_path, corrupt: bool = False, code: str = "000001") -> None:
    bars_dir = tmp_path / "market" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    if corrupt:
        (bars_dir / f"{code}.parquet").write_bytes(b"not-parquet")
        return
    rows = [{
        "code": code, "date": date(2026, 8, 18),
        "open": 9.9, "high": 10.2, "low": 9.7, "close": 10.0,
        "pre_close": 9.95, "volume": 100.0, "amount": 1000.0,
        "adj_factor": 1.0, "is_suspended": False,
    }]
    pl.DataFrame(rows, schema_overrides={"date": pl.Date}).write_parquet(
        bars_dir / f"{code}.parquet"
    )


def _write_meta(tmp_path, columns: dict) -> None:
    (tmp_path / "market").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(columns).write_parquet(tmp_path / "market" / "stock_meta.parquet")


def test_get_kline_503_when_bars_dir_missing(tmp_path):
    with pytest.raises(MarketDataUnavailable):
        get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)


def test_get_kline_404_when_file_missing(tmp_path):
    _write_bars(tmp_path)
    with pytest.raises(BarsUnavailable):
        get_kline(_store(tmp_path), "999999", KlinePeriod.DAILY, AdjustMode.QFQ)


def test_get_kline_503_when_corrupt_file(tmp_path):
    _write_bars(tmp_path, corrupt=True)
    with pytest.raises(MarketDataUnavailable):
        get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)


def test_get_kline_returns_series_with_meta(tmp_path):
    _write_bars(tmp_path)
    _write_meta(tmp_path, {"code": ["000001"], "name": ["平安银行"], "industry": ["银行"]})
    series = get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)
    assert series.name == "平安银行" and series.industry == "银行"
    assert series.adjust_degraded is False
    assert series.bars["close"].to_list() == [10.0]


def test_get_kline_meta_missing_falls_back_to_code(tmp_path):
    _write_bars(tmp_path)  # 不写 meta
    series = get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)
    assert series.name == "000001" and series.industry is None  # M9：不得按列直接索引


def test_get_kline_meta_without_name_column_falls_back(tmp_path):
    _write_bars(tmp_path)
    _write_meta(tmp_path, {"code": ["000001"]})
    series = get_kline(_store(tmp_path), "000001", KlinePeriod.DAILY, AdjustMode.QFQ)
    assert series.name == "000001" and series.industry is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/app/test_kline_service.py -v
```

Expected: FAIL（`ModuleNotFoundError: trendradar.app.services.kline_service`）。

- [ ] **Step 3: 实现 kline_service.py**

创建 `trendradar/app/services/kline_service.py`：

```python
"""个股K线读服务：404/503 判别 + meta 容错 + 域对象组装（spec §4.1）。"""
from __future__ import annotations

from datetime import date

from trendradar.domain.market.data_store import LocalParquetMarketStore
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    KlineSeries,
    MarketDataUnavailable,
    build_kline_series,
)


def _lookup_meta(market_store: LocalParquetMarketStore, code: str) -> tuple[str, str | None]:
    """stock_meta 容错读取（M9）：缺文件/缺列视同查无此股，不得按列直接索引。"""
    try:
        meta = market_store.stock_meta([code])
    except Exception:
        return code, None
    if meta.is_empty() or "name" not in meta.columns:
        return code, None
    row = meta.row(0, named=True)
    name = row.get("name") or code
    industry = row.get("industry") if "industry" in meta.columns else None
    return str(name), industry


def get_kline(
    market_store: LocalParquetMarketStore,
    code: str,
    period: KlinePeriod,
    adjust: AdjustMode,
) -> KlineSeries:
    """读单股全量日线并产出周期序列。

    404/503 判别机制（F2：get_rows 对缺失文件静默返空帧——data_store.py:186-187
    实证——不钉机制则 503 不可达）：
    ① bars_dir 缺失 → MarketDataUnavailable（503）；
    ② get_rows 读异常（OSError/parquet 解析错）→ MarketDataUnavailable（503）；
    ③ 空帧 → BarsUnavailable（404，文件缺失与 0 行同语义）。
    """
    bars_dir = market_store.bars_dir
    if not bars_dir.is_dir():
        raise MarketDataUnavailable("行情数据正在更新，请稍后重试")
    try:
        raw = market_store.get_rows(code, date(1990, 1, 1), date.today())
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise MarketDataUnavailable("行情数据读取失败，请稍后重试") from exc
    if raw.is_empty():
        raise BarsUnavailable("无该股行情数据")
    bars, degraded = build_kline_series(raw, period, adjust)
    name, industry = _lookup_meta(market_store, code)
    return KlineSeries(bars=bars, adjust_degraded=degraded, name=name, industry=industry)
```

注意文件头需要 `import polars as pl`（except 子句引用 `pl.exceptions.PolarsError`）——在 import 区补：

```python
import polars as pl
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/app/test_kline_service.py -v
```

Expected: 6 passed。

- [ ] **Step 5: 提交**

```bash
git add trendradar/app/services/kline_service.py tests/app/test_kline_service.py
git commit -m "feat: kline_service——404/503 判别三句机制（F2）与 meta 容错（M9）"
```

---

### Task 5: API 路由 + schema + 注册 + GZip（TDD）

**Files:**
- Create: `trendradar/interfaces/api/routes/stocks.py`
- Create: `tests/interfaces/test_kline_api.py`
- Modify: `trendradar/interfaces/api/schemas/market.py`（末尾追加）
- Modify: `trendradar/interfaces/api/app.py:119-127`（注册 + GZip）

- [ ] **Step 1: 写失败测试**

创建 `tests/interfaces/test_kline_api.py`：

```python
"""个股K线 API 契约测试（spec v2.6 §7）：形状/11键/404/503/422/degraded。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

EXPECTED_BAR_KEYS = {
    "timestamp", "date", "open", "high", "low", "close",
    "pre_close", "volume", "amount", "zx_short", "zx_long",
}  # N21：键集合精确断言是唯一闸门


def _write_kline_bars(storage, code="000001", with_pre_close=True, factors=None):
    factors = factors or (1.2, 1.2, 1.5, 1.5, 1.5)  # 已重建 + 合法除权形态
    bars_dir = storage / "market" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    base = date(2026, 8, 17)  # 周一
    rows = []
    for i, f in enumerate(factors):
        close = 10.0 + i
        row = {
            "code": code, "date": base + timedelta(days=i),
            "open": close - 0.1, "high": close + 0.2, "low": close - 0.3,
            "close": close, "volume": 1000.0 + i, "amount": 10000.0 + i,
            "adj_factor": f, "is_suspended": False,
        }
        if with_pre_close:
            row["pre_close"] = close - 0.05
        rows.append(row)
    pl.DataFrame(rows, schema_overrides={"date": pl.Date}).write_parquet(
        bars_dir / f"{code}.parquet"
    )


def _write_meta(storage):
    meta = pl.DataFrame({"code": ["000001"], "name": ["平安银行"], "industry": ["银行"]})
    meta.write_parquet(storage / "market" / "stock_meta.parquet")


@pytest.fixture()
def kline_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    storage = tmp_path / "storage"
    storage.mkdir(parents=True)
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema

    init_schema(StorageConnection(storage).connect())
    _write_kline_bars(storage)
    _write_meta(storage)
    monkeypatch.setenv("TREND_RADAR_FRONTEND_DIST", str(tmp_path / "missing-dist"))

    from fastapi.testclient import TestClient
    from trendradar.interfaces.api.app import create_app

    with TestClient(create_app()) as c:
        yield c, storage


def test_kline_200_shape_and_values(kline_client):
    client, _ = kline_client
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload.keys()) == {
        "code", "name", "industry", "period", "adjust",
        "adjust_degraded", "last_bar_date", "bars",
    }
    assert payload["code"] == "000001"
    assert payload["name"] == "平安银行" and payload["industry"] == "银行"
    assert payload["period"] == "daily" and payload["adjust"] == "qfq"  # 默认值
    assert payload["adjust_degraded"] is False
    assert payload["last_bar_date"] == "2026-08-21"
    assert len(payload["bars"]) == 5
    assert all(set(b.keys()) == EXPECTED_BAR_KEYS for b in payload["bars"])
    last = payload["bars"][-1]
    assert last["close"] == 10.4  # 末根 scale=1（M1 不变量）
    assert type(last["close"]) is float
    assert last["date"] == "2026-08-21"
    assert last["timestamp"] == int(
        datetime(2026, 8, 21, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000
    )  # M11+N19：Asia/Shanghai 午夜毫秒
    assert payload["bars"][0]["close"] == pytest.approx(10.0 * 1.2 / 1.5)


def test_kline_strict_json_no_nan(kline_client):
    client, _ = kline_client
    resp = client.get("/api/stocks/000001/kline")

    def _reject(const):
        raise AssertionError(f"非 JSON 标准常量: {const}")  # N6：NaN/Inf 不许出现

    json.loads(resp.text, parse_constant=_reject)


def test_kline_degraded_true_on_legacy_const_factor(kline_client, tmp_path):
    client, storage = kline_client
    _write_kline_bars(storage, with_pre_close=False, factors=(1.0,) * 5)  # 存量形态
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["adjust_degraded"] is True  # B1：qfq≡none 属故障态，显式可见
    assert payload["bars"][-1]["close"] == 10.4


def test_kline_weekly_and_monthly(kline_client):
    client, _ = kline_client
    weekly = client.get("/api/stocks/000001/kline?period=weekly").json()
    assert len(weekly["bars"]) == 1  # 2026-08-17(一)~08-21(五) 同周
    assert weekly["bars"][0]["date"] == "2026-08-21"
    monthly = client.get("/api/stocks/000001/kline?period=monthly").json()
    assert len(monthly["bars"]) == 1


def test_kline_404_unknown_code(kline_client):
    client, _ = kline_client
    resp = client.get("/api/stocks/999999/kline")
    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)


def test_kline_503_when_bars_dir_missing(kline_client, tmp_path):
    client, storage = kline_client
    import shutil

    shutil.rmtree(storage / "market" / "bars")
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 503  # N7：区别于 404 的可重试语义


def test_kline_503_on_corrupt_file(kline_client, tmp_path):
    client, storage = kline_client
    (storage / "market" / "bars" / "000001.parquet").write_bytes(b"not-parquet")
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 503


def test_kline_422_invalid_inputs(kline_client):
    client, _ = kline_client
    assert client.get("/api/stocks/abc123/kline").status_code == 422  # F3：Path 校验
    assert client.get("/api/stocks/000001/kline?period=yearly").status_code == 422
    assert client.get("/api/stocks/000001/kline?adjust=hfq").status_code == 422


def test_kline_name_fallback_when_meta_missing(kline_client, tmp_path):
    client, storage = kline_client
    (storage / "market" / "stock_meta.parquet").unlink()
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["name"] == "000001" and payload["industry"] is None  # M9


def test_kline_two_row_history_ok(kline_client, tmp_path):
    client, storage = kline_client
    _write_kline_bars(storage, factors=(1.5, 1.5))  # N17：极短历史
    resp = client.get("/api/stocks/000001/kline")
    assert resp.status_code == 200
    assert len(resp.json()["bars"]) == 2
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/interfaces/test_kline_api.py -v
```

Expected: 全部 FAIL（404，路由不存在）。

- [ ] **Step 3: 实现 schema（KlineResponse）**

在 `trendradar/interfaces/api/schemas/market.py` 末尾追加：

```python
class KlineBar(BaseModel):
    timestamp: int
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    pre_close: float | None = None
    volume: float
    amount: float
    zx_short: float | None = None
    zx_long: float | None = None


class KlineResponse(BaseModel):
    code: str
    name: str
    industry: str | None = None
    period: str
    adjust: str
    adjust_degraded: bool
    last_bar_date: str
    bars: list[KlineBar]
```

- [ ] **Step 4: 实现路由**

创建 `trendradar/interfaces/api/routes/stocks.py`：

```python
"""个股维度只读路由（N9：身份用 path）。"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Request as FastAPIRequest

from trendradar.app.services.kline_service import get_kline
from trendradar.domain.market.kline import (
    AdjustMode,
    BarsUnavailable,
    KlinePeriod,
    MarketDataUnavailable,
)
from trendradar.interfaces.api.schemas.market import KlineResponse

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

_PERIODS = {p.value for p in KlinePeriod}
_ADJUSTS = {a.value for a in AdjustMode}


def _num(value: Any) -> float | None:
    """N6：NaN/±Inf 归一为 null（FastAPI 默认序列化 NaN 会产出非法 JSON）。"""
    if value is None:
        return None
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _timestamp(d: date) -> int:
    """M11+N19：Asia/Shanghai 午夜毫秒，前端零时区转换。"""
    return int(
        datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000
    )


@router.get("/{code}/kline", response_model=KlineResponse)
def get_stock_kline(
    request: FastAPIRequest,
    code: str = Path(pattern=r"^\d{6}$"),  # F3：path 参数用 Path 校验（禁 int：000001→1）
    period: str | None = Query(default=None),
    adjust: str | None = Query(default=None),
) -> Any:
    period_value = (period or KlinePeriod.DAILY.value).lower()
    adjust_value = (adjust or AdjustMode.QFQ.value).lower()
    if period_value not in _PERIODS:
        raise HTTPException(status_code=422, detail=f"非法 period: {period}")
    if adjust_value not in _ADJUSTS:
        raise HTTPException(status_code=422, detail=f"非法 adjust: {adjust}")

    market_store = request.app.state.market_store
    try:
        series = get_kline(
            market_store, code, KlinePeriod(period_value), AdjustMode(adjust_value)
        )
    except BarsUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    bars_df = series.bars
    bars = [
        {
            "timestamp": _timestamp(row["date"]),
            "date": row["date"].isoformat(),
            "open": _num(row["open"]),
            "high": _num(row["high"]),
            "low": _num(row["low"]),
            "close": _num(row["close"]),
            "pre_close": _num(row["pre_close"]),
            "volume": _num(row["volume"]),
            "amount": _num(row["amount"]),
            "zx_short": _num(row["zx_short"]),
            "zx_long": _num(row["zx_long"]),
        }
        for row in bars_df.iter_rows(named=True)
    ]
    return KlineResponse(
        code=code,
        name=series.name,
        industry=series.industry,
        period=period_value,
        adjust=adjust_value,
        adjust_degraded=series.adjust_degraded,
        last_bar_date=bars_df["date"][-1].isoformat(),
        bars=bars,
    )
```

- [ ] **Step 5: 注册路由 + GZip 中间件**

修改 `trendradar/interfaces/api/app.py`，在 `app.add_middleware(CORSMiddleware, ...)` 之后（第 117 行 `)` 之后）追加：

```python
    # M8：全站无压缩中间件；重建后单股 JSON ~470KB，GZip 为交付项
    from fastapi.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=1024)
```

第 119-127 行的路由注册段改为（新增两行）：

```python
    from trendradar.interfaces.api.routes.strategies import router as strategies_router
    from trendradar.interfaces.api.routes.executions import router as executions_router
    from trendradar.interfaces.api.routes.market import router as market_router
    from trendradar.interfaces.api.routes.backtest import router as backtest_router
    from trendradar.interfaces.api.routes.stocks import router as stocks_router  # R6

    app.include_router(strategies_router)
    app.include_router(executions_router)
    app.include_router(market_router)
    app.include_router(backtest_router)
    app.include_router(stocks_router)
```

- [ ] **Step 6: 跑测试确认通过 + 全量回归**

```bash
.venv/bin/python -m pytest tests/interfaces/test_kline_api.py -v && .venv/bin/python -m pytest -q
```

Expected: test_kline_api 11 passed；全量 552+11+6=569 passed。

- [ ] **Step 7: 提交**

```bash
git add trendradar/interfaces/api/routes/stocks.py trendradar/interfaces/api/schemas/market.py trendradar/interfaces/api/app.py tests/interfaces/test_kline_api.py
git commit -m "feat: GET /api/stocks/{code}/kline——裸响应/11键契约/404-503-422 分流/GZip（M6/M8/F2/F3）"
```

---

### Task 6: 前端 types + service + KlineChart + 页面 + 路由

**Files:**
- Create: `frontend/src/types/kline.ts`、`frontend/src/pages/Stocks/useKline.ts`、`frontend/src/pages/Stocks/KlineChart.tsx`、`frontend/src/pages/Stocks/StockKlinePage.tsx`
- Modify: `frontend/src/services/marketData.ts`、`frontend/src/routes/AppRouter.tsx`、`frontend/src/layouts/AppShell.tsx`

前端无 JS 测试基建（§9 决策）：每步以 `npm run build`（tsc 严格检查）为机器关卡，浏览器可视验收集中在 Task 8。

- [ ] **Step 1: 安装 klinecharts**

```bash
cd frontend && npm install klinecharts@^10.0.3
```

- [ ] **Step 2: 类型与服务**

创建 `frontend/src/types/kline.ts`：

```ts
// Spec: docs/superpowers/specs/2026-09-07-stock-kline-chart-design.md (v2.6)
export type KlinePeriod = "daily" | "weekly" | "monthly";
export type AdjustMode = "qfq" | "none";

export interface KlineBar {
  timestamp: number;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  pre_close: number | null;
  volume: number;
  amount: number;
  zx_short: number | null;
  zx_long: number | null;
}

export interface KlineResponse {
  code: string;
  name: string;
  industry: string | null;
  period: KlinePeriod;
  adjust: AdjustMode;
  adjust_degraded: boolean;
  last_bar_date: string;
  bars: KlineBar[];
}
```

`frontend/src/services/marketData.ts` 顶部 import 区追加：

```ts
import type { AdjustMode, KlinePeriod, KlineResponse } from "../types/kline";
```

文件末尾追加：

```ts
export async function getKline(
  code: string,
  period: KlinePeriod,
  adjust: AdjustMode,
  options?: { signal?: AbortSignal },
): Promise<KlineResponse> {
  const params = new URLSearchParams({ period, adjust });
  return requestJson<KlineResponse>(`/api/stocks/${code}/kline?${params.toString()}`, {
    signal: options?.signal,
  });
}
```

- [ ] **Step 3: useKline hook**

创建 `frontend/src/pages/Stocks/useKline.ts`：

```ts
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../services/apiClient";
import { getKline } from "../../services/marketData";
import type { AdjustMode, KlinePeriod, KlineResponse } from "../../types/kline";

export interface KlineViewState {
  payload: KlineResponse | null;
  loading: boolean;
  error: string | null;
  status: number | null;
}

/** 单一取数通路（M10/M13）：useEffect 发 GET（abort + 序号守卫）→ payload 状态化；
 *  KlineChart 只消费 payload，loader 不发请求。 */
export function useKline(
  code: string,
  period: KlinePeriod,
  adjust: AdjustMode,
  retryKey: number,
): KlineViewState {
  const [payload, setPayload] = useState<KlineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    if (!/^\d{6}$/.test(code)) {
      setPayload(null);
      setStatus(422);
      setError("非法的股票代码。");
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const seq = ++seqRef.current;
    setLoading(true);
    getKline(code, period, adjust, { signal: controller.signal })
      .then((data) => {
        if (seq !== seqRef.current) return; // 乱序响应丢弃
        setPayload(data);
        setError(null);
        setStatus(200);
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        if (err instanceof DOMException && err.name === "AbortError") return; // 静默
        setPayload(null);
        setStatus(err instanceof ApiError ? err.status : null);
        setError(err instanceof Error ? err.message : "加载K线数据失败。");
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
    return () => controller.abort();
  }, [code, period, adjust, retryKey]);

  return { payload, loading, error, status };
}
```

- [ ] **Step 4: KlineChart 组件**

创建 `frontend/src/pages/Stocks/KlineChart.tsx`：

```tsx
import { useEffect, useRef } from "react";
import { dispose, init, registerIndicator } from "klinecharts";
import type { Chart, KLineData } from "klinecharts";
import type { KlineBar, KlineResponse } from "../../types/kline";

const ZX_SHORT_COLOR = "#f5a623"; // 短期趋势线
const ZX_LONG_COLOR = "#b45fd9"; // 多空线
const DEFAULT_SUB_INDICATORS = ["VOL", "MACD"];

let zxRegistered = false;

/** 自定义 ZX 指标：calc 直读 bar 附带的 zx_short/zx_long（后端已算好，null 跳过）。 */
function ensureZxRegistered() {
  if (zxRegistered) return;
  registerIndicator({
    name: "ZX",
    shortName: "短期趋势线/多空线",
    series: "price",
    precision: 2,
    figures: [
      { key: "zx_short", title: "短期趋势线: ", type: "line", styles: () => ({ color: ZX_SHORT_COLOR }) },
      { key: "zx_long", title: "多空线: ", type: "line", styles: () => ({ color: ZX_LONG_COLOR }) },
    ],
    calc: (dataList) =>
      dataList.map((k) => {
        const bar = k as KlineBar;
        return { zx_short: bar.zx_short ?? null, zx_long: bar.zx_long ?? null };
      }),
  });
  zxRegistered = true;
}

function toKlineData(bars: KlineBar[]): KLineData[] {
  // M12：千元 amount 不映射 turnover（KLineData 约定字段），成交额只走信息栏
  return bars.map((b) => ({
    timestamp: b.timestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume,
    zx_short: b.zx_short,
    zx_long: b.zx_long,
  })) as KLineData[];
}

export interface KlineChartProps {
  payload: KlineResponse | null;
  /** VOL/MACD 之外的副图内置指标（spec §5.3，可加可删）。 */
  subIndicators: string[];
}

/** v10 数据入口是 setDataLoader/resetData（无 applyNewData，B3）；
 *  请求由 useKline 发起，本组件只注册一次 loader 同步 ref 数据。 */
export function KlineChart({ payload, subIndicators }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const dataRef = useRef<KlineResponse | null>(payload);
  const createdSubsRef = useRef<string[]>([]);

  useEffect(() => {
    dataRef.current = payload;
    chartRef.current?.resetData(); // 触发 loader 'init' 重取 ref 数据
  }, [payload]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    ensureZxRegistered();
    const chart = init(container, { timezone: "Asia/Shanghai" }); // M11
    if (!chart) return;
    chartRef.current = chart;

    // 涨红跌绿（N12）：蜡烛实体/影线/边框与指标量柱两套样式路径，键名以 10.0.3 Styles 为准
    chart.setStyles({
      candle: {
        bar: {
          upColor: "#ef232a", downColor: "#14b143",
          upBorderColor: "#ef232a", downBorderColor: "#14b143",
          upWickColor: "#ef232a", downWickColor: "#14b143",
        },
      },
      indicator: { bars: [{ upColor: "#ef232a", downColor: "#14b143" }] },
    });

    // 主图叠加：MA(34/55/144/233) + ZX
    chart.createIndicator({ name: "MA", calcParams: [34, 55, 144, 233] }, true, { id: "candle_pane" });
    chart.createIndicator("ZX", true, { id: "candle_pane" });
    // 副图默认 VOL + MACD
    chart.createIndicator("VOL", false, { height: 90 });
    chart.createIndicator("MACD", false, { height: 90 });
    createdSubsRef.current = [...DEFAULT_SUB_INDICATORS];

    chart.setDataLoader({
      getBars: ({ type }, done) => {
        if (type !== "init") {
          done([], { forward: false, backward: false }); // 全量数据，无分页（M8）
          return;
        }
        const resp = dataRef.current;
        done(resp ? toKlineData(resp.bars) : [], { forward: false, backward: false });
      },
    });

    return () => {
      dispose(container); // M14：StrictMode 双挂载不残留画布
      chartRef.current = null;
      createdSubsRef.current = [];
    };
  }, []);

  // 副图指标增删（N12）：createIndicator/removeIndicator，空窗自动销毁
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const target = [...DEFAULT_SUB_INDICATORS, ...subIndicators];
    for (const name of createdSubsRef.current) {
      if (!target.includes(name)) chart.removeIndicator({ name });
    }
    for (const name of target) {
      if (!createdSubsRef.current.includes(name)) {
        chart.createIndicator(name, false, { height: 90 });
      }
    }
    createdSubsRef.current = target;
  }, [subIndicators]);

  return <div ref={containerRef} className="kline-chart-container" />;
}
```

`frontend/src/styles/global.css` 末尾追加（N13 CSS 口径）：

```css
/* 个股K线页：图区占满剩余视口（父级无确定高度时 height:100% 解析为 0，N13） */
.stock-kline-page {
  display: flex;
  flex-direction: column;
  height: calc(100dvh - 96px);
  min-height: 480px;
  gap: 12px;
}

.stock-kline-page .kline-chart-container {
  flex: 1;
  min-height: 0;
}

@media (max-width: 720px) {
  .stock-kline-page .kline-chart-container {
    height: 420px;
    flex: none;
  }
}
```

- [ ] **Step 5: StockKlinePage**

创建 `frontend/src/pages/Stocks/StockKlinePage.tsx`：

```tsx
import { ArrowLeftOutlined, DownOutlined } from "@ant-design/icons";
import { Alert, Button, Dropdown, Result, Segmented, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { formatNumber } from "../../utils/format";
import type { AdjustMode, KlineBar, KlinePeriod } from "../../types/kline";
import { KlineChart } from "./KlineChart";
import { useKline } from "./useKline";

const { Title, Text } = Typography;

const PERIOD_OPTIONS: { value: KlinePeriod; label: string }[] = [
  { value: "daily", label: "日" },
  { value: "weekly", label: "周" },
  { value: "monthly", label: "月" },
];
const ADJUST_OPTIONS: { value: AdjustMode; label: string }[] = [
  { value: "qfq", label: "前复权" },
  { value: "none", label: "不复权" },
];
const EXTRA_SUB_INDICATORS = ["KDJ", "RSI", "BOLL", "WR", "BBI"];

/** §4.3.4：qfq 档用序列比值（因子比相消，与真实涨幅等价）；
 *  none 档优先 (close−pre_close)/pre_close（交易所口径），null 回退序列比。 */
export function pctChange(bars: KlineBar[], adjust: AdjustMode): number | null {
  if (bars.length < 2) return null;
  const last = bars[bars.length - 1];
  if (last.close == null) return null;
  if (adjust === "none" && last.pre_close != null && last.pre_close > 0) {
    return (last.close - last.pre_close) / last.pre_close;
  }
  const prev = bars[bars.length - 2];
  if (prev.close == null || prev.close <= 0) return null;
  return (last.close - prev.close) / prev.close;
}

export default function StockKlinePage() {
  const navigate = useNavigate();
  const { code = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [subIndicators, setSubIndicators] = useState<string[]>([]);
  const [retryKey, setRetryKey] = useState(0);

  const rawPeriod = searchParams.get("period");
  const rawAdjust = searchParams.get("adjust");
  const period: KlinePeriod =
    rawPeriod === "weekly" || rawPeriod === "monthly" ? rawPeriod : "daily"; // 白名单归一（N10）
  const adjust: AdjustMode = rawAdjust === "none" ? "none" : "qfq";
  const codeValid = /^\d{6}$/.test(code);

  // 归一化后回写 URL（replace），后续刷新/分享不再带脏参数
  useEffect(() => {
    if (rawPeriod == null && rawAdjust == null) return;
    if (rawPeriod === period && rawAdjust === adjust) return;
    const next = new URLSearchParams(searchParams);
    next.set("period", period);
    next.set("adjust", adjust);
    setSearchParams(next, { replace: true });
  }, [rawPeriod, rawAdjust, period, adjust, searchParams, setSearchParams]);

  const { payload, loading, error, status } = useKline(code, period, adjust, retryKey);
  const pct = useMemo(
    () => (payload ? pctChange(payload.bars, adjust) : null),
    [payload, adjust],
  );

  const setParam = (key: "period" | "adjust", value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set(key, value);
    setSearchParams(next, { replace: true }); // N18：replace 不刷历史栈，回退由浏览器驱动
  };

  if (!codeValid) {
    return (
      <Result
        status="error"
        title="非法的股票代码"
        subTitle={`「${code}」不是 6 位数字代码。`}
        extra={<Button onClick={() => navigate(-1)}>返回</Button>}
      />
    );
  }

  if (status === 404) {
    return (
      <Result
        status="404"
        title="无该股行情数据"
        subTitle={`${code} 在本地行情库中没有数据（未同步或已退市出库）。`}
        extra={<Button onClick={() => navigate(-1)}>返回</Button>}
      />
    );
  }

  const lastBar = payload?.bars.length ? payload.bars[payload.bars.length - 1] : null;
  const up = pct != null && pct >= 0;

  return (
    <div className="stock-kline-page">
      <Space align="center" split={<span>·</span>} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} />
        <Title level={4} style={{ margin: 0 }}>
          {payload ? `${payload.name} ${payload.code}` : code}
        </Title>
        <Text type="secondary">{payload?.industry ?? "-"}</Text>
        {lastBar?.close != null && (
          <Text style={{ color: up ? "#ef232a" : "#14b143", fontSize: 18 }}>
            {formatNumber(lastBar.close, 2)}
          </Text>
        )}
        {pct != null && (
          <Text style={{ color: up ? "#ef232a" : "#14b143" }}>
            {up ? "+" : ""}
            {(pct * 100).toFixed(2)}%
          </Text>
        )}
        {pct == null && <Text type="secondary">—</Text>}
        <Text type="secondary">数据截至 {payload?.last_bar_date ?? "-"}</Text>
      </Space>

      {payload?.adjust_degraded && (
        <Alert
          type="warning"
          showIcon
          message="复权因子缺失，前复权当前退化为原价（与不复权逐值相同）"
          description="本地行情库尚未完成 2015 基线重建，请在「行情数据」页执行全量同步后刷新。"
        />
      )}

      <Space wrap>
        <Segmented
          options={PERIOD_OPTIONS}
          value={period}
          disabled={loading}
          onChange={(value) => setParam("period", value as string)}
        />
        <Segmented
          options={ADJUST_OPTIONS}
          value={adjust}
          disabled={loading}
          onChange={(value) => setParam("adjust", value as string)}
        />
        <Dropdown
          disabled={loading}
          menu={{
            items: EXTRA_SUB_INDICATORS.map((name) => ({
              key: name,
              label: (subIndicators.includes(name) ? "✓ " : "") + name,
            })),
            onClick: ({ key }) =>
              setSubIndicators((cur) =>
                cur.includes(key) ? cur.filter((n) => n !== key) : [...cur, key],
              ),
          }}
        >
          <Button>
            副图指标 <DownOutlined />
          </Button>
        </Dropdown>
      </Space>

      {loading && (
        <div style={{ textAlign: "center", padding: 80 }}>
          <Spin tip="加载K线数据…" />
        </div>
      )}

      {!loading && error && status !== 404 && (
        <Result
          status="error"
          title={status === 503 ? "行情数据正在更新，请稍后重试" : "K线数据加载失败"}
          subTitle={error}
          extra={<Button type="primary" onClick={() => setRetryKey((k) => k + 1)}>重试</Button>}
        />
      )}

      {!loading && !error && payload && (
        <KlineChart payload={payload} subIndicators={subIndicators} />
      )}
    </div>
  );
}
```

- [ ] **Step 6: 路由注册 + 菜单高亮**

`frontend/src/routes/AppRouter.tsx` 顶部 import 区追加：

```tsx
import { lazy, Suspense } from "react";
```

组件定义区（import 之后、`createBrowserRouter` 之前）追加：

```tsx
const StockKlinePage = lazy(() => import("../pages/Stocks/StockKlinePage"));
```

`children` 数组中 `market-data` 路由项之后追加：

```tsx
      {
        path: "stocks/:code",
        element: (
          <Suspense fallback={<Spin style={{ display: "block", margin: "80px auto" }} />}>
            <StockKlinePage />
          </Suspense>
        ),
      },
```

`frontend/src/layouts/AppShell.tsx` 的 `selectedKey` 函数（行 16-27）在 `return "selections";` 之前插入：

```tsx
  if (pathname.startsWith("/stocks")) {
    return ""; // 个股K线页不归属任何菜单（N11），不高亮「选股」
  }
```

`Spin` 需在 AppRouter.tsx 的 antd import 中补充（`import { Spin } from "antd";` 若已有则合并）。

- [ ] **Step 7: 构建验证**

```bash
cd frontend && npm run build
```

Expected: tsc -b 无错、vite build 成功。若 klinecharts 类型（`Chart`/`KLineData`/`Styles`）与代码签名有出入，以 `node_modules/klinecharts/dist/index.d.ts` 为准修正（spec §5.3 已声明以 d.ts 为准）。

- [ ] **Step 8: 提交**

```bash
git add frontend/src/types/kline.ts frontend/src/services/marketData.ts frontend/src/pages/Stocks frontend/src/routes/AppRouter.tsx frontend/src/layouts/AppShell.tsx frontend/src/styles/global.css frontend/package.json frontend/package-lock.json
git commit -m "feat: 个股K线页——klinecharts v10 单通路渲染/URL驱动/懒加载路由（B3/M10/M13/M14/N12/N13）"
```

---

### Task 7: 双入口链接

**Files:**
- Modify: `frontend/src/pages/Selections/SelectionResultPage.tsx:66-79`（pickColumns）
- Modify: `frontend/src/pages/Backtests/components/BacktestReportTables.tsx:102-217`（trade/skip/openPosition 三表）

- [ ] **Step 1: 选股结果页加链接**

`SelectionResultPage.tsx` import 区追加（react-router-dom 已有 import，合并）：

```tsx
import { Link } from "react-router-dom";
```

`pickColumns`（行 66-79）的代码与名称两列改为：

```tsx
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 110,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?anchor=${record.date}`}>{record.code}</Link>
    ),
  },
  {
    title: "名称",
    dataIndex: "name",
    key: "name",
    width: 140,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?anchor=${record.date}`}>{record.name}</Link>
    ),
  },
```

- [ ] **Step 2: 回测报告三张表加链接**

`BacktestReportTables.tsx` import 区追加：

```tsx
import { Link } from "react-router-dom";
```

`tradeColumns`（行 104）代码列改为：

```tsx
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 100,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?anchor=${record.buy_date}`}>{record.code}</Link>
    ),
  },
```

`skipColumns`（行 141）代码列改为：

```tsx
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 100,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?anchor=${record.signal_date}`}>{record.code}</Link>
    ),
  },
```

`openPositionColumns`（行 159）代码列改为：

```tsx
  {
    title: "代码",
    dataIndex: "code",
    key: "code",
    width: 100,
    render: (_, record) => (
      <Link to={`/stocks/${record.code}?anchor=${record.buy_date}`}>{record.code}</Link>
    ),
  },
```

- [ ] **Step 3: 构建验证**

```bash
cd frontend && npm run build
```

Expected: 无错。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/Selections/SelectionResultPage.tsx frontend/src/pages/Backtests/components/BacktestReportTables.tsx
git commit -m "feat: 选股与回测报告个股入口——三表 code/name 链接 /stocks/:code?anchor（M18/N11）"
```

---

### Task 8: 回归 + 浏览器可视验收 + 文档

- [ ] **Step 1: 后端全量回归**

```bash
.venv/bin/python -m pytest -q
```

Expected: 536 + 新增（16 domain + 6 service + 11 api）= 569 passed。

- [ ] **Step 2: 前端构建**

```bash
cd frontend && npm run build
```

Expected: 无错。

- [ ] **Step 3: 浏览器可视验收（起真实服务）**

```bash
.venv/bin/python -m uvicorn trendradar.interfaces.api.app:app --host 127.0.0.1 --port 8000 &
cd frontend && npm run dev
```

逐项验证（每项截图/记录结果）：

1. 从 `/selections/{key}` 点个股 code → 跳 `/stocks/000001`，蜡烛图 + MA4 条 + ZX 两线渲染
2. 周期切换 日→周→月：**无旧数据瞬闪**（R5）；月线页非空白图（M15）
3. 复权切换 前复权→不复权：URL query 同步；未重建时 degraded Alert 常亮（B1）
4. 副图加 KDJ → 再删掉：pane 出现与消失（N12）
5. 十字光标跨 pane 联动；滚轮缩放/拖拽平移；**切周期无旧数据瞬闪**
6. 回测报告逐笔/持仓/跳过三表 code 链接跳转（anchor 定位 v1 撤销——参数零消费，2026-09-11 剥除）
7. 访问 `/stocks/abc123` → 错误态（不发请求）；`/stocks/999999` → 404 Result
8. 系统时区切 America/New_York（或以 TZ 环境重启 dev）：轴日期不偏移（M11）
9. StrictMode（dev 默认开）：无两张画布（M14）
10. **最右轴日期 == last_bar_date**

- [ ] **Step 4: README 更新**

`README.md` 的 API 代码块（`## API` 段）在 `GET /api/market-data/trading-dates` 之后追加：

```text
GET    /api/stocks/{code}/kline           # 个股K线（日/周/月，前复权/不复权，含多空线）
```

- [ ] **Step 5: 提交**

```bash
git add README.md
git commit -m "docs: README 补个股K线端点"
```

---

## 完成定义（DoD）

1. `pytest -q` 全绿（569+）；`npm run build` 无错
2. Task 8 浏览器验收 10 项全过（有记录）
3. 上线前置（Task 0 重建）完成或明确声明「当前处于 degraded 态，Alert 为预期」
4. 筹码分布/画线工具/分钟线等 §8 范围外项未被顺手实现（YAGNI）
